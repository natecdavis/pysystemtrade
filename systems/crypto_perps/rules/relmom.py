"""
Relative Momentum (Cross-Sectional Rank) Rule - Phase 2

Computes cross-sectional momentum ranks over frozen Layer A membership.
Ranks are normalized to [-1, +1] scale where:
- Top performer (highest momentum) → +1
- Bottom performer (lowest momentum) → -1
- Linear interpolation for middle ranks

Key Properties:
- Zero-sum across Layer A membership on each date (mean ≈ 0)
- Membership frozen between reviews (NOT static, NOT traded_universe)
- Forecasts = NaN for instruments outside Layer A on that date
"""

import pandas as pd
import numpy as np
from typing import Dict, List
import logging

logger = logging.getLogger(__name__)


def relative_momentum_forecasts(
    prices_df: pd.DataFrame,
    membership_by_date: Dict[pd.Timestamp, List[str]],
    horizon: int = 20,
    ewma_span: int = 60
) -> Dict[str, pd.Series]:
    """
    Calculate relative momentum (cross-sectional rank)

    Logic:
    1. Compute raw momentum: momentum_t = (price_t / price_{t-horizon}) - 1
       - Simple return over horizon days
       - horizon: lookback period (e.g., 20 days)
    2. Smooth momentum with EWMA (span = ewma_span days)
       - smoothed_momentum_t = EWMA(momentum_t, span=ewma_span, adjust=False)
    3. For each date, rank instruments cross-sectionally ONLY over membership_by_date[date]
       - Membership frozen between reviews (NOT traded_universe, NOT static list)
       - Drop NaNs first: only rank over instruments with valid smoothed momentum (n_valid)
       - If n_valid < 2, set all member forecasts to NaN (avoid divide-by-zero)
       - Tie-breaking: rank(method="average") to preserve symmetry and mean ≈ 0
    4. Normalize ranks to [-1, +1] scale (based on n_valid):
       - Pandas ranks are 1..n, convert to 0-indexed: rank0 = rank - 1
       - Top instrument (in Layer A that date, valid) → +1
       - Bottom instrument (in Layer A that date, valid) → -1
       - Linear interpolation: normalized = 2 * (rank0 / (n_valid-1)) - 1
       - Tied instruments with method="average": rank becomes fractional, still works
       - Example: 5 instruments → rank0 ∈ [0, 1, 2, 3, 4] → normalized ∈ [-1, -0.5, 0, 0.5, 1]
    5. Return dict-of-series (matches forecast pipeline signature)

    Args:
        prices_df: Price history (DateIndex × Instruments, covers traded_universe)
        membership_by_date: Dict mapping pd.Timestamp -> Layer A instruments on that date
                            (frozen between reviews, changes only on review dates)
        horizon: Lookback period for momentum calculation (days)
        ewma_span: EWMA smoothing span (days)

    Returns:
        forecasts: Dict[instrument] -> pd.Series (normalized ranks in [-1, +1])
                   Forecasts are NaN for instruments not in Layer A on that date
                   (call-site Step 4a masks these to 0 via membership mask)

    Scaling Note:
        - Relmom raw output ∈ [-1, +1]
        - Forecast scaling (in process_all_forecasts) will scale to mean abs ≈ 10
        - This typically multiplies raw by ~10, then caps at 20
        - Same scaling applied to EWMAC, carry, etc. (unified forecast pipeline)

    Implementation:
        - Compute momentum_df = prices_df / prices_df.shift(horizon) - 1
        - Smooth: smoothed_df = momentum_df.ewm(span=ewma_span, adjust=False).mean()
        - For each date:
          - members = membership_by_date.get(pd.Timestamp(date), [])
          - Take smoothed values for members, drop NaNs
          - If < 2 valid: set all member forecasts to NaN
          - Else: rank cross-sectionally, normalize to [-1, +1]
          - Write only for valid members; leave others NaN
        - Return {inst: forecast_df[inst] for inst in forecast_df.columns}

    Mathematical Definition:
        momentum_t = (price_t / price_{t-horizon}) - 1
        smoothed_t = EWMA(momentum_t, span=ewma_span, adjust=False)
        rank_t = cross_sectional_rank(smoothed_t, over=membership_by_date[t])
        if n_valid >= 2:
            forecast_t = 2 * (rank0 / (n_valid-1)) - 1  # map to [-1, +1]
        else:
            forecast_t = NaN

    Notes:
        - Ranks computed ONLY over frozen Layer A membership on each date
        - Membership changes only on review dates (frozen between reviews)
        - Exclude instruments with missing data on computation date
        - Zero-sum property: mean(ranks) ≈ 0 across Layer A instruments (not traded_universe)
        - Instruments outside Layer A on that date: forecast = NaN
        - Return type: Dict[str, pd.Series] (matches EWMAC/carry signatures)
    """
    logger.info("Calculating relative momentum forecasts...")
    logger.info(f"  Horizon: {horizon} days, EWMA span: {ewma_span} days")

    # Compute raw momentum: (price_t / price_{t-horizon}) - 1
    momentum_df = prices_df / prices_df.shift(horizon) - 1

    # Smooth with EWMA
    smoothed_df = momentum_df.ewm(span=ewma_span, adjust=False).mean()

    # Initialize forecast DataFrame (all NaN)
    forecast_df = pd.DataFrame(np.nan, index=prices_df.index, columns=prices_df.columns)

    # Compute ranks for each date
    for date in prices_df.index:
        # Get Layer A membership for this date (frozen between reviews)
        members = membership_by_date.get(pd.Timestamp(date), [])

        if len(members) == 0:
            # No members on this date, leave all NaN
            continue

        # Get smoothed momentum for members on this date
        member_smoothed = {}
        for inst in members:
            if inst in smoothed_df.columns:
                val = smoothed_df.loc[date, inst]
                if not pd.isna(val):
                    member_smoothed[inst] = val

        n_valid = len(member_smoothed)

        if n_valid < 2:
            # Can't rank with fewer than 2 valid instruments
            # Leave all members as NaN
            continue

        # Rank cross-sectionally among valid members
        # Higher momentum → higher rank → higher forecast
        smoothed_series = pd.Series(member_smoothed)
        ranks = smoothed_series.rank(method='average', ascending=True)

        # Convert to 0-indexed
        rank0 = ranks - 1

        # Normalize to [-1, +1]
        normalized = 2 * (rank0 / (n_valid - 1)) - 1

        # Write forecasts for valid members
        for inst in normalized.index:
            forecast_df.loc[date, inst] = normalized[inst]

    # Log summary statistics
    for inst in forecast_df.columns:
        valid_forecasts = forecast_df[inst].dropna()
        if len(valid_forecasts) > 0:
            mean_forecast = valid_forecasts.mean()
            logger.info(f"  {inst}: {len(valid_forecasts)} valid forecasts, mean={mean_forecast:.3f}")

    # Convert to dict-of-series (matches forecast pipeline signature)
    forecasts = {inst: forecast_df[inst] for inst in forecast_df.columns}

    return forecasts


def calculate_cross_sectional_rank(
    momentum: Dict[str, float]
) -> Dict[str, float]:
    """
    Rank instruments and normalize to [-1, +1]

    Helper function for rank calculation (can be used for testing).

    Args:
        momentum: Dict mapping instrument -> momentum value

    Returns:
        Dict mapping instrument -> normalized rank in [-1, +1]

    Example:
        5 instruments, ranks [1, 2, 3, 4, 5]
        → normalized scores [-1, -0.5, 0, 0.5, 1]

    Notes:
        - Uses rank(method="average") for tie-breaking
        - Preserves zero-sum property (mean ≈ 0)
    """
    if len(momentum) < 2:
        # Can't rank with fewer than 2 instruments
        return {inst: np.nan for inst in momentum.keys()}

    # Convert to series for ranking
    momentum_series = pd.Series(momentum)
    ranks = momentum_series.rank(method='average', ascending=True)

    # Convert to 0-indexed
    rank0 = ranks - 1

    n = len(momentum)

    # Normalize to [-1, +1]
    normalized = 2 * (rank0 / (n - 1)) - 1

    return normalized.to_dict()
