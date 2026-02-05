# IDM Cap Logic: Reconciliation and Fix

**Date**: 2026-01-26
**Issue**: Conceptual inconsistency between Carver-style IDM normalization and apply_idm_cap() logic

**Status**: ✅ **IMPLEMENTATION COMPLETE** (2026-01-26)
- Carver-style IDM multiplier implemented
- All tests passing (13/13 constraint tests)
- Awaiting backtest verification

---

## The Fundamental Problem

With Carver-style normalization, **IDM is scale-invariant**:

```python
# Carver-style IDM calculation:
def calculate_idm(weights, corr_matrix, normalize=True):
    total_abs = sum(abs(w) for w in weights.values())
    normalized_weights = {k: v/total_abs for k, v in weights.items()}
    portfolio_stdev = sqrt(normalized_weights' * Corr * normalized_weights)
    return 1 / portfolio_stdev
```

**Key insight**: Scaling all weights by factor `k` does NOT change `idm`:
- `weights_scaled = k * weights`
- `normalized_weights_scaled = (k * weights) / (k * total) = weights / total`
- `normalized_weights_scaled == normalized_weights` (same!)
- Therefore: `idm(k * weights) == idm(weights)` for any `k > 0`

---

## Test Proof

From `tests/test_idm_scale_invariance.py`:

```
Weights 1.0x (sum=1.0): IDM = 1.165631
Weights 2.0x (sum=2.0): IDM = 1.165631  ← Same!
Weights 5.0x (sum=5.0): IDM = 1.165631  ← Same!

✓ IDM is scale-invariant with Carver-style normalization
```

---

## Why apply_idm_cap() Is Broken

The current `apply_idm_cap()` logic assumes:

```python
# WRONG assumption (only true WITHOUT normalization):
"If IDM too high → scale weights UP → portfolio_stdev increases → IDM decreases"
```

But with normalization:

```python
# ACTUAL behavior (WITH normalization):
"If IDM too high → scale weights UP → portfolio_stdev unchanged → IDM unchanged"
```

**Result**: `apply_idm_cap()` just increases leverage without changing IDM (pointless and dangerous).

Test proof:
```
Before apply_idm_cap: IDM = 1.035
After apply_idm_cap:  IDM = 1.035  ← Unchanged!
✗ apply_idm_cap() did NOT change IDM (scale-invariant with normalization)
```

---

## Correct Definitions

### 1. idm_raw (Scale-Invariant Diversification Measure)

```python
idm_raw = calculate_idm(weights, corr_matrix, normalize=True)
```

**Properties**:
- Scale-invariant: `idm_raw(k * weights) == idm_raw(weights)`
- Determined by correlation structure and relative weight distribution
- NOT affected by scaling all weights
- Always ≥ 1.0 with Carver-style normalization
- 1.0 = perfectly correlated (no diversification)
- Higher = better diversification

**Examples**:
- Perfect correlation (ρ=1.0): `idm_raw = 1.0`
- Zero correlation, equal weights: `idm_raw = sqrt(N)`
- Typical crypto (ρ~0.6-0.7): `idm_raw ≈ 1.5-2.0`

### 2. idm_applied (Cap for Position Sizing)

```python
idm_applied = min(idm_raw, idm_cap)
```

**Properties**:
- **Invariant**: `idm_applied <= idm_cap` (ALWAYS, by construction)
- Used as multiplier in position sizing (if applicable)
- Caps the leverage benefit from diversification
- Cannot be "violated" - it's just min(raw, cap)

**Examples**:
- If `idm_raw = 1.5` and `idm_cap = 2.0`: `idm_applied = 1.5` (no cap binding)
- If `idm_raw = 2.8` and `idm_cap = 2.0`: `idm_applied = 2.0` (cap binding)

### 3. gross_lev_pre (Before Gross Leverage Cap)

```python
gross_lev_pre = sum(abs(w) for w in weights_before_gross_cap.values())
```

