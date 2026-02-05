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
from typing import List, Dict, Tuple, Optional
import logging
from enum import Enum

logger = logging.getLogger(__name__)


class InstrumentState(Enum):
    """
    Instrument states for Phase 2 state machine

    State Semantics:
    - NOT_YET_LAUNCHED: Before instrument launch date (position must be 0)
    - WARMUP: After launch but insufficient history for indicators (position must be 0)
    - IDM_INELIGIBLE: Insufficient overlap for IDM calculation (position must be 0)
    - ACTIVE: Normal trading (increase/decrease positions)
    - INELIGIBLE_HOLD: Temporarily fails daily eligibility (reduce-only via decay)
    - DELISTED: Permanently removed from exchange (immediate flatten to 0)
    - BANNED_FLATTEN: Manually banned instrument (immediate flatten to 0)

    State Transitions:
    - NOT_YET_LAUNCHED → WARMUP (on launch date)
    - WARMUP → IDM_INELIGIBLE or ACTIVE (after sufficient indicator history)
    - IDM_INELIGIBLE → ACTIVE (after sufficient overlap with peers)
    - ACTIVE ↔ INELIGIBLE_HOLD (based on daily eligibility: ADV, missing price)
    - DELISTED (terminal state, never exits)
    - BANNED_FLATTEN (terminal state unless manually unbanned)

    State Priority Order (for determine_instrument_state):
    1. BANNED_FLATTEN (manual override, highest priority)
    2. NOT_YET_LAUNCHED (before launch date)
    3. DELISTED (after delist date)
    4. WARMUP (insufficient history for indicators)
    5. IDM_INELIGIBLE (insufficient overlap for IDM)
    6. INELIGIBLE_HOLD (ADV too low, missing price)
    7. ACTIVE (fully eligible for trading)

    CRITICAL: INELIGIBLE_HOLD does NOT auto-transition to BANNED_FLATTEN
    - These are separate states with different purposes
    - INELIGIBLE_HOLD = temporarily ineligible, reduce-only until eligible again
    - DELISTED/BANNED_FLATTEN = permanently removed (exchange delist, data loss, config ban)
    - IDM_INELIGIBLE = instrument valid but insufficient data overlap for diversification benefit
    """
    NOT_YET_LAUNCHED = "NOT_YET_LAUNCHED"
    WARMUP = "WARMUP"
    IDM_INELIGIBLE = "IDM_INELIGIBLE"
    ACTIVE = "ACTIVE"
    INELIGIBLE_HOLD = "INELIGIBLE_HOLD"
    DELISTED = "DELISTED"
    BANNED_FLATTEN = "BANNED_FLATTEN"


# Minimum history required for indicators (conservative: covers all warmup periods)
MIN_HISTORY_DAYS = 90  # Conservative: covers vol (35), EWMAC (64), carry (30), correlation (60)

# IDM eligibility requirements (conservative: prevent optimistic diversification assumptions)
IDM_MIN_OVERLAP_DAYS = 60  # Min overlapping returns required for pairwise correlation
IDM_MIN_PEER_COUNT = 2  # Min number of peers with sufficient overlap


# Phase 1: Static Layer A instruments (top 5 by ADV)
LAYER_A_INSTRUMENTS = [
    'BTCUSDT_PERP',
    'ETHUSDT_PERP',
    'BNBUSDT_PERP',
    'SOLUSDT_PERP',
    'XRPUSDT_PERP'
]


def has_sufficient_history(
    instrument: str,
    date: pd.Timestamp,
    prices_df: pd.DataFrame,
    min_days: int = MIN_HISTORY_DAYS
) -> bool:
    """
    Check if instrument has enough history for indicators

    Args:
        instrument: Instrument code
        date: Date to check
        prices_df: DataFrame with date index and instrument columns
        min_days: Minimum number of valid (non-NaN) prices required

    Returns:
        True if instrument has sufficient history, False otherwise
    """
    if instrument not in prices_df.columns:
        return False

    # Get historical prices up to (but not including) this date
    hist_prices = prices_df.loc[:date, instrument]

    # Count valid (non-NaN) prices
    valid_count = hist_prices.notna().sum()

    return valid_count >= min_days


