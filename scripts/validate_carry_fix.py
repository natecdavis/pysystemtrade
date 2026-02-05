#!/usr/bin/env python3
"""
Validate Carry Forecast Fix

Compares results before/after fixing rule_weights wiring bug.
Analyzes carry forecast impact on positions and PnL.
"""

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from pathlib import Path

def load_backtest_results(baseline_dir, carry_off_dir):
    """Load equity curves and positions from both scenarios."""
    baseline_equity = pd.read_csv(f"{baseline_dir}/equity_curve.csv", parse_dates=["date"])
    baseline_positions = pd.read_csv(f"{baseline_dir}/positions.csv", parse_dates=["date"])
    baseline_pnl = pd.read_csv(f"{baseline_dir}/pnl_breakdown.csv", parse_dates=["date"])
    baseline_diag = pd.read_parquet(f"{baseline_dir}/diagnostics.parquet")

    carry_off_equity = pd.read_csv(f"{carry_off_dir}/equity_curve.csv", parse_dates=["date"])
    carry_off_positions = pd.read_csv(f"{carry_off_dir}/positions.csv", parse_dates=["date"])
    carry_off_pnl = pd.read_csv(f"{carry_off_dir}/pnl_breakdown.csv", parse_dates=["date"])
    carry_off_diag = pd.read_parquet(f"{carry_off_dir}/diagnostics.parquet")

    return {
        "baseline": {
            "equity": baseline_equity,
            "positions": baseline_positions,
            "pnl": baseline_pnl,
            "diagnostics": baseline_diag
        },
        "carry_off": {
            "equity": carry_off_equity,
            "positions": carry_off_positions,
            "pnl": carry_off_pnl,
            "diagnostics": carry_off_diag
        }
    }

def compare_positions(baseline_positions, carry_off_positions):
    """Compare position differences between scenarios."""
    # Merge on date
    instruments = [col for col in baseline_positions.columns if col != "date"]

    diffs = {}
    for inst in instruments:
        baseline_pos = baseline_positions.set_index("date")[inst]
        carry_off_pos = carry_off_positions.set_index("date")[inst]

        # Position difference
        pos_diff = (baseline_pos - carry_off_pos).abs()

        diffs[inst] = {
            "mean_abs_diff": pos_diff.mean(),
            "median_abs_diff": pos_diff.median(),
            "max_abs_diff": pos_diff.max(),
            "days_different": (pos_diff > 0.01).sum(),
            "pct_days_different": 100 * (pos_diff > 0.01).sum() / len(pos_diff)
        }

    return diffs

def analyze_carry_direction(baseline_diag):
    """Analyze whether carry forecast agrees or disagrees with EWMAC."""
    baseline_diag = baseline_diag.copy()
    baseline_diag = baseline_diag[baseline_diag["forecast_combined"].notna()]

    # Compute EWMAC average (trend proxy)
    baseline_diag["ewmac_avg"] = (
        baseline_diag["forecast_ewmac_8_32"] + baseline_diag["forecast_ewmac_16_64"]
    ) / 2

    # Carry vs EWMAC agreement
    baseline_diag["carry_sign"] = np.sign(baseline_diag["forecast_carry_funding"])
    baseline_diag["ewmac_sign"] = np.sign(baseline_diag["ewmac_avg"])
    baseline_diag["same_sign"] = baseline_diag["carry_sign"] == baseline_diag["ewmac_sign"]

    agreement_pct = 100 * baseline_diag["same_sign"].mean()

    return {
        "agreement_pct": agreement_pct,
        "disagreement_pct": 100 - agreement_pct,
        "total_days": len(baseline_diag)
    }

def plot_comparison(baseline_equity, carry_off_equity, output_dir):
    """Plot equity curves comparison."""
    fig, axes = plt.subplots(2, 1, figsize=(14, 10))

    # Plot 1: Equity curves
    ax = axes[0]
    ax.plot(baseline_equity["date"], baseline_equity["equity"],
            label="Baseline (with carry, 1/3 weight)", linewidth=2)
    ax.plot(carry_off_equity["date"], carry_off_equity["equity"],
            label="Carry-Off (carry weight = 0)", linewidth=2, linestyle="--")
    ax.set_xlabel("Date")
    ax.set_ylabel("Equity ($)")
    ax.set_title("Equity Curves: Baseline vs Carry-Off (After Fix)")
    ax.legend()
    ax.grid(True, alpha=0.3)

    # Plot 2: Difference
    ax = axes[1]
    equity_diff = carry_off_equity["equity"] - baseline_equity["equity"]
    ax.plot(carry_off_equity["date"], equity_diff, color="red", linewidth=1.5)
    ax.axhline(0, color="black", linestyle="--", linewidth=1)
    ax.fill_between(carry_off_equity["date"], 0, equity_diff,
                     where=(equity_diff > 0), color="green", alpha=0.3, label="Carry-off outperforms")
    ax.fill_between(carry_off_equity["date"], 0, equity_diff,
                     where=(equity_diff < 0), color="red", alpha=0.3, label="Baseline outperforms")
    ax.set_xlabel("Date")
    ax.set_ylabel("Equity Difference ($)")
    ax.set_title("Carry-Off minus Baseline (positive = carry hurts performance)")
    ax.legend()
    ax.grid(True, alpha=0.3)

    plt.tight_layout()
    plt.savefig(f"{output_dir}/carry_fix_validation.png", dpi=150)
    plt.close()

