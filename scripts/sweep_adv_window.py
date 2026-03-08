"""
ADV window sweep for top-K instrument selection.

Tests adv_window = [7, 14, 30, 60, 90] days against the 6-year 300-instrument dataset.

The adv_window controls the rolling lookback used by TopKInstrumentSelector to rank
instruments by average daily volume (ADV = mean(price × volume) over the window).
This ranking drives Stage 2 top-K selection.

Theoretical prediction: ~60 days is likely better for crypto perps because:
- Crypto volume is highly cyclical (3-10x monthly swings from news/listings/liquidations)
- A 30-day window may select instruments that quickly lose liquidity when cycles turn
- 60 days filters through a full monthly funding cycle and event-driven volume episodes
- Results should improve monotonically from 7→60 days (noise reduction), then plateau at 90+

Adoption criteria (BOTH required):
1. ΔSharpe ≥ +1% vs adv_window=30 baseline
2. Calmar non-decreasing toward the winning value
3. Winner is 60 or 90 days (not 7 or 14 — short-window wins would be surprising)

Usage:
    python scripts/sweep_adv_window.py \\
        --config config/crypto_perps_full_rules.yaml \\
        --data data/dataset_538registry_6yr_jagged.parquet \\
        --outdir out/adv_window_sweep
"""

import argparse
import copy
import json
import logging
import sys
import time
from pathlib import Path

import yaml

# Ensure project root on path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


ADV_WINDOW_VALUES = [7, 14, 30, 60, 90]
BASELINE_WINDOW = 30


