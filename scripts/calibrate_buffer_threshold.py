#!/usr/bin/env python3
"""
Calibrate tracking_error_buffer parameter for Mr Greedy portfolio.

The tracking_error_buffer parameter controls when the portfolio rebalances.
Only trade if tracking_error > buffer. Higher values lead to less frequent
trading; lower values lead to more frequent rebalancing.

This script runs a grid search over buffer values and measures:
- Trade frequency (% of days with trades)
- Sharpe ratio
- Annual turnover
- Average tracking error

Expected optimal range: 0.01-0.0125 (1.0%-1.25% of portfolio volatility)
"""

import sys
from pathlib import Path
import pandas as pd
import numpy as np
import json
from datetime import datetime

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

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


def calculate_trade_frequency(positions_df: pd.DataFrame) -> float:
    """
    Calculate trade frequency as percentage of days with any position change.

    Args:
        positions_df: DataFrame of positions (dates × instruments)

    Returns:
        Percentage of days with trades (0-100)
    """
    # Calculate position changes
    position_changes = positions_df.diff().abs()

    # Days with any trade
    days_with_trades = (position_changes.sum(axis=1) > 0).sum()

    # Total days
    total_days = len(positions_df)

    # Percentage
    trade_frequency = (days_with_trades / total_days) * 100

    return trade_frequency


def calculate_turnover(positions_df: pd.DataFrame, prices_df: pd.DataFrame) -> float:
    """Calculate annual turnover in round-trips per year."""
    position_changes = positions_df.diff().abs()
    prices_aligned = prices_df.reindex(position_changes.index, method='ffill')
    trade_values = (position_changes * prices_aligned).sum(axis=1)

    avg_position_values = (positions_df.abs() * prices_aligned).sum(axis=1)
    avg_capital_deployed = avg_position_values.mean()

    if avg_capital_deployed == 0:
        return 0.0

    total_trades = trade_values.sum()
    years = len(positions_df) / 252
    annual_turnover = (total_trades / avg_capital_deployed / years) / 2

    return annual_turnover


def run_backtest_with_buffer(
    data_path: str,
    config_path: str,
    shadow_cost: float,
    tracking_error_buffer: float,
    output_dir: Path,
) -> dict:
    """
    Run backtest with specific tracking_error_buffer value.

    Args:
        data_path: Path to dataset
        config_path: Path to config file
        shadow_cost: Shadow cost value (from calibration)
        tracking_error_buffer: Buffer value to test
        output_dir: Directory to save results

    Returns:
        Dict with performance metrics
    """
    print(f"\n{'='*80}")
    print(f"Testing tracking_error_buffer = {tracking_error_buffer:.4f}")
    print(f"{'='*80}")

    # Load config
    import yaml

    with open(config_path) as f:
        config_dict = yaml.safe_load(f)

    # Update greedy_params
    if 'greedy_params' not in config_dict:
        config_dict['greedy_params'] = {}

    config_dict['greedy_params']['shadow_cost'] = shadow_cost
    config_dict['greedy_params']['tracking_error_buffer'] = tracking_error_buffer

    # Create Config object from modified dict
    config = Config(config_dict)

    # Load data
    data = parquetCryptoPerpsSimData(dataset_path=data_path)

    # Build system
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

    print(f"\nCalculating portfolio curve...")
    portfolio_curve = system.accounts.portfolio()

    sharpe = portfolio_curve.sharpe()
    annual_return = portfolio_curve.ann_mean() * 100
    annual_vol = portfolio_curve.ann_std() * 100

    print(f"  Sharpe ratio: {sharpe:.3f}")
    print(f"  Annual return: {annual_return:.1f}%")
    print(f"  Annual volatility: {annual_vol:.1f}%")

    # Get positions
    print(f"\nExtracting positions...")
    instruments = system.data.get_instrument_list()
    positions_dict = {}

    for instrument in instruments:
        try:
            positions_dict[instrument] = system.portfolio.get_notional_position(instrument)
        except Exception as e:
            print(f"  Warning: Could not get position for {instrument}: {e}")
            continue

    positions_df = pd.DataFrame(positions_dict)

    # Calculate statistics
    num_positions = (positions_df.abs() > 0).sum(axis=1)
    avg_positions = num_positions.mean()

    print(f"\n  Average positions: {avg_positions:.1f}")

    # Calculate trade frequency
    trade_freq = calculate_trade_frequency(positions_df)
    print(f"  Trade frequency: {trade_freq:.1f}% of days")

    # Calculate turnover
    print(f"\nCalculating turnover...")
    prices_dict = {}
    for instrument in positions_df.columns:
        try:
            prices_dict[instrument] = system.rawdata.get_daily_prices(instrument)
        except:
            continue

    prices_df = pd.DataFrame(prices_dict)
    turnover = calculate_turnover(positions_df, prices_df)

    print(f"  Annual turnover: {turnover:.1f} round-trips/year")

    # Save results
    output_file = output_dir / f"buffer_{tracking_error_buffer:.4f}.json"
    results = {
        'shadow_cost': shadow_cost,
        'tracking_error_buffer': tracking_error_buffer,
        'sharpe': float(sharpe),
        'annual_return': float(annual_return),
        'annual_vol': float(annual_vol),
        'avg_positions': float(avg_positions),
        'trade_frequency_pct': float(trade_freq),
        'annual_turnover': float(turnover),
        'start_date': str(positions_df.index[0].date()),
        'end_date': str(positions_df.index[-1].date()),
    }

    with open(output_file, 'w') as f:
        json.dump(results, f, indent=2)

    print(f"\nResults saved to: {output_file}")

    return results


