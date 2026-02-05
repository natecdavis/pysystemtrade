#!/usr/bin/env python3
"""
Daily Live Ops V1: Doctor CLI - Preflight Health Check

Comprehensive preflight check before running daily advisory.
Validates data recency, positions sanity, and system readiness.

Usage:
    # With explicit data status path
    python scripts/doctor_live_ops.py \
        --config config/crypto_perps_baseline_v1.yaml \
        --actual-positions live/current_positions.csv \
        --current-equity-file live/current_equity.txt \
        --data-status-path out/latest/raw_data_status.json \
        --cadence daily

    # Auto-discover latest data status
    python scripts/doctor_live_ops.py \
        --config config/crypto_perps_baseline_v1.yaml \
        --actual-positions live/current_positions.csv \
        --current-equity-file live/current_equity.txt \
        --data-dir data/raw/binance \
        --cadence daily

Exit Codes:
    0 - PASS (all checks green)
    1 - PASS_WITH_WARNINGS (non-critical warnings)
    2 - FAIL (critical checks failed, do not proceed)
"""

import argparse
import sys
import json
from pathlib import Path
from datetime import datetime, timedelta, date, timezone
import logging
import pandas as pd
import yaml

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sysdata.crypto.env_paths import LiveOpsEnvironment

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def check_data_recency(data_status_path: Path, cadence: str) -> tuple[str, list, list]:
    """
    Check data recency from data_status.json (PRIMARY SOURCE).

    Returns:
        (status, errors, warnings)
    """
    errors = []
    warnings = []

    if not data_status_path.exists():
        errors.append(f"Data status file not found: {data_status_path}")
        return 'FAIL', errors, warnings

    try:
        with open(data_status_path) as f:
            data_status = json.load(f)

        generated_at = data_status.get('generated_at', 'unknown')
        expected_as_of_date = date.fromisoformat(data_status['expected_as_of_date'])
        dataset_as_of_date = date.fromisoformat(data_status['dataset_as_of_date'])

        # Check expected_as_of_date is reasonable (yesterday UTC for daily cadence)
        if cadence == 'daily':
            today = datetime.now(timezone.utc).date()
            yesterday = today - timedelta(days=1)

            if expected_as_of_date > today:
                warnings.append(f"Expected as_of_date ({expected_as_of_date}) is in the FUTURE")
            elif expected_as_of_date < yesterday - timedelta(days=1):
                warnings.append(
                    f"Expected as_of_date ({expected_as_of_date}) is more than 1 day old. "
                    f"Expected {yesterday}, got {expected_as_of_date}."
                )

        # Check dataset vs expected
        lag = (expected_as_of_date - dataset_as_of_date).days
        if lag > 1:
            errors.append(
                f"Dataset as_of_date ({dataset_as_of_date}) lags expected ({expected_as_of_date}) "
                f"by {lag} days (tolerance: 1 day)"
            )
        elif lag == 1:
            warnings.append(
                f"Dataset as_of_date ({dataset_as_of_date}) lags expected ({expected_as_of_date}) by 1 day"
            )

        # Check staleness summary
        instruments = data_status.get('instruments', {})
        stale_count = sum(1 for inst_data in instruments.values() if inst_data.get('staleness_days', 0) > 0)
        max_staleness = max((inst_data.get('staleness_days', 0) for inst_data in instruments.values()), default=0)

        if stale_count > 0:
            warnings.append(
                f"{stale_count}/{len(instruments)} instruments lagging (max: {max_staleness} days)"
            )

        # Determine overall status
        if errors:
            status = 'FAIL'
        elif warnings:
            status = 'PASS_WITH_WARNINGS'
        else:
            status = 'PASS'

        return status, errors, warnings

    except Exception as e:
        errors.append(f"Error reading data_status.json: {e}")
        return 'FAIL', errors, warnings


