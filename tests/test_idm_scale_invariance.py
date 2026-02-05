"""
Test: IDM scale invariance with Carver-style normalization

This test demonstrates that with normalize=True, IDM is scale-invariant.
Scaling all weights by any factor k leaves IDM unchanged.

This breaks the apply_idm_cap() logic, which assumes scaling weights changes IDM.
"""

import numpy as np
import pandas as pd
from systems.crypto_perps.constraints import calculate_idm


def test_idm_scale_invariance():
    """
    Test: Scaling weights by any factor k does NOT change IDM when normalize=True
    """
    # Setup: 3 instruments with specific correlation structure
    corr_matrix = pd.DataFrame(
        [[1.0, 0.6, 0.6],
         [0.6, 1.0, 0.6],
         [0.6, 0.6, 1.0]],
        index=['A', 'B', 'C'],
        columns=['A', 'B', 'C']
    )

    # Scenario 1: Weights sum to 1.0 (unit leverage)
    weights_1x = {'A': 0.4, 'B': 0.3, 'C': 0.3}
    idm_1x = calculate_idm(weights_1x, corr_matrix, normalize=True)

    # Scenario 2: Weights sum to 2.0 (2x leverage)
    weights_2x = {'A': 0.8, 'B': 0.6, 'C': 0.6}
    idm_2x = calculate_idm(weights_2x, corr_matrix, normalize=True)

    # Scenario 3: Weights sum to 0.5 (0.5x leverage)
    weights_05x = {'A': 0.2, 'B': 0.15, 'C': 0.15}
    idm_05x = calculate_idm(weights_05x, corr_matrix, normalize=True)

    # Scenario 4: Weights sum to 5.0 (5x leverage)
    weights_5x = {'A': 2.0, 'B': 1.5, 'C': 1.5}
    idm_5x = calculate_idm(weights_5x, corr_matrix, normalize=True)

    print("\n" + "="*80)
    print("IDM Scale Invariance Test")
    print("="*80)
    print(f"\nWeights 1.0x (sum=1.0): {weights_1x}")
    print(f"  IDM: {idm_1x:.6f}")
    print(f"\nWeights 2.0x (sum=2.0): {weights_2x}")
    print(f"  IDM: {idm_2x:.6f}")
    print(f"\nWeights 0.5x (sum=0.5): {weights_05x}")
    print(f"  IDM: {idm_05x:.6f}")
    print(f"\nWeights 5.0x (sum=5.0): {weights_5x}")
    print(f"  IDM: {idm_5x:.6f}")

    # All should be equal (within numerical precision)
    assert abs(idm_1x - idm_2x) < 1e-6, f"IDM changed with scaling: {idm_1x} vs {idm_2x}"
    assert abs(idm_1x - idm_05x) < 1e-6, f"IDM changed with scaling: {idm_1x} vs {idm_05x}"
    assert abs(idm_1x - idm_5x) < 1e-6, f"IDM changed with scaling: {idm_1x} vs {idm_5x}"

    print(f"\n✓ IDM is scale-invariant: {idm_1x:.6f} (same for all leverage levels)")
    print("  → Scaling weights does NOT change IDM with Carver-style normalization")


