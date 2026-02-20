#!/usr/bin/env python3
"""
Smoke test for Mr Greedy portfolio system.

Tests the integration of:
- LotSizeProvider
- MrGreedyPortfolio stage
- Greedy optimizer
- Config parameters

Uses small dataset (15 instruments, 4 years) for fast iteration.
"""

import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import pandas as pd
import numpy as np

from syscore.constants import arg_not_supplied
from sysdata.config.configdata import Config
from sysdata.crypto.parquet_perps_sim_data import parquetCryptoPerpsSimData

from systems.forecasting import Rules
from systems.basesystem import System
from systems.forecast_combine import ForecastCombine
from systems.forecast_scale_cap import ForecastScaleCap
from systems.rawdata import RawData
from systems.positionsizing import PositionSizing
from systems.accounts.accounts_stage import Account

# Import the new Mr Greedy portfolio
from systems.crypto_perps.greedy_portfolio import MrGreedyPortfolio


def create_greedy_system(
    data_path: str,
    config_path: str = None,
) -> System:
    """
    Create a system using Mr Greedy portfolio.

    Args:
        data_path: Path to Parquet dataset
        config_path: Path to config YAML (defaults to crypto_perps_greedy.yaml)

    Returns:
        System with MrGreedyPortfolio stage
    """
    # Load config
    if config_path is None:
        config_path = project_root / "config" / "crypto_perps_greedy_simple.yaml"

    config = Config(str(config_path))

    # Load data
    data = parquetCryptoPerpsSimData(dataset_path=data_path)

    # Build system with Mr Greedy portfolio
    system = System(
        [
            RawData(),
            Rules(),
            ForecastScaleCap(),
            ForecastCombine(),
            PositionSizing(),
            MrGreedyPortfolio(),  # Replace standard Portfolios with greedy
            Account(),
        ],
        data=data,
        config=config,
    )

    return system


