"""
PRICE DATA AUDIT
================
Verify the stitched price data is correct after finding funding rate bug.
"""

import os
import sys
import numpy as np
import pandas as pd
from datetime import datetime

sys.path.insert(0, "/Users/nathanieldavis/pysystemtrade")

STITCHED_DIR = "/Users/nathanieldavis/pysystemtrade/data/crypto/stitched"
KRAKEN_DIR = "/Users/nathanieldavis/pysystemtrade/data/crypto/Kraken_OHLCVT"
COINMETRICS_DIR = "/Users/nathanieldavis/pysystemtrade/data/crypto/coinmetrics_community/csv"

print("=" * 80)
print("PRICE DATA AUDIT")
print("=" * 80)

# =============================================================================
# STEP 1: DATA SOURCES AND STITCHING
# =============================================================================

print("\n" + "=" * 80)
print("STEP 1: DATA SOURCES AND STITCHING")
print("=" * 80)

print("\n--- Available Stitched Files ---")
if os.path.exists(STITCHED_DIR):
    stitched_files = sorted(os.listdir(STITCHED_DIR))
    print(f"Files in {STITCHED_DIR}:")
    for f in stitched_files:
        if f.endswith('.csv'):
            path = os.path.join(STITCHED_DIR, f)
            size = os.path.getsize(path)
            print(f"  {f}: {size/1024:.1f} KB")
else:
    print("STITCHED_DIR not found!")

print("\n--- Kraken Source Files ---")
if os.path.exists(KRAKEN_DIR):
    kraken_files = [f for f in os.listdir(KRAKEN_DIR) if 'USD' in f and f.endswith('.csv')]
    print(f"Found {len(kraken_files)} USD pairs in Kraken directory")
    # Show a few examples
    for f in sorted(kraken_files)[:5]:
        print(f"  {f}")
    print("  ...")
else:
    print("KRAKEN_DIR not found!")

print("\n--- Coinmetrics Source Files ---")
if os.path.exists(COINMETRICS_DIR):
    cm_files = sorted(os.listdir(COINMETRICS_DIR))
    print(f"Found {len(cm_files)} files in Coinmetrics directory")
    for f in cm_files[:5]:
        print(f"  {f}")
    print("  ...")
else:
    print("COINMETRICS_DIR not found!")

# Check stitching code for methodology
print("\n--- Stitching Methodology ---")
stitcher_path = "/Users/nathanieldavis/pysystemtrade/systems/provided/crypto_example/data_stitcher.py"
if os.path.exists(stitcher_path):
    print("Reading data_stitcher.py for methodology...")
    with open(stitcher_path, 'r') as f:
        content = f.read()
        # Find key sections
        if 'priority' in content.lower():
            print("\nPriority order mentioned in stitcher")
        lines = content.split('\n')
        for i, line in enumerate(lines[:80]):
            if any(kw in line.lower() for kw in ['source', 'priority', 'kraken', 'coinmetrics', 'stitch']):
                print(f"  Line {i+1}: {line.strip()}")

# =============================================================================
# STEP 2: MAGNITUDE SANITY CHECKS
# =============================================================================

print("\n" + "=" * 80)
print("STEP 2: MAGNITUDE SANITY CHECKS")
print("=" * 80)

def load_stitched_price(instrument):
    """Load price data from stitched directory."""
    path = os.path.join(STITCHED_DIR, f"{instrument}_price.csv")
    if not os.path.exists(path):
        path = os.path.join(STITCHED_DIR, f"{instrument}.csv")
    if not os.path.exists(path):
        return pd.DataFrame()

    df = pd.read_csv(path, parse_dates=['date'])
    df = df.set_index('date')
    return df

# Key dates to verify
key_dates = [
    ("2017-12-17", "BTC ATH #1", {"BTC": 19500}),
    ("2018-01-13", "ETH ATH #1", {"ETH": 1400}),
    ("2020-03-12", "COVID crash", {"BTC": 5000, "ETH": 120}),
    ("2021-04-14", "BTC local high", {"BTC": 64000}),
    ("2021-11-10", "BTC ATH #2", {"BTC": 69000, "ETH": 4800}),
    ("2022-05-12", "LUNA crash", {"BTC": 28000}),
    ("2022-11-09", "FTX collapse", {"BTC": 17000, "ETH": 1200}),
    ("2024-03-14", "BTC ATH #3", {"BTC": 73000, "ETH": 4000}),
]

