#!/usr/bin/env python3
"""
Fetch Hyperliquid instrument list and write to data/hyperliquid_instruments.json.

Also prints a coverage report showing how many of the dataset instruments
are available on Hyperliquid.

Usage:
    python scripts/fetch_hyperliquid_instruments.py
    python scripts/fetch_hyperliquid_instruments.py --data data/dataset_538registry_6yr_jagged.parquet
    python scripts/fetch_hyperliquid_instruments.py --output data/hyperliquid_instruments.json
"""

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sysdata.crypto.config_helpers import instrument_id_to_hl_symbol

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

HL_INFO_URL = 'https://api.hyperliquid.xyz/info'


def fetch_hl_universe(timeout_sec: int = 15) -> list[dict]:
    """
    Fetch universe metadata from Hyperliquid /info endpoint.

    Returns:
        List of instrument dicts, each with at least {"name": "BTC", ...}

    Raises:
        requests.RequestException: on network error
        ValueError: if response format is unexpected
    """
    payload = {"type": "meta"}
    resp = requests.post(HL_INFO_URL, json=payload, timeout=timeout_sec)
    resp.raise_for_status()
    data = resp.json()

    universe = data.get('universe')
    if universe is None:
        raise ValueError(
            f"Unexpected response format — 'universe' key missing. "
            f"Keys found: {list(data.keys())}"
        )
    return universe


def build_coverage_report(
    hl_symbols: set[str],
    dataset_path: Path | None
) -> dict:
    """
    Build a coverage report for dataset instruments vs HL availability.

    Args:
        hl_symbols: Set of symbols available on Hyperliquid
        dataset_path: Path to parquet dataset (optional)

    Returns:
        Dict with coverage statistics and missing instruments list
    """
    if dataset_path is None or not dataset_path.exists():
        return {"dataset_path": str(dataset_path), "skipped": True}

    try:
        import pandas as pd
        df = pd.read_parquet(dataset_path, columns=['instrument'])
        dataset_instruments = list(df['instrument'].dropna().unique())
    except Exception as e:
        logger.warning(f"Could not read dataset for coverage check: {e}")
        return {"dataset_path": str(dataset_path), "error": str(e)}

    mapped = {inst: instrument_id_to_hl_symbol(inst) for inst in dataset_instruments}
    available = {inst for inst, sym in mapped.items() if sym in hl_symbols}
    missing = {inst for inst, sym in mapped.items() if sym not in hl_symbols}

    missing_detail = sorted([
        {"instrument": inst, "hl_symbol": mapped[inst]}
        for inst in missing
    ], key=lambda x: x["instrument"])

    return {
        "dataset_path": str(dataset_path),
        "dataset_instrument_count": len(dataset_instruments),
        "available_on_hl": len(available),
        "missing_from_hl": len(missing),
        "coverage_pct": round(100.0 * len(available) / len(dataset_instruments), 1)
        if dataset_instruments else 0.0,
        "missing_instruments": missing_detail,
    }


def main():
    parser = argparse.ArgumentParser(
        description='Fetch Hyperliquid instrument list and write to JSON',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        '--output',
        type=Path,
        default=Path('data/hyperliquid_instruments.json'),
        help='Output path for instrument list (default: data/hyperliquid_instruments.json)'
    )
    parser.add_argument(
        '--data',
        type=Path,
        default=None,
        help='Optional: path to dataset parquet for coverage report '
             '(default: auto-detect data/dataset_538registry_6yr_jagged.parquet)'
    )
    parser.add_argument(
        '--timeout',
        type=int,
        default=15,
        help='Request timeout in seconds (default: 15)'
    )
    args = parser.parse_args()

    # Auto-detect dataset for coverage report
    dataset_path = args.data
    if dataset_path is None:
        repo_root = Path(__file__).parent.parent
        candidate = repo_root / 'data' / 'dataset_538registry_6yr_jagged.parquet'
        if candidate.exists():
            dataset_path = candidate

    # Fetch instruments from Hyperliquid
    logger.info(f"Fetching instrument list from {HL_INFO_URL} ...")
    try:
        universe = fetch_hl_universe(timeout_sec=args.timeout)
    except requests.exceptions.RequestException as e:
        logger.error(f"✗ Network error fetching HL instruments: {e}")
        sys.exit(1)
    except ValueError as e:
        logger.error(f"✗ Unexpected response format: {e}")
        sys.exit(1)

    symbols = sorted(set(inst['name'] for inst in universe))
    logger.info(f"✓ Fetched {len(symbols)} instruments from Hyperliquid")

    # Build output document
    output_doc = {
        "symbols": symbols,
        "count": len(symbols),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "source": HL_INFO_URL,
        "raw_universe_count": len(universe),
    }

    # Write output
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, 'w') as f:
        json.dump(output_doc, f, indent=2)
    logger.info(f"✓ Written to {args.output}")

    # Coverage report
    logger.info("\n--- Coverage Report ---")
    hl_set = set(symbols)
    report = build_coverage_report(hl_set, dataset_path)

    if report.get('skipped') or report.get('error'):
        logger.info(
            "  Coverage check skipped (no dataset available). "
            "Pass --data to enable."
        )
    else:
        logger.info(
            f"  Dataset instruments:  {report['dataset_instrument_count']}"
        )
        logger.info(
            f"  Available on HL:      {report['available_on_hl']} "
            f"({report['coverage_pct']}%)"
        )
        logger.info(
            f"  Missing from HL:      {report['missing_from_hl']}"
        )
        if report['missing_instruments']:
            logger.info("  Missing instruments:")
            for item in report['missing_instruments'][:20]:
                logger.info(f"    {item['instrument']}  →  {item['hl_symbol']}")
            if len(report['missing_instruments']) > 20:
                logger.info(
                    f"    ... and {len(report['missing_instruments']) - 20} more"
                )

    logger.info("\n✓ Done")


if __name__ == '__main__':
    main()
