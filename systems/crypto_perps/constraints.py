"""
Portfolio constraints for crypto perpetual futures

Implements:
1. Gross leverage cap
2. IDM (Instrument Diversification Multiplier) cap

Phase 1: Minimal implementation using simple EWMA correlations
"""

import pandas as pd
import numpy as np
from typing import Dict, Tuple


def calculate_ewma_correlation(
    returns_df: pd.DataFrame,
    span: int = 60,
    min_periods: int = 20
) -> pd.DataFrame:
    """
    Calculate exponentially weighted moving average correlation matrix

    Args:
        returns_df: DataFrame of returns with instruments as columns
        span: EWMA span in days (default 60, shorter for crypto volatility)
        min_periods: Minimum periods required (default 20)

    Returns:
        Correlation matrix (DataFrame)

    Notes:
        - Phase 1: Simple EWMA correlation
        - Carver-style shorter half-life for crypto (60 days vs 125 for traditional)
        - Returns latest correlation matrix estimate
    """
    # Calculate EWMA covariance
    ewma_cov = returns_df.ewm(span=span, min_periods=min_periods).cov()

    # Extract the most recent covariance matrix
    latest_date = returns_df.index[-1]
    cov_matrix = ewma_cov.loc[latest_date]

    # Convert covariance to correlation
    # corr[i,j] = cov[i,j] / (std[i] * std[j])
    std_vec = np.sqrt(np.diag(cov_matrix))
    std_mat = np.outer(std_vec, std_vec)

    # Avoid division by zero
    corr_matrix = np.where(std_mat > 0, cov_matrix / std_mat, 0.0)

    # Ensure diagonal is 1.0
    np.fill_diagonal(corr_matrix, 1.0)

    # Convert to DataFrame
    corr_df = pd.DataFrame(
        corr_matrix,
        index=cov_matrix.index,
        columns=cov_matrix.columns
    )

    return corr_df


def calculate_portfolio_stdev(
    weights: Dict[str, float],
    corr_matrix: pd.DataFrame
) -> float:
    """
    Calculate portfolio standard deviation from weights and correlation matrix

    Args:
        weights: Dict mapping instrument -> weight (fraction of capital)
        corr_matrix: Correlation matrix (DataFrame)

    Returns:
        Portfolio standard deviation (assuming equal vol per instrument)

    Formula:
        σ_portfolio = sqrt(W' * Corr * W)

    Notes:
        - Assumes equal volatility across instruments (simplified for Phase 1)
        - More accurate would use actual volatilities, but correlation structure
          dominates for diversification calculation
    """
    # Align weights to correlation matrix columns
    instruments = list(corr_matrix.columns)
    weight_vec = np.array([weights.get(inst, 0.0) for inst in instruments])

    # Portfolio variance: W' * Corr * W
    portfolio_var = weight_vec @ corr_matrix.values @ weight_vec

    # Standard deviation
    portfolio_stdev = np.sqrt(max(portfolio_var, 0.0))

    return portfolio_stdev


def calculate_idm(
    weights: Dict[str, float],
    corr_matrix: pd.DataFrame
) -> float:
    """
    Calculate Instrument Diversification Multiplier (IDM)

    IDM = 1 / portfolio_stdev

    Args:
        weights: Dict mapping instrument -> weight
        corr_matrix: Correlation matrix

    Returns:
        IDM value

    Notes:
        - Higher IDM = better diversification
        - IDM = 1.0 means no diversification (fully correlated or single instrument)
        - IDM = sqrt(N) means perfect diversification (zero correlation)
        - Typical range: 1.0 to 2.5
    """
    portfolio_stdev = calculate_portfolio_stdev(weights, corr_matrix)

    if portfolio_stdev < 1e-10:
        return 1.0

    idm = 1.0 / portfolio_stdev

    return idm


def apply_gross_leverage_cap(
    weights: Dict[str, float],
    cap: float
) -> Dict[str, float]:
    """
    Apply gross leverage cap to weights

    Gross leverage = sum(|weights|)

    If gross leverage > cap, scale all weights proportionally to meet cap.

    Args:
        weights: Dict mapping instrument -> weight
        cap: Maximum gross leverage (e.g., 2.0)

    Returns:
        Dict mapping instrument -> adjusted weight

    Example:
        - weights: {BTC: 1.2, ETH: 0.8, BNB: -0.6}
        - gross leverage = |1.2| + |0.8| + |-0.6| = 2.6
        - cap = 2.0
        - scaling factor = 2.0 / 2.6 = 0.769
        - adjusted weights: {BTC: 0.923, ETH: 0.615, BNB: -0.462}
    """
    # Calculate gross leverage
    gross_leverage = sum(abs(w) for w in weights.values())

    if gross_leverage <= cap:
        # No adjustment needed
        return weights.copy()

    # Scale weights proportionally
    scaling_factor = cap / gross_leverage
    adjusted_weights = {
        inst: w * scaling_factor
        for inst, w in weights.items()
    }

    return adjusted_weights


