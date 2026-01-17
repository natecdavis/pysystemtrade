"""
FINAL BACKTEST V2 - CORRECTED CARRY VOL TARGETING
==================================================
Fixed: Carry vol targeting now based on funding rate volatility, not price volatility.
"""

import os
import sys
import numpy as np
import pandas as pd
from scipy.stats import skew, kurtosis

sys.path.insert(0, "/Users/nathanieldavis/pysystemtrade")

import logging
logging.disable(logging.CRITICAL)
import warnings
warnings.filterwarnings('ignore')

# =============================================================================
# CONFIGURATION
# =============================================================================

COMBINED_FUNDING_DIR = "/Users/nathanieldavis/pysystemtrade/data/crypto/funding_rates/combined"
STITCHED_DIR = "/Users/nathanieldavis/pysystemtrade/data/crypto/stitched"
PRICE_DIR = "/Users/nathanieldavis/pysystemtrade/data/crypto"

TREND_VOL_TARGET = 0.25   # Full Kelly - positive skew strategy
CARRY_VOL_TARGET = 0.125  # Half Kelly - negative skew strategy
DAYS_PER_YEAR = 365

print("=" * 90)
print("FINAL BACKTEST V2 - CORRECTED CARRY VOL TARGETING")
print("=" * 90)

# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def calc_stats(returns, name=""):
    """Calculate comprehensive statistics for a return series."""
    if len(returns) < 20:
        return None

    returns = returns.dropna()
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
        'total_return': cum.iloc[-1] - 1,
        'days': len(returns)
    }

def print_stats_table(stats_dict, title):
    """Print a formatted statistics table."""
    print(f"\n{title}")
    print(f"| {'Strategy':<8} | {'Sharpe':>7} | {'Return':>8} | {'Vol':>7} | {'Max DD':>8} | {'Skew':>7} | {'Kurt':>6} |")
    print(f"|{'-'*10}|{'-'*9}|{'-'*10}|{'-'*9}|{'-'*10}|{'-'*9}|{'-'*8}|")
    for name, stats in stats_dict.items():
        print(f"| {name:<8} | {stats['sharpe']:>7.2f} | {stats['ann_return']*100:>7.1f}% | {stats['ann_vol']*100:>6.1f}% | {stats['max_dd']*100:>7.1f}% | {stats['skew']:>+6.2f} | {stats['kurtosis']:>6.1f} |")

# =============================================================================
# PART 1: LOAD TREND STRATEGY RETURNS
# =============================================================================

print("\n" + "=" * 90)
print("PART 1: TREND STRATEGY")
print("=" * 90)

from sysdata.config.configdata import Config
from systems.provided.crypto_example.crypto_system import crypto_system

config = Config("systems.provided.crypto_example.crypto_config_diversified.yaml")
system = crypto_system(data_path=PRICE_DIR, config=config)

trend_account = system.accounts.portfolio()
trend_raw = trend_account.percent / 100
trend_raw.index = pd.to_datetime(trend_raw.index.date)

current_trend_vol = trend_raw.std() * np.sqrt(252)
trend_scale = TREND_VOL_TARGET / current_trend_vol
trend_returns = trend_raw * trend_scale

print(f"Trend strategy loaded:")
print(f"  Date range: {trend_returns.index.min().date()} to {trend_returns.index.max().date()}")
print(f"  Days: {len(trend_returns)}")
print(f"  Raw vol: {current_trend_vol*100:.1f}%, scaled to: {TREND_VOL_TARGET*100:.0f}%")
print(f"  Scale factor: {trend_scale:.2f}x")

# =============================================================================
# PART 2: LOAD AND SCALE CARRY STRATEGY
# =============================================================================

print("\n" + "=" * 90)
print("PART 2: CARRY STRATEGY")
print("=" * 90)

def load_funding(instrument):
    path = os.path.join(COMBINED_FUNDING_DIR, f"{instrument}_funding_combined.csv")
    if not os.path.exists(path):
        return pd.Series(dtype=float)
    df = pd.read_csv(path, parse_dates=['datetime'])
    df = df.set_index('datetime')
    df.index = pd.to_datetime(df.index.date)
    return df['fundingRate']

# Load all funding data
available_files = [f for f in os.listdir(COMBINED_FUNDING_DIR)
                   if f.endswith('_funding_combined.csv')]
