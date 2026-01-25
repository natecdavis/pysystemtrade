"""
Monthly Layer A Review Schedule (Phase 2)

Implements monthly universe reviews where Layer A membership is evaluated
and frozen between review dates.

Key Features:
- Review dates at business month start (BMS = first business day of month)
- Layer A membership frozen between reviews
- Eligibility criteria re-evaluated only on review dates
"""

import pandas as pd
import numpy as np
from typing import List, Dict, Tuple, Any
import logging

logger = logging.getLogger(__name__)


def generate_review_dates(
    start_date: pd.Timestamp,
    end_date: pd.Timestamp,
    freq: str = 'BMS'
) -> List[pd.Timestamp]:
    """
    Generate review dates at specified frequency

    Uses 'BMS' frequency (Business Month Start = first business day of month)
    to align with monthly review semantics.

    Args:
        start_date: Start of backtest period
        end_date: End of backtest period
        freq: Pandas offset alias (default 'BMS' = business month start)
              Common values: 'BMS', 'MS' (month start), 'M' (month end)

    Returns:
        List of review dates (pd.Timestamp)

    Notes:
        - BMS = first business day of each month (recommended for monthly reviews)
        - M = last day of month (month-end)
        - Tests verify dates fall on first business day for BMS
    """
    # Generate date range using pandas frequency
    review_dates = pd.date_range(start=start_date, end=end_date, freq=freq)

    # Convert to list of Timestamps
    review_list = [pd.Timestamp(date) for date in review_dates]

    logger.info(f"Generated {len(review_list)} review dates using freq='{freq}'")
    logger.info(f"  First review: {review_list[0].date() if review_list else 'None'}")
    logger.info(f"  Last review: {review_list[-1].date() if review_list else 'None'}")

    return review_list


def evaluate_layer_a_eligibility(
    date: pd.Timestamp,
    prices_df: pd.DataFrame,
    meta_df: pd.DataFrame,
    min_adv_notional: float,
    min_history_days: int
) -> Dict[str, Dict[str, Any]]:
    """
    Evaluate Layer A eligibility on a review date

    Eligibility Criteria:
    1. ADV >= min_adv_notional (trailing 30-day average)
    2. Data coverage >= min_history_days (historical depth)
    3. No missing data in past 30 days

    Args:
        date: Review date to evaluate
        prices_df: Price history (DateIndex × Instruments)
        meta_df: Metadata (MultiIndex: date × instrument, includes 'adv_notional')
        min_adv_notional: Minimum ADV threshold for Layer A membership
        min_history_days: Minimum data coverage requirement (days)

    Returns:
        Dict[instrument] = {'eligible': bool, 'reason': str}

    Notes:
        - Only called on review dates
        - Between reviews, membership is frozen (use cached results)
        - More stringent than daily eligibility (daily_min_adv_notional)
    """
    eligibility = {}

    # Get all instruments from prices_df
    instruments = prices_df.columns.tolist()

    for instrument in instruments:
        # Check 1: Data coverage (min_history_days)
        inst_prices = prices_df[instrument].loc[:date]
        non_null_count = inst_prices.notna().sum()

        if non_null_count < min_history_days:
            eligibility[instrument] = {
                'eligible': False,
                'reason': f'Insufficient history: {non_null_count} days < {min_history_days} required'
            }
            continue

        # Check 2: Recent data availability (no gaps in past 30 days)
        if date in prices_df.index:
            date_idx = prices_df.index.get_loc(date)
            lookback_start = max(0, date_idx - 30)
            recent_prices = prices_df[instrument].iloc[lookback_start:date_idx+1]

            if recent_prices.isna().any():
                eligibility[instrument] = {
                    'eligible': False,
                    'reason': 'Missing data in past 30 days'
                }
                continue

        # Check 3: ADV threshold (trailing 30-day average)
        try:
            # Get ADV for this date
            if (date, instrument) in meta_df.index:
                adv = meta_df.loc[(date, instrument), 'adv_notional']
            else:
                # If exact date not in meta_df, try to get closest prior date
                inst_meta = meta_df.xs(instrument, level=1, drop_level=False)
                inst_meta = inst_meta[inst_meta.index.get_level_values(0) <= date]
                if len(inst_meta) > 0:
                    adv = inst_meta.iloc[-1]['adv_notional']
                else:
                    eligibility[instrument] = {
                        'eligible': False,
                        'reason': 'No ADV data available'
                    }
                    continue

            if pd.isna(adv):
                eligibility[instrument] = {
                    'eligible': False,
                    'reason': 'Missing ADV data'
                }
                continue

            if adv < min_adv_notional:
                eligibility[instrument] = {
                    'eligible': False,
                    'reason': f'ADV ({adv:.2e}) below threshold ({min_adv_notional:.2e})'
                }
                continue

        except (KeyError, IndexError) as e:
            eligibility[instrument] = {
                'eligible': False,
                'reason': f'Error accessing ADV data: {e}'
            }
            continue

        # All checks passed
        eligibility[instrument] = {
            'eligible': True,
            'reason': ''
        }

    # Log summary
    eligible_count = sum(1 for e in eligibility.values() if e['eligible'])
    logger.info(f"{date.date()} - Layer A review: {eligible_count}/{len(instruments)} instruments eligible")

    # Log ineligible instruments
    for inst, info in eligibility.items():
        if not info['eligible']:
            logger.debug(f"  {inst}: INELIGIBLE - {info['reason']}")

    return eligibility


