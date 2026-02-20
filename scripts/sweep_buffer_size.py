#!/usr/bin/env python3
"""
buffer_size sensitivity sweep.

Runs the full backtest with different buffer_size values to understand
the trade-off between turnover (transaction costs) and tracking error.

buffer_size is the Carver position inertia threshold — positions are only
updated when the optimal position moves outside a zone of ±(buffer_size ×
position_volatility). Lower buffer → more trading → higher costs. Higher
buffer → less trading but higher tracking error.

Usage:
    python scripts/sweep_buffer_size.py \\
        --config config/crypto_perps_full_rules.yaml \\
        --data data/dataset_538registry_6yr_jagged.parquet \\
        --values 0.0 0.05 0.10 0.15 0.20 \\
        --outdir out/buffer_sweep

Optional:
    --values 0.0 0.05 0.10 0.15 0.20    (buffer_size values to test)
    --outdir out/buffer_sweep           (output directory)
"""

import argparse
import json
import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------

def run_backtest_with_buffer(
    base_config_path: str,
    data_path: str,
    buffer_value: float,
    output_dir: Path,
) -> dict:
    """
    Run backtest with buffer_size override, return metrics.

    Args:
        base_config_path: Path to base config YAML
        data_path: Path to parquet dataset
        buffer_value: buffer_size value to test
        output_dir: Root output directory for all buffer runs

    Returns:
        dict of extracted metrics, or None if run failed
    """
    # 1. Create temp config with buffer_size override
    logger.info(f"Creating temp config with buffer_size={buffer_value:.2f}")
    with open(base_config_path) as f:
        config = yaml.safe_load(f)

    # Override buffer_size at top level (pysystemtrade reads from config root)
    config['buffer_size'] = buffer_value

    # Create temporary config file
    temp_config_fd, temp_config_path = tempfile.mkstemp(suffix='.yaml', text=True)
    try:
        with os.fdopen(temp_config_fd, 'w') as f:
            yaml.dump(config, f)

        # 2. Run backtest
        outdir = output_dir / f"buffer_{buffer_value:.2f}"
        logger.info(f"Running backtest with buffer_size={buffer_value:.2f}")
        logger.info(f"  Output directory: {outdir}")

        result = subprocess.run(
            [
                sys.executable,  # Use same Python interpreter
                'scripts/run_dynamic_universe_backtest.py',
                '--config', temp_config_path,
                '--data', data_path,
                '--outdir', str(outdir),
            ],
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            logger.error(f"Backtest failed for buffer_size={buffer_value}")
            logger.error(f"STDERR:\n{result.stderr}")
            return None

        # 3. Read performance_summary.json
        summary_path = outdir / 'performance_summary.json'
        if not summary_path.exists():
            logger.error(f"No performance_summary.json found at {summary_path}")
            return None

        with open(summary_path) as f:
            summary = json.load(f)

        # 4. Extract relevant metrics
        metrics = summary.get('metrics', {})
        portfolio = summary.get('portfolio', {})
        cost_model = summary.get('cost_model', {})

        return {
            'buffer_size': buffer_value,
            'sharpe': metrics.get('sharpe'),
            'cagr': metrics.get('cagr'),
            'max_dd': metrics.get('max_dd'),
            'ann_vol': metrics.get('ann_vol'),
            'annual_turnover': portfolio.get('annual_turnover'),
            'avg_active_positions': portfolio.get('avg_active_positions'),
            'transaction_cost_ann': cost_model.get('transaction_cost_ann'),
            'funding_drag_ann': cost_model.get('funding_drag_ann'),
        }

    finally:
        # Clean up temp config file
        try:
            os.unlink(temp_config_path)
        except:
            pass


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------

def _fmt_pct(v, decimals=1):
    """Format value as percentage."""
    if v is None or (isinstance(v, float) and v != v):  # NaN check
        return 'N/A'
    return f"{v * 100:.{decimals}f}%"


def _fmt_f(v, decimals=2):
    """Format float value."""
    if v is None or (isinstance(v, float) and v != v):  # NaN check
        return 'N/A'
    return f"{v:.{decimals}f}"


def print_table(results: list[dict]) -> None:
    """Print markdown comparison table."""
    col_headers = [
        'buffer_size', 'Sharpe', 'CAGR', 'MaxDD', 'Vol',
        'Turnover/yr', 'Avg positions', 'Txn cost p.a.', 'Funding drag p.a.',
    ]
    col_widths = [max(12, len(h)) for h in col_headers]

    def _row(cells):
        padded = [str(c).ljust(w) for c, w in zip(cells, col_widths)]
        return '| ' + ' | '.join(padded) + ' |'

    def _sep():
        return '|-' + '-|-'.join(['-' * w for w in col_widths]) + '-|'

    print()
    print('## buffer_size Sensitivity Sweep')
    print()
    print(_row(col_headers))
    print(_sep())

    for r in results:
        cells = [
            _fmt_f(r.get('buffer_size'), decimals=2),
            _fmt_f(r.get('sharpe'), decimals=2),
            _fmt_pct(r.get('cagr'), decimals=1),
            _fmt_pct(r.get('max_dd'), decimals=1),
            _fmt_pct(r.get('ann_vol'), decimals=1),
            _fmt_f(r.get('annual_turnover'), decimals=2) + 'x',
            _fmt_f(r.get('avg_active_positions'), decimals=1),
            _fmt_pct(r.get('transaction_cost_ann'), decimals=2),
            _fmt_pct(r.get('funding_drag_ann'), decimals=2),
        ]
        print(_row(cells))

    print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description='buffer_size sensitivity sweep (position inertia threshold)'
    )
    parser.add_argument(
        '--config',
        required=True,
        help='Path to base config YAML'
    )
    parser.add_argument(
        '--data',
        required=True,
        help='Path to parquet dataset'
    )
    parser.add_argument(
        '--values',
        nargs='+',
        type=float,
        default=[0.0, 0.05, 0.10, 0.15, 0.20],
        help='buffer_size values to test (default: 0.0 0.05 0.10 0.15 0.20)'
    )
    parser.add_argument(
        '--outdir',
        default='out/buffer_sweep',
        help='Output directory for results (default: out/buffer_sweep)'
    )

    args = parser.parse_args()

    # Validate inputs
    config_path = Path(args.config)
    if not config_path.exists():
        logger.error(f"Config file not found: {args.config}")
        sys.exit(1)

    data_path = Path(args.data)
    if not data_path.exists():
        logger.error(f"Data file not found: {args.data}")
        sys.exit(1)

    # Create output directory
    output_dir = Path(args.outdir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Run sweep
    logger.info("=" * 70)
    logger.info(f"buffer_size Sensitivity Sweep")
    logger.info(f"  Config: {config_path}")
    logger.info(f"  Data: {data_path}")
    logger.info(f"  Values: {args.values}")
    logger.info(f"  Output: {output_dir}")
    logger.info("=" * 70)

    results = []
    for buffer_value in args.values:
        logger.info("")
        logger.info("=" * 70)
        logger.info(f"Testing buffer_size = {buffer_value:.2f}")
        logger.info("=" * 70)

        metrics = run_backtest_with_buffer(
            base_config_path=str(config_path),
            data_path=str(data_path),
            buffer_value=buffer_value,
            output_dir=output_dir,
        )

        if metrics:
            results.append(metrics)
            logger.info(
                f"  ✓ Sharpe={metrics.get('sharpe', float('nan')):.2f}  "
                f"Turnover={metrics.get('annual_turnover', float('nan')):.2f}x/yr"
            )
        else:
            logger.warning(f"  ✗ Failed to get metrics for buffer_size={buffer_value}")

    # Print results table
    if results:
        print_table(results)

        # Write JSON results
        results_path = output_dir / 'buffer_sweep_results.json'
        with open(results_path, 'w') as f:
            json.dump(results, f, indent=2, default=str)
        logger.info(f"Results written to {results_path}")
    else:
        logger.error("No results to display")
        sys.exit(1)


if __name__ == '__main__':
    main()
