# Implementation Summary: Trend-Gated Vol-Normalized Carry

## Overview

Successfully implemented a trend-gated carry system for crypto perpetuals backtesting. The system treats carry as a **conditional modifier** on trend exposure rather than an independent alpha source, addressing the negative IC (IC@5d = -0.009) of traditional carry rules.

**Status:** ✅ Implementation complete, validation passed, ready for testing

---

## What Was Implemented

### 1. Vol-Normalized Carry Rule

**File:** `systems/crypto_perps/rules/rule_library.py`

**New function:** `vol_normalized_carry(funding_rates, vol, smooth_days, vol_floor)`

**What it does:**
- Smooths funding rate with EWM (smooth_days parameter)
- Annualizes: F_t = f_smooth × 3 × 365 (for 8-hourly funding payments)
- Vol-normalizes: C_t = -F_t / σ_t (negated so high positive funding → short bias)
- Returns raw carry score (percentile-ranked in ForecastCombine stage)

**Three variations created:**
- `vol_norm_carry_10`: 10-day smoothing (faster response)
- `vol_norm_carry_30`: 30-day smoothing (balanced)
- `vol_norm_carry_60`: 60-day smoothing (slower, smoother)

---

### 2. Trend-Gated ForecastCombine

**File:** `systems/crypto_perps/forecast_combine_gated.py`

**New class:** `ForecastCombineGated(ForecastCombine)`

**Key methods:**

1. **`get_combined_forecast()`** - Main override
   - Calculates trend strength (sum of trend rule forecasts)
   - Applies cross-sectional percentile ranking to carry scores
   - Gates carry: zeros when |trend| < threshold OR sign(trend) ≠ sign(carry)
   - Blends: final = trend + (carry_weight × carry_gated)
   - Applies FDM and capping as usual

2. **`_apply_percentile_ranking_to_carry()`** - Cross-sectional ranking
   - For each date, ranks this instrument vs all other instruments
   - Maps percentile (0-1) to forecast (-20 to +20)
   - Formula: forecast = 40 × (percentile - 0.5)

3. **Diagnostic methods** (for analysis):
   - `get_trend_strength()`: Sum of trend rule forecasts
   - `get_raw_carry()`: Sum of carry forecasts before ranking
   - `get_ranked_carry()`: Carry after percentile ranking but before gating
   - `get_gated_carry()`: Final carry after trend gating

---

### 3. System Integration

**File:** `scripts/run_dynamic_universe_backtest.py`

**Changes:**
- Added import: `from systems.crypto_perps.forecast_combine_gated import ForecastCombineGated`
- Conditionally uses `ForecastCombineGated` when `use_gated_carry: true` in config
- Logs which combiner is active: "Using trend-gated carry combination" or "Using standard forecast combination"

**Logic:**
```python
use_gated_carry = config.get_element_or_default('use_gated_carry', False)
if use_gated_carry:
    combiner = ForecastCombineGated()
else:
    combiner = ForecastCombine()
```

---

### 4. Configuration Files

#### Baseline Config: `config/crypto_perps_full_rules.yaml`

**Added sections:**

1. **Trading rules** (after residual_momentum rules):
   ```yaml
   vol_norm_carry_10:
     function: systems.crypto_perps.rules.rule_library.vol_normalized_carry
     data:
       - "data.get_funding_rate"
       - "rawdata.daily_returns_volatility"
     other_args: {smooth_days: 10, vol_floor: 0.01}
   # ... vol_norm_carry_30, vol_norm_carry_60
   ```

2. **Forecast weights** (disabled by default):
   ```yaml
   vol_norm_carry_10:  0.0
   vol_norm_carry_30:  0.0
   vol_norm_carry_60:  0.0
   ```

3. **Gating parameters** (new section after forecast_cap):
   ```yaml
   use_gated_carry: false
   carry_weight: 0.2
   carry_trend_gate_threshold: 1.0
   trend_rule_list: [ewmac_8, ewmac_16, ..., residual_momentum_64]
   carry_rule_list: [vol_norm_carry_10, vol_norm_carry_30, vol_norm_carry_60]
   ```

