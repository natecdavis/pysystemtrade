"""
DIAGNOSTIC: Why is trend volatility 4.7% instead of 25%?
=========================================================
"""

import os
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, "/Users/nathanieldavis/pysystemtrade")

from sysquant.estimators.vol import robust_vol_calc

STITCHED_DIR = "/Users/nathanieldavis/pysystemtrade/data/crypto/stitched"

# Configuration (should match corrected_backtest.py)
CAPITAL = 10000
VOL_TARGET = 0.25  # 25%
DAYS_PER_YEAR = 365
FORECAST_DIV_MULTIPLIER = 1.35
N_INSTRUMENTS = 15
INSTRUMENT_WEIGHT = 1.0 / N_INSTRUMENTS
AVG_CORR = 0.6
IDM = min(np.sqrt(N_INSTRUMENTS) / np.sqrt(1 + (N_INSTRUMENTS - 1) * AVG_CORR), 2.5)

print("=" * 80)
print("VOLATILITY TARGETING DIAGNOSTIC")
print("=" * 80)

print(f"\n1. CONFIGURATION CHECK")
print(f"   Vol Target: {VOL_TARGET*100}%")
print(f"   Capital: ${CAPITAL:,}")
print(f"   N Instruments: {N_INSTRUMENTS}")
print(f"   Instrument Weight: {INSTRUMENT_WEIGHT:.4f}")
print(f"   IDM: {IDM:.3f}")
print(f"   FDM: {FORECAST_DIV_MULTIPLIER}")

# Load BTC data
btc_path = os.path.join(STITCHED_DIR, "BTC_price.csv")
df = pd.read_csv(btc_path, parse_dates=['date'])
df = df.set_index('date')
df.index = pd.to_datetime(df.index.date)
prices = df['close'].astype(float)
prices = prices[~prices.index.duplicated(keep='last')].sort_index()

# Calculate volatility
vol = robust_vol_calc(prices)

print(f"\n2. BTC DATA SAMPLE")

# Pick a few dates to analyze
sample_dates = [
    pd.Timestamp('2020-01-15'),
    pd.Timestamp('2022-01-15'),
    pd.Timestamp('2024-01-15'),
    pd.Timestamp('2025-10-01'),
]

for date in sample_dates:
    if date not in prices.index:
        # Find nearest date
        nearest = prices.index[prices.index <= date]
        if len(nearest) == 0:
            continue
        date = nearest[-1]

    if date not in vol.index:
        continue

    price = prices.loc[date]
    daily_vol = vol.loc[date]

    # Calculate annual vol as percentage
    daily_return_vol = daily_vol / price
    annual_return_vol = daily_return_vol * np.sqrt(DAYS_PER_YEAR)

    print(f"\n   Date: {date.strftime('%Y-%m-%d')}")
    print(f"   Price: ${price:,.2f}")
    print(f"   Daily Vol (price): ${daily_vol:,.2f}")
    print(f"   Daily Vol (%): {daily_return_vol*100:.2f}%")
    print(f"   Annual Vol (%): {annual_return_vol*100:.1f}%")

# Now do detailed position sizing for one date
print(f"\n" + "=" * 80)
print("3. DETAILED POSITION SIZING (BTC, 2024-01-15)")
print("=" * 80)

date = pd.Timestamp('2024-01-15')
if date not in prices.index:
    nearest = prices.index[prices.index <= date][-1]
    date = nearest

price = prices.loc[date]
daily_vol = vol.loc[date]
daily_return_vol = daily_vol / price
annual_return_vol = daily_return_vol * np.sqrt(DAYS_PER_YEAR)

# Simulate a forecast
# EWMAC forecasts with FDM applied
def ewmac(p, Lfast, Lslow):
    fast_ma = p.ewm(span=Lfast, min_periods=Lfast).mean()
    slow_ma = p.ewm(span=Lslow, min_periods=Lslow).mean()
    v = robust_vol_calc(p)
    return (fast_ma - slow_ma) / v

# Calculate individual forecasts
forecasts = {}
scalars = {
    'ewmac8_32': 5.3,
    'ewmac16_64': 3.75,
    'ewmac32_128': 2.65,
    'ewmac64_256': 1.87,
}

for (Lfast, Lslow), name in [((8, 32), 'ewmac8_32'),
                              ((16, 64), 'ewmac16_64'),
                              ((32, 128), 'ewmac32_128'),
                              ((64, 256), 'ewmac64_256')]:
    raw = ewmac(prices, Lfast, Lslow)
    scaled = raw * scalars[name]
    capped = scaled.clip(-20, 20)
    if date in capped.index:
        forecasts[name] = capped.loc[date]

