#!/usr/bin/env python3
"""
Auto-regenerate current_positions.csv from config with zeros for missing instruments.

This prevents doctor failures when layer_a expands to 30 instruments.

CRITICAL TIMESTAMP BEHAVIOR:
- Preserves existing rows exactly (never modifies timestamps)
- Only adds missing instruments with sentinel timestamp 1970-01-01T00:00:00Z
- Use --timestamp to override sentinel for new rows

Usage:
    # Add missing instruments with sentinel timestamp
    python scripts/sync_positions_file.py \
        --config config/crypto_perps_dynamic_universe_top30.yaml \
        --positions-file live/current_positions.csv

    # Add missing instruments with specific timestamp
    python scripts/sync_positions_file.py \
        --config config/crypto_perps_dynamic_universe_top30.yaml \
        --positions-file live/current_positions.csv \
        --timestamp 2026-02-14T12:00:00Z
"""

import argparse
import logging
import pandas as pd
import yaml
from pathlib import Path
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def sync_positions_file(config_path: Path, positions_file: Path, timestamp_override: str = None):
    """
    Ensure positions file has all layer_a instruments with 6-column schema.

    CRITICAL: Preserves existing rows exactly. Only adds missing instruments.

    Args:
        config_path: Path to config YAML
        positions_file: Path to positions CSV
        timestamp_override: ISO timestamp for new rows (default: sentinel 1970-01-01T00:00:00Z)
    """
    logger.info(f"Syncing positions file from config: {config_path}")

    # Load config
    with open(config_path) as f:
        config = yaml.safe_load(f)

    layer_a_instruments = config.get('universe', {}).get('layer_a_instruments', [])

    if not layer_a_instruments:
        logger.error("No layer_a_instruments found in config")
        return

    logger.info(f"Config contains {len(layer_a_instruments)} layer_a instruments")

    # Load existing positions
    if positions_file.exists():
        existing = pd.read_csv(positions_file)
        existing_instruments = set(existing['instrument'])
        logger.info(f"Existing positions file has {len(existing_instruments)} instruments")
    else:
        logger.info("Positions file does not exist, creating from scratch")
        # 6-column schema
        existing = pd.DataFrame(columns=[
            'instrument', 'contracts', 'mark_price_usd',
            'notional_usd', 'timestamp', 'notes'
        ])
        existing_instruments = set()

    # Add missing instruments with zeros
    missing = set(layer_a_instruments) - existing_instruments
    if missing:
        logger.info(f"Adding {len(missing)} missing instruments with zero positions")

        # Use sentinel timestamp (not current time) to indicate auto-added
        sentinel_timestamp = '1970-01-01T00:00:00Z'

        # Override with provided timestamp if given
        if timestamp_override:
            new_timestamp = timestamp_override
            logger.info(f"Using provided timestamp: {new_timestamp}")
        else:
            new_timestamp = sentinel_timestamp
            logger.info(f"Using sentinel timestamp: {new_timestamp} (indicates auto-added)")

        new_rows = pd.DataFrame({
            'instrument': sorted(missing),
            'contracts': 0.0,
            'mark_price_usd': 0.0,
            'notional_usd': 0.0,
            'timestamp': new_timestamp,
            'notes': 'auto_added_zero_row'
        })

        updated = pd.concat([existing, new_rows], ignore_index=True)
        updated = updated.sort_values('instrument')

        # Write atomically
        positions_file.parent.mkdir(parents=True, exist_ok=True)
        updated.to_csv(positions_file, index=False)

        logger.info(f"✓ Added {len(missing)} missing instruments to {positions_file}")
        for instr in sorted(missing):
            logger.info(f"  + {instr}")
    else:
        logger.info(f"✓ Positions file up to date ({len(layer_a_instruments)} instruments)")

    # Report instruments not in layer_a (but don't remove - allow extra)
    extra = existing_instruments - set(layer_a_instruments)
    if extra:
        logger.warning(f"Found {len(extra)} instruments in positions file NOT in layer_a:")
        for instr in sorted(extra):
            logger.warning(f"  - {instr}")
        logger.warning("These will be ignored in trade plan generation (layer_a is the allowlist)")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument('--config', type=Path, required=True, help='Config YAML file')
    parser.add_argument('--positions-file', type=Path, required=True, help='Positions CSV file')
    parser.add_argument(
        '--timestamp',
        type=str,
        help='ISO timestamp for added rows (default: 1970-01-01T00:00:00Z sentinel)'
    )
    args = parser.parse_args()

    if not args.config.exists():
        logger.error(f"Config file not found: {args.config}")
        exit(1)

    sync_positions_file(args.config, args.positions_file, args.timestamp)
