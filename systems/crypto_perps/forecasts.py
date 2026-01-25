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
    min_periods: int = 500,
    backfill: bool = True
) -> pd.Series:
    """
    Scale a single forecast to have mean absolute value ≈ target_abs

    Args:
        raw_forecast: Raw forecast series for one instrument/rule
        target_abs: Target mean absolute value (default 10.0)
        window: Rolling window for calculating mean (default: use all data)
        min_periods: Minimum periods before estimating scalar (default 500)
        backfill: Backfill first estimate (default True)

    Returns:
        Scaled forecast series

    Notes:
        - Uses PST's forecast_scalar() which expects DataFrame input
        - Returns scaled series (not capped - capping done separately)
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


def combine_forecasts(
    forecasts: Dict[str, pd.Series],
    weights: Dict[str, float] = None
) -> pd.Series:
    """
    Combine multiple forecasts with optional weights

    Args:
        forecasts: Dict mapping rule_name -> forecast series
        weights: Dict mapping rule_name -> weight (default: equal weights)

    Returns:
        Combined forecast series

    Notes:
        - If weights not provided, uses equal weights
        - Weights are normalized to sum to 1.0
        - Uses inner join on dates (only dates with all forecasts)
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

    return combined


def scale_and_combine_forecasts(
    raw_forecasts: Dict[str, pd.Series],
    weights: Dict[str, float] = None,
    target_abs: float = TARGET_ABS_FORECAST,
    cap: float = FORECAST_CAP
) -> pd.Series:
    """
    Scale, cap, and combine multiple forecasts

    Process:
    1. Scale each forecast to mean abs ≈ target_abs
    2. Cap each forecast at ±cap
    3. Combine with weights
    4. Cap combined forecast at ±cap

    Args:
        raw_forecasts: Dict mapping rule_name -> raw forecast series
        weights: Dict mapping rule_name -> weight (default: equal weights)
        target_abs: Target mean absolute value (default 10.0)
        cap: Maximum absolute value (default 20.0)

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

    # Combine
    combined = combine_forecasts(scaled_capped_forecasts, weights=weights)

    # Cap the combined forecast
    capped_combined = apply_forecast_cap(combined, cap=cap)

    return capped_combined


def process_all_forecasts(
    ewmac_forecasts: Dict[str, Dict[str, pd.Series]],
    carry_forecasts: Dict[str, pd.Series],
    rule_weights: Dict[str, float] = None
) -> Dict[str, pd.Series]:
    """
    Process all forecasts for all instruments

    Combines EWMAC and carry forecasts for each instrument, with scaling and capping.

    Args:
        ewmac_forecasts: Dict[instrument][rule_name] -> forecast series
        carry_forecasts: Dict[instrument] -> forecast series
        rule_weights: Dict mapping rule_name -> weight (default: equal weights)

    Returns:
        Dict mapping instrument -> combined forecast series

    Example:
        >>> ewmac = {
        ...     'BTC': {'ewmac_8_32': series1, 'ewmac_16_64': series2},
        ...     'ETH': {'ewmac_8_32': series3, 'ewmac_16_64': series4}
        ... }
        >>> carry = {'BTC': series5, 'ETH': series6}
        >>> combined = process_all_forecasts(ewmac, carry)
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

        # Scale, cap, and combine
        combined = scale_and_combine_forecasts(
            raw_forecasts,
            weights=rule_weights
        )

        combined_forecasts[instrument] = combined

    return combined_forecasts
