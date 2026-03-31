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
"""

import argparse
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).parent.parent
LIVE_DIR = REPO_ROOT / "live"
EQUITY_FILE = LIVE_DIR / "current_equity.txt"
EQUITY_HISTORY = LIVE_DIR / "equity_history.csv"
CB_STATE = LIVE_DIR / "circuit_breaker_state.json"
LOG_PATH = LIVE_DIR / "paper_run_latest.log"

sys.path.insert(0, str(REPO_ROOT))
from sysdata.crypto.circuit_breaker import CircuitBreaker


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def read_equity() -> float:
    if not EQUITY_FILE.exists():
        raise FileNotFoundError(
            f"{EQUITY_FILE} not found.\n"
            "Create it with your current portfolio value, e.g.:\n"
            "  echo '10000.00' > live/current_equity.txt"
        )
    raw = EQUITY_FILE.read_text().strip()
    try:
        return float(raw)
    except ValueError:
        raise ValueError(f"Cannot parse equity from {EQUITY_FILE}: {raw!r}")


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
        "--ignore-warnings",
        action="store_true",
        default=False,
        help=(
            "Continue pipeline even if doctor preflight returns PASS_WITH_WARNINGS (exit 1). "
            "Default: halt on warnings. Use only after manually reviewing warnings."
        ),
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
    args = parser.parse_args()

    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    today_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    output_dir = REPO_ROOT / "out" / f"paper_{today}"
    output_dir.mkdir(parents=True, exist_ok=True)

    log_lines: list[str] = [
        f"=== daily_paper_run.py === {datetime.now(timezone.utc).isoformat()}",
        f"Config: {args.config}",
        f"Dry run: {args.dry_run}",
        f"Refresh sector map: {args.refresh_sector_map}",
        f"Output dir: {output_dir}",
    ]

    warnings: list[str] = []

    # -----------------------------------------------------------------------
    # Step 1: Read equity
    # -----------------------------------------------------------------------
    log_lines.append("\n[1/10] Reading current equity...")
    try:
        equity = read_equity()
        log_lines.append(f"  Equity: ${equity:,.2f}")
    except Exception as e:
        msg = str(e)
        log_lines.append(f"  ERROR: {msg}")
        LOG_PATH.write_text("\n".join(log_lines))
        if args.notify:
            send_notification("⚠️ Paper Run Failed", f"Cannot read equity: {EQUITY_FILE.name}")
        return 1

    # -----------------------------------------------------------------------
    # Step 2: Circuit breaker pre-check
    # -----------------------------------------------------------------------
    log_lines.append("\n[2/10] Circuit breaker pre-check...")
    cb = CircuitBreaker(
        equity_history_path=EQUITY_HISTORY,
        state_path=CB_STATE,
    )

    if not args.skip_cb_check:
        cb_triggered, cb_reason = cb.check()
        if cb_triggered:
            log_lines.append(f"  TRIGGERED: {cb_reason}")
            LOG_PATH.write_text("\n".join(log_lines))
            if args.notify:
                send_notification(
                    "⚠️ CIRCUIT BREAKER TRIGGERED",
                    f"{cb_reason} — Review equity_history.csv and reset manually.",
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
            "--env", "dev",          # 300-instrument dataset lives in envs/dev/
            "--scope", "registry_all",  # dynamic universe has no layer_a_instruments
        ]
        rc, output = run_subprocess(update_cmd, log_lines)
        if rc == 3:
            # Exit code 3 = VPN unavailable. Data not updated; doctor preflight will flag
            # staleness. Continue so the trade plan is still generated (with stale data warning).
            log_lines.append("  WARNING: Binance unreachable (exit 3) — data not updated (VPN?). Continuing.")
            warnings.append("Binance data not updated — connect VPN and re-run update manually")
        elif rc != 0:
            log_lines.append(f"  FAILED (exit {rc}) — data not updated")
            LOG_PATH.write_text("\n".join(log_lines))
            if args.notify:
                send_notification(
                    "⚠️ Paper Run Failed",
                    f"Binance data update failed (exit {rc}) — check logs",
                )
            return 1
        else:
            log_lines.append("  OK.")

    # -----------------------------------------------------------------------
    # Step 3b: CoinMetrics data update (xs_activity + xs_val signals)
    # -----------------------------------------------------------------------
    log_lines.append("\n[3b/10] Updating CoinMetrics data (active addresses + market cap)...")
    if args.dry_run:
        log_lines.append("  Skipped (--dry-run).")
    else:
        cm_ok = True
        for script, output_file in [
            ("scripts/download_active_addresses.py", "data/active_addresses.parquet"),
            ("scripts/download_market_cap.py", "data/market_cap.parquet"),
        ]:
            cm_cmd = [sys.executable, script, "--output", output_file]
            rc, _ = run_subprocess(cm_cmd, log_lines)
            if rc != 0:
                log_lines.append(f"  WARNING: {script} failed (exit {rc}) — xs_activity/xs_val signals will use stale data")
                warnings.append(f"CoinMetrics update failed: {Path(script).stem}")
                cm_ok = False
        if cm_ok:
            log_lines.append("  OK.")

    # -----------------------------------------------------------------------
    # Step 3c: Sector map refresh (monthly / on-demand only)
    # -----------------------------------------------------------------------
    if args.refresh_sector_map:
        log_lines.append("\n[3c/10] Refreshing sector map from CoinGecko (~10 min)...")
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
                    "--output", str(REPO_ROOT / "data" / "sector_map.json"),
                ]
                rc, _ = run_subprocess(sector_cmd, log_lines)
                if rc != 0:
                    log_lines.append(f"  WARNING: Sector map refresh failed (exit {rc})")
                    warnings.append("Sector map refresh failed")
                else:
                    log_lines.append("  OK.")
    else:
        log_lines.append("\n[3c/10] Sector map refresh: skipped (pass --refresh-sector-map to rebuild).")

    # -----------------------------------------------------------------------
    # Step 3d: Hyperliquid instruments refresh
    # -----------------------------------------------------------------------
    log_lines.append("\n[3d/10] Refreshing Hyperliquid instrument list...")
    if args.dry_run:
        log_lines.append("  Skipped (--dry-run).")
    else:
        hl_cmd = [sys.executable, "scripts/fetch_hyperliquid_instruments.py"]
        rc, _ = run_subprocess(hl_cmd, log_lines)
        if rc != 0:
            log_lines.append(f"  WARNING: HL instrument refresh failed (exit {rc}) — exchange filter will use stale list")
            warnings.append("Hyperliquid instrument list not updated")
        else:
            log_lines.append("  OK.")

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
            "--actual-positions", str(LIVE_DIR / "current_positions.csv"),
            "--current-equity-file", str(EQUITY_FILE),
            "--cadence", "daily",
            "--data-dir", str(REPO_ROOT / "data" / "raw" / "binance"),
        ]
        doctor_rc, doctor_output = run_subprocess(doctor_cmd, log_lines)

    # Exit codes: 0=PASS, 1=PASS_WITH_WARNINGS, 2=FAIL
    if doctor_rc == 2:
        log_lines.append("  FAIL — aborting.")
        LOG_PATH.write_text("\n".join(log_lines))
        if args.notify:
            send_notification(
                "⚠️ Paper Run Aborted",
                "Doctor preflight FAILED — check live/paper_run_latest.log",
            )
        return 1
    elif doctor_rc == 1:
        if args.ignore_warnings:
            log_lines.append("  PASS_WITH_WARNINGS (--ignore-warnings active, continuing).")
            warnings.append("Doctor preflight: warnings present (overridden by --ignore-warnings)")
        else:
            log_lines.append("  PASS_WITH_WARNINGS — halting. Re-run with --ignore-warnings to override.")
            LOG_PATH.write_text("\n".join(log_lines))
            if args.notify:
                send_notification(
                    "⚠️ Paper Run Halted",
                    "Doctor preflight returned warnings — review live/paper_run_latest.log, then re-run with --ignore-warnings",
                )
            return 1
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
            "--actual-positions", str(LIVE_DIR / "current_positions.csv"),
            "--current-equity", str(equity),
            "--output-dir", str(output_dir),
            "--cadence", "daily",
            "--skip-data-update",  # already updated in steps 3 and 3b
            "--use-dynamic-universe",  # 1k config uses auto_discover registry
            "--env", "dev",           # 338-instrument dataset lives in envs/dev/
        ]
        adv_rc, _ = run_subprocess(advisory_cmd, log_lines)
        if adv_rc != 0:
            log_lines.append(f"  Advisory failed (exit {adv_rc}).")
            warnings.append(f"Advisory exited {adv_rc}")
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
    LOG_PATH.write_text("\n".join(log_lines))
    print("\n".join(log_lines))

    return 0


if __name__ == "__main__":
    sys.exit(main())
