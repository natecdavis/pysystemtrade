"""
Clean Allocation Validation: Trend + Carry Portfolio
=====================================================
CRITICAL FIX: Both series are volatility-targeted to the SAME level (25% annual)
before combining. This is essential for proper allocation analysis.

Previous bug: Raw returns were combined without vol-targeting, causing the higher-vol
series to dominate regardless of weights.
"""

import os
import sys

# MUST set logging before any other imports
import logging
logging.disable(logging.CRITICAL)
for handler in logging.root.handlers[:]:
    logging.root.removeHandler(handler)
logging.basicConfig(level=logging.CRITICAL)

import warnings
warnings.filterwarnings('ignore')

import numpy as np
import pandas as pd
from scipy.stats import skew, kurtosis
from scipy.optimize import minimize_scalar

sys.path.insert(0, "/Users/nathanieldavis/pysystemtrade")

# Suppress pysystemtrade logging
os.environ['PYSYS_LOGGING_LEVEL'] = 'off'

COMBINED_DIR = "/Users/nathanieldavis/pysystemtrade/data/crypto/funding_rates/combined"
PRICE_DIR = "/Users/nathanieldavis/pysystemtrade/data/crypto"

# Target volatility for BOTH series
TARGET_VOL = 0.25  # 25% annual

print("=" * 80)
print("CLEAN ALLOCATION VALIDATION: VOL-TARGETED TREND + CARRY")
print("=" * 80)

# =============================================================================
# SECTION 1: LOAD AND PREPARE DATA
# =============================================================================

print("\n" + "-" * 80)
print("SECTION 1: LOADING DATA")
print("-" * 80)

# --- Load Carry Returns (BTC funding) ---
def load_combined_funding(ticker: str) -> pd.Series:
    path = os.path.join(COMBINED_DIR, f"{ticker}_funding_combined.csv")
    df = pd.read_csv(path, parse_dates=['datetime'])
    df = df.set_index('datetime')
    return df['fundingRate']

btc_funding = load_combined_funding("BTC")
print(f"\nCarry data (BTC funding): {btc_funding.index.min()} to {btc_funding.index.max()}")
print(f"  Total days: {len(btc_funding)}")

# Convert funding rate to daily return for carry strategy
# Funding rate IS the return for a delta-neutral carry position (long spot + short perp)
# But we need to account for capital efficiency (using leverage)
# At 1.5x leverage multiplier, the return is funding / 1.5
CAPITAL_MULT = 1.5  # Standard for delta-neutral: 100% spot + 50% margin
carry_returns_raw = btc_funding / CAPITAL_MULT

# --- Load Trend Returns (pysystemtrade backtest) ---
# Suppress logging before import
import logging
for name in ['base_system', 'syslogdiag', 'syscore', 'sysdata', 'systems']:
    logging.getLogger(name).setLevel(logging.CRITICAL)
    logging.getLogger(name).disabled = True

from sysdata.config.configdata import Config
from systems.provided.crypto_example.crypto_system import crypto_system

print("Loading trend backtest (this may take a moment)...")
config = Config("systems.provided.crypto_example.crypto_config_diversified.yaml")
system = crypto_system(data_path=PRICE_DIR, config=config)
account = system.accounts.portfolio()
trend_returns_raw = account.percent / 100  # Convert percent to decimal

# Normalize index to just dates (no time component)
trend_returns_raw.index = pd.to_datetime(trend_returns_raw.index.date)
carry_returns_raw.index = pd.to_datetime(carry_returns_raw.index.date)

print(f"\nTrend data (diversified config): {trend_returns_raw.index.min()} to {trend_returns_raw.index.max()}")
print(f"  Total days: {len(trend_returns_raw)}")

# --- Align to common period ---
common_idx = trend_returns_raw.index.intersection(carry_returns_raw.index)
trend_raw = trend_returns_raw.loc[common_idx].dropna()
carry_raw = carry_returns_raw.loc[common_idx].dropna()

# Re-align after dropna
common_idx = trend_raw.index.intersection(carry_raw.index)
trend_raw = trend_raw.loc[common_idx]
carry_raw = carry_raw.loc[common_idx]

print(f"\nAligned period: {common_idx.min()} to {common_idx.max()}")
print(f"  Common days: {len(common_idx)}")
print(f"  Years: {len(common_idx) / 365:.2f}")

# =============================================================================
# SECTION 2: CALCULATE RAW STATISTICS (BEFORE VOL-TARGETING)
# =============================================================================

print("\n" + "-" * 80)
print("SECTION 2: RAW STATISTICS (BEFORE VOL-TARGETING)")
print("-" * 80)