# Breakout forecasts
def breakout(p, lookback):
    smooth = max(int(lookback / 4.0), 1)
    roll_max = p.rolling(lookback, min_periods=int(np.ceil(lookback / 2.0))).max()
    roll_min = p.rolling(lookback, min_periods=int(np.ceil(lookback / 2.0))).min()
    roll_mean = (roll_max + roll_min) / 2.0
    raw = 40.0 * ((p - roll_mean) / (roll_max - roll_min))
    smoothed = raw.ewm(span=smooth, min_periods=int(np.ceil(smooth / 2.0))).mean()
    return smoothed

breakout_scalars = {
    'breakout10': 0.8,
    'breakout20': 0.85,
    'breakout40': 0.9,
    'breakout80': 0.9,
}

for lookback, name in [(10, 'breakout10'), (20, 'breakout20'),
                        (40, 'breakout40'), (80, 'breakout80')]:
    raw = breakout(prices, lookback)
    scaled = raw * breakout_scalars[name]
    capped = scaled.clip(-20, 20)
    if date in capped.index:
        forecasts[name] = capped.loc[date]

print(f"\nIndividual Forecasts:")
for name, fc in forecasts.items():
    print(f"   {name}: {fc:.2f}")

# Combined forecast (equal weighted with FDM)
avg_forecast = np.mean(list(forecasts.values()))
combined_forecast = avg_forecast * FORECAST_DIV_MULTIPLIER
combined_forecast = np.clip(combined_forecast, -20, 20)

print(f"\n   Average forecast: {avg_forecast:.2f}")
print(f"   After FDM ({FORECAST_DIV_MULTIPLIER}): {combined_forecast:.2f}")

print(f"\n" + "-" * 60)
print("STEP-BY-STEP POSITION SIZING")
print("-" * 60)

print(f"\nInputs:")
print(f"   Price: ${price:,.2f}")
print(f"   Annual Vol: {annual_return_vol*100:.1f}%")
print(f"   Combined Forecast: {combined_forecast:.2f}")
print(f"   Capital: ${CAPITAL:,}")
print(f"   Vol Target: {VOL_TARGET*100}%")
print(f"   IDM: {IDM:.3f}")
print(f"   Instrument Weight: {INSTRUMENT_WEIGHT:.4f}")

# Step 1: Subsystem position (at forecast=10)
subsystem_position = (CAPITAL * VOL_TARGET) / (price * annual_return_vol)
print(f"\nStep 1: Subsystem position (at forecast=10)")
print(f"   = (capital × vol_target) / (price × annual_vol)")
print(f"   = ({CAPITAL} × {VOL_TARGET}) / ({price:.2f} × {annual_return_vol:.4f})")
print(f"   = {CAPITAL * VOL_TARGET:.2f} / {price * annual_return_vol:.2f}")
print(f"   = {subsystem_position:.6f} BTC")
print(f"   = ${subsystem_position * price:,.2f}")

# Step 2: Apply IDM
after_idm = subsystem_position * IDM
print(f"\nStep 2: After IDM")
print(f"   = {subsystem_position:.6f} × {IDM:.3f}")
print(f"   = {after_idm:.6f} BTC")
print(f"   = ${after_idm * price:,.2f}")

# Step 3: Apply instrument weight
after_weight = after_idm * INSTRUMENT_WEIGHT
print(f"\nStep 3: After instrument weight")
print(f"   = {after_idm:.6f} × {INSTRUMENT_WEIGHT:.4f}")
print(f"   = {after_weight:.6f} BTC")
print(f"   = ${after_weight * price:,.2f}")

# Step 4: Apply forecast scaling
final_position = after_weight * (combined_forecast / 10.0)
print(f"\nStep 4: After forecast scaling")
print(f"   = {after_weight:.6f} × ({combined_forecast:.2f} / 10)")
print(f"   = {final_position:.6f} BTC")
print(f"   = ${final_position * price:,.2f}")

# Check: What vol contribution does this give?
position_value = abs(final_position * price)
position_vol = position_value * annual_return_vol
print(f"\n" + "-" * 60)
print("VERIFICATION")
print("-" * 60)
print(f"   Position value: ${position_value:,.2f}")
print(f"   Position vol contribution: ${position_vol:,.2f}")
print(f"   As % of capital: {position_vol / CAPITAL * 100:.2f}%")

