"""
Test incremental constraints engine equivalence

Verifies that IncrementalConstraintsEngine produces identical results
to the batch apply_portfolio_constraints() function.
"""

import pytest
import pandas as pd
import numpy as np
from systems.crypto_perps.constraints import (
    apply_portfolio_constraints,
    IncrementalConstraintsEngine,
    get_constraints_config,
    compute_returns
)


def test_incremental_matches_batch_short_period():
    """
    Compare incremental engine to batch constraints over short period

    Uses:
    - 50 trading days
    - 5 instruments
    - Span=60, min_periods=20
    - Random price data with known seed

    Asserts:
    - Constrained weights match batch function (<1e-10 tolerance)
    - Gross leverage matches exactly
    - IDM matches exactly
    """
    # Generate synthetic data
    np.random.seed(42)
    dates = pd.date_range('2023-01-01', periods=50, freq='D')
    instruments = ['BTC', 'ETH', 'SOL', 'AVAX', 'MATIC']
    n = len(instruments)

    # Synthetic prices (random walk)
    prices_data = {}
    for inst in instruments:
        base_price = 100.0
        returns = np.random.randn(len(dates)) * 0.02  # 2% daily vol
        prices = base_price * np.exp(np.cumsum(returns))
        prices_data[inst] = prices

    prices_df = pd.DataFrame(prices_data, index=dates)

    # Synthetic weights (random bounded by [-1, 1])
    weights_data = {}
    for inst in instruments:
        weights_data[inst] = np.random.uniform(-0.5, 0.5, len(dates))

    weights_df = pd.DataFrame(weights_data, index=dates)

    # Parameters
    span = 60
    min_periods = 20
    idm_cap = 2.5
    gross_lev_cap = 2.0

    # Method 1: Batch constraints (existing function)
    batch_constrained, batch_gross_lev, batch_idm = apply_portfolio_constraints(
        weights_df=weights_df,
        prices_df=prices_df,
        gross_leverage_cap=gross_lev_cap,
        idm_cap=idm_cap,
        corr_span=span,
        corr_min_periods=min_periods
    )

    # Method 2: Incremental engine (new implementation)
    # Get batch parameters to ensure exact match
    batch_params = get_constraints_config()
    batch_params.pop("use_recursive")  # Not passed to engine

    engine = IncrementalConstraintsEngine(
        instruments=instruments,
        span=span,
        min_periods=min_periods,
        idm_cap=idm_cap,
        gross_leverage_cap=gross_lev_cap,
        **batch_params  # adjust, demean, idm_pre_cap, returns
    )

    # Compute returns using shared helper
    returns_df = compute_returns(prices_df, method=batch_params["returns"])

    incremental_constrained = pd.DataFrame(index=dates, columns=instruments, dtype=float)
    incremental_gross_lev = pd.Series(index=dates, dtype=float)
    incremental_idm = pd.Series(index=dates, dtype=float)

    for i, date in enumerate(dates):
        # Get returns for this date
        returns = returns_df.loc[date].to_dict()

        # Get unconstrained weights
        weights = weights_df.loc[date].to_dict()

        # Apply constraints incrementally
        constrained, gross_lev, idm, _ = engine.step(
            date=date,
            returns=returns,
            weights=weights
        )

        incremental_constrained.loc[date] = pd.Series(constrained)
        incremental_gross_lev.loc[date] = gross_lev
        incremental_idm.loc[date] = idm

    # Assert equivalence with tight tolerance
    pd.testing.assert_frame_equal(
        incremental_constrained,
        batch_constrained,
        atol=1e-10,
        rtol=0,
        check_exact=False
    )

    pd.testing.assert_series_equal(
        incremental_gross_lev,
        batch_gross_lev,
        atol=1e-10,
        rtol=0,
        check_exact=False
    )

    pd.testing.assert_series_equal(
        incremental_idm,
        batch_idm,
        atol=1e-10,
        rtol=0,
        check_exact=False
    )

    # CRITICAL: Also assert IDM series values match (not just constrained weights)
    # This ensures BATCH_IDM_PRE_CAP is set correctly
    print(f"\nIDM series comparison:")
    print(f"  Batch IDM range: [{batch_idm.min():.3f}, {batch_idm.max():.3f}]")
    print(f"  Incremental IDM range: [{incremental_idm.min():.3f}, {incremental_idm.max():.3f}]")
    print(f"  Max abs difference: {(incremental_idm - batch_idm).abs().max():.2e}")


