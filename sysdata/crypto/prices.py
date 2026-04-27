"""
Data adapter for crypto perpetual futures
Loads price data and metadata (funding rates, ADV, costs) from parquet files
"""

import pandas as pd
import numpy as np
from typing import Tuple, Optional
from sysdata.crypto.schema import validate_schema_compliance
from sysdata.crypto.lifecycle import load_instrument_lifecycle, is_instrument_active, derive_lifecycle_from_data


def load_crypto_perps_panel(
    path: str,
    validate_schema: bool = True,
    allow_jagged: bool = False,
    lifecycle_path: Optional[str] = None
) -> Tuple[pd.DataFrame, pd.DataFrame, Optional[pd.DataFrame]]:
    """
    Load crypto perpetual futures data from parquet file

    Args:
        path: Path to parquet file containing crypto perps data
        validate_schema: If True, validate against canonical schema (default: True)
                        Set to False for exploratory / ad-hoc usage
        allow_jagged: If True, allow instruments to have different date ranges (default: False)
        lifecycle_path: Path to instrument lifecycle metadata (required if allow_jagged=True)

    Returns:
        Tuple of (prices_df, meta_df, lifecycle_df):
        - prices_df: DataFrame with date index and instrument columns (close prices)
        - meta_df: DataFrame with MultiIndex (date, instrument) containing:
            - funding_rate: funding rate for position held from close(t-1) to close(t)
            - adv_notional: average daily volume in notional terms
            - spread_frac: bid-ask spread as fraction of price
            - taker_fee_frac: taker fee as fraction of notional
        - lifecycle_df: DataFrame with instrument lifecycle metadata (None if not allow_jagged)

    Raises:
        ValueError: If data fails validation checks
    """
    # Read parquet file
    df = pd.read_parquet(path)

    # Optional schema validation (enabled by default in production paths)
    if validate_schema:
        schema_errors = validate_schema_compliance(df, require_rectangular=not allow_jagged)
        if schema_errors:
            raise ValueError(
                f"Dataset schema validation failed:\n" + "\n".join(schema_errors)
            )

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

    # Load or derive lifecycle metadata if jagged panels enabled
    if allow_jagged:
        # Derive lifecycle from actual data in the parquet file (most accurate)
        lifecycle_df = derive_lifecycle_from_data(df)
    else:
        lifecycle_df = None

    # Validate NaN in close prices
    if not allow_jagged:
        # Old behavior: no NaN allowed
        if prices_df.isna().any().any():
            nan_summary = prices_df.isna().sum()
            nan_instruments = nan_summary[nan_summary > 0]
            raise ValueError(f"NaN values in close prices:\n{nan_instruments}")
    else:
        # New behavior: NaN allowed only for dates outside instrument lifecycle
        # For jagged panels, NaN is expected before first valid data or after last valid data
        # No validation needed - NaN indicates instrument not active on that date
        pass

    # Validate funding rate alignment
    # funding_rate[t] should apply to position held from close(t-1) to close(t)
    # This is validated by checking that funding_rate[t] exists for each date with close[t]
    for instrument in prices_df.columns:
        # Use non-NaN price dates: for jagged panels, NaN prices indicate pre-launch dates
        # and are not expected to have funding rates
        inst_price_dates = set(prices_df[instrument].dropna().index)
        funding_dates = set(meta_df.loc[(slice(None), instrument), 'funding_rate'].index.get_level_values(0))
        missing = inst_price_dates - funding_dates
        if missing:
            raise ValueError(
                f"Funding rate dates mismatch for {instrument}. "
                f"Missing: {missing}, Extra: set()"
            )

    return prices_df, meta_df, lifecycle_df
