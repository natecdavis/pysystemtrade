# Bug Report: IDM and Constraint Cap Issues

**Date**: 2026-01-26
**Discovered During**: Phase 2 Cross-Section Scaling Analysis
**Severity**: High (affects constraint application and IDM interpretation)
**Status**: ✅ **FIXED** - Implementation complete (2026-01-26), awaiting backtest verification

---

## Issue 1: IDM Cap Violation (Gross Leverage Priority)

### Problem
The IDM cap (2.5) is being exceeded in the final constrained weights:
- Max IDM observed: 3.063 (22% above cap)
- Days exceeding cap: 7 out of 1,345 (0.5%)

### Root Cause
By design, gross leverage cap has **absolute priority** over IDM cap:

```python
# Step 1: Apply IDM cap → scales weights UP to reduce IDM
weights_after_idm = apply_idm_cap(weights, corr_matrix, idm_cap)

# Step 2: Apply gross leverage cap → scales weights DOWN if gross_lev > cap
# This can cause IDM to exceed its cap again (accepted trade-off)
constrained_weights = apply_gross_leverage_cap(weights_after_idm, gross_leverage_cap)
```

### Status
**This is working as designed**, but not clearly documented. The comment at line 733 in constraints.py states:

> "This may cause IDM to exceed its cap, which we accept as necessary trade-off"

### Recommendation
1. ✓ **Document** this behavior explicitly in config and user guide
2. ✓ **Add diagnostics** to track:
   - `idm_raw` (before any constraints)
   - `idm_after_idm_cap` (after IDM cap, before gross lev cap)
   - `idm_final` (after all constraints - can exceed idm_cap)
   - `cap_priority` ('idm', 'gross_lev', 'both', 'none')
3. ✓ **Log warnings** when IDM cap is violated due to gross leverage priority

---

## Issue 2: IDM < 1.0 (Violates Carver Definition)

### Problem
Mean IDM = 0.902 < 1.0, which violates standard Carver-style IDM definition:
- Phase 1 (N=4): mean IDM = 1.061
- Phase 2 (N=15): mean IDM = 0.902

In Carver's "Leveraged Trading" and "Systematic Trading":
- IDM is a diversification **MULTIPLIER**
- IDM ≥ 1.0 **always** (1.0 = no diversification, higher = more diversification)
- IDM = sqrt(N) for N perfectly uncorrelated, equal-weighted assets

### Root Cause
The `calculate_idm()` function computes IDM on **leveraged weights** without normalization:

```python
# CURRENT (WRONG):
portfolio_stdev = calculate_portfolio_stdev(weights, corr_matrix)  # weights can sum to 2.0
idm = 1.0 / portfolio_stdev  # Can be < 1.0 if portfolio_stdev > 1.0
```

When weights sum to 2.0 (gross leverage at cap):
- `portfolio_stdev ≈ 2.0 × σ_normalized`
- `IDM = 1 / (2.0 × σ_normalized) ≈ 0.5 × IDM_normalized`

This is why mean IDM < 1.0 at N=15 (weights frequently at gross_lev_cap = 2.0).

### Unit Test Validation

Created `tests/test_idm_definition.py` demonstrating the bug:

```
✓ test_idm_perfectly_correlated_should_equal_one
  - Normalized weights (sum=1.0): IDM = 1.000 ✓

✓ test_idm_uncorrelated_should_be_greater_than_one
  - Normalized weights (sum=1.0): IDM = 1.732 ✓

✓ test_idm_leveraged_weights_violates_definition
  - Leveraged weights (sum=2.0): IDM = 0.500 ✗ (< 1.0, violates Carver!)

✓ test_idm_definition_carver_style
  - CORRECT (normalized): IDM = 1.168 ✓
  - CURRENT (leveraged): IDM = 0.584 ✗ (< 1.0)
```

### Fix Applied

Modified `calculate_idm()` to normalize weights before calculation:

