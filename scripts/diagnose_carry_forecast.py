#!/usr/bin/env python3
"""
Carry Forecast Diagnostic Script

Diagnoses why carry-off == baseline by analyzing forecast-level data.

Answers: "Does the carry forecast ever materially affect the combined forecast or positions?"

Usage:
    python scripts/diagnose_carry_forecast.py --diagnostics out/stage1_baseline/diagnostics.parquet
"""

import argparse
from pathlib import Path
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt


def load_diagnostics(diagnostics_path: Path) -> pd.DataFrame:
    """Load diagnostics parquet file."""
    df = pd.read_parquet(diagnostics_path)
    # Filter out NaN forecasts (warmup period)
    df = df[df["forecast_combined"].notna()].copy()
    return df


def compute_trend_forecast(df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute trend-only forecast (EWMAC combination without carry).

    Assumes equal weighting for EWMAC forecasts (Phase 1 default).
    """
    df = df.copy()

    # Trend-only: average of EWMAC forecasts
    df["trend_forecast_raw"] = (df["forecast_ewmac_8_32"] + df["forecast_ewmac_16_64"]) / 2.0

    return df


def analyze_carry_contribution(df: pd.DataFrame) -> dict:
    """
    Analyze carry forecast contribution to combined forecast.

    Returns dict with diagnostic metrics.
    """
    # Compute trend-only forecast
    df = compute_trend_forecast(df)

    # Compute carry contribution metrics
    diagnostics = {}

    # 1. Distribution of |carry| / |combined|
    df["carry_abs"] = df["forecast_carry_funding"].abs()
    df["combined_abs"] = df["forecast_combined"].abs()
    df["carry_fraction"] = df["carry_abs"] / (df["combined_abs"] + 1e-9)  # Avoid div by zero

    diagnostics["carry_fraction"] = {
        "mean": df["carry_fraction"].mean(),
        "median": df["carry_fraction"].median(),
        "p90": df["carry_fraction"].quantile(0.90),
        "p99": df["carry_fraction"].quantile(0.99),
        "max": df["carry_fraction"].max(),
    }

    # 2. Count of days where carry changes sign of combined forecast
    # Sign without carry (trend-only)
    df["trend_sign"] = np.sign(df["trend_forecast_raw"])
    df["combined_sign"] = np.sign(df["forecast_combined"])
    df["carry_flips_sign"] = (df["trend_sign"] != df["combined_sign"]) & (df["trend_sign"] != 0)

    diagnostics["sign_flips"] = {
        "count": df["carry_flips_sign"].sum(),
        "pct": 100 * df["carry_flips_sign"].sum() / len(df),
    }

    # 3. Counterfactual: what would combined forecast be without carry?
    # Combined = (w1*ewmac1 + w2*ewmac2 + w3*carry) / (w1 + w2 + w3) * FDM
    # For equal weights: combined ≈ (ewmac1 + ewmac2 + carry) / 3 * FDM
    # Without carry: combined_no_carry ≈ (ewmac1 + ewmac2) / 2 * FDM
    #
    # But we don't have FDM (forecast diversification multiplier) directly.
    # Instead, estimate by comparing actual combined to trend_forecast_raw.
    #
    # Better approach: Use the fact that combined should equal weighted average scaled by FDM.
    # Since we observe combined and individual forecasts, we can compute contribution.

    # Simplified: Assume equal weights (1/3 each), then combined ≈ (ewmac1 + ewmac2 + carry)/3 * scalar
    # The scalar is the FDM + any other scaling.
    # We can estimate what combined would be without carry by scaling trend_forecast_raw to match.

    # Compute scaling factor from trend to combined (when carry ≈ 0)
    # Use median scaling to avoid outliers
    df["trend_to_combined_ratio"] = df["forecast_combined"] / (df["trend_forecast_raw"] + 1e-9)
    median_scaling = df["trend_to_combined_ratio"].median()

    # Estimate combined forecast without carry
    df["combined_no_carry_est"] = df["trend_forecast_raw"] * median_scaling

    # Compute position impact (assuming positions ~ forecast)
    df["position_delta_pct"] = 100 * abs(df["forecast_combined"] - df["combined_no_carry_est"]) / (abs(df["combined_no_carry_est"]) + 1e-9)

    # Count days where carry changes position by >5%
    diagnostics["position_impact"] = {
        "delta_gt_5pct_count": (df["position_delta_pct"] > 5).sum(),
        "delta_gt_5pct_pct": 100 * (df["position_delta_pct"] > 5).sum() / len(df),
        "delta_gt_1pct_count": (df["position_delta_pct"] > 1).sum(),
        "delta_gt_1pct_pct": 100 * (df["position_delta_pct"] > 1).sum() / len(df),
        "delta_mean": df["position_delta_pct"].mean(),
        "delta_median": df["position_delta_pct"].median(),
        "delta_p90": df["position_delta_pct"].quantile(0.90),
    }

    # 4. Raw forecast magnitude comparison
    diagnostics["forecast_magnitudes"] = {
        "ewmac_8_32": {
            "mean": df["forecast_ewmac_8_32"].abs().mean(),
            "std": df["forecast_ewmac_8_32"].std(),
            "median": df["forecast_ewmac_8_32"].abs().median(),
        },
        "ewmac_16_64": {
            "mean": df["forecast_ewmac_16_64"].abs().mean(),
            "std": df["forecast_ewmac_16_64"].std(),
            "median": df["forecast_ewmac_16_64"].abs().median(),
        },
        "carry_funding": {
            "mean": df["forecast_carry_funding"].abs().mean(),
            "std": df["forecast_carry_funding"].std(),
            "median": df["forecast_carry_funding"].abs().median(),
        },
        "combined": {
            "mean": df["forecast_combined"].abs().mean(),
            "std": df["forecast_combined"].std(),
            "median": df["forecast_combined"].abs().median(),
        },
    }

    # 5. Scaling ratio: carry vs trend
    diagnostics["scaling_ratios"] = {
        "carry_to_ewmac_8_32": df["forecast_carry_funding"].abs().mean() / (df["forecast_ewmac_8_32"].abs().mean() + 1e-9),
        "carry_to_ewmac_16_64": df["forecast_carry_funding"].abs().mean() / (df["forecast_ewmac_16_64"].abs().mean() + 1e-9),
        "carry_to_trend_avg": df["forecast_carry_funding"].abs().mean() / ((df["forecast_ewmac_8_32"].abs().mean() + df["forecast_ewmac_16_64"].abs().mean()) / 2 + 1e-9),
    }

    return diagnostics, df


def plot_forecast_distributions(df: pd.DataFrame, output_dir: Path):
    """Plot forecast distributions for visual comparison."""
    fig, axes = plt.subplots(2, 2, figsize=(14, 10))

    # Plot 1: Forecast magnitude comparison (histograms)
    ax = axes[0, 0]
    ax.hist(df["forecast_ewmac_8_32"], bins=50, alpha=0.5, label="EWMAC 8-32", density=True)
    ax.hist(df["forecast_ewmac_16_64"], bins=50, alpha=0.5, label="EWMAC 16-64", density=True)
    ax.hist(df["forecast_carry_funding"], bins=50, alpha=0.5, label="Carry Funding", density=True)
    ax.set_xlabel("Forecast Value")
    ax.set_ylabel("Density")
    ax.set_title("Raw Forecast Distributions")
    ax.legend()
    ax.grid(True, alpha=0.3)

    # Plot 2: Carry vs Combined (scatter)
    ax = axes[0, 1]
    ax.scatter(df["forecast_combined"], df["forecast_carry_funding"], alpha=0.3, s=1)
    ax.set_xlabel("Combined Forecast")
    ax.set_ylabel("Carry Forecast")
    ax.set_title("Carry vs Combined Forecast")
    ax.grid(True, alpha=0.3)

    # Plot 3: Carry contribution fraction (histogram)
    ax = axes[1, 0]
    carry_frac_pct = df["carry_fraction"] * 100
    ax.hist(carry_frac_pct[carry_frac_pct < 10], bins=50, edgecolor="black", alpha=0.7)
    ax.set_xlabel("Carry Contribution (% of |Combined|)")
    ax.set_ylabel("Frequency")
    ax.set_title("Distribution of Carry Contribution (capped at 10%)")
    ax.grid(True, alpha=0.3, axis="y")

    # Plot 4: Time series of forecasts (sample instrument)
    ax = axes[1, 1]
    sample_inst = "BTCUSDT_PERP"
    sample_df = df[df["instrument"] == sample_inst].sort_values("date").iloc[:365]  # First year
    ax.plot(sample_df["date"], sample_df["forecast_ewmac_8_32"], label="EWMAC 8-32", alpha=0.7, linewidth=1)
    ax.plot(sample_df["date"], sample_df["forecast_ewmac_16_64"], label="EWMAC 16-64", alpha=0.7, linewidth=1)
    ax.plot(sample_df["date"], sample_df["forecast_carry_funding"] * 1000, label="Carry × 1000", alpha=0.7, linewidth=1)
    ax.plot(sample_df["date"], sample_df["forecast_combined"], label="Combined", color="black", linewidth=1.5)
    ax.set_xlabel("Date")
    ax.set_ylabel("Forecast Value")
    ax.set_title(f"Forecast Time Series ({sample_inst}, Year 1)")
    ax.legend()
    ax.grid(True, alpha=0.3)

    plt.tight_layout()
    plt.savefig(output_dir / "forecast_distributions.png", dpi=150)
    plt.close()


def print_diagnostics_report(diagnostics: dict):
    """Print formatted diagnostics report."""
    print("=" * 80)
    print("CARRY FORECAST DIAGNOSTIC REPORT")
    print("=" * 80)
    print()

    # 1. Forecast magnitude comparison
    print("1. RAW FORECAST MAGNITUDES")
    print("-" * 80)
    mags = diagnostics["forecast_magnitudes"]
    print(f"{'Forecast':<20} {'Mean |value|':<15} {'Std Dev':<15} {'Median |value|':<15}")
    print("-" * 80)
    for name, stats in mags.items():
        print(f"{name:<20} {stats['mean']:>14.6f} {stats['std']:>14.6f} {stats['median']:>14.6f}")
    print()

    # 2. Scaling ratios
    print("2. SCALING RATIOS (Carry / Trend)")
    print("-" * 80)
    ratios = diagnostics["scaling_ratios"]
    print(f"Carry / EWMAC 8-32:     {ratios['carry_to_ewmac_8_32']:.6f}  (1:{1/ratios['carry_to_ewmac_8_32']:.0f})")
    print(f"Carry / EWMAC 16-64:    {ratios['carry_to_ewmac_16_64']:.6f}  (1:{1/ratios['carry_to_ewmac_16_64']:.0f})")
    print(f"Carry / Trend Average:  {ratios['carry_to_trend_avg']:.6f}  (1:{1/ratios['carry_to_trend_avg']:.0f})")
    print()

    # 3. Carry contribution to combined forecast
    print("3. CARRY CONTRIBUTION TO COMBINED FORECAST")
    print("-" * 80)
    carry_frac = diagnostics["carry_fraction"]
    print(f"Mean |carry| / |combined|:    {carry_frac['mean']:.4f}  ({carry_frac['mean']*100:.2f}%)")
    print(f"Median |carry| / |combined|:  {carry_frac['median']:.4f}  ({carry_frac['median']*100:.2f}%)")
    print(f"P90 |carry| / |combined|:     {carry_frac['p90']:.4f}  ({carry_frac['p90']*100:.2f}%)")
    print(f"P99 |carry| / |combined|:     {carry_frac['p99']:.4f}  ({carry_frac['p99']*100:.2f}%)")
    print(f"Max |carry| / |combined|:     {carry_frac['max']:.4f}  ({carry_frac['max']*100:.2f}%)")
    print()

    # 4. Sign flips
    print("4. FORECAST SIGN FLIPS (Carry Changes Sign of Combined)")
    print("-" * 80)
    sign_flips = diagnostics["sign_flips"]
    print(f"Days where carry flips sign:  {sign_flips['count']:,} / 7,128  ({sign_flips['pct']:.2f}%)")
    print()

    # 5. Position impact
    print("5. POSITION IMPACT (% Change in Position Size)")
    print("-" * 80)
    pos_impact = diagnostics["position_impact"]
    print(f"Days where carry changes position by >5%:   {pos_impact['delta_gt_5pct_count']:,}  ({pos_impact['delta_gt_5pct_pct']:.2f}%)")
    print(f"Days where carry changes position by >1%:   {pos_impact['delta_gt_1pct_count']:,}  ({pos_impact['delta_gt_1pct_pct']:.2f}%)")
    print(f"Mean position delta:                        {pos_impact['delta_mean']:.2f}%")
    print(f"Median position delta:                      {pos_impact['delta_median']:.2f}%")
    print(f"P90 position delta:                         {pos_impact['delta_p90']:.2f}%")
    print()

    # Summary
    print("=" * 80)
    print("SUMMARY")
    print("=" * 80)
    print()

    # Diagnosis
    if ratios['carry_to_trend_avg'] < 0.01:
        print("❌ DIAGNOSIS: CARRY FORECAST IS SEVERELY UNDER-SCALED")
        print()
        print(f"   Carry forecast magnitude is ~{1/ratios['carry_to_trend_avg']:.0f}x SMALLER than trend forecasts.")
        print(f"   Mean |carry|: {mags['carry_funding']['mean']:.6f}")
        print(f"   Mean |trend|: {(mags['ewmac_8_32']['mean'] + mags['ewmac_16_64']['mean']) / 2:.6f}")
        print()
        print("   This explains why carry-off == baseline:")
        print("   - Even with equal weights, carry contributes ~0% to combined forecast")
        print(f"   - Carry changes combined forecast by <{carry_frac['median']*100:.2f}% (median)")
        print(f"   - Carry changes position size by <{pos_impact['delta_median']:.2f}% (median)")
        print()
        print("   ROOT CAUSE: Carry forecast is not being normalized to match trend forecast scale.")
        print()
    elif carry_frac['median'] < 0.05:
        print("⚠️  DIAGNOSIS: CARRY CONTRIBUTION IS MINIMAL BUT PRESENT")
        print()
        print(f"   Carry contributes ~{carry_frac['median']*100:.1f}% to combined forecast (median).")
        print("   This could be due to:")
        print("   - Low funding rate volatility in this period")
        print("   - Incorrect scaling/normalization")
        print("   - Carry signal genuinely weak compared to trend")
        print()
    else:
        print("✅ DIAGNOSIS: CARRY FORECAST IS CONTRIBUTING MATERIALLY")
        print()
        print(f"   Carry contributes ~{carry_frac['median']*100:.1f}% to combined forecast (median).")
        print(f"   Position impact: ~{pos_impact['delta_median']:.1f}% (median)")
        print()

    print("=" * 80)


def investigate_carry_calculation(diagnostics_df: pd.DataFrame):
    """
    Investigate carry forecast calculation by examining raw data.
    """
    print()
    print("6. RAW CARRY FORECAST INVESTIGATION")
    print("-" * 80)

    # Sample a few days to inspect
    sample = diagnostics_df[diagnostics_df["instrument"] == "BTCUSDT_PERP"].head(20)

    print("\nSample carry forecast values (BTCUSDT_PERP, first 20 days after warmup):")
    print(sample[["date", "forecast_carry_funding"]].to_string(index=False))
    print()

    # Check if carry is always near zero
    all_carry = diagnostics_df["forecast_carry_funding"]
    print(f"Carry forecast range: [{all_carry.min():.6f}, {all_carry.max():.6f}]")
    print(f"Carry forecast > 0.001: {(all_carry > 0.001).sum()} / {len(all_carry)}")
    print(f"Carry forecast < -0.001: {(all_carry < -0.001).sum()} / {len(all_carry)}")
    print()


def main():
    parser = argparse.ArgumentParser(
        description='Diagnose carry forecast contribution'
    )
    parser.add_argument(
        '--diagnostics',
        default='out/stage1_baseline/diagnostics.parquet',
        help='Path to diagnostics parquet file'
    )
    parser.add_argument(
        '--output-dir',
        default='out/stage1_baseline',
        help='Output directory for plots'
    )

    args = parser.parse_args()

    diagnostics_path = Path(args.diagnostics)
    output_dir = Path(args.output_dir)

    if not diagnostics_path.exists():
        print(f"ERROR: Diagnostics file not found: {diagnostics_path}")
        return 1

    # Load data
    print(f"Loading diagnostics from {diagnostics_path}...")
    df = load_diagnostics(diagnostics_path)
    print(f"Loaded {len(df):,} rows ({len(df['instrument'].unique())} instruments)")
    print()

    # Analyze carry contribution
    print("Analyzing carry forecast contribution...")
    diagnostics, diagnostics_df = analyze_carry_contribution(df)

    # Print report
    print_diagnostics_report(diagnostics)

    # Additional investigation
    investigate_carry_calculation(diagnostics_df)

    # Plot distributions
    print("Generating forecast distribution plots...")
    plot_forecast_distributions(diagnostics_df, output_dir)
    print(f"Saved: {output_dir / 'forecast_distributions.png'}")
    print()

    return 0


if __name__ == "__main__":
    exit(main())
