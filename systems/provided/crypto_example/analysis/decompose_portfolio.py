"""
Portfolio Decomposition & Marginal Contribution Analysis
=========================================================
Analyzes the marginal benefit of adding TREND (static or dynamic) to a CARRY baseline.

Key Questions:
1. Does TREND reduce left-tail events?
2. Is DYNAMIC TREND market-neutral (low beta to BTC)?
3. Which TREND variant provides better diversification?
4. What's the marginal Sharpe/drawdown/crisis contribution?

Usage:
    python decompose_portfolio.py [--results-file portfolio_comparison.csv]
"""

import os
import sys
import argparse
import pandas as pd
import numpy as np

# Get project root and add to path
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, "..", "..", "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from ..core.cache_systems import load_returns, cache_exists
from ..core.portfolio_metrics import (
    calculate_core_metrics, calculate_marginal_contribution,
    calculate_market_exposure, calculate_crisis_performance
)
from ..core.portfolio_combiner import calculate_correlation_and_beta


def analyze_tail_risk(returns: pd.Series, name: str = "Strategy") -> dict:
    """
    Analyze left-tail (worst outcomes) for a strategy.

    Args:
        returns: Daily percentage returns
        name: Strategy name

    Returns:
        dict with tail risk metrics
    """
    returns_clean = returns.dropna()

    # Percentiles
    pct_5 = returns_clean.quantile(0.05)   # Worst 5% of days
    pct_1 = returns_clean.quantile(0.01)   # Worst 1% of days

    # Count extreme negative days
    extreme_neg_days = (returns_clean < -0.02).sum()  # Days < -2%
    very_extreme_days = (returns_clean < -0.05).sum()  # Days < -5%

    # Worst streaks (consecutive negative days)
    is_negative = returns_clean < 0
    streaks = is_negative.ne(is_negative.shift()).cumsum()
    negative_streaks = streaks[is_negative]
    longest_streak = negative_streaks.value_counts().max() if len(negative_streaks) > 0 else 0

    return {
        'name': name,
        'pct_5': pct_5,
        'pct_1': pct_1,
        'days_below_minus_2pct': extreme_neg_days,
        'days_below_minus_5pct': very_extreme_days,
        'longest_losing_streak': longest_streak,
        'total_days': len(returns_clean)
    }


def compare_diversification(
    baseline_returns: pd.Series,
    addon_static_returns: pd.Series,
    addon_dynamic_returns: pd.Series,
    baseline_name: str = "CARRY",
    addon_name: str = "TREND"
) -> dict:
    """
    Compare diversification benefit of STATIC vs DYNAMIC variants.

    Lower correlation to baseline = better diversification benefit.

    Args:
        baseline_returns: Returns for baseline strategy (e.g., CARRY)
        addon_static_returns: Returns for static variant (e.g., TREND STATIC)
        addon_dynamic_returns: Returns for dynamic variant (e.g., TREND DYNAMIC)
        baseline_name: Name of baseline strategy
        addon_name: Name of addon strategy

    Returns:
        dict with comparison metrics
    """
    # Calculate correlations
    static_corr = calculate_correlation_and_beta(
        addon_static_returns, baseline_returns, verbose=False
    )

    dynamic_corr = calculate_correlation_and_beta(
        addon_dynamic_returns, baseline_returns, verbose=False
    )

    # Volatility of each
    DAYS_PER_YEAR = 365
    baseline_vol = baseline_returns.std() * np.sqrt(DAYS_PER_YEAR)
    static_vol = addon_static_returns.std() * np.sqrt(DAYS_PER_YEAR)
    dynamic_vol = addon_dynamic_returns.std() * np.sqrt(DAYS_PER_YEAR)

    return {
        'baseline': baseline_name,
        'addon': addon_name,
        'static_correlation': static_corr['correlation'],
        'dynamic_correlation': dynamic_corr['correlation'],
        'correlation_improvement': static_corr['correlation'] - dynamic_corr['correlation'],
        'static_beta': static_corr['beta'],
        'dynamic_beta': dynamic_corr['beta'],
        'baseline_vol': baseline_vol,
        'static_vol': static_vol,
        'dynamic_vol': dynamic_vol
    }