def get_review_membership(
    date: pd.Timestamp,
    review_dates: List[pd.Timestamp],
    prices_df: pd.DataFrame,
    meta_df: pd.DataFrame,
    min_adv_notional: float,
    min_history_days: int
) -> Tuple[List[str], pd.Timestamp]:
    """
    Get Layer A membership as of the last review before this date

    Membership is frozen between reviews and only updated on review dates.

    Logic:
    1. Find last review date <= current date
    2. If NO review date <= current date (before first review):
       - Compute "initial review" at start_date using same criteria
       - This handles mid-month backtest starts
    3. If on review date: re-evaluate eligibility
    4. If between reviews: return cached membership from last review
    5. Cache membership keyed by review date

    Args:
        date: Current date
        review_dates: List of review dates (from generate_review_dates)
        prices_df: Price history (for ADV calculation)
        meta_df: Metadata (for ADV calculation)
        min_adv_notional: Minimum ADV threshold for Layer A
        min_history_days: Minimum data coverage for Layer A

    Returns:
        - frozen_membership: List of instruments in Layer A (frozen between reviews)
        - last_review_date: Date of last review (or initial review date)

    Edge Case:
        - If backtest starts mid-month (before first BMS review), compute initial
          membership at start_date and use until first review

    Notes:
        - Membership changes ONLY on review dates
        - Between reviews, returns cached membership
        - Cache is persistent across calls (stored in function attribute)
    """
    # Initialize cache if not exists (persistent across calls)
    if not hasattr(get_review_membership, '_cache'):
        get_review_membership._cache = {}

    cache = get_review_membership._cache

    # Find last review date <= current date
    past_reviews = [r for r in review_dates if r <= date]

    if len(past_reviews) == 0:
        # Before first review: compute initial review at current date
        last_review_date = pd.Timestamp(date)

        if last_review_date not in cache:
            logger.info(f"Computing initial Layer A membership at {date.date()} (before first review)")
            eligibility = evaluate_layer_a_eligibility(
                date=date,
                prices_df=prices_df,
                meta_df=meta_df,
                min_adv_notional=min_adv_notional,
                min_history_days=min_history_days
            )

            membership = [inst for inst, info in eligibility.items() if info['eligible']]
            cache[last_review_date] = membership
    else:
        # Use last review date
        last_review_date = past_reviews[-1]

        # If this IS a review date and not cached, evaluate now
        if last_review_date not in cache:
            logger.info(f"Evaluating Layer A membership at review date {last_review_date.date()}")
            eligibility = evaluate_layer_a_eligibility(
                date=last_review_date,
                prices_df=prices_df,
                meta_df=meta_df,
                min_adv_notional=min_adv_notional,
                min_history_days=min_history_days
            )

            membership = [inst for inst, info in eligibility.items() if info['eligible']]
            cache[last_review_date] = membership
        else:
            # Already cached, use frozen membership
            membership = cache[last_review_date]

    return membership, last_review_date


def clear_review_cache():
    """
    Clear the review membership cache

    Useful for testing or when starting a new backtest.
    """
    if hasattr(get_review_membership, '_cache'):
        get_review_membership._cache.clear()
        logger.debug("Cleared review membership cache")
