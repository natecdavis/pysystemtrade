#!/usr/bin/env python3
"""
Extended Binance Data Downloader with Monthly + Daily Support

This script downloads historical data through the current date by combining:
- Monthly ZIPs for complete months
- Daily ZIPs for the current/incomplete month

Usage:
    # Download all 15 instruments through 2026-01-26
    python scripts/download_binance_extended.py --symbols BTCUSDT ETHUSDT BNBUSDT XRPUSDT LTCUSDT EOSUSDT BCHUSDT LINKUSDT SOLUSDT DOTUSDT ADAUSDT UNIUSDT MATICUSDT DOGEUSDT AVAXUSDT --start-date 2025-01-01 --end-date 2026-01-26

    # Download single symbol for 2025 + Jan 2026
    python scripts/download_binance_extended.py --symbols BTCUSDT --start-date 2025-01-01 --end-date 2026-01-26
"""

import argparse
import urllib.request
import urllib.error
from pathlib import Path
from typing import List, Optional, Tuple
import sys
import time
import zipfile
import hashlib
import os
from datetime import datetime, date, timedelta
import calendar

# Constants
BASE_URL_MONTHLY = "https://data.binance.vision/data/futures/um/monthly"
BASE_URL_DAILY = "https://data.binance.vision/data/futures/um/daily"

RETRY_ATTEMPTS = 3
RETRY_BACKOFF_BASE = 2
TIMEOUT = 60
USER_AGENT = "Mozilla/5.0 (compatible; BinanceDataDownloader/1.0)"

# All 15 instruments from Phase 1
ALL_SYMBOLS = [
    'BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'XRPUSDT', 'LTCUSDT', 'EOSUSDT', 'BCHUSDT',
    'LINKUSDT', 'SOLUSDT', 'DOTUSDT', 'ADAUSDT', 'UNIUSDT', 'MATICUSDT', 'DOGEUSDT', 'AVAXUSDT'
]


def download_file(
    url: str,
    output_path: Path,
    skip_existing: bool = True,
    verbose: bool = False
) -> dict:
    """
    Download file from URL to output path with validation

    Returns:
        dict with keys: status, size_bytes, error
    """
    if skip_existing and output_path.exists():
        size_bytes = output_path.stat().st_size
        return {
            'status': 'skipped_existing',
            'size_bytes': size_bytes,
            'error': None
        }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = output_path.with_suffix(output_path.suffix + '.tmp')

    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})

            if verbose and attempt > 1:
                print(f"  Retry attempt {attempt}/{RETRY_ATTEMPTS}...")

            with urllib.request.urlopen(req, timeout=TIMEOUT) as response:
                if verbose:
                    print(f"  HTTP {response.status} {response.reason}")

                with open(temp_path, 'wb') as f:
                    f.write(response.read())

            # Validate ZIP integrity
            if not validate_zip_integrity(temp_path, verbose):
                if temp_path.exists():
                    temp_path.unlink()
                return {
                    'status': 'failed',
                    'size_bytes': 0,
                    'error': 'ZIP integrity check failed'
                }

            # Atomic rename
            os.replace(temp_path, output_path)
            size_bytes = output_path.stat().st_size

            return {
                'status': 'downloaded',
                'size_bytes': size_bytes,
                'error': None
            }

        except urllib.error.HTTPError as e:
            if e.code == 404:
                if temp_path.exists():
                    temp_path.unlink()
                return {
                    'status': 'skipped_404',
                    'size_bytes': 0,
                    'error': f'HTTP 404 Not Found'
                }
            elif e.code == 403:
                if temp_path.exists():
                    temp_path.unlink()
                return {
                    'status': 'failed',
                    'size_bytes': 0,
                    'error': f'HTTP 403 Forbidden'
                }
            elif 500 <= e.code < 600:
                if verbose:
                    print(f"  HTTP {e.code} - retrying...")
                if attempt < RETRY_ATTEMPTS:
                    delay = RETRY_BACKOFF_BASE ** attempt
                    time.sleep(delay)
                    continue
                else:
                    if temp_path.exists():
                        temp_path.unlink()
                    return {
                        'status': 'failed',
                        'size_bytes': 0,
                        'error': f'HTTP {e.code} after {RETRY_ATTEMPTS} retries'
                    }
            else:
                if temp_path.exists():
                    temp_path.unlink()
                return {
                    'status': 'failed',
                    'size_bytes': 0,
                    'error': f'HTTP {e.code} {e.reason}'
                }

        except (urllib.error.URLError, OSError, TimeoutError) as e:
            if verbose:
                print(f"  Network error: {e}")
            if attempt < RETRY_ATTEMPTS:
                delay = RETRY_BACKOFF_BASE ** attempt
                time.sleep(delay)
                continue
            else:
                if temp_path.exists():
                    temp_path.unlink()
                return {
                    'status': 'failed',
                    'size_bytes': 0,
                    'error': f'Network timeout after {RETRY_ATTEMPTS} retries'
                }

    if temp_path.exists():
        temp_path.unlink()
    return {
        'status': 'failed',
        'size_bytes': 0,
        'error': 'Download failed after all retries'
    }


