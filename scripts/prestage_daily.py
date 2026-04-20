#!/usr/bin/env python3
"""
Pre-stage daily data that doesn't depend on the midnight UTC Binance close.

Run this any time during the day (e.g., 6pm ET) to front-load slow steps:
  - Macro factors (yfinance: SPX, DXY, US10Y, gold, VIX, oil)
  - CoinMetrics (active addresses, market cap)
  - Hyperliquid instruments list
  - Binance OI/LSR (yesterday's data is final by noon UTC)
  - Volume incremental update (yesterday's kline is final)

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
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

from sysdata.crypto.config_helpers import (
    extract_candidate_instruments_with_registry,
    instrument_id_to_symbol,
)
from sysdata.crypto.env_paths import LiveOpsEnvironment
from sysdata.crypto.required_data import required_auxiliary_files


def run_subprocess(cmd: list[str], log_lines: list[str]) -> tuple[int, str]:
    log_lines.append(f"\n>>> {' '.join(str(c) for c in cmd)}")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(REPO_ROOT))
        combined = result.stdout + result.stderr
        log_lines.append(combined)
        return result.returncode, combined
    except Exception as e:
        msg = f"Subprocess error: {e}"
        log_lines.append(msg)
        return 1, msg


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


def main() -> int:
    parser = argparse.ArgumentParser(description="Pre-stage non-Binance-close data before 8pm ET")
    parser.add_argument("--config", required=True)
    parser.add_argument("--env", default="dev")
    parser.add_argument("--env-root", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    env = LiveOpsEnvironment(env=args.env, env_root=args.env_root, project_root=REPO_ROOT)
    env_args = environment_cli_args(args)
    env_data_dir = env.env_root / "data"
    output_dir = env.resolve("out")
    env_data_dir.mkdir(parents=True, exist_ok=True)

    today_utc = datetime.now(timezone.utc).date()
    yesterday = today_utc - timedelta(days=1)
    yesterday_str = yesterday.strftime("%Y-%m-%d")

    requirements = required_auxiliary_files(Path(args.config), env.env_root)

    log_lines: list[str] = [
        f"=== prestage_daily.py === {datetime.now(timezone.utc).isoformat()}",
        f"Config: {args.config} | Env: {env}",
    ]
    warnings: list[str] = []

    # ------------------------------------------------------------------
    # 1. Macro factors (yfinance — any time)
    # ------------------------------------------------------------------
    log_lines.append("\n[1] Macro factors (SPX, DXY, US10Y, gold, VIX, oil)...")
    if args.dry_run:
        log_lines.append("  Skipped (--dry-run).")
    elif "macro_factors" not in requirements:
        log_lines.append("  Skipped (not required).")
    else:
        rc, _ = run_subprocess([
            sys.executable, "scripts/download_macro_factors.py",
            "--output", str(env_data_dir / "macro_factors.parquet"),
        ], log_lines)
        if rc != 0:
            warnings.append("Macro factor update failed")
            log_lines.append(f"  WARNING: failed (exit {rc})")
        else:
            log_lines.append("  OK.")

    # ------------------------------------------------------------------
    # 2. CoinMetrics (active addresses + market cap — any time)
    # ------------------------------------------------------------------
    log_lines.append("\n[2] CoinMetrics (active addresses + market cap)...")
    if args.dry_run:
        log_lines.append("  Skipped (--dry-run).")
    else:
        cm_jobs = []
        if "active_addresses" in requirements:
            cm_jobs.append(("scripts/download_active_addresses.py", env_data_dir / "active_addresses.parquet"))
        if "market_cap" in requirements:
            cm_jobs.append(("scripts/download_market_cap.py", env_data_dir / "market_cap.parquet"))
        if not cm_jobs:
            log_lines.append("  Skipped (not required).")
        for script, out_file in cm_jobs:
            rc, _ = run_subprocess([sys.executable, script, "--output", str(out_file)], log_lines)
            if rc != 0:
                warnings.append(f"CoinMetrics update failed: {Path(script).stem}")
                log_lines.append(f"  WARNING: {script} failed (exit {rc})")
        if cm_jobs and not warnings:
            log_lines.append("  OK.")

    # ------------------------------------------------------------------
    # 3. Hyperliquid instruments (any time)
    # ------------------------------------------------------------------
    log_lines.append("\n[3] Hyperliquid instrument list...")
    if args.dry_run:
        log_lines.append("  Skipped (--dry-run).")
    elif "hyperliquid_instruments" not in requirements:
        log_lines.append("  Skipped (not required).")
    else:
        rc, _ = run_subprocess([
            sys.executable, "scripts/fetch_hyperliquid_instruments.py",
            "--output", str(env_data_dir / "hyperliquid_instruments.json"),
        ], log_lines)
        if rc != 0:
            warnings.append("Hyperliquid instrument refresh failed")
            log_lines.append(f"  WARNING: failed (exit {rc})")
        else:
            log_lines.append("  OK.")

    # ------------------------------------------------------------------
    # 4. Binance OI/LSR for yesterday (final by noon UTC — safe any time)
    # ------------------------------------------------------------------
    log_lines.append(f"\n[4] Binance OI/LSR (up to {yesterday_str})...")
    if args.dry_run:
        log_lines.append("  Skipped (--dry-run).")
    elif "binance_oi_lsr" not in requirements:
        log_lines.append("  Skipped (not required).")
    else:
        try:
            symbols = candidate_binance_symbols(args.config, env.env_root)
            symbol_file = output_dir / "prestage_oi_symbols.txt"
            symbol_file.parent.mkdir(parents=True, exist_ok=True)
            symbol_file.write_text("\n".join(symbols) + "\n")

            oi_raw_dir = env_data_dir / "binance_oi_raw"
            oi_output = env_data_dir / "binance_oi_processed.parquet"
            oi_start = (yesterday - timedelta(days=2)).strftime("%Y-%m-%d")

            rc, _ = run_subprocess([
                sys.executable, "scripts/download_binance_oi_data.py",
                "--start-date", oi_start,
                "--end-date", yesterday_str,
                "--output-dir", str(oi_raw_dir),
                "--symbols-file", str(symbol_file),
                "--workers", "10",
            ], log_lines)
            if rc != 0:
                warnings.append("OI/LSR raw update failed")
                log_lines.append(f"  WARNING: download failed (exit {rc})")
            else:
                rc2, _ = run_subprocess([
                    sys.executable, "scripts/convert_oi_to_parquet.py",
                    "--input-dir", str(oi_raw_dir),
                    "--output", str(oi_output),
                ], log_lines)
                if rc2 != 0:
                    warnings.append("OI/LSR conversion failed")
                    log_lines.append(f"  WARNING: conversion failed (exit {rc2})")
                else:
                    log_lines.append("  OK.")
        except Exception as exc:
            warnings.append(f"OI/LSR update skipped: {exc}")
            log_lines.append(f"  WARNING: {exc}")

    # ------------------------------------------------------------------
    # 5. Volume incremental (yesterday's kline is final)
    # ------------------------------------------------------------------
    log_lines.append("\n[5] Volume incremental update...")
    if args.dry_run:
        log_lines.append("  Skipped (--dry-run).")
    elif "binance_volume" not in requirements:
        log_lines.append("  Skipped (not required).")
    else:
        rc, _ = run_subprocess([
            sys.executable, "scripts/backfill_volume.py", "--incremental",
        ], log_lines)
        if rc != 0:
            warnings.append("Volume update failed")
            log_lines.append(f"  WARNING: failed (exit {rc})")
        else:
            log_lines.append("  OK.")

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    log_lines.append(f"\n{'='*60}")
    if warnings:
        log_lines.append(f"PRESTAGE COMPLETE with {len(warnings)} warning(s):")
        for w in warnings:
            log_lines.append(f"  - {w}")
    else:
        log_lines.append("PRESTAGE COMPLETE — all steps OK.")
    log_lines.append(f"{'='*60}")

    print("\n".join(log_lines))
    return 0 if not warnings else 1


if __name__ == "__main__":
    sys.exit(main())
