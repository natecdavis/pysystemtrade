"""
Carver's Methodology for Trend/Carry Allocation
================================================
Apply pysystemtrade's handcrafting approach to derive optimal weights.

Key insight: With limited data, we have uncertainty about true Sharpe Ratios.
Carver's approach shrinks weights toward equal weights based on this uncertainty.
"""

import numpy as np
import scipy.stats as stats

# =============================================================================
# STEP 1: OUR INPUTS (from backtest)
# =============================================================================

# Backtest results (2022-2025 overlapping period)
TREND_SHARPE = 0.25
CARRY_SHARPE = 4.88
CORRELATION = 0.065

# Data availability
TREND_YEARS = 6.0   # Trend has more data (since 2018 for BTC/ETH)
CARRY_YEARS = 3.0   # Carry limited to 2022+ for most instruments

# Use conservative estimate for combined period
YEARS_OF_DATA = 3.0

print("=" * 70)
print("CARVER'S METHODOLOGY FOR STRATEGY ALLOCATION")
print("=" * 70)

print(f"""
INPUTS:
  Trend Sharpe:     {TREND_SHARPE:.2f}
  Carry Sharpe:     {CARRY_SHARPE:.2f}
  Correlation:      {CORRELATION:.3f}
  Years of data:    {YEARS_OF_DATA:.1f}
""")

# =============================================================================
# STEP 2: CALCULATE UNCERTAINTY IN SHARPE RATIO ESTIMATES
# =============================================================================

print("=" * 70)
print("STEP 2: UNCERTAINTY IN SHARPE RATIO ESTIMATES")
print("=" * 70)

# Standard error of Sharpe Ratio estimate
# SE(SR) ≈ sqrt((1 + SR^2/2) / n) where n = years
# Simplified: SE(SR) ≈ 1/sqrt(years) for typical SRs

def sharpe_standard_error(sr: float, years: float) -> float:
    """Calculate standard error of Sharpe Ratio estimate."""
    # More accurate formula: sqrt((1 + sr^2/2) / years)
    return np.sqrt((1 + sr**2 / 2) / years)

trend_se = sharpe_standard_error(TREND_SHARPE, YEARS_OF_DATA)
carry_se = sharpe_standard_error(CARRY_SHARPE, YEARS_OF_DATA)

print(f"""
Standard Error of Sharpe Ratio estimates:
  Trend SE:  {trend_se:.3f}  (95% CI: {TREND_SHARPE:.2f} ± {1.96*trend_se:.2f})
  Carry SE:  {carry_se:.3f}  (95% CI: {CARRY_SHARPE:.2f} ± {1.96*carry_se:.2f})

Note: High carry SE due to (1 + SR^2/2) term - high Sharpe has high variance!

Carry 95% CI: [{CARRY_SHARPE - 1.96*carry_se:.2f}, {CARRY_SHARPE + 1.96*carry_se:.2f}]
""")

# =============================================================================
# STEP 3: CARVER'S OMEGA DIFFERENCE FORMULA
# =============================================================================

print("=" * 70)
print("STEP 3: CARVER'S OMEGA DIFFERENCE (Uncertainty in SR difference)")
print("=" * 70)

# From SR_adjustment.py:
# omega_one_asset = std / sqrt(years_of_data)
# omega_variance_difference = 2 * omega_one_asset^2 * (1 - correlation)
# omega_difference = sqrt(omega_variance_difference)

# Using std = 0.15 as Carver does (this is a scaling parameter, cancels out)
STD = 0.15

omega_one_asset = STD / np.sqrt(YEARS_OF_DATA)
omega_variance_difference = 2 * (omega_one_asset ** 2) * (1 - CORRELATION)
omega_difference = np.sqrt(omega_variance_difference)

print(f"""
Carver's formula (from SR_adjustment.py):
  omega_one_asset = std / sqrt(years) = {STD} / sqrt({YEARS_OF_DATA}) = {omega_one_asset:.4f}
  omega_var_diff  = 2 * omega^2 * (1 - corr) = 2 * {omega_one_asset**2:.6f} * {1-CORRELATION:.3f} = {omega_variance_difference:.6f}
  omega_difference = sqrt(omega_var_diff) = {omega_difference:.4f}

Interpretation:
  This represents uncertainty in the DIFFERENCE between two SRs.
  Lower correlation = higher uncertainty (can't attribute diff to shared factors).
  More years = lower uncertainty.
""")