def test_apply_idm_cap_is_broken():
    """
    Test: apply_idm_cap() is now deprecated and returns unchanged weights

    The function was incompatible with normalize=True (scaling doesn't change IDM).
    Now it's deprecated and just returns weights unchanged with a warning.
    """
    from systems.crypto_perps.constraints import apply_idm_cap
    import warnings

    # Setup: high correlation → low IDM
    corr_matrix = pd.DataFrame(
        [[1.0, 0.9, 0.9],
         [0.9, 1.0, 0.9],
         [0.9, 0.9, 1.0]],
        index=['A', 'B', 'C'],
        columns=['A', 'B', 'C']
    )

    weights_before = {'A': 0.4, 'B': 0.3, 'C': 0.3}  # Sum = 1.0
    idm_before = calculate_idm(weights_before, corr_matrix, normalize=True)

    print("\n" + "="*80)
    print("apply_idm_cap() Deprecation Test")
    print("="*80)
    print(f"\nBefore apply_idm_cap:")
    print(f"  Weights: {weights_before} (sum={sum(weights_before.values()):.1f})")
    print(f"  IDM (normalized): {idm_before:.6f}")

    idm_cap = 2.0

    # Expect deprecation warning
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        weights_after = apply_idm_cap(weights_before, corr_matrix, idm_cap)

        # Verify deprecation warning was issued
        assert len(w) == 1
        assert issubclass(w[-1].category, DeprecationWarning)
        assert "deprecated" in str(w[-1].message).lower()
        print(f"\n✓ Deprecation warning issued: {w[-1].message}")

    idm_after = calculate_idm(weights_after, corr_matrix, normalize=True)

    print(f"\nAfter apply_idm_cap (deprecated):")
    print(f"  Weights: {weights_after}")
    print(f"  IDM (normalized): {idm_after:.6f}")

    # Function should return weights unchanged (safest deprecated behavior)
    assert weights_after == weights_before, \
        "Deprecated apply_idm_cap() should return weights unchanged"
    assert abs(idm_after - idm_before) < 1e-10, \
        "IDM should be unchanged (weights unchanged)"

    print(f"\n✓ apply_idm_cap() now deprecated: returns weights unchanged")
    print(f"  Use IncrementalConstraintsEngine.step() for Carver-style IDM multiplier")


def test_correct_constraint_flow():
    """
    Test: Demonstrate the CORRECT constraint flow with Carver-style IDM multiplier

    With Carver-style IDM:
    1. Calculate idm_raw (scale-invariant, ≥1.0)
    2. Apply IDM as multiplier: exposure = base_weight × min(idm_raw, idm_cap)
    3. Apply gross leverage cap by scaling (doesn't change IDM)
    4. Final IDM ≈ idm_raw (unchanged by uniform scaling)
    """
    corr_matrix = pd.DataFrame(
        [[1.0, 0.6, 0.6],
         [0.6, 1.0, 0.6],
         [0.6, 0.6, 1.0]],
        index=['A', 'B', 'C'],
        columns=['A', 'B', 'C']
    )

    # Start with base weights (before diversification benefit)
    base_weights = {'A': 0.4, 'B': 0.3, 'C': 0.3}  # Sum = 1.0
    gross_lev_base = sum(abs(w) for w in base_weights.values())

    print("\n" + "="*80)
    print("CORRECT Constraint Flow (Carver-Style IDM Multiplier)")
    print("="*80)

    # Step 1: Calculate idm_raw (normalized, scale-invariant)
    idm_raw = calculate_idm(base_weights, corr_matrix, normalize=True)
    print(f"\nStep 1: Calculate idm_raw (normalized)")
    print(f"  Base weights: {base_weights} (gross_lev={gross_lev_base:.1f})")
    print(f"  idm_raw: {idm_raw:.6f} (≥1.0, diversification measure)")

    # Step 2: Apply IDM as multiplier (Carver-style)
    idm_cap = 2.0
    idm_applied = min(idm_raw, idm_cap)
    exposure_weights = {k: v * idm_applied for k, v in base_weights.items()}
    gross_lev_pre = sum(abs(w) for w in exposure_weights.values())

    print(f"\nStep 2: Apply IDM as multiplier (Carver-style)")
    print(f"  idm_cap: {idm_cap:.1f}")
    print(f"  idm_applied: min({idm_raw:.3f}, {idm_cap:.1f}) = {idm_applied:.3f}")
    print(f"  exposure_weights = base_weights × {idm_applied:.3f}")
    print(f"  Exposure weights: {exposure_weights}")
    print(f"  gross_lev_pre: {gross_lev_pre:.3f} (= {gross_lev_base:.1f} × {idm_applied:.3f})")
    print(f"  ✓ IDM increased leverage by {(idm_applied - 1.0) * 100:.1f}% (diversification benefit)")

    # Step 3: Apply gross leverage cap by scaling
    gross_lev_cap = 2.0
    if gross_lev_pre > gross_lev_cap:
        gross_lev_scalar = gross_lev_cap / gross_lev_pre
        constrained_weights = {k: v * gross_lev_scalar for k, v in exposure_weights.items()}
        gross_lev_final = gross_lev_cap
    else:
        constrained_weights = exposure_weights
        gross_lev_scalar = 1.0
        gross_lev_final = gross_lev_pre

    print(f"\nStep 3: Apply gross leverage cap")
    print(f"  gross_lev_cap: {gross_lev_cap:.1f}")
    print(f"  gross_lev_scalar: {gross_lev_scalar:.6f}")
    print(f"  Constrained weights: {constrained_weights}")
    print(f"  gross_lev_final: {gross_lev_final:.3f}")
    print(f"  ✓ gross_lev_final <= gross_lev_cap (invariant satisfied)")

    # Step 4: Verify IDM unchanged by uniform scaling
    idm_final = calculate_idm(constrained_weights, corr_matrix, normalize=True)
    print(f"\nStep 4: Verify IDM unchanged by uniform scaling")
    print(f"  idm_final: {idm_final:.6f}")
    print(f"  idm_final ≈ idm_raw? {abs(idm_final - idm_raw) < 1e-6}")

    assert abs(idm_final - idm_raw) < 1e-6, \
        "IDM should not change when scaling weights (Carver-style normalization)"

    print(f"\n✓ Correct Carver-style flow:")
    print(f"  - idm_raw = {idm_raw:.3f} (scale-invariant measure, ≥1.0)")
    print(f"  - idm_applied = {idm_applied:.3f} ≤ {idm_cap:.1f} (capped for safety)")
    print(f"  - IDM multiplier increased leverage: {gross_lev_base:.1f} → {gross_lev_pre:.3f}")
    print(f"  - Gross lev cap scaled to: {gross_lev_final:.3f}")
    print(f"  - idm_final = {idm_final:.3f} ≈ idm_raw (unchanged by scaling)")


