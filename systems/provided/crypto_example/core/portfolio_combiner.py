"""
Portfolio Combination Framework
================================
Combines CARRY and TREND sleeves using simple weight-based allocation.

Uses fixed weights (e.g., 80/20, 50/50) rather than risk-based allocation to
preserve skew management. CARRY has high Sharpe but negative skew (rare large losses),
so Sharpe-driven allocation would over-allocate to CARRY. Following Carver's guidance,
we intentionally constrain CARRY exposure to manage portfolio skew.

Weight-based approach:
    combined_returns = trend_weight * trend_returns + carry_weight * carry_returns

where trend_weight + carry_weight = 1.0
"""

import numpy as np
import pandas as pd


def combine_sleeves_simple_weights(
    trend_returns: pd.Series,
    carry_returns: pd.Series,
    trend_weight: float = 0.8,
    carry_weight: float = 0.2,
    verbose: bool = True
) -> pd.Series:
    """
    Combine TREND and CARRY sleeves using fixed weights.

    Args:
        trend_returns: Daily percentage returns for TREND strategy
        carry_returns: Daily percentage returns for CARRY strategy
        trend_weight: Weight for TREND sleeve (default 0.8 = 80%)
        carry_weight: Weight for CARRY sleeve (default 0.2 = 20%)
        verbose: Print diagnostic information

    Returns:
        pd.Series: Combined daily percentage returns

    Raises:
        ValueError: If weights don't sum to 1.0 or are negative
    """

    # Validate weights
    if abs(trend_weight + carry_weight - 1.0) > 1e-6:
        raise ValueError(f"Weights must sum to 1.0, got {trend_weight + carry_weight:.4f}")
    if trend_weight < 0 or carry_weight < 0:
        raise ValueError("Weights must be non-negative")

    # Align returns on common dates
    common_dates = trend_returns.index.intersection(carry_returns.index)
    trend_aligned = trend_returns.loc[common_dates]
    carry_aligned = carry_returns.loc[common_dates]

    if len(common_dates) == 0:
        raise ValueError("No common dates between TREND and CARRY returns")

    # Combine with fixed weights
    combined_returns = trend_weight * trend_aligned + carry_weight * carry_aligned

    if verbose:
        print(f"\n{'='*90}")
        print(f"PORTFOLIO COMBINATION: {trend_weight*100:.0f}% TREND / {carry_weight*100:.0f}% CARRY")
        print(f"{'='*90}")
        print(f"  Date range: {combined_returns.index.min().date()} to {combined_returns.index.max().date()}")
        print(f"  Days: {len(combined_returns)}")
        print(f"  TREND contribution: {trend_weight*100:.0f}%")
        print(f"  CARRY contribution: {carry_weight*100:.0f}%")

        # Quick stats
        DAYS_PER_YEAR = 365
        ann_ret = combined_returns.mean() * DAYS_PER_YEAR
        ann_vol = combined_returns.std() * np.sqrt(DAYS_PER_YEAR)
        sharpe = ann_ret / ann_vol if ann_vol > 0 else 0

        print(f"\n  Combined portfolio:")
        print(f"    Ann return: {ann_ret*100:.2f}%")
        print(f"    Ann vol: {ann_vol*100:.2f}%")
        print(f"    Sharpe: {sharpe:.2f}")

    return combined_returns