**Properties**:
- Sum of absolute position weights
- Can exceed `gross_leverage_cap` before scaling
- Affected by all upstream logic (forecasts, IDM multiplier, etc.)

### 4. gross_lev_scalar (Scaling to Meet Gross Leverage Cap)

```python
gross_lev_scalar = min(1.0, gross_leverage_cap / gross_lev_pre)
```

**Properties**:
- Always ≤ 1.0 (only scales down, never up)
- Applied to all weights: `weights_final = gross_lev_scalar * weights_pre`
- Ensures `gross_lev_final <= gross_leverage_cap`
- **Does NOT change idm_raw** (scale-invariant)

### 5. overall_scalar_applied (Net Scalar Applied to Positions)

```python
overall_scalar_applied = gross_lev_final / gross_lev_initial
```

**Properties**:
- Overall scaling from initial weights to final constrained weights
- Typically ≤ 1.0 (constraints reduce positions)
- Can be < 1.0 even if each step has scalar ≤ 1.0 (compounding)

---

## Correct Constraint Flow

```python
# Step 1: Calculate idm_raw (normalized, scale-invariant)
idm_raw = calculate_idm(weights, corr_matrix, normalize=True)
# Result: idm_raw = 1.165 (determined by correlation + relative weights)

# Step 2: Cap IDM for safety (just take minimum)
idm_applied = min(idm_raw, idm_cap)
# Result: idm_applied = min(1.165, 2.0) = 1.165
# Invariant: idm_applied <= idm_cap ✓ (always true by construction)

# Step 3: Apply gross leverage cap by scaling
gross_lev_scalar = min(1.0, gross_leverage_cap / gross_lev_pre)
weights_final = {k: v * gross_lev_scalar for k, v in weights.items()}
gross_lev_final = sum(abs(w) for w in weights_final.values())
# Result: weights scaled from 2.5 to 2.0
# Invariant: gross_lev_final <= gross_leverage_cap ✓

# Step 4: Verify IDM unchanged
idm_final = calculate_idm(weights_final, corr_matrix, normalize=True)
# Result: idm_final = 1.165 == idm_raw ✓ (scale-invariant)
```

**Key points**:
- `idm_raw` is calculated once, never changes with scaling
- `idm_applied <= idm_cap` always (by definition of min)
- `gross_lev_final <= gross_leverage_cap` always (by scaling)
- NO conflict between caps (they operate on different quantities)

---

## Invariants That Must Hold

```python
# Invariant 1: Carver-style IDM always ≥ 1.0
assert idm_raw >= 1.0 - eps, f"IDM {idm_raw} should be >= 1.0 (Carver-style)"

# Invariant 2: Applied IDM never exceeds cap
assert idm_applied <= idm_cap + eps, f"idm_applied {idm_applied} exceeds cap {idm_cap}"

# Invariant 3: Gross leverage never exceeds cap
assert gross_lev_final <= gross_leverage_cap + eps, \
    f"gross_lev {gross_lev_final} exceeds cap {gross_leverage_cap}"

# Invariant 4: IDM is scale-invariant (with normalization)
idm_before_scaling = calculate_idm(weights_before, corr, normalize=True)
idm_after_scaling = calculate_idm(weights_after, corr, normalize=True)
assert abs(idm_after_scaling - idm_before_scaling) < eps, \
    "IDM should not change when scaling weights"
```

**Note**: We do NOT have an invariant `idm_raw <= idm_cap`. The raw IDM can exceed the cap - we just don't use values above the cap for position sizing.

---

## Numeric Example

