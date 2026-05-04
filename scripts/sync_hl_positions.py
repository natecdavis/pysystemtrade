#!/usr/bin/env python3
"""
Sync live positions from Hyperliquid into current_positions.csv.

Reads the wallet address and network (mainnet/testnet) from
envs/<env>/config/hl_account.json, queries the HL /info API, and
writes envs/<env>/live/current_positions.csv in the format expected
by daily_paper_run.py and the trade plan generator.

Usage:
    python scripts/sync_hl_positions.py --env dev
    python scripts/sync_hl_positions.py --env prod
    python scripts/sync_hl_positions.py --env-root /path/to/env
    python scripts/sync_hl_positions.py --env dev --dry-run
"""

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


def hl_symbol_to_instrument_id(hl_symbol: str) -> str:
    """Reverse of instrument_id_to_hl_symbol."""
    if hl_symbol.startswith("k"):
        return f"1000{hl_symbol[1:]}USDT_PERP"
    return f"{hl_symbol}USDT_PERP"


def fetch_positions(wallet_address: str, api_url: str) -> list[dict]:
    resp = requests.post(
        api_url,
        json={"type": "clearinghouseState", "user": wallet_address},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    return data.get("assetPositions", []), data.get("marginSummary", {})


def fetch_unified_equity(wallet_address: str, api_url: str) -> float:
    """Return total portfolio equity: spot USDC balance + unrealized PnL on open perps.

    In a Hyperliquid unified account the spot USDC wallet IS the margin pool.
    clearinghouseState.marginSummary.accountValue is the *perps-only* sub-margin
    (typically a tiny fraction of total equity), not the full account value.
    """
    perps_resp = requests.post(
        api_url,
        json={"type": "clearinghouseState", "user": wallet_address},
        timeout=15,
    )
    perps_resp.raise_for_status()
    perps_data = perps_resp.json()
    unrealized_pnl = sum(
        float(p["position"].get("unrealizedPnl", 0))
        for p in perps_data.get("assetPositions", [])
    )

    spot_resp = requests.post(
        api_url,
        json={"type": "spotClearinghouseState", "user": wallet_address},
        timeout=15,
    )
    spot_resp.raise_for_status()
    spot_usdc = 0.0
    for b in spot_resp.json().get("balances", []):
        if b.get("coin") == "USDC":
            spot_usdc = float(b.get("total", 0))
            break

    return round(spot_usdc + unrealized_pnl, 2)


def build_positions_df(asset_positions: list[dict]) -> pd.DataFrame:
    rows = []
    now = datetime.now(timezone.utc).isoformat()
    for entry in asset_positions:
        pos = entry["position"]
        coin = pos["coin"]
        szi = float(pos["szi"])
        if szi == 0.0:
            continue
        pos_value = float(pos["positionValue"])
        mark_price = pos_value / abs(szi) if szi != 0 else 0.0
        signed_notional = szi * mark_price
        instrument = hl_symbol_to_instrument_id(coin)
        rows.append({
            "instrument": instrument,
            "contracts": szi,
            "mark_price_usd": round(mark_price, 8),
            "notional_usd": round(signed_notional, 6),
            "timestamp": now,
            "notes": "",
        })
    return pd.DataFrame(rows, columns=["instrument", "contracts", "mark_price_usd",
                                        "notional_usd", "timestamp", "notes"])


def load_hl_account(env_root: Path) -> dict:
    path = env_root / "config" / "hl_account.json"
    if not path.exists():
        raise FileNotFoundError(
            f"HL account config not found: {path}\n"
            f"Create it with: {{\"wallet_address\": \"0x...\", \"network\": \"mainnet\"}}"
        )
    with open(path) as f:
        return json.load(f)


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync Hyperliquid positions and equity to live/")
    parser.add_argument("--env", default="dev")
    parser.add_argument("--env-root", type=Path)
    parser.add_argument("--dry-run", action="store_true", help="Print without writing")
    parser.add_argument("--no-equity", action="store_true", help="Skip writing current_equity.txt")
    args = parser.parse_args()

    env = LiveOpsEnvironment(env=args.env, env_root=args.env_root, project_root=REPO_ROOT)
    account = load_hl_account(env.env_root)

    wallet = account["wallet_address"]
    network = account.get("network", "mainnet")
    api_url = HL_TESTNET_URL if network == "testnet" else HL_MAINNET_URL

    print(f"Fetching positions from HL {network} for {wallet[:10]}...")
    asset_positions, margin = fetch_positions(wallet, api_url)
    df = build_positions_df(asset_positions)

    equity = fetch_unified_equity(wallet, api_url)
    print(f"Total equity: ${equity:,.2f}")
    print(f"Found {len(df)} open position(s):")
    print(df[["instrument", "contracts", "mark_price_usd", "notional_usd"]].to_string(index=False))

    if args.dry_run:
        print("\n--dry-run: not writing.")
        return 0

    live_dir = env.resolve("live")
    live_dir.mkdir(parents=True, exist_ok=True)

    from sysdata.crypto.atomic_io import atomic_write_csv, atomic_write_text

    out_path = live_dir / "current_positions.csv"
    atomic_write_csv(df, out_path, index=False)
    print(f"\nWritten to {out_path}")

    if not args.no_equity:
        equity_path = live_dir / "current_equity.txt"
        atomic_write_text(equity_path, f"{equity}\n")
        print(f"Written to {equity_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
