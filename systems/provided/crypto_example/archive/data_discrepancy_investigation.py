"""
DATA DISCREPANCY INVESTIGATION
==============================
Why do OLD vs STITCHED funding data have low correlation?
"""

import os
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, "/Users/nathanieldavis/pysystemtrade")

OLD_FUNDING_DIR = "/Users/nathanieldavis/pysystemtrade/data/crypto/funding_rates"
COMBINED_DIR = "/Users/nathanieldavis/pysystemtrade/data/crypto/funding_rates/combined"

print("=" * 80)
print("DATA DISCREPANCY INVESTIGATION")
print("=" * 80)

# =============================================================================
# STEP 1: COMPARE RAW DATA FILES
# =============================================================================

print("\n" + "=" * 80)
print("STEP 1: SIDE-BY-SIDE DATA COMPARISON")
print("=" * 80)

def load_old_funding_raw(ticker):
    """Load OLD funding data WITHOUT aggregation."""
    path = os.path.join(OLD_FUNDING_DIR, f"{ticker}_funding.csv")
    if not os.path.exists(path):
        return pd.DataFrame()
    df = pd.read_csv(path, parse_dates=['datetime'])
    df = df.set_index('datetime')
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    return df

def load_combined_funding_raw(ticker):
    """Load COMBINED funding data WITHOUT aggregation."""
    path = os.path.join(COMBINED_DIR, f"{ticker}_funding_combined.csv")
    if not os.path.exists(path):
        return pd.DataFrame()
    df = pd.read_csv(path, parse_dates=['datetime'])
    df = df.set_index('datetime')
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    return df

for ticker in ["BTC", "ETH"]:
    print(f"\n{'='*40}")
    print(f"Comparing {ticker}")
    print(f"{'='*40}")

    old_df = load_old_funding_raw(ticker)
    new_df = load_combined_funding_raw(ticker)

    if len(old_df) == 0 or len(new_df) == 0:
        print(f"  Missing data for {ticker}")
        continue

    print(f"\nOLD data shape: {old_df.shape}")
    print(f"OLD columns: {list(old_df.columns)}")
    print(f"OLD date range: {old_df.index.min()} to {old_df.index.max()}")
    print(f"OLD sample values (first 5):")
    print(old_df.head())

    print(f"\nNEW data shape: {new_df.shape}")
    print(f"NEW columns: {list(new_df.columns)}")
    print(f"NEW date range: {new_df.index.min()} to {new_df.index.max()}")
    print(f"NEW sample values (first 5):")
    print(new_df.head())

    # Check if raw data is 8-hourly or daily
    if len(old_df) > 10:
        old_freq = pd.infer_freq(old_df.index[:100])
        print(f"\nOLD inferred frequency: {old_freq}")
        old_time_diffs = old_df.index.to_series().diff().dropna()
        print(f"OLD typical time between records: {old_time_diffs.mode().iloc[0] if len(old_time_diffs) > 0 else 'N/A'}")

    if len(new_df) > 10:
        new_freq = pd.infer_freq(new_df.index[:100])
        print(f"NEW inferred frequency: {new_freq}")
        new_time_diffs = new_df.index.to_series().diff().dropna()
        print(f"NEW typical time between records: {new_time_diffs.mode().iloc[0] if len(new_time_diffs) > 0 else 'N/A'}")

    # Side-by-side comparison for overlapping dates
    old_daily = old_df['fundingRate'].resample('D').sum()
    new_daily = new_df['fundingRate'].resample('D').sum() if 'fundingRate' in new_df.columns else new_df.iloc[:, 0].resample('D').sum()

    overlap = old_daily.index.intersection(new_daily.index)
    print(f"\nOverlapping days: {len(overlap)}")

    if len(overlap) > 0:
        comparison = pd.DataFrame({
            'OLD': old_daily.loc[overlap],
            'NEW': new_daily.loc[overlap]
        }).dropna()
        comparison['DIFF'] = comparison['NEW'] - comparison['OLD']
        comparison['RATIO'] = comparison['NEW'] / comparison['OLD'].replace(0, np.nan)

        print(f"\n--- First 20 overlapping days ---")
        print(comparison.head(20).to_string())

        print(f"\n--- Rows with large difference (|DIFF| > 0.0001) ---")
        large_diff = comparison[abs(comparison['DIFF']) > 0.0001]
        print(f"Found {len(large_diff)} rows with large differences")
        if len(large_diff) > 0:
            print(large_diff.head(20).to_string())

        print(f"\n--- Statistics ---")
        print(f"Correlation: {comparison['OLD'].corr(comparison['NEW']):.4f}")
        print(f"Mean OLD: {comparison['OLD'].mean():.6f}")
        print(f"Mean NEW: {comparison['NEW'].mean():.6f}")
        print(f"Mean DIFF: {comparison['DIFF'].mean():.6f}")
        print(f"Std DIFF: {comparison['DIFF'].std():.6f}")

