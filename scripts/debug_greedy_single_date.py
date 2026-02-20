#!/usr/bin/env python3
"""
Debug script for Mr Greedy portfolio optimization on a single date.

Runs optimization for one specific date and logs all intermediate states
to help diagnose alignment issues between declared and optimized instruments.

Usage:
    python scripts/debug_greedy_single_date.py \
        --config config/crypto_perps_greedy.yaml \
        --data data/dataset_538registry_6yr_jagged.parquet \
        --date 2021-07-20 \
        --instrument ETHUSDT_PERP
"""

import sys
import argparse
from pathlib import Path
from datetime import datetime

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import pandas as pd
import numpy as np

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


def debug_single_date(
    config_path: str,
    data_path: str,
    target_date: str,
    target_instrument: str = None,
):
    """
    Debug optimization for a single date.

    Args:
        config_path: Path to config YAML
        data_path: Path to Parquet dataset
        target_date: Date to debug (YYYY-MM-DD format)
        target_instrument: Optional specific instrument to focus on
    """
    print("=" * 80)
    print("MR GREEDY SINGLE-DATE DEBUGGER")
    print("=" * 80)
    print(f"\nConfig: {config_path}")
    print(f"Data: {data_path}")
    print(f"Target date: {target_date}")
    if target_instrument:
        print(f"Target instrument: {target_instrument}")

    # Parse target date
    target_dt = pd.Timestamp(target_date)

    # Load config and data
    # Resolve full paths
    config_path_full = str(Path(config_path).resolve())
    data_path_full = str(Path(data_path).resolve())

    config = Config(config_path_full)
    data = parquetCryptoPerpsSimData(dataset_path=data_path_full)

    # Build system with Mr Greedy portfolio
    print("\n" + "-" * 80)
    print("Building system...")
    print("-" * 80)

    system = System(
        [
            RawData(),
            Rules(),
            ForecastScaleCap(),
            ForecastCombine(),
            PositionSizing(),
            MrGreedyPortfolio(),
            Account(),
        ],
        data=data,
        config=config,
    )

    # Get declared instrument list
    print("\n" + "-" * 80)
    print("Phase 1: Declared Instruments")
    print("-" * 80)

    declared_instruments = system.portfolio.get_instrument_list()
    print(f"\nDeclared instruments (N): {len(declared_instruments)}")
    print(f"Sample: {declared_instruments[:10]}")

    if target_instrument and target_instrument not in declared_instruments:
        print(f"\n⚠ WARNING: Target instrument {target_instrument} not in declared list!")
        return

    # Get ideal fractional positions
    print("\n" + "-" * 80)
    print("Phase 2: Ideal Fractional Positions")
    print("-" * 80)

    try:
        ideal_positions_df = system.portfolio._get_ideal_fractional_positions()
        print(f"\nIdeal positions shape: {ideal_positions_df.shape}")
        print(f"Date range: {ideal_positions_df.index[0]} to {ideal_positions_df.index[-1]}")

        # Check if target date exists
        if target_dt not in ideal_positions_df.index:
            print(f"\n⚠ WARNING: Target date {target_dt.date()} not in ideal positions index!")
            print(f"Closest dates:")
            closest_dates = ideal_positions_df.index[
                ideal_positions_df.index.get_indexer([target_dt], method='nearest')
            ]
            print(f"  {closest_dates[0].date()}")
            return

        # Get positions for target date
        ideal_positions_series = ideal_positions_df.loc[target_dt]
        active_instruments = ideal_positions_series[ideal_positions_series.abs() > 0.001].index.tolist()

        print(f"\nActive instruments on {target_dt.date()}: {len(active_instruments)}")
        print(f"Sample: {active_instruments[:10]}")

        if target_instrument:
            if target_instrument in active_instruments:
                print(f"\n✓ {target_instrument} is active (ideal position: {ideal_positions_series[target_instrument]:.4f})")
            else:
                print(f"\n⚠ {target_instrument} is NOT active (position: {ideal_positions_series.get(target_instrument, 0.0):.4f})")

    except Exception as e:
        print(f"\n✗ FAILED to get ideal positions: {e}")
        import traceback
        traceback.print_exc()
        return

    # Try to build optimization inputs
    print("\n" + "-" * 80)
    print("Phase 3: Optimization Inputs")
    print("-" * 80)

    try:
        # Get prices
        prices = system.portfolio._get_prices_at_date(target_dt, active_instruments)
        print(f"\nPrices available: {len(prices)}")
        print(f"Non-NaN prices: {prices.notna().sum()}")

        # Build optimization inputs (this is where filtering happens)
        print("\nBuilding optimization inputs...")
        (
            contracts_optimal,
            per_contract_value,
            costs,
            covariance_matrix,
            constraints,
        ) = system.portfolio._build_optimization_inputs(
            date=target_dt,
            ideal_positions=ideal_positions_series,
            active_instruments=active_instruments,
            prices=prices,
        )

        # Report what survived filtering
        optimized_instruments = list(contracts_optimal.keys())
        print(f"\n✓ Optimization inputs built successfully")
        print(f"\nOptimized instruments (M): {len(optimized_instruments)}")
        print(f"Sample: {optimized_instruments[:10]}")

        # Check alignment: N vs M
        print("\n" + "-" * 80)
        print("Phase 4: Alignment Check (N vs M)")
        print("-" * 80)

        N = len(declared_instruments)
        M = len(optimized_instruments)

        print(f"\nN (declared): {N}")
        print(f"M (optimized): {M}")
        print(f"Difference: {N - M}")

        if N > M:
            print(f"\n⚠ ALIGNMENT ISSUE: N > M (declared more than optimized)")
            print(f"\nInstruments DECLARED but NOT OPTIMIZED:")
            missing = set(declared_instruments) - set(optimized_instruments)
            print(f"Count: {len(missing)}")
            print(f"Sample: {list(missing)[:20]}")

            if target_instrument and target_instrument in missing:
                print(f"\n✗ TARGET INSTRUMENT {target_instrument} IS MISSING!")
        elif N == M:
            print(f"\n✓ ALIGNMENT OK: N == M")
        else:
            print(f"\n⚠ UNEXPECTED: N < M (optimized more than declared)")

        # Check covariance filtering
        print("\n" + "-" * 80)
        print("Phase 5: Filtering Breakdown")
        print("-" * 80)

        print(f"\nActive instruments (with signals): {len(active_instruments)}")

        # Try to get covariance matrix for all active instruments
        try:
            cov_matrix = system.portfolio._get_covariance_matrix(target_dt, active_instruments)
            instruments_in_cov = list(cov_matrix.columns)
            print(f"Instruments in covariance matrix: {len(instruments_in_cov)}")

            filtered_by_cov = set(active_instruments) - set(instruments_in_cov)
            if filtered_by_cov:
                print(f"\nFiltered by covariance (insufficient history):")
                print(f"Count: {len(filtered_by_cov)}")
                print(f"Sample: {list(filtered_by_cov)[:10]}")

                if target_instrument and target_instrument in filtered_by_cov:
                    print(f"\n✗ {target_instrument} filtered by covariance!")

        except Exception as e:
            print(f"\n✗ Failed to get covariance matrix: {e}")

        # Check cost filtering
        try:
            costs_all = system.portfolio._get_sr_costs(target_dt, instruments_in_cov)
            instruments_in_costs = list(costs_all.keys())
            print(f"Instruments with cost data: {len(instruments_in_costs)}")

            filtered_by_costs = set(instruments_in_cov) - set(instruments_in_costs)
            if filtered_by_costs:
                print(f"\nFiltered by costs:")
                print(f"Count: {len(filtered_by_costs)}")
                print(f"Sample: {list(filtered_by_costs)[:10]}")

                if target_instrument and target_instrument in filtered_by_costs:
                    print(f"\n✗ {target_instrument} filtered by costs!")

        except Exception as e:
            print(f"\n✗ Failed to get costs: {e}")

        # Final check: intersection
        final_instruments = set(instruments_in_cov).intersection(set(instruments_in_costs))
        print(f"\nFinal instruments (cov ∩ costs): {len(final_instruments)}")

        if target_instrument:
            if target_instrument in final_instruments:
                print(f"\n✓ {target_instrument} in final optimization set")
            else:
                print(f"\n✗ {target_instrument} NOT in final optimization set")

    except Exception as e:
        print(f"\n✗ FAILED to build optimization inputs: {e}")
        import traceback
        traceback.print_exc()
        return

    # Try to run full optimization
    print("\n" + "-" * 80)
    print("Phase 6: Run Optimization")
    print("-" * 80)

    try:
        optimal_positions = system.portfolio._optimize_integer_positions(
            date=target_dt,
            ideal_positions_df=ideal_positions_df,
            previous_positions=None,
        )

        print(f"\n✓ Optimization completed successfully")
        print(f"Optimal positions: {len(optimal_positions)}")
        print(f"Positions with non-zero values: {sum(1 for v in optimal_positions.values() if abs(v) > 0)}")

        if target_instrument:
            target_pos = optimal_positions.get(target_instrument, 0.0)
            print(f"\n{target_instrument} position: {target_pos:.4f}")

    except Exception as e:
        print(f"\n✗ Optimization failed: {e}")
        import traceback
        traceback.print_exc()

    # Try to get position via get_notional_position
    print("\n" + "-" * 80)
    print("Phase 7: Get Notional Position (End-to-End)")
    print("-" * 80)

    if target_instrument:
        try:
            position = system.portfolio.get_notional_position(target_instrument)
            position_at_date = position.loc[target_dt] if target_dt in position.index else np.nan

            print(f"\n✓ Position retrieved successfully")
            print(f"Position at {target_dt.date()}: {position_at_date:.4f}")

        except Exception as e:
            print(f"\n✗ Failed to get position: {e}")
            import traceback
            traceback.print_exc()

    # Summary
    print("\n" + "=" * 80)
    print("DEBUG SUMMARY")
    print("=" * 80)

    print(f"\nN (declared instruments): {N}")
    print(f"M (optimized instruments): {M}")
    print(f"Alignment issue: {'YES' if N > M else 'NO'}")

    if target_instrument:
        print(f"\nTarget instrument {target_instrument}:")
        print(f"  In declared list: {target_instrument in declared_instruments}")
        print(f"  Has active signal: {target_instrument in active_instruments if target_instrument in declared_instruments else 'N/A'}")
        print(f"  In covariance matrix: {target_instrument in instruments_in_cov if 'instruments_in_cov' in locals() else 'N/A'}")
        print(f"  In cost data: {target_instrument in instruments_in_costs if 'instruments_in_costs' in locals() else 'N/A'}")
        print(f"  In final optimization: {target_instrument in final_instruments if 'final_instruments' in locals() else 'N/A'}")


def main():
    parser = argparse.ArgumentParser(
        description="Debug Mr Greedy portfolio optimization for a single date"
    )
    parser.add_argument(
        "--config",
        type=str,
        default="config/crypto_perps_greedy.yaml",
        help="Path to config YAML",
    )
    parser.add_argument(
        "--data",
        type=str,
        default="data/dataset_538registry_6yr_jagged.parquet",
        help="Path to Parquet dataset",
    )
    parser.add_argument(
        "--date",
        type=str,
        required=True,
        help="Date to debug (YYYY-MM-DD format)",
    )
    parser.add_argument(
        "--instrument",
        type=str,
        default=None,
        help="Optional specific instrument to focus on (e.g., ETHUSDT_PERP)",
    )

    args = parser.parse_args()

    debug_single_date(
        config_path=args.config,
        data_path=args.data,
        target_date=args.date,
        target_instrument=args.instrument,
    )


if __name__ == "__main__":
    main()