```python
# Setup
corr_matrix = [[1.0, 0.6, 0.6],
               [0.6, 1.0, 0.6],
               [0.6, 0.6, 1.0]]

weights_initial = {'A': 1.0, 'B': 0.8, 'C': 0.7}  # Sum = 2.5
idm_cap = 2.0
gross_leverage_cap = 2.0

# Step 1: Calculate idm_raw
normalized = {'A': 0.4, 'B': 0.32, 'C': 0.28}  # Sum = 1.0
portfolio_stdev = sqrt(0.4^2 + 0.32^2 + 0.28^2 + 2*(0.4*0.32 + 0.4*0.28 + 0.32*0.28)*0.6)
                = sqrt(0.16 + 0.1024 + 0.0784 + 2*(0.128 + 0.112 + 0.0896)*0.6)
                = sqrt(0.3408 + 0.3955)
                = sqrt(0.7363)
                = 0.858
idm_raw = 1 / 0.858 = 1.165

# Step 2: Cap IDM
idm_applied = min(1.165, 2.0) = 1.165
✓ idm_applied <= idm_cap

# Step 3: Scale for gross leverage
gross_lev_pre = 2.5
gross_lev_scalar = 2.0 / 2.5 = 0.8
weights_final = {'A': 0.8, 'B': 0.64, 'C': 0.56}  # Sum = 2.0
✓ gross_lev_final <= gross_leverage_cap

# Step 4: Verify IDM unchanged
normalized_final = {'A': 0.4, 'B': 0.32, 'C': 0.28}  # Sum = 1.0 (same as before!)
idm_final = 1.165  # Same as idm_raw
✓ idm_final == idm_raw (scale-invariant)
```

**Summary**:
- `idm_raw = 1.165` (from correlation structure)
- `idm_applied = 1.165` (no cap binding, since 1.165 < 2.0)
- `gross_lev_final = 2.0` (scaled from 2.5 to meet cap)
- `idm_final = 1.165` (unchanged by scaling)
- **All invariants satisfied ✓**

---

## What Was Wrong in Original Report

My original report claimed:

> "IDM can exceed its cap (3.06 > 2.5) when gross leverage cap takes priority"

**This was wrong because**:
1. I confused `idm_raw` (scale-invariant measure) with `idm_applied` (capped multiplier)
2. With correct definitions: `idm_raw` can exceed `idm_cap` (that's fine - it's just an observation)
3. But `idm_applied = min(idm_raw, idm_cap)` can NEVER exceed cap (by definition)
4. The value being reported as "IDM" in diagnostics should be `idm_raw` (true diversification)
5. The value used for position sizing (if any) should be `idm_applied` (capped for safety)

**What actually happened**:
- The system calculated IDM on leveraged weights (wrong - should normalize)
- This gave IDM values < 1.0 (violates Carver definition)
- The IDM "cap" logic tried to scale weights (pointless with normalization)
- The reported "IDM exceeding cap" was due to using wrong calculation method

---

## Required Code Changes

### 1. Remove apply_idm_cap() Step

The current two-step process is broken:

```python
# CURRENT (BROKEN):
weights_after_idm = apply_idm_cap(weights, corr, idm_cap)  # Does nothing with normalize=True
constrained_weights = apply_gross_leverage_cap(weights_after_idm, gross_cap)
```

Replace with correct single-step:

```python
# CORRECT:
# Calculate idm_raw (normalized, scale-invariant)
idm_raw = calculate_idm(weights, corr_matrix, normalize=True)

# Cap for position sizing (if needed)
idm_applied = min(idm_raw, idm_cap)

# Apply gross leverage cap
constrained_weights = apply_gross_leverage_cap(weights, gross_leverage_cap)

# Verify idm_raw unchanged
idm_final = calculate_idm(constrained_weights, corr_matrix, normalize=True)
assert abs(idm_final - idm_raw) < eps  # Should be equal (scale-invariant)
```

### 2. Update Diagnostics

Report separate values:

```python
diagnostics = {
    'idm_raw': idm_raw,  # True diversification measure (scale-invariant)
    'idm_applied': idm_applied,  # Capped for position sizing (if used)
    'idm_cap': idm_cap,  # The cap value
    'gross_lev_pre': gross_lev_pre,  # Before gross lev cap
    'gross_lev_scalar': gross_lev_scalar,  # Scaling applied
    'gross_lev_final': gross_lev_final,  # After gross lev cap
    'overall_scalar_applied': overall_scalar  # Net scaling
}
```

### 3. Update Invariants

