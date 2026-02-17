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


def _write_universe_snapshot(system, output_path: Path, config_path: str) -> None:
    """
    Extract Stage 2 tradable set from portfolio stage and write universe_snapshot.json.

    The snapshot records the last date's tradable instruments, entry/exit transitions
    vs any previous snapshot, and the selector parameters used.

    Args:
        system: Backtest System object (portfolio stage must have _tradable_over_time)
        output_path: Directory where universe_snapshot.json will be written
        config_path: Path to config YAML (for reading dynamic_universe parameters)
    """
    import yaml

    portfolio_stage = system.portfolio
    tradable_over_time = getattr(portfolio_stage, '_tradable_over_time', None)

    if not tradable_over_time:
        logger.warning(
            "_tradable_over_time not found on portfolio stage — "
            "Stage 2 may not be wired in or top_k not configured. Skipping snapshot."
        )
        return

    last_date = max(tradable_over_time.keys())
    new_tradable = sorted(tradable_over_time[last_date])

    # Load dynamic_universe config parameters
    with open(config_path) as f:
        raw_config = yaml.safe_load(f)
    du_config = raw_config.get('dynamic_universe', {})
    K = du_config.get('top_k', 30)
    entry_buffer = du_config.get('entry_buffer', 5)
    exit_buffer = du_config.get('exit_buffer', 10)

    # Compute entrants/exits relative to any previous snapshot in the same output dir
    prev_snapshot_path = output_path / 'universe_snapshot.json'
    prev_tradable = None
    if prev_snapshot_path.exists():
        try:
            with open(prev_snapshot_path) as f:
                prev_snap = json.load(f)
            prev_tradable = set(prev_snap.get('tradable_instruments', []))
        except (json.JSONDecodeError, IOError):
            pass

    if prev_tradable is not None:
        entrants = sorted(set(new_tradable) - prev_tradable)
        exits = sorted(prev_tradable - set(new_tradable))
    else:
        entrants = []
        exits = []

    snapshot = {
        'as_of_date': last_date.strftime('%Y-%m-%d'),
        'tradable_instruments': new_tradable,
        'count': len(new_tradable),
        'K': K,
        'entry_threshold': K - entry_buffer,
        'exit_threshold': K + exit_buffer,
        'pinned_instruments': raw_config.get('pinned_instruments', []),
        'entrants': entrants,
        'exits': exits,
    }

    snapshot_path = output_path / 'universe_snapshot.json'
    with open(snapshot_path, 'w') as f:
        json.dump(snapshot, f, indent=2)

    logger.info(
        f"  ✓ {snapshot_path} "
        f"({len(new_tradable)} instruments, {len(entrants)} entrants, {len(exits)} exits)"
    )


def _compute_performance_metrics(
    system,
    portfolio_positions: pd.DataFrame,
    weights: pd.DataFrame,
    output_path: Path,
    precomputed_returns: pd.Series = None,
) -> None:
    """
    Compute and write performance_summary.json using existing accounting/metrics modules.
    Called after positions are computed and written.
    """
    from systems.provided.crypto_example.core.portfolio_metrics import (
        calculate_all_metrics,
        format_metrics_table,
    )

    logger.info("\nComputing performance metrics...")

    # 1. Get returns — use pre-computed if available, else try Account stage
    daily_returns_dec = precomputed_returns
    if daily_returns_dec is None:
        try:
            account = system.accounts.portfolio()
            pct_obj = account.percent
            pct_series = pd.Series(
                np.asarray(pct_obj, dtype=float), index=pct_obj.index
            )
            daily_returns_dec = pct_series.dropna() / 100.0
            logger.info(f"  Account stage: {len(daily_returns_dec)} days of returns")
        except Exception as e:
            logger.warning(f"  Account stage failed ({e}) — P&L metrics will be omitted")

    # 2. Portfolio-specific metrics from positions/weights
    universe_size = (weights > 0).sum(axis=1)
    avg_active = float(universe_size.mean())

    # Annual turnover: total abs position changes / (2 × avg exposure) / n_years
    total_exposure = portfolio_positions.abs().sum(axis=1)
    avg_exposure = float(total_exposure.mean())
    daily_delta = portfolio_positions.diff().abs().sum(axis=1)
    n_years = len(portfolio_positions) / 252.0
    if avg_exposure > 0 and n_years > 0:
        annual_turnover = float(daily_delta.sum() / (2.0 * avg_exposure) / n_years)
    else:
        annual_turnover = float('nan')

    start_date = portfolio_positions.index[0].strftime('%Y-%m-%d')
    end_date = portfolio_positions.index[-1].strftime('%Y-%m-%d')
    n_instruments = len(portfolio_positions.columns)
    n_days = len(portfolio_positions)

    # 3. Compute and display metrics
    metrics = {}
    if daily_returns_dec is not None and len(daily_returns_dec) >= 20:
        metrics = calculate_all_metrics(daily_returns_dec, name="Dynamic Universe")
        table = format_metrics_table([metrics])
        print("\nPERFORMANCE SUMMARY")
        print("=" * 60)
        print(table)
    else:
        logger.warning("  No returns available — performance metrics not computed")

    # 4. Write performance_summary.json
    def _to_python(v):
        """Convert numpy scalars to Python native for JSON."""
        if isinstance(v, (np.integer,)):
            return int(v)
        if isinstance(v, (np.floating,)):
            return float(v)
        if isinstance(v, (np.bool_,)):
            return bool(v)
        return v

    summary = {
        "metrics": {k: _to_python(v) for k, v in metrics.items() if k != 'name'},
        "portfolio": {
            "avg_active_positions": avg_active,
            "annual_turnover": annual_turnover,
            "start_date": start_date,
            "end_date": end_date,
            "n_instruments": n_instruments,
            "n_days": n_days,
        },
    }

    perf_path = output_path / 'performance_summary.json'
    with open(perf_path, 'w') as f:
        json.dump(summary, f, indent=2, default=str)
    logger.info(f"  ✓ {perf_path}")


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

    # Trigger Account stage portfolio calculation before the positions loop.
    # accountCurveGroup.percent returns an accountCurveGroup (not a plain pd.Series).
    # Calling .dropna() on it triggers pandas boolean-indexing via __getitem__,
    # which expects a string instrument key and raises TypeError on numpy arrays.
    # Fix: wrap in a plain pd.Series before calling dropna().
    logger.info("\nPre-computing Account stage portfolio returns...")
    _account_returns = None
    try:
        _acc = system.accounts.portfolio()
        _pct_obj = _acc.percent
        _pct_series = pd.Series(
            np.asarray(_pct_obj, dtype=float), index=_pct_obj.index
        )
        _account_returns = (_pct_series.dropna() / 100.0)
        logger.info(f"  Account stage: {len(_account_returns)} days of returns cached")
    except Exception as e:
        logger.warning(f"  Account stage pre-compute failed ({e}) — will retry after positions")

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

    # Write universe snapshot from Stage 2 selector output
    if use_dynamic_universe:
        _write_universe_snapshot(
            system=system,
            output_path=output_path,
            config_path=config_path,
        )

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

    # 4. Performance metrics
    _compute_performance_metrics(
        system=system,
        portfolio_positions=portfolio_positions,
        weights=weights,
        output_path=output_path,
        precomputed_returns=_account_returns,
    )

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