#### Test Config: `config/crypto_perps_gated_carry_test.yaml`

**Key differences from baseline:**
- `use_gated_carry: true` (ENABLED)
- Carry weights: 0.01 each (3% total, vs 0.0 in baseline)
- Same gating parameters (carry_weight: 0.2, threshold: 1.0)

---

### 5. Testing Tools

#### Validation Script: `scripts/validate_gated_carry.py`

**Purpose:** Quick smoke test to verify implementation is correct

**Tests:**
- ✓ Imports work (vol_normalized_carry, ForecastCombineGated)
- ✓ Config files are correctly defined
- ✓ vol_normalized_carry function executes without errors
- ✓ ForecastCombineGated can be instantiated

**Usage:**
```bash
python scripts/validate_gated_carry.py
```

**Status:** ✅ All tests passed

---

#### Parameter Sweep Script: `scripts/sweep_carry_params.py`

**Purpose:** Test combinations of carry_weight and carry_trend_gate_threshold

**Grid:**
- carry_weight: [0.0, 0.1, 0.2, 0.3]
- threshold: [0.5, 1.0, 1.5, 2.0]
- Total: 16 backtests

**Usage:**
```bash
python scripts/sweep_carry_params.py \
  --base-config config/crypto_perps_full_rules.yaml \
  --data data/dataset_538registry_6yr_jagged.parquet \
  --outdir out/carry_sweep
```

**Outputs:**
- `out/carry_sweep/wc0.0_th0.5/` - First config (baseline)
- `out/carry_sweep/wc0.2_th1.0/` - Default gating config
- ...
- `out/carry_sweep/sweep_summary.csv` - Comparison table

---

#### Testing Guide: `TESTING_GUIDE_GATED_CARRY.md`

**Purpose:** Complete testing protocol with success criteria

**Sections:**
1. Overview and hypothesis
2. Implementation summary
3. Running tests (baseline, gated, ungated, sweep)
4. Interpreting results
5. Decision framework (adopt, keep optional, or disable)
6. Common issues and debugging
7. Next steps after testing

---

## Architecture Overview

```
Layer 1: Vol-Normalized Carry Rule
         ↓ (returns raw carry score)
Layer 2: Trend-Gated Combination
         ↓ (percentile ranks, gates, blends)
Layer 3: Standard Position Sizing
```

**Gating logic:**
```
For each date:
  1. Calculate trend strength = Σ(trend rule forecasts)
  2. Rank carry scores across instruments → percentile → forecast ∈ [-20, +20]
  3. If |trend| < threshold OR sign(trend) ≠ sign(carry):
       carry_gated = 0  (gate active)
     Else:
       carry_gated = carry_ranked
  4. final_forecast = trend + (carry_weight × carry_gated)
```

---

## Testing Status

### Validation Tests: ✅ PASSED

```
✓ PASS   Imports
✓ PASS   Config Files
✓ PASS   Rule Function
✓ PASS   ForecastCombineGated
```

### Backtests: ⏳ NOT YET RUN

**Next steps:**

1. **Verify baseline** (ensure Sharpe 0.84):
   ```bash
   python scripts/run_dynamic_universe_backtest.py \
     --config config/crypto_perps_full_rules.yaml \
     --data data/dataset_538registry_6yr_jagged.parquet \
     --outdir out/carry_test/baseline_no_carry
   ```

2. **Test gated carry** (default parameters):
   ```bash
   python scripts/run_dynamic_universe_backtest.py \
     --config config/crypto_perps_gated_carry_test.yaml \
     --data data/dataset_538registry_6yr_jagged.parquet \
     --outdir out/carry_test/gated_wc0.2_th1.0
   ```

3. **Compare results:**
   - Target: Sharpe 0.84 → 0.86+ (2.4% improvement)
   - Acceptable: Sharpe ≥ 0.84 (neutral or positive)
   - Investigate: Sharpe < 0.84 (degradation)

