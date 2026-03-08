"""
Exit buffer sweep for top-K instrument selection hysteresis.

Tests exit_buffer = [5, 8, 11, 15, 20, 25] with entry_buffer=5 fixed.

The exit_buffer controls how long incumbents stay in the portfolio after their ADV rank
deteriorates. With K=35 and exit_buffer=11, an instrument exits only when its rank drops
to 47+ (exit_threshold = K + exit_buffer = 35 + 11 = 46). Larger exit_buffer = stickier
portfolio = lower turnover = lower transaction costs = potentially better net Sharpe.

With adv_window=90 now adopted, ADV rankings are more stable — the optimal sticky threshold
may shift. The primary question: does wider exit hysteresis reduce costly false-exit/re-entry
cycles, or does it hold genuinely declining instruments too long?

Theory: Sharpe should improve as exit_buffer increases from 5 → ~15-20 (fewer false exits),
then plateau or decline (stale holdings). Turnover should decrease monotonically.

Adoption criteria (ALL required):
1. ΔSharpe ≥ +1% vs exit_buffer=11 baseline
2. Calmar non-decreasing toward winning value (tolerance ≤0.05)
3. Turnover ≤ 20x at winning value (hard cap — K=50 was rejected at 20.37x)
4. exit_buffer ≥ 11 (wider buffers theoretically consistent; smaller wins flagged)

Usage:
    python scripts/sweep_buffer.py \\
        --config config/crypto_perps_full_rules.yaml \\
        --data data/dataset_538registry_6yr_jagged.parquet \\
        --outdir out/buffer_sweep_exit
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


EXIT_BUFFER_VALUES = [5, 8, 11, 15, 20, 25]
BASELINE_EXIT_BUFFER = 11
FIXED_ENTRY_BUFFER = 5  # held constant throughout sweep


def run_single_exit_buffer(
    config_path: str,
    data_path: str,
    output_dir: str,
    exit_buffer: int,
) -> dict:
    """Run a single backtest for the given exit_buffer value."""
    from scripts.run_dynamic_universe_backtest import run_backtest

    # Load base config
    with open(config_path) as f:
        base_config = yaml.safe_load(f)

    # Override exit_buffer only; K, entry_buffer, adv_window, max_lot_notional unchanged
    config = copy.deepcopy(base_config)
    config.setdefault('dynamic_universe', {})
    config['dynamic_universe']['exit_buffer'] = exit_buffer
    # Ensure entry_buffer is at the fixed value (in case base config differs)
    config['dynamic_universe']['entry_buffer'] = FIXED_ENTRY_BUFFER

    # Compute derived thresholds for logging/output
    du = config['dynamic_universe']
    k = du.get('top_k', 35)
    entry_threshold = k - FIXED_ENTRY_BUFFER   # must rank ≤ this to enter
    exit_threshold = k + exit_buffer            # exits when rank > this
    deadband = f"[{entry_threshold + 1}, {exit_threshold}]"

    # Write temp config
    outdir = Path(output_dir)
    outdir.mkdir(parents=True, exist_ok=True)
    temp_config_path = outdir / f"config_exit{exit_buffer}.yaml"
    with open(temp_config_path, 'w') as f:
        yaml.dump(config, f, default_flow_style=False, allow_unicode=True)

    run_outdir = outdir / f"exit{exit_buffer}"

    logger.info(
        f"─── exit_buffer={exit_buffer}: K={k}, entry_buffer={FIXED_ENTRY_BUFFER}, "
        f"entry_threshold={entry_threshold}, exit_threshold={exit_threshold}, "
        f"deadband={deadband}, adv_window={du.get('adv_window', '?')}d ───"
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
        logger.error(f"exit_buffer={exit_buffer}: backtest FAILED")
        return {
            'exit_buffer': exit_buffer,
            'entry_buffer': FIXED_ENTRY_BUFFER,
            'entry_threshold': entry_threshold,
            'exit_threshold': exit_threshold,
            'deadband': deadband,
            'error': 'backtest failed',
        }

    # Load results
    perf_path = run_outdir / 'performance_summary.json'
    if not perf_path.exists():
        logger.error(f"exit_buffer={exit_buffer}: performance_summary.json not found")
        return {
            'exit_buffer': exit_buffer,
            'entry_buffer': FIXED_ENTRY_BUFFER,
            'entry_threshold': entry_threshold,
            'exit_threshold': exit_threshold,
            'deadband': deadband,
            'error': 'no performance summary',
        }

    with open(perf_path) as f:
        perf = json.load(f)

    m = perf['metrics']
    p = perf.get('portfolio', {})
    c = perf.get('cost_model', {})

    row = {
        'exit_buffer': exit_buffer,
        'entry_buffer': FIXED_ENTRY_BUFFER,
        'entry_threshold': entry_threshold,
        'exit_threshold': exit_threshold,
        'deadband': deadband,
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
        f"exit_buffer={exit_buffer}: Sharpe={row['sharpe']}, Calmar={row['calmar']}, "
        f"CAGR={row['cagr_pct']}%, MaxDD={row['maxdd_pct']}%, "
        f"turnover={row['turnover']}x, txn={row['txn_cost_bps']}bps"
    )
    return row


def format_table(results: list) -> str:
    """Format results as markdown table with ΔSharpe vs baseline."""
    valid = [r for r in results if 'error' not in r]
    baseline = next(
        (r for r in valid if r['exit_buffer'] == BASELINE_EXIT_BUFFER), None
    )
    if baseline is None and valid:
        baseline = valid[0]
    base_sharpe = baseline['sharpe'] if baseline else 1.0

    header = (
        "| exit_buf | entry_thr | exit_thr | deadband "
        "| Sharpe | ΔSharpe | Calmar | CAGR | Vol | MaxDD "
        "| Avg Pos | Turnover | Txn bps | Runtime |"
    )
    sep = "| --- " * 14 + "|"
    lines = [header, sep]

    for r in results:
        if 'error' in r:
            eb = r['exit_buffer']
            lines.append(
                f"| {eb} | {r.get('entry_threshold','?')} | {r.get('exit_threshold','?')} "
                f"| {r.get('deadband','?')} | ERROR | — | — | — | — | — | — | — | — | — |"
            )
            continue
        d_sharpe = (r['sharpe'] / base_sharpe - 1) * 100
        marker = " ✓" if d_sharpe > 1.0 else (" ✗" if d_sharpe < -1.0 else "")
        baseline_marker = " *(baseline)*" if r['exit_buffer'] == BASELINE_EXIT_BUFFER else ""
        lines.append(
            f"| {r['exit_buffer']}{baseline_marker} "
            f"| {r['entry_threshold']} | {r['exit_threshold']} | {r['deadband']} "
            f"| **{r['sharpe']}** | {d_sharpe:+.1f}%{marker} "
            f"| {r['calmar']} | {r['cagr_pct']}% | {r['vol_pct']}% "
            f"| {r['maxdd_pct']}% "
            f"| {r['avg_positions']} | {r['turnover']}x | {r['txn_cost_bps']} "
            f"| {r['runtime_min']}m |"
        )

    return "\n".join(lines)


def make_adoption_decision(results: list) -> str:
    """Evaluate adoption criteria and return recommendation string."""
    valid = [r for r in results if 'error' not in r]
    if not valid:
        return "No valid results — cannot make recommendation."

    baseline = next(
        (r for r in valid if r['exit_buffer'] == BASELINE_EXIT_BUFFER), None
    )
    if baseline is None:
        return f"Baseline (exit_buffer={BASELINE_EXIT_BUFFER}) not found in results."

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
        f"(best=exit_buffer={best['exit_buffer']}, Δ={d_sharpe:+.1f}%)"
    )

    # Criterion 2: Calmar non-decreasing toward winner (with ≤0.05 tolerance)
    sorted_valid = sorted(valid, key=lambda r: r['exit_buffer'])
    winner_idx = next(
        (i for i, r in enumerate(sorted_valid) if r['exit_buffer'] == best['exit_buffer']),
        None
    )
    if winner_idx is not None and winner_idx > 0:
        calmar_trend = [r['calmar'] for r in sorted_valid[:winner_idx + 1]]
        non_decreasing = all(
            calmar_trend[i] >= calmar_trend[i - 1] - 0.05
            for i in range(1, len(calmar_trend))
        )
        crit2 = non_decreasing
        calmar_str = " → ".join(f"{c:.3f}" for c in calmar_trend)
        lines.append(
            f"Criterion 2 — Calmar non-decreasing to winner (tol=0.05): "
            f"{'PASS ✓' if crit2 else 'FAIL ✗'}  "
            f"(Calmar path: {calmar_str})"
        )
    else:
        crit2 = True  # Baseline is best or only one value
        lines.append(
            f"Criterion 2 — Calmar non-decreasing to winner: N/A (baseline is best)"
        )

    # Criterion 3: Turnover ≤ 20x at winning value
    best_turnover = best['turnover']
    crit3 = best_turnover <= 20.0
    lines.append(
        f"Criterion 3 — Turnover ≤ 20x at winner: "
        f"{'PASS ✓' if crit3 else 'FAIL ✗'}  "
        f"(turnover={best_turnover}x)"
    )

    # Criterion 4: exit_buffer ≥ 11 (theory-consistent direction)
    crit4 = best['exit_buffer'] >= BASELINE_EXIT_BUFFER
    lines.append(
        f"Criterion 4 — exit_buffer ≥ {BASELINE_EXIT_BUFFER} (wider = stickier, theory-consistent): "
        f"{'PASS ✓' if crit4 else 'WARN ⚠'}  "
        f"(winner=exit_buffer={best['exit_buffer']})"
    )

    if best['exit_buffer'] < BASELINE_EXIT_BUFFER:
        lines.append(
            f"\n  ⚠️  Smaller exit_buffer win ({best['exit_buffer']} < baseline {BASELINE_EXIT_BUFFER}) "
            f"is theoretically surprising."
        )
        lines.append(
            f"     Possible explanation: with adv_window=90, rankings are stable enough that"
        )
        lines.append(
            f"     the baseline buffer was ALREADY over-sticky; tighter exits help refresh"
        )
        lines.append(
            f"     the portfolio with better-ranked instruments. Investigate before adopting."
        )
        lines.append(
            f"     Compare avg_positions and turnover to verify this interpretation."
        )

    # Turnover trend comment
    turnover_trend = [r['turnover'] for r in sorted_valid]
    turnover_monotone = all(
        turnover_trend[i] <= turnover_trend[i - 1]
        for i in range(1, len(turnover_trend))
    )
    lines.append(
        f"\nTurnover trend (expected monotone decrease): "
        f"{'✓ Monotone' if turnover_monotone else '⚠ Non-monotone'}  "
        f"({' → '.join(f'{t}x' for t in turnover_trend)})"
    )
    if not turnover_monotone:
        lines.append(
            f"  Non-monotone turnover suggests universe instability at large buffers "
            f"(incumbents stay so long that exits cluster when they finally occur)."
        )

    lines.append("")
    # Criterion 4 is a warning, not hard FAIL
    all_pass = crit1 and crit2 and crit3
    if all_pass:
        lines.append(
            f"→ ADOPT exit_buffer={best['exit_buffer']} "
            f"(Sharpe={best['sharpe']}, Δ={d_sharpe:+.1f}% vs exit_buffer={BASELINE_EXIT_BUFFER} baseline)"
        )
        lines.append(
            f"  Update config/crypto_perps_full_rules.yaml: "
            f"exit_buffer: {BASELINE_EXIT_BUFFER} → exit_buffer: {best['exit_buffer']}"
        )
        if not crit4:
            lines.append(
                f"  ⚠️  WARNING: Criterion 4 failed (unexpected direction). "
                f"Verify interpretation before committing."
            )
    else:
        lines.append(
            f"→ KEEP exit_buffer={BASELINE_EXIT_BUFFER} "
            f"(not all adoption criteria met)"
        )
        failed = []
        if not crit1:
            failed.append(f"ΔSharpe {d_sharpe:+.1f}% < +1% threshold")
        if not crit2:
            failed.append("Calmar not non-decreasing toward winner (tolerance 0.05)")
        if not crit3:
            failed.append(f"Turnover {best_turnover}x > 20x hard cap")
        for reason in failed:
            lines.append(f"  Reason: {reason}")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Sweep exit_buffer for top-K selection hysteresis"
    )
    parser.add_argument('--config', required=True, help='Base config YAML path')
    parser.add_argument('--data', required=True, help='Data parquet path')
    parser.add_argument('--outdir', required=True, help='Output directory')
    parser.add_argument(
        '--exit-values', nargs='+', type=int, default=EXIT_BUFFER_VALUES,
        help=f'exit_buffer values to sweep (default: {EXIT_BUFFER_VALUES})'
    )
    args = parser.parse_args()

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    logger.info(f"Exit buffer sweep: values={args.exit_values}")
    logger.info(f"Baseline: exit_buffer={BASELINE_EXIT_BUFFER}")
    logger.info(f"Fixed: entry_buffer={FIXED_ENTRY_BUFFER}, K=35, adv_window=90, max_lot_notional='auto'")
    logger.info(f"Config: {args.config}")
    logger.info(f"Data: {args.data}")
    logger.info(f"Output: {outdir}")
    logger.info(f"Estimated runtime: ~{len(args.exit_values) * 6} min")
    logger.info(
        "Theory: wider exit_buffer → stickier portfolio → fewer false exits → "
        "lower transaction costs → better net Sharpe (up to the point of holding stale instruments)"
    )

    results = []
    for exit_buffer in args.exit_values:
        row = run_single_exit_buffer(
            config_path=args.config,
            data_path=args.data,
            output_dir=str(outdir),
            exit_buffer=exit_buffer,
        )
        results.append(row)
        # Save partial results after each run
        with open(outdir / 'sweep_results.json', 'w') as f:
            json.dump(results, f, indent=2)

    # Print final table
    print("\n\n" + "=" * 80)
    print("EXIT BUFFER SWEEP RESULTS")
    print("=" * 80)
    print(
        f"\nBaseline: exit_buffer={BASELINE_EXIT_BUFFER} (current production)\n"
        f"Fixed: K=35, entry_buffer={FIXED_ENTRY_BUFFER}, adv_window=90d, "
        f"max_lot_notional='auto'\n"
        f"Theory: larger exit_buffer → stickier portfolio → lower turnover → "
        f"better net Sharpe (up to holding-stale-instruments crossover)\n"
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
