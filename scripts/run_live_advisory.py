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
from sysdata.crypto.required_data import (
    required_auxiliary_files,
    write_required_data_status,
)

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def run_command(
    cmd: list, description: str, check: bool = True
) -> subprocess.CompletedProcess:
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
        result = subprocess.run(cmd, check=check, capture_output=True, text=True)

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


def extract_candidate_instruments(config_path: Path, env_root: Path) -> tuple:
    """
    Extract candidate instruments with registry support.

    Returns:
        (candidate_ids, source_description)
    """
    from sysdata.crypto.config_helpers import (
        extract_candidate_instruments_with_registry,
    )

    with open(config_path) as f:
        config = yaml.safe_load(f)

    candidate_ids, source = extract_candidate_instruments_with_registry(
        config, env_root
    )
    logger.info(f"Using {len(candidate_ids)} candidates from: {source}")
    return candidate_ids, source


def existing_aux_args(config_path: Path, env_root: Path) -> list[str]:
    """Return explicit dynamic-backtest aux data args for files that exist."""
    requirements = required_auxiliary_files(config_path, env_root)
    arg_map = {
        "macro_factors": "--macro-data",
        "binance_oi_lsr": "--oi-data",
        "sector_map": "--sector-map",
        "active_addresses": "--active-addresses-data",
        "market_cap": "--market-cap-data",
        "hyperliquid_instruments": "--hl-instruments",
        "binance_volume": "--volume-data",
    }
    args: list[str] = []
    for key, flag in arg_map.items():
        req = requirements.get(key)
        if not req:
            continue
        path = req.get("path")
        if path is not None and Path(path).exists():
            args.extend([flag, str(path)])
    return args


def write_oi_symbols_file(output_dir: Path, universe: list[str]) -> Path:
    """Write Binance symbols for the OI downloader and return the file path."""
    from sysdata.crypto.config_helpers import instrument_id_to_symbol

    symbols = sorted({instrument_id_to_symbol(inst) for inst in universe})
    path = output_dir / "oi_symbols.txt"
    path.write_text("\n".join(symbols) + "\n")
    return path


def refresh_active_rule_aux_data(
    config_path: Path,
    env_root: Path,
    output_dir: Path,
    universe: list[str],
    expected_date: str,
    tail_days: int,
    dry_run: bool,
) -> None:
    """
    Best-effort refresh for non-Binance-panel data required by active rules.

    Failures are warnings by design; required_data_status.json records whether
    each source is present and fresh enough.
    """
    requirements = required_auxiliary_files(config_path, env_root)
    data_dir = env_root / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    def run_best_effort(cmd: list[str], description: str) -> None:
        if dry_run:
            logger.info(f"Dry run: would run {' '.join(cmd)}")
            return
        result = run_command(cmd, description, check=False)
        if result.returncode != 0:
            logger.warning(
                f"{description} failed with exit {result.returncode}; "
                "continuing with existing data if available"
            )

    if "macro_factors" in requirements:
        run_best_effort(
            [
                sys.executable,
                "scripts/download_macro_factors.py",
                "--output",
                str(data_dir / "macro_factors.parquet"),
            ],
            "Refresh macro factors",
        )

    if "active_addresses" in requirements:
        run_best_effort(
            [
                sys.executable,
                "scripts/download_active_addresses.py",
                "--output",
                str(data_dir / "active_addresses.parquet"),
            ],
            "Refresh CoinMetrics active addresses",
        )

    if "market_cap" in requirements:
        run_best_effort(
            [
                sys.executable,
                "scripts/download_market_cap.py",
                "--output",
                str(data_dir / "market_cap.parquet"),
            ],
            "Refresh CoinMetrics market cap",
        )

    if "binance_oi_lsr" in requirements and universe:
        symbol_file = write_oi_symbols_file(output_dir, universe)
        oi_raw_dir = data_dir / "binance_oi_raw"
        oi_output = data_dir / "binance_oi_processed.parquet"
        oi_backfill_days = tail_days - 1 if oi_output.exists() else 90
        oi_start = (
            datetime.strptime(expected_date, "%Y-%m-%d")
            - timedelta(days=max(oi_backfill_days, 0))
        ).strftime("%Y-%m-%d")
        run_best_effort(
            [
                sys.executable,
                "scripts/download_binance_oi_data.py",
                "--start-date",
                oi_start,
                "--end-date",
                expected_date,
                "--output-dir",
                str(oi_raw_dir),
                "--symbols-file",
                str(symbol_file),
            ],
            "Refresh Binance OI/LSR raw data",
        )
        run_best_effort(
            [
                sys.executable,
                "scripts/convert_oi_to_parquet.py",
                "--input-dir",
                str(oi_raw_dir),
                "--output",
                str(oi_output),
            ],
            "Convert Binance OI/LSR parquet",
        )

    if "hyperliquid_instruments" in requirements:
        run_best_effort(
            [
                sys.executable,
                "scripts/fetch_hyperliquid_instruments.py",
                "--output",
                str(data_dir / "hyperliquid_instruments.json"),
            ],
            "Refresh Hyperliquid instrument list",
        )