```python
def calculate_idm(
    weights: Dict[str, float],
    corr_matrix: pd.DataFrame,
    normalize: bool = True  # NEW parameter (default True)
) -> float:
    """
    Calculate Carver-style IDM (Instrument Diversification Multiplier)

    If normalize=True (default):
      1. Normalize weights to sum to 1.0 in absolute terms
      2. Calculate portfolio_stdev on normalized weights
      3. IDM = 1 / portfolio_stdev

    Result: IDM >= 1.0 always
    """
    if normalize:
        total_abs_weight = sum(abs(w) for w in weights.values())
        if total_abs_weight < 1e-10:
            return 1.0
        normalized_weights = {k: v / total_abs_weight for k, v in weights.items()}
        portfolio_stdev = calculate_portfolio_stdev(normalized_weights, corr_matrix)
    else:
        # Legacy behavior (for backward compatibility)
        portfolio_stdev = calculate_portfolio_stdev(weights, corr_matrix)

    return 1.0 / portfolio_stdev
```

### Expected Impact of Fix

After fix (with normalization), expected IDM for Phase 2:
- Mean IDM: ~1.8 (instead of 0.902)
- All daily IDM values: ≥ 1.0
- Interpretation: IDM now correctly measures diversification benefit independent of leverage

The lower-than-expected IDM at N=15 vs N=4 will now be correctly attributed to **high forecast correlations** (median 0.649), not artifactual leverage effects.

---

## Issue 3: Missing Invariant Checks

### Problem
No runtime assertions to catch constraint violations.

### Fix Applied

Added invariant checks in `IncrementalConstraintsEngine.step()`:

```python
# Invariant checks (with small epsilon for numerical precision)
eps = 0.01
assert idm_final >= 1.0 - eps, \
    f"IDM should be >= 1.0 (Carver-style), got {idm_final}"
assert gross_lev_final <= self.gross_leverage_cap + eps, \
    f"Gross leverage {gross_lev_final} exceeds cap {self.gross_leverage_cap}"
```

**Note**: IDM can still exceed `idm_cap` when gross leverage cap takes priority (this is by design).

---

## Testing Plan

1. **Unit tests** (tests/test_idm_definition.py): ✓ Created and passing

2. **Integration test** (recommended):
   - Re-run Phase 2 backtest with fixed IDM calculation
   - Verify mean IDM ≥ 1.0
   - Compare scaling analysis with corrected metrics

3. **Regression test** (recommended):
   - Re-run Phase 1 backtest (N=4, 2020-2024)
   - Verify backtest still completes successfully
   - Document any changes in reported IDM values

---

## Documentation Updates Needed

1. **Config documentation**:
   - Clarify that `gross_leverage_cap` has absolute priority over `idm_cap`
   - Document that IDM can exceed `idm_cap` when gross leverage binds

2. **Code comments**:
   - Add detailed comments explaining constraint priority at `apply_gross_leverage_cap` call site
   - Document Carver-style IDM formula in `calculate_idm()`

3. **Diagnostics specification**:
   - Document all constraint diagnostic fields:
     - `idm_raw`, `idm_after_idm_cap`, `idm_final`
     - `gross_lev_raw`, `gross_lev_after_idm_cap`, `gross_lev_final`
     - `idm_scalar`, `gross_lev_scalar`, `overall_scalar`
     - `cap_priority`

4. **Phase 2 scaling report**:
   - Update analysis with corrected IDM values
   - Reinterpret "IDM decreased at N=15" observation with proper normalization

---

## Summary

### Critical Fixes Applied
1. ✓ IDM calculation now uses normalized weights (Carver-style)
2. ✓ Added invariant checks for IDM ≥ 1.0 and gross_lev ≤ cap
3. ✓ Extended diagnostics to track raw vs capped values
4. ✓ Added unit tests demonstrating correct vs incorrect IDM behavior

### Still TODO
1. Re-run Phase 2 backtest with fixed IDM
2. Update phase2_scaling_summary.md with corrected metrics
3. Add constraint diagnostics to saved diagnostics.parquet
4. Document constraint priority behavior in user guide

### Expected Outcomes
- Mean IDM for Phase 2 (N=15): ~1.8 (up from 0.902)
- All IDM values: ≥ 1.0 (Carver-style definition satisfied)
- IDM interpretation: Lower IDM at N=15 vs N=4 correctly attributed to high correlations (0.649 median), not leverage artifacts
- Gross leverage cap violations: 0 (enforced by invariant)
- IDM cap violations: 7 days (0.5%) - **by design** when gross leverage takes priority

---

## References

- Rob Carver, "Leveraged Trading" (2019), Chapter on portfolio construction
- Rob Carver, "Systematic Trading" (2015), IDM calculation methodology
- Test file: `tests/test_idm_definition.py`
- Fixed function: `systems/crypto_perps/constraints.py::calculate_idm()`

