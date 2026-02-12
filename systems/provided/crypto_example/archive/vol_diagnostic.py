"""
Carry Volatility Diagnostic
============================
Diagnosing why carry vol is 1.1% but max drawdown is -57%
"""

import os
import sys
import logging
logging.disable(logging.CRITICAL)

import warnings
warnings.filterwarnings('ignore')

import numpy as np
import pandas as pd
from scipy.stats import skew, kurtosis

sys.path.insert(0, "/Users/nathanieldavis/pysystemtrade")

COMBINED_DIR = "/Users/nathanieldavis/pysystemtrade/data/crypto/funding_rates/combined"
START_DATE = "2020-09-22"
CARRY_TOKENS = ["BTC", "ETH", "ADA", "AVAX", "LINK", "SOL", "UNI", "XRP"]
CAPITAL_MULT = 1.5

print("=" * 80)
print("CARRY VOLATILITY DIAGNOSTIC")
print("=" * 80)

# =============================================================================
# SECTION 1: LOAD DATA EXACTLY AS IN conservative_allocation.py
# =============================================================================

def load_combined_funding(ticker: str) -> pd.Series:
    path = os.path.join(COMBINED_DIR, f"{ticker}_funding_combined.csv")
    if not os.path.exists(path):
        return pd.Series(dtype=float)
    df = pd.read_csv(path, parse_dates=['datetime'])
    df = df.set_index('datetime')
    df.index = pd.to_datetime(df.index.date)
    return df['fundingRate']

carry_data = {}
for ticker in CARRY_TOKENS:
    funding = load_combined_funding(ticker)
    if len(funding) > 0:
        carry_data[ticker] = funding

carry_df = pd.DataFrame(carry_data)
carry_df = carry_df[carry_df.index >= START_DATE]

# This is exactly how carry_raw is calculated
carry_returns_per_token = carry_df / CAPITAL_MULT
carry_raw = carry_returns_per_token.mean(axis=1).dropna()

print(f"\nData period: {carry_raw.index.min().date()} to {carry_raw.index.max().date()}")
print(f"Days: {len(carry_raw)}")
print(f"Years: {len(carry_raw) / 365:.2f}")

# =============================================================================
# SECTION 2: EXACT VOLATILITY CALCULATION (how it's done now)
# =============================================================================

print("\n" + "=" * 80)
print("SECTION 2: CURRENT VOLATILITY CALCULATION")
print("=" * 80)

daily_std = carry_raw.std()
annual_vol = daily_std * np.sqrt(252)

print(f"\nCurrent calculation:")
print(f"  carry_raw.std() = {daily_std:.6f} ({daily_std*100:.4f}%)")
print(f"  Annual vol = std * sqrt(252) = {annual_vol:.4f} ({annual_vol*100:.2f}%)")

# =============================================================================
# SECTION 3: DETAILED RETURN STATISTICS
# =============================================================================

print("\n" + "=" * 80)
print("SECTION 3: DETAILED RETURN STATISTICS")
print("=" * 80)

print(f"\nDaily return statistics:")
print(f"  Mean:   {carry_raw.mean():.6f} ({carry_raw.mean()*100:.4f}%)")
print(f"  Std:    {carry_raw.std():.6f} ({carry_raw.std()*100:.4f}%)")
print(f"  Min:    {carry_raw.min():.6f} ({carry_raw.min()*100:.4f}%)")
print(f"  Max:    {carry_raw.max():.6f} ({carry_raw.max()*100:.4f}%)")
print(f"  Median: {carry_raw.median():.6f} ({carry_raw.median()*100:.4f}%)")

print(f"\nHigher moments:")
print(f"  Skewness:  {skew(carry_raw):.2f}")
print(f"  Kurtosis:  {kurtosis(carry_raw):.2f}")

# =============================================================================
# SECTION 4: WORST DAYS ANALYSIS
# =============================================================================

print("\n" + "=" * 80)
print("SECTION 4: WORST 20 DAYS")
print("=" * 80)

