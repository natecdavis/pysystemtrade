#!/usr/bin/env python
"""
Download Open Interest data from Binance Public Data Archive.

This script automates downloading historical OI data for USDT perpetual futures
from the official Binance public data repository.

Data source: https://github.com/binance/binance-public-data
Data format: Monthly CSV files with columns: timestamp, symbol, sumOpenInterest, sumOpenInterestValue

Usage:
    python scripts/download_binance_oi_data.py \
        --start-date 2020-01-01 \
        --end-date 2026-01-31 \
        --output-dir data/binance_oi_raw \
        --symbols-file data/crypto/instrument_list.txt

    # Download for specific symbols only
    python scripts/download_binance_oi_data.py \
        --start-date 2020-01-01 \
        --end-date 2026-01-31 \
        --output-dir data/binance_oi_raw \
        --symbols BTCUSDT ETHUSDT SOLUSDT

Author: Phase 2 OI Data Implementation
Date: 2026-02-21
"""

import argparse
import logging
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional
import time

import pandas as pd
import requests
from tqdm import tqdm

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class BinanceOIDownloader:
    """Downloads Open Interest data from Binance Public Data Archive."""

    BASE_URL = "https://data.binance.vision/data/futures/um/daily/metrics"

    def __init__(self, output_dir: str):
        """
        Initialize downloader.

        Args:
            output_dir: Directory to save downloaded CSV files
        """
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # Track statistics
        self.stats = {
            'total_files': 0,
            'downloaded': 0,
            'skipped': 0,
            'failed': 0,
            'total_bytes': 0
        }

    def get_monthly_dates(self, start_date: str, end_date: str) -> List[str]:
        """
        Generate list of year-month strings between start and end dates.

        Args:
            start_date: Start date (YYYY-MM-DD)
            end_date: End date (YYYY-MM-DD)

        Returns:
            List of year-month strings (YYYY-MM)
        """
        start = datetime.strptime(start_date, '%Y-%m-%d')
        end = datetime.strptime(end_date, '%Y-%m-%d')

        months = []
        current = start.replace(day=1)

        while current <= end:
            months.append(current.strftime('%Y-%m'))
            # Move to next month
            if current.month == 12:
                current = current.replace(year=current.year + 1, month=1)
            else:
                current = current.replace(month=current.month + 1)

        return months

    def get_daily_dates(self, year_month: str) -> List[str]:
        """
        Generate list of daily dates for a given month.

        Args:
            year_month: Year-month string (YYYY-MM)

        Returns:
            List of date strings (YYYY-MM-DD)
        """
        year, month = map(int, year_month.split('-'))
        start = datetime(year, month, 1)

        # Get last day of month
        if month == 12:
            end = datetime(year + 1, 1, 1) - timedelta(days=1)
        else:
            end = datetime(year, month + 1, 1) - timedelta(days=1)

        dates = []
        current = start
        while current <= end:
            dates.append(current.strftime('%Y-%m-%d'))
            current += timedelta(days=1)

        return dates

    def construct_url(self, symbol: str, date: str) -> str:
        """
        Construct download URL for a specific symbol and date.

        Binance URL format:
        https://data.binance.vision/data/futures/um/daily/metrics/{SYMBOL}/{SYMBOL}-metrics-{DATE}.zip

        The metrics files contain 5-minute data with columns:
        - create_time: timestamp
        - symbol: trading pair
        - sum_open_interest: OI in base currency
        - sum_open_interest_value: OI in USD notional
        - count_toptrader_long_short_ratio: top trader LS ratio sample count
        - sum_toptrader_long_short_ratio: top trader LS ratio
        - count_long_short_ratio: all trader LS ratio sample count
        - sum_taker_long_short_vol_ratio: taker buy/sell volume ratio

        Args:
            symbol: Trading pair (e.g., BTCUSDT)
            date: Date string (YYYY-MM-DD)

        Returns:
            Download URL
        """
        return f"{self.BASE_URL}/{symbol}/{symbol}-metrics-{date}.zip"

    def download_file(self, url: str, output_path: Path, skip_existing: bool = True) -> bool:
        """
        Download a single file.

        Args:
            url: URL to download
            output_path: Path to save file
            skip_existing: Skip if file already exists

        Returns:
            True if downloaded, False if skipped or failed
        """
        # Check if file exists
        if skip_existing and output_path.exists():
            logger.debug(f"Skipping {output_path.name} (already exists)")
            self.stats['skipped'] += 1
            return False

        try:
            # Send request
            response = requests.get(url, timeout=30)

            # Check if file exists on server
            if response.status_code == 404:
                logger.debug(f"File not found: {url}")
                self.stats['skipped'] += 1
                return False

            # Check for other errors
            response.raise_for_status()

            # Save file
            with open(output_path, 'wb') as f:
                f.write(response.content)

            self.stats['downloaded'] += 1
            self.stats['total_bytes'] += len(response.content)
            logger.debug(f"Downloaded {output_path.name} ({len(response.content):,} bytes)")

            return True

        except requests.exceptions.RequestException as e:
            logger.warning(f"Failed to download {url}: {e}")
            self.stats['failed'] += 1
            return False

    def download_symbol_data(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        skip_existing: bool = True,
        rate_limit_delay: float = 0.01
    ) -> None:
        """
        Download OI data for a single symbol across date range.

        Args:
            symbol: Trading pair (e.g., BTCUSDT)
            start_date: Start date (YYYY-MM-DD)
            end_date: End date (YYYY-MM-DD)
            skip_existing: Skip files that already exist
            rate_limit_delay: Delay between requests in seconds (default: 0.01)
        """
        # Get all months in range
        months = self.get_monthly_dates(start_date, end_date)

        # Create symbol directory
        symbol_dir = self.output_dir / symbol
        symbol_dir.mkdir(parents=True, exist_ok=True)

        logger.info(f"Downloading {symbol} data ({len(months)} months)")

        # Download each month's daily files
        for year_month in tqdm(months, desc=f"{symbol}", leave=False):
            dates = self.get_daily_dates(year_month)

            for date in dates:
                # Skip if date is before start_date
                if date < start_date:
                    continue

                # Stop if date exceeds end_date
                if date > end_date:
                    break

                # Construct URL and output path
                url = self.construct_url(symbol, date)
                output_path = symbol_dir / f"{symbol}-metrics-{date}.zip"

                self.stats['total_files'] += 1

                # Download file
                self.download_file(url, output_path, skip_existing)

                # Rate limiting (be nice to Binance servers)
                if rate_limit_delay > 0:
                    time.sleep(rate_limit_delay)

    def download_all_symbols(
        self,
        symbols: List[str],
        start_date: str,
        end_date: str,
        skip_existing: bool = True,
        rate_limit_delay: float = 0.01
    ) -> None:
        """
        Download OI data for multiple symbols.

        Args:
            symbols: List of trading pairs
            start_date: Start date (YYYY-MM-DD)
            end_date: End date (YYYY-MM-DD)
            skip_existing: Skip files that already exist
            rate_limit_delay: Delay between requests in seconds (default: 0.01)
        """
        logger.info(f"Starting download for {len(symbols)} symbols")
        logger.info(f"Date range: {start_date} to {end_date}")
        logger.info(f"Output directory: {self.output_dir}")

        # Download each symbol
        for symbol in tqdm(symbols, desc="Overall Progress"):
            try:
                self.download_symbol_data(symbol, start_date, end_date, skip_existing, rate_limit_delay)
            except Exception as e:
                logger.error(f"Error downloading {symbol}: {e}")
                self.stats['failed'] += 1

        # Print summary
        self.print_summary()

    def print_summary(self) -> None:
        """Print download statistics."""
        logger.info("=" * 60)
        logger.info("DOWNLOAD SUMMARY")
        logger.info("=" * 60)
        logger.info(f"Total files attempted: {self.stats['total_files']:,}")
        logger.info(f"Downloaded: {self.stats['downloaded']:,}")
        logger.info(f"Skipped (already exists): {self.stats['skipped']:,}")
        logger.info(f"Failed: {self.stats['failed']:,}")
        logger.info(f"Total data downloaded: {self.stats['total_bytes'] / (1024**2):.2f} MB")
        logger.info("=" * 60)


