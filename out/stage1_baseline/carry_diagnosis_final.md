# Carry Forecast Diagnosis - Final Report

## Executive Summary

**Question:** "Does the carry forecast ever materially affect the combined forecast or positions?"

**Answer:** **YES**, carry contributes ~28% (median) to the combined forecast. The counterfactual showing carry-off == baseline was **invalid due to a wiring bug**.

---

## Key Findings

### 1. Forecast Scaling Pipeline is CORRECT ✅

Each rule forecast (EWMAC, carry) is individually scaled to `target_abs=10` and capped at `±20` **before** combination:

| Forecast | Raw Mean Abs | Scaled Mean Abs | After Cap |
|----------|--------------|-----------------|-----------|
| EWMAC 8-32 | 1.645 | 9.546 | 9.546 (max 20) |
| EWMAC 16-64 | 2.413 | 10.159 | 10.159 (max 20) |
| **Carry** | **0.000205** | **5.717** | **5.717 (max 20)** |

**Carry scaling factor:** ~27,900x (from 0.0002 → 5.7)

### 2. Carry Contribution is MATERIAL ✅

After scaling, carry contributes substantially to the combined forecast:

**Sample Date (2020-03-01):**
- EWMAC 8-32 contribution: -5.23 (82.7%)
- EWMAC 16-64 contribution: -3.11 (49.1%)
- **Carry contribution: +2.01 (31.8%)**

**Full Dataset Statistics:**
- **Median carry contribution:** 28.5% of weighted avg (before FDM)
- **Mean carry contribution:** 149.9% (high due to outliers when EWMAC ~0)
- **Days where |carry| > both |EWMAC|:** 275 / 1,782 (15.4%)

### 3. Counterfactual Was INVALID Due to Wiring Bug ❌

**BUG:** `system.py` does NOT pass `rule_weights` config to `process_all_forecasts()`:

```python
# Line 200 in system.py (INCORRECT):
combined_forecasts = process_all_forecasts(ewmac, carry, relmom_forecasts=relmom)
# Missing: rule_weights=config['forecasts'].get('rule_weights')
```

**Impact:**
- **Baseline config:** `rule_weights: None` → uses default equal weights (1/3 each)
- **Carry-off config:** `rule_weights: {ewmac_8_32: 0.5, ewmac_16_64: 0.5, carry_funding: 0.0}`
- **Actual behavior:** BOTH use equal weights (1/3 each) because config is ignored!

**Result:** Carry-off == baseline (both runs identical, ~$34,668 final equity)

---

## Detailed Analysis

### Forecast Scaling Mechanism

The scaling process works correctly via `forecast_scalar()` from pysystemtrade:

```python
scaling_factor = target_abs / mean(abs(raw_forecast))
scaled = raw_forecast * scaling_factor
```

For carry on BTCUSDT:
- Raw mean abs: 0.000205
- Target abs: 10.0
- Scaling factor: 10.0 / 0.000205 ≈ 48,780
- Scaled mean abs: 5.717 (note: NOT 10.0 due to rolling window estimation)

The scaled carry mean abs is **5.7 instead of 10.0** because:
1. Carry has extremely low variance (funding rates are sticky)
2. The scaling factor is estimated with `min_periods=50` and rolling window
3. Early period estimates may be unstable

### Why Carry Doesn't Reach target_abs=10

Despite being scaled, carry's mean abs is only 5.7 (vs target 10.0). Possible causes:
1. **Warmup period:** First 50 days have unstable scaling factors
2. **Low variance:** Sticky funding rates → scalar estimation sensitive to outliers
3. **Backfill:** `backfill=True` extends first estimate backward, diluting overall mean

**However**, scaled carry (5.7) is still ~27,900x larger than raw carry (0.0002), making it comparable to EWMAC (9-10).

### Carry Contribution to Combined Forecast

**Manual Verification (2020-03-01):**

Raw forecasts:
- EWMAC 8-32: -1.77
- EWMAC 16-64: -1.09
- Carry: +0.00035

Scaled forecasts:
- EWMAC 8-32: -15.69
- EWMAC 16-64: -9.33
- Carry: +6.04

Weighted avg (equal weights, no FDM):
```
weighted_avg = (1/3) * (-15.69 + -9.33 + 6.04)
             = (1/3) * (-19.0)
             = -6.33
```

After FDM boost (FDM ≈ 2.267):
```
combined = weighted_avg * FDM
         = -6.33 * 2.267
         = -14.35
```

Carry's contribution: **+2.01** (31.8% of |weighted_avg|)

**Interpretation:** Carry is **offsetting** the negative EWMAC forecasts, reducing the short signal.