def analyze_marginal_contributions(results_dict: dict) -> pd.DataFrame:
    """
    Calculate marginal contribution of TREND variants to CARRY baseline.

    Compares:
    - D1 vs A: TREND STATIC 80/20 marginal contribution
    - E1 vs A: TREND DYNAMIC 80/20 marginal contribution
    - D2 vs A: TREND STATIC 50/50 marginal contribution
    - E2 vs A: TREND DYNAMIC 50/50 marginal contribution

    Args:
        results_dict: Results from run_portfolio_experiment.py

    Returns:
        pd.DataFrame with marginal contributions
    """
    baseline = results_dict['A_CARRY_ONLY']['metrics']

    comparisons = []

    # 80/20 allocations
    if 'D1_STATIC_80_20' in results_dict:
        d1_metrics = results_dict['D1_STATIC_80_20']['metrics']
        marginal_d1 = calculate_marginal_contribution(
            d1_metrics, baseline, 'TREND STATIC (80/20)'
        )
        comparisons.append(marginal_d1)

    if 'E1_DYNAMIC_80_20' in results_dict:
        e1_metrics = results_dict['E1_DYNAMIC_80_20']['metrics']
        marginal_e1 = calculate_marginal_contribution(
            e1_metrics, baseline, 'TREND DYNAMIC (80/20)'
        )
        comparisons.append(marginal_e1)

    # 50/50 allocations
    if 'D2_STATIC_50_50' in results_dict:
        d2_metrics = results_dict['D2_STATIC_50_50']['metrics']
        marginal_d2 = calculate_marginal_contribution(
            d2_metrics, baseline, 'TREND STATIC (50/50)'
        )
        comparisons.append(marginal_d2)

    if 'E2_DYNAMIC_50_50' in results_dict:
        e2_metrics = results_dict['E2_DYNAMIC_50_50']['metrics']
        marginal_e2 = calculate_marginal_contribution(
            e2_metrics, baseline, 'TREND DYNAMIC (50/50)'
        )
        comparisons.append(marginal_e2)

    # 20/80 allocations
    if 'D3_STATIC_20_80' in results_dict:
        d3_metrics = results_dict['D3_STATIC_20_80']['metrics']
        marginal_d3 = calculate_marginal_contribution(
            d3_metrics, baseline, 'TREND STATIC (20/80)'
        )
        comparisons.append(marginal_d3)

    if 'E3_DYNAMIC_20_80' in results_dict:
        e3_metrics = results_dict['E3_DYNAMIC_20_80']['metrics']
        marginal_e3 = calculate_marginal_contribution(
            e3_metrics, baseline, 'TREND DYNAMIC (20/80)'
        )
        comparisons.append(marginal_e3)

    df = pd.DataFrame(comparisons)
    return df


def print_marginal_analysis(marginal_df: pd.DataFrame):
    """Pretty-print marginal contribution analysis."""
    print("\n" + "=" * 90)
    print("MARGINAL CONTRIBUTION ANALYSIS")
    print("=" * 90)
    print("\nBaseline: CARRY only")
    print("Question: What does adding TREND (static vs dynamic) provide?\n")

    print(f"{'Strategy':<30} {'Δ Sharpe':>10} {'Δ CAGR':>10} {'Δ MaxDD':>10} {'Δ Calmar':>10} {'Δ Vol':>10}")
    print("-" * 90)

    for _, row in marginal_df.iterrows():
        print(f"{row['strategy_name']:<30} "
              f"{row['marginal_sharpe']:>+10.2f} "
              f"{row['marginal_cagr']*100:>+9.1f}% "
              f"{row['marginal_max_dd']*100:>+9.1f}% "
              f"{row['marginal_calmar']:>+10.2f} "
              f"{row['marginal_vol']*100:>+9.1f}%")

    print("\nInterpretation:")
    print("  Δ Sharpe: Positive = improvement in risk-adjusted returns")
    print("  Δ CAGR: Positive = higher returns")
    print("  Δ MaxDD: Negative = smaller drawdown (better)")
    print("  Δ Calmar: Positive = better return/drawdown ratio")
    print("  Δ Vol: Can be positive or negative depending on correlation")


