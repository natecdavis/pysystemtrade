"""
Audit cost model and position limits for crypto perps backtest.

Diagnostic-only script — no model changes. Loads existing artefacts and prints
a structured audit report covering:
  A. Fee model accuracy (P&L path vs filter path vs actual Binance)
  B. Funding cost impact (not captured in backtest P&L)
  C. Position minimum audit (Binance lot sizes at $10k capital)

Usage:
    python scripts/audit_cost_and_position_limits.py \\
        --config config/crypto_perps_full_rules.yaml \\
        --data data/dataset_538registry_6yr_jagged.parquet \\
        --backtest-dir out/vol_window_sweep/run_35 \\
        --capital 10000
"""

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
import yaml


# ---------------------------------------------------------------------------
# Binance perpetual minimum notional / lot sizes (hard-coded reference table).
# Source: Binance Futures contract specifications (standard USDT-margined perps).
# "min_notional" is in USD; "min_qty" is in base-asset units.
# ---------------------------------------------------------------------------
BINANCE_MIN_NOTIONAL = {
    "BTCUSDT_PERP":  {"min_notional": 100.0, "min_qty": 0.001,  "note": "0.001 BTC lot"},
    "ETHUSDT_PERP":  {"min_notional":  20.0, "min_qty": 0.01,   "note": "0.01 ETH lot"},
    "BNBUSDT_PERP":  {"min_notional":  10.0, "min_qty": 0.01,   "note": "0.01 BNB lot"},
    "SOLUSDT_PERP":  {"min_notional":   5.0, "min_qty": 0.1,    "note": "0.1 SOL lot"},
    "XRPUSDT_PERP":  {"min_notional":   5.0, "min_qty": 1.0,    "note": "1 XRP lot"},
    "DOGEUSDT_PERP": {"min_notional":   5.0, "min_qty": 10.0,   "note": "10 DOGE lot"},
    "default":       {"min_notional":   5.0, "min_qty": None,   "note": "Binance universal floor"},
}
UNIVERSAL_MIN_NOTIONAL = 5.0   # $5 — Binance minimum for most alts


def parse_args():
    p = argparse.ArgumentParser(description="Audit cost model and position limits")
    p.add_argument("--config", default="config/crypto_perps_full_rules.yaml")
    p.add_argument("--data", default="data/dataset_538registry_6yr_jagged.parquet")
    p.add_argument("--backtest-dir", default="out/vol_window_sweep/run_35")
    p.add_argument("--capital", type=float, default=10000.0,
                   help="Capital to audit against (default 10000)")
    return p.parse_args()


# ---------------------------------------------------------------------------
# Section A — Fee Model Audit
# ---------------------------------------------------------------------------

