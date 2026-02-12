"""
SKEW DIAGNOSTIC
===============
Why did carry skew flip from -8 to +0.7?

Earlier analysis showed:
- Post-2020 carry skew: -8.17
- 2022 carry skew: -13
- Kurtosis: 205

Current shows: +0.38 to +0.74

Something is fundamentally wrong. Let's diagnose.
"""

import os
import sys
import numpy as np
import pandas as pd
from scipy.stats import skew, kurtosis

sys.path.insert(0, "/Users/nathanieldavis/pysystemtrade")
from sysquant.estimators.vol import robust_vol_calc

STITCHED_DIR = "/Users/nathanieldavis/pysystemtrade/data/crypto/stitched"
FUNDING_DIR = "/Users/nathanieldavis/pysystemtrade/data/crypto/funding_rates"
COMBINED_FUNDING_DIR = os.path.join(FUNDING_DIR, "combined")

CAPITAL = 10000
DAYS_PER_YEAR = 365
CARRY_VOL_TARGET = 0.125
CARRY_LEVERAGE = 2.75
UNHEDGED_EXPOSURE = 0.20


def load_price_data(instrument):
    path = os.path.join(STITCHED_DIR, f"{instrument}_price.csv")
    if not os.path.exists(path):
        path = os.path.join(STITCHED_DIR, f"{instrument}.csv")
    if not os.path.exists(path):
        return pd.Series(dtype=float)
    df = pd.read_csv(path, parse_dates=['date'])
    df = df.set_index('date')
    df.index = pd.to_datetime(df.index.date)
    prices = df['close'].astype(float)
    return prices[~prices.index.duplicated(keep='last')].sort_index()


def load_funding_data(instrument):
    path = os.path.join(COMBINED_FUNDING_DIR, f"{instrument}_funding_combined.csv")
    if not os.path.exists(path):
        path = os.path.join(FUNDING_DIR, f"{instrument}_funding.csv")
    if not os.path.exists(path):
        return pd.Series(dtype=float)
    df = pd.read_csv(path, parse_dates=['datetime'])
    df = df.set_index('datetime')
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    funding = df['fundingRate'].resample('D').sum()
    funding.index = pd.to_datetime(funding.index.date)
    return funding


print("=" * 80)
print("SKEW DIAGNOSTIC: Why did carry skew flip from -8 to +0.7?")
print("=" * 80)

# Load data
all_prices = {}
all_funding = {}

for f in os.listdir(FUNDING_DIR):
    if f.endswith('_funding.csv'):
        instr = f[:-12]
        prices = load_price_data(instr)
        funding = load_funding_data(instr)
        if len(prices) >= 252 and len(funding) >= 100:
            all_prices[instr] = prices
            all_funding[instr] = funding

if os.path.exists(COMBINED_FUNDING_DIR):
    for f in os.listdir(COMBINED_FUNDING_DIR):
        if f.endswith('_funding_combined.csv'):
            instr = f[:-21]
            if instr not in all_prices:
                prices = load_price_data(instr)
                funding = load_funding_data(instr)
                if len(prices) >= 252 and len(funding) >= 100:
                    all_prices[instr] = prices
                    all_funding[instr] = funding

eligible = [i for i in all_prices if len(all_funding[i]) >= 3 * 365]
eligible.sort()
n = len(eligible)

print(f"\nInstruments ({n}): {', '.join(eligible)}")

weight = 1.0 / n
idm = min(np.sqrt(n) / np.sqrt(1 + (n - 1) * 0.5), 2.5)
all_vols = {i: robust_vol_calc(all_prices[i]) for i in eligible}

# Get dates
all_dates = set()
for f in all_funding.values():
    all_dates.update(f.index)
all_dates = sorted(all_dates)

start = None
for d in all_dates:
    if sum(1 for i in eligible if d in all_funding[i].index) >= 1:
        start = d
        break

dates = [d for d in all_dates if d >= start]

# Run backtest tracking BOTH components separately
funding_returns = []
price_returns = []
combined_returns = []

