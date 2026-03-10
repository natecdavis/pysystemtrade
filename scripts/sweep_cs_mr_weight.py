#!/usr/bin/env python3
"""
Sweep cs_mr combined weight and compare backtest performance.

Runs a full backtest for each combined cs_mr weight, collects metrics, and prints a
comparison table with adoption criteria check.

Signal: Carver (2017) cross-sectional mean reversion — 2 horizons (125d, 250d).
  For each instrument, compute rolling outperformance vs crypto-wide median normalised price,
  then negate (mean reversion bet). ewma_span=horizon/4.
  Carver's claim: negative correlation with trend rules → portfolio insurance.

Weight convention: equal weight shared across both horizons.
  e.g. combined_weight=0.10 → cs_mr_125=0.05, cs_mr_250=0.05

Adoption criteria:
  ΔSharpe vs w=0.0  > +1%    (must add meaningful Sharpe)
  ΔMaxDD  vs w=0.0  < +3pp   (drawdown must not worsen by more than 3pp)
  Calmar-peak must be at w > 0.0 (if peak at w=0.0 → reject)

Usage:
    python scripts/sweep_cs_mr_weight.py \\
        --base-config config/crypto_perps_full_rules.yaml \\
        --data data/dataset_538registry_6yr_jagged.parquet \\
        --outdir out/cs_mr_sweep \\
        --weights 0.0 0.03 0.05 0.07 0.10 0.15 0.20 \\
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

CS_MR_RULE_NAMES = ['cs_mr_125', 'cs_mr_250']
N_RULES = len(CS_MR_RULE_NAMES)


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


def print_comparison(results: list[dict]) -> None:
    """Print formatted comparison table with adoption criteria."""
    print()
    print('=' * 115)
    print('CS_MR COMBINED WEIGHT SWEEP — RESULTS')
    print('Signal: Carver (2017) XS mean reversion, 2 horizons: 125d / 250d.')
    print('Combined weight split equally: w_combined=0.10 → each rule gets 0.05.')
    print('Δ columns: relative to w_combined=0.0 (no-cs_mr baseline)')
    print('=' * 115)

    hdr = (
        f'{"w_comb":>8}  {"w_each":>7}  {"Sharpe":>8}  {"Calmar":>8}  {"CAGR":>8}  '
        f'{"MaxDD":>8}  {"ΔSharpe%":>9}  {"ΔCalmar":>8}  {"ΔMaxDD":>8}  {"Verdict"}'
    )
    print(hdr)
    print('─' * 115)

    # Baseline for Δ is w_combined=0.0
    baseline = None
    for r in results:
        if r['combined_weight'] == 0.0:
            baseline = r
            break
    if baseline is None and results:
        baseline = results[0]

    b_m = baseline.get('metrics', {}) if baseline else {}
    b_sharpe = b_m.get('sharpe', float('nan'))
    b_calmar = b_m.get('calmar', float('nan'))
    b_maxdd  = b_m.get('max_dd', float('nan'))

    candidates = []

    for r in results:
        m = r.get('metrics', {})
        w_combined = r['combined_weight']
        w_each     = w_combined / N_RULES
        sharpe  = m.get('sharpe',  float('nan'))
        calmar  = m.get('calmar',  float('nan'))
        cagr    = m.get('cagr',    float('nan'))
        maxdd   = m.get('max_dd',  float('nan'))

        if abs(w_combined - 0.0) < 1e-6:
            d_sharpe_pct = 0.0
            d_calmar     = 0.0
            d_maxdd_pp   = 0.0
            tag = ' ← zero-baseline'
            verdict = ''
        else:
            d_sharpe_pct = (sharpe - b_sharpe) / abs(b_sharpe) * 100 if b_sharpe else float('nan')
            d_calmar     = calmar - b_calmar
            d_maxdd_pp   = (maxdd - b_maxdd) * 100

            c1 = d_sharpe_pct > 1.0
            c2 = d_maxdd_pp < 3.0   # drawdown worsens by < 3pp (note: MaxDD is negative)
            verdict = '✓ CANDIDATE' if (c1 and c2) else '✗ skip'
            if c1 and c2:
                candidates.append(r)
            tag = ''

        print(
            f'{w_combined:>8.2f}  '
            f'{w_each:>7.4f}  '
            f'{sharpe:>8.4f}  '
            f'{calmar:>8.4f}  '
            f'{cagr*100:>7.2f}%  '
            f'{maxdd*100:>7.2f}%  '
            f'{d_sharpe_pct:>+8.1f}%  '
            f'{d_calmar:>+8.4f}  '
            f'{d_maxdd_pp:>+7.2f}pp'
            f'  {verdict}{tag}'
        )

    print('─' * 115)
    print()

    # --- Adoption criteria ---
    print('ADOPTION CRITERIA')
    print('  (1) ΔSharpe vs w=0.0  > +1%    — must add meaningful Sharpe')
    print('  (2) ΔMaxDD  vs w=0.0  < +3pp   — drawdown must not worsen by more than 3pp')
    print('  (3) Calmar-peak must be at w > 0.0  — reject if peak is at zero-signal baseline')
    print()

    # Check if Calmar-peak is at w=0.0
    best_calmar_result = max(results, key=lambda r: r.get('metrics', {}).get('calmar', float('-inf')))
    calmar_peak_at_zero = abs(best_calmar_result['combined_weight']) < 1e-6

    if calmar_peak_at_zero:
        print('  REJECT: Calmar-peak is at w=0.0 — cs_mr adds no value in this dataset.')
        print('  → XS mean reversion is likely too weak in trending crypto 2020–2026.')
        print()
        print('  ACTION: Zero both cs_mr weights in config and commit as rejected.')
        print('    cs_mr_125: 0.0')
        print('    cs_mr_250: 0.0')
    elif candidates:
        # Prefer highest Calmar among candidates
        best = max(candidates, key=lambda r: r.get('metrics', {}).get('calmar', float('-inf')))
        best_w = best['combined_weight']
        best_each = best_w / N_RULES
        best_m = best.get('metrics', {})
        d_s = (best_m.get('sharpe', 0) - b_sharpe) / abs(b_sharpe) * 100 if b_sharpe else 0.0
        d_c = best_m.get('calmar', 0) - b_calmar
        d_maxdd = (best_m.get('max_dd', 0) - b_maxdd) * 100

        print(f'  RECOMMENDATION: Adopt cs_mr combined weight = {best_w:.2f}')
        print(f'    Per rule (125d/250d): {best_each:.4f} each')
        print(f'    vs no-cs_mr baseline:  ΔSharpe={d_s:+.1f}%,  ΔCalmar={d_c:+.4f},  ΔMaxDD={d_maxdd:+.2f}pp')
        print()
        print(f'  NEXT STEP: Update config/crypto_perps_full_rules.yaml:')
        for rule in CS_MR_RULE_NAMES:
            print(f'    {rule}: {best_each:.4f}')
        print()
        print('  Then commit.')
    else:
        print('  REJECT: No weight passes all criteria.')
        print('  → cs_mr adds insufficient Sharpe or worsens drawdown too much.')
        print()
        print('  ACTION: Zero both cs_mr weights in config and commit as rejected.')
        for rule in CS_MR_RULE_NAMES:
            print(f'    # {rule}: 0.0  # REJECTED')
    print()


def main():
    parser = argparse.ArgumentParser(
        description='Sweep cs_mr combined weight.',
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
        default=Path('out/cs_mr_sweep'),
    )
    parser.add_argument(
        '--weights', type=float, nargs='+',
        default=[0.0, 0.03, 0.05, 0.07, 0.10, 0.15, 0.20],
        help='Combined cs_mr weight (split equally across 2 rules). Default: 0.0 0.03 0.05 0.07 0.10 0.15 0.20',
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
    print(f'Combined weights to test: {args.weights}')
    print(f'Rules:          {CS_MR_RULE_NAMES}  (equal share of combined weight)')
    print()

    results = []

    for w_combined in args.weights:
        w_each = w_combined / N_RULES
        tag = f'w{w_combined:.2f}'.replace('.', 'p')
        run_outdir = args.outdir / tag

        print(f'{"─" * 60}')
        print(f'Running: cs_mr combined weight = {w_combined:.2f}  (each rule: {w_each:.4f})  →  {run_outdir}')

        if args.skip_existing and (run_outdir / 'performance_summary.json').exists():
            print('  Skipping — results already exist (--skip-existing)')
            r = load_results(run_outdir)
            r['combined_weight'] = w_combined
            results.append(r)
            continue

        cfg = dict(base_cfg)

        fw = dict(cfg.get('forecast_weights', {}))
        if w_combined < 1e-9:
            # Zero weight: remove cs_mr rules from forecast_weights
            for rule in CS_MR_RULE_NAMES:
                fw.pop(rule, None)
        else:
            for rule in CS_MR_RULE_NAMES:
                fw[rule] = float(w_each)
        cfg['forecast_weights'] = fw

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
        r['combined_weight'] = w_combined
        results.append(r)

        m = r.get('metrics', {})
        print(
            f'  Sharpe={m.get("sharpe", float("nan")):.4f}  '
            f'Calmar={m.get("calmar", float("nan")):.4f}  '
            f'CAGR={m.get("cagr", 0) * 100:.2f}%  '
            f'MaxDD={m.get("max_dd", 0) * 100:.2f}%'
        )

    print_comparison(results)

    summary_path = args.outdir / 'sweep_summary.json'
    with open(summary_path, 'w') as f:
        json.dump(results, f, indent=2, default=str)
    print(f'Full results saved: {summary_path}')


if __name__ == '__main__':
    main()
