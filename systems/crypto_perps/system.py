#!/usr/bin/env python3
"""
Crypto Perpetual Futures Trading System - Phase 1 MVP

Main orchestrator that runs the daily trading loop.

Usage:
    python -m systems.crypto_perps.system --config CONFIG --data DATA --outdir OUT

Example:
    python -m systems.crypto_perps.system \\
        --config config/crypto_perps.yaml \\
        --data data/example_crypto_perps.parquet \\
        --outdir out/crypto_perps
"""

import argparse
import yaml
import logging
from pathlib import Path
import pandas as pd
import numpy as np

# Data loading
from sysdata.crypto.prices import load_crypto_perps_panel

# System modules
from systems.crypto_perps.universe import (
    get_layer_a_instruments,
    build_eligibility_history
)
from systems.crypto_perps.rules.ewmac import ewmac_forecasts
from systems.crypto_perps.rules.carry_funding import funding_carry_forecasts
from systems.crypto_perps.forecasts import process_all_forecasts
from systems.crypto_perps.sizing import calculate_target_weights, calculate_daily_volatility
from systems.crypto_perps.constraints import apply_portfolio_constraints
from systems.crypto_perps.execution import execute_trades
from systems.crypto_perps.accounting import calculate_cumulative_pnl

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def load_config(config_path: str) -> dict:
    """Load configuration from YAML file"""
    with open(config_path, 'r') as f:
        config = yaml.safe_load(f)
    return config


