#!/usr/bin/env python3
"""
Download US spot Bitcoin/Ethereum ETF activity proxy for the C2a etf_flow_trend rule.

Free public sources for actual *net flows* (CoinShares weekly reports, SoSoValue)
are paywalled or scrape-only. yfinance gives daily volume × close, which is a
reasonable proxy for "institutional dollars actively traded that day" — when
volume × price grows on net up days, capital is being deployed.

We pull the largest spot BTC ETF (IBIT, BlackRock, launched 2024-01-11) and the
largest spot ETH ETF (ETHA, BlackRock, launched 2024-07-23). Both are the
dominant volume venues in their respective categories.

Output schema (data/etf_flows.parquet):
    date, btc_etf_dollar_volume, eth_etf_dollar_volume,
          btc_etf_signed_volume, eth_etf_signed_volume

Where signed_volume = dollar_volume × sign(close - open) — captures the directional
intent behind the day's trading.

The harness must respect the launch-date temporal restriction — the C2a rule
returns NaN before its respective launch date, and the WF stitched OOS series
ignores those NaN windows automatically.

Usage:
    python scripts/download_etf_flows.py
    python scripts/download_etf_flows.py --start 2023-12-01
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

DEFAULT_OUTPUT = REPO_ROOT / "data" / "etf_flows.parquet"

# (yfinance_ticker, output_prefix, launch_date_iso) for the largest spot ETFs.
ETFS = [
    ("IBIT", "btc_etf", "2024-01-11"),
    ("ETHA", "eth_etf", "2024-07-23"),
]


def download_etf_activity(start: str, output_path: Path) -> pd.DataFrame:
    try:
        import yfinance as yf
    except ImportError:
        print("ERROR: yfinance not installed. Run: pip install yfinance", file=sys.stderr)
        return pd.DataFrame()

    end = date.today().strftime("%Y-%m-%d")
    series_dict: dict[str, pd.Series] = {}

    for ticker, prefix, launch in ETFS:
        effective_start = max(start, launch)  # yfinance complains if start < listing
        print(f"Fetching {ticker} ({prefix}) {effective_start} → {end}...")
        try:
            raw = yf.download(
                ticker,
                start=effective_start,
                end=end,
                auto_adjust=True,
                progress=False,
            )
        except Exception as e:
            print(f"  ERROR: {e}", file=sys.stderr)
            continue
        if raw.empty:
            print(f"  WARNING: empty response for {ticker}")
            continue
        # yfinance returns a single-column-block frame for one ticker; squeeze.
        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = [c[0] for c in raw.columns]
        raw.index = pd.to_datetime(raw.index).tz_localize(None)
        close = raw["Close"]
        op = raw["Open"]
        volume = raw["Volume"]
        dollar_volume = (volume * close).rename(f"{prefix}_dollar_volume")
        # Signed volume: positive on up days, negative on down days. Magnitude
        # tracks the day's gross dollar trading; sign tracks net directional intent.
        sign = (close - op).where((close - op) != 0, 0).pipe(lambda s: s.where(s == 0, s.mul(0).add(1).where(s > 0, -1)))
        signed_volume = (dollar_volume * sign).rename(f"{prefix}_signed_volume")
        series_dict[dollar_volume.name] = dollar_volume
        series_dict[signed_volume.name] = signed_volume
        print(f"  {len(dollar_volume)} rows, {dollar_volume.index[0].date()} → {dollar_volume.index[-1].date()}")

    if not series_dict:
        print("No ETF data downloaded — output not written.", file=sys.stderr)
        return pd.DataFrame()

    df = pd.DataFrame(series_dict)
    df = df.dropna(how="all").sort_index()
    df = df[~df.index.duplicated(keep="last")]

    print(f"\nETF activity dataset: {len(df)} rows")
    print(f"  Date range: {df.index[0].date()} → {df.index[-1].date()}")
    print(f"  Columns: {list(df.columns)}")
    print(f"  Latest BTC ETF $ volume: ${df.get('btc_etf_dollar_volume', pd.Series([0])).iloc[-1]:,.0f}")
    if 'eth_etf_dollar_volume' in df.columns:
        print(f"  Latest ETH ETF $ volume: ${df['eth_etf_dollar_volume'].iloc[-1]:,.0f}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    # Atomic write.
    import os, tempfile
    fd, tmp = tempfile.mkstemp(prefix=f".{output_path.name}.", suffix=".tmp", dir=str(output_path.parent))
    os.close(fd)
    df.to_parquet(tmp)
    os.replace(tmp, str(output_path))
    print(f"\n✓ Written to {output_path}")
    return df


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--start", default="2023-12-01")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    df = download_etf_activity(args.start, args.output)
    if df.empty:
        return 1

    assert df.index.is_monotonic_increasing
    assert "btc_etf_dollar_volume" in df.columns
    print("\n✓ Sanity checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