def test_carver_style_idm_multiplier_in_engine():
    """
    Test: IncrementalConstraintsEngine uses IDM as Carver-style multiplier

    Verifies:
    1. IDM calculated from normalized weights (idm_raw ≥ 1.0)
    2. IDM used as multiplier: exposure = base_weight × min(idm_raw, cap)
    3. Gross leverage cap applied to exposure_weights
    4. idm_final ≈ idm_raw (scale-invariant with uniform scaling)
    """
    from systems.crypto_perps.constraints import IncrementalConstraintsEngine

    # Setup engine
    engine = IncrementalConstraintsEngine(
        instruments=['A', 'B', 'C'],
        span=60,
        min_periods=20,
        idm_cap=2.0,
        gross_leverage_cap=10.0  # High cap to not interfere
    )

    # Inject known correlation matrix (deterministic)
    # Moderate correlation (ρ=0.6) → IDM ≈ 1.5-1.8
    corr_matrix = pd.DataFrame(
        [[1.0, 0.6, 0.6],
         [0.6, 1.0, 0.6],
         [0.6, 0.6, 1.0]],
        index=['A', 'B', 'C'],
        columns=['A', 'B', 'C']
    )
    engine.corr_matrix = corr_matrix

    # Test with base weights that should benefit from diversification
    date = pd.Timestamp('2024-02-01')
    base_weights = {'A': 0.3, 'B': 0.3, 'C': 0.3}  # Equal allocation, sum=0.9

    constrained, gross_lev, idm, diag = engine.step(
        date=date,
        returns={'A': 0.01, 'B': 0.01, 'C': 0.01},  # Returns still needed for interface
        weights=base_weights,
        return_diagnostics=True
    )

    # Verify IDM used as multiplier
    idm_raw = diag['idm_raw']
    idm_applied = diag['idm_applied']

    print("\n" + "="*80)
    print("✓ Carver-style IDM multiplier in IncrementalConstraintsEngine:")
    print("="*80)
    print(f"  Base weights sum: {sum(base_weights.values()):.3f}")
    print(f"  idm_raw: {idm_raw:.3f} (≥1.0, scale-invariant measure)")
    print(f"  idm_applied: {idm_applied:.3f} (= min(idm_raw, {diag['idm_cap']}))")
    print(f"  Expected gross_lev after IDM: {sum(base_weights.values()) * idm_applied:.3f}")
    print(f"  Actual gross_lev_pre: {diag['gross_lev_pre']:.3f}")
    print(f"  Final gross_lev: {diag['gross_lev_final']:.3f}")

    # Verify invariants
    assert idm_raw >= 1.0, f"idm_raw {idm_raw:.3f} should be >= 1.0"
    assert idm_applied <= diag['idm_cap'] + 1e-6, \
        f"idm_applied {idm_applied:.3f} exceeds cap {diag['idm_cap']}"
    assert idm_applied >= 1.0, f"idm_applied {idm_applied:.3f} should be >= 1.0"

    # Verify IDM was applied as multiplier
    expected_gross_lev = sum(abs(w) for w in base_weights.values()) * idm_applied
    assert abs(diag['gross_lev_pre'] - expected_gross_lev) < 1e-6, \
        f"Gross lev {diag['gross_lev_pre']:.3f} != expected {expected_gross_lev:.3f}"

    # Verify IDM increases leverage (diversification benefit)
    if idm_applied > 1.0 + 1e-6:
        assert diag['gross_lev_pre'] > sum(abs(w) for w in base_weights.values()) + 1e-6, \
            "IDM multiplier should increase gross leverage"
        print(f"  ✓ Leverage increase: {(idm_applied - 1.0) * 100:.1f}% from diversification benefit")

    # Verify IDM unchanged by uniform scaling
    assert abs(diag['idm_final'] - idm_raw) < 0.1, \
        f"idm_final {diag['idm_final']:.3f} should ≈ idm_raw {idm_raw:.3f}"


