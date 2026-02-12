"""
DIAGNOSTIC PART 2: Check average forecast magnitudes
====================================================
"""

import os
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, "/Users/nathanieldavis/pysystemtrade")

from sysquant.estimators.vol import robust_vol_calc

STITCHED_DIR = "/Users/nathanieldavis/pysystemtrade/data/crypto/stitched"

# Configuration
CAPITAL = 10000
VOL_TARGET = 0.25
DAYS_PER_YEAR = 365
FORECAST_DIV_MULTIPLIER = 1.35
FORECAST_CAP = 20.0

def load_prices(symbol):
    path = os.path.join(STITCHED_DIR, f"{symbol}_price.csv")
    if not os.path.exists(path):
        return pd.Series(dtype=float)
    df = pd.read_csv(path, parse_dates=['date'])
    df = df.set_index('date')
    df.index = pd.to_datetime(df.index.date)
    prices = df['close'].astype(float)
    return prices[~prices.index.duplicated(keep='last')].sort_index()


def ewmac(prices, Lfast, Lslow):
    fast_ma = prices.ewm(span=Lfast, min_periods=Lfast).mean()
    slow_ma = prices.ewm(span=Lslow, min_periods=Lslow).mean()
    vol = robust_vol_calc(prices)
    return (fast_ma - slow_ma) / vol


def breakout(prices, lookback):
    smooth = max(int(lookback / 4.0), 1)
    roll_max = prices.rolling(lookback, min_periods=int(np.ceil(lookback / 2.0))).max()
    roll_min = prices.rolling(lookback, min_periods=int(np.ceil(lookback / 2.0))).min()
    roll_mean = (roll_max + roll_min) / 2.0
    raw = 40.0 * ((prices - roll_mean) / (roll_max - roll_min))
    return raw.ewm(span=smooth, min_periods=int(np.ceil(smooth / 2.0))).mean()


FORECAST_SCALARS = {
    'ewmac8_32': 5.3,
    'ewmac16_64': 3.75,
    'ewmac32_128': 2.65,
    'ewmac64_256': 1.87,
    'breakout10': 0.8,
    'breakout20': 0.85,
    'breakout40': 0.9,
    'breakout80': 0.9,
}


def calculate_forecasts(prices):
    """Calculate all 8 forecasts for a price series."""
    forecasts = {}

    # EWMAC
    for (Lfast, Lslow), name in [((8, 32), 'ewmac8_32'),
                                  ((16, 64), 'ewmac16_64'),
                                  ((32, 128), 'ewmac32_128'),
                                  ((64, 256), 'ewmac64_256')]:
        raw = ewmac(prices, Lfast, Lslow)
        scaled = raw * FORECAST_SCALARS[name]
        forecasts[name] = scaled.clip(-FORECAST_CAP, FORECAST_CAP)

    # Breakout
    for lookback, name in [(10, 'breakout10'), (20, 'breakout20'),
                           (40, 'breakout40'), (80, 'breakout80')]:
        raw = breakout(prices, lookback)
        scaled = raw * FORECAST_SCALARS[name]
        forecasts[name] = scaled.clip(-FORECAST_CAP, FORECAST_CAP)

    return forecasts


print("=" * 80)
print("FORECAST MAGNITUDE ANALYSIS")
print("=" * 80)

# Analyze BTC forecasts
btc_prices = load_prices('BTC')
btc_forecasts = calculate_forecasts(btc_prices)

print("\n1. BTC INDIVIDUAL FORECAST STATISTICS")
print("-" * 60)
print(f"{'Rule':<15} {'Mean':>10} {'Std':>10} {'Avg|F|':>10} {'Avg|F|/10':>10}")
print("-" * 60)

for name, fc in btc_forecasts.items():
    fc = fc.dropna()
    print(f"{name:<15} {fc.mean():>10.2f} {fc.std():>10.2f} {fc.abs().mean():>10.2f} {fc.abs().mean()/10:>10.2f}")

