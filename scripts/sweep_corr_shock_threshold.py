#!/usr/bin/env python3
"""
Sweep correlation shock overlay threshold values and compare backtest performance.

Runs a full backtest for each threshold, collects metrics, and prints a
comparison table with adoption criteria check.

Signal: Rolling mean pairwise correlation estimated via portfolio-variance
decomposition (no N×(N-1)/2 pairwise computation needed):
  mean_corr = (port_var × N − mean_ind_var) / ((N − 1) × mean_ind_var)
When mean_corr > threshold, positions are scaled down linearly to min_scale
(default 0.5) at mean_corr = 1.0. EWM smooth (span=5) reduces day-to-day noise.

Carver analogue: Component 3 ("sum_abs_risk") of his 4-part risk overlay.

Adoption criteria (per plan — overlay is insurance, not alpha):
  ΔSharpe > -1%           — overlay can cost a little Sharpe (it's insurance)
  ΔMaxDD > +1pp           — MUST actually reduce drawdown (the whole point)
  Best threshold: Calmar-peak among thresholds that pass both criteria

Usage:
    python scripts/sweep_corr_shock_threshold.py \\
        --base-config config/crypto_perps_full_rules.yaml \\
        --data data/dataset_538registry_6yr_jagged.parquet \\
        --outdir out/corr_shock_sweep \\
        [--thresholds 0.60 0.65 0.70 0.75 0.80] \\
        [--min-scale 0.5] \\
        [--window 30] \\
        [--smooth-span 5] \\
        [--skip-existing]
"""

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))