worst_days = carry_raw.nsmallest(20)
print(f"\n{'Date':<12} {'Return':>10} {'Sigmas':>10}")
print("-" * 34)
for date, ret in worst_days.items():
    sigmas = ret / daily_std
    print(f"{date.strftime('%Y-%m-%d'):<12} {ret*100:>9.2f}% {sigmas:>10.1f}σ")

# =============================================================================
# SECTION 5: THE SIGMA PROBLEM
# =============================================================================

print("\n" + "=" * 80)
print("SECTION 5: THE SIGMA PROBLEM")
print("=" * 80)

worst_day = carry_raw.min()
worst_sigmas = abs(worst_day / daily_std)

print(f"\nStd dev:    {daily_std*100:.4f}%")
print(f"Worst day:  {worst_day*100:.2f}%")
print(f"Sigmas:     {worst_sigmas:.1f}σ")

print(f"""
PROBLEM: The worst day is {worst_sigmas:.0f} standard deviations from the mean!

For a normal distribution:
  3σ event: 1 in 370 days (once per 1.4 years)
  4σ event: 1 in 15,787 days (once per 63 years)
  5σ event: 1 in 1.7 million days (never in human lifetime)
  {worst_sigmas:.0f}σ event: Essentially impossible

This proves the distribution is NOT normal.
The standard deviation MASSIVELY underestimates tail risk.
""")

# =============================================================================
# SECTION 6: MAX DRAWDOWN ANALYSIS
# =============================================================================

print("\n" + "=" * 80)
print("SECTION 6: MAX DRAWDOWN ANALYSIS")
print("=" * 80)

cumulative = (1 + carry_raw).cumprod()
running_max = cumulative.expanding().max()
drawdown = (cumulative - running_max) / running_max
max_dd = drawdown.min()

# Find the drawdown period
dd_end = drawdown.idxmin()
dd_start = cumulative[:dd_end].idxmax()

print(f"\nMax drawdown: {max_dd*100:.1f}%")
print(f"Period: {dd_start.strftime('%Y-%m-%d')} to {dd_end.strftime('%Y-%m-%d')}")
print(f"Duration: {(dd_end - dd_start).days} days")

# What vol would be consistent with this drawdown?
# For a normal distribution, max DD ~ 2.5 * vol * sqrt(T)
# where T is time in years
T_years = len(carry_raw) / 365
implied_vol_from_dd = abs(max_dd) / (2.5 * np.sqrt(T_years))

print(f"\nExpected max DD for {annual_vol*100:.1f}% vol over {T_years:.1f} years: ~{2.5 * annual_vol * np.sqrt(T_years) * 100:.1f}%")
print(f"Actual max DD: {max_dd*100:.1f}%")
print(f"Ratio (actual/expected): {abs(max_dd) / (2.5 * annual_vol * np.sqrt(T_years)):.1f}x")

# =============================================================================
# SECTION 7: ROLLING VOLATILITY ANALYSIS
# =============================================================================

print("\n" + "=" * 80)
print("SECTION 7: ROLLING VOLATILITY ANALYSIS")
print("=" * 80)

rolling_vol_30d = carry_raw.rolling(30).std() * np.sqrt(252)
rolling_vol_60d = carry_raw.rolling(60).std() * np.sqrt(252)

print(f"\n30-day rolling vol statistics (annualized):")
print(f"  Mean:   {rolling_vol_30d.mean()*100:.2f}%")
print(f"  Std:    {rolling_vol_30d.std()*100:.2f}%")
print(f"  Min:    {rolling_vol_30d.min()*100:.2f}%")
print(f"  Max:    {rolling_vol_30d.max()*100:.2f}%")
print(f"  95th%:  {rolling_vol_30d.quantile(0.95)*100:.2f}%")

print(f"\nHighest rolling vol periods (30d):")
highest_vol = rolling_vol_30d.nlargest(5)
for date, vol in highest_vol.items():
    print(f"  {date.strftime('%Y-%m-%d')}: {vol*100:.2f}%")