# Expected vol contribution
# If all instruments had the same vol contribution, portfolio vol would be:
# sqrt(n × weight² × vol² + n×(n-1) × weight² × vol² × corr)
# = weight × vol × sqrt(n × (1 + (n-1)×corr))
# = vol_target × IDM × weight × forecast/10 × sqrt(n × (1 + (n-1)×corr))
expected_per_instrument = VOL_TARGET * IDM * INSTRUMENT_WEIGHT * (combined_forecast / 10.0)
print(f"\n   Expected per-instrument vol: {expected_per_instrument * 100:.2f}% of capital")

# Portfolio vol if all instruments contribute equally
# Approximate: total vol = per_instrument × sqrt(n) × sqrt((1-corr) + corr × n) / n
# For correlated assets: total_vol ≈ n × per_instrument × sqrt(1/n + corr × (n-1)/n)
diversified_factor = np.sqrt((1 - AVG_CORR) / N_INSTRUMENTS + AVG_CORR)
expected_portfolio_vol = expected_per_instrument * N_INSTRUMENTS * diversified_factor
print(f"   Expected portfolio vol (with diversification): {expected_portfolio_vol * 100:.2f}%")

print(f"\n" + "=" * 80)
print("4. THE PROBLEM: INSTRUMENT WEIGHT IS TOO SMALL")
print("=" * 80)

print(f"""
The issue is clear from the math:

At forecast = 10:
- Subsystem position: ${subsystem_position * price:,.2f} ({subsystem_position * price / CAPITAL * 100:.1f}% of capital)
- After IDM: ${after_idm * price:,.2f}
- After instrument weight (1/{N_INSTRUMENTS}): ${after_weight * price:,.2f}

The instrument weight of 1/{N_INSTRUMENTS} = {INSTRUMENT_WEIGHT:.4f} is correct for
ALLOCATING capital, but the position sizing formula already handles this.

THE BUG: We're double-counting diversification!

Carver's formula is:
  position = (capital × vol_target × IDM × instrument_weight × forecast) /
             (10 × price × annual_vol)

The instrument_weight should represent the FRACTION of capital allocated to this instrument.
But then the position should be sized to contribute (vol_target × instrument_weight × forecast/10)
to portfolio volatility.

Currently:
- Position value: ${position_value:,.2f} ({position_value/CAPITAL*100:.1f}% of capital)
- Position vol: {position_vol/CAPITAL*100:.2f}% of capital

This is WAY below the target!

CORRECT APPROACH:
For 25% vol target with 15 instruments:
- Each instrument should contribute ~{VOL_TARGET/np.sqrt(N_INSTRUMENTS)*100:.1f}% vol (if uncorrelated)
- With 60% correlation, need ~{VOL_TARGET/(IDM)*100:.1f}% per instrument before IDM
- Position value should be ~${CAPITAL * VOL_TARGET / annual_return_vol:,.2f} before IDM
""")

print(f"\n" + "=" * 80)
print("5. CORRECTED POSITION SIZING")
print("=" * 80)

# The correct formula should NOT divide by n_instruments again
# The subsystem position already gives us a full-sized position

# Option 1: Full position per instrument, let IDM handle diversification
corrected_position = subsystem_position * IDM * (combined_forecast / 10.0)
corrected_value = abs(corrected_position * price)
corrected_vol = corrected_value * annual_return_vol

print(f"\nOption 1: Remove instrument weight from position sizing")
print(f"   Position: {corrected_position:.6f} BTC (${corrected_value:,.2f})")
print(f"   Vol contribution: {corrected_vol/CAPITAL*100:.2f}% of capital")

# Option 2: Instrument weight in capital, not position
# Position = (instrument_capital × vol_target) / (price × vol) × IDM × forecast/10
instrument_capital = CAPITAL * INSTRUMENT_WEIGHT
opt2_position = (instrument_capital * VOL_TARGET) / (price * annual_return_vol) * IDM * (combined_forecast / 10.0)
opt2_value = abs(opt2_position * price)
opt2_vol = opt2_value * annual_return_vol

print(f"\nOption 2: Instrument weight applied to capital")
print(f"   Instrument capital: ${instrument_capital:,.2f}")
print(f"   Position: {opt2_position:.6f} BTC (${opt2_value:,.2f})")
print(f"   Vol contribution: {opt2_vol/CAPITAL*100:.2f}% of capital")

# Neither of these gives us 25% total vol. Let's think about this more carefully.
print(f"\n" + "=" * 80)
print("6. UNDERSTANDING THE MATH")
print("=" * 80)