def check_manifest_integrity(output_dir: Path) -> tuple[str, list, list]:
    """
    Check manifest integrity (checksums, missing files).

    Returns:
        (status, errors, warnings)
    """
    errors = []
    warnings = []

    # Find latest manifest
    manifest_files = list(output_dir.glob('manifest_*.json'))
    if not manifest_files:
        warnings.append("No manifest file found (optional check)")
        return 'PASS_WITH_WARNINGS', errors, warnings

    manifest_path = max(manifest_files, key=lambda p: p.stat().st_mtime)

    try:
        with open(manifest_path) as f:
            manifest = json.load(f)

        files_list = manifest.get('files', [])
        if not files_list:
            warnings.append("Manifest is empty")
            return 'PASS_WITH_WARNINGS', errors, warnings

        # Check if files exist
        missing_count = 0
        for file_entry in files_list:
            file_path = Path(file_entry['path'])
            if not file_path.exists():
                missing_count += 1

        if missing_count > 0:
            errors.append(f"{missing_count}/{len(files_list)} files in manifest are missing")
            return 'FAIL', errors, warnings

        # Check for recent API cache files
        api_cache_files = [f for f in files_list if 'api_cache' in f['path']]
        if api_cache_files:
            # Check if API cache is recent (within 7 days)
            now = datetime.now()
            stale_cache_count = 0
            for f in api_cache_files:
                file_path = Path(f['path'])
                if file_path.exists():
                    mtime = datetime.fromtimestamp(file_path.stat().st_mtime)
                    age_days = (now - mtime).days
                    if age_days > 7:
                        stale_cache_count += 1

            if stale_cache_count > 0:
                warnings.append(f"{stale_cache_count} API cache files are >7 days old")

        status = 'PASS' if not warnings and not errors else ('FAIL' if errors else 'PASS_WITH_WARNINGS')
        return status, errors, warnings

    except Exception as e:
        errors.append(f"Error reading manifest: {e}")
        return 'FAIL', errors, warnings


def check_positions_sanity(
    positions_path: Path,
    config_path: Path,
    equity_file: Path
) -> tuple[str, list, list]:
    """
    Check positions sanity using validation library.

    Returns:
        (status, errors, warnings)
    """
    from sysdata.crypto.positions_validation import validate_positions_file

    errors = []
    warnings = []

    # Check positions file exists
    if not positions_path.exists():
        errors.append(f"Positions file not found: {positions_path}")
        return 'FAIL', errors, warnings

    # Load config
    try:
        with open(config_path) as f:
            config = yaml.safe_load(f)
        universe = config.get('universe', {}).get('layer_a_instruments', [])
    except Exception as e:
        errors.append(f"Error loading config: {e}")
        return 'FAIL', errors, warnings

    # Load equity
    try:
        with open(equity_file) as f:
            equity = float(f.read().strip())
    except Exception as e:
        errors.append(f"Error reading equity file: {e}")
        return 'FAIL', errors, warnings

    # Load positions
    try:
        positions_df = pd.read_csv(positions_path)
    except Exception as e:
        errors.append(f"Error reading positions file: {e}")
        return 'FAIL', errors, warnings

    # Validate positions
    result = validate_positions_file(
        positions_df,
        universe,
        equity,
        critical_staleness_hours=48,
        allow_missing_instruments=False
    )

    # Convert validation errors/warnings to lists
    for error in result.errors:
        errors.append(f"{error.instrument}: {error.message}")

    for warning in result.warnings:
        warnings.append(f"{warning.instrument}: {warning.message}")

    # Determine status
    if errors:
        return 'FAIL', errors, warnings
    elif warnings:
        return 'PASS_WITH_WARNINGS', errors, warnings
    else:
        return 'PASS', errors, warnings


