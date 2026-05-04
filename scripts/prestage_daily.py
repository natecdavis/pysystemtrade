#!/usr/bin/env python3
"""
Pre-stage daily data that doesn't depend on the midnight UTC Binance close.

Run this any time during the day (e.g., 6pm ET) to front-load slow steps:
  - Macro factors (yfinance: SPX, DXY, US10Y, gold, VIX, oil)
  - CoinMetrics (active addresses, market cap)
  - Hyperliquid instruments list
  - Binance OI/LSR (yesterday's data is final by noon UTC)
  - Volume incremental update (yesterday's kline is final)

The five top-level steps run in a ThreadPoolExecutor — they hit independent endpoints,
the work is I/O-bound (subprocess waits on network), and each step's commands are still
internally sequential where they have to be (e.g., OI download → convert).

After 8pm ET (midnight UTC), run daily_paper_run.py with --skip-prestage to
skip these steps and only fetch today's Binance klines/funding + run backtest.

What waits for 8pm ET (midnight UTC):
  - Binance klines (today's daily candle)
  - Binance funding rates (today's final settlement)
  - Volume tail (today's kline)

Usage:
    python scripts/prestage_daily.py --config config/crypto_perps_1k.yaml --env dev
    python scripts/prestage_daily.py --config config/crypto_perps_1k.yaml --env prod
"""

import argparse
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

import yaml

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

from sysdata.crypto.config_helpers import (
    extract_candidate_instruments_with_registry,
    instrument_id_to_symbol,
)
from sysdata.crypto.env_paths import LiveOpsEnvironment
from sysdata.crypto.required_data import required_auxiliary_files


@dataclass
class StepResult:
    name: str
    rc: int  # 0 = ok, non-zero = failed, -1 = skipped
    log: list[str] = field(default_factory=list)
    warning: str | None = None


def run_subprocess(cmd: list[str], log: list[str]) -> int:
    log.append(f">>> {' '.join(str(c) for c in cmd)}")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(REPO_ROOT))
        log.append(result.stdout + result.stderr)
        return result.returncode
    except Exception as e:
        log.append(f"Subprocess error: {e}")
        return 1


def environment_cli_args(args: argparse.Namespace) -> list[str]:
    if args.env_root is not None:
        return ["--env-root", str(args.env_root)]
    if args.env is not None:
        return ["--env", args.env]
    return []


def candidate_binance_symbols(config_path: str, env_root: Path) -> list[str]:
    with open(config_path) as f:
        config = yaml.safe_load(f)
    instruments, _ = extract_candidate_instruments_with_registry(config, env_root)
    return sorted({instrument_id_to_symbol(inst) for inst in instruments})


# ---------------------------------------------------------------------------
# Step builders. Each returns a closure of zero args that runs the step and
# yields a StepResult. The closure approach makes ThreadPoolExecutor dispatch
# trivial without leaking concurrent state into a shared log list.
# ---------------------------------------------------------------------------


def _macro_step(env_data_dir: Path, requirements: set, dry_run: bool) -> Callable[[], StepResult]:
    def run() -> StepResult:
        log = [f"[1] Macro factors (SPX, DXY, US10Y, gold, VIX, oil)"]
        if dry_run:
            log.append("  Skipped (--dry-run).")
            return StepResult("macro", -1, log)
        if "macro_factors" not in requirements:
            log.append("  Skipped (not required).")
            return StepResult("macro", -1, log)
        rc = run_subprocess(
            [
                sys.executable, "scripts/download_macro_factors.py",
                "--output", str(env_data_dir / "macro_factors.parquet"),
            ],
            log,
        )
        if rc != 0:
            log.append(f"  WARNING: failed (exit {rc})")
            return StepResult("macro", rc, log, warning="Macro factor update failed")
        log.append("  OK.")
        return StepResult("macro", rc, log)

    return run