carry_instruments = [f.replace('_funding_combined.csv', '') for f in available_files]
carry_instruments.sort()

print(f"Available carry instruments: {carry_instruments}")

all_funding = {}
for instr in carry_instruments:
    funding = load_funding(instr)
    if len(funding) >= 365:
        all_funding[instr] = funding
        print(f"  {instr}: {len(funding)} funding days, mean={funding.mean()*100:.4f}%/day")

n_carry = len(all_funding)
print(f"\nLoaded {n_carry} carry instruments")

# Create equal-weighted portfolio of raw funding rates
funding_df = pd.DataFrame(all_funding)
raw_carry_portfolio = funding_df.mean(axis=1).dropna()

print(f"\nRaw carry portfolio:")
print(f"  Date range: {raw_carry_portfolio.index.min().date()} to {raw_carry_portfolio.index.max().date()}")
print(f"  Days: {len(raw_carry_portfolio)}")

# Calculate raw portfolio stats
raw_carry_vol = raw_carry_portfolio.std() * np.sqrt(DAYS_PER_YEAR)
raw_carry_mean = raw_carry_portfolio.mean() * DAYS_PER_YEAR
print(f"  Raw mean: {raw_carry_mean*100:.2f}%/year")
print(f"  Raw vol: {raw_carry_vol*100:.2f}%/year")
print(f"  Raw Sharpe: {raw_carry_mean/raw_carry_vol:.2f}")

# Scale to target volatility
carry_scale = CARRY_VOL_TARGET / raw_carry_vol
carry_returns = raw_carry_portfolio * carry_scale

print(f"\nScaled carry portfolio:")
print(f"  Target vol: {CARRY_VOL_TARGET*100:.1f}%")
print(f"  Scale factor: {carry_scale:.1f}x (this is the effective leverage)")

# Verify
scaled_vol = carry_returns.std() * np.sqrt(DAYS_PER_YEAR)
scaled_mean = carry_returns.mean() * DAYS_PER_YEAR
print(f"  Scaled mean: {scaled_mean*100:.1f}%/year")
print(f"  Scaled vol: {scaled_vol*100:.1f}%/year")
print(f"  Scaled Sharpe: {scaled_mean/scaled_vol:.2f}")

# =============================================================================
# PART 3: ALIGN STRATEGIES AND DEFINE WINDOWS
# =============================================================================

print("\n" + "=" * 90)
print("PART 3: ALIGN DATA AND DEFINE WINDOWS")
print("=" * 90)

common_idx = trend_returns.index.intersection(carry_returns.index)
trend_aligned = trend_returns.loc[common_idx].dropna()
carry_aligned = carry_returns.loc[common_idx].dropna()

common_idx = trend_aligned.index.intersection(carry_aligned.index)
trend_aligned = trend_aligned.loc[common_idx]
carry_aligned = carry_aligned.loc[common_idx]

print(f"Aligned period: {common_idx.min().date()} to {common_idx.max().date()}")
print(f"Total days: {len(common_idx)}")

# Define windows
recent_mask = common_idx >= '2020-01-01'
y2022_mask = (common_idx >= '2022-01-01') & (common_idx <= '2022-12-31')

print(f"Full period: {len(common_idx)} days")
print(f"Recent (post-2020): {recent_mask.sum()} days")
print(f"2022 stress test: {y2022_mask.sum()} days")

# =============================================================================
# PART 4: FULL PERIOD STATISTICS
# =============================================================================

print("\n" + "=" * 90)
print("PART 4: FULL PERIOD STATISTICS")
print("=" * 90)

trend_full_stats = calc_stats(trend_aligned, "Trend")
carry_full_stats = calc_stats(carry_aligned, "Carry")

print_stats_table({
    'Trend': trend_full_stats,
    'Carry': carry_full_stats
}, f"Full Period ({common_idx.min().date()} to {common_idx.max().date()})")

corr_full = trend_aligned.corr(carry_aligned)
print(f"\nCorrelation: {corr_full:.3f}")

# =============================================================================
# PART 5: RECENT WINDOW STATISTICS (POST-2020)
# =============================================================================

print("\n" + "=" * 90)
print("PART 5: RECENT WINDOW STATISTICS (Post-2020)")
print("=" * 90)