---

## Configuration Parameters

### Main Toggle

**`use_gated_carry`**: `true` / `false`
- Controls whether to use ForecastCombineGated (gated) or ForecastCombine (standard)
- Default: `false` (disabled in baseline)
- Test: `true` (enabled in test config)

### Gating Parameters

**`carry_weight`**: `0.1` to `0.3` (typical range)
- Additive blending weight for carry sleeve
- Applied AFTER gating: final = trend + (carry_weight × carry_gated)
- Default: `0.2`
- Higher → more carry influence, lower → more trend purity

**`carry_trend_gate_threshold`**: `0.5` to `2.0` (typical range)
- Minimum absolute trend strength to allow carry
- |trend| < threshold → carry zeroed (weak trend)
- Default: `1.0`
- Higher → stricter gating, lower → more permissive

### Rule Lists

**`trend_rule_list`**: List of rule names considered "trend"
- Used to calculate trend strength (sum of these rule forecasts)
- Default: 19 rules (ewmac, breakout, normmom, accel, assettrend, relmomentum, residual_momentum)

**`carry_rule_list`**: List of rule names considered "carry"
- Used to identify carry forecasts for ranking and gating
- Default: 3 rules (vol_norm_carry_10, vol_norm_carry_30, vol_norm_carry_60)

---

## File Inventory

### New Files (Created)

1. **`systems/crypto_perps/forecast_combine_gated.py`** (335 lines)
   - ForecastCombineGated class with trend-gating logic
   - 4 diagnostic methods for analysis

2. **`config/crypto_perps_gated_carry_test.yaml`** (580 lines)
   - Test config with carry enabled (3% weight)

3. **`scripts/sweep_carry_params.py`** (180 lines)
   - Parameter sweep script (16 backtests)

4. **`scripts/validate_gated_carry.py`** (200 lines)
   - Validation script (smoke tests)

5. **`TESTING_GUIDE_GATED_CARRY.md`** (500+ lines)
   - Complete testing protocol

6. **`IMPLEMENTATION_SUMMARY_GATED_CARRY.md`** (this file)
   - Implementation overview and reference

### Modified Files

7. **`systems/crypto_perps/rules/rule_library.py`**
   - Added `vol_normalized_carry()` function (30 lines)

8. **`scripts/run_dynamic_universe_backtest.py`**
   - Added ForecastCombineGated import and conditional logic (10 lines)

9. **`config/crypto_perps_full_rules.yaml`**
   - Added carry rule definitions (30 lines)
   - Added carry forecast weights (3 lines, all 0.0)
   - Added gating configuration section (50 lines)

10. **`.claude/rules/current-work.md`**
    - Updated with implementation summary

---

## Expected Performance

### Baseline (No Carry)

- **Sharpe:** 0.84
- **CAGR:** 14.4%
- **Vol:** 17.9%
- **Turnover:** 15.3x
- **Rules:** 19 (no carry)

### Gated Carry (Target)

- **Sharpe:** ≥0.86 (2.4% improvement target)
- **CAGR:** Similar or higher
- **Vol:** Similar
- **Turnover:** ≤20x (carry should add confirmation, not churn)
- **Rules:** 22 (19 trend + 3 carry)

### Success Criteria

**Primary:**
- Sharpe ≥ 0.86
- Turnover ≤ 20x
- No errors in carry calculation

**Secondary:**
- Gated Sharpe > Ungated Sharpe (validates gating benefit)
- Max drawdown ≤ baseline
- Transaction costs ≤ 40 bps/year

---

## Decision Framework

### If Sharpe ≥ 0.86 (Success)

**Action:** Adopt as default configuration

**Config changes:**
```yaml
# In crypto_perps_full_rules.yaml, update:
use_gated_carry: true
forecast_weights:
  vol_norm_carry_10: 0.01
  vol_norm_carry_30: 0.01
  vol_norm_carry_60: 0.01
```

