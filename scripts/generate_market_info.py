#!/usr/bin/env python3
"""
Generate binance_market_info.json from registry.

Uses standard Binance fees and typical spreads for all instruments.
This is a temporary workaround until we can fetch real spread data from Binance API.
"""

import json
import sys
from pathlib import Path

# Standard Binance perpetual futures parameters
DEFAULT_SPREAD_FRAC = 0.00025  # 0.025% spread (typical)
DEFAULT_TAKER_FEE_FRAC = 0.00045  # 0.045% taker fee — Hyperliquid standard (corrected 2026-04-11; was 0.0005 Binance rate)

def generate_market_info(registry_path: Path, output_path: Path):
    """
    Generate market_info.json from registry.

    Args:
        registry_path: Path to binance_perp_registry.json or discovered_candidate_instruments.json
        output_path: Path for output binance_market_info.json
    """
    # Load registry
    with open(registry_path) as f:
        data = json.load(f)

    # Handle both registry formats
    if 'instruments' in data:
        # binance_perp_registry.json format
        symbols = list(data['instruments'].keys())
    elif 'candidate_instruments' in data:
        # discovered_candidate_instruments.json format - need to strip _PERP suffix
        symbols = [inst.replace('_PERP', '') for inst in data['candidate_instruments']]
    else:
        raise ValueError(f"Unknown registry format. Keys: {list(data.keys())}")

    # Generate market info with standard values
    market_info = {}
    for symbol in symbols:
        market_info[symbol] = {
            'spread_frac': DEFAULT_SPREAD_FRAC,
            'taker_fee_frac': DEFAULT_TAKER_FEE_FRAC
        }

    # Write output
    with open(output_path, 'w') as f:
        json.dump(market_info, f, indent=2)

    print(f"✓ Generated market info for {len(symbols)} symbols")
    print(f"  Output: {output_path}")
    print(f"  Spread: {DEFAULT_SPREAD_FRAC} ({DEFAULT_SPREAD_FRAC*100:.3f}%)")
    print(f"  Taker fee: {DEFAULT_TAKER_FEE_FRAC} ({DEFAULT_TAKER_FEE_FRAC*100:.3f}%)")

if __name__ == '__main__':
    # Default paths
    registry_path = Path('envs/dev/data/raw/metadata/binance_perp_registry.json')
    output_path = Path('envs/dev/data/raw/metadata/binance_market_info.json')

    # Allow command line override
    if len(sys.argv) > 1:
        registry_path = Path(sys.argv[1])
    if len(sys.argv) > 2:
        output_path = Path(sys.argv[2])

    generate_market_info(registry_path, output_path)
