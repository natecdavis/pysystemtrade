#!/usr/bin/env python3
"""
Carver Audit Script — Static Diagnostics

Loads existing backtest output (diagnostics.parquet + performance_summary.json)
and produces a Carver checklist report. No new backtests are run.

Checks:
  A. Forecast Distribution — mean |FC|, std, cap hit rate (|FC| ≥ 19.9)
  B. Rule Weight Budget — family allocations, carry double-count
  C. Capital Config — capital vs notional_trading_capital
  D. FDM Status — walk-forward FDM enabled?
  E. Coverage Bias — CoinMetrics-covered vs total, active address instruments
  F. Cost Check — reported txn cost vs turnover × spread

Outputs:
  out/carver_audit/CHECKLIST.md

Usage:
    python scripts/audit_carver.py \\
        --diagnostics out/xs_val_sweep/w0p50/diagnostics.parquet \\
        --config config/crypto_perps_full_rules.yaml \\
        --outdir out/carver_audit
"""

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

# ---------------------------------------------------------------------------
# CoinMetrics-covered instrument list (from scripts/download_active_addresses.py)
# ---------------------------------------------------------------------------
CM_COVERED = {
    'BTCUSDT_PERP', 'ETHUSDT_PERP', 'SOLUSDT_PERP', 'XRPUSDT_PERP',
    'BNBUSDT_PERP', 'ADAUSDT_PERP', 'DOGEUSDT_PERP', 'LTCUSDT_PERP',
    'DOTUSDT_PERP', 'LINKUSDT_PERP', 'AVAXUSDT_PERP', 'MATICUSDT_PERP',
    'UNIUSDT_PERP', 'ATOMUSDT_PERP', 'XLMUSDT_PERP', 'ETCUSDT_PERP',
    'AAVEUSDT_PERP', 'VETUSDT_PERP', 'FILUSDT_PERP', 'SANDUSDT_PERP',
    'MANAUSDT_PERP', 'AXSUSDT_PERP', 'XMRUSDT_PERP', 'ALGOUSDT_PERP',
    'EGLDUSDT_PERP', 'HBARUSDT_PERP', 'NEARUSDT_PERP', 'ICPUSDT_PERP',
    'FTMUSDT_PERP', 'THETAUSDT_PERP', 'ZECUSDT_PERP', 'APEUSDT_PERP',
    'GALAUSDT_PERP', 'GRTUSDT_PERP', 'SUSHIUSDT_PERP', 'MKRUSDT_PERP',
    'SNXUSDT_PERP', 'COMPUSDT_PERP', 'YFIUSDT_PERP', 'CRVUSDT_PERP',
    '1INCHUSDT_PERP',
}
# Market cap instruments (41 — same minus TRX which returns 400 on community tier)
MCAP_COVERED = CM_COVERED - {'TRXUSDT_PERP'}


# ---------------------------------------------------------------------------
# Rule family definitions
# ---------------------------------------------------------------------------
RULE_FAMILIES = {
    'EWMAC':     ['ewmac_8', 'ewmac_16', 'ewmac_32'],
    'Breakout':  ['breakout_20', 'breakout_40', 'breakout_80', 'breakout_160'],
    'Normmom':   ['normmom_8', 'normmom_16', 'normmom_32'],
    'Accel':     ['accel_16', 'accel_32', 'accel_64'],
    'Assettrend':['assettrend_8', 'assettrend_16', 'assettrend_32', 'assettrend_64'],
    'RelMom':    ['relmomentum_20', 'relmomentum_40'],
    'ResMom':    ['residual_momentum_16', 'residual_momentum_32', 'residual_momentum_64'],
    'Carry':     ['vol_norm_carry_10', 'vol_norm_carry_30', 'vol_norm_carry_60'],
}

EXPECTED_FAMILY_WEIGHT = 1.0 / 7  # 7 non-carry families → ~14.3% each
CARRY_RULES = {'vol_norm_carry_10', 'vol_norm_carry_30', 'vol_norm_carry_60'}


def load_diagnostics(path: Path) -> pd.DataFrame:
    df = pd.read_parquet(path)
    if 'date' in df.columns:
        df['date'] = pd.to_datetime(df['date'])
    return df


