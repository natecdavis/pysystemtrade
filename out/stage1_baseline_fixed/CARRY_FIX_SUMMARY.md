# Carry Forecast Fix - Summary Report

## Bug Fixed ✅

**Location:** `systems/crypto_perps/system.py:199-200`

**Problem:** The `rule_weights` config parameter was not being passed to `process_all_forecasts()`, causing all backtests to use default equal weights regardless of config.

**Fix Applied:**
```python
# BEFORE (broken):
combined_forecasts = process_all_forecasts(ewmac, carry, relmom_forecasts=relmom)

# AFTER (fixed):
rule_weights = config.get('forecasts', {}).get('rule_weights')
combined_forecasts = process_all_forecasts(ewmac, carry, relmom_forecasts=relmom, rule_weights=rule_weights)
```

---

## Validation Results ✅

### Before Fix (Bug Present)
- **Baseline:** $34,667.94 final equity (+593.36%)
- **Carry-off:** $34,667.94 final equity (+593.36%)
- **Delta:** $0.00 (0.00%) ← **Identical results confirmed bug**

### After Fix (Bug Resolved)
- **Baseline:** $34,667.94 final equity (+593.36%)
- **Carry-off:** $37,021.92 final equity (+640.44%)
- **Delta:** +$2,353.98 (+6.79%) ← **Non-zero delta confirms fix works**

---

## Key Findings

### 1. Fix Validation ✅
- **Before fix:** Baseline == carry-off ($0 delta) → bug confirmed
- **After fix:** Carry-off > baseline ($2,354 delta) → fix confirmed
- **Position differences:** 97-99% of days show position changes between scenarios
- **Fix is working correctly**

### 2. Carry Forecast is Counter-Productive ⚠️

**Performance Impact:**
- Removing carry **improves** returns by +47 percentage points (+6.79% relative)
- Costs decrease from $894 → $745 (carry increases turnover)

**Why Carry Hurts:**
- **Carry opposes EWMAC trend 62.5% of the time**
- When carry disagrees with trend, it:
  - Reduces position size when trend is strong (missed opportunities)
  - Increases position size against trend (amplifies losses)
  - Adds unnecessary turnover (costs)

**Example Mechanism:**
- EWMAC says: "Go long (trend is up)"
- Carry says: "Funding is rising, favor short"
- Combined: Reduced long position (worse performance in uptrend)

### 3. Position Impact Analysis

Carry affects positions on **98% of days** with material differences:

| Instrument | Mean Abs Diff | Median Abs Diff | Max Abs Diff |
|------------|---------------|-----------------|--------------|
| BTCUSDT | $735 | $578 | $3,807 |
| ETHUSDT | $533 | $400 | $4,244 |
| BNBUSDT | $568 | $399 | $5,371 |
| XRPUSDT | $429 | $307 | $2,859 |

**Interpretation:** Carry forecast has substantial position impact, but in a detrimental direction.

---

## Diagnosis Summary

### What We Learned

1. **Scaling pipeline is correct:** Each forecast scaled to target_abs=10 before combination ✅
2. **Wiring bug identified:** `rule_weights` config not passed through ❌
3. **Bug is now fixed:** Config weights are correctly applied ✅
4. **Carry signal quality is poor:** Opposes trend 62.5% of the time, hurts performance ⚠️

### Why Carry Underperforms

**Hypothesis:** Funding rates are **contrarian** to price trends in crypto:
- **Uptrends → positive funding (longs pay shorts):** Carry says "short" when trend says "long"
- **Downtrends → negative funding (shorts pay longs):** Carry says "long" when trend says "short"

This creates systematic opposition between carry and momentum.

**Root Cause:** Funding reflects current positioning/sentiment, NOT future price direction:
- High funding = overcrowded longs (but trend may continue)
- Low/negative funding = overcrowded shorts (but trend may continue)

