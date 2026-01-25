"""
Funding carry rule for crypto perpetual futures

Generates carry signals based on exponentially weighted moving averages of funding rates.
The signal is the difference between slow and fast EWMA: slow - fast.

A positive signal (slow > fast) indicates funding has been trending higher,
suggesting a short position is favorable (you receive funding).
A negative signal indicates funding trending lower, favoring long positions.

Note: This is a per-instrument time-series signal. With only 5 instruments,
cross-sectional normalization is not meaningful. Scaling to mean abs ≈ 10
will be done later by forecast_scalar().
"""

import pandas as pd
from typing import Dict


def funding_carry_forecast(
    funding_rates: pd.Series,
    fast_halflife: int,
    slow_halflife: int
) -> pd.Series:
    """
    Calculate funding carry forecast for a single instrument

    Args:
        funding_rates: Time series of funding rates for one instrument
        fast_halflife: Half-life for fast EWMA in days
        slow_halflife: Half-life for slow EWMA in days

    Returns:
        Raw carry signal (unscaled, uncapped)
        Positive signal: slow > fast (funding trending up, favor short)
        Negative signal: slow < fast (funding trending down, favor long)

    Notes:
        - Uses pandas EWMA with halflife parameter
        - Signal = slow_ewma - fast_ewma
        - No cross-sectional normalization (per-instrument only)
        - Scaling will be applied later by forecast_scalar()

    Example:
        >>> import pandas as pd
        >>> rates = pd.Series([0.0001, 0.0002, 0.0003],
        ...                   index=pd.date_range('2023-01-01', periods=3))
        >>> signal = funding_carry_forecast(rates, fast_halflife=3, slow_halflife=30)
        >>> isinstance(signal, pd.Series)
        True
    """
    # Calculate exponentially weighted moving averages
    # halflife parameter: value decays to 50% after this many periods
    fast_ewma = funding_rates.ewm(halflife=fast_halflife, min_periods=1).mean()
    slow_ewma = funding_rates.ewm(halflife=slow_halflife, min_periods=1).mean()

    # Net carry signal: slow - fast
    # Positive when funding trending up (slow > fast)
    # Negative when funding trending down (slow < fast)
    carry_signal = slow_ewma - fast_ewma

    return carry_signal


def funding_carry_forecasts(
    meta_df: pd.DataFrame,
    fast_halflife: int,
    slow_halflife: int
) -> Dict[str, pd.Series]:
    """
    Calculate funding carry forecasts for all instruments

    Args:
        meta_df: DataFrame with MultiIndex (date, instrument) containing funding_rate column
        fast_halflife: Half-life for fast EWMA in days
        slow_halflife: Half-life for slow EWMA in days

    Returns:
        Dict mapping instrument -> carry forecast series
        Example: {
            'BTCUSDT_PERP': <Series>,
            'ETHUSDT_PERP': <Series>,
            ...
        }

    Notes:
        - Extracts funding_rate column from meta_df
        - Calculates carry signal for each instrument independently
        - Returns raw signals (no scaling or capping)
    """
    results = {}

    # Get unique instruments from MultiIndex
    instruments = meta_df.index.get_level_values('instrument').unique()

    for instrument in instruments:
        # Extract funding rates for this instrument
        funding_rates = meta_df.loc[(slice(None), instrument), 'funding_rate']

        # Reset index to have just dates (remove instrument level)
        funding_rates = funding_rates.droplevel('instrument')

        # Calculate carry forecast
        carry_forecast = funding_carry_forecast(
            funding_rates=funding_rates,
            fast_halflife=fast_halflife,
            slow_halflife=slow_halflife
        )

        results[instrument] = carry_forecast

    return results