def load_config(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def load_performance(diagnostics_path: Path) -> dict:
    """Try to load performance_summary.json from same directory as diagnostics."""
    summary_path = diagnostics_path.parent / 'performance_summary.json'
    if not summary_path.exists():
        return {}
    with open(summary_path) as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Check A: Forecast distribution
# ---------------------------------------------------------------------------
def check_forecast_distribution(diag: pd.DataFrame, lines: list) -> str:
    lines.append('\n## A. Forecast Distribution Analysis\n')

    fc_col = 'combined_forecast'
    if fc_col not in diag.columns:
        lines.append('- **WARN**: `combined_forecast` column not found in diagnostics.')
        return 'WARN'

    fc = diag[fc_col].dropna()
    fc = fc[fc != 0.0]  # remove zeros (unpositioned instruments)

    if len(fc) == 0:
        lines.append('- **FAIL**: No non-zero forecast values found.')
        return 'FAIL'

    mean_abs = float(fc.abs().mean())
    median_abs = float(fc.abs().median())
    std_fc = float(fc.std())
    pct_positive = float((fc > 0).mean() * 100)
    cap_thresh = 19.9
    cap_hit_rate = float((fc.abs() >= cap_thresh).mean() * 100)
    near_cap_rate = float((fc.abs() >= 15.0).mean() * 100)  # >15 is "approaching cap"

    lines.append(f'- **Mean |FC|**: {mean_abs:.2f} (Carver target ≈ 10.0)')
    lines.append(f'- **Median |FC|**: {median_abs:.2f}')
    lines.append(f'- **Std FC**: {std_fc:.2f}')
    lines.append(f'- **% Positive**: {pct_positive:.1f}%')
    lines.append(f'- **Cap hit rate** (|FC| ≥ 19.9): {cap_hit_rate:.1f}% of non-zero (date, instrument) pairs')
    lines.append(f'- **Near-cap rate** (|FC| ≥ 15.0): {near_cap_rate:.1f}% of non-zero pairs')

    # Percentiles
    pcts = [1, 5, 25, 50, 75, 95, 99]
    p_vals = [float(np.percentile(fc, p)) for p in pcts]
    pct_str = '  | ' + ' | '.join(f'{p}%' for p in pcts) + ' |'
    val_str = '  | ' + ' | '.join(f'{v:.1f}' for v in p_vals) + ' |'
    lines.append(f'\nPercentile table:')
    lines.append(pct_str)
    lines.append('  | ' + ' | '.join('---' for _ in pcts) + ' |')
    lines.append(val_str)

    # Verdict
    verdict = 'PASS'
    if mean_abs > 15.0:
        lines.append(f'\n- **FAIL**: Mean |FC| = {mean_abs:.2f} far exceeds Carver target of 10.')
        lines.append('  Additive sleeves are pushing combined forecasts well above calibration.')
        lines.append('  The ±20 cap is truncating genuine signal.')
        verdict = 'FAIL'
    elif mean_abs > 12.0:
        lines.append(f'\n- **WARN**: Mean |FC| = {mean_abs:.2f} exceeds Carver target (10) by >{(mean_abs-10)/10*100:.0f}%.')
        lines.append('  Cap is being hit for some instruments. Consider reducing sleeve weights.')
        verdict = 'WARN'
    else:
        lines.append(f'\n- **PASS**: Mean |FC| = {mean_abs:.2f} is close to Carver target of 10.')

    if cap_hit_rate > 10.0:
        lines.append(f'- **FAIL**: Cap hit rate {cap_hit_rate:.1f}% > 10% threshold.')
        lines.append('  More than 1 in 10 active forecasts are capped — sleeves generating truncated noise.')
        verdict = 'FAIL'
    elif cap_hit_rate > 5.0:
        lines.append(f'- **WARN**: Cap hit rate {cap_hit_rate:.1f}% > 5% — borderline.')
        if verdict == 'PASS':
            verdict = 'WARN'
    else:
        lines.append(f'- **PASS**: Cap hit rate {cap_hit_rate:.1f}% ≤ 5%.')

    return verdict


# ---------------------------------------------------------------------------
# Check B: Rule weight budget
# ---------------------------------------------------------------------------
def check_rule_weights(cfg: dict, lines: list) -> str:
    lines.append('\n## B. Rule Weight Budget Check\n')

    fw = cfg.get('forecast_weights', {})
    total_weight = sum(fw.values())

    lines.append(f'- **Total forecast_weights sum**: {total_weight:.4f} (expected 1.0)')

    # Family breakdown
    lines.append('\n**Forecast weights by family:**\n')
    lines.append('| Family | Rules | Total Weight | % Budget |')
    lines.append('|--------|-------|-------------|---------|')

    family_weights = {}
    for family, rules in RULE_FAMILIES.items():
        w = sum(fw.get(r, 0.0) for r in rules)
        family_weights[family] = w
        pct = w / total_weight * 100 if total_weight > 0 else 0
        rules_str = ', '.join(rules)
        lines.append(f'| {family} | `{rules_str}` | {w:.4f} | {pct:.1f}% |')

    # Check carry double-counting
    carry_weight_cfg = float(cfg.get('carry_weight', 0.0))
    use_gated_carry = cfg.get('use_gated_carry', False)
    carry_in_fw = sum(fw.get(r, 0.0) for r in CARRY_RULES)
    carry_rules_enabled = [r for r in CARRY_RULES if fw.get(r, 0.0) > 0.0]

    lines.append(f'\n**Carry double-count analysis:**')
    lines.append(f'- Carry rules in `forecast_weights`: {carry_rules_enabled} → sum = {carry_in_fw:.4f}')
    lines.append(f'- `use_gated_carry`: {use_gated_carry}')
    lines.append(f'- `carry_weight` (sleeve): {carry_weight_cfg}')

    verdict = 'PASS'

    if use_gated_carry and carry_in_fw > 0.0 and carry_weight_cfg > 0.0:
        lines.append(
            f'\n- **WARN**: Carry rules contribute via BOTH `forecast_weights` ({carry_in_fw:.4f} total) '
            f'AND the gated carry sleeve (`carry_weight={carry_weight_cfg}`). '
            f'This is double-counting. The forecast_weights contribution is tiny (3%) but '
            f'adds noise. Recommend zeroing `vol_norm_carry_*` in `forecast_weights` to '
            f'eliminate double-counting and reduce forecast_weights sum to 1.0.'
        )
        if verdict == 'PASS':
            verdict = 'WARN'
    elif use_gated_carry and carry_in_fw > 0.0 and carry_weight_cfg == 0.0:
        lines.append(f'- **INFO**: Carry rules in forecast_weights (carry_weight=0) — standard combination only.')
    else:
        lines.append(f'- **PASS**: No carry double-counting detected.')

    # Check 7-family equal budget (excluding carry)
    trend_families = {f: w for f, w in family_weights.items() if f != 'Carry'}
    trend_total = sum(trend_families.values())
    lines.append(f'\n**7-family equal-budget check (excluding Carry):**')
    lines.append(f'- Total trend budget: {trend_total:.4f}')
    for family, w in sorted(trend_families.items(), key=lambda x: -x[1]):
        pct = w / trend_total * 100 if trend_total > 0 else 0
        target_pct = 100.0 / 7
        delta = pct - target_pct
        flag = '✓' if abs(delta) < 3.0 else '⚠'
        lines.append(f'  - {family}: {w:.4f} ({pct:.1f}%, target {target_pct:.1f}%, Δ={delta:+.1f}pp) {flag}')

    return verdict


# ---------------------------------------------------------------------------
# Check C: Capital config
# ---------------------------------------------------------------------------
def check_capital_config(cfg: dict, perf: dict, lines: list) -> str:
    lines.append('\n## C. Capital Configuration Check\n')

    # pysystemtrade's PositionSizing uses config.notional_trading_capital
    ntc = cfg.get('notional_trading_capital', None)
    system_section = cfg.get('system', {})
    system_capital = system_section.get('capital', None) if isinstance(system_section, dict) else None

    lines.append(f'- `notional_trading_capital` (top-level): {ntc}')
    lines.append(f'  → Used by: pysystemtrade PositionSizing, custom P&L backtester')
    lines.append(f'- `system.capital` (nested): {system_capital}')
    lines.append(f'  → Status: **DEAD CONFIG** — pysystemtrade reads `notional_trading_capital`, not `system.capital`')

    # Vol target check
    vol_target = system_section.get('vol_target_ann', None) if isinstance(system_section, dict) else None
    pct_vol = cfg.get('percentage_vol_target', None)
    lines.append(f'- `system.vol_target_ann`: {vol_target}')
    lines.append(f'- `percentage_vol_target` (top-level): {pct_vol}')

    if perf:
        m = perf.get('metrics', {})
        reported_vol = m.get('ann_vol', None)
        reported_cagr = m.get('cagr', None)
        if reported_vol is not None:
            lines.append(f'\n**Reported metrics:**')
            lines.append(f'- Annual vol: {reported_vol:.1%}')
            lines.append(f'- CAGR: {reported_cagr:.1%}')

    verdict = 'PASS'

    if ntc is None:
        lines.append('\n- **WARN**: `notional_trading_capital` not set — system will use pysystemtrade default ($1000).')
        verdict = 'WARN'
    elif ntc <= 0:
        lines.append(f'\n- **FAIL**: `notional_trading_capital` = {ntc} ≤ 0.')
        verdict = 'FAIL'
    else:
        lines.append(f'\n- **PASS**: `notional_trading_capital = {ntc}` is consistent.')

    if system_capital is not None and ntc is not None and system_capital != ntc:
        lines.append(
            f'- **WARN**: `system.capital = {system_capital}` ≠ `notional_trading_capital = {ntc}`. '
            f'The `system.capital` key is DEAD (not read by any code) — but it is confusing. '
            f'Recommend removing or aligning it. Sharpe is scale-invariant (unaffected). '
            f'CAGR is computed vs `notional_trading_capital = {ntc}`, positions sized for {ntc} — consistent.'
        )
        if verdict == 'PASS':
            verdict = 'WARN'

    return verdict


# ---------------------------------------------------------------------------
# Check D: FDM configuration
# ---------------------------------------------------------------------------
def check_fdm_config(cfg: dict, lines: list) -> str:
    lines.append('\n## D. Forecast Diversification Multiplier (FDM) Check\n')

    use_fdm_estimates = cfg.get('use_forecast_div_mult_estimates', False)
    use_scalar_estimates = cfg.get('use_forecast_scale_estimates', False)
    fdm_cap = cfg.get('forecast_div_mult_cap', None)

    lines.append(f'- `use_forecast_div_mult_estimates`: {use_fdm_estimates}')
    lines.append(f'  → Walk-forward FDM estimation: {"ENABLED" if use_fdm_estimates else "DISABLED (fixed FDM)"}')
    lines.append(f'- `use_forecast_scale_estimates`: {use_scalar_estimates}')
    lines.append(f'- `forecast_div_mult_cap`: {fdm_cap}')

    # Architecture concern
    lines.append(
        f'\n**Known architecture limitation:** FDM is estimated from the `forecast_weights` '
        f'correlation matrix (22 trend+carry rules only). The 5 additive sleeves '
        f'(xscarry, inter_sector, xs_activity, xs_addr_growth, xs_val) add signal '
        f'AFTER FDM is applied, so the FDM does NOT account for sleeve correlations. '
        f'This means the FDM will correctly diversify the trend+carry portion, but '
        f'the combined forecast (post-sleeves) may exceed the calibrated mean |FC| = 10 '
        f'target if sleeves are large relative to the trend signal.'
    )

    sleeve_weights = {
        'xscarry':          cfg.get('xscarry_weight', 0.0),
        'inter_sector':     cfg.get('inter_sector_weight', 0.0),
        'xs_activity':      cfg.get('xs_activity_weight', 0.0),
        'xs_addr_growth':   cfg.get('xs_addr_growth_weight', 0.0),
        'xs_val':           cfg.get('xs_val_weight', 0.0),
    }
    carry_weight = cfg.get('carry_weight', 0.0)
    total_additive = sum(sleeve_weights.values()) + (carry_weight if cfg.get('use_gated_carry', False) else 0)

    lines.append(f'\n**Additive sleeve total effective weight:**')
    for name, w in sleeve_weights.items():
        lines.append(f'  - {name}: {w:.2f}')
    if cfg.get('use_gated_carry', False):
        lines.append(f'  - gated_carry: {carry_weight:.2f}')
    lines.append(f'  - **Total**: {total_additive:.2f}')
    lines.append(f'  - Note: Each sleeve is ±20 scale, so total additive signal range = ±{total_additive * 20:.0f} on top of the capped trend')

    verdict = 'PASS'
    if use_fdm_estimates:
        lines.append(f'\n- **PASS**: Walk-forward FDM estimation is enabled (good).')
    else:
        lines.append(f'\n- **WARN**: FDM is fixed (not walk-forward estimated). Walk-forward FDM is more robust.')
        verdict = 'WARN'

    if total_additive > 2.0:
        lines.append(
            f'- **WARN**: Total additive sleeve weight = {total_additive:.2f}. '
            f'Sleeves can contribute up to ±{total_additive * 20:.0f} to the combined forecast '
            f'before the ±20 cap, potentially overwhelming the trend signal. '
            f'Check forecast distribution (Check A) for cap hit rate.'
        )
        if verdict == 'PASS':
            verdict = 'WARN'

    return verdict


# ---------------------------------------------------------------------------
# Check E: Coverage bias
# ---------------------------------------------------------------------------
def check_coverage_bias(diag: pd.DataFrame, lines: list) -> str:
    lines.append('\n## E. CoinMetrics Coverage Bias Analysis\n')

    all_instruments = set(diag['instrument'].unique()) if 'instrument' in diag.columns else set()
    n_total = len(all_instruments)

    cm_in_universe = all_instruments & CM_COVERED
    n_cm = len(cm_in_universe)
    mcap_in_universe = all_instruments & MCAP_COVERED
    n_mcap = len(mcap_in_universe)

    lines.append(f'- Total instruments in diagnostics: {n_total}')
    lines.append(f'- CoinMetrics AdrActCnt covered: {n_cm} ({n_cm/n_total*100:.1f}%)')
    lines.append(f'- CoinMetrics CapMrktCurUSD covered: {n_mcap} ({n_mcap/n_total*100:.1f}%)')
    lines.append(f'- Uncovered (get forecast=0 for xs_activity/xs_val): {n_total - n_cm} ({(n_total - n_cm)/n_total*100:.1f}%)')

    if 'instrument' not in diag.columns or 'combined_forecast' not in diag.columns:
        lines.append('- **WARN**: Cannot compute per-group Sharpe — missing columns.')
        return 'WARN'

    # Compare average |FC| for covered vs uncovered instruments
    # (only look at non-zero forecasts)
    diag_nonzero = diag[diag['combined_forecast'] != 0.0].copy()
    diag_nonzero['covered'] = diag_nonzero['instrument'].isin(CM_COVERED)

    fc_covered = diag_nonzero[diag_nonzero['covered']]['combined_forecast'].abs()
    fc_uncovered = diag_nonzero[~diag_nonzero['covered']]['combined_forecast'].abs()

    mean_fc_covered = float(fc_covered.mean()) if len(fc_covered) > 0 else float('nan')
    mean_fc_uncovered = float(fc_uncovered.mean()) if len(fc_uncovered) > 0 else float('nan')

    lines.append(f'\n**Mean |FC| by coverage group** (non-zero positions only):')
    lines.append(f'- CoinMetrics-covered ({n_cm} instruments): {mean_fc_covered:.2f}')
    lines.append(f'- Uncovered ({n_total - n_cm} instruments): {mean_fc_uncovered:.2f}')

    cap_rate_covered = float((fc_covered >= 19.9).mean() * 100) if len(fc_covered) > 0 else float('nan')
    cap_rate_uncovered = float((fc_uncovered >= 19.9).mean() * 100) if len(fc_uncovered) > 0 else float('nan')

    lines.append(f'- Cap hit rate (|FC| ≥ 19.9) — covered: {cap_rate_covered:.1f}%')
    lines.append(f'- Cap hit rate (|FC| ≥ 19.9) — uncovered: {cap_rate_uncovered:.1f}%')

    # Fraction of active (non-zero) rows that are CoinMetrics-covered
    pct_covered_rows = float(diag_nonzero['covered'].mean() * 100)
    lines.append(f'\n- Fraction of active (date, instrument) pairs that are CM-covered: {pct_covered_rows:.1f}%')
    lines.append(
        f'  (Expected if uniform: {n_cm/n_total*100:.1f}%; CM instruments are larger caps '
        f'and likely in TopK more often, so this fraction is typically higher)'
    )

    verdict = 'PASS'

    if not np.isnan(mean_fc_covered) and not np.isnan(mean_fc_uncovered):
        fc_ratio = mean_fc_covered / mean_fc_uncovered if mean_fc_uncovered > 0 else float('inf')
        if fc_ratio > 1.5:
            lines.append(
                f'\n- **WARN**: Covered instruments have {fc_ratio:.1f}× higher mean |FC| than uncovered. '
                f'This is expected (they receive additional sleeve signal from xs_activity/xs_val), '
                f'but if coverage ≠ quality, this biases reported Sharpe upward. '
                f'Covered instruments are major caps (BTC, ETH, SOL) which are inherently more '
                f'trend-predictable — partially confounded.'
            )
            verdict = 'WARN'
        else:
            lines.append(f'\n- **PASS**: FC ratio (covered/uncovered) = {fc_ratio:.2f} — no extreme coverage bias.')

    return verdict


# ---------------------------------------------------------------------------
# Check F: Cost check
# ---------------------------------------------------------------------------
def check_costs(cfg: dict, perf: dict, lines: list) -> str:
    lines.append('\n## F. Cost Check\n')

    if not perf:
        lines.append('- **WARN**: No performance_summary.json found — cannot verify costs.')
        return 'WARN'

    m = perf.get('metrics', {})
    cm = perf.get('cost_model', {})
    port = perf.get('portfolio', {})

    txn_cost_ann = cm.get('transaction_cost_ann', None)
    funding_drag_ann = cm.get('funding_drag_ann', None)
    annual_turnover = port.get('annual_turnover', None)
    n_days = port.get('n_days', None)

    lines.append(f'- Reported annual transaction cost: {txn_cost_ann:.4f} ({txn_cost_ann*100:.2f}% p.a.)' if txn_cost_ann is not None else '- Reported annual transaction cost: N/A')
    lines.append(f'- Reported annual funding drag: {funding_drag_ann:.4f} ({funding_drag_ann*100:.2f}% p.a.)' if funding_drag_ann is not None else '- Reported annual funding drag: N/A')
    lines.append(f'- Annual turnover: {annual_turnover:.1f}× round-trips/yr' if annual_turnover is not None else '- Annual turnover: N/A')
    lines.append(f'- Backtest days: {n_days}')

    # Dynamic universe config
    du = cfg.get('dynamic_universe', {})
    fee_bps = du.get('fee_bps', 5.0) if isinstance(du, dict) else 5.0
    spread_assumption = 0.001  # ~10 bps typical spread

    if annual_turnover is not None and txn_cost_ann is not None:
        # Implied cost per round-trip from reported numbers
        if annual_turnover > 0:
            implied_cost_pct = txn_cost_ann / annual_turnover * 100
            lines.append(f'\n**Implied cost per round-trip**: {implied_cost_pct:.4f}% = {implied_cost_pct*100:.2f} bps')
            lines.append(f'  (Config fee_bps = {fee_bps} bps per leg; expected ~{fee_bps*2} bps round-trip)')

        # Carver SR cost check
        sharpe = m.get('sharpe', None)
        if sharpe is not None and annual_turnover > 0:
            sr_per_trade = sharpe / annual_turnover
            lines.append(f'\n**Carver SR-per-trade**: Sharpe / annual_turnover = {sharpe:.3f} / {annual_turnover:.1f} = {sr_per_trade:.4f}')
            lines.append(f'  (Carver max threshold: 0.01 SR/trade)')

    verdict = 'PASS'

    if txn_cost_ann is not None and txn_cost_ann > 0.02:
        lines.append(f'\n- **WARN**: Annual transaction cost {txn_cost_ann*100:.2f}% > 2% — high turnover.')
        verdict = 'WARN'
    elif txn_cost_ann is not None:
        lines.append(f'\n- **PASS**: Transaction cost {txn_cost_ann*100:.2f}% p.a. is reasonable.')

    if funding_drag_ann is not None and funding_drag_ann < -0.05:
        lines.append(f'- **WARN**: Funding drag {funding_drag_ann*100:.2f}% > 5% — significant carry cost.')
        if verdict == 'PASS':
            verdict = 'WARN'
    elif funding_drag_ann is not None:
        status = 'PASS' if funding_drag_ann >= 0 else 'INFO'
        lines.append(f'- **{status}**: Funding drag {funding_drag_ann*100:.2f}% p.a.')

    return verdict


# ---------------------------------------------------------------------------
# Check G: Sequential adoption inflation
# ---------------------------------------------------------------------------
def check_sequential_adoption(cfg: dict, lines: list) -> str:
    lines.append('\n## G. Sequential Adoption Inflation Assessment\n')

    sleeves_enabled = []
    sleeve_configs = [
        ('gated_carry',     cfg.get('use_gated_carry', False) and cfg.get('carry_weight', 0) > 0),
        ('xscarry',         cfg.get('xscarry_weight', 0.0) > 0),
        ('inter_sector',    cfg.get('inter_sector_weight', 0.0) > 0),
        ('xs_activity',     cfg.get('xs_activity_weight', 0.0) > 0),
        ('xs_addr_growth',  cfg.get('xs_addr_growth_weight', 0.0) > 0),
        ('xs_val',          cfg.get('xs_val_weight', 0.0) > 0),
        ('downside_beta',   cfg.get('use_downside_beta_overlay', False)),
        ('oi_overlay',      cfg.get('use_oi_overlay', False)),
    ]
    adoption_sharpe_gains = {
        'oi_overlay':    0.55,   # % gain at adoption (vs baseline)
        'gated_carry':   9.10,   # % gain at adoption
        'inter_sector':  21.4,   # % gain at adoption
        'xs_activity':   7.3,    # % gain at adoption
        'xs_addr_growth': 1.6,   # % gain at adoption
        'xs_val':        2.7,    # % gain at adoption
        'downside_beta': 5.9,    # % gain at adoption
        'xscarry':       9.1,    # % gain at adoption
    }

    lines.append('Each sleeve was adopted in sequence — later sleeves were tested on a system that')
    lines.append('already included all prior sleeves. The reported ΔSharpe at adoption ≠ true')
    lines.append('marginal contribution in the current combined stack.')
    lines.append('')
    lines.append('**Reported sequential ΔSharpe at adoption (in-sequence):**')
    lines.append('')
    lines.append('| Sleeve | Status | Sequential ΔSharpe | Note |')
    lines.append('|--------|--------|-------------------|------|')

    cumulative_multiplier = 1.0
    total_reported_gain = 0.0
    for sleeve, enabled in sleeve_configs:
        status = '✅ ON' if enabled else '❌ off'
        gain = adoption_sharpe_gains.get(sleeve, 0.0)
        if enabled:
            total_reported_gain += gain
        note = 'weakest — candidate for ablation review' if gain < 3.0 else ''
        lines.append(f'| {sleeve} | {status} | +{gain:.1f}% | {note} |')

    baseline_sharpe = 0.84
    current_sharpe = 1.5569
    actual_total_gain = (current_sharpe - baseline_sharpe) / baseline_sharpe * 100

    lines.append('')
    lines.append(f'**Sum of reported sequential gains**: +{total_reported_gain:.1f}%')
    lines.append(f'**Actual cumulative gain** (0.84 → 1.5569): +{actual_total_gain:.1f}%')
    lines.append(f'**Discrepancy**: {total_reported_gain - actual_total_gain:+.1f}pp')
    lines.append('')
    lines.append(
        'The sum of sequential gains is expected to exceed the actual gain due to '
        'interaction effects (each sleeve helps less than advertised when others are present). '
        'The ablation study (audit_sleeve_ablation.py) measures true marginal contributions.'
    )

    verdict = 'WARN'
    lines.append('\n- **WARN**: Sequential adoption inflates reported gains. True marginal contributions')
    lines.append('  measured by ablation study. Sleeves with sequential ΔSharpe < 3% are weakest candidates.')

    return verdict


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description='Carver Audit — static diagnostics from existing backtest output.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        '--diagnostics', type=Path,
        default=Path('out/xs_val_sweep/w0p50/diagnostics.parquet'),
        help='Path to diagnostics.parquet from a completed backtest run',
    )
    parser.add_argument(
        '--config', type=Path,
        default=Path('config/crypto_perps_full_rules.yaml'),
        help='Path to production config YAML',
    )
    parser.add_argument(
        '--outdir', type=Path,
        default=Path('out/carver_audit'),
        help='Output directory for CHECKLIST.md',
    )

    args = parser.parse_args()

    if not args.diagnostics.exists():
        print(f'ERROR: diagnostics file not found: {args.diagnostics}')
        sys.exit(1)
    if not args.config.exists():
        print(f'ERROR: config file not found: {args.config}')
        sys.exit(1)

    args.outdir.mkdir(parents=True, exist_ok=True)

    print(f'Loading diagnostics: {args.diagnostics}')
    diag = load_diagnostics(args.diagnostics)
    print(f'  Shape: {diag.shape}, columns: {diag.columns.tolist()}')

    print(f'Loading config: {args.config}')
    cfg = load_config(args.config)

    print(f'Loading performance summary...')
    perf = load_performance(args.diagnostics)

    lines = []
    lines.append('# Carver Audit Checklist')
    lines.append(f'\n**Date:** 2026-03-06')
    lines.append(f'**Config:** `{args.config}`')
    lines.append(f'**Diagnostics:** `{args.diagnostics}`')
    lines.append(f'**Baseline:** Sharpe 1.5569, CAGR 26.81%, Vol 16.09%, MaxDD −18.57%')
    lines.append('\n---\n')
    lines.append('| Check | Verdict |')
    lines.append('|-------|---------|')

    verdicts = {}

    # Run all checks
    lines_a = []
    verdicts['A_Forecast_Distribution'] = check_forecast_distribution(diag, lines_a)

    lines_b = []
    verdicts['B_Rule_Weights'] = check_rule_weights(cfg, lines_b)

    lines_c = []
    verdicts['C_Capital_Config'] = check_capital_config(cfg, perf, lines_c)

    lines_d = []
    verdicts['D_FDM'] = check_fdm_config(cfg, lines_d)

    lines_e = []
    verdicts['E_Coverage_Bias'] = check_coverage_bias(diag, lines_e)

    lines_f = []
    verdicts['F_Cost_Check'] = check_costs(cfg, perf, lines_f)

    lines_g = []
    verdicts['G_Sequential_Adoption'] = check_sequential_adoption(cfg, lines_g)

    # Summary table
    verdict_icons = {'PASS': '✅ PASS', 'WARN': '⚠ WARN', 'FAIL': '❌ FAIL'}
    check_names = {
        'A_Forecast_Distribution': 'A. Forecast Distribution',
        'B_Rule_Weights':          'B. Rule Weight Budget',
        'C_Capital_Config':        'C. Capital Config',
        'D_FDM':                   'D. FDM Status',
        'E_Coverage_Bias':         'E. Coverage Bias',
        'F_Cost_Check':            'F. Cost Check',
        'G_Sequential_Adoption':   'G. Sequential Adoption',
    }
    for key, name in check_names.items():
        v = verdicts[key]
        lines.append(f'| {name} | {verdict_icons.get(v, v)} |')

    # Overall verdict
    all_verdicts = list(verdicts.values())
    if 'FAIL' in all_verdicts:
        overall = 'FAIL'
    elif all_verdicts.count('WARN') >= 3:
        overall = 'WARN'
    else:
        overall = 'PASS'

    lines.append(f'\n**Overall:** {verdict_icons.get(overall, overall)}')

    # Append all check details
    lines.extend(lines_a)
    lines.extend(lines_b)
    lines.extend(lines_c)
    lines.extend(lines_d)
    lines.extend(lines_e)
    lines.extend(lines_f)
    lines.extend(lines_g)

    # Recommendations section
    lines.append('\n---\n')
    lines.append('## Summary Recommendations\n')

    if verdicts['A_Forecast_Distribution'] != 'PASS':
        lines.append(
            '1. **Forecast calibration**: Mean |FC| exceeds Carver target. '
            'Run ablation study to identify which sleeves are inflating forecasts. '
            'Consider reducing total sleeve weights.'
        )

    if verdicts['B_Rule_Weights'] != 'PASS':
        lines.append(
            '2. **Carry double-count fix**: Zero `vol_norm_carry_10/30/60` in `forecast_weights` '
            '(set to 0.0). These rules already contribute via the gated carry sleeve. '
            'This also makes forecast_weights sum = 1.0 exactly (currently 1.03).'
        )

    lines.append(
        '3. **Run ablation study**: `python scripts/audit_sleeve_ablation.py` '
        'to measure true marginal contribution of each sleeve. '
        'See adoption threshold: ΔSharpe ≥ 1.5% AND Calmar improvement.'
    )

    # Write output
    checklist_path = args.outdir / 'CHECKLIST.md'
    with open(checklist_path, 'w') as f:
        f.write('\n'.join(lines) + '\n')

    print(f'\n✓ Checklist written: {checklist_path}')
    print('\nSummary:')
    for key, name in check_names.items():
        v = verdicts[key]
        print(f'  {verdict_icons.get(v, v):15s} {name}')
    print(f'\nOverall: {verdict_icons.get(overall, overall)}')

    # Also save verdicts as JSON for programmatic use
    results_path = args.outdir / 'audit_results.json'
    with open(results_path, 'w') as f:
        json.dump({
            'verdicts': verdicts,
            'overall': overall,
            'config_path': str(args.config),
            'diagnostics_path': str(args.diagnostics),
        }, f, indent=2)
    print(f'✓ Results JSON: {results_path}')


if __name__ == '__main__':
    main()
