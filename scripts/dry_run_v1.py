#!/usr/bin/env python3
"""
Daily Live Ops V1: Dry Run Script

End-to-end validation of V1 workflow using real data with a small test universe.

Two operating modes:
- Mode A (recent-tail): Tests last N days from expected_as_of_date using existing Vision base + API tail
  * RECOMMENDED - no download required if Vision base is current
  * Uses get_expected_as_of_date() for expected date
  * Fetches recent tail via API
  * Fast and safe for routine validation

- Mode B (historical): Tests explicit date range, automatically downloads missing Vision ZIPs if needed
  * For testing historical windows
  * Auto-downloads missing Vision monthly ZIPs
  * Requires network access and time for downloads

Usage:
    # Mode A: Recent tail (RECOMMENDED)
    python scripts/dry_run_v1.py \
        --mode recent-tail \
        --instruments BTCUSDT_PERP ETHUSDT_PERP BNBUSDT_PERP \
        --tail-days 30 \
        --output-dir out/dry_run_$(date +%Y%m%d) \
        --data-dir data/raw/binance \
        --current-equity 5000.0

    # Mode B: Historical window
    python scripts/dry_run_v1.py \
        --mode historical \
        --instruments BTCUSDT_PERP ETHUSDT_PERP \
        --start-date 2025-12-01 \
        --end-date 2026-01-15 \
        --output-dir out/dry_run_historical \
        --data-dir data/raw/binance \
        --current-equity 5000.0
"""

import argparse
import sys
import subprocess
import json
from pathlib import Path
from datetime import datetime, timedelta, date
import logging

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sysdata.crypto.env_paths import LiveOpsEnvironment

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class DryRunReport:
    """Validation report builder."""

    def __init__(self):
        self.steps = []
        self.warnings = []
        self.errors = []
        self.metadata = {}
        self.start_time = datetime.now()

    def add_step(self, name: str, status: str, duration: float, details: str = ""):
        """Add a step result."""
        self.steps.append({
            'name': name,
            'status': status,  # 'pass', 'warn', 'fail'
            'duration_sec': duration,
            'details': details
        })

    def add_warning(self, message: str):
        """Add a warning."""
        self.warnings.append(message)

    def add_error(self, message: str):
        """Add an error."""
        self.errors.append(message)

    def add_metadata(self, key: str, value):
        """Add metadata."""
        self.metadata[key] = value

    def overall_status(self) -> str:
        """Compute overall status."""
        if self.errors or any(s['status'] == 'fail' for s in self.steps):
            return 'FAIL'
        elif self.warnings or any(s['status'] == 'warn' for s in self.steps):
            return 'PASS_WITH_WARNINGS'
        else:
            return 'PASS'

    def format_report(self) -> str:
        """Format as human-readable report."""
        lines = []
        lines.append("=" * 70)
        lines.append("DRY RUN VALIDATION REPORT")
        lines.append("=" * 70)
        lines.append(f"Status: {self.overall_status()}")
        lines.append(f"Duration: {(datetime.now() - self.start_time).total_seconds():.1f}s")
        lines.append("")

        if self.metadata:
            lines.append("Configuration:")
            for key, value in self.metadata.items():
                lines.append(f"  {key}: {value}")
            lines.append("")

        lines.append("Steps:")
        for step in self.steps:
            symbol = "✓" if step['status'] == 'pass' else ("⚠" if step['status'] == 'warn' else "✗")
            lines.append(f"  {symbol} {step['name']} ({step['duration_sec']:.1f}s)")
            if step['details']:
                for detail_line in step['details'].split('\n'):
                    if detail_line.strip():
                        lines.append(f"     {detail_line}")

        if self.warnings:
            lines.append("")
            lines.append("Warnings:")
            for warn in self.warnings:
                lines.append(f"  ⚠ {warn}")

        if self.errors:
            lines.append("")
            lines.append("Errors:")
            for err in self.errors:
                lines.append(f"  ✗ {err}")

        lines.append("")
        lines.append("=" * 70)
        return "\n".join(lines)


def run_command(cmd: list, description: str) -> tuple[subprocess.CompletedProcess, float]:
    """Run a command and return result with timing."""
    start = datetime.now()
    logger.info(f"Running: {description}")
    logger.debug(f"Command: {' '.join(str(c) for c in cmd)}")

    try:
        result = subprocess.run(
            cmd,
            check=True,
            capture_output=True,
            text=True
        )
        duration = (datetime.now() - start).total_seconds()
        return result, duration
    except subprocess.CalledProcessError as e:
        duration = (datetime.now() - start).total_seconds()
        logger.error(f"Command failed: {e.stderr}")
        raise


