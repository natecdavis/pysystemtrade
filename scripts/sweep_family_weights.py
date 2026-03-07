#!/usr/bin/env python3
"""
Equal Carver family weights — three-way comparison test.

Tests three structurally different ways to assign equal budgets across rule
families for the 29-rule stack (22 trend + 3 gated_carry + 4 XS passthrough).

Context:
  Current config uses equal family weights for 7 trend families, but the 7
  non-trend rules (gated_carry + XS) are all set to flat 0.05 — conservative
  initialization, not a principled family allocation. The three alternatives
  differ only in how inter_sector is assigned to a family.

  Config A (10 families): inter_sector gets its own Sector family (solo, 10%)
  Config B  (9 families): inter_sector merged into Assettrend (5-rule family)
  Config C  (8 families): all 7 XS/carry rules in one Cross-Sectional family

Carry-refactor baseline (current flat-0.05 after gated-carry refactor):
  Sharpe 0.94, CAGR 10.2%, Vol 10.9%, MaxDD -12.5%

Decision rule:
  - Pick highest Sharpe among A/B/C
  - Reject any scheme where MaxDD worsens >5pp vs current
  - Keep current if ALL 3 equal-family configs underperform by >3% Sharpe

Usage:
    # Dry run (verify weight sums, ~1 sec)
    python scripts/sweep_family_weights.py --dry-run \\
        --config config/crypto_perps_full_rules.yaml \\
        --data data/dataset_538registry_6yr_jagged.parquet \\
        --outdir out/family_weights_sweep

    # Full comparison (~44 min, 4 × 11 min)
    python scripts/sweep_family_weights.py \\
        --config config/crypto_perps_full_rules.yaml \\
        --data data/dataset_538registry_6yr_jagged.parquet \\
        --outdir out/family_weights_sweep
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

# Pre-refactor additive-sleeve peak (context only — not a decision threshold)
PREREFACTOR_SHARPE = 1.5161
PREREFACTOR_MAXDD  = -0.1540
PREREFACTOR_CALMAR = 1.6361

# Rule groups for effective-budget breakdown
TREND_RULES = [
    'ewmac_8', 'ewmac_16', 'ewmac_32',
    'breakout_20', 'breakout_40', 'breakout_80', 'breakout_160',
    'normmom_8', 'normmom_16', 'normmom_32',
    'accel_16', 'accel_32', 'accel_64',
    'assettrend_8', 'assettrend_16', 'assettrend_32', 'assettrend_64',
    'relmomentum_20', 'relmomentum_40',
    'residual_momentum_16', 'residual_momentum_32', 'residual_momentum_64',
]
CARRY_RULES  = ['xs_carry', 'gated_carry_10', 'gated_carry_30', 'gated_carry_60']
NETWORK_RULES = ['xs_activity', 'xs_val']
SECTOR_RULES  = ['inter_sector']

# ============================================================================
# WEIGHT SCHEMES
# All non-None schemes are constructed to sum exactly to 1.0.
# Fractions are written as exact rationals (e.g. 1/30) so Python evaluates
# them precisely; the dry-run checks the total to 1e-10 tolerance.
# ============================================================================

WEIGHT_SCHEMES = {
    # ─── CURRENT ────────────────────────────────────────────────────────────
    # Base config unchanged.
    # 7 trend families (equal, sum=1.0) + gated_carry×3 + XS×4 at flat 0.05.
    # Raw sum ≈ 1.35; pysystemtrade normalises internally.
    # Effective budget: trend ~74% | carry+XS ~26% (see breakdown in dry-run)
    'current': None,

    # ─── CONFIG A: 10 FAMILIES ──────────────────────────────────────────────
    # inter_sector gets its own Sector family (10th family, sole rule).
    # Each family = 10% of budget.
    # Effective budget: trend 70% | carry 10% | network 10% | sector 10%
    'config_a_10fam': {
        # EWMAC (3 rules × 3.333% = 10.000%)
        'ewmac_8':   1/30,  'ewmac_16':  1/30,  'ewmac_32':  1/30,
        # Breakout (4 rules × 2.500% = 10.000%)
        'breakout_20':  1/40, 'breakout_40':  1/40,
        'breakout_80':  1/40, 'breakout_160': 1/40,
        # Normmom (3 rules × 3.333% = 10.000%)
        'normmom_8':  1/30,  'normmom_16':  1/30,  'normmom_32':  1/30,
        # Accel (3 rules × 3.333% = 10.000%)
        'accel_16':   1/30,  'accel_32':    1/30,  'accel_64':    1/30,
        # Assettrend (4 rules × 2.500% = 10.000%)
        'assettrend_8':  1/40, 'assettrend_16': 1/40,
        'assettrend_32': 1/40, 'assettrend_64': 1/40,
        # Relmomentum (2 rules × 5.000% = 10.000%)
        'relmomentum_20': 1/20, 'relmomentum_40': 1/20,
        # ResidualMomentum (3 rules × 3.333% = 10.000%)
        'residual_momentum_16': 1/30,
        'residual_momentum_32': 1/30,
        'residual_momentum_64': 1/30,
        # Carry family: xs_carry + gated_carry×3 = 4 rules × 2.500% = 10.000%
        'xs_carry':       1/40,
        'gated_carry_10': 1/40,
        'gated_carry_30': 1/40,
        'gated_carry_60': 1/40,
        # Network family: xs_activity + xs_val = 2 rules × 5.000% = 10.000%
        'xs_activity': 1/20, 'xs_val': 1/20,
        # Sector family: inter_sector = 1 rule × 10.000% = 10.000%
        'inter_sector': 1/10,
    },

    # ─── CONFIG B: 9 FAMILIES ───────────────────────────────────────────────
    # inter_sector merged into Assettrend family (5-rule family, 11.111% budget).
    # Rationale: both are cross-sectional market-structure momentum signals
    # (EWMAC on ADV-weighted market index vs sector aggregate index).
    # Effective budget: trend 75.6% (22 pure) | carry 11.1% | network 11.1%
    #                   (inter_sector shares its 11.1% Assettrend budget: 2.2%)
    'config_b_9fam': {
        # EWMAC (3 rules × 3.704% = 11.111%)
        'ewmac_8':   1/27,  'ewmac_16':  1/27,  'ewmac_32':  1/27,
        # Breakout (4 rules × 2.778% = 11.111%)
        'breakout_20':  1/36, 'breakout_40':  1/36,
        'breakout_80':  1/36, 'breakout_160': 1/36,
        # Normmom (3 rules × 3.704% = 11.111%)
        'normmom_8':  1/27,  'normmom_16':  1/27,  'normmom_32':  1/27,
        # Accel (3 rules × 3.704% = 11.111%)
        'accel_16':   1/27,  'accel_32':    1/27,  'accel_64':    1/27,
        # Assettrend + inter_sector (5 rules × 2.222% = 11.111%)
        'assettrend_8':  1/45, 'assettrend_16': 1/45,
        'assettrend_32': 1/45, 'assettrend_64': 1/45,
        'inter_sector':  1/45,
        # Relmomentum (2 rules × 5.556% = 11.111%)
        'relmomentum_20': 1/18, 'relmomentum_40': 1/18,
        # ResidualMomentum (3 rules × 3.704% = 11.111%)
        'residual_momentum_16': 1/27,
        'residual_momentum_32': 1/27,
        'residual_momentum_64': 1/27,
        # Carry family: xs_carry + gated_carry×3 = 4 rules × 2.778% = 11.111%
        'xs_carry':       1/36,
        'gated_carry_10': 1/36,
        'gated_carry_30': 1/36,
        'gated_carry_60': 1/36,
        # Network family: xs_activity + xs_val = 2 rules × 5.556% = 11.111%
        'xs_activity': 1/18, 'xs_val': 1/18,
    },

    # ─── CONFIG C: 8 FAMILIES ───────────────────────────────────────────────
    # All 7 non-trend rules merged into one Cross-Sectional family.
    # Rationale: xs_carry, gated_carry, xs_activity, xs_val, inter_sector are
    # all cross-sectional (they rank instruments relative to each other). One
    # fixed budget (12.5%) for this entire class regardless of rule count.
    # Effective budget: trend 87.5% | cross-sectional 12.5%
    # Risk: gated_carry rules (larger raw scalar) may be under-represented
    #       at 1.786% each compared to their calibrated contribution.
    'config_c_8fam': {
        # EWMAC (3 rules × 4.167% = 12.500%)
        'ewmac_8':   1/24,  'ewmac_16':  1/24,  'ewmac_32':  1/24,
        # Breakout (4 rules × 3.125% = 12.500%)
        'breakout_20':  1/32, 'breakout_40':  1/32,
        'breakout_80':  1/32, 'breakout_160': 1/32,
        # Normmom (3 rules × 4.167% = 12.500%)
        'normmom_8':  1/24,  'normmom_16':  1/24,  'normmom_32':  1/24,
        # Accel (3 rules × 4.167% = 12.500%)
        'accel_16':   1/24,  'accel_32':    1/24,  'accel_64':    1/24,
        # Assettrend (4 rules × 3.125% = 12.500%)
        'assettrend_8':  1/32, 'assettrend_16': 1/32,
        'assettrend_32': 1/32, 'assettrend_64': 1/32,
        # Relmomentum (2 rules × 6.250% = 12.500%)
        'relmomentum_20': 1/16, 'relmomentum_40': 1/16,
        # ResidualMomentum (3 rules × 4.167% = 12.500%)
        'residual_momentum_16': 1/24,
        'residual_momentum_32': 1/24,
        'residual_momentum_64': 1/24,
        # Cross-Sectional family (7 rules × 1.786% = 12.500%)
        # xs_carry + gated_carry×3 + xs_activity + xs_val + inter_sector
        'xs_carry':       1/56,
        'gated_carry_10': 1/56,
        'gated_carry_30': 1/56,
        'gated_carry_60': 1/56,
        'xs_activity':    1/56,
        'xs_val':         1/56,
        'inter_sector':   1/56,
    },
}

SCHEME_LABELS = {
    'current':        'current (flat-0.05)',
    'config_a_10fam': 'Config A (10 fam)',
    'config_b_9fam':  'Config B  (9 fam)',
    'config_c_8fam':  'Config C  (8 fam)',
}

SCHEME_DESCRIPTIONS = {
    'current':        'flat 0.05 for non-trend; trend at equal-family (1/7 each)',
    'config_a_10fam': 'inter_sector solo Sector family → 10% budget',
    'config_b_9fam':  'inter_sector → Assettrend family (5 rules, 11.1% each)',
    'config_c_8fam':  'all 7 XS rules one Cross-Sectional family (12.5% total)',
}


def load_yaml(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def compute_budget_breakdown(weights: dict) -> dict:
    """Return effective budget fractions for each rule group."""
    total = sum(weights.values())
    if total <= 0:
        return {'trend': 0, 'carry': 0, 'network': 0, 'sector': 0}

    def group_pct(rules):
        return sum(weights.get(r, 0.0) for r in rules) / total * 100

    return {
        'trend':   group_pct(TREND_RULES),
        'carry':   group_pct(CARRY_RULES),
        'network': group_pct(NETWORK_RULES),
        'sector':  group_pct(SECTOR_RULES),
    }


def verify_weights(scheme_name: str, weights: dict) -> bool:
    """Verify weights sum to 1.0 within floating-point tolerance."""
    total = sum(weights.values())
    ok = abs(total - 1.0) < 1e-10
    status = '✓' if ok else '✗ ERROR'
    print(f'  {status}  {scheme_name}: sum = {total:.15f}  (target 1.0000000000000000)')
    return ok


def print_dry_run(base_fw: dict) -> None:
    """Print weight tables and budget breakdowns for all schemes."""
    print()
    print('=' * 80)
    print('DRY RUN — Weight verification and budget breakdown')
    print('=' * 80)

    all_ok = True

    for scheme_name, weights in WEIGHT_SCHEMES.items():
        label = SCHEME_LABELS[scheme_name]
        desc  = SCHEME_DESCRIPTIONS[scheme_name]

        print()
        print(f'Scheme: {label}')
        print(f'  Description: {desc}')

        if weights is None:
            # current: read from base config
            fw = base_fw
            raw_sum = sum(fw.values())
            print(f'  (uses base config as-is; raw sum = {raw_sum:.6f}, '
                  f'normalised internally by pysystemtrade)')
        else:
            ok = verify_weights(scheme_name, weights)
            all_ok = all_ok and ok
            fw = weights

        # Budget breakdown
        bd = compute_budget_breakdown(fw)
        print(f'  Effective budget: '
              f'trend={bd["trend"]:.1f}%  '
              f'carry={bd["carry"]:.1f}%  '
              f'network={bd["network"]:.1f}%  '
              f'sector={bd["sector"]:.1f}%')

        # Print individual weights for non-trend rules
        non_trend = {r: fw.get(r, 0.0)
                     for r in CARRY_RULES + NETWORK_RULES + SECTOR_RULES
                     if r in fw}
        if non_trend:
            raw_sum_nt = sum(fw.values())
            print('  Non-trend weights (raw → normalised):')
            for rule, w in non_trend.items():
                norm = w / raw_sum_nt * 100
                print(f'    {rule:<25}  {w:.6f}  → {norm:.3f}%')

    print()
    if all_ok:
        print('All weight sums verified ✓')
    else:
        print('ERROR: one or more weight sums do not equal 1.0 — fix before running backtests')
    print()


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


def print_comparison(results: list[dict], base_fw: dict) -> None:
    """Print formatted comparison table and decision recommendation."""
    print()
    print('=' * 130)
    print('EQUAL CARVER FAMILY WEIGHTS — COMPARISON RESULTS')
    print(f'Pre-refactor peak (additive sleeves): '
          f'Sharpe {PREREFACTOR_SHARPE:.4f}, Calmar {PREREFACTOR_CALMAR:.4f}, '
          f'MaxDD {PREREFACTOR_MAXDD*100:.2f}%')
    print('=' * 130)

    hdr = (
        f'{"Scheme":<24}  {"Fam":>4}  {"Trend%":>7}  {"Carry%":>7}  {"Net%":>5}  {"Sec%":>5}  '
        f'{"Sharpe":>8}  {"Calmar":>8}  {"CAGR":>8}  {"Vol":>8}  {"MaxDD":>8}  '
        f'{"ΔSharpe%":>9}  {"ΔCalmar":>8}  {"ΔMaxDD":>8}'
    )
    print(hdr)
    print('─' * 130)

    n_families = {
        'current':        '—',
        'config_a_10fam': '10',
        'config_b_9fam':   '9',
        'config_c_8fam':   '8',
    }

    baseline = None
    for r in results:
        scheme   = r['scheme']
        m        = r.get('metrics', {})
        label    = SCHEME_LABELS.get(scheme, scheme)
        sharpe   = m.get('sharpe',        float('nan'))
        calmar   = m.get('calmar',        float('nan'))
        cagr     = m.get('cagr',          float('nan'))
        vol      = m.get('ann_vol',       float('nan'))
        maxdd    = m.get('max_dd',        float('nan'))

        # Budget breakdown
        if WEIGHT_SCHEMES[scheme] is None:
            fw = base_fw
        else:
            fw = WEIGHT_SCHEMES[scheme]
        bd = compute_budget_breakdown(fw)

        if baseline is None:
            baseline     = r
            d_sharpe_pct = 0.0
            d_calmar     = 0.0
            d_maxdd      = 0.0
            tag = ' ← baseline'
        else:
            b_m          = baseline.get('metrics', {})
            b_sharpe     = b_m.get('sharpe', float('nan'))
            d_sharpe_pct = (sharpe - b_sharpe) / b_sharpe * 100
            d_calmar     = calmar - b_m.get('calmar', float('nan'))
            d_maxdd      = (maxdd - b_m.get('max_dd', float('nan'))) * 100
            tag = ''

        print(
            f'{label:<24}  '
            f'{n_families.get(scheme, "?"):>4}  '
            f'{bd["trend"]:>6.1f}%  '
            f'{bd["carry"]:>6.1f}%  '
            f'{bd["network"]:>4.1f}%  '
            f'{bd["sector"]:>4.1f}%  '
            f'{sharpe:>8.4f}  '
            f'{calmar:>8.4f}  '
            f'{cagr*100:>7.2f}%  '
            f'{vol*100:>7.2f}%  '
            f'{maxdd*100:>7.2f}%  '
            f'{d_sharpe_pct:>+8.1f}%  '
            f'{d_calmar:>+8.4f}  '
            f'{d_maxdd:>+6.2f}pp'
            f'{tag}'
        )

    print('─' * 130)
    print()

    # ── Decision logic ──────────────────────────────────────────────────────
    if not baseline:
        return

    b_m      = baseline.get('metrics', {})
    b_sharpe = b_m.get('sharpe', float('nan'))
    b_maxdd  = b_m.get('max_dd', float('nan'))

    print('DECISION CRITERIA:')
    print('  Pick highest Sharpe among A/B/C')
    print('  Reject any scheme where MaxDD worsens >5pp vs current')
    print('  Keep current if ALL 3 underperform by >3% Sharpe')
    print()

    candidates = []
    for r in results[1:]:  # skip current (baseline)
        scheme  = r['scheme']
        m       = r.get('metrics', {})
        sharpe  = m.get('sharpe',  float('nan'))
        maxdd   = m.get('max_dd',  float('nan'))
        calmar  = m.get('calmar',  float('nan'))

        d_sharpe_pct = (sharpe - b_sharpe) / b_sharpe * 100
        d_maxdd_pp   = (maxdd  - b_maxdd)  * 100

        maxdd_ok = d_maxdd_pp > -5.0
        label    = SCHEME_LABELS.get(scheme, scheme)

        if maxdd_ok:
            status = '✓ eligible'
            candidates.append(r)
        else:
            status = '✗ EXCLUDED (MaxDD worsens >5pp)'

        print(
            f'  {label:<24}  '
            f'ΔSharpe={d_sharpe_pct:+.1f}%  '
            f'ΔMaxDD={d_maxdd_pp:+.1f}pp  '
            f'ΔCalmar={calmar - b_m.get("calmar", float("nan")):+.4f}  '
            f'→ {status}'
        )

    print()

    if not candidates:
        print('  No scheme passes MaxDD gate. KEEP CURRENT weights.')
        return

    # Check if all eligible candidates underperform by >3%
    best_candidate = max(
        candidates,
        key=lambda r: r.get('metrics', {}).get('sharpe', float('-inf'))
    )
    best_sharpe   = best_candidate.get('metrics', {}).get('sharpe', float('nan'))
    best_d_sharpe = (best_sharpe - b_sharpe) / b_sharpe * 100

    if best_d_sharpe < -3.0:
        print(
            f'  Best eligible candidate ({SCHEME_LABELS.get(best_candidate["scheme"])}) '
            f'underperforms current by {best_d_sharpe:.1f}% (> -3% threshold).'
        )
        print('  RECOMMENDATION: Keep current flat-0.05 weights.')
        print('  (Current initialization is near-optimal despite not being Carver-compliant.)')
        print()
        return

    best_label = SCHEME_LABELS.get(best_candidate['scheme'], best_candidate['scheme'])
    best_m     = best_candidate.get('metrics', {})

    print(f'  RECOMMENDATION: Adopt {best_label}  '
          f'(ΔSharpe={best_d_sharpe:+.1f}%  '
          f'ΔMaxDD={(best_m.get("max_dd", 0) - b_maxdd)*100:+.2f}pp)')
    print()
    print('  NEXT STEP: Update config/crypto_perps_full_rules.yaml forecast_weights:')
    weights = WEIGHT_SCHEMES[best_candidate['scheme']]
    if weights:
        for rule, w in sorted(weights.items()):
            print(f'    {rule}: {w:.6f}')
    print()

    # Pre-refactor peak comparison
    print(f'  Best vs pre-refactor peak (additive sleeves):')
    print(f'    Sharpe: {best_sharpe:.4f} vs {PREREFACTOR_SHARPE:.4f}  '
          f'(Δ={(best_sharpe - PREREFACTOR_SHARPE) / PREREFACTOR_SHARPE * 100:+.1f}%)')
    print(f'    MaxDD:  {best_m.get("max_dd", 0)*100:.2f}% vs {PREREFACTOR_MAXDD*100:.2f}%')
    print()


def main():
    parser = argparse.ArgumentParser(
        description='Equal Carver family weights: 3-way comparison (A/B/C vs current).',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        '--config', type=Path,
        default=Path('config/crypto_perps_full_rules.yaml'),
    )
    parser.add_argument(
        '--data', type=Path,
        default=Path('data/dataset_538registry_6yr_jagged.parquet'),
    )
    parser.add_argument(
        '--outdir', type=Path,
        default=Path('out/family_weights_sweep'),
    )
    parser.add_argument(
        '--dry-run', action='store_true',
        help='Print weight tables and budget breakdowns, then exit (no backtests).',
    )
    parser.add_argument(
        '--skip-existing', action='store_true',
        help='Skip runs where performance_summary.json already exists.',
    )

    args = parser.parse_args()

    if not args.config.exists():
        print(f'ERROR: config not found: {args.config}')
        sys.exit(1)

    base_cfg = load_yaml(args.config)
    base_fw  = base_cfg.get('forecast_weights', {})

    print()
    print(f'Base config:  {args.config}')
    print(f'Data:         {args.data}')
    print(f'Output dir:   {args.outdir}')
    print()

    # Always print the dry-run information (also when running full sweep)
    print_dry_run(base_fw)

    if args.dry_run:
        print('Dry run complete — exiting (no backtests run).')
        return

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
    print()

    args.outdir.mkdir(parents=True, exist_ok=True)

    results = []

    for scheme_name, weights in WEIGHT_SCHEMES.items():
        label    = SCHEME_LABELS[scheme_name]
        run_outdir = args.outdir / scheme_name

        print(f'{"─" * 70}')
        print(f'Running: {label}  →  {run_outdir}')

        if args.skip_existing and (run_outdir / 'performance_summary.json').exists():
            print('  Skipping — results already exist (--skip-existing)')
            r = load_results(run_outdir)
            r['scheme'] = scheme_name
            results.append(r)
            continue

        if weights is None:
            # current: use base config directly
            config_path = args.config
            tmp_path    = None
        else:
            # Build modified config with full forecast_weights replacement
            cfg = copy.deepcopy(base_cfg)
            cfg['forecast_weights'] = {k: float(v) for k, v in weights.items()}

            with tempfile.NamedTemporaryFile(
                mode='w', suffix='.yaml', delete=False, dir=args.outdir
            ) as tmp:
                yaml.dump(cfg, tmp, default_flow_style=False, sort_keys=False)
                tmp_path    = Path(tmp.name)
                config_path = tmp_path

        try:
            rc = run_backtest(config_path, args.data, run_outdir)
        finally:
            if tmp_path is not None:
                tmp_path.unlink(missing_ok=True)

        if rc != 0:
            print(f'  WARNING: backtest returned non-zero exit code {rc}')

        r = load_results(run_outdir)
        r['scheme'] = scheme_name
        results.append(r)

        m = r.get('metrics', {})
        print(
            f'  Sharpe={m.get("sharpe", float("nan")):.4f}  '
            f'Calmar={m.get("calmar", float("nan")):.4f}  '
            f'CAGR={m.get("cagr", 0) * 100:.2f}%  '
            f'MaxDD={m.get("max_dd", 0) * 100:.2f}%  '
            f'Crisis={m.get("crisis_return", 0) * 100:.2f}%'
        )

    print_comparison(results, base_fw)

    summary_path = args.outdir / 'family_weights_sweep_summary.json'
    with open(summary_path, 'w') as f:
        json.dump(results, f, indent=2, default=str)
    print(f'Full results saved: {summary_path}')


if __name__ == '__main__':
    main()
