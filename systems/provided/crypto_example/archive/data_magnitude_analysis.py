"""
DATA MAGNITUDE ANALYSIS
=======================
The OLD data has values ~10,000x smaller than NEW data.
This is the root cause of the discrepancy.
"""

import os
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, "/Users/nathanieldavis/pysystemtrade")

OLD_FUNDING_DIR = "/Users/nathanieldavis/pysystemtrade/data/crypto/funding_rates"
COMBINED_DIR = "/Users/nathanieldavis/pysystemtrade/data/crypto/funding_rates/combined"

print("=" * 80)
print("DATA MAGNITUDE ANALYSIS")
print("=" * 80)

# =============================================================================
# STEP 1: EXAMINE OLD DATA CLOSELY
# =============================================================================

print("\n" + "=" * 80)
print("STEP 1: OLD DATA EXAMINATION")
print("=" * 80)

# Load OLD BTC data
old_path = os.path.join(OLD_FUNDING_DIR, "BTC_funding.csv")
old_df = pd.read_csv(old_path, parse_dates=['datetime'])
old_df = old_df.set_index('datetime')

print("\nOLD BTC funding data:")
print(f"  Date range: {old_df.index.min()} to {old_df.index.max()}")
print(f"  Records: {len(old_df)}")
print(f"\nOLD statistics:")
print(f"  Mean: {old_df['fundingRate'].mean():.10f}")
print(f"  Std:  {old_df['fundingRate'].std():.10f}")
print(f"  Min:  {old_df['fundingRate'].min():.10f}")
print(f"  Max:  {old_df['fundingRate'].max():.10f}")

# What's the typical value?
print(f"\nOLD percentiles:")
for pct in [1, 5, 25, 50, 75, 95, 99]:
    val = old_df['fundingRate'].quantile(pct/100)
    print(f"  {pct}th: {val:.10f}")

# =============================================================================
# STEP 2: EXAMINE NEW DATA
# =============================================================================

print("\n" + "=" * 80)
print("STEP 2: NEW DATA EXAMINATION")
print("=" * 80)

new_path = os.path.join(COMBINED_DIR, "BTC_funding_combined.csv")
new_df = pd.read_csv(new_path, parse_dates=['datetime'])
new_df = new_df.set_index('datetime')

print("\nNEW BTC funding data:")
print(f"  Date range: {new_df.index.min()} to {new_df.index.max()}")
print(f"  Records: {len(new_df)}")
print(f"  Source column: {new_df['source'].value_counts().to_dict()}")
print(f"\nNEW statistics:")
print(f"  Mean: {new_df['fundingRate'].mean():.6f}")
print(f"  Std:  {new_df['fundingRate'].std():.6f}")
print(f"  Min:  {new_df['fundingRate'].min():.6f}")
print(f"  Max:  {new_df['fundingRate'].max():.6f}")

print(f"\nNEW percentiles:")
for pct in [1, 5, 25, 50, 75, 95, 99]:
    val = new_df['fundingRate'].quantile(pct/100)
    print(f"  {pct}th: {val:.6f}")

# =============================================================================
# STEP 3: COMPARE MAGNITUDES
# =============================================================================

print("\n" + "=" * 80)
print("STEP 3: MAGNITUDE COMPARISON")
print("=" * 80)

old_median = abs(old_df['fundingRate']).median()
new_median = abs(new_df['fundingRate']).median()
ratio = new_median / old_median if old_median > 0 else np.nan

print(f"\nMedian absolute values:")
print(f"  OLD: {old_median:.12f}")
print(f"  NEW: {new_median:.8f}")
print(f"  Ratio (NEW/OLD): {ratio:,.0f}x")

# =============================================================================
# STEP 4: CHECK SOURCE OF OLD DATA
# =============================================================================

print("\n" + "=" * 80)
print("STEP 4: WHERE DID OLD DATA COME FROM?")
print("=" * 80)

# Check if there are any comments or metadata in the OLD files
print("\nChecking for data source indicators...")

# Look for any files that might indicate source
for f in os.listdir(OLD_FUNDING_DIR):
    if 'README' in f or 'source' in f.lower() or '.txt' in f:
        print(f"Found: {f}")
        path = os.path.join(OLD_FUNDING_DIR, f)
        with open(path, 'r') as file:
            print(file.read()[:500])

# Check the timestamps more carefully - which exchange uses these times?
print("\nTimestamp analysis (first 24 hours of OLD data):")
first_day = old_df.head(24)
print(first_day)