def combine_multiple_allocations(
    trend_returns: pd.Series,
    carry_returns: pd.Series,
    allocations: list = None,
    verbose: bool = True
) -> dict:
    """
    Combine TREND and CARRY using multiple allocation scenarios.

    Args:
        trend_returns: Daily percentage returns for TREND strategy
        carry_returns: Daily percentage returns for CARRY strategy
        allocations: List of (trend_weight, carry_weight) tuples
                    Default: [(0.8, 0.2), (0.5, 0.5), (0.2, 0.8)]
        verbose: Print diagnostic information

    Returns:
        dict: {
            'case_name': combined_returns_series,
            ...
        }
    """

    if allocations is None:
        # Default allocations: 80/20, 50/50, 20/80
        allocations = [
            (0.8, 0.2),  # Conservative CARRY
            (0.5, 0.5),  # Balanced
            (0.2, 0.8),  # Aggressive CARRY (skew test)
        ]

    results = {}

    for trend_weight, carry_weight in allocations:
        # Create case name
        case_name = f"{int(trend_weight*100)}_{int(carry_weight*100)}"

        # Combine
        combined = combine_sleeves_simple_weights(
            trend_returns=trend_returns,
            carry_returns=carry_returns,
            trend_weight=trend_weight,
            carry_weight=carry_weight,
            verbose=verbose
        )

        results[case_name] = combined

    return results


def calculate_correlation_and_beta(
    returns_x: pd.Series,
    returns_y: pd.Series,
    verbose: bool = True
) -> dict:
    """
    Calculate correlation and beta between two return series.

    Beta = Cov(X, Y) / Var(Y)
    - Beta measures how much X moves relative to Y
    - Beta > 1: X is more volatile than Y
    - Beta < 1: X is less volatile than Y
    - Beta ≈ 0: X is uncorrelated with Y (market-neutral)

    Args:
        returns_x: First return series (dependent variable)
        returns_y: Second return series (independent variable)
        verbose: Print results

    Returns:
        dict: {
            'correlation': float,
            'beta': float,
            'days': int
        }
    """

    # Align on common dates
    common_dates = returns_x.index.intersection(returns_y.index)
    x_aligned = returns_x.loc[common_dates]
    y_aligned = returns_y.loc[common_dates]

    # Calculate correlation
    correlation = x_aligned.corr(y_aligned)

    # Calculate beta: Cov(X, Y) / Var(Y)
    covariance = x_aligned.cov(y_aligned)
    variance_y = y_aligned.var()
    beta = covariance / variance_y if variance_y > 0 else 0

    if verbose:
        print(f"  Correlation: {correlation:.2f}")
        print(f"  Beta: {beta:.2f}")
        print(f"  Days: {len(common_dates)}")

    return {
        'correlation': correlation,
        'beta': beta,
        'days': len(common_dates)
    }


# =============================================================================
# MAIN (for testing)
# =============================================================================

if __name__ == "__main__":
    # Test combination
    print("=" * 90)
    print("TESTING PORTFOLIO COMBINER")
    print("=" * 90)

    # Create dummy returns for testing
    dates = pd.date_range('2020-01-01', '2025-12-31', freq='D')
    np.random.seed(42)

    # Dummy TREND: 25% vol, Sharpe 0.7
    trend_rets = pd.Series(
        np.random.normal(0.0007, 0.013, len(dates)),  # ~26% ann vol
        index=dates
    )

    # Dummy CARRY: 12.5% vol, Sharpe 1.5
    carry_rets = pd.Series(
        np.random.normal(0.0006, 0.0065, len(dates)),  # ~12.4% ann vol
        index=dates
    )

    # Test single combination
    print("\nTesting 80/20 allocation:")
    combined = combine_sleeves_simple_weights(
        trend_returns=trend_rets,
        carry_returns=carry_rets,
        trend_weight=0.8,
        carry_weight=0.2,
        verbose=True
    )

    # Test multiple allocations
    print("\n" + "=" * 90)
    print("Testing multiple allocations:")
    results = combine_multiple_allocations(
        trend_returns=trend_rets,
        carry_returns=carry_rets,
        verbose=True
    )

    # Test correlation and beta
    print("\n" + "=" * 90)
    print("Testing correlation and beta:")
    stats = calculate_correlation_and_beta(
        returns_x=trend_rets,
        returns_y=carry_rets,
        verbose=True
    )

    print("\n" + "=" * 90)
    print("✓ Portfolio combiner tests complete")
    print("=" * 90)