def validate_zip_integrity(path: Path, verbose: bool = False) -> bool:
    """Validate ZIP file integrity"""
    try:
        if path.stat().st_size == 0:
            if verbose:
                print(f"  ✗ ZIP file is empty")
            return False

        with zipfile.ZipFile(path) as z:
            bad_file = z.testzip()
            if bad_file is not None:
                if verbose:
                    print(f"  ✗ Corrupt ZIP: {bad_file}")
                return False

            namelist = z.namelist()
            if not namelist:
                if verbose:
                    print(f"  ✗ Empty ZIP file")
                return False

        return True

    except zipfile.BadZipFile:
        if verbose:
            print(f"  ✗ Invalid ZIP file format")
        return False
    except Exception as e:
        if verbose:
            print(f"  ✗ ZIP validation error: {e}")
        return False


def build_monthly_kline_url(symbol: str, year: int, month: int) -> str:
    """Build URL for monthly klines ZIP"""
    month_str = f"{month:02d}"
    return f"{BASE_URL_MONTHLY}/klines/{symbol}/1d/{symbol}-1d-{year}-{month_str}.zip"


def build_monthly_funding_url(symbol: str, year: int, month: int) -> str:
    """Build URL for monthly funding rates ZIP"""
    month_str = f"{month:02d}"
    return f"{BASE_URL_MONTHLY}/fundingRate/{symbol}/{symbol}-fundingRate-{year}-{month_str}.zip"


def build_daily_kline_url(symbol: str, date_obj: date) -> str:
    """Build URL for daily klines ZIP"""
    date_str = date_obj.strftime("%Y-%m-%d")
    return f"{BASE_URL_DAILY}/klines/{symbol}/1d/{symbol}-1d-{date_str}.zip"


def build_daily_funding_url(symbol: str, date_obj: date) -> str:
    """Build URL for daily funding rates ZIP"""
    date_str = date_obj.strftime("%Y-%m-%d")
    return f"{BASE_URL_DAILY}/fundingRate/{symbol}/{symbol}-fundingRate-{date_str}.zip"


def build_monthly_output_path(base_dir: Path, data_type: str, symbol: str, year: int, month: int) -> Path:
    """Build output path for monthly file"""
    month_str = f"{month:02d}"
    if data_type == 'klines':
        filename = f"{symbol}-1d-{year}-{month_str}.zip"
        return base_dir / 'klines' / symbol / filename
    elif data_type == 'funding':
        filename = f"{symbol}-fundingRate-{year}-{month_str}.zip"
        return base_dir / 'funding_rates' / symbol / filename
    else:
        raise ValueError(f"Unknown data type: {data_type}")


def build_daily_output_path(base_dir: Path, data_type: str, symbol: str, date_obj: date) -> Path:
    """Build output path for daily file"""
    date_str = date_obj.strftime("%Y-%m-%d")
    if data_type == 'klines':
        filename = f"{symbol}-1d-{date_str}.zip"
        return base_dir / 'klines' / symbol / filename
    elif data_type == 'funding':
        filename = f"{symbol}-fundingRate-{date_str}.zip"
        return base_dir / 'funding_rates' / symbol / filename
    else:
        raise ValueError(f"Unknown data type: {data_type}")


def download_monthly(
    symbol: str,
    year: int,
    month: int,
    data_dir: Path,
    skip_existing: bool = True,
    verbose: bool = False
) -> dict:
    """Download monthly klines and funding rates for one month"""
    results = {
        'downloaded': [],
        'skipped_existing': [],
        'skipped_404': [],
        'failed': []
    }

    # Download klines
    kline_url = build_monthly_kline_url(symbol, year, month)
    kline_path = build_monthly_output_path(data_dir, 'klines', symbol, year, month)

    print(f"  [Monthly] Klines: {kline_path.name}")
    kline_result = download_file(kline_url, kline_path, skip_existing, verbose)
    process_result(kline_result, kline_path, results)

    # Download funding
    funding_url = build_monthly_funding_url(symbol, year, month)
    funding_path = build_monthly_output_path(data_dir, 'funding', symbol, year, month)

    print(f"  [Monthly] Funding: {funding_path.name}")
    funding_result = download_file(funding_url, funding_path, skip_existing, verbose)
    process_result(funding_result, funding_path, results)

    return results


