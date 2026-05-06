# Implementation Summary: Minimum History Requirement Testing

**Date:** 2026-02-20
**Status:** ✅ Code Complete — Ready for Dataset Build & Testing

---

## Overview

Implemented configurable minimum history requirements to test whether lowering the 365-day barrier improves Sharpe by capturing high-performing instruments earlier in their lifecycle.

**Research Question:**
Does allowing instruments with less history improve risk-adjusted returns by capturing launch momentum, or does data quality matter more than early entry?

---

## What Was Implemented

### 1. **Code Modifications** ✅

#### File: `sysdata/crypto/dynamic_universe.py`
- **Added:** `MIN_HISTORY_ALL_RULES` constant (270 days)
- **Added:** `min_history_mode` parameter to `DynamicUniverseManager.__init__()`
- **Modified:** `_has_min_history()` to use `self._min_history_days` (configurable)
- **Modified:** `_get_history_filter_series()` to use `self._min_history_days`
- **Modes:**
  - `'any_rule'`: 15 days (minimum for breakout_10)
  - `'all_rules'`: 270 days (minimum for ewmac_64_256, tsmom252)

#### File: `sysdata/crypto/parquet_perps_sim_data.py`
- **Modified:** `_init_dynamic_universe()` to pass `min_history_rule_requirement` from config to `DynamicUniverseManager`

#### File: `systems/provided/crypto_example/core/dynamic_portfolio.py`
- **Added:** `min_history_days_topk` config parameter (TopK ADV calculation threshold)
- **Modified:** `_apply_top_k_selection()` to read and pass threshold to `TopKInstrumentSelector`

### 2. **Test Configuration Files** ✅

Created three test configs in `config/`:

| Config | Min History | Mode | Dataset | Expected Instruments |
|--------|-------------|------|---------|---------------------|
| `crypto_perps_test_365d_baseline.yaml` | 365 days | `'any_rule'` | `dataset_538registry_6yr_365d.parquet` | ~38 (current) |
| `crypto_perps_test_15d_any_rule.yaml` | 15 days | `'any_rule'` | `dataset_538registry_6yr_15d.parquet` | ~50-58 (+30-50%) |
| `crypto_perps_test_270d_all_rules.yaml` | 270 days | `'all_rules'` | `dataset_538registry_6yr_270d.parquet` | ~42 (+10%) |

**Key Config Parameters:**
```yaml
dynamic_universe:
  min_history_days_topk: 365  # TopK ADV calculation threshold
  min_history_rule_requirement: 'any_rule'  # DynamicUniverseManager mode
```

### 3. **Diagnostic Tool** ✅

**Script:** `scripts/analyze_min_history_impact.py`

**Features:**
- Universe composition comparison (instrument counts, active positions)
- Entry date analysis (which instruments enter earlier?)
- Rule coverage analysis (how many rules active per instrument?)
- Performance comparison (Sharpe, CAGR, turnover, drawdown)
- P&L attribution by cohort (young vs mature instruments)
- Automated recommendations based on results

**Usage:**
```bash
python scripts/analyze_min_history_impact.py \
  --baseline out/min_history_test/baseline_365d \
  --alt1 out/min_history_test/alt1_15d_any_rule \
  --alt2 out/min_history_test/alt2_270d_all_rules \
  --output out/min_history_test/ANALYSIS_REPORT.md
```

---

## Next Steps: Build Datasets & Run Tests

### Step 1: Build Test Datasets (3-6 hours)

The system is now configured, but you need to build the three datasets with different minimum history thresholds:

```bash
# Baseline (365 days) — should reproduce current results
python scripts/build_example_dataset.py \
  --source real \
  --start-date 2019-01-01 --end-date 2024-12-31 \
  --instruments-from-registry envs/dev/advisory/discovered_candidate_instruments.json \
  --min-history-days 365 \
  --allow-jagged \
  --output-path data/dataset_538registry_6yr_365d.parquet

# Alternative 1 (15 days) — early entry with partial coverage
python scripts/build_example_dataset.py \
  --source real \
  --start-date 2019-01-01 --end-date 2024-12-31 \
  --instruments-from-registry envs/dev/advisory/discovered_candidate_instruments.json \
  --min-history-days 15 \
  --allow-jagged \
  --output-path data/dataset_538registry_6yr_15d.parquet

# Alternative 2 (270 days) — moderate entry with full coverage
python scripts/build_example_dataset.py \
  --source real \
  --start-date 2019-01-01 --end-date 2024-12-31 \
  --instruments-from-registry envs/dev/advisory/discovered_candidate_instruments.json \
  --min-history-days 270 \
  --allow-jagged \
  --output-path data/dataset_538registry_6yr_270d.parquet
```

**Expected Runtime:** ~90 minutes per dataset (~4.5 hours total)

### Step 2: Run Backtests (20-30 minutes)

```bash
# Baseline verification — MUST reproduce Sharpe 0.95
python scripts/run_dynamic_universe_backtest.py \
  --config config/crypto_perps_test_365d_baseline.yaml \
  --data data/dataset_538registry_6yr_365d.parquet \
  --outdir out/min_history_test/baseline_365d

# Alternative 1: Early entry (15 days)
python scripts/run_dynamic_universe_backtest.py \
  --config config/crypto_perps_test_15d_any_rule.yaml \
  --data data/dataset_538registry_6yr_15d.parquet \
  --outdir out/min_history_test/alt1_15d_any_rule

# Alternative 2: Moderate entry (270 days)
python scripts/run_dynamic_universe_backtest.py \
  --config config/crypto_perps_test_270d_all_rules.yaml \
  --data data/dataset_538registry_6yr_270d.parquet \
  --outdir out/min_history_test/alt2_270d_all_rules
```

**Expected Runtime:** ~5-10 minutes per backtest (~15-30 minutes total)

### Step 3: Run Analysis & Generate Report

```bash
python scripts/analyze_min_history_impact.py \
  --baseline out/min_history_test/baseline_365d \
  --alt1 out/min_history_test/alt1_15d_any_rule \
  --alt2 out/min_history_test/alt2_270d_all_rules \
  --output out/min_history_test/ANALYSIS_REPORT.md
```

**Output:** Comprehensive markdown report with recommendation

---

## Expected Outcomes & Decision Criteria

### Success Criteria

**Adopt Alternative 1 (15d) if:**
- Sharpe ≥ 0.97 (≥2.1% improvement)
- Transaction cost increase <25% (cost drag ≤ 35 bps/yr)
- New instruments contribute positive net Sharpe

**Adopt Alternative 2 (270d) if:**
- Sharpe ≥ 0.96 (≥1.0% improvement)
- More stable than Alt 1 (lower turnover/drawdown)
- Broader universe diversity

**Keep Baseline (365d) if:**
- No material improvement (<1%)
- Transaction costs increase disproportionately
- Data quality issues (gaps, delisting, noise)

### Most Likely Outcome

**Hypothesis:** No improvement (keep baseline)

**Reasoning:**
- Current system already optimized (0.84 → 0.95 with carry)
- Data quality matters more than early entry
- Transaction costs higher on newer instruments
- Survivorship bias (failed coins delisted)

Carver's research emphasizes data quality over early entry. The 365-day threshold likely already captures the optimal balance.

---

## Key Design Decisions

### 1. Unified Thresholds

Used **same `min_history_days` value** across all three tiers:
- Dataset creation (`build_example_dataset.py --min-history-days`)
- TopK ADV calculation (`min_history_days_topk` in config)
- DynamicUniverseManager (`min_history_rule_requirement` mode)

**Rationale:** Simpler, easier to interpret, avoids edge cases.

### 2. Accept Registry Fallback for ADV

For instruments with <365 days, TopKSelector falls back to registry `volume_24h`.

**Trade-off:**
- Registry volume less accurate (24-hour snapshot vs rolling average)
- But allows ranking young instruments (better than excluding)
- As instrument ages past 365 days, switches to true rolling ADV

**Decision:** Accept this conservative fallback.