def apply_idm_cap(
    weights: Dict[str, float],
    corr_matrix: pd.DataFrame,
    cap: float
) -> Dict[str, float]:
    """
    Apply IDM cap to weights

    If IDM > cap, scale all weights proportionally to meet cap.

    Args:
        weights: Dict mapping instrument -> weight
        corr_matrix: Correlation matrix
        cap: Maximum IDM (e.g., 2.5)

    Returns:
        Dict mapping instrument -> adjusted weight

    Notes:
        - IDM is inversely related to position size
        - Higher weights → lower portfolio stdev → higher IDM
        - To reduce IDM, we scale down weights proportionally
    """
    # Calculate current IDM
    current_idm = calculate_idm(weights, corr_matrix)

    if current_idm <= cap:
        # No adjustment needed
        return weights.copy()

    # Scale weights to achieve target IDM
    # IDM = 1 / portfolio_stdev
    # If we scale weights by α, portfolio_stdev scales by α
    # So new_idm = 1 / (α * old_portfolio_stdev) = (1/α) * old_idm
    # We want new_idm = cap, so cap = (1/α) * old_idm, thus α = old_idm / cap
    scaling_factor = current_idm / cap

    adjusted_weights = {
        inst: w * scaling_factor
        for inst, w in weights.items()
    }

    return adjusted_weights


def apply_portfolio_constraints(
    weights_df: pd.DataFrame,
    prices_df: pd.DataFrame,
    gross_leverage_cap: float,
    idm_cap: float,
    corr_span: int = 60,
    corr_min_periods: int = 20
) -> Tuple[pd.DataFrame, pd.Series, pd.Series]:
    """
    Apply portfolio constraints (gross leverage and IDM caps) to weights

    Args:
        weights_df: DataFrame with date index and instrument columns (weights)
        prices_df: DataFrame with date index and instrument columns (prices)
        gross_leverage_cap: Maximum gross leverage (e.g., 2.0)
        idm_cap: Maximum IDM (e.g., 2.5)
        corr_span: EWMA span for correlation (default 60 days)
        corr_min_periods: Minimum periods for correlation (default 20)

    Returns:
        Tuple of (constrained_weights_df, gross_leverage_series, idm_series):
        - constrained_weights_df: Adjusted weights after constraints
        - gross_leverage_series: Gross leverage over time (for diagnostics)
        - idm_series: IDM over time (for diagnostics)

    Notes:
        - Applies constraints in order: gross leverage first, then IDM
        - Calculates returns and correlations from prices
        - Returns diagnostic series for validation
    """
    # Calculate returns for correlation
    returns_df = prices_df.pct_change()

    # Initialize outputs
    constrained_weights_data = {inst: [] for inst in weights_df.columns}
    gross_leverage_list = []
    idm_list = []

    for date in weights_df.index:
        # Get weights for this date
        weights_dict = weights_df.loc[date].to_dict()

        # Get returns up to this date for correlation calculation
        returns_history = returns_df.loc[:date]

        # Calculate EWMA correlation (if enough data)
        if len(returns_history) >= corr_min_periods:
            corr_matrix = calculate_ewma_correlation(
                returns_history,
                span=corr_span,
                min_periods=corr_min_periods
            )
        else:
            # Not enough data - use identity matrix (no correlation assumed)
            instruments = list(weights_df.columns)
            corr_matrix = pd.DataFrame(
                np.eye(len(instruments)),
                index=instruments,
                columns=instruments
            )

        # Apply constraints sequentially: IDM first, then gross leverage
        # This ensures gross leverage is never violated (absolute priority)

        # Step 1: Apply IDM cap (may scale weights up if IDM too high)
        weights_after_idm = apply_idm_cap(weights_dict, corr_matrix, idm_cap)

        # Step 2: Apply gross leverage cap (absolute priority)
        # This may cause IDM to exceed its cap, which we accept as necessary trade-off
        final_weights = apply_gross_leverage_cap(weights_after_idm, gross_leverage_cap)

        # Store constrained weights
        for inst in weights_df.columns:
            constrained_weights_data[inst].append(final_weights.get(inst, 0.0))

        # Calculate diagnostics
        gross_lev = sum(abs(w) for w in final_weights.values())
        idm = calculate_idm(final_weights, corr_matrix)

        gross_leverage_list.append(gross_lev)
        idm_list.append(idm)

    # Create DataFrames
    constrained_weights_df = pd.DataFrame(constrained_weights_data, index=weights_df.index)
    gross_leverage_series = pd.Series(gross_leverage_list, index=weights_df.index)
    idm_series = pd.Series(idm_list, index=weights_df.index)

    return constrained_weights_df, gross_leverage_series, idm_series
