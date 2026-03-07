#!/usr/bin/env python3
"""
Empirical Forecast Weight Diagnosis

Runs pysystemtrade's built-in handcraft weight estimator on the 29-rule crypto
stack to identify which rules are over/under-weighted vs the flat YAML weights.

The estimator pools P&L across all 300 instruments × all rules, runs a
hierarchical-clustering + SR-based shrinkage optimiser (date_method: expanding),
and produces walk-forward monthly weight estimates. This has never been run on
the crypto system before — the goal is a pure diagnostic, no config changes.

Usage:
    # Dry-run (syntax + import check, ~5 sec)
    python scripts/diagnose_forecast_weights.py --dry-run \\
        --config config/crypto_perps_full_rules.yaml \\
        --data data/dataset_538registry_6yr_jagged.parquet \\
        --outdir out/forecast_weight_diagnosis

    # Single instrument smoke test (~12-15 min; first instrument triggers pool)
    python scripts/diagnose_forecast_weights.py \\
        --config config/crypto_perps_full_rules.yaml \\
        --data data/dataset_538registry_6yr_jagged.parquet \\
        --outdir out/forecast_weight_diagnosis \\
        --instruments BTCUSDT_PERP

    # Full diagnostic with 3 instruments (~13-16 min total)
    python scripts/diagnose_forecast_weights.py \\
        --config config/crypto_perps_full_rules.yaml \\
        --data data/dataset_538registry_6yr_jagged.parquet \\
        --outdir out/forecast_weight_diagnosis \\
        --instruments BTCUSDT_PERP ETHUSDT_PERP SOLUSDT_PERP

Outputs:
    out/forecast_weight_diagnosis/weight_comparison.json
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
# Rule metadata
# ---------------------------------------------------------------------------

# Family-level grouping for the comparison table (29 active rules)
RULE_FAMILY = {
    'ewmac_8': 'EWMAC', 'ewmac_16': 'EWMAC', 'ewmac_32': 'EWMAC',
    'breakout_20': 'Breakout', 'breakout_40': 'Breakout',
    'breakout_80': 'Breakout', 'breakout_160': 'Breakout',
    'normmom_8': 'Normmom', 'normmom_16': 'Normmom', 'normmom_32': 'Normmom',
    'accel_16': 'Accel', 'accel_32': 'Accel', 'accel_64': 'Accel',
    'assettrend_8': 'Assettrend', 'assettrend_16': 'Assettrend',
    'assettrend_32': 'Assettrend', 'assettrend_64': 'Assettrend',
    'relmomentum_20': 'Relmomentum', 'relmomentum_40': 'Relmomentum',
    'residual_momentum_16': 'ResidMom', 'residual_momentum_32': 'ResidMom',
    'residual_momentum_64': 'ResidMom',
    'gated_carry_10': 'Carry', 'gated_carry_30': 'Carry', 'gated_carry_60': 'Carry',
    'xs_carry': 'XSCarry',
    'xs_activity': 'Network', 'xs_val': 'Network',
    'inter_sector': 'Sector',
}

# Budget-level aggregation (for the summary block)
BUDGET_GROUPS = {
    'TREND': ['EWMAC', 'Breakout', 'Normmom', 'Accel', 'Assettrend', 'Relmomentum', 'ResidMom'],
    'CARRY': ['Carry'],
    'XS_CARRY': ['XSCarry'],
    'NETWORK': ['Network'],
    'SECTOR': ['Sector'],
}

# Rules diagnostically interesting for the year-by-year evolution table
CARRY_XS_RULES = [
    'gated_carry_10', 'gated_carry_30', 'gated_carry_60',
    'xs_carry', 'xs_activity', 'xs_val', 'inter_sector',
]

DEFAULT_INSTRUMENTS = ['BTCUSDT_PERP', 'ETHUSDT_PERP', 'SOLUSDT_PERP']


# ---------------------------------------------------------------------------
# System builder
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
    Build a pysystemtrade System with use_forecast_weight_estimates injected.

    Returns (system, sim_data) where sim_data is the parquetCryptoPerpsSimData instance.
    The config dict is modified in-memory; the YAML file on disk is NOT touched.
    """
    logger.info(f"Loading config: {config_path}")
    with open(config_path) as f:
        config_dict = yaml.safe_load(f)

    # Inject weight estimation flag (in-memory only — production YAML unchanged)
    config_dict['use_forecast_weight_estimates'] = True
    config = Config(config_dict)
    n_weights = len(config_dict.get('forecast_weights', {}))
    logger.info(f"  Injected use_forecast_weight_estimates: True")
    logger.info(f"  forecast_weights entries in YAML: {n_weights}")

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
    logger.info(f"  Loaded {len(sim_data.get_instrument_list())} instruments")

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