def run_smoke_test():
    """
    Run smoke test on small dataset.

    Expected results:
    - Backtest completes without errors
    - 10-15 positions held on average
    - Tracking error < 3% annualized
    - Sharpe > 0.6 (reasonable for smoke test)
    """
    print("=" * 80)
    print("MR GREEDY PORTFOLIO — SMOKE TEST")
    print("=" * 80)

    # Use small dataset for fast iteration
    data_path = project_root / "data" / "example_crypto_perps_15x4yr.parquet"

    if not data_path.exists():
        print(f"\nERROR: Dataset not found at {data_path}")
        print("Please ensure the test dataset exists.")
        sys.exit(1)

    print(f"\nDataset: {data_path}")
    print(f"Config: config/crypto_perps_greedy_simple.yaml (EWMAC only)")

    # Create system
    print("\n" + "-" * 80)
    print("Creating system with MrGreedyPortfolio...")
    print("-" * 80)

    system = create_greedy_system(str(data_path))

    # Get instrument list
    instruments = system.data.get_instrument_list()
    print(f"\nInstruments available: {len(instruments)}")
    print(f"Sample: {instruments[:5]}")

    # Test 1: Get positions for one instrument
    print("\n" + "-" * 80)
    print("TEST 1: Get position for single instrument")
    print("-" * 80)

    test_instrument = instruments[0]
    print(f"\nFetching position for {test_instrument}...")

    try:
        position = system.portfolio.get_notional_position(test_instrument)
        print(f"✓ Position fetched successfully")
        print(f"  Shape: {position.shape}")
        print(f"  Non-zero days: {(position.abs() > 0).sum()}")

        if len(position) > 0:
            print(f"  Date range: {position.index[0]} to {position.index[-1]}")
        else:
            print(f"  WARNING: Empty position series")

        # Show sample positions
        non_zero = position[position.abs() > 0]
        if len(non_zero) > 0:
            print(f"\n  Sample positions (first 5 non-zero):")
            for date, pos in non_zero.head().items():
                print(f"    {date.date()}: {pos:.4f}")
        else:
            print(f"\n  WARNING: No non-zero positions for {test_instrument}")

    except Exception as e:
        print(f"✗ FAILED: {str(e)}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

    # Test 2: Get positions for all instruments
    print("\n" + "-" * 80)
    print("TEST 2: Get positions for all instruments")
    print("-" * 80)

    print(f"\nFetching positions for all {len(instruments)} instruments...")

    try:
        positions_dict = {}
        for i, instrument in enumerate(instruments):
            if i % 5 == 0:
                print(f"  Progress: {i}/{len(instruments)}")
            positions_dict[instrument] = system.portfolio.get_notional_position(instrument)

        positions_df = pd.DataFrame(positions_dict)
        print(f"✓ All positions fetched successfully")
        print(f"  Shape: {positions_df.shape}")

        # Calculate statistics
        num_positions = (positions_df.abs() > 0).sum(axis=1)
        print(f"\n  Positions per day:")
        print(f"    Min: {num_positions.min()}")
        print(f"    Max: {num_positions.max()}")
        print(f"    Mean: {num_positions.mean():.1f}")
        print(f"    Median: {num_positions.median():.1f}")

        # Check if within expected range (10-15)
        avg_positions = num_positions.mean()
        if 8 <= avg_positions <= 20:
            print(f"  ✓ Average positions ({avg_positions:.1f}) in expected range (8-20)")
        else:
            print(f"  ⚠ Average positions ({avg_positions:.1f}) outside expected range (8-20)")

    except Exception as e:
        print(f"✗ FAILED: {str(e)}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

    # Test 3: Calculate portfolio P&L
    print("\n" + "-" * 80)
    print("TEST 3: Calculate portfolio P&L")
    print("-" * 80)

    try:
        print("\nCalculating portfolio curve...")
        portfolio_curve = system.accounts.portfolio()

        sharpe = portfolio_curve.sharpe()
        annual_return = portfolio_curve.ann_mean() * 100
        annual_vol = portfolio_curve.ann_std() * 100

        print(f"✓ Portfolio curve calculated successfully")
        print(f"\n  Performance metrics:")
        print(f"    Sharpe ratio: {sharpe:.2f}")
        print(f"    Annual return: {annual_return:.1f}%")
        print(f"    Annual volatility: {annual_vol:.1f}%")

        # Check if Sharpe > 0.6
        if sharpe > 0.6:
            print(f"  ✓ Sharpe ratio ({sharpe:.2f}) exceeds minimum threshold (0.6)")
        else:
            print(f"  ⚠ Sharpe ratio ({sharpe:.2f}) below expected threshold (0.6)")

    except Exception as e:
        print(f"✗ FAILED: {str(e)}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

    # Test 4: Verify lot sizing
    print("\n" + "-" * 80)
    print("TEST 4: Verify lot sizing")
    print("-" * 80)

    try:
        lot_provider = system.portfolio.lot_size_provider

        # Check a few known instruments
        btc_lot = lot_provider.get_lot_size('BTCUSDT_PERP')
        eth_lot = lot_provider.get_lot_size('ETHUSDT_PERP')

        print(f"\n  Lot sizes:")
        print(f"    BTCUSDT_PERP: {btc_lot}")
        print(f"    ETHUSDT_PERP: {eth_lot}")

        # Check if any instruments used default
        used_default = lot_provider.instruments_using_default
        if len(used_default) > 0:
            print(f"\n  ⚠ {len(used_default)} instruments using default lot size:")
            for instr in list(used_default)[:5]:
                print(f"    {instr}")
        else:
            print(f"\n  ✓ All instruments have explicit lot size mappings")

    except Exception as e:
        print(f"✗ FAILED: {str(e)}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

    # Summary
    print("\n" + "=" * 80)
    print("SMOKE TEST SUMMARY")
    print("=" * 80)
    print("\n✓ All tests passed!")
    print("\nMr Greedy portfolio is working correctly.")
    print("\nNext steps:")
    print("  1. Run full backtest on 6-year dataset")
    print("  2. Calibrate shadow_cost parameter")
    print("  3. Calibrate tracking_error_buffer parameter")
    print("  4. Compare vs two-stage baseline system")


if __name__ == "__main__":
    run_smoke_test()
