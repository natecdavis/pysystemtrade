#!/usr/bin/env python3
"""
Live Operations Advisory System - Main Orchestrator

Single entry point for full monthly advisory workflow:
1. Update raw data (monthly batch through M-2)
2. Rebuild processed dataset with latest data
3. Run research_v1 backtest to get fresh targets
4. Generate trade plan comparing targets to actual positions
5. Optional: Generate human-readable report

**CRITICAL:** This is a MONTHLY advisory system (not daily) due to Binance Vision
publication lag (~2-4 weeks after month end).

Usage:
    python scripts/run_live_advisory.py \
        --config config/crypto_perps_baseline_v1.yaml \
        --actual-positions live/current_positions.csv \
        --current-equity 5125.50 \
        --output-dir out/live_advisory_$(date +%Y%m%d)

    # Dry run (skip data download, use existing data)
    python scripts/run_live_advisory.py \
        --config config/crypto_perps_baseline_v1.yaml \
        --actual-positions live/current_positions.csv \
        --current-equity 5125.50 \
        --output-dir out/live_advisory_test \
        --dry-run
"""

import argparse
import sys
import subprocess
from pathlib import Path
from datetime import datetime, timedelta
import yaml
import json
import logging

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sysdata.crypto.env_paths import LiveOpsEnvironment

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def run_command(cmd: list, description: str, check: bool = True) -> subprocess.CompletedProcess:
    """
    Run a command and handle errors.

    Args:
        cmd: Command to run (as list)
        description: Human-readable description
        check: If True, raise CalledProcessError on non-zero exit

    Returns:
        CompletedProcess object
    """
    logger.info(f"\n{'=' * 70}")
    logger.info(f"STEP: {description}")
    logger.info(f"{'=' * 70}")
    logger.info(f"Command: {' '.join(str(c) for c in cmd)}")

    try:
        result = subprocess.run(
            cmd,
            check=check,
            capture_output=True,
            text=True
        )

        # Log output
        if result.stdout:
            logger.info(f"Output:\n{result.stdout}")
        if result.stderr:
            logger.warning(f"Errors:\n{result.stderr}")

        logger.info(f"✓ {description} completed (exit code: {result.returncode})")
        return result

    except subprocess.CalledProcessError as e:
        logger.error(f"✗ {description} failed (exit code: {e.returncode})")
        if e.stdout:
            logger.error(f"Output:\n{e.stdout}")
        if e.stderr:
            logger.error(f"Errors:\n{e.stderr}")
        raise


def extract_universe_from_config(config_path: Path) -> list:
    """Extract instrument list from config."""
    with open(config_path) as f:
        config = yaml.safe_load(f)
    universe = config.get('universe', {}).get('layer_a_instruments', [])
    return universe


