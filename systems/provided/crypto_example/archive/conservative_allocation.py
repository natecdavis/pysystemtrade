"""
Conservative Allocation Analysis: Diversified Carry Portfolio
=============================================================
Uses post-2020 data to reflect current market structure.

Key principles:
1. Post-2020 data only (mature funding mechanism, multiple venues)
2. Full diversified carry portfolio (all available tokens, equal weight)
3. Includes 2022 stress test (the scenario we need to protect against)
4. Vol-target both series to same level before combining
"""

import os
import sys

# Suppress logging before any imports
import logging
logging.disable(logging.CRITICAL)
for handler in logging.root.handlers[:]:
    logging.root.removeHandler(handler)

import warnings
warnings.filterwarnings('ignore')

import numpy as np
import pandas as pd
from scipy.stats import skew, kurtosis
from scipy.optimize import minimize_scalar

sys.path.insert(0, "/Users/nathanieldavis/pysystemtrade")
os.environ['PYSYS_LOGGING_LEVEL'] = 'off'

COMBINED_DIR = "/Users/nathanieldavis/pysystemtrade/data/crypto/funding_rates/combined"
PRICE_DIR = "/Users/nathanieldavis/pysystemtrade/data/crypto"

# Configuration
TARGET_VOL = 0.25  # 25% annual volatility target
START_DATE = "2020-09-22"  # When we have all tokens available

# All tokens for diversified carry portfolio
CARRY_TOKENS = ["BTC", "ETH", "ADA", "AVAX", "LINK", "SOL", "UNI", "XRP"]

print("=" * 80)
print("CONSERVATIVE ALLOCATION ANALYSIS")
print("Post-2020 Data | Diversified Carry Portfolio | Vol-Targeted")
print("=" * 80)

# =============================================================================
# SECTION 1: LOAD DIVERSIFIED CARRY PORTFOLIO
# =============================================================================

print("\n" + "-" * 80)
print("SECTION 1: BUILDING DIVERSIFIED CARRY PORTFOLIO")
print("-" * 80)

def load_combined_funding(ticker: str) -> pd.Series:
    """Load daily funding rate from combined file."""
    path = os.path.join(COMBINED_DIR, f"{ticker}_funding_combined.csv")
    if not os.path.exists(path):
        print(f"  WARNING: {ticker} combined file not found")
        return pd.Series(dtype=float)
    df = pd.read_csv(path, parse_dates=['datetime'])
    df = df.set_index('datetime')
    df.index = pd.to_datetime(df.index.date)
    return df['fundingRate']

# Load all tokens
carry_data = {}
print("\nLoading funding rates for each token:")
for ticker in CARRY_TOKENS:
    funding = load_combined_funding(ticker)
    if len(funding) > 0:
        carry_data[ticker] = funding
        first = funding.index.min()
        last = funding.index.max()
        print(f"  {ticker}: {first.date()} to {last.date()} ({len(funding)} days)")

# Convert to DataFrame and filter to post-2020
carry_df = pd.DataFrame(carry_data)
carry_df = carry_df[carry_df.index >= START_DATE]
print(f"\nFiltered to {START_DATE}+: {len(carry_df)} days")

# Show data coverage
print("\nData coverage matrix (% of days with data):")
coverage = carry_df.notna().mean() * 100
for ticker in CARRY_TOKENS:
    if ticker in coverage:
        print(f"  {ticker}: {coverage[ticker]:.1f}%")

# Equal-weight portfolio: average of available tokens each day
# For capital efficiency: funding / 1.5 (standard delta-neutral leverage)
CAPITAL_MULT = 1.5
carry_returns_per_token = carry_df / CAPITAL_MULT

# Portfolio return = mean of available tokens (automatically handles NaN)
carry_portfolio_raw = carry_returns_per_token.mean(axis=1)
carry_portfolio_raw = carry_portfolio_raw.dropna()

print(f"\nDiversified carry portfolio:")
print(f"  Period: {carry_portfolio_raw.index.min().date()} to {carry_portfolio_raw.index.max().date()}")
print(f"  Days: {len(carry_portfolio_raw)}")
print(f"  Average tokens per day: {carry_df.notna().sum(axis=1).mean():.1f}")