# Combined forecast
fc_df = pd.DataFrame(btc_forecasts).dropna()
combined = fc_df.mean(axis=1) * FORECAST_DIV_MULTIPLIER
combined = combined.clip(-FORECAST_CAP, FORECAST_CAP)

print(f"\n{'Combined':<15} {combined.mean():>10.2f} {combined.std():>10.2f} {combined.abs().mean():>10.2f} {combined.abs().mean()/10:>10.2f}")

print("\n2. THE PROBLEM: AVERAGE |FORECAST| IS NOT 10")
print("-" * 60)

avg_abs_forecast = combined.abs().mean()
print(f"""
The combined forecast has average absolute value of {avg_abs_forecast:.2f}, not 10.

If avg|forecast| = {avg_abs_forecast:.2f}, then average position is scaled by {avg_abs_forecast/10:.2f}
instead of 1.0 (which would occur at avg|forecast| = 10).

This means realized volatility will be:
  target_vol × (avg|forecast| / 10) = 25% × {avg_abs_forecast/10:.2f} = {25 * avg_abs_forecast/10:.1f}%

This explains much of the low volatility!
""")

# Check if this is consistent across instruments
print("\n3. AVERAGE |FORECAST| BY INSTRUMENT")
print("-" * 60)

instruments = ['BTC', 'ETH', 'LTC', 'XRP', 'DOGE', 'XLM', 'ADA', 'LINK']
print(f"{'Instrument':<12} {'Avg|Combined|':>15} {'Scaling Factor':>15}")
print("-" * 45)

scaling_factors = []
for instr in instruments:
    prices = load_prices(instr)
    if len(prices) < 500:
        continue

    forecasts = calculate_forecasts(prices)
    fc_df = pd.DataFrame(forecasts).dropna()
    if len(fc_df) == 0:
        continue

    combined = fc_df.mean(axis=1) * FORECAST_DIV_MULTIPLIER
    combined = combined.clip(-FORECAST_CAP, FORECAST_CAP)

    avg_abs = combined.abs().mean()
    scaling = avg_abs / 10
    scaling_factors.append(scaling)

    print(f"{instr:<12} {avg_abs:>15.2f} {scaling:>15.2f}")

avg_scaling = np.mean(scaling_factors)
print(f"\nAverage scaling factor: {avg_scaling:.2f}")
print(f"Expected portfolio vol: 25% × {avg_scaling:.2f} = {25 * avg_scaling:.1f}%")

print("\n" + "=" * 80)
print("4. WHY IS AVG|FORECAST| < 10?")
print("=" * 80)

print("""
The forecast scalars are designed so that avg|raw_forecast| × scalar = 10.

But when we COMBINE forecasts, they can partially cancel:
- If EWMAC says +10 and Breakout says -10, combined = 0
- The average of |+10| and |-10| is 10
- But the |average| is 0

This is a fundamental property of combining negatively correlated signals.

SOLUTION OPTIONS:

A) ACCEPT LOWER VOL: The system is working as designed. When signals
   disagree, positions are smaller. This is a feature, not a bug.
   Pro: Reduces risk when signals conflict
   Con: Underuses risk budget

B) RESCALE COMBINED FORECAST: Multiply combined forecast by another
   scalar to achieve avg|combined| = 10.
   Pro: Achieves target vol
   Con: Amplifies positions when signals agree (could exceed caps)

C) USE ABSOLUTE FORECASTS: Size positions based on max(|EWMAC|, |Breakout|)
   instead of their average.
   Pro: Always takes a position
   Con: Ignores valuable information when signals conflict

D) CARVER'S APPROACH: Accept that vol targeting is approximate.
   The 25% is a TARGET, not a guarantee. Realized vol will vary.
   In periods of signal agreement, vol will be higher.
   In periods of signal disagreement, vol will be lower.
""")

print("\n" + "=" * 80)
print("5. CHECKING THE CORRELATION BETWEEN SIGNAL TYPES")
print("=" * 80)

# Check correlation between EWMAC and Breakout
btc_ewmac_avg = pd.DataFrame({
    'ewmac8_32': btc_forecasts['ewmac8_32'],
    'ewmac16_64': btc_forecasts['ewmac16_64'],
    'ewmac32_128': btc_forecasts['ewmac32_128'],
    'ewmac64_256': btc_forecasts['ewmac64_256'],
}).dropna().mean(axis=1)