def calc_stats(returns: pd.Series, name: str) -> dict:
    """Calculate key statistics for a return series."""
    ann_ret = returns.mean() * 252
    ann_vol = returns.std() * np.sqrt(252)
    sharpe = ann_ret / ann_vol if ann_vol > 0 else 0
    skewness = skew(returns.dropna())
    kurt = kurtosis(returns.dropna())  # Excess kurtosis

    print(f"\n{name}:")
    print(f"  Annual Return: {ann_ret*100:.2f}%")
    print(f"  Annual Vol:    {ann_vol*100:.2f}%")
    print(f"  Sharpe Ratio:  {sharpe:.3f}")
    print(f"  Skewness:      {skewness:+.3f}")
    print(f"  Exc. Kurtosis: {kurt:+.3f}")

    return {
        'ann_ret': ann_ret,
        'ann_vol': ann_vol,
        'sharpe': sharpe,
        'skew': skewness,
        'kurtosis': kurt
    }

trend_raw_stats = calc_stats(trend_raw, "TREND (raw)")
carry_raw_stats = calc_stats(carry_raw, "CARRY (raw)")

# =============================================================================
# SECTION 3: VOLATILITY-TARGET BOTH SERIES
# =============================================================================

print("\n" + "-" * 80)
print("SECTION 3: VOLATILITY-TARGETING TO 25% ANNUAL")
print("-" * 80)

# Calculate realized volatility
trend_realized_vol = trend_raw.std() * np.sqrt(252)
carry_realized_vol = carry_raw.std() * np.sqrt(252)

print(f"\nRealized annual volatility:")
print(f"  Trend: {trend_realized_vol*100:.2f}%")
print(f"  Carry: {carry_realized_vol*100:.2f}%")

# Scale to target volatility
trend_vol_scalar = TARGET_VOL / trend_realized_vol
carry_vol_scalar = TARGET_VOL / carry_realized_vol

print(f"\nVol scalars to reach {TARGET_VOL*100:.0f}% target:")
print(f"  Trend scalar: {trend_vol_scalar:.3f}")
print(f"  Carry scalar: {carry_vol_scalar:.3f}")

# Apply scaling
trend_scaled = trend_raw * trend_vol_scalar
carry_scaled = carry_raw * carry_vol_scalar

# Verify scaling worked
trend_scaled_vol = trend_scaled.std() * np.sqrt(252)
carry_scaled_vol = carry_scaled.std() * np.sqrt(252)

print(f"\nVerification - Vol after scaling:")
print(f"  Trend: {trend_scaled_vol*100:.2f}% (target: {TARGET_VOL*100:.0f}%)")
print(f"  Carry: {carry_scaled_vol*100:.2f}% (target: {TARGET_VOL*100:.0f}%)")

# Stats after vol-targeting
print("\n--- Statistics AFTER vol-targeting ---")
trend_scaled_stats = calc_stats(trend_scaled, "TREND (vol-targeted)")
carry_scaled_stats = calc_stats(carry_scaled, "CARRY (vol-targeted)")

# =============================================================================
# SECTION 4: APPLY COST ADJUSTMENTS
# =============================================================================

print("\n" + "-" * 80)
print("SECTION 4: COST ADJUSTMENTS")
print("-" * 80)

# Cost assumptions (applied to vol-targeted returns)
TREND_ANNUAL_COST = 0.006  # 0.6% annual (transaction costs)
CARRY_ANNUAL_COST = 0.015  # 1.5% annual (borrowing, exchange fees)
SURVIVORSHIP_COST = 0.006  # 0.6% annual (adjustment for backtest bias)

# Convert to daily
trend_daily_cost = TREND_ANNUAL_COST / 252
carry_daily_cost = (CARRY_ANNUAL_COST + SURVIVORSHIP_COST) / 365

print(f"\nCost adjustments:")
print(f"  Trend: {TREND_ANNUAL_COST*100:.1f}% annual ({trend_daily_cost*10000:.2f} bps/day)")
print(f"  Carry: {(CARRY_ANNUAL_COST+SURVIVORSHIP_COST)*100:.1f}% annual ({carry_daily_cost*10000:.2f} bps/day)")

# Apply costs
trend_final = trend_scaled - trend_daily_cost
carry_final = carry_scaled - carry_daily_cost

print("\n--- Statistics AFTER costs ---")
trend_final_stats = calc_stats(trend_final, "TREND (final)")
carry_final_stats = calc_stats(carry_final, "CARRY (final)")

# =============================================================================
# SECTION 5: ALLOCATION ANALYSIS (THE KEY SECTION)
# =============================================================================