def main():
    """Run tracking_error_buffer calibration sweep."""
    print("="*80)
    print("MR GREEDY — TRACKING ERROR BUFFER CALIBRATION")
    print("="*80)

    # Configuration
    data_path = project_root / "data" / "dataset_538registry_6yr_jagged.parquet"
    config_path = project_root / "config" / "research" / "crypto_perps_greedy_simple.yaml"
    output_dir = project_root / "out" / "calibration_buffer"

    # Create output directory
    output_dir.mkdir(parents=True, exist_ok=True)

    # Load recommended shadow_cost from previous calibration
    shadow_cost_rec_file = project_root / "out" / "calibration_shadow_cost" / "recommendation.json"

    if shadow_cost_rec_file.exists():
        with open(shadow_cost_rec_file) as f:
            shadow_cost_rec = json.load(f)
        shadow_cost = shadow_cost_rec['recommended_shadow_cost']
        print(f"\nUsing calibrated shadow_cost: {shadow_cost}")
    else:
        shadow_cost = 100
        print(f"\nWarning: No shadow_cost calibration found, using default: {shadow_cost}")
        print(f"  Run calibrate_shadow_cost.py first for better results")

    # Buffer values to test
    # Carver's futures value: 0.0125 (1.25%)
    # Expected crypto range: 0.005-0.020 (0.5%-2.0%)
    # Target: ~25% of days trade (weekly-ish rebalancing)
    buffers = [0.005, 0.0075, 0.010, 0.0125, 0.015, 0.020]

    print(f"\nDataset: {data_path}")
    print(f"Config: {config_path}")
    print(f"Output: {output_dir}")
    print(f"\nTesting tracking_error_buffer values: {buffers}")
    print(f"Expected optimal range: 0.010-0.0125")
    print(f"Target trade frequency: ~25% of days (weekly rebalancing)")

    # Run calibration sweep
    all_results = []

    for buffer in buffers:
        try:
            results = run_backtest_with_buffer(
                data_path=str(data_path),
                config_path=str(config_path),
                shadow_cost=shadow_cost,
                tracking_error_buffer=buffer,
                output_dir=output_dir,
            )
            all_results.append(results)

        except Exception as e:
            print(f"\nERROR: Backtest failed for buffer={buffer}")
            print(f"  {str(e)}")
            import traceback
            traceback.print_exc()
            continue

    # Generate summary
    print("\n" + "="*80)
    print("CALIBRATION SUMMARY")
    print("="*80)

    if len(all_results) == 0:
        print("\nNo results to summarize (all backtests failed)")
        return

    summary_df = pd.DataFrame(all_results)
    summary_df = summary_df.sort_values('tracking_error_buffer')

    print("\nResults by tracking_error_buffer:")
    print(summary_df[['tracking_error_buffer', 'sharpe', 'trade_frequency_pct',
                      'annual_turnover', 'avg_positions']].to_string(index=False))

    # Find optimal buffer
    # Target: maximize Sharpe while keeping trade frequency reasonable (~20-30% of days)
    print("\n" + "-"*80)
    print("ANALYSIS")
    print("-"*80)

    # Sharpe-optimal
    best_sharpe_idx = summary_df['sharpe'].idxmax()
    best_sharpe = summary_df.loc[best_sharpe_idx]

    print(f"\nBest Sharpe ratio: {best_sharpe['sharpe']:.3f}")
    print(f"  tracking_error_buffer: {best_sharpe['tracking_error_buffer']:.4f}")
    print(f"  Trade frequency: {best_sharpe['trade_frequency_pct']:.1f}% of days")
    print(f"  Turnover: {best_sharpe['annual_turnover']:.1f} rt/yr")

    # Trade-frequency-constrained optimal (20-30% of days)
    reasonable_frequency = summary_df[
        (summary_df['trade_frequency_pct'] >= 15) &
        (summary_df['trade_frequency_pct'] <= 35)
    ]

    if len(reasonable_frequency) > 0:
        best_constrained_idx = reasonable_frequency['sharpe'].idxmax()
        best_constrained = reasonable_frequency.loc[best_constrained_idx]

        print(f"\nBest Sharpe with reasonable trade frequency (15-35% of days):")
        print(f"  tracking_error_buffer: {best_constrained['tracking_error_buffer']:.4f}")
        print(f"  Sharpe: {best_constrained['sharpe']:.3f}")
        print(f"  Trade frequency: {best_constrained['trade_frequency_pct']:.1f}% of days")
        print(f"  Turnover: {best_constrained['annual_turnover']:.1f} rt/yr")

        recommended_buffer = best_constrained['tracking_error_buffer']
    else:
        print(f"\nNo results in target trade frequency range (15-35% of days)")
        recommended_buffer = best_sharpe['tracking_error_buffer']

    print(f"\n{'='*80}")
    print(f"RECOMMENDED TRACKING_ERROR_BUFFER: {recommended_buffer:.4f}")
    print(f"{'='*80}")

    # Save summary
    summary_file = output_dir / "summary.csv"
    summary_df.to_csv(summary_file, index=False)
    print(f"\nSummary saved to: {summary_file}")

    # Save recommendation
    recommendation = {
        'recommended_shadow_cost': float(shadow_cost),
        'recommended_tracking_error_buffer': float(recommended_buffer),
        'sharpe': float(summary_df.loc[summary_df['tracking_error_buffer'] == recommended_buffer, 'sharpe'].iloc[0]),
        'trade_frequency_pct': float(summary_df.loc[summary_df['tracking_error_buffer'] == recommended_buffer, 'trade_frequency_pct'].iloc[0]),
        'turnover': float(summary_df.loc[summary_df['tracking_error_buffer'] == recommended_buffer, 'annual_turnover'].iloc[0]),
        'calibration_date': datetime.now().isoformat(),
    }

    recommendation_file = output_dir / "recommendation.json"
    with open(recommendation_file, 'w') as f:
        json.dump(recommendation, f, indent=2)

    print(f"Recommendation saved to: {recommendation_file}")


if __name__ == "__main__":
    main()
