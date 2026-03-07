#!/usr/bin/env python3
"""
Sweep forecast_weight for gated_carry_60, keeping gated_carry_10/30 fixed at 0.05.

Motivation: empirical weight diagnosis (scripts/diagnose_forecast_weights.py) finds
gated_carry_60 deserves ~3.17× more weight than gated_carry_10/30 when evaluated on
gross P&L across 300 pooled instruments. The 60-day smoother captures a longer-horizon
funding-rate signal that is less correlated with the short-span carry rules, and its
estimated weight rises monotonically 2020→2025 as more history accumulates.

Fixed weights:
  gated_carry_10: 0.05
  gated_carry_30: 0.05
  All other rules: unchanged from base config

Sweep range: [0.05, 0.10, 0.15, 0.20, 0.30]
  0.05 = current baseline (equal to carry_10/30)
  0.10 = 2× carry_10/30
  0.15 = 3× carry_10/30  ← near empirical estimate (3.17×)
  0.20 = 4× carry_10/30
  0.30 = 6× carry_10/30  ← upper bound test

Adoption criteria (vs baseline at 0.05):
  ΔSharpe ≥ +2% (relative)   — modest threshold, single-rule tweak
  ΔMaxDD  > -2pp              — carry is low-turnover, shouldn't hurt DD much
  Calmar non-monotone         — proves genuine signal, not pure leverage

Usage:
    python scripts/sweep_gated_carry_60.py \\
        --base-config config/crypto_perps_full_rules.yaml \\
        --data data/dataset_538registry_6yr_jagged.parquet \\
        --outdir out/gated_carry_60_sweep
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

RULE_TO_SWEEP  = 'gated_carry_60'
FIXED_CARRY    = {'gated_carry_10': 0.05, 'gated_carry_30': 0.05}
DEFAULT_WEIGHTS = [0.05, 0.10, 0.15, 0.20, 0.30]


def load_yaml(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def run_backtest(config_path: Path, data_path: Path, outdir: Path) -> int:
    cmd = [
        sys.executable,
        'scripts/run_dynamic_universe_backtest.py',
        '--config', str(config_path),
        '--data',   str(data_path),
        '--outdir', str(outdir),
    ]
    print(f'\n  CMD: {" ".join(cmd)}')
    return subprocess.run(cmd, capture_output=False).returncode


def load_results(outdir: Path) -> dict:
    p = outdir / 'performance_summary.json'
    if not p.exists():
        return {}
    with open(p) as f:
        return json.load(f)


def effective_share(w60: float, base_cfg: dict) -> tuple[float, float]:
    """
    Return (carry_60_pct, total_carry_pct) as normalised % of all forecast_weights.
    Raw weight sum = sum(all weights) with carry_60 replaced by w60.
    """
    fw = dict(base_cfg.get('forecast_weights', {}))
    fw[RULE_TO_SWEEP] = w60
    total = sum(fw.values())
    if total == 0:
        return 0.0, 0.0
    c60_pct    = w60 / total * 100
    carry_total = (fw.get('gated_carry_10', 0) + fw.get('gated_carry_30', 0) + w60)
    carry_pct  = carry_total / total * 100
    return c60_pct, carry_pct


def print_comparison(results: list[dict], base_cfg: dict) -> None:
    print()
    print('=' * 110)
    print('GATED_CARRY_60 FORECAST WEIGHT SWEEP — RESULTS')
    print(f'Fixed: gated_carry_10=0.05, gated_carry_30=0.05. All other weights unchanged.')
    print('=' * 110)

    hdr = (
        f'{"c60 w":>7}  {"c60%":>6}  {"carry%":>7}  '
        f'{"Sharpe":>8}  {"Calmar":>8}  {"CAGR":>8}  '
        f'{"Vol":>7}  {"MaxDD":>8}  '
        f'{"ΔSharpe":>9}  {"ΔSharpe%":>9}  {"ΔCalmar":>8}  {"ΔMaxDD":>8}'
    )
    print(hdr)
    print('─' * 110)

    baseline = None
    for r in results:
        m   = r.get('metrics', {})
        w60 = r['carry60_weight']
        sharpe = m.get('sharpe',        float('nan'))
        calmar = m.get('calmar',        float('nan'))
        cagr   = m.get('cagr',          float('nan'))
        vol    = m.get('ann_vol',       float('nan'))
        maxdd  = m.get('max_dd',        float('nan'))

        c60_pct, carry_pct = effective_share(w60, base_cfg)

        if baseline is None:
            baseline = r
            d_sharpe = d_sharpe_pct = d_calmar = d_maxdd = 0.0
        else:
            bm = baseline.get('metrics', {})
            d_sharpe     = sharpe - bm.get('sharpe', float('nan'))
            d_sharpe_pct = d_sharpe / bm.get('sharpe', float('nan')) * 100
            d_calmar     = calmar  - bm.get('calmar', float('nan'))
            d_maxdd      = (maxdd  - bm.get('max_dd',  float('nan'))) * 100

        tag = ' ← baseline' if baseline is r else ''
        print(
            f'{w60:>7.2f}  {c60_pct:>5.1f}%  {carry_pct:>6.1f}%  '
            f'{sharpe:>8.4f}  {calmar:>8.4f}  {cagr*100:>7.2f}%  '
            f'{vol*100:>6.2f}%  {maxdd*100:>7.2f}%  '
            f'{d_sharpe:>+9.4f}  {d_sharpe_pct:>+8.1f}%  '
            f'{d_calmar:>+8.4f}  {d_maxdd:>+7.2f}pp'
            f'{tag}'
        )

    print('─' * 110)
    print()

    # Adoption criteria
    bm       = baseline.get('metrics', {}) if baseline else {}
    b_sharpe = bm.get('sharpe', float('nan'))
    b_maxdd  = bm.get('max_dd',  float('nan'))
    b_calmar = bm.get('calmar',  float('nan'))

    print('ADOPTION CRITERIA (vs baseline at carry60_weight=0.05):')
    print('  ΔSharpe ≥ +2% (relative)  |  ΔMaxDD > -2pp  |  Calmar non-monotone')
    print()

    candidates = []
    calmars    = []
    for r in results[1:]:
        m      = r.get('metrics', {})
        sharpe = m.get('sharpe', float('nan'))
        maxdd  = m.get('max_dd',  float('nan'))
        calmar = m.get('calmar',  float('nan'))

        d_sharpe_pct = (sharpe - b_sharpe) / b_sharpe * 100
        d_maxdd_pp   = (maxdd  - b_maxdd)  * 100

        sharpe_ok = d_sharpe_pct >= 2.0
        maxdd_ok  = d_maxdd_pp   > -2.0
        calmars.append(calmar)

        status = '✓ ADOPT' if (sharpe_ok and maxdd_ok) else '✗ REJECT'
        if sharpe_ok and maxdd_ok:
            candidates.append(r)

        print(
            f'  carry60_weight={r["carry60_weight"]:.2f}:  '
            f'ΔSharpe={d_sharpe_pct:+.1f}% {("✓" if sharpe_ok else "✗")}  '
            f'ΔMaxDD={d_maxdd_pp:+.1f}pp {("✓" if maxdd_ok else "✗")}  '
            f'ΔCalmar={(calmar - b_calmar):+.4f}  '
            f'→ {status}'
        )

    print()
    if len(calmars) >= 2:
        monotone = all(calmars[i] > calmars[i + 1] for i in range(len(calmars) - 1))
        if monotone:
            print('  ⚠ WARNING: Calmar falls monotonically — possible pure leverage effect')
        else:
            print('  ✓ Calmar is non-monotone — suggests genuine signal (not pure leverage)')

    print()
    if candidates:
        best = max(candidates, key=lambda r: r.get('metrics', {}).get('sharpe', float('-inf')))
        w    = best['carry60_weight']
        print(f'  RECOMMENDATION: Set gated_carry_60: {w:.2f} in forecast_weights')
        print(f'  (gated_carry_10 and gated_carry_30 remain at 0.05)')
    else:
        print('  No weight passes all adoption criteria — keep gated_carry_60: 0.05 (current)')
    print()


def main():
    parser = argparse.ArgumentParser(
        description='Sweep gated_carry_60 forecast_weight, carry_10/30 fixed at 0.05.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument('--base-config', type=Path,
                        default=Path('config/crypto_perps_full_rules.yaml'))
    parser.add_argument('--data',        type=Path,
                        default=Path('data/dataset_538registry_6yr_jagged.parquet'))
    parser.add_argument('--outdir',      type=Path,
                        default=Path('out/gated_carry_60_sweep'))
    parser.add_argument('--weights',     type=float, nargs='+',
                        default=DEFAULT_WEIGHTS,
                        help=f'carry60 weights to test (default: {DEFAULT_WEIGHTS})')
    parser.add_argument('--skip-existing', action='store_true',
                        help='Skip runs where performance_summary.json already exists')

    args = parser.parse_args()

    for p, name in [(args.base_config, 'base config'), (args.data, 'data file')]:
        if not p.exists():
            print(f'ERROR: {name} not found: {p}')
            sys.exit(1)

    args.outdir.mkdir(parents=True, exist_ok=True)

    base_cfg = load_yaml(args.base_config)
    fw       = base_cfg.get('forecast_weights', {})

    print()
    print(f'Base config:    {args.base_config}')
    print(f'Data:           {args.data}')
    print(f'Output dir:     {args.outdir}')
    print(f'Weights tested: {args.weights}')
    print()
    print('Current carry forecast_weights in base config:')
    for rule in ['gated_carry_10', 'gated_carry_30', 'gated_carry_60']:
        print(f'  {rule}: {fw.get(rule, "NOT FOUND")}')
    print()
    print(f'Total raw forecast_weight sum in base config: {sum(fw.values()):.4f}')
    print()

    results = []

    for w60 in args.weights:
        tag       = f'c60_{w60:.2f}'.replace('.', 'p')
        run_outdir = args.outdir / tag

        print(f'{"─" * 70}')
        c60_pct, carry_pct = effective_share(w60, base_cfg)
        print(
            f'Running: gated_carry_60 = {w60:.4f}  '
            f'(normalised: carry_60={c60_pct:.1f}%, total carry={carry_pct:.1f}%)  '
            f'→ {run_outdir}'
        )

        if args.skip_existing and (run_outdir / 'performance_summary.json').exists():
            print('  Skipping — results already exist (--skip-existing)')
            r = load_results(run_outdir)
            r['carry60_weight'] = w60
            results.append(r)
            continue

        cfg = copy.deepcopy(base_cfg)
        cfg.setdefault('forecast_weights', {})
        cfg['forecast_weights'][RULE_TO_SWEEP] = float(w60)
        for rule, val in FIXED_CARRY.items():
            cfg['forecast_weights'][rule] = float(val)

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
        r['carry60_weight'] = w60
        results.append(r)

        m = r.get('metrics', {})
        print(
            f'  Sharpe={m.get("sharpe", float("nan")):.4f}  '
            f'Calmar={m.get("calmar", float("nan")):.4f}  '
            f'CAGR={m.get("cagr", 0)*100:.2f}%  '
            f'MaxDD={m.get("max_dd", 0)*100:.2f}%'
        )

    print_comparison(results, base_cfg)

    summary_path = args.outdir / 'sweep_summary.json'
    with open(summary_path, 'w') as f:
        json.dump(results, f, indent=2, default=str)
    print(f'Full results saved: {summary_path}')


if __name__ == '__main__':
    main()