def main():
    print("=" * 80)
    print("CARRY FORECAST FIX VALIDATION")
    print("=" * 80)
    print()

    # Load OLD results (before fix)
    print("Loading OLD results (before fix)...")
    old = load_backtest_results("out/stage1_baseline", "out/stage1_carry_off")

    # Load NEW results (after fix)
    print("Loading NEW results (after fix)...")
    new = load_backtest_results("out/stage1_baseline_fixed", "out/stage1_carry_off_fixed")

    print()
    print("=" * 80)
    print("PART 1: BEFORE vs AFTER FIX COMPARISON")
    print("=" * 80)
    print()

    # OLD results
    old_baseline_final = old["baseline"]["equity"]["equity"].iloc[-1]
    old_carry_off_final = old["carry_off"]["equity"]["equity"].iloc[-1]
    old_delta = old_carry_off_final - old_baseline_final

    print("BEFORE FIX (rule_weights not wired):")
    print(f"  Baseline final equity:   ${old_baseline_final:,.2f}")
    print(f"  Carry-off final equity:  ${old_carry_off_final:,.2f}")
    print(f"  Delta:                   ${old_delta:+,.2f} ({100*old_delta/old_baseline_final:+.2f}%)")
    print()

    # NEW results
    new_baseline_final = new["baseline"]["equity"]["equity"].iloc[-1]
    new_carry_off_final = new["carry_off"]["equity"]["equity"].iloc[-1]
    new_delta = new_carry_off_final - new_baseline_final

    print("AFTER FIX (rule_weights wired correctly):")
    print(f"  Baseline final equity:   ${new_baseline_final:,.2f}")
    print(f"  Carry-off final equity:  ${new_carry_off_final:,.2f}")
    print(f"  Delta:                   ${new_delta:+,.2f} ({100*new_delta/new_baseline_final:+.2f}%)")
    print()

    # Verification
    if abs(old_delta) < 100:
        print("✅ BEFORE FIX: Delta ≈ $0 (confirms bug existed)")
    else:
        print("⚠️  BEFORE FIX: Delta ≠ $0 (unexpected!)")

    if abs(new_delta) > 100:
        print("✅ AFTER FIX: Delta ≠ $0 (confirms fix works)")
    else:
        print("⚠️  AFTER FIX: Delta ≈ $0 (fix may not be working)")

    print()
    print("=" * 80)
    print("PART 2: CARRY FORECAST IMPACT ANALYSIS")
    print("=" * 80)
    print()

    # Position differences
    print("POSITION DIFFERENCES (Baseline vs Carry-Off):")
    print("-" * 80)
    pos_diffs = compare_positions(new["baseline"]["positions"], new["carry_off"]["positions"])

    for inst, metrics in pos_diffs.items():
        print(f"\n{inst}:")
        print(f"  Mean abs diff:      ${metrics['mean_abs_diff']:,.2f}")
        print(f"  Median abs diff:    ${metrics['median_abs_diff']:,.2f}")
        print(f"  Max abs diff:       ${metrics['max_abs_diff']:,.2f}")
        print(f"  Days different:     {metrics['days_different']:,} / 1,782 ({metrics['pct_days_different']:.1f}%)")

    print()
    print("=" * 80)
    print("PART 3: CARRY vs TREND AGREEMENT")
    print("=" * 80)
    print()

    carry_direction = analyze_carry_direction(new["baseline"]["diagnostics"])

    print(f"Carry forecast agrees with EWMAC trend: {carry_direction['agreement_pct']:.1f}% of days")
    print(f"Carry forecast opposes EWMAC trend:     {carry_direction['disagreement_pct']:.1f}% of days")
    print()

    if carry_direction["disagreement_pct"] > 40:
        print("⚠️  Carry frequently opposes trend (>40% of days)")
        print("   This explains why removing carry improves performance.")

    print()
    print("=" * 80)
    print("PART 4: PERFORMANCE SUMMARY")
    print("=" * 80)
    print()

    baseline_return = (new_baseline_final / 5000 - 1) * 100
    carry_off_return = (new_carry_off_final / 5000 - 1) * 100

    print(f"Baseline return (with carry):     {baseline_return:+.2f}%")
    print(f"Carry-off return (without carry): {carry_off_return:+.2f}%")
    print(f"Performance delta:                {carry_off_return - baseline_return:+.2f} pp")
    print()

    if new_delta > 0:
        print("📉 FINDING: Carry forecast HURTS performance in this period")
        print()
        print("Possible explanations:")
        print("1. Carry opposes trend at inopportune times (fighting momentum)")
        print("2. Funding rates are not predictive of future returns in crypto perps")
        print("3. Fast/slow EWMA params (3d, 30d) may be too short for crypto funding")
        print("4. Carry signal quality is poor (low information ratio)")
        print("5. Equal weighting (1/3) gives too much weight to low-quality carry signal")
    else:
        print("📈 FINDING: Carry forecast HELPS performance")
        print()
        print("Carry contributes positive value to the system.")

    print()

    # Generate plots
    print("Generating validation plots...")
    plot_comparison(new["baseline"]["equity"], new["carry_off"]["equity"], "out/stage1_baseline_fixed")
    print(f"Saved: out/stage1_baseline_fixed/carry_fix_validation.png")
    print()

    print("=" * 80)
    print("VALIDATION COMPLETE")
    print("=" * 80)

if __name__ == "__main__":
    main()