# =============================================================================
# STEP 2: CHECK DATA SOURCES
# =============================================================================

print("\n" + "=" * 80)
print("STEP 2: DATA SOURCES")
print("=" * 80)

print("\n--- OLD Data Sources ---")
# Check what files exist
old_files = os.listdir(OLD_FUNDING_DIR)
funding_files = [f for f in old_files if f.endswith('_funding.csv')]
print(f"Files in OLD dir: {sorted(funding_files)}")

# Check for any metadata or source info in the files
for ticker in ["BTC"]:
    path = os.path.join(OLD_FUNDING_DIR, f"{ticker}_funding.csv")
    if os.path.exists(path):
        # Read first few lines to see format
        with open(path, 'r') as f:
            print(f"\n{ticker}_funding.csv first 10 lines:")
            for i, line in enumerate(f):
                if i < 10:
                    print(f"  {line.strip()}")
                else:
                    break

print("\n--- COMBINED Data Sources ---")
combined_files = os.listdir(COMBINED_DIR) if os.path.exists(COMBINED_DIR) else []
print(f"Files in COMBINED dir: {sorted(combined_files)}")

for ticker in ["BTC"]:
    path = os.path.join(COMBINED_DIR, f"{ticker}_funding_combined.csv")
    if os.path.exists(path):
        with open(path, 'r') as f:
            print(f"\n{ticker}_funding_combined.csv first 10 lines:")
            for i, line in enumerate(f):
                if i < 10:
                    print(f"  {line.strip()}")
                else:
                    break

# =============================================================================
# STEP 3: CHECK FOR COMMON ISSUES
# =============================================================================

print("\n" + "=" * 80)
print("STEP 3: CHECKING COMMON ISSUES")
print("=" * 80)

for ticker in ["BTC"]:
    old_df = load_old_funding_raw(ticker)
    new_df = load_combined_funding_raw(ticker)

    if len(old_df) == 0 or len(new_df) == 0:
        continue

    print(f"\n--- {ticker} ---")

    # A) TIMEZONE CHECK
    print("\nA) TIMEZONE CHECK:")
    print(f"   OLD timezone: {old_df.index.tz}")
    print(f"   NEW timezone: {new_df.index.tz}")
    print(f"   OLD sample timestamps:")
    for ts in old_df.index[:5]:
        print(f"      {ts}")
    print(f"   NEW sample timestamps:")
    for ts in new_df.index[:5]:
        print(f"      {ts}")

    # B) AGGREGATION CHECK
    print("\nB) AGGREGATION CHECK:")
    # Count records per day
    old_per_day = old_df.groupby(old_df.index.date).size()
    new_per_day = new_df.groupby(new_df.index.date).size()
    print(f"   OLD records per day (mode): {old_per_day.mode().iloc[0] if len(old_per_day) > 0 else 'N/A'}")
    print(f"   NEW records per day (mode): {new_per_day.mode().iloc[0] if len(new_per_day) > 0 else 'N/A'}")
    print(f"   OLD records per day distribution: min={old_per_day.min()}, max={old_per_day.max()}, mean={old_per_day.mean():.1f}")
    print(f"   NEW records per day distribution: min={new_per_day.min()}, max={new_per_day.max()}, mean={new_per_day.mean():.1f}")

    # C) SIGN CONVENTION CHECK
    print("\nC) SIGN CONVENTION CHECK:")
    old_rates = old_df['fundingRate']
    new_rates = new_df['fundingRate'] if 'fundingRate' in new_df.columns else new_df.iloc[:, 0]
    print(f"   OLD: mean={old_rates.mean():.6f}, min={old_rates.min():.6f}, max={old_rates.max():.6f}")
    print(f"   NEW: mean={new_rates.mean():.6f}, min={new_rates.min():.6f}, max={new_rates.max():.6f}")

    # Check if one is -1 × the other
    overlap_idx = old_rates.index.intersection(new_rates.index)
    if len(overlap_idx) > 100:
        old_overlap = old_rates.loc[overlap_idx[:1000]]
        new_overlap = new_rates.loc[overlap_idx[:1000]]
        corr_normal = old_overlap.corr(new_overlap)
        corr_negated = old_overlap.corr(-new_overlap)
        print(f"   Correlation (normal): {corr_normal:.4f}")
        print(f"   Correlation (negated): {corr_negated:.4f}")
        if corr_negated > corr_normal:
            print("   WARNING: One dataset may have OPPOSITE sign convention!")

    # D) PERCENTAGE vs DECIMAL CHECK
    print("\nD) MAGNITUDE CHECK (percentage vs decimal):")
    print(f"   OLD typical magnitude: {abs(old_rates).quantile(0.5):.8f}")
    print(f"   NEW typical magnitude: {abs(new_rates).quantile(0.5):.8f}")
    ratio = abs(old_rates).quantile(0.5) / abs(new_rates).quantile(0.5) if abs(new_rates).quantile(0.5) > 0 else np.nan
    print(f"   Ratio (OLD/NEW): {ratio:.2f}")
    if ratio > 50 or ratio < 0.02:
        print("   WARNING: Possible percentage vs decimal mismatch!")

    # E) MISSING DATA CHECK
    print("\nE) MISSING DATA CHECK:")
    old_daily = old_rates.resample('D').count()
    new_daily = new_rates.resample('D').count()
    print(f"   OLD days with data: {(old_daily > 0).sum()}")
    print(f"   NEW days with data: {(new_daily > 0).sum()}")
    print(f"   OLD days with gaps (0 records): {(old_daily == 0).sum()}")
    print(f"   NEW days with gaps (0 records): {(new_daily == 0).sum()}")

