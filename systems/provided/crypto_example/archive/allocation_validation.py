"""
Allocation Validation: Confirm 50/50 is Still Optimal
======================================================
Verify skew-neutral allocation with updated Sharpe estimates.
"""

import os
import sys
import numpy as np
import pandas as pd
from scipy.stats import skew, kurtosis
from scipy.optimize import minimize_scalar

sys.path.insert(0, "/Users/nathanieldavis/pysystemtrade")

COMBINED_DIR = "/Users/nathanieldavis/pysystemtrade/data/crypto/funding_rates/combined"
PRICE_DIR = "/Users/nathanieldavis/pysystemtrade/data/crypto"

# =============================================================================
# LOAD DATA
# =============================================================================

def load_combined_funding(ticker: str) -> pd.Series:
    path = os.path.join(COMBINED_DIR, f"{ticker}_funding_combined.csv")
    df = pd.read_csv(path, parse_dates=['datetime'])
    df = df.set_index('datetime')
    return df['fundingRate']

# Load carry returns
btc_funding = load_combined_funding("BTC")
CAPITAL_MULT = 1.5
carry_returns_raw = btc_funding / CAPITAL_MULT

# Load trend returns (suppress logging)
import logging
logging.getLogger().setLevel(logging.CRITICAL)

from sysdata.config.configdata import Config
from systems.provided.crypto_example.crypto_system import crypto_system

config = Config("systems.provided.crypto_example.crypto_config_diversified.yaml")
system = crypto_system(data_path=PRICE_DIR, config=config)
account = system.accounts.portfolio()
trend_returns_raw = account.percent / 100
trend_returns_raw.index = pd.to_datetime(trend_returns_raw.index.date)

# Align periods
common_idx = trend_returns_raw.index.intersection(carry_returns_raw.index)
trend_returns = trend_returns_raw.loc[common_idx]
carry_returns = carry_returns_raw.loc[common_idx]

print("=" * 70)
print("ALLOCATION VALIDATION: SKEW ANALYSIS WITH UPDATED ESTIMATES")
print("=" * 70)

print(f"\nData period: {common_idx.min()} to {common_idx.max()}")
print(f"Days: {len(common_idx)}")

# Apply cost adjustments to returns (approximate)
TREND_DAILY_COST = 0.006 / 252  # 0.6% annual
CARRY_DAILY_COST = 0.015 / 365  # 1.5% annual
SURVIVORSHIP_DAILY_COST = 0.006 / 365  # 0.6% annual

trend_returns_adj = trend_returns - TREND_DAILY_COST
carry_returns_adj = carry_returns - CARRY_DAILY_COST - SURVIVORSHIP_DAILY_COST

# =============================================================================
# SECTION 1: SKEWNESS AT DIFFERENT ALLOCATIONS
# =============================================================================

print("\n" + "=" * 70)
print("SECTION 1: SKEWNESS AT DIFFERENT ALLOCATIONS")
print("=" * 70)

def portfolio_stats(trend_ret, carry_ret, trend_wt):
    """Calculate portfolio statistics for given allocation."""
    carry_wt = 1 - trend_wt
    combined = trend_wt * trend_ret + carry_wt * carry_ret

    ann_ret = combined.mean() * 252
    ann_vol = combined.std() * np.sqrt(252)
    sharpe = ann_ret / ann_vol if ann_vol > 0 else 0
    skewness = skew(combined.dropna())
    kurt = kurtosis(combined.dropna())  # Excess kurtosis

    return {
        'trend_wt': trend_wt,
        'carry_wt': carry_wt,
        'ann_ret': ann_ret,
        'ann_vol': ann_vol,
        'sharpe': sharpe,
        'skew': skewness,
        'kurtosis': kurt,
        'returns': combined
    }

print("\n| Trend % | Carry % | Combined Skew | Combined SR | Ann Return |")
print("|---------|---------|---------------|-------------|------------|")

