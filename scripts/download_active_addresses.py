#!/usr/bin/env python3
"""
Download daily active address counts from CoinMetrics Community API.

Source: https://community-api.coinmetrics.io/v4/timeseries/asset-metrics
        ?assets={comma-joined}&metrics=AdrActCnt&frequency=1d&page_size=10000

AdrActCnt = count of unique addresses active in network (either sent or received)
            on a given day. A proxy for on-chain network utility / adoption.

Coverage: ~41 instruments with usable 2020-2026 data.
          BTC, ETH, ADA, AVAX, LINK, DOGE, XRP, LTC, BCH, AAVE, UNI, SUSHI, CRV,
          COMP, SNX, YFI, LDO, UMA, ZEC, ZRX, VET, KNC, TRX, MANA, and others.

Lit: Cong et al. (2022) C-5 — network activity predicts cross-sectional returns.
     High active addresses relative to peers → adoption signal → LONG.

Output: data/active_addresses.parquet
    Index: DatetimeIndex (tz-naive UTC midnight, daily)
    Columns: Binance instrument codes (BTCUSDT_PERP, ETHUSDT_PERP, ...)
    Values: float64 (daily active address count, NaN where not available)

Usage:
    python scripts/download_active_addresses.py
    python scripts/download_active_addresses.py --output data/active_addresses.parquet
"""

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

# Mapping: Binance instrument code → CoinMetrics asset ID
# Notes:
#   - DOTUSDT_PERP/dot coverage ends Jun 2022 (usable for backtests up to that date)
#   - XTZUSDT_PERP/xtz coverage ends Apr 2022 (usable for backtests up to that date)
#   - GASUSDT_PERP/gas and NEOUSDT_PERP/neo coverage ends Oct 2025
#   - BNBUSDT_PERP excluded (data ends 2019)
#   - LRCUSDT_PERP → lrc_eth (Loopring on Ethereum)
#   - POLUSDT_PERP → pol_eth (Polygon on Ethereum)
#   - VETUSDT_PERP → vet_eth (VeChain, Ethereum token)
INSTR_TO_CM = {
    'AAVEUSDT_PERP': 'aave',
    'ADAUSDT_PERP': 'ada',
    'ALGOUSDT_PERP': 'algo',
    'AVAXUSDT_PERP': 'avaxc',
    'BATUSDT_PERP': 'bat',
    'BCHUSDT_PERP': 'bch',
    'BSVUSDT_PERP': 'bsv',
    'BTCUSDT_PERP': 'btc',
    'COMPUSDT_PERP': 'comp',
    'CRVUSDT_PERP': 'crv',
    'CVCUSDT_PERP': 'cvc',
    'DASHUSDT_PERP': 'dash',
    'DOGEUSDT_PERP': 'doge',
    'DOTUSDT_PERP': 'dot',
    'ETCUSDT_PERP': 'etc',
    'ETHUSDT_PERP': 'eth',
    'GASUSDT_PERP': 'gas',
    'ICPUSDT_PERP': 'icp',
    'KNCUSDT_PERP': 'knc',
    'LDOUSDT_PERP': 'ldo',
    'LINKUSDT_PERP': 'link',
    'LPTUSDT_PERP': 'lpt',
    'LRCUSDT_PERP': 'lrc_eth',
    'LTCUSDT_PERP': 'ltc',
    'MANAUSDT_PERP': 'mana',
    'NEOUSDT_PERP': 'neo',
    'POLUSDT_PERP': 'pol_eth',
    'POWRUSDT_PERP': 'powr',
    'QNTUSDT_PERP': 'qnt',
    'SNXUSDT_PERP': 'snx',
    'SUSHIUSDT_PERP': 'sushi',
    'TRXUSDT_PERP': 'trx',
    'UMAUSDT_PERP': 'uma',
    'UNIUSDT_PERP': 'uni',
    'VETUSDT_PERP': 'vet_eth',
    'XLMUSDT_PERP': 'xlm',
    'XRPUSDT_PERP': 'xrp',
    'XTZUSDT_PERP': 'xtz',
    'XVGUSDT_PERP': 'xvg',
    'YFIUSDT_PERP': 'yfi',
    'ZECUSDT_PERP': 'zec',
    'ZRXUSDT_PERP': 'zrx',
}

# Reverse map: CM asset ID → Binance instrument code
CM_TO_INSTR = {v: k for k, v in INSTR_TO_CM.items()}

# CoinMetrics Community API base URL
CM_BASE = "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics"


def fetch_batch(cm_assets: list, start_time: str = "2020-01-01") -> list:
    """
    Fetch AdrActCnt for a batch of CoinMetrics asset IDs.

    CoinMetrics allows comma-joined assets in a single request.
    Returns list of API records: [{asset, time, AdrActCnt}, ...]
    """
    assets_str = ",".join(cm_assets)
    base_url = (
        f"{CM_BASE}?assets={assets_str}"
        f"&metrics=AdrActCnt"
        f"&frequency=1d"
        f"&start_time={start_time}"
        f"&page_size=10000"
    )

    all_records = []
    url = base_url
    page_num = 1

    while True:
        print(f"    Page {page_num} ({len(cm_assets)} assets) ...", end="", flush=True)
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; research-bot)"},
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                raw = resp.read()
        except Exception as e:
            print(f" ERROR: {e}")
            raise

        payload = json.loads(raw)
        records = payload.get("data", [])
        all_records.extend(records)
        print(f" got {len(records)} records")

        next_token = payload.get("next_page_token", None)
        if not next_token:
            break
        url = base_url + f"&next_page_token={next_token}"
        page_num += 1
        time.sleep(0.2)  # polite rate limiting

    return all_records