### 3. ForecastCombine Auto-Weighting

Instruments with 15-270 days may have only 1-15 rules active initially.

**How it works:**
- ForecastCombine auto-weights available rules
- If 5/22 rules active → each gets ~20% vs ~4.5% normally
- Missing forecasts = 0 (neutral)
- As instrument ages → more rules activate → weights rebalance

**Implication:** Young instruments concentrated in short-term rules (breakout, fast EWMAC) which may capture launch momentum better.

**Decision:** No special handling needed. Existing auto-weighting is designed for this.

---

## Verification Testing

After running backtests, verify:

1. **Baseline reproduces current results:**
   - Expected: Sharpe 0.95, CAGR 21.2%, ~25 avg positions
   - Tolerance: ±0.01 Sharpe (rounding differences)

2. **Alt 1 includes younger instruments:**
   - Check universe snapshot: instruments with 15-365 days present
   - Check diagnostics: some instruments have <22 rules active

3. **Alt 2 requires full rule coverage:**
   - Check universe snapshot: all instruments have 270+ days
   - Check diagnostics: all instruments have 22/22 rules active

4. **Config validation:**
   - Invalid `min_history_rule_requirement` → raises ValueError
   - Missing parameters → uses defaults (backward compatible)

---

## Risk Mitigation

| Risk | Mitigation |
|------|------------|
| **Data Quality** | Walk-forward cost filter still applies, TopK naturally ranks low-liquidity last |
| **Overfitting** | Test full 6-year period (multiple regimes), focus on economic rationale not just Sharpe |
| **Transaction Costs** | TopK hysteresis (entry_buffer=5, exit_buffer=10), position buffering (10%), cost filter |
| **Implementation** | Created new configs (don't modify production), verify baseline first, use defensive defaults |

---

## Files Created/Modified

### New Files
- `config/crypto_perps_test_365d_baseline.yaml` (baseline verification)
- `config/crypto_perps_test_15d_any_rule.yaml` (Alternative 1)
- `config/crypto_perps_test_270d_all_rules.yaml` (Alternative 2)
- `scripts/analyze_min_history_impact.py` (diagnostic tool)
- `IMPLEMENTATION_MIN_HISTORY_TEST.md` (this file)

### Modified Files
- `sysdata/crypto/dynamic_universe.py` (configurable threshold)
- `sysdata/crypto/parquet_perps_sim_data.py` (parameter passing)
- `systems/provided/crypto_example/core/dynamic_portfolio.py` (TopK threshold)

---

## Follow-Up Actions (If Results Are Promising)

If either alternative shows improvement:

1. **Run Parameter Sweep:**
   ```bash
   # Test intermediate thresholds: 30d, 60d, 90d, 120d, 180d
   for days in 30 60 90 120 180; do
     python scripts/build_example_dataset.py --min-history-days $days ...
     python scripts/run_dynamic_universe_backtest.py ...
   done
   ```

2. **Analyze Non-Linearity:**
   - Plot Sharpe vs min_history_days
   - Identify optimal threshold

3. **Production Deployment:**
   - Update `crypto_perps_full_rules.yaml` with optimal threshold
   - Rebuild production dataset
   - Update documentation

---

## Questions to Answer

The analysis report will answer:

1. **How many more instruments?** (total universe size)
2. **Which instruments enter earlier?** (entry date comparison)
3. **What's the rule coverage?** (how many rules active on young instruments?)
4. **Does early entry improve Sharpe?** (performance comparison)
5. **Which cohort contributes most?** (young vs mature P&L attribution)
6. **What's the cost?** (turnover, transaction costs)
7. **What's the optimal threshold?** (if results warrant sweep)

---

## Summary

✅ **Implementation Complete**
⏳ **Awaiting Dataset Build & Testing**
📊 **Next: Run Step 1 (Build Datasets)**

The code is ready. The next step is to build the three datasets and run the backtests to see if lowering the minimum history requirement improves performance.

**Recommendation:** Start with Step 1 (dataset build) and verify baseline reproduces Sharpe 0.95 before analyzing alternatives.