```python
# Invariant checks
eps = 0.01

# IDM is Carver-style (≥ 1.0)
assert idm_raw >= 1.0 - eps, f"idm_raw {idm_raw} should be >= 1.0"

# Applied IDM respects cap
assert idm_applied <= idm_cap + eps, f"idm_applied {idm_applied} exceeds cap {idm_cap}"

# Gross leverage respects cap
assert gross_lev_final <= gross_leverage_cap + eps, \
    f"gross_lev {gross_lev_final} exceeds cap {gross_leverage_cap}"

# IDM is scale-invariant
assert abs(idm_final - idm_raw) < eps, \
    "idm_final should equal idm_raw (scale-invariant)"
```

---

## Summary: Correct Variable Definitions

| Variable | Definition | Properties | Can Exceed Cap? |
|----------|-----------|------------|-----------------|
| `idm_raw` | `1 / portfolio_stdev(normalized_weights)` | Scale-invariant, ≥ 1.0, measures true diversification | Yes (just an observation) |
| `idm_applied` | `min(idm_raw, idm_cap)` | Used for position sizing, safety cap | **NO** (≤ cap by definition) |
| `gross_lev_pre` | `sum(abs(weights))` before gross cap | Can exceed cap before scaling | Yes (before constraint) |
| `gross_lev_final` | `sum(abs(weights))` after gross cap | After scaling down | **NO** (≤ cap by design) |
| `gross_lev_scalar` | `min(1.0, cap / gross_lev_pre)` | Scaling factor applied | N/A (≤ 1.0 always) |
| `overall_scalar_applied` | `gross_lev_final / gross_lev_initial` | Net scaling from start to finish | N/A (typically ≤ 1.0) |

