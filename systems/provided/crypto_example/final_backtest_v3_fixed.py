"""
FINAL BACKTEST V3 - ALL ISSUES FIXED
=====================================
Fixes:
1. Vol targeting properly verified (rolling vol, no look-ahead)
2. Combined skew calculated correctly
3. Survivorship-adjusted skew used for allocation

Run from the pysystemtrade project root directory.
"""

import os
import sys
import numpy as np
import pandas as pd
from scipy.stats import skew, kurtosis

# Get project root (this script is in systems/provided/crypto_example/)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, "..", "..", ".."))

# Add project root to path if running as script
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import logging
logging.disable(logging.CRITICAL)
import warnings
warnings.filterwarnings('ignore')

# =============================================================================
# CONFIGURATION
# =============================================================================

# Data paths relative to project root
COMBINED_FUNDING_DIR = os.path.join(PROJECT_ROOT, "data", "crypto", "funding_rates", "combined")
STITCHED_DIR = os.path.join(PROJECT_ROOT, "data", "crypto", "stitched")
PRICE_DIR = os.path.join(PROJECT_ROOT, "data", "crypto")

TREND_VOL_TARGET = 0.25   # 25%
CARRY_VOL_TARGET = 0.125  # 12.5%
DAYS_PER_YEAR = 365

print("=" * 90)
print("FINAL BACKTEST V3 - ALL ISSUES FIXED")
print("=" * 90)

# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def calc_stats(returns, name=""):
    returns = returns.dropna()
    if len(returns) < 20:
        return None

    cum = (1 + returns).cumprod()
    drawdown = (cum - cum.cummax()) / cum.cummax()

    ann_ret = returns.mean() * DAYS_PER_YEAR
    ann_vol = returns.std() * np.sqrt(DAYS_PER_YEAR)
    sharpe = ann_ret / ann_vol if ann_vol > 0 else 0

    return {
        'sharpe': sharpe,
        'ann_return': ann_ret,
        'ann_vol': ann_vol,
        'max_dd': drawdown.min(),
        'skew': skew(returns),
        'kurtosis': kurtosis(returns),
        'days': len(returns)
    }


def apply_position_buffer(
    target_positions: pd.Series,
    buffer_size: float,
    average_position: float = None,
    trade_to_edge: bool = True
) -> pd.Series:
    """
    Apply inertia buffer using Carver's approach.

    Buffer bands are centered around the TARGET position.
    Only trade if CURRENT position is outside the buffer band.

    This matches the implementation in systems/buffering.py and
    systems/accounts/account_buffering_subsystem.py.

    Args:
        target_positions: Series of optimal/target positions
        buffer_size: Buffer as fraction (e.g., 0.10 = 10%)
        average_position: If provided, use forecast method (constant buffer).
                         If None, use position method (buffer = target * buffer_size)
        trade_to_edge: If True, trade to buffer edge. If False, trade to optimal.

    Returns:
        buffered_positions: Actual positions after applying buffer
    """
    if buffer_size == 0:
        return target_positions.copy()

    target_positions = target_positions.dropna()
    if len(target_positions) == 0:
        return target_positions

    # Initialize with first target
    current_position = target_positions.iloc[0]
    buffered = [current_position]

    for i in range(1, len(target_positions)):
        optimal = target_positions.iloc[i]

        # Calculate buffer (forecast method if average_position provided)
        if average_position is not None:
            buffer = average_position * buffer_size
        else:
            buffer = abs(optimal) * buffer_size

        # Buffer bands centered around TARGET (Carver's approach)
        top_pos = optimal + buffer
        bot_pos = optimal - buffer

        # Check if CURRENT is outside band around TARGET
        if current_position > top_pos:
            current_position = top_pos if trade_to_edge else optimal
        elif current_position < bot_pos:
            current_position = bot_pos if trade_to_edge else optimal
        # else: hold current position

        buffered.append(current_position)

    return pd.Series(buffered, index=target_positions.index)


def select_optimal_buffer(
    buffer_results: list,
    sharpe_tolerance: float = 0.05,
    turnover_threshold: float = 0.15,
    strategy_name: str = ""
) -> tuple:
    """
    Select buffer using Carver-style regularization philosophy,
    with bias toward larger buffers for crypto (no contract rounding).

    Key principles:
    - 10% is the BASELINE, not a candidate to discover
    - Crypto has NO contract rounding, so buffer is ONLY inertia source
    - When in doubt, prefer larger buffer (conservative for crypto)

    Algorithm:
    1. Exclude buffers < 10% (diagnostics only)
    2. Use 10% as the baseline for turnover comparison
    3. Among {10%, 20%, 30%, 40%}:
       - Require >=15% turnover reduction vs 10% baseline
       - Require Sharpe within 5% of best in acceptable range
    4. Pick the FIRST buffer (smallest) that achieves threshold
    5. If no larger buffer achieves threshold but 20% has similar Sharpe,
       prefer 20% for additional inertia (crypto bias)

    Args:
        buffer_results: List of dicts with 'buffer_pct', 'sharpe', 'turnover' keys
        sharpe_tolerance: Max allowed Sharpe degradation from best (e.g., 0.05 = 5%)
        turnover_threshold: Required turnover reduction vs 10% baseline (e.g., 0.15 = 15%)
        strategy_name: Name for logging

    Returns:
        (selected_buffer_pct, selection_reason)
    """
    if not buffer_results:
        return (0.10, "No buffer results available, using 10% baseline")

    # Only consider buffers >= 10% (exclude 0% and 5% as diagnostics)
    candidates = [r for r in buffer_results if r['buffer_pct'] >= 0.10]

    if not candidates:
        return (0.10, "No candidates >= 10%, using default baseline")

    # Use 10% as the baseline for comparison
    baseline = next((r for r in candidates if r['buffer_pct'] == 0.10), candidates[0])
    baseline_turnover = baseline['turnover']
    baseline_sharpe = baseline['sharpe']

    # Find best Sharpe among candidates (>= 10%)
    best_sharpe = max(r['sharpe'] for r in candidates)
    min_acceptable_sharpe = best_sharpe * (1 - sharpe_tolerance)

    # Target turnover (15% reduction from 10% baseline)
    target_turnover = baseline_turnover * (1 - turnover_threshold)

    # Filter candidates: Sharpe within tolerance of best
    acceptable = [r for r in candidates if r['sharpe'] >= min_acceptable_sharpe]
    acceptable.sort(key=lambda x: x['buffer_pct'])

    # Find smallest buffer > 10% achieving turnover reduction
    for r in acceptable:
        if r['turnover'] <= target_turnover and r['buffer_pct'] > 0.10:
            reduction = (1 - r['turnover'] / baseline_turnover) * 100
            sharpe_vs_baseline = (r['sharpe'] / baseline_sharpe - 1) * 100
            reason = (f"{r['buffer_pct']*100:.0f}% buffer: {reduction:.0f}% turnover reduction vs 10% baseline, "
                     f"Sharpe {sharpe_vs_baseline:+.1f}% vs 10%")
            return (r['buffer_pct'], reason)

    # CRYPTO BIAS: If no larger buffer achieves threshold but 20% has similar Sharpe,
    # prefer 20% for additional inertia (since crypto has no contract rounding)
    r_20 = next((r for r in acceptable if r['buffer_pct'] == 0.20), None)
    if r_20 and r_20['sharpe'] >= baseline_sharpe * 0.98:  # Within 2% of 10% Sharpe
        reduction = (1 - r_20['turnover'] / baseline_turnover) * 100
        reason = (f"Crypto bias: 20% preferred for inertia ({reduction:.0f}% turnover reduction, "
                 f"Sharpe within 2% of 10% baseline)")
        return (0.20, reason)

    # Fallback to 10% baseline
    return (0.10, "10% baseline (no additional buffering justified)")


def calculate_turnover(positions):
    """
    Calculate annualized turnover from position series.

    Returns turnover as a multiple (e.g., 4.0 = 400% turnover/year)
    """
    positions = positions.dropna()
    if len(positions) < 2:
        return 0.0

    # Daily position changes (absolute)
    daily_changes = positions.diff().abs()

    # Mean absolute position for normalization
    mean_abs_position = positions.abs().mean()
    if mean_abs_position < 1e-10:
        return 0.0

    # Normalize by mean position and annualize
    daily_turnover = daily_changes.mean() / mean_abs_position
    annual_turnover = daily_turnover * DAYS_PER_YEAR

    return annual_turnover

# =============================================================================
# PART 1: LOAD TREND STRATEGY (uses pysystemtrade's built-in vol targeting)
# =============================================================================

print("\n" + "=" * 90)
print("PART 1: TREND STRATEGY")
print("=" * 90)

from sysdata.config.configdata import Config
from systems.provided.crypto_example.crypto_system import crypto_system

