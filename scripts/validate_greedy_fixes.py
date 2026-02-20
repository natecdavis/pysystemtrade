#!/usr/bin/env python3
"""
Quick validation script to test Mr Greedy optimizer fixes.

Tests:
1. Maximum position constraint is enforced (25% of capital)
2. Position sizes are reasonable (not absurdly large)
3. Optimizer runs without KeyErrors

Usage:
    python scripts/validate_greedy_fixes.py
"""

import sys
import pandas as pd
import numpy as np
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# Configure logging
from syscore.constants import arg_not_supplied
import logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(name)s %(message)s'
)

# Import system components
from systems.basesystem import System
from systems.rawdata import RawData
from systems.forecast_scale_cap import ForecastScaleCap
from systems.forecast_combine import ForecastCombine
from systems.positionsizing import PositionSizing
from systems.accounts.accounts_stage import Account

from sysdata.config.configdata import Config
from sysdata.crypto.parquet_perps_sim_data import parquetCryptoPerpsSimData

# Import MrGreedyPortfolio
from systems.crypto_perps.greedy_portfolio import MrGreedyPortfolio

def main():
    print("=" * 80)
    print("MR GREEDY OPTIMIZER FIX VALIDATION")
    print("=" * 80)
    print()

    # Load simple config (EWMAC only for speed)
    config_path = Path("config/crypto_perps_greedy_simple.yaml")
    if not config_path.exists():
        print(f"ERROR: Config file not found: {config_path}")
        sys.exit(1)

    config = Config(str(config_path))

    # Get key parameters
    capital = config.get_element_or_default('notional_trading_capital', 10000.0)
    greedy_params = config.get_element_or_default('greedy_params', {})
    max_position_fraction = greedy_params.get('max_position_fraction', 0.25)
    shadow_cost = greedy_params.get('shadow_cost', 100)
    tracking_error_buffer = greedy_params.get('tracking_error_buffer', 0.0125)

    print(f"Config Parameters:")
    print(f"  Capital: ${capital:,.0f}")
    print(f"  Max position fraction: {max_position_fraction:.1%}")
    print(f"  Max position value: ${capital * max_position_fraction:,.0f}")
    print(f"  Shadow cost: {shadow_cost}")
    print(f"  Tracking error buffer: {tracking_error_buffer:.2%}")
    print()

    # Load data
    data_path = Path("data/example_crypto_perps_15x4yr.parquet")
    if not data_path.exists():
        print(f"ERROR: Dataset not found: {data_path}")
        sys.exit(1)

    print(f"Loading dataset: {data_path}")
    data = parquetCryptoPerpsSimData(str(data_path))

    instruments = data.get_instrument_list()
    print(f"Instruments: {len(instruments)}")
    print()

    # Create system with MrGreedyPortfolio
    print("Creating system with MrGreedyPortfolio...")
    system = System(
        stage_list=[
            RawData(),
            ForecastScaleCap(),
            ForecastCombine(),
            PositionSizing(),
            MrGreedyPortfolio(),  # Use greedy optimizer
            Account(),
        ],
        data=data,
        config=config,
    )

    print("System created successfully!")
    print()

    # Test single instrument position
    test_instrument = instruments[0]
    print(f"Testing position calculation for {test_instrument}...")
    print()

    try:
        # Get position (this will trigger full optimization)
        position = system.portfolio.get_notional_position(test_instrument)

        print(f"✅ Position calculation successful!")
        print(f"   Position series length: {len(position)}")
        print(f"   Non-zero positions: {(position.abs() > 0).sum()}")
        print()

        # Get prices to calculate position values
        prices = data.get_raw_price(test_instrument)

        # Calculate position value for each date
        position_aligned = position.reindex(prices.index, method='ffill').fillna(0)
        position_values = position_aligned.abs() * prices

        # Check max position value
        max_position_value = position_values.max()
        max_allowed_value = capital * max_position_fraction

        print(f"Position Size Validation:")
        print(f"  Max position value: ${max_position_value:,.2f}")
        print(f"  Max allowed value: ${max_allowed_value:,.2f}")
        print(f"  Constraint respected: {'✅ YES' if max_position_value <= max_allowed_value else '❌ NO'}")
        print()

        # Get some stats
        avg_position = position[position.abs() > 0].abs().mean()
        print(f"Position Statistics:")
        print(f"  Average non-zero position: {avg_position:.2f} contracts")
        print(f"  Max position: {position.abs().max():.2f} contracts")
        print(f"  Min position: {position[position.abs() > 0].abs().min():.2f} contracts")
        print()

        # Test all instruments to get portfolio stats
        print("Calculating positions for all instruments...")
        all_positions = {}
        for inst in instruments:
            try:
                pos = system.portfolio.get_notional_position(inst)
                all_positions[inst] = pos
            except Exception as e:
                print(f"   ⚠️  Failed for {inst}: {e}")

        # Create position DataFrame
        positions_df = pd.DataFrame(all_positions)

        # Count positions per day
        positions_per_day = (positions_df.abs() > 0).sum(axis=1)

        print()
        print(f"Portfolio Statistics:")
        print(f"  Total instruments: {len(instruments)}")
        print(f"  Successful optimizations: {len(all_positions)}")
        print(f"  Avg positions per day: {positions_per_day.mean():.1f}")
        print(f"  Max positions per day: {positions_per_day.max():.0f}")
        print(f"  Min positions per day: {positions_per_day.min():.0f}")
        print()

        # Check if position counts are reasonable (should be > 3 with fixes)
        if positions_per_day.mean() > 4.0:
            print("✅ Position counts look good (avg > 4)")
        else:
            print(f"⚠️  Position counts seem low (avg = {positions_per_day.mean():.1f})")
            print("   This might indicate the tracking error buffer is still too tight.")

        print()
        print("=" * 80)
        print("VALIDATION COMPLETE")
        print("=" * 80)

        return 0

    except Exception as e:
        print(f"❌ ERROR: {type(e).__name__}: {str(e)}")
        import traceback
        print()
        print("Traceback:")
        print(traceback.format_exc())
        return 1


if __name__ == "__main__":
    sys.exit(main())
