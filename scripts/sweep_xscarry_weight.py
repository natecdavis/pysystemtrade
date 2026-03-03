#!/usr/bin/env python3
"""
Sweep xscarry_weight values and compare backtest performance.

Runs a full backtest for each weight, collects metrics, and prints a
comparison table including Calmar ratio.

Usage:
    python scripts/sweep_xscarry_weight.py \\
        --base-config config/crypto_perps_full_rules.yaml \\
        --data data/dataset_538registry_6yr_jagged.parquet \\
        --outdir out/xscarry_sweep \\
        --weights 0 0.2 0.5 1.0 2.0 3.0
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


def print_comparison(results: list[dict]) -> None:
    """Print formatted comparison table."""
    print()
    print('=' * 90)
    print('XSCARRY WEIGHT SWEEP — RESULTS')
    print('=' * 90)

    hdr = (
        f'{"Weight":>8}  {"Sharpe":>8}  {"Calmar":>8}  {"CAGR":>8}  '
        f'{"Vol":>8}  {"MaxDD":>8}  {"Crisis Ret":>10}  {"ΔSharpe":>8}  {"ΔCalmar":>8}'
    )
    print(hdr)
    print('─' * 90)

    baseline = None
    for r in results:
        m = r.get('metrics', {})
        weight   = r['weight']
        sharpe   = m.get('sharpe',         float('nan'))
        calmar   = m.get('calmar',         float('nan'))
        cagr     = m.get('cagr',           float('nan'))
        vol      = m.get('ann_vol',        float('nan'))
        maxdd    = m.get('max_dd',         float('nan'))
        crisis   = m.get('crisis_return',  float('nan'))

        if baseline is None:
            baseline = r
            d_sharpe = 0.0
            d_calmar = 0.0
        else:
            b_m = baseline.get('metrics', {})
            d_sharpe = sharpe - b_m.get('sharpe', float('nan'))
            d_calmar = calmar - b_m.get('calmar', float('nan'))

        tag = ' ← baseline' if baseline is r else ''
        print(
            f'{weight:>8.2f}  '
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

    print('─' * 90)
    print()

    # Adoption check
    baseline_m = baseline.get('metrics', {}) if baseline else {}
    b_sharpe = baseline_m.get('sharpe', float('nan'))
    b_crisis = baseline_m.get('crisis_return', float('nan'))
    b_maxdd  = baseline_m.get('max_dd', float('nan'))
    b_calmar = baseline_m.get('calmar', float('nan'))

    print('ADOPTION CRITERIA CHECK')
    print('  Sharpe improvement ≥ +1%      |  Crisis return drop < 5pp  |  MaxDD < +3pp')
    print('  (also check Calmar does not monotonically fall — ensures signal, not random)')
    print()

    candidates = []
    calmars = []
    for r in results[1:]:  # skip baseline
        m = r.get('metrics', {})
        sharpe  = m.get('sharpe',        float('nan'))
        crisis  = m.get('crisis_return', float('nan'))
        maxdd   = m.get('max_dd',        float('nan'))
        calmar  = m.get('calmar',        float('nan'))
        d_sharpe_pct = (sharpe - b_sharpe) / b_sharpe * 100
        d_crisis_pp  = (crisis - b_crisis) * 100
        d_maxdd_pp   = (maxdd  - b_maxdd)  * 100
        d_calmar     = calmar - b_calmar

        sharpe_ok = d_sharpe_pct >= 1.0
        crisis_ok = d_crisis_pp  > -5.0
        maxdd_ok  = d_maxdd_pp   < 3.0

        calmars.append(calmar)
        status = '✓ ADOPT' if (sharpe_ok and crisis_ok and maxdd_ok) else '✗ REJECT'
        if sharpe_ok and crisis_ok and maxdd_ok:
            candidates.append(r)

        print(
            f'  weight={r["weight"]:.2f}:  '
            f'ΔSharpe={d_sharpe_pct:+.1f}% {("✓" if sharpe_ok else "✗"):1}  '
            f'ΔCrisis={d_crisis_pp:+.1f}pp {("✓" if crisis_ok else "✗"):1}  '
            f'ΔMaxDD={d_maxdd_pp:+.1f}pp {("✓" if maxdd_ok else "✗"):1}  '
            f'ΔCalmar={d_calmar:+.4f}  '
            f'→ {status}'
        )

    # Check if Calmar is monotonically falling (bad sign — just adding leverage)
    print()
    if len(calmars) >= 2:
        monotone_fall = all(calmars[i] > calmars[i+1] for i in range(len(calmars)-1))
        if monotone_fall:
            print('  ⚠ WARNING: Calmar ratio falls monotonically with weight — signal may be spurious leverage')
        else:
            print('  ✓ Calmar is non-monotone — suggests genuine signal (not pure leverage)')

    print()
    if candidates:
        best = max(candidates, key=lambda r: r.get('metrics', {}).get('sharpe', float('-inf')))
        print(f'  RECOMMENDATION: Use xscarry_weight={best["weight"]:.2f}')
        print(f'  (highest Sharpe among candidates that pass all criteria)')
        print()
        print(f'  NEXT STEP: If adopted, sweep lookbacks [7, 14, 30, 60] at weight={best["weight"]:.2f}:')
        print(f'    python scripts/sweep_xscarry_weight.py \\')
        print(f'      --base-config config/crypto_perps_full_rules.yaml \\')
        print(f'      --data data/dataset_538registry_6yr_jagged.parquet \\')
        print(f'      --outdir out/xscarry_lookback_sweep \\')
        print(f'      --weights {best["weight"]:.2f} \\')
        print(f'      --lookbacks 7 14 30 60')
    else:
        print('  RECOMMENDATION: No weight passes all adoption criteria — keep xscarry_weight=0.0')
    print()


def main():
    parser = argparse.ArgumentParser(
        description='Sweep xscarry_weight values.',
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
        default=Path('out/xscarry_sweep'),
    )
    parser.add_argument(
        '--weights', type=float, nargs='+',
        default=[0.0, 0.2, 0.5, 1.0, 2.0, 3.0],
        help='xscarry_weight values to test (default: 0 0.2 0.5 1.0 2.0 3.0)',
    )
    parser.add_argument(
        '--lookbacks', type=int, nargs='+',
        default=None,
        help='If set, sweep xscarry_lookback values instead of weights (use with single --weights value)',
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

    # Lookback sweep mode: fix weight, sweep lookbacks
    if args.lookbacks is not None:
        if len(args.weights) != 1:
            print('ERROR: --lookbacks requires exactly one --weights value')
            sys.exit(1)
        fixed_weight = args.weights[0]
        print(f'Base config:  {args.base_config}')
        print(f'Data:         {args.data}')
        print(f'Output dir:   {args.outdir}')
        print(f'Fixed weight: {fixed_weight}')
        print(f'Lookbacks:    {args.lookbacks}')
        print()

        results = []
        for lb in args.lookbacks:
            tag = f'w{fixed_weight:.2f}_lb{lb}'.replace('.', 'p')
            run_outdir = args.outdir / tag

            print(f'{"─"*60}')
            print(f'Running: weight={fixed_weight:.2f}, lookback={lb}  →  {run_outdir}')

            if args.skip_existing and (run_outdir / 'performance_summary.json').exists():
                print('  Skipping — results already exist (--skip-existing)')
                r = load_results(run_outdir)
                r['weight'] = fixed_weight
                r['lookback'] = lb
                results.append(r)
                continue

            cfg = dict(base_cfg)
            cfg['xscarry_weight'] = float(fixed_weight)
            cfg['xscarry_lookback'] = int(lb)

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
            r['weight'] = fixed_weight
            r['lookback'] = lb
            results.append(r)

            m = r.get('metrics', {})
            print(
                f'  Sharpe={m.get("sharpe", float("nan")):.4f}  '
                f'Calmar={m.get("calmar", float("nan")):.4f}  '
                f'CAGR={m.get("cagr", 0)*100:.2f}%  '
                f'MaxDD={m.get("max_dd", 0)*100:.2f}%'
            )

        # Print lookback comparison table
        print()
        print('=' * 70)
        print('LOOKBACK SWEEP — RESULTS')
        print('=' * 70)
        hdr = f'{"Lookback":>10}  {"Sharpe":>8}  {"Calmar":>8}  {"CAGR":>8}  {"MaxDD":>8}'
        print(hdr)
        print('─' * 70)
        for r in results:
            m = r.get('metrics', {})
            print(
                f'{r["lookback"]:>10}  '
                f'{m.get("sharpe", float("nan")):>8.4f}  '
                f'{m.get("calmar", float("nan")):>8.4f}  '
                f'{m.get("cagr", 0)*100:>7.2f}%  '
                f'{m.get("max_dd", 0)*100:>7.2f}%'
            )
        print('─' * 70)
        best_lb = max(results, key=lambda r: r.get('metrics', {}).get('sharpe', float('-inf')))
        print(f'\n  RECOMMENDATION: xscarry_lookback={best_lb["lookback"]} (highest Sharpe)')

        summary_path = args.outdir / 'lookback_sweep_summary.json'
        with open(summary_path, 'w') as f:
            json.dump(results, f, indent=2, default=str)
        print(f'\nFull results saved: {summary_path}')
        return

    # Weight sweep mode (default)
    print(f'Base config:  {args.base_config}')
    print(f'Data:         {args.data}')
    print(f'Output dir:   {args.outdir}')
    print(f'Weights:      {args.weights}')
    print()

    results = []

    for weight in args.weights:
        tag = f'w{weight:.2f}'.replace('.', 'p')
        run_outdir = args.outdir / tag

        print(f'{"─"*60}')
        print(f'Running: xscarry_weight = {weight:.2f}  →  {run_outdir}')

        if args.skip_existing and (run_outdir / 'performance_summary.json').exists():
            print('  Skipping — results already exist (--skip-existing)')
            r = load_results(run_outdir)
            r['weight'] = weight
            results.append(r)
            continue

        cfg = dict(base_cfg)
        cfg['xscarry_weight'] = float(weight)

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
        r['weight'] = weight
        results.append(r)

        m = r.get('metrics', {})
        print(
            f'  Sharpe={m.get("sharpe", float("nan")):.4f}  '
            f'Calmar={m.get("calmar", float("nan")):.4f}  '
            f'CAGR={m.get("cagr", 0)*100:.2f}%  '
            f'MaxDD={m.get("max_dd", 0)*100:.2f}%'
        )

    print_comparison(results)

    summary_path = args.outdir / 'xscarry_sweep_summary.json'
    with open(summary_path, 'w') as f:
        json.dump(results, f, indent=2, default=str)
    print(f'Full results saved: {summary_path}')


if __name__ == '__main__':
    main()