print("\n" + "=" * 80)
print("SECTION 5: ALLOCATION ANALYSIS (VOL-TARGETED)")
print("=" * 80)

def portfolio_stats(trend_ret, carry_ret, trend_wt):
    """Calculate portfolio statistics for given allocation.

    CRITICAL: Both input series MUST be vol-targeted to same level!
    """
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
# SECTION 6: SANITY CHECKS
# =============================================================================

print("\n" + "-" * 80)
print("SANITY CHECKS")
print("-" * 80)

# Check 1: Skewness MUST change as allocation shifts
skews = [r['skew'] for r in results]
skew_range = max(skews) - min(skews)
print(f"\n1. Skewness range across allocations: {skew_range:.3f}")
if skew_range < 0.1:
    print("   ⚠️  WARNING: Skewness not changing enough - possible bug!")
else:
    print("   ✓  PASS: Skewness varies meaningfully across allocations")

# Check 2: At same target vol, higher Sharpe should = higher return
trend_sr = trend_final_stats['sharpe']
carry_sr = carry_final_stats['sharpe']
trend_ret = trend_final_stats['ann_ret']
carry_ret = carry_final_stats['ann_ret']

print(f"\n2. Sharpe vs Return consistency:")
print(f"   Trend: SR={trend_sr:.3f}, Return={trend_ret*100:.2f}%")
print(f"   Carry: SR={carry_sr:.3f}, Return={carry_ret*100:.2f}%")
if (trend_sr > carry_sr and trend_ret > carry_ret) or (carry_sr > trend_sr and carry_ret > trend_ret):
    print("   ✓  PASS: Higher Sharpe corresponds to higher return (at same vol)")
else:
    print("   ⚠️  Check: May be OK if vols not exactly matched")

# Check 3: Trend should have positive skew, carry should have negative skew
print(f"\n3. Expected skew signs:")
print(f"   Trend skew: {trend_final_stats['skew']:+.3f} (expected: positive)")
print(f"   Carry skew: {carry_final_stats['skew']:+.3f} (expected: negative)")
if trend_final_stats['skew'] > 0 and carry_final_stats['skew'] < 0:
    print("   ✓  PASS: Skew signs match expectations")
else:
    print("   ⚠️  WARNING: Unexpected skew signs")

# Check 4: Vol targeting worked
print(f"\n4. Volatility targeting check:")
print(f"   Trend vol: {trend_final.std() * np.sqrt(252) * 100:.1f}% (target: {TARGET_VOL*100:.0f}%)")
print(f"   Carry vol: {carry_final.std() * np.sqrt(252) * 100:.1f}% (target: {TARGET_VOL*100:.0f}%)")

# =============================================================================
# SECTION 7: FIND OPTIMAL ALLOCATIONS
# =============================================================================

print("\n" + "=" * 80)
print("SECTION 7: OPTIMAL ALLOCATIONS")
print("=" * 80)

# Find skew-neutral allocation
def skew_objective(trend_wt):
    stats = portfolio_stats(trend_final, carry_final, trend_wt)
    return abs(stats['skew'])

result_skew = minimize_scalar(skew_objective, bounds=(0, 1), method='bounded')
skew_neutral_wt = result_skew.x
skew_neutral_stats = portfolio_stats(trend_final, carry_final, skew_neutral_wt)

# Find Sharpe-optimal allocation
def neg_sharpe(trend_wt):
    stats = portfolio_stats(trend_final, carry_final, trend_wt)
    return -stats['sharpe']

result_sharpe = minimize_scalar(neg_sharpe, bounds=(0, 1), method='bounded')
sharpe_optimal_wt = result_sharpe.x
sharpe_optimal_stats = portfolio_stats(trend_final, carry_final, sharpe_optimal_wt)

# 50/50 stats
stats_50_50 = portfolio_stats(trend_final, carry_final, 0.5)

print("\n" + "-" * 80)
print("| Criterion            | Trend% | Carry% | Sharpe | Skew   | Ann Ret |")
print("|----------------------|--------|--------|--------|--------|---------|")
print(f"| Sharpe-Optimal       |  {sharpe_optimal_wt*100:4.0f}  |  {(1-sharpe_optimal_wt)*100:4.0f}  | {sharpe_optimal_stats['sharpe']:6.3f} | {sharpe_optimal_stats['skew']:+5.2f} |  {sharpe_optimal_stats['ann_ret']*100:5.1f}% |")
print(f"| Skew-Neutral         |  {skew_neutral_wt*100:4.0f}  |  {(1-skew_neutral_wt)*100:4.0f}  | {skew_neutral_stats['sharpe']:6.3f} | {skew_neutral_stats['skew']:+5.2f} |  {skew_neutral_stats['ann_ret']*100:5.1f}% |")
print(f"| 50/50                |   50   |   50   | {stats_50_50['sharpe']:6.3f} | {stats_50_50['skew']:+5.2f} |  {stats_50_50['ann_ret']*100:5.1f}% |")