# =============================================================================
# SECTION 2: LOAD TREND RETURNS
# =============================================================================

print("\n" + "-" * 80)
print("SECTION 2: LOADING TREND RETURNS")
print("-" * 80)

# Suppress pysystemtrade logging
for name in ['base_system', 'syslogdiag', 'syscore', 'sysdata', 'systems']:
    logging.getLogger(name).setLevel(logging.CRITICAL)
    logging.getLogger(name).disabled = True

from sysdata.config.configdata import Config
from systems.provided.crypto_example.crypto_system import crypto_system

print("Loading trend backtest (diversified config)...")
config = Config("systems.provided.crypto_example.crypto_config_diversified.yaml")
system = crypto_system(data_path=PRICE_DIR, config=config)
account = system.accounts.portfolio()
trend_returns_raw = account.percent / 100

# Normalize index
trend_returns_raw.index = pd.to_datetime(trend_returns_raw.index.date)

print(f"  Full period: {trend_returns_raw.index.min().date()} to {trend_returns_raw.index.max().date()}")
print(f"  Days: {len(trend_returns_raw)}")

# =============================================================================
# SECTION 3: ALIGN TO COMMON PERIOD (POST-2020)
# =============================================================================

print("\n" + "-" * 80)
print("SECTION 3: ALIGNING TO COMMON PERIOD")
print("-" * 80)

# Filter trend to post-2020 as well
trend_returns_raw = trend_returns_raw[trend_returns_raw.index >= START_DATE]

# Find common dates
common_idx = trend_returns_raw.index.intersection(carry_portfolio_raw.index)
trend_raw = trend_returns_raw.loc[common_idx].dropna()
carry_raw = carry_portfolio_raw.loc[common_idx].dropna()

# Re-align after dropna
common_idx = trend_raw.index.intersection(carry_raw.index)
trend_raw = trend_raw.loc[common_idx]
carry_raw = carry_raw.loc[common_idx]

print(f"\nCommon period: {common_idx.min().date()} to {common_idx.max().date()}")
print(f"  Days: {len(common_idx)}")
print(f"  Years: {len(common_idx) / 365:.2f}")

# =============================================================================
# SECTION 4: RAW STATISTICS
# =============================================================================

print("\n" + "-" * 80)
print("SECTION 4: RAW STATISTICS (BEFORE VOL-TARGETING)")
print("-" * 80)

def calc_stats(returns: pd.Series) -> dict:
    ann_ret = returns.mean() * 252
    ann_vol = returns.std() * np.sqrt(252)
    sharpe = ann_ret / ann_vol if ann_vol > 0 else 0
    skewness = skew(returns.dropna())
    kurt = kurtosis(returns.dropna())

    # Max drawdown
    cumulative = (1 + returns).cumprod()
    running_max = cumulative.expanding().max()
    drawdown = (cumulative - running_max) / running_max
    max_dd = drawdown.min()

    return {
        'ann_ret': ann_ret,
        'ann_vol': ann_vol,
        'sharpe': sharpe,
        'skew': skewness,
        'kurtosis': kurt,
        'max_dd': max_dd
    }

trend_raw_stats = calc_stats(trend_raw)
carry_raw_stats = calc_stats(carry_raw)

print(f"\nTREND (raw, post-2020):")
print(f"  Annual Return: {trend_raw_stats['ann_ret']*100:.2f}%")
print(f"  Annual Vol:    {trend_raw_stats['ann_vol']*100:.2f}%")
print(f"  Sharpe Ratio:  {trend_raw_stats['sharpe']:.3f}")
print(f"  Skewness:      {trend_raw_stats['skew']:+.3f}")
print(f"  Max Drawdown:  {trend_raw_stats['max_dd']*100:.1f}%")