allocations = []
for trend_pct in [100, 70, 60, 50, 40, 30, 0]:
    stats = portfolio_stats(trend_returns_adj, carry_returns_adj, trend_pct/100)
    allocations.append(stats)
    print(f"|   {trend_pct:3d}   |   {100-trend_pct:3d}   |    {stats['skew']:+6.2f}    |    {stats['sharpe']:5.2f}    |   {stats['ann_ret']*100:5.1f}%   |")

# =============================================================================
# SECTION 2: FIND SKEW-NEUTRAL ALLOCATION
# =============================================================================

print("\n" + "=" * 70)
print("SECTION 2: FIND SKEW-NEUTRAL ALLOCATION")
print("=" * 70)

def skew_objective(trend_wt):
    """Objective function: minimize |skew|."""
    stats = portfolio_stats(trend_returns_adj, carry_returns_adj, trend_wt)
    return abs(stats['skew'])

# Find minimum skew allocation
result = minimize_scalar(skew_objective, bounds=(0, 1), method='bounded')
skew_neutral_trend_wt = result.x
skew_neutral_stats = portfolio_stats(trend_returns_adj, carry_returns_adj, skew_neutral_trend_wt)

print(f"\nSkew-neutral allocation found:")
print(f"  Trend: {skew_neutral_trend_wt*100:.1f}%")
print(f"  Carry: {(1-skew_neutral_trend_wt)*100:.1f}%")
print(f"  Combined Skew: {skew_neutral_stats['skew']:+.3f}")
print(f"  Combined Sharpe: {skew_neutral_stats['sharpe']:.2f}")

# Compare to exactly 50/50
stats_50_50 = portfolio_stats(trend_returns_adj, carry_returns_adj, 0.5)
print(f"\n50/50 allocation:")
print(f"  Combined Skew: {stats_50_50['skew']:+.3f}")
print(f"  Combined Sharpe: {stats_50_50['sharpe']:.2f}")

print(f"\nDifference from 50/50: {abs(skew_neutral_trend_wt - 0.5)*100:.1f} percentage points")

# =============================================================================
# SECTION 3: TAIL RISK METRICS
# =============================================================================

print("\n" + "=" * 70)
print("SECTION 3: TAIL RISK METRICS AT SKEW-NEUTRAL ALLOCATION")
print("=" * 70)

def tail_risk_metrics(returns: pd.Series) -> dict:
    """Calculate comprehensive tail risk metrics."""
    returns_clean = returns.dropna()

    # Sort for percentile calculations
    sorted_returns = returns_clean.sort_values()
    n = len(sorted_returns)

    # CVaR (Expected Shortfall)
    cvar_95_idx = int(n * 0.05)
    cvar_99_idx = int(n * 0.01)
    cvar_95 = sorted_returns.iloc[:cvar_95_idx].mean()
    cvar_99 = sorted_returns.iloc[:cvar_99_idx].mean()

    # VaR
    var_95 = sorted_returns.iloc[cvar_95_idx]
    var_99 = sorted_returns.iloc[cvar_99_idx]

    # Drawdown analysis
    cumulative = (1 + returns_clean).cumprod()
    running_max = cumulative.expanding().max()
    drawdown = (cumulative - running_max) / running_max
    max_drawdown = drawdown.min()

    # Worst days
    worst_day = returns_clean.min()
    worst_5_days = sorted_returns.iloc[:5].tolist()

    # Best days (for comparison)
    best_day = returns_clean.max()

    return {
        'var_95': var_95,
        'var_99': var_99,
        'cvar_95': cvar_95,
        'cvar_99': cvar_99,
        'max_drawdown': max_drawdown,
        'worst_day': worst_day,
        'worst_5_days': worst_5_days,
        'best_day': best_day,
        'skew': skew(returns_clean),
        'kurtosis': kurtosis(returns_clean)
    }