**Key insight**: With correct definitions, there is NO cap violation. The confusion arose from:
1. Using wrong IDM calculation (on leveraged weights)
2. Trying to "enforce" IDM cap by scaling (doesn't work with normalization)
3. Reporting the wrong value as "IDM" in diagnostics

---

## Conclusion

**User was correct**: The original explanation was conceptually inconsistent.

**Root causes**:
1. `apply_idm_cap()` logic incompatible with Carver-style normalization
2. Confusion between `idm_raw` (observation) and `idm_applied` (capped multiplier)
3. Missing invariant that `idm_applied <= idm_cap` ALWAYS

**Fix required**:
1. Remove `apply_idm_cap()` step (it's a no-op with normalization)
2. Calculate `idm_applied = min(idm_raw, idm_cap)` directly
3. Report both `idm_raw` and `idm_applied` in diagnostics
4. Add proper invariant checks
5. Document that `idm_raw` can exceed `idm_cap` (that's fine - it's just diversification observation)

**Tests prove**:
- IDM is scale-invariant with normalization ✓
- apply_idm_cap() does nothing with normalization ✓
- Correct flow: calculate once, cap for sizing, scale for gross lev ✓
- No conceptual conflicts with correct definitions ✓

---

## Implementation Status

### ✅ Completed (2026-01-26)

**1. Fixed `calculate_idm()` to use normalization**:
   - Added `normalize=True` parameter (default)
   - Ensures IDM ≥ 1.0 (Carver-style definition)
   - Proved scale invariance in tests

**2. Created comprehensive test suite**:
   - `tests/test_idm_scale_invariance.py`: Proves IDM scale invariance
   - `tests/test_idm_definition.py`: Validates Carver-style IDM properties
   - All 13 constraint tests passing

**3. Documented correct variable definitions**:
   - idm_raw: Scale-invariant diversification measure (≥1.0)
   - idm_applied: min(idm_raw, idm_cap) - capped multiplier
   - Clear distinction between risk allocation and actual exposure

**4. Refactored constraint logic (Carver-style IDM multiplier)**:
   - Removed broken `apply_idm_cap()` step
   - Deprecated `apply_idm_cap()` function with clear warning
   - Implemented Carver-style IDM as leverage multiplier
   - Updated `IncrementalConstraintsEngine.step()` with new logic
   - Updated batch function `apply_portfolio_constraints()` to match

**5. Updated constraint flow**:
   - Step 1: Calculate idm_raw (scale-invariant, ≥1.0)
   - Step 2: Apply IDM multiplier: exposure = base_weight × min(idm_raw, cap)
   - Step 3: Apply gross leverage cap (absolute priority)

**6. Extended diagnostics**:
   - Tracks: idm_raw, idm_applied, idm_final, idm_cap_binding
   - Tracks: gross_lev_base, gross_lev_pre, gross_lev_final, gross_lev_cap_binding
   - Clear semantics for each stage

**7. Added comprehensive invariant checks**:
   - idm_raw ≥ 1.0 (Carver-style)
   - idm_applied ≤ idm_cap (by definition of min)
   - idm_applied ≥ 1.0 (multiplier property)
   - gross_lev_final ≤ gross_lev_cap (enforced)
   - idm_final ≈ idm_raw (conditional on uniform scaling)

**8. Updated all documentation**:
   - Module-level docstrings explain Carver-style flow
   - Function docstrings updated for clarity
   - Test documentation explains new behavior

### Verification Required

**DO NOT mark as FULLY RESOLVED until backtests pass:**

**Phase 1 Backtest** (2020-2024, N=4):
```bash
PYTHONPATH=. python systems/crypto_perps/system.py \
  --config config/crypto_perps_baseline_v1.yaml \
  --data data/example_crypto_perps_5yr.parquet \
  --outdir out/stage1_carver_idm
```

Expected outcomes:
- Backtest completes without errors
- All invariants hold (idm_raw ≥ 1.0, idm_applied ≤ cap, gross_lev ≤ cap)
- Mean IDM ≥ 1.0 (Carver-style)
- Gross leverage behavior consistent with IDM multiplier

**Phase 2 Backtest** (2021-2024, N=15):
```bash
PYTHONPATH=. python systems/crypto_perps/system.py \
  --config config/crypto_perps_phase2_v1.yaml \
  --data data/example_crypto_perps_15x4yr.parquet \
  --outdir out/phase2_carver_idm
```

Expected outcomes:
- Backtest completes without errors
- All invariants hold
- Mean IDM ~1.5-2.0 (up from 0.902 with bug)
- All daily IDM values ≥ 1.0
- Gross leverage higher than old implementation (due to IDM multiplier benefit)
- IDM multiplier used most days (idm_applied > 1.0)

**After backtests pass**:
1. Mark all issues as RESOLVED in BUG_REPORT_IDM_CONSTRAINTS.md
2. Update Phase 2 analysis with corrected metrics
3. Document IDM multiplier impact on performance
4. Archive investigation documents

### Files Modified

**Core Implementation** (constraints.py):
- `IncrementalConstraintsEngine.step()`: Carver-style IDM multiplier logic
- `apply_idm_cap()`: Deprecated with warning
- `apply_portfolio_constraints()`: Updated to match incremental logic
- `calculate_idm()`: Updated docstring
- `calculate_portfolio_stdev()`: Updated docstring
- Module-level docstring: Added comprehensive overview

**System Loop** (system.py):
- Updated invariant checks
- Updated comments for clarity
- Extended diagnostics collection

**Tests**:
- `test_idm_scale_invariance.py`: Added new tests for Carver-style multiplier
- `test_idm_definition.py`: Updated to verify fix
- `test_incremental_constraints.py`: Updated to handle 4-value return

**Documentation**:
- `IDM_RECONCILIATION.md`: This file (added implementation status)
- `BUG_REPORT_IDM_CONSTRAINTS.md`: Updated (see below)

### Next Steps

1. **Run Phase 1 backtest** to verify basic correctness
2. **Run Phase 2 backtest** to verify economic outcomes
3. **Analyze diagnostics** to confirm IDM behavior
4. **Update BUG_REPORT_IDM_CONSTRAINTS.md** with verification results
5. **Archive investigation** if all tests pass

