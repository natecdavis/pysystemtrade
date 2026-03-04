#!/usr/bin/env python3
"""
Sweep downside beta position scalar parameters.

Downside beta β_down measures how much an instrument amplifies crypto bear-market
crashes relative to the cross-sectional market factor (median return).  The scalar
is cross-sectionally ranked per date and applied as an always-on per-instrument
multiplicative overlay (highest β_down → min_scale, lowest → 1.0).

Adoption criteria:
  - ΔSharpe >= +1% relative vs baseline (>= 1.091 given baseline 1.08)
  - ΔMaxDD < +3pp absolute worsening
  - Calmar non-monotone across the grid (genuine signal, not pure leverage reduction)
  - avg_positions unchanged (overlay scales SIZE, not universe membership)

Default grid: 3 windows × 3 min_scales = 9 combos + 1 baseline = 10 runs (~50 min).

Usage:
    python scripts/sweep_downside_beta.py \\
        --base-config config/crypto_perps_full_rules.yaml \\
        --data data/dataset_538registry_6yr_jagged.parquet \\
        --outdir out/downside_beta_sweep

    # Custom grid
    python scripts/sweep_downside_beta.py \\
        --outdir out/downside_beta_sweep \\
        --windows 40 63 90 \\
        --min-scales 0.3 0.5 0.7

    # Skip already-completed runs
    python scripts/sweep_downside_beta.py --outdir out/downside_beta_sweep --skip-existing
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
    print('=' * 145)
    print('DOWNSIDE BETA SCALAR SWEEP — RESULTS (direction-aware)')
    print('=' * 145)

    hdr = (
        f'{"Window":>8}  {"MinScale":>8}  {"Sharpe":>8}  {"Calmar":>8}  '
        f'{"CAGR":>8}  {"Vol":>8}  {"MaxDD":>8}  {"Crisis Ret":>10}  '
        f'{"AvgPos":>7}  {"ΔSharpe":>8}  {"ΔCalmar":>8}  {"ΔMaxDD":>8}  {"ΔCrisis":>9}'
    )
    print(hdr)
    print('─' * 145)

    baseline = None
    for r in results:
        m = r.get('metrics', {})
        window   = r.get('window', 'N/A')
        scale    = r.get('min_scale', 'N/A')
        label    = r.get('label', '')
        sharpe   = m.get('sharpe',        float('nan'))
        calmar   = m.get('calmar',        float('nan'))
        cagr     = m.get('cagr',          float('nan'))
        vol      = m.get('ann_vol',       float('nan'))
        maxdd    = m.get('max_dd',        float('nan'))
        crisis   = m.get('crisis_return', float('nan'))
        avg_pos  = m.get('avg_positions', float('nan'))

        if baseline is None:
            baseline = r
            d_sharpe = 0.0
            d_calmar = 0.0
            d_maxdd  = 0.0
            d_crisis = 0.0
        else:
            b_m = baseline.get('metrics', {})
            d_sharpe = sharpe - b_m.get('sharpe', float('nan'))
            d_calmar = calmar - b_m.get('calmar', float('nan'))
            d_maxdd  = (maxdd - b_m.get('max_dd', float('nan'))) * 100  # pp
            d_crisis = (crisis - b_m.get('crisis_return', float('nan'))) * 100  # pp

        tag = f'  <- {label}' if label else ''
        avg_pos_str = f'{avg_pos:.1f}' if avg_pos == avg_pos else '  N/A'
        win_str = str(window) if window != 'N/A' else '  N/A'
        scl_str = f'{scale:.2f}' if scale != 'N/A' else '  N/A'
        print(
            f'{win_str:>8}  '
            f'{scl_str:>8}  '
            f'{sharpe:>8.4f}  '
            f'{calmar:>8.4f}  '
            f'{cagr*100:>7.2f}%  '
            f'{vol*100:>7.2f}%  '
            f'{maxdd*100:>7.2f}%  '
            f'{crisis*100:>10.2f}%  '
            f'{avg_pos_str:>7}  '
            f'{d_sharpe:>+8.4f}  '
            f'{d_calmar:>+8.4f}  '
            f'{d_maxdd:>+7.2f}pp  '
            f'{d_crisis:>+7.2f}pp'
            f'{tag}'
        )

    print('─' * 145)
    print()

    # Adoption criteria check
    baseline_m = baseline.get('metrics', {}) if baseline else {}
    b_sharpe  = baseline_m.get('sharpe',        float('nan'))
    b_maxdd   = baseline_m.get('max_dd',        float('nan'))
    b_crisis  = baseline_m.get('crisis_return', float('nan'))
    b_avg_pos = baseline_m.get('avg_positions', float('nan'))

    print('ADOPTION CRITERIA CHECK')
    print(
        '  ΔSharpe >= +1% relative  |  ΔMaxDD > -3pp (no worsening)  |  '
        'ΔCrisis > -5pp  |  Calmar non-monotone  |  avg_positions unchanged'
    )
    print()

    candidates = []
    calmar_values = []
    for r in results[1:]:  # skip baseline
        m = r.get('metrics', {})
        sharpe   = m.get('sharpe',        float('nan'))
        maxdd    = m.get('max_dd',        float('nan'))
        calmar   = m.get('calmar',        float('nan'))
        crisis   = m.get('crisis_return', float('nan'))
        avg_pos  = m.get('avg_positions', float('nan'))
        window   = r.get('window', 0)
        scale    = r.get('min_scale', 0.0)

        d_sharpe_pct = (sharpe - b_sharpe) / abs(b_sharpe) * 100 if b_sharpe else float('nan')
        # MaxDD is negative; d_maxdd_pp is POSITIVE when MaxDD improves (less negative).
        # Criterion: don't worsen MaxDD by more than 3pp → d_maxdd_pp > -3.0
        d_maxdd_pp   = (maxdd  - b_maxdd)  * 100
        d_crisis_pp  = (crisis - b_crisis) * 100
        d_pos_pct    = (avg_pos - b_avg_pos) / abs(b_avg_pos) * 100 if b_avg_pos else float('nan')

        sharpe_ok = d_sharpe_pct >= 1.0
        maxdd_ok  = d_maxdd_pp   > -3.0   # improvement is positive; worsening is negative
        crisis_ok = d_crisis_pp  > -5.0   # preserve at least 95% of crisis return
        # avg_positions should be essentially unchanged (±5%) — overlay scales SIZE not universe
        pos_ok    = abs(d_pos_pct) <= 5.0 if d_pos_pct == d_pos_pct else True
        calmar_values.append(calmar)

        all_ok = sharpe_ok and maxdd_ok and crisis_ok and pos_ok
        status = '✓ PASS' if all_ok else '✗ FAIL'
        if all_ok:
            candidates.append(r)

        print(
            f'  W={window:>3}d  S={scale:.2f}:  '
            f'ΔSharpe={d_sharpe_pct:+.1f}% {("✓" if sharpe_ok else "✗")}  '
            f'ΔMaxDD={d_maxdd_pp:+.1f}pp {("✓" if maxdd_ok else "✗")}  '
            f'ΔCrisis={d_crisis_pp:+.1f}pp {("✓" if crisis_ok else "✗")}  '
            f'ΔPos={d_pos_pct:+.1f}% {("✓" if pos_ok else "✗")}  '
            f'Calmar={calmar:.4f}  '
            f'-> {status}'
        )

    # Check Calmar non-monotonicity
    print()
    if len(calmar_values) >= 3:
        is_mono_dec = all(calmar_values[i] >= calmar_values[i+1] for i in range(len(calmar_values)-1))
        is_mono_inc = all(calmar_values[i] <= calmar_values[i+1] for i in range(len(calmar_values)-1))
        if is_mono_dec or is_mono_inc:
            print('  ⚠ Calmar is monotone — may be pure leverage reduction, not genuine signal')
        else:
            print('  ✓ Calmar is non-monotone — evidence of genuine downside beta signal')

    print()
    if candidates:
        best = max(candidates, key=lambda r: r.get('metrics', {}).get('sharpe', float('-inf')))
        print(
            f'  RECOMMENDATION: Use window={best["window"]}d, min_scale={best["min_scale"]:.2f}'
        )
        print(f'  (highest Sharpe among combinations that pass all adoption criteria)')
        print(f'  Set use_downside_beta_overlay: true and update downside_beta_params in config.')
    else:
        print(
            '  RECOMMENDATION: No parameter combination passes all adoption criteria '
            '— keep use_downside_beta_overlay: false'
        )
    print()


def main():
    parser = argparse.ArgumentParser(
        description='Sweep downside beta position scalar parameters.',
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
        default=Path('out/downside_beta_sweep'),
    )
    parser.add_argument(
        '--windows', type=int, nargs='+',
        default=[40, 63, 90],
        help='Rolling window values (days) to test (default: 40 63 90)',
    )
    parser.add_argument(
        '--min-scales', type=float, nargs='+',
        default=[0.3, 0.5, 0.7],
        help='min_scale values to test (default: 0.3 0.5 0.7)',
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

    combos = [(w, s) for w in args.windows for s in args.min_scales]
    n_runs = 1 + len(combos)
    print(f'Base config:  {args.base_config}')
    print(f'Data:         {args.data}')
    print(f'Output dir:   {args.outdir}')
    print(f'Windows:      {args.windows}d')
    print(f'Min scales:   {args.min_scales}')
    print(f'Total runs:   {n_runs} (1 baseline + {len(combos)} grid combos)')
    print()

    results = []

    # ── Baseline: use_downside_beta_overlay=false ─────────────────────────────
    print(f'{"─"*60}')
    print('Running baseline (use_downside_beta_overlay=false)')
    baseline_outdir = args.outdir / 'baseline'

    if args.skip_existing and (baseline_outdir / 'performance_summary.json').exists():
        print('  SKIP (already done)')
    else:
        cfg = copy.deepcopy(base_cfg)
        cfg['use_downside_beta_overlay'] = False
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
    result['window'] = 'N/A'
    result['min_scale'] = 'N/A'
    result['label'] = 'baseline'
    results.append(result)

    # ── Parameter grid ────────────────────────────────────────────────────────
    for window, min_scale in combos:
        tag = f'w{window}_s{int(min_scale*100)}'
        run_outdir = args.outdir / tag

        print(f'{"─"*60}')
        print(f'Running: window={window}d, min_scale={min_scale:.2f}  ->  {run_outdir}')

        if args.skip_existing and (run_outdir / 'performance_summary.json').exists():
            print('  SKIP (already done)')
        else:
            cfg = copy.deepcopy(base_cfg)
            cfg['use_downside_beta_overlay'] = True
            if 'downside_beta_params' not in cfg or cfg['downside_beta_params'] is None:
                cfg['downside_beta_params'] = {}
            cfg['downside_beta_params']['window'] = window
            cfg['downside_beta_params']['min_periods'] = 20
            cfg['downside_beta_params']['min_scale'] = min_scale
            cfg['downside_beta_params']['direction_aware'] = True  # only penalise longs
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
        result['window'] = window
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