def check_equity_staleness(equity_file: Path, critical_hours: int = 48) -> tuple[str, list, list]:
    """
    Check equity file staleness.

    Returns:
        (status, errors, warnings)
    """
    errors = []
    warnings = []

    if not equity_file.exists():
        errors.append(f"Equity file not found: {equity_file}")
        return 'FAIL', errors, warnings

    try:
        # Read equity value
        with open(equity_file) as f:
            equity = float(f.read().strip())

        # Check if reasonable
        if equity <= 0:
            errors.append(f"Equity must be > 0, got {equity}")
        elif equity > 1_000_000_000:
            warnings.append(f"Equity is very large: ${equity:,.2f}")

        # Check mtime as FYI
        mtime = datetime.fromtimestamp(equity_file.stat().st_mtime)
        age = datetime.now() - mtime
        age_hours = age.total_seconds() / 3600

        if age_hours > critical_hours:
            warnings.append(f"Equity file is {age.days} days old (last modified {age_hours:.1f}h ago)")

        status = 'PASS' if not errors and not warnings else ('FAIL' if errors else 'PASS_WITH_WARNINGS')
        return status, errors, warnings

    except Exception as e:
        errors.append(f"Error reading equity file: {e}")
        return 'FAIL', errors, warnings


def check_rectangular_panel(dataset_path: Path) -> tuple[str, list, list]:
    """
    Check rectangular panel (no NaNs, consistent dates).

    Returns:
        (status, errors, warnings)
    """
    errors = []
    warnings = []

    if not dataset_path.exists():
        warnings.append(f"Dataset not found: {dataset_path} (optional check)")
        return 'PASS_WITH_WARNINGS', errors, warnings

    try:
        df = pd.read_parquet(dataset_path)

        # Check for NaNs
        nan_count = df.isna().sum().sum()
        if nan_count > 0:
            errors.append(f"Dataset contains {nan_count} NaN values")

        # Check date consistency
        close_cols = [c for c in df.columns if c.startswith('close_')]
        date_counts = {col: df[col].notna().sum() for col in close_cols}
        if date_counts:
            min_dates = min(date_counts.values())
            max_dates = max(date_counts.values())
            if min_dates != max_dates:
                warnings.append(
                    f"Inconsistent date counts across instruments "
                    f"(min: {min_dates}, max: {max_dates})"
                )

        status = 'PASS' if not errors and not warnings else ('FAIL' if errors else 'PASS_WITH_WARNINGS')
        return status, errors, warnings

    except Exception as e:
        errors.append(f"Error reading dataset: {e}")
        return 'FAIL', errors, warnings