# Calculate for each component and combined
trend_tail = tail_risk_metrics(trend_returns_adj)
carry_tail = tail_risk_metrics(carry_returns_adj)
combined_tail = tail_risk_metrics(skew_neutral_stats['returns'])
combined_50_50_tail = tail_risk_metrics(stats_50_50['returns'])

print("\n| Metric           | Trend Only | Carry Only | Skew-Neutral | 50/50 |")
print("|------------------|------------|------------|--------------|-------|")
print(f"| Skewness         | {trend_tail['skew']:+10.2f} | {carry_tail['skew']:+10.2f} | {combined_tail['skew']:+12.2f} | {combined_50_50_tail['skew']:+5.2f} |")
print(f"| Excess Kurtosis  | {trend_tail['kurtosis']:+10.2f} | {carry_tail['kurtosis']:+10.2f} | {combined_tail['kurtosis']:+12.2f} | {combined_50_50_tail['kurtosis']:+5.2f} |")
print(f"| VaR 95% (daily)  | {trend_tail['var_95']*100:+10.2f}% | {carry_tail['var_95']*100:+10.2f}% | {combined_tail['var_95']*100:+12.2f}% | {combined_50_50_tail['var_95']*100:+5.2f}% |")
print(f"| CVaR 95% (daily) | {trend_tail['cvar_95']*100:+10.2f}% | {carry_tail['cvar_95']*100:+10.2f}% | {combined_tail['cvar_95']*100:+12.2f}% | {combined_50_50_tail['cvar_95']*100:+5.2f}% |")
print(f"| VaR 99% (daily)  | {trend_tail['var_99']*100:+10.2f}% | {carry_tail['var_99']*100:+10.2f}% | {combined_tail['var_99']*100:+12.2f}% | {combined_50_50_tail['var_99']*100:+5.2f}% |")
print(f"| CVaR 99% (daily) | {trend_tail['cvar_99']*100:+10.2f}% | {carry_tail['cvar_99']*100:+10.2f}% | {combined_tail['cvar_99']*100:+12.2f}% | {combined_50_50_tail['cvar_99']*100:+5.2f}% |")
print(f"| Max Drawdown     | {trend_tail['max_drawdown']*100:+10.1f}% | {carry_tail['max_drawdown']*100:+10.1f}% | {combined_tail['max_drawdown']*100:+12.1f}% | {combined_50_50_tail['max_drawdown']*100:+5.1f}% |")
print(f"| Worst Day        | {trend_tail['worst_day']*100:+10.2f}% | {carry_tail['worst_day']*100:+10.2f}% | {combined_tail['worst_day']*100:+12.2f}% | {combined_50_50_tail['worst_day']*100:+5.2f}% |")
print(f"| Best Day         | {trend_tail['best_day']*100:+10.2f}% | {carry_tail['best_day']*100:+10.2f}% | {combined_tail['best_day']*100:+12.2f}% | {combined_50_50_tail['best_day']*100:+5.2f}% |")

# =============================================================================
# SECTION 4: COMPARE SKEW-NEUTRAL VS SHARPE-OPTIMAL
# =============================================================================

print("\n" + "=" * 70)
print("SECTION 4: SKEW-NEUTRAL vs SHARPE-OPTIMAL")
print("=" * 70)

# Find Sharpe-optimal allocation
def neg_sharpe(trend_wt):
    stats = portfolio_stats(trend_returns_adj, carry_returns_adj, trend_wt)
    return -stats['sharpe']

result_sharpe = minimize_scalar(neg_sharpe, bounds=(0, 1), method='bounded')
sharpe_optimal_trend_wt = result_sharpe.x
sharpe_optimal_stats = portfolio_stats(trend_returns_adj, carry_returns_adj, sharpe_optimal_trend_wt)

