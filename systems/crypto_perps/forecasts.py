"""
Forecast scaling and combination for crypto perpetual futures

Handles:
1. Scaling raw forecasts to mean abs ≈ 10
2. Capping individual forecasts at ±20
3. Combining multiple forecasts (equal-weighted for Phase 1)
4. Capping combined forecasts at ±20
"""

import pandas as pd
import numpy as np
from typing import Dict, List
from sysquant.estimators.forecast_scalar import forecast_scalar


FORECAST_CAP = 20.0
TARGET_ABS_FORECAST = 10.0


def apply_forecast_cap(forecast: pd.Series, cap: float = FORECAST_CAP) -> pd.Series:
    """
    Cap forecast at ±cap

    Args:
        forecast: Forecast series
        cap: Maximum absolute value (default 20.0)

    Returns:
        Capped forecast series
    """
    return forecast.clip(-cap, cap)


def scale_forecast(
    raw_forecast: pd.Series,
    target_abs: float = TARGET_ABS_FORECAST,
    window: int = 250000,
    min_periods: int = 50,
    backfill: bool = True
) -> pd.Series:
    """
    Scale a single forecast to have mean absolute value ≈ target_abs

    Args:
        raw_forecast: Raw forecast series for one instrument/rule
        target_abs: Target mean absolute value (default 10.0)
        window: Rolling window for calculating mean (default: use all data)
        min_periods: Minimum periods before estimating scalar (default 50)
        backfill: Backfill first estimate (default True)

    Returns:
        Scaled forecast series

    Notes:
        - Uses PST's forecast_scalar() which expects DataFrame input
        - Returns scaled series (not capped - capping done separately)
        - min_periods=50 provides stable warmup for daily data
    """
    # forecast_scalar expects DataFrame (T x N)
    # Convert Series to single-column DataFrame
    raw_df = raw_forecast.to_frame(name='forecast')

    # Calculate scaling factor
    scaling_factor = forecast_scalar(
        cs_forecasts=raw_df,
        target_abs_forecast=target_abs,
        window=window,
        min_periods=min_periods,
        backfill=backfill
    )

    # Apply scaling
    scaled_forecast = raw_forecast * scaling_factor

    return scaled_forecast


def scale_and_cap_forecast(
    raw_forecast: pd.Series,
    target_abs: float = TARGET_ABS_FORECAST,
    cap: float = FORECAST_CAP
) -> pd.Series:
    """
    Scale forecast to target mean abs and cap at ±cap

    Args:
        raw_forecast: Raw forecast series
        target_abs: Target mean absolute value (default 10.0)
        cap: Maximum absolute value (default 20.0)

    Returns:
        Scaled and capped forecast series
    """
    scaled = scale_forecast(raw_forecast, target_abs=target_abs)
    capped = apply_forecast_cap(scaled, cap=cap)
    return capped


def calculate_fdm(
    forecasts: Dict[str, pd.Series],
    weights: Dict[str, float],
    span: int = 125,
    min_periods: int = 30,
    fdm_cap: float = 2.5
) -> float:
    """
    Calculate Forecast Diversification Multiplier (FDM)

    FDM = 1 / portfolio_stdev, where portfolio_stdev is calculated from
    forecast correlations and weights.

    Args:
        forecasts: Dict mapping rule_name -> forecast series
        weights: Dict mapping rule_name -> weight (normalized to sum to 1)
        span: EWMA span for correlation calculation (default 125)
        min_periods: Minimum periods for correlation (default 30)
        fdm_cap: Maximum FDM value (default 2.5)

    Returns:
        FDM value (float)

    Notes:
        - Returns 1.0 if insufficient data for correlation
        - FDM boosts combined forecasts to account for diversification
        - Similar to IDM but applied at the forecast/rule level
    """
    if len(forecasts) <= 1:
        return 1.0

    # Align all forecasts to same dates
    forecast_df = pd.DataFrame(forecasts)

    # Need at least min_periods of data
    if len(forecast_df.dropna()) < min_periods:
        return 1.0

    # Calculate EWMA correlation matrix
    corr_matrix = forecast_df.ewm(span=span, min_periods=min_periods).corr()

    # Extract latest correlation matrix
    if len(corr_matrix) == 0:
        return 1.0

    last_date = corr_matrix.index.get_level_values(0)[-1]
    corr_latest = corr_matrix.loc[last_date]

    # Handle NaN in correlation matrix
    if corr_latest.isna().any().any():
        return 1.0

    # Convert weights dict to array aligned with correlation matrix
    rule_names = list(corr_latest.columns)
    weight_array = np.array([weights.get(name, 0.0) for name in rule_names])

    # Calculate portfolio variance: W' * Corr * W
    portfolio_var = weight_array @ corr_latest.values @ weight_array
    portfolio_stdev = np.sqrt(max(portfolio_var, 0.0))

    if portfolio_stdev < 1e-10:
        return 1.0

    # FDM = 1 / stdev, capped at fdm_cap
    fdm = min(1.0 / portfolio_stdev, fdm_cap)

    return fdm