def audit_fee_model(market_info: dict, config: dict) -> None:
    print()
    print("=" * 70)
    print("=== A. FEE MODEL AUDIT ===")
    print("=" * 70)

    # --- P&L path: from binance_market_info.json ---
    spreads = [v["spread_frac"] for v in market_info.values()]
    fees    = [v["taker_fee_frac"] for v in market_info.values()]

    spread_uniform = len(set(spreads)) == 1
    fee_uniform    = len(set(fees)) == 1

    spread_val = spreads[0] if spread_uniform else np.mean(spreads)
    fee_val    = fees[0]    if fee_uniform    else np.mean(fees)

    half_spread  = spread_val / 2
    one_way_pnl  = half_spread + fee_val          # half-spread + fee (one side)
    roundtrip_pnl = spread_val + 2 * fee_val      # full spread + 2×fee

    print()
    print("P&L cost path  (get_raw_cost_data — from binance_market_info.json):")
    spread_note = "uniform" if spread_uniform else "mean"
    fee_note    = "uniform" if fee_uniform    else "mean"
    print(f"  spread_frac ({spread_note}, {len(market_info)} instruments):  "
          f"{spread_val:.5f}  = {spread_val*1e4:.1f} bps")
    print(f"  taker_fee_frac ({fee_note}):                  "
          f"{fee_val:.5f}  = {fee_val*1e4:.1f} bps")
    print(f"  One-way cost:    half_spread + fee  = "
          f"{half_spread*1e4:.2f} + {fee_val*1e4:.1f}  = {one_way_pnl*1e4:.2f} bps")
    print(f"  Round-trip cost: spread + 2×fee     = "
          f"{spread_val*1e4:.1f} + {2*fee_val*1e4:.1f}   = {roundtrip_pnl*1e4:.1f} bps  ← used in Sharpe calc")

    # --- Filter path: dynamic_universe config ---
    du_cfg = config.get("dynamic_universe", {})
    fee_bps_filter  = du_cfg.get("fee_bps", 5.0)
    # Spread used in filter: same metadata source (monkey-patched); assume same spread_val
    spread_filter   = spread_val
    roundtrip_filter = spread_filter * 1e4 + 2 * fee_bps_filter

    print()
    print("Universe filter cost path  (dynamic_universe._get_sr_cost_series):")
    print(f"  spread_frac (from metadata):               {spread_filter*1e4:.1f} bps")
    print(f"  fee_bps (from config dynamic_universe):    {fee_bps_filter:.1f} bps")
    print(f"  Round-trip cost used for eligibility:      {roundtrip_filter:.1f} bps")

    # --- Actual Binance ---
    actual_fee_bps    = 5.0    # standard taker, no BNB discount
    actual_spread_btc = 1.5    # representative mid-market BTC
    actual_spread_alt = 4.0    # representative mid-cap alt
    actual_spread_micro = 20.0 # micro-cap alt (rough)
    rt_btc   = actual_spread_btc  + 2 * actual_fee_bps
    rt_alt   = actual_spread_alt  + 2 * actual_fee_bps
    rt_micro = actual_spread_micro + 2 * actual_fee_bps

    print()
    print("Actual Binance Futures (standard taker, no BNB discount):")
    print(f"  Taker fee:      {actual_fee_bps:.1f} bps per side → {2*actual_fee_bps:.0f} bps round-trip on fees alone")
    print(f"  Spread (BTC):   ~{actual_spread_btc:.0f} bps    → round-trip {rt_btc:.1f} bps")
    print(f"  Spread (alts):  ~{actual_spread_alt:.0f} bps    → round-trip {rt_alt:.1f} bps")
    print(f"  Spread (micro): ~{actual_spread_micro:.0f} bps   → round-trip {rt_micro:.1f} bps")

    # --- Anomaly check: any instruments that deviate from uniform ---
    print()
    non_uniform = [(k, v) for k, v in market_info.items()
                   if v["spread_frac"] != spread_val or v["taker_fee_frac"] != fee_val]
    if non_uniform:
        print(f"  [!] {len(non_uniform)} instruments have non-uniform cost data:")
        for instr, vals in non_uniform[:5]:
            print(f"      {instr}: spread={vals['spread_frac']*1e4:.1f}bps fee={vals['taker_fee_frac']*1e4:.1f}bps")
    else:
        print(f"  [INFO] All {len(market_info)} instruments have identical cost data (generated synthetically)")

    # --- Gaps summary ---
    fee_gap_bps = (actual_fee_bps - fee_val * 1e4) * 2   # round-trip gap in bps
    print()
    print("GAPS:")
    if fee_val * 1e4 < actual_fee_bps:
        print(f"  [!] P&L fee underestimated: {fee_val*1e4:.0f} bps used vs {actual_fee_bps:.0f} bps actual"
              f" → -{fee_gap_bps:.0f} bps round-trip undercharge")
    else:
        print(f"  [OK] P&L fee matches actual: {fee_val*1e4:.0f} bps")

    print(f"  [!] Spread uniform {spread_val*1e4:.1f} bps for ALL {len(market_info)} instruments"
          f" — micro-alts likely {actual_spread_micro:.0f}+ bps in reality")
    print(f"  [OK] Universe filter is conservative ({roundtrip_filter:.1f} bps vs"
          f" ~{rt_btc:.1f}–{rt_alt:.1f} bps actual for BTC/alts)")
    print("  [INFO] Slippage: not modelled separately; half-spread is the slippage proxy")


# ---------------------------------------------------------------------------
# Section B — Funding Cost Impact
# ---------------------------------------------------------------------------

