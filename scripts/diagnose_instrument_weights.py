#!/usr/bin/env python3
"""
Instrument Weight Diagnosis (Handcraft at Instrument Level)

Runs pysystemtrade's built-in handcraft instrument weight estimator on all
300 crypto perps instruments. Compares handcraft-estimated weights vs equal
weights (1/N), and analyzes whether high-β_down instruments receive lower
weights — testing whether the direction-aware overlay is redundant with
what the handcraft already gives us.

The estimator uses per-instrument P&L (not pooled like forecast weights),
runs hierarchical-clustering + SR-based shrinkage over an expanding window,
and returns one weight per instrument. With 300 instruments and only 6 years
of data, heavy shrinkage toward equal weights is expected.

Usage:
    # Dry-run (syntax + import check, ~5 sec)
    python scripts/diagnose_instrument_weights.py --dry-run \\
        --config config/crypto_perps_full_rules.yaml \\
        --data data/dataset_538registry_6yr_jagged.parquet \\
        --outdir out/instrument_weight_diagnosis

    # Full run (~30-40 min)
    python scripts/diagnose_instrument_weights.py \\
        --config config/crypto_perps_full_rules.yaml \\
        --data data/dataset_538registry_6yr_jagged.parquet \\
        --outdir out/instrument_weight_diagnosis

Outputs:
    out/instrument_weight_diagnosis/
        instrument_weights.json   — per-instrument weights + beta + sector + ratio
        annual_snapshots.json     — year-end weight snapshots (normalized %)
        beta_correlation.json     — β_down correlation + quartile breakdown
"""

import argparse
import json
import logging
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

from sysdata.config.configdata import Config
from sysdata.crypto.parquet_perps_sim_data import parquetCryptoPerpsSimData
from systems.provided.crypto_example.core.dynamic_portfolio import CryptoDynamicPortfolio
from systems.crypto_perps.crypto_portfolio_oi_overlay import CryptoDynamicPortfolioWithOIOverlay
from systems.basesystem import System
from systems.forecasting import Rules
from systems.crypto_perps.forecast_combine_gated import ForecastCombineGated
from systems.forecast_scale_cap import ForecastScaleCap
from systems.rawdata import RawData
from systems.positionsizing import PositionSizing
from systems.accounts.accounts_stage import Account
from syscore.constants import arg_not_supplied

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# System builder (same boilerplate as diagnose_forecast_weights.py)
# ---------------------------------------------------------------------------

def _auto_discover(data_parent: Path, filename: str, description: str) -> str:
    """Return path string if file exists, else arg_not_supplied."""
    p = data_parent / filename
    if p.exists():
        logger.info(f"  Auto-discovered {description}: {p}")
        return str(p)
    logger.info(f"  {description} not found — {p}")
    return arg_not_supplied


