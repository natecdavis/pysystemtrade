#!/usr/bin/env python3
"""
Sweep inter_sector_weight values and compare backtest performance.

Runs a full backtest for each weight, collects metrics, and prints a
comparison table with adoption criteria check.

Signal: EWMAC(20,80) on ADV-weighted sector aggregate, cross-sectionally ranked
per date → ±20 forecast. 7 named sectors: L1/DeFi/AI/Meme/Gaming/L2/Infra.
Instruments in "Other" (~53) receive 0. Slice held since 2026-02-28 (w=1.0).

Background (Carver ablation finding):
  At w=0:  Sharpe 1.4724, Calmar 1.9273, MaxDD -11.49%, CAGR 22.14%
  At w=1:  Sharpe 1.5569, Calmar 1.4433, MaxDD -18.57%, CAGR 26.81%
  Delta:   +5.4% Sharpe, -0.48 Calmar, -7.08pp MaxDD, +4.67pp CAGR

The sleeve adds Sharpe but concentrates sector-rotation bets and widens MaxDD.
A weight between 0 and 1 may Pareto-dominate the current w=1 on Sharpe/Calmar.

Adoption criteria (DIFFERENT from prior sleeves — existing signal, not binary keep/reject):
  ΔSharpe vs w=0  ≥ +2%          (must add at least 2% Sharpe to justify drawdown cost)
  ΔCalmar vs w=1  ≥ +0.10        (meaningful Calmar improvement vs current production)
  ΔSharpe vs w=1  > -3%          (must not lose more than 3% Sharpe vs current production)
  If Calmar monotonically falling with weight → w=1.0 is already optimal (no change)

Usage:
    python scripts/sweep_inter_sector_weight.py \\
        --base-config config/crypto_perps_full_rules.yaml \\
        --data data/dataset_538registry_6yr_jagged.parquet \\
        --outdir out/inter_sector_sweep \\
        --weights 0.0 0.1 0.3 0.5 0.7 1.0 1.5 \\
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

PRODUCTION_WEIGHT = 1.0


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


def print_comparison(results: list[dict], lookback: int) -> None:
    """Print formatted comparison table with adoption criteria."""
    print()
    print('=' * 110)
    print('INTER-SECTOR WEIGHT SWEEP — RESULTS')
    print('Signal: EWMAC(20,80) on ADV-weighted sector aggregate, XS-ranked → ±20 forecast')
    print('Sectors: L1 / DeFi / AI / Meme / Gaming / L2 / Infra  (Other instruments receive 0)')
    print(f'Fixed params: lookback={lookback}d (sector aggregate EWM smoothing), production w=1.0')
    print('Δ columns: relative to w=0.0 (pure contribution curve)')
    print('=' * 110)

    hdr = (
        f'{"Weight":>8}  {"Sharpe":>8}  {"Calmar":>8}  {"CAGR":>8}  '
        f'{"MaxDD":>8}  {"ΔSharpe%":>9}  {"ΔCalmar":>8}  {"ΔMaxDD":>8}  {"Verdict"}'
    )
    print(hdr)
    print('─' * 110)

    # Baseline for Δ is w=0.0
    baseline = None
    for r in results:
        if r['weight'] == 0.0:
            baseline = r
            break
    if baseline is None and results:
        baseline = results[0]

    b_m = baseline.get('metrics', {}) if baseline else {}
    b_sharpe = b_m.get('sharpe', float('nan'))
    b_calmar = b_m.get('calmar', float('nan'))
    b_maxdd  = b_m.get('max_dd', float('nan'))

    # Find production w=1.0 metrics for relative comparison
    prod_metrics = {}
    for r in results:
        if abs(r['weight'] - PRODUCTION_WEIGHT) < 1e-6:
            prod_metrics = r.get('metrics', {})
            break

    for r in results:
        m = r.get('metrics', {})
        weight  = r['weight']
        sharpe  = m.get('sharpe',  float('nan'))
        calmar  = m.get('calmar',  float('nan'))
        cagr    = m.get('cagr',    float('nan'))
        maxdd   = m.get('max_dd',  float('nan'))

        if abs(weight - 0.0) < 1e-6:
            d_sharpe_pct = 0.0
            d_calmar     = 0.0
            d_maxdd_pp   = 0.0
            tag = ' ← zero-baseline'
        else:
            d_sharpe_pct = (sharpe - b_sharpe) / abs(b_sharpe) * 100 if b_sharpe else float('nan')
            d_calmar     = calmar - b_calmar
            d_maxdd_pp   = (maxdd - b_maxdd) * 100

            # Verdict: ΔSharpe vs w=0 ≥ +2%, ΔCalmar vs w=1 ≥ +0.1, ΔSharpe vs w=1 > -3%
            sharpe_vs_zero_ok = d_sharpe_pct >= 2.0
            p_sharpe = prod_metrics.get('sharpe', float('nan'))
            p_calmar = prod_metrics.get('calmar', float('nan'))
            sharpe_vs_prod_ok = (sharpe - p_sharpe) / abs(p_sharpe) * 100 > -3.0 if p_sharpe else True
            calmar_vs_prod_ok = (calmar - p_calmar) >= 0.10 if p_calmar else False

            if abs(weight - PRODUCTION_WEIGHT) < 1e-6:
                tag = ' ← PRODUCTION'
            else:
                tag = ''

        print(
            f'{weight:>8.2f}  '
            f'{sharpe:>8.4f}  '
            f'{calmar:>8.4f}  '
            f'{cagr*100:>7.2f}%  '
            f'{maxdd*100:>7.2f}%  '
            f'{d_sharpe_pct:>+8.1f}%  '
            f'{d_calmar:>+8.4f}  '
            f'{d_maxdd_pp:>+7.2f}pp'
            f'{tag}'
        )

    print('─' * 110)
    print()

    # --- Adoption criteria check ---
    print('ADOPTION CRITERIA')
    print('  (1) ΔSharpe vs w=0  ≥ +2%      — sleeve must add meaningful Sharpe to justify drawdown cost')
    print('  (2) ΔCalmar vs w=1  ≥ +0.10    — meaningful Calmar improvement over current production')
    print('  (3) ΔSharpe vs w=1  > -3%      — must not lose more than 3% Sharpe vs current production')
    print()

    p_sharpe = prod_metrics.get('sharpe', float('nan'))
    p_calmar = prod_metrics.get('calmar', float('nan'))

    candidates = []        # weights that pass all 3 criteria
    calmars = []           # to check monotonicity
    for r in results:
        m = r.get('metrics', {})
        weight  = r['weight']
        sharpe  = m.get('sharpe', float('nan'))
        calmar  = m.get('calmar', float('nan'))

        if abs(weight - 0.0) < 1e-6:
            continue  # skip the zero-baseline row

        calmars.append((weight, calmar))

        d_sharpe_vs_zero = (sharpe - b_sharpe) / abs(b_sharpe) * 100 if b_sharpe else float('nan')
        d_sharpe_vs_prod = (sharpe - p_sharpe) / abs(p_sharpe) * 100 if p_sharpe else float('nan')
        d_calmar_vs_prod = calmar - p_calmar if p_calmar else float('nan')

        c1 = d_sharpe_vs_zero >= 2.0
        c2 = d_calmar_vs_prod >= 0.10
        c3 = d_sharpe_vs_prod > -3.0

        prod_marker = ' (PRODUCTION)' if abs(weight - PRODUCTION_WEIGHT) < 1e-6 else ''
        verdict = '✓ CANDIDATE' if (c1 and c2 and c3) else '✗ skip'
        if c1 and c2 and c3:
            candidates.append(r)

        print(
            f'  weight={weight:.2f}{prod_marker}:  '
            f'ΔSharpe/0={d_sharpe_vs_zero:+.1f}% {("✓" if c1 else "✗")}  '
            f'ΔCalmar/1={d_calmar_vs_prod:+.4f} {("✓" if c2 else "✗")}  '
            f'ΔSharpe/1={d_sharpe_vs_prod:+.1f}% {("✓" if c3 else "✗")}  '
            f'→ {verdict}'
        )

    # Monotonicity check on Calmar
    print()
    calmar_vals = [c for _, c in sorted(calmars)]
    if len(calmar_vals) >= 2:
        monotone_fall = all(calmar_vals[i] >= calmar_vals[i + 1] for i in range(len(calmar_vals) - 1))
        if monotone_fall:
            print(
                '  ⚠ NOTE: Calmar falls monotonically with weight — '
                'current w=1.0 is already the Sharpe-optimal point; '
                'reducing weight does not improve risk-adjusted returns.'
            )
            print('  RECOMMENDATION: Keep inter_sector_weight=1.0 (no change)')
        else:
            print('  ✓ Calmar is non-monotone — a lower weight may Pareto-dominate on Sharpe/Calmar frontier')

    print()
    if candidates:
        # Prefer the highest Calmar among candidates that also preserve most Sharpe
        best = max(candidates, key=lambda r: r.get('metrics', {}).get('calmar', float('-inf')))
        best_w = best['weight']
        best_m = best.get('metrics', {})
        d_s = (best_m.get('sharpe', 0) - p_sharpe) / abs(p_sharpe) * 100 if p_sharpe else 0.0
        d_c = best_m.get('calmar', 0) - p_calmar if p_calmar else 0.0
        d_maxdd = (best_m.get('max_dd', 0) - prod_metrics.get('max_dd', 0)) * 100

        print(f'  RECOMMENDATION: Reduce inter_sector_weight to {best_w:.2f}')
        print(f'    vs production w=1.0:  ΔSharpe={d_s:+.1f}%,  ΔCalmar={d_c:+.4f},  ΔMaxDD={d_maxdd:+.2f}pp')
        print()
        print(f'  NEXT STEP: Update config/crypto_perps_full_rules.yaml:')
        print(f'    inter_sector_weight: {best_w:.2f}')
        print(f'    inter_sector_lookback: {lookback}')
        print()
        print('  Then run one verification backtest to confirm, and commit.')
    elif not any(abs(r['weight'] - PRODUCTION_WEIGHT) < 1e-6 for r in results if
                 (r.get('metrics', {}).get('sharpe', 0) > 0)):
        print(
            '  RECOMMENDATION: No weight passes all criteria — '
            'keep inter_sector_weight=1.0 (current production)'
        )
    else:
        # No improvement candidate found
        print(
            '  RECOMMENDATION: No weight below 1.0 improves Calmar by ≥0.10 — '
            'keep inter_sector_weight=1.0 (current production)'
        )
    print()


def main():
    parser = argparse.ArgumentParser(
        description='Sweep inter_sector_weight values.',
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
        default=Path('out/inter_sector_sweep'),
    )
    parser.add_argument(
        '--weights', type=float, nargs='+',
        default=[0.0, 0.1, 0.3, 0.5, 0.7, 1.0, 1.5],
        help='inter_sector_weight values to test (default: 0.0 0.1 0.3 0.5 0.7 1.0 1.5)',
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
    lookback = base_cfg.get('inter_sector_lookback', 20)

    print(f'Base config:    {args.base_config}')
    print(f'Data:           {args.data}')
    print(f'Output dir:     {args.outdir}')
    print(f'Weights:        {args.weights}')
    print(f'Lookback:       {lookback} days (fixed, sector aggregate EWM)')
    print(f'Production wt:  {PRODUCTION_WEIGHT} (current inter_sector_weight)')
    print()

    results = []

    for weight in args.weights:
        tag = f'w{weight:.2f}'.replace('.', 'p')
        run_outdir = args.outdir / tag

        print(f'{"─" * 60}')
        print(f'Running: inter_sector_weight = {weight:.2f}  →  {run_outdir}')

        if args.skip_existing and (run_outdir / 'performance_summary.json').exists():
            print('  Skipping — results already exist (--skip-existing)')
            r = load_results(run_outdir)
            r['weight'] = weight
            results.append(r)
            continue

        cfg = dict(base_cfg)
        cfg['inter_sector_weight'] = float(weight)

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
            f'CAGR={m.get("cagr", 0) * 100:.2f}%  '
            f'MaxDD={m.get("max_dd", 0) * 100:.2f}%'
        )

    print_comparison(results, lookback=lookback)

    summary_path = args.outdir / 'sweep_summary.json'
    with open(summary_path, 'w') as f:
        json.dump(results, f, indent=2, default=str)
    print(f'Full results saved: {summary_path}')


if __name__ == '__main__':
    main()
