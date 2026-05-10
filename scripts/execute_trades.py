#!/usr/bin/env python3
"""
Execute trades from the daily trade plan on Hyperliquid.

Reads the latest trade_plan_*.csv from the env output directory, shows
actionable trades, prompts for confirmation, then executes market orders
via the Hyperliquid SDK.

Private key is read from (in order of precedence):
  1. HL_PRIVATE_KEY environment variable
  2. envs/<env>/live/hl_private_key.txt  (gitignored — never commit this)

Usage:
    python scripts/execute_trades.py --env dev
    python scripts/execute_trades.py --env dev --trade-plan path/to/trade_plan.csv
    python scripts/execute_trades.py --env dev --dry-run
"""

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

from sysdata.crypto.env_paths import LiveOpsEnvironment
from systems.crypto_perps.reconciliation import (
    format_reconciliation_summary,
    reconcile_post_execution,
    write_reconciliation_report,
)

SLIPPAGE = 0.005  # 0.5% slippage for market orders
RECONCILIATION_TOLERANCE_USD = 10.0  # tracks min_notional_position in live config


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_private_key(env_root: Path) -> str:
    key = os.environ.get("HL_PRIVATE_KEY")
    if key:
        return key.strip()
    key_file = env_root / "live" / "hl_private_key.txt"
    if key_file.exists():
        return key_file.read_text().strip()
    raise FileNotFoundError(
        "No private key found. Set HL_PRIVATE_KEY env var or create "
        f"{key_file}"
    )


def load_sz_decimals(info) -> dict[str, int]:
    meta = info.meta()
    return {asset["name"]: asset.get("szDecimals", 2) for asset in meta["universe"]}


def round_sz(sz: float, decimals: int) -> float:
    return round(sz, decimals)


def find_latest_trade_plan(output_root: Path) -> Path:
    candidates = sorted(output_root.glob("paper_*/trade_plan_*.csv"))
    if not candidates:
        raise FileNotFoundError(f"No trade plan found under {output_root}")
    return candidates[-1]