# pysystemtrade System handles vol targeting internally via percentage_vol_target in config
config = Config("systems.provided.crypto_example.crypto_config_diversified.yaml")
system = crypto_system(data_path=PRICE_DIR, config=config)

trend_account = system.accounts.portfolio()
trend_returns_gross = trend_account.percent / 100
trend_returns_gross.index = pd.to_datetime(trend_returns_gross.index.date)

# Apply trading costs to trend returns
# Trend has no leverage costs (spot only) but has trading costs
TREND_ANNUAL_TRADE_COST = 0.006  # 0.6% (4 trades/year × 0.15% round-trip)
trend_daily_cost = TREND_ANNUAL_TRADE_COST / DAYS_PER_YEAR
trend_returns = trend_returns_gross - trend_daily_cost

print(f"\nTrend returns from pysystemtrade System:")
print(f"  Date range: {trend_returns.index.min().date()} to {trend_returns.index.max().date()}")
print(f"  Days: {len(trend_returns)}")
print(f"  Costs applied: {TREND_ANNUAL_TRADE_COST*100:.1f}%/year (trading only, no leverage)")

# Check realized vol (System targets {TREND_VOL_TARGET*100}% via config)
trend_post2020 = trend_returns[trend_returns.index >= '2020-01-01']
realized_vol = trend_post2020.std() * np.sqrt(DAYS_PER_YEAR)
print(f"  Realized vol (post-2020): {realized_vol*100:.1f}%")
print(f"  Config target: {TREND_VOL_TARGET*100:.0f}%")
print(f"  Note: pysystemtrade uses rolling vol targeting, so realized vol may differ from target")

# =============================================================================
# PART 2: LOAD AND VOL-TARGET CARRY STRATEGY (rolling vol approach)
# =============================================================================

print("\n" + "=" * 90)
print("PART 2: CARRY STRATEGY - ROLLING VOL TARGETING")
print("=" * 90)

def load_funding(instrument):
    path = os.path.join(COMBINED_FUNDING_DIR, f"{instrument}_funding_combined.csv")
    if not os.path.exists(path):
        return pd.Series(dtype=float)
    df = pd.read_csv(path, parse_dates=['datetime'])
    df = df.set_index('datetime')
    df.index = pd.to_datetime(df.index.date)
    return df['fundingRate']

def load_spot_price(instrument):
    """Load spot price for basis risk calculation."""
    # Try stitched format first
    path = os.path.join(STITCHED_DIR, f"{instrument}_price.csv")
    if os.path.exists(path):
        df = pd.read_csv(path, parse_dates=['date'])
        df = df.set_index('date')
        df.index = pd.to_datetime(df.index.date)
        return df['close']
    # Fall back to price dir
    path = os.path.join(PRICE_DIR, f"{instrument}_price.csv")
    if os.path.exists(path):
        df = pd.read_csv(path, parse_dates=['date'])
        df = df.set_index('date')
        df.index = pd.to_datetime(df.index.date)
        return df['close']
    return pd.Series(dtype=float)

# Load all funding data
available_files = [f for f in os.listdir(COMBINED_FUNDING_DIR) if f.endswith('_funding_combined.csv')]
carry_instruments = sorted([f.replace('_funding_combined.csv', '') for f in available_files])

print(f"Carry instruments: {carry_instruments}")

all_funding = {}
for instr in carry_instruments:
    funding = load_funding(instr)
    if len(funding) >= 365:
        all_funding[instr] = funding

n_carry = len(all_funding)

# Create DataFrame of all funding rates
funding_df = pd.DataFrame(all_funding)

# =============================================================================
# WALK-FORWARD INSTRUMENT INCLUSION
# =============================================================================
# Only include an instrument after it has enough history for our longest lookback.
# For carry, the longest lookback is CORR_LOOKBACK (60 days) for correlation estimation.
# This ensures we only trade instruments when we can properly estimate vol and correlation.

CORR_LOOKBACK = 60  # 60-day rolling window for correlation
VOL_LOOKBACK = 35   # 35-day rolling window for vol
CARRY_MIN_HISTORY = max(CORR_LOOKBACK, VOL_LOOKBACK)  # = 60 days

# Calculate cumulative count of non-NaN values per instrument
cum_count = funding_df.notna().cumsum()

# Mask: True if instrument has >= CARRY_MIN_HISTORY at this date
available_mask = cum_count >= CARRY_MIN_HISTORY

# Apply mask to funding_df (set unavailable to NaN)
masked_funding = funding_df.where(available_mask)

# Count available instruments per day
n_available = available_mask.sum(axis=1)

# Calculate equal-weighted mean (only where instruments available)
raw_carry = masked_funding.sum(axis=1) / n_available
raw_carry = raw_carry[n_available > 0]  # Drop days with no instruments

print(f"\nWalk-forward instrument inclusion (min {CARRY_MIN_HISTORY} days):")
print(f"  Instruments available over time:")
for year in range(2017, 2026):
    mask = raw_carry.index.year == year
    if mask.any():
        avg_instruments = n_available[raw_carry.index][mask].mean()
        print(f"    {year}: {avg_instruments:.1f} avg instruments")

print(f"\nRaw carry returns:")
print(f"  Date range: {raw_carry.index.min().date()} to {raw_carry.index.max().date()}")
print(f"  Days: {len(raw_carry)}")

# =============================================================================
# BASIS RISK CALCULATION
# =============================================================================
# In a delta-neutral carry trade (long spot + short perp), you're exposed to
# basis changes (perp-spot spread). When basis blows out during stress, the
# short perp position loses money even though you're delta-neutral.
#
# Total P&L = Funding P&L + Basis P&L
#           = position × funding_rate + position × basis_change
#
# We model basis risk using 15% "unhedged exposure" as a proxy for tracking error,
# timing mismatches, and mark price divergence. This affects BOTH vol calculation
# and P&L attribution.

UNHEDGED_EXPOSURE = 0.15  # 15% effective unhedged exposure
# Note: VOL_LOOKBACK and CORR_LOOKBACK defined above in walk-forward section

# Load spot prices for instruments with funding data
spot_returns_dict = {}
for instr in all_funding.keys():
    spot = load_spot_price(instr)
    if len(spot) > 0:
        spot_returns_dict[instr] = spot.pct_change()

if spot_returns_dict:
    spot_returns_df = pd.DataFrame(spot_returns_dict)

    # Apply same walk-forward logic to spot returns
    # Use the same available_mask from funding (instruments must have funding data first)
    # Also require minimum spot history
    spot_cum_count = spot_returns_df.notna().cumsum()
    spot_available_mask = spot_cum_count >= CARRY_MIN_HISTORY

    # Combined mask: instrument must have enough history in BOTH funding AND spot
    # Align the masks to the same index
    combined_mask_idx = available_mask.index.intersection(spot_available_mask.index)
    combined_cols = [c for c in available_mask.columns if c in spot_available_mask.columns]

    funding_mask_aligned = available_mask.loc[combined_mask_idx, combined_cols]
    spot_mask_aligned = spot_available_mask.loc[combined_mask_idx, combined_cols]
    combined_available = funding_mask_aligned & spot_mask_aligned

    # Apply mask to spot returns
    masked_spot = spot_returns_df.loc[combined_mask_idx, combined_cols].where(combined_available)

    # Count available instruments per day
    n_spot_available = combined_available.sum(axis=1)

    # Calculate equal-weighted mean of spot returns
    avg_spot_return = masked_spot.sum(axis=1) / n_spot_available
    avg_spot_return = avg_spot_return[n_spot_available > 0]

    print(f"\nBasis risk calculation (walk-forward):")
    print(f"  Instruments with spot data: {len(spot_returns_dict)}")
    print(f"  Unhedged exposure: {UNHEDGED_EXPOSURE*100:.0f}%")
else:
    avg_spot_return = pd.Series(0.0, index=raw_carry.index)
    print(f"\nBasis risk: No spot data found, using funding-only returns")

# Calculate effective vol that includes basis risk
# The carry position has two components:
# 1. Funding returns (what we collect)
# 2. Basis returns (unhedged exposure × spot returns)
#
# Effective vol = sqrt(funding_vol² + basis_vol²)
# Where basis_vol = unhedged_exposure × spot_vol

# Align indices for calculation
common_funding_idx = raw_carry.index.intersection(avg_spot_return.index)
funding_aligned = raw_carry.loc[common_funding_idx]
spot_aligned = avg_spot_return.loc[common_funding_idx]

# Calculate component volatilities
funding_vol = funding_aligned.rolling(window=VOL_LOOKBACK, min_periods=20).std() * np.sqrt(DAYS_PER_YEAR)
spot_vol = spot_aligned.rolling(window=VOL_LOOKBACK, min_periods=20).std() * np.sqrt(DAYS_PER_YEAR)

# Basis vol = unhedged exposure × spot vol
basis_vol = UNHEDGED_EXPOSURE * spot_vol

