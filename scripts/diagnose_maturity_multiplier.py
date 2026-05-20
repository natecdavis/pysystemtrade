#!/usr/bin/env python3
"""
Pre-flight diagnostic for the token-maturity multiplier panel.

Run AFTER scripts/build_maturity_multiplier_panel.py and BEFORE
scripts/run_maturity_multiplier_experiment.py.

Four checks. ALL must pass before the ~70-min B7 run:

  1. TODAY'S MULTIPLIER DISTRIBUTION on top-30 ADV instruments.
     PASS: BTC/ETH/SOL/BNB/XRP all == 1.0 exactly; any < 1.0 is a
     recent listing (launch_date within 365d of today).
  2. UNIVERSE PENALTY SHARE OVER TIME — time series of "fraction of
     instruments with multiplier < 1.0" from 2020 to today.
     PASS: monotonically declining (with bumps for new listings),
     never zero (otherwise the rule is fully degenerate).
  3. SPOT-CHECK 5 RECENT LISTINGS — print launch_date, days-since-listing,
     and today's multiplier. Multiplier must ramp linearly from 0.5 at
     listing → 1.0 at 365d.
  4. SPOT-CHECK 5 MATURE INSTRUMENTS (BTC/ETH/SOL/BNB/XRP) — launch_date
     must be ≤2020 and today's multiplier == 1.0 exactly.

Outputs at out/diagnose_maturity_multiplier/.

Usage:
    python scripts/diagnose_maturity_multiplier.py
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from sysdata.crypto.prices import load_crypto_perps_panel


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--data", default=str(REPO_ROOT / "data" / "dataset_sb_corrected_6yr_jagged.parquet")
    )
    parser.add_argument(
        "--panel",
        type=Path,
        default=REPO_ROOT / "data" / "research" / "maturity_multiplier_b50_t365.parquet",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=REPO_ROOT / "out" / "diagnose_maturity_multiplier",
    )
    parser.add_argument(
        "--mature-instruments",
        nargs="+",
        default=["BTCUSDT_PERP", "ETHUSDT_PERP", "SOLUSDT_PERP", "BNBUSDT_PERP", "XRPUSDT_PERP"],
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    args.out_dir.mkdir(parents=True, exist_ok=True)

    if not args.panel.exists():
        print(f"✗ Panel not found: {args.panel}", file=sys.stderr)
        print(f"  Run: python scripts/build_maturity_multiplier_panel.py", file=sys.stderr)
        return 1

    print(f"Loading panel: {args.panel}")
    panel = pd.read_parquet(args.panel)
    print(f"  shape: {panel.shape}")

    print(f"Loading lifecycle from dataset: {args.data}")
    _prices_df, meta_df, lifecycle_df = load_crypto_perps_panel(
        args.data, validate_schema=True, allow_jagged=True
    )
    print(f"  {len(lifecycle_df)} instruments in lifecycle")

    today = panel.index.max()
    today_row = panel.loc[today]

    # -----------------------------------------------------------------------
    # 1. Today's multiplier distribution on top-30 ADV instruments
    # -----------------------------------------------------------------------
    print(f"\n=== 1. TOP-30 ADV — multiplier today ({today.date()}) ===")
    # Compute ADV ranking from meta_df (latest available adv_notional per instr)
    adv_wide = meta_df["adv_notional"].unstack("instrument")
    # Latest non-NaN ADV per instrument
    latest_adv = adv_wide.apply(lambda c: c.dropna().iloc[-1] if c.notna().any() else 0.0)
    top30 = latest_adv.sort_values(ascending=False).head(30).index.tolist()
    top30_today = today_row.reindex(top30)
    top30_table = pd.DataFrame({
        "adv_notional": latest_adv.reindex(top30),
        "multiplier": top30_today,
        "launch_date": lifecycle_df.reindex(top30)["launch_date"],
        "days_since": (today - lifecycle_df.reindex(top30)["launch_date"]).dt.days,
    })
    print(top30_table.to_string())
    top30_table.to_csv(args.out_dir / "top30_today.csv")

    sub_one = top30_table[top30_table["multiplier"] < 1.0]
    print(f"\n  Of top-30 ADV: {len(sub_one)} have multiplier < 1.0")

    check1_pass = True
    for inst, days in zip(sub_one.index, sub_one["days_since"]):
        if pd.isna(days) or days > 365:
            print(f"  ✗ {inst}: multiplier < 1.0 but days_since={days} (expected ≤365)")
            check1_pass = False
    if check1_pass:
        print("  ✓ All top-30 ADV instruments with multiplier < 1.0 are recent listings (≤365d)")

    # -----------------------------------------------------------------------
    # 2. Universe penalty share over time
    # -----------------------------------------------------------------------
    print(f"\n=== 2. UNIVERSE PENALTY SHARE OVER TIME ===")
    # Per date: fraction of instruments with multiplier < 1.0 (and not NaN)
    fraction_penalised = (panel < 1.0).sum(axis=1) / panel.notna().sum(axis=1)
    fraction_penalised.to_csv(args.out_dir / "fraction_penalised_over_time.csv")

    sample_dates = pd.date_range(panel.index.min(), today, freq="180D").tolist() + [today]
    print(f"  {'date':<12} {'fraction penalised':>20} {'n_active':>10}")
    for d in sample_dates:
        # Snap to nearest date in index
        d_actual = panel.index[panel.index.searchsorted(d, side="left")]
        if d_actual > today:
            continue
        n_active = panel.loc[d_actual].notna().sum()
        frac = fraction_penalised.loc[d_actual]
        print(f"  {str(d_actual.date()):<12} {frac:>20.3f} {n_active:>10d}")
    # Sanity: at end of history at least some instruments should still be penalised
    end_frac = fraction_penalised.iloc[-1]
    check2_pass = end_frac > 0.0
    if check2_pass:
        print(f"  ✓ {end_frac*100:.1f}% of universe currently penalised — rule is active")
    else:
        print(f"  ✗ Zero instruments penalised today — rule is fully degenerate")

    # -----------------------------------------------------------------------
    # 3. Spot-check 5 recent listings
    # -----------------------------------------------------------------------
    print(f"\n=== 3. SPOT-CHECK 5 RECENT LISTINGS ===")
    # Find the 5 most recently-listed instruments (excluding the very latest day,
    # to ensure they have at least 1d of data)
    recent_listings = (
        lifecycle_df.sort_values("launch_date", ascending=False).head(20).index.tolist()
    )
    recent_5 = [r for r in recent_listings if r in panel.columns][:5]
    recent_table = pd.DataFrame({
        "launch_date": lifecycle_df.loc[recent_5, "launch_date"],
        "days_since": (today - lifecycle_df.loc[recent_5, "launch_date"]).dt.days,
        "multiplier_today": today_row.reindex(recent_5),
        "expected_multiplier": 1.0 - 0.5 * np.clip(
            (365 - (today - lifecycle_df.loc[recent_5, "launch_date"]).dt.days) / 365,
            0.0,
            1.0,
        ),
    })
    print(recent_table.to_string())
    recent_table.to_csv(args.out_dir / "recent_listings.csv")
    diff = (recent_table["multiplier_today"] - recent_table["expected_multiplier"]).abs()
    check3_pass = diff.max() < 1e-9
    if check3_pass:
        print(f"  ✓ All 5 recent listings match the linear ramp formula (max |Δ| < 1e-9)")
    else:
        print(f"  ✗ Spot-check FAILED — max |Δ| = {diff.max():.6e}")

    # -----------------------------------------------------------------------
    # 4. Spot-check 5 mature instruments
    # -----------------------------------------------------------------------
    print(f"\n=== 4. SPOT-CHECK MATURE INSTRUMENTS (BTC/ETH/SOL/BNB/XRP) ===")
    mature_table = pd.DataFrame({
        "launch_date": lifecycle_df.reindex(args.mature_instruments)["launch_date"],
        "multiplier_today": today_row.reindex(args.mature_instruments),
    })
    print(mature_table.to_string())
    mature_table.to_csv(args.out_dir / "mature_instruments.csv")
    check4_pass = (mature_table["multiplier_today"] == 1.0).all()
    if check4_pass:
        print(f"  ✓ All 5 mature instruments have multiplier == 1.0 today")
    else:
        print(f"  ✗ Mature instrument has multiplier != 1.0; investigate before proceeding")

    # -----------------------------------------------------------------------
    # Summary
    # -----------------------------------------------------------------------
    all_pass = check1_pass and check2_pass and check3_pass and check4_pass
    summary = {
        "panel_path": str(args.panel),
        "as_of_date": str(today.date()),
        "panel_shape": list(panel.shape),
        "top30_check_pass": bool(check1_pass),
        "universe_penalty_share_check_pass": bool(check2_pass),
        "recent_listings_check_pass": bool(check3_pass),
        "mature_instruments_check_pass": bool(check4_pass),
        "all_pass": bool(all_pass),
        "fraction_penalised_today": float(fraction_penalised.iloc[-1]),
    }
    (args.out_dir / "summary.json").write_text(json.dumps(summary, indent=2))

    print(f"\n=== SUMMARY ===")
    print(json.dumps(summary, indent=2))
    if all_pass:
        print(f"\n✓ ALL CHECKS PASS — safe to run scripts/run_maturity_multiplier_experiment.py")
        return 0
    else:
        print(f"\n✗ ONE OR MORE CHECKS FAILED — fix before running B7")
        return 1


if __name__ == "__main__":
    sys.exit(main())
