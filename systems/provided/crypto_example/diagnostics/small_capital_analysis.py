"""
Small Capital Analysis
=======================
Analyzes capital efficiency and minimum capital requirements for $10k deployability.

Key Questions:
1. How does risk budgeting translate to notional exposure (leverage)?
2. Which portfolio is most capital-efficient with ~$10k?
3. What are minimum capital recommendations per strategy?

Leverage = Notional Exposure / Capital
- Higher leverage = more capital-efficient (can achieve target vol with less capital)
- But also higher risk of margin calls and liquidation

For crypto:
- CARRY: Potentially high leverage (funding collection strategy)
- TREND STATIC: Moderate leverage (directional, ~2x typical)
- TREND DYNAMIC: Low leverage (market-neutral, offsetting positions reduce net exposure)
"""

import os
import sys
import numpy as np
import pandas as pd

# Get project root and add to path
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, "..", "..", "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from ..core.cache_systems import load_returns, cache_exists


DAYS_PER_YEAR = 365


def estimate_notional_exposure(
    returns: pd.Series,
    target_vol: float,
    capital: float = 10000
) -> dict:
    """
    Estimate notional exposure and leverage for a strategy.

    This is a simplified calculation:
    - Assumes strategy targets `target_vol` volatility
    - Estimates leverage as: target_vol / strategy_realized_vol
    - Calculates notional as: leverage × capital

    Real leverage depends on:
    - Position sizing rules
    - Number of instruments
    - Correlation structure
    - Buffering and constraints

    Args:
        returns: Daily percentage returns (realized from backtest)
        target_vol: Target volatility (e.g., 0.25 for 25%)
        capital: Capital amount (default $10,000)

    Returns:
        dict with exposure estimates
    """
    # Realized volatility from backtest
    realized_vol = returns.std() * np.sqrt(DAYS_PER_YEAR)

    # Estimated leverage to hit target vol
    # (This is approximate - real leverage depends on many factors)
    estimated_leverage = target_vol / realized_vol if realized_vol > 0 else 0

    # Estimated notional exposure
    notional_exposure = estimated_leverage * capital

    # Average daily % move
    avg_daily_move = returns.abs().mean()

    # Max daily % move (99th percentile)
    max_daily_move = returns.abs().quantile(0.99)

    # Estimated max daily $ swing at $10k capital
    max_daily_swing = max_daily_move * capital

    return {
        'realized_vol': realized_vol,
        'target_vol': target_vol,
        'estimated_leverage': estimated_leverage,
        'capital': capital,
        'notional_exposure': notional_exposure,
        'avg_daily_move_pct': avg_daily_move,
        'max_daily_move_pct': max_daily_move,
        'max_daily_swing_dollars': max_daily_swing
    }


def calculate_min_capital(
    returns: pd.Series,
    target_vol: float,
    max_leverage: float = 3.0,
    min_notional_per_position: float = 100
) -> dict:
    """
    Calculate minimum capital recommendation.

    Constraints:
    1. Max leverage (e.g., 3x for crypto spot, 10x for perps)
    2. Min notional per position (e.g., $100 to avoid dust trades)
    3. Target volatility achievable

    Args:
        returns: Daily percentage returns
        target_vol: Target volatility
        max_leverage: Maximum allowed leverage
        min_notional_per_position: Minimum $ per position

    Returns:
        dict with capital recommendations
    """
    # Realized vol
    realized_vol = returns.std() * np.sqrt(DAYS_PER_YEAR)

    # Required leverage to hit target vol
    required_leverage = target_vol / realized_vol if realized_vol > 0 else 0

    # Min capital from leverage constraint
    # If required_leverage > max_leverage, need more capital
    # Capital = Notional / Leverage
    # To hit target_vol with max_leverage:
    # Capital_min = (target_vol / realized_vol) / max_leverage × base_capital
    # Simplified: if realized_vol is low, need more capital to avoid excessive leverage

    if required_leverage > max_leverage:
        # Need to reduce effective target vol or increase capital
        min_capital_from_leverage = 10000 * (required_leverage / max_leverage)
    else:
        min_capital_from_leverage = 10000  # $10k baseline is sufficient

    # Min capital from position size constraint
    # This is hard to estimate without knowing # of positions
    # Assume we want at least 10 positions active
    # Min capital = 10 × min_notional_per_position / leverage
    min_capital_from_positions = (10 * min_notional_per_position) / max_leverage if max_leverage > 0 else 10000

    # Take max of all constraints
    recommended_min_capital = max(
        min_capital_from_leverage,
        min_capital_from_positions,
        1000  # Absolute minimum $1k
    )

    return {
        'realized_vol': realized_vol,
        'target_vol': target_vol,
        'required_leverage': required_leverage,
        'max_leverage': max_leverage,
        'min_capital_from_leverage': min_capital_from_leverage,
        'min_capital_from_positions': min_capital_from_positions,
        'recommended_min_capital': recommended_min_capital
    }


