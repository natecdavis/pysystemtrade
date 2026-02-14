#!/usr/bin/env python3
"""
Refresh Binance perpetual futures market registry.

Fetches derivatives data from CoinGecko API (not geo-blocked), filters to Binance
USDT-margined perpetuals, and writes three artifacts:
1. Raw snapshot (full CoinGecko response)
2. Normalized registry (filtered + enriched)
3. Candidate instrument list (instrument IDs with _PERP suffix)

Usage:
    python scripts/refresh_binance_market_registry.py --env dev
    python scripts/refresh_binance_market_registry.py --env prod --dry-run
"""

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional
import urllib.request
import time

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

logger = logging.getLogger(__name__)


def fetch_coingecko_derivatives() -> List[dict]:
    """
    Fetch derivatives data from CoinGecko API (not geo-blocked).

    Returns:
        List of derivative contracts from all exchanges.
    """
    url = "https://api.coingecko.com/api/v3/derivatives"

    try:
        logger.info(f"Fetching derivatives from CoinGecko API: {url}")
        with urllib.request.urlopen(url, timeout=30) as response:
            data = json.loads(response.read().decode('utf-8'))
        logger.info(f"Fetched {len(data)} derivative contracts from CoinGecko")
        return data
    except Exception as e:
        logger.error(f"Failed to fetch CoinGecko derivatives: {e}")
        raise


def filter_binance_usdt_perpetuals(derivatives: List[dict]) -> List[dict]:
    """
    Filter to Binance USDT-margined perpetuals only.

    Criteria:
    - market == "Binance (Futures)"
    - contract_type == "perpetual"
    - symbol contains "USDT"
    - expired_at is None (not delisted)
    """
    filtered = [
        d for d in derivatives
        if d.get('market') == 'Binance (Futures)'
        and d.get('contract_type') == 'perpetual'
        and 'USDT' in d.get('symbol', '')
        and d.get('expired_at') is None
    ]

    logger.info(f"Filtered: {len(derivatives)} total → {len(filtered)} Binance USDT perpetuals")
    return filtered


def normalize_symbol_info(derivative_data: dict) -> dict:
    """
    Extract relevant fields and normalize.

    CoinGecko provides current snapshot data (not historical).
    Fields included: volume, open interest, funding rate.
    """
    symbol = derivative_data.get('symbol', '')

    # Extract base asset (symbol minus 'USDT')
    base_asset = symbol.replace('USDT', '') if 'USDT' in symbol else None

    return {
        'symbol': symbol,
        'status': 'ACTIVE',  # CoinGecko only returns active contracts
        'base_asset': base_asset,
        'quote_asset': 'USDT',
        'volume_24h': derivative_data.get('volume_24h', 0),
        'open_interest': derivative_data.get('open_interest', 0),
        'funding_rate': derivative_data.get('funding_rate', 0),
        'last_traded_at': derivative_data.get('last_traded_at', 0),
    }


def build_registry(binance_perpetuals: List[dict]) -> dict:
    """
    Build normalized registry from filtered derivatives.

    Returns:
        Registry dict with instruments keyed by symbol.
    """
    instruments = {}
    for d in binance_perpetuals:
        symbol = d['symbol']
        instruments[symbol] = normalize_symbol_info(d)

    return {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'source': 'coingecko_derivatives_snapshot.json',
        'version': '1.0',
        'filter_criteria': {
            'market': 'Binance (Futures)',
            'contract_type': 'perpetual',
            'symbol_contains': 'USDT',
            'expired_at': 'null',
        },
        'instruments': instruments,
        'summary': {
            'total_instruments': len(instruments),
        }
    }


def build_candidate_list(registry: dict) -> dict:
    """
    Build candidate instrument list with _PERP suffix.

    Uses canonical symbol → instrument_id mapping.
    """
    from sysdata.crypto.config_helpers import symbol_to_instrument_id

    symbols = list(registry['instruments'].keys())
    candidate_ids = [symbol_to_instrument_id(s) for s in sorted(symbols)]

    return {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'source': 'binance_perp_registry.json',
        'version': '1.0',
        'candidate_instruments': candidate_ids,
        'count': len(candidate_ids),
    }


def detect_changes(metadata_dir: Path, new_candidate_ids: List[str]) -> dict:
    """
    Detect changes between previous and new registry.

    Returns:
        Changelog dict with new/delisted instruments.
    """
    prev_registry_path = metadata_dir / 'discovered_candidate_instruments.json'

    if prev_registry_path.exists():
        try:
            with open(prev_registry_path) as f:
                prev_data = json.load(f)

            prev_candidates = set(prev_data.get('candidate_instruments', []))
            new_candidates = set(new_candidate_ids)

            new_instruments = sorted(list(new_candidates - prev_candidates))
            delisted_instruments = sorted(list(prev_candidates - new_candidates))

            logger.info(f"Registry changes: {len(new_instruments)} new, {len(delisted_instruments)} delisted")
            return {
                'new_instruments': new_instruments,
                'delisted_instruments': delisted_instruments,
                'total_count': len(new_candidate_ids),
            }
        except Exception as e:
            logger.warning(f"Failed to detect changes: {e}")
            return {
                'new_instruments': [],
                'delisted_instruments': [],
                'total_count': len(new_candidate_ids),
                'error': str(e)
            }
    else:
        # First run, no previous registry
        logger.info("No previous registry found (first run)")
        return {
            'new_instruments': [],
            'delisted_instruments': [],
            'total_count': len(new_candidate_ids),
            'first_run': True
        }


