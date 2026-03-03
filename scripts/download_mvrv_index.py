#!/usr/bin/env python3
"""
Download MVRV ratio data from CoinMetrics Community API and save to parquet.

Source: https://community-api.coinmetrics.io/v4/timeseries/asset-metrics
        ?assets=btc&metrics=CapMVRVCur&frequency=1d&page_size=10000

CapMVRVCur = current Market Value to Realized Value ratio (MVRV).
Coverage: Daily, 2010-07-18 to present (~5700 rows).

Values interpretation:
  MVRV > 3.0-3.5 → overheated / bubble conditions → reduce positions
  MVRV < 1.0     → undervalued conditions
  MVRV ~1-2.5    → neutral / fair value

Output:   data/mvrv_index.parquet — columns: mvrv_ratio (float).
          Index: date (DatetimeIndex, tz-naive UTC midnight).

Usage:
    python scripts/download_mvrv_index.py [--output data/mvrv_index.parquet]
"""

import argparse
import json
import sys
import urllib.request
from pathlib import Path

import pandas as pd

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

BASE_URL = (
    "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics"
    "?assets=btc&metrics=CapMVRVCur&frequency=1d&page_size=10000"
)


def fetch_mvrv_data() -> list:
    """Fetch all available MVRV data from CoinMetrics Community API."""
    all_records = []
    url = BASE_URL
    page_num = 1

    while True:
        print(f"  Fetching page {page_num} from CoinMetrics Community API ...")
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; research-bot)"},
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read()
        payload = json.loads(raw)
        records = payload.get("data", [])
        all_records.extend(records)
        print(f"    Got {len(records)} records (total so far: {len(all_records)})")

        next_token = payload.get("next_page_token", None)
        if not next_token:
            break
        url = BASE_URL + f"&next_page_token={next_token}"
        page_num += 1

    return all_records


def parse_mvrv_data(records: list) -> pd.DataFrame:
    """
    Parse API response into a clean DataFrame.

    API record format:
        {
            "asset": "btc",
            "time": "2021-01-01T00:00:00.000000000Z",
            "CapMVRVCur": "28.69484637..."
        }
    """
    rows = []
    skipped = 0
    for rec in records:
        time_str = rec.get("time", "")
        mvrv_str = rec.get("CapMVRVCur")

        if not mvrv_str:
            skipped += 1
            continue

        try:
            mvrv_ratio = float(mvrv_str)
        except (ValueError, TypeError):
            skipped += 1
            continue

        # Parse ISO 8601 timestamp, normalize to midnight UTC, strip tz
        dt = pd.Timestamp(time_str).normalize().tz_localize(None)

        rows.append({
            "date": dt,
            "mvrv_ratio": mvrv_ratio,
        })

    if skipped:
        print(f"  Skipped {skipped} records with missing/invalid data")

    df = pd.DataFrame(rows)
    df = df.sort_values("date").drop_duplicates("date")
    df = df.set_index("date")
    df.index = pd.DatetimeIndex(df.index)
    return df


def save_mvrv_data(df: pd.DataFrame, output_path: str) -> None:
    """Save MVRV DataFrame to parquet."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path)
    print(f"  Saved {len(df)} rows to {path}")
    print(f"  Date range: {df.index.min().date()} to {df.index.max().date()}")
    print(f"  MVRV range: {df['mvrv_ratio'].min():.3f} – {df['mvrv_ratio'].max():.3f}")
    print(f"  Current MVRV: {df['mvrv_ratio'].iloc[-1]:.3f}")

    # Show time above key thresholds (for context)
    for threshold in [2.5, 3.0, 3.5]:
        pct = (df['mvrv_ratio'] > threshold).mean() * 100
        print(f"  Days with MVRV > {threshold}: {pct:.1f}%")


def main():
    parser = argparse.ArgumentParser(description="Download MVRV ratio data from CoinMetrics")
    parser.add_argument(
        "--output",
        default="data/mvrv_index.parquet",
        help="Output path for parquet file (default: data/mvrv_index.parquet)",
    )
    args = parser.parse_args()

    print(f"Downloading MVRV data (CapMVRVCur) from CoinMetrics Community API ...")
    records = fetch_mvrv_data()

    if not records:
        print("ERROR: No data returned from API")
        sys.exit(1)

    print(f"\nTotal records fetched: {len(records)}")
    df = parse_mvrv_data(records)
    print(f"Parsed {len(df)} daily MVRV records")
    print(f"\nSample (last 5):\n{df.tail()}")

    save_mvrv_data(df, args.output)
    print("\nDone.")


if __name__ == "__main__":
    main()
