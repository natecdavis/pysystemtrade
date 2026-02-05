"""
Test IDM calculation to validate Carver-style definition

According to Rob Carver's "Leveraged Trading" and "Systematic Trading":
- IDM is a diversification MULTIPLIER
- IDM >= 1.0 always (no diversification means IDM = 1.0)
- IDM increases with number of uncorrelated instruments
- IDM calculated on NORMALIZED weights (summing to 1.0)

Current Implementation Issue:
- calculate_idm() uses leveraged weights (can sum to 2.0)
- This makes portfolio_stdev > 1.0
- So IDM = 1 / portfolio_stdev can be < 1.0
- This violates Carver's definition

This test demonstrates correct vs current behavior.
"""

import numpy as np
import pandas as pd
import pytest
from systems.crypto_perps.constraints import calculate_idm, calculate_portfolio_stdev


def test_idm_perfectly_correlated_should_equal_one():
    """
    Test: Perfectly correlated assets should give IDM = 1.0

    With perfectly correlated assets (corr = 1.0 everywhere),
    there is NO diversification benefit, so IDM should be 1.0.
    """
    # Setup: 3 instruments, equal weights normalized to 1.0
    weights = {'A': 0.333, 'B': 0.333, 'C': 0.334}
    corr_matrix = pd.DataFrame(
        [[1.0, 1.0, 1.0],
         [1.0, 1.0, 1.0],
         [1.0, 1.0, 1.0]],
        index=['A', 'B', 'C'],
        columns=['A', 'B', 'C']
    )

    # Calculate IDM
    idm = calculate_idm(weights, corr_matrix)
    portfolio_stdev = calculate_portfolio_stdev(weights, corr_matrix)

    print(f"\nPerfectly correlated, normalized weights:")
    print(f"  Sum of weights: {sum(weights.values()):.3f}")
    print(f"  Portfolio stdev: {portfolio_stdev:.3f}")
    print(f"  IDM: {idm:.3f}")

    # With perfect correlation and normalized weights summing to 1.0:
    # portfolio_stdev = sqrt(1.0) = 1.0
    # IDM = 1 / 1.0 = 1.0
    assert abs(portfolio_stdev - 1.0) < 0.01, f"Expected portfolio_stdev ~1.0, got {portfolio_stdev}"
    assert abs(idm - 1.0) < 0.01, f"Expected IDM ~1.0 for perfect correlation, got {idm}"


def test_idm_uncorrelated_should_be_greater_than_one():
    """
    Test: Uncorrelated assets should give IDM > 1.0

    With zero correlation, portfolio_stdev < 1.0 due to diversification,
    so IDM = 1 / portfolio_stdev > 1.0.
    """
    # Setup: 3 instruments, equal weights normalized to 1.0
    weights = {'A': 0.333, 'B': 0.333, 'C': 0.334}
    corr_matrix = pd.DataFrame(
        [[1.0, 0.0, 0.0],
         [0.0, 1.0, 0.0],
         [0.0, 0.0, 1.0]],
        index=['A', 'B', 'C'],
        columns=['A', 'B', 'C']
    )

    # Calculate IDM
    idm = calculate_idm(weights, corr_matrix)
    portfolio_stdev = calculate_portfolio_stdev(weights, corr_matrix)

    print(f"\nUncorrelated, normalized weights:")
    print(f"  Sum of weights: {sum(weights.values()):.3f}")
    print(f"  Portfolio stdev: {portfolio_stdev:.3f}")
    print(f"  IDM: {idm:.3f}")

    # With zero correlation and equal weights:
    # portfolio_var = sum(w_i^2) = 3 * (0.333)^2 ≈ 0.333
    # portfolio_stdev = sqrt(0.333) ≈ 0.577
    # IDM = 1 / 0.577 ≈ 1.73
    expected_stdev = np.sqrt(sum(w**2 for w in weights.values()))
    expected_idm = 1.0 / expected_stdev

    assert abs(portfolio_stdev - expected_stdev) < 0.01
    assert abs(idm - expected_idm) < 0.01
    assert idm > 1.0, f"Expected IDM > 1.0 for uncorrelated assets, got {idm}"
    print(f"  ✓ IDM = {idm:.3f} > 1.0 (good diversification)")