def build_system(config_path: str, data_path: str) -> tuple:
    """
    Build system with use_instrument_weight_estimates injected in-memory.

    The YAML on disk is NOT modified. The injection overrides the fixed-weight
    path in Portfolios.get_unsmoothed_raw_instrument_weights(), causing the
    handcraft estimator to run instead of the dynamic universe logic.

    Returns (system, sim_data).
    """
    logger.info(f"Loading config: {config_path}")
    with open(config_path) as f:
        config_dict = yaml.safe_load(f)

    # Inject instrument weight estimation flag (in-memory only)
    config_dict['use_instrument_weight_estimates'] = True
    config = Config(config_dict)
    logger.info("  Injected use_instrument_weight_estimates: True")

    # Auto-discover auxiliary data files
    data_parent = Path(data_path).parent
    env_root_str = os.environ.get('LIVE_OPS_ENV_ROOT')
    env_root = Path(env_root_str) if env_root_str else Path.cwd()

    du = getattr(config, 'dynamic_universe', {}) or {}
    dynamic_universe_config = {
        'max_sr_cost_per_trade': du.get('max_sr_cost_per_trade', 0.01),
        'max_sr_cost_annual':    du.get('max_sr_cost_annual', 0.13),
        'stack_turnover':        du.get('stack_turnover', 15.0),
        'adv_window':            du.get('adv_window', 30),
        'fee_bps':               du.get('fee_bps', 5),
        'vol_window':            du.get('vol_window', 35),
    }

    sim_data = parquetCryptoPerpsSimData(
        dataset_path=data_path,
        config_path=config_path,
        env_root=env_root,
        use_dynamic_universe=True,
        dynamic_universe_config=dynamic_universe_config,
        macro_data_path=_auto_discover(data_parent, 'macro_factors.parquet', 'macro factors'),
        oi_data_path=_auto_discover(data_parent, 'binance_oi_processed.parquet', 'OI data'),
        sector_map_path=_auto_discover(data_parent, 'sector_map.json', 'sector map'),
        fg_data_path=_auto_discover(data_parent, 'fg_index.parquet', 'F&G index'),
        mvrv_data_path=_auto_discover(data_parent, 'mvrv_index.parquet', 'MVRV index'),
        active_addresses_data_path=_auto_discover(data_parent, 'active_addresses.parquet', 'active addresses'),
        market_cap_data_path=_auto_discover(data_parent, 'market_cap.parquet', 'market cap'),
    )
    n_instr = len(sim_data.get_instrument_list())
    logger.info(f"  Loaded {n_instr} instruments")

    use_oi_overlay   = config.get_element_or_default('use_oi_overlay', False)
    use_fg_overlay   = config.get_element_or_default('use_fg_overlay', False)
    use_mvrv_overlay = config.get_element_or_default('use_mvrv_overlay', False)
    use_any_overlay  = use_oi_overlay or use_fg_overlay or use_mvrv_overlay

    portfolio_stage = (
        CryptoDynamicPortfolioWithOIOverlay() if use_any_overlay
        else CryptoDynamicPortfolio()
    )

    system = System(
        stage_list=[
            Account(),
            portfolio_stage,
            PositionSizing(),
            ForecastCombineGated(),
            ForecastScaleCap(),
            Rules(),
            RawData(),
        ],
        data=sim_data,
        config=config,
    )

    logger.info("✓ System built (ForecastCombineGated + Account stage)")
    return system, sim_data


# ---------------------------------------------------------------------------
# Weight extraction
# ---------------------------------------------------------------------------

def extract_instrument_weights(system) -> dict:
    """
    Trigger the handcraft instrument weight estimator and return both the
    smoothed daily weights and the raw optimizer output.

    One call computes all instruments simultaneously (~30-40 min total).

    Returns:
        {
            'weights_df': TxK DataFrame — EWM-smoothed daily weights
            'raw_df':     TxK DataFrame — raw optimizer output (pre-smoothing)
        }
    """
    logger.info("Computing smoothed instrument weights (all instruments)...")
    logger.info("One call triggers the full handcraft estimation (~30-40 min).\n")

    # Smoothed daily weights (span=125 business days EWM)
    weights_df = system.portfolio.get_instrument_weights()
    logger.info(f"  Smoothed weights shape: {weights_df.shape}")

    # Raw estimated weights (pre-smoothing, typically weekly/annual frequency)
    logger.info("Computing raw (pre-smoothed) instrument weights...")
    raw_df = system.portfolio.get_raw_estimated_instrument_weights()
    logger.info(f"  Raw weights shape:      {raw_df.shape}")

    return {
        'weights_df': weights_df,
        'raw_df':     raw_df,
    }


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------

def _get_beta_series(sim_data) -> pd.Series:
    """Most recent β_down per instrument. Returns empty Series if panel not available."""
    panel = sim_data._downside_beta_panel
    if panel is None or (hasattr(panel, 'empty') and panel.empty):
        logger.warning(
            "_downside_beta_panel is None or empty — β_down analysis unavailable.\n"
            "  Ensure use_downside_beta_overlay: true in config to enable the panel."
        )
        return pd.Series(dtype=float)
    beta = panel.iloc[-1].rename('beta_down')
    logger.info(f"  β_down series: {beta.count()} non-NaN values, "
                f"median={beta.median():.3f}")
    return beta


def _get_sector_map(sim_data) -> dict:
    """Return {instrument: sector} from sim_data._sector_map. Empty dict if not loaded."""
    m = sim_data._sector_map
    if m is None:
        return {}
    return dict(m)


def _annual_snapshots(raw_df: pd.DataFrame) -> dict:
    """
    Return {year: {instrument: weight}} using the last raw optimizer row per year.
    """
    df = raw_df.copy()
    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.DatetimeIndex(df.index)

    snapshots = {}
    for year in sorted(df.index.year.unique()):
        year_rows = df.loc[df.index.year == year]
        if not year_rows.empty:
            snapshots[int(year)] = year_rows.iloc[-1].to_dict()
    return snapshots


