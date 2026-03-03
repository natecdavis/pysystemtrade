#!/usr/bin/env python3
"""
Download Fear & Greed Index data from alternative.me and save to parquet.

Source: https://api.alternative.me/fng/?limit=0&format=json
Coverage: Daily, from 2018-02-01 to present.
Output:  data/fg_index.parquet — columns: fg_value (int 0-100), classification (str)
         Index: date (DatetimeIndex, UTC midnight)

Usage:
    python scripts/download_fg_index.py [--output data/fg_index.parquet]
"""

import argparse
import sys
import urllib.request
import json
from pathlib import Path
from datetime import datetime, timezone

import pandas as pd

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))


API_URL = "https://api.alternative.me/fng/?limit=0&format=json"


def fetch_fg_data() -> list:
    """Fetch all available F&G data from alternative.me API."""
    print(f"Fetching data from {API_URL} ...")
    req = urllib.request.Request(
        API_URL,
        headers={"User-Agent": "Mozilla/5.0 (compatible; research-bot)"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read()
    payload = json.loads(raw)
    data = payload.get("data", [])
    print(f"  Received {len(data)} records")
    return data


def parse_fg_data(records: list) -> pd.DataFrame:
    """
    Parse API response into a clean DataFrame.

    API record format:
        {
            "value": "72",
            "value_classification": "Greed",
            "timestamp": "1740614400",
            "time_until_update": "..."
        }
    """
    rows = []
    for rec in records:
        ts = int(rec["timestamp"])
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        rows.append({
            "date": dt,
            "fg_value": int(rec["value"]),
            "classification": str(rec["value_classification"]),
        })

    df = pd.DataFrame(rows)
    df = df.sort_values("date").drop_duplicates("date")
    df = df.set_index("date")
    df.index = pd.DatetimeIndex(df.index)
    return df


def save_fg_data(df: pd.DataFrame, output_path: str) -> None:
    """Save F&G DataFrame to parquet."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path)
    print(f"  Saved {len(df)} rows to {path}")
    print(f"  Date range: {df.index.min().date()} to {df.index.max().date()}")
    print(f"  fg_value range: {df['fg_value'].min()} – {df['fg_value'].max()}")
    print(f"  Classifications: {df['classification'].value_counts().to_dict()}")


def main():
    parser = argparse.ArgumentParser(description="Download Fear & Greed Index data")
    parser.add_argument(
        "--output",
        default="data/fg_index.parquet",
        help="Output path for parquet file (default: data/fg_index.parquet)",
    )
    args = parser.parse_args()

    records = fetch_fg_data()
    if not records:
        print("ERROR: No data returned from API")
        sys.exit(1)

    df = parse_fg_data(records)
    print(f"\nParsed {len(df)} daily F&G records")
    print(f"  Sample (last 5):\n{df.tail()}")

    save_fg_data(df, args.output)
    print("\nDone.")


if __name__ == "__main__":
    main()
