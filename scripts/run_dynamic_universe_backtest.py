#!/usr/bin/env python3
"""
Run backtest with dynamic universe using parquet-backed data adapter.

This script runs a pysystemtrade backtest with dynamic instrument universe,
outputting results compatible with the live advisory workflow.

Usage:
    python scripts/run_dynamic_universe_backtest.py \
        --config config/crypto_perps_dynamic_universe_v1.yaml \
        --data data/dataset_latest.parquet \
        --outdir out/backtest_latest

Outputs (compatible with generate_trade_plan.py):
    - positions.csv: Daily positions for all instruments
    - diagnostics.parquet: Full system diagnostics
    - metadata.json: Run metadata (config, dates, instruments)
"""

import argparse
import sys
import json
import logging
from pathlib import Path
from datetime import datetime

import pandas as pd
import numpy as np

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sysdata.config.configdata import Config
from sysdata.crypto.parquet_perps_sim_data import parquetCryptoPerpsSimData
from systems.provided.crypto_example.core.dynamic_portfolio import CryptoDynamicPortfolio
from systems.basesystem import System
from systems.forecasting import Rules
from systems.forecast_combine import ForecastCombine
from systems.forecast_scale_cap import ForecastScaleCap
from systems.rawdata import RawData
from systems.positionsizing import PositionSizing
from systems.accounts.accounts_stage import Account

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def run_backtest(config_path: str, data_path: str, output_dir: str, use_dynamic_universe: bool = True):
    """
    Run pysystemtrade backtest with parquet-backed data adapter.

    Args:
        config_path: Path to YAML config file
        data_path: Path to parquet dataset
        output_dir: Output directory for results
        use_dynamic_universe: If True, use dynamic universe with cost filtering
    """
    logger.info("="*80)
    logger.info("DYNAMIC UNIVERSE BACKTEST")
    logger.info("="*80)

    # Create output directory
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    # Load config
    logger.info(f"Loading config: {config_path}")
    # Config class expects either a package path or a dict, not a file path string
    # Load YAML and pass as dict
    import yaml
    with open(config_path) as f:
        config_dict = yaml.safe_load(f)
    config = Config(config_dict)

    # Extract dynamic universe config if enabled
    dynamic_universe_config = None
    if use_dynamic_universe:
        dynamic_universe_config = {
            'max_sr_cost_per_trade': config.get_element_or_default(
                'dynamic_universe.max_sr_cost_per_trade', 0.01
            ),
            'max_sr_cost_annual': config.get_element_or_default(
                'dynamic_universe.max_sr_cost_annual', 0.13
            ),
            'stack_turnover': config.get_element_or_default(
                'dynamic_universe.stack_turnover', 15.0
            ),
            'adv_window': config.get_element_or_default(
                'dynamic_universe.adv_window', 30
            ),
            'fee_bps': config.get_element_or_default(
                'dynamic_universe.fee_bps', 5
            ),
        }
        logger.info(f"Dynamic universe enabled:")
        logger.info(f"  Max SR cost per trade: {dynamic_universe_config['max_sr_cost_per_trade']}")
        logger.info(f"  Max SR cost annual: {dynamic_universe_config['max_sr_cost_annual']}")
        logger.info(f"  Stack turnover: {dynamic_universe_config['stack_turnover']}")

    # Load data
    logger.info(f"Loading dataset: {data_path}")

    # Pass config_path and env_root for registry-aware candidate extraction
    # Determine env_root (use environment variable or current directory)
    import os
    env_root_str = os.environ.get('LIVE_OPS_ENV_ROOT')
    env_root = Path(env_root_str) if env_root_str else Path.cwd()

    data = parquetCryptoPerpsSimData(
        dataset_path=data_path,
        config_path=config_path,
        env_root=env_root,
        use_dynamic_universe=use_dynamic_universe,
        dynamic_universe_config=dynamic_universe_config,
    )

    instruments = data.get_instrument_list()
    logger.info(f"  Loaded {len(instruments)} instruments")
    logger.info(f"  Sample: {instruments[:5]}")

    # Create system with dynamic portfolio
    logger.info("Creating system...")
    if use_dynamic_universe:
        portfolio_stage = CryptoDynamicPortfolio()
    else:
        from systems.portfolio import Portfolios
        portfolio_stage = Portfolios()

    system = System(
        stage_list=[
            Account(),
            portfolio_stage,
            PositionSizing(),
            ForecastCombine(),
            ForecastScaleCap(),
            Rules(),
            RawData(),
        ],
        data=data,
        config=config,
    )

    logger.info("✓ System created")

    # Run backtest by getting portfolio positions for all instruments
    logger.info("\nRunning backtest...")
    logger.info("  Getting portfolio positions...")

    # Get positions for all instruments
    position_dict = {}
    for instrument in instruments:
        try:
            position = system.portfolio.get_notional_position(instrument)
            position_dict[instrument] = position
        except Exception as e:
            logger.warning(f"Could not get position for {instrument}: {e}")
            continue

    portfolio_positions = pd.DataFrame(position_dict)

    logger.info(f"  Backtest completed:")
    logger.info(f"    Date range: {portfolio_positions.index[0].date()} to {portfolio_positions.index[-1].date()}")
    logger.info(f"    N days: {len(portfolio_positions)}")
    logger.info(f"    Instruments: {len(portfolio_positions.columns)}")

    # Get instrument weights for diagnostic
    weights = system.portfolio.get_instrument_weights()
    universe_size = (weights > 0).sum(axis=1)
    logger.info(f"    Universe size: min={universe_size.min():.0f}, max={universe_size.max():.0f}, avg={universe_size.mean():.1f}")

    # Write outputs
    logger.info("\nWriting outputs...")

    # 1. positions.csv (compatible with generate_trade_plan.py)
    positions_path = output_path / 'positions.csv'
    portfolio_positions.to_csv(positions_path)
    logger.info(f"  ✓ {positions_path}")

    # 2. diagnostics.parquet (full system state)
    diagnostics_path = output_path / 'diagnostics.parquet'
    try:
        # Collect key diagnostic series
        diagnostics_data = []

        for instrument in portfolio_positions.columns:
            # Positions
            position = portfolio_positions[instrument]

            # Get forecasts if available
            try:
                combined_forecast = system.combForecast.get_combined_forecast(instrument)
            except:
                combined_forecast = pd.Series(np.nan, index=portfolio_positions.index)

            # Get instrument weight
            try:
                instrument_weight = weights[instrument]
            except:
                instrument_weight = pd.Series(np.nan, index=portfolio_positions.index)

            # Build diagnostic row
            diag = pd.DataFrame({
                'date': portfolio_positions.index,
                'instrument': instrument,
                'position': position.values,
                'combined_forecast': combined_forecast.reindex(portfolio_positions.index).values,
                'instrument_weight': instrument_weight.reindex(portfolio_positions.index).values,
            })

            diagnostics_data.append(diag)

        diagnostics_df = pd.concat(diagnostics_data, ignore_index=True)
        diagnostics_df.to_parquet(diagnostics_path)
        logger.info(f"  ✓ {diagnostics_path}")

    except Exception as e:
        logger.warning(f"  Could not write diagnostics: {e}")

    # 3. metadata.json (run provenance)
    metadata = {
        'run_timestamp': datetime.utcnow().isoformat(),
        'system_type': 'dynamic_universe' if use_dynamic_universe else 'static_universe',
        'config_path': str(config_path),
        'data_path': str(data_path),
        'backtest_start_date': portfolio_positions.index[0].strftime('%Y-%m-%d'),
        'backtest_end_date': portfolio_positions.index[-1].strftime('%Y-%m-%d'),
        'n_days': len(portfolio_positions),
        'instruments': list(portfolio_positions.columns),
        'n_instruments': len(portfolio_positions.columns),
        'dynamic_universe_stats': {
            'min_active': int(universe_size.min()),
            'max_active': int(universe_size.max()),
            'avg_active': float(universe_size.mean()),
            'median_active': float(universe_size.median()),
        } if use_dynamic_universe else None,
        'dynamic_universe_config': dynamic_universe_config,
    }

    metadata_path = output_path / 'metadata.json'
    with open(metadata_path, 'w') as f:
        json.dump(metadata, f, indent=2)
    logger.info(f"  ✓ {metadata_path}")

    logger.info("\n" + "="*80)
    logger.info("✓ BACKTEST COMPLETED SUCCESSFULLY")
    logger.info("="*80)

    return True


def main():
    parser = argparse.ArgumentParser(
        description='Run backtest with dynamic universe',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument(
        '--config',
        type=Path,
        required=True,
        help='Path to config YAML file'
    )
    parser.add_argument(
        '--data',
        type=Path,
        required=True,
        help='Path to parquet dataset'
    )
    parser.add_argument(
        '--outdir',
        type=Path,
        required=True,
        help='Output directory for results'
    )
    parser.add_argument(
        '--static-universe',
        action='store_true',
        help='Use static universe (disable dynamic cost filtering)'
    )

    args = parser.parse_args()

    # Validate inputs
    if not args.config.exists():
        logger.error(f"Config file not found: {args.config}")
        sys.exit(1)

    if not args.data.exists():
        logger.error(f"Data file not found: {args.data}")
        sys.exit(1)

    try:
        use_dynamic = not args.static_universe
        success = run_backtest(
            config_path=str(args.config),
            data_path=str(args.data),
            output_dir=str(args.outdir),
            use_dynamic_universe=use_dynamic
        )

        sys.exit(0 if success else 1)

    except Exception as e:
        logger.error(f"Backtest failed: {e}", exc_info=True)
        sys.exit(1)


if __name__ == '__main__':
    main()