def combine_forecasts(
    forecasts: Dict[str, pd.Series],
    weights: Dict[str, float] = None,
    apply_fdm: bool = True,
    fdm_cap: float = 2.5
) -> pd.Series:
    """
    Combine multiple forecasts with optional weights and FDM boost

    Args:
        forecasts: Dict mapping rule_name -> forecast series
        weights: Dict mapping rule_name -> weight (default: equal weights)
        apply_fdm: If True, apply Forecast Diversification Multiplier (default: True)
        fdm_cap: Maximum FDM value (default: 2.5)

    Returns:
        Combined forecast series

    Notes:
        - If weights not provided, uses equal weights
        - Weights are normalized to sum to 1.0
        - Uses inner join on dates (only dates with all forecasts)
        - FDM boosts combined forecast to account for diversification benefit
    """
    if not forecasts:
        raise ValueError("No forecasts provided")

    # Default to equal weights if not provided
    if weights is None:
        weights = {name: 1.0 for name in forecasts.keys()}

    # Normalize weights to sum to 1.0
    total_weight = sum(weights.values())
    norm_weights = {name: w / total_weight for name, w in weights.items()}

    # Combine forecasts
    # Align all forecasts to same dates (inner join)
    forecast_df = pd.DataFrame(forecasts)

    # Apply weights and sum
    combined = sum(
        forecast_df[name] * norm_weights[name]
        for name in forecasts.keys()
    )

    # Apply FDM boost if requested
    if apply_fdm and len(forecasts) > 1:
        fdm = calculate_fdm(forecasts, norm_weights, fdm_cap=fdm_cap)
        combined = combined * fdm

    return combined


def scale_and_combine_forecasts(
    raw_forecasts: Dict[str, pd.Series],
    weights: Dict[str, float] = None,
    target_abs: float = TARGET_ABS_FORECAST,
    cap: float = FORECAST_CAP,
    apply_fdm: bool = True,
    fdm_cap: float = 2.5
) -> pd.Series:
    """
    Scale, cap, and combine multiple forecasts with FDM boost

    Process:
    1. Scale each forecast to mean abs ≈ target_abs
    2. Cap each forecast at ±cap
    3. Combine with weights
    4. Apply FDM (Forecast Diversification Multiplier) boost
    5. Cap combined forecast at ±cap

    Args:
        raw_forecasts: Dict mapping rule_name -> raw forecast series
        weights: Dict mapping rule_name -> weight (default: equal weights)
        target_abs: Target mean absolute value (default 10.0)
        cap: Maximum absolute value (default 20.0)
        apply_fdm: If True, apply FDM boost (default: True)
        fdm_cap: Maximum FDM value (default: 2.5)

    Returns:
        Combined and capped forecast series

    Example:
        >>> import pandas as pd
        >>> dates = pd.date_range('2023-01-01', periods=100)
        >>> raw_forecasts = {
        ...     'rule1': pd.Series(range(100), index=dates),
        ...     'rule2': pd.Series(range(-50, 50), index=dates)
        ... }
        >>> combined = scale_and_combine_forecasts(raw_forecasts)
        >>> isinstance(combined, pd.Series)
        True
    """
    # Scale and cap each forecast
    scaled_capped_forecasts = {}
    for name, raw_forecast in raw_forecasts.items():
        scaled_capped = scale_and_cap_forecast(
            raw_forecast,
            target_abs=target_abs,
            cap=cap
        )
        scaled_capped_forecasts[name] = scaled_capped

    # Combine with FDM boost
    combined = combine_forecasts(
        scaled_capped_forecasts,
        weights=weights,
        apply_fdm=apply_fdm,
        fdm_cap=fdm_cap
    )

    # Cap the combined forecast
    capped_combined = apply_forecast_cap(combined, cap=cap)

    return capped_combined


def process_all_forecasts(
    ewmac_forecasts: Dict[str, Dict[str, pd.Series]],
    carry_forecasts: Dict[str, pd.Series],
    relmom_forecasts: Dict[str, pd.Series] = None,  # NEW: Phase 2
    rule_weights: Dict[str, float] = None
) -> Dict[str, pd.Series]:
    """
    Process all forecasts for all instruments

    Combines EWMAC, carry, and optionally relative momentum forecasts for each instrument,
    with scaling and capping.

    Phase 2: Adds relative momentum to the rule set

    Args:
        ewmac_forecasts: Dict[instrument][rule_name] -> forecast series
        carry_forecasts: Dict[instrument] -> forecast series
        relmom_forecasts: Dict[instrument] -> forecast series (optional, Phase 2)
        rule_weights: Dict mapping rule_name -> weight (default: equal weights)

    Returns:
        Dict mapping instrument -> combined forecast series

    Notes:
        - relmom_forecasts may contain NaN for instruments outside Layer A on each date
        - NaNs are handled by forecast combination (ignored or treated as 0)
        - Scaling/capping applied consistently across all rules

    Example:
        >>> ewmac = {
        ...     'BTC': {'ewmac_8_32': series1, 'ewmac_16_64': series2},
        ...     'ETH': {'ewmac_8_32': series3, 'ewmac_16_64': series4}
        ... }
        >>> carry = {'BTC': series5, 'ETH': series6}
        >>> relmom = {'BTC': series7, 'ETH': series8}  # Phase 2
        >>> combined = process_all_forecasts(ewmac, carry, relmom)
        >>> 'BTC' in combined and 'ETH' in combined
        True
    """
    combined_forecasts = {}

    for instrument in ewmac_forecasts.keys():
        # Collect all raw forecasts for this instrument
        raw_forecasts = {}

        # Add EWMAC forecasts
        for rule_name, forecast in ewmac_forecasts[instrument].items():
            raw_forecasts[rule_name] = forecast

        # Add carry forecast
        if instrument in carry_forecasts:
            raw_forecasts['carry_funding'] = carry_forecasts[instrument]

        # Phase 2: Add relative momentum forecast
        if relmom_forecasts is not None and instrument in relmom_forecasts:
            raw_forecasts['relative_momentum'] = relmom_forecasts[instrument]

        # Scale, cap, and combine
        combined = scale_and_combine_forecasts(
            raw_forecasts,
            weights=rule_weights
        )

        combined_forecasts[instrument] = combined

    return combined_forecasts