trend_recent = trend_aligned[recent_mask]
carry_recent = carry_aligned[recent_mask]

trend_recent_stats = calc_stats(trend_recent, "Trend")
carry_recent_stats = calc_stats(carry_recent, "Carry")

print_stats_table({
    'Trend': trend_recent_stats,
    'Carry': carry_recent_stats
}, f"Recent Window (2020-01-01 to {common_idx.max().date()})")

corr_recent = trend_recent.corr(carry_recent)
print(f"\nCorrelation: {corr_recent:.3f}")

# =============================================================================
# PART 6: ALLOCATION TABLE
# =============================================================================

print("\n" + "=" * 90)
print("PART 6: ALLOCATION TABLE (Recent Window)")
print("=" * 90)

print(f"\n| {'Trend%':>6} | {'Carry%':>6} | {'Sharpe':>7} | {'Return':>8} | {'Vol':>7} | {'Skew':>7} | {'Max DD':>8} | {'2022 Ret':>9} |")
print(f"|{'-'*8}|{'-'*8}|{'-'*9}|{'-'*10}|{'-'*9}|{'-'*9}|{'-'*10}|{'-'*11}|")

allocations = []
for trend_pct in [100, 80, 70, 60, 50, 40, 30, 20, 10, 0]:
    t_wt = trend_pct / 100
    c_wt = 1 - t_wt

    combined = t_wt * trend_recent + c_wt * carry_recent
    stats = calc_stats(combined)

    # 2022 return
    trend_2022 = trend_aligned[y2022_mask]
    carry_2022 = carry_aligned[y2022_mask]
    combined_2022 = t_wt * trend_2022 + c_wt * carry_2022
    ret_2022 = (1 + combined_2022).cumprod().iloc[-1] - 1 if len(combined_2022) > 0 else 0

    allocations.append({
        'trend_pct': trend_pct,
        'carry_pct': 100 - trend_pct,
        **stats,
        'ret_2022': ret_2022
    })

    print(f"| {trend_pct:>6} | {100-trend_pct:>6} | {stats['sharpe']:>7.2f} | {stats['ann_return']*100:>7.1f}% | {stats['ann_vol']*100:>6.1f}% | {stats['skew']:>+6.2f} | {stats['max_dd']*100:>7.1f}% | {ret_2022*100:>+8.1f}% |")

# Find skew-neutral point
print("\n--- SKEW-NEUTRAL POINT ---")
skew_neutral_pct = None
for i in range(len(allocations) - 1):
    s1 = allocations[i]['skew']
    s2 = allocations[i + 1]['skew']
    if s1 * s2 < 0:  # Sign change
        t1 = allocations[i]['trend_pct']
        t2 = allocations[i + 1]['trend_pct']
        skew_neutral_pct = t1 + (0 - s1) * (t2 - t1) / (s2 - s1)
        print(f"Skew crosses zero between {t1}% and {t2}% trend")
        print(f"Interpolated skew-neutral: {skew_neutral_pct:.0f}% Trend / {100-skew_neutral_pct:.0f}% Carry")
        break

if skew_neutral_pct is None:
    closest = min(allocations, key=lambda x: abs(x['skew']))
    skew_neutral_pct = closest['trend_pct']
    print(f"No zero crossing. Closest to zero: {closest['trend_pct']}% Trend (skew = {closest['skew']:+.2f})")

# =============================================================================
# PART 7: SURVIVORSHIP BIAS ADJUSTMENT
# =============================================================================

print("\n" + "=" * 90)
print("PART 7: SURVIVORSHIP BIAS ADJUSTMENT")
print("=" * 90)

print(f"""
CARRY UNIVERSE: {n_carry} instruments
  {list(all_funding.keys())}

MISSING TOKENS (survivorship bias):
  - LUNA (Terra): Collapsed May 2022
  - FTT (FTX): Collapsed Nov 2022

IMPACT ESTIMATION:

If LUNA and FTT had been included at 1/{n_carry+2} = {1/(n_carry+2)*100:.1f}% weight each:

1. LUNA COLLAPSE (May 2022):
   - Death spiral funding: extreme negative (-10% to -50%/day)
   - At {carry_scale:.1f}x leverage: position loss of ~{min(carry_scale * 0.5, 1.0)*100:.0f}%
   - Portfolio impact: {1/(n_carry+2)*100:.1f}% × {min(carry_scale * 0.5, 1.0)*100:.0f}% = {100/(n_carry+2) * min(carry_scale * 0.5, 1.0):.1f}%

2. FTT COLLAPSE (Nov 2022):
   - Similar dynamics
   - Portfolio impact: ~{100/(n_carry+2) * min(carry_scale * 0.4, 1.0):.1f}%

3. TOTAL SURVIVORSHIP IMPACT:
   - One-time losses: ~{100/(n_carry+2) * min(carry_scale * 0.5, 1.0) + 100/(n_carry+2) * min(carry_scale * 0.4, 1.0):.1f}%
   - Spread over {len(carry_recent)/365:.1f} years
""")