# Walk-forward correlation between funding and spot returns
# During stress, funding and spot tend to move together (positive correlation)
# Note: CORR_LOOKBACK defined above in walk-forward section (= 60 days)
rolling_corr = funding_aligned.rolling(
    window=CORR_LOOKBACK,
    min_periods=30
).corr(spot_aligned)

# Shift by 1 day to avoid lookahead bias
rolling_corr = rolling_corr.shift(1)

# Clip to [0, 0.8] - negative correlation unlikely, >0.8 is extreme
rolling_corr = rolling_corr.clip(lower=0.0, upper=0.8).fillna(0.3)  # Default 0.3

# Effective vol includes both funding and basis risk WITH correlation
# Formula: sqrt(a² + b² + 2ab*corr) instead of just sqrt(a² + b²)
effective_vol = np.sqrt(
    funding_vol**2 +
    basis_vol**2 +
    2 * funding_vol * basis_vol * rolling_corr
)

print(f"\n  Vol components (recent average):")
print(f"    Funding vol: {funding_vol.iloc[-250:].mean()*100:.1f}%")
print(f"    Spot vol: {spot_vol.iloc[-250:].mean()*100:.1f}%")
print(f"    Funding-spot correlation: {rolling_corr.iloc[-250:].mean():.2f}")
print(f"    Basis vol (15% × spot): {basis_vol.iloc[-250:].mean()*100:.1f}%")
print(f"    Effective vol: {effective_vol.iloc[-250:].mean()*100:.1f}%")

# Position scale = target_vol / effective_vol (lagged to avoid look-ahead)
rolling_vol = effective_vol
position_scale = CARRY_VOL_TARGET / rolling_vol.shift(1)

# Cap extreme leverage (max 10x) to prevent blowups during low-vol periods
position_scale = position_scale.clip(upper=10.0)

# Calculate P&L components
funding_pnl = funding_aligned * position_scale
basis_pnl = spot_aligned * UNHEDGED_EXPOSURE * position_scale

# Total carry returns = funding P&L + basis P&L
carry_returns_with_basis = funding_pnl + basis_pnl
carry_returns_gross = carry_returns_with_basis.dropna()

# Apply costs to carry returns
# These are real costs that reduce returns, not just annotations
CARRY_ANNUAL_LEVERAGE_COST = 0.07    # 7% (borrowing + margin opportunity cost)
CARRY_ANNUAL_OTHER_COSTS = 0.051     # 5.1% (exchange, basis blowout, margin, regime)
CARRY_ANNUAL_TRADE_COST = 0.003      # 0.3% (2 trades/year × 0.15% round-trip)
CARRY_TOTAL_ANNUAL_COST = CARRY_ANNUAL_LEVERAGE_COST + CARRY_ANNUAL_OTHER_COSTS + CARRY_ANNUAL_TRADE_COST

carry_daily_cost = CARRY_TOTAL_ANNUAL_COST / DAYS_PER_YEAR
carry_returns = carry_returns_gross - carry_daily_cost

# Calculate funding-only returns (for comparison, also net of costs)
funding_only_returns = (funding_pnl - carry_daily_cost).dropna()

print(f"\nRolling vol targeting:")
print(f"  Target vol: {CARRY_VOL_TARGET*100:.1f}%")
print(f"  Vol lookback: {VOL_LOOKBACK} days")
print(f"  Costs applied: {CARRY_TOTAL_ANNUAL_COST*100:.1f}%/year")
print(f"    - Leverage: {CARRY_ANNUAL_LEVERAGE_COST*100:.1f}%")
print(f"    - Other risks: {CARRY_ANNUAL_OTHER_COSTS*100:.1f}%")
print(f"    - Trading: {CARRY_ANNUAL_TRADE_COST*100:.1f}%")
print(f"  Max leverage cap: 10x")

# Verify with basis risk
carry_post2020 = carry_returns[carry_returns.index >= '2020-01-01']
realized_carry_vol = carry_post2020.std() * np.sqrt(DAYS_PER_YEAR)
print(f"  Realized vol (post-2020, with basis risk): {realized_carry_vol*100:.1f}%")
print(f"  Note: Basis risk adds volatility during stress periods")

# Compare funding-only vs with basis risk
funding_only_post2020 = funding_only_returns[funding_only_returns.index >= '2020-01-01'].dropna()
funding_only_vol = funding_only_post2020.std() * np.sqrt(DAYS_PER_YEAR)
funding_only_sharpe = funding_only_post2020.mean() * DAYS_PER_YEAR / funding_only_vol
with_basis_sharpe = carry_post2020.mean() * DAYS_PER_YEAR / realized_carry_vol

print(f"\n  Comparison (funding-only vs with basis risk):")
print(f"    Funding-only vol:  {funding_only_vol*100:.1f}%")
print(f"    With basis vol:    {realized_carry_vol*100:.1f}%")
print(f"    Funding-only Sharpe: {funding_only_sharpe:.2f}")
print(f"    With basis Sharpe:   {with_basis_sharpe:.2f}")
print(f"    Funding-only skew:   {skew(funding_only_post2020):+.2f}")
print(f"    With basis skew:     {skew(carry_post2020):+.2f}")

# Also track raw returns for reference
carry_post2020_raw = raw_carry[raw_carry.index >= '2020-01-01']
raw_carry_vol = carry_post2020_raw.std() * np.sqrt(DAYS_PER_YEAR)
print(f"  Raw (unleveraged) vol: {raw_carry_vol*100:.2f}%")

# -----------------------------------------------------------------------------
# DIAGNOSTIC: Carry position scale analysis
# -----------------------------------------------------------------------------
print(f"\n--- CARRY POSITION SCALE DIAGNOSTICS ---")
print(f"  (Investigating why avg position may differ from 'forecast=10' equivalent)")

# Filter to valid position scales
valid_scale = position_scale.dropna()

# Position scale statistics
print(f"\n  Position scale statistics:")
print(f"    Average: {valid_scale.mean():.2f}")
print(f"    Median:  {valid_scale.median():.2f}")
print(f"    Std dev: {valid_scale.std():.2f}")
print(f"    Min/Max: {valid_scale.min():.2f} / {valid_scale.max():.2f}")

# Distribution by percentile
print(f"\n  Distribution by percentile:")
for pct in [10, 25, 50, 75, 90]:
    val = valid_scale.quantile(pct/100)
    print(f"    {pct}th percentile: {val:.2f}")

# Time evolution - rolling average
rolling_avg_scale = valid_scale.rolling(90, min_periods=30).mean()
print(f"\n  Rolling 90d avg position scale by year:")
for year in range(2017, 2026):
    year_data = rolling_avg_scale[rolling_avg_scale.index.year == year]
    if len(year_data) > 0:
        print(f"    {year}: {year_data.mean():.2f}")

# Expected scale calculation
# For carry: scale = target_vol / effective_vol
# If effective_vol = target_vol, scale = 1.0
avg_effective_vol_recent = effective_vol.iloc[-500:].mean()
expected_scale = CARRY_VOL_TARGET / avg_effective_vol_recent
print(f"\n  Expected vs Actual:")
print(f"    Avg effective vol (recent): {avg_effective_vol_recent*100:.1f}%")
print(f"    Target vol: {CARRY_VOL_TARGET*100:.1f}%")
print(f"    Expected scale (target/effective): {expected_scale:.2f}")
print(f"    Actual avg scale: {valid_scale.mean():.2f}")
print(f"    Ratio (actual/expected): {valid_scale.mean()/expected_scale:.2f}")

# =============================================================================
# PART 3: ALIGN AND DEFINE WINDOWS
# =============================================================================

print("\n" + "=" * 90)
print("PART 3: ALIGN DATA")
print("=" * 90)

common_idx = trend_returns.index.intersection(carry_returns.index)
trend_aligned = trend_returns.loc[common_idx].dropna()
carry_aligned = carry_returns.loc[common_idx].dropna()

common_idx = trend_aligned.index.intersection(carry_aligned.index)
trend_aligned = trend_aligned.loc[common_idx]
carry_aligned = carry_aligned.loc[common_idx]

recent_mask = common_idx >= '2020-01-01'
y2022_mask = (common_idx >= '2022-01-01') & (common_idx <= '2022-12-31')

trend_recent = trend_aligned[recent_mask]
carry_recent = carry_aligned[recent_mask]

print(f"Aligned period: {common_idx.min().date()} to {common_idx.max().date()}")
print(f"Recent window: {recent_mask.sum()} days")

# Final vol verification
print(f"\nFinal vol verification (recent window):")
print(f"  Trend: {trend_recent.std() * np.sqrt(DAYS_PER_YEAR)*100:.1f}% (target: {TREND_VOL_TARGET*100:.0f}%)")
print(f"  Carry: {carry_recent.std() * np.sqrt(DAYS_PER_YEAR)*100:.1f}% (target: {CARRY_VOL_TARGET*100:.1f}%)")