def format_report(checks: dict, config_path: Path, cadence: str) -> str:
    """Format doctor report."""
    lines = []
    lines.append("=" * 70)
    lines.append("DAILY LIVE OPS DOCTOR REPORT")
    lines.append("=" * 70)
    lines.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}")
    lines.append(f"Config: {config_path}")
    lines.append(f"Cadence: {cadence}")
    lines.append("")

    total_errors = 0
    total_warnings = 0

    for check_name, (status, errors, warnings) in checks.items():
        lines.append(f"{check_name.upper().replace('_', ' ')}")

        symbol = "✓" if status == 'PASS' else ("⚠" if status == 'PASS_WITH_WARNINGS' else "✗")
        lines.append(f"  {symbol} Status: {status}")

        if errors:
            for error in errors:
                lines.append(f"    ✗ {error}")
                total_errors += 1

        if warnings:
            for warning in warnings:
                lines.append(f"    ⚠ {warning}")
                total_warnings += 1

        if not errors and not warnings:
            lines.append(f"    All checks passed")

        lines.append("")

    # Overall status
    if total_errors > 0:
        overall = "FAIL"
        recommendation = "DO NOT PROCEED. Fix critical errors before running advisory."
    elif total_warnings > 0:
        overall = "PASS_WITH_WARNINGS"
        recommendation = "Proceed with caution. Review warnings."
    else:
        overall = "PASS"
        recommendation = "Ready to run daily advisory."

    lines.append("=" * 70)
    lines.append(f"OVERALL STATUS: {overall}")
    lines.append(f"Errors: {total_errors}, Warnings: {total_warnings}")
    lines.append("")
    lines.append(f"Recommendation: {recommendation}")
    lines.append("=" * 70)

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description='Daily Live Ops V1: Doctor CLI - Preflight Health Check',
        formatter_class=argparse.RawDescriptionHelpFormatter
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
        help='Path to actual positions CSV'
    )
    parser.add_argument(
        '--current-equity-file',
        type=Path,
        required=True,
        help='Path to current equity file (live/current_equity.txt)'
    )
    parser.add_argument(
        '--data-status-path',
        type=Path,
        help='Path to data_status.json (optional, will auto-discover if not provided)'
    )
    parser.add_argument(
        '--data-dir',
        type=Path,
        default=Path('data/raw/binance'),
        help='Data directory for auto-discovery (default: data/raw/binance)'
    )
    parser.add_argument(
        '--cadence',
        choices=['monthly', 'daily'],
        default='daily',
        help='Operating cadence (default: daily)'
    )
    parser.add_argument(
        '--output-dir',
        type=Path,
        help='Output directory to check (for manifest, dataset). If not provided, checks will be skipped.'
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
    data_dir = env.resolve_binance_raw_dir(override=args.data_dir)
    output_dir = env.resolve('out', override=args.output_dir)

    logger.info(f"Environment: {env}")
    logger.info(f"Data directory: {data_dir}")
    logger.info(f"Output directory: {output_dir}")

    # Auto-discover data status if not provided
    if not args.data_status_path:
        # For live ops, require V1 format (day-level)
        # Search in: {data_dir.parent} (e.g., data/raw/) and output_dir
        v1_candidates = []
        if (data_dir.parent / 'raw_data_status_v1.json').exists():
            v1_candidates.append(data_dir.parent / 'raw_data_status_v1.json')
        v1_candidates.extend(output_dir.rglob('raw_data_status_v1.json'))

        if v1_candidates:
            args.data_status_path = max(v1_candidates, key=lambda p: p.stat().st_mtime)
            logger.info(f"Auto-discovered V1 data status: {args.data_status_path}")
        else:
            # Don't fall back to V0 - error clearly
            logger.error(
                "Could not find V1 data_status report (raw_data_status_v1.json). "
                "Doctor requires day-level V1 format for live ops. "
                "Run update_data_monthly.py first to generate V1 report."
            )
            sys.exit(2)

    # Run checks
    checks = {}

    # Check 1: Data recency
    status, errors, warnings = check_data_recency(args.data_status_path, args.cadence)
    checks['data_recency'] = (status, errors, warnings)

    # Check 2: Manifest integrity (optional if output_dir provided)
    if output_dir.exists():
        status, errors, warnings = check_manifest_integrity(output_dir)
        checks['manifest_integrity'] = (status, errors, warnings)

    # Check 3: Positions sanity
    status, errors, warnings = check_positions_sanity(
        args.actual_positions,
        args.config,
        args.current_equity_file
    )
    checks['positions_sanity'] = (status, errors, warnings)

    # Check 4: Equity staleness
    status, errors, warnings = check_equity_staleness(args.current_equity_file)
    checks['equity_staleness'] = (status, errors, warnings)

    # Check 5: Rectangular panel (optional if output_dir provided)
    if output_dir.exists():
        dataset_candidates = list(output_dir.glob('dataset_*.parquet'))
        if dataset_candidates:
            latest_dataset = max(dataset_candidates, key=lambda p: p.stat().st_mtime)
            status, errors, warnings = check_rectangular_panel(latest_dataset)
            checks['rectangular_panel'] = (status, errors, warnings)

    # Format and print report
    report = format_report(checks, args.config, args.cadence)
    print(report)

    # Determine exit code
    total_errors = sum(len(errors) for _, errors, _ in checks.values())
    total_warnings = sum(len(warnings) for _, _, warnings in checks.values())

    if total_errors > 0:
        logger.error("DOCTOR CHECK FAILED")
        sys.exit(2)
    elif total_warnings > 0:
        logger.warning("DOCTOR CHECK PASSED WITH WARNINGS")
        sys.exit(1)
    else:
        logger.info("DOCTOR CHECK PASSED")
        sys.exit(0)


if __name__ == '__main__':
    main()
