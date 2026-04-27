#!/usr/bin/env python3
"""
Daily Paper Trading Orchestrator

Fires at 01:00 UTC via launchd. Runs the full advisory pipeline with
circuit-breaker checks, equity tracking, and macOS notifications.

Steps:
    1. Read live/current_equity.txt
    2. Check circuit breaker — abort if triggered
    3. Run update_data_daily.py (Binance klines + funding; VPN check built-in)
    3b. Run download_active_addresses.py + download_market_cap.py (CoinMetrics)
    3c. [optional] Run build_sector_map.py (CoinGecko; ~10 min; monthly only)
    4. Run doctor_live_ops.py — abort on FAIL, flag warnings
    5. Run run_live_advisory.py — backtest + trade plan
    6. Append equity to equity_history.csv
    7. Re-evaluate circuit breaker with new equity
    8. Parse trade plan CSV, count trades + cost
    9. Send macOS notification
   10. Write live/paper_run_latest.log

Usage:
    python scripts/daily_paper_run.py \\
        --config config/crypto_perps_full_rules.yaml \\
        [--dry-run]              # skip data download + backtest
        [--skip-cb-check]        # bypass circuit breaker (testing)
        [--no-notify]            # suppress macOS notification
        [--refresh-sector-map]   # rebuild sector_map.json from CoinGecko (~10 min)
        [--skip-prestage]        # skip macro/CoinMetrics/OI/volume/HL (already done by prestage_daily.py)
"""

import argparse
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).parent.parent

sys.path.insert(0, str(REPO_ROOT))
from sysdata.crypto.circuit_breaker import CircuitBreaker
from sysdata.crypto.config_helpers import (
    extract_candidate_instruments_with_registry,
    instrument_id_to_symbol,
)
from sysdata.crypto.env_paths import LiveOpsEnvironment
from sysdata.crypto.required_data import (
    required_auxiliary_files,
    write_required_data_status,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def read_equity(equity_file: Path) -> float:
    if not equity_file.exists():
        raise FileNotFoundError(
            f"{equity_file} not found.\n"
            "Create it with your current portfolio value, e.g.:\n"
            f"  echo '10000.00' > {equity_file}"
        )
    raw = equity_file.read_text().strip()
    try:
        return float(raw)
    except ValueError:
        raise ValueError(f"Cannot parse equity from {equity_file}: {raw!r}")


def run_subprocess(cmd: list[str], log_lines: list[str]) -> tuple[int, str]:
    """Run a subprocess, capture output, append to log_lines. Return (returncode, combined_output)."""
    log_lines.append(f"\n>>> {' '.join(cmd)}")
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        )
        combined = result.stdout + result.stderr
        log_lines.append(combined)
        return result.returncode, combined
    except Exception as e:
        msg = f"Subprocess error: {e}"
        log_lines.append(msg)
        return 1, msg


def environment_cli_args(args: argparse.Namespace) -> list[str]:
    """Return environment flags for child scripts."""
    if args.env_root is not None:
        return ["--env-root", str(args.env_root)]
    if args.env is not None:
        return ["--env", args.env]
    return []


def candidate_binance_symbols(config_path: str, env_root: Path) -> list[str]:
    """Return Binance symbols for the configured candidate universe."""
    import yaml

    with open(config_path) as f:
        config = yaml.safe_load(f)
    instruments, _ = extract_candidate_instruments_with_registry(config, env_root)
    return sorted({instrument_id_to_symbol(inst) for inst in instruments})


def write_symbol_file(output_dir: Path, symbols: list[str]) -> Path:
    path = output_dir / "oi_symbols.txt"
    path.write_text("\n".join(symbols) + "\n")
    return path


def send_notification(title: str, body: str) -> None:
    """Send macOS notification via osascript."""
    script = f'display notification "{body}" with title "{title}"'
    try:
        subprocess.run(["osascript", "-e", script], check=False)
    except Exception:
        pass  # Notification failure is non-fatal