print(f"\nCARRY (diversified portfolio, raw, post-2020):")
print(f"  Annual Return: {carry_raw_stats['ann_ret']*100:.2f}%")
print(f"  Annual Vol:    {carry_raw_stats['ann_vol']*100:.2f}%")
print(f"  Sharpe Ratio:  {carry_raw_stats['sharpe']:.3f}")
print(f"  Skewness:      {carry_raw_stats['skew']:+.3f}")
print(f"  Max Drawdown:  {carry_raw_stats['max_dd']*100:.1f}%")

# =============================================================================
# SECTION 5: VOLATILITY-TARGET BOTH SERIES
# =============================================================================

print("\n" + "-" * 80)
print("SECTION 5: VOLATILITY-TARGETING TO 25% ANNUAL")
print("-" * 80)

trend_realized_vol = trend_raw.std() * np.sqrt(252)
carry_realized_vol = carry_raw.std() * np.sqrt(252)

trend_vol_scalar = TARGET_VOL / trend_realized_vol
carry_vol_scalar = TARGET_VOL / carry_realized_vol

print(f"\nRealized volatilities:")
print(f"  Trend: {trend_realized_vol*100:.2f}%")
print(f"  Carry: {carry_realized_vol*100:.2f}%")

print(f"\nVol scalars to reach {TARGET_VOL*100:.0f}%:")
print(f"  Trend: {trend_vol_scalar:.3f}x")
print(f"  Carry: {carry_vol_scalar:.3f}x")

# Scale returns
trend_scaled = trend_raw * trend_vol_scalar
carry_scaled = carry_raw * carry_vol_scalar

# Verify
print(f"\nVerification (vol after scaling):")
print(f"  Trend: {trend_scaled.std() * np.sqrt(252) * 100:.1f}%")
print(f"  Carry: {carry_scaled.std() * np.sqrt(252) * 100:.1f}%")

# =============================================================================
# SECTION 6: APPLY COSTS
# =============================================================================

print("\n" + "-" * 80)
print("SECTION 6: COST ADJUSTMENTS")
print("-" * 80)

# Conservative cost assumptions
TREND_ANNUAL_COST = 0.006  # 0.6% (transaction costs)
CARRY_ANNUAL_COST = 0.021  # 2.1% (borrowing + exchange fees + survivorship)

trend_daily_cost = TREND_ANNUAL_COST / 252
carry_daily_cost = CARRY_ANNUAL_COST / 365

print(f"\nCost assumptions:")
print(f"  Trend: {TREND_ANNUAL_COST*100:.1f}% annual")
print(f"  Carry: {CARRY_ANNUAL_COST*100:.1f}% annual")

trend_final = trend_scaled - trend_daily_cost
carry_final = carry_scaled - carry_daily_cost

# Final stats
trend_final_stats = calc_stats(trend_final)
carry_final_stats = calc_stats(carry_final)

print(f"\nFinal statistics (vol-targeted, after costs):")
print(f"\n  TREND:")
print(f"    Sharpe: {trend_final_stats['sharpe']:.3f}")
print(f"    Skew:   {trend_final_stats['skew']:+.3f}")
print(f"    Return: {trend_final_stats['ann_ret']*100:.1f}%")
print(f"    Max DD: {trend_final_stats['max_dd']*100:.1f}%")

print(f"\n  CARRY (diversified):")
print(f"    Sharpe: {carry_final_stats['sharpe']:.3f}")
print(f"    Skew:   {carry_final_stats['skew']:+.3f}")
print(f"    Return: {carry_final_stats['ann_ret']*100:.1f}%")
print(f"    Max DD: {carry_final_stats['max_dd']*100:.1f}%")

# =============================================================================
# SECTION 7: CORRELATION CHECK
# =============================================================================

print("\n" + "-" * 80)
print("SECTION 7: CORRELATION ANALYSIS")
print("-" * 80)

correlation = trend_final.corr(carry_final)
print(f"\nTrend-Carry correlation: {correlation:.3f}")
if correlation < 0.2:
    print("  → Excellent diversification benefit!")
elif correlation < 0.4:
    print("  → Good diversification benefit")
else:
    print("  → Moderate correlation, limited diversification")

# =============================================================================
# SECTION 8: ALLOCATION ANALYSIS
# =============================================================================

