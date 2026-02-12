"""
Staleness-based eligibility overlay for per-instrument position management.

Applies conservative rules to reduce risk when instrument data is stale,
WITHOUT modifying the underlying research_v1 targets.

Rules:
- If no position (abs(actual) ~ 0):
    - staleness = 0: allow opening (use research target)
    - staleness ≥ 1: force target = 0 (no new positions on stale data)

- If position exists:
    - staleness = 0: normal operations (use research target)
    - staleness = 1: no adds (cap target ≤ abs(actual), can reduce/flatten)
    - staleness ≥ 2: forced wind-down (target = actual × 0.5^(staleness - 1))

This is applied AFTER research_v1 generates targets, BEFORE computing trade deltas.
"""

from datetime import date
from typing import Dict, Tuple
import pandas as pd
import logging

logger = logging.getLogger(__name__)


def apply_staleness_overlay(
    targets: pd.Series,
    actual_positions: pd.Series,
    staleness_days: pd.Series,
    as_of_date: date,
    position_tolerance: float = 1e-6
) -> Tuple[pd.Series, Dict]:
    """
    Apply staleness-based eligibility rules to override targets.

    Args:
        targets: Research_v1 target notionals (from backtest)
        actual_positions: Current actual notionals
        staleness_days: Per-instrument staleness (from data_status)
        as_of_date: Dataset as_of_date used for backtest
        position_tolerance: Threshold for "no position" (default: 1e-6)

    Returns:
        tuple: (overridden_targets, audit_record)
            overridden_targets: Series with adjusted targets
            audit_record: dict with per-instrument overrides applied
    """
    overridden_targets = targets.copy()
    audit_record = {}

    # Ensure all instruments in targets have staleness and actual positions
    for inst in targets.index:
        target = targets[inst]
        actual = actual_positions.get(inst, 0.0)
        staleness = staleness_days.get(inst, 0)

        # Check if position exists
        has_position = abs(actual) > position_tolerance

        if not has_position:
            # No position: staleness ≥1 → force target=0
            if staleness >= 1:
                overridden_targets[inst] = 0.0
                audit_record[inst] = {
                    'original_target': float(target),
                    'overridden_target': 0.0,
                    'actual_position': float(actual),
                    'staleness_days': int(staleness),
                    'reason': 'no_new_positions_on_stale_data',
                    'rule': f'staleness={staleness}≥1, no position → target=0'
                }
                logger.info(
                    f"{inst}: Blocking new position (staleness={staleness}, target={target:.2f} → 0)"
                )
            # staleness=0: allow opening (no override needed)

        else:
            # Position exists
            if staleness == 0:
                # Normal operations (no override)
                pass

            elif staleness == 1:
                # No adds: cap target ≤ abs(actual)
                # Allow reduces (target closer to 0) and sign flips, but no adds
                if abs(target) > abs(actual):
                    # Capping: maintain sign of actual, cap magnitude
                    capped_target = actual  # Keep actual magnitude and sign
                    overridden_targets[inst] = capped_target
                    audit_record[inst] = {
                        'original_target': float(target),
                        'overridden_target': float(capped_target),
                        'actual_position': float(actual),
                        'staleness_days': 1,
                        'reason': 'no_adds_on_day1_staleness',
                        'rule': f'staleness=1, |target|={abs(target):.2f} > |actual|={abs(actual):.2f} → cap to actual'
                    }
                    logger.info(
                        f"{inst}: Capping target (staleness=1, "
                        f"target={target:.2f} → {capped_target:.2f}, actual={actual:.2f})"
                    )
                # else: target is reducing or flipping → allow

            else:  # staleness >= 2
                # Forced wind-down: halve exposure each additional day
                decay_factor = 0.5 ** (staleness - 1)
                new_target = actual * decay_factor
                overridden_targets[inst] = new_target
                audit_record[inst] = {
                    'original_target': float(target),
                    'overridden_target': float(new_target),
                    'actual_position': float(actual),
                    'staleness_days': int(staleness),
                    'decay_factor': float(decay_factor),
                    'reason': 'forced_wind_down',
                    'rule': f'staleness={staleness}≥2 → target=actual×0.5^{staleness-1} = {new_target:.2f}'
                }
                logger.warning(
                    f"{inst}: Forced wind-down (staleness={staleness}, "
                    f"actual={actual:.2f} → target={new_target:.2f}, decay={decay_factor:.3f})"
                )

    return overridden_targets, audit_record


def compute_staleness_summary(staleness_days: pd.Series) -> Dict:
    """
    Compute summary statistics for staleness across instruments.

    Args:
        staleness_days: Per-instrument staleness

    Returns:
        Summary dict with staleness distribution
    """
    return {
        'total_instruments': len(staleness_days),
        'up_to_date': int((staleness_days == 0).sum()),
        'lagging_1day': int((staleness_days == 1).sum()),
        'lagging_2plus_days': int((staleness_days >= 2).sum()),
        'max_staleness': int(staleness_days.max()) if len(staleness_days) > 0 else 0,
        'mean_staleness': float(staleness_days.mean()) if len(staleness_days) > 0 else 0.0
    }


def validate_staleness_inputs(
    targets: pd.Series,
    actual_positions: pd.Series,
    staleness_days: pd.Series
) -> None:
    """
    Validate inputs to staleness overlay.

    Args:
        targets: Target notionals
        actual_positions: Actual notionals
        staleness_days: Staleness days

    Raises:
        ValueError: If inputs are invalid
    """
    # Check for missing staleness data
    missing_staleness = set(targets.index) - set(staleness_days.index)
    if missing_staleness:
        raise ValueError(
            f"Missing staleness data for instruments: {missing_staleness}. "
            f"Cannot apply overlay without staleness tracking."
        )

    # Check for negative staleness (data bug)
    if (staleness_days < 0).any():
        negative = staleness_days[staleness_days < 0]
        raise ValueError(
            f"Negative staleness detected (data bug): {negative.to_dict()}"
        )

    # Warn if staleness is very high
    max_staleness = staleness_days.max()
    if max_staleness > 7:
        logger.warning(
            f"Very high staleness detected: {max_staleness} days. "
            f"Consider running update_data_daily.py to fetch recent data."
        )