# =============================================================================
# PART 4: DIAGNOSE SKEW CALCULATION ISSUE
# =============================================================================

print("\n" + "=" * 90)
print("PART 4: SKEW CALCULATION DIAGNOSIS")
print("=" * 90)

print(f"\nIndividual strategy skew (recent window):")
print(f"  Trend skew: {skew(trend_recent):+.2f}")
print(f"  Carry skew: {skew(carry_recent):+.2f}")

print(f"\n--- Testing 80/20 allocation ---")
t_wt, c_wt = 0.80, 0.20

trend_contribution = t_wt * trend_recent
carry_contribution = c_wt * carry_recent
combined_80_20 = trend_contribution + carry_contribution

print(f"  Trend contribution: weight={t_wt}, vol={trend_contribution.std()*np.sqrt(365)*100:.1f}%")
print(f"  Carry contribution: weight={c_wt}, vol={carry_contribution.std()*np.sqrt(365)*100:.1f}%")
print(f"  Combined returns: vol={combined_80_20.std()*np.sqrt(365)*100:.1f}%")
print(f"  Combined skew: {skew(combined_80_20):+.2f}")

print(f"\n--- Testing 50/50 allocation ---")
t_wt, c_wt = 0.50, 0.50

combined_50_50 = t_wt * trend_recent + c_wt * carry_recent
print(f"  Combined skew: {skew(combined_50_50):+.2f}")

print(f"\n--- Testing 20/80 allocation ---")
t_wt, c_wt = 0.20, 0.80

combined_20_80 = t_wt * trend_recent + c_wt * carry_recent
print(f"  Combined skew: {skew(combined_20_80):+.2f}")

# =============================================================================
# PART 5: INDIVIDUAL STRATEGY STATS
# =============================================================================

print("\n" + "=" * 90)
print("PART 5: INDIVIDUAL STRATEGY STATISTICS")
print("=" * 90)

# Full period
trend_full_stats = calc_stats(trend_aligned)
carry_full_stats = calc_stats(carry_aligned)

print(f"\n--- FULL PERIOD ({common_idx.min().date()} to {common_idx.max().date()}) ---")
print(f"| Strategy | Sharpe | Return |   Vol | Max DD |   Skew |   Kurt |")
print(f"|----------|--------|--------|-------|--------|--------|--------|")
print(f"| Trend    | {trend_full_stats['sharpe']:>6.2f} | {trend_full_stats['ann_return']*100:>5.1f}% | {trend_full_stats['ann_vol']*100:>4.1f}% | {trend_full_stats['max_dd']*100:>5.1f}% | {trend_full_stats['skew']:>+5.2f} | {trend_full_stats['kurtosis']:>6.1f} |")
print(f"| Carry    | {carry_full_stats['sharpe']:>6.2f} | {carry_full_stats['ann_return']*100:>5.1f}% | {carry_full_stats['ann_vol']*100:>4.1f}% | {carry_full_stats['max_dd']*100:>5.1f}% | {carry_full_stats['skew']:>+5.2f} | {carry_full_stats['kurtosis']:>6.1f} |")
print(f"\nCorrelation: {trend_aligned.corr(carry_aligned):.3f}")

# Recent period
trend_recent_stats = calc_stats(trend_recent)
carry_recent_stats = calc_stats(carry_recent)

print(f"\n--- RECENT WINDOW (Post-2020) ---")
print(f"| Strategy | Sharpe | Return |   Vol | Max DD |   Skew |   Kurt |")
print(f"|----------|--------|--------|-------|--------|--------|--------|")
print(f"| Trend    | {trend_recent_stats['sharpe']:>6.2f} | {trend_recent_stats['ann_return']*100:>5.1f}% | {trend_recent_stats['ann_vol']*100:>4.1f}% | {trend_recent_stats['max_dd']*100:>5.1f}% | {trend_recent_stats['skew']:>+5.2f} | {trend_recent_stats['kurtosis']:>6.1f} |")
print(f"| Carry    | {carry_recent_stats['sharpe']:>6.2f} | {carry_recent_stats['ann_return']*100:>5.1f}% | {carry_recent_stats['ann_vol']*100:>4.1f}% | {carry_recent_stats['max_dd']*100:>5.1f}% | {carry_recent_stats['skew']:>+5.2f} | {carry_recent_stats['kurtosis']:>6.1f} |")
print(f"\nCorrelation: {trend_recent.corr(carry_recent):.3f}")

# Pre-2020 vs Post-2020 comparison (verifies walk-forward is working)
pre2020_mask = common_idx < '2020-01-01'
trend_pre2020 = trend_aligned[pre2020_mask]
carry_pre2020 = carry_aligned[pre2020_mask]

if len(trend_pre2020) > 20 and len(carry_pre2020) > 20:
    trend_pre_stats = calc_stats(trend_pre2020)
    carry_pre_stats = calc_stats(carry_pre2020)

    print(f"\n--- PRE-2020 WINDOW ({common_idx[pre2020_mask].min().date()} to {common_idx[pre2020_mask].max().date()}) ---")
    print(f"| Strategy | Sharpe | Return |   Vol | Max DD |   Skew |   Kurt |")
    print(f"|----------|--------|--------|-------|--------|--------|--------|")
    if trend_pre_stats:
        print(f"| Trend    | {trend_pre_stats['sharpe']:>6.2f} | {trend_pre_stats['ann_return']*100:>5.1f}% | {trend_pre_stats['ann_vol']*100:>4.1f}% | {trend_pre_stats['max_dd']*100:>5.1f}% | {trend_pre_stats['skew']:>+5.2f} | {trend_pre_stats['kurtosis']:>6.1f} |")
    if carry_pre_stats:
        print(f"| Carry    | {carry_pre_stats['sharpe']:>6.2f} | {carry_pre_stats['ann_return']*100:>5.1f}% | {carry_pre_stats['ann_vol']*100:>4.1f}% | {carry_pre_stats['max_dd']*100:>5.1f}% | {carry_pre_stats['skew']:>+5.2f} | {carry_pre_stats['kurtosis']:>6.1f} |")

    print(f"\n  Pre-2020: {len(trend_pre2020)} days | Post-2020: {len(trend_recent)} days")
    print(f"  Note: Pre-2020 carry had fewer instruments (1-2 avg) vs post-2020 (8 avg)")

# =============================================================================
# PART 6: SURVIVORSHIP ADJUSTMENT
# =============================================================================

print("\n" + "=" * 90)
print("PART 6: SURVIVORSHIP ADJUSTMENT")
print("=" * 90)

# Calculate survivorship impact
# If LUNA and FTT were included at 10% weight each:
# - Each had catastrophic negative funding during collapse
# - Estimate: -50% loss per collapse event (funding can't exceed position value)
# - Total: 2 collapses × 10% weight × 50% loss = 10% drag
# - Over 3.8 years = 2.6%/year drag

survivor_one_time = 0.10  # 10% one-time loss from LUNA + FTT
years_recent = len(carry_recent) / DAYS_PER_YEAR
survivor_annual = survivor_one_time / years_recent

# To estimate impact on skew, we simulate adding two catastrophic events
# Each collapse would add a -10% to -20% day to the series
# This makes skew more negative

# Estimate: each collapse adds equivalent of a -5 sigma event
# Impact on skew: approximately -0.5 to -1.0

survivor_skew_penalty = 0.5  # Skew becomes more negative by this amount

print(f"""
Survivorship Bias Estimation:

  Missing tokens: LUNA, FTT (10% weight each if included)

  Impact:
    - One-time losses: ~{survivor_one_time*100:.0f}%
    - Annualized: ~{survivor_annual*100:.1f}%/year
    - Skew penalty: ~{survivor_skew_penalty:+.1f}

  Adjusted Carry Metrics:
    Raw Sharpe:  {carry_recent_stats['sharpe']:.2f} → Adj: {(carry_recent_stats['ann_return'] - survivor_annual) / carry_recent_stats['ann_vol']:.2f}
    Raw Skew:    {carry_recent_stats['skew']:+.2f} → Adj: {carry_recent_stats['skew'] - survivor_skew_penalty:+.2f}
    Raw Max DD:  {carry_recent_stats['max_dd']*100:.1f}% → Adj: {(carry_recent_stats['max_dd'] - survivor_one_time)*100:.1f}%
""")

carry_adj_skew = carry_recent_stats['skew'] - survivor_skew_penalty

# =============================================================================
# PART 7: CORRECTED ALLOCATION TABLE
# =============================================================================

print("\n" + "=" * 90)
print("PART 7: CORRECTED ALLOCATION TABLE")
print("=" * 90)