# =============================================================================
# SECTION 8: DETAILED SKEW CURVE
# =============================================================================

print("\n" + "-" * 80)
print("SKEW CURVE (fine-grained)")
print("-" * 80)

print("\n| Trend% | Combined Skew | Note                |")
print("|--------|---------------|---------------------|")

for trend_pct in np.arange(0, 101, 10):
    stats = portfolio_stats(trend_final, carry_final, trend_pct/100)
    note = ""
    if abs(stats['skew']) < 0.05:
        note = "<-- Near skew-neutral"
    elif trend_pct == 100:
        note = "(Pure trend)"
    elif trend_pct == 0:
        note = "(Pure carry)"
    print(f"|   {trend_pct:3.0f}  |    {stats['skew']:+6.3f}    | {note}")

# =============================================================================
# SECTION 9: CORRELATION CHECK
# =============================================================================

print("\n" + "-" * 80)
print("CORRELATION BETWEEN TREND AND CARRY")
print("-" * 80)

correlation = trend_final.corr(carry_final)
print(f"\nCorrelation: {correlation:.3f}")
print(f"  (Low correlation = good diversification)")

# =============================================================================
# SECTION 10: FINAL SUMMARY
# =============================================================================

print("\n" + "=" * 80)
print("FINAL SUMMARY")
print("=" * 80)

print(f"""
KEY FINDINGS:
=============

1. INDIVIDUAL STRATEGY PERFORMANCE (at {TARGET_VOL*100:.0f}% target vol, after costs):

   TREND (diversified EWMAC + breakout):
   - Sharpe: {trend_final_stats['sharpe']:.3f}
   - Skewness: {trend_final_stats['skew']:+.3f} (POSITIVE - good for tail risk)

   CARRY (BTC funding, full history 2016+):
   - Sharpe: {carry_final_stats['sharpe']:.3f}
   - Skewness: {carry_final_stats['skew']:+.3f} (NEGATIVE - tail risk concern)

2. CORRELATION: {correlation:.3f}

3. OPTIMAL ALLOCATIONS:

   ┌─────────────────────────────────────────────────────────────────────┐
   │ Sharpe-Optimal: {sharpe_optimal_wt*100:.0f}% Trend / {(1-sharpe_optimal_wt)*100:.0f}% Carry                              │
   │   Sharpe: {sharpe_optimal_stats['sharpe']:.3f}  |  Skew: {sharpe_optimal_stats['skew']:+.3f}  |  Return: {sharpe_optimal_stats['ann_ret']*100:.1f}%           │
   │                                                                     │
   │ Skew-Neutral: {skew_neutral_wt*100:.0f}% Trend / {(1-skew_neutral_wt)*100:.0f}% Carry                                │
   │   Sharpe: {skew_neutral_stats['sharpe']:.3f}  |  Skew: {skew_neutral_stats['skew']:+.3f}  |  Return: {skew_neutral_stats['ann_ret']*100:.1f}%           │
   │                                                                     │
   │ 50/50 Allocation:                                                   │
   │   Sharpe: {stats_50_50['sharpe']:.3f}  |  Skew: {stats_50_50['skew']:+.3f}  |  Return: {stats_50_50['ann_ret']*100:.1f}%           │
   └─────────────────────────────────────────────────────────────────────┘

4. RECOMMENDATION:
""")

if abs(skew_neutral_wt - 0.5) < 0.10:
    print(f"   50/50 remains a reasonable allocation (within 10pp of skew-neutral).")
    print(f"   Skew-neutral is at {skew_neutral_wt*100:.0f}/{(1-skew_neutral_wt)*100:.0f}.")
else:
    print(f"   Consider adjusting to {round(skew_neutral_wt*10)*10:.0f}/{round((1-skew_neutral_wt)*10)*10:.0f}")
    print(f"   for skew-neutral performance.")

print(f"""
5. SHARPE AFTER 50% HAIRCUT (conservative estimate):
   - At skew-neutral allocation: {skew_neutral_stats['sharpe']*0.5:.3f}
   - Expected return at 25% vol: {skew_neutral_stats['sharpe']*0.5*0.25*100:.1f}% annual
""")