# ---------------------------------------------------------------------------
# Output / printing
# ---------------------------------------------------------------------------

def build_rows(final_weights: pd.Series, beta_series: pd.Series, sector_map: dict) -> list:
    """Build the per-instrument row dicts for printing + JSON output."""
    n_total = len(final_weights)
    one_over_n = 1.0 / n_total if n_total > 0 else 0.0

    rows = []
    for instrument in final_weights.index:
        est = float(final_weights[instrument])
        ratio = est / one_over_n if one_over_n > 0 else None
        beta_val = beta_series.get(instrument, float('nan'))
        beta = float(beta_val) if not pd.isna(beta_val) else None
        sector = sector_map.get(instrument, 'Unknown')
        rows.append({
            'instrument':     instrument,
            'est_pct':        round(est * 100, 6),
            'one_over_n_pct': round(one_over_n * 100, 6),
            'ratio':          round(ratio, 4) if ratio is not None else None,
            'beta_down':      round(beta, 4) if beta is not None else None,
            'sector':         sector,
        })

    return sorted(rows, key=lambda x: x['est_pct'], reverse=True)


def print_weight_table(rows: list, n: int = 30) -> None:
    """Print top-N and bottom-N rows."""
    n_total = len(rows)
    one_over_n_pct = rows[0]['one_over_n_pct'] if rows else 0.0

    print()
    print("=" * 84)
    print(f"INSTRUMENT WEIGHT DIAGNOSIS  (N={n_total}, 1/N = {one_over_n_pct:.4f}%)")
    print("=" * 84)

    hdr = (f"{'Instrument':<26}  {'Est%':>8}  {'1/N%':>7}  "
           f"{'Ratio':>6}  {'β_down':>7}  {'Sector':<12}")
    sep = "-" * 84

    def fmt_row(r):
        beta_s  = f"{r['beta_down']:>7.3f}" if r['beta_down'] is not None else "    n/a"
        ratio_s = f"{r['ratio']:>6.3f}" if r['ratio'] is not None else "   n/a"
        return (f"{r['instrument']:<26}  {r['est_pct']:>7.4f}%  {r['one_over_n_pct']:>6.4f}%  "
                f"{ratio_s}  {beta_s}  {r['sector']:<12}")

    print(f"\nTOP {n} (highest estimated weight):")
    print(hdr)
    print(sep)
    for r in rows[:n]:
        print(fmt_row(r))

    print(f"\nBOTTOM {n} (lowest estimated weight):")
    print(hdr)
    print(sep)
    for r in rows[-n:]:
        print(fmt_row(r))

    print("=" * 84)


def print_distribution_summary(final_weights: pd.Series) -> dict:
    """Print weight dispersion statistics vs 1/N."""
    n_total = len(final_weights)
    one_over_n = 1.0 / n_total if n_total > 0 else 0.0
    deviation = final_weights - one_over_n
    within_20pct = ((deviation.abs() / one_over_n) < 0.20).sum()
    pct_within_20 = 100.0 * within_20pct / n_total if n_total > 0 else 0.0

    max_idx = final_weights.idxmax()
    min_idx = final_weights.idxmin()

    print()
    print("DISTRIBUTION SUMMARY")
    print("-" * 62)
    print(f"  Equal weight (1/N):             {one_over_n*100:.4f}%")
    print(f"  Mean estimated:                 {final_weights.mean()*100:.4f}%"
          "  (by construction ≈ 1/N)")
    print(f"  Median estimated:               {final_weights.median()*100:.4f}%")
    print(f"  Std deviation:                  {final_weights.std()*100:.4f}%")
    print(f"  Max deviation from 1/N:         +{(final_weights.max()-one_over_n)*100:.4f}%"
          f"  ({max_idx})")
    print(f"                                  -{(one_over_n-final_weights.min())*100:.4f}%"
          f"  ({min_idx})")
    print(f"  % within ±20% of 1/N:           {pct_within_20:.1f}%")

    if pct_within_20 > 90:
        print("  → Handcraft is essentially equal-weighted (heavy shrinkage).")
    elif pct_within_20 > 70:
        print("  → Modest deviation from equal weights.")
    else:
        print("  → Significant weight dispersion — handcraft has strong preferences.")
    print("-" * 62)

    return {
        'n_instruments':        n_total,
        'one_over_n_pct':       round(one_over_n * 100, 6),
        'mean_pct':             round(final_weights.mean() * 100, 6),
        'median_pct':           round(final_weights.median() * 100, 6),
        'std_pct':              round(final_weights.std() * 100, 6),
        'max_dev_pct':          round((final_weights.max() - one_over_n) * 100, 6),
        'max_dev_instrument':   max_idx,
        'min_dev_pct':          round((one_over_n - final_weights.min()) * 100, 6),
        'min_dev_instrument':   min_idx,
        'pct_within_20pct_of_equal': round(pct_within_20, 2),
    }


