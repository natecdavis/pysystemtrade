"""
Corrected Allocation Analysis
==============================
Uses realistic volatility estimates that account for non-normal distributions.
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
from scipy.optimize import minimize_scalar

sys.path.insert(0, "/Users/nathanieldavis/pysystemtrade")
os.environ['PYSYS_LOGGING_LEVEL'] = 'off'

COMBINED_DIR = "/Users/nathanieldavis/pysystemtrade/data/crypto/funding_rates/combined"
PRICE_DIR = "/Users/nathanieldavis/pysystemtrade/data/crypto"

TARGET_VOL = 0.25  # 25% annual volatility target
START_DATE = "2020-09-22"
CARRY_TOKENS = ["BTC", "ETH", "ADA", "AVAX", "LINK", "SOL", "UNI", "XRP"]
CAPITAL_MULT = 1.5

print("=" * 80)
print("CORRECTED ALLOCATION ANALYSIS")
print("Using Realistic Volatility Estimates")
print("=" * 80)

# =============================================================================
# SECTION 1: LOAD DATA
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

carry_returns_per_token = carry_df / CAPITAL_MULT
carry_raw = carry_returns_per_token.mean(axis=1).dropna()

# Load trend data
for name in ['base_system', 'syslogdiag', 'syscore', 'sysdata', 'systems']:
    logging.getLogger(name).setLevel(logging.CRITICAL)
    logging.getLogger(name).disabled = True

from sysdata.config.configdata import Config
from systems.provided.crypto_example.crypto_system import crypto_system

print("\nLoading trend backtest...")
config = Config("systems.provided.crypto_example.crypto_config_diversified.yaml")
system = crypto_system(data_path=PRICE_DIR, config=config)
account = system.accounts.portfolio()
trend_returns_raw = account.percent / 100
trend_returns_raw.index = pd.to_datetime(trend_returns_raw.index.date)
trend_returns_raw = trend_returns_raw[trend_returns_raw.index >= START_DATE]

# Align
common_idx = trend_returns_raw.index.intersection(carry_raw.index)
trend_raw = trend_returns_raw.loc[common_idx].dropna()
carry_raw = carry_raw.loc[common_idx].dropna()
common_idx = trend_raw.index.intersection(carry_raw.index)
trend_raw = trend_raw.loc[common_idx]
carry_raw = carry_raw.loc[common_idx]

print(f"Data loaded: {len(common_idx)} days ({len(common_idx)/365:.2f} years)")
print(f"Period: {common_idx.min().date()} to {common_idx.max().date()}")

# =============================================================================
# SECTION 2: CALCULATE REALISTIC VOLATILITY ESTIMATES
# =============================================================================

print("\n" + "=" * 80)
print("SECTION 2: VOLATILITY ESTIMATES")
print("=" * 80)

def calc_vol_estimates(returns: pd.Series, name: str) -> dict:
    """Calculate various volatility estimates."""
    daily_std = returns.std()
    simple_vol = daily_std * np.sqrt(252)

    # Stress period vol (2022)
    stress_returns = returns[returns.index.year == 2022]
    stress_vol = stress_returns.std() * np.sqrt(252) if len(stress_returns) > 30 else simple_vol

    # Max rolling vol
    rolling_60d = returns.rolling(60).std() * np.sqrt(252)
    max_rolling_vol = rolling_60d.max()

    # Downside deviation
    downside_returns = returns[returns < 0]
    downside_vol = downside_returns.std() * np.sqrt(252) if len(downside_returns) > 0 else simple_vol

    # Cornish-Fisher adjusted vol (for 99% VaR)
    s = skew(returns)
    k = kurtosis(returns)
    z = 2.326  # 99% normal
    cf_z = z + (z**2 - 1) * s / 6 + (z**3 - 3*z) * k / 24 - (2*z**3 - 5*z) * s**2 / 36
    cf_vol = simple_vol * (cf_z / z)

    # Max DD implied vol
    cumulative = (1 + returns).cumprod()
    running_max = cumulative.expanding().max()
    drawdown = (cumulative - running_max) / running_max
    max_dd = drawdown.min()
    years = len(returns) / 365
    dd_implied_vol = abs(max_dd) / (2.5 * np.sqrt(years))

    print(f"\n{name}:")
    print(f"  Simple vol (std):     {simple_vol*100:.2f}%")
    print(f"  Stress vol (2022):    {stress_vol*100:.2f}%")
    print(f"  Max rolling vol (60d): {max_rolling_vol*100:.2f}%")
    print(f"  Downside deviation:   {downside_vol*100:.2f}%")
    print(f"  Cornish-Fisher adj:   {cf_vol*100:.2f}%")
    print(f"  DD-implied vol:       {dd_implied_vol*100:.2f}%")
    print(f"  Skewness: {s:.2f}, Kurtosis: {k:.1f}")

    return {
        'simple': simple_vol,
        'stress': stress_vol,
        'max_rolling': max_rolling_vol,
        'downside': downside_vol,
        'cornish_fisher': cf_vol,
        'dd_implied': dd_implied_vol,
        'skew': s,
        'kurtosis': k
    }

trend_vols = calc_vol_estimates(trend_raw, "TREND")
carry_vols = calc_vol_estimates(carry_raw, "CARRY")

# =============================================================================
# SECTION 3: SELECT APPROPRIATE VOL ESTIMATES
# =============================================================================

print("\n" + "=" * 80)
print("SECTION 3: SELECTING VOL ESTIMATES FOR ALLOCATION")
print("=" * 80)

print("""
For allocation purposes, we need a vol estimate that:
1. Captures tail risk (not just normal days)
2. Is consistent with observed drawdowns
3. Results in reasonable leverage (not 20x+)

