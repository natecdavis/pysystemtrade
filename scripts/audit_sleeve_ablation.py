#!/usr/bin/env python3
"""
Sleeve Ablation Study — True Marginal Contribution of Each Sleeve

Runs the full system with each sleeve individually disabled (weight=0) while
all others remain at current production values. Reports the Sharpe drop when
each sleeve is removed — the true marginal contribution in the current stack.

Distinct from the sequential ΔSharpe reported at adoption time (which measured
marginal gain over a different, smaller baseline). Marginal contribution in the
current combined stack can be smaller (if sleeves overlap) or larger (if they
are synergistic).

Sleeves ablated (8 runs + 1 production baseline):
  0. Production baseline (all sleeves ON)
  1. No gated_carry  (carry_weight: 0.0 + use_gated_carry: false)
  2. No xscarry      (xscarry_weight: 0.0)
  3. No inter_sector (inter_sector_weight: 0.0)
  4. No xs_activity  (xs_activity_weight: 0.0)
  5. No xs_addr_growth (xs_addr_growth_weight: 0.0)
  6. No xs_val       (xs_val_weight: 0.0)
  7. No downside_beta (use_downside_beta_overlay: false)
  8. Core only       (all sleeves + overlays disabled — pure trend+carry rules)

Adoption threshold for ablation:
  A sleeve is "genuinely contributing" if removing it drops Sharpe by ≥ 1.5%
  AND its removal degrades Calmar. If removing a sleeve barely changes or
  improves Sharpe/Calmar, it is a candidate for simplification.

Usage:
    python scripts/audit_sleeve_ablation.py \\
        --base-config config/crypto_perps_full_rules.yaml \\
        --data data/dataset_538registry_6yr_jagged.parquet \\
        --outdir out/carver_audit \\
        [--skip-existing]

Runtime: ~5 min per run × 9 runs ≈ 45 minutes total
"""

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))


# ---------------------------------------------------------------------------
# Sleeve definitions: what to zero out in the config for each ablation
# ---------------------------------------------------------------------------
ABLATION_CONFIGS = [
    {
        'name':  'production',
        'label': 'Production (all ON)',
        'overrides': {},   # no changes — baseline
    },
    {
        'name':  'no_gated_carry',
        'label': 'No gated carry',
        'overrides': {
            'use_gated_carry': False,
            'carry_weight': 0.0,
        },
    },
    {
        'name':  'no_xscarry',
        'label': 'No XS carry',
        'overrides': {'xscarry_weight': 0.0},
    },
    {
        'name':  'no_inter_sector',
        'label': 'No inter-sector',
        'overrides': {'inter_sector_weight': 0.0},
    },
    {
        'name':  'no_xs_activity',
        'label': 'No XS activity',
        'overrides': {'xs_activity_weight': 0.0},
    },
    {
        'name':  'no_xs_addr_growth',
        'label': 'No XS addr growth',
        'overrides': {'xs_addr_growth_weight': 0.0},
    },
    {
        'name':  'no_xs_val',
        'label': 'No XS VAL',
        'overrides': {'xs_val_weight': 0.0},
    },
    {
        'name':  'no_downside_beta',
        'label': 'No downside beta overlay',
        'overrides': {'use_downside_beta_overlay': False},
    },
    {
        'name':  'core_only',
        'label': 'Core only (all sleeves OFF)',
        'overrides': {
            # Disable all sleeves
            'use_gated_carry': False,
            'carry_weight': 0.0,
            'xscarry_weight': 0.0,
            'inter_sector_weight': 0.0,
            'xs_activity_weight': 0.0,
            'xs_addr_growth_weight': 0.0,
            'xs_val_weight': 0.0,
            # Disable overlays
            'use_downside_beta_overlay': False,
            'use_oi_overlay': False,
            'use_fg_overlay': False,
            'use_mvrv_overlay': False,
        },
    },
]