print(f"""
For a portfolio with n instruments at 25% vol target:

If we want TOTAL portfolio volatility of 25%, and instruments have correlation ρ,
then each instrument's volatility contribution needs to satisfy:

  portfolio_vol² = Σᵢ Σⱼ (wᵢ × wⱼ × σᵢ × σⱼ × ρᵢⱼ)

For equal weights (w = 1/n) and equal contributions (σ_contribution):
  portfolio_vol² = n × (1/n)² × σ² + n×(n-1) × (1/n)² × σ² × ρ
                 = σ²/n × (1 + (n-1)ρ)

So: portfolio_vol = σ/√n × √(1 + (n-1)ρ)

To get portfolio_vol = 25% with n={N_INSTRUMENTS}, ρ={AVG_CORR}:
  σ = 25% × √{N_INSTRUMENTS} / √(1 + ({N_INSTRUMENTS}-1)×{AVG_CORR})
  σ = 25% × {np.sqrt(N_INSTRUMENTS):.2f} / {np.sqrt(1 + (N_INSTRUMENTS-1)*AVG_CORR):.2f}
  σ = {VOL_TARGET * np.sqrt(N_INSTRUMENTS) / np.sqrt(1 + (N_INSTRUMENTS-1)*AVG_CORR) * 100:.1f}%

So each instrument should contribute ~{VOL_TARGET * np.sqrt(N_INSTRUMENTS) / np.sqrt(1 + (N_INSTRUMENTS-1)*AVG_CORR) * 100:.1f}%
volatility to achieve 25% portfolio vol.

This is exactly what IDM is for!
IDM = √n / √(1 + (n-1)ρ) = {IDM:.2f}

So the per-instrument vol target is:
  vol_target × IDM = {VOL_TARGET} × {IDM:.2f} = {VOL_TARGET * IDM:.2f} = {VOL_TARGET * IDM * 100:.1f}%

But we're applying instrument_weight which divides this by {N_INSTRUMENTS}!
That gives us only {VOL_TARGET * IDM / N_INSTRUMENTS * 100:.2f}% per instrument.
""")

# The fix
print(f"\n" + "=" * 80)
print("7. THE FIX")
print("=" * 80)

print(f"""
The issue is that we're applying BOTH IDM and instrument_weight.

Carver's approach (from "Systematic Trading"):
- instrument_weight determines CAPITAL allocation
- IDM adjusts positions for diversification benefit
- These are NOT applied together to position sizing

CORRECT FORMULA:
  position = (capital × vol_target × IDM) / (price × annual_vol) × (forecast / 10)

The instrument weight should only be used if we want to LIMIT exposure to
a single instrument (e.g., max 20% of capital in one asset), not to scale down
every position.

With the corrected formula:
  position = ({CAPITAL} × {VOL_TARGET} × {IDM:.2f}) / ({price:.2f} × {annual_return_vol:.4f}) × ({combined_forecast:.2f} / 10)
  position = {CAPITAL * VOL_TARGET * IDM / (price * annual_return_vol) * (combined_forecast / 10):.6f} BTC
  value = ${CAPITAL * VOL_TARGET * IDM / (price * annual_return_vol) * (combined_forecast / 10) * price:,.2f}

This position contributes:
  vol = ${CAPITAL * VOL_TARGET * IDM / (price * annual_return_vol) * (combined_forecast / 10) * price * annual_return_vol:,.2f}
  = {CAPITAL * VOL_TARGET * IDM * (combined_forecast / 10) / CAPITAL * 100:.2f}% of capital

At forecast = 10, this would be {VOL_TARGET * IDM * 100:.1f}% per instrument.
With {N_INSTRUMENTS} instruments at {AVG_CORR*100:.0f}% correlation,
portfolio vol = {VOL_TARGET * IDM * 100:.1f}% × √(1/n + ρ×(n-1)/n)
             = {VOL_TARGET * IDM * 100:.1f}% × {diversified_factor:.2f}
             = {VOL_TARGET * IDM * diversified_factor * 100:.1f}%

Wait, that's still not 25%...

Actually, let me reconsider. With IDM = √n/√(1+(n-1)ρ):
- Each instrument vol = vol_target × IDM = vol_target × √n/√(1+(n-1)ρ)
- n instruments, equal correlation ρ
- Portfolio variance = n × (inst_vol)² × (1/n + (n-1)ρ/n) / n²
                     = (inst_vol)² × (1 + (n-1)ρ) / n

Hmm, I'm getting confused. Let me just verify empirically.
""")