def run_full_decomposition(use_cache: bool = True):
    """
    Run full portfolio decomposition analysis.

    Args:
        use_cache: Use cached returns if available

    Returns:
        dict with all analysis results
    """
    print("=" * 90)
    print("PORTFOLIO DECOMPOSITION ANALYSIS")
    print("=" * 90)

    # Step 1: Load returns from cache
    if not use_cache:
        print("\nError: This script requires cached returns.")
        print("Please run run_portfolio_experiment.py first.")
        return None

    required_caches = ['carry_returns', 'trend_static_returns', 'trend_dynamic_returns', 'btc_returns']
    missing = [c for c in required_caches if not cache_exists(c)]

    if missing:
        print(f"\nError: Missing cached returns: {missing}")
        print("Please run run_portfolio_experiment.py first to generate cache.")
        return None

    carry_rets = load_returns('carry_returns')
    trend_static_rets = load_returns('trend_static_returns')
    trend_dynamic_rets = load_returns('trend_dynamic_returns')
    btc_rets = load_returns('btc_returns')

    # Step 2: Tail risk analysis
    print("\n" + "=" * 90)
    print("TAIL RISK ANALYSIS")
    print("=" * 90)

    tail_carry = analyze_tail_risk(carry_rets, "CARRY")
    tail_static = analyze_tail_risk(trend_static_rets, "TREND STATIC")
    tail_dynamic = analyze_tail_risk(trend_dynamic_rets, "TREND DYNAMIC")

    print("\n" + "-" * 90)
    print(f"{'Strategy':<20} {'5th %ile':>12} {'1st %ile':>12} {'Days <-2%':>12} {'Days <-5%':>12} {'Max Streak':>12}")
    print("-" * 90)
    for tail in [tail_carry, tail_static, tail_dynamic]:
        print(f"{tail['name']:<20} "
              f"{tail['pct_5']*100:>11.2f}% "
              f"{tail['pct_1']*100:>11.2f}% "
              f"{tail['days_below_minus_2pct']:>12} "
              f"{tail['days_below_minus_5pct']:>12} "
              f"{tail['longest_losing_streak']:>12}")

    # Step 3: Diversification comparison
    print("\n" + "=" * 90)
    print("DIVERSIFICATION COMPARISON")
    print("=" * 90)

    div_comp = compare_diversification(
        carry_rets, trend_static_rets, trend_dynamic_rets,
        "CARRY", "TREND"
    )

    print(f"\nCorrelation to {div_comp['baseline']}:")
    print(f"  {div_comp['addon']} STATIC:  {div_comp['static_correlation']:+.3f}")
    print(f"  {div_comp['addon']} DYNAMIC: {div_comp['dynamic_correlation']:+.3f}")
    print(f"  Improvement (lower is better): {div_comp['correlation_improvement']:+.3f}")

    print(f"\nBeta to {div_comp['baseline']}:")
    print(f"  {div_comp['addon']} STATIC:  {div_comp['static_beta']:+.3f}")
    print(f"  {div_comp['addon']} DYNAMIC: {div_comp['dynamic_beta']:+.3f}")

    print(f"\nVolatility:")
    print(f"  {div_comp['baseline']}: {div_comp['baseline_vol']*100:.1f}%")
    print(f"  {div_comp['addon']} STATIC:  {div_comp['static_vol']*100:.1f}%")
    print(f"  {div_comp['addon']} DYNAMIC: {div_comp['dynamic_vol']*100:.1f}%")

    # Step 4: Market neutrality check (beta to BTC)
    print("\n" + "=" * 90)
    print("MARKET NEUTRALITY CHECK")
    print("=" * 90)

    btc_carry = calculate_market_exposure(carry_rets, btc_rets, 'BTC')
    btc_static = calculate_market_exposure(trend_static_rets, btc_rets, 'BTC')
    btc_dynamic = calculate_market_exposure(trend_dynamic_rets, btc_rets, 'BTC')

    print(f"\n{'Strategy':<20} {'Correlation':>15} {'Beta':>10}")
    print("-" * 50)
    for name, exp in [('CARRY', btc_carry), ('TREND STATIC', btc_static), ('TREND DYNAMIC', btc_dynamic)]:
        print(f"{name:<20} {exp['correlation']:>+15.3f} {exp['beta']:>+10.3f}")

    print("\nInterpretation:")
    print("  Beta ≈ 0: Market-neutral (not correlated with BTC)")
    print("  Beta > 0.5: Directional exposure to BTC")
    print("  Beta ≈ 1: Moves with BTC")

    # Return all results
    return {
        'tail_risk': [tail_carry, tail_static, tail_dynamic],
        'diversification': div_comp,
        'market_exposure': {
            'CARRY': btc_carry,
            'TREND_STATIC': btc_static,
            'TREND_DYNAMIC': btc_dynamic
        }
    }


# =============================================================================
# MAIN
# =============================================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Decompose portfolio and analyze marginal contributions')
    parser.add_argument('--no-cache', action='store_true',
                        help='Disable cache (will fail if returns not cached)')

    args = parser.parse_args()

    # Run decomposition
    results = run_full_decomposition(use_cache=not args.no_cache)

    if results is not None:
        print("\n" + "=" * 90)
        print("✓ Portfolio decomposition complete")
        print("=" * 90)
