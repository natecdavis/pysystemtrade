"""Test that backtests are deterministic using canonical comparison"""

import pytest
import subprocess
import hashlib
import json
import sys
import os
import pandas as pd
from pathlib import Path
import shutil


def canonicalize_csv(filepath: str) -> str:
    """Load CSV, sort, format floats consistently, return hash"""
    df = pd.read_csv(filepath)

    # Sort by all columns for deterministic ordering
    df = df.sort_values(by=list(df.columns)).reset_index(drop=True)

    # Format floats to 8 decimals to avoid precision differences
    for col in df.select_dtypes(include=['float64', 'float32']).columns:
        df[col] = df[col].round(8)

    # Convert to string with consistent formatting
    csv_str = df.to_csv(index=False, float_format='%.8f')

    return hashlib.sha256(csv_str.encode()).hexdigest()


def compare_headline_metrics(metadata1_path: str, metadata2_path: str) -> bool:
    """Compare only headline_metrics from metadata, ignore timestamps"""
    with open(metadata1_path) as f:
        meta1 = json.load(f)
    with open(metadata2_path) as f:
        meta2 = json.load(f)

    # Compare only headline metrics (rounded to avoid float precision issues)
    metrics1 = meta1['headline_metrics']
    metrics2 = meta2['headline_metrics']

    for key in metrics1:
        val1 = round(metrics1[key], 8)
        val2 = round(metrics2[key], 8)
        if val1 != val2:
            print(f"  Metric {key} differs: {val1} vs {val2}")
            return False

    return True


def test_deterministic_backtest_phase1():
    """Test that Phase 1 backtest produces identical results on repeat runs"""

    config = 'config/crypto_perps_baseline_v1.yaml'
    dataset = 'data/example_crypto_perps_5yr.parquet'

    # Check if dataset exists
    if not Path(dataset).exists():
        pytest.skip(f"Dataset {dataset} not found - skipping determinism test")

    # Run backtest twice
    output_dirs = []

    for run in [1, 2]:
        output_dir = f'out/determinism_test_run{run}'

        # Clean previous run
        if Path(output_dir).exists():
            shutil.rmtree(output_dir)

        # Run backtest
        cmd = [
            sys.executable, 'systems/crypto_perps/system.py',
            '--config', config,
            '--data', dataset,
            '--outdir', output_dir
        ]

        # Set PYTHONPATH to current directory
        env = os.environ.copy()
        env['PYTHONPATH'] = '.'

        result = subprocess.run(cmd, env=env, capture_output=True, text=True)
        assert result.returncode == 0, f"Run {run} failed:\n{result.stderr}"

        output_dirs.append(output_dir)

    # Compare canonical CSVs
    csv_files = ['equity_curve.csv', 'positions.csv', 'pnl_breakdown.csv']

    for filename in csv_files:
        hash1 = canonicalize_csv(f"{output_dirs[0]}/{filename}")
        hash2 = canonicalize_csv(f"{output_dirs[1]}/{filename}")

        assert hash1 == hash2, \
            f"{filename} differs between runs (canonical hash mismatch). " \
            f"Backtest is not deterministic!"

    # Compare headline metrics only from metadata
    assert compare_headline_metrics(
        f"{output_dirs[0]}/metadata.json",
        f"{output_dirs[1]}/metadata.json"
    ), "Headline metrics differ between runs!"

    # Cleanup
    for output_dir in output_dirs:
        shutil.rmtree(output_dir)

    print("✓ Determinism verified: identical canonical outputs on repeat runs")


def test_deterministic_dataset_build():
    """Test that dataset build is deterministic"""

    # Build same dataset twice
    output_files = []

    for run in [1, 2]:
        output_file = f'data/test_determinism_run{run}.parquet'

        cmd = [
            sys.executable, 'scripts/build_example_dataset.py',
            '--source', 'real',
            '--data-dir', 'data/raw/binance',
            '--start-date', '2023-01-01',
            '--end-date', '2023-12-31',
            '--instruments', 'BTCUSDT_PERP', 'ETHUSDT_PERP',
            '--output-path', output_file,
            '--min-coverage', '0.95'
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0:
            # If data doesn't exist, skip test
            if 'not found' in result.stderr or 'No such file' in result.stderr:
                pytest.skip("Raw data not available - skipping dataset build determinism test")
            else:
                assert False, f"Run {run} failed:\n{result.stderr}"

        output_files.append(output_file)

    # Load parquets, sort, and compare canonical representation
    df1 = pd.read_parquet(output_files[0])
    df2 = pd.read_parquet(output_files[1])

    # Sort by all columns for deterministic comparison
    df1 = df1.sort_values(by=list(df1.columns)).reset_index(drop=True)
    df2 = df2.sort_values(by=list(df2.columns)).reset_index(drop=True)

    # Round floats to avoid precision issues
    for col in df1.select_dtypes(include=['float64', 'float32']).columns:
        df1[col] = df1[col].round(8)
        df2[col] = df2[col].round(8)

    # Compare DataFrames
    pd.testing.assert_frame_equal(df1, df2)

    # Cleanup
    for filepath in output_files:
        Path(filepath).unlink()

    print("✓ Dataset build determinism verified")