# Calculate realistic survivorship adjustment
# With leverage, losses are amplified but capped at position size
luna_impact = 1/(n_carry+2) * min(carry_scale * 0.5, 1.0)  # 50% funding loss on Luna
ftt_impact = 1/(n_carry+2) * min(carry_scale * 0.4, 1.0)   # 40% funding loss on FTT
survivor_one_time = luna_impact + ftt_impact
survivor_annual = survivor_one_time / (len(carry_recent) / 365)

# Adjust carry metrics
carry_adj_return = carry_recent_stats['ann_return'] - survivor_annual
carry_adj_sharpe = carry_adj_return / carry_recent_stats['ann_vol']
carry_adj_maxdd = carry_recent_stats['max_dd'] - survivor_one_time
carry_adj_skew = carry_recent_stats['skew'] - 0.5  # Tail events make skew more negative

print(f"""SURVIVORSHIP-ADJUSTED CARRY METRICS:
                    Raw         Adjusted
  Sharpe:          {carry_recent_stats['sharpe']:>6.2f}       {carry_adj_sharpe:>6.2f}
  Annual Return:   {carry_recent_stats['ann_return']*100:>5.1f}%       {carry_adj_return*100:>5.1f}%
  Max Drawdown:    {carry_recent_stats['max_dd']*100:>5.1f}%      {carry_adj_maxdd*100:>5.1f}%
  Skew:            {carry_recent_stats['skew']:>+5.2f}       {carry_adj_skew:>+5.2f}
""")

# =============================================================================
# PART 8: OTHER UNMODELED RISKS
# =============================================================================

print("\n" + "=" * 90)
print("PART 8: OTHER UNMODELED RISKS")
print("=" * 90)

print(f"""
1. EXCHANGE RISK (FTX-style failure)
   ─────────────────────────────────
   Probability: ~8%/year (diversified across 3 exchanges)
   Impact if hit: 33% loss
   Expected annual drag: 8% × 33% = 2.6%/year

2. BASIS BLOWOUT
   ─────────────────────────────────
   Perp-spot divergence during stress
   Expected annual drag: ~1.5%/year

3. MARGIN/LIQUIDATION RISK
   ────────────────────────
   At {carry_scale:.1f}x leverage, margin buffer is moderate
   Half-Kelly helps but doesn't eliminate
   Expected annual drag: ~0.5%/year

4. REGIME CHANGE
   ───────────────
   Funding rate compression over time
   Expected annual drag: ~0.5%/year
""")

exchange_drag = 0.026
basis_drag = 0.015
margin_drag = 0.005
regime_drag = 0.005
total_other_drag = exchange_drag + basis_drag + margin_drag + regime_drag

print(f"""SUMMARY OF OTHER RISK ADJUSTMENTS:
  Exchange risk:     -{exchange_drag*100:.1f}%/year
  Basis blowout:     -{basis_drag*100:.1f}%/year
  Margin risk:       -{margin_drag*100:.1f}%/year
  Regime decay:      -{regime_drag*100:.1f}%/year
  ────────────────────────────────
  Total other drag:  -{total_other_drag*100:.1f}%/year
""")

# =============================================================================
# PART 9: FINAL HONEST ESTIMATES
# =============================================================================

print("\n" + "=" * 90)
print("PART 9: FINAL HONEST ESTIMATES")
print("=" * 90)

# Trend adjustments
trend_final_sharpe = trend_recent_stats['sharpe'] * 0.90  # 10% haircut

# Carry adjustments
carry_final_return = carry_adj_return - total_other_drag
carry_final_sharpe = carry_final_return / carry_recent_stats['ann_vol']
carry_final_maxdd = carry_adj_maxdd - 0.05