def print_beta_correlation(rows: list) -> dict:
    """
    Analyze Pearson + Spearman correlation between estimated weight and β_down.
    Print quartile breakdown and interpretation.
    """
    try:
        from scipy import stats
    except ImportError:
        logger.warning("scipy not available — skipping β_down correlation analysis")
        return {}

    valid = [
        (r['est_pct'], r['beta_down'])
        for r in rows
        if r['beta_down'] is not None and r['est_pct'] is not None
    ]

    if len(valid) < 10:
        logger.warning(
            f"Only {len(valid)} instruments have β_down data — "
            "correlation analysis skipped."
        )
        return {}

    weights_arr = np.array([v[0] for v in valid])
    betas_arr   = np.array([v[1] for v in valid])

    corr,          pvalue          = stats.pearsonr(weights_arr, betas_arr)
    spearman_corr, spearman_pvalue = stats.spearmanr(weights_arr, betas_arr)

    print()
    print("β_DOWN CORRELATION ANALYSIS")
    print("-" * 62)
    print(f"  N instruments with β_down data:   {len(valid)}")
    print(f"  Pearson  corr(weight, β_down):     {corr:+.3f}  (p={pvalue:.4f})")
    print(f"  Spearman corr(weight, β_down):     {spearman_corr:+.3f}  (p={spearman_pvalue:.4f})")

    # Quartile breakdown
    q25, q50, q75 = np.percentile(betas_arr, [25, 50, 75])
    one_over_n = weights_arr.mean()  # = 1/N since weights sum to 1 on average

    quartile_results = {}
    print(f"\n  β_down quartile breakdown:")
    print(f"  {'Quartile':<28}  {'β range':>14}  {'Avg wt%':>8}  {'vs 1/N':>8}")
    print(f"  {'-'*63}")

    for q_name, lo, hi, inclusive_right in [
        ('Q1 (lowest β_down)',  -np.inf, q25, False),
        ('Q2',                   q25,    q50, False),
        ('Q3',                   q50,    q75, False),
        ('Q4 (highest β_down)', q75,    np.inf, True),
    ]:
        if inclusive_right or hi == np.inf:
            mask = betas_arr >= lo
        else:
            mask = (betas_arr >= lo) & (betas_arr < hi)

        q_weights = weights_arr[mask]
        avg_w = float(q_weights.mean()) if len(q_weights) > 0 else float('nan')
        dev = (avg_w - one_over_n) / one_over_n * 100 if (one_over_n > 0 and not np.isnan(avg_w)) else 0.0

        lo_str = f"{lo:.2f}" if lo != -np.inf else "  -∞"
        hi_str = f"{hi:.2f}" if hi != np.inf  else "+∞"

        print(f"  {q_name:<28}  {lo_str:>7}–{hi_str:<6}  {avg_w:>7.4f}%  {dev:>+7.1f}%")

        quartile_results[q_name] = {
            'beta_lo':             None if lo == -np.inf else round(float(lo), 4),
            'beta_hi':             None if hi == np.inf  else round(float(hi), 4),
            'n':                   int(mask.sum()),
            'avg_weight_pct':      round(avg_w, 6) if not np.isnan(avg_w) else None,
            'dev_from_equal_pct':  round(dev, 2),
        }

    # Interpretation
    print()
    if corr < -0.15 and pvalue < 0.05:
        interp = (
            "Negative correlation (statistically significant): handcraft penalises "
            "high-β_down instruments via their lower risk-adjusted P&L. The direction-"
            "aware overlay and handcraft weights provide OVERLAPPING crash protection — "
            "some redundancy is likely."
        )
    elif corr < -0.05:
        interp = (
            "Weak negative correlation: handcraft mildly penalises high-β_down. "
            "Some overlap with the overlay exists, but both still add independent value."
        )
    elif abs(corr) < 0.05:
        interp = (
            "Near-zero correlation: handcraft is agnostic to β_down. The handcraft "
            "optimizer uses full-sample P&L, not tail-conditional risk. The direction-"
            "aware overlay adds ORTHOGONAL crash protection not captured by instrument "
            "weights alone — the two approaches are complementary."
        )
    else:
        interp = (
            "Positive correlation: high-β_down instruments receive HIGHER weights. "
            "Handcraft and overlay work in OPPOSITE directions on crash-prone instruments."
        )

    # Word-wrap for readability
    print("  Interpretation:")
    words = interp.split()
    line = "    "
    for w in words:
        if len(line) + len(w) + 1 > 78:
            print(line)
            line = "    " + w
        else:
            line += (" " if line != "    " else "") + w
    if line.strip():
        print(line)
    print("-" * 62)

    return {
        'n_valid':            len(valid),
        'pearson_corr':       round(float(corr), 4),
        'pearson_pvalue':     round(float(pvalue), 4),
        'spearman_corr':      round(float(spearman_corr), 4),
        'spearman_pvalue':    round(float(spearman_pvalue), 4),
        'interpretation':     interp,
        'quartile_breakdown': quartile_results,
    }


