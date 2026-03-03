#!/usr/bin/env python3
"""
Sweep Fear & Greed overlay parameters (greed_threshold × min_scale grid).

Runs a full backtest for each combination, collects metrics, and prints a
comparison table including ΔSharpe, ΔCalmar, and ΔCrisis.

Default grid: 3 thresholds × 2 scales = 6 runs + 1 baseline = 7 runs total (~35 min).

Usage:
    python scripts/sweep_fg_params.py \\
        --base-config config/crypto_perps_full_rules.yaml \\
        --data data/dataset_538registry_6yr_jagged.parquet \\
        --outdir out/fg_sweep

    # With custom grid
    python scripts/sweep_fg_params.py \\
        --outdir out/fg_sweep \\
        --thresholds 70 75 80 \\
        --scales 0.5 0.7

    # Skip already-completed runs
    python scripts/sweep_fg_params.py --outdir out/fg_sweep --skip-existing
"""

import argparse
import copy
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


def write_yaml(data: dict, path: Path) -> None:
    with open(path, 'w') as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)


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


def print_comparison(results: list) -> None:
    """Print formatted comparison table."""
    print()
    print('=' * 110)
    print('FEAR & GREED OVERLAY SWEEP — RESULTS')
    print('=' * 110)

    hdr = (
        f'{"Threshold":>10}  {"MinScale":>8}  {"Sharpe":>8}  {"Calmar":>8}  {"CAGR":>8}  '
        f'{"Vol":>8}  {"MaxDD":>8}  {"Crisis Ret":>10}  {"ΔSharpe":>8}  {"ΔCalmar":>8}'
    )
    print(hdr)
    print('─' * 110)

    baseline = None
    for r in results:
        m = r.get('metrics', {})
        threshold = r['greed_threshold']
        min_scale = r['min_scale']
        label = r.get('label', '')
        sharpe  = m.get('sharpe',        float('nan'))
        calmar  = m.get('calmar',        float('nan'))
        cagr    = m.get('cagr',          float('nan'))
        vol     = m.get('ann_vol',       float('nan'))
        maxdd   = m.get('max_dd',        float('nan'))
        crisis  = m.get('crisis_return', float('nan'))

        if baseline is None:
            baseline = r
            d_sharpe = 0.0
            d_calmar = 0.0
        else:
            b_m = baseline.get('metrics', {})
            d_sharpe = sharpe - b_m.get('sharpe', float('nan'))
            d_calmar = calmar - b_m.get('calmar', float('nan'))

        tag = f' ← {label}' if label else ''
        print(
            f'{threshold:>10}  '
            f'{min_scale:>8.2f}  '
            f'{sharpe:>8.4f}  '
            f'{calmar:>8.4f}  '
            f'{cagr*100:>7.2f}%  '
            f'{vol*100:>7.2f}%  '
            f'{maxdd*100:>7.2f}%  '
            f'{crisis*100:>10.2f}%  '
            f'{d_sharpe:>+8.4f}  '
            f'{d_calmar:>+8.4f}'
            f'{tag}'
        )

    print('─' * 110)
    print()

    # Adoption criteria check
    baseline_m = baseline.get('metrics', {}) if baseline else {}
    b_sharpe = baseline_m.get('sharpe', float('nan'))
    b_crisis = baseline_m.get('crisis_return', float('nan'))
    b_maxdd  = baseline_m.get('max_dd', float('nan'))
    b_calmar = baseline_m.get('calmar', float('nan'))

    print('ADOPTION CRITERIA CHECK')
    print('  Sharpe improvement ≥ +1%  |  MaxDD worsening < +3pp absolute  |  Calmar non-monotone (signal, not pure leverage reduction)')
    print()
    candidates = []
    calmar_values = []  # to check non-monotonicity
    for r in results[1:]:  # skip baseline
        m = r.get('metrics', {})
        sharpe  = m.get('sharpe',        float('nan'))
        crisis  = m.get('crisis_return', float('nan'))
        maxdd   = m.get('max_dd',        float('nan'))
        calmar  = m.get('calmar',        float('nan'))
        d_sharpe_pct = (sharpe - b_sharpe) / abs(b_sharpe) * 100 if b_sharpe else float('nan')
        d_maxdd_pp   = (maxdd - b_maxdd) * 100

        sharpe_ok = d_sharpe_pct >= 1.0
        maxdd_ok  = d_maxdd_pp   < 3.0
        calmar_values.append(calmar)

        status = '✓ PASS' if (sharpe_ok and maxdd_ok) else '✗ FAIL'
        if sharpe_ok and maxdd_ok:
            candidates.append(r)

        print(
            f'  th={r["greed_threshold"]}, scale={r["min_scale"]:.2f}:  '
            f'ΔSharpe={d_sharpe_pct:+.1f}% {("✓" if sharpe_ok else "✗")}  '
            f'ΔMaxDD={d_maxdd_pp:+.1f}pp {("✓" if maxdd_ok else "✗")}  '
            f'Calmar={calmar:.4f}  '
            f'→ {status}'
        )

    # Check Calmar non-monotonicity (peaks somewhere, not just monotone decline)
    print()
    if len(calmar_values) >= 3:
        is_monotone = all(calmar_values[i] >= calmar_values[i+1] for i in range(len(calmar_values)-1))
        if is_monotone:
            print('  ⚠ Calmar is monotonically decreasing — may be pure leverage reduction, not genuine signal')
        else:
            print('  ✓ Calmar is non-monotone — evidence of genuine signal (not just leverage reduction)')

    print()
    if candidates:
        best = max(candidates, key=lambda r: r.get('metrics', {}).get('sharpe', float('-inf')))
        print(
            f'  RECOMMENDATION: Use greed_threshold={best["greed_threshold"]}, '
            f'min_scale={best["min_scale"]:.2f}'
        )
        print(f'  (highest Sharpe among combinations that pass all criteria)')
        print(f'  Set use_fg_overlay: true in config after validation.')
    else:
        print('  RECOMMENDATION: No parameter combination passes all adoption criteria — keep use_fg_overlay: false')
    print()


