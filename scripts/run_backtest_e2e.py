#!/usr/bin/env python3
"""
End-to-end backtest runner for reproducible research

COMPOSABLE DESIGN: Each step requires explicit flag.

Usage:
    # Run backtest with existing dataset (most common)
    python scripts/run_backtest_e2e.py \
        --config config/crypto_perps_baseline_v1.yaml \
        --data data/example_crypto_perps_5yr.parquet

    # Build dataset then run backtest
    python scripts/run_backtest_e2e.py \
        --config config/crypto_perps_baseline_v1.yaml \
        --build-dataset \
        --start-date 2020-01-01 \
        --end-date 2024-12-31

    # Full workflow: download + build + backtest
    python scripts/run_backtest_e2e.py \
        --config config/crypto_perps_baseline_v1.yaml \
        --download-data \
        --build-dataset \
        --start-year 2020 \
        --end-year 2024 \
        --start-date 2020-01-01 \
        --end-date 2024-12-31
"""

import argparse
import subprocess
import sys
import os
from pathlib import Path
import yaml
import hashlib
import json


def load_config(config_path: str) -> dict:
    """Load and validate config"""
    with open(config_path) as f:
        config = yaml.safe_load(f)

    # Validate required sections
    required_sections = ['system', 'universe', 'rules', 'forecasts', 'sizing', 'costs']
    for section in required_sections:
        if section not in config:
            raise ValueError(f"Config missing required section: {section}")

    return config


def compute_config_hash(config: dict) -> str:
    """Compute deterministic hash of config"""
    config_str = json.dumps(config, sort_keys=True)
    return hashlib.sha256(config_str.encode()).hexdigest()[:8]


def download_data(instruments: list, start_year: int, end_year: int, data_dir: str):
    """Download Binance data (idempotent)"""
    print(f"\n=== Step 1: Downloading data for {len(instruments)} instruments ===")

    for instrument in instruments:
        # Strip _PERP suffix for download
        symbol = instrument.replace('_PERP', '')

        for year in range(start_year, end_year + 1):
            cmd = [
                'python', 'scripts/download_binance_data.py',
                '--symbols', symbol,
                '--year', str(year),
                '--data-dir', data_dir
            ]

            print(f"  Downloading {symbol} {year}...")
            result = subprocess.run(cmd, capture_output=True, text=True)

            if result.returncode != 0 and 'already exists' not in result.stderr:
                print(f"    WARNING: {result.stderr.strip()}")


def build_dataset(config: dict, start_date: str, end_date: str, output_path: str, data_dir: str):
    """Build dataset from raw data"""
    print(f"\n=== Step 2: Building dataset ===")

    instruments = config['universe']['layer_a_instruments']
    allow_jagged = config.get('system', {}).get('allow_jagged', False)

    cmd = [
        'python', 'scripts/build_example_dataset.py',
        '--source', 'real',
        '--data-dir', data_dir,
        '--start-date', start_date,
        '--end-date', end_date,
        '--output-path', output_path,
        '--instruments'] + instruments

    if allow_jagged:
        cmd.append('--allow-jagged')
        cmd.extend(['--min-coverage', '0.50'])
    else:
        cmd.extend(['--min-coverage', '0.95'])

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        print(f"ERROR: Dataset build failed:\n{result.stderr}")
        sys.exit(1)

    print(f"  Dataset created: {output_path}")


def run_backtest(config_path: str, data_path: str, output_dir: str):
    """Run backtest"""
    print(f"\n=== Step 3: Running backtest ===")

    cmd = [
        'python', 'systems/crypto_perps/system.py',
        '--config', config_path,
        '--data', data_path,
        '--outdir', output_dir
    ]

    # Set PYTHONPATH
    env = os.environ.copy()
    env['PYTHONPATH'] = '.'

    result = subprocess.run(cmd, env=env, capture_output=True, text=True)

    if result.returncode != 0:
        print(f"ERROR: Backtest failed:\n{result.stderr}")
        sys.exit(1)

    print(result.stdout)
    print(f"  Results saved to: {output_dir}")