# =============================================================================
# SECTION 8: THE ROOT CAUSE
# =============================================================================

print("\n" + "=" * 80)
print("SECTION 8: ROOT CAUSE DIAGNOSIS")
print("=" * 80)

# Look at return distribution
positive_days = carry_raw[carry_raw > 0]
negative_days = carry_raw[carry_raw < 0]

print(f"\nReturn distribution:")
print(f"  Positive days: {len(positive_days)} ({len(positive_days)/len(carry_raw)*100:.1f}%)")
print(f"  Negative days: {len(negative_days)} ({len(negative_days)/len(carry_raw)*100:.1f}%)")
print(f"\n  Average positive day: +{positive_days.mean()*100:.4f}%")
print(f"  Average negative day: {negative_days.mean()*100:.4f}%")
print(f"\n  Worst positive day:   +{positive_days.max()*100:.4f}%")
print(f"  Worst negative day:   {negative_days.min()*100:.4f}%")

# The ratio tells the story
ratio = abs(negative_days.min()) / positive_days.max()
print(f"\n  Worst negative / Worst positive: {ratio:.1f}x")

print(f"""
ROOT CAUSE:
The funding rate is ASYMMETRIC:
- On normal days: tiny positive returns (+0.02-0.05%)
- On crash days: huge negative returns (-2 to -5%)

The standard deviation is dominated by the many small positive days,
completely masking the rare but devastating crashes.

This is the "picking up pennies in front of a steamroller" problem!
""")

# =============================================================================
# SECTION 9: CORRECTED VOL ESTIMATES
# =============================================================================

print("\n" + "=" * 80)
print("SECTION 9: CORRECTED VOL ESTIMATES")
print("=" * 80)

print("\n--- Option A: Stress-period vol ---")
# Use vol during stress periods (2022)
stress_period = carry_raw[carry_raw.index.year == 2022]
stress_vol = stress_period.std() * np.sqrt(252)
print(f"2022 volatility: {stress_vol*100:.2f}%")

print("\n--- Option B: Max rolling vol ---")
max_rolling_vol = rolling_vol_60d.max()
print(f"Maximum 60-day rolling vol: {max_rolling_vol*100:.2f}%")

print("\n--- Option C: Downside deviation (semi-deviation) ---")
downside_returns = carry_raw[carry_raw < 0]
downside_dev = downside_returns.std() * np.sqrt(252)
print(f"Downside deviation (annualized): {downside_dev*100:.2f}%")

print("\n--- Option D: Drawdown-implied vol ---")
# Rearranging: max_dd = 2.5 * vol * sqrt(T)
# vol = max_dd / (2.5 * sqrt(T))
drawdown_implied_vol = abs(max_dd) / (2.5 * np.sqrt(T_years))
print(f"Drawdown-implied vol: {drawdown_implied_vol*100:.2f}%")

print("\n--- Option E: Cornish-Fisher adjusted vol ---")
# For non-normal distributions, adjust vol for skew and kurtosis
cf_skew = skew(carry_raw)
cf_kurt = kurtosis(carry_raw)
# Cornish-Fisher 99% VaR multiplier
z = 2.326  # 99% normal
cf_z = z + (z**2 - 1) * cf_skew / 6 + (z**3 - 3*z) * cf_kurt / 24 - (2*z**3 - 5*z) * cf_skew**2 / 36
effective_vol = daily_std * (cf_z / z) * np.sqrt(252)
print(f"Cornish-Fisher adjusted vol: {effective_vol*100:.2f}%")

print("\n--- RECOMMENDED: Conservative blend ---")
# Use weighted average of stress vol and max rolling vol
conservative_vol = max(stress_vol, max_rolling_vol, drawdown_implied_vol)
print(f"Conservative vol (max of above): {conservative_vol*100:.2f}%")

# =============================================================================
# SECTION 10: IMPACT ON ALLOCATIONS
# =============================================================================

print("\n" + "=" * 80)
print("SECTION 10: IMPACT ON ALLOCATION ANALYSIS")
print("=" * 80)

