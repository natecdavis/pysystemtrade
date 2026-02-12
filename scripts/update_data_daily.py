#!/usr/bin/env python3
"""
Update Binance raw data daily for live advisory system.

Fetches recent tail (2-7 days) via REST API to fill gap between last Vision data
and current date. Complements monthly Vision ZIP updates.

**Daily cadence only** - for historical data, use update_data_monthly.py

Usage:
    python scripts/update_data_daily.py --config config/crypto_perps_baseline_v1.yaml
    python scripts/update_data_daily.py --config config/crypto_perps_baseline_v1.yaml --tail-days 5
    python scripts/update_data_daily.py --config config/crypto_perps_baseline_v1.yaml --dry-run
"""

import argparse
import sys
from pathlib import Path
from datetime import datetime, date, timedelta
import yaml
import logging

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sysdata.crypto.binance_api import BinanceAPIClient, aggregate_funding_to_daily
from sysdata.crypto.data_status import (
    get_last_available_month,
    get_expected_last_month
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def load_config(config_path: Path) -> dict:
    """Load system config and extract universe."""
    with open(config_path) as f:
        config = yaml.safe_load(f)
    return config


def extract_universe_symbols(config: dict) -> list:
    """
    Extract instrument symbols from config universe.

    Converts internal instrument IDs (e.g., BTCUSDT_PERP) to Binance symbols
    (e.g., BTCUSDT) for download.
    """
    universe_config = config.get('universe', {})
    layer_a = universe_config.get('layer_a_instruments', [])

    # Convert from instrument IDs to Binance symbols
    symbols = []
    for inst_id in layer_a:
        # Remove _PERP suffix if present
        symbol = inst_id.replace('_PERP', '')
        symbols.append(symbol)

    return symbols


def get_last_available_date_from_vision(data_dir: Path, symbol: str) -> date:
    """
    Find last available date from Vision monthly ZIPs.

    Args:
        data_dir: Root data directory (e.g., data/raw/binance)
        symbol: Binance symbol (e.g., BTCUSDT)

    Returns:
        Last day of last available month from Vision data
    """
    last_month_str = get_last_available_month(data_dir, symbol, "klines")

    if last_month_str is None:
        # No Vision data - start from a reasonable default (e.g., 30 days ago)
        logger.warning(f"No Vision data found for {symbol}, will fetch longer tail")
        return datetime.utcnow().date() - timedelta(days=30)

    # Parse YYYY-MM and get last day of that month
    year, month = map(int, last_month_str.split('-'))

    # Get last day of month
    if month == 12:
        last_day = date(year, 12, 31)
    else:
        # First day of next month - 1 day
        last_day = date(year, month + 1, 1) - timedelta(days=1)

    return last_day


def update_daily_tail(
    config_path: Path,
    data_dir: Path,
    tail_days: int = 3,
    dry_run: bool = False,
    expected_as_of_date: date = None,
    output_report: Path = None
) -> dict:
    """
    Update raw Binance data with recent daily tail via REST API.

    Args:
        config_path: Path to system config
        data_dir: Root data directory (e.g., data/raw/binance)
        tail_days: Number of recent days to fetch (default: 3)
        dry_run: If True, preview what would be fetched without executing
        expected_as_of_date: Expected date to have data through (default: yesterday UTC)
        output_report: Path to save data status report

    Returns:
        Data status report dict with day-level granularity

    Raises:
        RuntimeError: If API fetches fail
    """
    # Load config
    logger.info(f"Loading config from {config_path}")
    config = load_config(config_path)

    # Extract universe
    symbols = extract_universe_symbols(config)
    logger.info(f"Universe: {len(symbols)} instruments")

    # Determine expected as_of_date (yesterday UTC by default)
    if expected_as_of_date is None:
        expected_as_of_date = (datetime.utcnow().date() - timedelta(days=1))

    logger.info(f"Expected as_of_date: {expected_as_of_date} (D-1 policy)")

    # Initialize API client
    api_cache_dir = data_dir / 'api_cache'
    client = BinanceAPIClient(
        cache_dir=api_cache_dir,
        sleep_ms=50,  # Conservative rate limiting
        max_retries=3
    )

    # Determine fetch windows for each symbol
    fetch_plan = []
    instrument_status = {}

    for symbol in symbols:
        # Find last available date from Vision data
        last_vision_date = get_last_available_date_from_vision(data_dir, symbol)

        # Fetch window: [last_vision_date + 1, expected_as_of_date]
        # But cap by tail_days to avoid fetching too much
        earliest_fetch_date = expected_as_of_date - timedelta(days=tail_days - 1)
        start_date = max(last_vision_date + timedelta(days=1), earliest_fetch_date)
        end_date = expected_as_of_date

        if start_date > end_date:
            # Already up to date
            logger.info(f"{symbol}: up to date (last_vision_date={last_vision_date})")
            instrument_status[symbol] = {
                'last_available_date': str(last_vision_date),
                'staleness_days': (expected_as_of_date - last_vision_date).days,
                'status': 'up_to_date' if last_vision_date >= expected_as_of_date else 'lagging',
                'fetch_window': None,
                'warnings': []
            }
        else:
            # Need to fetch
            days_to_fetch = (end_date - start_date).days + 1
            logger.info(
                f"{symbol}: fetching {days_to_fetch} days "
                f"from {start_date} to {end_date}"
            )
            fetch_plan.append((symbol, start_date, end_date))
            instrument_status[symbol] = {
                'last_available_date': str(last_vision_date),
                'staleness_days': (expected_as_of_date - last_vision_date).days,
                'status': 'fetching',
                'fetch_window': f"{start_date} to {end_date}",
                'warnings': []
            }

    if dry_run:
        logger.info("\n=== DRY RUN MODE ===")
        logger.info(f"Would fetch API data for {len(fetch_plan)} symbol(s):")
        for symbol, start, end in fetch_plan:
            days = (end - start).days + 1
            logger.info(f"  - {symbol}: {days} days ({start} to {end})")
        logger.info("===================\n")

        # Generate dry-run report
        report = {
            'generated_at': datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
            'expected_as_of_date': str(expected_as_of_date),
            'dataset_as_of_date': None,  # Not computed in dry-run
            'lag_policy_days': 1,
            'cadence': 'daily',
            'dry_run': True,
            'instruments': instrument_status,
            'summary': {
                'total_instruments': len(symbols),
                'symbols_to_fetch': len(fetch_plan)
            }
        }

        if output_report:
            import json
            with open(output_report, 'w') as f:
                json.dump(report, f, indent=2)
            logger.info(f"Dry-run report saved: {output_report}")

        return report

    if not fetch_plan:
        logger.info("All data up to date - no API fetches needed")

        # Generate minimal report
        report = generate_daily_status_report(
            data_dir,
            symbols,
            expected_as_of_date,
            instrument_status
        )

        if output_report:
            import json
            with open(output_report, 'w') as f:
                json.dump(report, f, indent=2)

        return report

    # Fetch data via API
    logger.info(f"\nFetching API data for {len(fetch_plan)} symbol(s)...")

    total_fetched = 0
    total_failed = 0
    fetch_errors = []

    for i, (symbol, start_date, end_date) in enumerate(fetch_plan, 1):
        logger.info(f"\n[{i}/{len(fetch_plan)}] Fetching {symbol}...")

        try:
            # Fetch klines
            klines_df = client.fetch_klines(symbol, start_date, end_date)
            if not klines_df.empty:
                logger.info(f"  Klines: {len(klines_df)} days")
                total_fetched += len(klines_df)
            else:
                logger.warning(f"  Klines: no data returned")

            # Fetch funding rates
            funding_df = client.fetch_funding_rates(symbol, start_date, end_date)
            if not funding_df.empty:
                # Aggregate to daily
                daily_funding = aggregate_funding_to_daily(funding_df)
                logger.info(f"  Funding: {len(funding_df)} events → {len(daily_funding)} days")
            else:
                logger.warning(f"  Funding: no data returned")

            # Update status
            instrument_status[symbol]['status'] = 'fetched'
            instrument_status[symbol]['last_available_date'] = str(end_date)
            instrument_status[symbol]['staleness_days'] = (expected_as_of_date - end_date).days

        except Exception as e:
            logger.error(f"  Failed: {e}")
            total_failed += 1
            fetch_errors.append((symbol, str(e)))
            instrument_status[symbol]['status'] = 'fetch_failed'
            instrument_status[symbol]['warnings'].append(f"API fetch failed: {e}")

    # Generate updated data status report
    logger.info("\nGenerating data status report...")
    final_report = generate_daily_status_report(
        data_dir,
        symbols,
        expected_as_of_date,
        instrument_status
    )

    # Add fetch summary
    final_report['fetch_summary'] = {
        'symbols_attempted': len(fetch_plan),
        'days_fetched': total_fetched,
        'failed': total_failed,
        'dry_run': False
    }

    if fetch_errors:
        final_report['fetch_errors'] = fetch_errors

    # Save report
    if output_report:
        import json
        with open(output_report, 'w') as f:
            json.dump(final_report, f, indent=2)
        logger.info(f"Report saved: {output_report}")

    # Print summary
    logger.info("\n=== UPDATE SUMMARY ===")
    logger.info(f"Fetched: {total_fetched} days")
    logger.info(f"Failed: {total_failed} symbol(s)")
    logger.info("======================\n")

    # Return exit status
    if total_failed:
        logger.error(f"{total_failed} fetch(es) failed - check errors above")
        raise RuntimeError(f"{total_failed} fetch(es) failed")

    return final_report


def generate_daily_status_report(
    data_dir: Path,
    symbols: list,
    expected_as_of_date: date,
    instrument_status: dict
) -> dict:
    """
    Generate day-level data status report.

    Args:
        data_dir: Root data directory
        symbols: List of instrument symbols
        expected_as_of_date: Expected date to have data through
        instrument_status: Per-instrument status dict

    Returns:
        Data status report with day-level fields
    """
    # Compute dataset_as_of_date (min across instruments)
    last_dates = []
    for symbol in symbols:
        last_date_str = instrument_status[symbol].get('last_available_date')
        if last_date_str:
            last_dates.append(datetime.strptime(last_date_str, '%Y-%m-%d').date())

    dataset_as_of_date = min(last_dates) if last_dates else None

    # Count statuses
    up_to_date = sum(1 for s in instrument_status.values() if s.get('staleness_days', 999) == 0)
    lagging = sum(1 for s in instrument_status.values() if s.get('staleness_days', 0) > 0)
    max_staleness = max((s.get('staleness_days', 0) for s in instrument_status.values()), default=0)

    report = {
        'generated_at': datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
        'expected_as_of_date': str(expected_as_of_date),
        'dataset_as_of_date': str(dataset_as_of_date) if dataset_as_of_date else None,
        'lag_policy_days': 1,
        'cadence': 'daily',
        'instruments': instrument_status,
        'summary': {
            'total_instruments': len(symbols),
            'up_to_date': up_to_date,
            'lagging': lagging,
            'max_staleness_days': max_staleness,
            'as_of_date_alignment': 'strict_pass' if max_staleness == 0 else 'strict_fail'
        }
    }

    return report


def main():
    parser = argparse.ArgumentParser(
        description='Update Binance raw data daily via REST API (tail only)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Fetch recent 3 days via API
  %(prog)s --config config/crypto_perps_baseline_v1.yaml

  # Fetch recent 5 days
  %(prog)s --config config/crypto_perps_baseline_v1.yaml --tail-days 5

  # Dry run (preview what would be fetched)
  %(prog)s --config config/crypto_perps_baseline_v1.yaml --dry-run

  # Custom data directory
  %(prog)s --config config/crypto_perps_baseline_v1.yaml --data-dir /mnt/data/binance

Notes:
  - Uses D-1 lag policy: updates through yesterday UTC
  - Fetches only recent tail (2-7 days) via REST API
  - Complements monthly Vision ZIP updates
  - Cached API responses stored in data/raw/binance/api_cache/
  - Daily cadence only - for historical data, use update_data_monthly.py
        """
    )

    parser.add_argument(
        '--config',
        type=Path,
        required=True,
        help='Path to system config (to extract universe)'
    )
    parser.add_argument(
        '--data-dir',
        type=Path,
        default=Path('data/raw/binance'),
        help='Root data directory. Default: data/raw/binance'
    )
    parser.add_argument(
        '--tail-days',
        type=int,
        default=3,
        help='Number of recent days to fetch via API (default: 3)'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Preview what would be fetched without executing'
    )
    parser.add_argument(
        '--output-report',
        type=Path,
        help='Path to save data status report'
    )

    # Environment isolation
    env_group = parser.add_argument_group('Environment settings')
    env_group.add_argument(
        '--env',
        help='Environment name (uses envs/<env>/ structure). Examples: prod, dev, paper, exp1. Default: current directory'
    )
    env_group.add_argument(
        '--env-root',
        type=Path,
        help='Custom environment root (overrides --env). Can also use LIVE_OPS_ENV_ROOT env var'
    )

    args = parser.parse_args()

    # Initialize environment resolver
    from sysdata.crypto.env_paths import LiveOpsEnvironment
    env = LiveOpsEnvironment(
        env=args.env if hasattr(args, 'env') else None,
        env_root=args.env_root if hasattr(args, 'env_root') else None
    )

    # Resolve environment-aware paths (explicit args override environment)
    data_dir = env.resolve_binance_raw_dir(override=args.data_dir if args.data_dir != Path('data/raw/binance') else None)

    logger.info(f"Environment: {env}")
    logger.info(f"Data directory: {data_dir}")

    # Validate inputs
    if not args.config.exists():
        logger.error(f"Config file not found: {args.config}")
        sys.exit(1)

    if args.tail_days < 1 or args.tail_days > 30:
        logger.error(f"Invalid --tail-days: {args.tail_days} (must be 1-30)")
        sys.exit(1)

    # Run update
    try:
        report = update_daily_tail(
            args.config,
            data_dir,
            args.tail_days,
            args.dry_run,
            output_report=args.output_report
        )

        # Exit with success
        logger.info("✓ Daily tail update completed successfully")
        sys.exit(0)

    except Exception as e:
        logger.error(f"✗ Daily tail update failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