def validate_data_status(data_status_path: Path, report: DryRunReport) -> tuple[date, date, dict]:
    """Validate data_status.json and extract dates."""
    with open(data_status_path) as f:
        data_status = json.load(f)

    expected_as_of_date = date.fromisoformat(data_status['expected_as_of_date'])
    dataset_as_of_date = date.fromisoformat(data_status['dataset_as_of_date'])
    staleness = data_status.get('instruments', {})

    # Compute staleness summary
    stale_count = sum(1 for inst_data in staleness.values() if inst_data.get('staleness_days', 0) > 0)
    max_staleness = max((inst_data.get('staleness_days', 0) for inst_data in staleness.values()), default=0)

    details = [
        f"Expected as_of_date: {expected_as_of_date}",
        f"Dataset as_of_date: {dataset_as_of_date}",
        f"Stale instruments: {stale_count}/{len(staleness)}",
        f"Max staleness: {max_staleness} days"
    ]

    # Validate invariants
    if expected_as_of_date != dataset_as_of_date:
        if max_staleness > 0:
            report.add_warning(
                f"Dataset as_of_date ({dataset_as_of_date}) lags expected ({expected_as_of_date}) "
                f"by {max_staleness} days due to stale instruments"
            )
        else:
            report.add_error(
                f"Expected != dataset dates but max_staleness=0 (INVARIANT VIOLATION)"
            )

    return expected_as_of_date, dataset_as_of_date, staleness


