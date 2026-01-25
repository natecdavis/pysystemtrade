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
from enum import Enum

logger = logging.getLogger(__name__)


class InstrumentState(Enum):
    """
    Instrument states for Phase 2 state machine

    State Semantics:
    - ACTIVE: Normal trading (increase/decrease positions)
    - INELIGIBLE_HOLD: Temporarily fails daily eligibility (reduce-only via decay)
    - BANNED_FLATTEN: Instrument removed/untradeable (immediate flatten to 0)

    State Transitions:
    - ACTIVE ↔ INELIGIBLE_HOLD (based on daily eligibility)
    - BANNED_FLATTEN (never exits, permanent or until unbanned)

    CRITICAL: INELIGIBLE_HOLD does NOT auto-transition to BANNED_FLATTEN
    - These are separate states with different purposes
    - INELIGIBLE_HOLD = temporarily ineligible, reduce-only until eligible again
    - BANNED_FLATTEN = permanently removed (exchange delist, data loss, config ban)
    """
    ACTIVE = "active"
    INELIGIBLE_HOLD = "ineligible_hold"
    BANNED_FLATTEN = "banned_flatten"


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


def compute_daily_eligibility_df(
    prices_df: pd.DataFrame,
    meta_df: pd.DataFrame,
    instruments: List[str],
    daily_min_adv_notional: float,
    data_gap_days: int = 2
) -> pd.DataFrame:
    """
    Compute daily eligibility over traded universe

    Daily eligibility determines ACTIVE vs INELIGIBLE_HOLD state (Phase 2).
    An instrument is eligible on a given day if:
    1. Trailing 30-day ADV >= daily_min_adv_notional
    2. No data gaps > data_gap_days in past 30 days

    This is separate from Layer A membership (which uses min_adv_notional at reviews).
    Layer A = membership pool (evaluated monthly)
    Daily eligibility = trading readiness filter (evaluated daily)

    Args:
        prices_df: Price history (DateIndex × Instruments)
        meta_df: Metadata (MultiIndex: date × instrument, includes 'adv_notional')
        instruments: List of instruments (typically traded_universe = union of all Layer A)
        daily_min_adv_notional: Minimum ADV for daily eligibility
        data_gap_days: Maximum consecutive missing days allowed (default 2)

    Returns:
        eligibility_df: DateIndex × Instruments (bool), True = eligible

    Notes:
        - Computed over constant-shape traded_universe (not just current Layer A)
        - State can later be masked by Layer A membership (Step 4a in system.py)
        - Used by build_instrument_states() to determine ACTIVE vs INELIGIBLE_HOLD
        - This is NOT Layer A membership logic (which happens at monthly reviews)

    Example:
        Instrument in Layer A (membership frozen) but fails daily ADV
        → State = INELIGIBLE_HOLD (reduce-only/decay)
        → Still in Layer A membership (frozen until next review)
    """
    # Initialize eligibility DataFrame (all False initially)
    eligibility_df = pd.DataFrame(False, index=prices_df.index, columns=instruments)

    for instrument in instruments:
        if instrument not in prices_df.columns:
            # Instrument not in price data, stays ineligible
            logger.warning(f"{instrument} not in price data, marking ineligible")
            continue

        inst_prices = prices_df[instrument]

        for i, date in enumerate(prices_df.index):
            # Check 1: Price data exists for this date
            if pd.isna(inst_prices.loc[date]):
                eligibility_df.loc[date, instrument] = False
                continue

            # Check 2: No data gaps > data_gap_days in past 30 days
            lookback_start = max(0, i - 30)
            recent_prices = inst_prices.iloc[lookback_start:i+1]

            # Find consecutive NaN runs
            is_nan = recent_prices.isna()
            consecutive_nans = is_nan.groupby((~is_nan).cumsum()).sum()
            max_gap = consecutive_nans.max() if len(consecutive_nans) > 0 else 0

            if max_gap > data_gap_days:
                eligibility_df.loc[date, instrument] = False
                continue

            # Check 3: ADV >= daily_min_adv_notional
            try:
                if (date, instrument) in meta_df.index:
                    adv = meta_df.loc[(date, instrument), 'adv_notional']
                else:
                    # Try to get closest prior date
                    inst_meta = meta_df.xs(instrument, level=1, drop_level=False)
                    inst_meta = inst_meta[inst_meta.index.get_level_values(0) <= date]
                    if len(inst_meta) > 0:
                        adv = inst_meta.iloc[-1]['adv_notional']
                    else:
                        eligibility_df.loc[date, instrument] = False
                        continue

                if pd.isna(adv) or adv < daily_min_adv_notional:
                    eligibility_df.loc[date, instrument] = False
                    continue

            except (KeyError, IndexError):
                eligibility_df.loc[date, instrument] = False
                continue

            # All checks passed
            eligibility_df.loc[date, instrument] = True

    # Log summary statistics
    eligible_pct = eligibility_df.mean() * 100
    logger.info("Daily eligibility computed:")
    for inst in instruments:
        if inst in eligible_pct.index:
            logger.info(f"  {inst}: {eligible_pct[inst]:.1f}% eligible days")

    return eligibility_df