For TREND:
- Returns are more normally distributed (skew +0.3, kurtosis ~3-5)
- Simple vol is reasonable, but use stress vol for safety

For CARRY:
- Extreme negative skew (-6), massive kurtosis (200)
- Simple vol MASSIVELY underestimates risk
- Use max rolling vol or Cornish-Fisher adjusted vol
""")

# For trend: use max of simple and stress (more conservative)
trend_realistic_vol = max(trend_vols['simple'], trend_vols['stress'])

# For carry: use Cornish-Fisher as it captures tail risk properly
# But cap it at something reasonable (not 50%+)
carry_realistic_vol = min(
    carry_vols['cornish_fisher'],
    max(carry_vols['max_rolling'] * 2, carry_vols['stress'] * 3)
)

print(f"\nSelected volatility estimates:")
print(f"  Trend: {trend_realistic_vol*100:.2f}% (max of simple and stress)")
print(f"  Carry: {carry_realistic_vol*100:.2f}% (Cornish-Fisher capped)")

# What leverage is needed?
trend_leverage = TARGET_VOL / trend_realistic_vol
carry_leverage = TARGET_VOL / carry_realistic_vol

print(f"\nLeverage to reach {TARGET_VOL*100:.0f}% vol target:")
print(f"  Trend: {trend_leverage:.2f}x")
print(f"  Carry: {carry_leverage:.2f}x")

# =============================================================================
# SECTION 4: THREE APPROACHES TO ALLOCATION
# =============================================================================

print("\n" + "=" * 80)
print("SECTION 4: ALLOCATION APPROACHES")
print("=" * 80)

def calc_stats(returns: pd.Series) -> dict:
    ann_ret = returns.mean() * 252
    ann_vol = returns.std() * np.sqrt(252)
    sharpe = ann_ret / ann_vol if ann_vol > 0 else 0
    skewness = skew(returns.dropna())
    kurt = kurtosis(returns.dropna())

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

# -----------------------------------------------------------------------------
# APPROACH A: Simple Vol-Targeting (the flawed method)
# -----------------------------------------------------------------------------

print("\n--- APPROACH A: Simple Vol-Targeting (FLAWED) ---")
print("This is what we were doing - scaling by simple std dev")

trend_scalar_A = TARGET_VOL / trend_vols['simple']
carry_scalar_A = TARGET_VOL / carry_vols['simple']

print(f"\nScalars: Trend {trend_scalar_A:.2f}x, Carry {carry_scalar_A:.1f}x")
print(f"PROBLEM: {carry_scalar_A:.0f}x leverage on carry is UNREALISTIC!")

trend_A = trend_raw * trend_scalar_A
carry_A = carry_raw * carry_scalar_A

# -----------------------------------------------------------------------------
# APPROACH B: Realistic Vol-Targeting (cap leverage)
# -----------------------------------------------------------------------------

print("\n--- APPROACH B: Realistic Vol-Targeting (capped leverage) ---")
print("Cap carry leverage at 5x to avoid unrealistic positions")

MAX_CARRY_LEVERAGE = 5.0
MAX_TREND_LEVERAGE = 3.0

trend_scalar_B = min(TARGET_VOL / trend_vols['simple'], MAX_TREND_LEVERAGE)
carry_scalar_B = min(TARGET_VOL / carry_vols['simple'], MAX_CARRY_LEVERAGE)

print(f"\nScalars: Trend {trend_scalar_B:.2f}x, Carry {carry_scalar_B:.1f}x")

trend_B = trend_raw * trend_scalar_B
carry_B = carry_raw * carry_scalar_B

trend_B_vol = trend_B.std() * np.sqrt(252)
carry_B_vol = carry_B.std() * np.sqrt(252)
print(f"Resulting vols: Trend {trend_B_vol*100:.1f}%, Carry {carry_B_vol*100:.1f}%")

# -----------------------------------------------------------------------------
# APPROACH C: Risk-Parity at Natural Volatilities
# -----------------------------------------------------------------------------

print("\n--- APPROACH C: Risk-Parity at Natural Vols (NO leverage) ---")
print("Each strategy contributes equal RISK to portfolio")

# Use realistic vols
# Equal risk contribution means: w_trend * vol_trend = w_carry * vol_carry
# with w_trend + w_carry = 1

# Solve: w_trend * vol_trend = (1 - w_trend) * vol_carry
# w_trend = vol_carry / (vol_trend + vol_carry)

trend_nat_vol = trend_realistic_vol
carry_nat_vol = carry_realistic_vol

w_trend_risk_parity = carry_nat_vol / (trend_nat_vol + carry_nat_vol)
w_carry_risk_parity = 1 - w_trend_risk_parity

print(f"\nNatural vols: Trend {trend_nat_vol*100:.1f}%, Carry {carry_nat_vol*100:.1f}%")
print(f"Risk-parity weights: Trend {w_trend_risk_parity*100:.1f}%, Carry {w_carry_risk_parity*100:.1f}%")

# Combined portfolio at natural vols
combined_risk_parity = w_trend_risk_parity * trend_raw + w_carry_risk_parity * carry_raw
stats_risk_parity = calc_stats(combined_risk_parity)
print(f"Portfolio vol (unlevered): {stats_risk_parity['ann_vol']*100:.2f}%")

# -----------------------------------------------------------------------------
# APPROACH D: Capital Allocation (most realistic)
# -----------------------------------------------------------------------------

print("\n--- APPROACH D: Capital Allocation ---")
print("Allocate capital between strategies, each at their natural leverage")

print("""
This is the RIGHT approach for negatively-skewed carry:
- Don't try to vol-target carry to 25%
- Allocate X% of capital to carry, (100-X)% to trend
- Each runs at natural/moderate leverage
- Portfolio vol emerges from allocation, not scaling
""")

# =============================================================================
# SECTION 5: ALLOCATION ANALYSIS UNDER EACH APPROACH
# =============================================================================

print("\n" + "=" * 80)
print("SECTION 5: ALLOCATION COMPARISON")
print("=" * 80)

def portfolio_analysis(trend_ret, carry_ret, trend_wt, approach_name):
    """Calculate portfolio stats for given allocation."""
    carry_wt = 1 - trend_wt
    combined = trend_wt * trend_ret + carry_wt * carry_ret
    stats = calc_stats(combined)
    stats['trend_wt'] = trend_wt
    stats['carry_wt'] = carry_wt
    return stats

# APPROACH A: Flawed (for comparison)
print("\n--- A: Vol-Targeted (flawed ~25x carry leverage) ---")
print("| Trend% | Carry% | Sharpe | Vol    | Skew    | Max DD  |")
print("|--------|--------|--------|--------|---------|---------|")

for t_wt in [1.0, 0.8, 0.7, 0.5, 0.3, 0.0]:
    stats = portfolio_analysis(trend_A, carry_A, t_wt, "A")
    print(f"|  {t_wt*100:4.0f}  |  {(1-t_wt)*100:4.0f}  | {stats['sharpe']:6.2f} | {stats['ann_vol']*100:5.1f}% | {stats['skew']:+6.2f} | {stats['max_dd']*100:6.1f}% |")

# APPROACH B: Capped leverage
print("\n--- B: Capped Leverage (max 5x carry) ---")
print("| Trend% | Carry% | Sharpe | Vol    | Skew    | Max DD  |")
print("|--------|--------|--------|--------|---------|---------|")

for t_wt in [1.0, 0.8, 0.7, 0.5, 0.3, 0.0]:
    stats = portfolio_analysis(trend_B, carry_B, t_wt, "B")
    print(f"|  {t_wt*100:4.0f}  |  {(1-t_wt)*100:4.0f}  | {stats['sharpe']:6.2f} | {stats['ann_vol']*100:5.1f}% | {stats['skew']:+6.2f} | {stats['max_dd']*100:6.1f}% |")

# APPROACH C/D: Natural vols (capital allocation)
print("\n--- C/D: Capital Allocation (natural vols, no artificial leverage) ---")
print("| Trend% | Carry% | Sharpe | Vol    | Skew    | Max DD  |")
print("|--------|--------|--------|--------|---------|---------|")

for t_wt in [1.0, 0.8, 0.7, 0.5, 0.3, 0.0]:
    stats = portfolio_analysis(trend_raw, carry_raw, t_wt, "C")
    print(f"|  {t_wt*100:4.0f}  |  {(1-t_wt)*100:4.0f}  | {stats['sharpe']:6.2f} | {stats['ann_vol']*100:5.1f}% | {stats['skew']:+6.2f} | {stats['max_dd']*100:6.1f}% |")

# =============================================================================
# SECTION 6: FIND OPTIMAL ALLOCATIONS
# =============================================================================

print("\n" + "=" * 80)
print("SECTION 6: OPTIMAL ALLOCATIONS")
print("=" * 80)

# Using Approach B (capped leverage) as the realistic method
print("\nUsing APPROACH B (capped leverage at 5x carry):")

# Find skew-neutral
def skew_obj_B(t_wt):
    combined = t_wt * trend_B + (1-t_wt) * carry_B
    return abs(skew(combined.dropna()))

result_skew_B = minimize_scalar(skew_obj_B, bounds=(0, 1), method='bounded')
skew_neutral_B = result_skew_B.x
stats_skew_neutral_B = portfolio_analysis(trend_B, carry_B, skew_neutral_B, "B")

# Find Sharpe-optimal
def neg_sharpe_B(t_wt):
    combined = t_wt * trend_B + (1-t_wt) * carry_B
    s = calc_stats(combined)
    return -s['sharpe']

result_sharpe_B = minimize_scalar(neg_sharpe_B, bounds=(0, 1), method='bounded')
sharpe_optimal_B = result_sharpe_B.x
stats_sharpe_B = portfolio_analysis(trend_B, carry_B, sharpe_optimal_B, "B")

print(f"\nSkew-neutral: {skew_neutral_B*100:.0f}% Trend / {(1-skew_neutral_B)*100:.0f}% Carry")
print(f"  Sharpe: {stats_skew_neutral_B['sharpe']:.2f}, Skew: {stats_skew_neutral_B['skew']:+.2f}, Max DD: {stats_skew_neutral_B['max_dd']*100:.1f}%")

print(f"\nSharpe-optimal: {sharpe_optimal_B*100:.0f}% Trend / {(1-sharpe_optimal_B)*100:.0f}% Carry")
print(f"  Sharpe: {stats_sharpe_B['sharpe']:.2f}, Skew: {stats_sharpe_B['skew']:+.2f}, Max DD: {stats_sharpe_B['max_dd']*100:.1f}%")

# Using natural vols (Approach C/D)
print("\n\nUsing APPROACH C/D (capital allocation, natural vols):")

def skew_obj_natural(t_wt):
    combined = t_wt * trend_raw + (1-t_wt) * carry_raw
    return abs(skew(combined.dropna()))

result_skew_natural = minimize_scalar(skew_obj_natural, bounds=(0, 1), method='bounded')
skew_neutral_natural = result_skew_natural.x
stats_skew_neutral_natural = portfolio_analysis(trend_raw, carry_raw, skew_neutral_natural, "natural")

print(f"\nSkew-neutral (capital allocation): {skew_neutral_natural*100:.0f}% Trend / {(1-skew_neutral_natural)*100:.0f}% Carry")
print(f"  Portfolio vol: {stats_skew_neutral_natural['ann_vol']*100:.1f}%")
print(f"  Sharpe: {stats_skew_neutral_natural['sharpe']:.2f}, Skew: {stats_skew_neutral_natural['skew']:+.2f}")
print(f"  Max DD: {stats_skew_neutral_natural['max_dd']*100:.1f}%")

# =============================================================================
# SECTION 7: REALISTIC SHARPE ESTIMATES
# =============================================================================

print("\n" + "=" * 80)
print("SECTION 7: REALISTIC SHARPE ESTIMATES")
print("=" * 80)

# Calculate Sharpe using realistic vol estimates
trend_ann_ret = trend_raw.mean() * 252
carry_ann_ret = carry_raw.mean() * 365  # funding is 365 days

trend_sharpe_realistic = trend_ann_ret / trend_realistic_vol
carry_sharpe_realistic = carry_ann_ret / carry_realistic_vol

print(f"\nTREND:")
print(f"  Annual return: {trend_ann_ret*100:.1f}%")
print(f"  Simple vol: {trend_vols['simple']*100:.1f}% → Sharpe: {trend_ann_ret/trend_vols['simple']:.2f}")
print(f"  Realistic vol: {trend_realistic_vol*100:.1f}% → Sharpe: {trend_sharpe_realistic:.2f}")

print(f"\nCARRY:")
print(f"  Annual return: {carry_ann_ret*100:.1f}%")
print(f"  Simple vol: {carry_vols['simple']*100:.1f}% → Sharpe: {carry_ann_ret/carry_vols['simple']:.2f}")
print(f"  Realistic vol: {carry_realistic_vol*100:.1f}% → Sharpe: {carry_sharpe_realistic:.2f}")

# Apply additional haircuts for overfitting
TREND_HAIRCUT = 0.85  # 15% for limited data
CARRY_HAIRCUT = 0.70  # 30% for survivorship + limited data

trend_sharpe_honest = trend_sharpe_realistic * TREND_HAIRCUT
carry_sharpe_honest = carry_sharpe_realistic * CARRY_HAIRCUT

print(f"\nAfter haircuts (trend 15%, carry 30%):")
print(f"  Trend honest Sharpe: {trend_sharpe_honest:.2f}")
print(f"  Carry honest Sharpe: {carry_sharpe_honest:.2f}")

# =============================================================================
# SECTION 8: FINAL RECOMMENDATIONS
# =============================================================================

print("\n" + "=" * 80)
print("SECTION 8: FINAL RECOMMENDATIONS")
print("=" * 80)

print(f"""
KEY FINDINGS:

