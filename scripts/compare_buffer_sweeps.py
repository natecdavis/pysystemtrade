#!/usr/bin/env python3
"""
Compare unbuffered vs buffered buffer sweep results.

This script loads the results from two buffer sweeps (one with buffering
disabled, one with buffering enabled) and compares the performance metrics
to quantify the impact of buffering on Sharpe, turnover, and costs.

Usage:
    python scripts/compare_buffer_sweeps.py

Outputs:
    Comparison table printed to console
"""

import json
import sys
from pathlib import Path


def load_results(json_path):
    """Load sweep results from JSON file."""
    with open(json_path) as f:
        return json.load(f)


def compare_sweeps(unbuffered_path, buffered_path):
    """
    Compare unbuffered vs buffered sweep results.

    Args:
        unbuffered_path: Path to unbuffered sweep results JSON
        buffered_path: Path to buffered sweep results JSON
    """
    # Load results
    unbuf_list = load_results(unbuffered_path)
    buf_list = load_results(buffered_path)

    # Convert to dicts keyed by buffer_size
    unbuf = {r['buffer_size']: r for r in unbuf_list}
    buf = {r['buffer_size']: r for r in buf_list}

    # Print comparison table
    print("Buffer Size Impact Comparison (Unbuffered vs Buffered)")
    print("=" * 120)
    print(f"{'Buffer':>6} │ {'Unbuffered':^40} │ {'Buffered':^40} │ {'Delta':^25}")
    print(f"{'Size':>6} │ {'Sharpe':>10} {'Turnover':>12} {'Cost (bps)':>12} │ {'Sharpe':>10} {'Turnover':>12} {'Cost (bps)':>12} │ {'ΔSharpe':>10} {'ΔTurnover':>12}")
    print("-" * 120)

    for size in sorted(unbuf.keys()):
        if size not in buf:
            print(f"{size:6.2f} │ Missing buffered result")
            continue

        u = unbuf[size]
        b = buf[size]

        # Extract metrics
        sharpe_u = u['sharpe']
        sharpe_b = b['sharpe']
        turn_u = u['annual_turnover']
        turn_b = b['annual_turnover']

        # Calculate costs (turnover × 10 bps / 2)
        cost_u = turn_u * 10.0 / 2.0  # bps per year
        cost_b = turn_b * 10.0 / 2.0

        # Deltas
        d_sharpe = sharpe_b - sharpe_u
        d_turn = turn_b - turn_u
        d_turn_pct = 100.0 * d_turn / turn_u if turn_u > 0 else 0.0

        print(
            f"{size:6.2f} │ "
            f"{sharpe_u:10.4f} {turn_u:11.2f}x {cost_u:11.1f} │ "
            f"{sharpe_b:10.4f} {turn_b:11.2f}x {cost_b:11.1f} │ "
            f"{d_sharpe:+10.4f} {d_turn_pct:+11.1f}%"
        )

    print("=" * 120)


def main():
    """Main entry point."""
    # Paths to sweep result files
    unbuffered_path = Path('out/buffer_sweep/buffer_sweep_results.json')
    buffered_path = Path('out/buffer_sweep_buffered/buffer_sweep_results.json')

    # Check files exist
    if not unbuffered_path.exists():
        print(f"ERROR: Unbuffered results not found: {unbuffered_path}", file=sys.stderr)
        sys.exit(1)

    if not buffered_path.exists():
        print(f"ERROR: Buffered results not found: {buffered_path}", file=sys.stderr)
        print(f"       Run the buffer sweep first:", file=sys.stderr)
        print(f"       python scripts/sweep_buffer_size.py \\", file=sys.stderr)
        print(f"         --config config/crypto_perps_full_rules.yaml \\", file=sys.stderr)
        print(f"         --data data/dataset_538registry_6yr_jagged.parquet \\", file=sys.stderr)
        print(f"         --values 0.0 0.05 0.10 0.15 0.20 \\", file=sys.stderr)
        print(f"         --outdir out/buffer_sweep_buffered", file=sys.stderr)
        sys.exit(1)

    # Compare results
    compare_sweeps(unbuffered_path, buffered_path)


if __name__ == '__main__':
    main()