def build_instrument_states(
    dates: pd.DatetimeIndex,
    instruments: List[str],
    eligibility_df: pd.DataFrame,
    banned_instruments: List[str] = None
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Build state history over constant-shape traded_universe

    Membership enforcement (Layer A on each date) is applied later via masking (Step 4a).
    This function only considers:
    1. Explicit banned list (config)
    2. Daily eligibility (ADV + data gaps)

    Logic:
    1. If instrument in banned_instruments → BANNED_FLATTEN
    2. Else if passes daily eligibility (eligibility_df=True) → ACTIVE
    3. Else if fails daily eligibility (eligibility_df=False) → INELIGIBLE_HOLD (increment days_in_state)

    CRITICAL: INELIGIBLE_HOLD does NOT auto-transition to BANNED_FLATTEN.
    - BANNED_FLATTEN = instrument removed/untradeable (exchange delist, data loss, config ban)
    - INELIGIBLE_HOLD = temporarily fails daily eligibility, reduce-only until eligible again
    - Even when position reaches 0, state stays INELIGIBLE_HOLD until eligibility restored

    Args:
        dates: DatetimeIndex for backtest period
        instruments: List of instruments (traded_universe = union of all Layer A)
        eligibility_df: Daily eligibility (DateIndex × Instruments, bool)
        banned_instruments: Explicit banned list from config (default empty)

    Returns:
        - state_df: DateIndex × Instruments (state as string: "active"/"ineligible_hold"/"banned_flatten")
        - days_in_state_df: DateIndex × Instruments (trading days since entering INELIGIBLE_HOLD, 0 otherwise)
                           NOTE: Row-count based, not calendar days (if prices_df has gaps, decay is by data rows)

    days_in_state Increment Rule (CRITICAL):
        - Entry day (state first becomes INELIGIBLE_HOLD): days_in_state = 0
        - Each subsequent consecutive INELIGIBLE_HOLD row: days_in_state += 1
        - Reset to 0 when NOT in INELIGIBLE_HOLD

        Example:
          Row 0: ACTIVE → days_in_state = 0
          Row 1: INELIGIBLE_HOLD (entry) → days_in_state = 0, target = entry_weight
          Row 2: INELIGIBLE_HOLD → days_in_state = 1, target = entry_weight * (1 - 1/5)
          Row 3: INELIGIBLE_HOLD → days_in_state = 2, target = entry_weight * (1 - 2/5)
          Row 4: ACTIVE → days_in_state = 0

    Implementation Note:
        - ALWAYS store InstrumentState.X.value (strings) in state_df, NOT enum objects
        - Example: state_df.loc[date, inst] = InstrumentState.BANNED_FLATTEN.value
        - This ensures .value comparisons work correctly in downstream code

    Notes:
        - Does NOT track entry_weight (can't access current positions)
        - entry_weight will be computed in apply_exit_rules() using current holdings
          at moment of state transition to INELIGIBLE_HOLD
        - Membership masking (Layer A) applied in system.py Step 4a (not here)
    """
    if banned_instruments is None:
        banned_instruments = []

    # Initialize DataFrames (constant shape = instruments)
    state_df = pd.DataFrame(index=dates, columns=instruments, dtype=str)
    days_in_state_df = pd.DataFrame(0, index=dates, columns=instruments, dtype=int)

    # Compute ineligibility boolean matrix (inverse of eligibility)
    ineligible = ~eligibility_df

    # Vectorized approach for days_in_state (consecutive ineligible periods)
    for instrument in instruments:
        if instrument in banned_instruments:
            # Explicit ban: state=banned_flatten everywhere, days_in_state=0
            state_df[instrument] = InstrumentState.BANNED_FLATTEN.value
            days_in_state_df[instrument] = 0
        else:
            # Eligibility-based states
            # ACTIVE where eligible, INELIGIBLE_HOLD where not
            state_df.loc[eligibility_df[instrument], instrument] = InstrumentState.ACTIVE.value
            state_df.loc[~eligibility_df[instrument], instrument] = InstrumentState.INELIGIBLE_HOLD.value

            # Days in state: cumcount within each ineligible period
            # Group consecutive True runs, cumcount within each group
            inelig_series = ineligible[instrument]

            # Create groups for consecutive ineligible periods
            # When state changes, new group starts
            state_changes = inelig_series != inelig_series.shift(1)
            groups = state_changes.cumsum()

            # Cumcount within each group (only for ineligible periods)
            days_in_state_series = inelig_series.groupby(groups).cumcount()

            # Zero out when not ineligible
            days_in_state_series.loc[~inelig_series] = 0

            days_in_state_df[instrument] = days_in_state_series.values

    logger.info("Instrument states built:")
    for instrument in instruments:
        state_counts = state_df[instrument].value_counts()
        logger.info(f"  {instrument}: {state_counts.to_dict()}")

    return state_df, days_in_state_df


def calculate_decay_target(
    entry_weight: float,
    days_in_state: int,
    total_days: int
) -> float:
    """
    Linear decay: reduce position to 0 over N days, anchored to entry weight

    Formula:
        if total_days <= 0: return 0.0  # Guard against config typo
        factor = max(0.0, 1.0 - days_in_state / total_days)
        target_weight(t) = entry_weight * factor

    CRITICAL: Anchors decay to entry_weight (weight when entering INELIGIBLE_HOLD),
    NOT current position. This ensures:
    - Decay path is deterministic (not recalculated each day)
    - Monotonic reduction toward zero
    - Position reaches 0 at day = total_days
    - Clamping ensures target stays at 0 if days_in_state > total_days
    - Guard against divide-by-zero if total_days <= 0 (config typo)

    Args:
        entry_weight: Weight when entering INELIGIBLE_HOLD state (can be positive or negative)
        days_in_state: Days since entering INELIGIBLE_HOLD
        total_days: Total days for decay (forced_exit_days config, must be > 0)

    Returns:
        Target weight for today (preserves sign of entry_weight)

    Example (long position):
        - Entry weight = 0.10 (10% of capital, long)
        - total_days = 5
        - Day 0 (entry): days_in_state=0, factor = 1.0, target = 0.10 * 1.0 = 0.10 (NO reduction on entry day)
        - Day 1: days_in_state=1, factor = 0.8, target = 0.10 * 0.8 = 0.08
        - Day 2: days_in_state=2, factor = 0.6, target = 0.10 * 0.6 = 0.06
        - Day 3: days_in_state=3, factor = 0.4, target = 0.10 * 0.4 = 0.04
        - Day 4: days_in_state=4, factor = 0.2, target = 0.10 * 0.2 = 0.02
        - Day 5: days_in_state=5, factor = 0.0, target = 0.10 * 0.0 = 0.00
        - Day 6+: factor = 0.0 (clamped), target = 0.00

    CRITICAL: Entry day (days_in_state=0) has factor=1.0 (no reduction yet)

    Example (short position):
        - Entry weight = -0.10 (10% short)
        - Day 3: factor = 0.4, target = -0.10 * 0.4 = -0.04 (decays toward 0)

    Edge Cases:
        - total_days <= 0: returns 0.0 immediately (guards against config typo)
        - entry_weight = 0: returns 0.0 (no position to decay)
    """
    # Guard against config typo or edge case
    if total_days <= 0:
        return 0.0

    # Calculate decay factor (clamped to [0, 1])
    factor = max(0.0, 1.0 - days_in_state / total_days)

    # Apply to entry weight (preserves sign)
    return entry_weight * factor
