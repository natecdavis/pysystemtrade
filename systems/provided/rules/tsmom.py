import numpy as np


def tsmom(price, lookback=126, skip=5, vol_floor=0.01):
    """
    Time-Series Momentum (TSMOM) trading rule.

    Measures vol-normalized cumulative return over a lookback period,
    with a skip period to avoid short-term reversal.

    Based on Moskowitz et al. methodology, adapted for Carver's framework.

    :param price: Daily price series
    :type price: pd.Series

    :param lookback: Momentum lookback in days (default 126 = ~6 months)
    :type lookback: int

    :param skip: Days to skip for short-term noise avoidance (default 5)
    :type skip: int

    :param vol_floor: Minimum annualized vol to avoid extreme forecasts (default 0.01 = 1%)
    :type vol_floor: float

    :returns: pd.Series -- unscaled, uncapped forecast
    """
    # Use log prices for cumulative return calculation
    log_price = np.log(price)

    # Cumulative log return over lookback, skipping recent days
    cum_ret = log_price.shift(skip) - log_price.shift(lookback + skip)

    # Realized volatility of daily log returns
    daily_log_ret = log_price.diff()
    vol = daily_log_ret.rolling(lookback, min_periods=20).std().shift(skip)

    # Apply vol floor to avoid extreme forecasts in low-vol regimes
    # Convert daily vol floor to match rolling vol units
    daily_vol_floor = vol_floor / np.sqrt(252)
    vol_floored = vol.clip(lower=daily_vol_floor)

    # Vol-normalized momentum (raw forecast)
    # Let pysystemtrade's forecast scalar and ±20 cap handle normalization
    forecast = cum_ret / vol_floored

    return forecast
