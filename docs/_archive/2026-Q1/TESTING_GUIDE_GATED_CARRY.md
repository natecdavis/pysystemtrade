# Testing Guide: Trend-Gated Vol-Normalized Carry

## Overview

This guide explains how to test the trend-gated carry implementation and interpret results.

**Research Question:** Can trend-gated carry improve Sharpe (current: 0.84) by providing trend confirmation signals without the negative IC of traditional carry rules?

**Hypothesis:** Gating carry by trend direction (only allowing carry when it agrees with trend) should:
- Prevent carry from fighting momentum (root cause of negative IC)
- Provide modest additive alpha as confirmation signal
- Achieve Sharpe improvement (target: 0.84 → 0.86+)

---

## Implementation Summary

### Architecture

**Layer 1: Vol-Normalized Carry Rule** (`rule_library.py:vol_normalized_carry`)
- Smooths funding rate with EWM
- Annualizes: F_t = f_smooth × 3 × 365
- Vol-normalizes: C_t = -F_t / σ_t
- Returns raw score (percentile-ranked in Layer 2)

**Layer 2: Trend-Gated Combination** (`forecast_combine_gated.py:ForecastCombineGated`)
- Calculates trend strength (sum of trend rule forecasts)
- Applies cross-sectional percentile ranking to carry scores
- Gates carry: zeros when sign(trend) ≠ sign(carry) OR |trend| < threshold
- Blends: final = trend + (carry_weight × carry_gated)

**Layer 3: Standard Position Sizing** (unchanged)

### Configuration

**Baseline Config** (`crypto_perps_full_rules.yaml`):
- 19 rules, 0% carry weight (disabled)
- `use_gated_carry: false`
- Expected Sharpe: 0.84

**Test Config** (`crypto_perps_gated_carry_test.yaml`):
- 22 rules (19 + 3 carry), 3% carry weight (1% each)
- `use_gated_carry: true`
- `carry_weight: 0.2` (additive blending weight)
- `carry_trend_gate_threshold: 1.0` (min trend strength)

---

## Running Tests

### Prerequisite: Verify Baseline

**IMPORTANT:** First verify baseline reproduces Sharpe 0.84 to ensure no regression.

```bash
python scripts/run_dynamic_universe_backtest.py \
  --config config/crypto_perps_full_rules.yaml \
  --data data/dataset_538registry_6yr_jagged.parquet \
  --outdir out/carry_test/baseline_no_carry
```

**Expected results:**
- Sharpe: 0.84
- CAGR: 14.4%
- Vol: 17.9%
- Avg Positions: 24.9

If Sharpe ≠ 0.84, stop and investigate before proceeding.

---

### Test 1: Gated Carry (Default Parameters)

Test with default gating parameters (w_c=0.2, threshold=1.0):

```bash
python scripts/run_dynamic_universe_backtest.py \
  --config config/crypto_perps_gated_carry_test.yaml \
  --data data/dataset_538registry_6yr_jagged.parquet \
  --outdir out/carry_test/gated_wc0.2_th1.0
```

**What to check:**

1. **Sharpe improvement:**
   - Compare to baseline (0.84)
   - Target: ≥0.86 (2.4% improvement)
   - Acceptable: ≥0.84 (neutral)
   - Fail: <0.84 (degradation)

2. **Turnover:**
   - Baseline: 15.3x round-trips/year
   - Gated carry should be similar or slightly lower (carry acts as confirmation)
   - Red flag: >20x (carry adding churn)

3. **Transaction costs:**
   - Baseline: ~28 bps/year
   - Should remain similar (turnover × 5 bps fee)

4. **System logs:**
   - Check for "Using trend-gated carry combination" message
   - No errors in carry rule calculation
   - Diagnostics should show non-zero carry forecasts

---

### Test 2: Ungated Carry (Comparison)

Test carry WITHOUT gating (w_c=0.2, threshold=0.0) to validate gating benefit:

**Create ungated config:**
```bash
cp config/crypto_perps_gated_carry_test.yaml config/crypto_perps_ungated_carry_test.yaml
# Edit: set carry_trend_gate_threshold: 0.0
```

**Run:**
```bash
python scripts/run_dynamic_universe_backtest.py \
  --config config/crypto_perps_ungated_carry_test.yaml \
  --data data/dataset_538registry_6yr_jagged.parquet \
  --outdir out/carry_test/ungated_wc0.2_th0.0
```

**Expected:** Sharpe should be LOWER than gated version (carry fights trend → negative IC).