# Find max Sharpe with skew >= 0 constraint
def neg_sharpe_with_skew_constraint(trend_wt):
    stats = portfolio_stats(trend_returns_adj, carry_returns_adj, trend_wt)
    if stats['skew'] < 0:
        return 100  # Penalty
    return -stats['sharpe']

# Grid search for constrained optimization
best_constrained = None
for trend_wt in np.linspace(0, 1, 101):
    stats = portfolio_stats(trend_returns_adj, carry_returns_adj, trend_wt)
    if stats['skew'] >= 0:
        if best_constrained is None or stats['sharpe'] > best_constrained['sharpe']:
            best_constrained = stats

print("\n| Criterion              | Trend % | Carry % | Sharpe | Skew   |")
print("|------------------------|---------|---------|--------|--------|")
print(f"| Max Sharpe             |   {sharpe_optimal_trend_wt*100:5.1f} |   {(1-sharpe_optimal_trend_wt)*100:5.1f} |  {sharpe_optimal_stats['sharpe']:.2f}  | {sharpe_optimal_stats['skew']:+.2f}  |")
print(f"| Zero Skew              |   {skew_neutral_trend_wt*100:5.1f} |   {(1-skew_neutral_trend_wt)*100:5.1f} |  {skew_neutral_stats['sharpe']:.2f}  | {skew_neutral_stats['skew']:+.2f}  |")
if best_constrained:
    print(f"| Max SR w/ skew >= 0    |   {best_constrained['trend_wt']*100:5.1f} |   {best_constrained['carry_wt']*100:5.1f} |  {best_constrained['sharpe']:.2f}  | {best_constrained['skew']:+.2f}  |")
print(f"| Pre-specified 50/50    |   {50.0:5.1f} |   {50.0:5.1f} |  {stats_50_50['sharpe']:.2f}  | {stats_50_50['skew']:+.2f}  |")

# =============================================================================
# SECTION 5: SENSITIVITY ANALYSIS
# =============================================================================

print("\n" + "=" * 70)
print("SECTION 5: SENSITIVITY ANALYSIS")
print("=" * 70)

print("\nHow does skew-neutral allocation change with different cost assumptions?")
print("\n| Cost Scenario          | Skew-Neutral Trend % | Combined Sharpe |")
print("|------------------------|----------------------|-----------------|")

# Test different cost scenarios
cost_scenarios = [
    ("Base case (validated)", 0.006, 0.015, 0.006),
    ("Lower costs (-50%)", 0.003, 0.0075, 0.003),
    ("Higher costs (+50%)", 0.009, 0.0225, 0.009),
    ("Zero costs", 0, 0, 0),
]

for scenario_name, trend_cost, carry_cost, surv_cost in cost_scenarios:
    trend_adj = trend_returns - trend_cost/252
    carry_adj = carry_returns - (carry_cost + surv_cost)/365

    def skew_obj(trend_wt):
        combined = trend_wt * trend_adj + (1-trend_wt) * carry_adj
        return abs(skew(combined.dropna()))

    result = minimize_scalar(skew_obj, bounds=(0, 1), method='bounded')
    optimal_wt = result.x

    combined = optimal_wt * trend_adj + (1-optimal_wt) * carry_adj
    sr = combined.mean() / combined.std() * np.sqrt(252)

    print(f"| {scenario_name:<22} |        {optimal_wt*100:5.1f}%        |      {sr:.2f}       |")

# =============================================================================
# SECTION 6: FINAL RECOMMENDATION
# =============================================================================

print("\n" + "=" * 70)
print("SECTION 6: FINAL RECOMMENDATION")
print("=" * 70)

# Calculate the Sharpe cost of choosing skew-neutral vs Sharpe-optimal
sharpe_cost = sharpe_optimal_stats['sharpe'] - skew_neutral_stats['sharpe']
sharpe_cost_50_50 = sharpe_optimal_stats['sharpe'] - stats_50_50['sharpe']