def main():
    parser = argparse.ArgumentParser(
        description='Live Operations Advisory System - Monthly Advisory Workflow',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
WORKFLOW:
  1. Update raw data (monthly batch, through month M-2)
  2. Rebuild processed dataset with latest data
  3. Run research_v1 backtest for fresh targets
  4. Generate trade plan (target vs actual deltas)
  5. Optional: Generate advisory report

CRITICAL:
  - Monthly cadence only (not daily) due to Binance Vision lag
  - Targets computed from FRESH data (not stale backtest)
  - Trade plan uses current_equity (not initial capital)
  - Prices snapshot included in audit trail

Examples:
  # Full advisory workflow
  %(prog)s \
      --config config/crypto_perps_baseline_v1.yaml \
      --actual-positions live/current_positions.csv \
      --current-equity 5125.50 \
      --output-dir out/live_advisory_$(date +%%Y%%m%%d)

  # Dry run (skip download, use existing data)
  %(prog)s \
      --config config/crypto_perps_baseline_v1.yaml \
      --actual-positions live/current_positions.csv \
      --current-equity 5125.50 \
      --output-dir out/live_advisory_test \
      --dry-run

  # Skip data update (use existing raw data)
  %(prog)s \
      --config config/crypto_perps_baseline_v1.yaml \
      --actual-positions live/current_positions.csv \
      --current-equity 5125.50 \
      --output-dir out/live_advisory_test \
      --skip-data-update
        """
    )

    parser.add_argument(
        '--config',
        type=Path,
        required=True,
        help='Path to system config (e.g., config/crypto_perps_baseline_v1.yaml)'
    )
    parser.add_argument(
        '--actual-positions',
        type=Path,
        required=True,
        help='Path to actual positions CSV (with contracts, mark_price_usd, notional_usd, timestamp)'
    )
    parser.add_argument(
        '--current-equity',
        type=float,
        required=True,
        help='Current account equity in USD (should reflect actual P&L, not initial capital)'
    )
    parser.add_argument(
        '--output-dir',
        type=Path,
        required=True,
        help='Output directory for all advisory outputs'
    )
    parser.add_argument(
        '--data-dir',
        type=Path,
        help='Root data directory. Default: data/raw/binance (or env-aware path if --env used)'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Dry run mode (skip data download, use existing data for all steps)'
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
    parser.add_argument(
        '--skip-data-update',
        action='store_true',
        help='Skip data update step (use existing raw data, but still rebuild dataset)'
    )
    parser.add_argument(
        '--skip-report',
        action='store_true',
        help='Skip advisory report generation (only generate trade plan)'
    )
    parser.add_argument(
        '--cadence',
        choices=['monthly', 'daily'],
        default='monthly',
        help='Data update cadence: monthly (V0, M-2 lag) or daily (V1, D-1 lag). Default: monthly'
    )
    parser.add_argument(
        '--tail-days',
        type=int,
        default=3,
        help='For daily cadence: number of recent days to fetch via API (default: 3)'
    )
    parser.add_argument(
        '--expected-date',
        type=str,
        help='Override expected as_of_date (YYYY-MM-DD). For testing only. Default: yesterday UTC. '
             'Disables cutover time warnings when specified.'
    )
    parser.add_argument(
        '--use-dynamic-universe',
        action='store_true',
        help='Use dynamic universe with parquet-backed adapter (pysystemtrade framework). '
             'If not specified, uses research_v1 system (custom implementation).'
    )

    args = parser.parse_args()

    # Initialize environment resolver
    env = LiveOpsEnvironment(
        env=args.env if hasattr(args, 'env') else None,
        env_root=args.env_root if hasattr(args, 'env_root') else None
    )

    # Resolve environment-aware paths (explicit args override environment)
    data_dir = env.resolve_binance_raw_dir(override=args.data_dir)
    output_dir = args.output_dir  # Output dir is always explicit
    actual_positions = args.actual_positions  # Explicit path

    logger.info(f"Environment: {env}")
    logger.info(f"Data directory: {data_dir}")
    logger.info(f"Output directory: {output_dir}")

    # Validate inputs
    if not args.config.exists():
        logger.error(f"Config file not found: {args.config}")
        sys.exit(1)

    if not actual_positions.exists():
        logger.error(f"Actual positions file not found: {actual_positions}")
        logger.error("This file must be manually maintained. See live/README.md for details.")
        sys.exit(1)

    # Create output directory
    output_dir.mkdir(parents=True, exist_ok=True)
    logger.info(f"Output directory resolved: {output_dir}")

    # Extract universe for dataset building
    # For dynamic universe, use all instruments from config (will be filtered by cost thresholds at backtest time)
    # For static universe, use explicit tradable instruments
    if args.use_dynamic_universe:
        # For dynamic universe, we want to build dataset with ALL candidate instruments
        # The dynamic universe logic will filter based on cost thresholds
        # For now, fall back to config extraction (future: use registry)
        universe = extract_universe_from_config(args.config)
        logger.info(f"Dynamic universe mode: building dataset with {len(universe)} candidates")
        logger.info(f"  (actual tradable universe will be determined by cost filters)")
    else:
        # Static universe: use explicit list from config
        universe = extract_universe_from_config(args.config)
        logger.info(f"Static universe mode: {len(universe)} instruments")

    # Handle expected_as_of_date - SINGLE SOURCE OF TRUTH for all date computations
    if args.expected_date:
        # Override: parse and use for ALL date computations
        expected_as_of_date = datetime.strptime(args.expected_date, '%Y-%m-%d').date()
        logger.info(f"Using override expected_as_of_date: {expected_as_of_date}")
        logger.info(f"  (disables cutover time warnings)")
    elif args.cadence == 'daily':
        # Default for daily: yesterday UTC with cutover time warnings
        from sysdata.crypto.data_status import get_expected_as_of_date
        expected_as_of_date = get_expected_as_of_date(
            override_date=None,
            warn_if_early=True,
            warn_if_late=True
        )
    else:
        # Default for monthly: yesterday UTC (no cutover warnings)
        expected_as_of_date = (datetime.utcnow().date() - timedelta(days=1))

    logger.info(f"Expected as_of_date: {expected_as_of_date}")

    # Compute start_date/end_date FROM expected_as_of_date (single source of truth)
    # Conservative: use expected_as_of_date as end date (not "today")
    end_date = expected_as_of_date.strftime('%Y-%m-%d')

    # Start date: use reasonable history (e.g., 4 years before expected_as_of_date)
    start_date = (expected_as_of_date - timedelta(days=4*365)).strftime('%Y-%m-%d')

    logger.info(f"Dataset window: {start_date} to {end_date}")

    try:
        # STEP 1: Update raw data
        if args.skip_data_update:
            logger.info("Skipping data update (--skip-data-update specified)")
        else:
            if args.cadence == 'monthly':
                # V0 workflow: monthly batch only
                update_cmd = [
                    sys.executable,
                    'scripts/update_data_monthly.py',
                    '--config', str(args.config),
                    '--data-dir', str(data_dir),
                    '--output-report', str(output_dir / 'raw_data_status.json')
                ]

                # Pass expected-date if provided (for historical-live testing)
                if args.expected_date:
                    update_cmd.extend(['--expected-date', args.expected_date])

                if args.dry_run:
                    update_cmd.append('--dry-run')

                run_command(update_cmd, "Update raw data (monthly batch)")

            else:  # daily cadence
                # V1 workflow: monthly base + daily tail
                # First, run monthly update to ensure base data current
                monthly_update_cmd = [
                    sys.executable,
                    'scripts/update_data_monthly.py',
                    '--config', str(args.config),
                    '--data-dir', str(args.data_dir),
                    '--output-report', str(args.output_dir / 'raw_data_status_monthly.json')
                ]

                if args.dry_run:
                    monthly_update_cmd.append('--dry-run')

                run_command(monthly_update_cmd, "Update base data (monthly Vision ZIPs)")

                # Then, fetch recent tail via API
                daily_update_cmd = [
                    sys.executable,
                    'scripts/update_data_daily.py',
                    '--config', str(args.config),
                    '--data-dir', str(args.data_dir),
                    '--tail-days', str(args.tail_days),
                    '--output-report', str(args.output_dir / 'raw_data_status.json')
                ]

                if args.dry_run:
                    daily_update_cmd.append('--dry-run')

                run_command(daily_update_cmd, "Update recent tail (daily via API)")

        # STEP 2: Rebuild processed dataset
        dataset_path = output_dir / 'dataset_latest.parquet'
        build_log_path = output_dir / 'dataset_build_log.txt'

        build_cmd = [
            sys.executable,
            'scripts/build_example_dataset.py',
            '--source', 'real',
            '--data-dir', str(data_dir),
            '--start-date', start_date,
            '--end-date', end_date,
            '--instruments', *universe,
            '--output-path', str(dataset_path),
            '--allow-jagged',
            '--min-coverage', '0.50'
        ]

        # Add V1 flags for daily cadence
        if args.cadence == 'daily':
            build_cmd.append('--include-api-cache')

        # Run and capture output to log file
        logger.info(f"Building dataset from {start_date} to {end_date}")
        result = run_command(build_cmd, "Rebuild processed dataset")

        # Write build log
        with open(build_log_path, 'w') as f:
            f.write(f"Dataset Build Log\n")
            f.write(f"==================\n\n")
            f.write(f"Start date: {start_date}\n")
            f.write(f"End date: {end_date}\n")
            f.write(f"Instruments: {len(universe)}\n")
            f.write(f"Output: {dataset_path}\n\n")
            f.write(f"Command:\n{' '.join(build_cmd)}\n\n")
            f.write(f"Output:\n{result.stdout}\n")
            if result.stderr:
                f.write(f"\nWarnings/Errors:\n{result.stderr}\n")

        # Verify dataset was created
        if not dataset_path.exists():
            raise FileNotFoundError(f"Dataset not created: {dataset_path}")

        logger.info(f"Dataset created: {dataset_path}")

        # STEP 3: Run backtest
        backtest_dir = output_dir / 'backtest_latest'

        if args.use_dynamic_universe:
            # Use dynamic universe backtest with parquet adapter
            backtest_cmd = [
                sys.executable,
                'scripts/run_dynamic_universe_backtest.py',
                '--config', str(args.config),
                '--data', str(dataset_path),
                '--outdir', str(backtest_dir)
            ]
            run_command(backtest_cmd, "Run dynamic universe backtest (parquet-backed)")
        else:
            # Use research_v1 backtest (custom implementation)
            backtest_cmd = [
                sys.executable,
                '-m', 'systems.crypto_perps.system',
                '--config', str(args.config),
                '--data', str(dataset_path),
                '--outdir', str(backtest_dir)
            ]
            run_command(backtest_cmd, "Run research_v1 backtest for fresh targets")

        # Verify backtest outputs
        required_outputs = ['positions.csv', 'diagnostics.parquet', 'metadata.json']
        for output in required_outputs:
            output_path = backtest_dir / output
            if not output_path.exists():
                raise FileNotFoundError(f"Backtest output not found: {output_path}")

        # Extract as_of_date (last date in backtest)
        import pandas as pd
        positions = pd.read_csv(backtest_dir / 'positions.csv', index_col=0, parse_dates=True)
        as_of_date = positions.index[-1].strftime('%Y-%m-%d')
        logger.info(f"Backtest as_of_date: {as_of_date}")

        # STEP 4: Generate trade plan
        trade_plan_cmd = [
            sys.executable,
            'scripts/generate_trade_plan.py',
            '--backtest-dir', str(backtest_dir),
            '--actual-positions', str(actual_positions),
            '--current-equity', str(args.current_equity),
            '--as-of-date', as_of_date,
            '--output-dir', str(output_dir),
            '--config', str(args.config)
        ]

        # Add data status for staleness overlay (V1 daily cadence)
        if args.cadence == 'daily':
            data_status_path = output_dir / 'raw_data_status.json'
            if data_status_path.exists():
                trade_plan_cmd.extend(['--data-status', str(data_status_path)])
            else:
                logger.warning(f"Data status file not found: {data_status_path}. Staleness overlay skipped.")

        run_command(trade_plan_cmd, "Generate trade plan (target vs actual deltas)")

        # STEP 5: Generate advisory report (optional)
        if args.skip_report:
            logger.info("Skipping advisory report (--skip-report specified)")
        else:
            # Check if report script exists
            report_script = Path('reports/advisory_report.py')
            if report_script.exists():
                report_cmd = [
                    sys.executable,
                    str(report_script),
                    '--advisory-dir', str(output_dir),
                    '--output', str(output_dir / 'advisory_report.txt')
                ]
                run_command(report_cmd, "Generate advisory report", check=False)
            else:
                logger.info("Advisory report script not found - skipping (optional)")

        # SUCCESS
        logger.info("\n" + "=" * 70)
        logger.info("✓ LIVE ADVISORY WORKFLOW COMPLETED SUCCESSFULLY")
        logger.info("=" * 70)
        logger.info(f"\nMode: {'Dynamic Universe' if args.use_dynamic_universe else 'Static Universe (research_v1)'}")
        logger.info(f"Output directory: {output_dir}")
        logger.info(f"\nGenerated files:")
        logger.info(f"  - raw_data_status.json (data freshness)")
        logger.info(f"  - dataset_latest.parquet (processed dataset)")
        logger.info(f"  - dataset_build_log.txt (build log)")
        logger.info(f"  - backtest_latest/ (fresh backtest outputs)")
        logger.info(f"  - trade_plan_{as_of_date}.csv (actionable trades)")
        logger.info(f"  - sanity_checks_{as_of_date}.json (risk validation)")
        logger.info(f"  - audit_bundle_{as_of_date}.json (full provenance)")
        if not args.skip_report and (output_dir / 'advisory_report.txt').exists():
            logger.info(f"  - advisory_report.txt (human-readable summary)")

        # Log dynamic universe stats if available
        if args.use_dynamic_universe:
            metadata_path = backtest_dir / 'metadata.json'
            if metadata_path.exists():
                import json
                with open(metadata_path) as f:
                    metadata = json.load(f)
                du_stats = metadata.get('dynamic_universe_stats', {})
                if du_stats:
                    logger.info(f"\nDynamic Universe Stats:")
                    logger.info(f"  Active instruments: min={du_stats['min_active']}, max={du_stats['max_active']}, avg={du_stats['avg_active']:.1f}")
                    logger.info(f"  vs static universe of {len(universe)} instruments")

        logger.info(f"\nNext steps:")
        logger.info(f"  1. Review trade plan: {output_dir / f'trade_plan_{as_of_date}.csv'}")
        logger.info(f"  2. Verify live prices on exchange")
        logger.info(f"  3. Execute trades manually")
        logger.info(f"  4. Update live/current_positions.csv with actual fills")
        logger.info(f"  5. Update live/current_equity.txt with actual P&L")
        logger.info("")

        sys.exit(0)

    except subprocess.CalledProcessError as e:
        logger.error(f"\n✗ Workflow failed at step: {e.cmd[1] if len(e.cmd) > 1 else 'unknown'}")
        logger.error(f"Exit code: {e.returncode}")
        sys.exit(1)

    except Exception as e:
        logger.error(f"\n✗ Workflow failed: {e}", exc_info=True)
        sys.exit(1)


if __name__ == '__main__':
    main()
