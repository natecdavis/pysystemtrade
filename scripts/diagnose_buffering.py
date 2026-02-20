#!/usr/bin/env python3
"""
Diagnose buffering behavior in backtest positions.

Validates whether buffer_size would actually affect turnover if applied
correctly (currently buffers are computed but not used in backtest output).

The backtest runner calls get_notional_position() which returns optimal
(unbuffered) positions. This script simulates what would happen if buffering
were correctly applied via inertia logic.

Usage:
    python scripts/diagnose_buffering.py \\
        --positions out/buffer_sweep/buffer_0.10/positions.csv \\
        --buffer-size 0.10 \\
        --data data/dataset_538registry_6yr_jagged.parquet \\
        --capital 10000
"""

import argparse
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from sysdata.crypto.prices import load_crypto_perps_panel

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def apply_buffering(
    optimal_positions: pd.DataFrame,
    buffer_size: float,
) -> tuple[pd.DataFrame, dict]:
    """
    Apply position buffering to optimal positions using inertia logic.

    For each instrument and date, calculate:
      - Buffer threshold based on average absolute position
      - If |optimal - current_buffered| > buffer: Update to optimal
      - Else: Hold current_buffered

    Args:
        optimal_positions: dates × instruments DataFrame (from positions.csv)
        buffer_size: buffer as fraction of average position

    Returns:
        (buffered_positions, stats_dict)
        buffered_positions: same shape as input, with inertia applied
        stats_dict: diagnostic statistics (breach counts, etc.)
    """
    buffered = optimal_positions.copy()

    total_updates = 0
    total_holds = 0
    total_days = 0

    for instrument in optimal_positions.columns:
        opt = optimal_positions[instrument].copy()

        # Calculate expanding average absolute position for buffer scaling
        # This approximates the "average position" used in forecast method
        avg_pos = opt.abs().expanding().mean()
        buffer_threshold = avg_pos * buffer_size

        # Initialize: start with optimal position on first day
        buffered_series = []
        current_pos = opt.iloc[0]
        buffered_series.append(current_pos)

        # Apply buffering logic day by day
        for i in range(1, len(opt)):
            optimal = opt.iloc[i]
            threshold = buffer_threshold.iloc[i]

            # Check if optimal position is outside buffer zone
            delta = abs(optimal - current_pos)
            if delta > threshold:
                # Breach buffer — update to optimal
                current_pos = optimal
                total_updates += 1
            else:
                # Within buffer — hold current position
                total_holds += 1

            buffered_series.append(current_pos)
            total_days += 1

        buffered[instrument] = buffered_series

    # Calculate statistics
    stats = {
        'total_days': total_days,
        'breach_count': total_updates,
        'hold_count': total_holds,
        'breach_pct': 100.0 * total_updates / total_days if total_days > 0 else 0.0,
        'hold_pct': 100.0 * total_holds / total_days if total_days > 0 else 0.0,
    }

    return buffered, stats


def compute_turnover(
    positions: pd.DataFrame,
    prices_df: pd.DataFrame,
    capital: float,
) -> float:
    """
    Compute annualized turnover from positions.

    Uses the same formula as run_dynamic_universe_backtest.py:
    Turnover = sum(|position changes|) / (2 × avg_exposure) / n_years

    This measures how many times the average position size turns over per year.

    Args:
        positions: dates × instruments held positions (in base asset units)
        prices_df: dates × instruments close prices (not used, kept for API compat)
        capital: notional capital in USD (not used, kept for API compat)

    Returns:
        annual_turnover: turnover as multiple of average exposure per year
    """
    # Calculate average total exposure (sum of absolute positions)
    total_exposure = positions.abs().sum(axis=1)
    avg_exposure = float(total_exposure.mean())

    # Calculate daily position changes (sum of absolute changes)
    daily_delta = positions.diff().abs().sum(axis=1)

    # Annualize (match backtest runner: 365 days/year, divide by 2 for round-trip)
    n_years = len(positions) / 365.0

    if avg_exposure > 0 and n_years > 0:
        annual_turnover = float(daily_delta.sum() / (2.0 * avg_exposure) / n_years)
    else:
        annual_turnover = float('nan')

    return annual_turnover