def run_single_window(
    config_path: str,
    data_path: str,
    output_dir: str,
    adv_window: int,
) -> dict:
    """Run a single backtest for the given adv_window value."""
    from scripts.run_dynamic_universe_backtest import run_backtest

    # Load base config
    with open(config_path) as f:
        base_config = yaml.safe_load(f)

    # Override adv_window only; K, buffers, max_lot_notional unchanged from base config
    config = copy.deepcopy(base_config)
    config.setdefault('dynamic_universe', {})
    config['dynamic_universe']['adv_window'] = adv_window

    # Write temp config
    outdir = Path(output_dir)
    outdir.mkdir(parents=True, exist_ok=True)
    temp_config_path = outdir / f"config_adv{adv_window}d.yaml"
    with open(temp_config_path, 'w') as f:
        yaml.dump(config, f, default_flow_style=False, allow_unicode=True)

    run_outdir = outdir / f"adv{adv_window}d"

    # Log fixed parameters for clarity
    du = config.get('dynamic_universe', {})
    logger.info(
        f"─── adv_window={adv_window}d: K={du.get('top_k', '?')}, "
        f"entry={du.get('entry_buffer', '?')}, exit={du.get('exit_buffer', '?')}, "
        f"max_lot={du.get('max_lot_notional', '?')} ───"
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
        logger.error(f"adv_window={adv_window}: backtest FAILED")
        return {'adv_window': adv_window, 'error': 'backtest failed'}

    # Load results
    perf_path = run_outdir / 'performance_summary.json'
    if not perf_path.exists():
        logger.error(f"adv_window={adv_window}: performance_summary.json not found")
        return {'adv_window': adv_window, 'error': 'no performance summary'}

    with open(perf_path) as f:
        perf = json.load(f)

    m = perf['metrics']
    p = perf.get('portfolio', {})
    c = perf.get('cost_model', {})

    row = {
        'adv_window': adv_window,
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
        f"adv_window={adv_window}d: Sharpe={row['sharpe']}, Calmar={row['calmar']}, "
        f"CAGR={row['cagr_pct']}%, MaxDD={row['maxdd_pct']}%, "
        f"avg_pos={row['avg_positions']}, turnover={row['turnover']}x"
    )
    return row


def format_table(results: list) -> str:
    """Format results as markdown table with ΔSharpe vs baseline."""
    valid = [r for r in results if 'error' not in r]
    baseline = next((r for r in valid if r['adv_window'] == BASELINE_WINDOW), None)
    if baseline is None and valid:
        baseline = valid[0]
    base_sharpe = baseline['sharpe'] if baseline else 1.0

    header = (
        "| adv_window | Sharpe | ΔSharpe | Calmar | CAGR | Vol | MaxDD | "
        "Crisis | Avg Pos | Turnover | Txn bps | Runtime |"
    )
    sep = "| --- " * 12 + "|"
    lines = [header, sep]

    for r in results:
        if 'error' in r:
            lines.append(
                f"| {r['adv_window']}d | ERROR | — | — | — | — | — | — | — | — | — | — |"
            )
            continue
        d_sharpe = (r['sharpe'] / base_sharpe - 1) * 100
        marker = " ✓" if d_sharpe > 1.0 else (" ✗" if d_sharpe < -1.0 else "")
        baseline_marker = " *(baseline)*" if r['adv_window'] == BASELINE_WINDOW else ""
        lines.append(
            f"| {r['adv_window']}d{baseline_marker} "
            f"| **{r['sharpe']}** | {d_sharpe:+.1f}%{marker} "
            f"| {r['calmar']} | {r['cagr_pct']}% | {r['vol_pct']}% "
            f"| {r['maxdd_pct']}% | {r['crisis_ret_pct']}% "
            f"| {r['avg_positions']} | {r['turnover']}x | {r['txn_cost_bps']} "
            f"| {r['runtime_min']}m |"
        )

    return "\n".join(lines)


def make_adoption_decision(results: list) -> str:
    """Evaluate adoption criteria and return recommendation string."""
    valid = [r for r in results if 'error' not in r]
    if not valid:
        return "No valid results — cannot make recommendation."

    baseline = next((r for r in valid if r['adv_window'] == BASELINE_WINDOW), None)
    if baseline is None:
        return f"Baseline (adv_window={BASELINE_WINDOW}) not found in results."

    base_sharpe = baseline['sharpe']

    # Find best by Sharpe
    best = max(valid, key=lambda r: r['sharpe'])
    d_sharpe = (best['sharpe'] / base_sharpe - 1) * 100

    lines = ["=" * 70, "ADOPTION DECISION", "=" * 70]

    # Criterion 1: ΔSharpe ≥ +1%
    crit1 = d_sharpe >= 1.0
    lines.append(
        f"\nCriterion 1 — ΔSharpe ≥ +1%: "
        f"{'PASS ✓' if crit1 else 'FAIL ✗'}  "
        f"(best={best['adv_window']}d, Δ={d_sharpe:+.1f}%)"
    )

    # Criterion 2: Calmar non-decreasing toward winner
    # Sort by adv_window; check Calmar trend up to winner
    sorted_valid = sorted(valid, key=lambda r: r['adv_window'])
    winner_idx = next(
        (i for i, r in enumerate(sorted_valid) if r['adv_window'] == best['adv_window']),
        None
    )
    if winner_idx is not None and winner_idx > 0:
        calmar_trend = [r['calmar'] for r in sorted_valid[:winner_idx + 1]]
        # Non-decreasing: each step should be ≥ previous (allow small dips)
        non_decreasing = all(
            calmar_trend[i] >= calmar_trend[i - 1] - 0.05
            for i in range(1, len(calmar_trend))
        )
        crit2 = non_decreasing
        calmar_str = " → ".join(f"{c:.3f}" for c in calmar_trend)
        lines.append(
            f"Criterion 2 — Calmar non-decreasing to winner: "
            f"{'PASS ✓' if crit2 else 'FAIL ✗'}  "
            f"(Calmar path: {calmar_str})"
        )
    else:
        crit2 = True  # Only one value or baseline is best
        lines.append(
            f"Criterion 2 — Calmar non-decreasing to winner: N/A (baseline is best)"
        )

    # Criterion 3: Winner is long-window (60 or 90), not short (7 or 14)
    crit3 = best['adv_window'] >= 60
    lines.append(
        f"Criterion 3 — Winner ≥ 60d (theory-consistent): "
        f"{'PASS ✓' if crit3 else 'FAIL ✗'}  "
        f"(winner={best['adv_window']}d)"
    )

    if best['adv_window'] < 30:
        lines.append(
            f"\n  ⚠️  Short-window win ({best['adv_window']}d) is theoretically surprising."
        )
        lines.append(
            f"     Do NOT adopt without deeper investigation — could be in-sample noise"
        )
        lines.append(
            f"     or a crisis-period artifact. The 30-day baseline may already be optimal."
        )

    lines.append("")
    all_pass = crit1 and crit2 and crit3
    if all_pass:
        lines.append(
            f"→ ADOPT adv_window={best['adv_window']} "
            f"(Sharpe={best['sharpe']}, Δ={d_sharpe:+.1f}% vs {BASELINE_WINDOW}d baseline)"
        )
        lines.append(
            f"  Update config/crypto_perps_full_rules.yaml line ~108: "
            f"adv_window: {BASELINE_WINDOW} → adv_window: {best['adv_window']}"
        )
    else:
        lines.append(
            f"→ KEEP adv_window={BASELINE_WINDOW} "
            f"(not all adoption criteria met)"
        )
        failed = []
        if not crit1:
            failed.append(f"ΔSharpe {d_sharpe:+.1f}% < +1% threshold")
        if not crit2:
            failed.append("Calmar not monotonically non-decreasing toward winner")
        if not crit3:
            failed.append(
                f"Winner is short-window ({best['adv_window']}d) — investigate before adopting"
            )
        for f in failed:
            lines.append(f"  Reason: {f}")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Sweep ADV window for top-K selection")
    parser.add_argument('--config', required=True, help='Base config YAML path')
    parser.add_argument('--data', required=True, help='Data parquet path')
    parser.add_argument('--outdir', required=True, help='Output directory')
    parser.add_argument(
        '--adv-values', nargs='+', type=int, default=ADV_WINDOW_VALUES,
        help=f'ADV window values to sweep (default: {ADV_WINDOW_VALUES})'
    )
    args = parser.parse_args()

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    logger.info(f"ADV window sweep: values={args.adv_values}")
    logger.info(f"Baseline: adv_window={BASELINE_WINDOW}")
    logger.info(f"Config: {args.config}")
    logger.info(f"Data: {args.data}")
    logger.info(f"Output: {outdir}")
    logger.info(f"Estimated runtime: ~{len(args.adv_values) * 6} min")
    logger.info(
        "Theory: crypto volume is cyclical; ~60d expected optimal "
        "(filters monthly funding cycles + event-driven spikes)"
    )

    results = []
    for adv_window in args.adv_values:
        row = run_single_window(
            config_path=args.config,
            data_path=args.data,
            output_dir=str(outdir),
            adv_window=adv_window,
        )
        results.append(row)
        # Save partial results after each run
        with open(outdir / 'sweep_results.json', 'w') as f:
            json.dump(results, f, indent=2)

    # Print final table
    print("\n\n" + "=" * 80)
    print("ADV WINDOW SWEEP RESULTS")
    print("=" * 80)
    print(
        f"\nBaseline: adv_window={BASELINE_WINDOW}d (current production)\n"
        f"Fixed: K=35, entry_buffer=5, exit_buffer=11, max_lot_notional='auto'\n"
        f"Theory: ADV window should be ~60d for crypto perps "
        f"(cyclical volume, monthly funding cycles)\n"
    )
    print(format_table(results))
    print()
    print(make_adoption_decision(results))
    print()

    # Save final results
    with open(outdir / 'sweep_results.json', 'w') as f:
        json.dump(results, f, indent=2)

    logger.info(f"Sweep complete. Results saved to {outdir / 'sweep_results.json'}")


if __name__ == '__main__':
    main()
