#!/usr/bin/env python3
"""
Audit mean_abs of raw, scaled, and capped forecasts for every trading rule.

Computes per-rule stats across a sample of instruments and prints a sorted table.
Target: capped_abs should be ~10 for every rule (after walk-forward scalar).

Usage:
    python scripts/audit_rule_mean_abs.py
"""
import sys
import yaml
import logging
from pathlib import Path

import pandas as pd
import numpy as np

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

CONFIG_PATH = "config/crypto_perps_full_rules.yaml"
DATA_PATH   = "data/dataset_538registry_6yr_jagged.parquet"

# Instruments to sample (diverse: large-cap, mid-cap, carry-heavy)
SAMPLE_INSTS = [
    "BTCUSDT_PERP", "ETHUSDT_PERP", "SOLUSDT_PERP",
    "ADAUSDT_PERP", "LINKUSDT_PERP", "DOGEUSDT_PERP",
    "AVAXUSDT_PERP", "LTCUSDT_PERP",
]

def build_system():
    with open(CONFIG_PATH) as f:
        cfg_dict = yaml.safe_load(f)
    config = Config(cfg_dict)
    data = parquetCryptoPerpsSimData(DATA_PATH)
    system = System(
        [RawData(), Rules(), ForecastScaleCap(), ForecastCombine(),
         PositionSizing(), CryptoDynamicPortfolio(), Account()],
        data, config,
    )
    return system


def get_rule_names(system) -> list[str]:
    """Return list of all configured rule names (regardless of weight)."""
    return list(system.config.trading_rules.keys())


def stats_for_rule_instrument(system, rule: str, inst: str) -> dict | None:
    """Compute raw / scalar / capped mean_abs for one rule × instrument pair."""
    try:
        raw = system.rules.get_raw_forecast(inst, rule)
        if raw is None or raw.dropna().empty:
            return None
        raw_abs = raw.dropna().abs().mean()

        # scalar: walk-forward scalar values (Series indexed by date)
        scalar_series = system.forecastScaleCap.get_forecast_scalar(inst, rule)
        scalar_mean = scalar_series.dropna().mean() if scalar_series is not None else np.nan

        capped = system.forecastScaleCap.get_capped_forecast(inst, rule)
        if capped is None or capped.dropna().empty:
            capped_abs = np.nan
        else:
            capped_abs = capped.dropna().abs().mean()

        return {"raw_abs": raw_abs, "scalar_mean": scalar_mean, "capped_abs": capped_abs}
    except Exception as e:
        return {"error": str(e)[:80]}


def main():
    print(f"Building system from {CONFIG_PATH} / {DATA_PATH} ...")
    system = build_system()

    rules = get_rule_names(system)
    print(f"Found {len(rules)} rules: {rules[:5]}...\n")

    rows = []
    for rule in rules:
        rule_raw_vals, rule_capped_vals, rule_scalar_vals = [], [], []
        errors = []
        for inst in SAMPLE_INSTS:
            result = stats_for_rule_instrument(system, rule, inst)
            if result is None:
                continue
            if "error" in result:
                errors.append(f"{inst}: {result['error']}")
                continue
            rule_raw_vals.append(result["raw_abs"])
            rule_capped_vals.append(result["capped_abs"])
            rule_scalar_vals.append(result["scalar_mean"])

        if rule_capped_vals:
            rows.append({
                "rule": rule,
                "n_insts": len(rule_capped_vals),
                "raw_abs_mean": np.nanmean(rule_raw_vals),
                "scalar_mean": np.nanmean(rule_scalar_vals),
                "capped_abs_mean": np.nanmean(rule_capped_vals),
                "capped_abs_min": np.nanmin(rule_capped_vals),
                "capped_abs_max": np.nanmax(rule_capped_vals),
                "errors": "; ".join(errors) if errors else "",
            })
        else:
            rows.append({
                "rule": rule,
                "n_insts": 0,
                "raw_abs_mean": np.nan,
                "scalar_mean": np.nan,
                "capped_abs_mean": np.nan,
                "capped_abs_min": np.nan,
                "capped_abs_max": np.nan,
                "errors": "; ".join(errors),
            })

    df = pd.DataFrame(rows).sort_values("capped_abs_mean")

    # Print full table
    pd.set_option("display.max_rows", 100)
    pd.set_option("display.width", 160)
    pd.set_option("display.float_format", "{:.3f}".format)

    print("=" * 100)
    print(f"{'Rule':<30} {'n':>3} {'raw_abs':>9} {'scalar':>9} {'capped_mean':>12} {'capped_min':>11} {'capped_max':>11}  note")
    print("=" * 100)
    for _, r in df.iterrows():
        flag = ""
        if pd.isna(r["capped_abs_mean"]):
            flag = "ERROR"
        elif r["capped_abs_mean"] < 7.0:
            flag = "<<<  UNDERPOWERED"
        elif r["capped_abs_mean"] < 9.0:
            flag = "<  low"
        elif r["capped_abs_mean"] > 14.0:
            flag = ">  high"
        err_note = f"  [{r['errors'][:60]}]" if r["errors"] else ""
        print(
            f"{r['rule']:<30} {int(r['n_insts']):>3}"
            f" {r['raw_abs_mean']:>9.3f}"
            f" {r['scalar_mean']:>9.3f}"
            f" {r['capped_abs_mean']:>12.3f}"
            f" {r['capped_abs_min']:>11.3f}"
            f" {r['capped_abs_max']:>11.3f}"
            f"  {flag}{err_note}"
        )
    print("=" * 100)

    # Summary
    good   = df[df["capped_abs_mean"].between(9, 12)].shape[0]
    low    = df[df["capped_abs_mean"] < 7].shape[0]
    mid    = df[df["capped_abs_mean"].between(7, 9)].shape[0]
    high   = df[df["capped_abs_mean"] > 12].shape[0]
    errors = df[df["n_insts"] == 0].shape[0]
    print(f"\nSummary: {len(df)} rules total")
    print(f"  Good (9-12): {good}")
    print(f"  OK   (7-9):  {mid}")
    print(f"  Low  (<7):   {low}  ← underpowered (drag on realized vol)")
    print(f"  High (>12):  {high} ← slightly over-capped")
    print(f"  Errors:      {errors}")

    fleet_mean = df["capped_abs_mean"].dropna().mean()
    print(f"\nFleet-wide capped_abs mean across rules: {fleet_mean:.3f}  (target: 10.0)")
    shortfall = fleet_mean / 10.0
    print(f"Forecast power ratio vs target:          {shortfall:.3f}x")
    implied_aaf = fleet_mean
    print(f"Suggested average_absolute_forecast:     {implied_aaf:.2f}")


if __name__ == "__main__":
    main()