def load_yaml(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def run_backtest(config_path: Path, data_path: Path, outdir: Path) -> int:
    """Run a single backtest. Returns subprocess return code."""
    cmd = [
        sys.executable,
        'scripts/run_dynamic_universe_backtest.py',
        '--config', str(config_path),
        '--data', str(data_path),
        '--outdir', str(outdir),
    ]
    print(f'\n  CMD: {" ".join(cmd)}')
    result = subprocess.run(cmd, capture_output=False)
    return result.returncode


def load_results(outdir: Path) -> dict:
    """Load performance_summary.json from a backtest outdir."""
    summary_path = outdir / 'performance_summary.json'
    if not summary_path.exists():
        return {}
    with open(summary_path) as f:
        return json.load(f)


def print_comparison(results: list, baseline_metrics: dict) -> None:
    """Print formatted comparison table with adoption criteria."""
    print()
    print('=' * 115)
    print('CORRELATION SHOCK OVERLAY — THRESHOLD SWEEP RESULTS')
    print('Signal: mean pairwise correlation via portfolio-variance decomposition')
    print('Action: linear scale-down from 1.0 at threshold to min_scale at mean_corr=1.0')
    print('Δ columns: relative to baseline (no overlay)')
    print('=' * 115)

    hdr = (
        f'{"Threshold":>10}  {"Sharpe":>8}  {"Calmar":>8}  {"CAGR":>8}  '
        f'{"MaxDD":>8}  {"ΔSharpe%":>9}  {"ΔCalmar":>8}  {"ΔMaxDD pp":>10}  {"Verdict"}'
    )
    print(hdr)
    print('─' * 115)

    b_sharpe = baseline_metrics.get('sharpe', float('nan'))
    b_calmar = baseline_metrics.get('calmar', float('nan'))
    b_maxdd  = baseline_metrics.get('max_dd', float('nan'))

    # Print baseline row
    print(
        f'{"baseline":>10}  '
        f'{b_sharpe:>8.4f}  '
        f'{b_calmar:>8.4f}  '
        f'{baseline_metrics.get("cagr", 0) * 100:>7.2f}%  '
        f'{b_maxdd * 100:>7.2f}%  '
        f'{"":>9}  '
        f'{"":>8}  '
        f'{"":>10}  '
        f'← no overlay'
    )

    for r in results:
        m = r.get('metrics', {})
        threshold = r['threshold']
        sharpe    = m.get('sharpe',  float('nan'))
        calmar    = m.get('calmar',  float('nan'))
        cagr      = m.get('cagr',    float('nan'))
        maxdd     = m.get('max_dd',  float('nan'))

        d_sharpe_pct = (sharpe - b_sharpe) / abs(b_sharpe) * 100 if b_sharpe else float('nan')
        d_calmar     = calmar - b_calmar
        d_maxdd_pp   = (maxdd - b_maxdd) * 100

        # Adoption criteria
        c_sharpe = d_sharpe_pct > -1.0
        c_maxdd  = d_maxdd_pp > 1.0   # MaxDD improvement: less negative → positive delta
        verdict  = '✓ CANDIDATE' if (c_sharpe and c_maxdd) else '✗ skip'

        print(
            f'{threshold:>10.2f}  '
            f'{sharpe:>8.4f}  '
            f'{calmar:>8.4f}  '
            f'{cagr * 100:>7.2f}%  '
            f'{maxdd * 100:>7.2f}%  '
            f'{d_sharpe_pct:>+8.1f}%  '
            f'{d_calmar:>+8.4f}  '
            f'{d_maxdd_pp:>+9.2f}pp  '
            f'{verdict}'
        )

    print('─' * 115)
    print()

    # --- Adoption criteria check ---
    print('ADOPTION CRITERIA')
    print('  (1) ΔSharpe > -1%      — overlay can cost a little Sharpe (it is insurance)')
    print('  (2) ΔMaxDD > +1pp      — MUST actually reduce drawdown (the whole point)')
    print('  Best: Calmar-peak among thresholds that pass both criteria')
    print()

    candidates = []
    for r in results:
        m = r.get('metrics', {})
        threshold    = r['threshold']
        sharpe       = m.get('sharpe', float('nan'))
        calmar       = m.get('calmar', float('nan'))
        maxdd        = m.get('max_dd', float('nan'))

        d_sharpe_pct = (sharpe - b_sharpe) / abs(b_sharpe) * 100 if b_sharpe else float('nan')
        d_maxdd_pp   = (maxdd - b_maxdd) * 100

        c_sharpe = d_sharpe_pct > -1.0
        c_maxdd  = d_maxdd_pp > 1.0
        passed   = c_sharpe and c_maxdd

        print(
            f'  threshold={threshold:.2f}:  '
            f'ΔSharpe={d_sharpe_pct:+.1f}% {("✓" if c_sharpe else "✗")}  '
            f'ΔMaxDD={d_maxdd_pp:+.2f}pp {("✓" if c_maxdd else "✗")}  '
            f'→ {"✓ CANDIDATE" if passed else "✗ skip"}'
        )
        if passed:
            candidates.append(r)

    print()
    if candidates:
        best = max(candidates, key=lambda r: r.get('metrics', {}).get('calmar', float('-inf')))
        best_t = best['threshold']
        best_m = best.get('metrics', {})
        d_s = (best_m.get('sharpe', 0) - b_sharpe) / abs(b_sharpe) * 100 if b_sharpe else 0.0
        d_c = best_m.get('calmar', 0) - b_calmar
        d_maxdd = (best_m.get('max_dd', 0) - b_maxdd) * 100

        print(f'  RECOMMENDATION: Adopt threshold={best_t:.2f}')
        print(f'    vs baseline (no overlay):  ΔSharpe={d_s:+.1f}%,  ΔCalmar={d_c:+.4f},  ΔMaxDD={d_maxdd:+.2f}pp')
        print()
        print(f'  NEXT STEP: Update config/crypto_perps_full_rules.yaml:')
        print(f'    use_correlation_shock_overlay: true')
        print(f'    correlation_shock_params:')
        print(f'      threshold: {best_t:.2f}')
        print()
        print('  Then commit with Calmar-peak threshold; update current-work.md and MEMORY.md.')
    else:
        print(
            '  RECOMMENDATION: REJECT — no threshold reduces MaxDD by ≥1pp without losing >1% Sharpe.'
        )
        print('  Keep use_correlation_shock_overlay: false (default).')
        print('  Document in config comment and memory.')
    print()


def main():
    parser = argparse.ArgumentParser(
        description='Sweep correlation shock overlay threshold values.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        '--base-config', type=Path,
        default=Path('config/crypto_perps_full_rules.yaml'),
    )
    parser.add_argument(
        '--data', type=Path,
        default=Path('data/dataset_538registry_6yr_jagged.parquet'),
    )
    parser.add_argument(
        '--outdir', type=Path,
        default=Path('out/corr_shock_sweep'),
    )
    parser.add_argument(
        '--thresholds', type=float, nargs='+',
        default=[0.60, 0.65, 0.70, 0.75, 0.80],
        help='mean_corr threshold values to sweep (default: 0.60 0.65 0.70 0.75 0.80)',
    )
    parser.add_argument(
        '--min-scale', type=float, default=0.5,
        help='Minimum position multiplier at mean_corr=1.0 (default: 0.5)',
    )
    parser.add_argument(
        '--window', type=int, default=30,
        help='Rolling window for variance estimation in days (default: 30)',
    )
    parser.add_argument(
        '--smooth-span', type=int, default=5,
        help='EWM span for smoothing the mean_corr signal (default: 5)',
    )
    parser.add_argument(
        '--skip-existing', action='store_true',
        help='Skip runs where outdir/performance_summary.json already exists',
    )

    args = parser.parse_args()

    if not args.base_config.exists():
        print(f'ERROR: base config not found: {args.base_config}')
        sys.exit(1)
    if not args.data.exists():
        print(f'ERROR: data file not found: {args.data}')
        sys.exit(1)

    args.outdir.mkdir(parents=True, exist_ok=True)

    base_cfg = load_yaml(args.base_config)

    print(f'Base config:    {args.base_config}')
    print(f'Data:           {args.data}')
    print(f'Output dir:     {args.outdir}')
    print(f'Thresholds:     {args.thresholds}')
    print(f'min_scale:      {args.min_scale}')
    print(f'window:         {args.window}d')
    print(f'smooth_span:    {args.smooth_span}')
    print()

    # --- Baseline (no overlay) ---
    baseline_outdir = args.outdir / 'baseline'
    baseline_metrics = {}

    print('─' * 60)
    print('Running: BASELINE (no correlation shock overlay)')

    if args.skip_existing and (baseline_outdir / 'performance_summary.json').exists():
        print('  Skipping baseline — results already exist (--skip-existing)')
        baseline_metrics = load_results(baseline_outdir).get('metrics', {})
    else:
        baseline_cfg = dict(base_cfg)
        baseline_cfg['use_correlation_shock_overlay'] = False

        with tempfile.NamedTemporaryFile(
            mode='w', suffix='.yaml', delete=False, dir=args.outdir
        ) as tmp:
            yaml.dump(baseline_cfg, tmp, default_flow_style=False, sort_keys=False)
            tmp_path = Path(tmp.name)

        try:
            rc = run_backtest(tmp_path, args.data, baseline_outdir)
        finally:
            tmp_path.unlink(missing_ok=True)

        if rc != 0:
            print(f'  WARNING: baseline backtest returned non-zero exit code {rc}')

        baseline_metrics = load_results(baseline_outdir).get('metrics', {})

    m = baseline_metrics
    print(
        f'  Sharpe={m.get("sharpe", float("nan")):.4f}  '
        f'Calmar={m.get("calmar", float("nan")):.4f}  '
        f'CAGR={m.get("cagr", 0) * 100:.2f}%  '
        f'MaxDD={m.get("max_dd", 0) * 100:.2f}%'
    )

    # --- Threshold sweep ---
    results = []

    for threshold in args.thresholds:
        tag = f't{threshold:.2f}'.replace('.', 'p')
        run_outdir = args.outdir / tag

        print(f'{"─" * 60}')
        print(f'Running: threshold={threshold:.2f}  →  {run_outdir}')

        if args.skip_existing and (run_outdir / 'performance_summary.json').exists():
            print('  Skipping — results already exist (--skip-existing)')
            r = load_results(run_outdir)
            r['threshold'] = threshold
            results.append(r)
            continue

        cfg = dict(base_cfg)
        cfg['use_correlation_shock_overlay'] = True
        cfg['correlation_shock_params'] = {
            'window': args.window,
            'threshold': float(threshold),
            'min_scale': float(args.min_scale),
            'smooth_span': args.smooth_span,
        }

        with tempfile.NamedTemporaryFile(
            mode='w', suffix='.yaml', delete=False, dir=args.outdir
        ) as tmp:
            yaml.dump(cfg, tmp, default_flow_style=False, sort_keys=False)
            tmp_path = Path(tmp.name)

        try:
            rc = run_backtest(tmp_path, args.data, run_outdir)
        finally:
            tmp_path.unlink(missing_ok=True)

        if rc != 0:
            print(f'  WARNING: backtest returned non-zero exit code {rc}')

        r = load_results(run_outdir)
        r['threshold'] = threshold
        results.append(r)

        m = r.get('metrics', {})
        print(
            f'  Sharpe={m.get("sharpe", float("nan")):.4f}  '
            f'Calmar={m.get("calmar", float("nan")):.4f}  '
            f'CAGR={m.get("cagr", 0) * 100:.2f}%  '
            f'MaxDD={m.get("max_dd", 0) * 100:.2f}%'
        )

    print_comparison(results, baseline_metrics)

    summary_path = args.outdir / 'sweep_summary.json'
    with open(summary_path, 'w') as f:
        json.dump(
            {'baseline': {'metrics': baseline_metrics}, 'sweep': results},
            f, indent=2, default=str,
        )
    print(f'Full results saved: {summary_path}')


if __name__ == '__main__':
    main()