def main():
    parser = argparse.ArgumentParser(
        description='Sweep F&G overlay parameters.',
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
        default=Path('out/fg_sweep'),
    )
    parser.add_argument(
        '--thresholds', type=int, nargs='+',
        default=[70, 75, 80],
        help='greed_threshold values to test (default: 70 75 80)',
    )
    parser.add_argument(
        '--scales', type=float, nargs='+',
        default=[0.5, 0.7],
        help='min_scale values to test (default: 0.5 0.7)',
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

    # Check F&G data is present
    fg_candidate = args.data.parent / 'fg_index.parquet'
    if not fg_candidate.exists():
        print(f'ERROR: fg_index.parquet not found at {fg_candidate}')
        print('  Run: python scripts/download_fg_index.py')
        sys.exit(1)

    args.outdir.mkdir(parents=True, exist_ok=True)

    base_cfg = load_yaml(args.base_config)

    n_runs = 1 + len(args.thresholds) * len(args.scales)
    print(f'Base config:  {args.base_config}')
    print(f'Data:         {args.data}')
    print(f'Output dir:   {args.outdir}')
    print(f'Thresholds:   {args.thresholds}')
    print(f'Min scales:   {args.scales}')
    print(f'Total runs:   {n_runs} (1 baseline + {n_runs - 1} parameter combinations)')
    print()

    results = []

    # ── Baseline: use_fg_overlay=false ───────────────────────────────────────
    print(f'{"─"*60}')
    print('Running baseline (use_fg_overlay=false)')
    baseline_outdir = args.outdir / 'baseline'

    if args.skip_existing and (baseline_outdir / 'performance_summary.json').exists():
        print('  SKIP (already done)')
    else:
        cfg = copy.deepcopy(base_cfg)
        cfg['use_fg_overlay'] = False
        with tempfile.NamedTemporaryFile(
            mode='w', suffix='.yaml', delete=False, dir=args.outdir
        ) as tf:
            yaml.dump(cfg, tf, default_flow_style=False, sort_keys=False)
            tmp_cfg = Path(tf.name)
        rc = run_backtest(tmp_cfg, args.data, baseline_outdir)
        tmp_cfg.unlink(missing_ok=True)
        if rc != 0:
            print(f'  WARNING: baseline backtest returned code {rc}')

    result = load_results(baseline_outdir)
    result['greed_threshold'] = 'N/A'
    result['min_scale'] = float('nan')
    result['label'] = 'baseline'
    results.append(result)

    # ── Parameter grid ────────────────────────────────────────────────────────
    for threshold in args.thresholds:
        for min_scale in args.scales:
            tag = f'th{threshold}_scale{int(min_scale*100)}'
            run_outdir = args.outdir / tag

            print(f'{"─"*60}')
            print(f'Running: greed_threshold={threshold}, min_scale={min_scale:.2f}  →  {run_outdir}')

            if args.skip_existing and (run_outdir / 'performance_summary.json').exists():
                print('  SKIP (already done)')
            else:
                cfg = copy.deepcopy(base_cfg)
                cfg['use_fg_overlay'] = True
                cfg['fg_overlay_params'] = {
                    'greed_threshold': threshold,
                    'fear_threshold': 25,
                    'min_scale': min_scale,
                }
                with tempfile.NamedTemporaryFile(
                    mode='w', suffix='.yaml', delete=False, dir=args.outdir
                ) as tf:
                    yaml.dump(cfg, tf, default_flow_style=False, sort_keys=False)
                    tmp_cfg = Path(tf.name)
                rc = run_backtest(tmp_cfg, args.data, run_outdir)
                tmp_cfg.unlink(missing_ok=True)
                if rc != 0:
                    print(f'  WARNING: backtest returned code {rc}')

            result = load_results(run_outdir)
            result['greed_threshold'] = threshold
            result['min_scale'] = min_scale
            result['label'] = ''
            results.append(result)

    # ── Print comparison ──────────────────────────────────────────────────────
    print_comparison(results)

    # Save results summary
    summary_path = args.outdir / 'sweep_summary.json'
    with open(summary_path, 'w') as f:
        json.dump(results, f, indent=2, default=str)
    print(f'Results saved to: {summary_path}')


if __name__ == '__main__':
    main()