def extract_weights(system, instruments: list) -> dict:
    """
    Call the estimation machinery for each instrument and collect results.

    First instrument triggers the full pool P&L computation (~12-15 min).
    Subsequent instruments reuse the cached results and are fast (<1 min each).

    Returns:
        {instrument: {
            'final_weights':   {rule: float},   # last row of monthly optimizer output
            'annual_snapshots': {year: {rule: float}},  # last monthly row per year
            'daily_weights_last': {rule: float},  # last row of EWM-smoothed daily weights
        }} or {instrument: None} on failure.
    """
    results = {}

    for idx, instrument in enumerate(instruments):
        logger.info(f"\n[{idx+1}/{len(instruments)}] Computing weights for {instrument}...")
        if idx == 0:
            logger.info("  (First instrument triggers full pool computation — ~12-15 min)")
        else:
            logger.info("  (Reusing cached pool P&L — should be fast)")

        try:
            # EWM-smoothed daily weights (span=125 business days)
            daily_w = system.combForecast.get_forecast_weights(instrument)
            logger.info(f"  Daily weights shape: {daily_w.shape}")

            # Raw monthly optimizer output (no smoothing, one row per optimisation period)
            monthly_w = system.combForecast.get_raw_monthly_forecast_weights(instrument)
            logger.info(f"  Monthly weights shape: {monthly_w.shape}")

            # Final weight = last row of the expanding-window optimizer
            final_weights = monthly_w.iloc[-1].to_dict()

            # Year-by-year: last optimizer row per calendar year
            monthly_w.index = pd.DatetimeIndex(monthly_w.index)
            annual_snapshots = {}
            for year in sorted(monthly_w.index.year.unique()):
                year_mask = monthly_w.index.year == year
                year_rows = monthly_w.loc[year_mask]
                if not year_rows.empty:
                    annual_snapshots[int(year)] = year_rows.iloc[-1].to_dict()

            daily_last = daily_w.iloc[-1].to_dict()

            results[instrument] = {
                'final_weights':    final_weights,
                'annual_snapshots': annual_snapshots,
                'daily_weights_last': daily_last,
            }
            logger.info(f"  ✓ Done: {len(final_weights)} rules estimated")

        except Exception as e:
            logger.error(f"  FAILED for {instrument}: {e}", exc_info=True)
            results[instrument] = None

    return results


# ---------------------------------------------------------------------------
# Output / analysis
# ---------------------------------------------------------------------------

def _normalize(weights: dict) -> dict:
    """Normalize a dict of weights to sum to 1."""
    total = sum(abs(v) for v in weights.values())
    if total == 0:
        return dict(weights)
    return {k: v / total for k, v in weights.items()}