**Economic Intuition:**
- **Carry assumes mean reversion:** Extreme funding → price reversal
- **EWMAC assumes momentum:** Price trend → continuation
- In trending crypto markets (2020-2024), momentum dominates mean reversion

---

## Recommendations

### Immediate Actions

1. ✅ **Bug is fixed** - no further action needed on wiring
2. ✅ **Fix validated** - counterfactual now works correctly

### Carry Forecast Strategy

**Option 1: Disable Carry (Recommended for Phase 1)**
```yaml
# config/crypto_perps.yaml
forecasts:
  rule_weights:
    ewmac_8_32: 0.5
    ewmac_16_64: 0.5
    carry_funding: 0.0  # Disable (current data shows it hurts)
```

**Rationale:** In trending crypto markets (2020-2024), carry is counter-productive.

**Option 2: Reduce Carry Weight**
```yaml
forecasts:
  rule_weights:
    ewmac_8_32: 0.4
    ewmac_16_64: 0.4
    carry_funding: 0.2  # Reduce from 1/3 to 1/5
```

**Rationale:** Keep carry but limit damage from poor signal quality.

**Option 3: Investigate Carry Parameters**
- Current params: fast=3d, slow=30d (very short)
- Try longer windows: fast=7d, slow=90d (smoother, less reactive)
- Hypothesis: Longer windows may capture sustained funding stress better

**Option 4: Invert Carry Signal (Research)**
```python
# Experimental: Flip carry signal
carry_signal = -(slow_ewma - fast_ewma)  # Negative of current
```

**Rationale:** If funding is contrarian, inverting it makes it trend-following.

**Option 5: Use Carry as Filter, Not Forecast (Advanced)**
- Don't combine carry with EWMAC
- Instead: Only trade when carry agrees with EWMAC
- Reduces position turnover, filters out low-conviction trades

### For Phase 2

Before expanding to N=15:
1. Re-run Stage-1 analysis with carry disabled (option 1)
2. Compare baseline vs carry-off vs constraints-off
3. Decide whether to include carry in Phase 2 universe expansion

---

## Files Modified

### Code Changes
- **`systems/crypto_perps/system.py`** (lines 199-201)
  - Added: `rule_weights = config.get('forecasts', {}).get('rule_weights')`
  - Modified: `process_all_forecasts()` now receives `rule_weights` parameter

### New Outputs
- **`out/stage1_baseline_fixed/`** - Baseline backtest (after fix)
- **`out/stage1_carry_off_fixed/`** - Carry-off backtest (after fix)
- **`out/stage1_baseline_fixed/carry_fix_validation.png`** - Validation plots
- **`scripts/validate_carry_fix.py`** - Validation script

---

## Next Steps

### Immediate
1. ✅ Bug fixed and validated
2. ✅ Carry impact quantified
3. ⏳ **Decision needed:** Keep carry, disable it, or tune parameters?

### Phase 2 Preparation
1. Re-run Stage-1 research summary with carry disabled
2. Update config recommendations
3. Document carry decision in design spec

### Research Questions (Future)
1. Is carry contrarian in all crypto regimes? (test on different periods)
2. Do longer EWMA windows (7/90 vs 3/30) improve carry signal quality?
3. Is funding predictive at longer horizons (weekly vs daily)?
4. Does carry work better in sideways markets vs trending markets?

---

## Summary

**Bug Status:** ✅ FIXED

**Fix Validation:** ✅ CONFIRMED
- Before: $0 delta (bug present)
- After: $2,354 delta (bug resolved)

**Carry Performance:** ⚠️ COUNTER-PRODUCTIVE
- Opposes trend 62.5% of time
- Reduces returns by 47 percentage points
- Recommendation: Disable or reduce weight

**Next Decision:** Should we keep carry in the system?

---

*Report generated: 2026-01-26*
*Dataset: example_crypto_perps_5yr.parquet (2020-2024, 4 instruments)*
*Config: crypto_perps_baseline_v1.yaml (after fix)*