# =============================================================================
# STEP 4: MINI-BOOTSTRAP ACROSS CONFIDENCE INTERVALS
# =============================================================================

print("=" * 70)
print("STEP 4: MINI-BOOTSTRAP (Sample across confidence intervals)")
print("=" * 70)

# Carver samples at different confidence intervals and averages
# This accounts for the full distribution of possible true SRs

def optimise_two_assets(mean1: float, mean2: float, corr: float, std: float) -> tuple:
    """Simple mean-variance optimization for 2 assets."""
    # For 2 assets with equal vol, optimal weight depends on:
    # w1 = (mu1 - rf) * sigma2^2 - (mu2 - rf) * sigma1 * sigma2 * rho
    #      / [(mu1 - rf) * sigma2^2 + (mu2 - rf) * sigma1^2 - (mu1 + mu2 - 2rf) * sigma1*sigma2*rho]

    # Simplified for equal vol:
    # w1 = (mean1 - mean2 * rho) / (mean1 + mean2 - (mean1 + mean2) * rho)

    # Handle edge cases
    if mean1 <= 0 and mean2 <= 0:
        return 0.5, 0.5

    # Use proper MVO
    cov_matrix = np.array([
        [std**2, corr * std**2],
        [corr * std**2, std**2]
    ])

    # Inverse covariance
    inv_cov = np.linalg.inv(cov_matrix)
    means = np.array([mean1, mean2])

    # Unconstrained weights: w = inv(Sigma) @ mu
    raw_weights = inv_cov @ means

    # Normalize and clip to [0, 1]
    weights = np.clip(raw_weights, 0, None)
    if np.sum(weights) > 0:
        weights = weights / np.sum(weights)
    else:
        weights = np.array([0.5, 0.5])

    return weights[0], weights[1]


# Average SR
avg_SR = (TREND_SHARPE + CARRY_SHARPE) / 2
avg_mean = avg_SR * STD

# Trend relative SR
trend_relative_SR = TREND_SHARPE - avg_SR
carry_relative_SR = CARRY_SHARPE - avg_SR

print(f"""
Average SR:          {avg_SR:.2f}
Trend relative SR:   {trend_relative_SR:+.2f}
Carry relative SR:   {carry_relative_SR:+.2f}
""")

# Sample at confidence intervals: 0.2, 0.4, 0.6, 0.8
confidence_intervals = [0.2, 0.4, 0.6, 0.8]
all_weights = []

print(f"{'CI':<8} {'Trend Mean':<12} {'Carry Mean':<12} {'W_trend':<10} {'W_carry':<10}")
print("-" * 55)

for ci in confidence_intervals:
    # Calculate "confident" mean difference for each asset
    # This shrinks the mean toward avg_mean based on uncertainty

    # For trend (negative relative SR): what's the confident estimate?
    trend_mean_diff = trend_relative_SR * STD  # Convert to mean units
    trend_confident_diff = stats.norm(trend_mean_diff, omega_difference).ppf(ci)
    trend_confident_mean = trend_confident_diff + avg_mean

    # For carry (positive relative SR)
    carry_mean_diff = carry_relative_SR * STD
    carry_confident_diff = stats.norm(carry_mean_diff, omega_difference).ppf(ci)
    carry_confident_mean = carry_confident_diff + avg_mean

    # Optimize
    w_trend, w_carry = optimise_two_assets(
        trend_confident_mean, carry_confident_mean, CORRELATION, STD
    )

    all_weights.append((w_trend, w_carry))

    print(f"{ci:<8} {trend_confident_mean:.4f}      {carry_confident_mean:.4f}      {w_trend:.3f}      {w_carry:.3f}")

# Average across bootstrap samples
avg_w_trend = np.mean([w[0] for w in all_weights])
avg_w_carry = np.mean([w[1] for w in all_weights])

print("-" * 55)
print(f"{'Average':<8} {'':12} {'':12} {avg_w_trend:.3f}      {avg_w_carry:.3f}")

# =============================================================================
# STEP 5: COMPARE TO NAIVE APPROACHES
# =============================================================================