def test_idm_cap_binding():
    """
    Test: When idm_raw > cap, idm_applied is capped
    """
    from systems.crypto_perps.constraints import IncrementalConstraintsEngine

    # Setup with low IDM cap
    engine = IncrementalConstraintsEngine(
        instruments=['A', 'B', 'C'],
        span=60,
        min_periods=20,
        idm_cap=1.5,  # Low cap to test binding
        gross_leverage_cap=10.0
    )

    # Inject low-correlation matrix (→ high IDM > cap)
    # Zero correlation (independent) → IDM = sqrt(3) ≈ 1.73 > cap (1.5)
    corr_matrix = pd.DataFrame(
        [[1.0, 0.0, 0.0],
         [0.0, 1.0, 0.0],
         [0.0, 0.0, 1.0]],
        index=['A', 'B', 'C'],
        columns=['A', 'B', 'C']
    )
    engine.corr_matrix = corr_matrix

    date = pd.Timestamp('2024-02-01')
    base_weights = {'A': 0.3, 'B': 0.3, 'C': 0.3}

    constrained, gross_lev, idm, diag = engine.step(
        date=date,
        returns={'A': 0.01, 'B': 0.01, 'C': 0.01},
        weights=base_weights,
        return_diagnostics=True
    )

    idm_raw = diag['idm_raw']
    idm_applied = diag['idm_applied']

    print("\n" + "="*80)
    print("✓ IDM cap binding:")
    print("="*80)
    print(f"  idm_raw: {idm_raw:.3f}")
    print(f"  idm_cap: {diag['idm_cap']:.3f}")
    print(f"  idm_applied: {idm_applied:.3f}")

    # Verify cap is respected
    assert idm_applied <= diag['idm_cap'] + 1e-6, \
        f"idm_applied {idm_applied:.3f} exceeds cap {diag['idm_cap']}"

    if idm_raw > diag['idm_cap'] + 1e-6:
        # Cap is binding
        assert abs(idm_applied - diag['idm_cap']) < 1e-6, \
            f"idm_applied should equal cap when idm_raw > cap"
        assert diag['idm_cap_binding'], "Cap should be marked as binding"
        print(f"  ✓ Cap binding: idm_raw={idm_raw:.3f} > cap={diag['idm_cap']:.3f}")
        print(f"  ✓ Leverage increase capped at {(idm_applied - 1.0) * 100:.1f}%")
    else:
        # Cap not binding
        assert abs(idm_applied - idm_raw) < 1e-6, \
            f"idm_applied should equal idm_raw when below cap"
        print(f"  ✓ Cap not binding: idm_raw={idm_raw:.3f} < cap={diag['idm_cap']:.3f}")


if __name__ == '__main__':
    import pytest
    pytest.main([__file__, '-v', '-s'])
