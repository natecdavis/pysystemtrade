#!/usr/bin/env python
"""
Extract unique symbols from a parquet dataset and save to text file.

This script reads a dataset (parquet format) and extracts all unique instrument codes,
converting them to Binance symbol format (e.g., BTC → BTCUSDT).

Usage:
    python scripts/extract_symbols_from_dataset.py \\
        --data data/dataset_538registry_6yr_jagged.parquet \\
        --output data/binance_oi_symbols.txt

Author: Phase 2 OI Data Implementation
Date: 2026-02-21
"""

import argparse
import logging
import sys
from pathlib import Path

import pandas as pd

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def load_instruments_from_parquet(filepath: str) -> list:
    """
    Load unique instruments from parquet dataset.

    Args:
        filepath: Path to parquet file

    Returns:
        List of unique instrument codes
    """
    logger.info(f"Loading dataset from {filepath}")
    df = pd.read_parquet(filepath)

    # Get unique instruments
    if 'instrument' in df.index.names:
        instruments = df.index.get_level_values('instrument').unique().tolist()
    elif 'instrument' in df.columns:
        instruments = df['instrument'].unique().tolist()
    else:
        raise ValueError("Dataset must have 'instrument' column or index level")

    logger.info(f"Found {len(instruments)} unique instruments")
    return sorted(instruments)


def convert_to_binance_symbols(instruments: list) -> list:
    """
    Convert instrument codes to Binance USDT perpetual symbol format.

    Examples:
        BTC_PERP → BTCUSDT
        AAVEUSDT_PERP → AAVEUSDT
        ETH → ETHUSDT
        1000PEPE_PERP → 1000PEPEUSDT

    Args:
        instruments: List of instrument codes

    Returns:
        List of Binance symbols
    """
    symbols = []

    for inst in instruments:
        # Remove _PERP suffix if present
        clean_inst = inst.replace('_PERP', '')

        # Check if already ends with USDT
        if clean_inst.endswith('USDT'):
            symbol = clean_inst
        else:
            # Append USDT
            symbol = f"{clean_inst}USDT"

        symbols.append(symbol)

    return symbols


def save_symbols_to_file(symbols: list, output_path: str) -> None:
    """
    Save symbols to text file (one per line).

    Args:
        symbols: List of symbols
        output_path: Output file path
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, 'w') as f:
        for symbol in symbols:
            f.write(f"{symbol}\n")

    logger.info(f"Saved {len(symbols)} symbols to {output_path}")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description='Extract symbols from dataset for Binance OI download'
    )

    parser.add_argument(
        '--data',
        type=str,
        required=True,
        help='Path to parquet dataset'
    )

    parser.add_argument(
        '--output',
        type=str,
        default='data/binance_oi_symbols.txt',
        help='Output file path (default: data/binance_oi_symbols.txt)'
    )

    parser.add_argument(
        '--show-preview',
        action='store_true',
        help='Show first 10 symbols before saving'
    )

    args = parser.parse_args()

    try:
        # Load instruments
        instruments = load_instruments_from_parquet(args.data)

        # Convert to Binance symbols
        symbols = convert_to_binance_symbols(instruments)

        # Show preview if requested
        if args.show_preview:
            logger.info("First 10 symbols:")
            for symbol in symbols[:10]:
                logger.info(f"  {symbol}")

        # Save to file
        save_symbols_to_file(symbols, args.output)

        logger.info(f"Success! Symbol list ready for download_binance_oi_data.py")

    except Exception as e:
        logger.error(f"Error: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