**Next steps:**
- Document optimal parameters in current-work.md
- Consider testing more carry variations (longer smoothing windows)
- Monitor carry contribution in future backtests

---

### If Sharpe ~0.84 (Neutral)

**Action:** Keep as optional feature

**Rationale:**
- No harm from gating (Sharpe unchanged)
- May provide value in specific regimes
- Can be toggled via config for experimentation

**Config:** Leave baseline as-is (disabled), keep test config for future use

---

### If Sharpe <0.84 (Degradation)

**Action:** Investigate and likely disable

**Debug steps:**
1. Check diagnostics (carry forecasts, gating activity)
2. Test stricter gating (higher threshold)
3. Test lower carry weight (w_c=0.1)
4. Compare by regime (high/low funding, trending/ranging)

**If still negative:** Disable carry (keep weights at 0.0), document findings

---

## Quick Start

### 1. Validate Implementation

```bash
python scripts/validate_gated_carry.py
```

Expected: All tests pass ✅

---

### 2. Run Baseline

```bash
python scripts/run_dynamic_universe_backtest.py \
  --config config/crypto_perps_full_rules.yaml \
  --data data/dataset_538registry_6yr_jagged.parquet \
  --outdir out/carry_test/baseline_no_carry
```

Expected: Sharpe 0.84

---

### 3. Run Gated Carry Test

```bash
python scripts/run_dynamic_universe_backtest.py \
  --config config/crypto_perps_gated_carry_test.yaml \
  --data data/dataset_538registry_6yr_jagged.parquet \
  --outdir out/carry_test/gated_wc0.2_th1.0
```

Target: Sharpe ≥ 0.86

---

### 4. Compare Results

```bash
# Check baseline Sharpe
cat out/carry_test/baseline_no_carry/metadata.json | grep sharpe

# Check gated Sharpe
cat out/carry_test/gated_wc0.2_th1.0/metadata.json | grep sharpe

# Compare (should see improvement)
```

---

### 5. (Optional) Run Parameter Sweep

```bash
python scripts/sweep_carry_params.py \
  --base-config config/crypto_perps_full_rules.yaml \
  --data data/dataset_538registry_6yr_jagged.parquet \
  --outdir out/carry_sweep

# View results
cat out/carry_sweep/sweep_summary.csv
```

---

## Key Insights

### Why Gating Works

**Problem:** Traditional carry has negative IC because funding reflects trend positioning
- High positive funding → long positioning → trend up
- Carry says "short" (receive funding) but trend says "long"
- Fighting trend → loses money

**Solution:** Gate carry by trend direction
- Only allow carry when it agrees with trend
- Carry becomes confirmation signal, not contrarian bet
- Sign(carry) = sign(trend) → reinforcement
- Sign(carry) ≠ sign(trend) → zeroed out

### Architecture Design Choices

**Why percentile ranking?**
- Raw carry scores vary widely across instruments
- Percentile ranking normalizes to [-20, +20] forecast range
- Ensures fair cross-sectional comparison

**Why additive blending?**
- Trend rules keep original weights (97% total)
- Carry added as small sleeve (3% weight × carry_weight)
- Allows independent control of carry influence

**Why custom ForecastCombine?**
- Can't create rule that depends on combined trend forecast (circular dependency)
- ForecastCombine stage has access to all rule forecasts simultaneously
- Natural place to implement cross-sectional ranking and gating

---

## References

- **Testing Guide:** `TESTING_GUIDE_GATED_CARRY.md`
- **Validation Script:** `scripts/validate_gated_carry.py`
- **Sweep Script:** `scripts/sweep_carry_params.py`
- **Current Work:** `.claude/rules/current-work.md`

---

## Contact / Questions

For issues or questions about this implementation:

1. Check `TESTING_GUIDE_GATED_CARRY.md` for common issues
2. Run `scripts/validate_gated_carry.py` to diagnose problems
3. Review diagnostics from failed backtests
4. Check system logs for "trend-gated carry" messages

---

**Status:** ✅ Implementation complete, ready for testing

**Last Updated:** 2026-02-20