# Adoption threshold: Sharpe drop (percent) must exceed this to keep sleeve
MARGINAL_THRESHOLD_PCT = 1.5  # 1.5% Sharpe drop when removed = sleeve is contributing


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


def print_comparison_table(results: list) -> None:
    """Print formatted ablation comparison table."""
    print()
    print('=' * 110)
    print('SLEEVE ABLATION STUDY — TRUE MARGINAL CONTRIBUTION')
    print('Each row shows performance when that sleeve is removed. ΔSharpe < 0 means sleeve contributes.')
    print('Adoption threshold: ΔSharpe ≤ -1.5% (removing sleeve degrades Sharpe by ≥ 1.5%)')
    print('=' * 110)

    hdr = (
        f'{"Config":25s}  '
        f'{"Sharpe":>8}  {"Calmar":>8}  {"CAGR":>8}  '
        f'{"Vol":>7}  {"MaxDD":>8}  '
        f'{"ΔSharpe":>9}  {"ΔCalmar":>8}  {"ΔMaxDD":>8}  '
        f'{"Verdict":15s}'
    )
    print(hdr)
    print('─' * 110)

    # First result is production baseline
    baseline = results[0] if results else None
    b_m = baseline.get('metrics', {}) if baseline else {}

    for r in results:
        m = r.get('metrics', {})
        label    = r.get('label', r.get('name', '?'))
        sharpe   = m.get('sharpe',   float('nan'))
        calmar   = m.get('calmar',   float('nan'))
        cagr     = m.get('cagr',     float('nan'))
        vol      = m.get('ann_vol',  float('nan'))
        maxdd    = m.get('max_dd',   float('nan'))

        if r is baseline:
            d_sharpe = 0.0
            d_calmar = 0.0
            d_maxdd  = 0.0
            verdict  = '← baseline'
        else:
            d_sharpe = sharpe - b_m.get('sharpe', float('nan'))
            d_calmar = calmar - b_m.get('calmar', float('nan'))
            d_maxdd  = (maxdd - b_m.get('max_dd', float('nan'))) * 100

            d_sharpe_pct = d_sharpe / b_m.get('sharpe', 1.0) * 100
            calmar_ok = d_calmar < 0   # Calmar worsens = sleeve was contributing
            sharpe_ok = d_sharpe_pct <= -MARGINAL_THRESHOLD_PCT

            if r.get('name') == 'core_only':
                verdict = '← floor'
            elif sharpe_ok and calmar_ok:
                verdict = '✅ KEEP'
            elif sharpe_ok and not calmar_ok:
                verdict = '⚠ KEEP (no Calmar)'
            elif not sharpe_ok and calmar_ok:
                verdict = '? WEAK (Calmar only)'
            else:
                verdict = '❌ DROP CANDIDATE'

        print(
            f'{label:25s}  '
            f'{sharpe:>8.4f}  '
            f'{calmar:>8.4f}  '
            f'{cagr*100:>7.2f}%  '
            f'{vol*100:>7.2f}%  '
            f'{maxdd*100:>7.2f}%  '
            f'{d_sharpe:>+8.4f}  '
            f'{d_calmar:>+8.4f}  '
            f'{d_maxdd:>+7.2f}pp  '
            f'{verdict}'
        )

    print('─' * 110)
    print()

    # Analysis
    print('ANALYSIS')
    print(f'Adoption threshold: ΔSharpe ≤ -{MARGINAL_THRESHOLD_PCT:.1f}% (sleeve drops Sharpe when removed)')
    print()

    b_sharpe = b_m.get('sharpe', float('nan'))
    for r in results[1:]:
        if r.get('name') == 'core_only':
            continue
        m = r.get('metrics', {})
        sharpe = m.get('sharpe', float('nan'))
        calmar = m.get('calmar', float('nan'))
        d_sharpe = sharpe - b_sharpe
        d_sharpe_pct = d_sharpe / b_sharpe * 100
        d_calmar = calmar - b_m.get('calmar', float('nan'))

        sharpe_ok = d_sharpe_pct <= -MARGINAL_THRESHOLD_PCT
        calmar_ok = d_calmar < 0

        if sharpe_ok and calmar_ok:
            decision = 'KEEP — genuine marginal contribution'
        elif sharpe_ok and not calmar_ok:
            decision = 'KEEP (Sharpe threshold met, but Calmar doesn\'t confirm)'
        elif abs(d_sharpe_pct) < 0.5:
            decision = 'DROP CANDIDATE — near-zero marginal contribution'
        else:
            decision = 'WEAK — below threshold, review further'

        print(f'  [{r.get("name"):20s}]  ΔSharpe={d_sharpe_pct:+.1f}%  ΔCalmar={d_calmar:+.4f}  → {decision}')

    print()

    # Core only comparison
    core = next((r for r in results if r.get('name') == 'core_only'), None)
    if core and baseline:
        core_sharpe = core.get('metrics', {}).get('sharpe', float('nan'))
        d_core = (core_sharpe - b_sharpe) / b_sharpe * 100
        print(
            f'  Core only vs production: ΔSharpe = {d_core:+.1f}% '
            f'({b_sharpe:.4f} → {core_sharpe:.4f})'
        )
        print(f'  Total sleeve stack contribution: {-d_core:.1f}% Sharpe improvement')
    print()


