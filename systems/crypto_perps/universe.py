"""
Universe selection and eligibility logic for crypto perpetual futures

Implements Layer A (static membership) and Layer B (daily eligibility) filtering.

Phase 1 Simplified State Machine:
- All Layer A instruments default to ACTIVE
- If Layer B ineligible (missing price or low ADV): FREEZE position (no trades allowed)
- No skip-days logic (instrument remains in universe, just frozen)

Missing Price Handling:
- If close[t] missing: instrument becomes ineligible for day t
- Position frozen at previous value (no trades)
- Price return = 0 for that day (NOT carry-forward)
- PnL = 0 for that day
- Log warning
"""

import pandas as pd
import numpy as np
from typing import List, Dict, Tuple
import logging

logger = logging.getLogger(__name__)


# Phase 1: Static Layer A instruments (top 5 by ADV)
LAYER_A_INSTRUMENTS = [
    'BTCUSDT_PERP',
    'ETHUSDT_PERP',
    'BNBUSDT_PERP',
    'SOLUSDT_PERP',
    'XRPUSDT_PERP'
]


def get_layer_a_instruments() -> List[str]:
    """
    Get Layer A instruments (static for Phase 1)

    Returns:
        List of instrument codes for Layer A universe

    Notes:
        - Phase 1: Static list of top 5 instruments by ADV
        - Phase 2: Will implement monthly review and dynamic membership
    """
    return LAYER_A_INSTRUMENTS.copy()


def check_layer_b_eligibility(
    date: pd.Timestamp,
    instrument: str,
    prices_df: pd.DataFrame,
    meta_df: pd.DataFrame,
    min_adv_notional: float
) -> Tuple[bool, str]:
    """
    Check if an instrument is eligible on a given date (Layer B filter)

    Args:
        date: Date to check
        instrument: Instrument code
        prices_df: DataFrame with date index and instrument columns (close prices)
        meta_df: DataFrame with MultiIndex (date, instrument) containing metadata
        min_adv_notional: Minimum ADV threshold in notional terms

    Returns:
        Tuple of (is_eligible, reason)
        - is_eligible: True if instrument passes all Layer B checks
        - reason: String describing ineligibility reason (empty if eligible)

    Eligibility Criteria:
        1. Close price must exist for this date
        2. ADV must be >= min_adv_notional
    """
    # Check if date exists in prices_df
    if date not in prices_df.index:
        return False, f"Date {date} not in price data"

    # Check if close price exists (not NaN)
    if instrument not in prices_df.columns:
        return False, f"Instrument {instrument} not in price data"

    close_price = prices_df.loc[date, instrument]
    if pd.isna(close_price):
        return False, "Missing close price"

    # Check ADV threshold
    try:
        adv = meta_df.loc[(date, instrument), 'adv_notional']
        if pd.isna(adv):
            return False, "Missing ADV data"
        if adv < min_adv_notional:
            return False, f"ADV ({adv:.2e}) below threshold ({min_adv_notional:.2e})"
    except KeyError:
        return False, f"No metadata for date {date}, instrument {instrument}"

    # All checks passed
    return True, ""


def get_eligible_instruments(
    date: pd.Timestamp,
    prices_df: pd.DataFrame,
    meta_df: pd.DataFrame,
    min_adv_notional: float
) -> Dict[str, Dict[str, any]]:
    """
    Get eligibility status for all Layer A instruments on a given date

    Args:
        date: Date to check
        prices_df: DataFrame with date index and instrument columns
        meta_df: DataFrame with MultiIndex (date, instrument) containing metadata
        min_adv_notional: Minimum ADV threshold

    Returns:
        Dict mapping instrument -> eligibility info
        Example: {
            'BTCUSDT_PERP': {'eligible': True, 'reason': ''},
            'ETHUSDT_PERP': {'eligible': False, 'reason': 'Low ADV'},
            ...
        }

    Notes:
        - Checks all Layer A instruments
        - Ineligible instruments are FROZEN (position held, no trades)
        - Phase 1: No skip-days or state transitions beyond ACTIVE/FROZEN
    """
    layer_a = get_layer_a_instruments()
    eligibility = {}

    for instrument in layer_a:
        is_eligible, reason = check_layer_b_eligibility(
            date=date,
            instrument=instrument,
            prices_df=prices_df,
            meta_df=meta_df,
            min_adv_notional=min_adv_notional
        )

        eligibility[instrument] = {
            'eligible': is_eligible,
            'reason': reason
        }

        if not is_eligible:
            logger.warning(
                f"{date.date()} - {instrument} INELIGIBLE: {reason}"
            )

    return eligibility


def build_eligibility_history(
    prices_df: pd.DataFrame,
    meta_df: pd.DataFrame,
    min_adv_notional: float
) -> pd.DataFrame:
    """
    Build complete eligibility history for all dates and instruments

    Args:
        prices_df: DataFrame with date index and instrument columns
        meta_df: DataFrame with MultiIndex (date, instrument) containing metadata
        min_adv_notional: Minimum ADV threshold

    Returns:
        DataFrame with date index and instrument columns (boolean values)
        True = eligible, False = frozen

    Example:
                      BTCUSDT_PERP  ETHUSDT_PERP  ...
        2023-01-01    True          True          ...
        2023-01-02    True          False         ...
        ...

    Notes:
        - Useful for vectorized operations in backtesting
        - Phase 1: Frozen instruments stay in universe (no skip-days)
    """
    layer_a = get_layer_a_instruments()
    dates = prices_df.index

    eligibility_data = {}
    for instrument in layer_a:
        eligibility_series = []
        for date in dates:
            is_eligible, _ = check_layer_b_eligibility(
                date=date,
                instrument=instrument,
                prices_df=prices_df,
                meta_df=meta_df,
                min_adv_notional=min_adv_notional
            )
            eligibility_series.append(is_eligible)

        eligibility_data[instrument] = eligibility_series

    eligibility_df = pd.DataFrame(eligibility_data, index=dates)
    return eligibility_df


def handle_missing_price(
    date: pd.Timestamp,
    instrument: str,
    prev_position: float
) -> Tuple[float, float, float]:
    """
    Handle missing price for an instrument

    Phase 1 Explicit Behavior:
    - Position: frozen at previous value
    - Price return: 0 (NOT carry-forward)
    - PnL: 0

    Args:
        date: Date with missing price
        instrument: Instrument code
        prev_position: Previous position (notional or contracts)

    Returns:
        Tuple of (new_position, price_return, pnl)
        - new_position: Same as prev_position (frozen)
        - price_return: 0.0
        - pnl: 0.0

    Notes:
        - Logs warning
        - Explicit zero PnL (not inferred)
        - No forecast update, no trades
    """
    logger.warning(
        f"{date.date()} - {instrument} has missing price. "
        f"Position frozen at {prev_position:.2f}, PnL = 0"
    )

    return prev_position, 0.0, 0.0