print(f"\n| Trend% | Carry% | Sharpe | Return |   Vol | RAW Skew | ADJ Skew | Max DD | 2022 Ret |")
print(f"|--------|--------|--------|--------|-------|----------|----------|--------|----------|")

allocations = []
trend_2022 = trend_aligned[y2022_mask]
carry_2022 = carry_aligned[y2022_mask]

for trend_pct in [100, 90, 80, 70, 60, 50, 40, 30, 20, 10, 0]:
    t_wt = trend_pct / 100
    c_wt = 1 - t_wt

    # Combined returns
    combined = t_wt * trend_recent + c_wt * carry_recent

    # Statistics
    stats = calc_stats(combined)
    raw_skew = stats['skew']

    # Adjusted skew: blend of trend skew (unchanged) and adjusted carry skew
    # The adjustment applies to the carry component
    adj_skew = t_wt * trend_recent_stats['skew'] + c_wt * carry_adj_skew
    # But skew isn't linear! Use actual calculation with penalty applied
    adj_skew = raw_skew - c_wt * survivor_skew_penalty

    # 2022 return
    combined_2022 = t_wt * trend_2022 + c_wt * carry_2022
    ret_2022 = (1 + combined_2022).cumprod().iloc[-1] - 1 if len(combined_2022) > 0 else 0

    allocations.append({
        'trend_pct': trend_pct,
        'carry_pct': 100 - trend_pct,
        'sharpe': stats['sharpe'],
        'ann_return': stats['ann_return'],
        'ann_vol': stats['ann_vol'],
        'raw_skew': raw_skew,
        'adj_skew': adj_skew,
        'max_dd': stats['max_dd'],
        'ret_2022': ret_2022
    })

    print(f"| {trend_pct:>6} | {100-trend_pct:>6} | {stats['sharpe']:>6.2f} | {stats['ann_return']*100:>5.1f}% | {stats['ann_vol']*100:>4.1f}% | {raw_skew:>+8.2f} | {adj_skew:>+8.2f} | {stats['max_dd']*100:>5.1f}% | {ret_2022*100:>+7.1f}% |")

# =============================================================================
# PART 8: FIND SKEW-NEUTRAL POINTS
# =============================================================================

print("\n" + "=" * 90)
print("PART 8: SKEW-NEUTRAL POINTS")
print("=" * 90)

# Find raw skew-neutral point
raw_skew_neutral = None
for i in range(len(allocations) - 1):
    s1 = allocations[i]['raw_skew']
    s2 = allocations[i + 1]['raw_skew']
    if s1 * s2 < 0:
        t1, t2 = allocations[i]['trend_pct'], allocations[i + 1]['trend_pct']
        raw_skew_neutral = t1 + (0 - s1) * (t2 - t1) / (s2 - s1)
        break

if raw_skew_neutral is None:
    closest = min(allocations, key=lambda x: abs(x['raw_skew']))
    raw_skew_neutral = closest['trend_pct']
    print(f"Raw skew: No zero crossing. Closest: {closest['trend_pct']}% (skew={closest['raw_skew']:+.2f})")
else:
    print(f"Raw skew-neutral: {raw_skew_neutral:.0f}% Trend / {100-raw_skew_neutral:.0f}% Carry")

# Find adjusted skew-neutral point
adj_skew_neutral = None
for i in range(len(allocations) - 1):
    s1 = allocations[i]['adj_skew']
    s2 = allocations[i + 1]['adj_skew']
    if s1 * s2 < 0:
        t1, t2 = allocations[i]['trend_pct'], allocations[i + 1]['trend_pct']
        adj_skew_neutral = t1 + (0 - s1) * (t2 - t1) / (s2 - s1)
        break

if adj_skew_neutral is None:
    closest = min(allocations, key=lambda x: abs(x['adj_skew']))
    adj_skew_neutral = closest['trend_pct']
    print(f"Adjusted skew: No zero crossing. Closest: {closest['trend_pct']}% (adj_skew={closest['adj_skew']:+.2f})")
else:
    print(f"Adjusted skew-neutral: {adj_skew_neutral:.0f}% Trend / {100-adj_skew_neutral:.0f}% Carry")

print(f"\n→ Use ADJUSTED skew-neutral for final recommendation: {adj_skew_neutral:.0f}% Trend / {100-adj_skew_neutral:.0f}% Carry")

# =============================================================================
# PART 9: COST SUMMARY (Already Applied to Returns)
# =============================================================================

print("\n" + "=" * 90)
print("PART 9: COST SUMMARY (Already Applied to Returns)")
print("=" * 90)

print(f"""
Costs Applied to Trend:
  Trading costs:     -{TREND_ANNUAL_TRADE_COST*100:.1f}%/year
  ────────────────────────────────
  Total trend drag:  -{TREND_ANNUAL_TRADE_COST*100:.1f}%/year

Costs Applied to Carry:
  Leverage costs:    -{CARRY_ANNUAL_LEVERAGE_COST*100:.1f}%/year
  Other risks:       -{CARRY_ANNUAL_OTHER_COSTS*100:.1f}%/year
    (exchange 2.6% + basis blowout 1.5% + margin 0.5% + regime 0.5%)
  Trading costs:     -{CARRY_ANNUAL_TRADE_COST*100:.1f}%/year
  ────────────────────────────────
  Total carry drag:  -{CARRY_TOTAL_ANNUAL_COST*100:.1f}%/year

Note: All statistics below are NET of these costs.
""")

# =============================================================================
# PART 10: SPEED LIMIT CHECK (Carver's 1/3 Rule)
# =============================================================================

print("\n" + "=" * 90)
print("PART 10: SPEED LIMIT CHECK (Carver's 1/3 Rule)")
print("=" * 90)

def check_speed_limit(annual_costs, vol_target, expected_gross_sr, strategy_name):
    """
    Check if trading costs are within Carver's "speed limit" (1/3 of expected gross SR).

    From Carver's "Leveraged Trading" and blog:
    - Cost SR = Annual Costs / Vol Target
    - Max Cost SR = Expected Gross SR / 3
    - If Cost SR > Max Cost SR, strategy may not be worth trading

    Note: Carver also found that "using all rules is consistently better, after costs,
    than excluding expensive rules" because the optimizer already penalizes costly rules.
    """
    cost_sr = annual_costs / vol_target
    max_cost_sr = expected_gross_sr / 3
    within_limit = cost_sr <= max_cost_sr
    excess = max(0, cost_sr - max_cost_sr)

    return {
        'strategy': strategy_name,
        'annual_costs': annual_costs,
        'vol_target': vol_target,
        'expected_gross_sr': expected_gross_sr,
        'cost_sr': cost_sr,
        'max_cost_sr': max_cost_sr,
        'within_limit': within_limit,
        'excess': excess
    }

def longest_drawdown_duration(returns, name=""):
    """
    Calculate the longest drawdown duration in days.

    A drawdown starts when cumulative returns fall below the previous peak
    and ends when a new peak is reached.
    """
    returns = returns.dropna()
    if len(returns) < 20:
        return {'duration_days': 0, 'start': None, 'end': None, 'depth': 0}

    cum = (1 + returns).cumprod()
    running_max = cum.cummax()
    drawdown = (cum - running_max) / running_max

    # Find drawdown periods
    in_drawdown = drawdown < 0

    # Find start and end of each drawdown period
    longest_duration = 0
    longest_start = None
    longest_end = None
    longest_depth = 0

    current_start = None
    current_depth = 0

    for i, (date, is_dd) in enumerate(in_drawdown.items()):
        if is_dd and current_start is None:
            current_start = date
            current_depth = drawdown.iloc[i]
        elif is_dd and current_start is not None:
            current_depth = min(current_depth, drawdown.iloc[i])
        elif not is_dd and current_start is not None:
            # Drawdown ended
            duration = (date - current_start).days
            if duration > longest_duration:
                longest_duration = duration
                longest_start = current_start
                longest_end = date
                longest_depth = current_depth
            current_start = None
            current_depth = 0

    # Check if still in drawdown at end
    if current_start is not None:
        duration = (returns.index[-1] - current_start).days
        if duration > longest_duration:
            longest_duration = duration
            longest_start = current_start
            longest_end = returns.index[-1]
            longest_depth = current_depth

    return {
        'duration_days': longest_duration,
        'start': longest_start,
        'end': longest_end,
        'depth': longest_depth
    }

# Estimate gross Sharpe ratios (before costs)
# Trend: Add back trading costs to get gross
trend_gross_return = trend_recent_stats['ann_return'] + TREND_ANNUAL_TRADE_COST
trend_gross_sr = trend_gross_return / trend_recent_stats['ann_vol']

# Carry: Add back all costs to get gross
carry_gross_return = carry_recent_stats['ann_return'] + CARRY_TOTAL_ANNUAL_COST
carry_gross_sr = carry_gross_return / carry_recent_stats['ann_vol']