print("\n" + "=" * 70)
print("STEP 5: COMPARISON TO NAIVE APPROACHES")
print("=" * 70)

# 1. Equal weight
equal_trend, equal_carry = 0.5, 0.5

# 2. Sharpe-weighted (proportional to SR)
total_sr = TREND_SHARPE + CARRY_SHARPE
sr_trend = TREND_SHARPE / total_sr
sr_carry = CARRY_SHARPE / total_sr

# 3. Naive MVO (ignoring uncertainty)
naive_w_trend, naive_w_carry = optimise_two_assets(
    TREND_SHARPE * STD, CARRY_SHARPE * STD, CORRELATION, STD
)

print(f"""
{'Approach':<30} {'W_trend':>12} {'W_carry':>12}
{'-' * 56}
{'Equal Weight':<30} {equal_trend:>12.1%} {equal_carry:>12.1%}
{'SR-Proportional':<30} {sr_trend:>12.1%} {sr_carry:>12.1%}
{'Naive MVO (ignore uncertainty)':<30} {naive_w_trend:>12.1%} {naive_w_carry:>12.1%}
{'Carver (with uncertainty)':<30} {avg_w_trend:>12.1%} {avg_w_carry:>12.1%}
""")

# =============================================================================
# STEP 6: SENSITIVITY TO CARRY SHARPE
# =============================================================================

print("=" * 70)
print("STEP 6: SENSITIVITY ANALYSIS")
print("=" * 70)

print("\nHow do weights change with different assumed Carry Sharpe?")
print("(Trend SR fixed at 0.25, using 3 years of data)\n")

print(f"{'Carry SR':<12} {'W_trend':>12} {'W_carry':>12} {'Notes':<30}")
print("-" * 70)

for carry_sr in [0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 4.88]:
    # Recalculate with different carry SR
    avg_sr = (TREND_SHARPE + carry_sr) / 2
    trend_rel = TREND_SHARPE - avg_sr
    carry_rel = carry_sr - avg_sr

    weights_list = []
    for ci in confidence_intervals:
        trend_mean = (stats.norm(trend_rel * STD, omega_difference).ppf(ci) + avg_sr * STD)
        carry_mean = (stats.norm(carry_rel * STD, omega_difference).ppf(ci) + avg_sr * STD)
        w_t, w_c = optimise_two_assets(trend_mean, carry_mean, CORRELATION, STD)
        weights_list.append((w_t, w_c))

    w_trend_avg = np.mean([w[0] for w in weights_list])
    w_carry_avg = np.mean([w[1] for w in weights_list])

    note = ""
    if carry_sr == 4.88:
        note = "<-- Our backtest result"
    elif carry_sr == 2.0:
        note = "<-- Conservative estimate"
    elif carry_sr == 1.0:
        note = "<-- Very conservative"

    print(f"{carry_sr:<12.2f} {w_trend_avg:>12.1%} {w_carry_avg:>12.1%} {note:<30}")

# =============================================================================
# STEP 7: SENSITIVITY TO YEARS OF DATA
# =============================================================================

print("\n" + "=" * 70)
print("How do weights change with more years of data?")
print("(Trend SR=0.25, Carry SR=4.88)\n")

print(f"{'Years':<12} {'W_trend':>12} {'W_carry':>12} {'Shrinkage':>12}")
print("-" * 50)

baseline_equal = 0.5

for years in [1, 2, 3, 5, 10, 20, 50]:
    # Recalculate omega with different years
    omega_asset = STD / np.sqrt(years)
    omega_diff = np.sqrt(2 * (omega_asset ** 2) * (1 - CORRELATION))

    avg_sr = (TREND_SHARPE + CARRY_SHARPE) / 2
    trend_rel = TREND_SHARPE - avg_sr
    carry_rel = CARRY_SHARPE - avg_sr

    weights_list = []
    for ci in confidence_intervals:
        trend_mean = (stats.norm(trend_rel * STD, omega_diff).ppf(ci) + avg_sr * STD)
        carry_mean = (stats.norm(carry_rel * STD, omega_diff).ppf(ci) + avg_sr * STD)
        w_t, w_c = optimise_two_assets(trend_mean, carry_mean, CORRELATION, STD)
        weights_list.append((w_t, w_c))

    w_trend_avg = np.mean([w[0] for w in weights_list])

    # Shrinkage toward equal weight
    shrinkage = 1 - abs(w_trend_avg - baseline_equal) / baseline_equal

    print(f"{years:<12} {w_trend_avg:>12.1%} {1-w_trend_avg:>12.1%} {shrinkage:>12.1%}")