print("\n--- Key Date Verification ---")
print(f"\n{'Date':<12} {'Event':<20} {'Instrument':<6} {'Expected':<12} {'Our Data':<12} {'Diff %':<10} {'Match?'}")
print("-" * 90)

for date_str, event, expected in key_dates:
    date = pd.Timestamp(date_str)

    for instr, exp_price in expected.items():
        df = load_stitched_price(instr)
        if len(df) == 0:
            print(f"{date_str:<12} {event:<20} {instr:<6} ${exp_price:<11,} {'NO DATA':<12} {'N/A':<10} ✗")
            continue

        # Find closest date
        if date in df.index:
            our_price = df.loc[date, 'close']
        else:
            # Find nearest date within 3 days
            mask = abs(df.index - date) <= pd.Timedelta(days=3)
            if mask.any():
                nearest = df[mask].iloc[0]
                our_price = nearest['close']
            else:
                our_price = None

        if our_price is not None:
            diff_pct = (our_price - exp_price) / exp_price * 100
            match = "✓" if abs(diff_pct) < 20 else "✗"
            print(f"{date_str:<12} {event:<20} {instr:<6} ${exp_price:<11,} ${our_price:<11,.0f} {diff_pct:>+8.1f}% {match}")
        else:
            print(f"{date_str:<12} {event:<20} {instr:<6} ${exp_price:<11,} {'NOT FOUND':<12} {'N/A':<10} ✗")

# =============================================================================
# STEP 3: CHECK FOR COMMON DATA ISSUES
# =============================================================================

print("\n" + "=" * 80)
print("STEP 3: DATA QUALITY CHECKS")
print("=" * 80)

instruments_to_check = ['BTC', 'ETH', 'SOL', 'LINK', 'ADA']

for instr in instruments_to_check:
    df = load_stitched_price(instr)
    if len(df) == 0:
        print(f"\n{instr}: NO DATA")
        continue

    print(f"\n--- {instr} ---")
    print(f"Date range: {df.index.min().date()} to {df.index.max().date()}")
    print(f"Total rows: {len(df)}")

    # A) GAPS
    if len(df) > 1:
        date_diff = df.index.to_series().diff()
        gaps = date_diff[date_diff > pd.Timedelta(days=1)]
        if len(gaps) > 0:
            print(f"\nGaps > 1 day: {len(gaps)}")
            for idx, gap in gaps.head(5).items():
                print(f"  {idx.date()}: {gap.days} days gap")
            if len(gaps) > 5:
                print(f"  ... and {len(gaps) - 5} more")
        else:
            print("Gaps > 1 day: None ✓")

    # B) DUPLICATES
    dup_dates = df.index[df.index.duplicated()]
    if len(dup_dates) > 0:
        print(f"Duplicate dates: {len(dup_dates)} ✗")
    else:
        print("Duplicate dates: None ✓")

    # Stale data (same close price multiple days)
    stale = (df['close'].diff() == 0).sum()
    print(f"Stale prices (unchanged): {stale} days ({stale/len(df)*100:.1f}%)")

    # C) OUTLIERS
    returns = df['close'].pct_change()

    extreme_up = returns[returns > 0.50]
    if len(extreme_up) > 0:
        print(f"\nReturns > +50%: {len(extreme_up)}")
        for idx, ret in extreme_up.items():
            print(f"  {idx.date()}: +{ret*100:.1f}%")

    extreme_down = returns[returns < -0.50]
    if len(extreme_down) > 0:
        print(f"Returns < -50%: {len(extreme_down)}")
        for idx, ret in extreme_down.items():
            print(f"  {idx.date()}: {ret*100:.1f}%")

    # Zero or negative prices
    bad_prices = df[df['close'] <= 0]
    if len(bad_prices) > 0:
        print(f"Zero/negative prices: {len(bad_prices)} ✗")
    else:
        print("Zero/negative prices: None ✓")

    # Sudden reversals (potential data errors)
    if len(returns) > 2:
        # Two consecutive days with >30% moves in opposite directions
        next_ret = returns.shift(-1)
        reversals = ((returns > 0.30) & (next_ret < -0.30)) | ((returns < -0.30) & (next_ret > 0.30))
        rev_count = reversals.sum()
        if rev_count > 0:
            print(f"Suspicious reversals (>30% opposite moves): {rev_count}")
        else:
            print("Suspicious reversals: None ✓")