def generate_report(output_dir: str):
    """Generate standard report"""
    print(f"\n=== Step 4: Generating report ===")

    # Read metadata
    metadata_path = Path(output_dir) / 'metadata.json'
    with open(metadata_path) as f:
        metadata = json.load(f)

    print("\n--- Backtest Summary ---")
    print(f"Config: {metadata.get('config_snapshot', {}).get('_config_path', 'N/A')}")
    print(f"Dataset: {metadata['dataset_path']}")
    print(f"Git commit: {metadata['git_commit']} ({metadata['git_status']})")
    print(f"Dataset fingerprint: {metadata['dataset_fingerprint']}")

    metrics = metadata['headline_metrics']
    print(f"\n--- Performance Metrics ---")
    print(f"  Sharpe Ratio: {metrics['sharpe']:.3f}")
    print(f"  Ann. Return:  {metrics['ann_return']:.2%}")
    print(f"  Ann. Vol:     {metrics['ann_vol']:.2%}")
    print(f"  Max Drawdown: {metrics['max_drawdown']:.2%}")
    print(f"  Gross Leverage: {metrics['gross_exposure']:.2f}x")
    print(f"  Turnover:     {metrics['turnover']:.2%}")

    print(f"\nFull results: {output_dir}")


def main():
    parser = argparse.ArgumentParser(
        description='Composable backtest runner - each step requires explicit flag'
    )

    # Required
    parser.add_argument('--config', required=True, help='Config file path')

    # Step selection (mutually exclusive with --data)
    parser.add_argument('--data', help='Use existing dataset (skip build), provide path')
    parser.add_argument('--build-dataset', action='store_true', help='Build dataset from raw data')
    parser.add_argument('--download-data', action='store_true', help='Download raw data before building')

    # Date range (for build/download)
    parser.add_argument('--start-date', help='Start date YYYY-MM-DD (for build)')
    parser.add_argument('--end-date', help='End date YYYY-MM-DD (for build)')
    parser.add_argument('--start-year', type=int, help='Start year (for download)')
    parser.add_argument('--end-year', type=int, help='End year (for download)')

    # Paths
    parser.add_argument('--data-dir', default=os.environ.get('DATA_ROOT', 'data/raw/binance'),
                        help='Raw data directory (default: $DATA_ROOT or data/raw/binance)')
    parser.add_argument('--output-name', help='Custom output directory name')

    args = parser.parse_args()

    # Validation
    if args.data and args.build_dataset:
        parser.error("Cannot use both --data (existing) and --build-dataset")

    if args.download_data and not args.build_dataset:
        parser.error("--download-data requires --build-dataset")

    if args.build_dataset:
        if not args.start_date or not args.end_date:
            parser.error("--build-dataset requires --start-date and --end-date")

    if args.download_data:
        if not args.start_year or not args.end_year:
            parser.error("--download-data requires --start-year and --end-year")

    # Load config
    config = load_config(args.config)
    config_hash = compute_config_hash(config)

    # Generate output path
    if args.output_name:
        output_dir = f"out/{args.output_name}"
    else:
        config_name = Path(args.config).stem
        output_dir = f"out/{config_name}_{config_hash}"

    # Step 1: Download data (if requested)
    if args.download_data:
        instruments = config['universe']['layer_a_instruments']  # NOTE: This is the candidate pool
        download_data(instruments, args.start_year, args.end_year, args.data_dir)

    # Step 2: Build dataset (if requested)
    if args.build_dataset:
        dataset_name = f"data/dataset_{Path(args.config).stem}_{config_hash}.parquet"
        build_dataset(config, args.start_date, args.end_date, dataset_name, args.data_dir)
    elif args.data:
        dataset_name = args.data
        print(f"=== Using existing dataset: {dataset_name} ===")
    else:
        parser.error("Must specify either --data or --build-dataset")

    # Step 3: Run backtest (always)
    run_backtest(args.config, dataset_name, output_dir)

    # Step 4: Generate report (always)
    generate_report(output_dir)

    print("\n=== Backtest complete! ===")
    print(f"Results: {output_dir}")


if __name__ == '__main__':
    main()