# Combined at skew-neutral
t_wt = skew_neutral_pct / 100
c_wt = 1 - t_wt

combined_raw = t_wt * trend_recent + c_wt * carry_recent
combined_raw_stats = calc_stats(combined_raw)

combined_adj_return = t_wt * trend_recent_stats['ann_return'] * 0.90 + c_wt * carry_final_return
combined_adj_vol = combined_raw_stats['ann_vol']
combined_adj_sharpe = combined_adj_return / combined_adj_vol

print(f"""
| Metric                          | Raw      | Survivor | Other    | Final    |
|                                 |          | Adjusted | Adjusted | Honest   |
|---------------------------------|----------|----------|----------|----------|
| Trend Sharpe                    | {trend_recent_stats['sharpe']:>8.2f} | {trend_recent_stats['sharpe']*0.95:>8.2f} | {trend_final_sharpe:>8.2f} | {trend_final_sharpe:>8.2f} |
| Carry Sharpe                    | {carry_recent_stats['sharpe']:>8.2f} | {carry_adj_sharpe:>8.2f} | {carry_final_sharpe:>8.2f} | {carry_final_sharpe:>8.2f} |
| Combined Sharpe ({skew_neutral_pct:.0f}/{100-skew_neutral_pct:.0f})          | {combined_raw_stats['sharpe']:>8.2f} | {'--':>8} | {'--':>8} | {combined_adj_sharpe:>8.2f} |
""")

# =============================================================================
# PART 10: FINAL RECOMMENDATION
# =============================================================================

print("\n" + "=" * 90)
print("PART 10: FINAL RECOMMENDATION")
print("=" * 90)

# 2022 stress test
trend_2022 = trend_aligned[y2022_mask]
carry_2022 = carry_aligned[y2022_mask]
combined_2022 = t_wt * trend_2022 + c_wt * carry_2022
ret_2022_raw = (1 + combined_2022).cumprod().iloc[-1] - 1
ret_2022_honest = ret_2022_raw - survivor_one_time

print(f"""
══════════════════════════════════════════════════════════════════════════════════════
FINAL RECOMMENDATION
══════════════════════════════════════════════════════════════════════════════════════

SKEW-NEUTRAL ALLOCATION:
  Trend: {skew_neutral_pct:.0f}%
  Carry: {100-skew_neutral_pct:.0f}%

EXPECTED PERFORMANCE (Honest Estimates):
  Sharpe Ratio:      {combined_adj_sharpe:.2f}
  Annual Return:     {combined_adj_return*100:.1f}%
  Annual Volatility: {combined_adj_vol*100:.1f}%
  Portfolio Skew:    ~0 (by design)

STRESS TEST:
  2022 Return (raw):    {ret_2022_raw*100:+.1f}%
  2022 Return (honest): {ret_2022_honest*100:+.1f}% (with LUNA/FTT adjustment)
  Max Drawdown (raw):   {combined_raw_stats['max_dd']*100:.1f}%
  Max Drawdown (honest): ~{(abs(combined_raw_stats['max_dd']) + survivor_one_time + 0.05)*100:.0f}%

KEY RISKS TO MONITOR:
  1. Exchange solvency - diversify across 3+ exchanges
  2. Funding rate compression - track average funding vs history
  3. Regulatory changes - especially US and EU restrictions
  4. Correlation spike in stress - strategies may correlate during crashes

POSITION SIZING FOR $10,000 ACCOUNT:

  Trend: ${10000 * t_wt:,.0f} at {TREND_VOL_TARGET*100:.0f}% vol target
  Carry: ${10000 * c_wt:,.0f} at {CARRY_VOL_TARGET*100:.1f}% vol target
         (requires {carry_scale:.1f}x leverage on funding rates)

  Effective leverage: ~{t_wt * 2 + c_wt * carry_scale:.1f}x
  Cash buffer: ${10000 * 0.30:,.0f} (30% for margin calls)

══════════════════════════════════════════════════════════════════════════════════════
""")

print(f"\nStrategy Correlation: {corr_recent:.3f}")
print(f"Carry Leverage: {carry_scale:.1f}x (to achieve {CARRY_VOL_TARGET*100:.1f}% vol from {raw_carry_vol*100:.1f}% raw)")