# =============================================================================
# STEP 4: CROSS-VALIDATE AGAINST EXTERNAL SOURCE
# =============================================================================

print("\n" + "=" * 80)
print("STEP 4: CROSS-VALIDATION")
print("=" * 80)

print("\n--- Latest Data Point Comparison ---")
for instr in ['BTC', 'ETH']:
    df = load_stitched_price(instr)
    if len(df) > 0:
        latest_date = df.index.max()
        latest_price = df.loc[latest_date, 'close']
        print(f"\n{instr}:")
        print(f"  Our latest: {latest_date.date()} @ ${latest_price:,.0f}")
        print(f"  (Compare to CoinGecko/CoinMarketCap to verify)")

# =============================================================================
# STEP 5: VOLATILITY SANITY CHECK
# =============================================================================

print("\n" + "=" * 80)
print("STEP 5: VOLATILITY SANITY CHECK")
print("=" * 80)

print("\n--- Annual Volatility Calculation ---")
print(f"\n{'Instrument':<10} {'Vol (Full)':<15} {'Vol (2020-24)':<15} {'Expected':<15} {'Match?'}")
print("-" * 60)

expected_vols = {
    'BTC': (0.50, 0.80),  # 50-80%
    'ETH': (0.60, 1.00),  # 60-100%
    'SOL': (0.80, 1.50),  # 80-150%
    'LINK': (0.70, 1.20),  # 70-120%
    'ADA': (0.60, 1.20),  # 60-120%
}

for instr in instruments_to_check:
    df = load_stitched_price(instr)
    if len(df) == 0:
        continue

    returns = df['close'].pct_change().dropna()

    # Full history vol
    full_vol = returns.std() * np.sqrt(252)

    # 2020-2024 vol
    mask = (df.index >= '2020-01-01') & (df.index <= '2024-12-31')
    recent_returns = df[mask]['close'].pct_change().dropna()
    recent_vol = recent_returns.std() * np.sqrt(252) if len(recent_returns) > 100 else np.nan

    exp_low, exp_high = expected_vols.get(instr, (0.5, 1.5))
    match = "✓" if exp_low <= full_vol <= exp_high else "✗"

    print(f"{instr:<10} {full_vol*100:>12.1f}% {recent_vol*100:>13.1f}% {exp_low*100:.0f}-{exp_high*100:.0f}% {match:>10}")

# =============================================================================
# STEP 6: LUNA/FTT CRASH VERIFICATION
# =============================================================================

print("\n" + "=" * 80)
print("STEP 6: CRASH EVENT VERIFICATION")
print("=" * 80)

# Check if LUNA/FTT are in our data and show crashes
for token, crash_date, crash_desc in [
    ('LUNA', '2022-05-12', 'Terra collapse'),
    ('FTT', '2022-11-09', 'FTX collapse'),
]:
    df = load_stitched_price(token)
    print(f"\n--- {token} ({crash_desc}) ---")

    if len(df) == 0:
        print(f"  {token} not in stitched data (OK - may have been excluded)")
        continue

    crash = pd.Timestamp(crash_date)

    # Get prices around crash
    mask = (df.index >= crash - pd.Timedelta(days=30)) & (df.index <= crash + pd.Timedelta(days=30))
    crash_period = df[mask]

    if len(crash_period) > 0:
        pre_crash = crash_period.iloc[0]['close']
        min_price = crash_period['close'].min()
        min_date = crash_period['close'].idxmin()

        print(f"  Pre-crash (30d before): ${pre_crash:,.2f}")
        print(f"  Minimum price: ${min_price:,.4f} on {min_date.date()}")
        print(f"  Crash magnitude: {(min_price - pre_crash) / pre_crash * 100:.1f}%")

        if (min_price / pre_crash) < 0.1:  # >90% crash
            print("  ✓ Crash properly captured")
        else:
            print("  ✗ Crash may not be fully captured")