def download_daily(
    symbol: str,
    date_obj: date,
    data_dir: Path,
    skip_existing: bool = True,
    verbose: bool = False
) -> dict:
    """Download daily klines and funding rates for one day"""
    results = {
        'downloaded': [],
        'skipped_existing': [],
        'skipped_404': [],
        'failed': []
    }

    # Download klines
    kline_url = build_daily_kline_url(symbol, date_obj)
    kline_path = build_daily_output_path(data_dir, 'klines', symbol, date_obj)

    print(f"  [Daily] Klines: {kline_path.name}")
    kline_result = download_file(kline_url, kline_path, skip_existing, verbose)
    process_result(kline_result, kline_path, results)

    # Download funding
    funding_url = build_daily_funding_url(symbol, date_obj)
    funding_path = build_daily_output_path(data_dir, 'funding', symbol, date_obj)

    print(f"  [Daily] Funding: {funding_path.name}")
    funding_result = download_file(funding_url, funding_path, skip_existing, verbose)
    process_result(funding_result, funding_path, results)

    return results


def process_result(result: dict, path: Path, results: dict):
    """Process download result and add to results dict"""
    status_icon = {
        'downloaded': '✓',
        'skipped_existing': '○',
        'skipped_404': '⚠',
        'failed': '✗'
    }
    icon = status_icon.get(result['status'], '?')

    if result['status'] == 'downloaded':
        size_str = format_size(result['size_bytes'])
        print(f"    Status: {icon} Downloaded ({size_str})")
        results['downloaded'].append(path)
    elif result['status'] == 'skipped_existing':
        size_str = format_size(result['size_bytes'])
        print(f"    Status: {icon} Skipped (exists, {size_str})")
        results['skipped_existing'].append(path)
    elif result['status'] == 'skipped_404':
        print(f"    Status: {icon} Skipped (404)")
        results['skipped_404'].append(path)
    elif result['status'] == 'failed':
        print(f"    Status: {icon} Failed ({result['error']})")
        results['failed'].append((path, result['error']))


def format_size(size_bytes: int) -> str:
    """Format bytes to human-readable string"""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"


def generate_date_ranges(start_date: date, end_date: date) -> Tuple[List[Tuple[int, int]], List[date]]:
    """
    Generate list of complete months and partial month days

    Returns:
        tuple: (complete_months, partial_days)
            complete_months: [(year, month), ...]
            partial_days: [date, date, ...]
    """
    complete_months = []
    partial_days = []

    current = start_date
    while current <= end_date:
        # Check if this is a complete month
        _, last_day = calendar.monthrange(current.year, current.month)
        month_start = date(current.year, current.month, 1)
        month_end = date(current.year, current.month, last_day)

        # Is the entire month within our range?
        if current == month_start and end_date >= month_end:
            # Complete month - use monthly download
            complete_months.append((current.year, current.month))
            # Jump to next month
            if current.month == 12:
                current = date(current.year + 1, 1, 1)
            else:
                current = date(current.year, current.month + 1, 1)
        else:
            # Partial month - use daily downloads for remaining days in this month
            month_last_day = min(month_end, end_date)
            day = current
            while day <= month_last_day:
                partial_days.append(day)
                day += timedelta(days=1)
            # Move to next month
            if current.month == 12:
                current = date(current.year + 1, 1, 1)
            else:
                current = date(current.year, current.month + 1, 1)

    return complete_months, partial_days