def print_comparison_table(yaml_weights: dict, instrument_results: dict) -> dict:
    """
    Print the YAML-vs-estimated comparison table and budget summary.

    Returns a dict with 'comparison', 'budget_summary', 'yaml_weights_normalized',
    and 'estimated_weights_normalized' for JSON serialization.
    """
    successful = {k: v for k, v in instrument_results.items() if v is not None}
    if not successful:
        logger.error("No successful weight estimates — cannot produce comparison table.")
        return {}

    # Normalize YAML weights to sum=1 (only the active rules in forecast_weights)
    yaml_norm = _normalize(yaml_weights)

    # Average estimated final_weights across instruments, then normalize
    all_rules = set(yaml_norm.keys())
    for res in successful.values():
        all_rules.update(res['final_weights'].keys())
    all_rules = sorted(all_rules)

    raw_est = {}
    for rule in all_rules:
        vals = [
            res['final_weights'][rule]
            for res in successful.values()
            if rule in res['final_weights']
        ]
        raw_est[rule] = float(np.mean(vals)) if vals else 0.0

    est_norm = _normalize(raw_est)

    # --- Comparison table ---
    print()
    print("=" * 78)
    print("EMPIRICAL FORECAST WEIGHT DIAGNOSIS")
    print(f"Instruments: {list(successful.keys())}")
    print("=" * 78)
    header = f"{'Rule':<30}  {'YAML%':>7}  {'Est%':>7}  {'Ratio':>7}  {'Family':<12}"
    print(header)
    print("-" * 78)

    comparison = {}
    for rule in all_rules:
        yaml_pct = yaml_norm.get(rule, 0.0) * 100
        est_pct  = est_norm.get(rule, 0.0) * 100
        if yaml_pct > 0:
            ratio = est_pct / yaml_pct
            ratio_str = f"{ratio:>7.2f}"
        else:
            ratio = None
            ratio_str = "   n/a"
        family = RULE_FAMILY.get(rule, 'Other')

        print(f"{rule:<30}  {yaml_pct:>6.2f}%  {est_pct:>6.2f}%  {ratio_str}  {family:<12}")

        comparison[rule] = {
            'yaml_pct': round(yaml_pct, 4),
            'est_pct':  round(est_pct, 4),
            'ratio':    round(ratio, 4) if ratio is not None else None,
            'family':   family,
        }

    print("-" * 78)

    # --- Budget summary ---
    print(f"\n{'Budget':<12}  {'YAML%':>7}  {'Est%':>7}  {'Delta':>10}")
    print("-" * 45)

    budget_summary = {}
    for budget_name, families in BUDGET_GROUPS.items():
        rules_in_budget = [r for r, fam in RULE_FAMILY.items() if fam in families]
        yaml_budget = sum(yaml_norm.get(r, 0.0) for r in rules_in_budget) * 100
        est_budget  = sum(est_norm.get(r, 0.0)  for r in rules_in_budget) * 100
        delta = est_budget - yaml_budget
        print(f"{budget_name:<12}  {yaml_budget:>6.1f}%  {est_budget:>6.1f}%  {delta:>+9.1f}pp")
        budget_summary[budget_name] = {
            'yaml_pct':  round(yaml_budget, 2),
            'est_pct':   round(est_budget, 2),
            'delta_pp':  round(delta, 2),
        }

    print("=" * 78)

    return {
        'comparison':                 comparison,
        'budget_summary':             budget_summary,
        'yaml_weights_normalized':    {k: round(v, 6) for k, v in yaml_norm.items()},
        'estimated_weights_normalized': {k: round(v, 6) for k, v in est_norm.items()},
    }