def refresh_registry_opportunistic(env_root: Path) -> tuple:
    """
    Refresh registry with best-effort + cache fallback.

    This is called during advisory workflow to keep the registry fresh.
    If CoinGecko API fails, falls back to cached registry.

    Returns:
        (success: bool, registry_hash: Optional[str], changelog: dict)
    """
    import hashlib

    registry_path = env_root / "data/raw/metadata/discovered_candidate_instruments.json"

    try:
        # Attempt refresh (CoinGecko API call)
        logger.info("Refreshing registry from CoinGecko...")

        # Import here to avoid circular dependency
        sys.path.insert(0, str(Path(__file__).parent))
        from refresh_binance_market_registry import run_refresh

        changelog = run_refresh(env_root, verbose=False, dry_run=False)

        # Compute hash of refreshed registry
        if registry_path.exists():
            with open(registry_path, "rb") as f:
                registry_hash = hashlib.sha256(f.read()).hexdigest()[:12]

            logger.info(f"✓ Registry refreshed (hash: {registry_hash})")
            return True, registry_hash, changelog
        else:
            logger.error("Registry refresh succeeded but file not found")
            return False, None, changelog

    except Exception as e:
        # Fallback to cached registry
        logger.warning(f"Registry refresh failed: {e}")

        if registry_path.exists():
            with open(registry_path, "rb") as f:
                registry_hash = hashlib.sha256(f.read()).hexdigest()[:12]

            logger.info(f"✓ Using cached registry (hash: {registry_hash})")

            # Return empty changelog for cache fallback
            changelog = {
                "cached": True,
                "error": str(e),
                "timestamp": datetime.utcnow().isoformat(),
            }
            return False, registry_hash, changelog
        else:
            raise RuntimeError("No cached registry available and refresh failed") from e


