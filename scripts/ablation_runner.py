#!/usr/bin/env python
"""
Ablation study runner for crypto perpetual futures trading system

Runs grid experiments with different feature combinations to measure impact
of Phase 2 features (monthly reviews, state machine, relative momentum).

Usage:
    python scripts/ablation_runner.py \\
        --base-config config/crypto_perps.yaml \\
        --data data/example_crypto_perps.parquet \\
        --outdir out/ablation_study_20260125 \\
        --start-date 2023-01-01 \\
        --end-date 2023-12-31 \\
        --tag "monthly_review_sensitivity"
"""

import argparse
import yaml
import pandas as pd
from pathlib import Path
import sys

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from systems.crypto_perps.system import run_backtest
from systems.crypto_perps.metrics import calculate_metrics


def define_experiments():
    """
    Define config overrides for each experiment

    Returns:
        Dict mapping experiment name -> config overrides
    """
    return {
        'baseline': {
            'universe.review_freq': None,
            'universe.forced_exit_days': None,
            'forecasts.use_relative_momentum': False,
            'diagnostics.enabled': True
        },
        'reviews': {
            'universe.review_freq': 'BMS',
            'universe.forced_exit_days': None,
            'forecasts.use_relative_momentum': False,
            'diagnostics.enabled': True
        },
        'state_machine': {
            'universe.review_freq': 'BMS',
            'universe.forced_exit_days': 5,
            'forecasts.use_relative_momentum': False,
            'diagnostics.enabled': True
        },
        'relmom': {
            'universe.review_freq': 'BMS',
            'universe.forced_exit_days': 5,
            'forecasts.use_relative_momentum': True,
            'diagnostics.enabled': True
        }
    }


def apply_config_overrides(config, overrides):
    """
    Apply nested overrides to config dict

    Args:
        config: Config dict to modify
        overrides: Dict of key.path -> value overrides

    Example:
        apply_config_overrides(config, {'universe.review_freq': 'BMS'})
        Sets config['universe']['review_freq'] = 'BMS'
    """
    for key, value in overrides.items():
        keys = key.split('.')
        d = config
        for k in keys[:-1]:
            d = d.setdefault(k, {})
        if value is None:
            # Remove key if value is None
            d.pop(keys[-1], None)
        else:
            d[keys[-1]] = value


def run_ablation_study(base_config_path, data_path, outdir, start_date, end_date, tag):
    """
    Run ablation study and produce results table

    Args:
        base_config_path: Path to base config YAML
        data_path: Path to data parquet
        outdir: Output directory for results
        start_date: Backtest start date (YYYY-MM-DD)
        end_date: Backtest end date (YYYY-MM-DD)
        tag: Optional tag for experiment tracking

    Outputs:
        - ablation_results.csv: Tidy results table (1 row per experiment)
        - {experiment}/diagnostics.parquet: Per-experiment diagnostics
        - {experiment}/config.yaml: Per-experiment config snapshot
    """
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    # Load base config
    with open(base_config_path) as f:
        base_config = yaml.safe_load(f)

    experiments = define_experiments()
    results = []

    for name, overrides in experiments.items():
        print(f"\n{'='*60}")
        print(f"Running experiment: {name}")
        print(f"{'='*60}")

        # Apply overrides to fresh copy of config
        config = yaml.safe_load(yaml.dump(base_config))  # Deep copy
        apply_config_overrides(config, overrides)

        # Override date range if specified
        if start_date or end_date:
            config['backtest'] = config.get('backtest', {})
            if start_date:
                config['backtest']['start_date'] = start_date
            if end_date:
                config['backtest']['end_date'] = end_date

        # Create experiment output directory
        exp_outdir = outdir / name
        exp_outdir.mkdir(exist_ok=True)

        # Write experiment config snapshot
        with open(exp_outdir / 'config.yaml', 'w') as f:
            yaml.dump(config, f, default_flow_style=False, sort_keys=False)

        # Run backtest (returns dict of objects)
        print(f"Running backtest for {name}...")
        try:
            backtest_outputs = run_backtest(config, str(data_path), str(exp_outdir))
        except Exception as e:
            print(f"ERROR: Experiment {name} failed: {e}")
            import traceback
            traceback.print_exc()
            continue

        # Calculate overall constraint scalar from diagnostics if available
        constraint_scalars = None
        diagnostics_file = exp_outdir / 'diagnostics.parquet'
        if diagnostics_file.exists():
            df_diag = pd.read_parquet(diagnostics_file)
            # Extract unique date -> overall_scalar mapping
            constraint_scalars = df_diag.groupby('date')['overall_scalar'].first()

        # Calculate metrics from returned objects (not re-reading files)
        print(f"Calculating metrics for {name}...")
        metrics = calculate_metrics(
            equity_curve=backtest_outputs['equity_curve'],
            weights_df=backtest_outputs['weights_df'],
            trades_df=backtest_outputs['trades_df'],
            capital=config['system']['capital'],
            state_df=backtest_outputs.get('state_df'),
            constraint_scalars=constraint_scalars
        )

        # Store results
        results.append({
            'experiment': name,
            'tag': tag,
            'start_date': start_date,
            'end_date': end_date,
            **metrics
        })

        print(f"\nResults for {name}:")
        print(f"  Sharpe: {metrics['sharpe']:.2f}")
        print(f"  Ann Return: {metrics['ann_return']:.2%}")
        print(f"  Ann Vol: {metrics['ann_vol']:.2%}")
        print(f"  Max Drawdown: {metrics['max_drawdown']:.2%}")
        print(f"  Gross Exposure: {metrics['gross_exposure']:.2f}")
        print(f"  Turnover: {metrics['turnover']:.3f}")
        print(f"  Days Constrained: {metrics['days_constrained']}")
        print(f"  Exit Flattens: {metrics['exit_flattens']}")
        print(f"  Exit Decays: {metrics['exit_decays']}")

    # Write results table
    if results:
        results_df = pd.DataFrame(results)
        results_df.to_csv(outdir / 'ablation_results.csv', index=False)

        print(f"\n{'='*60}")
        print(f"Ablation study complete. Summary:")
        print(f"{'='*60}")
        print(results_df[['experiment', 'sharpe', 'ann_return', 'max_drawdown', 'turnover']].to_string(index=False))
        print(f"\nFull results written to: {outdir / 'ablation_results.csv'}")
    else:
        print("\nERROR: No experiments completed successfully")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description='Run ablation study for crypto perpetual futures system'
    )
    parser.add_argument(
        '--base-config',
        required=True,
        help='Path to base config YAML (e.g., config/crypto_perps.yaml)'
    )
    parser.add_argument(
        '--data',
        required=True,
        help='Path to data parquet file (e.g., data/example_crypto_perps.parquet)'
    )
    parser.add_argument(
        '--outdir',
        required=True,
        help='Output directory for ablation study (e.g., out/ablation_20260125)'
    )
    parser.add_argument(
        '--start-date',
        default=None,
        help='Backtest start date YYYY-MM-DD (optional, uses data start if not specified)'
    )
    parser.add_argument(
        '--end-date',
        default=None,
        help='Backtest end date YYYY-MM-DD (optional, uses data end if not specified)'
    )
    parser.add_argument(
        '--tag',
        default='',
        help='Optional tag for experiment tracking (e.g., "monthly_review_test")'
    )

    args = parser.parse_args()

    run_ablation_study(
        base_config_path=args.base_config,
        data_path=args.data,
        outdir=args.outdir,
        start_date=args.start_date,
        end_date=args.end_date,
        tag=args.tag
    )


if __name__ == '__main__':
    main()