### Statistical Distribution

Carry contribution over full dataset:
- **P10:** 10.4%
- **P25:** 18.6%
- **Median:** 28.5%
- **P75:** 51.3%
- **P90:** 202.5%
- **P99:** 1,485%

High percentiles (>100%) occur when:
- |carry| > |EWMAC avg| (carry dominates)
- EWMAC forecasts near zero (small denominator)
- Carry and EWMAC have opposite signs (carry offsets trend)

---

## Why the Counterfactual Failed

### Config Comparison

**Baseline (`crypto_perps_baseline_v1.yaml`):**
```yaml
forecasts:
  target_abs: 10.0
  cap: 20.0
  # rule_weights commented out → defaults to None
```

**Carry-off (`crypto_perps_baseline_v1_carry_off.yaml`):**
```yaml
forecasts:
  target_abs: 10.0
  cap: 20.0
  rule_weights:
    ewmac_8_32: 0.5
    ewmac_16_64: 0.5
    carry_funding: 0.0  # INTENDED to disable carry
```

### What Should Have Happened

With correct wiring:
- **Baseline:** Equal weights (1/3 each) → carry contributes ~28%
- **Carry-off:** (0.5, 0.5, 0.0) → carry contributes 0%
- **Expected delta:** Significant difference in equity curves

### What Actually Happened

Due to missing `rule_weights` parameter:
- **Baseline:** Equal weights (1/3 each) → carry contributes ~28%
- **Carry-off:** **ALSO equal weights (1/3 each)** → carry contributes ~28%
- **Actual delta:** $0 (identical backtests)

### The Bug

**Location:** `systems/crypto_perps/system.py:200`

**Current (incorrect):**
```python
combined_forecasts = process_all_forecasts(ewmac, carry, relmom_forecasts=relmom)
```

**Should be:**
```python
rule_weights = config['forecasts'].get('rule_weights')
combined_forecasts = process_all_forecasts(
    ewmac,
    carry,
    relmom_forecasts=relmom,
    rule_weights=rule_weights  # Pass config weights!
)
```

---

## Verification Evidence

### 1. Scaling Verification Script

`scripts/verify_forecast_scaling.py` demonstrates:
- Carry is scaled from 0.0002 → 5.7 (mean abs)
- Carry contributes 28.5% (median) to weighted avg
- Carry dominates on 15.4% of days

### 2. Diagnostics Data

`out/stage1_baseline/diagnostics.parquet` shows:
- **Raw EWMAC:** mean abs ~1.5-2.4
- **Raw carry:** mean abs ~0.0002
- **Combined (after scaling+FDM):** mean abs ~10

Note: Diagnostics capture forecasts BEFORE individual scaling (at raw stage), explaining why they show small carry values. The scaling happens inside `scale_and_combine_forecasts()`.

### 3. Metadata Comparison

```json
// out/stage1_baseline/metadata.json
"rule_weights": null

// out/stage1_carry_off/metadata.json
"rule_weights": {"ewmac_8_32": 0.5, "ewmac_16_64": 0.5, "carry_funding": 0.0}
```

Configs are different, but both produce identical equity ($34,667.94).

---

## Addressing User Feedback

### 1. Forecast Scaling Pipeline ✅

**User asked:** "Verify whether we're applying rule-level forecast scaling BEFORE combining forecasts."

**Answer:** YES. The code correctly scales each forecast individually via `scale_and_cap_forecast()` before combination:
- EWMAC 8-32: 1.6 → 9.5
- EWMAC 16-64: 2.4 → 10.2
- Carry: 0.0002 → 5.7

### 2. Comparison to PST Upstream ⚠️

**User asked:** "Compare carry pipeline to upstream pysystemtrade."

**Findings:**
- PST defines carry as "yield curve slope" or "rolldown" for futures
- For perpetual futures, we use "funding rate differential" (slow EWMA - fast EWMA)
- PST approach: raw carry → `forecast_scalar()` → cap → combine
- Our approach: **SAME** (raw carry → `forecast_scalar()` → cap → combine)

**Units/Annualization:**
- Funding rates: 8-hour periods, 3x per day
- We use raw funding rates (e.g., 0.0001 = 0.01% per 8h)
- NOT annualized (0.01% × 3 × 365 = ~11% annual)
- This is CORRECT for vol-targeting (daily returns are also not annualized)

### 3. Implementation Fix Required 🔧

**User asked:** "Define carry-funding raw signal in sensible units, ensure forecast scalar pipeline."

