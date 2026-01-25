#!/usr/bin/env python3
"""
Smoke test for real data integration

Usage:
    python scripts/validate_real_data.py data/example_crypto_perps.parquet
"""

import sys
import pandas as pd
from pathlib import Path
from sysdata.crypto.prices import load_crypto_perps_panel


def validate_real_data_parquet(parquet_path: str):
    """
    Load parquet and print basic stats

    Args:
        parquet_path: Path to parquet file
    """
    print(f"Loading parquet from: {parquet_path}")
    prices, meta = load_crypto_perps_panel(parquet_path)

    print("\n" + "=" * 60)
    print("PRICES DATAFRAME")
    print("=" * 60)
    print(f"Shape: {prices.shape}")
    print(f"Date range: {prices.index.min()} to {prices.index.max()}")
    print(f"Instruments: {list(prices.columns)}")

    print("\n" + "=" * 60)
    print("PER-INSTRUMENT STATS")
    print("=" * 60)
    for instrument in prices.columns:
        inst_prices = prices[instrument].dropna()
        inst_meta = meta.loc[(slice(None), instrument), :]

        print(f"\n{instrument}:")
        print(f"  Rows: {len(inst_prices)}")
        print(f"  Close: min=${inst_prices.min():.2f}, max=${inst_prices.max():.2f}")
        print(f"  Funding: mean={inst_meta['funding_rate'].mean():.6f}, "
              f"std={inst_meta['funding_rate'].std():.6f}")
        print(f"  ADV: mean=${inst_meta['adv_notional'].mean():.2e}")

    print("\n" + "=" * 60)
    print("✓ Validation passed: data loaded successfully")
    print("=" * 60)


if __name__ == '__main__':
    if len(sys.argv) != 2:
        print("Usage: python scripts/validate_real_data.py <parquet_path>")
        sys.exit(1)

    validate_real_data_parquet(sys.argv[1])
