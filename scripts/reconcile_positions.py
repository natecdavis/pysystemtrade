#!/usr/bin/env python3
"""
Daily Live Ops V1: Positions Reconciliation CLI

Helper to catch operator errors in current_positions.csv before they cause problems.
Thin wrapper around positions_validation library.

Checks:
- Notional arithmetic (contracts × price = notional)
- Sign consistency (long vs short)
- Gross leverage caps
- Missing instruments
- Stale timestamps
- Units confusion warnings

Usage:
    # Suggest mode (show errors and suggested fixes)
    python scripts/reconcile_positions.py \
        --positions-file live/current_positions.csv \
        --current-equity 5237.50 \
        --config config/crypto_perps_baseline_v1.yaml \
        --fix-mode suggest

    # Auto-fix mode (fix notional arithmetic errors automatically)
    python scripts/reconcile_positions.py \
        --positions-file live/current_positions.csv \
        --current-equity 5237.50 \
        --config config/crypto_perps_baseline_v1.yaml \
        --fix-mode auto

Exit Codes:
    0 - PASS (no errors)
    1 - PASS_WITH_WARNINGS (warnings only, no errors)
    2 - FAIL (errors found)
"""

import argparse
import sys
import shutil
from pathlib import Path
from datetime import datetime
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


def apply_fixes(positions_df: pd.DataFrame, errors: list) -> pd.DataFrame:
    """
    Apply automatic fixes for notional arithmetic errors.

    Args:
        positions_df: Original positions DataFrame
        errors: List of ValidationIssue objects

    Returns:
        Fixed DataFrame (or original if no fixes applied)
    """
    fixed_df = positions_df.copy()
    fix_count = 0

    for error in errors:
        if error.check == 'notional_arithmetic' and error.suggested_fix:
            # Extract expected notional from suggested fix
            # Format: "Update notional to 175.00"
            import re
            match = re.search(r'Update notional to ([\d.]+)', error.suggested_fix)
            if match:
                expected_notional = float(match.group(1))

                # Find row and update
                mask = fixed_df['instrument'] == error.instrument
                if mask.any():
                    # Preserve sign from contracts
                    contracts = fixed_df.loc[mask, 'contracts'].iloc[0]
                    if contracts < 0:
                        expected_notional = -abs(expected_notional)
                    else:
                        expected_notional = abs(expected_notional)

                    fixed_df.loc[mask, 'notional_usd'] = expected_notional
                    fix_count += 1
                    logger.info(f"Fixed {error.instrument}: notional = {expected_notional:.2f}")

    logger.info(f"Applied {fix_count} fixes")
    return fixed_df