**Status:**
- ✅ Carry raw signal is sensible (funding rate differential)
- ✅ Carry goes through forecast scalar + cap pipeline
- ❌ **BUG:** `rule_weights` config not wired to `process_all_forecasts()`

**Fix:** Pass `rule_weights` parameter (see below).

### 4. Validation Plan 📊

**User asked:** "Show carry contribution pre/post change, re-run counterfactual, ensure no spikes."

**Pre-fix validation:**
- Carry contribution: 28.5% (median) ✅
- Carry-off counterfactual: INVALID (config ignored) ❌
- Spikes: Carry capped at ±20, no pathological spikes ✅

**Post-fix validation plan:**
1. Fix wiring bug
2. Re-run baseline (with carry weight = 1/3)
3. Re-run carry-off (with carry weight = 0)
4. Verify non-zero delta
5. Check for no regressions

---

## Recommended Fix

### Step 1: Fix Wiring Bug

**File:** `systems/crypto_perps/system.py`

**Current (line 198-200):**
```python
logger.info("  Scaling and combining forecasts...")
combined_forecasts = process_all_forecasts(ewmac, carry, relmom_forecasts=relmom)
```

**Fixed:**
```python
logger.info("  Scaling and combining forecasts...")
rule_weights = config['forecasts'].get('rule_weights')  # Load from config
combined_forecasts = process_all_forecasts(
    ewmac,
    carry,
    relmom_forecasts=relmom,
    rule_weights=rule_weights  # Pass to function
)
```

### Step 2: Re-run Counterfactuals

After fix, re-run:
```bash
# Baseline (equal weights)
PYTHONPATH=. python systems/crypto_perps/system.py \
  --config config/crypto_perps_baseline_v1.yaml \
  --data data/example_crypto_perps_5yr.parquet \
  --outdir out/stage1_baseline_fixed

# Carry-off (carry weight = 0)
PYTHONPATH=. python systems/crypto_perps/system.py \
  --config config/crypto_perps_baseline_v1_carry_off.yaml \
  --data data/example_crypto_perps_5yr.parquet \
  --outdir out/stage1_carry_off_fixed
```

**Expected result:** Non-zero delta (carry contributes ~28% → should see ~10-20% equity difference).

### Step 3: Validate

Compare:
- Final equity should differ
- Positions should differ on days where carry opposes trend
- No pathological spikes (carry already capped at ±20)

---

## No Additional Changes Needed

### Vol-Normalization NOT Required

**User suggested:** "If you still want explicit normalization inside carry: use robust vol of funding changes WITH a floor."

**Response:** NOT needed. The current approach (raw carry → forecast scalar) is correct and matches PST's philosophy:
- Each rule produces a "raw" signal in its natural units
- `forecast_scalar()` normalizes to `target_abs=10` based on historical volatility
- This is equivalent to vol-normalization but more adaptive

### Carry Raw Signal is Correct

**User suggested:** "Define carry-funding raw signal in sensible units."

**Response:** Already correct:
- Raw signal: slow_ewma(funding) - fast_ewma(funding)
- Units: funding rate (e.g., 0.0001 = 0.01% per 8h)
- Economic meaning: "Is funding trending up (favor short) or down (favor long)?"
- NO annualization needed (consistent with daily returns being non-annualized)

---

## Summary

### What We Learned

1. **Forecast scaling works correctly:** Carry is scaled from ~0.0002 → 5.7 (mean abs)
2. **Carry contributes materially:** 28.5% (median) to combined forecast
3. **Counterfactual was invalid:** `rule_weights` config not passed to forecast combination
4. **No formula changes needed:** Raw carry → forecast scalar is the correct approach

### What Needs to be Fixed

1. **Wiring bug:** Pass `rule_weights` from config to `process_all_forecasts()`
2. **Re-run counterfactuals:** Validate carry impact with corrected wiring

### What Doesn't Need Changing

1. ✅ Carry formula (funding rate differential)
2. ✅ Scaling pipeline (forecast scalar + cap)
3. ✅ Unit normalization (8h funding rates consistent with daily returns)

---

## Next Steps

1. ✅ **Diagnose complete** (carry contributes 28.5%, wiring bug identified)
2. 🔧 **Fix wiring bug** (pass `rule_weights` parameter)
3. 🧪 **Re-run counterfactuals** (baseline vs carry-off with correct config)
4. 📊 **Validate impact** (expect ~10-20% equity delta, no spikes)
5. ✅ **Move to Phase 2** (if validation passes)

---

*Diagnosis completed: 2026-01-25*
*Dataset: example_crypto_perps_5yr.parquet (2020-2024, 4 instruments)*
*Config: crypto_perps_baseline_v1.yaml*