def main():
    parser = argparse.ArgumentParser(
        description='Daily Live Ops V1: Dry Run Validation',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    parser.add_argument(
        '--mode',
        choices=['recent-tail', 'historical'],
        required=True,
        help='Operating mode: recent-tail (recommended, no downloads) or historical (auto-downloads missing ZIPs)'
    )
    parser.add_argument(
        '--instruments',
        nargs='+',
        required=True,
        help='Test universe instruments (e.g., BTCUSDT_PERP ETHUSDT_PERP)'
    )
    parser.add_argument(
        '--output-dir',
        type=Path,
        required=True,
        help='Output directory for dry run results'
    )
    parser.add_argument(
        '--data-dir',
        type=Path,
        default=Path('data/raw/binance'),
        help='Root data directory (default: data/raw/binance)'
    )
    parser.add_argument(
        '--current-equity',
        type=float,
        required=True,
        help='Current equity for trade plan generation (USD)'
    )

    # Mode-specific arguments
    parser.add_argument(
        '--tail-days',
        type=int,
        default=30,
        help='Mode A only: Number of recent days to test (default: 30)'
    )
    parser.add_argument(
        '--start-date',
        type=str,
        help='Mode B only: Start date (YYYY-MM-DD)'
    )
    parser.add_argument(
        '--end-date',
        type=str,
        help='Mode B only: End date (YYYY-MM-DD)'
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
    env = LiveOpsEnvironment(
        env=args.env if hasattr(args, 'env') else None,
        env_root=args.env_root if hasattr(args, 'env_root') else None
    )

    # Resolve environment-aware paths (explicit args override environment)
    data_dir = env.resolve_binance_raw_dir(override=args.data_dir if args.data_dir != Path('data/raw/binance') else None)
    output_dir = args.output_dir  # Output dir is always explicit

    logger.info(f"Environment: {env}")
    logger.info(f"Data directory: {data_dir}")
    logger.info(f"Output directory: {output_dir}")

    # Validate mode-specific arguments
    if args.mode == 'historical' and (not args.start_date or not args.end_date):
        logger.error("Mode 'historical' requires --start-date and --end-date")
        sys.exit(2)

    # Create output directory
    output_dir.mkdir(parents=True, exist_ok=True)

    # Initialize report
    report = DryRunReport()
    report.add_metadata('mode', args.mode)
    report.add_metadata('instruments', ', '.join(args.instruments))
    report.add_metadata('equity', f"${args.current_equity:.2f}")

    try:
        # ====================================================================
        # STEP 1: Data Preparation (mode-specific)
        # ====================================================================

        if args.mode == 'recent-tail':
            # Mode A: Recent tail (RECOMMENDED)
            from sysdata.crypto.data_status import get_expected_as_of_date

            expected_as_of_date = get_expected_as_of_date(warn_if_early=False, warn_if_late=False)
            start_date = expected_as_of_date - timedelta(days=args.tail_days - 1)

            report.add_metadata('expected_as_of_date', str(expected_as_of_date))
            report.add_metadata('start_date', str(start_date))
            report.add_metadata('tail_days', args.tail_days)

            logger.info(f"Mode A: Testing recent tail ({start_date} to {expected_as_of_date})")

            # Run daily tail update
            step_start = datetime.now()
            update_cmd = [
                sys.executable,
                'scripts/update_data_daily.py',
                '--instruments', *args.instruments,
                '--data-dir', str(data_dir),
                '--tail-days', str(args.tail_days),
                '--output-report', str(output_dir / 'raw_data_status.json')
            ]

            result, duration = run_command(update_cmd, "Update recent tail via API")
            report.add_step('Data update (API tail)', 'pass', duration)

        else:
            # Mode B: Historical window
            start_date = date.fromisoformat(args.start_date)
            end_date = date.fromisoformat(args.end_date)

            report.add_metadata('start_date', str(start_date))
            report.add_metadata('end_date', str(end_date))

            logger.info(f"Mode B: Testing historical window ({start_date} to {end_date})")

            # Run monthly update (will auto-download missing ZIPs)
            step_start = datetime.now()
            update_cmd = [
                sys.executable,
                'scripts/update_data_monthly.py',
                '--instruments', *args.instruments,
                '--data-dir', str(data_dir),
                '--output-report', str(output_dir / 'raw_data_status.json')
            ]

            result, duration = run_command(update_cmd, "Update base data (Vision monthly ZIPs)")
            report.add_step('Data update (Vision monthly)', 'pass', duration)

            # If end_date is recent, also run daily tail
            days_ago = (datetime.now().date() - end_date).days
            if days_ago < 30:
                daily_update_cmd = [
                    sys.executable,
                    'scripts/update_data_daily.py',
                    '--instruments', *args.instruments,
                    '--data-dir', str(args.data_dir),
                    '--tail-days', '30',
                    '--output-report', str(args.output_dir / 'raw_data_status.json')
                ]
                result, duration = run_command(daily_update_cmd, "Update recent tail (optional)")
                report.add_step('Data update (API tail)', 'pass', duration)

        # ====================================================================
        # STEP 2: Date Validation
        # ====================================================================

        step_start = datetime.now()
        data_status_path = output_dir / 'raw_data_status.json'

        if not data_status_path.exists():
            report.add_error("Data status file not generated")
            report.add_step('Date validation', 'fail', 0)
        else:
            expected, dataset, staleness = validate_data_status(data_status_path, report)

            stale_count = sum(1 for inst_data in staleness.values() if inst_data.get('staleness_days', 0) > 0)
            details = f"Expected: {expected}, Dataset: {dataset}, Stale: {stale_count}/{len(staleness)}"

            duration = (datetime.now() - step_start).total_seconds()
            status = 'warn' if stale_count > 0 else 'pass'
            report.add_step('Date validation', status, duration, details)

        # ====================================================================
        # STEP 3: Dataset Build
        # ====================================================================

        step_start = datetime.now()
        dataset_path = output_dir / 'dataset_test.parquet'

        build_cmd = [
            sys.executable,
            'scripts/build_example_dataset.py',
            '--source', 'real',
            '--data-dir', str(data_dir),
            '--start-date', str(start_date),
            '--end-date', str(expected_as_of_date if args.mode == 'recent-tail' else end_date),
            '--instruments', *args.instruments,
            '--output-path', str(dataset_path),
            '--include-api-cache',
            '--allow-jagged',
            '--min-coverage', '0.50'
        ]

        result, duration = run_command(build_cmd, "Build dataset")

        # Verify dataset
        if not dataset_path.exists():
            report.add_error("Dataset not created")
            report.add_step('Dataset build', 'fail', duration)
        else:
            import pandas as pd
            df = pd.read_parquet(dataset_path)
            num_days = len(df.index.unique())
            num_instruments = len([c for c in df.columns if c.startswith('close_')])
            nan_count = df.isna().sum().sum()

            details = f"{num_days} days, {num_instruments} instruments"
            if nan_count > 0:
                details += f", {nan_count} NaNs (WARNING)"
                report.add_warning(f"Dataset contains {nan_count} NaN values")

            status = 'warn' if nan_count > 0 else 'pass'
            report.add_step('Dataset build', status, duration, details)

        # ====================================================================
        # STEP 4: Backtest
        # ====================================================================

        step_start = datetime.now()
        backtest_dir = output_dir / 'backtest_test'

        # Create minimal config for test
        test_config_path = output_dir / 'test_config.yaml'
        test_config = {
            'universe': {'layer_a_instruments': args.instruments},
            'leverage': {'gross_cap': 2.0},
            'forecasting': {
                'ewmac_fast_span': 16,
                'ewmac_slow_span': 64,
                'scalar': 2.0,
                'cap': 20.0
            },
            'position_sizing': {'idm': 1.5}
        }
        import yaml
        with open(test_config_path, 'w') as f:
            yaml.dump(test_config, f)

        backtest_cmd = [
            sys.executable,
            'systems/crypto_perps/system.py',
            '--config', str(test_config_path),
            '--data', str(dataset_path),
            '--outdir', str(backtest_dir)
        ]

        result, duration = run_command(backtest_cmd, "Run backtest")

        # Verify backtest outputs
        positions_path = backtest_dir / 'positions.csv'
        if not positions_path.exists():
            report.add_error("Backtest positions not generated")
            report.add_step('Backtest', 'fail', duration)
        else:
            positions = pd.read_csv(positions_path, index_col=0, parse_dates=True)
            last_date = positions.index[-1].date()
            details = f"Last date: {last_date}"
            report.add_step('Backtest', 'pass', duration, details)

        # ====================================================================
        # STEP 5: Trade Plan (with staleness overlay)
        # ====================================================================

        step_start = datetime.now()

        # Create dummy actual positions (all zero)
        actual_positions_path = output_dir / 'actual_positions_test.csv'
        with open(actual_positions_path, 'w') as f:
            f.write("instrument,contracts,mark_price_usd,notional_usd,timestamp,notes\n")
            for inst in args.instruments:
                f.write(f"{inst},0.0,0.0,0.0,{datetime.now().isoformat()},test\n")

        trade_plan_cmd = [
            sys.executable,
            'scripts/generate_trade_plan.py',
            '--backtest-dir', str(backtest_dir),
            '--actual-positions', str(actual_positions_path),
            '--current-equity', str(args.current_equity),
            '--as-of-date', str(last_date),
            '--output-dir', str(output_dir),
            '--config', str(test_config_path),
            '--data-status', str(data_status_path)
        ]

        result, duration = run_command(trade_plan_cmd, "Generate trade plan")

        # Verify staleness overlay was applied
        audit_bundle_path = args.output_dir / f'audit_bundle_{last_date}.json'
        if audit_bundle_path.exists():
            with open(audit_bundle_path) as f:
                audit = json.load(f)

            if 'staleness_overlay' in audit:
                overlay_count = len(audit['staleness_overlay'].get('blocked_instruments', []))
                details = f"Overlay applied: {overlay_count} instruments blocked"
                status = 'warn' if overlay_count > 0 else 'pass'
            else:
                details = "No staleness overlay (all up to date)"
                status = 'pass'

            report.add_step('Trade plan (with staleness overlay)', status, duration, details)
        else:
            report.add_warning("Audit bundle not found (staleness overlay not verified)")
            report.add_step('Trade plan (with staleness overlay)', 'warn', duration)

        # ====================================================================
        # FINAL: Generate Report
        # ====================================================================

        report_text = report.format_report()
        print(report_text)

        # Write report to file
        report_path = output_dir / 'dry_run_report.txt'
        with open(report_path, 'w') as f:
            f.write(report_text)

        logger.info(f"Dry run report saved to: {report_path}")

        # Exit code based on status
        if report.overall_status() == 'FAIL':
            logger.error("DRY RUN FAILED")
            sys.exit(2)
        elif report.overall_status() == 'PASS_WITH_WARNINGS':
            logger.warning("DRY RUN PASSED WITH WARNINGS")
            sys.exit(1)
        else:
            logger.info("DRY RUN PASSED")
            sys.exit(0)

    except subprocess.CalledProcessError as e:
        report.add_error(f"Command failed: {e.cmd[1] if len(e.cmd) > 1 else 'unknown'}")
        report_text = report.format_report()
        print(report_text)
        sys.exit(2)

    except Exception as e:
        report.add_error(f"Unexpected error: {e}")
        report_text = report.format_report()
        print(report_text)
        logger.error(f"Dry run failed", exc_info=True)
        sys.exit(2)


if __name__ == '__main__':
    main()