# Run speed limit checks
trend_check = check_speed_limit(
    TREND_ANNUAL_TRADE_COST, TREND_VOL_TARGET, trend_gross_sr, "Trend"
)
carry_check = check_speed_limit(
    CARRY_TOTAL_ANNUAL_COST, CARRY_VOL_TARGET, carry_gross_sr, "Carry"
)

print(f"""
Speed Limit Rule (from Carver's "Leveraged Trading"):
  "Costs should be max 1/3 of expected gross Sharpe Ratio"
  Cost SR = Annual Costs / Vol Target
  Max Cost SR = Expected Gross SR / 3

┌──────────┬────────────┬────────────┬────────────┬────────────┬────────────┬────────┐
│ Strategy │ Ann. Costs │ Vol Target │ Gross SR   │ Cost SR    │ Max Cost   │ Status │
├──────────┼────────────┼────────────┼────────────┼────────────┼────────────┼────────┤
│ Trend    │ {TREND_ANNUAL_TRADE_COST*100:>9.1f}% │ {TREND_VOL_TARGET*100:>9.0f}% │ {trend_gross_sr:>10.2f} │ {trend_check['cost_sr']:>10.3f} │ {trend_check['max_cost_sr']:>10.3f} │ {'✓ OK' if trend_check['within_limit'] else '✗ OVER':>6} │
│ Carry    │ {CARRY_TOTAL_ANNUAL_COST*100:>9.1f}% │ {CARRY_VOL_TARGET*100:>9.1f}% │ {carry_gross_sr:>10.2f} │ {carry_check['cost_sr']:>10.3f} │ {carry_check['max_cost_sr']:>10.3f} │ {'✓ OK' if carry_check['within_limit'] else '✗ OVER':>6} │
└──────────┴────────────┴────────────┴────────────┴────────────┴────────────┴────────┘
""")

if not carry_check['within_limit']:
    print(f"""
  ⚠ CARRY EXCEEDS SPEED LIMIT by {carry_check['excess']:.3f} SR

  Note from Carver's blog: "Using all rules is consistently better, after costs,
  than excluding expensive rules" because the optimizer already penalizes costly
  rules. The difference is only 1-2 SR basis points in costs but 5-12 basis
  points in gross performance. Consider this when deciding whether to trade carry.
""")

# =============================================================================
# PART 10b: LONGEST DRAWDOWN DURATION ANALYSIS
# =============================================================================

print("\n" + "-" * 90)
print("LONGEST DRAWDOWN DURATION ANALYSIS")
print("-" * 90)

trend_dd = longest_drawdown_duration(trend_recent, "Trend")
carry_dd = longest_drawdown_duration(carry_recent, "Carry")

# Combined at skew-neutral allocation
t_wt_temp = adj_skew_neutral / 100
c_wt_temp = 1 - t_wt_temp
combined_temp = t_wt_temp * trend_recent + c_wt_temp * carry_recent
combined_dd = longest_drawdown_duration(combined_temp, "Combined")

print(f"""
Longest Drawdown Duration (for vol target decisions):

┌──────────────────┬────────────────┬─────────────┬────────────────────────────────────┐
│ Strategy         │ Duration (days)│ Max Depth   │ Period                             │
├──────────────────┼────────────────┼─────────────┼────────────────────────────────────┤
│ Trend ({TREND_VOL_TARGET*100:.0f}% vol)  │ {trend_dd['duration_days']:>14} │ {trend_dd['depth']*100:>10.1f}% │ {str(trend_dd['start'].date()) if trend_dd['start'] else 'N/A':>12} to {str(trend_dd['end'].date()) if trend_dd['end'] else 'N/A':>12} │
│ Carry ({CARRY_VOL_TARGET*100:.0f}% vol) │ {carry_dd['duration_days']:>14} │ {carry_dd['depth']*100:>10.1f}% │ {str(carry_dd['start'].date()) if carry_dd['start'] else 'N/A':>12} to {str(carry_dd['end'].date()) if carry_dd['end'] else 'N/A':>12} │
│ Combined ({adj_skew_neutral:.0f}/{100-adj_skew_neutral:.0f})   │ {combined_dd['duration_days']:>14} │ {combined_dd['depth']*100:>10.1f}% │ {str(combined_dd['start'].date()) if combined_dd['start'] else 'N/A':>12} to {str(combined_dd['end'].date()) if combined_dd['end'] else 'N/A':>12} │
└──────────────────┴────────────────┴─────────────┴────────────────────────────────────┘

Vol Target Guidance (from Carver):
  - Full-Kelly:    vol = expected SR (risky, max growth)
  - Half-Kelly:    vol = SR / 2 (recommended for most traders)
  - Quarter-Kelly: vol = SR / 4 (for negative skew strategies)

  Current targets: Trend {TREND_VOL_TARGET*100:.0f}%, Carry {CARRY_VOL_TARGET*100:.1f}% (Carry = Trend / 2)

  Adjust vol targets based on your tolerance for drawdown duration.
  Lower vol target = shorter drawdowns but lower returns.
""")

# =============================================================================
# PART 10c: INERTIA BUFFER ANALYSIS
# =============================================================================

print("\n" + "=" * 90)
print("PART 10c: INERTIA BUFFER ANALYSIS")
print("=" * 90)

print("""
Buffer fitting procedure (Carver-consistent):
  1. Buffer bands centered around TARGET position (not current)
  2. Only trade if CURRENT position is outside the buffer band
  3. Trade to buffer EDGE (not optimal) to reduce turnover
  4. Use forecast method: constant buffer based on average position

CRYPTO-SPECIFIC: No contract rounding = buffer is ONLY inertia source
  - 10% is the BASELINE (not something to discover)
  - 0% and 5% shown for diagnostics only
  - Bias toward 20%+ since crypto has no natural rounding friction

Selection criteria:
  - Exclude buffers < 10%
  - Require >=15% turnover reduction vs 10% baseline
  - Sharpe within 5% of best in acceptable range
  - When in doubt, prefer larger buffer (crypto bias)
""")

# -----------------------------------------------------------------------------
# DIAGNOSTIC: Forecast scaling analysis (Trend sleeve)
# -----------------------------------------------------------------------------
print("\n--- FORECAST SCALING DIAGNOSTICS (Trend) ---")
print("  (Investigating why avg|forecast| may differ from target of 10)")

# Get instruments from system
instruments = system.get_instrument_list()

# Carver's reference scalars from his book
carver_scalars = {
    'ewmac8_32': 5.3, 'ewmac16_64': 3.75, 'ewmac32_128': 2.65, 'ewmac64_256': 1.87,
    'breakout10': 2.0, 'breakout20': 1.6, 'breakout40': 1.4, 'breakout80': 1.2
}

# Analyze individual rule forecasts for first instrument (BTC as reference)
ref_instr = 'BTC'
print(f"\n  Per-rule analysis for {ref_instr}:")
print(f"  {'Rule':<15} {'avg|scaled|':>12} {'avg|capped|':>12} {'cap_rate':>10} {'est_scalar':>12} {'carver':>8}")
print(f"  {'-'*15} {'-'*12} {'-'*12} {'-'*10} {'-'*12} {'-'*8}")

for rule in system.rules.trading_rules():
    try:
        # Get scaled forecast (before capping)
        scaled = system.forecastScaleCap.get_scaled_forecast(ref_instr, rule)
        # Get capped forecast
        capped = system.forecastScaleCap.get_capped_forecast(ref_instr, rule)
        # Get estimated scalar
        scalar = system.forecastScaleCap.get_forecast_scalar(ref_instr, rule)

        avg_scaled = scaled.abs().mean()
        avg_capped = capped.abs().mean()
        cap_rate = (scaled.abs() > 20).mean() * 100
        recent_scalar = scalar.iloc[-1]
        carver_val = carver_scalars.get(rule, 0)

        print(f"  {rule:<15} {avg_scaled:>12.2f} {avg_capped:>12.2f} {cap_rate:>9.1f}% {recent_scalar:>12.2f} {carver_val:>8.2f}")
    except Exception as e:
        pass

# Combined forecast analysis for multiple instruments
print(f"\n  Combined forecast (avg across rules) per instrument:")
for instr in instruments[:6]:  # First 6 instruments
    try:
        combined = system.combForecast.get_combined_forecast(instr)
        avg_combined = combined.abs().mean()
        recent_combined = combined.iloc[-250:].abs().mean()  # Recent 1 year
        print(f"    {instr}: all_time={avg_combined:.2f}, recent={recent_combined:.2f}")
    except Exception:
        pass

# Overall portfolio average forecast
all_forecasts = []
for instr in instruments:
    try:
        fc = system.combForecast.get_combined_forecast(instr)
        if len(fc) > 0:
            all_forecasts.append(fc)
    except Exception:
        pass