def fetch_all_active_addresses(start_time: str = "2020-01-01") -> list:
    """
    Fetch AdrActCnt for all instruments in INSTR_TO_CM.

    Fetches in batches of 10 assets (CoinMetrics limit per request for free tier).
    """
    cm_assets = list(set(INSTR_TO_CM.values()))  # deduplicated CM asset IDs
    print(f"Fetching AdrActCnt for {len(cm_assets)} CoinMetrics assets in batches ...")

    # Batch size of 10 to be safe with Community API
    batch_size = 10
    all_records = []
    for i in range(0, len(cm_assets), batch_size):
        batch = cm_assets[i:i + batch_size]
        print(f"  Batch {i//batch_size + 1}/{(len(cm_assets) + batch_size - 1)//batch_size}: {batch}")
        try:
            records = fetch_batch(batch, start_time=start_time)
            all_records.extend(records)
        except Exception as e:
            print(f"  WARNING: Batch failed: {e}")
            # Try assets individually
            for asset in batch:
                print(f"  Retrying individual: {asset}")
                try:
                    records = fetch_batch([asset], start_time=start_time)
                    all_records.extend(records)
                    time.sleep(0.5)
                except Exception as e2:
                    print(f"  SKIP {asset}: {e2}")
        time.sleep(0.3)

    return all_records


def parse_records(records: list) -> pd.DataFrame:
    """
    Parse API response into a wide DataFrame.

    Input records format:
        {"asset": "btc", "time": "2020-01-01T00:00:00.000000000Z", "AdrActCnt": "12345"}

    Output: wide DataFrame, index=date, columns=Binance instrument codes.
    """
    rows = []
    skipped = 0
    for rec in records:
        cm_asset = rec.get("asset", "")
        time_str = rec.get("time", "")
        adr_str = rec.get("AdrActCnt")

        # Skip records without coverage
        if not adr_str or adr_str in ("", "null", None):
            skipped += 1
            continue

        try:
            adr_count = float(adr_str)
        except (ValueError, TypeError):
            skipped += 1
            continue

        # Map CM asset → Binance instrument code
        instr = CM_TO_INSTR.get(cm_asset)
        if instr is None:
            continue  # Unknown asset, skip

        # Parse ISO 8601 timestamp, normalize to midnight UTC, strip tz
        try:
            dt = pd.Timestamp(time_str).normalize().tz_localize(None)
        except Exception:
            skipped += 1
            continue

        rows.append({
            "date": dt,
            "instrument": instr,
            "AdrActCnt": adr_count,
        })

    if skipped:
        print(f"  Skipped {skipped} records with missing/invalid data")

    if not rows:
        return pd.DataFrame()

    long_df = pd.DataFrame(rows)
    long_df = long_df.drop_duplicates(subset=["date", "instrument"])

    # Pivot to wide format: index=date, columns=instrument
    wide_df = long_df.pivot(index="date", columns="instrument", values="AdrActCnt")
    wide_df.index = pd.DatetimeIndex(wide_df.index)
    wide_df = wide_df.sort_index()

    # Drop rows where all instruments are NaN
    wide_df = wide_df.dropna(how="all")

    return wide_df


def print_coverage_summary(df: pd.DataFrame) -> None:
    """Print per-instrument coverage statistics."""
    print()
    print("=" * 72)
    print("ACTIVE ADDRESS COVERAGE SUMMARY")
    print("=" * 72)
    print(f"  Total date range: {df.index.min().date()} to {df.index.max().date()}")
    print(f"  Total calendar days: {len(df)}")
    print(f"  Instruments covered: {len(df.columns)}")
    print()
    print(f"  {'Instrument':30}  {'First':12}  {'Last':12}  {'Days':>6}  {'Coverage':>9}")
    print("  " + "─" * 70)

    total_days = len(df)
    for instr in sorted(df.columns):
        col = df[instr].dropna()
        if len(col) == 0:
            print(f"  {instr:30}  {'NO DATA':>12}  {'':>12}  {'':>6}  {'':>9}")
            continue
        first = col.index.min().date()
        last = col.index.max().date()
        n_days = len(col)
        coverage = n_days / total_days * 100
        print(f"  {instr:30}  {str(first):12}  {str(last):12}  {n_days:>6}  {coverage:>8.1f}%")

    print("=" * 72)
    print()


def save_parquet(df: pd.DataFrame, output_path: str) -> None:
    """Save wide DataFrame to parquet."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path)
    print(f"Saved {len(df)} rows × {len(df.columns)} instruments to {path}")


def main():
    parser = argparse.ArgumentParser(
        description="Download active address counts from CoinMetrics Community API",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--output",
        default="data/active_addresses.parquet",
        help="Output path for parquet file (default: data/active_addresses.parquet)",
    )
    parser.add_argument(
        "--start-time",
        default="2020-01-01",
        help="Start date for data download (default: 2020-01-01)",
    )
    args = parser.parse_args()

    print(f"Downloading AdrActCnt from CoinMetrics Community API ...")
    print(f"  Instruments: {len(INSTR_TO_CM)} ({len(set(INSTR_TO_CM.values()))} unique CM assets)")
    print(f"  Start: {args.start_time}")
    print(f"  Output: {args.output}")
    print()

    records = fetch_all_active_addresses(start_time=args.start_time)

    if not records:
        print("ERROR: No data returned from API")
        sys.exit(1)

    print(f"\nTotal records fetched: {len(records)}")

    df = parse_records(records)
    if df.empty:
        print("ERROR: No valid records parsed")
        sys.exit(1)

    print(f"Parsed to {df.shape[0]} rows × {df.shape[1]} instruments")

    print_coverage_summary(df)

    save_parquet(df, args.output)
    print("Done.")


if __name__ == "__main__":
    main()
