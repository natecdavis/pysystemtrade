#!/usr/bin/env python3
"""
Verify Forecast Scaling Pipeline

Tests whether individual forecasts are being scaled to target_abs=10 before combination.

This verifies the scaling pipeline is working as designed (per-rule scaling BEFORE combination).
"""

import pandas as pd
import numpy as np
from pathlib import Path

# Import the forecast scaling functions
from systems.crypto_perps.forecasts import scale_and_cap_forecast, scale_and_combine_forecasts

# Load the baseline backtest data to get raw forecasts
from sysdata.crypto.prices import load_crypto_perps_panel
from systems.crypto_perps.rules.ewmac import ewmac_forecasts
from systems.crypto_perps.rules.carry_funding import funding_carry_forecasts

print("=== Forecast Scaling Pipeline Verification ===\n")

# Load data
data_path = Path("data/example_crypto_perps_5yr.parquet")
prices_df, meta_df = load_crypto_perps_panel(data_path)
print(f"Loaded data: {len(prices_df)} days, {len(prices_df.columns)} instruments\n")

# Calculate raw forecasts (same as in system.py)
print("Calculating raw forecasts...")
ewmac_pairs = [[8, 32], [16, 64]]
ewmac = ewmac_forecasts(prices_df, ewmac_pairs)
carry = funding_carry_forecasts(meta_df, fast_halflife=3, slow_halflife=30)

# Focus on one instrument
inst = "BTCUSDT_PERP"
print(f"\nAnalyzing: {inst}")
print("-" * 80)

# Raw forecasts
raw_ewmac_8_32 = ewmac[inst]['ewmac_8_32']
raw_ewmac_16_64 = ewmac[inst]['ewmac_16_64']
raw_carry = carry[inst]

print("\n1. RAW FORECASTS (before scaling):")
print(f"   EWMAC 8-32:    mean |value| = {raw_ewmac_8_32.abs().mean():.3f}, max = {raw_ewmac_8_32.abs().max():.3f}")
print(f"   EWMAC 16-64:   mean |value| = {raw_ewmac_16_64.abs().mean():.3f}, max = {raw_ewmac_16_64.abs().max():.3f}")
print(f"   Carry:         mean |value| = {raw_carry.abs().mean():.6f}, max = {raw_carry.abs().max():.6f}")

# Apply individual scaling
print("\n2. SCALED FORECASTS (after scale_and_cap_forecast):")
scaled_ewmac_8_32 = scale_and_cap_forecast(raw_ewmac_8_32, target_abs=10.0, cap=20.0)
scaled_ewmac_16_64 = scale_and_cap_forecast(raw_ewmac_16_64, target_abs=10.0, cap=20.0)
scaled_carry = scale_and_cap_forecast(raw_carry, target_abs=10.0, cap=20.0)

print(f"   EWMAC 8-32:    mean |value| = {scaled_ewmac_8_32.abs().mean():.3f}, max = {scaled_ewmac_8_32.abs().max():.3f}")
print(f"   EWMAC 16-64:   mean |value| = {scaled_ewmac_16_64.abs().mean():.3f}, max = {scaled_ewmac_16_64.abs().max():.3f}")
print(f"   Carry:         mean |value| = {scaled_carry.abs().mean():.3f}, max = {scaled_carry.abs().max():.3f}")

# Check if carry is hitting the cap
carry_capped_pct = (scaled_carry.abs() >= 19.9).sum() / len(scaled_carry) * 100
print(f"\n   Carry forecasts hitting cap (≥19.9): {carry_capped_pct:.1f}%")

# Sample a specific date to trace through
sample_date = pd.Timestamp('2020-03-01')
print(f"\n3. SAMPLE DATE: {sample_date.date()}")
print("-" * 80)
print("Raw forecasts:")
print(f"   EWMAC 8-32:  {raw_ewmac_8_32.loc[sample_date]:+.6f}")
print(f"   EWMAC 16-64: {raw_ewmac_16_64.loc[sample_date]:+.6f}")
print(f"   Carry:       {raw_carry.loc[sample_date]:+.6f}")

print("\nScaled forecasts (target_abs=10, cap=±20):")
print(f"   EWMAC 8-32:  {scaled_ewmac_8_32.loc[sample_date]:+.6f}")
print(f"   EWMAC 16-64: {scaled_ewmac_16_64.loc[sample_date]:+.6f}")
print(f"   Carry:       {scaled_carry.loc[sample_date]:+.6f}")

# Combine using scale_and_combine_forecasts (what system.py does)
raw_forecasts = {
    'ewmac_8_32': raw_ewmac_8_32,
    'ewmac_16_64': raw_ewmac_16_64,
    'carry_funding': raw_carry
}

