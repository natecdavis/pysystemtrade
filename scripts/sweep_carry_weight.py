"""
Sweep carry_weight parameter with threshold fixed at 0.5.

Tests carry_weight from 0.3 (current optimum) to 4.76 (equal weight with
one trend family) to determine optimal balance between carry and trend.

Fixed: carry_trend_gate_threshold = 0.5 (optimal from previous sweep)
Variable: carry_weight ∈ [0.3, 0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 4.76]

Total: 8 backtests (~40 minutes runtime)
"""

import subprocess
import yaml
from pathlib import Path
import json
from datetime import datetime
import sys

# Parameter grid
carry_weights = [0.3, 0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 4.76]
threshold = 0.5  # Fixed at optimal value

base_config = Path("config/crypto_perps_full_rules.yaml")
data_path = Path("data/dataset_538registry_6yr_jagged.parquet")
out_base = Path("out/carry_weight_sweep")

def main():
    # Validate inputs
    if not base_config.exists():
        print(f"Error: Base config not found: {base_config}")
        sys.exit(1)

    if not data_path.exists():
        print(f"Error: Data file not found: {data_path}")
        sys.exit(1)

    # Create output directory
    out_base.mkdir(parents=True, exist_ok=True)

    # Track results
    results = []

    print(f"\n{'='*80}")
    print(f"CARRY WEIGHT PARAMETER SWEEP")
    print(f"{'='*80}")
    print(f"Testing {len(carry_weights)} configurations")
    print(f"Fixed parameter: threshold = {threshold}")
    print(f"Variable parameter: carry_weight ∈ {carry_weights}")
    print(f"Estimated runtime: ~{len(carry_weights) * 5} minutes")
    print(f"{'='*80}\n")

    for idx, w_c in enumerate(carry_weights, 1):
        # Create config for this run
        run_name = f"wc{w_c:.2f}_th{threshold:.1f}"
        config_path = out_base / f"config_{run_name}.yaml"

        # Load base config
        with open(base_config) as f:
            config = yaml.safe_load(f)

        # Modify parameters
        config['carry_weight'] = w_c
        config['carry_trend_gate_threshold'] = threshold
        config['use_gated_carry'] = True  # Always enabled

        # Save modified config
        with open(config_path, 'w') as f:
            yaml.dump(config, f)

        # Run backtest
        outdir = out_base / run_name
        cmd = [
            "python", "scripts/run_dynamic_universe_backtest.py",
            "--config", str(config_path),
            "--data", str(data_path),
            "--outdir", str(outdir)
        ]

        print(f"\n{'='*80}")
        print(f"[{idx}/{len(carry_weights)}] Running: {run_name}")
        print(f"  carry_weight={w_c}, threshold={threshold}")
        print(f"{'='*80}")

        try:
            subprocess.run(cmd, check=True)

            # Extract results
            summary_path = outdir / "performance_summary.json"
            if summary_path.exists():
                with open(summary_path) as f:
                    summary = json.load(f)
                    results.append({
                        'run_name': run_name,
                        'carry_weight': w_c,
                        'threshold': threshold,
                        'sharpe': summary['metrics']['sharpe'],
                        'cagr': summary['metrics']['cagr'],
                        'vol': summary['metrics']['ann_vol'],
                        'max_dd': summary['metrics']['max_dd'],
                        'avg_positions': summary['portfolio']['avg_active_positions'],
                        'turnover': summary['portfolio']['annual_turnover']
                    })

            print(f"✓ Completed: {run_name}")

        except subprocess.CalledProcessError as e:
            print(f"✗ Failed: {run_name} - {e}")
            results.append({
                'run_name': run_name,
                'carry_weight': w_c,
                'threshold': threshold,
                'error': str(e)
            })

    # Save summary
    summary_path = out_base / "sweep_summary.json"
    with open(summary_path, 'w') as f:
        json.dump({
            'timestamp': datetime.now().isoformat(),
            'sweep_type': 'carry_weight',
            'fixed_params': {'threshold': threshold},
            'tested_weights': carry_weights,
            'total_configs': len(carry_weights),
            'results': results
        }, f, indent=2)

    print(f"\n{'='*80}")
    print(f"SWEEP COMPLETE!")
    print(f"Results saved to: {summary_path}")
    print(f"{'='*80}")

    # Print summary table
    print("\nRESULTS SUMMARY:")
    print("-" * 80)
    print(f"{'carry_weight':<15} {'Sharpe':<10} {'CAGR':<10} {'Vol':<10} {'MaxDD':<10} {'Turnover':<10}")
    print("-" * 80)

    for r in results:
        if 'error' not in r:
            print(f"{r['carry_weight']:<15.2f} {r['sharpe']:<10.4f} {r['cagr']*100:<10.2f} "
                  f"{r['vol']*100:<10.2f} {r['max_dd']*100:<10.2f} {r['turnover']:<10.2f}")
        else:
            print(f"{r['carry_weight']:<15.2f} ERROR: {r['error']}")

    print("-" * 80)
    print(f"\nRun analysis script to generate detailed report:")
    print(f"  python scripts/analyze_carry_weight_sweep.py")
    print()

if __name__ == "__main__":
    main()