---

### Test 3: Parameter Sweep (Optional)

Run full parameter grid to find optimal w_c and threshold:

```bash
python scripts/sweep_carry_params.py \
  --base-config config/crypto_perps_full_rules.yaml \
  --data data/dataset_538registry_6yr_jagged.parquet \
  --outdir out/carry_sweep
```

**Grid:**
- carry_weight: [0.0, 0.1, 0.2, 0.3]
- threshold: [0.5, 1.0, 1.5, 2.0]
- Total: 16 runs (approx 80 minutes)

**Results:** `out/carry_sweep/sweep_summary.csv`

**What to look for:**
- Sharpe peak at w_c ∈ [0.1, 0.3]
- Sharpe decreases at threshold=0.0 (no gating)
- Sharpe stable or increases with higher threshold (stricter gating)

---

## Interpreting Results

### Success Criteria

**Primary (must achieve):**
- [ ] Sharpe ≥ 0.86 (2.4% improvement over baseline)
- [ ] Turnover ≤ 20x (no excessive churn)
- [ ] Transaction costs ≤ 40 bps/year
- [ ] No errors in carry rule calculation

**Secondary (nice to have):**
- [ ] Gated > Ungated Sharpe (validates gating logic)
- [ ] Max drawdown ≤ baseline (tail risk management)
- [ ] Crisis performance (2022 bear market) ≥ baseline
- [ ] Carry contribution visible in diagnostics

---

### Diagnostic Checks

**After backtest completes, analyze diagnostics:**

```python
import pandas as pd

# Load diagnostics
diag = pd.read_parquet('out/carry_test/gated_wc0.2_th1.0/diagnostics.parquet')

# Check carry forecasts are non-zero
carry_cols = [c for c in diag.columns if 'vol_norm_carry' in c]
print(diag[carry_cols].describe())

# Check gating is active (trend strength vs carry)
# (Requires system to export combForecast diagnostics)
```

**Red flags:**
- All carry forecasts are zero → rules not firing or data missing
- Carry forecasts identical across instruments → percentile ranking failed
- Trend strength always < threshold → gating too strict

---

### Comparison Table Template

| Metric | Baseline (no carry) | Gated (w_c=0.2, th=1.0) | Ungated (w_c=0.2, th=0.0) | Winner |
|--------|---------------------|-------------------------|---------------------------|--------|
| **Sharpe** | 0.84 | ? | ? | ? |
| **CAGR** | 14.4% | ? | ? | ? |
| **Ann Vol** | 17.9% | ? | ? | ? |
| **Max DD** | -21.9% | ? | ? | ? |
| **Turnover** | 15.3x | ? | ? | ? |
| **Txn Costs** | 28 bps | ? | ? | ? |
| **Avg Pos** | 24.9 | ? | ? | ? |

---

## Decision Framework

### If Gated Carry Improves Sharpe (≥0.86)

**Action:** Adopt as default configuration

**Next steps:**
1. Update `crypto_perps_full_rules.yaml` with optimal parameters
2. Document carry weight and threshold in `.claude/rules/current-work.md`
3. Consider adding more carry variations (longer smoothing windows)
4. Monitor carry contribution in production

**Config changes:**
```yaml
use_gated_carry: true
carry_weight: 0.2  # Or optimal from sweep
carry_trend_gate_threshold: 1.0  # Or optimal from sweep

forecast_weights:
  vol_norm_carry_10: 0.01
  vol_norm_carry_30: 0.01
  vol_norm_carry_60: 0.01
```

---

### If Gated Carry Is Neutral (Sharpe ~0.84)

**Action:** Keep as optional feature (toggled via config)

**Rationale:**
- No harm from gating (Sharpe unchanged)
- May provide value in specific regimes (high funding periods)
- Can be enabled for diversification

**Documentation:**
- Note that carry is neutral when gated properly
- Document that ungated carry is negative (validates gating logic)
- Keep disabled by default to maintain simplicity

---

### If Gated Carry Degrades Sharpe (<0.84)

**Action:** Investigate root cause, likely disable

**Debugging steps:**

1. **Check diagnostics:**
   - Are carry forecasts reasonable? (not all zero, not extreme)
   - Is percentile ranking working? (scores vary cross-sectionally)
   - Is gating active? (carry zeroed when trend weak/opposite)

2. **Check data quality:**
   - Funding rate data completeness
   - Volatility calculation (should use 35-day robust_vol_calc)