combined = scale_and_combine_forecasts(raw_forecasts, weights=None)  # Equal weights

print(f"\nCombined forecast (equal weights + FDM): {combined.loc[sample_date]:+.6f}")

# Manual combination to understand the math
print("\n4. MANUAL COMBINATION (verifying the pipeline):")
print("-" * 80)

# Equal weights = 1/3 each
w = 1/3

# Weighted average of SCALED forecasts (before FDM)
weighted_avg = (
    scaled_ewmac_8_32.loc[sample_date] * w +
    scaled_ewmac_16_64.loc[sample_date] * w +
    scaled_carry.loc[sample_date] * w
)
print(f"Weighted avg (no FDM): {weighted_avg:+.6f}")

# The difference is FDM boost
fdm_estimate = combined.loc[sample_date] / weighted_avg if weighted_avg != 0 else 0
print(f"Implied FDM boost:     {fdm_estimate:.3f}")

# Contribution analysis
print("\n5. CONTRIBUTION TO COMBINED FORECAST:")
print("-" * 80)
ewmac_8_32_contrib = scaled_ewmac_8_32.loc[sample_date] * w
ewmac_16_64_contrib = scaled_ewmac_16_64.loc[sample_date] * w
carry_contrib = scaled_carry.loc[sample_date] * w

total_contrib = ewmac_8_32_contrib + ewmac_16_64_contrib + carry_contrib
print(f"EWMAC 8-32:  {ewmac_8_32_contrib:+.6f}  ({100*abs(ewmac_8_32_contrib)/abs(total_contrib):.1f}% of |total|)")
print(f"EWMAC 16-64: {ewmac_16_64_contrib:+.6f}  ({100*abs(ewmac_16_64_contrib)/abs(total_contrib):.1f}% of |total|)")
print(f"Carry:       {carry_contrib:+.6f}  ({100*abs(carry_contrib)/abs(total_contrib):.1f}% of |total|)")
print(f"Total (before FDM): {total_contrib:+.6f}")
print(f"After FDM:          {combined.loc[sample_date]:+.6f}")

# Statistical analysis over full dataset
print("\n6. CARRY CONTRIBUTION OVER FULL DATASET:")
print("-" * 80)

# Align dates
common_dates = scaled_ewmac_8_32.index.intersection(scaled_ewmac_16_64.index).intersection(scaled_carry.index)
ewmac_8_32_aligned = scaled_ewmac_8_32.loc[common_dates]
ewmac_16_64_aligned = scaled_ewmac_16_64.loc[common_dates]
carry_aligned = scaled_carry.loc[common_dates]

# Weighted contributions (before FDM)
ewmac_8_32_contribs = ewmac_8_32_aligned * w
ewmac_16_64_contribs = ewmac_16_64_aligned * w
carry_contribs = carry_aligned * w
total_contribs = ewmac_8_32_contribs + ewmac_16_64_contribs + carry_contribs

# Fraction of |combined| from |carry|
carry_fraction = carry_contribs.abs() / (total_contribs.abs() + 1e-9)

print(f"Carry contribution to weighted avg (before FDM):")
print(f"   Mean:   {100*carry_fraction.mean():.2f}%")
print(f"   Median: {100*carry_fraction.median():.2f}%")
print(f"   P90:    {100*carry_fraction.quantile(0.90):.2f}%")
print(f"   Max:    {100*carry_fraction.max():.2f}%")

# Check if carry ever dominates
carry_dominates = (carry_contribs.abs() > ewmac_8_32_contribs.abs()) & (carry_contribs.abs() > ewmac_16_64_contribs.abs())
print(f"\nDays where |carry| > both |EWMAC|: {carry_dominates.sum()} / {len(carry_dominates)} ({100*carry_dominates.mean():.2f}%)")

print("\n" + "=" * 80)
print("CONCLUSION:")
print("=" * 80)

if scaled_carry.abs().mean() < 1.0:
    print("❌ PROBLEM: Carry is NOT being scaled to target_abs=10")
    print(f"   Scaled carry mean |value| = {scaled_carry.abs().mean():.3f} (expected ~10)")
    print("   → forecast_scalar() may be failing to estimate scaling factor")
    print("   → Likely cause: carry variance too low, scalar estimation unstable")
elif carry_fraction.median() < 0.10:
    print("⚠️  PROBLEM: Carry IS scaled to ~10, but contributes <10% to combined")
    print(f"   Median carry contribution: {100*carry_fraction.median():.1f}%")
    print("   → This is EXPECTED if carry variance is low (sticky funding rates)")
    print("   → After scaling, high-variance EWMAC still dominates low-variance carry")
else:
    print("✅ Carry is contributing materially to combined forecast")
    print(f"   Median carry contribution: {100*carry_fraction.median():.1f}%")

print()