def _coinmetrics_step(env_data_dir: Path, requirements: set, dry_run: bool) -> Callable[[], StepResult]:
    def run() -> StepResult:
        log = ["[2] CoinMetrics (active addresses + market cap)"]
        if dry_run:
            log.append("  Skipped (--dry-run).")
            return StepResult("coinmetrics", -1, log)
        jobs = []
        if "active_addresses" in requirements:
            jobs.append(("scripts/download_active_addresses.py", env_data_dir / "active_addresses.parquet"))
        if "market_cap" in requirements:
            jobs.append(("scripts/download_market_cap.py", env_data_dir / "market_cap.parquet"))
        if not jobs:
            log.append("  Skipped (not required).")
            return StepResult("coinmetrics", -1, log)
        warning = None
        worst_rc = 0
        for script, out_file in jobs:
            rc = run_subprocess(
                [sys.executable, script, "--output", str(out_file)],
                log,
            )
            if rc != 0:
                warning = f"CoinMetrics update failed: {Path(script).stem}"
                log.append(f"  WARNING: {script} failed (exit {rc})")
                worst_rc = rc
        if warning is None:
            log.append("  OK.")
        return StepResult("coinmetrics", worst_rc, log, warning=warning)

    return run


def _hyperliquid_step(env_data_dir: Path, requirements: set, dry_run: bool) -> Callable[[], StepResult]:
    def run() -> StepResult:
        log = ["[3] Hyperliquid instrument list"]
        if dry_run:
            log.append("  Skipped (--dry-run).")
            return StepResult("hyperliquid", -1, log)
        if "hyperliquid_instruments" not in requirements:
            log.append("  Skipped (not required).")
            return StepResult("hyperliquid", -1, log)
        rc = run_subprocess(
            [
                sys.executable, "scripts/fetch_hyperliquid_instruments.py",
                "--output", str(env_data_dir / "hyperliquid_instruments.json"),
            ],
            log,
        )
        if rc != 0:
            log.append(f"  WARNING: failed (exit {rc})")
            return StepResult("hyperliquid", rc, log, warning="Hyperliquid instrument refresh failed")
        log.append("  OK.")
        return StepResult("hyperliquid", rc, log)

    return run


def _oi_lsr_step(
    config_path: str,
    env_root: Path,
    env_data_dir: Path,
    output_dir: Path,
    requirements: set,
    yesterday: "datetime.date",
    yesterday_str: str,
    dry_run: bool,
) -> Callable[[], StepResult]:
    def run() -> StepResult:
        log = [f"[4] Binance OI/LSR (up to {yesterday_str})"]
        if dry_run:
            log.append("  Skipped (--dry-run).")
            return StepResult("oi_lsr", -1, log)
        if "binance_oi_lsr" not in requirements:
            log.append("  Skipped (not required).")
            return StepResult("oi_lsr", -1, log)
        try:
            symbols = candidate_binance_symbols(config_path, env_root)
            symbol_file = output_dir / "prestage_oi_symbols.txt"
            symbol_file.parent.mkdir(parents=True, exist_ok=True)
            symbol_file.write_text("\n".join(symbols) + "\n")

            oi_raw_dir = env_data_dir / "binance_oi_raw"
            oi_output = env_data_dir / "binance_oi_processed.parquet"
            oi_start = (yesterday - timedelta(days=2)).strftime("%Y-%m-%d")

            rc = run_subprocess(
                [
                    sys.executable, "scripts/download_binance_oi_data.py",
                    "--start-date", oi_start,
                    "--end-date", yesterday_str,
                    "--output-dir", str(oi_raw_dir),
                    "--symbols-file", str(symbol_file),
                    "--workers", "10",
                ],
                log,
            )
            if rc != 0:
                log.append(f"  WARNING: download failed (exit {rc})")
                return StepResult("oi_lsr", rc, log, warning="OI/LSR raw update failed")

            rc2 = run_subprocess(
                [
                    sys.executable, "scripts/convert_oi_to_parquet.py",
                    "--input-dir", str(oi_raw_dir),
                    "--output", str(oi_output),
                ],
                log,
            )
            if rc2 != 0:
                log.append(f"  WARNING: conversion failed (exit {rc2})")
                return StepResult("oi_lsr", rc2, log, warning="OI/LSR conversion failed")
            log.append("  OK.")
            return StepResult("oi_lsr", 0, log)
        except Exception as exc:
            log.append(f"  WARNING: {exc}")
            return StepResult("oi_lsr", 1, log, warning=f"OI/LSR update skipped: {exc}")

    return run