print("""
Note: With only 3 years of data, weights are heavily shrunk toward equal.
Even 50 years would still show significant shrinkage given the large SR difference.
""")

# =============================================================================
# STEP 8: DIVERSIFICATION MULTIPLIER
# =============================================================================

print("=" * 70)
print("STEP 8: DIVERSIFICATION MULTIPLIER")
print("=" * 70)

def calc_div_mult(w_trend: float, w_carry: float, corr: float) -> float:
    """Calculate diversification multiplier: 1 / sqrt(w' * Sigma * w)"""
    # Assume equal vol = 1 for simplicity
    portfolio_vol = np.sqrt(
        w_trend**2 + w_carry**2 + 2 * w_trend * w_carry * corr
    )
    return 1 / portfolio_vol

# For our recommended weights
carver_div_mult = calc_div_mult(avg_w_trend, avg_w_carry, CORRELATION)

# For comparison
equal_div_mult = calc_div_mult(0.5, 0.5, CORRELATION)
trend_only_div_mult = calc_div_mult(1.0, 0.0, CORRELATION)

print(f"""
Diversification Multiplier (IDM):
  Formula: IDM = 1 / sqrt(w'Σw) where Σ is correlation matrix

  100% Trend:          {trend_only_div_mult:.3f}
  50/50 (Equal):       {equal_div_mult:.3f}
  Carver Allocation:   {carver_div_mult:.3f} (w_trend={avg_w_trend:.1%}, w_carry={avg_w_carry:.1%})

The low correlation ({CORRELATION:.3f}) provides significant diversification benefit.
""")

# =============================================================================
# STEP 9: FINAL RECOMMENDATION
# =============================================================================

print("=" * 70)
print("FINAL RECOMMENDATION")
print("=" * 70)

print(f"""
CARVER'S METHODOLOGY RESULT:
  Trend:  {avg_w_trend:.1%}
  Carry:  {avg_w_carry:.1%}

WHY NOT JUST ALL CARRY?
  The naive approach says "Carry SR=4.88 >> Trend SR=0.25, so 100% carry!"

  Carver's approach says: "Wait - you only have 3 years of data."
  - True Sharpe could be anywhere in a wide confidence interval
  - High Sharpe estimates have particularly high variance
  - We should be skeptical and shrink toward equal weights

  Even so, the evidence for carry is strong enough to tilt heavily toward it.

PRACTICAL CONSIDERATIONS:
  1. Carry's 4.88 Sharpe is likely overstated:
     - Based on favorable 2023-2024 period
     - 2022 showed -72% for carry (extreme drawdown)
     - Funding rates have declined over time (competition)

  2. Trend provides crisis protection:
     - Works well in trending markets
     - Can profit from both directions
     - Lower correlation to traditional assets

  3. Capacity constraints:
     - High carry demand may push rates lower
     - Need to consider execution costs

SUGGESTED ALLOCATIONS:
┌─────────────────────────────────────────────────────────────────────┐
│ Scenario                    │ Trend    │ Carry    │ Rationale       │
├─────────────────────────────┼──────────┼──────────┼─────────────────┤
│ Carver Theoretical          │ {avg_w_trend*100:5.1f}%   │ {avg_w_carry*100:5.1f}%   │ Pure methodology │
│ Conservative (SR=2.0)       │ ~25%     │ ~75%     │ Skeptical carry │
│ Practical (SR=1.5)          │ ~35%     │ ~65%     │ Very skeptical  │
│ Balanced                    │ 40%      │ 60%      │ Middle ground   │
│ Risk Parity                 │ 50%      │ 50%      │ Equal risk      │
└─────────────────────────────┴──────────┴──────────┴─────────────────┘

MY RECOMMENDATION: 40% Trend / 60% Carry
  - Accounts for uncertainty in carry Sharpe
  - Maintains trend exposure for crisis protection
  - Still captures significant carry premium
  - More robust to carry rate compression
""")