def parse_trade_plan(output_dir: Path) -> tuple[int, float]:
    """
    Parse trade_plan_*.csv in output_dir.
    Returns (num_trades, total_estimated_cost).
    Returns (0, 0.0) if file not found or unparseable.
    """
    candidates = sorted(output_dir.glob("trade_plan_*.csv"))
    if not candidates:
        return 0, 0.0
    try:
        df = pd.read_csv(candidates[-1])
        # Filter to actionable trades only: non-zero delta, above min size, not buffer-suppressed
        if 'delta_notional' in df.columns:
            df = df[df['delta_notional'].abs() > 1e-6]
        if 'warnings' in df.columns:
            df = df[
                ~df['warnings'].fillna('').str.contains('below_min_trade_size') &
                ~df['warnings'].fillna('').str.contains('buffer_suppressed')
            ]
        num_trades = len(df)
        cost_col = next(
            (c for c in df.columns if "cost" in c.lower() or "fee" in c.lower()),
            None,
        )
        total_cost = float(df[cost_col].sum()) if cost_col else 0.0
        return num_trades, total_cost
    except Exception:
        return 0, 0.0


def get_current_maxdd(cb: CircuitBreaker) -> str:
    """Return current max drawdown as a string, e.g. '-9.4%'."""
    history = cb.get_history_summary(n=9999)
    if history is None or len(history) < 2:
        return "n/a"
    equity = history["equity"].values
    peak = equity.max()
    last = equity[-1]
    dd = (last / peak) - 1.0
    return f"{dd:.1%}"