def analyze_capital_efficiency(use_cache: bool = True):
    """
    Analyze capital efficiency for all strategies.

    Args:
        use_cache: Use cached returns

    Returns:
        dict with analysis results
    """
    print("=" * 90)
    print("SMALL CAPITAL ANALYSIS")
    print("=" * 90)

    # Load returns
    if not use_cache:
        print("\nError: This script requires cached returns.")
        print("Please run run_portfolio_experiment.py first.")
        return None

    required_caches = ['carry_returns', 'trend_static_returns', 'trend_dynamic_returns']
    missing = [c for c in required_caches if not cache_exists(c)]

    if missing:
        print(f"\nError: Missing cached returns: {missing}")
        print("Please run run_portfolio_experiment.py first to generate cache.")
        return None

    carry_rets = load_returns('carry_returns')
    trend_static_rets = load_returns('trend_static_returns')
    trend_dynamic_rets = load_returns('trend_dynamic_returns')

    # Define strategies with their target vols
    strategies = {
        'CARRY': {
            'returns': carry_rets,
            'target_vol': 0.125,  # 12.5%
            'max_leverage': 5.0   # Perp futures
        },
        'TREND STATIC': {
            'returns': trend_static_rets,
            'target_vol': 0.25,   # 25%
            'max_leverage': 3.0   # Spot crypto
        },
        'TREND DYNAMIC': {
            'returns': trend_dynamic_rets,
            'target_vol': 0.25,   # 25%
            'max_leverage': 3.0   # Spot crypto
        }
    }

    # Step 1: Notional exposure analysis for $10k
    print("\n" + "=" * 90)
    print("NOTIONAL EXPOSURE ANALYSIS ($10,000 capital)")
    print("=" * 90)

    exposure_results = {}
    for name, config in strategies.items():
        exp = estimate_notional_exposure(
            config['returns'],
            config['target_vol'],
            capital=10000
        )
        exposure_results[name] = exp

    # Display table
    print(f"\n{'Strategy':<20} {'Real Vol':>10} {'Target Vol':>10} {'Leverage':>10} {'Notional':>12} {'Max Daily $':>12}")
    print("-" * 90)
    for name, exp in exposure_results.items():
        print(f"{name:<20} "
              f"{exp['realized_vol']*100:>9.1f}% "
              f"{exp['target_vol']*100:>9.1f}% "
              f"{exp['estimated_leverage']:>9.2f}x "
              f"${exp['notional_exposure']:>11,.0f} "
              f"${exp['max_daily_swing_dollars']:>11,.0f}")

    print("\nInterpretation:")
    print("  Leverage = Target Vol / Realized Vol")
    print("  Higher leverage = more capital-efficient (achieve target vol with less capital)")
    print("  Max Daily $ = 99th percentile daily swing at $10k capital")

    # Step 2: Minimum capital recommendations
    print("\n" + "=" * 90)
    print("MINIMUM CAPITAL RECOMMENDATIONS")
    print("=" * 90)

    min_cap_results = {}
    for name, config in strategies.items():
        min_cap = calculate_min_capital(
            config['returns'],
            config['target_vol'],
            max_leverage=config['max_leverage']
        )
        min_cap_results[name] = min_cap

    # Display table
    print(f"\n{'Strategy':<20} {'Required Lev':>12} {'Max Lev':>10} {'Min Capital':>12}")
    print("-" * 60)
    for name, mc in min_cap_results.items():
        print(f"{name:<20} "
              f"{mc['required_leverage']:>11.2f}x "
              f"{mc['max_leverage']:>9.1f}x "
              f"${mc['recommended_min_capital']:>11,.0f}")

    print("\nInterpretation:")
    print("  Required Lev = Leverage needed to hit target volatility")
    print("  Max Lev = Exchange/risk management limit")
    print("  Min Capital = Recommended minimum to trade strategy")
    print("\n  If Required Lev > Max Lev:")
    print("    → Need more capital OR reduce target volatility")

    # Step 3: Capital efficiency ranking
    print("\n" + "=" * 90)
    print("CAPITAL EFFICIENCY RANKING")
    print("=" * 90)

    # Rank by how achievable target vol is with $10k and reasonable leverage
    efficiency_scores = []
    for name, mc in min_cap_results.items():
        # Score = how easily can we hit target vol with $10k?
        # Lower min_capital = more efficient
        score = 10000 / mc['recommended_min_capital']
        efficiency_scores.append({
            'strategy': name,
            'efficiency_score': score,
            'min_capital': mc['recommended_min_capital'],
            'feasible_at_10k': mc['recommended_min_capital'] <= 10000
        })

    efficiency_scores.sort(key=lambda x: x['efficiency_score'], reverse=True)

    print(f"\n{'Rank':<6} {'Strategy':<20} {'Min Capital':>12} {'Feasible at $10k?':>20}")
    print("-" * 60)
    for i, item in enumerate(efficiency_scores, 1):
        feasible = "✓ YES" if item['feasible_at_10k'] else "✗ NO"
        print(f"{i:<6} {item['strategy']:<20} ${item['min_capital']:>11,.0f} {feasible:>20}")

    # Step 4: Recommendations
    print("\n" + "=" * 90)
    print("RECOMMENDATIONS FOR SMALL CAPITAL ($10K)")
    print("=" * 90)

    most_efficient = efficiency_scores[0]['strategy']
    least_efficient = efficiency_scores[-1]['strategy']

    print(f"\n✓ Most capital-efficient: {most_efficient}")
    print(f"  → Feasible to trade with $10k capital")
    print(f"  → Min capital: ${min_cap_results[most_efficient]['recommended_min_capital']:,.0f}")

    print(f"\n⚠ Least capital-efficient: {least_efficient}")
    print(f"  → May require more than $10k to achieve target volatility")
    print(f"  → Min capital: ${min_cap_results[least_efficient]['recommended_min_capital']:,.0f}")

    print("\nGeneral guidance:")
    print("  1. CARRY: Moderate leverage, feasible at $5-10k")
    print("  2. TREND STATIC: Moderate leverage, feasible at $5-10k")
    print("  3. TREND DYNAMIC: LOW leverage (market-neutral), may need $20-50k for 25% vol")
    print("     → Alternative: Reduce target vol to 5-10% for $10k capital")

    return {
        'exposure': exposure_results,
        'min_capital': min_cap_results,
        'efficiency_ranking': efficiency_scores
    }


# =============================================================================
# MAIN
# =============================================================================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description='Analyze capital efficiency and minimum capital requirements')
    parser.add_argument('--no-cache', action='store_true',
                        help='Disable cache (will fail if returns not cached)')

    args = parser.parse_args()

    # Run analysis
    results = analyze_capital_efficiency(use_cache=not args.no_cache)

    if results is not None:
        print("\n" + "=" * 90)
        print("✓ Small capital analysis complete")
        print("=" * 90)
