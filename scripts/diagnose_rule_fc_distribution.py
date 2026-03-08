#!/usr/bin/env python3
"""
Per-Rule Mean |FC| Diagnostic — pooled across a broad instrument sample.

Answers two questions:
1. Are all rules hitting mean |FC| ≈ 10 when pooled across instruments?
   (3-instrument sample was not representative; need large-cap + mid + small mix)

2. For gated_carry rules specifically: are the near-zero |FC| values for BTC/ETH
   expected given the gate logic, or is something broken?
   Shows: raw (pre-scalar) carry magnitude, gate pass-rate, scaled |FC| per instrument tier.

Usage:
    python scripts/diagnose_rule_fc_distribution.py \\
        --config config/crypto_perps_full_rules.yaml \\
        --data data/dataset_538registry_6yr_jagged.parquet
"""

import argparse
import logging
import sys
import os
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

logging.basicConfig(level=logging.WARNING, format='%(levelname)s %(message)s')
logger = logging.getLogger(__name__)


# Representative sample — large/mid/small caps + various sectors
# Deliberately broad to get a pooled picture that isn't dominated by BTC/ETH
SAMPLE_INSTRUMENTS = [
    # Large-cap L1
    'BTCUSDT_PERP', 'ETHUSDT_PERP', 'BNBUSDT_PERP', 'SOLUSDT_PERP', 'XRPUSDT_PERP',
    # Mid-cap L1
    'ADAUSDT_PERP', 'DOTUSDT_PERP', 'AVAXUSDT_PERP', 'ATOMUSDT_PERP', 'NEARUSDT_PERP',
    # DeFi
    'UNIUSDT_PERP', 'AAVEUSDT_PERP', 'LDOUSDT_PERP', 'STGUSDT_PERP', 'MORPHOUSDT_PERP',
    # AI / Meme
    'ACTUSDT_PERP', 'GRASSUSDT_PERP', 'AIXBTUSDT_PERP', 'COOKIEUSDT_PERP', 'SCRTUSDT_PERP',
    # Small-cap / older alts
    'KSMUSDT_PERP', 'CELRUSDT_PERP', 'RVNUSDT_PERP', 'DENTUSDT_PERP', 'COTIUSDT_PERP',
    # Gaming / Infra
    'ILVUSDT_PERP', 'SANDUSDT_PERP', 'PORTALUSDT_PERP', 'DRIFTUSDT_PERP', 'ARKUSDT_PERP',
]

GATED_CARRY_RULES = ['gated_carry_10', 'gated_carry_30', 'gated_carry_60']

# Large-cap tier for gated carry deep-dive
LARGE_CAP = ['BTCUSDT_PERP', 'ETHUSDT_PERP', 'BNBUSDT_PERP', 'SOLUSDT_PERP', 'XRPUSDT_PERP']
MID_CAP   = ['ADAUSDT_PERP', 'DOTUSDT_PERP', 'AVAXUSDT_PERP', 'ATOMUSDT_PERP', 'NEARUSDT_PERP']
SMALL_CAP = ['STGUSDT_PERP', 'COTIUSDT_PERP', 'KSMUSDT_PERP', 'DRIFTUSDT_PERP', 'ARKUSDT_PERP']


def _auto_discover(data_parent, filename, description):
    p = data_parent / filename
    if p.exists():
        return str(p)
    return arg_not_supplied


def build_system(config_path, data_path):
    with open(config_path) as f:
        cfg = yaml.safe_load(f)
    config = Config(cfg)

    dp = Path(data_path).parent
    env_root = Path(os.environ.get('LIVE_OPS_ENV_ROOT', Path.cwd()))
    du = getattr(config, 'dynamic_universe', {}) or {}

    sim_data = parquetCryptoPerpsSimData(
        dataset_path=data_path,
        config_path=config_path,
        env_root=env_root,
        use_dynamic_universe=True,
        dynamic_universe_config={
            'max_sr_cost_per_trade': du.get('max_sr_cost_per_trade', 0.01),
            'max_sr_cost_annual':    du.get('max_sr_cost_annual', 0.13),
            'stack_turnover':        du.get('stack_turnover', 15.0),
            'adv_window':            du.get('adv_window', 30),
            'fee_bps':               du.get('fee_bps', 5),
            'vol_window':            du.get('vol_window', 35),
        },
        sector_map_path=_auto_discover(dp, 'sector_map.json', 'sector'),
        active_addresses_data_path=_auto_discover(dp, 'active_addresses.parquet', 'aa'),
        market_cap_data_path=_auto_discover(dp, 'market_cap.parquet', 'mc'),
        macro_data_path=_auto_discover(dp, 'macro_factors.parquet', 'macro'),
        oi_data_path=_auto_discover(dp, 'binance_oi_processed.parquet', 'oi'),
        fg_data_path=_auto_discover(dp, 'fg_index.parquet', 'fg'),
        mvrv_data_path=_auto_discover(dp, 'mvrv_index.parquet', 'mvrv'),
    )

    use_any = any([
        config.get_element_or_default('use_oi_overlay', False),
        config.get_element_or_default('use_fg_overlay', False),
        config.get_element_or_default('use_mvrv_overlay', False),
    ])
    portfolio_stage = CryptoDynamicPortfolioWithOIOverlay() if use_any else CryptoDynamicPortfolio()

    return System(
        stage_list=[Account(), portfolio_stage, PositionSizing(),
                    ForecastCombineGated(), ForecastScaleCap(), Rules(), RawData()],
        data=sim_data, config=config,
    ), sim_data


