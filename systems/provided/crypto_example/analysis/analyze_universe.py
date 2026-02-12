"""
Diagnostic script for analyzing the dynamic crypto universe.

Shows:
1. Universe size over time
2. Which instruments are eligible at different dates
3. Cost metrics for each instrument
4. Entry/exit patterns

Run from pysystemtrade root:
    python systems/provided/crypto_example/analyze_universe.py
"""

import os
import sys
import pandas as pd
import numpy as np
from datetime import datetime

# Add parent to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))))

from sysdata.crypto.spot_sim_data import csvSpotSimData
from sysdata.crypto.walk_forward_costs import (
    WalkForwardCostEstimator,
    calculate_stack_turnover,
    RULE_TURNOVER,
)
from sysdata.crypto.dynamic_universe import DynamicUniverseManager


def analyze_universe(
    data_path: str = "data/crypto",
    max_sr_per_trade: float = 0.01,
    max_sr_annual: float = 0.13,
    stack_turnover: float = 15.0,
    sample_dates: list = None,
):
    """
    Analyze the dynamic instrument universe.

    Args:
        data_path: Path to crypto CSV data
        max_sr_per_trade: Maximum SR cost per trade
        max_sr_annual: Maximum annual SR cost
        stack_turnover: Expected round-trips per year
        sample_dates: Specific dates to analyze (None for automatic)
    """
    print("=" * 70)
    print("Dynamic Instrument Universe Analysis")
    print("=" * 70)
    print()

    # Load data
    print(f"Loading data from {data_path}...")
    data = csvSpotSimData(data_path=data_path)
    all_instruments = data.get_instrument_list()
    print(f"Total instruments in data: {len(all_instruments)}")
    print()

    # Initialize cost estimator and universe manager
    cost_estimator = WalkForwardCostEstimator(
        prices_data=data._prices_data,
        adv_window=30,
        fee_bps=5,
    )

    universe_manager = DynamicUniverseManager(
        cost_estimator=cost_estimator,
        max_sr_cost_per_trade=max_sr_per_trade,
        max_sr_cost_annual=max_sr_annual,
        stack_turnover=stack_turnover,
    )

    # Load all price data
    print("Loading price data for all instruments...")
    price_data = {}
    for instr in all_instruments:
        prices = data._prices_data.get_spot_prices(instr)
        if len(prices) > 0:
            price_data[instr] = prices
    print(f"Loaded {len(price_data)} instruments with price data")
    print()

    # Get union of all dates
    all_dates = set()
    for prices in price_data.values():
        all_dates.update(prices.index)
    all_dates = pd.DatetimeIndex(sorted(all_dates))

    # Analyze at sample dates
    if sample_dates is None:
        # Use every 6 months + latest
        sample_dates = pd.date_range(
            start=all_dates[0],
            end=all_dates[-1],
            freq='6ME'
        ).tolist()
        if all_dates[-1] not in sample_dates:
            sample_dates.append(all_dates[-1])

    print("-" * 70)
    print("Universe Size Over Time")
    print("-" * 70)
    print()

    universe_sizes = []
    for date in sample_dates:
        eligible = universe_manager.get_eligible_instruments(
            date=pd.Timestamp(date),
            all_instruments=list(price_data.keys()),
            price_data=price_data,
        )
        universe_sizes.append((date, len(eligible)))
        print(f"{date.strftime('%Y-%m-%d')}: {len(eligible):3d} instruments eligible")

    print()

    # Analyze latest date in detail
    latest_date = pd.Timestamp(sample_dates[-1])
    print("-" * 70)
    print(f"Detailed Analysis at {latest_date.strftime('%Y-%m-%d')}")
    print("-" * 70)
    print()

    # Get eligible instruments
    eligible = universe_manager.get_eligible_instruments(
        date=latest_date,
        all_instruments=list(price_data.keys()),
        price_data=price_data,
    )

    # Calculate metrics for all instruments
    metrics = []
    for instr in price_data.keys():
        prices = price_data[instr]

        # Only analyze if we have data at latest date
        valid_prices = prices[prices.index <= latest_date]
        if len(valid_prices) < 15:
            continue

        # Get spread and cost
        spread_series = cost_estimator.get_spread_series(instr)
        if len(spread_series) == 0:
            continue

        spread_at_date = spread_series[spread_series.index <= latest_date]
        if len(spread_at_date) == 0:
            continue
        spread_bps = spread_at_date.iloc[-1]

        # Get ADV
        adv_series = cost_estimator.get_trailing_adv(instr)
        adv_at_date = adv_series[adv_series.index <= latest_date]
        if len(adv_at_date) == 0:
            continue
        adv_usd = adv_at_date.iloc[-1]

        # Calculate volatility
        returns = np.log(valid_prices / valid_prices.shift(1))
        daily_vol = returns.iloc[-35:].std()
        annual_vol = daily_vol * np.sqrt(252)

        if annual_vol <= 0 or np.isnan(annual_vol):
            continue

        # SR costs
        sr_per_trade = cost_estimator.get_sr_cost_per_trade(
            instr, latest_date, annual_vol
        )
        sr_annual = sr_per_trade * stack_turnover

        is_eligible = instr in eligible

        metrics.append({
            'instrument': instr,
            'adv_usd': adv_usd,
            'spread_bps': spread_bps,
            'annual_vol': annual_vol,
            'sr_per_trade': sr_per_trade,
            'sr_annual': sr_annual,
            'eligible': is_eligible,
            'history_days': len(valid_prices),
        })

    # Sort by ADV descending
    metrics_df = pd.DataFrame(metrics)
    metrics_df = metrics_df.sort_values('adv_usd', ascending=False)

    print("Top 30 Instruments by ADV$ (showing eligibility):")
    print()
    print(f"{'Instrument':<10} {'ADV$ (M)':<12} {'Spread':<8} {'Vol':<8} {'SR/Trade':<10} {'SR/Year':<10} {'Eligible':<8}")
    print("-" * 76)

    for _, row in metrics_df.head(30).iterrows():
        eligible_str = "YES" if row['eligible'] else "no"
        print(
            f"{row['instrument']:<10} "
            f"${row['adv_usd']/1e6:>9.2f}M "
            f"{row['spread_bps']:>6.1f}bp "
            f"{row['annual_vol']:>6.1%} "
            f"{row['sr_per_trade']:>8.4f} "
            f"{row['sr_annual']:>8.4f} "
            f"{eligible_str:<8}"
        )

    print()
    print("-" * 70)
    print("Summary Statistics")
    print("-" * 70)
    print()

    eligible_df = metrics_df[metrics_df['eligible']]
    excluded_df = metrics_df[~metrics_df['eligible']]

    print(f"Total instruments analyzed: {len(metrics_df)}")
    print(f"Eligible for trading:       {len(eligible_df)}")
    print(f"Excluded:                   {len(excluded_df)}")
    print()

    if len(eligible_df) > 0:
        print("Eligible Instruments Statistics:")
        print(f"  ADV$ range:     ${eligible_df['adv_usd'].min()/1e6:.2f}M - ${eligible_df['adv_usd'].max()/1e6:.2f}M")
        print(f"  Spread range:   {eligible_df['spread_bps'].min():.1f} - {eligible_df['spread_bps'].max():.1f} bps")
        print(f"  SR/trade range: {eligible_df['sr_per_trade'].min():.4f} - {eligible_df['sr_per_trade'].max():.4f}")
        print()

    if len(excluded_df) > 0:
        # Analyze why instruments are excluded
        high_cost = excluded_df[excluded_df['sr_per_trade'] > max_sr_per_trade]
        low_history = excluded_df[excluded_df['history_days'] < 15]

        print("Exclusion Reasons:")
        print(f"  High cost (SR > {max_sr_per_trade}): {len(high_cost)}")
        print(f"  Low history (<15 days):  {len(low_history)}")
        print()

    # List eligible instruments
    print("-" * 70)
    print("Eligible Instruments List")
    print("-" * 70)
    print()
    eligible_sorted = eligible_df.sort_values('adv_usd', ascending=False)
    instruments_str = ", ".join(eligible_sorted['instrument'].tolist())
    print(f"({len(eligible_df)} instruments):")
    print(instruments_str)
    print()

    # Calculate equal weights
    if len(eligible_df) > 0:
        weight = 1.0 / len(eligible_df)
        print(f"Equal weight per instrument: {weight:.4f} ({weight*100:.2f}%)")
        print()

    return metrics_df, eligible


def main():
    """Main entry point."""
    # Default parameters matching Carver's guidelines
    metrics_df, eligible = analyze_universe(
        data_path="data/crypto",
        max_sr_per_trade=0.01,  # 1% of SR per trade
        max_sr_annual=0.13,     # 13% of annual SR
        stack_turnover=15.0,    # ~15 round-trips for 15-rule stack
    )

    print("=" * 70)
    print("Analysis Complete")
    print("=" * 70)


if __name__ == "__main__":
    main()
