#!/usr/bin/env python3
"""
Download Binance USDT-M perpetual futures historical data

Binance Data Vision structure:
  - /daily/    → files named YYYY-MM-DD (individual days)
  - /monthly/  → files named YYYY-MM (monthly aggregates)

This script uses --year and --months CLI args, so it downloads monthly files.
BASE_URL must be .../monthly/ to match the YYYY-MM filename format.

Usage:
    python scripts/download_binance_data.py --symbols BTCUSDT --year 2023 --months 1
    python scripts/download_binance_data.py --symbols BTCUSDT ETHUSDT --year 2023
    python scripts/download_binance_data.py --symbols BTCUSDT --year 2023 --months 1 2 3 --verify-checksums
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
from datetime import datetime

# Constants
BASE_URL = "https://data.binance.vision/data/futures/um/monthly"

# Sanity check: URL must match filename format
# We use YYYY-MM filenames (monthly aggregates), so BASE_URL must contain "/monthly"
# If BASE_URL contains "/daily" but we build YYYY-MM filenames, we'll get 404s
if '/monthly' not in BASE_URL:
    raise ValueError(
        f"BASE_URL must contain '/monthly' for YYYY-MM filename format. "
        f"Current BASE_URL: {BASE_URL}"
    )

RETRY_ATTEMPTS = 3
RETRY_BACKOFF_BASE = 2  # seconds (exponential backoff: 2s, 4s, 8s)
TIMEOUT = 60  # seconds (single socket timeout for urllib)
USER_AGENT = "Mozilla/5.0 (compatible; BinanceDataDownloader/1.0)"

# Recommended symbols (Layer A perpetuals from Phase 1)
# Script accepts any symbol ending in 'USDT' but will warn if not in this list
RECOMMENDED_SYMBOLS = ['BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'SOLUSDT', 'XRPUSDT']


def download_file(
    url: str,
    output_path: Path,
    skip_existing: bool = True,
    verify_checksums: bool = False,
    strict: bool = False,
    verbose: bool = False
) -> dict:
    """
    Download file from URL to output path with validation

    Atomic write strategy:
        - Write to temp file in same directory: {output_path}.tmp
        - On success: os.replace(temp_path, output_path)
        - On failure: Remove temp file
        - Rationale: Prevents partial/corrupt files in output directory

    Args:
        url: Source URL
        output_path: Destination file path
        skip_existing: If True, skip if file already exists
        verify_checksums: If True, download and verify SHA256 checksum
        strict: If True, treat 404 as failure (collect, exit 1 at end)
        verbose: If True, show extra diagnostics (retry logs, HTTP status codes)

    Returns:
        dict with keys:
            status: 'downloaded' | 'skipped_existing' | 'skipped_404' | 'failed'
            size_bytes: int (file size in bytes)
            checksum_status: 'not_requested' | 'verified' | 'unavailable' | 'failed'
            error: str | None (error message if failed/skipped_404)
    """
    # Check if file exists
    if skip_existing and output_path.exists():
        size_bytes = output_path.stat().st_size
        return {
            'status': 'skipped_existing',
            'size_bytes': size_bytes,
            'checksum_status': 'not_requested',
            'error': None
        }

    # Create parent directory if needed
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Temp file for atomic write
    temp_path = output_path.with_suffix(output_path.suffix + '.tmp')

    # Try download with retries
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            # Create request with headers
            req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})

            if verbose and attempt > 1:
                print(f"  Retry attempt {attempt}/{RETRY_ATTEMPTS}...")

            # Download file
            with urllib.request.urlopen(req, timeout=TIMEOUT) as response:
                if verbose:
                    print(f"  HTTP {response.status} {response.reason}")

                # Write to temp file
                with open(temp_path, 'wb') as f:
                    f.write(response.read())

            # Validate ZIP integrity
            if not validate_zip_integrity(temp_path):
                if temp_path.exists():
                    temp_path.unlink()
                return {
                    'status': 'failed',
                    'size_bytes': 0,
                    'checksum_status': 'not_requested',
                    'error': 'ZIP integrity check failed'
                }

            # Verify checksum if requested
            checksum_status = 'not_requested'
            checksum_error = None
            if verify_checksums:
                checksum_url = build_checksum_url(url)
                checksum_status, checksum_error = verify_checksum_file(temp_path, checksum_url, verbose)
                if checksum_status == 'failed':
                    if temp_path.exists():
                        temp_path.unlink()
                    return {
                        'status': 'failed',
                        'size_bytes': 0,
                        'checksum_status': checksum_status,
                        'error': checksum_error
                    }

            # Atomic rename
            os.replace(temp_path, output_path)
            size_bytes = output_path.stat().st_size

            return {
                'status': 'downloaded',
                'size_bytes': size_bytes,
                'checksum_status': checksum_status,
                'error': None
            }

        except urllib.error.HTTPError as e:
            if e.code == 404:
                # 404 handling per strict mode policy
                if temp_path.exists():
                    temp_path.unlink()
                return {
                    'status': 'skipped_404',
                    'size_bytes': 0,
                    'checksum_status': 'not_requested',
                    'error': f'HTTP 404 Not Found - file may not exist yet'
                }
            elif e.code == 403:
                if temp_path.exists():
                    temp_path.unlink()
                error_msg = f'HTTP 403 Forbidden: Access denied. Check if IP is blocked or UA is required.'
                return {
                    'status': 'failed',
                    'size_bytes': 0,
                    'checksum_status': 'not_requested',
                    'error': error_msg
                }
            elif e.code == 429:
                # Rate limiting - parse Retry-After header if present
                retry_after = e.headers.get('Retry-After', '60')
                try:
                    retry_seconds = int(retry_after)
                except ValueError:
                    retry_seconds = 60
                error_msg = f'rate_limited: HTTP 429 Too Many Requests. Wait {retry_seconds}s and retry.'
                if temp_path.exists():
                    temp_path.unlink()
                return {
                    'status': 'failed',
                    'size_bytes': 0,
                    'checksum_status': 'not_requested',
                    'error': error_msg,
                    'retry_after_seconds': retry_seconds
                }
            elif 500 <= e.code < 600:
                # Server error - retry with backoff
                if verbose:
                    print(f"  HTTP {e.code} {e.reason} - retrying...")
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
                        'checksum_status': 'not_requested',
                        'error': f'HTTP {e.code} {e.reason} after {RETRY_ATTEMPTS} retries'
                    }
            else:
                # Other HTTP error
                if temp_path.exists():
                    temp_path.unlink()
                return {
                    'status': 'failed',
                    'size_bytes': 0,
                    'checksum_status': 'not_requested',
                    'error': f'HTTP {e.code} {e.reason}'
                }

        except (urllib.error.URLError, OSError, TimeoutError) as e:
            # Network error or timeout - retry with backoff
            if verbose:
                print(f"  Network error: {e}")
            if attempt < RETRY_ATTEMPTS:
                delay = RETRY_BACKOFF_BASE ** attempt
                if verbose:
                    print(f"  Retrying in {delay}s...")
                time.sleep(delay)
                continue
            else:
                if temp_path.exists():
                    temp_path.unlink()
                return {
                    'status': 'failed',
                    'size_bytes': 0,
                    'checksum_status': 'not_requested',
                    'error': f'Network timeout after {RETRY_ATTEMPTS} retries'
                }

    # Should not reach here
    if temp_path.exists():
        temp_path.unlink()
    return {
        'status': 'failed',
        'size_bytes': 0,
        'checksum_status': 'not_requested',
        'error': 'Download failed after all retries'
    }


def validate_zip_integrity(path: Path) -> bool:
    """
    Validate ZIP file integrity without extracting

    Returns:
        True if valid, False if corrupt
    """
    try:
        # Check file size
        if path.stat().st_size == 0:
            print(f"  ✗ ZIP file is empty")
            return False

        # Validate ZIP structure
        with zipfile.ZipFile(path) as z:
            # Check ZIP integrity
            bad_file = z.testzip()
            if bad_file is not None:
                print(f"  ✗ Corrupt ZIP: {bad_file}")
                return False

            # Check ZIP contains data
            namelist = z.namelist()
            if not namelist:
                print(f"  ✗ Empty ZIP file")
                return False

            # Expected CSV name
            expected_csv = path.stem + '.csv'

            # Relaxed check: accept exact match OR a single CSV file
            csv_files = [f for f in namelist if f.endswith('.csv')]
            if expected_csv in namelist:
                # Perfect: expected CSV found
                pass
            elif len(csv_files) == 1:
                # Acceptable: single CSV with different name
                print(f"  ⚠ Warning: ZIP contains '{csv_files[0]}' instead of expected '{expected_csv}'. Proceeding anyway.")
            else:
                print(f"  ✗ Expected CSV '{expected_csv}' not found. ZIP contains {len(csv_files)} CSV files: {csv_files}")
                return False

        return True

    except zipfile.BadZipFile:
        print(f"  ✗ Invalid ZIP file format")
        return False
    except Exception as e:
        print(f"  ✗ ZIP validation error: {e}")
        return False


def verify_checksum_file(zip_path: Path, checksum_url: str, verbose: bool = False) -> Tuple[str, Optional[str]]:
    """
    Download CHECKSUM file and verify ZIP SHA256

    Returns:
        tuple: (checksum_status, error_msg)
            checksum_status: 'verified' | 'unavailable' | 'failed'
            error_msg: str | None (error description if status != 'verified')
    """
    try:
        # Download CHECKSUM file
        req = urllib.request.Request(checksum_url, headers={'User-Agent': USER_AGENT})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as response:
            checksum_content = response.read().decode('utf-8').strip()

        if verbose:
            print(f"  Downloaded CHECKSUM file")

        # Parse CHECKSUM file (format: "{SHA256} {filename}")
        parts = checksum_content.split()
        if len(parts) != 2:
            return ('failed', f'CHECKSUM file parse error: expected "{{SHA256}} {{filename}}", got: {checksum_content}')

        expected_hash, expected_filename = parts

        # Verify filename matches
        if expected_filename != zip_path.name:
            return ('failed', f'CHECKSUM file contains entry for {expected_filename}, expected {zip_path.name}')

        # Compute SHA256 of downloaded ZIP
        sha256_hash = hashlib.sha256()
        with open(zip_path, 'rb') as f:
            for chunk in iter(lambda: f.read(8192), b''):
                sha256_hash.update(chunk)
        actual_hash = sha256_hash.hexdigest()

        # Compare hashes (case-insensitive)
        if actual_hash.lower() != expected_hash.lower():
            return ('failed', f'SHA256 mismatch: expected {expected_hash}, got {actual_hash}')

        return ('verified', None)

    except urllib.error.HTTPError as e:
        if e.code == 404:
            # CHECKSUM file not found - log warning but don't fail
            return ('unavailable', 'CHECKSUM file not found (404), cannot verify integrity')
        else:
            return ('failed', f'Failed to download CHECKSUM: HTTP {e.code} {e.reason}')

    except Exception as e:
        return ('failed', f'Checksum verification error: {e}')


def normalize_and_validate_symbol(symbol: str) -> str:
    """
    Normalize and validate Binance symbol format

    Normalization:
        - Strip whitespace
        - Convert to uppercase

    Validation:
        - Must end with "USDT"
        - Must not contain "_PERP" suffix (common mistake)
        - Length: 6-12 characters (typical range)

    Args:
        symbol: Input symbol (any case, may have whitespace)

    Returns:
        Normalized symbol (uppercase, stripped)

    Raises:
        ValueError: If symbol is invalid
    """
    # Normalize
    symbol = symbol.strip().upper()

    # Validate length
    if len(symbol) < 6 or len(symbol) > 12:
        raise ValueError(f"Invalid symbol '{symbol}': length must be 6-12 characters")

    # Check for _PERP suffix (common mistake)
    if '_PERP' in symbol:
        base_symbol = symbol.replace('_PERP', '')
        raise ValueError(
            f"Invalid symbol '{symbol}': Use Binance symbol '{base_symbol}' not '{symbol}'. "
            f"The _PERP suffix is for internal instrument IDs, not Binance download URLs."
        )

    # Must end with USDT
    if not symbol.endswith('USDT'):
        raise ValueError(f"Invalid symbol '{symbol}': must end with 'USDT'")

    return symbol


def validate_year(year: int) -> None:
    """
    Validate year is reasonable

    Raises:
        ValueError: If year < 2017
    """
    if year < 2017:
        raise ValueError(f"Invalid year {year}: Binance futures launched in 2017")

    current_year = datetime.now().year
    if year > current_year + 1:
        print(f"⚠ Warning: Year {year} is in the future (current year: {current_year}). Proceeding anyway.")


def build_kline_url(symbol: str, year: int, month: int) -> str:
    """Build URL for klines ZIP file"""
    month_str = f"{month:02d}"
    return f"{BASE_URL}/klines/{symbol}/1d/{symbol}-1d-{year}-{month_str}.zip"


def build_funding_url(symbol: str, year: int, month: int) -> str:
    """Build URL for funding rates ZIP file"""
    month_str = f"{month:02d}"
    return f"{BASE_URL}/fundingRate/{symbol}/{symbol}-fundingRate-{year}-{month_str}.zip"


def build_checksum_url(data_url: str) -> str:
    """Build checksum URL by appending .CHECKSUM"""
    return data_url + '.CHECKSUM'


def build_output_path(base_dir: Path, data_type: str, symbol: str, year: int, month: int) -> Path:
    """Build output path for downloaded file"""
    month_str = f"{month:02d}"
    if data_type == 'klines':
        filename = f"{symbol}-1d-{year}-{month_str}.zip"
        return base_dir / 'klines' / symbol / filename
    elif data_type == 'funding':
        filename = f"{symbol}-fundingRate-{year}-{month_str}.zip"
        return base_dir / 'funding_rates' / symbol / filename
    else:
        raise ValueError(f"Unknown data type: {data_type}")


def download_symbol_month(
    symbol: str,
    year: int,
    month: int,
    data_dir: Path,
    skip_existing: bool = True,
    verify_checksums: bool = False,
    strict: bool = False,
    verbose: bool = False
) -> dict:
    """
    Download both klines and funding for one symbol-month

    Returns:
        dict with keys matching status taxonomy:
            downloaded: list of Path objects
            skipped_existing: list of Path objects
            skipped_404: list of Path objects
            failed: list of (Path, error_msg) tuples
    """
    results = {
        'downloaded': [],
        'skipped_existing': [],
        'skipped_404': [],
        'failed': []
    }

    # Download klines
    kline_url = build_kline_url(symbol, year, month)
    kline_path = build_output_path(data_dir, 'klines', symbol, year, month)

    print(f"\n[1/2] Klines: {kline_path.name}")
    print(f"  URL: {kline_url}")
    print(f"  → {kline_path}")

    kline_result = download_file(kline_url, kline_path, skip_existing, verify_checksums, strict, verbose)

    # Process kline result
    status_icon = {
        'downloaded': '✓',
        'skipped_existing': '○',
        'skipped_404': '⚠',
        'failed': '✗'
    }
    icon = status_icon.get(kline_result['status'], '?')

    if kline_result['status'] == 'downloaded':
        size_str = format_size(kline_result['size_bytes'])
        print(f"  Status: {icon} Downloaded ({size_str})")
        print(f"  Validation: ✓ ZIP integrity OK")
        if kline_result['checksum_status'] == 'verified':
            print(f"  Checksum: ✓ SHA256 verified")
        elif kline_result['checksum_status'] == 'unavailable':
            print(f"  Checksum: ⚠ CHECKSUM file not found (404), cannot verify integrity")
        results['downloaded'].append(kline_path)
    elif kline_result['status'] == 'skipped_existing':
        size_str = format_size(kline_result['size_bytes'])
        print(f"  Status: {icon} Skipped (already exists, {size_str})")
        results['skipped_existing'].append(kline_path)
    elif kline_result['status'] == 'skipped_404':
        print(f"  Status: {icon} Skipped ({kline_result['error']})")
        results['skipped_404'].append(kline_path)
    elif kline_result['status'] == 'failed':
        print(f"  Status: {icon} Failed ({kline_result['error']})")
        results['failed'].append((kline_path, kline_result['error']))

    # Download funding rates
    funding_url = build_funding_url(symbol, year, month)
    funding_path = build_output_path(data_dir, 'funding', symbol, year, month)

    print(f"\n[2/2] Funding: {funding_path.name}")
    print(f"  URL: {funding_url}")
    print(f"  → {funding_path}")

    funding_result = download_file(funding_url, funding_path, skip_existing, verify_checksums, strict, verbose)

    # Process funding result
    icon = status_icon.get(funding_result['status'], '?')

    if funding_result['status'] == 'downloaded':
        size_str = format_size(funding_result['size_bytes'])
        print(f"  Status: {icon} Downloaded ({size_str})")
        print(f"  Validation: ✓ ZIP integrity OK")
        if funding_result['checksum_status'] == 'verified':
            print(f"  Checksum: ✓ SHA256 verified")
        elif funding_result['checksum_status'] == 'unavailable':
            print(f"  Checksum: ⚠ CHECKSUM file not found (404), cannot verify integrity")
        results['downloaded'].append(funding_path)
    elif funding_result['status'] == 'skipped_existing':
        size_str = format_size(funding_result['size_bytes'])
        print(f"  Status: {icon} Skipped (already exists, {size_str})")
        results['skipped_existing'].append(funding_path)
    elif funding_result['status'] == 'skipped_404':
        print(f"  Status: {icon} Skipped ({funding_result['error']})")
        results['skipped_404'].append(funding_path)
    elif funding_result['status'] == 'failed':
        print(f"  Status: {icon} Failed ({funding_result['error']})")
        results['failed'].append((funding_path, funding_result['error']))

    return results


def format_size(size_bytes: int) -> str:
    """Format bytes to human-readable string (KB, MB, GB)"""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"


def main():
    parser = argparse.ArgumentParser(
        description='Download Binance USDT-M perpetual futures data',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Download BTCUSDT for January 2023
  %(prog)s --symbols BTCUSDT --year 2023 --months 1

  # Download multiple months
  %(prog)s --symbols BTCUSDT --year 2023 --months 1 2 3

  # Download full year (all 12 months)
  %(prog)s --symbols BTCUSDT --year 2023

  # Download multiple symbols
  %(prog)s --symbols BTCUSDT ETHUSDT --year 2023 --months 1

  # Force redownload (overwrite existing)
  %(prog)s --symbols BTCUSDT --year 2023 --months 1 --force

  # Strict mode (fail on 404 or any error)
  %(prog)s --symbols BTCUSDT --year 2023 --months 1 --strict

  # With checksum verification (slower but validates integrity)
  %(prog)s --symbols BTCUSDT --year 2023 --months 1 --verify-checksums
        """
    )

    parser.add_argument(
        '--symbols',
        nargs='+',
        default=['BTCUSDT'],
        help='Binance symbols to download (e.g., BTCUSDT ETHUSDT). Use Binance symbols WITHOUT _PERP suffix. Default: BTCUSDT'
    )
    parser.add_argument(
        '--year',
        type=int,
        required=True,
        help='Year to download (must be >= 2017)'
    )
    parser.add_argument(
        '--months',
        nargs='+',
        type=int,
        help='Months to download (1-12). If omitted, downloads all 12 months. Example: --months 1 or --months 1 2 3'
    )
    parser.add_argument(
        '--data-dir',
        type=Path,
        default=Path('data/raw/binance'),
        help='Base data directory. Default: data/raw/binance'
    )
    parser.add_argument(
        '--skip-existing',
        action='store_true',
        default=True,
        help='Skip files that already exist (default: True)'
    )
    parser.add_argument(
        '--force',
        action='store_true',
        help='Force redownload (overwrite existing files)'
    )
    parser.add_argument(
        '--strict',
        action='store_true',
        help='Treat 404 as failure, exit 1 at end if any occurred (default: False, 404 logged as warning)'
    )
    parser.add_argument(
        '--verify-checksums',
        action='store_true',
        help='Download and verify SHA256 checksums (default: False, slower but validates integrity)'
    )
    parser.add_argument(
        '--verbose',
        action='store_true',
        help='Show extra diagnostics (retry logs, HTTP status codes, detailed error traces)'
    )

    args = parser.parse_args()

    # Handle --force flag
    if args.force:
        skip_existing = False
    else:
        skip_existing = args.skip_existing

    # Default to all 12 months if not specified
    months = args.months if args.months else list(range(1, 13))

    print("Binance Data Downloader v1.0")
    print("=" * 50)
    print(f"Symbols: {', '.join(args.symbols)}")
    print(f"Year: {args.year}")
    print(f"Months: {months} ({len(months)} month{'s' if len(months) > 1 else ''})")
    print(f"Output directory: {args.data_dir}/")
    if skip_existing:
        print("Mode: Skip existing files (use --force to overwrite)")
    else:
        print("Mode: Force redownload (overwrite existing)")
    if args.strict:
        print("Strict mode: 404 errors will be treated as failures")
    if args.verify_checksums:
        print("Checksum verification: Enabled (slower but validates integrity)")
    print()

    # Validate inputs
    print("Validating inputs...")

    # Normalize and validate symbols
    normalized_symbols = []
    for symbol in args.symbols:
        try:
            normalized_symbol = normalize_and_validate_symbol(symbol)
            normalized_symbols.append(normalized_symbol)
            print(f"  ✓ Symbol: {normalized_symbol} (valid Binance symbol)")

            # Warn if not in recommended list
            if normalized_symbol not in RECOMMENDED_SYMBOLS:
                print(f"    ⚠ Warning: {normalized_symbol} not in recommended list ({', '.join(RECOMMENDED_SYMBOLS)}). Proceeding anyway.")
        except ValueError as e:
            print(f"  ✗ {e}")
            sys.exit(1)

    # Validate year
    try:
        validate_year(args.year)
        print(f"  ✓ Year: {args.year} (valid)")
    except ValueError as e:
        print(f"  ✗ {e}")
        sys.exit(1)

    # Validate months
    for month in months:
        if month < 1 or month > 12:
            print(f"  ✗ Invalid month: {month} (must be 1-12)")
            sys.exit(1)
    print(f"  ✓ Months: {months} (valid)")

    print()

    # Track overall results
    total_downloaded = []
    total_skipped_existing = []
    total_skipped_404 = []
    total_failed = []

    # Download files
    for symbol in normalized_symbols:
        for month in months:
            print(f"\n{'=' * 50}")
            print(f"Downloading {symbol} ({args.year}-{month:02d})...")
            print('=' * 50)

            results = download_symbol_month(
                symbol,
                args.year,
                month,
                args.data_dir,
                skip_existing,
                args.verify_checksums,
                args.strict,
                args.verbose
            )

            # Accumulate results
            total_downloaded.extend(results['downloaded'])
            total_skipped_existing.extend(results['skipped_existing'])
            total_skipped_404.extend(results['skipped_404'])
            total_failed.extend(results['failed'])

    # Print summary
    print(f"\n{'=' * 50}")
    print("Summary")
    print('=' * 50)

    # Calculate total size
    total_size = sum(p.stat().st_size for p in total_downloaded if p.exists())

    # Downloaded files
    if total_downloaded:
        print(f"✓ Downloaded: {len(total_downloaded)} file{'s' if len(total_downloaded) != 1 else ''} ({format_size(total_size)} total)")
        for path in total_downloaded:
            print(f"  - {path}")
    else:
        print(f"Downloaded: 0 files")

    print()

    # Skipped (existing) files
    if total_skipped_existing:
        total_existing_size = sum(p.stat().st_size for p in total_skipped_existing if p.exists())
        print(f"Skipped (existing): {len(total_skipped_existing)} file{'s' if len(total_skipped_existing) != 1 else ''} ({format_size(total_existing_size)} total)")
        for path in total_skipped_existing:
            print(f"  - {path}")
    else:
        print(f"Skipped (existing): 0 files")

    print()

    # Skipped (404) files
    if total_skipped_404:
        print(f"Skipped (404): {len(total_skipped_404)} file{'s' if len(total_skipped_404) != 1 else ''}")
        for path in total_skipped_404:
            print(f"  - {path}")
    else:
        print(f"Skipped (404): 0 files")

    print()

    # Failed files
    if total_failed:
        print(f"✗ Failed: {len(total_failed)} file{'s' if len(total_failed) != 1 else ''}")
        for path, error in total_failed:
            print(f"  - {path}")
            print(f"    Error: {error}")
    else:
        print(f"Failed: 0 files")

    print()

    # Determine exit code
    has_failures = len(total_failed) > 0
    has_404_in_strict = args.strict and len(total_skipped_404) > 0

    if not has_failures and not has_404_in_strict:
        print("✓ All downloads completed successfully!")
        if total_downloaded:
            print("\nNext steps:")
            print("  # Build dataset from downloaded data:")
            print(f"  python scripts/build_example_dataset.py --source real \\")
            print(f"    --instruments {normalized_symbols[0]}_PERP \\")
            print(f"    --start-date {args.year}-{months[0]:02d}-01 --end-date {args.year}-{months[-1]:02d}-28")
            print()
            print("  # Validate output:")
            print("  python scripts/validate_real_data.py data/example_crypto_perps.parquet")
        sys.exit(0)
    else:
        if has_failures:
            print(f"✗ {len(total_failed)} file(s) failed to download")
        if has_404_in_strict:
            print(f"✗ {len(total_skipped_404)} file(s) not found (404) in strict mode")
        sys.exit(1)


if __name__ == '__main__':
    main()