def run_backtest(config: dict, data_path: str, output_dir: str):
    """
    Run complete backtest

    Args:
        config: Configuration dict from YAML
        data_path: Path to data parquet file
        output_dir: Output directory for results
    """
    logger.info("=" * 80)
    logger.info("Crypto Perpetual Futures Trading System - Phase 1")
    logger.info("=" * 80)

    # Create output directory
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    # Extract config parameters
    capital = config['system']['capital']
    vol_target = config['system']['vol_target_ann']
    gross_lev_cap = config['system']['gross_leverage_cap']
    idm_cap = config['system']['idm_cap']
    min_pos_frac = config['system']['min_position_frac']
    min_adv = config['universe']['daily_min_adv_notional']
    ewmac_pairs = config['rules']['ewmac_pairs']
    carry_fast_hl = config['rules']['carry_fast_halflife']
    carry_slow_hl = config['rules']['carry_slow_halflife']
    buffer_frac = config['execution']['buffer_frac']
    corr_span = config['constraints']['correlation_span']
    corr_min_periods = config['constraints']['correlation_min_periods']

    logger.info(f"Starting capital: ${capital:,.2f}")
    logger.info(f"Vol target: {vol_target:.1%}")
    logger.info(f"Gross leverage cap: {gross_lev_cap}")
    logger.info(f"IDM cap: {idm_cap}")

    # Step 1: Load data
    logger.info("\nStep 1: Loading data...")
    prices_df, meta_df = load_crypto_perps_panel(data_path)
    logger.info(f"  Loaded data: {len(prices_df)} days, {len(prices_df.columns)} instruments")
    logger.info(f"  Date range: {prices_df.index[0].date()} to {prices_df.index[-1].date()}")

    # Step 2: Get universe and eligibility
    logger.info("\nStep 2: Building universe and eligibility...")
    layer_a = get_layer_a_instruments()
    logger.info(f"  Layer A instruments: {layer_a}")

    eligibility_df = build_eligibility_history(prices_df, meta_df, min_adv)
    eligible_pct = eligibility_df.mean() * 100
    for inst in layer_a:
        logger.info(f"  {inst}: {eligible_pct[inst]:.1f}% eligible days")

    # Step 3: Calculate forecasts
    logger.info("\nStep 3: Calculating forecasts...")
    logger.info(f"  EWMAC pairs: {ewmac_pairs}")
    ewmac = ewmac_forecasts(prices_df, ewmac_pairs)

    logger.info(f"  Carry: fast_hl={carry_fast_hl}, slow_hl={carry_slow_hl}")
    carry = funding_carry_forecasts(meta_df, carry_fast_hl, carry_slow_hl)

    logger.info("  Scaling and combining forecasts...")
    combined_forecasts = process_all_forecasts(ewmac, carry)

    # Validate forecast caps
    for inst, forecast in combined_forecasts.items():
        max_abs = forecast.abs().max()
        logger.info(f"  {inst}: max |forecast| = {max_abs:.2f}")

    # Step 4: Size positions
    logger.info("\nStep 4: Sizing positions...")
    weights_df, notionals_df = calculate_target_weights(
        forecasts=combined_forecasts,
        prices_df=prices_df,
        capital=capital,
        vol_target_ann=vol_target,
        min_position_frac=min_pos_frac
    )

    # Calculate volatilities (needed for execution buffers)
    daily_vols_df = pd.DataFrame(
        {inst: calculate_daily_volatility(prices_df[inst])
         for inst in prices_df.columns}
    )

    # Step 5: Apply portfolio constraints
    logger.info("\nStep 5: Applying portfolio constraints...")
    constrained_weights_df, gross_lev_series, idm_series = apply_portfolio_constraints(
        weights_df=weights_df,
        prices_df=prices_df,
        gross_leverage_cap=gross_lev_cap,
        idm_cap=idm_cap,
        corr_span=corr_span,
        corr_min_periods=corr_min_periods
    )

    logger.info(f"  Gross leverage: mean={gross_lev_series.mean():.2f}, max={gross_lev_series.max():.2f}")
    logger.info(f"  IDM: mean={idm_series.mean():.2f}, max={idm_series.max():.2f}")

    # Convert weights to notionals
    constrained_notionals_df = constrained_weights_df * capital

    # Step 6: Execute trades with buffers
    logger.info("\nStep 6: Executing trades...")

    # Initialize positions (start with zero)
    current_positions = pd.DataFrame(0.0, index=prices_df.index, columns=prices_df.columns)

    trades_df, costs_df, srcosts_df = execute_trades(
        target_weights_df=constrained_weights_df,
        current_weights_df=current_positions / capital,  # Convert to weights
        prices_df=prices_df,
        meta_df=meta_df,
        eligibility_df=eligibility_df,
        daily_vols_df=daily_vols_df,
        capital=capital,
        buffer_frac=buffer_frac
    )

    # Update positions based on trades
    for i, date in enumerate(prices_df.index):
        if i == 0:
            # First day: execute trades from zero
            current_positions.loc[date] = trades_df.loc[date] * capital
        else:
            # Subsequent days: previous position + trades
            current_positions.loc[date] = (
                current_positions.iloc[i-1] + trades_df.loc[date] * capital
            )

    total_costs = costs_df.sum().sum()
    logger.info(f"  Total trading costs: ${total_costs:.2f}")

    # Step 7: Calculate PnL and equity curve
    logger.info("\nStep 7: Calculating PnL and equity curve...")
    price_pnl_df, funding_pnl_df, total_pnl_df, equity_curve = calculate_cumulative_pnl(
        positions_df=current_positions,
        prices_df=prices_df,
        meta_df=meta_df,
        costs_df=costs_df,
        initial_capital=capital
    )

    final_equity = equity_curve.iloc[-1]
    total_return = (final_equity - capital) / capital
    total_pnl = final_equity - capital

    logger.info(f"  Starting equity: ${capital:,.2f}")
    logger.info(f"  Final equity: ${final_equity:,.2f}")
    logger.info(f"  Total return: {total_return:+.2%}")
    logger.info(f"  Total PnL: ${total_pnl:+,.2f}")

    # Step 8: Write outputs
    logger.info("\nStep 8: Writing outputs...")

    # Equity curve
    equity_file = output_path / config['output']['equity_curve_file']
    equity_curve.to_csv(equity_file, header=['equity'])
    logger.info(f"  Saved equity curve: {equity_file}")

    # Positions
    positions_file = output_path / config['output']['positions_file']
    current_positions.to_csv(positions_file)
    logger.info(f"  Saved positions: {positions_file}")

    # PnL breakdown (optional detailed output)
    pnl_breakdown_file = output_path / config['output']['pnl_breakdown_file']
    pnl_breakdown = pd.DataFrame({
        'total_pnl': total_pnl_df.sum(axis=1),
        'price_pnl': price_pnl_df.sum(axis=1),
        'funding_pnl': funding_pnl_df.sum(axis=1),
        'costs': costs_df.sum(axis=1),
        'equity': equity_curve
    })
    pnl_breakdown.to_csv(pnl_breakdown_file)
    logger.info(f"  Saved PnL breakdown: {pnl_breakdown_file}")

    logger.info("\n" + "=" * 80)
    logger.info("Backtest complete!")
    logger.info("=" * 80)


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description='Crypto Perpetual Futures Trading System - Phase 1'
    )
    parser.add_argument(
        '--config',
        required=True,
        help='Path to config YAML file (e.g., config/crypto_perps.yaml)'
    )
    parser.add_argument(
        '--data',
        required=True,
        help='Path to data parquet file (e.g., data/example_crypto_perps.parquet)'
    )
    parser.add_argument(
        '--outdir',
        required=True,
        help='Output directory for results (e.g., out/crypto_perps)'
    )

    args = parser.parse_args()

    # Load config
    config = load_config(args.config)

    # Run backtest
    run_backtest(config, args.data, args.outdir)


if __name__ == '__main__':
    main()
