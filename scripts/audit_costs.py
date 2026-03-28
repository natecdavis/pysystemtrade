#!/usr/bin/env python3
"""
Cost Audit — Per-Rule Turnover, Per-Instrument Drag, Forecast-Adjusted SR Cost, Speed Limit.

Four sections:
  A: Per-rule turnover vs return contribution (10 instruments × active rules)
  B: Per-instrument cost drag (all instruments in dataset)
  C: Forecast-adjusted SR cost Carver test (8 instruments × rules grid)
  D: Carver speed limit — annual_cost ≤ gross_SR/3 per rule

Usage:
    python scripts/audit_costs.py
    python scripts/audit_costs.py 2>/dev/null   # suppress warnings
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

# Carver thresholds
MAX_SR_PER_TRADE  = 0.01
MAX_SR_ANNUAL     = 0.13
FEE_BPS           = 5        # one-way taker fee
FLEET_TURNOVER    = 14.32    # round-trips/yr (adv_window=252, commit 447cd578)

# ADV → spread bins (bps), matching walk_forward_costs.py
ADV_SPREAD_BINS = [
    (50_000_000, 5),
    (10_000_000, 10),
    (1_000_000,  20),
    (0,          40),
]

# Instruments for Section A (10-sample, diverse cap range)
SAMPLE_A = [
    "BTCUSDT_PERP", "ETHUSDT_PERP", "SOLUSDT_PERP", "BNBUSDT_PERP",
    "ADAUSDT_PERP", "DOTUSDT_PERP", "LINKUSDT_PERP", "AVAXUSDT_PERP",
    "DOGEUSDT_PERP", "LTCUSDT_PERP",
]

# Instruments for Section C (8-instrument grid)
SAMPLE_C = [
    "BTCUSDT_PERP", "ETHUSDT_PERP", "SOLUSDT_PERP", "LINKUSDT_PERP",
    "AVAXUSDT_PERP", "DOGEUSDT_PERP", "LTCUSDT_PERP", "ADAUSDT_PERP",
]


# ============================================================================
# Helpers
# ============================================================================

def _adv_to_spread(adv_usd: float) -> float:
    """Map a trailing ADV$ to spread estimate in bps."""
    for threshold, spread in ADV_SPREAD_BINS:
        if adv_usd >= threshold:
            return float(spread)
    return 40.0


def _sr_cost_per_trade(spread_bps: float, fee_bps: float, annual_vol: float) -> float:
    """Carver SR cost per round-trip."""
    if annual_vol <= 0 or np.isnan(annual_vol):
        return float("inf")
    spread_cost = spread_bps / 10_000
    fee_cost    = 2 * (fee_bps / 10_000)   # entry + exit
    return (spread_cost + fee_cost) / annual_vol


def build_system() -> System:
    with open(CONFIG_PATH) as f:
        cfg_dict = yaml.safe_load(f)
    config = Config(cfg_dict)
    data = parquetCryptoPerpsSimData(DATA_PATH)
    return System(
        [RawData(), Rules(), ForecastScaleCap(), ForecastCombine(),
         PositionSizing(), CryptoDynamicPortfolio(), Account()],
        data, config,
    )


def get_active_weights(system) -> dict:
    """Return only the non-zero forecast weights from config."""
    weights = system.config.forecast_weights
    return {k: v for k, v in weights.items() if v > 0}


# ============================================================================
# Section A — Per-Rule Turnover vs Return Contribution
# ============================================================================

def _ann_return_vol(system, inst: str) -> float:
    """
    Annualized return-based volatility for an instrument.

    daily_returns_volatility() gives dollar (price-unit) vol.
    Dividing by price gives return-based daily vol; annualize with sqrt(252).
    """
    try:
        prices = system.data.daily_prices(inst)
        # Use log returns directly — same approach as walk_forward_costs.py
        log_ret = np.log(prices / prices.shift(1)).dropna()
        daily_vol = log_ret.rolling(35, min_periods=10).std()
        window = min(252, len(daily_vol.dropna()))
        return float(daily_vol.dropna().iloc[-window:].mean()) * np.sqrt(252)
    except Exception:
        return np.nan


def section_a(system):
    print("\n" + "=" * 80)
    print("SECTION A — Per-Rule Turnover vs Return Contribution")
    print(f"  Sample: {len(SAMPLE_A)} instruments × active rules")
    print("=" * 80)

    active_weights = get_active_weights(system)
    rules = list(active_weights.keys())

    # Estimate fleet-average SR cost per trade from liquid anchor instruments
    # BTC/ETH trailing ADV → spread → return-vol → sr_cost_per_trade
    anchor_sr_costs = []
    for inst in ["BTCUSDT_PERP", "ETHUSDT_PERP"]:
        try:
            adv_series = system.data.get_adv_notional(inst)
            adv = adv_series.iloc[-252:].median() if len(adv_series) > 252 else adv_series.median()
            ann_vol = _ann_return_vol(system, inst)
            spread = _adv_to_spread(adv)
            anchor_sr_costs.append(_sr_cost_per_trade(spread, FEE_BPS, ann_vol))
        except Exception:
            pass
    fleet_sr_cost = float(np.nanmean(anchor_sr_costs)) if anchor_sr_costs else 0.005

    print(f"\n  Fleet-average SR cost/trade (BTC+ETH anchor): {fleet_sr_cost:.4f}")

    # Measure turnover per rule × instrument
    rule_turnover_data = {}   # rule → list of (inst, turnover)
    for rule in rules:
        rule_turnover_data[rule] = []
        for inst in SAMPLE_A:
            try:
                to = system.accounts.forecast_turnover(inst, rule)
                if np.isfinite(to) and to > 0:
                    rule_turnover_data[rule].append(to)
            except Exception:
                pass

    # Build per-rule summary
    rows = []
    total_weighted_turnover = 0.0
    weight_sum = sum(active_weights.values())

    for rule in rules:
        w = active_weights[rule] / weight_sum   # normalised
        turnovers = rule_turnover_data[rule]
        if turnovers:
            avg_to = float(np.mean(turnovers))
        else:
            avg_to = np.nan
        rows.append({"rule": rule, "weight": w, "avg_turnover": avg_to})
        if np.isfinite(avg_to):
            total_weighted_turnover += w * avg_to

    # Compute contributions
    result_rows = []
    for r in rows:
        w, avg_to = r["weight"], r["avg_turnover"]
        if np.isfinite(avg_to) and total_weighted_turnover > 0:
            to_contrib  = w * avg_to / total_weighted_turnover
            w_contrib   = w
            ratio       = to_contrib / w_contrib if w_contrib > 0 else np.nan
            annual_drag = fleet_sr_cost * avg_to
        else:
            to_contrib = w_contrib = ratio = annual_drag = np.nan
        result_rows.append({
            "rule":         r["rule"],
            "weight_pct":   r["weight"] * 100,
            "avg_to":       avg_to,
            "to_contrib_pct": to_contrib * 100 if np.isfinite(to_contrib) else np.nan,
            "ratio":        ratio,
            "annual_drag":  annual_drag,
        })

    # Sort by ratio descending
    df = pd.DataFrame(result_rows)
    df = df.sort_values("ratio", ascending=False, na_position="last").reset_index(drop=True)

    # Print
    header = (
        f"{'#':>3} {'Rule':<22} {'Wt%':>6} {'AvgTO(rt/yr)':>13}"
        f" {'TO-Contrib%':>12} {'Ratio':>7} {'AnnSR-Drag':>11} {'Flag'}"
    )
    sep = "=" * len(header)
    print(f"\n{sep}")
    print(header)
    print(sep)
    for i, row in df.iterrows():
        flag = ""
        if pd.notna(row["ratio"]) and row["ratio"] > 2.0:
            flag = "<<< DISPROPORTIONATE CHURN"
        elif pd.notna(row["ratio"]) and row["ratio"] > 1.5:
            flag = "<  elevated"
        def fmt(v, d=3):
            return f"{v:{'.'+str(d)+'f'}}" if pd.notna(v) else "   NaN"
        print(
            f"{i+1:>3} {row['rule']:<22} {fmt(row['weight_pct'],1):>6}"
            f" {fmt(row['avg_to'],1):>13}"
            f" {fmt(row['to_contrib_pct'],1):>12}"
            f" {fmt(row['ratio'],2):>7}"
            f" {fmt(row['annual_drag'],4):>11}"
            f"  {flag}"
        )
    print(sep)

    # Summary
    measured = df.dropna(subset=["avg_to"])
    wt_avg_to = (measured["weight_pct"] * measured["avg_to"]).sum() / measured["weight_pct"].sum() if len(measured) else np.nan
    print(f"\n  Weighted-average measured turnover: {wt_avg_to:.1f} rt/yr (backtest metadata: {FLEET_TURNOVER})")
    high_ratio = df[df["ratio"] > 2.0]
    if len(high_ratio):
        print(f"  Rules with ratio > 2.0 ({len(high_ratio)}):")
        for _, r in high_ratio.iterrows():
            print(f"    {r['rule']} — ratio={r['ratio']:.2f}, TO={r['avg_to']:.1f} rt/yr, annual_drag={r['annual_drag']:.4f} SR")
    else:
        print("  No rules with turnover ratio > 2.0 — stack is balanced.")

    return df


# ============================================================================
# Section B — Per-Instrument Cost Drag
# ============================================================================

def section_b(system):
    print("\n" + "=" * 80)
    print("SECTION B — Per-Instrument Cost Drag")
    print(f"  All instruments in dataset | fleet turnover = {FLEET_TURNOVER} rt/yr")
    print("=" * 80)

    all_insts = system.data.get_instrument_list()
    print(f"  Processing {len(all_insts)} instruments...")

    rows = []
    for inst in all_insts:
        try:
            # ADV: trailing 1-year median
            adv_series = system.data.get_adv_notional(inst)
            if len(adv_series) == 0:
                adv = np.nan
            else:
                window = min(252, len(adv_series))
                adv = float(adv_series.iloc[-window:].median())

            # Spread from bins
            spread_bps = _adv_to_spread(adv) if np.isfinite(adv) else 40.0

            # Annual return-based vol (log returns → rolling std → annualized)
            ann_vol = _ann_return_vol(system, inst)

            sr_per_trade = _sr_cost_per_trade(spread_bps, FEE_BPS, ann_vol)
            annual_sr    = sr_per_trade * FLEET_TURNOVER

            # Flags
            flag = ""
            if sr_per_trade > MAX_SR_PER_TRADE:
                flag += "FAIL-TRADE "
            if annual_sr > MAX_SR_ANNUAL:
                flag += "FAIL-ANNUAL"
            flag = flag.strip()

            rows.append({
                "instrument":   inst,
                "adv_m":        adv / 1_000_000 if np.isfinite(adv) else np.nan,
                "spread_bps":   spread_bps,
                "ann_vol":      ann_vol,
                "sr_per_trade": sr_per_trade,
                "annual_sr":    annual_sr,
                "flag":         flag,
            })
        except Exception as e:
            rows.append({
                "instrument": inst,
                "adv_m": np.nan, "spread_bps": np.nan,
                "ann_vol": np.nan, "sr_per_trade": np.nan,
                "annual_sr": np.nan,
                "flag": f"ERROR: {str(e)[:40]}",
            })

    df = pd.DataFrame(rows)
    df = df.sort_values("annual_sr", ascending=False, na_position="last").reset_index(drop=True)

    # Print top 30 by annual SR cost (most expensive)
    header = (
        f"{'#':>4} {'Instrument':<22} {'ADV($M)':>9} {'Sprd(bps)':>10}"
        f" {'AnnVol':>8} {'SRperTrade':>12} {'AnnSR':>8} {'Flag'}"
    )
    sep = "=" * (len(header) + 10)
    print(f"\n{sep}")
    print(header)
    print(sep)

    # Show worst 30 + all flagged
    show_set = set(df[df["flag"] != ""].index.tolist()) | set(df.head(30).index.tolist())
    shown = df.loc[sorted(show_set)].head(50)

    for i, (_, row) in enumerate(shown.iterrows()):
        def fmt(v, d=3):
            return f"{v:{'.'+str(d)+'f'}}" if pd.notna(v) else "  NaN"
        print(
            f"{i+1:>4} {row['instrument']:<22}"
            f" {fmt(row['adv_m'],1):>9}"
            f" {fmt(row['spread_bps'],0):>10}"
            f" {fmt(row['ann_vol'],3):>8}"
            f" {fmt(row['sr_per_trade'],4):>12}"
            f" {fmt(row['annual_sr'],3):>8}"
            f"  {row['flag']}"
        )
    print(sep)

    # ADV bucket summary
    print("\n  ADV Bucket Summary:")
    bins   = [0, 1, 10, 50, float("inf")]
    labels = ["<$1M", "$1–10M", "$10–50M", ">$50M"]
    df_valid = df.dropna(subset=["adv_m"])
    df_valid = df_valid.copy()
    df_valid["adv_bucket"] = pd.cut(df_valid["adv_m"], bins=bins, labels=labels, right=False)

    print(f"  {'Bucket':<12} {'N':>5} {'FailTrade':>10} {'FailAnnual':>11} {'MedianSR/Trade':>15}")
    for bucket in labels:
        sub = df_valid[df_valid["adv_bucket"] == bucket]
        n = len(sub)
        if n == 0:
            continue
        fail_trade  = (sub["sr_per_trade"] > MAX_SR_PER_TRADE).sum()
        fail_annual = (sub["annual_sr"]    > MAX_SR_ANNUAL).sum()
        med_sr      = sub["sr_per_trade"].median()
        print(f"  {bucket:<12} {n:>5} {fail_trade:>10} {fail_annual:>11} {med_sr:>15.4f}")

    # Overall stats
    n_fail_trade  = (df["sr_per_trade"] > MAX_SR_PER_TRADE).sum()
    n_fail_annual = (df["annual_sr"]    > MAX_SR_ANNUAL).sum()
    n_total       = len(df)
    print(f"\n  Total: {n_total} instruments | FAIL-TRADE: {n_fail_trade} ({100*n_fail_trade/n_total:.0f}%) | FAIL-ANNUAL: {n_fail_annual} ({100*n_fail_annual/n_total:.0f}%)")

    return df


# ============================================================================
# Section C — Forecast-Adjusted SR Cost (Carver Test) per Rule × Instrument
# ============================================================================

def section_c(system, section_a_df: pd.DataFrame, section_b_df: pd.DataFrame):
    print("\n" + "=" * 80)
    print("SECTION C — Forecast-Adjusted SR Cost per Rule × Instrument")
    print(f"  Grid: {len(SAMPLE_C)} instruments × active rules")
    print(f"  Thresholds: SR/trade ≤ {MAX_SR_PER_TRADE}, Annual SR ≤ {MAX_SR_ANNUAL}")
    print("=" * 80)

    active_weights = get_active_weights(system)
    rules = list(active_weights.keys())

    # Pull per-instrument SR/trade from Section B results
    sr_per_trade_map = dict(zip(section_b_df["instrument"], section_b_df["sr_per_trade"]))

    # Build grid: rule × instrument turnover from Section A measurement
    # Re-measure on SAMPLE_C instruments (may overlap with SAMPLE_A)
    print("\n  Measuring forecast turnover on Section C instruments...")
    rule_inst_to = {}   # (rule, inst) → float
    for rule in rules:
        for inst in SAMPLE_C:
            try:
                to = system.accounts.forecast_turnover(inst, rule)
                rule_inst_to[(rule, inst)] = float(to) if np.isfinite(to) else np.nan
            except Exception:
                rule_inst_to[(rule, inst)] = np.nan

    # Compute rule × instrument annual SR cost
    grid = {}   # rule → {inst: annual_sr}
    for rule in rules:
        grid[rule] = {}
        for inst in SAMPLE_C:
            sr_trade = sr_per_trade_map.get(inst, np.nan)
            to       = rule_inst_to.get((rule, inst), np.nan)
            if np.isfinite(sr_trade) and np.isfinite(to):
                grid[rule][inst] = sr_trade * to
            else:
                grid[rule][inst] = np.nan

    # Per-rule pass/fail
    rule_rows = []
    for rule in rules:
        w = active_weights[rule]
        per_trade_vals  = []
        annual_vals     = []
        for inst in SAMPLE_C:
            sr_trade = sr_per_trade_map.get(inst, np.nan)
            ann_sr   = grid[rule].get(inst, np.nan)
            if np.isfinite(sr_trade):
                per_trade_vals.append(sr_trade > MAX_SR_PER_TRADE)
            if np.isfinite(ann_sr):
                annual_vals.append(ann_sr > MAX_SR_ANNUAL)

        pct_fail_trade  = np.mean(per_trade_vals) * 100 if per_trade_vals else np.nan
        pct_fail_annual = np.mean(annual_vals)    * 100 if annual_vals    else np.nan

        # Carver weight haircut
        adj_weight = w * (1 - pct_fail_annual / 100) if np.isfinite(pct_fail_annual) else np.nan

        rule_rows.append({
            "rule":            rule,
            "weight_pct":      w * 100,
            "pct_fail_trade":  pct_fail_trade,
            "pct_fail_annual": pct_fail_annual,
            "adj_weight_pct":  adj_weight * 100 if pd.notna(adj_weight) else np.nan,
            "weight_haircut":  (1 - adj_weight / w) * 100 if (pd.notna(adj_weight) and w > 0) else np.nan,
        })

    rule_df = pd.DataFrame(rule_rows).sort_values("pct_fail_annual", ascending=False).reset_index(drop=True)

    print("\n  Table C1 — Rules by % Instruments Failing Annual SR Cost Test")
    header1 = (
        f"{'#':>3} {'Rule':<22} {'Wt%':>6} {'%FailTrade':>11}"
        f" {'%FailAnnual':>12} {'AdjWt%':>8} {'Haircut%':>9} {'Flag'}"
    )
    sep1 = "=" * len(header1)
    print(f"\n{sep1}")
    print(header1)
    print(sep1)
    for i, row in rule_df.iterrows():
        flag = ""
        if pd.notna(row["pct_fail_annual"]) and row["pct_fail_annual"] > 50:
            flag = ">>> MOST INSTRUMENTS FAIL"
        elif pd.notna(row["pct_fail_annual"]) and row["pct_fail_annual"] > 25:
            flag = "<  minority fail"
        def fmt(v, d=1):
            return f"{v:{'.'+str(d)+'f'}}" if pd.notna(v) else "  NaN"
        print(
            f"{i+1:>3} {row['rule']:<22} {fmt(row['weight_pct']):>6}"
            f" {fmt(row['pct_fail_trade']):>11}"
            f" {fmt(row['pct_fail_annual']):>12}"
            f" {fmt(row['adj_weight_pct']):>8}"
            f" {fmt(row['weight_haircut']):>9}"
            f"  {flag}"
        )
    print(sep1)

    # Per-instrument pass/fail
    inst_rows = []
    for inst in SAMPLE_C:
        annual_fails = []
        for rule in rules:
            ann_sr = grid[rule].get(inst, np.nan)
            if np.isfinite(ann_sr):
                annual_fails.append(ann_sr > MAX_SR_ANNUAL)
        pct_fail = np.mean(annual_fails) * 100 if annual_fails else np.nan
        sr_trade = sr_per_trade_map.get(inst, np.nan)
        inst_rows.append({
            "instrument":    inst,
            "sr_per_trade":  sr_trade,
            "pct_rules_fail": pct_fail,
        })

    inst_df = pd.DataFrame(inst_rows).sort_values("pct_rules_fail", ascending=False).reset_index(drop=True)

    print("\n  Table C2 — Instruments by % Rules Failing Annual Cost Test")
    header2 = f"{'#':>3} {'Instrument':<22} {'SR/Trade':>10} {'%RulesFail':>12} {'Flag'}"
    sep2 = "=" * len(header2)
    print(f"\n{sep2}")
    print(header2)
    print(sep2)
    for i, row in inst_df.iterrows():
        flag = ""
        if pd.notna(row["pct_rules_fail"]) and row["pct_rules_fail"] > 75:
            flag = ">>> MOST RULES FAIL"
        elif pd.notna(row["pct_rules_fail"]) and row["pct_rules_fail"] > 50:
            flag = "<  majority fail"
        def fmt(v, d=3):
            return f"{v:{'.'+str(d)+'f'}}" if pd.notna(v) else "  NaN"
        print(
            f"{i+1:>3} {row['instrument']:<22}"
            f" {fmt(row['sr_per_trade'],4):>10}"
            f" {fmt(row['pct_rules_fail'],1):>12}"
            f"  {flag}"
        )
    print(sep2)

    # Summary recommendations
    print("\n  ACTION ITEMS FROM SECTION C:")
    severe_rules = rule_df[rule_df["pct_fail_annual"] > 50]
    if len(severe_rules):
        print(f"  • {len(severe_rules)} rules fail annual SR test on >50% of instruments:")
        for _, r in severe_rules.iterrows():
            print(f"      {r['rule']}: {r['pct_fail_annual']:.0f}% fail, haircut={r['weight_haircut']:.0f}%")
    else:
        print("  • No rules fail annual SR test on >50% of instruments.")

    high_fail_insts = inst_df[inst_df["pct_rules_fail"] > 75]
    if len(high_fail_insts):
        print(f"  • {len(high_fail_insts)} instruments fail cost test for >75% of rules:")
        for _, r in high_fail_insts.iterrows():
            print(f"      {r['instrument']}: SR/trade={r['sr_per_trade']:.4f}, {r['pct_rules_fail']:.0f}% rules fail")
    else:
        print("  • No instruments fail cost tests for >75% of rules.")

    # Specific check: streversal_1 vs illiquid instruments
    streversal_row = rule_df[rule_df["rule"] == "streversal_1"]
    if len(streversal_row):
        r = streversal_row.iloc[0]
        print(f"\n  streversal_1 check: {r['pct_fail_annual']:.0f}% of instruments fail annual test "
              f"(expected high for fast reversal).")

    return rule_df, inst_df


# ============================================================================
# Section D — Carver Speed Limit: annual_cost ≤ gross_SR / 3
# ============================================================================

def section_d(system, section_a_df: pd.DataFrame, section_c_rule_df: pd.DataFrame):
    """
    Carver speed limit: max_annual_cost_SR = gross_SR_per_rule / 3.

    For each active rule:
      1. Measure gross weighted SR via pandl_for_trading_rule_weighted(rule).sharpe()
      2. Compute speed limit = gross_SR / 3
      3. Read avg annual cost from Section A (fleet_sr_cost × avg_turnover)
      4. Flag: FAIL if annual_cost > speed_limit, WARN if > 80% of limit, PASS otherwise

    Note: pandl_for_trading_rule_weighted triggers Account stage computation
    (300 instruments × each rule). Runtime ~5-10 min for all rules after
    Section A/B/C have warmed up the cache.
    """
    print("\n" + "=" * 80)
    print("SECTION D — Carver Speed Limit (annual_cost ≤ gross_SR / 3)")
    print("=" * 80)

    active_weights = get_active_weights(system)
    rules = list(active_weights.keys())

    print(f"\n  Computing gross SR for {len(rules)} rules via pandl_for_trading_rule_weighted()")
    print(f"  (First run: ~5-10 min. Cached on subsequent runs.)")

    rows = []
    for rule in rules:
        # --- Gross SR (weighted across all instruments) ---
        try:
            curve = system.accounts.pandl_for_trading_rule_weighted(rule)
            gross_sr = float(curve.sharpe())
        except Exception as e:
            gross_sr = np.nan

        # Speed limit = gross_SR / 3
        if np.isfinite(gross_sr) and gross_sr > 0:
            speed_limit = gross_sr / 3.0
        else:
            speed_limit = np.nan   # negative or missing gross SR → no valid limit

        # --- Annual cost: from Section A (fleet_sr_cost × avg_to) ---
        a_row = section_a_df[section_a_df["rule"] == rule]
        if len(a_row):
            avg_to      = float(a_row["avg_to"].iloc[0])
            annual_cost = float(a_row["annual_drag"].iloc[0])   # = fleet_sr_cost × avg_to
        else:
            avg_to      = np.nan
            annual_cost = np.nan

        # --- Flag ---
        if np.isfinite(speed_limit) and np.isfinite(annual_cost):
            headroom = speed_limit - annual_cost
            if annual_cost > speed_limit:
                status = "FAIL"
            elif annual_cost > 0.8 * speed_limit:
                status = "WARN"
            else:
                status = "PASS"
        elif np.isfinite(gross_sr) and gross_sr <= 0:
            headroom = np.nan
            status = "FAIL"   # pre-cost losing rule automatically fails
        else:
            headroom = np.nan
            status = "N/A"

        rows.append({
            "rule":        rule,
            "gross_sr":    gross_sr,
            "speed_limit": speed_limit,
            "avg_to":      avg_to,
            "annual_cost": annual_cost,
            "headroom":    headroom,
            "status":      status,
        })

    df = pd.DataFrame(rows)

    # Sort: FAIL first, then WARN, then PASS, then N/A; within group by headroom asc
    status_order = {"FAIL": 0, "WARN": 1, "PASS": 2, "N/A": 3}
    df["_sort_key"] = df["status"].map(status_order)
    df = df.sort_values(["_sort_key", "headroom"], ascending=[True, True]).drop(columns="_sort_key").reset_index(drop=True)

    # --- Print table ---
    header = (
        f"{'#':>3} {'Rule':<22} {'GrossSR':>9} {'SpeedLim':>10}"
        f" {'AvgTO':>7} {'AnnCost':>9} {'Headroom':>10} {'Status'}"
    )
    sep = "=" * len(header)
    print(f"\n{sep}")
    print(header)
    print(sep)

    def fmt(v, d=3):
        return f"{v:{'.'+str(d)+'f'}}" if (v is not None and pd.notna(v)) else "    NaN"

    for i, row in df.iterrows():
        status_flag = ""
        if row["status"] == "FAIL":
            status_flag = "<<< FAILS SPEED LIMIT"
        elif row["status"] == "WARN":
            status_flag = "<  within 20% of limit"
        elif np.isfinite(row.get("gross_sr", np.nan)) and row["gross_sr"] <= 0:
            status_flag = "<<< negative gross SR"
        print(
            f"{i+1:>3} {row['rule']:<22}"
            f" {fmt(row['gross_sr'], 3):>9}"
            f" {fmt(row['speed_limit'], 3):>10}"
            f" {fmt(row['avg_to'], 1):>7}"
            f" {fmt(row['annual_cost'], 4):>9}"
            f" {fmt(row['headroom'], 4):>10}"
            f"  {row['status']}  {status_flag}"
        )
    print(sep)

    # --- Summary ---
    n_fail = (df["status"] == "FAIL").sum()
    n_warn = (df["status"] == "WARN").sum()
    n_pass = (df["status"] == "PASS").sum()
    print(f"\n  Speed limit results: {n_pass} PASS | {n_warn} WARN | {n_fail} FAIL (of {len(df)} rules)")

    fail_rules = df[df["status"] == "FAIL"]
    if len(fail_rules):
        print(f"\n  Rules failing Carver speed limit (annual_cost > gross_SR/3):")
        for _, r in fail_rules.iterrows():
            if np.isfinite(r["gross_sr"]) and r["gross_sr"] <= 0:
                print(f"    {r['rule']:<22}  gross_SR={r['gross_sr']:.3f} (pre-cost losing rule)")
            else:
                print(
                    f"    {r['rule']:<22}  gross_SR={r['gross_sr']:.3f}"
                    f"  limit={r['speed_limit']:.3f}"
                    f"  cost={r['annual_cost']:.4f}"
                    f"  over by {-r['headroom']:.4f} SR"
                )
        print(f"\n  Recommendation: consider reducing forecast_weight proportionally to headroom,")
        print(f"  or accept as diversification contribution if gross_SR is positive.")

    warn_rules = df[df["status"] == "WARN"]
    if len(warn_rules):
        print(f"\n  Rules within 20% of speed limit (monitor closely):")
        for _, r in warn_rules.iterrows():
            print(
                f"    {r['rule']:<22}  gross_SR={r['gross_sr']:.3f}"
                f"  limit={r['speed_limit']:.3f}"
                f"  cost={r['annual_cost']:.4f}"
                f"  headroom={r['headroom']:.4f}"
            )

    return df


# ============================================================================
# Main
# ============================================================================

def main():
    print(f"Cost Audit — {CONFIG_PATH}")
    print(f"Data:        {DATA_PATH}")
    print(f"Thresholds:  SR/trade ≤ {MAX_SR_PER_TRADE} | Annual SR ≤ {MAX_SR_ANNUAL}")
    print(f"Fee (1-way): {FEE_BPS} bps | Fleet turnover: {FLEET_TURNOVER} rt/yr")

    print("\nBuilding system...")
    system = build_system()
    active_weights = get_active_weights(system)
    print(f"Active rules: {len(active_weights)} | Instruments in dataset: {len(system.data.get_instrument_list())}")

    a_df = section_a(system)
    b_df = section_b(system)
    c_rule_df, c_inst_df = section_c(system, a_df, b_df)
    d_df = section_d(system, a_df, c_rule_df)

    print("\n" + "=" * 80)
    print("AUDIT COMPLETE")
    print("=" * 80)
    print("\nSummary of findings:")
    print(f"  Section A: {len(a_df[a_df['ratio'] > 2.0])} rules with turnover ratio > 2.0")
    n_fail_trade  = (b_df["sr_per_trade"] > MAX_SR_PER_TRADE).sum()
    n_fail_annual = (b_df["annual_sr"]    > MAX_SR_ANNUAL).sum()
    print(f"  Section B: {n_fail_trade}/{len(b_df)} instruments fail SR/trade, {n_fail_annual}/{len(b_df)} fail annual SR")
    severe_c = (c_rule_df["pct_fail_annual"] > 50).sum()
    print(f"  Section C: {severe_c}/{len(c_rule_df)} rules fail annual SR test on >50% of instruments")
    speed_fail = (d_df["status"] == "FAIL").sum()
    print(f"  Section D: {speed_fail}/{len(d_df)} rules fail speed limit (annual_cost > gross_SR/3)")
    print("\nNext steps: see ACTION ITEMS printed above.")


if __name__ == "__main__":
    main()