1. VOLATILITY MISCALCULATION:
   - Simple carry std dev: {carry_vols['simple']*100:.1f}%
   - Realistic carry vol (Cornish-Fisher): {carry_realistic_vol*100:.1f}%
   - Ratio: {carry_realistic_vol/carry_vols['simple']:.1f}x

   Standard deviation MASSIVELY underestimates carry risk due to:
   - Extreme negative skew ({carry_vols['skew']:.1f})
   - Massive kurtosis ({carry_vols['kurtosis']:.0f} vs 3 for normal)

2. LEVERAGE IMPLICATIONS:
   - To hit 25% vol with simple std: {TARGET_VOL/carry_vols['simple']:.0f}x leverage (IMPOSSIBLE)
   - To hit 25% vol with realistic vol: {TARGET_VOL/carry_realistic_vol:.1f}x leverage (still high!)
   - Recommended max carry leverage: 5x

3. REALISTIC SHARPE RATIOS:
   - Trend: {trend_sharpe_honest:.2f} (was {trend_ann_ret/trend_vols['simple']:.2f})
   - Carry: {carry_sharpe_honest:.2f} (was {carry_ann_ret/carry_vols['simple']:.2f})

4. ALLOCATION RECOMMENDATIONS:

   A) IF targeting 25% portfolio vol (typical systematic):
      Use capped leverage (5x carry max)
      Skew-neutral: ~{skew_neutral_B*100:.0f}% Trend / {(1-skew_neutral_B)*100:.0f}% Carry
      Conservative: ~80% Trend / 20% Carry (buffer for tail risk)

   B) IF using capital allocation (natural vols):
      Skew-neutral: ~{skew_neutral_natural*100:.0f}% Trend / {(1-skew_neutral_natural)*100:.0f}% Carry
      This results in ~{stats_skew_neutral_natural['ann_vol']*100:.0f}% portfolio vol
      To reach 25%: scale ENTIRE portfolio by {TARGET_VOL/stats_skew_neutral_natural['ann_vol']:.1f}x

   RECOMMENDED: Approach B (capital allocation)

   Rationale:
   - Doesn't require impossibly high carry leverage
   - Respects the asymmetric nature of carry risk
   - {skew_neutral_natural*100:.0f}/{(1-skew_neutral_natural)*100:.0f} allocation captures carry alpha safely
   - Scale entire portfolio to desired vol (not individual legs)

