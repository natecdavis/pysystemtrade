"""
Canonical schema for crypto perpetual futures datasets

This module defines the single source of truth for dataset structure,
validation rules, and funding rate semantics.
"""

from typing import List
import pandas as pd
import numpy as np

# Column specifications
REQUIRED_COLUMNS = [
    'date',
    'instrument',
    'close',
    'funding_rate',
    'adv_notional',
    'spread_frac',
    'taker_fee_frac'
]

SCHEMA_RULES = {
    'date': ['is_datetime', 'monotonic_per_instrument'],
    'instrument': ['is_string_or_object'],
    'close': ['is_numeric', 'positive', 'finite', 'max_1e6'],
    'funding_rate': ['is_numeric', 'finite'],
    'adv_notional': ['is_numeric', 'non_negative', 'finite'],
    'spread_frac': ['is_numeric', 'between_0_and_1'],
    'taker_fee_frac': ['is_numeric', 'between_0_and_1'],
}

# Funding semantics documentation
FUNDING_SEMANTICS = {
    'frequency': '8-hourly events (00:00, 08:00, 16:00 UTC)',
    'aggregation': 'Sum of 3 events per calendar day',
    'alignment': 'funding_rate[D] applies to positions held from close(D-1) to close(D)',
    'sign_convention_accounting': (
        'funding_cost = position × funding_rate\n'
        '  - Positive rate + long position → positive cost (longs pay)\n'
        '  - Positive rate + short position → negative cost (shorts receive)\n'
        'In PnL accounting: funding_pnl = -funding_cost\n'
        '  - Positive PnL = profit (received funding)\n'
        '  - Negative PnL = loss (paid funding)'
    ),
    'typical_range': '-1% to +3% per day (extreme market conditions)',
}

# Dataset invariants (universal - apply to all datasets)
UNIVERSAL_INVARIANTS = [
    'No duplicate (date, instrument) pairs',
    'Monotonic increasing dates per instrument',
    'No NaN prices',
    'All values finite',
]

# Fixed-universe invariants (apply only to test fixtures and example datasets)
FIXED_UNIVERSE_INVARIANTS = [
    'Rectangular panel (all instruments have identical date sets)',
]


def validate_schema_compliance(df: pd.DataFrame, require_rectangular: bool = False) -> List[str]:
    """
    Validate DataFrame against canonical schema

    Args:
        df: DataFrame to validate
        require_rectangular: If True, enforce rectangular panel (all instruments same dates)
                           Only set for test fixtures and fixed-universe datasets

    Returns:
        List of error messages (empty if valid)
    """
    errors = []

    # Check required columns
    missing = set(REQUIRED_COLUMNS) - set(df.columns)
    if missing:
        errors.append(f"Missing columns: {missing}")
        return errors  # Can't validate further without columns

    # Check dtypes using pandas helpers (NOT string matching)
    if not pd.api.types.is_datetime64_any_dtype(df['date']):
        errors.append(f"date: expected datetime, got {df['date'].dtype}")

    for col in ['close', 'funding_rate', 'adv_notional', 'spread_frac', 'taker_fee_frac']:
        if not pd.api.types.is_numeric_dtype(df[col]):
            errors.append(f"{col}: expected numeric, got {df[col].dtype}")

    # Check per-column rules (use NumPy arrays to avoid dtype issues with np.isfinite)
    if 'close' in df.columns:
        close_np = df['close'].to_numpy(dtype=float, na_value=np.nan)
        if not (close_np > 0).all():
            errors.append("close: contains non-positive values")
        if not (close_np < 1e6).all():
            errors.append("close: contains unrealistic values > 1e6")
        if not np.isfinite(close_np).all():
            errors.append("close: contains inf/nan")

    if 'funding_rate' in df.columns:
        funding_np = df['funding_rate'].to_numpy(dtype=float, na_value=np.nan)
        if not np.isfinite(funding_np).all():
            errors.append("funding_rate: contains inf/nan")

    if 'adv_notional' in df.columns:
        adv_np = df['adv_notional'].to_numpy(dtype=float, na_value=np.nan)
        if not (adv_np >= 0).all():
            errors.append("adv_notional: contains negative values")
        if not np.isfinite(adv_np).all():
            errors.append("adv_notional: contains inf/nan")

    for col in ['spread_frac', 'taker_fee_frac']:
        if col in df.columns:
            col_np = df[col].to_numpy(dtype=float, na_value=np.nan)
            if not ((col_np >= 0) & (col_np < 1)).all():
                errors.append(f"{col}: values outside [0, 1) range")

    # Check for NaN in close prices (other columns NaN is caught by finite checks)
    if df['close'].isna().any():
        errors.append("close: contains NaN values")

    # Check duplicate (date, instrument)
    if df.duplicated(subset=['date', 'instrument']).any():
        errors.append("Duplicate (date, instrument) pairs found")

    # Check monotonic dates per instrument
    for instrument in df['instrument'].unique():
        inst_df = df[df['instrument'] == instrument].sort_values('date')
        if not inst_df['date'].is_monotonic_increasing:
            errors.append(f"{instrument}: dates not monotonic increasing")

    # Check rectangular panel (OPTIONAL - only for fixed-universe datasets)
    if require_rectangular:
        date_counts = df.groupby('instrument')['date'].count()
        if date_counts.nunique() > 1:
            errors.append(f"Non-rectangular panel: date counts vary {dict(date_counts)}")

    return errors
