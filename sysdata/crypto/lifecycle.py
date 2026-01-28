"""
Instrument lifecycle metadata management

Handles instrument launch/delist dates and active status determination.
"""
import json
from typing import Tuple
from pathlib import Path

import pandas as pd


def load_instrument_lifecycle(
    path: str = None,
    data_root: str = None
) -> pd.DataFrame:
    """
    Load instrument lifecycle metadata

    Args:
        path: Explicit path to lifecycle JSON (takes precedence)
        data_root: Root data directory (will look for metadata/binance_symbol_lifecycle.json)
                   If neither provided, uses environment variable DATA_ROOT or 'data/raw/binance'

    Returns:
        DataFrame with columns: instrument, launch_date, status, delist_date
        Index: instrument name (with _PERP suffix)
    """
    import os

    if path is None:
        if data_root is None:
            data_root = os.environ.get('DATA_ROOT', 'data/raw/binance')
        path = f"{data_root}/metadata/binance_symbol_lifecycle.json"

    with open(path) as f:
        data = json.load(f)

    records = []
    for instrument, meta in data.items():
        # Add _PERP suffix if not present
        inst_name = instrument if instrument.endswith('_PERP') else f"{instrument}_PERP"

        records.append({
            'instrument': inst_name,
            'launch_date': pd.Timestamp(meta['launch_date']),
            'status': meta['status'],
            'delist_date': pd.Timestamp(meta['delist_date']) if meta.get('delist_date') else None
        })

    df = pd.DataFrame(records).set_index('instrument')
    return df


def is_instrument_active(
    instrument: str,
    date: pd.Timestamp,
    lifecycle_df: pd.DataFrame
) -> Tuple[bool, str]:
    """
    Check if instrument is active (launched and not delisted) on given date

    Args:
        instrument: Instrument name (e.g., 'BTCUSDT_PERP')
        date: Date to check
        lifecycle_df: DataFrame from load_instrument_lifecycle()

    Returns:
        (is_active, reason) tuple:
            - (False, "NOT_YET_LAUNCHED") if date < launch_date
            - (False, "DELISTED") if date >= delist_date
            - (False, "Unknown instrument") if not in lifecycle_df
            - (True, "ACTIVE") if launched and not delisted
    """
    if instrument not in lifecycle_df.index:
        return False, "Unknown instrument"

    meta = lifecycle_df.loc[instrument]

    if date < meta['launch_date']:
        return False, "NOT_YET_LAUNCHED"

    if meta['delist_date'] is not None and date >= meta['delist_date']:
        return False, "DELISTED"

    return True, "ACTIVE"


def derive_lifecycle_from_data(df: pd.DataFrame) -> pd.DataFrame:
    """
    Derive instrument lifecycle metadata from actual data

    Extracts first and last valid dates for each instrument based on
    non-NaN close prices in the dataset.

    Args:
        df: Long-format DataFrame with columns: date, instrument, close, ...

    Returns:
        DataFrame with index=instrument, columns: launch_date, delist_date, status
    """
    records = []

    for instrument in df['instrument'].unique():
        inst_df = df[df['instrument'] == instrument].copy()
        inst_df = inst_df.sort_values('date')

        # Find first and last non-NaN close price
        valid_prices = inst_df[inst_df['close'].notna()]

        if len(valid_prices) == 0:
            # No valid data for this instrument
            continue

        launch_date = valid_prices.iloc[0]['date']
        last_date = valid_prices.iloc[-1]['date']

        # Determine if delisted (last date is before dataset end)
        dataset_end = df['date'].max()
        is_delisted = (dataset_end - last_date).days > 30  # >30 days gap = likely delisted

        records.append({
            'instrument': instrument,
            'launch_date': pd.Timestamp(launch_date),
            'delist_date': pd.Timestamp(last_date) if is_delisted else None,
            'status': 'delisted' if is_delisted else 'active'
        })

    lifecycle_df = pd.DataFrame(records).set_index('instrument')
    return lifecycle_df