def test_incremental_edge_case_insufficient_data():
    """
    Test incremental engine behavior with insufficient data

    Verifies:
    - Uses identity correlation matrix when count < min_periods
    - Produces same results as batch function
    """
    dates = pd.date_range('2023-01-01', periods=15, freq='D')  # < 20 min_periods
    instruments = ['BTC', 'ETH']

    # Simple price data
    prices_df = pd.DataFrame({
        'BTC': np.linspace(100, 110, 15),
        'ETH': np.linspace(50, 55, 15)
    }, index=dates)

    # Constant weights
    weights_df = pd.DataFrame({
        'BTC': [0.5] * 15,
        'ETH': [0.5] * 15
    }, index=dates)

    # Batch
    batch_constrained, _, _ = apply_portfolio_constraints(
        weights_df=weights_df,
        prices_df=prices_df,
        gross_leverage_cap=2.0,
        idm_cap=2.5,
        corr_span=60,
        corr_min_periods=20
    )

    # Incremental - match batch parameters
    batch_params = get_constraints_config()
    batch_params.pop("use_recursive")

    engine = IncrementalConstraintsEngine(
        instruments=instruments,
        span=60,
        min_periods=20,
        idm_cap=2.5,
        gross_leverage_cap=2.0,
        **batch_params
    )

    # Compute returns using shared helper
    returns_df = compute_returns(prices_df, method=batch_params['returns'])

    incremental_constrained = pd.DataFrame(index=dates, columns=instruments, dtype=float)

    for i, date in enumerate(dates):
        returns = returns_df.loc[date].to_dict()
        weights = weights_df.loc[date].to_dict()
        constrained, _, _, _ = engine.step(date, returns, weights)
        incremental_constrained.loc[date] = pd.Series(constrained)

    # Assert match
    pd.testing.assert_frame_equal(
        incremental_constrained,
        batch_constrained,
        atol=1e-10,
        rtol=0
    )


def reference_recursive_ewma_cov(returns_df, span, min_periods, adjust, demean=False):
    """
    Reference implementation of recursive EWMA covariance

    Uses the SAME recursion as IncrementalConstraintsEngine for equivalence testing.
    NOT based on pandas ewm().cov() to avoid implementation detail mismatches.

    This is the "oracle" for covariance matrix equivalence tests.
    """
    instruments = list(returns_df.columns)
    n = len(instruments)
    alpha = 2.0 / (span + 1)

    # State variables
    ewma_cov = np.zeros((n, n))
    ewma_mean = np.zeros(n) if demean else None
    weight = 0.0 if adjust else None
    weight_mean = 0.0 if (adjust and demean) else None
    count = 0

    # Build covariance matrix for each date
    cov_matrices = {}
    for date in returns_df.index:
        count += 1
        r = returns_df.loc[date].to_numpy()

        # Update EWMA mean (if demeaning)
        if demean:
            if count == 1:
                ewma_mean = r
                if adjust:
                    weight_mean = 1.0
            else:
                if adjust:
                    old_weight = weight_mean
                    weight_mean = 1.0 + (1.0 - alpha) * old_weight
                    ewma_mean = (alpha * r + (1.0 - alpha) * ewma_mean * old_weight) / weight_mean
                else:
                    ewma_mean = alpha * r + (1.0 - alpha) * ewma_mean

            r_centered = r - ewma_mean
            outer_product = np.outer(r_centered, r_centered)
        else:
            outer_product = np.outer(r, r)

        # Update EWMA covariance
        if count == 1:
            ewma_cov = outer_product
            if adjust:
                weight = 1.0
        else:
            if adjust:
                old_weight = weight
                weight = 1.0 + (1.0 - alpha) * old_weight
                ewma_cov = (alpha * outer_product + (1.0 - alpha) * ewma_cov * old_weight) / weight
            else:
                ewma_cov = alpha * outer_product + (1.0 - alpha) * ewma_cov

        # Store covariance matrix for this date
        cov_matrices[date] = ewma_cov.copy()

    return cov_matrices