def audit_funding_costs(dataset_path: str, backtest_dir: Path, config: dict,
                        capital: float) -> None:
    print()
    print("=" * 70)
    print("=== B. FUNDING COST IMPACT ===")
    print("=" * 70)

    # --- Load dataset ---
    df = pd.read_parquet(dataset_path)

    fund = df[["date", "instrument", "close", "funding_rate"]].copy()
    fund = fund.dropna(subset=["funding_rate"])
    fund["date"] = pd.to_datetime(fund["date"])
    fund["year"] = fund["date"].dt.year

    # --- Cross-sectional funding summary ---
    daily_mean = fund.groupby("date")["funding_rate"].mean()

    print()
    print("Daily funding rate summary (cross-sectional mean across all instruments × dates):")
    print(f"  Mean:   {daily_mean.mean():+.6f}/day  → {daily_mean.mean()*365:+.1%}/year annualised")
    print(f"  Median: {daily_mean.median():+.6f}/day")
    pcts = daily_mean.quantile([0.25, 0.75, 0.95])
    print(f"  P25:    {pcts[0.25]:+.6f}/day")
    print(f"  P75:    {pcts[0.75]:+.6f}/day")
    print(f"  P95:    {pcts[0.95]:+.6f}/day")

    # --- Year-by-year ---
    yearly = fund.groupby("year")["funding_rate"].mean()
    print()
    print("Year-by-year mean daily funding rate (cross-sectional average):")
    cols = list(yearly.items())
    # Print in groups of 3
    for i in range(0, len(cols), 3):
        row = "  ".join(f"{yr}: {val:+.5f}" for yr, val in cols[i:i+3])
        print(f"  {row}")
    print(f"  (Annualised: ", end="")
    ann_parts = "  ".join(f"{yr}: {val*365:+.1%}" for yr, val in cols)
    print(f"{ann_parts})")

    # --- Load positions from diagnostics ---
    diag_path = backtest_dir / "diagnostics.parquet"
    if not diag_path.exists():
        print()
        print("[WARN] diagnostics.parquet not found — skipping position-weighted funding analysis")
        return

    diag = pd.read_parquet(diag_path)
    diag["date"] = pd.to_datetime(diag["date"])

    # Merge with prices and funding
    prices_df = df[["date", "instrument", "close", "funding_rate"]].copy()
    prices_df["date"] = pd.to_datetime(prices_df["date"])

    merged = diag.merge(prices_df, on=["date", "instrument"], how="left")

    # Scale capital: backtest uses config capital; we want user's capital
    config_capital = float(config.get("notional_trading_capital", 5000.0))
    scale = capital / config_capital

    # Dollar notional of each position
    merged["notional"] = merged["position"].abs() * merged["close"] * scale
    merged["signed_notional"] = merged["position"] * merged["close"] * scale

    # Daily net long/short
    daily_pos = merged.groupby("date").agg(
        gross_long=("signed_notional", lambda x: x[x > 0].sum()),
        gross_short=("signed_notional", lambda x: x[x < 0].sum()),
        net_notional=("signed_notional", "sum"),
        capital_deployed=("notional", "sum"),
    )

    avg_long  = daily_pos["gross_long"].mean()
    avg_short = daily_pos["gross_short"].abs().mean()
    avg_net   = daily_pos["net_notional"].mean()
    avg_cap   = daily_pos["capital_deployed"].mean()

    net_long_frac = avg_net / capital if capital > 0 else 0.0

    print()
    print(f"Signed position analysis (baseline backtest, ${capital:,.0f} capital equivalent):")
    print(f"  Average gross long notional per day:  ${avg_long:>8,.0f}")
    print(f"  Average gross short notional per day: ${avg_short:>8,.0f}")
    print(f"  Average net long notional per day:    ${avg_net:>8,.0f}")
    print(f"  Average net long fraction of capital: {net_long_frac:>+.1%}")

    # --- Estimate funding drag ---
    ann_funding_rate = daily_mean.mean() * 365
    funding_drag_pct = ann_funding_rate * net_long_frac
    funding_drag_usd = funding_drag_pct * capital

    vol_target = float(config.get("percentage_vol_target", 25.0)) / 100.0
    # Approximate portfolio vol (vol target × IDM shrinkage; use vol_target as rough proxy)
    portfolio_vol = vol_target

    sharpe_drag = funding_drag_pct / portfolio_vol if portfolio_vol > 0 else float("nan")

    perf_path = backtest_dir / "performance_summary.json"
    reported_sharpe = float("nan")
    if perf_path.exists():
        with open(perf_path) as f:
            perf = json.load(f)
        reported_sharpe = perf.get("metrics", {}).get("sharpe", float("nan"))

    print()
    print("Estimated annual funding drag (on net long positions):")
    print(f"  Annualised funding rate (cross-sectional mean): {ann_funding_rate:+.1%}/year")
    print(f"  Net long fraction of capital:                   {net_long_frac:+.1%}")
    print(f"  Funding drag (rate × fraction × capital):       ${funding_drag_usd:,.0f}/yr"
          f"  =  {funding_drag_pct:+.2%} of capital")

    print()
    print(f"Implied Sharpe reduction (if funding were charged):")
    print(f"  Portfolio vol target:   {portfolio_vol:.0%}")
    print(f"  Funding drag / vol:     {funding_drag_pct:.2%} / {portfolio_vol:.0%}"
          f"  =  {sharpe_drag:+.2f} Sharpe points")
    adj_sharpe = reported_sharpe + sharpe_drag
    print()
    print(f"NOTE: Funding drag is NOT included in backtest returns. Reported Sharpe = {reported_sharpe:.2f}")
    print(f"      Adjusting for funding, estimated net Sharpe ≈ {adj_sharpe:.2f}")
    print(f"      (This is a rough estimate; actual drag depends on realised funding by instrument)")