3. **Test stricter gating:**
   - Increase threshold (1.0 → 2.0) to gate more aggressively
   - Test w_c=0.1 (lower carry weight)

4. **Compare by regime:**
   - High funding periods (|funding| > median)
   - Low funding periods (|funding| < median)
   - Trending vs ranging markets

**If still negative after debugging:**
- Disable carry rules (set weights to 0.0)
- Document findings in current-work.md
- Consider that carry may be fundamentally negative in crypto perps (even with gating)

---

## Common Issues

### Issue: "No carry forecasts generated"

**Symptoms:**
- All carry rule forecasts are zero or NaN
- System logs show "Could not get forecast for vol_norm_carry_*"

**Causes:**
- Missing funding rate data
- Volatility calculation failed
- Instrument doesn't have funding data

**Fix:**
- Check data.get_funding_rate() returns non-empty series
- Verify rawdata.daily_returns_volatility() works
- Check logs for data loading errors

---

### Issue: "Percentile ranking failed"

**Symptoms:**
- Carry forecasts identical across instruments
- All instruments have same percentile rank (50th)

**Causes:**
- Only 1 instrument has carry data on a given date
- Carry scores are identical (all zero or all same value)

**Fix:**
- Check carry_panel in ForecastCombineGated has ≥2 instruments per date
- Verify raw carry scores vary across instruments

---

### Issue: "Gating not working"

**Symptoms:**
- Carry forecasts non-zero even when trend is weak or opposite sign
- Log says "Using standard forecast combination" (not gated)

**Causes:**
- use_gated_carry: false in config
- ForecastCombine used instead of ForecastCombineGated
- trend_rule_list or carry_rule_list empty

**Fix:**
- Verify use_gated_carry: true in YAML
- Check system logs for "Using trend-gated carry combination"
- Verify trend_rule_list and carry_rule_list populated in config

---

## Next Steps After Testing

### If Successful (Sharpe ≥ 0.86)

1. **Optimize parameters** (optional):
   - Run sweep to find optimal w_c and threshold
   - Test smoothing windows (10d, 30d, 60d, 125d)
   - Consider adaptive carry weight based on funding regime

2. **Cross-sectional enhancements:**
   - Implement true percentile ranking across instruments (if not already working)
   - Test relative carry (vs median) instead of absolute
   - Consider sector-neutral carry (long crypto with low funding, short high funding)

3. **Production deployment:**
   - Update default config with optimal parameters
   - Add monitoring for carry contribution to P&L
   - Document carry rule rationale in CLAUDE.md

4. **Research extensions:**
   - Test carry in different market regimes (bull, bear, ranging)
   - Analyze carry performance by instrument type (majors vs alts)
   - Consider dynamic carry weight based on funding environment

---

### If Neutral or Negative

1. **Document findings:**
   - Update `.claude/rules/current-work.md` with test results
   - Note why carry doesn't work (even with gating)
   - Archive test configs for future reference

2. **Consider alternatives:**
   - Funding-based volatility adjustment (higher funding → reduce position size)
   - Funding regime filter (disable certain rules when funding extreme)
   - Carry as defensive signal (reduce exposure when funding very high)

3. **Maintain code:**
   - Keep ForecastCombineGated for future use
   - Set default weights to 0.0 (disabled)
   - Document that gating was tested and rejected

---

## Files Modified

### New Files
1. `systems/crypto_perps/rules/rule_library.py` - Added `vol_normalized_carry()` function
2. `systems/crypto_perps/forecast_combine_gated.py` - New ForecastCombineGated class
3. `config/crypto_perps_gated_carry_test.yaml` - Test config with carry enabled
4. `scripts/sweep_carry_params.py` - Parameter sweep script
5. `TESTING_GUIDE_GATED_CARRY.md` - This file

### Modified Files
6. `scripts/run_dynamic_universe_backtest.py` - Added ForecastCombineGated integration
7. `config/crypto_perps_full_rules.yaml` - Added carry rule definitions and gating params

---

## References

**Plan Document:** Plan details in previous conversation turn (before exiting plan mode)

**Key Concepts:**
- Trend-gating: Only allow carry when it agrees with trend direction
- Cross-sectional ranking: Percentile rank carry scores across instruments
- Additive blending: final = trend + (w_c × carry_gated)

**Expected Outcome:** Sharpe 0.84 → 0.86+ (2-4% improvement)

**Success Metric:** Sharpe ≥ 0.86 with turnover ≤ 20x