def _volume_step(requirements: set, dry_run: bool) -> Callable[[], StepResult]:
    def run() -> StepResult:
        log = ["[5] Volume incremental update"]
        if dry_run:
            log.append("  Skipped (--dry-run).")
            return StepResult("volume", -1, log)
        if "binance_volume" not in requirements:
            log.append("  Skipped (not required).")
            return StepResult("volume", -1, log)
        rc = run_subprocess(
            [sys.executable, "scripts/backfill_volume.py", "--incremental"],
            log,
        )
        if rc != 0:
            log.append(f"  WARNING: failed (exit {rc})")
            return StepResult("volume", rc, log, warning="Volume update failed")
        log.append("  OK.")
        return StepResult("volume", rc, log)

    return run


def _sb_dataset_rebuild_step(dry_run: bool) -> Callable[[], StepResult]:
    """Rebuild the SB-corrected research dataset when base or graveyard inputs change."""
    def run() -> StepResult:
        log = ["[6] SB-corrected dataset auto-rebuild"]
        if dry_run:
            log.append("  Skipped (--dry-run).")
            return StepResult("sb_dataset", -1, log)
        rc = run_subprocess(
            [sys.executable, "scripts/auto_rebuild_sb_dataset.py"],
            log,
        )
        if rc != 0:
            log.append(f"  WARNING: failed (exit {rc})")
            return StepResult("sb_dataset", rc, log, warning="SB dataset rebuild failed")
        log.append("  OK.")
        return StepResult("sb_dataset", rc, log)

    return run


def _stablecoin_supply_step(dry_run: bool) -> Callable[[], StepResult]:
    """Fetch total stablecoin supply (DefiLlama) for C2b stablecoin_supply_trend rule."""
    def run() -> StepResult:
        log = ["[7] Stablecoin supply (DefiLlama)"]
        if dry_run:
            log.append("  Skipped (--dry-run).")
            return StepResult("stablecoin_supply", -1, log)
        rc = run_subprocess(
            [sys.executable, "scripts/download_stablecoin_supply.py"],
            log,
        )
        if rc != 0:
            log.append(f"  WARNING: failed (exit {rc})")
            return StepResult("stablecoin_supply", rc, log, warning="Stablecoin supply update failed")
        log.append("  OK.")
        return StepResult("stablecoin_supply", rc, log)

    return run


def _etf_flows_step(dry_run: bool) -> Callable[[], StepResult]:
    """Fetch BTC/ETH spot-ETF activity (yfinance) for C2a btc_etf_flow_trend rule."""
    def run() -> StepResult:
        log = ["[8] ETF flows (yfinance IBIT/ETHA)"]
        if dry_run:
            log.append("  Skipped (--dry-run).")
            return StepResult("etf_flows", -1, log)
        rc = run_subprocess(
            [sys.executable, "scripts/download_etf_flows.py"],
            log,
        )
        if rc != 0:
            log.append(f"  WARNING: failed (exit {rc})")
            return StepResult("etf_flows", rc, log, warning="ETF flows update failed")
        log.append("  OK.")
        return StepResult("etf_flows", rc, log)

    return run


