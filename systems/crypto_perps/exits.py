"""
Exit Rules for Phase 2 State Machine

Applies state-based exit rules to modify target weights BEFORE constraints and buffers.
Exit rules override forecast-based target weights.

Execution Flow:
    Forecasts → Target Weights → [Exit Rules] → Constraints → Buffer Logic → Trades
                   Step 4           Step 4c        Step 5        Step 6
"""

import pandas as pd
import numpy as np
from typing import Dict, Tuple
import logging

from systems.crypto_perps.universe import InstrumentState, calculate_decay_target

logger = logging.getLogger(__name__)


def apply_exit_rules(
    target_weights_df: pd.DataFrame,
    current_weights_df: pd.DataFrame,
    state_df: pd.DataFrame,
    days_in_state_df: pd.DataFrame,
    forced_exit_days: int
) -> Tuple[pd.DataFrame, Dict[str, Dict[str, float]]]:
    """
    Apply exit rules to modify target weights based on instrument states

    CRITICAL: This runs BEFORE constraints and BEFORE buffers.
    Exit rules override forecast-based target weights.

    Logic:
    - BANNED_FLATTEN: target_weight = 0 (immediate flatten)
    - INELIGIBLE_HOLD: target_weight = decay_target (linear reduction)
      - On first day (days_in_state=0): record entry_weight from current_weights
      - Subsequent days: apply decay from entry_weight
    - ACTIVE: target_weight unchanged (use forecast-based weight)

    Args:
        target_weights_df: Forecast-based target weights (from sizing)
        current_weights_df: Pre-trade holdings at start-of-day (DateIndex × Instruments)
                            CRITICAL: Must represent holdings BEFORE today's trades
                            (i.e., yesterday's post-trade holdings carried forward)
        state_df: Instrument states (DateIndex × Instruments)
        days_in_state_df: Days in INELIGIBLE_HOLD state (0 for other states)
        forced_exit_days: Total days for decay

    Returns:
        - modified_weights_df: Target weights after applying exit rules
        - entry_weights_log: Dict[instrument][date] = entry_weight (for diagnostics)

    Notes:
        - Entry weight computed from current_weights on day state first becomes INELIGIBLE_HOLD
        - Decay formula: target = entry_weight * max(0, 1 - days_in_state / total_days)
        - Preserves sign (long decays to 0 from positive, short from negative)
    """
    modified_weights = target_weights_df.copy()
    entry_weights_log = {}  # Track entry weights for diagnostics

    # Track entry weights by instrument (persistent across dates)
    entry_weights = {}  # Dict[instrument] = entry_weight

    for date in target_weights_df.index:
        for instrument in target_weights_df.columns:
            state = state_df.loc[date, instrument]
            days_in_state = days_in_state_df.loc[date, instrument]

            if state == InstrumentState.BANNED_FLATTEN.value:
                # Immediate flatten: override target to 0
                modified_weights.loc[date, instrument] = 0.0

                # Clear entry weight (prevent stale values)
                if instrument in entry_weights:
                    del entry_weights[instrument]

            elif state == InstrumentState.INELIGIBLE_HOLD.value:
                # First day in INELIGIBLE_HOLD: record entry weight
                if days_in_state == 0:
                    current_weight = current_weights_df.loc[date, instrument]
                    # Fallback: if current_weight is NaN (warmup / missing data), treat as 0
                    entry_weights[instrument] = 0.0 if pd.isna(current_weight) else current_weight

                # Linear decay: override target to decay path
                entry_weight = entry_weights.get(instrument, 0.0)

                decay_target = calculate_decay_target(
                    entry_weight=entry_weight,
                    days_in_state=days_in_state,
                    total_days=forced_exit_days
                )

                modified_weights.loc[date, instrument] = decay_target

                # Log for diagnostics
                if instrument not in entry_weights_log:
                    entry_weights_log[instrument] = {}
                entry_weights_log[instrument][date] = entry_weight

            else:  # ACTIVE (or any other state)
                # Clear entry weight when not in INELIGIBLE_HOLD
                if instrument in entry_weights:
                    del entry_weights[instrument]

                # No modification to target weight (use forecast-based weight)
                # Already in modified_weights (copied from target_weights_df)

    logger.info("Exit rules applied")
    for instrument in target_weights_df.columns:
        # Count modifications
        original = target_weights_df[instrument]
        modified = modified_weights[instrument]
        num_modifications = (original != modified).sum()

        if num_modifications > 0:
            logger.info(f"  {instrument}: {num_modifications} dates modified by exit rules")

    return modified_weights, entry_weights_log