if all_forecasts:
    forecast_df = pd.concat(all_forecasts, axis=1)
    avg_forecast = forecast_df.mean(axis=1)
    overall_avg = avg_forecast.abs().mean()
    recent_avg = avg_forecast.iloc[-500:].abs().mean()

    print(f"\n  Portfolio-level summary:")
    print(f"    Target avg|forecast|: 10.00")
    print(f"    Actual avg|forecast| (all time): {overall_avg:.2f}")
    print(f"    Actual avg|forecast| (recent 500d): {recent_avg:.2f}")
    print(f"    Shortfall: {(1 - overall_avg/10)*100:.1f}%")

    # Time evolution
    rolling_avg_fc = avg_forecast.abs().rolling(250).mean()
    print(f"\n  Rolling 250d avg|forecast| by year:")
    for year in range(2015, 2026):
        year_data = rolling_avg_fc[rolling_avg_fc.index.year == year]
        if len(year_data) > 0:
            print(f"      {year}: {year_data.mean():.2f}")

# Buffer grid to test
BUFFER_GRID = [0.0, 0.05, 0.10, 0.20, 0.30, 0.40]

# Cost per trade (for turnover -> cost calculation)
TREND_COST_PER_TRADE = 0.0015  # 0.15% round-trip
CARRY_COST_PER_TRADE = 0.0015  # 0.15% round-trip (trading only, leverage costs fixed)

# -----------------------------------------------------------------------------
# CARRY SLEEVE - Buffer Analysis (Carver's forecast method)
# -----------------------------------------------------------------------------

print("\n--- CARRY SLEEVE - Buffer Analysis (Carver's approach) ---")
print("(Using forecast method: constant buffer based on average position)")

# Calculate average position for forecast method (constant buffer width)
average_carry_position = position_scale.abs().mean()
print(f"  Average position scale: {average_carry_position:.2f}")

carry_buffer_results = []

for buffer_pct in BUFFER_GRID:
    # Apply Carver-consistent buffer with forecast method
    # - Bands centered around TARGET
    # - Trade to EDGE when outside band
    # - Constant buffer width based on average position
    buffered_scale = apply_position_buffer(
        position_scale,
        buffer_size=buffer_pct,
        average_position=average_carry_position,
        trade_to_edge=True
    )

    # Calculate returns with buffered positions
    buffered_funding_pnl = funding_aligned * buffered_scale
    buffered_basis_pnl = spot_aligned * UNHEDGED_EXPOSURE * buffered_scale
    buffered_carry_gross = (buffered_funding_pnl + buffered_basis_pnl).dropna()

    # Apply costs (leverage costs are fixed, but trading costs depend on turnover)
    turnover = calculate_turnover(buffered_scale)
    trading_cost = turnover * CARRY_COST_PER_TRADE
    total_cost = CARRY_ANNUAL_LEVERAGE_COST + CARRY_ANNUAL_OTHER_COSTS + trading_cost
    buffered_carry = buffered_carry_gross - total_cost / DAYS_PER_YEAR

    # Calculate stats on recent window
    buffered_recent = buffered_carry[buffered_carry.index >= '2020-01-01']
    if len(buffered_recent) > 20:
        stats = calc_stats(buffered_recent)
        dd_info = longest_drawdown_duration(buffered_recent)

        carry_buffer_results.append({
            'buffer_pct': buffer_pct,
            'turnover': turnover,
            'trading_cost': trading_cost,
            'total_cost': total_cost,
            'dd_duration': dd_info['duration_days'],
            'sharpe': stats['sharpe'],
            'ann_return': stats['ann_return'],
            'ann_vol': stats['ann_vol']
        })

print(f"\n┌─────────────┬──────────────┬────────────────┬────────────────┬──────────────┐")
print(f"│ Buffer      │ Turnover/yr  │ Costs/yr       │ Max DD Dur     │ Net Sharpe   │")
print(f"├─────────────┼──────────────┼────────────────┼────────────────┼──────────────┤")
for r in carry_buffer_results:
    # Mark 0% and 5% as diagnostics
    if r['buffer_pct'] < 0.10:
        label = f"{r['buffer_pct']*100:>3.0f}%*"
    elif r['buffer_pct'] == 0.10:
        label = f"{r['buffer_pct']*100:>3.0f}% BASE"
    else:
        label = f"{r['buffer_pct']*100:>5.0f}%   "
    print(f"│ {label:>9} │ {r['turnover']:>11.1f}x │ {r['total_cost']*100:>13.1f}% │ {r['dd_duration']:>13}d │ {r['sharpe']:>12.2f} │")
print(f"└─────────────┴──────────────┴────────────────┴────────────────┴──────────────┘")
print(f"  * = diagnostic only (not considered for selection)")

# Select optimal buffer using Carver-style regularization with crypto bias
carry_recommended, carry_reason = select_optimal_buffer(
    carry_buffer_results,
    sharpe_tolerance=0.05,
    turnover_threshold=0.15,
    strategy_name="Carry"
)

print(f"\nCarry recommended buffer: {carry_recommended*100:.0f}%")
print(f"  {carry_reason}")

# -----------------------------------------------------------------------------
# TREND SLEEVE - Buffer Analysis (Carver's forecast method)
# -----------------------------------------------------------------------------

print("\n--- TREND SLEEVE - Buffer Analysis (Carver's approach) ---")
print("(Using pysystemtrade baseline turnover with estimated buffer reductions)")

trend_buffer_results = []

# Get instruments and weights
instruments = system.get_instrument_list()
instr_weights = system.portfolio.get_instrument_weights()

# Step 1: Get ACTUAL baseline turnover from pysystemtrade (authoritative source)
# This uses pysystemtrade's proper turnover calculation which normalizes correctly
baseline_turnover = 0
print(f"\n  Per-instrument turnover (from pysystemtrade):")
for instr in instruments:
    try:
        instr_turnover = system.accounts.subsystem_turnover(instr)
        weight = instr_weights[instr].iloc[-1]
        weighted_turnover = instr_turnover * weight
        baseline_turnover += weighted_turnover
        print(f"    {instr}: {instr_turnover:.1f}x/yr × {weight:.1%} weight = {weighted_turnover:.1f}x contribution")
    except Exception:
        pass

print(f"\n  Portfolio baseline turnover: {baseline_turnover:.1f}x/year")

# Step 2: Get position at forecast=10 for buffer width calculation
idm = system.portfolio.get_instrument_diversification_multiplier()
all_positions_at_10 = []
all_actual_positions = []

for instr in instruments:
    try:
        avg_pos_subsystem = system.positionSize.get_average_position_at_subsystem_level(instr)
        actual_pos = system.portfolio.get_notional_position(instr)

        if len(avg_pos_subsystem) > 0:
            weight = instr_weights[instr].reindex(avg_pos_subsystem.index).ffill()
            idm_aligned = idm.reindex(avg_pos_subsystem.index).ffill()
            pos_at_10 = avg_pos_subsystem * weight * idm_aligned
            all_positions_at_10.append(pos_at_10)

        if len(actual_pos) > 0:
            all_actual_positions.append(actual_pos)
    except Exception:
        pass

# Get averaged positions for buffer simulation (to calculate RELATIVE reductions)
if all_positions_at_10:
    position_at_10_df = pd.concat(all_positions_at_10, axis=1)
    avg_position_at_10 = position_at_10_df.mean(axis=1)
else:
    avg_position_at_10 = pd.Series(dtype=float)

if all_actual_positions:
    position_df = pd.concat(all_actual_positions, axis=1)
    avg_position = position_df.mean(axis=1)
else:
    avg_position = pd.Series(dtype=float)

# Step 3: Simulate buffers and calculate RELATIVE turnover reductions
# (Absolute turnover from simulation is wrong, but relative reductions are useful)
if len(avg_position_at_10) > 0 and len(avg_position) > 0:
    average_trend_position = avg_position_at_10.abs().mean()

    # Get unbuffered simulation turnover (for calculating relative reductions)
    unbuffered_sim_turnover = calculate_turnover(avg_position)

    for buffer_pct in BUFFER_GRID:
        # Simulate buffered positions
        buffered_position = apply_position_buffer(
            avg_position,
            buffer_size=buffer_pct,
            average_position=average_trend_position,
            trade_to_edge=True
        )

        # Get simulated turnover (wrong absolute, but useful for relative reduction)
        sim_turnover = calculate_turnover(buffered_position)

        # Calculate relative reduction factor
        if unbuffered_sim_turnover > 0:
            reduction_factor = sim_turnover / unbuffered_sim_turnover
        else:
            reduction_factor = 1.0

        # Apply reduction factor to REAL baseline turnover
        estimated_turnover = baseline_turnover * reduction_factor

        # Calculate trading costs from estimated turnover
        trading_cost = estimated_turnover * TREND_COST_PER_TRADE

        # Calculate Sharpe with adjusted costs
        trend_returns_with_buffer = trend_returns_gross - trading_cost / DAYS_PER_YEAR

        trend_buffered_recent = trend_returns_with_buffer[trend_returns_with_buffer.index >= '2020-01-01']
        if len(trend_buffered_recent) > 20:
            stats = calc_stats(trend_buffered_recent)
            dd_info = longest_drawdown_duration(trend_buffered_recent)

            trend_buffer_results.append({
                'buffer_pct': buffer_pct,
                'turnover': estimated_turnover,
                'reduction_pct': (1 - reduction_factor) * 100,
                'trading_cost': trading_cost,
                'dd_duration': dd_info['duration_days'],
                'sharpe': stats['sharpe'],
                'ann_return': stats['ann_return'],
                'ann_vol': stats['ann_vol']
            })

