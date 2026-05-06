#!/usr/bin/env python3
"""
Diagnostic script to analyze Mr Greedy optimizer behavior on specific dates.

Shows:
- How many instruments had signals vs were selected
- Position sizes as % of capital
- Why optimizer terminated (buffer? costs? max positions?)
- Tracking error and cost trade-offs

Usage:
    python scripts/diagnose_greedy_positions.py \
        --config config/research/crypto_perps_greedy.yaml \
        --data data/example_crypto_perps_15x4yr.parquet \
        --dates 2021-02-15,2021-06-15,2022-01-15
"""

import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import argparse
import pandas as pd
import numpy as np
from datetime import datetime

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

from systems.crypto_perps.greedy_portfolio import MrGreedyPortfolio


def analyze_date(
    system: System,
    date: pd.Timestamp,
    capital: float,
    max_position_fraction: float,
) -> None:
    """Analyze optimizer behavior for a specific date."""

    print("\n" + "=" * 80)
    print(f"DATE: {date.date()}")
    print("=" * 80)

    # Get ideal fractional positions
    ideal_positions_df = system.portfolio._get_ideal_fractional_positions()

    if date not in ideal_positions_df.index:
        print(f"⚠️  Date not in dataset")
        return

    ideal_positions = ideal_positions_df.loc[date]

    # Get instruments with signals
    instruments_with_signals = ideal_positions[ideal_positions.abs() > 0.001].index.tolist()

    print(f"\nSignals:")
    print(f"  Total instruments in universe: {len(ideal_positions)}")
    print(f"  Instruments with signals: {len(instruments_with_signals)}")

    if len(instruments_with_signals) == 0:
        print("  ⚠️  No signals on this date")
        return

    # Show top 5 strongest signals
    print(f"\n  Top 5 strongest signals:")
    top_signals = ideal_positions.abs().nlargest(5)
    for inst, signal in top_signals.items():
        print(f"    {inst:20s}: {signal:8.2f}")

    # Get optimized positions
    optimized_positions_df = system.portfolio._get_all_optimized_positions()

    if date not in optimized_positions_df.index:
        print(f"\n⚠️  No optimized positions for this date")
        return

    optimized_positions = optimized_positions_df.loc[date]

    # Get selected instruments
    instruments_selected = optimized_positions[optimized_positions.abs() > 0.001].index.tolist()

    print(f"\nOptimization Results:")
    print(f"  Instruments selected: {len(instruments_selected)}")
    print(f"  Selection rate: {len(instruments_selected) / len(instruments_with_signals):.1%}")

    if len(instruments_selected) == 0:
        print("  ⚠️  Optimizer selected zero positions (tracking error buffer may have prevented trading)")
        return

    # Calculate position values
    data = system.data
    position_values = {}
    position_fractions = {}

    for inst in instruments_selected:
        try:
            price = data.get_raw_price(inst).loc[:date].iloc[-1]
            lot_size = system.portfolio.lot_size_provider.get_lot_size(inst)
            lot_value = system.portfolio.lot_size_provider.get_lot_value(inst, price)

            position = optimized_positions[inst]
            position_value = abs(position) * lot_value
            position_fraction = position_value / capital

            position_values[inst] = position_value
            position_fractions[inst] = position_fraction

        except Exception as e:
            print(f"    ⚠️  Could not calculate position value for {inst}: {e}")
            continue

    # Show position sizes
    print(f"\n  Position Sizes (as % of ${capital:,.0f} capital):")

    # Sort by position fraction descending
    sorted_positions = sorted(
        position_fractions.items(),
        key=lambda x: x[1],
        reverse=True
    )

    for inst, frac in sorted_positions:
        value = position_values[inst]
        max_value = capital * max_position_fraction
        status = "✓" if value <= max_value else "❌ EXCEEDS LIMIT"
        print(f"    {inst:20s}: {frac:6.1%} (${value:8,.0f})  {status}")

    # Summary stats
    print(f"\n  Summary:")
    max_position = max(position_fractions.values())
    print(f"    Max position size: {max_position:.1%} (limit: {max_position_fraction:.1%})")
    print(f"    Positions within limit: {sum(1 for f in position_fractions.values() if f <= max_position_fraction)} / {len(position_fractions)}")

    # Check if any positions exceeded limit
    if max_position > max_position_fraction:
        print(f"\n  ❌ WARNING: Position size constraint violated!")
        print(f"     Max position {max_position:.1%} exceeds limit {max_position_fraction:.1%}")
    else:
        print(f"\n  ✅ All positions within limit")


def main():
    parser = argparse.ArgumentParser(description="Diagnose Mr Greedy optimizer positions")
    parser.add_argument("--config", required=True, help="Config YAML path")
    parser.add_argument("--data", required=True, help="Dataset parquet path")
    parser.add_argument("--dates", required=True, help="Comma-separated dates (YYYY-MM-DD)")
    args = parser.parse_args()

    print("=" * 80)
    print("MR GREEDY OPTIMIZER DIAGNOSTIC")
    print("=" * 80)

    # Load config
    print(f"\nLoading config: {args.config}")
    config = Config(args.config)

    capital = config.get_element_or_default('notional_trading_capital', 10000.0)
    greedy_params = config.get_element_or_default('greedy_params', {})
    max_position_fraction = greedy_params.get('max_position_fraction', 0.25)
    shadow_cost = greedy_params.get('shadow_cost', 100)
    tracking_error_buffer = greedy_params.get('tracking_error_buffer', 0.0125)

    print(f"\nConfig Parameters:")
    print(f"  Capital: ${capital:,.0f}")
    print(f"  Max position fraction: {max_position_fraction:.1%}")
    print(f"  Shadow cost: {shadow_cost}")
    print(f"  Tracking error buffer: {tracking_error_buffer:.2%}")

    # Load data
    print(f"\nLoading dataset: {args.data}")
    data = parquetCryptoPerpsSimData(args.data)

    # Create system
    print(f"\nCreating system with MrGreedyPortfolio...")
    system = System(
        stage_list=[
            RawData(),
            ForecastScaleCap(),
            ForecastCombine(),
            PositionSizing(),
            MrGreedyPortfolio(),
            Account(),
        ],
        data=data,
        config=config,
    )

    # Parse dates
    date_strings = args.dates.split(',')
    dates = [pd.Timestamp(d.strip()) for d in date_strings]

    print(f"\nAnalyzing {len(dates)} dates...")

    # Analyze each date
    for date in dates:
        try:
            analyze_date(system, date, capital, max_position_fraction)
        except Exception as e:
            print(f"\n❌ Error analyzing {date.date()}: {e}")
            import traceback
            print(traceback.format_exc())

    print("\n" + "=" * 80)
    print("DIAGNOSTIC COMPLETE")
    print("=" * 80)


if __name__ == "__main__":
    main()