def is_idm_eligible(
    instrument: str,
    date: pd.Timestamp,
    prices_df: pd.DataFrame,
    instruments: List[str],
    min_overlap_days: int = IDM_MIN_OVERLAP_DAYS,
    min_peer_count: int = IDM_MIN_PEER_COUNT
) -> Tuple[bool, str]:
    """
    Check if instrument has sufficient overlap for IDM calculation

    Conservative IDM eligibility policy:
    - Instrument must have overlapping returns with >= min_peer_count other instruments
    - Each peer must have >= min_overlap_days overlapping non-NaN returns
    - This prevents optimistic IDM inflation from instruments with sparse overlap

    Args:
        instrument: Instrument code to check
        date: Date to check (use data up to this date)
        prices_df: DataFrame with date index and instrument columns
        instruments: List of all instruments in universe
        min_overlap_days: Minimum overlapping returns required with each peer
        min_peer_count: Minimum number of peers with sufficient overlap

    Returns:
        Tuple of (is_eligible, reason):
        - is_eligible: True if has sufficient overlap
        - reason: Empty string if eligible, otherwise reason for ineligibility

    Notes:
        - Computes returns internally via pct_change()
        - Only counts overlapping non-NaN returns (both instruments have valid prices)
        - Used to determine IDM_INELIGIBLE state
    """
    if instrument not in prices_df.columns:
        return False, "Instrument not in prices_df"

    # Get historical prices up to this date
    hist_prices = prices_df.loc[:date]

    # Compute returns (need both t and t-1 prices for each instrument)
    returns_df = hist_prices.pct_change()

    # Get this instrument's returns
    inst_returns = returns_df[instrument]
    inst_valid = inst_returns.notna()

    # Count peers with sufficient overlap
    peers_with_overlap = 0
    overlap_counts = {}

    for peer in instruments:
        if peer == instrument:
            continue  # Skip self

        if peer not in returns_df.columns:
            continue

        # Get peer's returns
        peer_returns = returns_df[peer]
        peer_valid = peer_returns.notna()

        # Count overlapping valid returns
        overlap = (inst_valid & peer_valid).sum()
        overlap_counts[peer] = overlap

        if overlap >= min_overlap_days:
            peers_with_overlap += 1

    # Check if enough peers
    if peers_with_overlap >= min_peer_count:
        return True, ""
    else:
        return False, f"Insufficient IDM overlap: only {peers_with_overlap}/{min_peer_count} peers with >= {min_overlap_days} days"