def write_summary_json(results: list, outdir: Path) -> Path:
    """Write ablation results to JSON for downstream consumption."""
    baseline_m = results[0].get('metrics', {}) if results else {}

    output = {
        'production_baseline': {
            'name': results[0].get('name'),
            'metrics': results[0].get('metrics', {}),
        } if results else {},
        'ablations': [],
        'core_only': {},
    }

    for r in results[1:]:
        name = r.get('name', '?')
        m = r.get('metrics', {})
        sharpe = m.get('sharpe', float('nan'))
        calmar = m.get('calmar', float('nan'))
        d_sharpe = sharpe - baseline_m.get('sharpe', float('nan'))
        d_sharpe_pct = d_sharpe / baseline_m.get('sharpe', 1.0) * 100
        d_calmar = calmar - baseline_m.get('calmar', float('nan'))

        entry = {
            'name': name,
            'label': r.get('label', name),
            'metrics': m,
            'delta_sharpe': d_sharpe,
            'delta_sharpe_pct': d_sharpe_pct,
            'delta_calmar': d_calmar,
            'delta_maxdd_pp': (m.get('max_dd', 0.0) - baseline_m.get('max_dd', 0.0)) * 100,
            'sharpe_threshold_met': d_sharpe_pct <= -MARGINAL_THRESHOLD_PCT,
            'calmar_degraded': d_calmar < 0,
        }

        if name == 'core_only':
            output['core_only'] = entry
        else:
            output['ablations'].append(entry)

    summary_path = outdir / 'ablation_summary.json'
    with open(summary_path, 'w') as f:
        json.dump(output, f, indent=2, default=str)
    return summary_path