def check_plan_freshness(trade_plan_path: Path, positions_path: Path) -> tuple[bool, str]:
    """Return (is_fresh, message). A plan is stale if it was generated before
    the live positions file was last refreshed — that means the plan's frozen
    deltas are based on pre-refresh state and re-executing them would over-
    trade. This is the failure mode that almost bit us 2026-05-09 when the
    morning trades had filled and an evening daily_paper_run failed before
    regenerating the plan; a dry-run still surfaced the morning's deltas as
    if they were pending.
    """
    if not positions_path.exists():
        return True, ""
    plan_mtime = trade_plan_path.stat().st_mtime
    pos_mtime = positions_path.stat().st_mtime
    if plan_mtime >= pos_mtime:
        return True, ""
    plan_dt = datetime.fromtimestamp(plan_mtime, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    pos_dt = datetime.fromtimestamp(pos_mtime, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    gap_h = (pos_mtime - plan_mtime) / 3600
    msg = (
        f"⚠  STALE TRADE PLAN\n"
        f"   Trade plan written:  {plan_dt}\n"
        f"   Positions refreshed: {pos_dt}  ({gap_h:.1f}h later)\n"
        f"   Positions have been updated since this plan was generated; the\n"
        f"   plan's frozen deltas are based on pre-refresh state. Executing\n"
        f"   again would over-trade. Re-run daily_paper_run.py to regenerate."
    )
    return False, msg


def load_actionable_trades(trade_plan_path: Path) -> pd.DataFrame:
    df = pd.read_csv(trade_plan_path)
    warnings_col = df["warnings"].fillna("") if "warnings" in df.columns else pd.Series([""] * len(df))

    # Filter out non-actionable rows
    df = df[df["delta_notional"].abs() > 1e-6]
    df = df[~warnings_col.str.contains("buffer_suppressed")]
    df = df[~warnings_col.str.contains("below_min_trade_size")]
    df = df[~warnings_col.str.contains("reduce_only_capped")]
    return df.reset_index(drop=True)


def hl_symbol(instrument: str) -> str:
    """BTCUSDT_PERP → BTC, 1000SHIBUSDT_PERP → kSHIB"""
    base = instrument[:-5] if instrument.endswith("_PERP") else instrument
    if base.endswith("USDT"):
        base = base[:-4]
    if base.startswith("1000"):
        return "k" + base[4:]
    return base


def print_trade_table(trades: pd.DataFrame) -> None:
    print(f"\n{'─'*72}")
    print(f"  {'#':<4} {'Symbol':<10} {'Action':<18} {'Contracts':>12} {'Notional':>10}")
    print(f"{'─'*72}")
    for i, row in trades.iterrows():
        sym = row["hl_symbol"] if "hl_symbol" in row else hl_symbol(row["instrument"])
        delta = row["delta_notional"]
        action = row.get("reason", "")
        direction = "BUY" if delta > 0 else "SELL"
        action_str = f"{direction} ({action})"
        mark = row.get("mark_price_usd", 0) or 0
        current = row.get("current_notional", 0) or 0
        sz = abs(delta) / mark if mark else 0
        print(f"  {i+1:<4} {sym:<10} {action_str:<18} {sz:>12.4f} {delta:>+10.2f}")
    print(f"{'─'*72}")
    total_cost = trades["estimated_cost"].sum() if "estimated_cost" in trades.columns else 0
    print(f"  {len(trades)} trade(s) — estimated fees: ${total_cost:.4f}\n")


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------

def execute_trade(exchange, row: pd.Series, sz_decimals: dict, live_prices: dict, dry_run: bool) -> dict:
    sym = row["hl_symbol"] if "hl_symbol" in row else hl_symbol(row["instrument"])
    delta = row["delta_notional"]
    reason = row.get("reason", "")
    is_buy = delta > 0

    # Use live mark price if trade plan has 0 (new position)
    mark = row.get("mark_price_usd", 0) or 0
    if mark == 0:
        mark = live_prices.get(sym, 0)

    if sym not in sz_decimals:
        print(f"  {sym}: not available on this network — skipping")
        return {"status": "skipped", "reason": "not_on_network"}

    if mark == 0:
        print(f"  {sym}: no mark price available — skipping")
        return {"status": "skipped", "reason": "no_price"}

    decimals = sz_decimals.get(sym, 2)
    sz = round_sz(abs(delta) / mark, decimals)
    if sz == 0:
        print(f"  {sym}: sz rounds to 0 — skipping")
        return {"status": "skipped", "reason": "sz=0"}

    if reason == "flatten_to_zero":
        direction = "buy" if is_buy else "sell"
        print(f"  {sym}: market {direction} {sz} contracts (flatten to zero)", end=" ", flush=True)
        if not dry_run:
            result = exchange.market_open(sym, is_buy, sz, slippage=SLIPPAGE)
        else:
            result = {"status": "dry_run"}
    else:
        direction = "buy" if is_buy else "sell"
        print(f"  {sym}: market {direction} {sz} contracts", end=" ", flush=True)
        if not dry_run:
            result = exchange.market_open(sym, is_buy, sz, slippage=SLIPPAGE)
        else:
            result = {"status": "dry_run"}

    status = result.get("status", "?") if isinstance(result, dict) else str(result)
    print(f"→ {status}")
    return result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Execute trades from daily trade plan on Hyperliquid")
    parser.add_argument("--env", default="dev")
    parser.add_argument("--env-root", type=Path)
    parser.add_argument("--trade-plan", type=Path, help="Explicit trade plan CSV path")
    parser.add_argument("--dry-run", action="store_true", help="Show trades but don't execute")
    parser.add_argument("--yes", "-y", action="store_true", help="Skip confirmation prompt")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Override stale-plan refusal (use only if you're certain the plan is current).",
    )
    args = parser.parse_args()

    env = LiveOpsEnvironment(env=args.env, env_root=args.env_root, project_root=REPO_ROOT)

    # Load trade plan
    trade_plan_path = args.trade_plan or find_latest_trade_plan(env.resolve("out"))
    print(f"Trade plan: {trade_plan_path}")

    # Refuse to execute a plan that's older than the positions it claims to act on.
    is_fresh, freshness_msg = check_plan_freshness(
        trade_plan_path, env.env_root / "live" / "current_positions.csv"
    )
    if not is_fresh:
        print()
        print(freshness_msg)
        if args.dry_run:
            print("\n   (--dry-run: continuing with stale plan for inspection only.)")
        elif args.force:
            print("\n   --force: continuing despite stale plan.")
        else:
            print("\n   Refusing to execute. Pass --force to override.")
            return 2

    trades = load_actionable_trades(trade_plan_path)

    if trades.empty:
        print("No actionable trades.")
        return 0

    print_trade_table(trades)

    if args.dry_run:
        print("--dry-run: no orders will be placed.")
        return 0

    # Load HL config early (needed for network selection)
    import json
    from hyperliquid.info import Info
    from hyperliquid.utils import constants

    account_cfg = json.loads((env.env_root / "config" / "hl_account.json").read_text())
    network = account_cfg.get("network", "mainnet")
    api_url = constants.TESTNET_API_URL if network == "testnet" else constants.MAINNET_API_URL

    # Confirmation
    if not args.yes:
        answer = input(f"Execute these {len(trades)} trade(s) on HL? [y/N]: ").strip().lower()
        if answer != "y":
            print("Aborted.")
            return 0

    # Connect to HL
    from eth_account import Account
    from hyperliquid.exchange import Exchange

    info = Info(api_url, skip_ws=True)
    sz_decimals = load_sz_decimals(info)
    live_prices = {k: float(v) for k, v in info.all_mids().items()}

    private_key = load_private_key(env.env_root)
    wallet = Account.from_key(private_key)
    print(f"\nConnected: {wallet.address[:10]}... ({network})")

    exchange = Exchange(wallet, api_url)

    # Execute
    print(f"\nExecuting {len(trades)} trade(s)...")
    results = []
    for _, row in trades.iterrows():
        try:
            r = execute_trade(exchange, row, sz_decimals, live_prices, dry_run=False)
            results.append(r)
        except Exception as e:
            print(f"  ERROR: {e}")
            results.append({"status": "error", "error": str(e)})

    # Summary
    ok = sum(1 for r in results if isinstance(r, dict) and "error" not in r and r.get("status") != "skipped")
    print(f"\n{ok}/{len(trades)} trade(s) submitted.")

    # Refresh positions
    print("\nRefreshing positions from HL...")
    import subprocess
    sync_cmd = [sys.executable, "scripts/sync_hl_positions.py"]
    if args.env_root:
        sync_cmd += ["--env-root", str(args.env_root)]
    else:
        sync_cmd += ["--env", args.env]
    sync_rc = subprocess.run(sync_cmd, cwd=str(REPO_ROOT)).returncode
    if sync_rc != 0:
        print(f"  WARNING: sync_hl_positions exited {sync_rc} — reconciliation skipped.")
        return 0

    # Post-execution reconciliation: did each submitted trade actually move the
    # position to the planned target? A skipped/failed trade or partial fill will
    # show up here so it can't silently distort the next day's plan.
    skipped = sum(1 for r in results if isinstance(r, dict) and r.get("status") == "skipped")
    errors = sum(1 for r in results if isinstance(r, dict) and "error" in r)
    submitted = ok
    execution_summary = {
        "submitted": submitted,
        "skipped": skipped,
        "errors": errors,
        "total_actionable": len(trades),
    }

    positions_after = env.env_root / "live" / "current_positions.csv"
    if not positions_after.exists():
        print(f"  WARNING: {positions_after} not found — reconciliation skipped.")
        return 0

    recon = reconcile_post_execution(
        trade_plan_path=trade_plan_path,
        positions_after_path=positions_after,
        tolerance_usd=RECONCILIATION_TOLERANCE_USD,
        execution_summary=execution_summary,
    )
    report_path = trade_plan_path.parent / f"reconciliation_{trade_plan_path.stem.replace('trade_plan_', '')}.json"
    write_reconciliation_report(recon, report_path)

    print()
    print(format_reconciliation_summary(recon))
    print(f"  Report: {report_path}")

    if not recon.passed:
        # Non-zero exit so the operator notices on review; the run is still salvageable
        # — they can manually flatten orphans or rerun.
        return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