def determine_instrument_state(
    date: pd.Timestamp,
    instrument: str,
    prices_df: pd.DataFrame,
    meta_df: pd.DataFrame,
    lifecycle_df: Optional[pd.DataFrame],
    min_adv_notional: float,
    instruments: List[str] = None,
    banned_instruments: List[str] = None,
    check_idm_eligibility: bool = True
) -> InstrumentState:
    """
    Determine instrument state for given date

    Priority order:
    1. BANNED_FLATTEN (manual override)
    2. NOT_YET_LAUNCHED (before launch date)
    3. DELISTED (after delist date)
    4. WARMUP (launched but insufficient history for indicators)
    5. IDM_INELIGIBLE (insufficient overlap for IDM calculation)
    6. INELIGIBLE_HOLD (missing data or ADV too low)
    7. ACTIVE (fully eligible for trading)

    Args:
        date: Date to check
        instrument: Instrument code
        prices_df: DataFrame with date index and instrument columns
        meta_df: DataFrame with MultiIndex (date, instrument)
        lifecycle_df: DataFrame with lifecycle metadata (None if not using jagged panels)
        min_adv_notional: Minimum ADV threshold
        instruments: List of all instruments in universe (required for IDM eligibility check)
        banned_instruments: List of manually banned instruments
        check_idm_eligibility: If True, check IDM eligibility (default: True)

    Returns:
        InstrumentState enum value
    """
    banned_instruments = banned_instruments or []
    instruments = instruments or []

    # Check banned list (highest priority)
    if instrument in banned_instruments:
        return InstrumentState.BANNED_FLATTEN

    # Check lifecycle if available
    if lifecycle_df is not None and instrument in lifecycle_df.index:
        from sysdata.crypto.lifecycle import is_instrument_active
        is_active, reason = is_instrument_active(instrument, date, lifecycle_df)

        if not is_active:
            if reason == "NOT_YET_LAUNCHED":
                return InstrumentState.NOT_YET_LAUNCHED
            elif reason == "DELISTED":
                return InstrumentState.DELISTED

    # Check if in warmup period (launched but insufficient history)
    if lifecycle_df is not None and instrument in lifecycle_df.index:
        launch_date = lifecycle_df.loc[instrument, 'launch_date']
        if date >= launch_date:
            if not has_sufficient_history(instrument, date, prices_df):
                return InstrumentState.WARMUP

    # Check IDM eligibility (conservative: require sufficient overlap with peers)
    if check_idm_eligibility and len(instruments) > 0:
        idm_eligible, idm_reason = is_idm_eligible(
            instrument=instrument,
            date=date,
            prices_df=prices_df,
            instruments=instruments
        )
        if not idm_eligible:
            return InstrumentState.IDM_INELIGIBLE

    # Check eligibility (data quality + ADV threshold)
    is_eligible, _ = check_layer_b_eligibility(
        date=date,
        instrument=instrument,
        prices_df=prices_df,
        meta_df=meta_df,
        min_adv_notional=min_adv_notional
    )

    if not is_eligible:
        return InstrumentState.INELIGIBLE_HOLD

    return InstrumentState.ACTIVE


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
    min_adv_notional: float,
    layer_a_instruments: List[str] = None
) -> pd.DataFrame:
    """
    Build complete eligibility history for all dates and instruments

    Args:
        prices_df: DataFrame with date index and instrument columns
        meta_df: DataFrame with MultiIndex (date, instrument) containing metadata
        min_adv_notional: Minimum ADV threshold
        layer_a_instruments: Optional list of instruments to check (default: use get_layer_a_instruments())

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
    layer_a = layer_a_instruments if layer_a_instruments is not None else get_layer_a_instruments()
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
    banned_instruments: List[str] = None,
    lifecycle_df: Optional[pd.DataFrame] = None,
    prices_df: Optional[pd.DataFrame] = None,
    meta_df: Optional[pd.DataFrame] = None,
    min_adv_notional: float = 1e7
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Build state history over constant-shape traded_universe

    Membership enforcement (Layer A on each date) is applied later via masking (Step 4a).
    This function considers:
    1. Explicit banned list (config) → BANNED_FLATTEN
    2. Instrument lifecycle (launch/delist dates) → NOT_YET_LAUNCHED / DELISTED
    3. Warmup period (insufficient history) → WARMUP
    4. Daily eligibility (ADV + data gaps) → ACTIVE / INELIGIBLE_HOLD

    State Priority Order:
    1. BANNED_FLATTEN (manual override, highest priority)
    2. NOT_YET_LAUNCHED (before launch date)
    3. DELISTED (after delist date)
    4. WARMUP (launched but insufficient history for indicators)
    5. INELIGIBLE_HOLD (missing data or ADV too low)
    6. ACTIVE (eligible for trading)

    CRITICAL: INELIGIBLE_HOLD does NOT auto-transition to BANNED_FLATTEN.
    - BANNED_FLATTEN = instrument removed/untradeable (exchange delist, data loss, config ban)
    - INELIGIBLE_HOLD = temporarily fails daily eligibility, reduce-only until eligible again
    - Even when position reaches 0, state stays INELIGIBLE_HOLD until eligibility restored

    Args:
        dates: DatetimeIndex for backtest period
        instruments: List of instruments (traded_universe = union of all Layer A)
        eligibility_df: Daily eligibility (DateIndex × Instruments, bool)
        banned_instruments: Explicit banned list from config (default empty)
        lifecycle_df: DataFrame with instrument lifecycle metadata (None if not using jagged panels)
        prices_df: DataFrame with prices (for warmup history check)
        meta_df: DataFrame with metadata (for eligibility check)
        min_adv_notional: Minimum ADV threshold (for eligibility check)

    Returns:
        - state_df: DateIndex × Instruments (state as string: "not_yet_launched"/"warmup"/"active"/"ineligible_hold"/"delisted"/"banned_flatten")
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

    # Use lifecycle-aware state determination if lifecycle_df provided
    if lifecycle_df is not None and prices_df is not None and meta_df is not None:
        # Lifecycle-aware path: use determine_instrument_state for each date/instrument
        for date in dates:
            for instrument in instruments:
                state = determine_instrument_state(
                    date=date,
                    instrument=instrument,
                    prices_df=prices_df,
                    meta_df=meta_df,
                    lifecycle_df=lifecycle_df,
                    min_adv_notional=min_adv_notional,
                    instruments=instruments,  # Pass for IDM eligibility check
                    banned_instruments=banned_instruments,
                    check_idm_eligibility=True
                )
                state_df.loc[date, instrument] = state.value

        # Compute days_in_state for INELIGIBLE_HOLD periods
        ineligible = (state_df == InstrumentState.INELIGIBLE_HOLD.value)

        for instrument in instruments:
            inelig_series = ineligible[instrument]

            if inelig_series.any():
                # Create groups for consecutive ineligible periods
                state_changes = inelig_series != inelig_series.shift(1)
                groups = state_changes.cumsum()

                # Cumcount within each group (only for ineligible periods)
                days_in_state_series = inelig_series.groupby(groups).cumcount()

                # Zero out when not ineligible
                days_in_state_series.loc[~inelig_series] = 0

                days_in_state_df[instrument] = days_in_state_series.values
    else:
        # Legacy path (rectangular panels, no lifecycle): use original logic
        ineligible = ~eligibility_df

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
                inelig_series = ineligible[instrument]

                # Create groups for consecutive ineligible periods
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