def write_checklist_section(results: list, outdir: Path) -> None:
    """Append ablation results to CHECKLIST.md (or create ablation section)."""
    baseline_m = results[0].get('metrics', {}) if results else {}
    b_sharpe = baseline_m.get('sharpe', float('nan'))
    b_calmar = baseline_m.get('calmar', float('nan'))

    lines = [
        '\n---\n',
        '## Ablation Study Results\n',
        '| Sleeve Removed | Sharpe | ΔSharpe | Calmar | ΔCalmar | MaxDD | ΔMaxDD | Verdict |',
        '|---------------|--------|---------|--------|---------|-------|--------|---------|',
    ]

    for r in results:
        name = r.get('name', '?')
        label = r.get('label', name)
        m = r.get('metrics', {})
        sharpe = m.get('sharpe', float('nan'))
        calmar = m.get('calmar', float('nan'))
        maxdd  = m.get('max_dd',  float('nan'))

        if r is results[0]:
            d_sharpe_str = '—'
            d_calmar_str = '—'
            d_maxdd_str  = '—'
            verdict = 'baseline'
        else:
            d_sharpe = sharpe - b_sharpe
            d_sharpe_pct = d_sharpe / b_sharpe * 100
            d_calmar = calmar - b_calmar
            d_maxdd_pp = (maxdd - baseline_m.get('max_dd', 0.0)) * 100
            d_sharpe_str = f'{d_sharpe_pct:+.1f}%'
            d_calmar_str = f'{d_calmar:+.4f}'
            d_maxdd_str  = f'{d_maxdd_pp:+.2f}pp'

            if name == 'core_only':
                verdict = 'floor'
            elif d_sharpe_pct <= -MARGINAL_THRESHOLD_PCT and d_calmar < 0:
                verdict = '✅ KEEP'
            elif d_sharpe_pct <= -MARGINAL_THRESHOLD_PCT:
                verdict = '⚠ KEEP'
            elif abs(d_sharpe_pct) < 0.5:
                verdict = '❌ DROP'
            else:
                verdict = '? WEAK'

        lines.append(
            f'| {label} | {sharpe:.4f} | {d_sharpe_str} | '
            f'{calmar:.4f} | {d_calmar_str} | '
            f'{maxdd*100:.2f}% | {d_maxdd_str} | {verdict} |'
        )

    # Write or append to CHECKLIST.md
    checklist_path = outdir / 'CHECKLIST.md'
    mode = 'a' if checklist_path.exists() else 'w'
    with open(checklist_path, mode) as f:
        f.write('\n'.join(lines) + '\n')

    print(f'✓ Ablation results appended to: {checklist_path}')