def print_carry_xs_evolution(instrument_results: dict) -> None:
    """
    Print year-by-year estimated weight evolution for carry + XS rules.

    Only prints for the first successful instrument to keep output concise.
    These are the diagnostically interesting rules — trend rules will be near-equal.
    """
    successful = {k: v for k, v in instrument_results.items() if v is not None}
    if not successful:
        return

    # Use first successful instrument
    instrument, res = next(iter(successful.items()))
    annual = res['annual_snapshots']
    if not annual:
        return

    years = sorted(annual.keys())

    # Find which carry/XS rules actually appear in the data
    rules_present = [
        r for r in CARRY_XS_RULES
        if any(r in annual[y] for y in years)
    ]
    if not rules_present:
        logger.info("No carry/XS rules found in annual snapshots.")
        return

    print(f"\nYear-by-year estimated weight evolution ({instrument}):")
    col_w = 10  # width per column
    header = f"{'Year':<6}  " + "  ".join(f"{r[:col_w]:<{col_w}}" for r in rules_present)
    print(header)
    print("-" * len(header))

    for year in years:
        row = annual[year]
        # Each row from the optimizer already sums to ~1; normalize for safety
        row_norm = _normalize(row)
        vals = "  ".join(
            f"{row_norm.get(r, 0.0) * 100:>8.2f}% " for r in rules_present
        )
        print(f"{year:<6}  {vals}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description='Empirical forecast weight diagnosis (handcraft estimator)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument('--config',  type=Path, required=True,
                        help='Config YAML path')
    parser.add_argument('--data',    type=Path, required=True,
                        help='Dataset parquet path')
    parser.add_argument('--outdir',  type=Path, required=True,
                        help='Output directory for weight_comparison.json')
    parser.add_argument('--instruments', nargs='+', default=DEFAULT_INSTRUMENTS,
                        help=f'Instruments to sample (default: {DEFAULT_INSTRUMENTS})')
    parser.add_argument('--dry-run', action='store_true',
                        help='Build system and exit without computing weights')

    args = parser.parse_args()

    for p, name in [(args.config, 'Config'), (args.data, 'Data')]:
        if not p.exists():
            logger.error(f"{name} not found: {p}")
            sys.exit(1)

    args.outdir.mkdir(parents=True, exist_ok=True)

    logger.info("=" * 70)
    logger.info("EMPIRICAL FORECAST WEIGHT DIAGNOSIS")
    logger.info("=" * 70)

    # Step 1: Build system with estimation flag injected
    system, sim_data = build_system(str(args.config), str(args.data))

    if args.dry_run:
        logger.info("\n✓ DRY RUN COMPLETE — system built successfully, exiting.")
        sys.exit(0)

    # Step 2: Validate instrument selection
    available = set(sim_data.get_instrument_list())
    instruments = [i for i in args.instruments if i in available]
    missing     = [i for i in args.instruments if i not in available]
    if missing:
        logger.warning(f"Instruments not in dataset (skipping): {missing}")
    if not instruments:
        logger.error("No valid instruments to process.")
        sys.exit(1)
    logger.info(f"\nInstruments to process: {instruments}")

    # Step 3: Trigger estimation and extract results
    instrument_results = extract_weights(system, instruments)

    # Step 4: Load YAML weights for comparison (raw, not normalized)
    with open(args.config) as f:
        raw_config = yaml.safe_load(f)
    yaml_weights = raw_config.get('forecast_weights', {})

    # Step 5: Print comparison table + budget summary
    analysis = print_comparison_table(yaml_weights, instrument_results)

    # Step 6: Print year-by-year carry/XS evolution
    print_carry_xs_evolution(instrument_results)

    # Step 7: Save JSON output
    output = {
        'instruments': instruments,
        'final_weights': {
            inst: res['final_weights'] if res else None
            for inst, res in instrument_results.items()
        },
        'annual_snapshots': {
            inst: (
                {str(yr): wts for yr, wts in res['annual_snapshots'].items()}
                if res else None
            )
            for inst, res in instrument_results.items()
        },
        'daily_weights_last': {
            inst: res['daily_weights_last'] if res else None
            for inst, res in instrument_results.items()
        },
        'yaml_weights_normalized':    analysis.get('yaml_weights_normalized', {}),
        'estimated_weights_normalized': analysis.get('estimated_weights_normalized', {}),
        'budget_summary':             analysis.get('budget_summary', {}),
        'comparison':                 analysis.get('comparison', {}),
    }

    out_file = args.outdir / 'weight_comparison.json'
    with open(out_file, 'w') as f:
        json.dump(output, f, indent=2, default=str)

    logger.info(f"\n✓ Results saved: {out_file}")
    logger.info("✓ DIAGNOSIS COMPLETE")


if __name__ == '__main__':
    main()