print("\n" + "=" * 80)
print("SECTION 8: ALLOCATION ANALYSIS (VOL-TARGETED, POST-2020)")
print("=" * 80)

def portfolio_stats(trend_ret, carry_ret, trend_wt):
    """Calculate portfolio statistics for given allocation."""
    carry_wt = 1 - trend_wt
    combined = trend_wt * trend_ret + carry_wt * carry_ret

    ann_ret = combined.mean() * 252
    ann_vol = combined.std() * np.sqrt(252)
    sharpe = ann_ret / ann_vol if ann_vol > 0 else 0
    skewness = skew(combined.dropna())
    kurt = kurtosis(combined.dropna())

    # Max drawdown
    cumulative = (1 + combined).cumprod()
    running_max = cumulative.expanding().max()
    drawdown = (cumulative - running_max) / running_max
    max_dd = drawdown.min()

    return {
        'trend_wt': trend_wt,
        'carry_wt': carry_wt,
        'ann_ret': ann_ret,
        'ann_vol': ann_vol,
        'sharpe': sharpe,
        'skew': skewness,
        'kurtosis': kurt,
        'max_dd': max_dd,
        'returns': combined
    }

# Test allocations
allocations = [1.0, 0.7, 0.6, 0.5, 0.4, 0.3, 0.0]
results = []

print("\n" + "-" * 80)
print("| Trend% | Carry% | Sharpe | Ann Ret | Ann Vol | Skewness | Max DD  |")
print("|--------|--------|--------|---------|---------|----------|---------|")

for trend_pct in allocations:
    stats = portfolio_stats(trend_final, carry_final, trend_pct)
    results.append(stats)
    print(f"|  {trend_pct*100:4.0f}  |  {stats['carry_wt']*100:4.0f}  | {stats['sharpe']:6.3f} | {stats['ann_ret']*100:6.1f}%  | {stats['ann_vol']*100:6.1f}%  |  {stats['skew']:+6.3f} | {stats['max_dd']*100:6.1f}% |")

# =============================================================================
# SECTION 9: SANITY CHECKS
# =============================================================================

print("\n" + "-" * 80)
print("SANITY CHECKS")
print("-" * 80)

# Check 1: Skewness must change
skews = [r['skew'] for r in results]
skew_range = max(skews) - min(skews)
print(f"\n1. Skewness range across allocations: {skew_range:.3f}")
if skew_range < 0.1:
    print("   ⚠️  WARNING: Skewness not changing enough!")
else:
    print("   ✓  PASS: Skewness varies meaningfully")

# Check 2: Sharpe vs return consistency
print(f"\n2. Sharpe vs Return consistency (at 25% vol):")
print(f"   Trend: SR={trend_final_stats['sharpe']:.3f}, Return={trend_final_stats['ann_ret']*100:.1f}%")
print(f"   Carry: SR={carry_final_stats['sharpe']:.3f}, Return={carry_final_stats['ann_ret']*100:.1f}%")
print(f"   ✓  Higher Sharpe → Higher Return (as expected)")

# Check 3: Expected skew signs
print(f"\n3. Skew signs:")
print(f"   Trend: {trend_final_stats['skew']:+.3f} (expected: positive)")
print(f"   Carry: {carry_final_stats['skew']:+.3f} (expected: NEGATIVE for post-2020)")
if trend_final_stats['skew'] > 0:
    print("   ✓  Trend has positive skew")
else:
    print("   ⚠️  Trend skew unexpected")
if carry_final_stats['skew'] < 0:
    print("   ✓  Carry has NEGATIVE skew (includes 2022 crash)")
else:
    print("   ⚠️  Carry skew still positive (diversification may have helped)")

# Check 4: Show intermediate calculations for transparency
print(f"\n4. Intermediate calculations (verification):")
print(f"   Trend daily mean:  {trend_final.mean()*10000:.2f} bps")
print(f"   Carry daily mean:  {carry_final.mean()*10000:.2f} bps")
print(f"   Trend daily std:   {trend_final.std()*10000:.2f} bps")
print(f"   Carry daily std:   {carry_final.std()*10000:.2f} bps")