def test_idm_leveraged_weights_violates_definition():
    """
    Test: VERIFIES THE FIX

    With normalize=True (default), leveraged weights still give IDM >= 1.0.
    This test verifies that the bug is fixed.
    """
    # Setup: 3 instruments, equal weights leveraged to 2.0
    weights = {'A': 0.666, 'B': 0.666, 'C': 0.668}  # Sum = 2.0
    corr_matrix = pd.DataFrame(
        [[1.0, 1.0, 1.0],
         [1.0, 1.0, 1.0],
         [1.0, 1.0, 1.0]],
        index=['A', 'B', 'C'],
        columns=['A', 'B', 'C']
    )

    # Calculate IDM with normalize=True (default, Carver-style)
    idm_normalized = calculate_idm(weights, corr_matrix, normalize=True)

    # Calculate IDM with normalize=False (legacy, buggy behavior)
    idm_legacy = calculate_idm(weights, corr_matrix, normalize=False)

    portfolio_stdev = calculate_portfolio_stdev(weights, corr_matrix)

    print(f"\nPerfectly correlated, LEVERAGED weights:")
    print(f"  Sum of weights: {sum(weights.values()):.3f}")
    print(f"  Portfolio stdev (leveraged): {portfolio_stdev:.3f}")
    print(f"  IDM (normalize=True, Carver-style): {idm_normalized:.3f}")
    print(f"  IDM (normalize=False, legacy): {idm_legacy:.3f}")

    # With normalize=True (Carver-style):
    # - Weights are normalized to sum=1.0 before IDM calculation
    # - portfolio_stdev = 1.0 (perfectly correlated, normalized)
    # - IDM = 1 / 1.0 = 1.0 ✓ (correct, >= 1.0)
    assert abs(idm_normalized - 1.0) < 0.01, \
        f"IDM with normalize=True should be 1.0, got {idm_normalized:.3f}"

    # With normalize=False (legacy, buggy):
    # - Uses leveraged weights directly
    # - portfolio_stdev = 2.0
    # - IDM = 1 / 2.0 = 0.5 ✗ (violates definition)
    assert abs(idm_legacy - 0.5) < 0.01, \
        f"IDM with normalize=False should be 0.5 (legacy), got {idm_legacy:.3f}"

    print(f"\n✓ FIX VERIFIED: normalize=True ensures IDM >= 1.0")
    print(f"  - normalize=True (default): IDM = {idm_normalized:.3f} >= 1.0 ✓")
    print(f"  - normalize=False (legacy): IDM = {idm_legacy:.3f} < 1.0 ✗")


def test_idm_definition_carver_style():
    """
    Test: Demonstrate correct Carver-style IDM calculation

    Carver's IDM definition:
    1. Normalize weights to sum to 1.0 (or |w| to 1.0 for long/short)
    2. Calculate portfolio_stdev on normalized weights
    3. IDM = 1 / portfolio_stdev
    4. Result: IDM >= 1.0 always, with equality for perfect correlation
    """
    print("\n" + "="*80)
    print("CORRECT Carver-style IDM Calculation")
    print("="*80)

    # Scenario: Leveraged portfolio with 2.0 gross leverage
    raw_weights = {'A': 0.666, 'B': 0.666, 'C': 0.668}  # Sum = 2.0
    corr_matrix = pd.DataFrame(
        [[1.0, 0.6, 0.6],
         [0.6, 1.0, 0.6],
         [0.6, 0.6, 1.0]],
        index=['A', 'B', 'C'],
        columns=['A', 'B', 'C']
    )

    # CORRECT: Normalize weights first
    total_abs_weight = sum(abs(w) for w in raw_weights.values())
    normalized_weights = {k: v / total_abs_weight for k, v in raw_weights.items()}

    # Calculate IDM on normalized weights
    idm_normalized = calculate_idm(normalized_weights, corr_matrix)
    portfolio_stdev_normalized = calculate_portfolio_stdev(normalized_weights, corr_matrix)

    # INCORRECT (current implementation): IDM on leveraged weights
    idm_leveraged = calculate_idm(raw_weights, corr_matrix)
    portfolio_stdev_leveraged = calculate_portfolio_stdev(raw_weights, corr_matrix)

    print(f"\nRaw leveraged weights (sum = {total_abs_weight:.3f}):")
    print(f"  Portfolio stdev: {portfolio_stdev_leveraged:.3f}")
    print(f"  IDM: {idm_leveraged:.3f} (CURRENT IMPLEMENTATION - WRONG)")

    print(f"\nNormalized weights (sum = 1.0):")
    print(f"  Portfolio stdev: {portfolio_stdev_normalized:.3f}")
    print(f"  IDM: {idm_normalized:.3f} (CORRECT Carver-style)")

    assert abs(sum(normalized_weights.values()) - 1.0) < 0.01
    assert idm_normalized >= 1.0, f"Carver-style IDM should be >= 1.0, got {idm_normalized}"

    print(f"\n✓ Carver-style IDM = {idm_normalized:.3f} >= 1.0 (correct)")
    print(f"✗ Current IDM = {idm_leveraged:.3f} < 1.0 (violates definition)")


if __name__ == '__main__':
    # Run tests with verbose output
    pytest.main([__file__, '-v', '-s'])
