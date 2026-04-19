"""
Backfill daily USDT trading volume for all instruments in the dataset
that are not covered by Binance Vision ZIPs.

Uses BinanceAPIClient.fetch_klines() which handles rate limiting, retry,
and caching in data/raw/binance/api_cache/.

Fetches in two chunks to stay within the 1500-row API limit:
  chunk1: 2020-01-01 → 2023-12-31  (~1461 days)
  chunk2: 2024-01-01 → today        (~850 days as of 2026-04)

Existing Vision-sourced volume data (30 instruments) is preserved as-is.
New API data is appended and deduped by (instrument, date).

Usage:
  python scripts/backfill_volume.py
  python scripts/backfill_volume.py --dry-run          # list missing instruments only
  python scripts/backfill_volume.py --refresh          # re-fetch even already-covered instruments
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from sysdata.crypto.binance_api import BinanceAPIClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("backfill_volume")

_DATASET_PATH = Path("data/dataset_538registry_6yr_jagged.parquet")
_VOLUME_PATH = Path("data/binance_volume_daily.parquet")
_CACHE_DIR = Path("data/raw/binance/api_cache")

# Two chunks to stay under the 1500-row API limit
_CHUNK1_START = date(2020, 1, 1)
_CHUNK1_END = date(2023, 12, 31)
_CHUNK2_START = date(2024, 1, 1)
_CHUNK2_END = date.today()


def _instrument_to_symbol(instrument: str) -> str:
    return instrument.replace("_PERP", "")


def _get_all_dataset_instruments() -> list[str]:
    df = pd.read_parquet(_DATASET_PATH, columns=["instrument"])
    return sorted(df["instrument"].unique())


def _get_covered_instruments() -> set[str]:
    if not _VOLUME_PATH.exists():
        return set()
    vol = pd.read_parquet(_VOLUME_PATH)
    return set(vol["instrument"].unique())


def _fetch_volume_for_instrument(
    client: BinanceAPIClient, instrument: str
) -> pd.DataFrame:
    symbol = _instrument_to_symbol(instrument)
    rows = []
    for start, end in [(_CHUNK1_START, _CHUNK1_END), (_CHUNK2_START, _CHUNK2_END)]:
        try:
            klines = client.fetch_klines(symbol, start, end, use_cache=True)
            if klines.empty or "quote_volume" not in klines.columns:
                continue
            chunk = klines[["date", "quote_volume"]].copy()
            chunk = chunk.dropna(subset=["quote_volume"])
            chunk = chunk[chunk["quote_volume"] > 0]
            rows.append(chunk)
        except Exception as exc:
            logger.warning(f"{instrument} ({symbol}) chunk {start}→{end} failed: {exc}")

    if not rows:
        return pd.DataFrame(columns=["date", "quote_volume", "instrument"])

    df = pd.concat(rows, ignore_index=True)
    df["date"] = pd.to_datetime(df["date"])
    df["instrument"] = instrument
    df = df.drop_duplicates(subset=["date"]).sort_values("date")
    return df[["date", "quote_volume", "instrument"]]


def _get_latest_dates() -> dict[str, date]:
    """Return the max date per instrument in the existing volume parquet."""
    if not _VOLUME_PATH.exists():
        return {}
    vol = pd.read_parquet(_VOLUME_PATH)
    vol["date"] = pd.to_datetime(vol["date"])
    return vol.groupby("instrument")["date"].max().apply(lambda x: x.date()).to_dict()


def _fetch_volume_incremental(
    client: BinanceAPIClient, instrument: str, since: date
) -> pd.DataFrame:
    """Fetch volume for instrument from `since` date to today."""
    symbol = _instrument_to_symbol(instrument)
    today = date.today()
    if since >= today:
        return pd.DataFrame(columns=["date", "quote_volume", "instrument"])
    try:
        klines = client.fetch_klines(symbol, since, today, use_cache=False)
        if klines.empty or "quote_volume" not in klines.columns:
            return pd.DataFrame(columns=["date", "quote_volume", "instrument"])
        chunk = klines[["date", "quote_volume"]].copy()
        chunk = chunk.dropna(subset=["quote_volume"])
        chunk = chunk[chunk["quote_volume"] > 0]
        chunk["date"] = pd.to_datetime(chunk["date"])
        chunk["instrument"] = instrument
        return chunk[["date", "quote_volume", "instrument"]]
    except Exception as exc:
        logger.warning(f"{instrument} incremental fetch failed: {exc}")
        return pd.DataFrame(columns=["date", "quote_volume", "instrument"])


def run_backfill(dry_run: bool = False, refresh: bool = False, incremental: bool = False) -> None:
    all_instruments = _get_all_dataset_instruments()
    covered = _get_covered_instruments()

    if incremental:
        # Update tail for all already-covered instruments
        latest_dates = _get_latest_dates()
        to_update = [i for i in all_instruments if i in covered]
        new_missing = [i for i in all_instruments if i not in covered]
        logger.info(
            f"Incremental mode: updating tail for {len(to_update)} instruments, "
            f"{len(new_missing)} newly missing (will full-backfill)"
        )
        if dry_run:
            logger.info("Dry run — would update tail for all covered instruments + backfill missing.")
            return

        client = BinanceAPIClient(cache_dir=_CACHE_DIR, sleep_ms=100)
        new_rows: list[pd.DataFrame] = []
        failed: list[str] = []

        for i, instrument in enumerate(to_update, 1):
            since = latest_dates.get(instrument, _CHUNK1_START)
            logger.info(f"[{i}/{len(to_update)}] {instrument} from {since}")
            vol_df = _fetch_volume_incremental(client, instrument, since)
            if not vol_df.empty:
                new_rows.append(vol_df)

        for instrument in new_missing:
            logger.info(f"Full backfill: {instrument}")
            vol_df = _fetch_volume_for_instrument(client, instrument)
            if vol_df.empty:
                failed.append(instrument)
            else:
                new_rows.append(vol_df)

    elif refresh:
        to_fetch = all_instruments
        logger.info(f"--refresh: fetching all {len(to_fetch)} instruments")
        if dry_run:
            for inst in to_fetch:
                print(f"  {inst}")
            return
        client = BinanceAPIClient(cache_dir=_CACHE_DIR, sleep_ms=100)
        new_rows, failed = [], []
        for i, instrument in enumerate(to_fetch, 1):
            logger.info(f"[{i}/{len(to_fetch)}] {instrument}")
            vol_df = _fetch_volume_for_instrument(client, instrument)
            if vol_df.empty:
                failed.append(instrument)
            else:
                new_rows.append(vol_df)

    else:
        to_fetch = [i for i in all_instruments if i not in covered]
        logger.info(
            f"{len(covered)} already covered, {len(to_fetch)} to fetch "
            f"(of {len(all_instruments)} total)"
        )
        if dry_run:
            logger.info("Dry run — instruments that would be fetched:")
            for inst in to_fetch:
                print(f"  {inst}")
            return
        if not to_fetch:
            logger.info("Nothing to fetch — all instruments already covered.")
            return
        client = BinanceAPIClient(cache_dir=_CACHE_DIR, sleep_ms=100)
        new_rows, failed = [], []
        for i, instrument in enumerate(to_fetch, 1):
            logger.info(f"[{i}/{len(to_fetch)}] {instrument}")
            vol_df = _fetch_volume_for_instrument(client, instrument)
            if vol_df.empty:
                logger.warning(f"  {instrument}: no data returned")
                failed.append(instrument)
            else:
                new_rows.append(vol_df)
                logger.info(
                    f"  {instrument}: {len(vol_df)} days "
                    f"{vol_df['date'].min().date()} → {vol_df['date'].max().date()}"
                )

    if not new_rows:
        logger.info("No new data fetched.")
        return

    # Load existing data and append
    if _VOLUME_PATH.exists():
        existing = pd.read_parquet(_VOLUME_PATH)
        existing["date"] = pd.to_datetime(existing["date"])
        combined = pd.concat([existing] + new_rows, ignore_index=True)
    else:
        combined = pd.concat(new_rows, ignore_index=True)

    combined = (
        combined.drop_duplicates(subset=["instrument", "date"])
        .sort_values(["instrument", "date"])
        .reset_index(drop=True)
    )

    _VOLUME_PATH.parent.mkdir(parents=True, exist_ok=True)
    combined.to_parquet(_VOLUME_PATH, index=False)

    logger.info(
        f"\nWrote {len(combined)} rows, {combined['instrument'].nunique()} instruments → {_VOLUME_PATH}"
    )

    if 'failed' in dir() and failed:
        logger.warning(f"\nFailed ({len(failed)}): {failed}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill volume for all dataset instruments")
    parser.add_argument("--dry-run", action="store_true", help="List missing instruments without fetching")
    parser.add_argument("--refresh", action="store_true", help="Re-fetch all instruments, not just missing")
    parser.add_argument("--incremental", action="store_true", help="Update tail only for already-covered instruments (daily cadence)")
    args = parser.parse_args()
    run_backfill(dry_run=args.dry_run, refresh=args.refresh, incremental=args.incremental)


if __name__ == "__main__":
    main()