5. CONSERVATIVE EXPECTED PERFORMANCE:

   At {skew_neutral_natural*100:.0f}/{(1-skew_neutral_natural)*100:.0f} allocation, scaled to 25% vol:
   - Combined Sharpe (honest): ~{(skew_neutral_natural * trend_sharpe_honest + (1-skew_neutral_natural) * carry_sharpe_honest):.2f}
   - After 50% haircut: ~{(skew_neutral_natural * trend_sharpe_honest + (1-skew_neutral_natural) * carry_sharpe_honest) * 0.5:.2f}
   - Expected annual return: ~{(skew_neutral_natural * trend_sharpe_honest + (1-skew_neutral_natural) * carry_sharpe_honest) * 0.5 * TARGET_VOL * 100:.0f}%
   - Expected skew: ~0 (neutral)
   - Max DD (historical scaled): ~{abs(stats_skew_neutral_natural['max_dd']) * TARGET_VOL / stats_skew_neutral_natural['ann_vol'] * 100:.0f}%
""")

print("""
SUMMARY TABLE:

| Metric          | Old (Flawed)        | Corrected           |
|-----------------|---------------------|---------------------|
| Carry vol       | 1.0%                | {:.0f}% (realistic) |
| Carry leverage  | 25x                 | 5x max              |
| Carry Sharpe    | 5.0+                | {:.1f} (honest)     |
| Trend Sharpe    | 0.5                 | {:.1f} (honest)     |
| Skew-neutral    | 77/23               | {:.0f}/{:.0f}       |
| Max DD at 25%   | ~60% (uncontrolled) | ~{:.0f}% (expected) |
""".format(
    carry_realistic_vol * 100,
    carry_sharpe_honest,
    trend_sharpe_honest,
    skew_neutral_natural * 100,
    (1-skew_neutral_natural) * 100,
    abs(stats_skew_neutral_natural['max_dd']) * TARGET_VOL / stats_skew_neutral_natural['ann_vol'] * 100
))