def _premium_index_step(
    config_path: str,
    env_root: Path,
    env_data_dir: Path,
    output_dir: Path,
    yesterday: "datetime.date",
    yesterday_str: str,
    dry_run: bool,
) -> Callable[[], StepResult]:
    """Incremental Binance premium-index fetch + convert (C2c basis_mr rule)."""
    def run() -> StepResult:
        log = [f"[9] Binance premium-index (basis) → {yesterday_str}"]
        if dry_run:
            log.append("  Skipped (--dry-run).")
            return StepResult("premium_index", -1, log)
        try:
            symbols = candidate_binance_symbols(config_path, env_root)
            symbol_file = output_dir / "prestage_premium_symbols.txt"
            symbol_file.parent.mkdir(parents=True, exist_ok=True)
            symbol_file.write_text("\n".join(symbols) + "\n")

            raw_dir = env_data_dir / "binance_premium_index_raw"
            output_path = env_data_dir / "binance_premium_index_processed.parquet"
            # Incremental: only the last 3 days. Vision publishes daily at end-of-day UTC.
            start = (yesterday - timedelta(days=2)).strftime("%Y-%m-%d")

            rc = run_subprocess(
                [
                    sys.executable, "scripts/download_binance_premium_index.py",
                    "--start-date", start,
                    "--end-date", yesterday_str,
                    "--output-dir", str(raw_dir),
                    "--symbols-file", str(symbol_file),
                    "--workers", "10",
                ],
                log,
            )
            if rc != 0:
                log.append(f"  WARNING: download failed (exit {rc})")
                return StepResult("premium_index", rc, log, warning="Premium-index download failed")

            rc2 = run_subprocess(
                [
                    sys.executable, "scripts/convert_premium_index_to_parquet.py",
                    "--input-dir", str(raw_dir),
                    "--output", str(output_path),
                ],
                log,
            )
            if rc2 != 0:
                log.append(f"  WARNING: conversion failed (exit {rc2})")
                return StepResult("premium_index", rc2, log, warning="Premium-index conversion failed")
            log.append("  OK.")
            return StepResult("premium_index", 0, log)
        except Exception as exc:
            log.append(f"  WARNING: {exc}")
            return StepResult("premium_index", 1, log, warning=f"Premium-index step crashed: {exc}")

    return run


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="Pre-stage non-Binance-close data before 8pm ET")
    parser.add_argument("--config", required=True)
    parser.add_argument("--env", default="dev")
    parser.add_argument("--env-root", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--max-workers",
        type=int,
        default=4,
        help="Thread pool size for parallel data fetches (default: 4).",
    )
    args = parser.parse_args()

    env = LiveOpsEnvironment(env=args.env, env_root=args.env_root, project_root=REPO_ROOT)
    env_data_dir = env.env_root / "data"
    output_dir = env.resolve("out")
    env_data_dir.mkdir(parents=True, exist_ok=True)

    today_utc = datetime.now(timezone.utc).date()
    yesterday = today_utc - timedelta(days=1)
    yesterday_str = yesterday.strftime("%Y-%m-%d")

    requirements = required_auxiliary_files(Path(args.config), env.env_root)

    print(f"=== prestage_daily.py === {datetime.now(timezone.utc).isoformat()}")
    print(f"Config: {args.config} | Env: {env}")
    print(f"Workers: {args.max_workers} (parallel data fetches)")

    # Build the step closures. Steps stay independent — each runs its own subprocess
    # chain; the only sharing is via the parent env (filesystem paths), which is fine.
    step_runners: list[Callable[[], StepResult]] = [
        _macro_step(env_data_dir, requirements, args.dry_run),
        _coinmetrics_step(env_data_dir, requirements, args.dry_run),
        _hyperliquid_step(env_data_dir, requirements, args.dry_run),
        _oi_lsr_step(args.config, env.env_root, env_data_dir, output_dir, requirements, yesterday, yesterday_str, args.dry_run),
        _volume_step(requirements, args.dry_run),
        _sb_dataset_rebuild_step(args.dry_run),
        _stablecoin_supply_step(args.dry_run),
        _etf_flows_step(args.dry_run),
        _premium_index_step(args.config, env.env_root, env_data_dir, output_dir, yesterday, yesterday_str, args.dry_run),
    ]

    results: list[StepResult] = [None] * len(step_runners)  # preserve order in output
    started = datetime.now(timezone.utc)

    with ThreadPoolExecutor(max_workers=args.max_workers) as pool:
        future_to_idx = {pool.submit(runner): i for i, runner in enumerate(step_runners)}
        for fut in as_completed(future_to_idx):
            idx = future_to_idx[fut]
            try:
                results[idx] = fut.result()
            except Exception as exc:
                results[idx] = StepResult(
                    name=f"step_{idx}",
                    rc=1,
                    log=[f"[step {idx}] EXCEPTION: {exc}"],
                    warning=f"step_{idx} crashed: {exc}",
                )

    elapsed = (datetime.now(timezone.utc) - started).total_seconds()
    warnings: list[str] = []

    # Print logs in step-order, not completion-order, for readability.
    for r in results:
        print()
        print("\n".join(r.log))
        if r.warning:
            warnings.append(r.warning)

    print()
    print("=" * 60)
    if warnings:
        print(f"PRESTAGE COMPLETE in {elapsed:.1f}s with {len(warnings)} warning(s):")
        for w in warnings:
            print(f"  - {w}")
    else:
        print(f"PRESTAGE COMPLETE in {elapsed:.1f}s — all steps OK.")
    print("=" * 60)

    return 0 if not warnings else 1


if __name__ == "__main__":
    sys.exit(main())