---

## Implementation Status Summary

### Issue 1: IDM Cap "Violation" - ✅ FIXED
**Root cause**: Confusion between idm_raw (observation) and idm_applied (capped multiplier)

**Fix**:
- Clearly defined `idm_applied = min(idm_raw, idm_cap)`
- idm_applied never exceeds cap by definition
- idm_raw can exceed cap (it's just an observation of diversification)
- Updated diagnostics to track both values separately

**Status**: Implementation complete. Awaiting backtest verification.

---

### Issue 2: IDM < 1.0 - ✅ FIXED
**Root cause**: `calculate_idm()` was using leveraged weights without normalization

**Fix**:
- Added `normalize=True` parameter (default) to `calculate_idm()`
- Ensures IDM ≥ 1.0 (Carver-style definition)
- Proved scale invariance in tests
- Updated all callers to use normalization

**Status**: Implementation complete. All tests show IDM ≥ 1.0. Awaiting backtest verification.

**Expected outcome**: Mean IDM at N=15 should be ~1.5-2.0 (up from 0.902)

---

### Issue 3: apply_idm_cap() Broken with Normalization - ✅ FIXED
**Root cause**: Function tried to change IDM by scaling weights (doesn't work with normalization)

**Fix**:
- Removed/deprecated `apply_idm_cap()` function
- Replaced with Carver-style IDM multiplier logic
- IDM now used as multiplier: `exposure = base_weight × min(idm_raw, cap)`
- Gross leverage cap still applied afterward (absolute priority)

**Status**: Implementation complete. Deprecated with clear warning. Awaiting backtest verification.

**Expected outcome**: Carver-style diversification benefit realized (higher leverage from diversification)

---

## Verification Checklist

Before marking as FULLY RESOLVED, verify the following in backtests:

### Engineering Requirements (Hard Pass/Fail)
- [ ] Phase 1 backtest completes without errors
- [ ] Phase 2 backtest completes without errors
- [ ] No NaN/Inf in diagnostics parquet
- [ ] All invariant checks pass
- [ ] Runtime acceptable (<30s for both)

### Invariant Checks (Must Hold Every Day)
- [ ] `idm_raw >= 1.0 - eps` (Carver-style definition)
- [ ] `idm_applied >= 1.0 - eps` (multiplier ≥1.0)
- [ ] `idm_applied <= idm_cap + eps` (by definition of min)
- [ ] `gross_lev_final <= gross_lev_cap + eps` (constraint enforcement)
- [ ] `idm_final ≈ idm_raw` (scale-invariant, conditional on no drops)

### Economic Outcomes (Expected Changes)
- [ ] Mean IDM at N=4: ~1.5-2.0 (Carver-style, ≥1.0)
- [ ] Mean IDM at N=15: ~1.5-2.0 (up from 0.902 with bug)
- [ ] All daily IDM values ≥ 1.0 (no more violations)
- [ ] Gross leverage higher than old implementation (due to IDM multiplier)
- [ ] IDM multiplier used most days (idm_applied > 1.0)
- [ ] IDM cap binding occasionally (when correlations low → high diversification)

### Diagnostics Verification
- [ ] All new diagnostic columns present:
  - idm_raw, idm_applied, idm_final, idm_cap, idm_cap_binding, idm_multiplier_used
  - gross_lev_base, gross_lev_pre, gross_lev_scalar, gross_lev_final, gross_lev_cap_binding
  - overall_scalar_from_base
- [ ] Constraint binding patterns make sense
- [ ] IDM distributions reasonable (1.0 to ~2.0 range)

---

## Refactoring Completed: 2026-01-26

**Files Modified**:
- `systems/crypto_perps/constraints.py`: Core constraint logic refactored
- `systems/crypto_perps/system.py`: Updated invariant checks and diagnostics
- `tests/test_idm_scale_invariance.py`: Added comprehensive Carver-style tests
- `tests/test_idm_definition.py`: Updated to verify fix
- `tests/test_incremental_constraints.py`: Updated for 4-value return
- `IDM_RECONCILIATION.md`: Added implementation status
- `BUG_REPORT_IDM_CONSTRAINTS.md`: This file (updated status)

**Test Results**: All 13 constraint tests passing ✅

**Next Step**: Run Phase 1 & 2 backtests to verify economic outcomes and mark as FULLY RESOLVED.