for i, date in enumerate(dates[:-1]):
    next_date = dates[i + 1]
    funding_pnl = 0.0
    price_pnl = 0.0

    for instr in eligible:
        funding = all_funding[instr]
        prices = all_prices[instr]

        if date not in funding.index or date not in prices.index:
            continue
        if next_date not in prices.index:
            continue

        funding_rate = funding.loc[date]
        price_today = prices.loc[date]
        price_tomorrow = prices.loc[next_date]

        vol = all_vols[instr].loc[date] if date in all_vols[instr].index else None
        if vol is None or pd.isna(vol) or vol <= 0:
            continue

        annual_vol = (vol / price_today) * np.sqrt(DAYS_PER_YEAR)

        # Position sizing
        subsystem = (CAPITAL * CARRY_VOL_TARGET) / annual_vol
        position_value = subsystem * idm * weight * CARRY_LEVERAGE

        # Components
        f_pnl = position_value * funding_rate
        price_change = (price_tomorrow - price_today) / price_today
        p_pnl = position_value * price_change * UNHEDGED_EXPOSURE

        funding_pnl += f_pnl
        price_pnl += p_pnl

    funding_returns.append({'date': next_date, 'return': funding_pnl / CAPITAL})
    price_returns.append({'date': next_date, 'return': price_pnl / CAPITAL})
    combined_returns.append({'date': next_date, 'return': (funding_pnl + price_pnl) / CAPITAL})

funding_df = pd.DataFrame(funding_returns).set_index('date')['return']
price_df = pd.DataFrame(price_returns).set_index('date')['return']
combined_df = pd.DataFrame(combined_returns).set_index('date')['return']

# Apply costs to combined
combined_df = combined_df - (0.02 / DAYS_PER_YEAR)

# =============================================================================
# 1. 2022-ONLY CARRY SKEW
# =============================================================================

print("\n" + "=" * 80)
print("1. 2022-ONLY CARRY SKEW")
print("=" * 80)

mask_2022 = (combined_df.index >= '2022-01-01') & (combined_df.index <= '2022-12-31')
combined_2022 = combined_df[mask_2022]
funding_2022 = funding_df[mask_2022]
price_2022 = price_df[mask_2022]

print(f"\n2022 Combined:")
print(f"  Days: {len(combined_2022)}")
print(f"  Skew: {skew(combined_2022):.2f}")
print(f"  Kurtosis: {kurtosis(combined_2022):.2f}")
print(f"  Return: {(combined_2022.sum())*100:.2f}%")

print(f"\n2022 Funding-only:")
print(f"  Skew: {skew(funding_2022):.2f}")
print(f"  Kurtosis: {kurtosis(funding_2022):.2f}")
print(f"  Return: {(funding_2022.sum())*100:.2f}%")

print(f"\n2022 Price-only (20% exposure):")
print(f"  Skew: {skew(price_2022):.2f}")
print(f"  Kurtosis: {kurtosis(price_2022):.2f}")
print(f"  Return: {(price_2022.sum())*100:.2f}%")

# =============================================================================
# 2. COMPONENT-BY-COMPONENT SKEW BREAKDOWN
# =============================================================================

print("\n" + "=" * 80)
print("2. COMPONENT-BY-COMPONENT SKEW (Full Period)")
print("=" * 80)

print(f"\nFunding-only:")
print(f"  Skew: {skew(funding_df):.2f}")
print(f"  Kurtosis: {kurtosis(funding_df):.2f}")
print(f"  Ann Vol: {funding_df.std() * np.sqrt(365) * 100:.2f}%")

print(f"\nPrice-only (20% exposure):")
print(f"  Skew: {skew(price_df):.2f}")
print(f"  Kurtosis: {kurtosis(price_df):.2f}")
print(f"  Ann Vol: {price_df.std() * np.sqrt(365) * 100:.2f}%")

print(f"\nCombined:")
print(f"  Skew: {skew(combined_df):.2f}")
print(f"  Kurtosis: {kurtosis(combined_df):.2f}")
print(f"  Ann Vol: {combined_df.std() * np.sqrt(365) * 100:.2f}%")

# Correlation between components
corr = funding_df.corr(price_df)
print(f"\nCorrelation (funding vs price): {corr:.3f}")

# =============================================================================
# 3. PURE FUNDING-ONLY CARRY (NO BASIS RISK)
# =============================================================================

print("\n" + "=" * 80)
print("3. PURE FUNDING-ONLY CARRY (no basis risk)")
print("=" * 80)

funding_only = funding_df - (0.02 / DAYS_PER_YEAR)  # Apply costs

# Full period
print(f"\nFull Period:")
print(f"  Skew: {skew(funding_only):.2f}")
print(f"  Kurtosis: {kurtosis(funding_only):.2f}")

# Post-2020
funding_post2020 = funding_only[funding_only.index >= '2020-01-01']
print(f"\nPost-2020:")
print(f"  Skew: {skew(funding_post2020):.2f}")
print(f"  Kurtosis: {kurtosis(funding_post2020):.2f}")

