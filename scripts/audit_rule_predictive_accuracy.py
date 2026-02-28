#!/usr/bin/env python3
"""
Rank trading rules by predictive accuracy.

Metrics per rule (averaged across sample instruments):
  IC@1d   : Pearson corr(forecast[t], return[t+1])           (t-stat for significance)
  IC@5d   : Pearson corr(forecast[t], return[t+1:t+6].sum())
  IC@21d  : Pearson corr(forecast[t], return[t+1:t+22].sum())
  hit_rate: fraction of days where sign(forecast) == sign(next_return_1d)
  return_spread: mean(ret|forecast>0) - mean(ret|forecast<0), vol-normalised

Convergent rules are expected to be best at 1d-5d; trend rules at 5d-21d+.
Usage:
    python scripts/audit_rule_predictive_accuracy.py
"""
import sys
import yaml
import logging
from pathlib import Path

import pandas as pd
import numpy as np
from scipy import stats

sys.path.insert(0, str(Path(__file__).parent.parent))

from sysdata.config.configdata import Config
from sysdata.crypto.parquet_perps_sim_data import parquetCryptoPerpsSimData
from systems.provided.crypto_example.core.dynamic_portfolio import CryptoDynamicPortfolio
from systems.basesystem import System
from systems.forecasting import Rules
from systems.forecast_combine import ForecastCombine
from systems.forecast_scale_cap import ForecastScaleCap
from systems.rawdata import RawData
from systems.positionsizing import PositionSizing
from systems.accounts.accounts_stage import Account

logging.basicConfig(level=logging.WARNING)

CONFIG_PATH   = "config/crypto_perps_full_rules.yaml"
DATA_PATH     = "data/dataset_538registry_6yr_jagged.parquet"
MACRO_PATH    = "data/macro_factors.parquet"
SECTOR_MAP_PATH = "data/sector_map.json"

# Larger sample for statistical power; mix of large/mid/small cap, different histories
SAMPLE_INSTS = [
    "BTCUSDT_PERP", "ETHUSDT_PERP", "SOLUSDT_PERP", "BNBUSDT_PERP",
    "ADAUSDT_PERP", "DOTUSDT_PERP", "LINKUSDT_PERP", "AVAXUSDT_PERP",
    "DOGEUSDT_PERP", "LTCUSDT_PERP", "XRPUSDT_PERP", "UNIUSDT_PERP",
    "ATOMUSDT_PERP", "NEARUSDT_PERP", "INJUSDT_PERP",
]

HORIZONS = [1, 5, 21]  # days

# Rule family classification (for colour-coding in output)
RULE_FAMILY = {
    "ewmac_4":   "trend", "ewmac_8":   "trend", "ewmac_16":  "trend",
    "ewmac_32":  "trend", "ewmac_64":  "trend",
    "breakout_10": "trend", "breakout_20": "trend", "breakout_40":  "trend",
    "breakout_80": "trend", "breakout_160": "trend", "breakout_320": "trend",
    "normmom_4":   "trend", "normmom_8":   "trend", "normmom_16":   "trend",
    "normmom_32":  "trend", "normmom_64":  "trend",
    "accel_16":    "trend", "accel_32":    "trend", "accel_64":     "trend",
    "assettrend_8": "trend", "assettrend_16": "trend",
    "assettrend_32": "trend", "assettrend_64": "trend",
    "relmomentum_10": "trend", "relmomentum_20": "trend",
    "relmomentum_40": "trend", "relmomentum_80": "trend",
    "btc_lead_lag_1": "trend", "btc_lead_lag_2": "trend",
    "funding_carry_10": "carry", "funding_carry_30": "carry",
    "funding_carry_60": "carry", "funding_carry_125": "carry",
    "relcarry_30": "carry", "relcarry_60": "carry", "relcarry_125": "carry",
    "funding_mr": "carry",
    "streversal_1": "reversion", "streversal_2": "reversion",
    "streversal_3": "reversion",
    "return_skew_20": "reversion", "return_skew_60": "reversion",
    "mrinasset": "reversion", "illiquidity_20": "reversion",
    "illiquidity_60": "reversion",
    "residual_momentum_16": "resmom",
    "residual_momentum_32": "resmom",
    "residual_momentum_64": "resmom",
    "sector_momentum_10": "sector",
    "sector_momentum_20": "sector",
    "sector_momentum_40": "sector",
    "vol_norm_carry_10": "carry",
    "vol_norm_carry_30": "carry",
    "vol_norm_carry_60": "carry",
}


def build_system():
    from pathlib import Path as _Path
    from syscore.constants import arg_not_supplied as _ans
    with open(CONFIG_PATH) as f:
        cfg_dict = yaml.safe_load(f)
    config = Config(cfg_dict)
    macro_kwarg = MACRO_PATH if _Path(MACRO_PATH).exists() else _ans
    sector_kwarg = SECTOR_MAP_PATH if _Path(SECTOR_MAP_PATH).exists() else _ans
    data = parquetCryptoPerpsSimData(DATA_PATH, macro_data_path=macro_kwarg, sector_map_path=sector_kwarg)
    return System(
        [RawData(), Rules(), ForecastScaleCap(), ForecastCombine(),
         PositionSizing(), CryptoDynamicPortfolio(), Account()],
        data, config,
    )