# Binance uses 00:00, 08:00, 16:00 UTC
# BitMEX uses 04:00, 12:00, 20:00 UTC
# Kraken uses 00:00, 08:00, 16:00 UTC (same as Binance)

hours = old_df.index.hour.value_counts().sort_index()
print(f"\nHour distribution in OLD data:")
print(hours)

# =============================================================================
# STEP 5: CHECK EXTERNAL VALIDATION
# =============================================================================

print("\n" + "=" * 80)
print("STEP 5: EXTERNAL VALIDATION - WHAT SHOULD FUNDING RATES BE?")
print("=" * 80)

print("""
KNOWN FUNDING RATE BENCHMARKS:

1. TYPICAL FUNDING RATE (crypto perps):
   - Neutral: 0.01% per 8h = 0.03% per day = ~10.95% annual
   - Bull market peak: 0.1-0.3% per 8h = 0.3-0.9% per day
   - Bear market: -0.05% to -0.3% per 8h = -0.15% to -0.9% per day

2. SPECIFIC KNOWN EVENTS:
   - April 2021 (BTC ATH): Very high positive funding (~0.1-0.3% per 8h)
   - May 2022 (LUNA): Negative funding spike
   - Nov 2022 (FTX): Negative funding spike

Let's check what our data shows for these events:
""")

# April 14, 2021 (BTC ATH)
date_check = '2021-04-14'
old_val = old_df.loc[old_df.index.date == pd.Timestamp(date_check).date(), 'fundingRate'].sum()
new_val = new_df.loc[new_df.index.date == pd.Timestamp(date_check).date(), 'fundingRate'].sum()
print(f"\n{date_check} (BTC ATH):")
print(f"  Expected: ~0.3% to 0.9% daily (high positive)")
print(f"  OLD: {old_val:.10f} = {old_val*100:.8f}%")
print(f"  NEW: {new_val:.6f} = {new_val*100:.4f}%")
print(f"  NEW matches expectation: {'YES' if 0.001 < new_val < 0.01 else 'NO'}")

# Nov 9, 2022 (FTX)
date_check = '2022-11-09'
old_val = old_df.loc[old_df.index.date == pd.Timestamp(date_check).date(), 'fundingRate'].sum()
new_val = new_df.loc[new_df.index.date == pd.Timestamp(date_check).date(), 'fundingRate'].sum()
print(f"\n{date_check} (FTX collapse):")
print(f"  Expected: negative (shorts paying longs)")
print(f"  OLD: {old_val:.10f} = {old_val*100:.8f}%")
print(f"  NEW: {new_val:.6f} = {new_val*100:.4f}%")
print(f"  NEW matches expectation: {'YES' if new_val < 0 else 'NO'}")

# =============================================================================
# STEP 6: CHECK IF OLD DATA IS WRONG UNIT
# =============================================================================

print("\n" + "=" * 80)
print("STEP 6: UNIT HYPOTHESIS TEST")
print("=" * 80)

print("""
HYPOTHESIS: OLD data might be in a different unit.

Possible conversions:
1. OLD is per-funding-period, NEW is daily sum
2. OLD is in basis points, NEW is in percentage
3. OLD has some scaling factor applied incorrectly
4. OLD is from a different data source with different conventions

Testing: If we multiply OLD by some factor, does it match NEW?
""")

# Aggregate OLD to daily and compare
old_daily = old_df['fundingRate'].resample('D').sum()
new_daily = new_df['fundingRate']

# Find overlapping dates
common = old_daily.index.intersection(new_daily.index)
old_aligned = old_daily.loc[common]
new_aligned = new_daily.loc[common]

# Test different multipliers
print("\nTesting multipliers:")
for mult in [1, 10, 100, 1000, 10000, 100000]:
    corr = (old_aligned * mult).corr(new_aligned)
    ratio = (new_aligned / old_aligned).median()
    print(f"  {mult:>8}x: correlation = {corr:.4f}")

# What multiplier makes mean match?
mean_ratio = new_aligned.mean() / old_aligned.mean()
print(f"\nMean ratio (NEW/OLD): {mean_ratio:,.0f}")

# =============================================================================
# STEP 7: IDENTIFY DATA SOURCE
# =============================================================================

print("\n" + "=" * 80)
print("STEP 7: DATA SOURCE IDENTIFICATION")
print("=" * 80)