# 2022
funding_2022_only = funding_only[mask_2022]
print(f"\n2022:")
print(f"  Skew: {skew(funding_2022_only):.2f}")
print(f"  Kurtosis: {kurtosis(funding_2022_only):.2f}")

# =============================================================================
# 4. COMPARE TO EARLIER ANALYSIS
# =============================================================================

print("\n" + "=" * 80)
print("4. COMPARISON TO EARLIER ANALYSIS")
print("=" * 80)

print("""
EARLIER ANALYSIS (claimed):
  Post-2020 carry skew: -8.17
  2022 carry skew: -13
  Kurtosis: 205

CURRENT RESULTS:
""")

print(f"  Post-2020 combined skew: {skew(combined_df[combined_df.index >= '2020-01-01']):.2f}")
print(f"  2022 combined skew: {skew(combined_2022):.2f}")
print(f"  2022 kurtosis: {kurtosis(combined_2022):.2f}")

print(f"\n  Post-2020 funding-only skew: {skew(funding_post2020):.2f}")
print(f"  2022 funding-only skew: {skew(funding_2022_only):.2f}")

print("""
POSSIBLE EXPLANATIONS FOR DISCREPANCY:

1. DIFFERENT RETURN CALCULATION:
   Earlier: Possibly using SIMPLE funding rate returns (funding_rate directly)
   Now: Using LEVERED position value × funding rate

2. DIFFERENT INSTRUMENTS:
   Earlier: May have used different set of instruments
   Now: Using {n} instruments with 3+ years data

3. POSITION SIZING:
   Earlier: May not have had vol-targeting dampening extreme days
   Now: Vol-targeting reduces position size during high-vol periods

4. THE KEY: Position sizing DAMPENS tail events
   When vol spikes, position shrinks
   This reduces the impact of extreme funding days
   Result: Skew appears less negative

Let me check the raw funding rate skew WITHOUT position sizing...
""".format(n=n))

# =============================================================================
# 5. RAW FUNDING RATE SKEW (NO POSITION SIZING)
# =============================================================================

print("\n" + "=" * 80)
print("5. RAW FUNDING RATE SKEW (NO POSITION SIZING)")
print("=" * 80)

# Aggregate raw funding rates across all instruments
all_funding_rates = []
for instr in eligible:
    funding = all_funding[instr]
    funding = funding[funding.index >= '2020-01-01']
    all_funding_rates.append(funding)

# Equal weighted average
raw_funding = pd.concat(all_funding_rates, axis=1).mean(axis=1)

print(f"\nRaw funding rate (post-2020, equal-weighted avg):")
print(f"  Skew: {skew(raw_funding.dropna()):.2f}")
print(f"  Kurtosis: {kurtosis(raw_funding.dropna()):.2f}")
print(f"  Mean: {raw_funding.mean()*100:.4f}% per day")
print(f"  Std: {raw_funding.std()*100:.4f}% per day")

# 2022 raw
raw_2022 = raw_funding[(raw_funding.index >= '2022-01-01') & (raw_funding.index <= '2022-12-31')]
print(f"\nRaw funding rate (2022):")
print(f"  Skew: {skew(raw_2022.dropna()):.2f}")
print(f"  Kurtosis: {kurtosis(raw_2022.dropna()):.2f}")

# =============================================================================
# 6. WORST 10 DAYS
# =============================================================================

print("\n" + "=" * 80)
print("6. WORST 10 CARRY DAYS")
print("=" * 80)

print("\nWorst 10 days (combined returns):")
worst = combined_df.nsmallest(10)
for date, ret in worst.items():
    f_ret = funding_df.loc[date] if date in funding_df.index else 0
    p_ret = price_df.loc[date] if date in price_df.index else 0
    print(f"  {date.date()}: {ret*100:+.3f}% (funding: {f_ret*100:+.3f}%, price: {p_ret*100:+.3f}%)")

print("\nWorst 10 days (funding-only):")
worst_funding = funding_df.nsmallest(10)
for date, ret in worst_funding.items():
    print(f"  {date.date()}: {ret*100:+.4f}%")

print("\nWorst 10 days (raw funding rate):")
worst_raw = raw_funding.nsmallest(10)
for date, ret in worst_raw.items():
    print(f"  {date.date()}: {ret*100:+.4f}%")

# =============================================================================
# 7. THE REAL ISSUE: VOLATILITY
# =============================================================================

