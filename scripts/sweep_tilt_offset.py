#!/usr/bin/env python3
"""
Sweep forecast_tilt_offset values and compare backtest performance.

Runs a full backtest for each offset, collects metrics, and prints a
comparison table including Calmar ratio.

Usage:
    python scripts/sweep_tilt_offset.py \\
        --base-config config/crypto_perps_full_rules.yaml \\
        --data data/dataset_538registry_6yr_jagged.parquet \\
        --outdir out/tilt_sweep \\
        --offsets 0 1 2 3 5
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
    print('TILT OFFSET SWEEP — RESULTS')
    print('=' * 90)

    hdr = (
        f'{"Offset":>8}  {"Sharpe":>8}  {"Calmar":>8}  {"CAGR":>8}  '
        f'{"Vol":>8}  {"MaxDD":>8}  {"Crisis Ret":>10}  {"ΔSharpe":>8}  {"ΔCalmar":>8}'
    )
    print(hdr)
    print('─' * 90)

    baseline = None
    for r in results:
        m = r.get('metrics', {})
        offset   = r['offset']
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
            f'{offset:>+8.1f}  '
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
    print()
    candidates = []
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

        status = '✓ ADOPT' if (sharpe_ok and crisis_ok and maxdd_ok) else '✗ REJECT'
        if sharpe_ok and crisis_ok and maxdd_ok:
            candidates.append(r)

        print(
            f'  offset={r["offset"]:+.1f}:  '
            f'ΔSharpe={d_sharpe_pct:+.1f}% {("✓" if sharpe_ok else "✗"):1}  '
            f'ΔCrisis={d_crisis_pp:+.1f}pp {("✓" if crisis_ok else "✗"):1}  '
            f'ΔMaxDD={d_maxdd_pp:+.1f}pp {("✓" if maxdd_ok else "✗"):1}  '
            f'ΔCalmar={d_calmar:+.4f}  '
            f'→ {status}'
        )

    print()
    if candidates:
        best = max(candidates, key=lambda r: r.get('metrics', {}).get('sharpe', float('-inf')))
        print(f'  RECOMMENDATION: Use forecast_tilt_offset={best["offset"]:+.1f}')
        print(f'  (highest Sharpe among candidates that pass all criteria)')
    else:
        print('  RECOMMENDATION: No offset passes all adoption criteria — keep offset=0.0')
    print()


def main():
    parser = argparse.ArgumentParser(
        description='Sweep forecast_tilt_offset values.',
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
        default=Path('out/tilt_sweep'),
    )
    parser.add_argument(
        '--offsets', type=float, nargs='+',
        default=[0.0, 1.0, 2.0, 3.0, 5.0],
        help='Tilt offset values to test (default: 0 1 2 3 5)',
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

    print(f'Base config:  {args.base_config}')
    print(f'Data:         {args.data}')
    print(f'Output dir:   {args.outdir}')
    print(f'Offsets:      {args.offsets}')
    print()

    results = []

    for offset in args.offsets:
        tag = f'offset_{offset:+.1f}'.replace('+', 'pos').replace('-', 'neg').replace('.', 'p')
        run_outdir = args.outdir / tag

        print(f'{"─"*60}')
        print(f'Running: forecast_tilt_offset = {offset:+.1f}  →  {run_outdir}')

        # Check if already done
        if args.skip_existing and (run_outdir / 'performance_summary.json').exists():
            print('  Skipping — results already exist (--skip-existing)')
            r = load_results(run_outdir)
            r['offset'] = offset
            results.append(r)
            continue

        # Write temp config with modified offset
        cfg = dict(base_cfg)
        cfg['forecast_tilt_offset'] = float(offset)

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
        r['offset'] = offset
        results.append(r)

        # Quick preview
        m = r.get('metrics', {})
        print(
            f'  Sharpe={m.get("sharpe", float("nan")):.4f}  '
            f'Calmar={m.get("calmar", float("nan")):.4f}  '
            f'CAGR={m.get("cagr", 0)*100:.2f}%  '
            f'MaxDD={m.get("max_dd", 0)*100:.2f}%'
        )

    print_comparison(results)

    # Save summary
    summary_path = args.outdir / 'tilt_sweep_summary.json'
    with open(summary_path, 'w') as f:
        json.dump(results, f, indent=2, default=str)
    print(f'Full results saved: {summary_path}')


if __name__ == '__main__':
    main()
