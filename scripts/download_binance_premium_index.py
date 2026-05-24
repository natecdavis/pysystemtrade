#!/usr/bin/env python3
"""
Download Binance USDM perpetual premium-index daily klines from Binance Vision.

The premium index = (mark_price - index_price) / index_price, sampled by
Binance every 5 seconds and aggregated to daily OHLCV. It IS the perpetual
basis: positive when mark > spot (longs paying), negative when mark < spot
(shorts paying). Mean-reversion of |basis| > ~50bp is the C2c trading
hypothesis (basis_mr_5).

Vision URL pattern:
  https://data.binance.vision/data/futures/um/daily/premiumIndexKlines/{SYM}/1d/{SYM}-1d-{DATE}.zip

Each daily zip contains a single CSV row with the standard kline schema
(open_time, open, high, low, close, volume, close_time, ...). For premium
index, `volume` is always 0 — only the price columns matter. We use `close`
as the day's terminal basis.

Usage:
    python scripts/download_binance_premium_index.py \\
        --start-date 2020-01-01 \\
        --end-date 2026-05-01 \\
        --output-dir envs/dev/data/binance_premium_index_raw \\
        --symbols-file envs/dev/out/prestage_oi_symbols.txt \\
        --workers 10
"""

from __future__ import annotations

import argparse
import logging
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
from typing import List

import requests
from tqdm import tqdm

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


class BinancePremiumIndexDownloader:
    BASE_URL = "https://data.binance.vision/data/futures/um/daily/premiumIndexKlines"
    INTERVAL = "1d"

    def __init__(self, output_dir: str):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.stats = {"total_files": 0, "downloaded": 0, "skipped": 0, "failed": 0, "total_bytes": 0}
        self._stats_lock = threading.Lock()

    def construct_url(self, symbol: str, date: str) -> str:
        return f"{self.BASE_URL}/{symbol}/{self.INTERVAL}/{symbol}-{self.INTERVAL}-{date}.zip"

    def get_dates(self, start_date: str, end_date: str) -> List[str]:
        start = datetime.strptime(start_date, "%Y-%m-%d")
        end = datetime.strptime(end_date, "%Y-%m-%d")
        dates = []
        current = start
        while current <= end:
            dates.append(current.strftime("%Y-%m-%d"))
            current += timedelta(days=1)
        return dates

    def download_file(self, url: str, output_path: Path, skip_existing: bool = True) -> bool:
        if skip_existing and output_path.exists():
            with self._stats_lock:
                self.stats["skipped"] += 1
            return False
        try:
            response = requests.get(url, timeout=30)
            if response.status_code == 404:
                with self._stats_lock:
                    self.stats["skipped"] += 1
                return False
            response.raise_for_status()
            with open(output_path, "wb") as f:
                f.write(response.content)
            with self._stats_lock:
                self.stats["downloaded"] += 1
                self.stats["total_bytes"] += len(response.content)
            return True
        except requests.exceptions.RequestException as e:
            logger.warning(f"Failed to download {url}: {e}")
            with self._stats_lock:
                self.stats["failed"] += 1
            return False

    def download_symbol_data(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        skip_existing: bool = True,
        rate_limit_delay: float = 0.01,
    ) -> None:
        dates = self.get_dates(start_date, end_date)
        symbol_dir = self.output_dir / symbol
        symbol_dir.mkdir(parents=True, exist_ok=True)
        for date in dates:
            url = self.construct_url(symbol, date)
            output_path = symbol_dir / f"{symbol}-{self.INTERVAL}-{date}.zip"
            with self._stats_lock:
                self.stats["total_files"] += 1
            self.download_file(url, output_path, skip_existing)
            if rate_limit_delay > 0:
                time.sleep(rate_limit_delay)

    def download_all_symbols(
        self,
        symbols: List[str],
        start_date: str,
        end_date: str,
        skip_existing: bool = True,
        rate_limit_delay: float = 0.01,
        max_workers: int = 10,
    ) -> None:
        logger.info(
            f"Starting premium-index download for {len(symbols)} symbols "
            f"({max_workers} workers, {start_date} → {end_date})"
        )
        progress = tqdm(total=len(symbols), desc="Overall Progress")

        def _one(symbol: str) -> None:
            try:
                self.download_symbol_data(symbol, start_date, end_date, skip_existing, rate_limit_delay)
            except Exception as e:
                logger.error(f"Error downloading {symbol}: {e}")
                with self._stats_lock:
                    self.stats["failed"] += 1
            finally:
                progress.update(1)

        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures = {ex.submit(_one, s): s for s in symbols}
            for fut in as_completed(futures):
                pass

        progress.close()
        self.print_summary()

    def print_summary(self) -> None:
        logger.info("=" * 60)
        logger.info("PREMIUM-INDEX DOWNLOAD SUMMARY")
        logger.info("=" * 60)
        logger.info(f"Files attempted:     {self.stats['total_files']:,}")
        logger.info(f"Downloaded:          {self.stats['downloaded']:,}")
        logger.info(f"Skipped (existing):  {self.stats['skipped']:,}")
        logger.info(f"Failed/missing:      {self.stats['failed']:,}")
        logger.info(f"Total bytes:         {self.stats['total_bytes'] / (1024**2):.2f} MB")
        logger.info("=" * 60)


def load_symbols_from_file(filepath: str) -> List[str]:
    with open(filepath) as f:
        return [line.strip() for line in f if line.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--output-dir", required=True)
    sym_group = parser.add_mutually_exclusive_group(required=True)
    sym_group.add_argument("--symbols-file", help="Text file with one symbol per line")
    sym_group.add_argument("--symbols", nargs="+", help="Explicit symbol list")
    parser.add_argument("--workers", type=int, default=10)
    parser.add_argument("--rate-limit-delay", type=float, default=0.01)
    parser.add_argument("--no-skip-existing", action="store_true", help="Re-download existing files")
    args = parser.parse_args()

    if args.symbols_file:
        symbols = load_symbols_from_file(args.symbols_file)
    else:
        symbols = args.symbols

    downloader = BinancePremiumIndexDownloader(args.output_dir)
    downloader.download_all_symbols(
        symbols=symbols,
        start_date=args.start_date,
        end_date=args.end_date,
        skip_existing=not args.no_skip_existing,
        rate_limit_delay=args.rate_limit_delay,
        max_workers=args.workers,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