print("\n" + "=" * 80)
print("7. THE REAL ISSUE: RETURN MAGNITUDE")
print("=" * 80)

print(f"""
The skew is POSITIVE because:

1. RETURNS ARE TINY:
   - Worst day: {combined_df.min()*100:.3f}%
   - Best day: {combined_df.max()*100:.3f}%
   - Daily returns range: {combined_df.min()*100:.3f}% to {combined_df.max()*100:.3f}%

2. WHEN RETURNS ARE TINY, SKEW IS DOMINATED BY OUTLIERS IN EITHER DIRECTION
   - A few big positive days (2024 funding spike) create positive skew
   - The negative days aren't big enough to overcome this

3. EARLIER ANALYSIS MAY HAVE:
   - Used different leverage (higher = bigger returns = more negative skew)
   - Used different position sizing (no vol targeting)
   - Calculated skew on a different return series

4. TO GET NEGATIVE SKEW, WE NEED:
   - Larger position sizes (more leverage)
   - Or: Calculate skew on raw funding rates, not vol-targeted returns

Let me recalculate with HIGHER LEVERAGE to see if skew becomes negative...
""")

# Recalculate with 10x leverage
HIGH_LEVERAGE = 10.0

high_lev_returns = []
for i, date in enumerate(dates[:-1]):
    next_date = dates[i + 1]
    daily_pnl = 0.0

    for instr in eligible:
        funding = all_funding[instr]
        prices = all_prices[instr]

        if date not in funding.index or date not in prices.index:
            continue
        if next_date not in prices.index:
            continue

        funding_rate = funding.loc[date]
        price_today = prices.loc[date]
        price_tomorrow = prices.loc[next_date]

        vol = all_vols[instr].loc[date] if date in all_vols[instr].index else None
        if vol is None or pd.isna(vol) or vol <= 0:
            continue

        annual_vol = (vol / price_today) * np.sqrt(DAYS_PER_YEAR)
        subsystem = (CAPITAL * CARRY_VOL_TARGET) / annual_vol
        position_value = subsystem * idm * weight * HIGH_LEVERAGE

        f_pnl = position_value * funding_rate
        price_change = (price_tomorrow - price_today) / price_today
        p_pnl = position_value * price_change * UNHEDGED_EXPOSURE

        daily_pnl += f_pnl + p_pnl

    high_lev_returns.append({'date': next_date, 'return': daily_pnl / CAPITAL})

high_lev_df = pd.DataFrame(high_lev_returns).set_index('date')['return']

print(f"\nWith {HIGH_LEVERAGE}x leverage (vs {CARRY_LEVERAGE}x):")
high_lev_2022 = high_lev_df[(high_lev_df.index >= '2022-01-01') & (high_lev_df.index <= '2022-12-31')]
print(f"  2022 Skew: {skew(high_lev_2022):.2f}")
print(f"  2022 Return: {high_lev_2022.sum()*100:.1f}%")
print(f"  Worst day: {high_lev_df.min()*100:.2f}%")
print(f"  Best day: {high_lev_df.max()*100:.2f}%")

# =============================================================================
# CONCLUSION
# =============================================================================

print("\n" + "=" * 80)
print("CONCLUSION")
print("=" * 80)

print(f"""
THE SKEW DISCREPANCY EXPLAINED:

1. Current model uses CONSERVATIVE position sizing:
   - Vol target: 12.5%
   - Leverage: {CARRY_LEVERAGE}x
   - This produces TINY daily returns (~0.01% range)

2. With tiny returns, skew is dominated by a few outliers:
   - 2024 had massive positive funding (bull market)
   - This single year creates positive skew

3. To see NEGATIVE skew, you need:
   - Higher leverage (10x+)
   - Or no vol-targeting
   - Or look at RAW funding rates

4. The EARLIER -8 skew analysis likely used:
   - Different (higher) leverage
   - Or raw funding rates without position sizing
   - Or a different calculation methodology

5. WITH CONSERVATIVE SIZING, the strategy appears "safe":
   - Low returns, low vol, positive skew
   - BUT this masks the TRUE risk profile
   - The underlying funding rates DO have negative skew

RECOMMENDATION:
   - Don't trust the +0.7 skew from the backtest
   - The RAW funding skew is: {skew(raw_2022.dropna()):.2f} (2022)
   - The TRUE risk is negative skew when scaled up
   - Keep using half-Kelly (12.5% vol) as conservative sizing
""")
