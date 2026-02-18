#!/usr/bin/env python3
"""
Download historical data from Binance Vision (bulk CSV files).

NO VPN REQUIRED - Vision data is publicly accessible.
Resumable/idempotent: interruptions don't force restart.

This script downloads full historical data for Binance perpetual futures from
Binance Vision archives. Use this for initial data population or backfilling.

For recent data (last 7 days), use update_data_daily.py instead (requires VPN).

Usage:
    # Download all instruments from registry
    python scripts/download_vision_bulk.py --env dev

    # Download first 50 instruments (incremental)
    python scripts/download_vision_bulk.py --env dev --instruments-limit 50

    # Resume from specific instrument
    python scripts/download_vision_bulk.py --env dev --resume-from ARBUSDT_PERP

    # Dry run (show plan without downloading)
    python scripts/download_vision_bulk.py --env dev --dry-run
"""

import argparse
import json
import logging
import sys
from pathlib import Path
from datetime import datetime
from typing import List, Set

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sysdata.crypto.config_helpers import load_registry

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def load_progress(env_root: Path) -> dict:
    """Load download progress tracker."""
    progress_path = env_root / 'data/raw/vision_download_progress.json'

    if progress_path.exists():
        with open(progress_path) as f:
            return json.load(f)

    return {'completed': [], 'last_updated': None}


def save_progress(env_root: Path, completed: List[str]) -> None:
    """Save download progress (idempotent)."""
    progress_path = env_root / 'data/raw/vision_download_progress.json'
    progress_path.parent.mkdir(parents=True, exist_ok=True)

    with open(progress_path, 'w') as f:
        json.dump({
            'completed': completed,
            'last_updated': datetime.utcnow().isoformat(),
            'count': len(completed)
        }, f, indent=2)

    logger.debug(f"Progress saved: {len(completed)} completed")


def load_registry_candidates(env_root: Path) -> List[str]:
    """Load candidate instruments from registry."""
    try:
        registry_data = load_registry(env_root)
        candidates = registry_data.get('candidate_instruments', [])
        logger.info(f"Loaded {len(candidates)} candidates from registry")
        return candidates
    except FileNotFoundError:
        logger.error(f"Registry not found at {env_root / 'data/raw/metadata/discovered_candidate_instruments.json'}")
        logger.error("Run scripts/refresh_binance_market_registry.py first")
        sys.exit(1)