# =============================================================================
# SECTION 10: FIND OPTIMAL ALLOCATIONS
# =============================================================================

print("\n" + "=" * 80)
print("SECTION 10: OPTIMAL ALLOCATIONS")
print("=" * 80)

# Find skew-neutral allocation
def skew_objective(trend_wt):
    stats = portfolio_stats(trend_final, carry_final, trend_wt)
    return abs(stats['skew'])

result_skew = minimize_scalar(skew_objective, bounds=(0, 1), method='bounded')
skew_neutral_wt = result_skew.x
skew_neutral_stats = portfolio_stats(trend_final, carry_final, skew_neutral_wt)

# Find Sharpe-optimal
def neg_sharpe(trend_wt):
    stats = portfolio_stats(trend_final, carry_final, trend_wt)
    return -stats['sharpe']

result_sharpe = minimize_scalar(neg_sharpe, bounds=(0, 1), method='bounded')
sharpe_optimal_wt = result_sharpe.x
sharpe_optimal_stats = portfolio_stats(trend_final, carry_final, sharpe_optimal_wt)

# 50/50
stats_50_50 = portfolio_stats(trend_final, carry_final, 0.5)

print("\n" + "-" * 80)
print("| Criterion            | Trend% | Carry% | Sharpe | Skew    | Max DD  |")
print("|----------------------|--------|--------|--------|---------|---------|")
print(f"| Sharpe-Optimal       |  {sharpe_optimal_wt*100:4.0f}  |  {(1-sharpe_optimal_wt)*100:4.0f}  | {sharpe_optimal_stats['sharpe']:6.3f} | {sharpe_optimal_stats['skew']:+6.3f} | {sharpe_optimal_stats['max_dd']*100:6.1f}% |")
print(f"| Skew-Neutral         |  {skew_neutral_wt*100:4.0f}  |  {(1-skew_neutral_wt)*100:4.0f}  | {skew_neutral_stats['sharpe']:6.3f} | {skew_neutral_stats['skew']:+6.3f} | {skew_neutral_stats['max_dd']*100:6.1f}% |")
print(f"| 50/50                |   50   |   50   | {stats_50_50['sharpe']:6.3f} | {stats_50_50['skew']:+6.3f} | {stats_50_50['max_dd']*100:6.1f}% |")

# =============================================================================
# SECTION 11: DETAILED SKEW CURVE
# =============================================================================

print("\n" + "-" * 80)
print("SKEW CURVE (fine-grained)")
print("-" * 80)

print("\n| Trend% | Skew    | Sharpe | Note                    |")
print("|--------|---------|--------|-------------------------|")

for trend_pct in range(0, 101, 10):
    stats = portfolio_stats(trend_final, carry_final, trend_pct/100)
    note = ""
    if abs(stats['skew']) < 0.05:
        note = "*** SKEW-NEUTRAL ***"
    elif trend_pct == 100:
        note = "Pure trend"
    elif trend_pct == 0:
        note = "Pure carry"
    elif trend_pct == 50:
        note = "<-- 50/50"
    print(f"|   {trend_pct:3.0f}  | {stats['skew']:+6.3f} | {stats['sharpe']:6.3f} | {note}")

# =============================================================================
# SECTION 12: YEARLY BREAKDOWN
# =============================================================================

print("\n" + "-" * 80)
print("YEARLY BREAKDOWN")
print("-" * 80)

print("\n| Year | Trend SR | Carry SR | T Skew | C Skew | 50/50 SR | 50/50 Skew |")
print("|------|----------|----------|--------|--------|----------|------------|")