def resolve_dataset_path(config_path: str) -> Path | None:
    """
    Try to find the dataset path from the config, for sector map rebuild.
    Returns None if not resolvable.
    """
    import yaml
    try:
        with open(config_path) as f:
            cfg = yaml.safe_load(f)
        dataset = cfg.get("parquet_data_path") or cfg.get("dataset_path")
        if dataset:
            p = Path(dataset)
            return p if p.exists() else REPO_ROOT / dataset
    except Exception:
        pass
    # Fallback: look for the jagged dataset
    default = REPO_ROOT / "data" / "dataset_538registry_6yr_jagged.parquet"
    return default if default.exists() else None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Daily paper trading orchestrator")
    parser.add_argument("--config", required=True, help="Backtest config YAML path")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Skip data download and backtest (for testing the pipeline)",
    )
    parser.add_argument(
        "--skip-cb-check",
        action="store_true",
        help="Bypass circuit breaker check (for testing)",
    )
    parser.add_argument(
        "--notify",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Send macOS notification (default: on)",
    )
    parser.add_argument(
        "--refresh-sector-map",
        action="store_true",
        default=False,
        help=(
            "Rebuild data/sector_map.json from CoinGecko API. Takes ~10 minutes. "
            "Run monthly or when the instrument universe changes significantly, "
            "NOT on every daily run."
        ),
    )
    parser.add_argument(
        "--skip-prestage",
        action="store_true",
        default=False,
        help=(
            "Skip steps already completed by prestage_daily.py (macro, CoinMetrics, "
            "Hyperliquid instruments, OI/LSR, volume). Use after running prestage_daily.py "
            "earlier in the day to avoid redundant fetches."
        ),
    )
    parser.add_argument(
        "--env",
        default="dev",
        help=(
            "Environment name (uses envs/<env>/ structure). Defaults to dev, "
            "matching the market-data environment used by this daily dynamic run."
        ),
    )
    parser.add_argument(
        "--env-root",
        type=Path,
        help="Custom environment root (overrides --env).",
    )
    args = parser.parse_args()

    env = LiveOpsEnvironment(
        env=args.env,
        env_root=args.env_root,
        project_root=REPO_ROOT,
    )
    env_args = environment_cli_args(args)
    live_dir = env.resolve("live")
    output_root = env.resolve("out")
    data_dir = env.resolve_binance_raw_dir()
    equity_file = live_dir / "current_equity.txt"
    equity_history = live_dir / "equity_history.csv"
    cb_state = live_dir / "circuit_breaker_state.json"
    log_path = live_dir / "paper_run_latest.log"

    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    today_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    expected_as_of_date = datetime.now(timezone.utc).date() - timedelta(days=1)
    expected_as_of_str = expected_as_of_date.strftime("%Y-%m-%d")
    live_dir.mkdir(parents=True, exist_ok=True)
    output_dir = output_root / f"paper_{today}"
    output_dir.mkdir(parents=True, exist_ok=True)
    env_data_dir = env.env_root / "data"
    env_data_dir.mkdir(parents=True, exist_ok=True)

    log_lines: list[str] = [
        f"=== daily_paper_run.py === {datetime.now(timezone.utc).isoformat()}",
        f"Config: {args.config}",
        f"Dry run: {args.dry_run}",
        f"Refresh sector map: {args.refresh_sector_map}",
        f"Environment: {env}",
        f"Live dir: {live_dir}",
        f"Data dir: {data_dir}",
        f"Aux data dir: {env_data_dir}",
        f"Output dir: {output_dir}",
    ]

    warnings: list[str] = []

    # -----------------------------------------------------------------------
    # Step 1: Read equity
    # -----------------------------------------------------------------------
    # -----------------------------------------------------------------------
    # Step 0: Sync live positions + equity from Hyperliquid (if configured)
    # -----------------------------------------------------------------------
    hl_account_file = env.env_root / "config" / "hl_account.json"
    if args.dry_run:
        log_lines.append("\n[0/10] HL pre-sync: skipped (--dry-run).")
    elif not hl_account_file.exists():
        log_lines.append("\n[0/10] HL pre-sync: skipped (no hl_account.json).")
    else:
        log_lines.append("\n[0/10] Syncing equity + positions from Hyperliquid...")
        pre_sync_cmd = [sys.executable, "scripts/sync_hl_positions.py"]
        pre_sync_cmd.extend(env_args)
        rc, _ = run_subprocess(pre_sync_cmd, log_lines)
        if rc != 0:
            log_lines.append("  WARNING: HL pre-sync failed — using stale equity and positions.")
            warnings.append("HL pre-sync failed — equity and positions may be stale")
        else:
            log_lines.append("  OK.")

    log_lines.append("\n[1/10] Reading current equity...")
    try:
        equity = read_equity(equity_file)
        log_lines.append(f"  Equity: ${equity:,.2f}")
    except Exception as e:
        msg = str(e)
        log_lines.append(f"  ERROR: {msg}")
        log_path.write_text("\n".join(log_lines))
        if args.notify:
            send_notification(
                "⚠️ Paper Run Failed",
                f"Cannot read equity: {equity_file.name}",
            )
        return 1

    # Auto-update notional_trading_capital if leverage_multiple is set in config.
    import re as _re
    import yaml
    with open(args.config) as _f:
        _cfg_text = _f.read()
    _cfg = yaml.safe_load(_cfg_text)
    _lev = _cfg.get("leverage_multiple")
    if _lev:
        _notional = round(equity * _lev, 2)
        _cfg_text = _re.sub(
            r"^(notional_trading_capital:\s*)[\d.]+",
            f"notional_trading_capital: {_notional}",
            _cfg_text, flags=_re.MULTILINE,
        )
        _cfg_text = _re.sub(
            r"^(\s+capital:\s*)[\d.]+",
            lambda m: f"{m.group(1)}{_notional}",
            _cfg_text, flags=_re.MULTILINE,
        )
        with open(args.config, "w") as _f:
            _f.write(_cfg_text)
        log_lines.append(
            f"  leverage_multiple={_lev} → notional_trading_capital=${_notional:,.2f}"
        )

    # -----------------------------------------------------------------------
    # Step 2: Circuit breaker pre-check
    # -----------------------------------------------------------------------
    log_lines.append("\n[2/10] Circuit breaker pre-check...")
    cb = CircuitBreaker(
        equity_history_path=equity_history,
        state_path=cb_state,
    )

    if not args.skip_cb_check:
        cb_triggered, cb_reason = cb.check()
        if cb_triggered:
            log_lines.append(f"  TRIGGERED: {cb_reason}")
            log_path.write_text("\n".join(log_lines))
            if args.notify:
                send_notification(
                    "⚠️ CIRCUIT BREAKER TRIGGERED",
                    f"{cb_reason} — Review {equity_history.name} and reset manually.",
                )
            return 1
        log_lines.append("  Clear.")
    else:
        log_lines.append("  Skipped (--skip-cb-check).")

    # -----------------------------------------------------------------------
    # Step 3: Binance data update
    # -----------------------------------------------------------------------
    log_lines.append("\n[3/10] Updating Binance data (klines + funding)...")
    if args.dry_run:
        log_lines.append("  Skipped (--dry-run).")
    else:
        update_cmd = [
            sys.executable, "scripts/update_data_daily.py",
            "--config", args.config,
            "--scope", "registry_all",  # dynamic universe has no layer_a_instruments
            "--output-report", str(env.resolve("out") / "raw_data_status_v1.json"),
        ]
        update_cmd.extend(env_args)
        rc, output = run_subprocess(update_cmd, log_lines)
        if rc == 3:
            # Exit code 3 = VPN unavailable. Data not updated; doctor preflight will flag
            # staleness. Continue so the trade plan is still generated (with stale data warning).
            log_lines.append("  WARNING: Binance unreachable (exit 3) — data not updated (VPN?). Continuing.")
            warnings.append("Binance data not updated — connect VPN and re-run update manually")
        elif rc != 0:
            log_lines.append(f"  FAILED (exit {rc}) — data not updated")
            log_path.write_text("\n".join(log_lines))
            if args.notify:
                send_notification(
                    "⚠️ Paper Run Failed",
                    f"Binance data update failed (exit {rc}) — check logs",
                )
            return 1
        else:
            log_lines.append("  OK.")

    requirements = required_auxiliary_files(Path(args.config), env.env_root)

    # -----------------------------------------------------------------------
    # Step 3b: Macro factor update (residual momentum)
    # -----------------------------------------------------------------------
    log_lines.append("\n[3b/10] Updating macro factor data...")
    if args.dry_run:
        log_lines.append("  Skipped (--dry-run).")
    elif args.skip_prestage:
        log_lines.append("  Skipped (--skip-prestage).")
    elif "macro_factors" not in requirements:
        log_lines.append("  Skipped (not required by active rules).")
    else:
        macro_cmd = [
            sys.executable,
            "scripts/download_macro_factors.py",
            "--output",
            str(env_data_dir / "macro_factors.parquet"),
        ]
        rc, _ = run_subprocess(macro_cmd, log_lines)
        if rc != 0:
            log_lines.append(
                f"  WARNING: macro factor update failed (exit {rc})"
            )
            warnings.append("Macro factor update failed")
        else:
            log_lines.append("  OK.")

    # -----------------------------------------------------------------------
    # Step 3c: CoinMetrics data update (xs_activity + xs_val signals)
    # -----------------------------------------------------------------------
    log_lines.append("\n[3c/10] Updating CoinMetrics data (active addresses + market cap)...")
    if args.dry_run:
        log_lines.append("  Skipped (--dry-run).")
    elif args.skip_prestage:
        log_lines.append("  Skipped (--skip-prestage).")
    else:
        cm_ok = True
        cm_jobs = []
        if "active_addresses" in requirements:
            cm_jobs.append(
                (
                    "scripts/download_active_addresses.py",
                    env_data_dir / "active_addresses.parquet",
                )
            )
        if "market_cap" in requirements:
            cm_jobs.append(
                ("scripts/download_market_cap.py", env_data_dir / "market_cap.parquet")
            )
        if not cm_jobs:
            log_lines.append("  Skipped (not required by active rules).")
        for script, output_file in cm_jobs:
            cm_cmd = [sys.executable, script, "--output", str(output_file)]
            rc, _ = run_subprocess(cm_cmd, log_lines)
            if rc != 0:
                log_lines.append(f"  WARNING: {script} failed (exit {rc}) — xs_activity/xs_val signals will use stale data")
                warnings.append(f"CoinMetrics update failed: {Path(script).stem}")
                cm_ok = False
        if cm_jobs and cm_ok:
            log_lines.append("  OK.")

    # -----------------------------------------------------------------------
    # Step 3d: Binance OI/LSR data update
    # -----------------------------------------------------------------------
    log_lines.append("\n[3d/10] Updating Binance OI/LSR data...")
    if args.dry_run:
        log_lines.append("  Skipped (--dry-run).")
    elif args.skip_prestage:
        log_lines.append("  Skipped (--skip-prestage).")
    elif "binance_oi_lsr" not in requirements:
        log_lines.append("  Skipped (not required by active rules).")
    else:
        try:
            symbols = candidate_binance_symbols(args.config, env.env_root)
            symbol_file = write_symbol_file(output_dir, symbols)
            oi_raw_dir = env_data_dir / "binance_oi_raw"
            oi_output = env_data_dir / "binance_oi_processed.parquet"
            oi_backfill_days = 2 if oi_output.exists() else 90
            oi_start = (
                expected_as_of_date - timedelta(days=oi_backfill_days)
            ).strftime("%Y-%m-%d")
            oi_cmd = [
                sys.executable,
                "scripts/download_binance_oi_data.py",
                "--start-date",
                oi_start,
                "--end-date",
                expected_as_of_str,
                "--output-dir",
                str(oi_raw_dir),
                "--symbols-file",
                str(symbol_file),
                "--workers",
                "10",
            ]
            rc, _ = run_subprocess(oi_cmd, log_lines)
            if rc != 0:
                log_lines.append(
                    f"  WARNING: OI/LSR raw update failed (exit {rc})"
                )
                warnings.append("Binance OI/LSR update failed")
            else:
                convert_cmd = [
                    sys.executable,
                    "scripts/convert_oi_to_parquet.py",
                    "--input-dir",
                    str(oi_raw_dir),
                    "--output",
                    str(oi_output),
                ]
                rc, _ = run_subprocess(convert_cmd, log_lines)
                if rc != 0:
                    log_lines.append(
                        f"  WARNING: OI/LSR conversion failed (exit {rc})"
                    )
                    warnings.append("Binance OI/LSR conversion failed")
                else:
                    log_lines.append("  OK.")
        except Exception as exc:
            log_lines.append(f"  WARNING: OI/LSR update skipped: {exc}")
            warnings.append("Binance OI/LSR update skipped")

    # -----------------------------------------------------------------------
    # Step 3e_vol: Daily volume update (incremental tail fetch)
    # -----------------------------------------------------------------------
    log_lines.append("\n[3e/10] Updating daily volume data (incremental)...")
    if args.dry_run:
        log_lines.append("  Skipped (--dry-run).")
    elif args.skip_prestage:
        log_lines.append("  Skipped (--skip-prestage).")
    elif "binance_volume" not in requirements:
        log_lines.append("  Skipped (not required by active rules).")
    else:
        vol_cmd = [
            sys.executable,
            "scripts/backfill_volume.py",
            "--incremental",
        ]
        rc, _ = run_subprocess(vol_cmd, log_lines)
        if rc != 0:
            log_lines.append(f"  WARNING: volume update failed (exit {rc}) — volume signals will use stale data")
            warnings.append("Daily volume update failed")
        else:
            log_lines.append("  OK.")

    # -----------------------------------------------------------------------
    # Step 3f: Sector map refresh (monthly / on-demand only)
    # -----------------------------------------------------------------------

    if args.refresh_sector_map:
        log_lines.append("\n[3e/10] Refreshing sector map from CoinGecko (~10 min)...")
        if args.dry_run:
            log_lines.append("  Skipped (--dry-run).")
        else:
            dataset_path = resolve_dataset_path(args.config)
            if dataset_path is None:
                log_lines.append("  WARNING: Could not find dataset parquet — skipping sector map refresh.")
                warnings.append("Sector map refresh skipped: dataset not found")
            else:
                sector_cmd = [
                    sys.executable, "scripts/build_sector_map.py",
                    "--dataset", str(dataset_path),
                    "--output", str(env_data_dir / "sector_map.json"),
                ]
                rc, _ = run_subprocess(sector_cmd, log_lines)
                if rc != 0:
                    log_lines.append(f"  WARNING: Sector map refresh failed (exit {rc})")
                    warnings.append("Sector map refresh failed")
                else:
                    log_lines.append("  OK.")
    else:
        log_lines.append("\n[3e/10] Sector map refresh: skipped (pass --refresh-sector-map to rebuild).")

    # -----------------------------------------------------------------------
    # Step 3f: Hyperliquid instruments refresh
    # -----------------------------------------------------------------------
    log_lines.append("\n[3f/10] Refreshing Hyperliquid instrument list...")
    if args.dry_run:
        log_lines.append("  Skipped (--dry-run).")
    elif args.skip_prestage:
        log_lines.append("  Skipped (--skip-prestage).")
    elif "hyperliquid_instruments" not in requirements:
        log_lines.append("  Skipped (not required by active rules).")
    else:
        hl_cmd = [
            sys.executable,
            "scripts/fetch_hyperliquid_instruments.py",
            "--output",
            str(env_data_dir / "hyperliquid_instruments.json"),
        ]
        rc, _ = run_subprocess(hl_cmd, log_lines)
        if rc != 0:
            log_lines.append(f"  WARNING: HL instrument refresh failed (exit {rc}) — exchange filter will use stale list")
            warnings.append("Hyperliquid instrument list not updated")
        else:
            log_lines.append("  OK.")

    required_status = write_required_data_status(
        Path(args.config),
        env.env_root,
        expected_as_of_date,
        output_dir / "required_data_status.json",
    )
    if required_status["warnings"]:
        log_lines.append("\n[3g/10] Active-rule data status: WARN")
        for warning in required_status["warnings"]:
            log_lines.append(f"  WARNING: {warning}")
        warnings.append("Active-rule data warnings present")
    else:
        log_lines.append("\n[3g/10] Active-rule data status: OK")

    # -----------------------------------------------------------------------
    # Step 3h: HL position sync — already done in step 0.
    # -----------------------------------------------------------------------
    log_lines.append("\n[3h/10] HL position sync: already completed in step 0.")

    # -----------------------------------------------------------------------
    # Step 3i: Patch dataset_as_of_date for base-dataset advisory flow
    # -----------------------------------------------------------------------
    # When run_live_advisory.py uses --base-dataset, the actual dataset is built
    # from the base parquet + API cache delta, not Vision ZIPs. The status file
    # reports min(all_instruments) which pulls back to Jan 2026 for Vision-only
    # instruments. Patch it to reflect only "fetched" (API-cache-fresh) instruments.
    status_path = env.resolve("out") / "raw_data_status_v1.json"
    if not args.dry_run and status_path.exists():
        try:
            import json
            with open(status_path) as _f:
                _status = json.load(_f)
            _fetched_dates = [
                v["last_available_date"]
                for v in _status.get("instruments", {}).values()
                if v.get("staleness_days", 999) == 0 and v.get("last_available_date")
            ]
            if _fetched_dates:
                _effective_date = min(_fetched_dates)
                _status["dataset_as_of_date"] = _effective_date
                with open(status_path, "w") as _f:
                    json.dump(_status, _f, indent=2)
                log_lines.append(f"\n[3i/10] Patched dataset_as_of_date → {_effective_date} (fetched instruments only).")
        except Exception as _e:
            log_lines.append(f"\n[3i/10] WARNING: Could not patch status file: {_e}")

    # -----------------------------------------------------------------------
    # Step 4: Doctor preflight
    # -----------------------------------------------------------------------
    log_lines.append("\n[4/10] Running doctor preflight...")
    if args.dry_run:
        log_lines.append("  Skipped (--dry-run).")
        doctor_rc = 0
    else:
        doctor_cmd = [
            sys.executable, "scripts/doctor_live_ops.py",
            "--config", args.config,
            "--actual-positions", str(live_dir / "current_positions.csv"),
            "--current-equity-file", str(equity_file),
            "--cadence", "daily",
            "--data-dir", str(data_dir),
        ]
        doctor_cmd.extend(env_args)
        doctor_rc, doctor_output = run_subprocess(doctor_cmd, log_lines)

    # Exit codes: 0=PASS, 1=PASS_WITH_WARNINGS, 2=FAIL
    if doctor_rc == 2:
        log_lines.append("  FAIL — aborting.")
        log_path.write_text("\n".join(log_lines))
        if args.notify:
            send_notification(
                "⚠️ Paper Run Aborted",
                f"Doctor preflight FAILED — check {log_path}",
            )
        return 1
    elif doctor_rc == 1:
        log_lines.append("  PASS_WITH_WARNINGS — continuing.")
        warnings.append("Doctor preflight: warnings present (see log for details)")
    else:
        log_lines.append("  PASS.")

    # -----------------------------------------------------------------------
    # Step 5: Run advisory (backtest + trade plan)
    # -----------------------------------------------------------------------
    log_lines.append("\n[5/10] Running advisory (backtest + trade plan)...")
    if args.dry_run:
        log_lines.append("  Skipped (--dry-run).")
    else:
        advisory_cmd = [
            sys.executable, "scripts/run_live_advisory.py",
            "--config", args.config,
            "--actual-positions", str(live_dir / "current_positions.csv"),
            "--current-equity", str(equity),
            "--output-dir", str(output_dir),
            "--cadence", "daily",
            "--skip-data-update",  # already updated in steps 3 and 3b
            "--use-dynamic-universe",  # 1k config uses auto_discover registry
            "--base-dataset", "data/dataset_538registry_6yr_jagged.parquet",
        ]
        advisory_cmd.extend(env_args)
        adv_rc, _ = run_subprocess(advisory_cmd, log_lines)
        if adv_rc != 0:
            log_lines.append(f"  Advisory failed (exit {adv_rc}) — aborting.")
            log_path.write_text("\n".join(log_lines))
            if args.notify:
                send_notification(
                    "⚠️ Paper Run Failed",
                    f"Advisory failed (exit {adv_rc}) — check {log_path}",
                )
            return 1
        else:
            log_lines.append("  OK.")

    # -----------------------------------------------------------------------
    # Step 6: Append equity to history
    # -----------------------------------------------------------------------
    log_lines.append("\n[6/10] Appending equity to history...")
    cb.append_equity(today_iso, equity)
    log_lines.append(f"  Appended {today_iso}: ${equity:,.2f}")

    # -----------------------------------------------------------------------
    # Step 7: Re-evaluate circuit breaker
    # -----------------------------------------------------------------------
    log_lines.append("\n[7/10] Re-evaluating circuit breaker...")
    if not args.skip_cb_check:
        cb_triggered, cb_reason = cb.check()
        if cb_triggered:
            log_lines.append(f"  TRIGGERED: {cb_reason}")
            warnings.append(f"CIRCUIT BREAKER: {cb_reason}")
        else:
            log_lines.append("  Clear.")
    else:
        log_lines.append("  Skipped (--skip-cb-check).")

    # -----------------------------------------------------------------------
    # Step 8: Parse trade plan
    # -----------------------------------------------------------------------
    log_lines.append("\n[8/10] Parsing trade plan...")
    num_trades, total_cost = parse_trade_plan(output_dir)
    log_lines.append(f"  {num_trades} trades • Est cost ${total_cost:.2f}")

    # -----------------------------------------------------------------------
    # Step 9: Notification
    # -----------------------------------------------------------------------
    log_lines.append("\n[9/10] Sending notification...")
    maxdd = get_current_maxdd(cb)

    if args.dry_run:
        title = "📋 Paper Trade Plan (DRY RUN)"
    elif warnings and any("CIRCUIT BREAKER" in w for w in warnings):
        title = "⚠️ CIRCUIT BREAKER TRIGGERED"
    else:
        title = "📋 Paper Trade Plan Ready"

    body_parts = [f"{num_trades} trades • Est cost ${total_cost:.2f} • MaxDD {maxdd}"]
    if warnings:
        body_parts.append(f"{len(warnings)} warning(s) — check live/paper_run_latest.log")

    body = " | ".join(body_parts)
    log_lines.append(f"  Title: {title}")
    log_lines.append(f"  Body:  {body}")

    if args.notify:
        send_notification(title, body)

    # -----------------------------------------------------------------------
    # Write log
    # -----------------------------------------------------------------------
    log_lines.append(f"\n[10/10] Done {datetime.now(timezone.utc).isoformat()}")
    log_path.write_text("\n".join(log_lines))
    print("\n".join(log_lines))

    return 0


if __name__ == "__main__":
    sys.exit(main())