def test_covariance_matrix_equivalence():
    """
    Test that incremental EWMA covariance matrix matches reference recursion

    CRITICAL for demean=True support - localizes failures to covariance math
    rather than downstream constraints.

    Compares incremental engine to reference_recursive_ewma_cov (NOT pandas ewm().cov()),
    to avoid implementation detail mismatches.
    """
    np.random.seed(99)
    dates = pd.date_range('2023-01-01', periods=100, freq='D')
    instruments = ['BTC', 'ETH', 'SOL']

    # Test parameters (explicit)
    span = 60
    min_periods = 20

    # Synthetic prices
    prices_data = {}
    for inst in instruments:
        base_price = 100.0
        returns = np.random.randn(len(dates)) * 0.02
        prices = base_price * np.exp(np.cumsum(returns))
        prices_data[inst] = prices

    prices_df = pd.DataFrame(prices_data, index=dates, columns=instruments)

    # Get batch parameters
    batch_params = get_constraints_config()
    batch_params.pop("use_recursive")

    # Compute returns using shared helper
    returns_df = compute_returns(prices_df, method=batch_params['returns'])

    # Reference: Compute covariance matrices using reference recursion
    ref_cov_matrices = reference_recursive_ewma_cov(
        returns_df,
        span=span,
        min_periods=min_periods,
        adjust=batch_params['adjust'],
        demean=batch_params['demean']
    )

    # Convert reference covariances to correlations
    ref_correlations = {}
    test_dates = list(returns_df.index[min_periods:])  # Skip insufficient data

    for date in test_dates:
        cov_matrix = ref_cov_matrices[date]

        # Convert to correlation
        std_vec = np.sqrt(np.diag(cov_matrix))
        std_mat = np.outer(std_vec, std_vec)
        corr_matrix = np.where(std_mat > 0, cov_matrix / std_mat, 0.0)
        np.fill_diagonal(corr_matrix, 1.0)
        ref_correlations[date] = corr_matrix

    # Incremental: Build correlation matrices
    engine = IncrementalConstraintsEngine(
        instruments=instruments,
        span=span,
        min_periods=min_periods,
        idm_cap=2.5,
        gross_leverage_cap=2.0,
        **batch_params
    )

    incremental_correlations = {}
    for date in returns_df.index:
        returns_today = returns_df.loc[date].to_dict()

        # Step engine (call private methods for testing)
        engine._update_ewma_cov(returns_today)
        corr_matrix = engine._get_correlation_matrix()
        incremental_correlations[date] = corr_matrix.values  # Convert to numpy for comparison

    # Assert correlation matrices match (only for dates with sufficient data)
    for date in ref_correlations.keys():
        np.testing.assert_allclose(
            incremental_correlations[date],
            ref_correlations[date],
            atol=1e-14,  # Should be near machine precision since same recursion
            rtol=0,
            err_msg=f"Correlation mismatch at {date}"
        )


def test_incremental_with_longer_period():
    """
    Test incremental engine on longer period to verify stability

    Uses:
    - 250 trading days (1 year)
    - 10 instruments
    - Validates that incremental matches batch over full year
    """
    np.random.seed(123)
    dates = pd.date_range('2023-01-01', periods=250, freq='D')
    instruments = [f'INST{i}' for i in range(10)]

    # Synthetic prices (random walk)
    prices_data = {}
    for inst in instruments:
        base_price = 100.0
        returns = np.random.randn(len(dates)) * 0.015  # 1.5% daily vol
        prices = base_price * np.exp(np.cumsum(returns))
        prices_data[inst] = prices

    prices_df = pd.DataFrame(prices_data, index=dates)

    # Synthetic weights
    weights_data = {}
    for inst in instruments:
        weights_data[inst] = np.random.uniform(-0.3, 0.3, len(dates))

    weights_df = pd.DataFrame(weights_data, index=dates)

    # Batch
    batch_constrained, batch_gross_lev, batch_idm = apply_portfolio_constraints(
        weights_df=weights_df,
        prices_df=prices_df,
        gross_leverage_cap=2.0,
        idm_cap=2.5,
        corr_span=60,
        corr_min_periods=20
    )

    # Incremental - match batch parameters
    batch_params = get_constraints_config()
    batch_params.pop("use_recursive")

    engine = IncrementalConstraintsEngine(
        instruments=instruments,
        span=60,
        min_periods=20,
        idm_cap=2.5,
        gross_leverage_cap=2.0,
        **batch_params
    )

    incremental_constrained = pd.DataFrame(index=dates, columns=instruments, dtype=float)
    incremental_gross_lev = pd.Series(index=dates, dtype=float)
    incremental_idm = pd.Series(index=dates, dtype=float)

    # Compute returns using shared helper
    returns_df = compute_returns(prices_df, method=batch_params["returns"])

    for date in dates:
        returns = returns_df.loc[date].to_dict()
        weights = weights_df.loc[date].to_dict()
        constrained, gross_lev, idm, _ = engine.step(date, returns, weights)
        incremental_constrained.loc[date] = pd.Series(constrained)
        incremental_gross_lev.loc[date] = gross_lev
        incremental_idm.loc[date] = idm

    # Assert match
    pd.testing.assert_frame_equal(
        incremental_constrained,
        batch_constrained,
        atol=1e-10,
        rtol=0
    )

    pd.testing.assert_series_equal(
        incremental_gross_lev,
        batch_gross_lev,
        atol=1e-10,
        rtol=0
    )

    pd.testing.assert_series_equal(
        incremental_idm,
        batch_idm,
        atol=1e-10,
        rtol=0
    )

    print(f"\nLonger period test passed:")
    print(f"  Days tested: {len(dates)}")
    print(f"  Instruments: {len(instruments)}")
    print(f"  Max weight difference: {(incremental_constrained - batch_constrained).abs().max().max():.2e}")
