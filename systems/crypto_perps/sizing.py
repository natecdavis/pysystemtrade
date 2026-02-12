"""
Position sizing for crypto perpetual futures

Implements volatility-targeted position sizing with minimum steady position rule.

Position Units:
- Weights (w_i): fraction of capital allocated to instrument i (e.g., 0.15 = 15%)
- Notional (N_i): dollar value of position = w_i * capital
- Contracts: not used in Phase 1 (crypto perps are notional-traded)

Minimum Steady Position Rule:
- Applied on weights, not notional
- Threshold: min_weight_threshold = min_position_frac / N_active
- If |w_i| < threshold, then w_i = 0
"""

import pandas as pd
import numpy as np
from typing import Dict, Tuple
from sysquant.estimators.vol import robust_vol_calc
from syscore.dateutils import BUSINESS_DAYS_IN_YEAR


def calculate_daily_volatility(
    prices: pd.Series,
    vol_days: int = 35
) -> pd.Series:
    """
    Calculate daily volatility for a price series

    Args:
        prices: Price series for one instrument
        vol_days: Lookback period for volatility (default 35 days)

    Returns:
        Daily volatility series (in price units, not percentage)

    Notes:
        - Uses PST's robust_vol_calc
        - Returns daily vol (not annualized)
    """
    returns = prices.diff()
    vol = robust_vol_calc(returns, days=vol_days)
    return vol


def calculate_target_weight(
    forecast: float,
    price: float,
    daily_vol: float,
    capital: float,
    vol_target_ann: float
) -> float:
    """
    Calculate volatility-targeted weight for a single instrument on a single date

    Formula (from Carver):
        w_i = (forecast / 10) * (vol_target / vol_ann) * (1 / N_instruments)

    Where:
        - forecast: combined forecast (scaled to mean abs = 10)
        - vol_target: target annualized volatility (e.g., 0.25 = 25%)
        - vol_ann: actual annualized volatility of instrument
        - N_instruments: number of instruments (diversification)

    For crypto perps (notional trading):
        notional_i = w_i * capital
        vol_ann = daily_vol * sqrt(BUSINESS_DAYS_IN_YEAR)

    Args:
        forecast: Combined forecast for instrument (scaled to mean abs = 10)
        price: Current price
        daily_vol: Daily volatility in price units
        capital: Total capital
        vol_target_ann: Target annualized volatility (e.g., 0.25)

    Returns:
        Target weight (fraction of capital)

    Notes:
        - Forecast is scaled to 10, so forecast/10 gives signal strength
        - Vol targeting ensures equal risk contribution
        - Result is weight, not notional
    """
    if pd.isna(forecast) or pd.isna(price) or pd.isna(daily_vol):
        return 0.0

    if daily_vol == 0 or price == 0:
        return 0.0

    # Annualize volatility
    vol_ann = daily_vol * np.sqrt(BUSINESS_DAYS_IN_YEAR)

    # Percentage volatility (volatility as fraction of price)
    vol_pct_ann = vol_ann / price

    if vol_pct_ann == 0:
        return 0.0

    # Forecast scalar (forecast is scaled to 10, so this is signal strength)
    forecast_scalar = forecast / 10.0

    # Vol targeting: allocate less to high-vol instruments
    # weight ∝ vol_target / vol_actual
    vol_scalar = vol_target_ann / vol_pct_ann

    # Position weight (fraction of capital)
    # Note: We don't divide by N_instruments here - that's handled by IDM
    weight = forecast_scalar * vol_scalar

    return weight


def apply_minimum_position_rule(
    weights: Dict[str, float],
    min_position_frac: float
) -> Dict[str, float]:
    """
    Apply minimum steady position rule to weights

    Args:
        weights: Dict mapping instrument -> weight
        min_position_frac: Minimum position fraction (e.g., 0.03 = 3%)

    Returns:
        Dict mapping instrument -> adjusted weight

    Logic:
        - Count active instruments (non-zero forecasts/weights)
        - Threshold = min_position_frac / N_active
        - If |w_i| < threshold, set w_i = 0
        - This prevents tiny positions that incur costs without meaningful exposure

    Example:
        - 5 instruments active, min_position_frac = 0.03
        - Threshold = 0.03 / 5 = 0.006 (0.6% of capital)
        - Any weight < 0.6% is set to 0
    """
    # Count active instruments (non-zero weights)
    n_active = sum(1 for w in weights.values() if w != 0.0)

    if n_active == 0:
        return weights

    # Calculate threshold
    threshold = min_position_frac / n_active

    # Apply threshold
    adjusted_weights = {}
    for instrument, weight in weights.items():
        if abs(weight) < threshold:
            adjusted_weights[instrument] = 0.0
        else:
            adjusted_weights[instrument] = weight

    return adjusted_weights


def calculate_target_weights(
    forecasts: Dict[str, pd.Series],
    prices_df: pd.DataFrame,
    capital: float,
    vol_target_ann: float,
    min_position_frac: float,
    vol_days: int = 35
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Calculate target weights for all instruments over time

    Args:
        forecasts: Dict mapping instrument -> combined forecast series
        prices_df: DataFrame with date index and instrument columns
        capital: Total capital
        vol_target_ann: Target annualized volatility (e.g., 0.25)
        min_position_frac: Minimum position fraction (e.g., 0.03)
        vol_days: Volatility lookback in days (default 35)

    Returns:
        Tuple of (weights_df, notionals_df):
        - weights_df: DataFrame with date index and instrument columns (weights)
        - notionals_df: DataFrame with date index and instrument columns (dollar notionals)

    Notes:
        - Calculates volatility for each instrument
        - Applies vol-targeting to each forecast
        - Applies minimum position rule on weights
        - Returns both weights and notionals for diagnostics
    """
    instruments = list(forecasts.keys())
    dates = prices_df.index

    # Calculate volatilities for all instruments
    volatilities = {}
    for instrument in instruments:
        volatilities[instrument] = calculate_daily_volatility(
            prices_df[instrument],
            vol_days=vol_days
        )

    # Calculate weights for each date
    weights_data = {inst: [] for inst in instruments}
    notionals_data = {inst: [] for inst in instruments}

    for date in dates:
        # Get forecasts, prices, and vols for this date
        date_weights = {}
        for instrument in instruments:
            if date in forecasts[instrument].index:
                forecast = forecasts[instrument].loc[date]
                price = prices_df.loc[date, instrument]
                daily_vol = volatilities[instrument].loc[date] if date in volatilities[instrument].index else np.nan

                # Calculate target weight
                weight = calculate_target_weight(
                    forecast=forecast,
                    price=price,
                    daily_vol=daily_vol,
                    capital=capital,
                    vol_target_ann=vol_target_ann
                )
                date_weights[instrument] = weight
            else:
                date_weights[instrument] = 0.0

        # Apply minimum position rule
        adjusted_weights = apply_minimum_position_rule(
            date_weights,
            min_position_frac=min_position_frac
        )

        # Store weights and calculate notionals
        for instrument in instruments:
            weight = adjusted_weights[instrument]
            notional = weight * capital

            weights_data[instrument].append(weight)
            notionals_data[instrument].append(notional)

    # Create DataFrames
    weights_df = pd.DataFrame(weights_data, index=dates)
    notionals_df = pd.DataFrame(notionals_data, index=dates)

    return weights_df, notionals_df