def main():
    parser = argparse.ArgumentParser(
        description="Live Operations Advisory System - Monthly Advisory Workflow",
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
  - Trade plan shows delta_weight relative to current_equity (display only)
  - Position sizing uses notional_trading_capital from config (not current_equity)
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
        """,
    )

    parser.add_argument(
        "--config",
        type=Path,
        required=True,
        help="Path to system config (e.g., config/crypto_perps_baseline_v1.yaml)",
    )
    parser.add_argument(
        "--actual-positions",
        type=Path,
        required=True,
        help="Path to actual positions CSV (columns: instrument, hl_symbol, contracts, timestamp[, notes])",
    )
    parser.add_argument(
        "--current-equity",
        type=float,
        required=True,
        help="Current account equity in USD (should reflect actual P&L, not initial capital)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="Output directory for all advisory outputs",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        help="Root data directory. Default: data/raw/binance (or env-aware path if --env used)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Dry run mode (skip data download, use existing data for all steps)",
    )

    # Environment isolation
    env_group = parser.add_argument_group("Environment settings")
    env_group.add_argument(
        "--env",
        help="Environment name (uses envs/<env>/ structure). Examples: prod, dev, paper, exp1. Default: current directory",
    )
    env_group.add_argument(
        "--env-root",
        type=Path,
        help="Custom environment root (overrides --env). Can also use LIVE_OPS_ENV_ROOT env var",
    )
    parser.add_argument(
        "--skip-data-update",
        action="store_true",
        help="Skip Binance raw price/funding update step (still refresh active-rule auxiliary data)",
    )
    parser.add_argument(
        "--skip-aux-data-update",
        action="store_true",
        help="Skip active-rule auxiliary data refreshes (macro, OI/LSR, CoinMetrics, Hyperliquid)",
    )
    parser.add_argument(
        "--skip-dataset-rebuild",
        action="store_true",
        help="Skip dataset rebuild (use existing dataset from previous run). Requires --use-dynamic-universe.",
    )
    parser.add_argument(
        "--skip-report",
        action="store_true",
        help="Skip advisory report generation (only generate trade plan)",
    )
    parser.add_argument(
        "--cadence",
        choices=["monthly", "daily"],
        default="monthly",
        help="Data update cadence: monthly (V0, M-2 lag) or daily (V1, D-1 lag). Default: monthly",
    )
    parser.add_argument(
        "--tail-days",
        type=int,
        default=3,
        help="For daily cadence: number of recent days to fetch via API (default: 3)",
    )
    parser.add_argument(
        "--expected-date",
        type=str,
        help="Override expected as_of_date (YYYY-MM-DD). For testing only. Default: yesterday UTC. "
        "Disables cutover time warnings when specified.",
    )
    parser.add_argument(
        "--use-dynamic-universe",
        action="store_true",
        help="Use dynamic universe with parquet-backed adapter (pysystemtrade framework). "
        "If not specified, uses research_v1 system (custom implementation).",
    )
    parser.add_argument(
        "--prev-snapshot",
        type=Path,
        help="Override stable pointer lookup: explicit path to previous run's universe_snapshot.json.",
    )
    parser.add_argument(
        "--base-dataset",
        type=Path,
        default=None,
        help=(
            "Path to a reference parquet dataset (e.g. data/dataset_538registry_6yr_jagged.parquet). "
            "When provided, the advisory extends this dataset with recent API cache data instead of "
            "rebuilding from scratch. Preserves full historical depth for accurate Sharpe estimation."
        ),
    )

    args = parser.parse_args()

    # Initialize environment resolver
    env = LiveOpsEnvironment(
        env=args.env if hasattr(args, "env") else None,
        env_root=args.env_root if hasattr(args, "env_root") else None,
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
        logger.error(
            "This file must be manually maintained. See live/README.md for details."
        )
        sys.exit(1)

    # Create output directory
    output_dir.mkdir(parents=True, exist_ok=True)
    logger.info(f"Output directory resolved: {output_dir}")

    # Opportunistic registry refresh (Phase 2)
    # This ensures the registry is fresh when using auto_discover
    registry_metadata = None
    if args.use_dynamic_universe:
        # Check if config uses auto_discover
        with open(args.config) as f:
            config = yaml.safe_load(f)

        if config.get("data_acquisition", {}).get("auto_discover", False):
            logger.info("Config has auto_discover=true, refreshing registry...")
            try:
                (
                    refresh_success,
                    registry_hash,
                    changelog,
                ) = refresh_registry_opportunistic(env.env_root)

                registry_metadata = {
                    "hash": registry_hash,
                    "refresh_success": refresh_success,
                    "timestamp": datetime.utcnow().isoformat(),
                    "changelog": changelog,
                }

                if refresh_success:
                    if changelog.get("new_instruments"):
                        logger.info(
                            f"  Registry updated: {len(changelog['new_instruments'])} new instruments"
                        )
                    if changelog.get("delisted_instruments"):
                        logger.info(
                            f"  Registry updated: {len(changelog['delisted_instruments'])} delisted instruments"
                        )
                else:
                    logger.info(f"  Using cached registry (refresh failed)")

            except Exception as e:
                logger.warning(f"Registry refresh skipped: {e}")
                logger.warning("Continuing with existing registry if available")
                registry_metadata = {
                    "refresh_skipped": True,
                    "error": str(e),
                    "timestamp": datetime.utcnow().isoformat(),
                }

    # Extract candidates for dataset building
    # For dynamic universe, use candidates from config/registry (will be filtered by cost thresholds at backtest time)
    # For static universe, use explicit tradable instruments
    if args.use_dynamic_universe:
        # For dynamic universe, we want to build dataset with ALL candidate instruments
        # The dynamic universe logic will filter based on cost thresholds
        candidates, source = extract_candidate_instruments(args.config, env.env_root)
        logger.info(
            f"Dynamic universe mode: building dataset with {len(candidates)} candidates"
        )
        logger.info(f"  Source: {source}")
        logger.info(f"  (actual tradable universe will be determined by cost filters)")
        universe = candidates
    else:
        # Static universe: use instruments from config (backward compat)
        with open(args.config) as f:
            config = yaml.safe_load(f)
        # Support both legacy layer_a_instruments and newer candidate_instruments
        universe = (
            config.get("data_acquisition", {}).get("candidate_instruments", [])
            or config.get("universe", {}).get("instruments", [])
            or config.get("universe", {}).get("layer_a_instruments", [])
        )
        logger.info(f"Static universe mode: {len(universe)} instruments")

    # Handle expected_as_of_date - SINGLE SOURCE OF TRUTH for all date computations
    if args.expected_date:
        # Override: parse and use for ALL date computations
        expected_as_of_date = datetime.strptime(args.expected_date, "%Y-%m-%d").date()
        logger.info(f"Using override expected_as_of_date: {expected_as_of_date}")
        logger.info(f"  (disables cutover time warnings)")
    elif args.cadence == "daily":
        # Default for daily: yesterday UTC with cutover time warnings
        from sysdata.crypto.data_status import get_expected_as_of_date

        expected_as_of_date = get_expected_as_of_date(
            override_date=None, warn_if_early=True, warn_if_late=True
        )
    else:
        # Default for monthly: yesterday UTC (no cutover warnings)
        expected_as_of_date = datetime.utcnow().date() - timedelta(days=1)

    logger.info(f"Expected as_of_date: {expected_as_of_date}")

    # Compute start_date/end_date FROM expected_as_of_date (single source of truth)
    # Conservative: use expected_as_of_date as end date (not "today")
    end_date = expected_as_of_date.strftime("%Y-%m-%d")

    # 2 years is sufficient: longest signal (EWMAC 64/256) needs ~512 days to stabilise,
    # adv_window=252 needs 252 days. 730 days gives ~1.5× buffer over the longest lookback.
    # Research backtests use the full 6-year dataset separately.
    start_date = (expected_as_of_date - timedelta(days=2 * 365)).strftime("%Y-%m-%d")

    logger.info(f"Dataset window: {start_date} to {end_date}")

    try:
        # STEP 1: Update raw data
        if args.skip_data_update:
            logger.info("Skipping data update (--skip-data-update specified)")
        else:
            if args.cadence == "monthly":
                # V0 workflow: monthly batch only
                update_cmd = [
                    sys.executable,
                    "scripts/update_data_monthly.py",
                    "--config",
                    str(args.config),
                    "--data-dir",
                    str(data_dir),
                    "--output-report",
                    str(output_dir / "raw_data_status.json"),
                ]

                # Pass expected-date if provided (for historical-live testing)
                if args.expected_date:
                    update_cmd.extend(["--expected-date", args.expected_date])

                if args.dry_run:
                    update_cmd.append("--dry-run")

                run_command(update_cmd, "Update raw data (monthly batch)")

            else:  # daily cadence
                # V1 workflow: monthly base + daily tail
                # First, run monthly update to ensure base data current
                monthly_update_cmd = [
                    sys.executable,
                    "scripts/update_data_monthly.py",
                    "--config",
                    str(args.config),
                    "--data-dir",
                    str(data_dir),
                    "--output-report",
                    str(output_dir / "raw_data_status_monthly.json"),
                ]

                if args.dry_run:
                    monthly_update_cmd.append("--dry-run")

                run_command(
                    monthly_update_cmd, "Update base data (monthly Vision ZIPs)"
                )

                # Then, fetch recent tail via API
                daily_update_cmd = [
                    sys.executable,
                    "scripts/update_data_daily.py",
                    "--config",
                    str(args.config),
                    "--data-dir",
                    str(data_dir),
                    "--tail-days",
                    str(args.tail_days),
                    "--output-report",
                    str(output_dir / "raw_data_status.json"),
                ]

                if args.dry_run:
                    daily_update_cmd.append("--dry-run")

                run_command(daily_update_cmd, "Update recent tail (daily via API)")

        if args.use_dynamic_universe:
            if args.skip_aux_data_update:
                logger.info(
                    "Skipping active-rule auxiliary refresh "
                    "(--skip-aux-data-update specified)"
                )
            else:
                refresh_active_rule_aux_data(
                    config_path=args.config,
                    env_root=env.env_root,
                    output_dir=output_dir,
                    universe=universe,
                    expected_date=end_date,
                    tail_days=args.tail_days,
                    dry_run=args.dry_run,
                )

            required_status = write_required_data_status(
                args.config,
                env.env_root,
                expected_as_of_date,
                output_dir / "required_data_status.json",
            )
            if required_status["warnings"]:
                logger.warning("Active-rule data warnings:")
                for warning in required_status["warnings"]:
                    logger.warning(f"  - {warning}")
            else:
                logger.info("Active-rule auxiliary data status: OK")

        # STEP 2: Rebuild processed dataset (or reuse existing)
        dataset_path = output_dir / "dataset_latest.parquet"
        build_log_path = output_dir / "dataset_build_log.txt"

        if args.skip_dataset_rebuild:
            if not args.use_dynamic_universe:
                logger.error("--skip-dataset-rebuild requires --use-dynamic-universe")
                sys.exit(1)

            if dataset_path.exists():
                logger.info(
                    f"Skipping dataset rebuild (--skip-dataset-rebuild specified)"
                )
                logger.info(f"Using existing dataset: {dataset_path}")
            else:
                logger.error(f"Dataset not found: {dataset_path}")
                logger.error("Cannot skip rebuild when dataset doesn't exist")
                sys.exit(1)
        elif args.base_dataset is not None:
            # Extend reference dataset with recent API cache data instead of full rebuild.
            # Preserves full historical depth (e.g. 6yr) while keeping signals current.
            import pandas as _pd
            base_path = args.base_dataset
            if not base_path.exists():
                logger.error(f"--base-dataset not found: {base_path}")
                sys.exit(1)
            logger.info(f"Loading base dataset: {base_path}")
            base_df = _pd.read_parquet(base_path)
            base_end = _pd.to_datetime(base_df["date"]).max()
            delta_start = (base_end + _pd.Timedelta(days=1)).strftime("%Y-%m-%d")
            logger.info(f"Base dataset ends {base_end.date()}; fetching delta {delta_start} → {end_date}")

            if delta_start <= end_date:
                delta_path = dataset_path.with_suffix(".delta.parquet")
                build_cmd = [
                    sys.executable,
                    "scripts/build_example_dataset.py",
                    "--source", "real",
                    "--data-dir", str(data_dir),
                    "--start-date", delta_start,
                    "--end-date", end_date,
                    "--instruments", *universe,
                    "--output-path", str(delta_path),
                    "--allow-jagged",
                    "--min-coverage", "0.0",
                    "--min-history-days", "1",
                    "--include-api-cache",
                ]
                logger.info(f"Building delta dataset {delta_start} → {end_date}")
                result = run_command(build_cmd, "Build delta dataset (API cache)")

                delta_df = _pd.read_parquet(delta_path)
                combined = _pd.concat([base_df, delta_df], ignore_index=True)
                combined = combined.drop_duplicates(subset=["instrument", "date"], keep="last")
                combined = combined.sort_values(["instrument", "date"]).reset_index(drop=True)
                combined.to_parquet(dataset_path, index=False)
                delta_path.unlink(missing_ok=True)
                logger.info(
                    f"Extended dataset: {base_df['instrument'].nunique()} base instruments, "
                    f"{delta_df['instrument'].nunique()} delta instruments → "
                    f"{combined['instrument'].nunique()} combined. "
                    f"Date range: {_pd.to_datetime(combined['date']).min().date()} → "
                    f"{_pd.to_datetime(combined['date']).max().date()}"
                )
                with open(build_log_path, "w") as f:
                    f.write(
                        f"Dataset Build Log\n==================\n\n"
                        f"Mode: base + delta\nBase: {base_path}\n"
                        f"Delta: {delta_start} → {end_date}\nOutput: {dataset_path}\n\n"
                        f"Command:\n{' '.join(build_cmd)}\n\nOutput:\n{result.stdout}\n"
                    )
                    if result.stderr:
                        f.write(f"\nWarnings/Errors:\n{result.stderr}\n")
            else:
                import shutil as _shutil
                logger.info(f"Base dataset already current ({base_end.date()}); copying to output")
                _shutil.copy2(base_path, dataset_path)
        else:
            build_cmd = [
                sys.executable,
                "scripts/build_example_dataset.py",
                "--source",
                "real",
                "--data-dir",
                str(data_dir),
                "--start-date",
                start_date,
                "--end-date",
                end_date,
                "--instruments",
                *universe,
                "--output-path",
                str(dataset_path),
                "--allow-jagged",
                "--min-coverage",
                "0.50",
            ]

            # Add V1 flags for daily cadence
            if args.cadence == "daily":
                build_cmd.append("--include-api-cache")

            # Run and capture output to log file
            logger.info(f"Building dataset from {start_date} to {end_date}")
            result = run_command(build_cmd, "Rebuild processed dataset")

            # Write build log
            with open(build_log_path, "w") as f:
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

        # Stage 1: record the dataset hash. Subsequent stages (backtest, trade-plan)
        # verify their inputs against this entry so a half-written or out-of-band-replaced
        # dataset cannot silently feed downstream.
        from sysdata.crypto.manifest_chain import CHAIN_FILENAME, append_stage
        chain_path = output_dir / CHAIN_FILENAME
        append_stage(
            chain_path,
            stage="dataset_build",
            outputs={"dataset": dataset_path},
            extra={"config": str(args.config)},
        )

        # STEP 3: Run backtest
        backtest_dir = output_dir / "backtest_latest"

        if args.use_dynamic_universe:
            # Use dynamic universe backtest with parquet adapter
            backtest_cmd = [
                sys.executable,
                "scripts/run_dynamic_universe_backtest.py",
                "--config",
                str(args.config),
                "--data",
                str(dataset_path),
                "--outdir",
                str(backtest_dir),
            ]
            backtest_cmd.extend(existing_aux_args(args.config, env.env_root))
            run_command(backtest_cmd, "Run dynamic universe backtest (parquet-backed)")
        else:
            # Use research_v1 backtest (custom implementation)
            backtest_cmd = [
                sys.executable,
                "-m",
                "systems.crypto_perps.system",
                "--config",
                str(args.config),
                "--data",
                str(dataset_path),
                "--outdir",
                str(backtest_dir),
            ]
            run_command(backtest_cmd, "Run research_v1 backtest for fresh targets")

        # Verify backtest outputs
        required_outputs = ["positions.csv", "diagnostics.parquet", "metadata.json"]
        for output in required_outputs:
            output_path = backtest_dir / output
            if not output_path.exists():
                raise FileNotFoundError(f"Backtest output not found: {output_path}")

        # Extract as_of_date (last date in backtest)
        import pandas as pd

        positions = pd.read_csv(
            backtest_dir / "positions.csv", index_col=0, parse_dates=True
        )
        as_of_date = positions.index[-1].strftime("%Y-%m-%d")
        logger.info(f"Backtest as_of_date: {as_of_date}")

        # STEP 4: Generate trade plan

        # Resolve stable snapshot pointer (for reduce-only computation)
        # The pointer file stores the absolute path to the previous run's snapshot.
        prev_snapshot_path = None
        if args.use_dynamic_universe:
            # --prev-snapshot overrides the pointer file lookup
            if hasattr(args, "prev_snapshot") and args.prev_snapshot:
                if args.prev_snapshot.exists():
                    prev_snapshot_path = str(args.prev_snapshot)
                    logger.info(
                        f"Using explicit previous snapshot: {prev_snapshot_path}"
                    )
                else:
                    logger.warning(
                        f"--prev-snapshot path not found: {args.prev_snapshot}"
                    )
            else:
                pointer_path = env.env_root / "live" / "latest_snapshot_path.txt"
                if pointer_path.exists():
                    prev_snapshot_path = pointer_path.read_text().strip()
                    if prev_snapshot_path and not Path(prev_snapshot_path).exists():
                        logger.warning(
                            f"Previous snapshot pointer exists but file not found: {prev_snapshot_path}"
                        )
                        prev_snapshot_path = None
                    else:
                        logger.info(f"Previous universe snapshot: {prev_snapshot_path}")
                else:
                    logger.info(
                        "No previous universe snapshot pointer found "
                        "(first run or pointer not written yet — reduce-only skipped)"
                    )

        trade_plan_cmd = [
            sys.executable,
            "scripts/generate_trade_plan.py",
            "--backtest-dir",
            str(backtest_dir),
            "--actual-positions",
            str(actual_positions),
            "--current-equity",
            str(args.current_equity),
            "--as-of-date",
            as_of_date,
            "--output-dir",
            str(output_dir),
            "--config",
            str(args.config),
        ]

        # Add data status for staleness overlay and API staleness hard exits
        data_status_path = output_dir / "raw_data_status.json"
        if data_status_path.exists():
            trade_plan_cmd.extend(["--data-status", str(data_status_path)])
        elif args.cadence == "daily":
            logger.warning(
                f"Data status file not found: {data_status_path}. Staleness overlay skipped."
            )

        # Add universe snapshot args (dynamic universe mode only)
        if args.use_dynamic_universe:
            new_snapshot_path = backtest_dir / "universe_snapshot.json"
            if new_snapshot_path.exists():
                trade_plan_cmd.extend(["--universe-snapshot", str(new_snapshot_path)])
            else:
                logger.warning(
                    f"Universe snapshot not found at {new_snapshot_path}. "
                    "Universe validation skipped."
                )

            if prev_snapshot_path:
                trade_plan_cmd.extend(["--prev-universe-snapshot", prev_snapshot_path])

            # Registry changelog for delisting hard exits
            if registry_metadata:
                # Write changelog to a file that generate_trade_plan.py can read
                changelog_path = output_dir / "registry_changelog.json"
                with open(changelog_path, "w") as f:
                    json.dump(registry_metadata.get("changelog", {}), f, indent=2)
                trade_plan_cmd.extend(["--registry-changelog", str(changelog_path)])

        run_command(trade_plan_cmd, "Generate trade plan (target vs actual deltas)")

        # Update stable snapshot pointer after successful trade plan generation
        if args.use_dynamic_universe:
            new_snapshot_path = backtest_dir / "universe_snapshot.json"
            if new_snapshot_path.exists():
                pointer_path = env.env_root / "live" / "latest_snapshot_path.txt"
                pointer_path.parent.mkdir(parents=True, exist_ok=True)
                pointer_path.write_text(str(new_snapshot_path))
                logger.info(f"Universe pointer updated → {new_snapshot_path}")

        # Write advisory metadata (includes registry snapshot)
        advisory_metadata = {
            "workflow": "live_advisory",
            "version": "1.0",
            "timestamp": datetime.utcnow().isoformat(),
            "config": str(args.config),
            "mode": "dynamic_universe"
            if args.use_dynamic_universe
            else "static_universe",
            "cadence": args.cadence,
            "as_of_date": as_of_date,
            "candidate_count": len(universe),
            "environment": str(env.env_root) if hasattr(env, "root") else "unknown",
        }

        # Add registry snapshot if available
        if registry_metadata:
            advisory_metadata["registry_snapshot"] = registry_metadata

        metadata_path = output_dir / f"advisory_metadata_{as_of_date}.json"
        with open(metadata_path, "w") as f:
            json.dump(advisory_metadata, f, indent=2)
        logger.info(f"✓ Wrote advisory metadata: {metadata_path}")

        # STEP 5: Generate advisory report (optional)
        if args.skip_report:
            logger.info("Skipping advisory report (--skip-report specified)")
        else:
            # Check if report script exists
            report_script = Path("reports/advisory_report.py")
            if report_script.exists():
                report_cmd = [
                    sys.executable,
                    str(report_script),
                    "--advisory-dir",
                    str(output_dir),
                    "--output",
                    str(output_dir / "advisory_report.txt"),
                ]
                run_command(report_cmd, "Generate advisory report", check=False)
            else:
                logger.info("Advisory report script not found - skipping (optional)")

        # SUCCESS
        logger.info("\n" + "=" * 70)
        logger.info("✓ LIVE ADVISORY WORKFLOW COMPLETED SUCCESSFULLY")
        logger.info("=" * 70)
        logger.info(
            f"\nMode: {'Dynamic Universe' if args.use_dynamic_universe else 'Static Universe (research_v1)'}"
        )
        logger.info(f"Output directory: {output_dir}")
        logger.info(f"\nGenerated files:")
        logger.info(f"  - raw_data_status.json (data freshness)")
        logger.info(f"  - dataset_latest.parquet (processed dataset)")
        logger.info(f"  - dataset_build_log.txt (build log)")
        logger.info(f"  - backtest_latest/ (fresh backtest outputs)")
        logger.info(f"  - trade_plan_{as_of_date}.csv (actionable trades)")
        logger.info(f"  - sanity_checks_{as_of_date}.json (risk validation)")
        logger.info(f"  - audit_bundle_{as_of_date}.json (full provenance)")
        logger.info(
            f"  - advisory_metadata_{as_of_date}.json (workflow metadata + registry snapshot)"
        )
        if not args.skip_report and (output_dir / "advisory_report.txt").exists():
            logger.info(f"  - advisory_report.txt (human-readable summary)")

        # Log dynamic universe stats if available
        if args.use_dynamic_universe:
            metadata_path = backtest_dir / "metadata.json"
            if metadata_path.exists():
                with open(metadata_path) as f:
                    metadata = json.load(f)
                du_stats = metadata.get("dynamic_universe_stats", {})
                if du_stats:
                    logger.info(f"\nDynamic Universe Stats:")
                    logger.info(
                        f"  Active instruments: min={du_stats['min_active']}, max={du_stats['max_active']}, avg={du_stats['avg_active']:.1f}"
                    )
                    logger.info(
                        f"  vs candidate universe of {len(universe)} instruments"
                    )

            # Log universe transitions from snapshot
            snapshot_path = backtest_dir / "universe_snapshot.json"
            if snapshot_path.exists():
                with open(snapshot_path) as f:
                    snapshot = json.load(f)
                entrants = snapshot.get("entrants", [])
                exits = snapshot.get("exits", [])
                count = snapshot.get("count", "?")
                logger.info(
                    f"\nUniverse: {len(entrants)} entrants, {len(exits)} exits, {count} active"
                )
                if entrants:
                    logger.info(f"  Entrants: {entrants}")
                if exits:
                    logger.info(f"  Exits: {exits}")

        logger.info(f"\nNext steps:")
        logger.info(
            f"  1. Review trade plan: {output_dir / f'trade_plan_{as_of_date}.csv'}"
        )
        logger.info(f"  2. Verify live prices on exchange")
        logger.info(f"  3. Execute trades manually")
        logger.info(f"  4. Update live/current_positions.csv with actual fills")
        logger.info(f"  5. Update live/current_equity.txt with actual P&L")
        logger.info("")

        sys.exit(0)

    except subprocess.CalledProcessError as e:
        logger.error(
            f"\n✗ Workflow failed at step: {e.cmd[1] if len(e.cmd) > 1 else 'unknown'}"
        )
        logger.error(f"Exit code: {e.returncode}")
        sys.exit(1)

    except Exception as e:
        logger.error(f"\n✗ Workflow failed: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