def load_symbols_from_file(filepath: str) -> List[str]:
    """
    Load list of symbols from a text file (one per line).

    Args:
        filepath: Path to symbols file

    Returns:
        List of symbols
    """
    with open(filepath, 'r') as f:
        symbols = [line.strip() for line in f if line.strip()]

    logger.info(f"Loaded {len(symbols)} symbols from {filepath}")
    return symbols


def get_default_symbol_list() -> List[str]:
    """
    Get default list of major USDT perpetuals.

    Returns:
        List of major symbols
    """
    # Top 50 by market cap (as of 2026)
    return [
        'BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'SOLUSDT', 'XRPUSDT',
        'ADAUSDT', 'AVAXUSDT', 'DOGEUSDT', 'DOTUSDT', 'MATICUSDT',
        'LINKUSDT', 'UNIUSDT', 'ATOMUSDT', 'LTCUSDT', 'ETCUSDT',
        'NEARUSDT', 'FILUSDT', 'APTUSDT', 'ARBUSDT', 'OPUSDT',
        'ICPUSDT', 'THETAUSDT', 'ALGOUSDT', 'VETUSDT', 'EOSUSDT',
        'AAVEUSDT', 'MKRUSDT', 'GRTUSDT', 'SANDUSDT', 'MANAUSDT',
        'AXSUSDT', 'FTMUSDT', 'KSMUSDT', 'EGLDUSDT', 'ROSEUSDT',
        'CHZUSDT', 'ENJUSDT', 'ZILUSDT', 'COMPUSDT', 'SUSHIUSDT',
        '1INCHUSDT', 'CRVUSDT', 'SNXUSDT', 'YFIUSDT', 'RUNEUSDT',
        'STXUSDT', 'FLOWUSDT', 'CELOUSDT', 'HBARUSDT', 'WAVESUSDT'
    ]


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description='Download Binance Open Interest data',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Download for default symbol list (top 50)
  python scripts/download_binance_oi_data.py \\
    --start-date 2020-01-01 \\
    --end-date 2026-01-31 \\
    --output-dir data/binance_oi_raw

  # Download for specific symbols
  python scripts/download_binance_oi_data.py \\
    --start-date 2020-01-01 \\
    --end-date 2026-01-31 \\
    --output-dir data/binance_oi_raw \\
    --symbols BTCUSDT ETHUSDT SOLUSDT

  # Load symbols from file
  python scripts/download_binance_oi_data.py \\
    --start-date 2020-01-01 \\
    --end-date 2026-01-31 \\
    --output-dir data/binance_oi_raw \\
    --symbols-file data/crypto/instrument_list.txt
        """
    )

    parser.add_argument(
        '--start-date',
        type=str,
        required=True,
        help='Start date (YYYY-MM-DD)'
    )

    parser.add_argument(
        '--end-date',
        type=str,
        required=True,
        help='End date (YYYY-MM-DD)'
    )

    parser.add_argument(
        '--output-dir',
        type=str,
        default='data/binance_oi_raw',
        help='Output directory for downloaded files (default: data/binance_oi_raw)'
    )

    parser.add_argument(
        '--symbols',
        type=str,
        nargs='+',
        help='Specific symbols to download (e.g., BTCUSDT ETHUSDT)'
    )

    parser.add_argument(
        '--symbols-file',
        type=str,
        help='Path to file containing symbols (one per line)'
    )

    parser.add_argument(
        '--skip-existing',
        action='store_true',
        default=True,
        help='Skip files that already exist (default: True)'
    )

    parser.add_argument(
        '--rate-limit',
        type=float,
        default=0.01,
        help='Delay between requests in seconds (default: 0.01, use 0 for no delay)'
    )

    parser.add_argument(
        '--verbose',
        action='store_true',
        help='Enable verbose logging'
    )

    args = parser.parse_args()

    # Set logging level
    if args.verbose:
        logger.setLevel(logging.DEBUG)

    # Determine symbol list
    if args.symbols_file:
        symbols = load_symbols_from_file(args.symbols_file)
    elif args.symbols:
        symbols = args.symbols
    else:
        logger.info("No symbols specified, using default top 50 list")
        symbols = get_default_symbol_list()

    # Validate dates
    try:
        datetime.strptime(args.start_date, '%Y-%m-%d')
        datetime.strptime(args.end_date, '%Y-%m-%d')
    except ValueError as e:
        logger.error(f"Invalid date format: {e}")
        sys.exit(1)

    # Create downloader and run
    downloader = BinanceOIDownloader(args.output_dir)
    downloader.download_all_symbols(
        symbols=symbols,
        start_date=args.start_date,
        end_date=args.end_date,
        skip_existing=args.skip_existing,
        rate_limit_delay=args.rate_limit
    )

    logger.info("Download complete!")


if __name__ == '__main__':
    main()
