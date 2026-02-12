#!/usr/bin/env python3
"""
Diagnostic script to investigate volatility targeting issue in dynamic universe.

Problem: Dynamic universe shows 2.08% realized vol vs 21.90% for static (both targeting 25%).
Hypothesis: EWMA weight smoothing fails to preserve normalization during universe expansion.

This script compares static vs dynamic systems at each pipeline stage to identify
where the dampening occurs.
"""

import pandas as pd
import numpy as np
import logging
from pathlib import Path
from systems.provided.crypto_example.crypto_system import (
    crypto_system,
    crypto_system_with_dynamic_universe,
)

# Suppress verbose debug logging
logging.getLogger().setLevel(logging.WARNING)


# Sample dates spanning universe growth: 50 → 400 instruments
SAMPLE_DATES = [
    "2018-06-01",  # ~50 instruments
    "2020-06-01",  # ~100 instruments
    "2022-06-01",  # ~200 instruments
    "2024-05-31",  # ~350 instruments (2024-06-01 doesn't exist)
    "2025-08-01",  # ~400 instruments
]

# Instruments to track in detail
CORE_INSTRUMENTS = ["BTC", "ETH", "ADA", "DOT", "SOL"]


def get_pipeline_diagnostics(system, system_type, sample_date, instruments=None):
    """
    Extract metrics at each pipeline stage for a given date.

    Pipeline: Forecast → Vol Scalar → Raw Weights → Smoothed Weights →
              Subsystem Position → Notional Position → Buffered Position

    Args:
        system: System object (static or dynamic)
        system_type: "static" or "dynamic"
        sample_date: Date to analyze
        instruments: List of instruments to track (None = all active)

    Returns:
        dict: Metrics for each pipeline stage
    """
    date = pd.Timestamp(sample_date)

    # Get all active instruments at this date if not specified
    if instruments is None:
        weights = system.portfolio.get_instrument_weights()
        active_at_date = weights.loc[date].dropna()
        instruments = active_at_date[active_at_date > 0].index.tolist()

    diagnostics = {
        "system_type": system_type,
        "date": sample_date,
        "num_instruments": len(instruments),
    }

    # Stage 1: Combined Forecast (should be similar between systems)
    try:
        forecasts = {}
        for inst in instruments:
            try:
                fc = system.combForecast.get_combined_forecast(inst)
                if date in fc.index:
                    forecasts[inst] = fc.loc[date]
            except:
                forecasts[inst] = np.nan

        diagnostics["forecast_mean"] = np.nanmean(list(forecasts.values()))
        diagnostics["forecast_std"] = np.nanstd(list(forecasts.values()))
        diagnostics["forecast_abs_mean"] = np.nanmean(np.abs(list(forecasts.values())))
    except Exception as e:
        print(f"  Warning: Could not get forecasts for {system_type}: {e}")
        diagnostics["forecast_mean"] = np.nan
        diagnostics["forecast_std"] = np.nan
        diagnostics["forecast_abs_mean"] = np.nan

    # Stage 2: Volatility Scalar (should be similar between systems)
    try:
        vol_scalars = {}
        for inst in instruments:
            try:
                vs = system.positionSize.get_volatility_scalar(inst)
                if date in vs.index:
                    vol_scalars[inst] = vs.loc[date]
            except:
                vol_scalars[inst] = np.nan

        diagnostics["vol_scalar_mean"] = np.nanmean(list(vol_scalars.values()))
        diagnostics["vol_scalar_std"] = np.nanstd(list(vol_scalars.values()))
    except Exception as e:
        print(f"  Warning: Could not get vol scalars for {system_type}: {e}")
        diagnostics["vol_scalar_mean"] = np.nan
        diagnostics["vol_scalar_std"] = np.nan

    # Stage 3: Raw Weights (before EWMA smoothing)
    # For dynamic system, check if raw weights sum to 1.0
    try:
        if system_type == "dynamic":
            # Access raw weights before EWMA
            raw_weights = system.portfolio.get_raw_fixed_instrument_weights()
            if date in raw_weights.index:
                raw_row = raw_weights.loc[date]
                diagnostics["raw_weights_sum"] = raw_row.sum()
                diagnostics["raw_weights_count_nonzero"] = (raw_row > 0).sum()
                diagnostics["raw_weights_max"] = raw_row.max()
        else:
            # Static system doesn't use EWMA
            diagnostics["raw_weights_sum"] = np.nan
            diagnostics["raw_weights_count_nonzero"] = np.nan
            diagnostics["raw_weights_max"] = np.nan
    except Exception as e:
        print(f"  Warning: Could not get raw weights for {system_type}: {e}")
        diagnostics["raw_weights_sum"] = np.nan
        diagnostics["raw_weights_count_nonzero"] = np.nan
        diagnostics["raw_weights_max"] = np.nan

    # Stage 4: Smoothed Weights (after EWMA, should sum to 1.0)
    try:
        weights = system.portfolio.get_instrument_weights()
        if date in weights.index:
            weight_row = weights.loc[date]
            diagnostics["smoothed_weights_sum"] = weight_row.sum()
            diagnostics["smoothed_weights_count_nonzero"] = (weight_row > 0).sum()
            diagnostics["smoothed_weights_max"] = weight_row.max()
            diagnostics["smoothed_weights_mean"] = weight_row[weight_row > 0].mean()
    except Exception as e:
        print(f"  Warning: Could not get weights for {system_type}: {e}")
        diagnostics["smoothed_weights_sum"] = np.nan
        diagnostics["smoothed_weights_count_nonzero"] = np.nan
        diagnostics["smoothed_weights_max"] = np.nan
        diagnostics["smoothed_weights_mean"] = np.nan

    # Stage 5: Subsystem Position (forecast × vol_scalar)
    try:
        subsystem_positions = {}
        for inst in instruments:
            try:
                pos = system.positionSize.get_subsystem_position(inst)
                if date in pos.index:
                    subsystem_positions[inst] = pos.loc[date]
            except:
                subsystem_positions[inst] = np.nan

        diagnostics["subsystem_pos_mean"] = np.nanmean(np.abs(list(subsystem_positions.values())))
        diagnostics["subsystem_pos_max"] = np.nanmax(np.abs(list(subsystem_positions.values())))
    except Exception as e:
        print(f"  Warning: Could not get subsystem positions for {system_type}: {e}")
        diagnostics["subsystem_pos_mean"] = np.nan
        diagnostics["subsystem_pos_max"] = np.nan

    # Stage 6: Notional Position (subsystem_pos × weight × IDM / 10)
    try:
        notional_positions = {}
        for inst in instruments:
            try:
                pos = system.portfolio.get_notional_position(inst)
                if date in pos.index:
                    notional_positions[inst] = pos.loc[date]
            except:
                notional_positions[inst] = np.nan

        diagnostics["notional_pos_mean"] = np.nanmean(np.abs(list(notional_positions.values())))
        diagnostics["notional_pos_max"] = np.nanmax(np.abs(list(notional_positions.values())))
        diagnostics["notional_pos_sum"] = np.nansum(np.abs(list(notional_positions.values())))
    except Exception as e:
        print(f"  Warning: Could not get notional positions for {system_type}: {e}")
        diagnostics["notional_pos_mean"] = np.nan
        diagnostics["notional_pos_max"] = np.nan
        diagnostics["notional_pos_sum"] = np.nan

    # Stage 7: Buffered Position (after 10% buffering)
    try:
        buffered_positions = {}
        for inst in instruments:
            try:
                pos = system.portfolio.get_buffered_position(inst)
                if date in pos.index:
                    buffered_positions[inst] = pos.loc[date]
            except:
                buffered_positions[inst] = np.nan

        diagnostics["buffered_pos_mean"] = np.nanmean(np.abs(list(buffered_positions.values())))
        diagnostics["buffered_pos_max"] = np.nanmax(np.abs(list(buffered_positions.values())))
        diagnostics["buffered_pos_sum"] = np.nansum(np.abs(list(buffered_positions.values())))
    except Exception as e:
        print(f"  Warning: Could not get buffered positions for {system_type}: {e}")
        diagnostics["buffered_pos_mean"] = np.nan
        diagnostics["buffered_pos_max"] = np.nan
        diagnostics["buffered_pos_sum"] = np.nan

    return diagnostics