def write_artifacts(
    metadata_dir: Path,
    raw_snapshot: dict,
    registry: dict,
    candidate_list: dict,
    changelog: dict,
    dry_run: bool = False
) -> None:
    """
    Write four artifacts with atomic writes.

    Files:
    1. coingecko_derivatives_snapshot.json - Full response
    2. binance_perp_registry.json - Normalized registry
    3. discovered_candidate_instruments.json - Candidate list for update_data_monthly.py
    4. registry_changelog.json - Diff from previous registry
    """
    artifacts = [
        ('coingecko_derivatives_snapshot.json', raw_snapshot),
        ('binance_perp_registry.json', registry),
        ('discovered_candidate_instruments.json', candidate_list),
        ('registry_changelog.json', changelog),
    ]

    for filename, data in artifacts:
        output_path = metadata_dir / filename

        if dry_run:
            logger.info(f"[DRY RUN] Would write {output_path}")
            continue

        # Atomic write (write to .tmp, then rename)
        tmp_path = output_path.with_suffix('.tmp')
        with open(tmp_path, 'w') as f:
            json.dump(data, f, indent=2)
        tmp_path.replace(output_path)

        logger.info(f"✓ Wrote {output_path}")


def run_refresh(env_root: Path, verbose: bool = True, dry_run: bool = False) -> dict:
    """
    Run registry refresh and return changelog.

    This function can be called both from CLI and from other scripts (e.g., advisory workflow).

    Args:
        env_root: Environment root path (e.g., Path('envs/dev'))
        verbose: Enable verbose logging
        dry_run: Print actions without writing

    Returns:
        Changelog dict: {'new_instruments': [...], 'delisted_instruments': [...], 'total_count': N}

    Raises:
        Exception: If CoinGecko API fetch fails
    """
    metadata_dir = env_root / 'data/raw/metadata'
    metadata_dir.mkdir(parents=True, exist_ok=True)

    if verbose:
        logger.info(f"Metadata directory: {metadata_dir}")

    # Fetch and filter
    if verbose:
        logger.info("Fetching derivatives from CoinGecko API (not geo-blocked)...")
    derivatives = fetch_coingecko_derivatives()

    if verbose:
        logger.info("Filtering to Binance USDT perpetuals...")
    binance_perpetuals = filter_binance_usdt_perpetuals(derivatives)

    # Build artifacts
    if verbose:
        logger.info("Building artifacts...")
    raw_snapshot = {
        'fetched_at': datetime.now(timezone.utc).isoformat(),
        'source': 'https://api.coingecko.com/api/v3/derivatives',
        'total_derivatives': len(derivatives),
        'binance_perpetuals': len(binance_perpetuals),
        'raw_derivatives': derivatives,
    }

    registry = build_registry(binance_perpetuals)
    candidate_list = build_candidate_list(registry)

    # Detect changes
    changelog = detect_changes(metadata_dir, candidate_list['candidate_instruments'])
    changelog['timestamp'] = datetime.now(timezone.utc).isoformat()

    # Write artifacts
    if verbose:
        logger.info(f"Writing artifacts to {metadata_dir}...")
    write_artifacts(metadata_dir, raw_snapshot, registry, candidate_list, changelog, dry_run)

    # Summary
    if verbose:
        logger.info("="*80)
        logger.info("Registry refresh complete!")
        logger.info(f"  Total derivatives fetched: {raw_snapshot['total_derivatives']}")
        logger.info(f"  Binance USDT perpetuals: {raw_snapshot['binance_perpetuals']}")
        logger.info(f"  Candidate instruments: {candidate_list['count']}")
        if changelog.get('new_instruments'):
            logger.info(f"  New instruments: {len(changelog['new_instruments'])}")
        if changelog.get('delisted_instruments'):
            logger.info(f"  Delisted instruments: {len(changelog['delisted_instruments'])}")
        logger.info("="*80)

    return changelog


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument('--env', default='dev', help='Environment (dev/prod)')
    parser.add_argument('--env-root', type=Path, help='Override env root path')
    parser.add_argument('--dry-run', action='store_true', help='Print actions without writing')
    parser.add_argument('--verbose', '-v', action='store_true', help='Verbose logging')
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    # Resolve env root
    if args.env_root:
        env_root = args.env_root
    else:
        env_root = Path(f'envs/{args.env}')

    # Run refresh
    try:
        changelog = run_refresh(env_root, verbose=True, dry_run=args.dry_run)

        # Display changes if any
        if not args.dry_run:
            if changelog.get('new_instruments'):
                print(f"\nNew instruments ({len(changelog['new_instruments'])}):")
                for instr in changelog['new_instruments'][:10]:
                    print(f"  + {instr}")
                if len(changelog['new_instruments']) > 10:
                    print(f"  ... and {len(changelog['new_instruments']) - 10} more")

            if changelog.get('delisted_instruments'):
                print(f"\nDelisted instruments ({len(changelog['delisted_instruments'])}):")
                for instr in changelog['delisted_instruments'][:10]:
                    print(f"  - {instr}")
                if len(changelog['delisted_instruments']) > 10:
                    print(f"  ... and {len(changelog['delisted_instruments']) - 10} more")

    except Exception as e:
        logger.error(f"Registry refresh failed: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
