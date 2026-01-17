"""
Comprehensive weight pipeline diagnostic with invariant validation.

Purpose: Prove WHERE and WHY weight concentration occurs before implementing fixes.
"""
import pandas as pd
import numpy as np
from systems.provided.crypto_example.crypto_system import crypto_system_with_dynamic_universe

def check_invariants(weights_df, stage_name):
    """Check weight invariants and return summary."""
    num_active = (weights_df > 0).sum(axis=1)
    sum_weights = weights_df.sum(axis=1)
    max_weight = weights_df.max(axis=1)
    weights_squared = (weights_df ** 2).sum(axis=1)
    N_eff = 1.0 / weights_squared  # Effective universe size

    # Check for sum violations (should be ~1.0 when N_active > 0)
    sum_violations = sum_weights[(num_active > 0) & (abs(sum_weights - 1.0) > 0.001)]

    # Check for epsilon weights (numerical artifacts)
    epsilon_weights = ((weights_df > 0) & (weights_df < 1e-6)).sum().sum()

    summary = {
        'stage': stage_name,
        'num_active': num_active,
        'sum_weights': sum_weights,
        'max_weight': max_weight,
        'N_eff': N_eff,
        'sum_violations': len(sum_violations),
        'epsilon_weights': epsilon_weights,
    }

    print(f"\n{'='*70}")
    print(f"{stage_name}")
    print(f"{'='*70}")
    print(f"  Shape: {weights_df.shape}")
    print(f"  N_active: min={num_active.min():.0f}, max={num_active.max():.0f}, avg={num_active.mean():.1f}")
    print(f"  Sum: min={sum_weights.min():.4f}, max={sum_weights.max():.4f}, avg={sum_weights.mean():.4f}")
    print(f"  Max weight: min={max_weight.min():.4f}, max={max_weight.max():.4f}, avg={max_weight.mean():.4f}")
    print(f"  N_effective (1/sum(w^2)): avg={N_eff.mean():.1f}")
    print(f"  Expected max (1/N_active): avg={1.0/num_active.mean():.4f}")

    if len(sum_violations) > 0:
        print(f"\n  ⚠️ WARNING: {len(sum_violations)} days with sum != 1.0")
        print(f"     Sample violations: {sum_violations.head().to_dict()}")
    else:
        print(f"\n  ✓ Sum invariant satisfied (all days sum ≈ 1.0)")

    if epsilon_weights > 0:
        print(f"  ⚠️ WARNING: {epsilon_weights} epsilon weights (0 < w < 1e-6)")
        print(f"     (May indicate numerical artifacts)")

    return summary

# Build system
print("Building crypto system with dynamic universe...")
system = crypto_system_with_dynamic_universe(data_path='data/crypto')

# Stage 1: Raw weights from _calculate_dynamic_weights()
print("\n" + "="*70)
print("STAGE 1: RAW WEIGHTS (from _calculate_dynamic_weights)")
print("="*70)
raw_weights = system.portfolio.get_raw_fixed_instrument_weights()
stage1 = check_invariants(raw_weights, "STAGE 1: RAW WEIGHTS")

# Stage 2: After fix_weights_vs_position_or_forecast (before resample)
print("\n" + "="*70)
print("STAGE 2: FITTED WEIGHTS (after fix_weights_vs_position_or_forecast)")
print("="*70)
print("⚠️ This is the CRITICAL stage - if sum << 1.0, concentration starts here")

# We need to manually reproduce the logic to get weights BEFORE resample
from syscore.pandas.strategy_functions import fix_weights_vs_position_or_forecast

instrument_list = list(raw_weights.columns)
subsystem_positions = system.portfolio.get_subsystem_positions_for_instrument_list(instrument_list)
fitted_weights_pre_resample = fix_weights_vs_position_or_forecast(
    raw_weights, subsystem_positions
)
stage2 = check_invariants(fitted_weights_pre_resample, "STAGE 2: POST-FIX (pre-resample)")

# Log dropped instruments
print(f"\n  Instruments with valid subsystem positions:")
valid_positions_per_day = (~subsystem_positions.isna()).sum(axis=1)
print(f"    N_position_valid: avg={valid_positions_per_day.mean():.1f}")
print(f"    N_eligible (from raw weights): avg={(raw_weights > 0).sum(axis=1).mean():.1f}")
print(f"    Drop rate: {1.0 - valid_positions_per_day.mean() / (raw_weights > 0).sum(axis=1).mean():.2%}")

# Stage 3: After resample to daily
fitted_weights_daily = system.portfolio.get_unsmoothed_instrument_weights_fitted_to_position_lengths()
stage3 = check_invariants(fitted_weights_daily, "STAGE 3: POST-RESAMPLE (daily)")

# Stage 4: After EWMA smoothing (before final normalization)
# We need to reproduce EWMA step
smooth_weighting = system.portfolio.config.instrument_weight_ewma_span
smoothed_weights = fitted_weights_daily.ewm(span=smooth_weighting).mean()
stage4 = check_invariants(smoothed_weights, f"STAGE 4: POST-EWMA (span={smooth_weighting})")