def analyze_weight_evolution(dynamic_system):
    """
    Analyze how sum of weights evolves over time for dynamic system.

    Returns:
        pd.DataFrame: Daily sum of weights, count of instruments, max weight
    """
    print("\n=== ANALYZING WEIGHT EVOLUTION ===")
    print("Extracting weights for entire backtest period...")

    weights = dynamic_system.portfolio.get_instrument_weights()

    evolution = pd.DataFrame({
        'weight_sum': weights.sum(axis=1),
        'num_instruments': (weights > 0).sum(axis=1),
        'max_weight': weights.max(axis=1),
    })

    # Also get raw weights if available
    try:
        raw_weights = dynamic_system.portfolio.get_raw_fixed_instrument_weights()
        evolution['raw_weight_sum'] = raw_weights.sum(axis=1)
    except:
        print("  Could not extract raw weights (might not be cached)")

    return evolution


def calculate_dampening_ratios(static_diag, dynamic_diag):
    """
    Calculate dampening ratios between static and dynamic systems.

    Returns:
        dict: Ratios showing where dynamic is dampened vs static
    """
    ratios = {}

    # Compare forecasts (should be ~1.0 if similar)
    if not np.isnan(static_diag["forecast_abs_mean"]) and not np.isnan(dynamic_diag["forecast_abs_mean"]):
        ratios["forecast_ratio"] = dynamic_diag["forecast_abs_mean"] / static_diag["forecast_abs_mean"]

    # Compare vol scalars (should be ~1.0 if similar)
    if not np.isnan(static_diag["vol_scalar_mean"]) and not np.isnan(dynamic_diag["vol_scalar_mean"]):
        ratios["vol_scalar_ratio"] = dynamic_diag["vol_scalar_mean"] / static_diag["vol_scalar_mean"]

    # Compare subsystem positions (forecast × vol_scalar)
    if not np.isnan(static_diag["subsystem_pos_mean"]) and not np.isnan(dynamic_diag["subsystem_pos_mean"]):
        ratios["subsystem_pos_ratio"] = dynamic_diag["subsystem_pos_mean"] / static_diag["subsystem_pos_mean"]

    # Compare notional positions (subsystem × weight)
    if not np.isnan(static_diag["notional_pos_mean"]) and not np.isnan(dynamic_diag["notional_pos_mean"]):
        ratios["notional_pos_ratio"] = dynamic_diag["notional_pos_mean"] / static_diag["notional_pos_mean"]

    # Compare buffered positions
    if not np.isnan(static_diag["buffered_pos_mean"]) and not np.isnan(dynamic_diag["buffered_pos_mean"]):
        ratios["buffered_pos_ratio"] = dynamic_diag["buffered_pos_mean"] / static_diag["buffered_pos_mean"]

    # Key metric: weight sum for dynamic (should be ~1.0)
    ratios["dynamic_weight_sum"] = dynamic_diag["smoothed_weights_sum"]

    return ratios


