#!/usr/bin/env python3
"""
Download total USD-pegged stablecoin supply from DefiLlama.

The aggregate stablecoin supply (USDT + USDC + DAI + USDe + everything pegged to
USD) is a widely-followed leading indicator of capital flows into crypto:
issuance grows when capital enters (bullish for spot prices over weeks); supply
contracts when capital leaves (bearish). Used by the C2b stablecoin_supply_trend
rule.

Source: https://stablecoins.llama.fi/stablecoincharts/all (no auth, no rate
limit on this aggregate endpoint as of 2026-05-02).

Usage:
    python scripts/download_stablecoin_supply.py
    python scripts/download_stablecoin_supply.py --output data/stablecoin_supply.parquet
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

DEFAULT_OUTPUT = REPO_ROOT / "data" / "stablecoin_supply.parquet"
ENDPOINT = "https://stablecoins.llama.fi/stablecoincharts/all"


def download_stablecoin_supply(output_path: Path = DEFAULT_OUTPUT) -> pd.DataFrame:
    print(f"Fetching stablecoin supply from {ENDPOINT}...")
    resp = requests.get(ENDPOINT, timeout=60)
    resp.raise_for_status()
    rows = resp.json()
    print(f"  Got {len(rows)} daily rows")

    records = []
    for row in rows:
        # date is a unix timestamp (seconds), USD value lives under
        # totalCirculatingUSD.peggedUSD (the USD-pegged stablecoin aggregate).
        ts = int(row["date"])
        date = datetime.fromtimestamp(ts, tz=timezone.utc).date()
        circulating_usd = row.get("totalCirculatingUSD", {}).get("peggedUSD")
        circulating_native = row.get("totalCirculating", {}).get("peggedUSD")
        if circulating_usd is None and circulating_native is None:
            continue
        records.append(
            {
                "date": date,
                "stablecoin_supply_usd": float(circulating_usd or circulating_native),
            }
        )

    df = pd.DataFrame(records)
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()
    df = df[~df.index.duplicated(keep="last")]

    print(f"  Date range: {df.index[0].date()} → {df.index[-1].date()}")
    print(f"  Latest supply: ${df['stablecoin_supply_usd'].iloc[-1]:,.0f}")

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Atomic write so a partial fetch doesn't corrupt the existing file.
    try:
        from sysdata.crypto.atomic_io import atomic_write_csv
        # parquet first via tempfile
        import tempfile, os
        fd, tmp = tempfile.mkstemp(prefix=f".{output_path.name}.", suffix=".tmp", dir=str(output_path.parent))
        os.close(fd)
        df.to_parquet(tmp)
        os.replace(tmp, str(output_path))
    except Exception:
        df.to_parquet(output_path)
    print(f"✓ Written to {output_path}")

    return df


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    df = download_stablecoin_supply(output_path=args.output)

    # Sanity checks
    assert len(df) > 1000, f"Expected >1000 rows, got {len(df)}"
    assert df["stablecoin_supply_usd"].iloc[-1] > 1e10, "Latest supply implausibly small"
    assert df.index.is_monotonic_increasing, "Index not sorted"
    print("\n✓ Sanity checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
