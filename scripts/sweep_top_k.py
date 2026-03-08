"""
Top-K universe size sweep with Binance lot-size awareness.

Tests K = [15, 20, 25, 30, 35, 40, 50] against the 6-year 300-instrument dataset.

For each K, max_lot_notional is set to capital/K (auto mode), so only instruments
whose Binance minimum lot value fits within the per-K capital allocation are
eligible. This prevents phantom inclusions where an instrument is counted in K
but its lot size makes the position round to 0 after sizing.

Usage:
    python scripts/sweep_top_k.py \
        --config config/crypto_perps_full_rules.yaml \
        --data data/dataset_538registry_6yr_jagged.parquet \
        --outdir out/top_k_sweep
"""

import argparse
import json
import sys
import time
from pathlib import Path

import pandas as pd
import yaml

# Ensure project root on path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


K_VALUES = [15, 20, 25, 30, 35, 40, 50]


def run_single_k(
    config_path: str,
    data_path: str,
    output_dir: str,
    K: int,
) -> dict:
    """Run a single backtest for the given K value."""
    from scripts.run_dynamic_universe_backtest import run_backtest
    import copy

    # Load base config
    with open(config_path) as f:
        base_config = yaml.safe_load(f)

    # Override K and max_lot_notional (leave as 'auto' — dynamic_portfolio.py
    # will compute capital/K automatically)
    config = copy.deepcopy(base_config)
    config.setdefault('dynamic_universe', {})
    config['dynamic_universe']['top_k'] = K
    config['dynamic_universe']['max_lot_notional'] = 'auto'  # = capital/K

    # Scale entry/exit buffers proportionally with K
    # entry_buffer ≈ K/6, exit_buffer ≈ K/3 (same ratio as K=30 defaults)
    entry_buffer = max(2, K // 6)
    exit_buffer = max(4, K // 3)
    config['dynamic_universe']['entry_buffer'] = entry_buffer
    config['dynamic_universe']['exit_buffer'] = exit_buffer

    # Write temp config
    temp_config_path = Path(output_dir) / f"config_k{K:02d}.yaml"
    temp_config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(temp_config_path, 'w') as f:
        yaml.dump(config, f, default_flow_style=False, allow_unicode=True)

    run_outdir = Path(output_dir) / f"k{K:02d}"

    capital = float(config.get('notional_trading_capital', 10_000.0))
    max_lot = capital / K
    logger.info(
        f"─── K={K}: entry≤{entry_buffer}, exit>{exit_buffer}, "
        f"max_lot_notional=${max_lot:.0f} ───"
    )

    t0 = time.time()
    success = run_backtest(
        config_path=str(temp_config_path),
        data_path=data_path,
        output_dir=str(run_outdir),
        use_dynamic_universe=True,
    )
    elapsed = time.time() - t0

    if not success:
        logger.error(f"K={K}: backtest FAILED")
        return {'K': K, 'error': 'backtest failed'}

    # Load results
    perf_path = run_outdir / 'performance_summary.json'
    if not perf_path.exists():
        logger.error(f"K={K}: performance_summary.json not found")
        return {'K': K, 'error': 'no performance summary'}

    with open(perf_path) as f:
        perf = json.load(f)

    m = perf['metrics']
    p = perf.get('portfolio', {})
    c = perf.get('cost_model', {})

    row = {
        'K': K,
        'entry_buffer': entry_buffer,
        'exit_buffer': exit_buffer,
        'max_lot_notional': round(max_lot, 0),
        'sharpe': round(m['sharpe'], 3),
        'calmar': round(m['calmar'], 3),
        'cagr_pct': round(m['cagr'] * 100, 1),
        'vol_pct': round(m['ann_vol'] * 100, 1),
        'maxdd_pct': round(m['max_dd'] * 100, 1),
        'crisis_ret_pct': round(m.get('crisis_return', 0) * 100, 1),
        'avg_positions': round(p.get('avg_active_positions', 0), 1),
        'turnover': round(p.get('annual_turnover', 0), 2),
        'txn_cost_bps': round(c.get('transaction_cost_ann', 0) * 10_000, 1),
        'runtime_min': round(elapsed / 60, 1),
    }

    logger.info(
        f"K={K}: Sharpe={row['sharpe']}, Calmar={row['calmar']}, "
        f"CAGR={row['cagr_pct']}%, MaxDD={row['maxdd_pct']}%, "
        f"avg_pos={row['avg_positions']}, turnover={row['turnover']}x"
    )
    return row


def format_table(results: list) -> str:
    """Format results as markdown table."""
    baseline = next((r for r in results if r['K'] == 30), results[0])
    base_sharpe = baseline['sharpe']

    header = (
        "| K | entry | exit | max_lot | Sharpe | ΔSharpe | Calmar | CAGR | Vol | MaxDD | "
        "Crisis | Avg Pos | Turnover | Txn bps |"
    )
    sep = "| --- " * 14 + "|"
    lines = [header, sep]

    for r in results:
        if 'error' in r:
            lines.append(f"| {r['K']} | — | — | — | ERROR | — | — | — | — | — | — | — | — | — |")
            continue
        d_sharpe = (r['sharpe'] / base_sharpe - 1) * 100
        marker = " ✓" if d_sharpe > 1.0 else (" ✗" if d_sharpe < -1.0 else "")
        lines.append(
            f"| {r['K']} | {r['entry_buffer']} | {r['exit_buffer']} "
            f"| ${r['max_lot_notional']:.0f} "
            f"| **{r['sharpe']}** | {d_sharpe:+.1f}%{marker} "
            f"| {r['calmar']} | {r['cagr_pct']}% | {r['vol_pct']}% "
            f"| {r['maxdd_pct']}% | {r['crisis_ret_pct']}% "
            f"| {r['avg_positions']} | {r['turnover']}x | {r['txn_cost_bps']} |"
        )

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Sweep top-K universe size")
    parser.add_argument('--config', required=True, help='Base config YAML path')
    parser.add_argument('--data', required=True, help='Data parquet path')
    parser.add_argument('--outdir', required=True, help='Output directory')
    parser.add_argument(
        '--k-values', nargs='+', type=int, default=K_VALUES,
        help=f'K values to sweep (default: {K_VALUES})'
    )
    args = parser.parse_args()

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    logger.info(f"Top-K sweep: K={args.k_values}")
    logger.info(f"Config: {args.config}")
    logger.info(f"Data: {args.data}")
    logger.info(f"Output: {outdir}")
    logger.info(f"Estimated runtime: ~{len(args.k_values) * 10} min")

    results = []
    for K in args.k_values:
        row = run_single_k(
            config_path=args.config,
            data_path=args.data,
            output_dir=str(outdir),
            K=K,
        )
        results.append(row)
        # Save partial results after each run
        with open(outdir / 'sweep_results.json', 'w') as f:
            json.dump(results, f, indent=2)

    # Print final table
    print("\n\n" + "=" * 80)
    print("TOP-K SWEEP RESULTS")
    print("=" * 80)
    print(f"\nBaseline: K=30 (current production)\n")
    print(format_table(results))
    print()

    # Save final results
    with open(outdir / 'sweep_results.json', 'w') as f:
        json.dump(results, f, indent=2)

    # Find best K by Sharpe
    valid = [r for r in results if 'error' not in r]
    if valid:
        best = max(valid, key=lambda r: r['sharpe'])
        baseline = next((r for r in valid if r['K'] == 30), valid[0])
        delta = (best['sharpe'] / baseline['sharpe'] - 1) * 100
        print(f"\nBest Sharpe: K={best['K']} (Sharpe={best['sharpe']}, Δ={delta:+.1f}% vs K=30)")
        if delta >= 1.0:
            print(f"→ ADOPT K={best['K']} (ΔSharpe ≥ +1%)")
        else:
            print(f"→ KEEP K=30 (best improvement {delta:+.1f}% < +1% threshold)")

    logger.info(f"Sweep complete. Results: {outdir / 'sweep_results.json'}")


if __name__ == '__main__':
    main()
