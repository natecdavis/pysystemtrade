#!/usr/bin/env python3
"""
Sweep inter_sector_weight values and compare backtest performance.

Runs a full backtest for each weight, collects metrics, and prints a
comparison table. Tests whether cross-sectional sector momentum (Cong et al.
2022 C-5) adds alpha on top of the production trend+carry+xscarry stack.

Adoption criteria:
  - ΔSharpe ≥ +1% relative vs baseline
  - ΔCrisis return > -5pp (sector rotation shouldn't strip bear protection)
  - ΔMaxDD > -3pp absolute worsening
  - Calmar non-monotone across weight sweep (proves genuine signal)

Usage:
    python scripts/sweep_inter_sector_rotation.py \\
        --base-config config/crypto_perps_full_rules.yaml \\
        --data data/dataset_538registry_6yr_jagged.parquet \\
        --outdir out/inter_sector_rotation_sweep \\
        --weights 0.0 0.2 0.5 1.0 2.0
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
    print('=' * 100)
    print('INTER-SECTOR ROTATION WEIGHT SWEEP — RESULTS')
    print('=' * 100)

    hdr = (
        f'{"Weight":>8}  {"Sharpe":>8}  {"Calmar":>8}  {"CAGR":>8}  '
        f'{"Vol":>8}  {"MaxDD":>8}  {"Crisis Ret":>10}  {"AvgPos":>7}  '
        f'{"ΔSharpe":>8}  {"ΔCalmar":>8}  {"ΔMaxDD":>8}  {"ΔCrisis":>8}'
    )
    print(hdr)
    print('─' * 100)

    baseline = None
    for r in results:
        m = r.get('metrics', {})
        weight  = r['weight']
        sharpe  = m.get('sharpe',         float('nan'))
        calmar  = m.get('calmar',         float('nan'))
        cagr    = m.get('cagr',           float('nan'))
        vol     = m.get('ann_vol',        float('nan'))
        maxdd   = m.get('max_dd',         float('nan'))
        crisis  = m.get('crisis_return',  float('nan'))
        avg_pos = m.get('avg_positions',  float('nan'))

        if baseline is None:
            baseline = r
            d_sharpe = 0.0
            d_calmar = 0.0
            d_maxdd  = 0.0
            d_crisis = 0.0
        else:
            b_m = baseline.get('metrics', {})
            d_sharpe = sharpe - b_m.get('sharpe',        float('nan'))
            d_calmar = calmar - b_m.get('calmar',        float('nan'))
            d_maxdd  = maxdd  - b_m.get('max_dd',        float('nan'))
            d_crisis = crisis - b_m.get('crisis_return', float('nan'))

        tag = ' ← baseline' if baseline is r else ''
        print(
            f'{weight:>8.2f}  '
            f'{sharpe:>8.4f}  '
            f'{calmar:>8.4f}  '
            f'{cagr*100:>7.2f}%  '
            f'{vol*100:>7.2f}%  '
            f'{maxdd*100:>7.2f}%  '
            f'{crisis*100:>10.2f}%  '
            f'{avg_pos:>7.1f}  '
            f'{d_sharpe:>+8.4f}  '
            f'{d_calmar:>+8.4f}  '
            f'{d_maxdd*100:>+7.2f}pp  '
            f'{d_crisis*100:>+7.2f}pp'
            f'{tag}'
        )

    print('─' * 100)
    print()

    # Adoption check
    baseline_m = baseline.get('metrics', {}) if baseline else {}
    b_sharpe = baseline_m.get('sharpe',        float('nan'))
    b_crisis = baseline_m.get('crisis_return', float('nan'))
    b_maxdd  = baseline_m.get('max_dd',        float('nan'))
    b_calmar = baseline_m.get('calmar',        float('nan'))
    b_pos    = baseline_m.get('avg_positions', float('nan'))

    print('ADOPTION CRITERIA CHECK')
    print('  ΔSharpe ≥ +1%  |  ΔCrisis > -5pp  |  ΔMaxDD > -3pp')
    print('  (Calmar non-monotone → genuine signal, not pure leverage)')
    print('  (avg_positions stable ±5% → sleeve changes conviction, not universe)')
    print()

    candidates = []
    calmars = []
    for r in results[1:]:  # skip baseline
        m = r.get('metrics', {})
        sharpe  = m.get('sharpe',        float('nan'))
        crisis  = m.get('crisis_return', float('nan'))
        maxdd   = m.get('max_dd',        float('nan'))
        calmar  = m.get('calmar',        float('nan'))
        avg_pos = m.get('avg_positions', float('nan'))

        d_sharpe_pct = (sharpe - b_sharpe) / abs(b_sharpe) * 100
        d_crisis_pp  = (crisis - b_crisis) * 100
        d_maxdd_pp   = (maxdd  - b_maxdd)  * 100
        d_calmar     = calmar - b_calmar
        d_pos_pct    = (avg_pos - b_pos) / b_pos * 100 if b_pos else float('nan')

        sharpe_ok = d_sharpe_pct >= 1.0
        crisis_ok = d_crisis_pp  > -5.0
        maxdd_ok  = d_maxdd_pp   > -3.0   # MaxDD is negative; worsening = more negative delta

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
            f'ΔPos={d_pos_pct:+.1f}%  '
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
        print(f'  RECOMMENDATION: Use inter_sector_weight={best["weight"]:.2f}')
        print(f'  (highest Sharpe among candidates that pass all criteria)')
        print()
        print(f'  NEXT STEP: Update config and commit:')
        print(f'    inter_sector_weight: {best["weight"]:.2f}')
        print(f'    inter_sector_lookback: 20')
    else:
        print('  RECOMMENDATION: No weight passes all adoption criteria — keep inter_sector_weight=0.0')
    print()


def main():
    parser = argparse.ArgumentParser(
        description='Sweep inter_sector_weight values for inter-sector rotation sleeve.',
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
        default=Path('out/inter_sector_rotation_sweep'),
    )
    parser.add_argument(
        '--weights', type=float, nargs='+',
        default=[0.0, 0.2, 0.5, 1.0, 2.0],
        help='inter_sector_weight values to test (default: 0.0 0.2 0.5 1.0 2.0)',
    )
    parser.add_argument(
        '--lookback', type=int,
        default=20,
        help='Fixed inter_sector_lookback for the weight sweep (default: 20)',
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

    print(f'Base config:      {args.base_config}')
    print(f'Data:             {args.data}')
    print(f'Output dir:       {args.outdir}')
    print(f'Weights:          {args.weights}')
    print(f'Fixed lookback:   {args.lookback}')
    print()

    results = []

    for weight in args.weights:
        tag = f'w{weight:.2f}'.replace('.', 'p')
        run_outdir = args.outdir / tag

        print(f'{"─"*60}')
        print(f'Running: inter_sector_weight = {weight:.2f}  →  {run_outdir}')

        if args.skip_existing and (run_outdir / 'performance_summary.json').exists():
            print('  Skipping — results already exist (--skip-existing)')
            r = load_results(run_outdir)
            r['weight'] = weight
            results.append(r)
            continue

        cfg = dict(base_cfg)
        cfg['inter_sector_weight']   = float(weight)
        cfg['inter_sector_lookback'] = int(args.lookback)

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
            f'MaxDD={m.get("max_dd", 0)*100:.2f}%  '
            f'Crisis={m.get("crisis_return", 0)*100:.2f}%'
        )

    print_comparison(results)

    summary_path = args.outdir / 'inter_sector_sweep_summary.json'
    with open(summary_path, 'w') as f:
        json.dump(results, f, indent=2, default=str)
    print(f'Full results saved: {summary_path}')


if __name__ == '__main__':
    main()