def ic_and_stats(forecast: pd.Series, returns: pd.Series, horizon: int) -> dict:
    """
    Compute IC, t-stat, hit-rate, and return-spread between aligned forecast and
    forward-horizon return.  Returns NaN dict if insufficient data.
    """
    # Align on common index
    common = forecast.index.intersection(returns.index)
    f = forecast.reindex(common).dropna()
    r = returns.reindex(common)

    # Forward cumulative return over `horizon` days (sum of daily returns)
    fwd_ret = r.rolling(horizon).sum().shift(-horizon)

    # vol-normalise forward returns (remove heteroskedasticity for IC)
    roll_vol = r.rolling(63, min_periods=21).std()
    fwd_ret_norm = fwd_ret / roll_vol.replace(0, np.nan)

    aligned = pd.concat({"f": f, "r": fwd_ret_norm}, axis=1).dropna()
    n = len(aligned)
    if n < 100:
        return {"ic": np.nan, "tstat": np.nan, "hit": np.nan, "spread": np.nan, "n": n}

    ic, pval = stats.pearsonr(aligned["f"], aligned["r"])
    tstat = ic * np.sqrt(n - 2) / np.sqrt(max(1 - ic**2, 1e-8))

    # hit rate (sign agreement)
    hit = (np.sign(aligned["f"]) == np.sign(aligned["r"])).mean()

    # return spread (long half vs short half by forecast quantile)
    median_f = aligned["f"].median()
    long_ret  = aligned.loc[aligned["f"] > median_f, "r"].mean()
    short_ret = aligned.loc[aligned["f"] < median_f, "r"].mean()
    spread = long_ret - short_ret

    return {"ic": ic, "tstat": tstat, "hit": hit, "spread": spread, "n": n}


def main():
    print(f"Building system from {CONFIG_PATH} / {DATA_PATH} ...")
    system = build_system()

    rules = list(system.config.trading_rules.keys())
    print(f"Found {len(rules)} rules, sampling {len(SAMPLE_INSTS)} instruments.\n")

    results = []
    for rule in rules:
        family = RULE_FAMILY.get(rule, "?")
        inst_stats = {h: [] for h in HORIZONS}
        n_insts = 0

        for inst in SAMPLE_INSTS:
            try:
                fc = system.forecastScaleCap.get_capped_forecast(inst, rule)
                if fc is None or fc.dropna().empty:
                    continue
                # Daily percentage returns
                price = system.data.daily_prices(inst)
                daily_ret = price.pct_change(fill_method=None)

                for h in HORIZONS:
                    s = ic_and_stats(fc, daily_ret, h)
                    if not np.isnan(s["ic"]):
                        inst_stats[h].append(s)
                n_insts += 1
            except Exception:
                pass

        row = {"rule": rule, "family": family, "n_insts": n_insts}
        for h in HORIZONS:
            vals = inst_stats[h]
            if vals:
                row[f"ic_{h}d"]     = np.nanmean([v["ic"]     for v in vals])
                row[f"tstat_{h}d"]  = np.nanmean([v["tstat"]  for v in vals])
                row[f"hit_{h}d"]    = np.nanmean([v["hit"]    for v in vals])
                row[f"spread_{h}d"] = np.nanmean([v["spread"] for v in vals])
            else:
                row[f"ic_{h}d"]     = np.nan
                row[f"tstat_{h}d"]  = np.nan
                row[f"hit_{h}d"]    = np.nan
                row[f"spread_{h}d"] = np.nan
        results.append(row)

    df = pd.DataFrame(results)

    # Sort by IC@5d (balanced horizon) descending
    df = df.sort_values("ic_5d", ascending=False).reset_index(drop=True)

    # --- Print table ---
    header = (
        f"{'#':>3} {'Rule':<24} {'Fam':>8}"
        f" {'IC@1d':>7} {'t@1d':>6}"
        f" {'IC@5d':>7} {'t@5d':>6}"
        f" {'IC@21d':>8} {'t@21d':>6}"
        f" {'hit@1d':>8}"
        f" {'sprd@5d':>9}"
    )
    sep = "=" * len(header)
    print(sep)
    print(header)
    print(sep)

    for rank, row in df.iterrows():
        fam_icon = {"trend": "↗", "carry": "⟺", "reversion": "↩", "resmom": "∿", "sector": "⊕"}.get(row["family"], "?")
        def fmt(v, decimals=4):
            return f"{v:.{decimals}f}" if not np.isnan(v) else "   NaN"
        print(
            f"{rank+1:>3} {row['rule']:<24} {fam_icon + row['family']:>8}"
            f" {fmt(row['ic_1d']):>7} {fmt(row['tstat_1d'],1):>6}"
            f" {fmt(row['ic_5d']):>7} {fmt(row['tstat_5d'],1):>6}"
            f" {fmt(row['ic_21d']):>8} {fmt(row['tstat_21d'],1):>6}"
            f" {fmt(row['hit_1d'],3):>8}"
            f" {fmt(row['spread_5d'],4):>9}"
        )
    print(sep)

    # --- Family summary ---
    print("\nFamily IC summary (mean across rules in family, sorted by IC@5d):")
    fam_summary = df.groupby("family")[["ic_1d","ic_5d","ic_21d","tstat_5d"]].mean()
    fam_summary = fam_summary.sort_values("ic_5d", ascending=False)
    for fam, row in fam_summary.iterrows():
        icon = {"trend": "↗", "carry": "⟺", "reversion": "↩", "resmom": "∿", "sector": "⊕"}.get(fam, "?")
        n_rules = (df["family"] == fam).sum()
        print(f"  {icon} {fam:<12}  IC@1d={row['ic_1d']:.4f}  IC@5d={row['ic_5d']:.4f}  IC@21d={row['ic_21d']:.4f}  t@5d={row['tstat_5d']:.1f}  (n={n_rules})")


if __name__ == "__main__":
    main()