def main():
    parser = argparse.ArgumentParser(
        description='Download Binance data with monthly + daily support',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Download all 15 symbols from 2025-01-01 through 2026-01-26
  %(prog)s --symbols BTCUSDT ETHUSDT BNBUSDT XRPUSDT LTCUSDT EOSUSDT BCHUSDT LINKUSDT SOLUSDT DOTUSDT ADAUSDT UNIUSDT MATICUSDT DOGEUSDT AVAXUSDT --start-date 2025-01-01 --end-date 2026-01-26

  # Download single symbol
  %(prog)s --symbols BTCUSDT --start-date 2025-01-01 --end-date 2026-01-26

  # Download all 15 symbols (shorthand)
  %(prog)s --all --start-date 2025-01-01 --end-date 2026-01-26
        """
    )

    parser.add_argument(
        '--symbols',
        nargs='+',
        help='Binance symbols to download (e.g., BTCUSDT ETHUSDT). Use Binance symbols WITHOUT _PERP suffix.'
    )
    parser.add_argument(
        '--all',
        action='store_true',
        help='Download all 15 Phase 1 symbols (shorthand for full list)'
    )
    parser.add_argument(
        '--start-date',
        type=str,
        required=True,
        help='Start date (YYYY-MM-DD format, e.g., 2025-01-01)'
    )
    parser.add_argument(
        '--end-date',
        type=str,
        required=True,
        help='End date (YYYY-MM-DD format, e.g., 2026-01-26)'
    )
    parser.add_argument(
        '--data-dir',
        type=Path,
        default=Path('data/raw/binance'),
        help='Base data directory. Default: data/raw/binance'
    )
    parser.add_argument(
        '--force',
        action='store_true',
        help='Force redownload (overwrite existing files)'
    )
    parser.add_argument(
        '--verbose',
        action='store_true',
        help='Show extra diagnostics'
    )

    args = parser.parse_args()

    # Determine symbols
    if args.all:
        symbols = ALL_SYMBOLS
    elif args.symbols:
        symbols = [s.strip().upper() for s in args.symbols]
    else:
        print("Error: Must specify --symbols or --all")
        sys.exit(1)

    # Parse dates
    try:
        start_date = datetime.strptime(args.start_date, "%Y-%m-%d").date()
        end_date = datetime.strptime(args.end_date, "%Y-%m-%d").date()
    except ValueError as e:
        print(f"Error: Invalid date format: {e}")
        print("Use YYYY-MM-DD format (e.g., 2025-01-01)")
        sys.exit(1)

    if start_date > end_date:
        print(f"Error: start-date ({start_date}) must be before end-date ({end_date})")
        sys.exit(1)

    skip_existing = not args.force

    print("Binance Extended Downloader (Monthly + Daily)")
    print("=" * 60)
    print(f"Symbols: {', '.join(symbols)} ({len(symbols)} total)")
    print(f"Date range: {start_date} to {end_date}")
    print(f"Output directory: {args.data_dir}/")
    print(f"Mode: {'Force redownload' if args.force else 'Skip existing'}")
    print()

    # Generate date ranges
    print("Analyzing date range...")
    complete_months, partial_days = generate_date_ranges(start_date, end_date)

    print(f"  Complete months: {len(complete_months)}")
    for year, month in complete_months:
        print(f"    {year}-{month:02d}")

    print(f"  Partial month days: {len(partial_days)}")
    if partial_days:
        print(f"    {partial_days[0]} to {partial_days[-1]} ({len(partial_days)} days)")

    print()

    # Track results
    total_downloaded = []
    total_skipped_existing = []
    total_skipped_404 = []
    total_failed = []

    # Download files
    for symbol in symbols:
        print(f"\n{'=' * 60}")
        print(f"Downloading {symbol}")
        print('=' * 60)

        # Download complete months
        for year, month in complete_months:
            print(f"\n{symbol} ({year}-{month:02d}) - Monthly")
            results = download_monthly(symbol, year, month, args.data_dir, skip_existing, args.verbose)

            total_downloaded.extend(results['downloaded'])
            total_skipped_existing.extend(results['skipped_existing'])
            total_skipped_404.extend(results['skipped_404'])
            total_failed.extend(results['failed'])

        # Download partial days
        for day in partial_days:
            print(f"\n{symbol} ({day}) - Daily")
            results = download_daily(symbol, day, args.data_dir, skip_existing, args.verbose)

            total_downloaded.extend(results['downloaded'])
            total_skipped_existing.extend(results['skipped_existing'])
            total_skipped_404.extend(results['skipped_404'])
            total_failed.extend(results['failed'])

    # Print summary
    print(f"\n{'=' * 60}")
    print("Summary")
    print('=' * 60)

    total_size = sum(p.stat().st_size for p in total_downloaded if p.exists())

    if total_downloaded:
        print(f"✓ Downloaded: {len(total_downloaded)} files ({format_size(total_size)} total)")
    else:
        print(f"Downloaded: 0 files")

    if total_skipped_existing:
        total_existing_size = sum(p.stat().st_size for p in total_skipped_existing if p.exists())
        print(f"○ Skipped (existing): {len(total_skipped_existing)} files ({format_size(total_existing_size)} total)")
    else:
        print(f"Skipped (existing): 0 files")

    if total_skipped_404:
        print(f"⚠ Skipped (404): {len(total_skipped_404)} files")
        if args.verbose:
            for path in total_skipped_404:
                print(f"  - {path}")
    else:
        print(f"Skipped (404): 0 files")

    if total_failed:
        print(f"✗ Failed: {len(total_failed)} files")
        for path, error in total_failed:
            print(f"  - {path}")
            print(f"    Error: {error}")
    else:
        print(f"Failed: 0 files")

    print()

    if not total_failed:
        print("✓ All downloads completed successfully!")
        sys.exit(0)
    else:
        print(f"✗ {len(total_failed)} file(s) failed to download")
        sys.exit(1)


if __name__ == '__main__':
    main()
