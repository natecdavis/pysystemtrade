"""
EWMAC (Exponentially Weighted Moving Average Crossover) rule for crypto perpetual futures

This is a thin wrapper around pysystemtrade's EWMAC implementation,
adapted for crypto perpetual futures data.
"""

import pandas as pd
from typing import Dict, List, Tuple
from systems.provided.rules.ewmac import ewmac_calc_vol


def ewmac_forecasts(
    prices_df: pd.DataFrame,
    ewmac_pairs: List[Tuple[int, int]],
    vol_days: int = 35
) -> Dict[str, Dict[str, pd.Series]]:
    """
    Calculate EWMAC forecasts for all instruments and all EWMA speed pairs

    Args:
        prices_df: DataFrame with date index and instrument columns (close prices)
        ewmac_pairs: List of (Lfast, Lslow) tuples, e.g., [(8, 32), (16, 64)]
        vol_days: Lookback period for volatility calculation (default 35 days)

    Returns:
        Dict mapping instrument -> rule_name -> forecast series
        Example: {
            'BTCUSDT_PERP': {
                'ewmac_8_32': <Series>,
                'ewmac_16_64': <Series>
            },
            ...
        }

    Notes:
        - Uses PST's ewmac_calc_vol which calculates volatility internally
        - Forecasts are raw (unscaled, uncapped) - scaling happens in forecast module
        - Volatility is calculated using robust_vol_calc with default parameters
    """
    results = {}

    for instrument in prices_df.columns:
        price_series = prices_df[instrument].dropna()

        instrument_forecasts = {}
        for Lfast, Lslow in ewmac_pairs:
            rule_name = f"ewmac_{Lfast}_{Lslow}"

            # Use PST's ewmac_calc_vol which handles vol calculation internally
            forecast = ewmac_calc_vol(
                price=price_series,
                Lfast=Lfast,
                Lslow=Lslow,
                vol_days=vol_days
            )

            instrument_forecasts[rule_name] = forecast

        results[instrument] = instrument_forecasts

    return results


def ewmac_forecast_single_instrument(
    price_series: pd.Series,
    Lfast: int,
    Lslow: int,
    vol_days: int = 35
) -> pd.Series:
    """
    Calculate EWMAC forecast for a single instrument and single speed pair

    Args:
        price_series: Price series for single instrument
        Lfast: Fast EWMA span in days
        Lslow: Slow EWMA span in days
        vol_days: Lookback period for volatility calculation (default 35 days)

    Returns:
        Raw forecast series (unscaled, uncapped)

    Example:
        >>> import pandas as pd
        >>> prices = pd.Series([100, 101, 102, 103], index=pd.date_range('2023-01-01', periods=4))
        >>> forecast = ewmac_forecast_single_instrument(prices, Lfast=2, Lslow=4)
        >>> isinstance(forecast, pd.Series)
        True
    """
    return ewmac_calc_vol(
        price=price_series,
        Lfast=Lfast,
        Lslow=Lslow,
        vol_days=vol_days
    )