print(f"\n┌─────────────┬──────────────┬────────────────┬────────────────┬──────────────┐")
print(f"│ Buffer      │ Turnover/yr  │ Costs/yr       │ Max DD Dur     │ Net Sharpe   │")
print(f"├─────────────┼──────────────┼────────────────┼────────────────┼──────────────┤")
for r in trend_buffer_results:
    # Mark 0% and 5% as diagnostics
    if r['buffer_pct'] < 0.10:
        label = f"{r['buffer_pct']*100:>3.0f}%*"
    elif r['buffer_pct'] == 0.10:
        label = f"{r['buffer_pct']*100:>3.0f}% BASE"
    else:
        label = f"{r['buffer_pct']*100:>5.0f}%   "
    print(f"│ {label:>9} │ {r['turnover']:>11.1f}x │ {r['trading_cost']*100:>13.2f}% │ {r['dd_duration']:>13}d │ {r['sharpe']:>12.2f} │")
print(f"└─────────────┴──────────────┴────────────────┴────────────────┴──────────────┘")
print(f"  * = diagnostic only (not considered for selection)")

# Select optimal buffer using Carver-style regularization with crypto bias
trend_recommended, trend_reason = select_optimal_buffer(
    trend_buffer_results,
    sharpe_tolerance=0.05,
    turnover_threshold=0.15,
    strategy_name="Trend"
)

print(f"\nTrend recommended buffer: {trend_recommended*100:.0f}%")
print(f"  {trend_reason}")

# -----------------------------------------------------------------------------
# SUMMARY
# -----------------------------------------------------------------------------

print(f"\n" + "-" * 90)
print("BUFFER ANALYSIS SUMMARY (Carver-consistent)")
print("-" * 90)

# Get baseline and selected results for comparison
trend_baseline = next((r for r in trend_buffer_results if r['buffer_pct'] == 0), None)
trend_selected = next((r for r in trend_buffer_results if r['buffer_pct'] == trend_recommended), None)
carry_baseline = next((r for r in carry_buffer_results if r['buffer_pct'] == 0), None)
carry_selected = next((r for r in carry_buffer_results if r['buffer_pct'] == carry_recommended), None)

print(f"""
┌────────────────┬─────────────────────┬─────────────────────┐
│                │ TREND SLEEVE        │ CARRY SLEEVE        │
├────────────────┼─────────────────────┼─────────────────────┤
│ Recommended    │ {trend_recommended*100:>17.0f}% │ {carry_recommended*100:>17.0f}% │
│ Turnover at    │ {trend_selected['turnover'] if trend_selected else 0:>16.1f}x │ {carry_selected['turnover'] if carry_selected else 0:>16.1f}x │
│ Costs at       │ {trend_selected['trading_cost']*100 if trend_selected else 0:>15.2f}% │ {carry_selected['total_cost']*100 if carry_selected else 0:>15.1f}% │
│ Net Sharpe     │ {trend_selected['sharpe'] if trend_selected else 0:>18.2f} │ {carry_selected['sharpe'] if carry_selected else 0:>18.2f} │
└────────────────┴─────────────────────┴─────────────────────┘

CRYPTO-SPECIFIC RATIONALE:
  Unlike futures (discrete contracts), crypto has NO natural rounding friction.
  Buffer is the ONLY source of position inertia.
  → 10% is the BASELINE (Carver's default), not something to discover
  → Bias toward larger buffers (20%+) when Sharpe impact is minimal

Selection Criteria:
  - Exclude buffers < 10% (diagnostics only)
  - 10% is baseline for comparison
  - Require >=15% turnover reduction vs 10% to justify increase
  - Sharpe within 5% of best in acceptable range
  - Crypto bias: prefer 20% when Sharpe is within 2% of 10%

Trend: {trend_reason}
Carry: {carry_reason}
""")

# =============================================================================
# PART 11: FINAL NET-OF-COSTS ESTIMATES
# =============================================================================

print("\n" + "=" * 90)
print("PART 11: FINAL NET-OF-COSTS ESTIMATES")
print("=" * 90)

# Calculate at adjusted skew-neutral point
t_wt = adj_skew_neutral / 100
c_wt = 1 - t_wt

combined_final = t_wt * trend_recent + c_wt * carry_recent
final_stats = calc_stats(combined_final)

# Survivorship adjustment (still needed - not in daily costs)
carry_adj_return = carry_recent_stats['ann_return'] - survivor_annual

# 2022 stress test
combined_2022 = t_wt * trend_2022 + c_wt * carry_2022
ret_2022 = (1 + combined_2022).cumprod().iloc[-1] - 1

print(f"""
All figures are NET of costs (already deducted from returns):

| Metric              | Net of Costs | After Survivorship Adj |
|---------------------|--------------|------------------------|
| Trend Sharpe        | {trend_recent_stats['sharpe']:>12.2f} | {trend_recent_stats['sharpe']:>22.2f} |
| Carry Sharpe        | {carry_recent_stats['sharpe']:>12.2f} | {carry_adj_return/carry_recent_stats['ann_vol']:>22.2f} |
| Combined ({adj_skew_neutral:.0f}/{100-adj_skew_neutral:.0f})   | {final_stats['sharpe']:>12.2f} | {(t_wt*trend_recent_stats['ann_return'] + c_wt*carry_adj_return)/final_stats['ann_vol']:>22.2f} |
""")

# =============================================================================
# PART 12: FINAL RECOMMENDATION
# =============================================================================

print("\n" + "=" * 90)
print("PART 12: FINAL RECOMMENDATION")
print("=" * 90)

# Calculate final adjusted metrics
final_adj_return = t_wt * trend_recent_stats['ann_return'] + c_wt * carry_adj_return
final_adj_sharpe = final_adj_return / final_stats['ann_vol']
honest_maxdd = abs(final_stats['max_dd']) + c_wt * survivor_one_time + 0.05

print(f"""
══════════════════════════════════════════════════════════════════════════════════════
FINAL RECOMMENDATION (Using Adjusted Skew-Neutral Point)
══════════════════════════════════════════════════════════════════════════════════════

ALLOCATION:
  Trend: {adj_skew_neutral:.0f}%
  Carry: {100-adj_skew_neutral:.0f}%

EXPECTED PERFORMANCE (NET OF COSTS):
                    Net of Costs    After Survivorship Adj
  Sharpe:           {final_stats['sharpe']:>10.2f}    {final_adj_sharpe:>10.2f}
  Annual Return:    {final_stats['ann_return']*100:>9.1f}%    {final_adj_return*100:>9.1f}%
  Annual Vol:       {final_stats['ann_vol']*100:>9.1f}%    {final_stats['ann_vol']*100:>9.1f}%
  Portfolio Skew:   {final_stats['skew']:>+9.2f}

STRESS TEST:
  2022 Return:      {ret_2022*100:>+5.1f}%
  Max Drawdown:     {final_stats['max_dd']*100:>5.1f}%
  Honest Max DD:    ~{honest_maxdd*100:.0f}% (includes survivorship adjustment)

VOL TARGETS VERIFIED:
  Trend: {trend_recent_stats['ann_vol']*100:.1f}% actual vs {TREND_VOL_TARGET*100:.0f}% target ({'✓' if abs(trend_recent_stats['ann_vol'] - TREND_VOL_TARGET) < 0.03 else '✗'})
  Carry: {carry_recent_stats['ann_vol']*100:.1f}% actual vs {CARRY_VOL_TARGET*100:.1f}% target ({'✓' if abs(carry_recent_stats['ann_vol'] - CARRY_VOL_TARGET) < 0.03 else '✗'})

POSITION SIZING FOR $10,000:
  Trend allocation: ${10000 * t_wt:,.0f}
  Carry allocation: ${10000 * c_wt:,.0f}
  Carry leverage: dynamic (rolling vol targeting to {CARRY_VOL_TARGET*100:.1f}% vol)
  Cash buffer: $3,000 (30%)

══════════════════════════════════════════════════════════════════════════════════════
""")

print(f"Strategy Correlation: {trend_recent.corr(carry_recent):.3f}")
