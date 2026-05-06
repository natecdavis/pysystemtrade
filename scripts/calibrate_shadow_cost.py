#!/usr/bin/env python3
"""
Calibrate shadow_cost parameter for Mr Greedy portfolio.

The shadow_cost parameter controls the trade-off between tracking error and
transaction costs. Higher values lead to fewer trades and more tracking error;
lower values lead to more trades and less tracking error.

This script runs a grid search over shadow_cost values and measures:
- Sharpe ratio
- Annual turnover
- Average number of positions
- Average tracking error

Expected optimal range: 100-200 (lower than Carver's futures value of 250
due to higher correlation in crypto).
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


def calculate_turnover(positions_df: pd.DataFrame, prices_df: pd.DataFrame) -> float:
    """
    Calculate annual turnover in round-trips per year.

    Args:
        positions_df: DataFrame of positions (dates × instruments)
        prices_df: DataFrame of prices (dates × instruments)

    Returns:
        Annual turnover in round-trips per year
    """
    # Calculate position changes
    position_changes = positions_df.diff().abs()

    # Calculate notional value of trades
    # Align prices with position changes
    prices_aligned = prices_df.reindex(position_changes.index, method='ffill')
    trade_values = (position_changes * prices_aligned).sum(axis=1)

    # Calculate average position value
    avg_position_values = (positions_df.abs() * prices_aligned).sum(axis=1)
    avg_capital_deployed = avg_position_values.mean()

    if avg_capital_deployed == 0:
        return 0.0

    # Annual turnover = (sum of trades / avg capital) / years
    total_trades = trade_values.sum()
    years = len(positions_df) / 252

    # Divide by 2 because round-trip = buy + sell
    annual_turnover = (total_trades / avg_capital_deployed / years) / 2

    return annual_turnover


def calculate_tracking_error(
    actual_positions_df: pd.DataFrame,
    ideal_positions_df: pd.DataFrame,
    returns_df: pd.DataFrame
) -> float:
    """
    Calculate tracking error between actual and ideal portfolios.

    Args:
        actual_positions_df: Actual positions (greedy optimized)
        ideal_positions_df: Ideal fractional positions
        returns_df: Daily returns for each instrument

    Returns:
        Annualized tracking error (portfolio volatility)
    """
    # Align all DataFrames
    common_dates = actual_positions_df.index.intersection(ideal_positions_df.index)
    common_dates = common_dates.intersection(returns_df.index)

    if len(common_dates) == 0:
        return np.nan

    actual_pos = actual_positions_df.reindex(common_dates).fillna(0)
    ideal_pos = ideal_positions_df.reindex(common_dates).fillna(0)
    returns = returns_df.reindex(common_dates).fillna(0)

    # Calculate gap portfolio
    gap_positions = actual_pos - ideal_pos

    # Calculate daily returns of gap portfolio
    # Position × return = contribution to portfolio return
    gap_returns = (gap_positions.shift(1) * returns).sum(axis=1)

    # Annualized tracking error
    daily_te = gap_returns.std()
    annual_te = daily_te * np.sqrt(252)

    return annual_te


def run_backtest_with_shadow_cost(
    data_path: str,
    config_path: str,
    shadow_cost: float,
    output_dir: Path,
) -> dict:
    """
    Run backtest with specific shadow_cost value.

    Args:
        data_path: Path to dataset
        config_path: Path to config file
        shadow_cost: Shadow cost value to test
        output_dir: Directory to save results

    Returns:
        Dict with performance metrics
    """
    print(f"\n{'='*80}")
    print(f"Testing shadow_cost = {shadow_cost}")
    print(f"{'='*80}")

    # Load config
    import yaml

    with open(config_path) as f:
        config_dict = yaml.safe_load(f)

    # Update greedy_params with shadow_cost
    if 'greedy_params' not in config_dict:
        config_dict['greedy_params'] = {}

    config_dict['greedy_params']['shadow_cost'] = shadow_cost

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
    print(f"  Position range: {num_positions.min():.0f} - {num_positions.max():.0f}")

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
    output_file = output_dir / f"shadow_cost_{shadow_cost}.json"
    results = {
        'shadow_cost': shadow_cost,
        'sharpe': float(sharpe),
        'annual_return': float(annual_return),
        'annual_vol': float(annual_vol),
        'avg_positions': float(avg_positions),
        'min_positions': int(num_positions.min()),
        'max_positions': int(num_positions.max()),
        'annual_turnover': float(turnover),
        'start_date': str(positions_df.index[0].date()),
        'end_date': str(positions_df.index[-1].date()),
    }

    with open(output_file, 'w') as f:
        json.dump(results, f, indent=2)

    print(f"\nResults saved to: {output_file}")

    return results


def main():
    """Run shadow_cost calibration sweep."""
    print("="*80)
    print("MR GREEDY — SHADOW COST CALIBRATION")
    print("="*80)

    # Configuration
    # Using 15-instrument dataset for clean calibration (smoke-tested instruments)
    data_path = project_root / "data" / "example_crypto_perps_15x4yr.parquet"
    config_path = project_root / "config" / "research" / "crypto_perps_greedy_simple.yaml"
    output_dir = project_root / "out" / "calibration_shadow_cost_15instr"

    # Create output directory
    output_dir.mkdir(parents=True, exist_ok=True)

    # Shadow cost values to test
    # Carver's futures value: 250
    # Expected crypto range: 50-200 (higher correlation = less diversification benefit)
    shadow_costs = [50, 100, 150, 200, 300]

    print(f"\nDataset: {data_path}")
    print(f"Config: {config_path}")
    print(f"Output: {output_dir}")
    print(f"\nTesting shadow_cost values: {shadow_costs}")
    print(f"Expected optimal range: 100-200")

    # Run calibration sweep
    all_results = []

    for shadow_cost in shadow_costs:
        try:
            results = run_backtest_with_shadow_cost(
                data_path=str(data_path),
                config_path=str(config_path),
                shadow_cost=shadow_cost,
                output_dir=output_dir,
            )
            all_results.append(results)

        except Exception as e:
            print(f"\nERROR: Backtest failed for shadow_cost={shadow_cost}")
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
    summary_df = summary_df.sort_values('shadow_cost')

    print("\nResults by shadow_cost:")
    print(summary_df.to_string(index=False))

    # Find optimal shadow_cost
    # Target: maximize Sharpe while keeping turnover reasonable (10-15 rt/yr)
    print("\n" + "-"*80)
    print("ANALYSIS")
    print("-"*80)

    # Sharpe-optimal
    best_sharpe_idx = summary_df['sharpe'].idxmax()
    best_sharpe = summary_df.loc[best_sharpe_idx]

    print(f"\nBest Sharpe ratio: {best_sharpe['sharpe']:.3f}")
    print(f"  shadow_cost: {best_sharpe['shadow_cost']}")
    print(f"  Turnover: {best_sharpe['annual_turnover']:.1f} rt/yr")
    print(f"  Positions: {best_sharpe['avg_positions']:.1f}")

    # Turnover-constrained optimal (10-15 rt/yr)
    reasonable_turnover = summary_df[
        (summary_df['annual_turnover'] >= 8) &
        (summary_df['annual_turnover'] <= 16)
    ]

    if len(reasonable_turnover) > 0:
        best_constrained_idx = reasonable_turnover['sharpe'].idxmax()
        best_constrained = reasonable_turnover.loc[best_constrained_idx]

        print(f"\nBest Sharpe with reasonable turnover (8-16 rt/yr):")
        print(f"  shadow_cost: {best_constrained['shadow_cost']}")
        print(f"  Sharpe: {best_constrained['sharpe']:.3f}")
        print(f"  Turnover: {best_constrained['annual_turnover']:.1f} rt/yr")
        print(f"  Positions: {best_constrained['avg_positions']:.1f}")

        recommended_shadow_cost = best_constrained['shadow_cost']
    else:
        print(f"\nNo results in target turnover range (8-16 rt/yr)")
        recommended_shadow_cost = best_sharpe['shadow_cost']

    print(f"\n{'='*80}")
    print(f"RECOMMENDED SHADOW_COST: {recommended_shadow_cost}")
    print(f"{'='*80}")

    # Save summary
    summary_file = output_dir / "summary.csv"
    summary_df.to_csv(summary_file, index=False)
    print(f"\nSummary saved to: {summary_file}")

    # Save recommendation
    recommendation = {
        'recommended_shadow_cost': float(recommended_shadow_cost),
        'sharpe': float(summary_df.loc[summary_df['shadow_cost'] == recommended_shadow_cost, 'sharpe'].iloc[0]),
        'turnover': float(summary_df.loc[summary_df['shadow_cost'] == recommended_shadow_cost, 'annual_turnover'].iloc[0]),
        'avg_positions': float(summary_df.loc[summary_df['shadow_cost'] == recommended_shadow_cost, 'avg_positions'].iloc[0]),
        'calibration_date': datetime.now().isoformat(),
    }

    recommendation_file = output_dir / "recommendation.json"
    with open(recommendation_file, 'w') as f:
        json.dump(recommendation, f, indent=2)

    print(f"Recommendation saved to: {recommendation_file}")


if __name__ == "__main__":
    main()