# =============================================================================
# STEP 4: SPOT CHECK SPECIFIC DATES
# =============================================================================

print("\n" + "=" * 80)
print("STEP 4: SPOT CHECK SPECIFIC DATES")
print("=" * 80)

spot_check_dates = [
    ("2021-04-14", "Bitcoin ATH day (should have high positive funding)"),
    ("2022-05-09", "LUNA collapse (should have negative funding)"),
    ("2022-11-09", "FTX collapse (should have negative funding)"),
    ("2023-06-15", "Mid-2023 (normal day)"),
    ("2024-03-14", "2024 bull market (should have positive funding)"),
]

for ticker in ["BTC", "ETH"]:
    old_df = load_old_funding_raw(ticker)
    new_df = load_combined_funding_raw(ticker)

    if len(old_df) == 0 or len(new_df) == 0:
        continue

    old_daily = old_df['fundingRate'].resample('D').sum()
    new_daily = new_df['fundingRate'].resample('D').sum() if 'fundingRate' in new_df.columns else new_df.iloc[:, 0].resample('D').sum()

    print(f"\n--- {ticker} Spot Checks ---")
    print(f"{'Date':<12} {'OLD':<15} {'NEW':<15} {'Description'}")
    print("-" * 70)

    for date_str, description in spot_check_dates:
        date = pd.Timestamp(date_str)
        old_val = old_daily.get(date, np.nan)
        new_val = new_daily.get(date, np.nan)

        old_str = f"{old_val:.6f}" if not pd.isna(old_val) else "N/A"
        new_str = f"{new_val:.6f}" if not pd.isna(new_val) else "N/A"

        print(f"{date_str:<12} {old_str:<15} {new_str:<15} {description}")

# =============================================================================
# STEP 5: CHECK THE STITCHING CODE
# =============================================================================

print("\n" + "=" * 80)
print("STEP 5: STITCHING CODE ANALYSIS")
print("=" * 80)

stitcher_path = "/Users/nathanieldavis/pysystemtrade/systems/provided/crypto_example/data_stitcher.py"
if os.path.exists(stitcher_path):
    print(f"\nFound stitcher at: {stitcher_path}")
    with open(stitcher_path, 'r') as f:
        content = f.read()
        # Look for key sections
        print("\n--- Key sections from data_stitcher.py ---")
        lines = content.split('\n')
        for i, line in enumerate(lines[:100]):  # First 100 lines
            if any(keyword in line.lower() for keyword in ['priority', 'merge', 'combine', 'source', 'binance', 'bitmex', 'kraken']):
                print(f"Line {i+1}: {line}")
else:
    print(f"Stitcher not found at {stitcher_path}")

# Check for other potential stitching files
potential_files = [
    "data_stitching_methodology.py",
    "crypto_data_adapter.py",
]