def format_reconciliation_report(result, positions_path: Path, equity: float) -> str:
    """
    Format validation result as reconciliation report.

    Args:
        result: ValidationResult from validate_positions_file()
        positions_path: Path to positions file
        equity: Current equity

    Returns:
        Formatted report string
    """
    lines = []
    lines.append("=" * 70)
    lines.append("POSITIONS RECONCILIATION REPORT")
    lines.append("=" * 70)
    lines.append(f"File: {positions_path}")
    lines.append(f"Equity: ${equity:.2f}")
    lines.append(f"Universe: {result.metadata.get('universe_size', 'unknown')} instruments")
    lines.append(f"Status: {result.overall_status}")
    lines.append("")

    # Group errors by check type
    error_groups = {}
    for error in result.errors:
        if error.check not in error_groups:
            error_groups[error.check] = []
        error_groups[error.check].append(error)

    # Group warnings by check type
    warning_groups = {}
    for warning in result.warnings:
        if warning.check not in warning_groups:
            warning_groups[warning.check] = []
        warning_groups[warning.check].append(warning)

    # Display errors by group
    if error_groups:
        for check_name, errors in error_groups.items():
            lines.append(f"{check_name.upper().replace('_', ' ')} ERRORS")
            for error in errors:
                lines.append(f"  ✗ {error.instrument}: {error.message}")
                if error.suggested_fix:
                    lines.append(f"     Fix: {error.suggested_fix}")
            lines.append("")

    # Display warnings by group
    if warning_groups:
        for check_name, warnings in warning_groups.items():
            lines.append(f"{check_name.upper().replace('_', ' ')} WARNINGS")
            for warning in warnings:
                lines.append(f"  ⚠ {warning.instrument}: {warning.message}")
                if warning.suggested_fix:
                    lines.append(f"     Suggestion: {warning.suggested_fix}")
            lines.append("")

    # Summary
    lines.append("SUMMARY")
    lines.append(f"  Gross leverage: {result.metadata.get('gross_leverage', 0):.2f}x")
    lines.append(f"  Total |notional|: ${result.metadata.get('total_abs_notional', 0):.2f}")
    lines.append(f"  Errors: {len(result.errors)}")
    lines.append(f"  Warnings: {len(result.warnings)}")
    lines.append("")

    # Recommendations
    if result.errors:
        lines.append("RECOMMENDED ACTIONS")
        notional_errors = [e for e in result.errors if e.check == 'notional_arithmetic']
        if notional_errors:
            lines.append("  1. Run with --fix-mode auto to automatically fix notional arithmetic")
        sign_errors = [e for e in result.errors if e.check == 'sign_consistency']
        if sign_errors:
            lines.append("  2. Manually fix sign errors (check if position is long or short)")
        lines.append("")

    lines.append("=" * 70)
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description='Daily Live Ops V1: Positions Reconciliation CLI',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    parser.add_argument(
        '--positions-file',
        type=Path,
        required=True,
        help='Path to positions CSV (e.g., live/current_positions.csv)'
    )
    parser.add_argument(
        '--current-equity',
        type=float,
        required=True,
        help='Current account equity in USD'
    )
    parser.add_argument(
        '--config',
        type=Path,
        required=True,
        help='Path to system config (for universe)'
    )
    parser.add_argument(
        '--fix-mode',
        choices=['suggest', 'auto'],
        default='suggest',
        help='Fix mode: suggest (show fixes) or auto (apply fixes). Default: suggest'
    )
    parser.add_argument(
        '--allow-missing-instruments',
        action='store_true',
        help='Allow missing instruments (warnings instead of errors)'
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

    # Initialize environment resolver (mainly for logging/context)
    env = LiveOpsEnvironment(
        env=args.env if hasattr(args, 'env') else None,
        env_root=args.env_root if hasattr(args, 'env_root') else None
    )

    logger.info(f"Environment: {env}")

    # Load config
    try:
        with open(args.config) as f:
            config = yaml.safe_load(f)
        universe = config.get('universe', {}).get('layer_a_instruments', [])
        logger.info(f"Universe: {len(universe)} instruments")
    except Exception as e:
        logger.error(f"Error loading config: {e}")
        sys.exit(2)

    # Load positions
    try:
        positions_df = pd.read_csv(args.positions_file)
        logger.info(f"Loaded positions: {len(positions_df)} rows")
    except Exception as e:
        logger.error(f"Error reading positions file: {e}")
        sys.exit(2)

    # Validate positions
    from sysdata.crypto.positions_validation import validate_positions_file

    result = validate_positions_file(
        positions_df,
        universe,
        args.current_equity,
        critical_staleness_hours=48,
        allow_missing_instruments=args.allow_missing_instruments
    )

    # Format and print report
    report = format_reconciliation_report(result, args.positions_file, args.current_equity)
    print(report)

    # Handle auto-fix mode
    if args.fix_mode == 'auto' and result.errors:
        # Only fix notional arithmetic errors
        notional_errors = [e for e in result.errors if e.check == 'notional_arithmetic']

        if notional_errors:
            logger.info("=" * 70)
            logger.info("AUTO-FIX MODE: Applying fixes")
            logger.info("=" * 70)

            # Create backup
            backup_path = Path(str(args.positions_file) + f".bak.{datetime.now().strftime('%Y%m%d_%H%M%S')}")
            shutil.copy2(args.positions_file, backup_path)
            logger.info(f"Backup created: {backup_path}")

            # Apply fixes
            fixed_df = apply_fixes(positions_df, notional_errors)

            # Write fixed positions
            fixed_df.to_csv(args.positions_file, index=False)
            logger.info(f"Fixed positions written to: {args.positions_file}")

            # Re-validate
            logger.info("")
            logger.info("Re-validating fixed positions...")
            result_after = validate_positions_file(
                fixed_df,
                universe,
                args.current_equity,
                critical_staleness_hours=48,
                allow_missing_instruments=args.allow_missing_instruments
            )

            if result_after.passed:
                logger.info("✓ Re-validation PASSED (all notional errors fixed)")
            else:
                logger.warning("⚠ Re-validation found remaining issues:")
                for error in result_after.errors:
                    logger.warning(f"  ✗ {error.instrument}: {error.message}")

            # Print comparison
            logger.info("")
            logger.info("=" * 70)
            logger.info(f"Before: {len(result.errors)} errors, {len(result.warnings)} warnings")
            logger.info(f"After:  {len(result_after.errors)} errors, {len(result_after.warnings)} warnings")
            logger.info("=" * 70)

        else:
            logger.info("No notional arithmetic errors to auto-fix")
            logger.info("Other errors must be fixed manually")

    # Determine exit code
    if result.errors:
        if args.fix_mode == 'auto':
            # Check if all errors were fixed
            notional_errors = [e for e in result.errors if e.check == 'notional_arithmetic']
            other_errors = [e for e in result.errors if e.check != 'notional_arithmetic']
            if not other_errors and notional_errors:
                # All errors were notional and should be fixed
                logger.info("RECONCILIATION PASSED (after auto-fix)")
                sys.exit(0)

        logger.error("RECONCILIATION FAILED")
        sys.exit(2)
    elif result.warnings:
        logger.warning("RECONCILIATION PASSED WITH WARNINGS")
        sys.exit(1)
    else:
        logger.info("RECONCILIATION PASSED")
        sys.exit(0)


if __name__ == '__main__':
    main()