def print_sector_summary(rows: list) -> dict:
    """Print sector-level weight breakdown."""
    from collections import defaultdict
    sectors = defaultdict(list)
    for r in rows:
        sectors[r['sector']].append(r)

    print()
    print("SECTOR BREAKDOWN")
    print("-" * 84)
    print(f"{'Sector':<16}  {'N':>4}  {'Avg Est%':>9}  {'Avg 1/N%':>9}  "
          f"{'Avg Ratio':>10}  {'Avg β_down':>10}")
    print("-" * 84)

    sector_stats = {}
    # Sort by descending avg estimated weight
    sorted_sectors = sorted(
        sectors.keys(),
        key=lambda s: np.mean([r['est_pct'] for r in sectors[s]]),
        reverse=True,
    )

    for sector in sorted_sectors:
        rs = sectors[sector]
        avg_est   = float(np.mean([r['est_pct'] for r in rs]))
        avg_1n    = float(np.mean([r['one_over_n_pct'] for r in rs]))
        ratios    = [r['ratio'] for r in rs if r['ratio'] is not None]
        avg_ratio = float(np.mean(ratios)) if ratios else float('nan')
        betas     = [r['beta_down'] for r in rs if r['beta_down'] is not None]
        avg_beta  = float(np.mean(betas)) if betas else float('nan')

        ratio_s = f"{avg_ratio:>10.3f}" if not np.isnan(avg_ratio) else "       n/a"
        beta_s  = f"{avg_beta:>10.3f}"  if not np.isnan(avg_beta)  else "       n/a"
        print(f"{sector:<16}  {len(rs):>4}  {avg_est:>8.4f}%  {avg_1n:>8.4f}%  "
              f"{ratio_s}  {beta_s}")

        sector_stats[sector] = {
            'n':                 len(rs),
            'avg_est_pct':       round(avg_est, 6),
            'avg_one_over_n_pct': round(avg_1n, 6),
            'avg_ratio':         round(avg_ratio, 4) if not np.isnan(avg_ratio) else None,
            'avg_beta_down':     round(avg_beta, 4) if not np.isnan(avg_beta) else None,
        }

    print("-" * 84)
    return sector_stats