# =============================================================================
# STEP 7: COMPARE DATA SOURCES (if multiple exist)
# =============================================================================

print("\n" + "=" * 80)
print("STEP 7: SOURCE COMPARISON")
print("=" * 80)

print("\n--- Comparing Stitched vs Kraken Source ---")

for instr, kraken_symbol in [('BTC', 'XBTUSD'), ('ETH', 'ETHUSD')]:
    stitched = load_stitched_price(instr)

    # Load Kraken source
    kraken_path = os.path.join(KRAKEN_DIR, f"{kraken_symbol}_1440.csv")
    if not os.path.exists(kraken_path):
        kraken_path = os.path.join(KRAKEN_DIR, f"{kraken_symbol}.csv")

    if os.path.exists(kraken_path):
        try:
            kraken_df = pd.read_csv(kraken_path)
            # Kraken format varies, try to parse
            if 'timestamp' in kraken_df.columns:
                kraken_df['date'] = pd.to_datetime(kraken_df['timestamp'], unit='s')
            elif 'time' in kraken_df.columns:
                kraken_df['date'] = pd.to_datetime(kraken_df['time'], unit='s')
            else:
                kraken_df['date'] = pd.to_datetime(kraken_df.iloc[:, 0], unit='s')

            kraken_df = kraken_df.set_index('date')

            # Get close price column
            if 'close' in kraken_df.columns:
                kraken_close = kraken_df['close']
            else:
                kraken_close = kraken_df.iloc[:, 3]  # Usually 4th column is close

            # Resample to daily if needed
            kraken_daily = kraken_close.resample('D').last().dropna()
            kraken_daily.index = pd.to_datetime(kraken_daily.index.date)

            # Compare
            common = stitched.index.intersection(kraken_daily.index)
            if len(common) > 100:
                stitched_prices = stitched.loc[common, 'close']
                kraken_prices = kraken_daily.loc[common]

                corr = stitched_prices.corr(kraken_prices)
                mean_diff = ((stitched_prices - kraken_prices) / kraken_prices).abs().mean() * 100

                print(f"\n{instr}:")
                print(f"  Overlapping days: {len(common)}")
                print(f"  Correlation: {corr:.6f}")
                print(f"  Mean absolute difference: {mean_diff:.2f}%")

                if corr > 0.999:
                    print("  ✓ Data matches source")
                else:
                    print("  ✗ Possible discrepancy!")
            else:
                print(f"\n{instr}: Insufficient overlap ({len(common)} days)")
        except Exception as e:
            print(f"\n{instr}: Error loading Kraken data: {e}")
    else:
        print(f"\n{instr}: Kraken source file not found at {kraken_path}")

# =============================================================================
# SUMMARY
# =============================================================================

print("\n" + "=" * 80)
print("AUDIT SUMMARY")
print("=" * 80)

print("""
PRICE DATA AUDIT RESULTS:

1. DATA SOURCES:
   - Stitched data available in /data/crypto/stitched/
   - Kraken OHLCVT source data available
   - Coinmetrics extension data available

2. MAGNITUDE CHECKS:
   - Review the key date verification table above
   - Prices should be within ±20% of expected values

3. DATA QUALITY:
   - Check for gaps, duplicates, outliers listed above
   - Crypto trades 24/7, minimal gaps expected

4. VOLATILITY:
   - BTC: Should be 50-80% annual
   - ETH: Should be 60-100% annual
   - Altcoins: 60-150% typical

5. CRASH EVENTS:
   - LUNA/FTT crashes should show >90% declines if included

6. SOURCE VALIDATION:
   - Stitched vs Kraken correlation should be >0.999

OVERALL: Review flags above. If all checks pass, price data is trustworthy.
""")