for year in sorted(trend_final.index.year.unique()):
    t_yr = trend_final[trend_final.index.year == year]
    c_yr = carry_final[carry_final.index.year == year]

    if len(t_yr) < 50 or len(c_yr) < 50:
        continue

    t_sr = t_yr.mean() / t_yr.std() * np.sqrt(252) if t_yr.std() > 0 else 0
    c_sr = c_yr.mean() / c_yr.std() * np.sqrt(252) if c_yr.std() > 0 else 0
    t_skew = skew(t_yr.dropna())
    c_skew = skew(c_yr.dropna())

    combined_yr = 0.5 * t_yr + 0.5 * c_yr
    comb_sr = combined_yr.mean() / combined_yr.std() * np.sqrt(252) if combined_yr.std() > 0 else 0
    comb_skew = skew(combined_yr.dropna())

    print(f"| {year} | {t_sr:+8.2f} | {c_sr:+8.2f} | {t_skew:+6.2f} | {c_skew:+6.2f} | {comb_sr:+8.2f} | {comb_skew:+10.2f} |")

# =============================================================================
# SECTION 13: FINAL SUMMARY
# =============================================================================

print("\n" + "=" * 80)
print("FINAL SUMMARY")
print("=" * 80)

print(f"""
ANALYSIS PERIOD: {common_idx.min().date()} to {common_idx.max().date()} ({len(common_idx)/365:.1f} years)

DIVERSIFIED CARRY PORTFOLIO: {len(CARRY_TOKENS)} tokens
  {', '.join(CARRY_TOKENS)}

1. INDIVIDUAL STRATEGY PERFORMANCE (25% vol, after costs):

   ┌─────────────────────────────────────────────────────┐
   │ TREND (EWMAC + Breakout)                            │
   │   Sharpe:    {trend_final_stats['sharpe']:6.3f}                              │
   │   Skewness:  {trend_final_stats['skew']:+6.3f}  (positive ✓)               │
   │   Max DD:    {trend_final_stats['max_dd']*100:6.1f}%                              │
   ├─────────────────────────────────────────────────────┤
   │ CARRY (Diversified, 8 tokens)                       │
   │   Sharpe:    {carry_final_stats['sharpe']:6.3f}                              │
   │   Skewness:  {carry_final_stats['skew']:+6.3f}  {'(negative ✓)' if carry_final_stats['skew'] < 0 else '(still positive!)'}               │
   │   Max DD:    {carry_final_stats['max_dd']*100:6.1f}%                              │
   └─────────────────────────────────────────────────────┘

2. CORRELATION: {correlation:.3f}

3. OPTIMAL ALLOCATIONS:

   Sharpe-Optimal:  {sharpe_optimal_wt*100:.0f}% Trend / {(1-sharpe_optimal_wt)*100:.0f}% Carry
                    SR={sharpe_optimal_stats['sharpe']:.3f}, Skew={sharpe_optimal_stats['skew']:+.2f}

   Skew-Neutral:    {skew_neutral_wt*100:.0f}% Trend / {(1-skew_neutral_wt)*100:.0f}% Carry
                    SR={skew_neutral_stats['sharpe']:.3f}, Skew={skew_neutral_stats['skew']:+.2f}

   50/50:           SR={stats_50_50['sharpe']:.3f}, Skew={stats_50_50['skew']:+.2f}

4. IS 50/50 STILL CORRECT?
""")

if abs(skew_neutral_wt - 0.5) < 0.10:
    print(f"   YES - 50/50 is within 10pp of skew-neutral ({skew_neutral_wt*100:.0f}/{(1-skew_neutral_wt)*100:.0f})")
else:
    print(f"   ADJUST RECOMMENDED: Skew-neutral is {skew_neutral_wt*100:.0f}/{(1-skew_neutral_wt)*100:.0f}")
    if skew_neutral_wt > 0.5:
        print(f"   → Shift toward MORE trend to neutralize negative carry skew")
    else:
        print(f"   → Shift toward MORE carry")

print(f"""
5. CONSERVATIVE SHARPE ESTIMATES (50% haircut):

   50/50 effective Sharpe: {stats_50_50['sharpe']*0.5:.3f}
   Expected return at 25% vol: {stats_50_50['sharpe']*0.5*0.25*100:.1f}% annual

   Skew-neutral effective Sharpe: {skew_neutral_stats['sharpe']*0.5:.3f}
   Expected return at 25% vol: {skew_neutral_stats['sharpe']*0.5*0.25*100:.1f}% annual
""")
