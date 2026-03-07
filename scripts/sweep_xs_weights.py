#!/usr/bin/env python3
"""
Sweep the 4 cross-sectional rule forecast_weights jointly and compare performance.

After the 2026-03-06 architectural refactor, XS Carry, XS Activity, XS VAL, and
Inter-Sector were moved from additive post-FDM sleeves into standard Carver trading
rules (forecast scalar → weighted avg → FDM → ±20 cap).

This sweep tests the optimal forecast_weights for those 4 rules by varying them
jointly (same weight for all 4 XS rules at each grid point). As standard rules they
DISPLACE trend weight budget, unlike the old additive sleeves. Higher weights reduce
the effective weight of trend rules (pysystemtrade normalises all forecast_weights to
sum=1.0 internally via ForecastCombine).

Pre-refactor production baseline (additive sleeves, 2026-03-06):
  Sharpe 1.5161, Calmar 1.6361, MaxDD -15.40%

Refactor baseline (all 4 XS rules at w=0.05, 2026-03-06):
  Sharpe 0.99, CAGR 17.0%, Vol 17.3%, MaxDD -16.4%

Adoption criteria (vs refactor baseline at w=0.05):
  ΔSharpe ≥ +5%        (larger threshold — refactor baseline is a calibration floor)
  ΔMaxDD > -3pp        (MaxDD must not worsen by more than 3pp vs refactor baseline)
  Calmar non-monotone  (ensures genuine signal, not pure leverage)

Context: Also compare against pre-refactor peak (Sharpe 1.5161) to measure how much
of the additive-sleeve performance is recovered through proper weight calibration.

Usage:
    python scripts/sweep_xs_weights.py \\
        --base-config config/crypto_perps_full_rules.yaml \\
        --data data/dataset_538registry_6yr_jagged.parquet \\
        --outdir out/xs_weights_sweep \\
        --weights 0.05 0.10 0.20 0.30 0.50
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

# The 4 XS rules whose forecast_weights we sweep together
XS_RULES = ['xs_carry', 'xs_activity', 'xs_val', 'inter_sector']

# Pre-refactor additive-sleeve peak (for comparison context)
PREREFACTOR_SHARPE  = 1.5161
PREREFACTOR_MAXDD   = -0.1540
PREREFACTOR_CALMAR  = 1.6361


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
    summary_path = outdir / 'performance_summary.json'
    if not summary_path.exists():
        return {}
    with open(summary_path) as f:
        return json.load(f)


def print_comparison(results: list[dict]) -> None:
    """Print formatted comparison table with adoption criteria."""
    print()
    print('=' * 120)
    print('XS FORECAST WEIGHTS SWEEP — RESULTS')
    print('Rules swept jointly: xs_carry, xs_activity, xs_val, inter_sector')
    print('All 4 XS rules receive the same weight at each grid point.')
    print(f'Pre-refactor peak (additive sleeves): '
          f'Sharpe {PREREFACTOR_SHARPE:.4f}, Calmar {PREREFACTOR_CALMAR:.4f}, '
          f'MaxDD {PREREFACTOR_MAXDD*100:.2f}%')
    print('=' * 120)

    hdr = (
        f'{"XS Weight":>10}  {"Trend%":>7}  {"Sharpe":>8}  {"Calmar":>8}  '
        f'{"CAGR":>8}  {"Vol":>8}  {"MaxDD":>8}  {"Crisis":>8}  '
        f'{"ΔSharpe":>9}  {"ΔSharpe%":>9}  {"ΔCalmar":>8}  {"ΔMaxDD":>8}'
    )
    print(hdr)
    print('─' * 120)

    baseline = None
    for r in results:
        m = r.get('metrics', {})
        weight   = r['xs_weight']
        sharpe   = m.get('sharpe',        float('nan'))
        calmar   = m.get('calmar',        float('nan'))
        cagr     = m.get('cagr',          float('nan'))
        vol      = m.get('ann_vol',       float('nan'))
        maxdd    = m.get('max_dd',        float('nan'))
        crisis   = m.get('crisis_return', float('nan'))

        # Estimate effective trend weight budget consumed
        # Total raw weight: 22 trend (1.0 sum) + 3 carry (0.03) + 4 XS (4*w)
        # Normalised trend share ≈ 1.0 / (1.03 + 4*w)
        trend_pct = 100.0 * 1.0 / (1.03 + 4.0 * weight)

        if baseline is None:
            baseline = r
            d_sharpe     = 0.0
            d_sharpe_pct = 0.0
            d_calmar     = 0.0
            d_maxdd      = 0.0
        else:
            b_m = baseline.get('metrics', {})
            b_sharpe = b_m.get('sharpe', float('nan'))
            d_sharpe     = sharpe - b_sharpe
            d_sharpe_pct = (sharpe - b_sharpe) / b_sharpe * 100
            d_calmar     = calmar - b_m.get('calmar', float('nan'))
            d_maxdd      = (maxdd - b_m.get('max_dd', float('nan'))) * 100

        tag = ' ← baseline' if baseline is r else ''
        print(
            f'{weight:>10.2f}  '
            f'{trend_pct:>6.1f}%  '
            f'{sharpe:>8.4f}  '
            f'{calmar:>8.4f}  '
            f'{cagr*100:>7.2f}%  '
            f'{vol*100:>7.2f}%  '
            f'{maxdd*100:>7.2f}%  '
            f'{crisis*100:>7.2f}%  '
            f'{d_sharpe:>+9.4f}  '
            f'{d_sharpe_pct:>+8.1f}%  '
            f'{d_calmar:>+8.4f}  '
            f'{d_maxdd:>+7.2f}pp'
            f'{tag}'
        )

    print('─' * 120)
    print()

    # Adoption criteria
    baseline_m = baseline.get('metrics', {}) if baseline else {}
    b_sharpe = baseline_m.get('sharpe', float('nan'))
    b_maxdd  = baseline_m.get('max_dd', float('nan'))
    b_calmar = baseline_m.get('calmar', float('nan'))

    print('ADOPTION CRITERIA (vs refactor baseline at xs_weight=0.05):')
    print('  ΔSharpe ≥ +5% (relative)  |  ΔMaxDD > -3pp  |  Calmar non-monotone')
    print()

    candidates = []
    calmars = []
    for r in results[1:]:  # skip baseline
        m = r.get('metrics', {})
        sharpe = m.get('sharpe', float('nan'))
        maxdd  = m.get('max_dd', float('nan'))
        calmar = m.get('calmar', float('nan'))

        d_sharpe_pct = (sharpe - b_sharpe) / b_sharpe * 100
        d_maxdd_pp   = (maxdd  - b_maxdd)  * 100
        d_calmar     = calmar - b_calmar

        sharpe_ok = d_sharpe_pct >= 5.0
        maxdd_ok  = d_maxdd_pp   > -3.0

        calmars.append(calmar)
        status = '✓ ADOPT' if (sharpe_ok and maxdd_ok) else '✗ REJECT'
        if sharpe_ok and maxdd_ok:
            candidates.append(r)

        print(
            f'  xs_weight={r["xs_weight"]:.2f}:  '
            f'ΔSharpe={d_sharpe_pct:+.1f}% {("✓" if sharpe_ok else "✗"):1}  '
            f'ΔMaxDD={d_maxdd_pp:+.1f}pp {("✓" if maxdd_ok else "✗"):1}  '
            f'ΔCalmar={d_calmar:+.4f}  '
            f'→ {status}'
        )

    print()
    if len(calmars) >= 2:
        monotone_fall = all(calmars[i] > calmars[i + 1] for i in range(len(calmars) - 1))
        if monotone_fall:
            print(
                '  ⚠ WARNING: Calmar falls monotonically — possible pure leverage effect'
            )
        else:
            print('  ✓ Calmar is non-monotone — suggests genuine signal (not pure leverage)')

    print()

    # Pre-refactor peak comparison
    if results:
        best_r   = max(results, key=lambda r: r.get('metrics', {}).get('sharpe', float('-inf')))
        best_m   = best_r.get('metrics', {})
        best_sharpe = best_m.get('sharpe', float('nan'))
        best_maxdd  = best_m.get('max_dd', float('nan'))
        recovery_sharpe = (best_sharpe - PREREFACTOR_SHARPE) / PREREFACTOR_SHARPE * 100
        print(f'  Best vs pre-refactor peak (additive sleeves):')
        print(f'    Sharpe: {best_sharpe:.4f} vs {PREREFACTOR_SHARPE:.4f}  '
              f'(Δ={recovery_sharpe:+.1f}% — gap vs old additive approach)')
        print(f'    MaxDD:  {best_maxdd*100:.2f}% vs {PREREFACTOR_MAXDD*100:.2f}%')
        print()

    if candidates:
        best = max(candidates, key=lambda r: r.get('metrics', {}).get('sharpe', float('-inf')))
        w    = best['xs_weight']
        print(f'  RECOMMENDATION: Set xs_weight={w:.2f} for all 4 XS rules in forecast_weights')
        print()
        print(f'  NEXT STEP: Update config/crypto_perps_full_rules.yaml forecast_weights:')
        for rule in XS_RULES:
            print(f'    {rule}: {w:.2f}')
    else:
        print(
            '  No xs_weight passes all adoption criteria vs refactor baseline. '
            'Consider keeping xs_weight=0.05 (current) or investigate whether the '
            'XS signals need different weights for each rule.'
        )
    print()


def main():
    parser = argparse.ArgumentParser(
        description='Sweep xs forecast_weights jointly for xs_carry, xs_activity, xs_val, inter_sector.',
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
        default=Path('out/xs_weights_sweep'),
    )
    parser.add_argument(
        '--weights', type=float, nargs='+',
        default=[0.05, 0.10, 0.20, 0.30, 0.50],
        help='xs weight values to test (all 4 XS rules get same weight)',
    )
    parser.add_argument(
        '--skip-existing', action='store_true',
        help='Skip runs where performance_summary.json already exists',
    )

    args = parser.parse_args()

    if not args.base_config.exists():
        print(f'ERROR: base config not found: {args.base_config}')
        sys.exit(1)
    if not args.data.exists():
        print(f'ERROR: data file not found: {args.data}')
        sys.exit(1)

    # Check auxiliary data files
    aa_path   = args.data.parent / 'active_addresses.parquet'
    mcap_path = args.data.parent / 'market_cap.parquet'
    for p in [aa_path, mcap_path]:
        if not p.exists():
            print(f'ERROR: required data file not found: {p}')
            sys.exit(1)
        print(f'  {p.name}: ✓')

    args.outdir.mkdir(parents=True, exist_ok=True)

    base_cfg = load_yaml(args.base_config)
    fw = base_cfg.get('forecast_weights', {})

    print()
    print(f'Base config:    {args.base_config}')
    print(f'Data:           {args.data}')
    print(f'Output dir:     {args.outdir}')
    print(f'Weights tested: {args.weights}')
    print()
    print('Current XS forecast_weights in base config:')
    for rule in XS_RULES:
        print(f'  {rule}: {fw.get(rule, "NOT FOUND")}')
    print()

    # Current total raw weight (for context on budget displacement)
    total_raw = sum(fw.values())
    print(f'Total raw forecast_weight sum in base config: {total_raw:.4f}')
    print('(pysystemtrade normalises internally — trend rules lose weight as XS weights rise)')
    print()

    results = []

    for weight in args.weights:
        tag = f'xs{weight:.2f}'.replace('.', 'p')
        run_outdir = args.outdir / tag

        print(f'{"─" * 70}')
        print(f'Running: xs_weight = {weight:.4f}  (all 4 XS rules)  →  {run_outdir}')

        if args.skip_existing and (run_outdir / 'performance_summary.json').exists():
            print('  Skipping — results already exist (--skip-existing)')
            r = load_results(run_outdir)
            r['xs_weight'] = weight
            results.append(r)
            continue

        # Deep copy base config and set all 4 XS rule weights
        cfg = copy.deepcopy(base_cfg)
        if 'forecast_weights' not in cfg:
            cfg['forecast_weights'] = {}
        for rule in XS_RULES:
            cfg['forecast_weights'][rule] = float(weight)

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
        r['xs_weight'] = weight
        results.append(r)

        m = r.get('metrics', {})
        print(
            f'  Sharpe={m.get("sharpe", float("nan")):.4f}  '
            f'Calmar={m.get("calmar", float("nan")):.4f}  '
            f'CAGR={m.get("cagr", 0) * 100:.2f}%  '
            f'MaxDD={m.get("max_dd", 0) * 100:.2f}%  '
            f'Crisis={m.get("crisis_return", 0) * 100:.2f}%'
        )

    print_comparison(results)

    summary_path = args.outdir / 'xs_weights_sweep_summary.json'
    with open(summary_path, 'w') as f:
        json.dump(results, f, indent=2, default=str)
    print(f'Full results saved: {summary_path}')


if __name__ == '__main__':
    main()
