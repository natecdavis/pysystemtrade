#!/usr/bin/env python3
"""
Sweep IVOL cap percentile values.

Idiosyncratic volatility (IVOL) — residual vol after removing the common market
return — predicts underperformance in crypto (lottery-preference effect). This
sweep tests different percentile thresholds for excluding high-IVOL instruments
from the universe.

Default grid: 6 percentile values + 1 baseline = 7 runs total (~35 min).

Adoption criteria:
  - Sharpe improvement >= +1% relative vs baseline
  - MaxDD worsening < +3pp absolute
  - Calmar non-monotone across percentile grid (proves genuine signal, not pure
    leverage reduction from excluding more instruments)
  - avg_positions decrease should be modest (10-30%) — large drop means cutting
    good instruments

Usage:
    python scripts/sweep_ivol_cap.py \\
        --base-config config/crypto_perps_full_rules.yaml \\
        --data data/dataset_538registry_6yr_jagged.parquet \\
        --outdir out/ivol_sweep

    # Custom percentile grid
    python scripts/sweep_ivol_cap.py \\
        --outdir out/ivol_sweep \\
        --percentiles 50 60 70 75 80 90

    # Skip already-completed runs
    python scripts/sweep_ivol_cap.py --outdir out/ivol_sweep --skip-existing
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
    print('=' * 120)
    print('IVOL CAP PERCENTILE SWEEP — RESULTS')
    print('=' * 120)

    hdr = (
        f'{"Percentile":>10}  {"Sharpe":>8}  {"Calmar":>8}  {"CAGR":>8}  '
        f'{"Vol":>8}  {"MaxDD":>8}  {"Crisis Ret":>10}  {"AvgPos":>7}  '
        f'{"ΔSharpe":>8}  {"ΔCalmar":>8}  {"ΔMaxDD":>8}'
    )
    print(hdr)
    print('─' * 120)

    baseline = None
    for r in results:
        m = r.get('metrics', {})
        percentile = r.get('ivol_cap_percentile', 'N/A')
        label = r.get('label', '')
        sharpe  = m.get('sharpe',        float('nan'))
        calmar  = m.get('calmar',        float('nan'))
        cagr    = m.get('cagr',          float('nan'))
        vol     = m.get('ann_vol',       float('nan'))
        maxdd   = m.get('max_dd',        float('nan'))
        crisis  = m.get('crisis_return', float('nan'))
        avg_pos = m.get('avg_positions', float('nan'))

        if baseline is None:
            baseline = r
            d_sharpe = 0.0
            d_calmar = 0.0
            d_maxdd = 0.0
        else:
            b_m = baseline.get('metrics', {})
            d_sharpe = sharpe - b_m.get('sharpe', float('nan'))
            d_calmar = calmar - b_m.get('calmar', float('nan'))
            d_maxdd  = (maxdd - b_m.get('max_dd', float('nan'))) * 100  # in pp

        tag = f' <- {label}' if label else ''
        avg_pos_str = f'{avg_pos:.1f}' if not (avg_pos != avg_pos) else '  N/A'
        print(
            f'{str(percentile):>10}  '
            f'{sharpe:>8.4f}  '
            f'{calmar:>8.4f}  '
            f'{cagr*100:>7.2f}%  '
            f'{vol*100:>7.2f}%  '
            f'{maxdd*100:>7.2f}%  '
            f'{crisis*100:>10.2f}%  '
            f'{avg_pos_str:>7}  '
            f'{d_sharpe:>+8.4f}  '
            f'{d_calmar:>+8.4f}  '
            f'{d_maxdd:>+7.2f}pp'
            f'{tag}'
        )

    print('─' * 120)
    print()

    # Adoption criteria check
    baseline_m = baseline.get('metrics', {}) if baseline else {}
    b_sharpe = baseline_m.get('sharpe', float('nan'))
    b_maxdd  = baseline_m.get('max_dd', float('nan'))
    b_avg_pos = baseline_m.get('avg_positions', float('nan'))

    print('ADOPTION CRITERIA CHECK')
    print(
        '  Sharpe >= +1% relative  |  MaxDD worsening < +3pp absolute  |  '
        'Calmar non-monotone  |  avg_positions decrease 10-30%'
    )
    print()
    candidates = []
    calmar_values = []
    for r in results[1:]:  # skip baseline
        m = r.get('metrics', {})
        sharpe   = m.get('sharpe',        float('nan'))
        maxdd    = m.get('max_dd',        float('nan'))
        calmar   = m.get('calmar',        float('nan'))
        avg_pos  = m.get('avg_positions', float('nan'))
        d_sharpe_pct = (sharpe - b_sharpe) / abs(b_sharpe) * 100 if b_sharpe else float('nan')
        d_maxdd_pp   = (maxdd - b_maxdd) * 100
        d_pos_pct    = (avg_pos - b_avg_pos) / abs(b_avg_pos) * 100 if b_avg_pos else float('nan')

        sharpe_ok = d_sharpe_pct >= 1.0
        maxdd_ok  = d_maxdd_pp   < 3.0
        pos_ok    = -30.0 <= d_pos_pct <= 0.0  # must decrease but not by more than 30%
        calmar_values.append(calmar)

        all_ok = sharpe_ok and maxdd_ok and pos_ok
        status = '✓ PASS' if all_ok else '✗ FAIL'
        if all_ok:
            candidates.append(r)

        print(
            f'  pct={r["ivol_cap_percentile"]:>3}:  '
            f'ΔSharpe={d_sharpe_pct:+.1f}% {("✓" if sharpe_ok else "✗")}  '
            f'ΔMaxDD={d_maxdd_pp:+.1f}pp {("✓" if maxdd_ok else "✗")}  '
            f'ΔPos={d_pos_pct:+.1f}% {("✓" if pos_ok else "✗")}  '
            f'Calmar={calmar:.4f}  '
            f'-> {status}'
        )

    # Check Calmar non-monotonicity
    print()
    if len(calmar_values) >= 3:
        is_monotone_dec = all(calmar_values[i] >= calmar_values[i+1] for i in range(len(calmar_values)-1))
        is_monotone_inc = all(calmar_values[i] <= calmar_values[i+1] for i in range(len(calmar_values)-1))
        if is_monotone_dec or is_monotone_inc:
            print('  ⚠ Calmar is monotone — may be pure instrument-count reduction, not genuine signal')
        else:
            print('  ✓ Calmar is non-monotone — evidence of genuine IVOL signal')

    print()
    if candidates:
        best = max(candidates, key=lambda r: r.get('metrics', {}).get('sharpe', float('-inf')))
        print(
            f'  RECOMMENDATION: Use ivol_cap_percentile={best["ivol_cap_percentile"]}'
        )
        print(f'  (highest Sharpe among combinations that pass all criteria)')
        print(f'  Set ivol_cap_enabled: true in config after validation.')
    else:
        print(
            '  RECOMMENDATION: No parameter combination passes all adoption criteria '
            '— keep ivol_cap_enabled: false'
        )
    print()


def main():
    parser = argparse.ArgumentParser(
        description='Sweep IVOL cap percentile values.',
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
        default=Path('out/ivol_sweep'),
    )
    parser.add_argument(
        '--percentiles', type=int, nargs='+',
        default=[50, 60, 70, 75, 80, 90],
        help='ivol_cap_percentile values to test (default: 50 60 70 75 80 90)',
    )
    parser.add_argument(
        '--ivol-window', type=int, default=35,
        help='Rolling window for IVOL calculation (days, default: 35)',
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

    n_runs = 1 + len(args.percentiles)
    print(f'Base config:  {args.base_config}')
    print(f'Data:         {args.data}')
    print(f'Output dir:   {args.outdir}')
    print(f'Percentiles:  {args.percentiles}')
    print(f'IVOL window:  {args.ivol_window}d')
    print(f'Total runs:   {n_runs} (1 baseline + {n_runs - 1} parameter combinations)')
    print()
    print('Interpretation of percentile:')
    for p in args.percentiles:
        print(f'  pct={p:>3}: exclude top {100 - p}% by IVOL (keep bottom {p}% of instruments)')
    print()

    results = []

    # ── Baseline: ivol_cap_enabled=false ─────────────────────────────────────
    print(f'{"─"*60}')
    print('Running baseline (ivol_cap_enabled=false)')
    baseline_outdir = args.outdir / 'baseline'

    if args.skip_existing and (baseline_outdir / 'performance_summary.json').exists():
        print('  SKIP (already done)')
    else:
        cfg = copy.deepcopy(base_cfg)
        if 'dynamic_universe' not in cfg:
            cfg['dynamic_universe'] = {}
        cfg['dynamic_universe']['ivol_cap_enabled'] = False
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
    result['ivol_cap_percentile'] = 'N/A'
    result['label'] = 'baseline'
    results.append(result)

    # ── Parameter grid ────────────────────────────────────────────────────────
    for percentile in args.percentiles:
        tag = f'pct{percentile}'
        run_outdir = args.outdir / tag

        print(f'{"─"*60}')
        print(f'Running: ivol_cap_percentile={percentile}  ->  {run_outdir}')

        if args.skip_existing and (run_outdir / 'performance_summary.json').exists():
            print('  SKIP (already done)')
        else:
            cfg = copy.deepcopy(base_cfg)
            if 'dynamic_universe' not in cfg:
                cfg['dynamic_universe'] = {}
            cfg['dynamic_universe']['ivol_cap_enabled'] = True
            cfg['dynamic_universe']['ivol_cap_percentile'] = percentile
            cfg['dynamic_universe']['ivol_window'] = args.ivol_window
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
        result['ivol_cap_percentile'] = percentile
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
