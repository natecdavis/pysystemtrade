#!/usr/bin/env python3
"""
Generate trade plan by comparing backtest targets to actual positions.

This script is the core of the live advisory system, generating actionable
trade recommendations with risk checks and audit trails.

Usage:
    python scripts/generate_trade_plan.py \
        --backtest-dir out/live_advisory_20260128/backtest_latest \
        --actual-positions live/current_positions.csv \
        --current-equity 5125.50 \
        --as-of-date 2026-01-28 \
        --output-dir out/live_advisory_20260128
"""

import argparse
import sys
from pathlib import Path
import yaml
import json
import logging

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from systems.crypto_perps.trade_plan import generate_trade_plan

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(
        description='Generate trade plan by comparing targets to actual positions',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Generate trade plan (called by run_live_advisory.py)
  %(prog)s \
      --backtest-dir out/live_advisory_20260128/backtest_latest \
      --actual-positions live/current_positions.csv \
      --current-equity 5125.50 \
      --as-of-date 2026-01-28 \
      --output-dir out/live_advisory_20260128

  # Historical replay (for testing)
  %(prog)s \
      --backtest-dir out/live_advisory_20260120/backtest_latest \
      --actual-positions live/positions_2026-01-20.csv \
      --current-equity 5050.25 \
      --as-of-date 2026-01-20 \
      --output-dir out/trade_plans/historical

Notes:
  - as_of_date MUST match last date in backtest (fresh targets only)
  - current_equity should reflect actual P&L, not initial capital
  - Actual positions must have contracts, mark_price_usd, notional_usd, timestamp
  - Trade plan uses current_equity for all calculations (not initial capital)
        """
    )

    parser.add_argument(
        '--backtest-dir',
        type=Path,
        required=True,
        help='Path to FRESH backtest output directory (must contain positions.csv, diagnostics.parquet, metadata.json)'
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
        '--as-of-date',
        type=str,
        required=True,
        help='Evaluation date in YYYY-MM-DD format (MUST match last date in backtest)'
    )
    parser.add_argument(
        '--output-dir',
        type=Path,
        required=True,
        help='Output directory for trade plan and audit files'
    )
    parser.add_argument(
        '--config',
        type=Path,
        help='Optional: path to system config (if not provided, will try to load from backtest metadata)'
    )
    parser.add_argument(
        '--data-status',
        type=Path,
        help='Optional: path to raw_data_status.json (for V1 staleness overlay). If not provided, staleness overlay skipped.'
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
    from sysdata.crypto.env_paths import LiveOpsEnvironment
    env = LiveOpsEnvironment(
        env=args.env if hasattr(args, 'env') else None,
        env_root=args.env_root if hasattr(args, 'env_root') else None
    )

    logger.info(f"Environment: {env}")

    # Validate inputs
    if not args.backtest_dir.exists():
        logger.error(f"Backtest directory not found: {args.backtest_dir}")
        sys.exit(1)

    if not args.actual_positions.exists():
        logger.error(f"Actual positions file not found: {args.actual_positions}")
        logger.error("This file must be manually maintained after trade execution.")
        sys.exit(1)

    # Create output directory
    args.output_dir.mkdir(parents=True, exist_ok=True)

    # Load config
    if args.config:
        config_path = args.config
    else:
        # Try to load from backtest metadata
        metadata_path = args.backtest_dir / 'metadata.json'
        if metadata_path.exists():
            with open(metadata_path) as f:
                metadata = json.load(f)
            config_path = Path(metadata.get('config_path', 'config/crypto_perps_baseline_v1.yaml'))
        else:
            logger.error("No config provided and cannot load from backtest metadata")
            sys.exit(1)

    if not config_path.exists():
        logger.error(f"Config file not found: {config_path}")
        sys.exit(1)

    logger.info(f"Loading config from {config_path}")
    with open(config_path) as f:
        config = yaml.safe_load(f)

    # Generate trade plan
    try:
        logger.info("=" * 60)
        logger.info("GENERATING TRADE PLAN")
        logger.info("=" * 60)

        trade_plan, sanity_checks, audit_bundle = generate_trade_plan(
            args.backtest_dir,
            args.actual_positions,
            args.current_equity,
            args.as_of_date,
            config,
            data_status_path=args.data_status if hasattr(args, 'data_status') else None
        )

        # Write outputs
        trade_plan_path = args.output_dir / f'trade_plan_{args.as_of_date}.csv'
        sanity_checks_path = args.output_dir / f'sanity_checks_{args.as_of_date}.json'
        audit_bundle_path = args.output_dir / f'audit_bundle_{args.as_of_date}.json'

        logger.info(f"Writing trade plan to {trade_plan_path}")
        trade_plan.to_csv(trade_plan_path)

        logger.info(f"Writing sanity checks to {sanity_checks_path}")
        with open(sanity_checks_path, 'w') as f:
            json.dump(sanity_checks, f, indent=2)

        logger.info(f"Writing audit bundle to {audit_bundle_path}")
        with open(audit_bundle_path, 'w') as f:
            json.dump(audit_bundle, f, indent=2)

        # Print summary
        logger.info("\n" + "=" * 60)
        logger.info("TRADE PLAN SUMMARY")
        logger.info("=" * 60)

        total_trades = len(trade_plan)
        trades_above_min = len(trade_plan[trade_plan['warnings'].str.contains('below_min_trade_size') == False])
        total_cost = trade_plan['estimated_cost'].sum()

        logger.info(f"Total trades: {total_trades}")
        logger.info(f"Trades above min size: {trades_above_min}")
        logger.info(f"Total estimated cost: ${total_cost:.2f}")
        logger.info(f"Overall status: {sanity_checks['overall_status']}")

        if sanity_checks['warnings']:
            logger.warning("\nWARNINGS:")
            for warning in sanity_checks['warnings']:
                logger.warning(f"  - {warning}")

        logger.info("\n" + "=" * 60)
        logger.info("✓ Trade plan generation complete")
        logger.info("=" * 60)
        logger.info(f"\nOutputs:")
        logger.info(f"  - Trade plan: {trade_plan_path}")
        logger.info(f"  - Sanity checks: {sanity_checks_path}")
        logger.info(f"  - Audit bundle: {audit_bundle_path}")
        logger.info("")

        # Exit with appropriate status
        if sanity_checks['overall_status'] == 'fail':
            logger.error("Trade plan failed sanity checks - review before executing")
            sys.exit(2)  # Exit code 2 = warnings/failures
        elif sanity_checks['overall_status'] == 'pass_with_warnings':
            logger.warning("Trade plan has warnings - review carefully")
            sys.exit(0)  # Still success, but with warnings
        else:
            sys.exit(0)

    except Exception as e:
        logger.error(f"✗ Trade plan generation failed: {e}", exc_info=True)
        sys.exit(1)


if __name__ == '__main__':
    main()