def get_fc(system, instrument, rule):
    """Return capped forecast series, or None on failure."""
    try:
        return system.forecastScaleCap.get_capped_forecast(instrument, rule)
    except Exception:
        return None


def get_raw(system, instrument, rule):
    """Return raw (pre-scalar) forecast series, or None on failure."""
    try:
        return system.rules.get_raw_forecast(instrument, rule)
    except Exception:
        return None


def main():
    parser = argparse.ArgumentParser(description='Per-rule |FC| distribution diagnostic')
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--data',   type=Path, required=True)
    args = parser.parse_args()

    print("Building system...")
    system, sim_data = build_system(str(args.config), str(args.data))

    available = set(sim_data.get_instrument_list())
    sample = [i for i in SAMPLE_INSTRUMENTS if i in available]
    missing = [i for i in SAMPLE_INSTRUMENTS if i not in available]
    if missing:
        print(f"  Not in dataset (skipping): {missing}")
    print(f"  Sample: {len(sample)} instruments\n")

    with open(args.config) as f:
        cfg = yaml.safe_load(f)
    rules = list(cfg.get('forecast_weights', {}).keys())
    yaml_weights = cfg.get('forecast_weights', {})

    # -------------------------------------------------------------------------
    # Q1: Per-rule mean |FC| pooled across all sample instruments
    # -------------------------------------------------------------------------
    print("Computing per-rule mean |FC| across sample instruments...")
    print("(First instrument per rule triggers scalar estimation — subsequent are fast)\n")

    rule_stats = {}
    for rule in rules:
        abs_vals = []
        nan_instruments = []
        for instr in sample:
            fc = get_fc(system, instr, rule)
            if fc is None or fc.dropna().empty:
                nan_instruments.append(instr)
                continue
            abs_vals.extend(fc.abs().dropna().tolist())

        if abs_vals:
            rule_stats[rule] = {
                'mean_abs':    np.mean(abs_vals),
                'median_abs':  np.median(abs_vals),
                'pct_zero':    np.mean(np.array(abs_vals) == 0.0) * 100,
                'pct_at_cap':  np.mean(np.array(abs_vals) >= 19.9) * 100,
                'n_ok':        len(sample) - len(nan_instruments),
                'nan_instrs':  nan_instruments,
            }
        else:
            rule_stats[rule] = None

    # Print Q1 table
    print("=" * 82)
    print(f"Q1: PER-RULE MEAN |FC|  (pooled across {len(sample)} instruments, target = 10)")
    print("=" * 82)
    print(f"{'Rule':<28}  {'YAML%':>6}  {'Mean|FC|':>9}  {'Median':>8}  "
          f"{'%Zero':>7}  {'%AtCap':>7}  {'N':>4}  Status")
    print("-" * 82)

    for rule in rules:
        s = rule_stats.get(rule)
        yw = yaml_weights.get(rule, 0)
        if s is None:
            print(f"{rule:<28}  {yw*100:>5.1f}%  {'NaN':>9}  {'NaN':>8}  "
                  f"{'NaN':>7}  {'NaN':>7}  {'0':>4}  ⚠ ALL NaN")
            continue

        mean_fc = s['mean_abs']
        if mean_fc > 13:
            status = "↑ overshoot"
        elif mean_fc > 11:
            status = "↑ high"
        elif mean_fc >= 9:
            status = "✓ on target"
        elif mean_fc >= 5:
            status = "↓ low"
        else:
            status = "↓↓ very low"

        nan_note = ""
        if s['nan_instrs']:
            nan_note = f"  [NaN: {', '.join(s['nan_instrs'][:3])}{'...' if len(s['nan_instrs'])>3 else ''}]"

        print(f"{rule:<28}  {yw*100:>5.1f}%  {mean_fc:>8.3f}   {s['median_abs']:>7.3f}  "
              f"{s['pct_zero']:>6.1f}%  {s['pct_at_cap']:>6.1f}%  {s['n_ok']:>4}  {status}{nan_note}")

    print("=" * 82)

    # Pooled mean across all rules
    all_means = [s['mean_abs'] for s in rule_stats.values() if s is not None]
    print(f"\nPooled mean |FC| across all rules: {np.mean(all_means):.3f}  (target: 10)")

    # -------------------------------------------------------------------------
    # Q2: Gated carry deep-dive — gate pass-rate by instrument tier
    # -------------------------------------------------------------------------
    print()
    print("=" * 82)
    print("Q2: GATED CARRY DEEP-DIVE — raw carry vs gate pass-rate by tier")
    print("=" * 82)
    print()
    print("The gate: carry.where(sign(carry) == sign(trend), other=0.0)")
    print("Expected ~50% pass-rate IF carry & trend are uncorrelated.")
    print("In practice for trending crypto: carry OPPOSES trend direction,")
    print("so pass-rate < 50% — potentially much lower for strong-trend instruments.")
    print()

    tiers = [('Large-cap', LARGE_CAP), ('Mid-cap', MID_CAP), ('Small-cap', SMALL_CAP)]

    for rule in GATED_CARRY_RULES:
        print(f"  {rule}:")
        print(f"  {'Instrument':<26}  {'Raw mean|FC|':>13}  {'Gate pass%':>11}  "
              f"{'Scaled mean|FC|':>16}  {'Scaled med|FC|':>15}")
        print(f"  {'-'*82}")

        for tier_name, tier_instrs in tiers:
            tier_instrs_avail = [i for i in tier_instrs if i in available]
            tier_rows = []
            for instr in tier_instrs_avail:
                raw  = get_raw(system, instr, rule)
                scaled = get_fc(system, instr, rule)

                if raw is None or raw.dropna().empty:
                    continue

                raw_nonzero = raw.dropna()
                pass_rate = (raw_nonzero != 0).mean() * 100
                raw_mean_abs = raw_nonzero.abs().mean()

                scaled_mean_abs = scaled.abs().mean() if scaled is not None else float('nan')
                scaled_med_abs  = scaled.abs().median() if scaled is not None else float('nan')

                tier_rows.append((instr, raw_mean_abs, pass_rate, scaled_mean_abs, scaled_med_abs))
                print(f"  {instr:<26}  {raw_mean_abs:>12.4f}   {pass_rate:>10.1f}%  "
                      f"{scaled_mean_abs:>15.3f}   {scaled_med_abs:>14.3f}")

            if tier_rows:
                avg_pass = np.mean([r[2] for r in tier_rows])
                avg_scaled = np.mean([r[3] for r in tier_rows if not np.isnan(r[3])])
                print(f"  {'  → ' + tier_name + ' avg':<26}  {'':>13}   {avg_pass:>10.1f}%  "
                      f"{avg_scaled:>15.3f}")
            print()

        # Pooled gate pass rate across full sample
        all_pass = []
        for instr in sample:
            raw = get_raw(system, instr, rule)
            if raw is not None and not raw.dropna().empty:
                all_pass.append((raw.dropna() != 0).mean() * 100)
        if all_pass:
            print(f"  → Pooled gate pass-rate across {len(all_pass)} instruments: "
                  f"{np.mean(all_pass):.1f}%  (median: {np.median(all_pass):.1f}%)")
        print()

    # -------------------------------------------------------------------------
    # Residual momentum NaN investigation
    # -------------------------------------------------------------------------
    resid_rules = [r for r in rules if 'residual' in r]
    if resid_rules:
        print("=" * 82)
        print("RESIDUAL MOMENTUM — NaN INVESTIGATION")
        print("=" * 82)
        for rule in resid_rules:
            nan_list, ok_list = [], []
            for instr in sample:
                fc = get_fc(system, instr, rule)
                if fc is None or fc.dropna().empty:
                    nan_list.append(instr)
                else:
                    ok_list.append(instr)
            print(f"\n  {rule}:")
            print(f"    NaN ({len(nan_list)}): {nan_list}")
            print(f"    OK  ({len(ok_list)}): {ok_list[:10]}{'...' if len(ok_list)>10 else ''}")
            # Check one ok instrument for mean|FC|
            if ok_list:
                fc = get_fc(system, ok_list[0], rule)
                print(f"    Mean |FC| for {ok_list[0]}: {fc.abs().mean():.3f}")

    print("\n✓ DONE")


if __name__ == '__main__':
    main()
