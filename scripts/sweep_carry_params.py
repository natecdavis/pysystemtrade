#!/usr/bin/env python3
"""
Sweep carry weight and threshold parameters for trend-gated carry.

Tests combinations of:
- carry_weight: [0.0, 0.1, 0.2, 0.3]
- carry_trend_gate_threshold: [0.5, 1.0, 1.5, 2.0]

Total: 16 backtests (approx 80 minutes on standard hardware)

Usage:
    python scripts/sweep_carry_params.py \
        --base-config config/crypto_perps_full_rules.yaml \
        --data data/dataset_538registry_6yr_jagged.parquet \
        --outdir out/carry_sweep

Outputs:
    out/carry_sweep/wc0.0_th0.5/ - First config (baseline, no carry)
    out/carry_sweep/wc0.1_th1.0/ - Second config
    ...
    out/carry_sweep/sweep_summary.csv - Comparison table
"""

import argparse
import subprocess
import yaml
import sys
from pathlib import Path
import pandas as pd

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))


def run_sweep(base_config: Path, data_path: Path, out_base: Path):
    """
    Run parameter sweep over carry_weight and carry_trend_gate_threshold.

    Args:
        base_config: Path to base YAML config
        data_path: Path to dataset parquet
        out_base: Output directory for sweep results
    """
    # Parameter grid
    carry_weights = [0.0, 0.1, 0.2, 0.3]
    thresholds = [0.5, 1.0, 1.5, 2.0]

    out_base.mkdir(parents=True, exist_ok=True)

    results = []

    for w_c in carry_weights:
        for thresh in thresholds:
            # Create config for this run
            run_name = f"wc{w_c:.1f}_th{thresh:.1f}"
            config_path = out_base / f"config_{run_name}.yaml"

            # Load base config
            with open(base_config) as f:
                config = yaml.safe_load(f)

            # Modify parameters
            config['use_gated_carry'] = (w_c > 0.0)  # Disable if w_c=0
            config['carry_weight'] = w_c
            config['carry_trend_gate_threshold'] = thresh

            # Enable carry rules if w_c > 0
            if w_c > 0.0:
                # Set each carry rule to 1% weight (3% total)
                config['forecast_weights']['vol_norm_carry_10'] = 0.01
                config['forecast_weights']['vol_norm_carry_30'] = 0.01
                config['forecast_weights']['vol_norm_carry_60'] = 0.01
            else:
                # Disable carry rules for baseline
                config['forecast_weights']['vol_norm_carry_10'] = 0.0
                config['forecast_weights']['vol_norm_carry_30'] = 0.0
                config['forecast_weights']['vol_norm_carry_60'] = 0.0

            # Save modified config
            with open(config_path, 'w') as f:
                yaml.dump(config, f, default_flow_style=False, sort_keys=False)

            # Run backtest
            outdir = out_base / run_name
            cmd = [
                "python", "scripts/run_dynamic_universe_backtest.py",
                "--config", str(config_path),
                "--data", str(data_path),
                "--outdir", str(outdir)
            ]

            print(f"\n{'='*80}")
            print(f"Running: {run_name} (carry_weight={w_c}, threshold={thresh})")
            print(f"{'='*80}")

            try:
                subprocess.run(cmd, check=True)

                # Read results
                metadata_path = outdir / "metadata.json"
                if metadata_path.exists():
                    import json
                    with open(metadata_path) as f:
                        metadata = json.load(f)

                    results.append({
                        'run_name': run_name,
                        'carry_weight': w_c,
                        'threshold': thresh,
                        'sharpe': metadata.get('sharpe', None),
                        'cagr': metadata.get('cagr', None),
                        'vol': metadata.get('ann_vol', None),
                        'max_dd': metadata.get('max_drawdown', None),
                        'avg_positions': metadata.get('avg_positions', None),
                    })
                else:
                    print(f"Warning: metadata.json not found for {run_name}")
                    results.append({
                        'run_name': run_name,
                        'carry_weight': w_c,
                        'threshold': thresh,
                        'sharpe': None,
                        'cagr': None,
                        'vol': None,
                        'max_dd': None,
                        'avg_positions': None,
                    })

            except subprocess.CalledProcessError as e:
                print(f"Error running {run_name}: {e}")
                results.append({
                    'run_name': run_name,
                    'carry_weight': w_c,
                    'threshold': thresh,
                    'sharpe': None,
                    'cagr': None,
                    'vol': None,
                    'max_dd': None,
                    'avg_positions': None,
                })

    # Write summary table
    summary_df = pd.DataFrame(results)
    summary_path = out_base / "sweep_summary.csv"
    summary_df.to_csv(summary_path, index=False)

    print(f"\n{'='*80}")
    print(f"Sweep complete! Results saved to {summary_path}")
    print(f"{'='*80}")
    print(summary_df.to_string(index=False))


def main():
    parser = argparse.ArgumentParser(
        description="Sweep carry_weight and carry_trend_gate_threshold parameters",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Run full sweep (16 backtests, ~80 minutes)
    python scripts/sweep_carry_params.py \\
        --base-config config/crypto_perps_full_rules.yaml \\
        --data data/dataset_538registry_6yr_jagged.parquet \\
        --outdir out/carry_sweep

    # Results will be in:
    #   out/carry_sweep/wc0.0_th0.5/  (baseline, no carry)
    #   out/carry_sweep/wc0.2_th1.0/  (default gating)
    #   ...
    #   out/carry_sweep/sweep_summary.csv  (comparison table)
        """
    )

    parser.add_argument(
        '--base-config',
        type=Path,
        required=True,
        help='Path to base config YAML (will be modified for each run)',
    )

    parser.add_argument(
        '--data',
        type=Path,
        required=True,
        help='Path to dataset parquet file',
    )

    parser.add_argument(
        '--outdir',
        type=Path,
        required=True,
        help='Output directory for sweep results',
    )

    args = parser.parse_args()

    # Validate inputs
    if not args.base_config.exists():
        print(f"Error: Base config not found: {args.base_config}")
        sys.exit(1)

    if not args.data.exists():
        print(f"Error: Dataset not found: {args.data}")
        sys.exit(1)

    print("Parameter Sweep Configuration:")
    print(f"  Base config: {args.base_config}")
    print(f"  Dataset: {args.data}")
    print(f"  Output dir: {args.outdir}")
    print(f"  Grid: carry_weight × threshold = 4 × 4 = 16 runs")
    print("")

    run_sweep(
        base_config=args.base_config,
        data_path=args.data,
        out_base=args.outdir,
    )


if __name__ == "__main__":
    main()