def print_diagnostic_summary(static_diag, dynamic_diag, ratios):
    """Print formatted diagnostic summary for a single date."""
    date = static_diag["date"]

    print(f"\n{'='*80}")
    print(f"DATE: {date}")
    print(f"{'='*80}")

    print(f"\n{'Metric':<30} {'Static':>12} {'Dynamic':>12} {'Ratio':>12}")
    print("-" * 70)

    # Universe size
    print(f"{'Instruments':<30} {static_diag['num_instruments']:>12} {dynamic_diag['num_instruments']:>12} {'-':>12}")

    # Forecasts
    if not np.isnan(static_diag["forecast_abs_mean"]):
        print(f"{'Forecast (abs mean)':<30} {static_diag['forecast_abs_mean']:>12.2f} {dynamic_diag['forecast_abs_mean']:>12.2f} {ratios.get('forecast_ratio', np.nan):>12.3f}")

    # Vol scalars
    if not np.isnan(static_diag["vol_scalar_mean"]):
        print(f"{'Vol Scalar (mean)':<30} {static_diag['vol_scalar_mean']:>12.4f} {dynamic_diag['vol_scalar_mean']:>12.4f} {ratios.get('vol_scalar_ratio', np.nan):>12.3f}")

    # Raw weights (dynamic only)
    print(f"\n{'--- WEIGHTS (Dynamic) ---':<30}")
    if not np.isnan(dynamic_diag["raw_weights_sum"]):
        print(f"{'Raw Weights Sum':<30} {'-':>12} {dynamic_diag['raw_weights_sum']:>12.4f} {'-':>12}")
    print(f"{'Smoothed Weights Sum':<30} {'-':>12} {dynamic_diag['smoothed_weights_sum']:>12.4f} {'-':>12}")
    print(f"{'Smoothed Weights Max':<30} {'-':>12} {dynamic_diag['smoothed_weights_max']:>12.4f} {'-':>12}")

    # Positions
    print(f"\n{'--- POSITIONS ---':<30}")
    if not np.isnan(static_diag["subsystem_pos_mean"]):
        print(f"{'Subsystem Pos (mean)':<30} {static_diag['subsystem_pos_mean']:>12.2f} {dynamic_diag['subsystem_pos_mean']:>12.2f} {ratios.get('subsystem_pos_ratio', np.nan):>12.3f}")

    if not np.isnan(static_diag["notional_pos_mean"]):
        print(f"{'Notional Pos (mean)':<30} {static_diag['notional_pos_mean']:>12.2f} {dynamic_diag['notional_pos_mean']:>12.2f} {ratios.get('notional_pos_ratio', np.nan):>12.3f}")

    if not np.isnan(static_diag["buffered_pos_mean"]):
        print(f"{'Buffered Pos (mean)':<30} {static_diag['buffered_pos_mean']:>12.2f} {dynamic_diag['buffered_pos_mean']:>12.2f} {ratios.get('buffered_pos_ratio', np.nan):>12.3f}")

    # Diagnosis
    print(f"\n{'--- DIAGNOSIS ---':<30}")
    weight_sum = dynamic_diag["smoothed_weights_sum"]
    if weight_sum < 0.95:
        print(f"⚠️  ISSUE DETECTED: Weight sum = {weight_sum:.4f} (should be ~1.0)")
        print(f"    → Systematic under-allocation of capital")
        print(f"    → Expected vol impact: {weight_sum:.2%} of target (explains {weight_sum*25:.1f}% vs 25% target)")
    else:
        print(f"✓  Weight sum = {weight_sum:.4f} (normal)")