# Stage 5: Final weights (after normalization)
final_weights = system.portfolio.get_instrument_weights()
stage5 = check_invariants(final_weights, "STAGE 5: FINAL WEIGHTS (post-normalization)")

# Compute entry/exit events from final weights
print(f"\n{'='*70}")
print("ENTRY/EXIT AUDIT (from final weights)")
print(f"{'='*70}")

# Define entry/exit strictly: weight crosses zero threshold
threshold = 1e-10
is_active = final_weights > threshold

entries = (is_active.astype(int).diff() > 0).sum().sum()
exits = (is_active.astype(int).diff() < 0).sum().sum()

print(f"  Total entries (0 → >0): {entries}")
print(f"  Total exits (>0 → 0): {exits}")
print(f"  Avg entries per day: {entries / len(final_weights):.1f}")
print(f"  Avg exits per day: {exits / len(final_weights):.1f}")
print(f"  Balance (entries - exits): {entries - exits}")

# Sample date analysis (3 dates)
print(f"\n{'='*70}")
print("SAMPLE DATE ANALYSIS")
print(f"{'='*70}")

sample_dates = [
    raw_weights.index[len(raw_weights)//4],
    raw_weights.index[len(raw_weights)//2],
    raw_weights.index[-100]
]

for sample_date in sample_dates:
    print(f"\n{sample_date.date()}:")
    print(f"  Raw weights sum: {raw_weights.loc[sample_date].sum():.4f}")
    print(f"  Fitted weights sum: {fitted_weights_pre_resample.loc[sample_date].sum():.4f}")
    print(f"  Final weights sum: {final_weights.loc[sample_date].sum():.4f}")

    raw_active = raw_weights.loc[sample_date][raw_weights.loc[sample_date] > 0]
    fitted_active = fitted_weights_pre_resample.loc[sample_date][
        fitted_weights_pre_resample.loc[sample_date] > 0
    ]
    final_active = final_weights.loc[sample_date][final_weights.loc[sample_date] > threshold]

    print(f"  N_active: raw={len(raw_active)}, fitted={len(fitted_active)}, final={len(final_active)}")
    print(f"  Instruments dropped (raw → fitted): {len(raw_active) - len(fitted_active)}")

    # Check top-weighted instruments alignment
    if len(final_active) > 0:
        top_5_final = final_active.nlargest(5)
        print(f"\n  Top 5 instruments in final weights:")
        for instr, wt in top_5_final.items():
            raw_wt = raw_weights.loc[sample_date, instr]
            fitted_wt = fitted_weights_pre_resample.loc[sample_date, instr]
            has_position = not pd.isna(subsystem_positions.loc[sample_date, instr])
            print(f"    {instr}: raw={raw_wt:.4f}, fitted={fitted_wt:.4f}, final={wt:.4f}, "
                  f"has_position={has_position}")

# SUMMARY CONCLUSION
print(f"\n{'='*70}")
print("DIAGNOSTIC SUMMARY")
print(f"{'='*70}")

print("\n1. CONCENTRATION DIAGNOSIS:")
if stage2['sum_violations'] > 0:
    print("   ✗ CONFIRMED: fix_weights_vs_position_or_forecast() breaks sum=1 invariant")
    print(f"     Stage 2 avg sum: {stage2['sum_weights'].mean():.4f} (should be 1.0)")
    print(f"     Concentration starts at Stage 2 (post-fix)")
else:
    print("   ? UNEXPECTED: fix_weights_vs_position_or_forecast() preserves sum=1")
    print("   Concentration must occur at a later stage")

print(f"\n2. TRUE EFFECTIVE UNIVERSE:")
print(f"   N_eligible (cost filter): avg={(raw_weights > 0).sum(axis=1).mean():.1f}")
print(f"   N_tradable (with positions): avg={(fitted_weights_pre_resample > 0).sum(axis=1).mean():.1f}")
print(f"   N_effective (concentration): avg={stage5['N_eff'].mean():.1f}")
print(f"   Expected max weight (1/N_tradable): {1.0/(fitted_weights_pre_resample > 0).sum(axis=1).mean():.4f}")
print(f"   Actual max weight (final): {stage5['max_weight'].mean():.4f}")

print(f"\n3. ENTRY/EXIT CHURN:")
if entries / len(final_weights) > 10:
    print(f"   ⚠️ HIGH CHURN: {entries / len(final_weights):.1f} entries/day")
    print("   Investigate: NaN-driven vs signal-driven vs smoothing artifacts")
else:
    print(f"   ✓ REASONABLE: {entries / len(final_weights):.1f} entries/day")

print(f"\n4. NEXT STEPS:")
if stage2['sum_violations'] > 0:
    print("   → Implement renormalization after fix_weights_vs_position_or_forecast()")
    print("   → Verify why so many instruments lack subsystem positions")
else:
    print("   → Investigate EWMA smoothing impact")
    print("   → Check for misalignment in index/frequency")