print(f"""
ANALYSIS SUMMARY:
=================

1. SKEW-NEUTRAL ALLOCATION: {skew_neutral_trend_wt*100:.0f}% Trend / {(1-skew_neutral_trend_wt)*100:.0f}% Carry
   - Combined Skew: {skew_neutral_stats['skew']:+.3f} (essentially zero)
   - Combined Sharpe: {skew_neutral_stats['sharpe']:.2f}

2. 50/50 ALLOCATION:
   - Combined Skew: {stats_50_50['skew']:+.3f}
   - Combined Sharpe: {stats_50_50['sharpe']:.2f}
   - Difference from skew-neutral: {abs(skew_neutral_trend_wt - 0.5)*100:.1f} percentage points

3. SHARPE-OPTIMAL ALLOCATION: {sharpe_optimal_trend_wt*100:.0f}% Trend / {(1-sharpe_optimal_trend_wt)*100:.0f}% Carry
   - Combined Sharpe: {sharpe_optimal_stats['sharpe']:.2f}
   - Combined Skew: {sharpe_optimal_stats['skew']:+.2f} (NEGATIVE!)

4. SHARPE COST OF CHOOSING SKEW-NEUTRAL:
   - vs Max Sharpe: {sharpe_cost:.2f} SR points ({sharpe_cost/sharpe_optimal_stats['sharpe']*100:.0f}% reduction)
   - This is the "premium" paid for avoiding negative skew

5. IS 50/50 STILL CORRECT?
""")

if abs(skew_neutral_trend_wt - 0.5) < 0.05:  # Within 5 percentage points
    print(f"""   YES - 50/50 remains the correct allocation.

   The skew-neutral point is {skew_neutral_trend_wt*100:.0f}/{(1-skew_neutral_trend_wt)*100:.0f},
   which rounds to 50/50.

   The small difference ({abs(skew_neutral_trend_wt - 0.5)*100:.1f}pp) is not worth
   the complexity of non-round-number allocation.
""")
else:
    diff = skew_neutral_trend_wt - 0.5
    direction = "more Trend" if diff > 0 else "more Carry"
    print(f"""   ADJUSTMENT RECOMMENDED:

   The skew-neutral point has shifted to {skew_neutral_trend_wt*100:.0f}/{(1-skew_neutral_trend_wt)*100:.0f}.
   This is {abs(diff)*100:.0f} percentage points {direction} than 50/50.

   Consider adjusting to {round(skew_neutral_trend_wt*20)*5:.0f}/{round((1-skew_neutral_trend_wt)*20)*5:.0f}
   (rounded to nearest 5%).
""")

print(f"""
FINAL NUMBERS TO USE:
=====================

┌────────────────────────────────────────────────────────────────┐
│  RECOMMENDED ALLOCATION: {round(skew_neutral_trend_wt*20)*5:.0f}% Trend / {round((1-skew_neutral_trend_wt)*20)*5:.0f}% Carry           │
│                                                                │
│  Combined Sharpe (best estimate):     {skew_neutral_stats['sharpe']:.2f}                    │
│  Combined Sharpe (after 50% haircut): {skew_neutral_stats['sharpe']*0.5:.2f}                    │
│  Combined Skew:                       {skew_neutral_stats['skew']:+.2f}                    │
│                                                                │
│  At 25% target vol:                                            │
│  - Expected return (best):        {skew_neutral_stats['sharpe']*0.25*100:5.1f}%                   │
│  - Expected return (conservative): {skew_neutral_stats['sharpe']*0.5*0.25*100:5.1f}%                   │
│                                                                │
│  Tail Risk (at recommended allocation):                        │
│  - CVaR 95%: {combined_tail['cvar_95']*100:+.2f}% daily                                │
│  - Max Drawdown: {combined_tail['max_drawdown']*100:.1f}%                                │
│  - Worst Day: {combined_tail['worst_day']*100:+.2f}%                                  │
└────────────────────────────────────────────────────────────────┘
""")
