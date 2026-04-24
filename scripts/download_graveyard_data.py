#!/usr/bin/env python3
"""
Download historical data for delisted Binance USDT-M perpetual futures.

Enumerates all symbols ever listed on Binance USDT-M perpetuals via the
Binance Vision S3 directory listing, computes the difference from the
active registry, and downloads monthly klines + funding rate ZIPs for
each delisted (graveyard) symbol.

Data is stored under data/raw/graveyard/ and will be processed by
scripts/build_sb_corrected_dataset.py.

Usage:
    python scripts/download_graveyard_data.py
    python scripts/download_graveyard_data.py --dry-run
    python scripts/download_graveyard_data.py --skip-existing
    python scripts/download_graveyard_data.py --graveyard-dir /path/to/graveyard
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.download_binance_data import download_file

KLINES_S3_PREFIX = "data/futures/um/monthly/klines/"
FUNDING_S3_PREFIX = "data/futures/um/monthly/fundingRate/"
S3_LISTING_BASE = (
    "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
    "?prefix={prefix}&delimiter=/"
)
VISION_BASE = "https://data.binance.vision"
REGISTRY_PATH = REPO_ROOT / "data/raw/metadata/binance_perp_registry.json"
DEFAULT_GRAVEYARD_DIR = REPO_ROOT / "data/raw/graveyard"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("download_graveyard")

USER_AGENT = "Mozilla/5.0 (compatible; BinanceDataDownloader/1.0)"
TIMEOUT = 30


def _fetch_xml(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return resp.read().decode("utf-8")


def _parse_s3_prefixes(xml_text: str) -> list[str]:
    """Extract <Prefix> values from S3 ListBucketResult XML."""
    root = ET.fromstring(xml_text)
    ns = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}
    prefixes = []
    for cp in root.findall("s3:CommonPrefixes", ns):
        p = cp.find("s3:Prefix", ns)
        if p is not None and p.text:
            prefixes.append(p.text)
    # Also check for truncation marker
    is_truncated_el = root.find("s3:IsTruncated", ns)
    next_marker_el = root.find("s3:NextMarker", ns)
    is_truncated = (
        is_truncated_el is not None
        and is_truncated_el.text.lower() == "true"
    )
    next_marker = next_marker_el.text if next_marker_el is not None else None
    return prefixes, is_truncated, next_marker


def enumerate_s3_symbols(prefix: str) -> set[str]:
    """
    List all symbol subdirectory names under a Binance Vision S3 prefix.

    Handles S3 pagination via marker parameter.

    Returns set of Binance symbols (e.g. {'BTCUSDT', 'ETHUSDT', ...}).
    """
    symbols = set()
    marker = None

    while True:
        url = S3_LISTING_BASE.format(prefix=prefix)
        if marker:
            url += f"&marker={urllib.parse.quote(marker)}"

        logger.info(f"Fetching S3 listing: {url}")
        try:
            xml_text = _fetch_xml(url)
        except Exception as e:
            logger.error(f"Failed to fetch S3 listing: {e}")
            raise

        prefixes, is_truncated, next_marker = _parse_s3_prefixes(xml_text)

        # Extract symbol from prefix like "data/futures/um/monthly/klines/BTCUSDT/"
        for p in prefixes:
            # Strip the leading prefix and trailing slash to get the symbol
            symbol = p[len(prefix):].rstrip("/")
            if symbol:
                symbols.add(symbol)

        logger.info(f"  Got {len(prefixes)} entries (total so far: {len(symbols)})")

        if not is_truncated or not next_marker:
            break
        marker = next_marker

    return symbols


def load_active_symbols() -> set[str]:
    """Load active Binance symbols from the local registry."""
    with open(REGISTRY_PATH) as f:
        registry = json.load(f)
    # instruments is a dict keyed by Binance symbol (e.g. 'BTCUSDT')
    return set(registry["instruments"].keys())


def month_iter(start_year: int = 2019, start_month: int = 1):
    """Yield (year, month) tuples from start up to and including current month."""
    today = date.today()
    y, m = start_year, start_month
    while (y, m) <= (today.year, today.month):
        yield y, m
        m += 1
        if m > 12:
            m = 1
            y += 1


def download_symbol_monthly(
    symbol: str,
    data_type: str,
    out_dir: Path,
    dry_run: bool = False,
    skip_existing: bool = True,
) -> dict:
    """
    Download all available monthly ZIPs for a symbol.

    Args:
        symbol: Binance symbol (e.g. 'LUNAUSDT')
        data_type: 'klines' or 'fundingRate'
        out_dir: Root graveyard directory
        dry_run: Print what would be downloaded, don't actually download
        skip_existing: Skip files that already exist

    Returns:
        dict with counts: downloaded, skipped_existing, skipped_404, failed
    """
    # Skip symbols with non-ASCII characters (novelty tokens like 龙虾USDT)
    try:
        symbol.encode("ascii")
    except UnicodeEncodeError:
        logger.info(f"  Skipping non-ASCII symbol: {symbol}")
        return {"downloaded": 0, "skipped_existing": 0, "skipped_404": 0, "failed": 0}

    if data_type == "klines":
        url_prefix = f"{VISION_BASE}/data/futures/um/monthly/klines/{symbol}/1d"
        file_suffix_fn = lambda y, m: f"{symbol}-1d-{y}-{m:02d}.zip"
        dest_dir = out_dir / "klines" / symbol
    elif data_type == "fundingRate":
        url_prefix = f"{VISION_BASE}/data/futures/um/monthly/fundingRate/{symbol}"
        file_suffix_fn = lambda y, m: f"{symbol}-fundingRate-{y}-{m:02d}.zip"
        dest_dir = out_dir / "funding_rates" / symbol
    else:
        raise ValueError(f"Unknown data_type: {data_type}")

    dest_dir.mkdir(parents=True, exist_ok=True)

    counts = {"downloaded": 0, "skipped_existing": 0, "skipped_404": 0, "failed": 0}

    for year, month in month_iter():
        filename = file_suffix_fn(year, month)
        url = f"{url_prefix}/{filename}"
        dest = dest_dir / filename

        if dry_run:
            print(f"  [dry-run] {url}")
            continue

        result = download_file(url, dest, skip_existing=skip_existing)
        status = result["status"]
        counts[status] = counts.get(status, 0) + 1

        if status == "downloaded":
            logger.info(f"  ✓ {filename} ({result['size_bytes'] / 1024:.0f} KB)")
        elif status == "skipped_404":
            pass  # Expected for months before listing or after delist
        elif status == "failed":
            logger.warning(f"  ✗ {filename}: {result.get('error', 'unknown')}")

    return counts


def main():
    parser = argparse.ArgumentParser(
        description="Download graveyard data for delisted Binance USDT-M perpetuals"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be downloaded without actually downloading",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        default=True,
        help="Skip files that already exist (default: True)",
    )
    parser.add_argument(
        "--no-skip-existing",
        dest="skip_existing",
        action="store_false",
        help="Re-download even if files already exist",
    )
    parser.add_argument(
        "--graveyard-dir",
        type=Path,
        default=DEFAULT_GRAVEYARD_DIR,
        help=f"Output directory for graveyard data (default: {DEFAULT_GRAVEYARD_DIR})",
    )
    parser.add_argument(
        "--no-funding",
        action="store_true",
        help="Skip downloading funding rate data",
    )
    parser.add_argument(
        "--symbol",
        nargs="+",
        help="Download only these specific symbols (skip S3 enumeration)",
    )
    args = parser.parse_args()

    graveyard_dir = args.graveyard_dir
    graveyard_dir.mkdir(parents=True, exist_ok=True)

    # Step 1: Get the graveyard symbol list
    if args.symbol:
        graveyard_symbols = sorted(s.upper() for s in args.symbol)
        logger.info(f"Using {len(graveyard_symbols)} user-specified symbols")
    else:
        logger.info("Enumerating all historical USDT-M symbols from Binance Vision S3...")
        try:
            import urllib.parse  # noqa: F811 (needed for marker quoting)
            all_s3_symbols = enumerate_s3_symbols(KLINES_S3_PREFIX)
        except Exception as e:
            logger.error(f"S3 enumeration failed: {e}")
            sys.exit(1)

        logger.info(f"S3 total symbols: {len(all_s3_symbols)}")

        logger.info("Loading active registry...")
        active_symbols = load_active_symbols()
        logger.info(f"Active registry symbols: {len(active_symbols)}")

        # Filter to USDT-denominated symbols only
        usdt_s3 = {s for s in all_s3_symbols if s.endswith("USDT")}
        usdt_active = {s for s in active_symbols if s.endswith("USDT")}

        graveyard_symbols = sorted(usdt_s3 - usdt_active)
        logger.info(f"Graveyard USDT symbols: {len(graveyard_symbols)}")

    if not graveyard_symbols:
        logger.info("No graveyard symbols found. Nothing to download.")
        return

    # Print list
    print(f"\nGraveyard symbols ({len(graveyard_symbols)}):")
    for s in graveyard_symbols:
        print(f"  {s}")

    if args.dry_run:
        print("\n[dry-run mode] Would download klines + funding rates for each symbol above.")
        print("Re-run without --dry-run to actually download.")
        return

    # Step 2: Download klines + funding rates for each graveyard symbol
    total_klines = {"downloaded": 0, "skipped_existing": 0, "skipped_404": 0, "failed": 0}
    total_funding = {"downloaded": 0, "skipped_existing": 0, "skipped_404": 0, "failed": 0}

    for i, symbol in enumerate(graveyard_symbols, 1):
        logger.info(f"\n[{i}/{len(graveyard_symbols)}] {symbol}")

        # Klines
        logger.info(f"  Downloading klines...")
        counts = download_symbol_monthly(
            symbol, "klines", graveyard_dir, skip_existing=args.skip_existing
        )
        for k, v in counts.items():
            total_klines[k] = total_klines.get(k, 0) + v
        logger.info(
            f"  Klines: {counts['downloaded']} downloaded, "
            f"{counts['skipped_existing']} existing, "
            f"{counts['skipped_404']} not-found (pre-launch/post-delist)"
        )

        # Funding rates
        if not args.no_funding:
            logger.info(f"  Downloading funding rates...")
            counts = download_symbol_monthly(
                symbol, "fundingRate", graveyard_dir, skip_existing=args.skip_existing
            )
            for k, v in counts.items():
                total_funding[k] = total_funding.get(k, 0) + v
            logger.info(
                f"  Funding: {counts['downloaded']} downloaded, "
                f"{counts['skipped_existing']} existing, "
                f"{counts['skipped_404']} not-found"
            )

        # Small delay to be polite to the server
        time.sleep(0.1)

    # Summary
    print(f"\n{'='*60}")
    print("DOWNLOAD COMPLETE")
    print(f"{'='*60}")
    print(f"Symbols processed: {len(graveyard_symbols)}")
    print(f"Klines:   {total_klines['downloaded']} downloaded, "
          f"{total_klines['skipped_existing']} existing, "
          f"{total_klines['failed']} failed")
    if not args.no_funding:
        print(f"Funding:  {total_funding['downloaded']} downloaded, "
              f"{total_funding['skipped_existing']} existing, "
              f"{total_funding['failed']} failed")
    print(f"Data stored in: {graveyard_dir}")


if __name__ == "__main__":
    import urllib.parse  # noqa: E402
    main()