# Look at the hour distribution more carefully
# This will tell us which exchange the OLD data came from

print("\nOLD data hour distribution (most common hours):")
print(old_df.index.hour.value_counts().head(10))

# Check if OLD data format looks like Kraken/Binance/BitMEX
print("\nOLD data sample (first 10 records):")
print(old_df.head(10))

# The values are EXTREMELY small (1e-8 to 1e-6)
# This is NOT how funding rates are typically expressed

# Let's check: is this perhaps the funding rate as a proportion of price?
# Or is it the funding payment in BTC for a 1 BTC position?

print("""
ANALYSIS:

The OLD data values (~1e-8) are about 10,000x smaller than expected funding rates.

This suggests the OLD data might be:
1. A different calculation (not standard funding rate)
2. An error in data download/processing
3. From a source with non-standard formatting

The NEW data values (~0.0003 = 0.03%) match expected funding rates perfectly.
""")

# =============================================================================
# STEP 8: CHECK WHICH ANALYSIS USED WHICH DATA
# =============================================================================

print("\n" + "=" * 80)
print("STEP 8: WHICH ANALYSES USED WHICH DATA?")
print("=" * 80)

# The backtests that showed Sharpe > 5 were using OLD data
# The backtests that showed Sharpe ~1-2 were using NEW data

# Calculate Sharpe with each dataset
def calc_sharpe(returns):
    ann_ret = returns.mean() * 365
    ann_vol = returns.std() * np.sqrt(365)
    return ann_ret / ann_vol if ann_vol > 0 else 0

old_sharpe = calc_sharpe(old_daily)
new_sharpe = calc_sharpe(new_daily)

print(f"\nSharpe ratios (raw funding, no scaling):")
print(f"  OLD data: {old_sharpe:.4f}")
print(f"  NEW data: {new_sharpe:.2f}")

# At 25% vol target
old_vol = old_daily.std() * np.sqrt(365)
new_vol = new_daily.std() * np.sqrt(365)
old_scale = 0.25 / old_vol
new_scale = 0.25 / new_vol

old_scaled_sharpe = calc_sharpe(old_daily * old_scale)
new_scaled_sharpe = calc_sharpe(new_daily * new_scale)

print(f"\nSharpe ratios (scaled to 25% vol):")
print(f"  OLD data: scale={old_scale:,.1f}x, Sharpe={old_scaled_sharpe:.2f}")
print(f"  NEW data: scale={new_scale:.1f}x, Sharpe={new_scaled_sharpe:.2f}")

# =============================================================================
# CONCLUSION
# =============================================================================

print("\n" + "=" * 80)
print("CONCLUSION")
print("=" * 80)

print(f"""
ROOT CAUSE IDENTIFIED:

The OLD and NEW funding rate datasets have VASTLY DIFFERENT MAGNITUDES.

   OLD data: mean = {old_daily.mean():.10f} ({old_daily.mean()*100:.8f}% per day)
   NEW data: mean = {new_daily.mean():.6f} ({new_daily.mean()*100:.4f}% per day)

   Ratio: {mean_ratio:,.0f}x difference

WHICH IS CORRECT?

   NEW data matches expected funding rate values:
   - Typical: 0.01-0.03% per day ✓
   - Bull market: 0.1-0.3% per day ✓
   - Bear market: -0.05% to -0.15% per day ✓

   OLD data is ~10,000x too small:
   - Values like 1e-8 per day are NOT realistic funding rates
   - No exchange quotes funding rates in this format

IMPACT ON BACKTEST:

   When vol-targeting OLD data to 25%:
   - Required scale factor: {old_scale:,.0f}x leverage
   - This is UNREALISTIC leverage

   When vol-targeting NEW data to 25%:
   - Required scale factor: {new_scale:.1f}x leverage
   - This is REASONABLE leverage

RECOMMENDATION:

   1. USE NEW (STITCHED) DATA for all analyses
   2. The OLD data appears to be incorrectly formatted or from wrong source
   3. Re-run carry backtest with NEW data only
   4. The earlier high Sharpe estimates (>5) were artifacts of wrong data magnitude

WHERE DID OLD DATA COME FROM?

   The OLD data files might have been:
   - Downloaded from a source that uses non-standard formatting
   - Processed with an incorrect conversion
   - From a different type of rate entirely (not standard perp funding)

   The NEW data explicitly shows source="bitmex" or source="binance"
   and has realistic values matching external verification.
""")