for filename in potential_files:
    path = os.path.join("/Users/nathanieldavis/pysystemtrade/systems/provided/crypto_example", filename)
    if os.path.exists(path):
        print(f"\n--- Found: {filename} ---")
        with open(path, 'r') as f:
            # Print first 50 lines
            for i, line in enumerate(f):
                if i < 50:
                    print(f"  {i+1}: {line.rstrip()}")
                else:
                    print("  ... (truncated)")
                    break

# =============================================================================
# STEP 6: LOOK FOR RAW SOURCE FILES
# =============================================================================

print("\n" + "=" * 80)
print("STEP 6: RAW SOURCE FILES")
print("=" * 80)

# Check for Binance, BitMEX, Kraken subdirectories
potential_sources = [
    "/Users/nathanieldavis/pysystemtrade/data/crypto/funding_rates/binance",
    "/Users/nathanieldavis/pysystemtrade/data/crypto/funding_rates/bitmex",
    "/Users/nathanieldavis/pysystemtrade/data/crypto/funding_rates/kraken",
]

for source_dir in potential_sources:
    if os.path.exists(source_dir):
        files = os.listdir(source_dir)
        print(f"\n{source_dir}:")
        print(f"  Files: {sorted(files)[:10]}{'...' if len(files) > 10 else ''}")

        # Sample one file
        btc_files = [f for f in files if 'btc' in f.lower() or 'BTC' in f]
        if btc_files:
            sample_path = os.path.join(source_dir, btc_files[0])
            print(f"\n  Sample from {btc_files[0]}:")
            with open(sample_path, 'r') as f:
                for i, line in enumerate(f):
                    if i < 5:
                        print(f"    {line.strip()}")

# =============================================================================
# STEP 7: COMPARE 8-HOURLY DATA DIRECTLY
# =============================================================================

print("\n" + "=" * 80)
print("STEP 7: COMPARE 8-HOURLY DATA DIRECTLY (NO AGGREGATION)")
print("=" * 80)

for ticker in ["BTC"]:
    old_df = load_old_funding_raw(ticker)
    new_df = load_combined_funding_raw(ticker)

    if len(old_df) == 0 or len(new_df) == 0:
        continue

    # Find exact timestamp matches
    exact_matches = old_df.index.intersection(new_df.index)
    print(f"\n{ticker}: Found {len(exact_matches)} exact timestamp matches out of {len(old_df)} OLD and {len(new_df)} NEW records")

    if len(exact_matches) > 0:
        old_vals = old_df.loc[exact_matches, 'fundingRate']
        new_col = 'fundingRate' if 'fundingRate' in new_df.columns else new_df.columns[0]
        new_vals = new_df.loc[exact_matches, new_col]

        comparison = pd.DataFrame({
            'OLD': old_vals,
            'NEW': new_vals
        })
        comparison['MATCH'] = (abs(comparison['OLD'] - comparison['NEW']) < 0.000001)

        print(f"\nExact matches: {comparison['MATCH'].sum()} / {len(comparison)} ({comparison['MATCH'].mean()*100:.1f}%)")
        print(f"\nSample of mismatches:")
        mismatches = comparison[~comparison['MATCH']]
        if len(mismatches) > 0:
            print(mismatches.head(20).to_string())
        else:
            print("All matching timestamps have identical values!")

# =============================================================================
# STEP 8: ROOT CAUSE HYPOTHESIS
# =============================================================================

print("\n" + "=" * 80)
print("STEP 8: ROOT CAUSE ANALYSIS")
print("=" * 80)

print("""
HYPOTHESIS TESTING:

Based on the investigation above, the most likely causes of discrepancy are:

1. DIFFERENT DATA SOURCES
   - OLD data might be from one exchange (e.g., Binance only)
   - STITCHED data combines multiple exchanges
   - Different exchanges have different funding rates!

2. AGGREGATION METHOD
   - 8-hourly funding rates aggregated differently
   - OLD: possibly using last() or first() instead of sum()
   - NEW: using sum() of all 3 daily funding payments

3. TIME ALIGNMENT
   - Funding settlement times differ by exchange
   - Binance: 00:00, 08:00, 16:00 UTC
   - BitMEX: 04:00, 12:00, 20:00 UTC
   - Misaligned timestamps could cause apparent mismatch

4. DATA QUALITY ISSUES
   - Missing records in one dataset
   - Data entry errors
   - Different handling of holidays/outages

RECOMMENDATION:
- Check which dataset matches external verification sources
- The dataset that matches CoinGlass/exchange APIs is correct
- Rerun analysis with the verified dataset
""")