def main():
    """Run full diagnostic analysis."""
    print("="*80)
    print("VOLATILITY TARGETING DIAGNOSTIC")
    print("="*80)
    print("\nProblem: Dynamic universe shows 2.08% realized vol vs 21.90% for static")
    print("Hypothesis: EWMA weight smoothing fails to preserve normalization\n")

    # Create systems
    print("Loading systems (this may take a few minutes)...")
    static_system = crypto_system(data_path="data/crypto")
    dynamic_system = crypto_system_with_dynamic_universe(data_path="data/crypto")
    print("✓ Systems loaded\n")

    # Collect diagnostics for each sample date
    all_diagnostics = []

    for sample_date in SAMPLE_DATES:
        print(f"\nProcessing {sample_date}...")

        # Get diagnostics for both systems
        static_diag = get_pipeline_diagnostics(static_system, "static", sample_date)
        dynamic_diag = get_pipeline_diagnostics(dynamic_system, "dynamic", sample_date)

        # Calculate ratios
        ratios = calculate_dampening_ratios(static_diag, dynamic_diag)

        # Print summary
        print_diagnostic_summary(static_diag, dynamic_diag, ratios)

        # Store for CSV export
        all_diagnostics.append({**static_diag, **{f"dynamic_{k}": v for k, v in dynamic_diag.items()}})

    # Analyze weight evolution over time
    weight_evolution = analyze_weight_evolution(dynamic_system)

    # Print summary statistics
    print(f"\n{'='*80}")
    print("WEIGHT EVOLUTION SUMMARY")
    print(f"{'='*80}\n")
    print(weight_evolution.describe())

    # Check for systematic under-allocation
    print(f"\n{'='*80}")
    print("ROOT CAUSE ANALYSIS")
    print(f"{'='*80}\n")

    avg_weight_sum = weight_evolution['weight_sum'].mean()
    recent_weight_sum = weight_evolution['weight_sum'].iloc[-252:].mean()  # Last year

    print(f"Average weight sum (full period): {avg_weight_sum:.4f}")
    print(f"Average weight sum (last year):   {recent_weight_sum:.4f}")

    if avg_weight_sum < 0.95:
        print(f"\n⚠️  ROOT CAUSE CONFIRMED: EWMA smoothing breaks normalization")
        print(f"    → Average capital allocation: {avg_weight_sum:.1%}")
        print(f"    → Expected realized vol: {avg_weight_sum * 25:.1f}% (vs 25% target)")
        print(f"    → This explains the 2.08% vs 21.90% discrepancy")
        print(f"\nRECOMMENDED FIX: Renormalize weights after EWMA smoothing")
    else:
        print(f"\n✓  Hypothesis REJECTED: Weight sum is normal ({avg_weight_sum:.4f})")
        print(f"    → Need to investigate other pipeline stages")

    # Export to CSV
    output_dir = Path("systems/provided/crypto_example")

    diagnostics_df = pd.DataFrame(all_diagnostics)
    diagnostics_df.to_csv(output_dir / "diagnostic_results.csv", index=False)
    print(f"\n✓ Saved: {output_dir / 'diagnostic_results.csv'}")

    weight_evolution.to_csv(output_dir / "weight_evolution.csv")
    print(f"✓ Saved: {output_dir / 'weight_evolution.csv'}")

    # Also create a comparison CSV for key instruments
    print(f"\n{'='*80}")
    print("INSTRUMENT-LEVEL COMPARISON")
    print(f"{'='*80}\n")

    # Get final date positions for core instruments
    final_date = SAMPLE_DATES[-1]
    print(f"Comparing positions for core instruments on {final_date}:\n")

    comparison_data = []
    for inst in CORE_INSTRUMENTS:
        try:
            # Static
            static_weight = static_system.portfolio.get_instrument_weights()[inst].loc[final_date]
            static_notional = static_system.portfolio.get_notional_position(inst).loc[final_date]

            # Dynamic
            dynamic_weight = dynamic_system.portfolio.get_instrument_weights()[inst].loc[final_date]
            dynamic_notional = dynamic_system.portfolio.get_notional_position(inst).loc[final_date]

            comparison_data.append({
                'instrument': inst,
                'static_weight': static_weight,
                'dynamic_weight': dynamic_weight,
                'static_notional': static_notional,
                'dynamic_notional': dynamic_notional,
                'weight_ratio': dynamic_weight / static_weight if static_weight != 0 else np.nan,
                'position_ratio': dynamic_notional / static_notional if static_notional != 0 else np.nan,
            })

            print(f"{inst:6s}  Static weight: {static_weight:6.2%}  Dynamic weight: {dynamic_weight:6.2%}  Ratio: {dynamic_weight/static_weight if static_weight != 0 else np.nan:6.3f}")
        except Exception as e:
            print(f"{inst:6s}  Error: {e}")

    comparison_df = pd.DataFrame(comparison_data)
    comparison_df.to_csv(output_dir / "position_comparison.csv", index=False)
    print(f"\n✓ Saved: {output_dir / 'position_comparison.csv'}")

    # Get account curves for final validation
    print(f"\n{'='*80}")
    print("REALIZED VOLATILITY CHECK")
    print(f"{'='*80}\n")

    account_static = static_system.accounts.portfolio()
    account_dynamic = dynamic_system.accounts.portfolio()

    print(f"Static realized vol:  {account_static.ann_std():.2f}%")
    print(f"Dynamic realized vol: {account_dynamic.ann_std():.2f}%")
    print(f"Target vol:           25.00%")
    print(f"\nDynamic/Static ratio: {account_dynamic.ann_std() / account_static.ann_std():.3f}")

    print(f"\n{'='*80}")
    print("DIAGNOSTIC COMPLETE")
    print(f"{'='*80}\n")


if __name__ == "__main__":
    main()
