#!/usr/bin/env python3
"""
Verify that positions.csv contains base-asset token counts, NOT USD notionals.

This script demonstrates the units mismatch bug: pysystemtrade's get_notional_position()
returns base-asset contracts (e.g., PENGU tokens), not USD. The trade plan was treating
these as USD, causing massive over-sizing for low-price instruments.

Usage:
    python scripts/verify_position_units.py --backtest-dir out/fee_fix_1k
"""

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description="Verify position units in backtest output")
    parser.add_argument("--backtest-dir", required=True, type=Path)
    args = parser.parse_args()

    backtest_dir = args.backtest_dir
    if not backtest_dir.is_absolute():
        backtest_dir = ROOT / backtest_dir

    # --- Load positions (token counts) ---
    positions_path = backtest_dir / "positions.csv"
    if not positions_path.exists():
        print(f"ERROR: {positions_path} not found", file=sys.stderr)
        sys.exit(1)

    positions_df = pd.read_csv(positions_path, index_col=0, parse_dates=True)
    last_tokens = positions_df.iloc[-1].dropna()
    last_date = positions_df.index[-1].date()

    # --- Load last prices ---
    prices_path = backtest_dir / "last_prices.json"
    if not prices_path.exists():
        print(f"ERROR: {prices_path} not found — re-run backtest to generate", file=sys.stderr)
        sys.exit(1)

    with open(prices_path) as f:
        prices_raw = json.load(f)
    prices = pd.Series(prices_raw)

    # --- Compute USD notionals ---
    common = last_tokens.index.intersection(prices.index)
    tokens = last_tokens.loc[common]
    px = prices.loc[common]
    usd_notionals = tokens * px

    # --- Load diagnostics for forecasts ---
    diag_path = backtest_dir / "diagnostics.parquet"
    forecast_map = {}
    if diag_path.exists():
        diag = pd.read_parquet(diag_path)
        last_diag_date = diag["date"].max() if "date" in diag.columns else diag.index.get_level_values(0).max()
        if "date" in diag.columns:
            last_diag = diag[diag["date"] == last_diag_date].set_index("instrument")
        else:
            last_diag = diag.xs(last_diag_date)
        if "combined_forecast" in last_diag.columns:
            forecast_map = last_diag["combined_forecast"].dropna().to_dict()

    # --- Print results ---
    print()
    print("=" * 90)
    print(f"UNITS MISMATCH VERIFICATION  —  backtest dir: {backtest_dir.name}")
    print(f"Last backtest date: {last_date}")
    print(f"N instruments with positions: {(tokens != 0).sum()}")
    print("=" * 90)
    print()

    # Sort by absolute error ratio (largest first)
    rows = []
    for inst in sorted(common):
        tok = tokens[inst]
        if tok == 0.0:
            continue
        p = px[inst]
        usd = usd_notionals[inst]
        fc = forecast_map.get(inst, float("nan"))
        # error ratio: what the old code thought vs what it should be
        ratio = abs(tok / usd) if abs(usd) > 1e-10 else float("nan")
        rows.append((inst, tok, p, usd, fc, ratio))

    rows.sort(key=lambda r: r[5] if not pd.isna(r[5]) else 0, reverse=True)

    header = f"{'Instrument':<24} {'Tokens':>10} {'Price':>9} {'USD (correct)':>14} {'USD (old/wrong)':>16} {'Error':>8} {'Forecast':>10}"
    print(header)
    print("-" * len(header))

    for inst, tok, p, usd, fc, ratio in rows:
        fc_str = f"{fc:+.2f}" if not pd.isna(fc) else "  n/a"
        ratio_str = f"{ratio:.1f}×" if not pd.isna(ratio) else "n/a"
        print(
            f"{inst:<24} {tok:>10.1f} {p:>9.5f} {usd:>+14.2f} {tok:>+16.1f} {ratio_str:>8} {fc_str:>10}"
        )

    print()
    print("INTERPRETATION")
    print("-" * 60)
    print("  'Tokens'      = what positions.csv contains")
    print("  'USD (correct)' = tokens × price = true USD notional")
    print("  'USD (old/wrong)' = how trade_plan.py was reading it (tokens as USD)")
    print("  'Error' = how many times larger the old target was vs correct")
    print()

    # Summary stats
    non_trivial = [(inst, tok, p, usd, fc, r) for inst, tok, p, usd, fc, r in rows if not pd.isna(r)]
    if non_trivial:
        max_err = max(r for *_, r in non_trivial)
        median_err = sorted(r for *_, r in non_trivial)[len(non_trivial) // 2]
        print(f"  Max error ratio:    {max_err:.1f}× (instrument: {non_trivial[0][0]})")
        print(f"  Median error ratio: {median_err:.1f}×")
        total_usd_correct = sum(abs(usd) for _, _, _, usd, _, _ in non_trivial)
        total_usd_wrong = sum(abs(tok) for _, tok, _, _, _, _ in non_trivial)
        print(f"  Total gross notional (correct): ${total_usd_correct:,.2f}")
        print(f"  Total gross notional (old):     ${total_usd_wrong:,.2f}")

    print()
    print("CONCLUSION: positions.csv stores BASE-ASSET TOKEN COUNTS, not USD.")
    print("The bug is in trade_plan.py: targets must be multiplied by last_prices.json")
    print("before comparing to actual USD positions.")
    print()

    # --- Impact on current positions ---
    current_positions_path = ROOT / "live" / "current_positions.csv"
    if current_positions_path.exists():
        print("=" * 90)
        print("IMPACT ON CURRENT LIVE POSITIONS")
        print("=" * 90)
        live = pd.read_csv(current_positions_path)
        live = live.set_index("instrument")

        print()
        header2 = f"{'Instrument':<24} {'Live contracts':>15} {'Live price':>12} {'Live USD':>10} {'Target USD':>12} {'Wrong target':>14} {'Delta (correct)':>16}"
        print(header2)
        print("-" * len(header2))

        for inst, tok, p, usd_target, fc, ratio in rows:
            if inst not in live.index:
                continue
            live_contracts = live.loc[inst, "contracts"]
            # Live price from prices (use same backtest price as proxy)
            live_price = px.get(inst, 0.0)
            live_usd = live_contracts * live_price
            correct_delta = usd_target - live_usd
            wrong_delta = tok - live_usd  # what the old code computed

            print(
                f"{inst:<24} {live_contracts:>15.1f} {live_price:>12.5f} {live_usd:>+10.2f} {usd_target:>+12.2f} {tok:>+14.1f} {correct_delta:>+16.2f}"
            )

        print()
        print("  'Delta (correct)' = what the trade plan SHOULD have recommended")
        print("  Positive delta = need to BUY (reduce short / add long)")
        print()


if __name__ == "__main__":
    main()
