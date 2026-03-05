#!/usr/bin/env python3
"""
Compare XSMOM long-only gate variants.

Hypothesis (Han et al. 2024 / Dobrynskaya): cross-sectional momentum alpha is
concentrated in the LONG (winner) side. The short leg — going short instruments
that underperformed the cross-section — tends to mean-revert, not continue falling.
Clipping XSMOM forecasts to max(0, fc) removes the reversal-prone shorts.

TSMOM rules (EWMAC, breakout, accel) still go short freely — those are time-series
signals about the instrument's own trend, unaffected by this gate.

Three configs tested:
  baseline       — xsmom_long_only: false (symmetric, reproduces production Sharpe)
  gate_relmom    — xsmom_long_only: true, rules: [relmomentum_20, relmomentum_40]
  gate_all_xsmom — xsmom_long_only: true, rules: [relmomentum_20/40 + assettrend_8/16/32/64]

Adoption criteria:
  - ΔSharpe >= +1% relative vs baseline
  - ΔCrisis return > -5pp (bear-market protection mostly intact)
  - ΔMaxDD > -3pp absolute worsening
  - ΔCalmar: monitor (not a hard threshold)
  - avg_positions essentially unchanged ±5% (gate changes direction, not universe)

Usage:
    python scripts/sweep_xsmom_long_only.py \\
        --base-config config/crypto_perps_full_rules.yaml \\
        --data data/dataset_538registry_6yr_jagged.parquet \\
        --outdir out/xsmom_long_only_sweep

    # Skip already-completed runs (useful on re-run)
    python scripts/sweep_xsmom_long_only.py --outdir out/xsmom_long_only_sweep --skip-existing
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


def print_comparison(results: list) -> None:
    """Print formatted comparison table."""
    print()
    print('=' * 140)
    print('XSMOM LONG-ONLY GATE SWEEP — RESULTS')
    print('=' * 140)

    hdr = (
        f'{"Config":<22}  {"Sharpe":>8}  {"Calmar":>8}  '
        f'{"CAGR":>8}  {"Vol":>8}  {"MaxDD":>8}  {"Crisis Ret":>10}  '
        f'{"AvgPos":>7}  {"ΔSharpe":>8}  {"ΔCalmar":>8}  {"ΔMaxDD":>8}  {"ΔCrisis":>9}'
    )
    print(hdr)
    print('─' * 140)

    baseline = None
    for r in results:
        m = r.get('metrics', {})
        tag      = r.get('tag', 'N/A')
        sharpe   = m.get('sharpe',        float('nan'))
        calmar   = m.get('calmar',        float('nan'))
        cagr     = m.get('cagr',          float('nan'))
        vol      = m.get('ann_vol',       float('nan'))
        maxdd    = m.get('max_dd',        float('nan'))
        crisis   = m.get('crisis_return', float('nan'))
        avg_pos  = m.get('avg_positions', float('nan'))

        if baseline is None:
            baseline = r
            d_sharpe = 0.0
            d_calmar = 0.0
            d_maxdd  = 0.0
            d_crisis = 0.0
        else:
            b_m = baseline.get('metrics', {})
            d_sharpe = sharpe - b_m.get('sharpe', float('nan'))
            d_calmar = calmar - b_m.get('calmar', float('nan'))
            d_maxdd  = (maxdd - b_m.get('max_dd', float('nan'))) * 100
            d_crisis = (crisis - b_m.get('crisis_return', float('nan'))) * 100

        avg_pos_str = f'{avg_pos:.1f}' if avg_pos == avg_pos else '  N/A'
        print(
            f'{tag:<22}  '
            f'{sharpe:>8.4f}  '
            f'{calmar:>8.4f}  '
            f'{cagr*100:>7.2f}%  '
            f'{vol*100:>7.2f}%  '
            f'{maxdd*100:>7.2f}%  '
            f'{crisis*100:>10.2f}%  '
            f'{avg_pos_str:>7}  '
            f'{d_sharpe:>+8.4f}  '
            f'{d_calmar:>+8.4f}  '
            f'{d_maxdd:>+7.2f}pp  '
            f'{d_crisis:>+7.2f}pp'
        )

    print('─' * 140)
    print()

    # Adoption criteria check
    baseline_m = baseline.get('metrics', {}) if baseline else {}
    b_sharpe  = baseline_m.get('sharpe',        float('nan'))
    b_maxdd   = baseline_m.get('max_dd',        float('nan'))
    b_crisis  = baseline_m.get('crisis_return', float('nan'))
    b_avg_pos = baseline_m.get('avg_positions', float('nan'))

    print('ADOPTION CRITERIA CHECK')
    print(
        '  ΔSharpe >= +1% relative  |  ΔCrisis > -5pp  |  '
        'ΔMaxDD > -3pp  |  avg_positions unchanged ±5%'
    )
    print()

    candidates = []
    for r in results[1:]:  # skip baseline
        m = r.get('metrics', {})
        tag      = r.get('tag', 'N/A')
        sharpe   = m.get('sharpe',        float('nan'))
        maxdd    = m.get('max_dd',        float('nan'))
        calmar   = m.get('calmar',        float('nan'))
        crisis   = m.get('crisis_return', float('nan'))
        avg_pos  = m.get('avg_positions', float('nan'))

        d_sharpe_pct = (sharpe - b_sharpe) / abs(b_sharpe) * 100 if b_sharpe else float('nan')
        d_maxdd_pp   = (maxdd  - b_maxdd)  * 100
        d_crisis_pp  = (crisis - b_crisis) * 100
        d_pos_pct    = (avg_pos - b_avg_pos) / abs(b_avg_pos) * 100 if b_avg_pos else float('nan')

        sharpe_ok = d_sharpe_pct >= 1.0
        maxdd_ok  = d_maxdd_pp   > -3.0
        crisis_ok = d_crisis_pp  > -5.0
        pos_ok    = abs(d_pos_pct) <= 5.0 if d_pos_pct == d_pos_pct else True

        all_ok = sharpe_ok and maxdd_ok and crisis_ok and pos_ok
        status = '✓ PASS' if all_ok else '✗ FAIL'
        if all_ok:
            candidates.append(r)

        print(
            f'  {tag}:  '
            f'ΔSharpe={d_sharpe_pct:+.1f}% {("✓" if sharpe_ok else "✗")}  '
            f'ΔMaxDD={d_maxdd_pp:+.1f}pp {("✓" if maxdd_ok else "✗")}  '
            f'ΔCrisis={d_crisis_pp:+.1f}pp {("✓" if crisis_ok else "✗")}  '
            f'ΔPos={d_pos_pct:+.1f}% {("✓" if pos_ok else "✗")}  '
            f'Calmar={calmar:.4f}  '
            f'-> {status}'
        )

    print()
    if candidates:
        best = max(candidates, key=lambda r: r.get('metrics', {}).get('sharpe', float('-inf')))
        best_tag = best.get('tag', 'unknown')
        print(f'  RECOMMENDATION: Use config "{best_tag}"')
        print(f'  (highest Sharpe among configurations that pass all adoption criteria)')
        if 'relmom' in best_tag:
            print('  Set xsmom_long_only: true with xsmom_rule_list: [relmomentum_20, relmomentum_40]')
        elif 'all_xsmom' in best_tag:
            print('  Set xsmom_long_only: true with xsmom_rule_list including assettrend rules')
    else:
        print(
            '  RECOMMENDATION: No configuration passes all adoption criteria '
            '— keep xsmom_long_only: false'
        )
    print()


def main():
    parser = argparse.ArgumentParser(
        description='Compare XSMOM long-only gate variants.',
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
        default=Path('out/xsmom_long_only_sweep'),
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

    # Three configs: baseline (off) + gate_relmom + gate_all_xsmom
    configs = [
        {
            'tag': 'baseline',
            'xsmom_long_only': False,
            'xsmom_rule_list': [],
            'description': 'Symmetric (no gate) — should reproduce production Sharpe',
        },
        {
            'tag': 'gate_relmom',
            'xsmom_long_only': True,
            'xsmom_rule_list': ['relmomentum_20', 'relmomentum_40'],
            'description': 'Gate relmomentum only (purest XSMOM rules)',
        },
        {
            'tag': 'gate_all_xsmom',
            'xsmom_long_only': True,
            'xsmom_rule_list': [
                'relmomentum_20', 'relmomentum_40',
                'assettrend_8', 'assettrend_16', 'assettrend_32', 'assettrend_64',
            ],
            'description': 'Gate relmomentum + assettrend (all XS comparators)',
        },
    ]

    print(f'Base config:  {args.base_config}')
    print(f'Data:         {args.data}')
    print(f'Output dir:   {args.outdir}')
    print(f'Total runs:   {len(configs)}')
    print()

    results = []

    for cfg_spec in configs:
        tag  = cfg_spec['tag']
        desc = cfg_spec['description']
        run_outdir = args.outdir / tag

        print(f'{"─"*60}')
        print(f'Running: {tag}')
        print(f'  {desc}')

        if args.skip_existing and (run_outdir / 'performance_summary.json').exists():
            print('  SKIP (already done)')
        else:
            cfg = copy.deepcopy(base_cfg)
            cfg['xsmom_long_only'] = cfg_spec['xsmom_long_only']
            cfg['xsmom_rule_list'] = cfg_spec['xsmom_rule_list']

            with tempfile.NamedTemporaryFile(
                mode='w', suffix='.yaml', delete=False, dir=args.outdir
            ) as tf:
                yaml.dump(cfg, tf, default_flow_style=False, sort_keys=False)
                tmp_cfg = Path(tf.name)

            rc = run_backtest(tmp_cfg, args.data, run_outdir)
            tmp_cfg.unlink(missing_ok=True)

            if rc != 0:
                print(f'  WARNING: backtest returned code {rc}')

        result = load_results(run_outdir)
        result['tag'] = tag
        results.append(result)

    # Print comparison table
    print_comparison(results)

    # Save summary
    summary_path = args.outdir / 'sweep_summary.json'
    with open(summary_path, 'w') as f:
        json.dump(results, f, indent=2, default=str)
    print(f'Results saved to: {summary_path}')


if __name__ == '__main__':
    main()
