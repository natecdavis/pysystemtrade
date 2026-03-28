#!/usr/bin/env python3
"""
Sweep gated_carry_90 + gated_carry_180 combined weight and compare backtest performance.

Runs a full backtest for each combined weight, collects metrics, and prints a comparison
table with adoption criteria check.

Signal: gated_carry with longer smoothing windows (90d and 180d). Carry_60 already
earns the highest weight in the stack (0.10 vs 0.07 for 10/30). This sweep tests
whether extending to 90d and 180d adds further diversification value.

Weight convention: equal weight shared across both new rules.
  e.g. combined_weight=0.10 → gated_carry_90=0.05, gated_carry_180=0.05

Existing carry rules (gated_carry_10=0.07, gated_carry_30=0.07, gated_carry_60=0.10)
are held FIXED throughout the sweep — only gated_carry_90/180 vary.

Adoption criteria:
  ΔSharpe vs w=0.0  > +1%    (must add meaningful Sharpe)
  ΔMaxDD  vs w=0.0  < +3pp   (drawdown must not worsen by more than 3pp)
  Calmar: note separately (Calmar-peak candidate preferred)
  Turnover: flag if Carver cost filter would fail (SR/trade ≤ 0.01 or annual SR ≤ 0.13)

Usage:
    python scripts/sweep_long_carry_weight.py \\
        --base-config config/crypto_perps_full_rules.yaml \\
        --data data/dataset_538registry_6yr_jagged.parquet \\
        --outdir out/long_carry_sweep \\
        --weights 0.0 0.03 0.05 0.08 0.10 0.15 0.20 \\
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

LONG_CARRY_RULES = ['gated_carry_90', 'gated_carry_180']

# Carver cost filter thresholds
CARVER_SR_PER_TRADE_MIN = 0.01
CARVER_ANNUAL_SR_MIN = 0.13


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


def check_carver_cost_filter(metrics: dict) -> str | None:
    """Return a warning string if Carver cost filter is breached, else None."""
    warnings = []
    sr_per_trade = metrics.get('sr_per_trade')
    annual_sr = metrics.get('sharpe')
    if sr_per_trade is not None and sr_per_trade < CARVER_SR_PER_TRADE_MIN:
        warnings.append(f'SR/trade={sr_per_trade:.4f} < {CARVER_SR_PER_TRADE_MIN}')
    if annual_sr is not None and annual_sr < CARVER_ANNUAL_SR_MIN:
        warnings.append(f'annual SR={annual_sr:.4f} < {CARVER_ANNUAL_SR_MIN}')
    return '; '.join(warnings) if warnings else None


def print_comparison(results: list[dict]) -> None:
    """Print formatted comparison table with adoption criteria."""
    print()
    print('=' * 120)
    print('LONG CARRY WEIGHT SWEEP — RESULTS')
    print('Signal: gated_carry with longer smoothing windows (90d and 180d).')
    print('Combined weight split equally: w_combined=0.10 → each rule gets 0.05.')
    print('Existing carry_10/30/60 weights held fixed at 0.07/0.07/0.10 throughout.')
    print('Δ columns: relative to w_combined=0.0 (no-long-carry baseline)')
    print('=' * 120)

    hdr = (
        f'{"w_comb":>8}  {"w_each":>7}  {"Sharpe":>8}  {"Calmar":>8}  {"CAGR":>8}  '
        f'{"MaxDD":>8}  {"Turnover":>9}  {"ΔSharpe%":>9}  {"ΔCalmar":>8}  {"ΔMaxDD":>8}  {"Verdict"}'
    )
    print(hdr)
    print('─' * 120)

    # Baseline: w_combined=0.0
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
        w_each     = w_combined / 2.0
        sharpe   = m.get('sharpe',          float('nan'))
        calmar   = m.get('calmar',          float('nan'))
        cagr     = m.get('cagr',            float('nan'))
        maxdd    = m.get('max_dd',          float('nan'))
        turnover = m.get('annual_turnover', float('nan'))

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
            c2 = d_maxdd_pp < 3.0   # MaxDD is negative fraction, so worsening = more negative
            verdict = '✓ CANDIDATE' if (c1 and c2) else '✗ skip'
            if c1 and c2:
                candidates.append(r)
            tag = ''

        turnover_str = f'{turnover:.1f}' if turnover == turnover else 'N/A'

        print(
            f'{w_combined:>8.2f}  '
            f'{w_each:>7.4f}  '
            f'{sharpe:>8.4f}  '
            f'{calmar:>8.4f}  '
            f'{cagr*100:>7.2f}%  '
            f'{maxdd*100:>7.2f}%  '
            f'{turnover_str:>9}  '
            f'{d_sharpe_pct:>+8.1f}%  '
            f'{d_calmar:>+8.4f}  '
            f'{d_maxdd_pp:>+7.2f}pp'
            f'  {verdict}{tag}'
        )

    print('─' * 120)
    print()

    # --- Adoption criteria ---
    print('ADOPTION CRITERIA')
    print('  (1) ΔSharpe vs w=0.0  > +1%    — must add meaningful Sharpe')
    print('  (2) ΔMaxDD  vs w=0.0  < +3pp   — drawdown must not worsen by more than 3pp')
    print('  Calmar: note separately (Calmar-peak candidate preferred)')
    print(f'  Carver cost filter: SR/trade > {CARVER_SR_PER_TRADE_MIN}, annual SR > {CARVER_ANNUAL_SR_MIN}')
    print()

    if candidates:
        # Prefer highest Calmar among candidates
        best = max(candidates, key=lambda r: r.get('metrics', {}).get('calmar', float('-inf')))
        best_w = best['combined_weight']
        best_each = best_w / 2.0
        best_m = best.get('metrics', {})
        d_s = (best_m.get('sharpe', 0) - b_sharpe) / abs(b_sharpe) * 100 if b_sharpe else 0.0
        d_c = best_m.get('calmar', 0) - b_calmar
        d_maxdd = (best_m.get('max_dd', 0) - b_maxdd) * 100

        print(f'  RECOMMENDATION: Adopt long-carry combined weight = {best_w:.2f}')
        print(f'    Per rule (90d/180d): {best_each:.4f} each')
        print(f'    vs no-long-carry baseline:  ΔSharpe={d_s:+.1f}%,  ΔCalmar={d_c:+.4f},  ΔMaxDD={d_maxdd:+.2f}pp')
        print()

        cost_warn = check_carver_cost_filter(best_m)
        if cost_warn:
            print(f'  ⚠ CARVER COST FILTER WARNING at adopted weight: {cost_warn}')
            print('    Consider reducing weight or excluding rule.')
            print()

        print(f'  NEXT STEP: Update config/crypto_perps_full_rules.yaml:')
        print(f'    gated_carry_90:  {best_each:.4f}')
        print(f'    gated_carry_180: {best_each:.4f}')
        print()
        print('  Then run one verification backtest to confirm, and commit.')
    else:
        print('  RECOMMENDATION: No weight passes all criteria — keep gated_carry_90/180 at forecast_weight=0.0.')
        print('  Document in decisions.md and set permanent flag in MEMORY.md.')
    print()


def main():
    parser = argparse.ArgumentParser(
        description='Sweep gated_carry_90/180 combined weight.',
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
        default=Path('out/long_carry_sweep'),
    )
    parser.add_argument(
        '--weights', type=float, nargs='+',
        default=[0.0, 0.03, 0.05, 0.08, 0.10, 0.15, 0.20],
        help='Combined weight for gated_carry_90+180 (split equally). Default: 0.0 0.03 0.05 0.08 0.10 0.15 0.20',
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
    print(f'Rules:          {LONG_CARRY_RULES}  (equal share of combined weight)')
    print(f'Fixed weights:  gated_carry_10=0.07, gated_carry_30=0.07, gated_carry_60=0.10')
    print()

    results = []

    for w_combined in args.weights:
        w_each = w_combined / 2.0
        tag = f'w{w_combined:.2f}'.replace('.', 'p')
        run_outdir = args.outdir / tag

        print(f'{"─" * 60}')
        print(f'Running: long-carry combined weight = {w_combined:.2f}  (each rule: {w_each:.4f})  →  {run_outdir}')

        if args.skip_existing and (run_outdir / 'performance_summary.json').exists():
            print('  Skipping — results already exist (--skip-existing)')
            r = load_results(run_outdir)
            r['combined_weight'] = w_combined
            results.append(r)
            continue

        cfg = dict(base_cfg)

        # Set per-rule weights in forecast_weights
        fw = dict(cfg.get('forecast_weights', {}))
        if w_combined < 1e-9:
            # Zero weight: remove new carry rules from forecast_weights (true baseline)
            for rule in LONG_CARRY_RULES:
                fw.pop(rule, None)
        else:
            for rule in LONG_CARRY_RULES:
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
            f'MaxDD={m.get("max_dd", 0) * 100:.2f}%  '
            f'Turnover={m.get("annual_turnover", float("nan")):.1f}'
        )

    print_comparison(results)

    summary_path = args.outdir / 'sweep_summary.json'
    with open(summary_path, 'w') as f:
        json.dump(results, f, indent=2, default=str)
    print(f'Full results saved: {summary_path}')


if __name__ == '__main__':
    main()