def main():
    parser = argparse.ArgumentParser(
        description='Sleeve ablation study — true marginal contribution of each sleeve.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        '--base-config', type=Path,
        default=Path('config/crypto_perps_full_rules.yaml'),
        help='Production config to ablate from',
    )
    parser.add_argument(
        '--data', type=Path,
        default=Path('data/dataset_538registry_6yr_jagged.parquet'),
        help='Dataset parquet path',
    )
    parser.add_argument(
        '--outdir', type=Path,
        default=Path('out/carver_audit'),
        help='Output directory for ablation results',
    )
    parser.add_argument(
        '--skip-existing', action='store_true',
        help='Skip runs where performance_summary.json already exists',
    )
    parser.add_argument(
        '--only', nargs='+',
        help='Only run specific ablation names (e.g. --only no_xs_val no_xs_addr_growth)',
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

    # Filter ablations if --only specified
    configs_to_run = ABLATION_CONFIGS
    if args.only:
        configs_to_run = [c for c in ABLATION_CONFIGS if c['name'] in args.only]
        # Always include production baseline
        if not any(c['name'] == 'production' for c in configs_to_run):
            configs_to_run = [ABLATION_CONFIGS[0]] + configs_to_run

    print(f'Sleeve Ablation Study')
    print(f'Base config:  {args.base_config}')
    print(f'Data:         {args.data}')
    print(f'Output dir:   {args.outdir}')
    print(f'Runs to exec: {len(configs_to_run)}')
    print(f'Threshold:    ΔSharpe ≤ -{MARGINAL_THRESHOLD_PCT:.1f}% to KEEP')
    print()

    # Show production config values
    print('Production sleeve weights:')
    for key in ['carry_weight', 'xscarry_weight', 'inter_sector_weight',
                'xs_activity_weight', 'xs_addr_growth_weight', 'xs_val_weight',
                'use_downside_beta_overlay', 'use_oi_overlay', 'use_gated_carry']:
        val = base_cfg.get(key, 'NOT_SET')
        print(f'  {key}: {val}')
    print()

    results = []

    for abl in configs_to_run:
        name = abl['name']
        label = abl['label']
        overrides = abl['overrides']

        run_outdir = args.outdir / f'ablation_{name}'
        print(f'{"─" * 60}')
        print(f'Running: {label}')
        if overrides:
            print(f'  Overrides: {overrides}')
        else:
            print(f'  Overrides: none (production baseline)')

        if args.skip_existing and (run_outdir / 'performance_summary.json').exists():
            print('  Skipping — results already exist (--skip-existing)')
            r = load_results(run_outdir)
            r['name'] = name
            r['label'] = label
            results.append(r)
            m = r.get('metrics', {})
            print(
                f'  Loaded: Sharpe={m.get("sharpe", float("nan")):.4f}  '
                f'Calmar={m.get("calmar", float("nan")):.4f}  '
                f'CAGR={m.get("cagr", 0)*100:.2f}%  '
                f'MaxDD={m.get("max_dd", 0)*100:.2f}%'
            )
            continue

        # Build modified config
        cfg = dict(base_cfg)
        cfg.update(overrides)

        # Write temp config
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
        r['name'] = name
        r['label'] = label
        results.append(r)

        m = r.get('metrics', {})
        print(
            f'  Result: Sharpe={m.get("sharpe", float("nan")):.4f}  '
            f'Calmar={m.get("calmar", float("nan")):.4f}  '
            f'CAGR={m.get("cagr", 0)*100:.2f}%  '
            f'MaxDD={m.get("max_dd", 0)*100:.2f}%'
        )

    # Print final comparison table
    if results:
        print_comparison_table(results)

        # Save JSON summary
        summary_path = write_summary_json(results, args.outdir)
        print(f'✓ Ablation summary JSON: {summary_path}')

        # Append to CHECKLIST.md
        write_checklist_section(results, args.outdir)

        # Print recommendations
        baseline_m = results[0].get('metrics', {}) if results else {}
        b_sharpe = baseline_m.get('sharpe', float('nan'))
        b_calmar = baseline_m.get('calmar', float('nan'))

        print('RECOMMENDATIONS')
        print('─' * 60)
        drop_candidates = []
        keep_list = []

        for r in results[1:]:
            name = r.get('name')
            if name == 'core_only':
                continue
            m = r.get('metrics', {})
            sharpe = m.get('sharpe', float('nan'))
            calmar = m.get('calmar', float('nan'))
            d_sharpe_pct = (sharpe - b_sharpe) / b_sharpe * 100
            d_calmar = calmar - b_calmar

            if d_sharpe_pct > -MARGINAL_THRESHOLD_PCT:
                drop_candidates.append((name, d_sharpe_pct, d_calmar))
            else:
                keep_list.append((name, d_sharpe_pct, d_calmar))

        if drop_candidates:
            print('\nDROP CANDIDATES (marginal contribution < threshold):')
            for name, ds, dc in sorted(drop_candidates, key=lambda x: x[1]):
                print(f'  - {name:25s}  ΔSharpe={ds:+.1f}%  ΔCalmar={dc:+.4f}')
            print()
            print('  Recommendation: Disable these sleeves in config/crypto_perps_full_rules.yaml.')
            print('  Expected benefit: simpler system, fewer parameters, better out-of-sample robustness.')
        else:
            print('\nAll sleeves meet the ≥1.5% marginal contribution threshold — no drop candidates.')

        if keep_list:
            print('\nKEEP (genuine marginal contribution):')
            for name, ds, dc in sorted(keep_list, key=lambda x: x[1]):
                print(f'  - {name:25s}  ΔSharpe={ds:+.1f}%  ΔCalmar={dc:+.4f}')

        print()
        print(f'Next step: If drop candidates exist, verify carry fix and run:')
        print(f'  python scripts/audit_carver.py --diagnostics out/carver_audit/ablation_production/diagnostics.parquet')


if __name__ == '__main__':
    main()