def compute_position_delta_distribution(
    optimal_positions: pd.DataFrame,
    buffered_positions: pd.DataFrame,
    buffer_size: float,
) -> dict:
    """
    Analyze distribution of position deltas relative to buffer size.

    Returns:
        dict with counts of deltas in different buckets
    """
    # Calculate average positions for buffer scaling
    avg_positions = optimal_positions.abs().expanding().mean()
    buffers = avg_positions * buffer_size

    # Position changes that would have been attempted
    deltas = (optimal_positions - buffered_positions.shift(1)).abs()

    # Normalize by buffer size
    # Where buffer is zero or very small, skip
    normalized_deltas = deltas / buffers.replace(0, np.nan)
    normalized_deltas = normalized_deltas.stack().dropna()

    # Count occurrences in buckets
    n_total = len(normalized_deltas)
    n_small = (normalized_deltas < 0.5).sum()
    n_medium = ((normalized_deltas >= 0.5) & (normalized_deltas < 1.0)).sum()
    n_large = (normalized_deltas >= 1.0).sum()

    return {
        'n_total': n_total,
        'n_small': n_small,
        'n_medium': n_medium,
        'n_large': n_large,
        'pct_small': 100.0 * n_small / n_total if n_total > 0 else 0.0,
        'pct_medium': 100.0 * n_medium / n_total if n_total > 0 else 0.0,
        'pct_large': 100.0 * n_large / n_total if n_total > 0 else 0.0,
    }


def main():
    parser = argparse.ArgumentParser(
        description='Diagnose buffering behavior in backtest positions'
    )
    parser.add_argument(
        '--positions',
        required=True,
        help='Path to positions.csv from backtest'
    )
    parser.add_argument(
        '--buffer-size',
        type=float,
        required=True,
        dest='buffer_size',
        help='Buffer size to test (e.g., 0.10)'
    )
    parser.add_argument(
        '--data',
        required=True,
        help='Path to parquet dataset (for prices)'
    )
    parser.add_argument(
        '--capital',
        type=float,
        default=10_000.0,
        help='Notional capital in USD (default: 10000)'
    )

    args = parser.parse_args()

    positions_path = Path(args.positions)
    if not positions_path.exists():
        logger.error(f"Positions file not found: {args.positions}")
        sys.exit(1)

    data_path = Path(args.data)
    if not data_path.exists():
        logger.error(f"Data file not found: {args.data}")
        sys.exit(1)

    # 1. Load positions
    logger.info(f"Loading positions from {args.positions}")
    optimal_positions = pd.read_csv(args.positions, index_col=0, parse_dates=True)
    logger.info(
        f"  {len(optimal_positions)} days × {optimal_positions.shape[1]} instruments"
    )

    # 2. Load prices
    logger.info(f"Loading prices from {args.data}")
    prices_df, _, _ = load_crypto_perps_panel(
        args.data,
        validate_schema=False,
        allow_jagged=True,
    )
    logger.info(
        f"  {len(prices_df)} days × {prices_df.shape[1]} instruments"
    )

    # 3. Apply buffering
    logger.info(f"Applying buffer_size={args.buffer_size}")
    buffered_positions, stats = apply_buffering(optimal_positions, args.buffer_size)

    # 4. Compute turnover
    logger.info("Computing turnover...")
    unbuffered_turnover = compute_turnover(optimal_positions, prices_df, args.capital)
    buffered_turnover = compute_turnover(buffered_positions, prices_df, args.capital)

    # 5. Compute position delta distribution
    logger.info("Analyzing position delta distribution...")
    delta_dist = compute_position_delta_distribution(
        optimal_positions, buffered_positions, args.buffer_size
    )

    # 6. Report
    print("\n" + "=" * 60)
    print("Buffer Diagnostic Report")
    print("=" * 60)
    print(f"Buffer size: {args.buffer_size}")
    print(f"Input positions: {args.positions}")
    print()
    print("Turnover Comparison:")
    print(f"  Unbuffered (current): {unbuffered_turnover:.2f}x/yr")
    print(f"  Buffered (simulated):  {buffered_turnover:.2f}x/yr")

    if unbuffered_turnover > 0:
        reduction_pct = (1 - buffered_turnover / unbuffered_turnover) * 100
        print(f"  Reduction: {reduction_pct:.1f}%")

    print()
    print("Buffer Breach Analysis:")
    print(f"  Days with position updates: {stats['breach_count']:,} / {stats['total_days']:,} ({stats['breach_pct']:.1f}%)")
    print(f"  Days held by buffer:        {stats['hold_count']:,} / {stats['total_days']:,} ({stats['hold_pct']:.1f}%)")

    print()
    print("Position Delta Distribution (relative to buffer):")
    print(f"  |delta| < 0.5 × buffer:  {delta_dist['n_small']:,} days ({delta_dist['pct_small']:.1f}%) ← buffer prevented trade")
    print(f"  |delta| < 1.0 × buffer:  {delta_dist['n_medium']:,} days ({delta_dist['pct_medium']:.1f}%) ← buffer breached slightly")
    print(f"  |delta| ≥ 1.0 × buffer:  {delta_dist['n_large']:,} days ({delta_dist['pct_large']:.1f}%) ← buffer breached significantly")
    print("=" * 60)
    print()


if __name__ == '__main__':
    main()
