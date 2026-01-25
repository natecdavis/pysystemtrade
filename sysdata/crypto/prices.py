"""
Data adapter for crypto perpetual futures
Loads price data and metadata (funding rates, ADV, costs) from parquet files
"""

import pandas as pd
import numpy as np
from typing import Tuple


def load_crypto_perps_panel(path: str) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Load crypto perpetual futures data from parquet file

    Args:
        path: Path to parquet file containing crypto perps data

    Returns:
        Tuple of (prices_df, meta_df):
        - prices_df: DataFrame with date index and instrument columns (close prices)
        - meta_df: DataFrame with MultiIndex (date, instrument) containing:
            - funding_rate: funding rate for position held from close(t-1) to close(t)
            - adv_notional: average daily volume in notional terms
            - spread_frac: bid-ask spread as fraction of price
            - taker_fee_frac: taker fee as fraction of notional

    Raises:
        ValueError: If data fails validation checks
    """
    # Read parquet file
    df = pd.read_parquet(path)

    # Validate required columns
    required_cols = ['date', 'instrument', 'close', 'funding_rate',
                     'adv_notional', 'spread_frac', 'taker_fee_frac']
    missing_cols = set(required_cols) - set(df.columns)
    if missing_cols:
        raise ValueError(f"Missing required columns: {missing_cols}")

    # Ensure date column is datetime
    df['date'] = pd.to_datetime(df['date'])

    # Validate date index is monotonic and unique per instrument
    for instrument in df['instrument'].unique():
        inst_df = df[df['instrument'] == instrument].copy()
        if not inst_df['date'].is_monotonic_increasing:
            raise ValueError(f"Date index not monotonic for {instrument}")
        if inst_df['date'].duplicated().any():
            raise ValueError(f"Duplicate dates found for {instrument}")

    # Create prices DataFrame (wide format: dates x instruments)
    prices_df = df.pivot(index='date', columns='instrument', values='close')
    prices_df.index.name = 'date'

    # Create metadata DataFrame (long format with MultiIndex)
    meta_cols = ['funding_rate', 'adv_notional', 'spread_frac', 'taker_fee_frac']
    meta_df = df.set_index(['date', 'instrument'])[meta_cols]

    # Validate no NaN in close prices (metadata can have NaN which triggers ineligibility)
    if prices_df.isna().any().any():
        nan_summary = prices_df.isna().sum()
        nan_instruments = nan_summary[nan_summary > 0]
        raise ValueError(f"NaN values in close prices:\n{nan_instruments}")

    # Validate funding rate alignment
    # funding_rate[t] should apply to position held from close(t-1) to close(t)
    # This is validated by checking that funding_rate[t] exists for each date with close[t]
    for instrument in prices_df.columns:
        price_dates = set(prices_df.index)
        funding_dates = set(meta_df.loc[(slice(None), instrument), 'funding_rate'].index.get_level_values(0))
        if price_dates != funding_dates:
            missing = price_dates - funding_dates
            extra = funding_dates - price_dates
            raise ValueError(
                f"Funding rate dates mismatch for {instrument}. "
                f"Missing: {missing}, Extra: {extra}"
            )

    return prices_df, meta_df