# ---------------------------------------------------------------------------
# Section C — Position Minimum Audit
# ---------------------------------------------------------------------------

def audit_position_minimums(dataset_path: str, backtest_dir: Path, config: dict,
                             capital: float) -> None:
    print()
    print("=" * 70)
    print(f"=== C. POSITION MINIMUM AUDIT (${capital:,.0f} capital) ===")
    print("=" * 70)

    diag_path = backtest_dir / "diagnostics.parquet"
    if not diag_path.exists():
        print("[WARN] diagnostics.parquet not found — cannot run position audit")
        return

    # Load data
    diag = pd.read_parquet(diag_path)
    diag["date"] = pd.to_datetime(diag["date"])

    df = pd.read_parquet(dataset_path)
    df["date"] = pd.to_datetime(df["date"])
    prices_df = df[["date", "instrument", "close"]].copy()

    # Merge
    merged = diag.merge(prices_df, on=["date", "instrument"], how="left")
    config_capital = float(config.get("notional_trading_capital", 5000.0))
    scale = capital / config_capital

    merged["notional_10k"] = merged["position"].abs() * merged["close"] * scale

    # Filter to non-zero positions with valid prices
    active = merged[
        merged["position"].notna() &
        (merged["position"] != 0) &
        merged["notional_10k"].notna() &
        (merged["notional_10k"] > 0)
    ].copy()

    total_active = len(active)
    print()
    print(f"Non-zero position notional distribution (across all instruments × dates):")
    print(f"  Total active instrument-days: {total_active:,}")

    if total_active == 0:
        print("[WARN] No active positions found")
        return

    n = active["notional_10k"]
    pcts = n.quantile([0.0, 0.05, 0.25, 0.50, 0.75, 0.95, 1.0])
    print(f"  Min:    ${pcts[0.00]:>8.2f}    "
          f"P5:  ${pcts[0.05]:>8.2f}    "
          f"P25: ${pcts[0.25]:>8.2f}")
    print(f"  Median: ${pcts[0.50]:>8.2f}    "
          f"P75: ${pcts[0.75]:>8.2f}    "
          f"P95: ${pcts[0.95]:>8.2f}    "
          f"Max: ${pcts[1.00]:>8,.2f}")

    # --- Threshold checks ---
    thresh_5   = (active["notional_10k"] < 5.0).sum()
    thresh_25  = (active["notional_10k"] < 25.0).sum()
    thresh_100 = (active["notional_10k"] < 100.0).sum()

    print()
    print("Positions below Binance minimum notional thresholds:")
    print(f"  Below $5   (universal floor):      {thresh_5:>6,} instrument-days  "
          f"({thresh_5/total_active:>5.1%} of active positions)")
    print(f"  Below $25  (positionsizing default):{thresh_25:>6,}                 "
          f"({thresh_25/total_active:>5.1%})")
    print(f"  Below $100 (BTC lot minimum):       {thresh_100:>6,}                 "
          f"({thresh_100/total_active:>5.1%})")

    # --- BTC-specific lot size check ---
    print()
    btc_col = None
    for candidate in ["BTCUSDT_PERP", "BTCUSDT"]:
        btc_mask = (merged["instrument"] == candidate) & merged["position"].notna() & (merged["position"] != 0)
        if btc_mask.any():
            btc_col = candidate
            break

    if btc_col:
        btc_active = merged[
            (merged["instrument"] == btc_col) &
            merged["position"].notna() &
            (merged["position"] != 0)
        ].copy()
        btc_active["qty_10k"] = btc_active["position"].abs() * scale

        min_btc_qty = btc_active["qty_10k"].min()
        days_below_lot = (btc_active["qty_10k"] < 0.001).sum()
        total_btc_days = len(btc_active)
        frac_below = days_below_lot / total_btc_days if total_btc_days > 0 else 0.0

        # Dollar value of sub-minimum positions
        btc_sub = btc_active[btc_active["qty_10k"] < 0.001]
        avg_usd_below = (btc_sub["qty_10k"] * btc_sub["close"]).mean() if len(btc_sub) > 0 else 0.0

        print(f"BTC-specific lot size check ({btc_col}):")
        print(f"  Total BTC trading days (non-zero position): {total_btc_days:,}")
        print(f"  Min BTC position seen (at ${capital:,.0f} capital):  "
              f"{min_btc_qty:.6f} BTC")
        print(f"  Days below 0.001 BTC lot:  {days_below_lot:,}  ({frac_below:.1%} of BTC trading days)")
        if avg_usd_below > 0:
            print(f"  Average USD value of sub-minimum positions: ${avg_usd_below:.2f}")
    else:
        print("BTC-specific lot size check: BTC instrument not found in active positions")

    # --- Per-instrument breakdown: smallest positions ---
    print()
    print("Top 10 instruments with smallest median active position notional:")
    per_instr = (
        active.groupby("instrument")["notional_10k"]
        .agg(
            median_notional="median",
            days_active="count",
            days_sub5=lambda x: (x < 5.0).sum(),
            days_sub25=lambda x: (x < 25.0).sum(),
        )
        .sort_values("median_notional")
        .head(10)
    )
    for instr, row in per_instr.iterrows():
        pct_sub5 = row["days_sub5"] / row["days_active"] if row["days_active"] > 0 else 0
        pct_sub25 = row["days_sub25"] / row["days_active"] if row["days_active"] > 0 else 0
        print(f"  {instr:<22}  median ${row['median_notional']:>7.2f}  "
              f"({row['days_active']:>4,.0f} days active,  "
              f"{pct_sub5:.0%} sub-$5,  {pct_sub25:.0%} sub-$25)")

    # --- Verdict ---
    print()
    print("VERDICT:")
    if thresh_5 / total_active < 0.01:
        print(f"  [OK]   Below $5:   {thresh_5/total_active:.1%} — minimal phantom trades at universal floor")
    elif thresh_5 / total_active < 0.05:
        print(f"  [WARN] Below $5:   {thresh_5/total_active:.1%} — some phantom trades at universal floor")
    else:
        print(f"  [FAIL] Below $5:   {thresh_5/total_active:.1%} — significant phantom trades at universal floor")

    if thresh_25 / total_active < 0.05:
        print(f"  [OK]   Below $25:  {thresh_25/total_active:.1%} — positionsizing default not needed")
    elif thresh_25 / total_active < 0.15:
        print(f"  [WARN] Below $25:  {thresh_25/total_active:.1%} — consider enabling min_notional_position=25")
    else:
        print(f"  [FAIL] Below $25:  {thresh_25/total_active:.1%} — enable min_notional_position=25 in config")

    if btc_col and days_below_lot / total_btc_days < 0.01:
        print(f"  [OK]   BTC lots:   {days_below_lot/total_btc_days:.1%} of BTC days below 0.001 BTC lot")
    elif btc_col and days_below_lot / total_btc_days < 0.10:
        print(f"  [WARN] BTC lots:   {days_below_lot/total_btc_days:.1%} of BTC days below 0.001 BTC lot — minor overstating")
    elif btc_col:
        print(f"  [FAIL] BTC lots:   {days_below_lot/total_btc_days:.1%} of BTC days below 0.001 BTC lot — Sharpe overstated")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    args = parse_args()

    config_path  = Path(args.config)
    dataset_path = args.data
    backtest_dir = Path(args.backtest_dir)
    capital      = args.capital

    # Load config
    with open(config_path) as f:
        config = yaml.safe_load(f)

    # Load market info
    market_info_path = Path("data/raw/metadata/binance_market_info.json")
    with open(market_info_path) as f:
        market_info = json.load(f)

    print()
    print("=" * 70)
    print("CRYPTO PERPS BACKTEST — COST MODEL & POSITION LIMITS AUDIT")
    print("=" * 70)
    print(f"  Config:       {config_path}")
    print(f"  Dataset:      {dataset_path}")
    print(f"  Backtest dir: {backtest_dir}")
    print(f"  Audit capital: ${capital:,.0f}")
    config_capital = float(config.get("notional_trading_capital", 5000.0))
    print(f"  Config capital: ${config_capital:,.0f}  "
          f"(scale factor: {capital/config_capital:.1f}×)")

    audit_fee_model(market_info, config)
    audit_funding_costs(dataset_path, backtest_dir, config, capital)
    audit_position_minimums(dataset_path, backtest_dir, config, capital)

    print()
    print("=" * 70)
    print("AUDIT COMPLETE")
    print("=" * 70)
    print()


if __name__ == "__main__":
    main()