btc_breakout_avg = pd.DataFrame({
    'breakout10': btc_forecasts['breakout10'],
    'breakout20': btc_forecasts['breakout20'],
    'breakout40': btc_forecasts['breakout40'],
    'breakout80': btc_forecasts['breakout80'],
}).dropna().mean(axis=1)

common = btc_ewmac_avg.index.intersection(btc_breakout_avg.index)
ewmac_aligned = btc_ewmac_avg.loc[common]
breakout_aligned = btc_breakout_avg.loc[common]

corr = ewmac_aligned.corr(breakout_aligned)
print(f"\nCorrelation between EWMAC avg and Breakout avg: {corr:.3f}")

# When they disagree
disagree_mask = (ewmac_aligned > 0) != (breakout_aligned > 0)
pct_disagree = disagree_mask.mean() * 100
print(f"Percentage of time they disagree on direction: {pct_disagree:.1f}%")

print("\n" + "=" * 80)
print("6. REVISED POSITION SIZING OPTIONS")
print("=" * 80)

print("""
Given that avg|combined_forecast| ≈ {:.1f} instead of 10, we have two choices:

OPTION 1: ADD ANOTHER MULTIPLIER (forecast rescaling)
---------------------------------------------------------
Scale the combined forecast so avg|combined| = 10.
Rescale factor = 10 / {:.1f} = {:.2f}

New formula:
  combined_forecast = avg_forecast × FDM × RESCALE_FACTOR
  where RESCALE_FACTOR = 10 / avg|combined|

This would give:
  position = subsystem × IDM × weight × (rescaled_forecast / 10)

At forecast = +/-10 (after rescaling), position would be:
  vol_contribution = 25% × IDM × weight = 25% × 1.26 × 0.067 = 2.1%

This achieves target vol when signals agree.

OPTION 2: INSTRUMENT WEIGHT IS THE PROBLEM
---------------------------------------------------------
Looking at the math again:

At forecast = 10 (average absolute):
  subsystem_position_value = capital × vol_target / instrument_vol
                          = $10,000 × 25% / 100%
                          = $2,500

  After IDM (1.26): $3,150
  After weight (0.067): $210

  Vol contribution = $210 × 100% = $210 = 2.1% of capital

With 15 instruments each at 2.1% vol, and 60% correlation:
  portfolio_vol = sqrt(15 × 2.1%² × (1 + 14×0.6))
               = sqrt(15 × 0.044% × 9.4)
               = sqrt(6.2%)
               = 24.9%  ✓

THE MATH IS CORRECT!

The issue is that avg|forecast| < 10, so realized vol is lower.
This is expected behavior - it's a feature of combining signals.

RECOMMENDATION: Accept ~5% vol or rescale forecasts
""".format(avg_abs_forecast, avg_abs_forecast, 10/avg_abs_forecast))

# Calculate what rescale factor would achieve
print("\n" + "=" * 80)
print("7. IF WE WANT TO FORCE 25% VOL")
print("=" * 80)

# What rescale factor do we need?
# realized_vol = target_vol × avg|forecast|/10
# target_vol = realized_vol × 10/avg|forecast|
# rescale = 10 / avg|combined|
rescale_factor = 10 / avg_abs_forecast

print(f"""
Current average |combined forecast|: {avg_abs_forecast:.2f}
Rescale factor needed: {rescale_factor:.2f}

With this rescale factor:
  new_combined = combined × {rescale_factor:.2f}
  new avg|combined| = {avg_abs_forecast:.2f} × {rescale_factor:.2f} = 10.0

But this would cause problems:
- When signals agree strongly (both +20), rescaled = 20 × {rescale_factor:.2f} = {20*rescale_factor:.1f}
- This exceeds the ±20 cap, causing clipping
- Clipping would reduce the effective rescaling

BETTER APPROACH: Increase vol target or accept lower realized vol
""")