def print_annual_evolution(snapshots: dict, selected_instruments: list) -> None:
    """Print year-by-year weight evolution for selected instruments."""
    if not snapshots or not selected_instruments:
        return

    col_w = 13
    print()
    print("ANNUAL WEIGHT EVOLUTION (selected instruments, normalized %)")
    print("-" * (6 + 2 + (col_w + 2) * len(selected_instruments)))

    header = f"{'Year':<6}  " + "  ".join(
        f"{i[:col_w]:<{col_w}}" for i in selected_instruments
    )
    print(header)
    print("-" * len(header))

    for year, wts in sorted(snapshots.items()):
        total = sum(wts.values())
        if total <= 0:
            continue
        vals = []
        for i in selected_instruments:
            w = wts.get(i, float('nan'))
            if not np.isnan(w):
                norm_pct = w / total * 100
                vals.append(f"{norm_pct:{col_w-1}.4f}%")
            else:
                vals.append(f"{'—':>{col_w}}")
        print(f"{year:<6}  " + "  ".join(vals))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description='Instrument weight diagnosis (handcraft at instrument level)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument('--config',  type=Path, required=True, help='Config YAML path')
    parser.add_argument('--data',    type=Path, required=True, help='Dataset parquet path')
    parser.add_argument('--outdir',  type=Path, required=True, help='Output directory')
    parser.add_argument('--dry-run', action='store_true',
                        help='Build system and exit without computing weights')

    args = parser.parse_args()

    for p, name in [(args.config, 'Config'), (args.data, 'Data')]:
        if not p.exists():
            logger.error(f"{name} not found: {p}")
            sys.exit(1)

    args.outdir.mkdir(parents=True, exist_ok=True)

    logger.info("=" * 70)
    logger.info("INSTRUMENT WEIGHT DIAGNOSIS (handcraft at instrument level)")
    logger.info("=" * 70)

    # Step 1: Build system
    system, sim_data = build_system(str(args.config), str(args.data))

    if args.dry_run:
        logger.info("\n✓ DRY RUN COMPLETE — system built successfully, exiting.")
        sys.exit(0)

    # Step 2: Extract β_down and sector metadata
    beta_series = _get_beta_series(sim_data)
    sector_map  = _get_sector_map(sim_data)

    if beta_series.empty:
        logger.warning("β_down panel unavailable — β_down columns will show n/a.")
    if not sector_map:
        logger.warning("Sector map unavailable — sector columns will show 'Unknown'.")

    # Step 3: Trigger weight estimation
    logger.info("\nTriggering instrument weight estimation (this is the slow step)...")
    result     = extract_instrument_weights(system)
    weights_df = result['weights_df']
    raw_df     = result['raw_df']

    # Step 4: Final weights = last row of smoothed DataFrame, normalized
    final_weights = weights_df.iloc[-1].dropna()
    total = final_weights.sum()
    if total > 0:
        final_weights = final_weights / total
    final_weights = final_weights.sort_values(ascending=False)

    logger.info(f"\n✓ Estimation complete: {len(final_weights)} instruments")

    # Step 5: Build row list for analysis
    rows = build_rows(final_weights, beta_series, sector_map)

    # Step 6: Print main comparison table (top/bottom 30)
    print_weight_table(rows, n=30)

    # Step 7: Distribution summary
    dist_summary = print_distribution_summary(final_weights)

    # Step 8: β_down correlation analysis
    beta_corr = {}
    try:
        beta_corr = print_beta_correlation(rows)
    except Exception as e:
        logger.warning(f"β_down correlation analysis failed: {e}", exc_info=True)

    # Step 9: Sector summary
    sector_summary = print_sector_summary(rows)

    # Step 10: Annual weight evolution
    snapshots = _annual_snapshots(raw_df)
    # Show top 5 by estimated weight + bottom 3 for contrast
    top5     = [r['instrument'] for r in rows[:5]]
    bottom3  = [r['instrument'] for r in rows[-3:]]
    selected = top5 + [i for i in bottom3 if i not in top5]
    print_annual_evolution(snapshots, selected)

    # Step 11: Save outputs
    print()
    logger.info("Saving outputs...")

    # instrument_weights.json
    out_weights = {
        'instruments':        rows,
        'distribution':       dist_summary,
        'sector_summary':     sector_summary,
    }
    out_weights_path = args.outdir / 'instrument_weights.json'
    with open(out_weights_path, 'w') as f:
        json.dump(out_weights, f, indent=2, default=str)
    logger.info(f"  Saved: {out_weights_path}")

    # annual_snapshots.json (normalized to %)
    readable_snapshots = {}
    for year, wts in snapshots.items():
        total = sum(wts.values())
        if total > 0:
            readable_snapshots[str(year)] = {
                k: round(v / total * 100, 4) for k, v in wts.items()
            }
        else:
            readable_snapshots[str(year)] = {k: 0.0 for k in wts}
    out_snap_path = args.outdir / 'annual_snapshots.json'
    with open(out_snap_path, 'w') as f:
        json.dump(readable_snapshots, f, indent=2, default=str)
    logger.info(f"  Saved: {out_snap_path}")

    # beta_correlation.json
    out_beta_path = args.outdir / 'beta_correlation.json'
    with open(out_beta_path, 'w') as f:
        json.dump(beta_corr, f, indent=2, default=str)
    logger.info(f"  Saved: {out_beta_path}")

    logger.info(f"\n✓ DIAGNOSIS COMPLETE — outputs in: {args.outdir}")


if __name__ == '__main__':
    main()