def download_instrument_from_vision(
    instrument_id: str,
    data_dir: Path,
    start_date: str = '2019-01-01',
    dry_run: bool = False
) -> bool:
    """
    Download klines and funding data from Binance Vision for one instrument.

    Downloads monthly ZIP files from Binance Vision and stores them in canonical format:
    - data/raw/binance/klines/{SYMBOL}/{SYMBOL}-1d-YYYY-MM.zip
    - data/raw/binance/funding_rates/{SYMBOL}/{SYMBOL}-fundingRate-YYYY-MM.zip

    Args:
        instrument_id: Instrument ID (e.g., BTCUSDT_PERP)
        data_dir: Raw data directory (e.g., envs/dev/data/raw/binance)
        start_date: Start date for download (YYYY-MM-DD)
        dry_run: If True, print URLs without downloading

    Returns:
        True if successful (at least one file downloaded), False otherwise
    """
    import requests
    import pandas as pd
    from sysdata.crypto.config_helpers import instrument_id_to_symbol

    symbol = instrument_id_to_symbol(instrument_id)

    if dry_run:
        logger.info(f"[DRY RUN] Would download {symbol} from Vision (start: {start_date})")
        logger.info(f"  - Klines: https://data.binance.vision/data/futures/um/monthly/klines/{symbol}/1d/")
        logger.info(f"  - Funding: https://data.binance.vision/data/futures/um/monthly/fundingRate/{symbol}/")
        return True

    # Compute month range from start_date to present
    start = pd.to_datetime(start_date)
    end = datetime.now()
    months = pd.date_range(start, end, freq='MS').strftime('%Y-%m')

    downloaded_files = 0
    klines_dir = data_dir / 'klines' / symbol
    funding_dir = data_dir / 'funding_rates' / symbol
    klines_dir.mkdir(parents=True, exist_ok=True)
    funding_dir.mkdir(parents=True, exist_ok=True)

    # Download klines ZIPs
    for month in months:
        year, mon = month.split('-')
        zip_filename = f"{symbol}-1d-{year}-{mon}.zip"
        klines_path = klines_dir / zip_filename

        # Skip if already exists
        if klines_path.exists():
            logger.debug(f"{symbol}: Klines {month} already exists, skipping")
            downloaded_files += 1
            continue

        klines_url = f"https://data.binance.vision/data/futures/um/monthly/klines/{symbol}/1d/{zip_filename}"

        try:
            response = requests.get(klines_url, timeout=30)
            if response.status_code == 200:
                klines_path.write_bytes(response.content)
                logger.debug(f"{symbol}: Downloaded klines {month} ({len(response.content)} bytes)")
                downloaded_files += 1
            elif response.status_code == 404:
                logger.debug(f"{symbol}: Klines {month} not available (404 - expected for pre-launch)")
            else:
                logger.warning(f"{symbol}: Klines {month} failed ({response.status_code})")
        except Exception as e:
            logger.error(f"{symbol}: Klines {month} error: {e}")

    # Download funding ZIPs
    for month in months:
        year, mon = month.split('-')
        zip_filename = f"{symbol}-fundingRate-{year}-{mon}.zip"
        funding_path = funding_dir / zip_filename

        # Skip if already exists
        if funding_path.exists():
            logger.debug(f"{symbol}: Funding {month} already exists, skipping")
            downloaded_files += 1
            continue

        funding_url = f"https://data.binance.vision/data/futures/um/monthly/fundingRate/{symbol}/{zip_filename}"

        try:
            response = requests.get(funding_url, timeout=30)
            if response.status_code == 200:
                funding_path.write_bytes(response.content)
                logger.debug(f"{symbol}: Downloaded funding {month} ({len(response.content)} bytes)")
                downloaded_files += 1
            elif response.status_code == 404:
                logger.debug(f"{symbol}: Funding {month} not available (404)")
            else:
                logger.warning(f"{symbol}: Funding {month} failed ({response.status_code})")
        except Exception as e:
            logger.error(f"{symbol}: Funding {month} error: {e}")

    if downloaded_files > 0:
        logger.info(f"{instrument_id}: Downloaded {downloaded_files} files from Vision")
        return True
    else:
        logger.warning(f"{instrument_id}: No files downloaded (may be pre-launch or network issue)")
        return False


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument('--env', default='dev', help='Environment (dev/prod)')
    parser.add_argument('--env-root', type=Path, help='Override env root path')
    parser.add_argument(
        '--instruments-limit',
        type=int,
        help='Download N instruments (incremental mode). Use for resumable downloads.'
    )
    parser.add_argument(
        '--resume-from',
        help='Skip instruments before this symbol (e.g., ARBUSDT_PERP)'
    )
    parser.add_argument(
        '--start-date',
        default='2019-01-01',
        help='Start date for downloads (YYYY-MM-DD, default: 2019-01-01)'
    )
    parser.add_argument(
        '--force-redownload',
        action='store_true',
        help='Redownload even if already completed (use for data refresh)'
    )
    parser.add_argument('--dry-run', action='store_true', help='Print plan without downloading')
    parser.add_argument('--verbose', '-v', action='store_true', help='Verbose logging')
    parser.add_argument(
        '--config',
        type=Path,
        help='Config file to prioritize layer_a instruments (optional)'
    )
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Resolve env root
    if args.env_root:
        env_root = args.env_root
    else:
        env_root = Path(f'envs/{args.env}')

    data_dir = env_root / 'data/raw/binance'
    data_dir.mkdir(parents=True, exist_ok=True)

    logger.info(f"Environment: {env_root}")
    logger.info(f"Data directory: {data_dir}")
    logger.info(f"Start date: {args.start_date}")

    # Load candidate instruments from registry
    all_candidates = load_registry_candidates(env_root)

    # Prioritize layer_a instruments if config provided
    if args.config:
        import yaml
        with open(args.config) as f:
            config = yaml.safe_load(f)
        layer_a = config.get('universe', {}).get('layer_a_instruments', [])
        if layer_a:
            # Sort: layer_a first, then rest of registry
            priority_order = layer_a + [c for c in all_candidates if c not in layer_a]
            logger.info(f"Prioritized {len(layer_a)} layer_a instruments from config")
            all_candidates = priority_order

    # Load progress tracker
    progress = load_progress(env_root)
    completed_set = set(progress['completed'])

    # Filter to pending instruments (idempotent)
    if not args.force_redownload:
        pending = [c for c in all_candidates if c not in completed_set]
        logger.info(f"Progress: {len(completed_set)}/{len(all_candidates)} completed, {len(pending)} pending")
    else:
        pending = all_candidates
        completed_set = set()
        logger.info(f"Force redownload mode: {len(all_candidates)} instruments")

    # Apply resume filter
    if args.resume_from:
        if args.resume_from in pending:
            resume_idx = pending.index(args.resume_from)
            pending = pending[resume_idx:]
            logger.info(f"Resuming from {args.resume_from} ({len(pending)} instruments remaining)")
        else:
            logger.warning(f"Resume instrument {args.resume_from} not in pending list")

    # Apply limit filter
    if args.instruments_limit:
        pending = pending[:args.instruments_limit]
        logger.info(f"Limiting to {len(pending)} instruments")

    if not pending:
        logger.info("✓ All instruments already downloaded (or limit exhausted)")
        return

    # Download from Vision (NO VPN REQUIRED)
    logger.info(f"\n{'='*70}")
    logger.info(f"Downloading {len(pending)} instruments from Binance Vision")
    logger.info(f"NO VPN REQUIRED - Vision data is publicly accessible")
    logger.info(f"{'='*70}\n")

    success_count = 0
    failure_count = 0

    for i, instrument in enumerate(pending, 1):
        logger.info(f"\n[{i}/{len(pending)}] Processing {instrument}...")

        success = download_instrument_from_vision(
            instrument,
            data_dir,
            start_date=args.start_date,
            dry_run=args.dry_run
        )

        if success:
            success_count += 1
            if not args.dry_run:
                # Mark as completed
                completed_set.add(instrument)
                save_progress(env_root, list(completed_set))
        else:
            failure_count += 1

    # Summary
    logger.info(f"\n{'='*70}")
    logger.info(f"Download Summary")
    logger.info(f"{'='*70}")
    logger.info(f"Successful: {success_count}")
    logger.info(f"Failed: {failure_count}")
    logger.info(f"Total progress: {len(completed_set)}/{len(all_candidates)} completed")
    logger.info(f"{'='*70}\n")

    if failure_count > 0:
        logger.warning(f"{failure_count} instruments failed - check logs for details")
        sys.exit(1)


if __name__ == '__main__':
    main()