print(f"""
CURRENT ANALYSIS (flawed):
  Carry vol: {annual_vol*100:.2f}%
  Vol scalar to reach 25%: {0.25 / annual_vol:.1f}x (INSANE!)

This means we were scaling carry returns by ~22x leverage!
At 22x, a -2.5% day becomes a -55% day (matching max DD).

CORRECTED ANALYSIS:
  Conservative carry vol: {conservative_vol*100:.2f}%
  Vol scalar to reach 25%: {0.25 / conservative_vol:.2f}x
""")

if conservative_vol > 0.25:
    print("  WARNING: Conservative vol EXCEEDS 25% target!")
    print("  → Carry must be SCALED DOWN (< 1x leverage)")
    print(f"  → Realistic scale factor: {0.25 / conservative_vol:.2f}x")
else:
    print(f"  Reasonable leverage: {0.25 / conservative_vol:.2f}x")

# =============================================================================
# SECTION 11: WHAT THIS MEANS FOR SHARPE
# =============================================================================

print("\n" + "=" * 80)
print("SECTION 11: RECALCULATED SHARPE WITH REALISTIC VOL")
print("=" * 80)

ann_return = carry_raw.mean() * 365
print(f"\nAnnual return (raw): {ann_return*100:.2f}%")

# Sharpe with different vol estimates
print(f"\nSharpe ratio with different vol estimates:")
print(f"  With current vol ({annual_vol*100:.1f}%):       {ann_return/annual_vol:.2f}")
print(f"  With stress vol ({stress_vol*100:.1f}%):      {ann_return/stress_vol:.2f}")
print(f"  With max rolling vol ({max_rolling_vol*100:.1f}%): {ann_return/max_rolling_vol:.2f}")
print(f"  With drawdown vol ({drawdown_implied_vol*100:.1f}%):  {ann_return/drawdown_implied_vol:.2f}")
print(f"  With conservative vol ({conservative_vol*100:.1f}%): {ann_return/conservative_vol:.2f}")

print(f"""
CONCLUSION:
The "real" carry Sharpe using realistic vol is {ann_return/conservative_vol:.2f}, not {ann_return/annual_vol:.2f}!

The original analysis was wrong because:
1. Standard deviation assumes normal distribution
2. Carry has extreme negative skew (-8) and high kurtosis
3. Std dev captures "normal days" but misses crash risk
4. Max drawdown proves the true risk is ~{conservative_vol/annual_vol:.0f}x higher than std dev suggests
""")

# =============================================================================
# SECTION 12: FINAL SUMMARY
# =============================================================================

print("\n" + "=" * 80)
print("FINAL SUMMARY")
print("=" * 80)

print(f"""
THE PROBLEM:
  Calculated carry vol: {annual_vol*100:.2f}% (wrong!)
  Max drawdown: {max_dd*100:.1f}%

  These are INCONSISTENT - 1.1% vol cannot produce 57% drawdown

THE DIAGNOSIS:
  Daily std is tiny ({daily_std*100:.4f}%) because:
  - {len(positive_days)/len(carry_raw)*100:.0f}% of days are small positive returns
  - Std dev is dominated by routine days, not crashes
  - Worst day was {worst_sigmas:.0f} sigma - impossible for normal distribution

THE FIX:
  Use stress-period or drawdown-implied vol: ~{conservative_vol*100:.1f}%

IMPLICATIONS FOR ALLOCATION:
  1. Carry cannot be levered 22x to reach 25% vol target
  2. At natural vol (~{conservative_vol*100:.0f}%), carry contributes LESS to portfolio vol
  3. True carry Sharpe is ~{ann_return/conservative_vol:.2f}, not ~5.0
  4. Skew-neutral allocation shifts significantly toward more carry (by risk contribution)

RECOMMENDED NEXT STEPS:
  1. Re-run allocation analysis using conservative vol estimate
  2. Target portfolio vol (not individual strategy vol)
  3. Size carry based on stress scenario, not historical std dev
""")
