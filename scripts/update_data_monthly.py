#!/usr/bin/env python3
"""
Update Binance raw data monthly for live advisory system.

Handles monthly batch updates through last complete month (M-2 policy) with
explicit handling of Binance Vision publication lag (~2-4 weeks after month end).

**NOT for daily updates** - monthly cadence only.

Usage:
    python scripts/update_data_monthly.py --config config/crypto_perps_baseline_v1.yaml
    python scripts/update_data_monthly.py --config config/crypto_perps_baseline_v1.yaml --dry-run
    python scripts/update_data_monthly.py --config config/crypto_perps_baseline_v1.yaml --fail-on-missing
"""

import argparse
import sys
from pathlib import Path
from datetime import datetime, date
import yaml
import logging

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sysdata.crypto.data_status import (
    generate_data_status_report,
    generate_data_status_report_v1,
    save_data_status_report,
    validate_data_completeness,
    get_expected_last_month,
    get_last_available_month,
    get_missing_months
)
from scripts.download_binance_data import (
    download_symbol_month,
    normalize_and_validate_symbol
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
    Extract instrument symbols for download from config.

    Uses canonical mapping via config_helpers module.
    """
    from sysdata.crypto.config_helpers import (
        extract_candidate_instruments,
        instrument_id_to_symbol
    )

    # Get candidate instruments (with error checking)
    try:
        candidate_ids = extract_candidate_instruments(config)
    except ValueError as e:
        logger.error(str(e))
        raise

    # Determine source
    data_acq = config.get('data_acquisition', {})
    if 'candidate_instruments' in data_acq:
        logger.info(f"Using data_acquisition.candidate_instruments: {len(candidate_ids)} instruments")
    else:
        logger.info(f"Using universe.layer_a_instruments: {len(candidate_ids)} instruments (fallback)")

    # Convert using canonical mapping
    symbols = [instrument_id_to_symbol(inst_id) for inst_id in candidate_ids]

    return symbols


def update_raw_data(
    config_path: Path,
    data_dir: Path,
    dry_run: bool = False,
    fail_on_missing: bool = False,
    lag_months: int = 2,
    output_report: Path = None,
    expected_as_of_date: date = None
) -> dict:
    """
    Update raw Binance data through last complete month.

    Args:
        config_path: Path to system config
        data_dir: Root data directory (e.g., data/raw/binance)
        dry_run: If True, preview what would be downloaded without executing
        fail_on_missing: If True, exit with error if expected month is missing
        lag_months: Conservative lag policy in months (default: 2 for M-2)
        output_report: Path to save data status report (default: data_dir/raw_data_status.json)
        expected_as_of_date: Override expected as_of_date (for historical testing). Default: today

    Returns:
        Data status report dict

    Raises:
        ValueError: If critical data issues found (missing symbols, data gaps)
    """
    # Load config
    logger.info(f"Loading config from {config_path}")
    config = load_config(config_path)

    # Extract universe
    symbols = extract_universe_symbols(config)
    logger.info(f"Universe: {len(symbols)} instruments")

    # Normalize symbols
    normalized_symbols = []
    for symbol in symbols:
        try:
            normalized = normalize_and_validate_symbol(symbol)
            normalized_symbols.append(normalized)
        except ValueError as e:
            logger.error(f"Invalid symbol {symbol}: {e}")
            raise

    # Determine update range
    if expected_as_of_date is None:
        as_of_date = datetime.utcnow()
    else:
        # Convert date to datetime for compatibility with existing code
        as_of_date = datetime.combine(expected_as_of_date, datetime.min.time())

    expected_last_month = get_expected_last_month(as_of_date, lag_months)

    logger.info(f"As of date: {as_of_date.strftime('%Y-%m-%d')}")
    logger.info(f"Expected last month: {expected_last_month} (M-{lag_months} policy)")
    if expected_as_of_date is not None:
        logger.info(f"  (using override expected_as_of_date: {expected_as_of_date})")

    # Generate initial data status report
    logger.info("Analyzing current data status...")
    initial_report = generate_data_status_report(
        data_dir,
        normalized_symbols,
        as_of_date,
        lag_months
    )

    # Validate data completeness (fail fast on critical issues)
    # Allow missing data for initial setup - we'll download it
    try:
        validate_data_completeness(initial_report, fail_on_missing, allow_missing_data=True)
    except ValueError as e:
        logger.error(f"Data validation failed: {e}")
        raise

    # Identify missing months to download
    downloads_needed = []
    for symbol in normalized_symbols:
        inst_status = initial_report['instruments'][symbol]
        missing_months = inst_status.get('missing_months', [])

        if missing_months:
            logger.info(f"{symbol}: {len(missing_months)} missing months")
            for month_str in missing_months:
                year, month = month_str.split('-')
                downloads_needed.append((symbol, int(year), int(month)))
        else:
            logger.info(f"{symbol}: up to date")

    # Check for data gaps (missing months in sequence)
    for symbol in normalized_symbols:
        last_month = get_last_available_month(data_dir, symbol, "klines")
        if last_month is not None:
            # Check for gaps between last_month and expected_last_month
            missing_months = get_missing_months(
                data_dir, symbol, last_month, expected_last_month, "klines"
            )
            if missing_months:
                # Check if this is just the expected lag, or an actual gap
                # Gap = missing month that's older than expected_last_month
                older_gaps = [m for m in missing_months if m < expected_last_month]
                if older_gaps:
                    raise ValueError(
                        f"Data gap detected for {symbol}: missing {older_gaps} "
                        f"(have data through {last_month}, expected {expected_last_month})"
                    )

    if dry_run:
        logger.info("\n=== DRY RUN MODE ===")
        logger.info(f"Would download {len(downloads_needed)} month(s):")
        for symbol, year, month in downloads_needed:
            logger.info(f"  - {symbol} {year}-{month:02d}")
        logger.info("===================\n")
        return initial_report

    if not downloads_needed:
        logger.info("All data up to date - no downloads needed")
        # Save V0 report
        if output_report is None:
            output_report = data_dir.parent / 'raw_data_status.json'
        save_data_status_report(initial_report, output_report)

        # Also generate V1 day-level report for live ops
        logger.info("Generating V1 day-level data status report...")
        # V1 report should cover all candidate instruments (not just tradable universe)
        from sysdata.crypto.config_helpers import extract_candidate_instruments
        instrument_ids = extract_candidate_instruments(config)

        v1_report = generate_data_status_report_v1(
            data_dir,
            instrument_ids,
            expected_as_of_date=as_of_date.date(),
            include_staleness=True
        )

        # Save V1 report
        if output_report.name == 'raw_data_status.json':
            v1_output_report = output_report.parent / 'raw_data_status_v1.json'
        else:
            v1_output_report = output_report.parent / output_report.name.replace('.json', '_v1.json')

        save_data_status_report(v1_report, v1_output_report)
        logger.info(f"V0 report saved: {output_report}")
        logger.info(f"V1 report saved: {v1_output_report}")

        return initial_report

    # Download missing months
    logger.info(f"\nDownloading {len(downloads_needed)} month(s)...")

    total_downloaded = []
    total_failed = []

    for i, (symbol, year, month) in enumerate(downloads_needed, 1):
        logger.info(f"\n[{i}/{len(downloads_needed)}] Downloading {symbol} {year}-{month:02d}...")

        results = download_symbol_month(
            symbol,
            year,
            month,
            data_dir,
            skip_existing=True,  # Default: skip existing
            verify_checksums=False,  # Don't verify checksums for speed
            strict=False,  # Don't fail on 404 (expected lag)
            verbose=False
        )

        # Track results
        total_downloaded.extend(results['downloaded'])
        total_failed.extend(results['failed'])

        # Log status
        if results['failed']:
            logger.error(f"  Failed: {len(results['failed'])} file(s)")
            for path, error in results['failed']:
                logger.error(f"    - {path.name}: {error}")

    # Generate updated data status report
    logger.info("\nGenerating updated data status report...")
    final_report = generate_data_status_report(
        data_dir,
        normalized_symbols,
        as_of_date,
        lag_months
    )

    # Add download summary to report
    final_report['download_summary'] = {
        'total_months_attempted': len(downloads_needed),
        'successful': len(total_downloaded),
        'failed': len(total_failed),
        'dry_run': dry_run
    }

    # Save report
    if output_report is None:
        output_report = data_dir.parent / 'raw_data_status.json'
    save_data_status_report(final_report, output_report)

    # Also generate V1 day-level report for live ops
    logger.info("Generating V1 day-level data status report...")

    # V1 report should cover all candidate instruments (not just tradable universe)
    from sysdata.crypto.config_helpers import extract_candidate_instruments
    instrument_ids = extract_candidate_instruments(config)

    v1_report = generate_data_status_report_v1(
        data_dir,
        instrument_ids,  # Pass instrument IDs directly (e.g., BTCUSDT_PERP)
        expected_as_of_date=as_of_date.date(),  # Convert datetime to date
        include_staleness=True
    )

    # Save V1 report using canonical env-aware path
    # Default: {data_dir}/../raw_data_status_v1.json (same parent as V0)
    if output_report.name == 'raw_data_status.json':
        v1_output_report = output_report.parent / 'raw_data_status_v1.json'
    else:
        # Derive V1 path from explicit V0 path
        v1_output_report = output_report.parent / output_report.name.replace('.json', '_v1.json')

    save_data_status_report(v1_report, v1_output_report)
    logger.info(f"V1 report saved: {v1_output_report}")

    # Print summary
    logger.info("\n=== UPDATE SUMMARY ===")
    logger.info(f"Downloaded: {len(total_downloaded)} file(s)")
    logger.info(f"Failed: {len(total_failed)} file(s)")
    logger.info(f"V0 report saved: {output_report}")
    logger.info(f"V1 report saved: {v1_output_report}")
    logger.info("======================\n")

    # Return exit status
    if total_failed:
        logger.error(f"{len(total_failed)} download(s) failed - check errors above")
        raise RuntimeError(f"{len(total_failed)} download(s) failed")

    return final_report


def main():
    parser = argparse.ArgumentParser(
        description='Update Binance raw data for live advisory system (monthly cadence)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Update data for config universe
  %(prog)s --config config/crypto_perps_baseline_v1.yaml

  # Dry run (preview what would be downloaded)
  %(prog)s --config config/crypto_perps_baseline_v1.yaml --dry-run

  # Fail on missing expected data
  %(prog)s --config config/crypto_perps_baseline_v1.yaml --fail-on-missing

  # Custom data directory
  %(prog)s --config config/crypto_perps_baseline_v1.yaml --data-dir /mnt/data/binance

Notes:
  - Uses M-2 lag policy: updates through (current_month - 2)
  - Binance Vision publication lag: ~2-4 weeks after month end
  - 404 errors are expected (not failures) for recent months
  - Monthly cadence only - not for daily updates
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
        '--dry-run',
        action='store_true',
        help='Preview what would be downloaded without executing'
    )
    parser.add_argument(
        '--fail-on-missing',
        action='store_true',
        help='Exit with error if expected month is missing (default: log warning only)'
    )
    parser.add_argument(
        '--lag-months',
        type=int,
        default=2,
        help='Conservative lag policy in months (default: 2 for M-2). Use 1 for M-1 (less conservative).'
    )
    parser.add_argument(
        '--output-report',
        type=Path,
        help='Path to save data status report (default: {data-dir}/../raw_data_status.json)'
    )
    parser.add_argument(
        '--expected-date',
        type=str,
        help='Override expected as_of_date (YYYY-MM-DD). For historical-live testing. Default: uses current date.'
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

    # Parse expected_date if provided
    expected_as_of_date = None
    if args.expected_date:
        try:
            expected_as_of_date = datetime.strptime(args.expected_date, '%Y-%m-%d').date()
        except ValueError:
            logger.error(f"Invalid --expected-date format: {args.expected_date}. Use YYYY-MM-DD.")
            sys.exit(1)

    # Run update
    try:
        report = update_raw_data(
            args.config,
            data_dir,
            args.dry_run,
            args.fail_on_missing,
            args.lag_months,
            args.output_report,
            expected_as_of_date
        )

        # Exit with success
        logger.info("✓ Data update completed successfully")
        sys.exit(0)

    except Exception as e:
        logger.error(f"✗ Data update failed: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
