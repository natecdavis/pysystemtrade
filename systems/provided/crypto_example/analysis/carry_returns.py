"""
CARRY Returns Calculation
==========================
Extracted from final_backtest_v3_fixed.py (PART 2, lines 280-580)

Implements rolling vol-targeted carry strategy with:
- Funding rate collection (equal-weighted across instruments)
- Basis risk modeling (15% unhedged exposure)
- Walk-forward instrument inclusion (60-day minimum history)
- Rolling volatility targeting (12.5% target)
- Comprehensive cost model (12.4% annual: 7% leverage + 5.1% other + 0.3% trading)

Returns:
    Daily percentage returns series for CARRY strategy (2020+ typically)
"""

import os
import numpy as np
import pandas as pd
from scipy.stats import skew

# =============================================================================
# CONFIGURATION
# =============================================================================

# Get project root (this script is in systems/provided/crypto_example/)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, "..", "..", ".."))

# Data paths
COMBINED_FUNDING_DIR = os.path.join(PROJECT_ROOT, "data", "crypto", "funding_rates", "combined")
STITCHED_DIR = os.path.join(PROJECT_ROOT, "data", "crypto", "stitched")
PRICE_DIR = os.path.join(PROJECT_ROOT, "data", "crypto")

# Carry parameters
CARRY_VOL_TARGET = 0.125  # 12.5% (half of TREND's 25%)
VOL_LOOKBACK = 35         # 35-day rolling window for vol
CORR_LOOKBACK = 60        # 60-day rolling window for correlation
CARRY_MIN_HISTORY = max(CORR_LOOKBACK, VOL_LOOKBACK)  # = 60 days
UNHEDGED_EXPOSURE = 0.15  # 15% effective unhedged exposure for basis risk
DAYS_PER_YEAR = 365

# Cost parameters
CARRY_ANNUAL_LEVERAGE_COST = 0.07    # 7% (borrowing + margin opportunity cost)
CARRY_ANNUAL_OTHER_COSTS = 0.051     # 5.1% (exchange, basis blowout, margin, regime)
CARRY_ANNUAL_TRADE_COST = 0.003      # 0.3% (2 trades/year × 0.15% round-trip)
CARRY_TOTAL_ANNUAL_COST = CARRY_ANNUAL_LEVERAGE_COST + CARRY_ANNUAL_OTHER_COSTS + CARRY_ANNUAL_TRADE_COST


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def load_funding(instrument):
    """Load funding rate data for a single instrument."""
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


# =============================================================================
# MAIN CARRY CALCULATION
# =============================================================================

def get_carry_returns(start_date='2020-01-01', verbose=True):
    """
    Calculate CARRY strategy returns with rolling vol targeting and basis risk.

    Args:
        start_date: Start date for returns (default '2020-01-01')
        verbose: Print diagnostic information (default True)

    Returns:
        pd.Series: Daily percentage returns for CARRY strategy
    """

    if verbose:
        print("\n" + "=" * 90)
        print("CARRY STRATEGY - ROLLING VOL TARGETING")
        print("=" * 90)

    # Load all funding data
    available_files = [f for f in os.listdir(COMBINED_FUNDING_DIR) if f.endswith('_funding_combined.csv')]
    carry_instruments = sorted([f.replace('_funding_combined.csv', '') for f in available_files])

    if verbose:
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

    if verbose:
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

        if verbose:
            print(f"\nBasis risk calculation (walk-forward):")
            print(f"  Instruments with spot data: {len(spot_returns_dict)}")
            print(f"  Unhedged exposure: {UNHEDGED_EXPOSURE*100:.0f}%")
    else:
        avg_spot_return = pd.Series(0.0, index=raw_carry.index)
        if verbose:
            print(f"\nBasis risk: No spot data found, using funding-only returns")

    # Calculate effective vol that includes basis risk
    # The carry position has two components:
    # 1. Funding returns (what we collect)
    # 2. Basis returns (unhedged exposure × spot returns)
    #
    # Effective vol = sqrt(funding_vol² + basis_vol² + 2*corr*funding_vol*basis_vol)
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

    if verbose:
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
    carry_daily_cost = CARRY_TOTAL_ANNUAL_COST / DAYS_PER_YEAR
    carry_returns = carry_returns_gross - carry_daily_cost

    # Calculate funding-only returns (for comparison, also net of costs)
    funding_only_returns = (funding_pnl - carry_daily_cost).dropna()

    if verbose:
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

        # Position scale diagnostics
        valid_scale = position_scale.dropna()
        print(f"\n  Position scale statistics:")
        print(f"    Average: {valid_scale.mean():.2f}")
        print(f"    Median:  {valid_scale.median():.2f}")
        print(f"    Min/Max: {valid_scale.min():.2f} / {valid_scale.max():.2f}")

    # Filter to requested start date
    carry_returns_filtered = carry_returns[carry_returns.index >= start_date]

    if verbose:
        print(f"\nReturns summary ({start_date} onwards):")
        print(f"  Days: {len(carry_returns_filtered)}")
        print(f"  Date range: {carry_returns_filtered.index.min().date()} to {carry_returns_filtered.index.max().date()}")
        ann_ret = carry_returns_filtered.mean() * DAYS_PER_YEAR
        ann_vol = carry_returns_filtered.std() * np.sqrt(DAYS_PER_YEAR)
        sharpe = ann_ret / ann_vol if ann_vol > 0 else 0
        print(f"  Ann return: {ann_ret*100:.2f}%")
        print(f"  Ann vol: {ann_vol*100:.2f}%")
        print(f"  Sharpe: {sharpe:.2f}")

    return carry_returns_filtered


# =============================================================================
# MAIN (for testing)
# =============================================================================

if __name__ == "__main__":
    # Test extraction
    returns = get_carry_returns(start_date='2020-01-01', verbose=True)
    print("\n" + "=" * 90)
    print(f"CARRY extraction complete: {len(returns)} daily returns from {returns.index.min().date()} to {returns.index.max().date()}")
    print("=" * 90)
