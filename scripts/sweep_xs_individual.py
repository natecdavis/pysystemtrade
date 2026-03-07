#!/usr/bin/env python3
"""
Sweep each XS rule's forecast_weight independently, others fixed at 0.10.

After the joint sweep adopted w=0.10 for all 4 XS rules, ablation evidence shows
very different marginal contributions:
  xs_carry:     -13.9% ΔSharpe when removed  (strongest)
  xs_activity:   -6.4% ΔSharpe when removed
  inter_sector:  -5.4% ΔSharpe when removed
  xs_val:        -2.6% ΔSharpe when removed  (weakest)

This script sweeps each rule individually to find the per-rule optimal weight.

For each rule, the other 3 are fixed at 0.10 (adopted baseline).
Sweep range: [0.05, 0.10, 0.15, 0.20, 0.30]
  0.10 = current baseline (shared across all 4 sweeps)
  0.05 = half baseline (test reducing xs_val / weaker rules)
  0.15 = 1.5× baseline
  0.20 = 2× baseline
  0.30 = 3× baseline (upper bound for xs_carry)

Adoption criteria (vs baseline at w=0.10):
  ΔSharpe ≥ +2% (relative)   — single-rule tweak
  ΔMaxDD  > -2pp
  Calmar non-monotone

Shared baseline: all 4 XS at 0.10 → outdir/xs_baseline/
Saves 3 redundant re-runs vs sweeping each rule from scratch.

Usage:
    python scripts/sweep_xs_individual.py \\
        --base-config config/crypto_perps_full_rules.yaml \\
        --data data/dataset_538registry_6yr_jagged.parquet \\
        --outdir out/xs_individual_sweep
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

XS_RULES       = ['xs_carry', 'xs_activity', 'xs_val', 'inter_sector']
BASELINE_WEIGHT = 0.10
SWEEP_WEIGHTS   = [0.05, 0.10, 0.15, 0.20, 0.30]


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


def run_one(cfg: dict, data_path: Path, outdir: Path, skip_existing: bool) -> dict:
    """Write tempfile config, run backtest, return metrics dict."""
    if skip_existing and (outdir / 'performance_summary.json').exists():
        print('  Skipping — results already exist (--skip-existing)')
        return load_results(outdir)

    with tempfile.NamedTemporaryFile(
        mode='w', suffix='.yaml', delete=False, dir=outdir.parent
    ) as tmp:
        yaml.dump(cfg, tmp, default_flow_style=False, sort_keys=False)
        tmp_path = Path(tmp.name)

    try:
        rc = run_backtest(tmp_path, data_path, outdir)
    finally:
        tmp_path.unlink(missing_ok=True)

    if rc != 0:
        print(f'  WARNING: backtest returned non-zero exit code {rc}')

    return load_results(outdir)


def print_rule_table(rule: str, results: list[dict]) -> None:
    other_rules = [r for r in XS_RULES if r != rule]
    print()
    print('=' * 110)
    print(f'{rule.upper()} INDIVIDUAL WEIGHT SWEEP')
    print(f'Fixed: {", ".join(f"{r}=0.10" for r in other_rules)}')
    print('=' * 110)

    hdr = (
        f'{"weight":>8}  {"Sharpe":>8}  {"Calmar":>8}  {"CAGR":>8}  '
        f'{"Vol":>7}  {"MaxDD":>8}  '
        f'{"ΔSharpe":>9}  {"ΔSharpe%":>9}  {"ΔCalmar":>8}  {"ΔMaxDD":>8}'
    )
    print(hdr)
    print('─' * 110)

    baseline = None
    for r in results:
        m      = r.get('metrics', {})
        w      = r['weight']
        sharpe = m.get('sharpe',  float('nan'))
        calmar = m.get('calmar',  float('nan'))
        cagr   = m.get('cagr',    float('nan'))
        vol    = m.get('ann_vol', float('nan'))
        maxdd  = m.get('max_dd',  float('nan'))

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
            f'{w:>8.2f}  '
            f'{sharpe:>8.4f}  {calmar:>8.4f}  {cagr*100:>7.2f}%  '
            f'{vol*100:>6.2f}%  {maxdd*100:>7.2f}%  '
            f'{d_sharpe:>+9.4f}  {d_sharpe_pct:>+8.1f}%  '
            f'{d_calmar:>+8.4f}  {d_maxdd:>+7.2f}pp'
            f'{tag}'
        )

    print('─' * 110)
    print()

    bm       = baseline.get('metrics', {}) if baseline else {}
    b_sharpe = bm.get('sharpe', float('nan'))
    b_maxdd  = bm.get('max_dd',  float('nan'))
    b_calmar = bm.get('calmar',  float('nan'))

    print('ADOPTION CRITERIA:  ΔSharpe ≥ +2%  |  ΔMaxDD > -2pp  |  Calmar non-monotone')
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
            f'  {rule}={r["weight"]:.2f}:  '
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
    # Prefer Calmar-peak candidate if it passes criteria; else highest Sharpe
    calmar_peak = None
    if candidates:
        # Find candidate with highest Calmar (Calmar-peak criterion)
        calmar_peak = max(candidates, key=lambda r: r.get('metrics', {}).get('calmar', float('-inf')))
        best_sharpe = max(candidates, key=lambda r: r.get('metrics', {}).get('sharpe', float('-inf')))
        cp_w  = calmar_peak['weight']
        bs_w  = best_sharpe['weight']
        if cp_w == bs_w:
            print(f'  RECOMMENDATION: {rule}: {cp_w:.2f}  (Calmar-peak = best Sharpe)')
        else:
            print(f'  RECOMMENDATION: {rule}: {cp_w:.2f}  (Calmar peak)  '
                  f'[best Sharpe at {bs_w:.2f} — check if de-leveraging]')
    else:
        calmar_peak = baseline
        print(f'  No weight passes all adoption criteria — keep {rule}: {BASELINE_WEIGHT:.2f}')
    print()

    return calmar_peak['weight'] if calmar_peak else BASELINE_WEIGHT


def print_final_summary(recommendations: dict[str, float]) -> None:
    print()
    print('=' * 60)
    print('INDIVIDUAL XS SWEEP — FINAL RECOMMENDATIONS')
    print('=' * 60)
    raw_total_before = sum([1.0,  # trend
                            0.07 + 0.07 + 0.10,  # carry
                            4 * BASELINE_WEIGHT])  # XS at baseline
    raw_xs_after = sum(recommendations.values())
    raw_total_after = 1.0 + 0.07 + 0.07 + 0.10 + raw_xs_after

    for rule in XS_RULES:
        w_old = BASELINE_WEIGHT
        w_new = recommendations[rule]
        flag  = ' ← unchanged' if w_new == w_old else f' ← was {w_old:.2f}'
        print(f'  {rule:<16}: {w_new:.2f}{flag}')
    print()
    print(f'  XS raw weight sum: {4 * BASELINE_WEIGHT:.2f} → {raw_xs_after:.2f}')
    print(f'  Total raw weight:  {raw_total_before:.2f} → {raw_total_after:.2f}')
    print()


def main():
    parser = argparse.ArgumentParser(
        description='Sweep each XS rule forecast_weight independently (others fixed at 0.10).',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument('--base-config', type=Path,
                        default=Path('config/crypto_perps_full_rules.yaml'))
    parser.add_argument('--data',        type=Path,
                        default=Path('data/dataset_538registry_6yr_jagged.parquet'))
    parser.add_argument('--outdir',      type=Path,
                        default=Path('out/xs_individual_sweep'))
    parser.add_argument('--weights',     type=float, nargs='+',
                        default=SWEEP_WEIGHTS)
    parser.add_argument('--rules',       type=str, nargs='+',
                        default=XS_RULES,
                        help='Which XS rules to sweep (default: all 4)')
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
    print(f'Rules swept:    {args.rules}')
    print(f'Weights tested: {args.weights}')
    print()
    print('Current XS forecast_weights in base config:')
    for rule in XS_RULES:
        print(f'  {rule}: {fw.get(rule, "NOT FOUND")}')
    print()

    # ── Shared baseline: all 4 XS rules at BASELINE_WEIGHT ──────────────────
    baseline_outdir = args.outdir / 'xs_baseline'
    baseline_outdir.mkdir(exist_ok=True)

    print(f'{"─" * 70}')
    print(f'SHARED BASELINE: all XS rules = {BASELINE_WEIGHT:.2f}  →  {baseline_outdir}')

    baseline_cfg = copy.deepcopy(base_cfg)
    for rule in XS_RULES:
        baseline_cfg['forecast_weights'][rule] = float(BASELINE_WEIGHT)

    baseline_results = run_one(baseline_cfg, args.data, baseline_outdir, args.skip_existing)

    m = baseline_results.get('metrics', {})
    print(
        f'  Sharpe={m.get("sharpe", float("nan")):.4f}  '
        f'Calmar={m.get("calmar", float("nan")):.4f}  '
        f'CAGR={m.get("cagr", 0)*100:.2f}%  '
        f'MaxDD={m.get("max_dd", 0)*100:.2f}%'
    )

    # ── Per-rule sweeps ──────────────────────────────────────────────────────
    recommendations = {rule: BASELINE_WEIGHT for rule in XS_RULES}
    all_sweep_results = {}

    for target_rule in args.rules:
        other_rules = [r for r in XS_RULES if r != target_rule]
        rule_results = []

        for w in args.weights:
            tag        = f'{target_rule}_{w:.2f}'.replace('.', 'p')
            run_outdir = args.outdir / tag

            print(f'{"─" * 70}')
            print(
                f'Sweeping {target_rule} = {w:.4f}  '
                f'(others fixed at {BASELINE_WEIGHT:.2f})  →  {run_outdir}'
            )

            if w == BASELINE_WEIGHT:
                # Reuse shared baseline
                print(f'  Reusing shared baseline (w={BASELINE_WEIGHT:.2f})')
                r = dict(baseline_results)
                r['weight'] = w
                rule_results.append(r)
                m = r.get('metrics', {})
                print(
                    f'  Sharpe={m.get("sharpe", float("nan")):.4f}  '
                    f'Calmar={m.get("calmar", float("nan")):.4f}  '
                    f'CAGR={m.get("cagr", 0)*100:.2f}%  '
                    f'MaxDD={m.get("max_dd", 0)*100:.2f}%'
                )
                continue

            run_outdir.mkdir(exist_ok=True)
            cfg = copy.deepcopy(base_cfg)
            for rule in XS_RULES:
                cfg['forecast_weights'][rule] = (
                    float(w) if rule == target_rule else float(BASELINE_WEIGHT)
                )

            r = run_one(cfg, args.data, run_outdir, args.skip_existing)
            r['weight'] = w
            rule_results.append(r)

            m = r.get('metrics', {})
            print(
                f'  Sharpe={m.get("sharpe", float("nan")):.4f}  '
                f'Calmar={m.get("calmar", float("nan")):.4f}  '
                f'CAGR={m.get("cagr", 0)*100:.2f}%  '
                f'MaxDD={m.get("max_dd", 0)*100:.2f}%'
            )

        all_sweep_results[target_rule] = rule_results
        recommended_w = print_rule_table(target_rule, rule_results)
        recommendations[target_rule] = recommended_w

    print_final_summary(recommendations)

    summary_path = args.outdir / 'individual_sweep_summary.json'
    with open(summary_path, 'w') as f:
        json.dump({'recommendations': recommendations,
                   'sweep_results': all_sweep_results}, f, indent=2, default=str)
    print(f'Full results saved: {summary_path}')


if __name__ == '__main__':
    main()
