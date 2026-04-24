#!/usr/bin/env python3
"""
Compare live Hyperliquid execution statistics to backtesting assumptions.

Pulls fill history and funding history from the HL API and computes:
  - Actual fee rate vs assumed 4.5bps taker
  - Implied annual turnover vs backtest value
  - Net funding received/paid vs backtest assumption
  - Trade frequency and average trade size

Usage:
    python scripts/analyze_live_performance.py --env dev
    python scripts/analyze_live_performance.py --env dev --verbose
    python scripts/analyze_live_performance.py --env dev --start 2026-03-01
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

from sysdata.crypto.env_paths import LiveOpsEnvironment

HL_MAINNET_URL = "https://api.hyperliquid.xyz/info"
HL_TESTNET_URL = "https://api.hyperliquid-testnet.xyz/info"

# Backtest assumptions to compare against (from 1k live config)
ASSUMED_TAKER_FEE_BPS = 4.5
ASSUMED_TAKER_FEE_FRAC = 0.00045


def _post(api_url: str, payload: dict) -> dict | list:
    resp = requests.post(api_url, json=payload, timeout=15)
    resp.raise_for_status()
    return resp.json()


def fetch_fills(wallet: str, api_url: str) -> list[dict]:
    return _post(api_url, {"type": "userFills", "user": wallet})


def fetch_funding(wallet: str, api_url: str, start_ms: int = 0) -> list[dict]:
    return _post(
        api_url,
        {"type": "userFunding", "user": wallet, "startTime": start_ms},
    )


def fetch_account_value(wallet: str, api_url: str) -> tuple[float, float]:
    """Return (perps_account_value, spot_usdc_balance)."""
    perps = _post(api_url, {"type": "clearinghouseState", "user": wallet})
    perps_value = float(perps.get("marginSummary", {}).get("accountValue", 0))

    spot = _post(api_url, {"type": "spotClearinghouseState", "user": wallet})
    spot_usdc = 0.0
    for b in spot.get("balances", []):
        if b.get("coin") == "USDC":
            spot_usdc = float(b.get("total", 0))
            break

    return perps_value, spot_usdc


def load_equity_history(env: LiveOpsEnvironment) -> pd.DataFrame:
    path = env.resolve("live") / "equity_history.csv"
    if not path.exists():
        return pd.DataFrame(columns=["date", "equity"])
    df = pd.read_csv(path, parse_dates=["date"])
    return df.sort_values("date")


def load_backtest_summary(env: LiveOpsEnvironment) -> dict:
    """Load performance_summary.json from the latest paper run backtest."""
    paper_dirs = sorted(
        [d for d in (env.resolve("out") if env.resolve("out").exists() else Path(".")).iterdir()
         if d.is_dir() and d.name.startswith("paper_")],
        reverse=True,
    )
    for d in paper_dirs:
        path = d / "backtest_latest" / "performance_summary.json"
        if path.exists():
            return json.loads(path.read_text())
    return {}


def analyze_fills(fills: list[dict], start_dt: datetime | None, verbose: bool) -> dict:
    if not fills:
        return {"count": 0}

    rows = []
    for f in fills:
        ts_ms = f.get("time", 0)
        dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
        if start_dt and dt < start_dt:
            continue
        coin = f.get("coin", "")
        px = float(f.get("px", 0))
        sz = abs(float(f.get("sz", 0)))
        notional = px * sz
        fee = abs(float(f.get("fee", 0)))
        side = f.get("side", "")
        rows.append({
            "dt": dt,
            "coin": coin,
            "px": px,
            "sz": sz,
            "notional": notional,
            "fee": fee,
            "fee_bps": (fee / notional * 10000) if notional > 0 else 0,
            "side": side,
        })

    if not rows:
        return {"count": 0, "note": "no fills after start date"}

    df = pd.DataFrame(rows).sort_values("dt")

    total_notional = df["notional"].sum()
    total_fee = df["fee"].sum()
    avg_fee_bps = (total_fee / total_notional * 10000) if total_notional > 0 else 0
    avg_trade_size = df["notional"].mean()
    n_trades = len(df)
    n_buys = (df["side"] == "B").sum()
    n_sells = (df["side"] == "A").sum()

    span_days = (df["dt"].max() - df["dt"].min()).total_seconds() / 86400
    trades_per_day = n_trades / max(span_days, 1)

    if verbose:
        print("\n--- Fill detail (last 20) ---")
        cols = ["dt", "coin", "side", "notional", "fee", "fee_bps"]
        print(df[cols].tail(20).to_string(index=False))

    return {
        "count": n_trades,
        "buys": int(n_buys),
        "sells": int(n_sells),
        "total_notional_usd": total_notional,
        "total_fee_usd": total_fee,
        "avg_fee_bps": avg_fee_bps,
        "avg_trade_size_usd": avg_trade_size,
        "span_days": span_days,
        "trades_per_day": trades_per_day,
        "df": df,
    }


def analyze_funding(funding: list[dict], start_dt: datetime | None, verbose: bool) -> dict:
    if not funding:
        return {"count": 0}

    rows = []
    for f in funding:
        ts_ms = f.get("time", 0)
        dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
        if start_dt and dt < start_dt:
            continue
        delta = f.get("delta", {})
        if delta.get("type") != "funding":
            continue
        usdc = float(delta.get("usdc", 0))
        coin = delta.get("coin", "")
        funding_rate = float(delta.get("fundingRate", 0))
        szi = float(delta.get("szi", 0))
        rows.append({
            "dt": dt,
            "coin": coin,
            "usdc": usdc,
            "funding_rate": funding_rate,
            "szi": szi,
        })

    if not rows:
        return {"count": 0, "note": "no funding after start date"}

    df = pd.DataFrame(rows).sort_values("dt")

    total_received = df["usdc"].sum()
    n_positive = (df["usdc"] > 0).sum()
    n_negative = (df["usdc"] < 0).sum()
    span_days = (df["dt"].max() - df["dt"].min()).total_seconds() / 86400 if len(df) > 1 else 1

    by_coin = df.groupby("coin")["usdc"].sum().sort_values()
    top_payers = by_coin.head(5)
    top_receivers = by_coin.tail(5)[::-1]

    if verbose:
        print("\n--- Funding by coin (top 10 by abs value) ---")
        print(by_coin.abs().sort_values(ascending=False).head(10).to_string())

    return {
        "count": len(df),
        "total_received_usd": total_received,
        "n_positive": int(n_positive),
        "n_negative": int(n_negative),
        "span_days": span_days,
        "top_payers": top_payers.to_dict(),
        "top_receivers": top_receivers.to_dict(),
        "df": df,
    }


def annualize(value: float, span_days: float) -> float:
    if span_days <= 0:
        return 0.0
    return value * 365.25 / span_days


def print_report(
    fills: dict,
    funding: dict,
    account_value: float,
    equity_history: pd.DataFrame,
    backtest: dict,
):
    bt_metrics = backtest.get("metrics", {})
    bt_costs = backtest.get("cost_model", {})
    bt_portfolio = backtest.get("portfolio", {})

    # Average equity for normalization
    if not equity_history.empty:
        avg_equity = equity_history["equity"].mean()
    else:
        avg_equity = account_value or 1.0

    print(f"\n{'='*70}")
    print("LIVE vs BACKTEST COMPARISON")
    print(f"Total capital: ${account_value:,.2f} | Avg equity (history): ${avg_equity:,.2f}")
    print(f"{'='*70}")

    # --- FEES ---
    print(f"\n{'─'*70}")
    print("FEES")
    print(f"{'─'*70}")
    print(f"  Backtest assumption:    {ASSUMED_TAKER_FEE_BPS:.1f} bps taker fee")

    if fills.get("count", 0) > 0:
        actual_bps = fills["avg_fee_bps"]
        delta_bps = actual_bps - ASSUMED_TAKER_FEE_BPS
        flag = " ✓" if abs(delta_bps) < 0.5 else (" ⚠ HIGHER" if delta_bps > 0 else " ⚠ LOWER")
        print(f"  Actual (avg per fill):  {actual_bps:.2f} bps{flag}")
        print(f"  Delta:                  {delta_bps:+.2f} bps")
        print(f"  Total fees paid:        ${fills['total_fee_usd']:.4f} over {fills['span_days']:.0f} days")
        print(f"  Implied annual (% eq):  {annualize(fills['total_fee_usd'], fills['span_days']) / avg_equity * 100:.3f}%")
        bt_cost_pct = bt_costs.get("transaction_cost_ann", 0) * 100
        print(f"  Backtest annual cost:   {bt_cost_pct:.3f}% of capital")
    else:
        print("  No fills found in range.")

    # --- TURNOVER ---
    print(f"\n{'─'*70}")
    print("TURNOVER")
    print(f"{'─'*70}")
    bt_turnover = bt_portfolio.get("annual_turnover", None)
    if bt_turnover:
        print(f"  Backtest annual:        {bt_turnover:.2f}x")

    if fills.get("count", 0) > 0:
        implied_ann_notional = annualize(fills["total_notional_usd"], fills["span_days"])
        # Notional capital = avg_equity * 2.5 (phantom leverage)
        notional_capital = avg_equity * 2.5
        implied_turnover = implied_ann_notional / notional_capital if notional_capital > 0 else 0
        print(f"  Actual (implied ann):   {implied_turnover:.2f}x  "
              f"(${fills['total_notional_usd']:.0f} traded over {fills['span_days']:.0f} days)")
        print(f"  Avg trade size:         ${fills['avg_trade_size_usd']:.2f}")
        print(f"  Trades per day:         {fills['trades_per_day']:.2f}")
        print(f"  Total fills:            {fills['count']} ({fills['buys']} buys, {fills['sells']} sells)")
        if bt_turnover:
            delta_t = implied_turnover - bt_turnover
            flag = " ✓" if abs(delta_t) < bt_turnover * 0.3 else " ⚠"
            print(f"  Delta vs backtest:      {delta_t:+.2f}x{flag}")
    else:
        print("  No fills found in range.")

    # --- FUNDING ---
    print(f"\n{'─'*70}")
    print("FUNDING (net received = positive)")
    print(f"{'─'*70}")
    bt_funding = bt_costs.get("funding_drag_ann", None)
    if bt_funding is not None:
        # funding_drag_ann convention: negative = net receipt (favorable), positive = net cost
        bt_as_receipt_pct = -bt_funding * 100
        print(f"  Backtest annual:        {bt_as_receipt_pct:+.3f}% of capital  "
              f"({'favorable ↑' if bt_as_receipt_pct > 0 else 'drag ↓'})")

    if funding.get("count", 0) > 0:
        total_fund = funding["total_received_usd"]
        span = funding["span_days"]
        ann_funding = annualize(total_fund, span)
        ann_funding_pct = ann_funding / avg_equity * 100 if avg_equity > 0 else 0
        sign = "favorable ↑" if ann_funding_pct > 0 else "drag ↓"
        # bt_funding uses drag convention (negative = receipt/benefit); flip for comparison
        bt_as_receipt = -(bt_funding or 0)
        flag = " ✓" if bt_as_receipt * ann_funding_pct >= 0 else " ⚠ SIGN FLIP"
        print(f"  Actual (implied ann):   {ann_funding_pct:+.3f}% of equity  ({sign}){flag}")
        print(f"  Total received:         ${total_fund:+.4f} over {span:.0f} days")
        print(f"  Payments:               {funding['n_positive']} positive, {funding['n_negative']} negative")
        if funding.get("top_receivers"):
            top = list(funding["top_receivers"].items())[:3]
            print(f"  Top receivers:          " + ", ".join(f"{c}: ${v:+.3f}" for c, v in top))
        if funding.get("top_payers"):
            top = list(funding["top_payers"].items())[:3]
            print(f"  Top payers:             " + ", ".join(f"{c}: ${v:+.3f}" for c, v in top))
    else:
        print("  No funding records found in range.")

    # --- POSITIONS ---
    print(f"\n{'─'*70}")
    print("POSITION COUNT")
    print(f"{'─'*70}")
    bt_avg_pos = bt_portfolio.get("avg_active_positions", None)
    if bt_avg_pos:
        print(f"  Backtest avg active:    {bt_avg_pos:.1f}")

    # --- SUMMARY ---
    print(f"\n{'='*70}")
    print("ASSUMPTIONS STATUS")
    print(f"{'='*70}")
    issues = []
    if fills.get("count", 0) > 0:
        if abs(fills["avg_fee_bps"] - ASSUMED_TAKER_FEE_BPS) > 1.0:
            issues.append(f"Fee rate: actual {fills['avg_fee_bps']:.2f}bps vs assumed {ASSUMED_TAKER_FEE_BPS}bps")
        if bt_turnover:
            implied_turnover = (
                annualize(fills["total_notional_usd"], fills["span_days"]) / (avg_equity * 2.5)
            )
            if abs(implied_turnover - bt_turnover) > bt_turnover * 0.5:
                issues.append(f"Turnover: actual {implied_turnover:.2f}x vs backtest {bt_turnover:.2f}x")
    if funding.get("count", 0) > 0 and bt_funding is not None:
        ann_funding_pct = annualize(funding["total_received_usd"], funding["span_days"]) / avg_equity * 100
        bt_as_receipt_pct = -bt_funding * 100  # flip drag→receipt convention
        if bt_as_receipt_pct * ann_funding_pct < 0:
            issues.append(f"Funding sign flip: actual {ann_funding_pct:+.3f}% vs backtest {bt_as_receipt_pct:+.3f}%")
    if not issues:
        print("  All checked assumptions within tolerance. ✓")
    else:
        for issue in issues:
            print(f"  ⚠ {issue}")
    print()


def main():
    parser = argparse.ArgumentParser(
        description="Compare live HL execution stats to backtest assumptions"
    )
    parser.add_argument("--env", default="dev")
    parser.add_argument("--env-root", type=Path)
    parser.add_argument("--start", default=None,
                        help="Start date for analysis, e.g. 2026-03-01 (default: all history)")
    parser.add_argument("--verbose", action="store_true",
                        help="Print per-fill and per-coin breakdown")
    args = parser.parse_args()

    env = LiveOpsEnvironment(env=args.env, env_root=args.env_root, project_root=REPO_ROOT)

    account_path = env.env_root / "config" / "hl_account.json"
    if not account_path.exists():
        print(f"HL account config not found: {account_path}", file=sys.stderr)
        sys.exit(1)
    account = json.loads(account_path.read_text())
    wallet = account["wallet_address"]
    network = account.get("network", "mainnet")
    api_url = HL_TESTNET_URL if network == "testnet" else HL_MAINNET_URL

    start_dt = None
    start_ms = 0
    if args.start:
        start_dt = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        start_ms = int(start_dt.timestamp() * 1000)

    print(f"Fetching data from HL {network} for {wallet[:10]}...")

    perps_value, spot_usdc = fetch_account_value(wallet, api_url)
    account_value = spot_usdc + perps_value
    print(f"Perps account: ${perps_value:,.2f} | Spot USDC: ${spot_usdc:,.2f} | Total: ${account_value:,.2f}")

    print("Fetching fill history...")
    raw_fills = fetch_fills(wallet, api_url)
    print(f"  {len(raw_fills)} total fills in account history")

    print("Fetching funding history...")
    raw_funding = fetch_funding(wallet, api_url, start_ms=start_ms)
    print(f"  {len(raw_funding)} funding records")

    fills = analyze_fills(raw_fills, start_dt, args.verbose)
    funding = analyze_funding(raw_funding, start_dt, args.verbose)
    equity_history = load_equity_history(env)
    backtest = load_backtest_summary(env)

    print_report(fills, funding, account_value, equity_history, backtest)


if __name__ == "__main__":
    main()
