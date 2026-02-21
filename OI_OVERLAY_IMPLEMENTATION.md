# OI Regime Overlay + Crowding Signals - Phase 1 MVP Implementation

## Overview

This document describes the Phase 1 MVP implementation of OI (Open Interest) regime overlay and crowding signals for the crypto perpetuals trading system.

**Goal:** Add defensive overlays and contrarian alpha sources to improve risk-adjusted returns and reduce maximum drawdown.

**Current Performance:** Sharpe 0.99, CAGR 21.7%, MaxDD -23.9%

**Target Performance (Phase 1):**
- Sharpe: ≥1.00 (+1% minimum)
- MaxDD: ≤-22.9% (1% improvement)
- Crisis returns: +15-25% vs baseline during cascade events

## What Was Implemented

### 1. OI Regime Overlay (Defensive Position Scaling)

**Purpose:** Scale down positions during periods of elevated leverage/funding (proxy for liquidation cascade risk)

**Implementation:**
- File: `systems/crypto_perps/crypto_portfolio_oi_overlay.py` (NEW, 153 lines)
- Classes: `CryptoPortfolioWithOIOverlay`, `CryptoDynamicPortfolioWithOIOverlay`
- Helper: `apply_oi_overlay()` function for code reuse

**How It Works:**
1. Calculate funding rate z-score (rolling 90-day mean/std)
2. When |z| > threshold (default 2.0σ), scale positions down
3. Multiplier range: [min_scale, 1.0] (default: [0.5, 1.0])
4. Linear interpolation between threshold and max scaling

**Example:**
- Normal funding (z=0.5): multiplier = 1.0 → no scaling
- Elevated funding (z=2.0): multiplier = 1.0 → scaling starts
- Extreme funding (z=3.0): multiplier = 0.5 → 50% position reduction
- Very extreme (z≥4.0): multiplier = 0.5 → capped at min_scale

**Phase 1 Proxy:** Uses `funding_rate` as OI proxy (no new data acquisition needed)
**Phase 2+:** Will use true OI/Volume ratio when OI data is available

### 2. Data Layer Extension

**File:** `sysdata/crypto/parquet_perps_sim_data.py` (MODIFIED, +84 lines)

**New Method:** `get_oi_regime_multiplier(instrument_code, lookback, threshold, min_scale)`
- Fetches funding rate for instrument
- Calculates annualized funding: `rate × 3 × 365`
- Computes rolling z-score
- Returns position multiplier series ∈ [min_scale, 1.0]

**Graceful Degradation:**
- If no funding data → returns 1.0 (no scaling)
- If insufficient history → uses min_periods=30
- Never errors, always returns valid multiplier

### 3. Backtest Runner Integration

**File:** `scripts/run_dynamic_universe_backtest.py` (MODIFIED, +12 lines)

**Changes:**
- Import `CryptoPortfolioWithOIOverlay` and `CryptoDynamicPortfolioWithOIOverlay`
- Conditional portfolio stage selection based on `use_oi_overlay` config flag
- Works with both static and dynamic universe modes
- Logging: indicates which portfolio stage is active

**Logic:**
```python
if use_dynamic_universe:
    if use_oi_overlay:
        portfolio_stage = CryptoDynamicPortfolioWithOIOverlay()  # Dynamic + OI
    else:
        portfolio_stage = CryptoDynamicPortfolio()  # Dynamic only
else:
    if use_oi_overlay:
        portfolio_stage = CryptoPortfolioWithOIOverlay()  # Static + OI
    else:
        portfolio_stage = CryptoPortfolios()  # Static only
```

### 4. Crowding Signal (Relcarry)

**Purpose:** Contrarian alpha from extreme positioning (fade the crowd)

**Implementation:** Already exists in `systems/crypto_perps/rules/rule_library.py`
- Rule: `relcarry(funding_rate, median_funding, smooth_days)`
- Logic: Signal = -(funding - median) / vol → fade instruments with extreme funding vs median
- Expected IC: < -0.05 (negative = contrarian)

**What Changed:** Simply enabled the rules by adding weights to config
- `relcarry_30`: 2% weight (30-day smoothing)
- `relcarry_60`: 2% weight (60-day smoothing)
- `relcarry_125`: 2% weight (125-day smoothing)
- Total: 6% allocation to crowding signals

### 5. Configuration Files

**Base Config:** `config/crypto_perps_full_rules.yaml` (MODIFIED, +32 lines)
- Added `use_oi_overlay: false` flag (disabled by default)
- Added `oi_overlay_params:` section with 3 parameters
- Documentation for Phase 1 testing approach

**Test Configs:** 4 new config files created
1. `crypto_perps_oi_baseline.yaml` - Current system (no overlay, no crowding)
2. `crypto_perps_oi_overlay_only.yaml` - OI overlay enabled, no crowding
3. `crypto_perps_oi_crowding_only.yaml` - Crowding enabled, no OI overlay
4. `crypto_perps_oi_test.yaml` - Combined (overlay + crowding)

### 6. Test Runner Script

**File:** `scripts/run_oi_mvp_tests.sh` (NEW, 93 lines, executable)

**What It Does:**
- Runs all 4 test scenarios sequentially
- Uses same dataset for fair comparison
- Total runtime: ~20 minutes (4 × 5 min)
- Outputs results to `out/oi_mvp/` subdirectories

**Usage:**
```bash
./scripts/run_oi_mvp_tests.sh
```

## Configuration Parameters

### OI Overlay Parameters

| Parameter | Range | Default | Description |
|-----------|-------|---------|-------------|
| `lookback` | 30-180 days | 90 | Rolling window for z-score calculation |
| `threshold` | 1.5-3.0 σ | 2.0 | Z-score where position scaling begins |
| `min_scale` | 0.3-0.7 | 0.5 | Minimum position multiplier (max reduction) |

### Relcarry Weights

| Rule | Default Weight | Smoothing |
|------|----------------|-----------|
| `relcarry_30` | 0.02 (2%) | 30 days |
| `relcarry_60` | 0.02 (2%) | 60 days |
| `relcarry_125` | 0.02 (2%) | 125 days |

## Testing Protocol

### Test Scenarios

**1. Baseline** (Sharpe 0.99 expected)
- Config: `crypto_perps_oi_baseline.yaml`
- OI overlay: OFF
- Relcarry: OFF (weights = 0.0)
- Purpose: Verify no regression from current system

**2. Overlay Only** (Sharpe 0.96-0.98 expected)
- Config: `crypto_perps_oi_overlay_only.yaml`
- OI overlay: ON
- Relcarry: OFF
- Purpose: Measure defensive overlay impact in isolation

**3. Crowding Only** (Sharpe 0.99-1.01 expected)
- Config: `crypto_perps_oi_crowding_only.yaml`
- OI overlay: OFF
- Relcarry: ON (weights = 0.02 each)
- Purpose: Measure contrarian alpha in isolation

**4. Combined** (Sharpe 1.00-1.04 expected)
- Config: `crypto_perps_oi_test.yaml`
- OI overlay: ON
- Relcarry: ON
- Purpose: Test full Phase 1 implementation

### Success Criteria

From the plan, Phase 1 succeeds if:

✅ **OI Overlay (Test 2 vs Test 1):**
- MaxDD reduction ≥ 1% (e.g., -23.9% → -22.5%)
- Sharpe: neutral to slightly lower (defensive overlay, not alpha)
- Crisis protection: better returns during May 2021, Jun 2022, Nov 2022 crashes

✅ **Crowding (Test 3 vs Test 1):**
- Sharpe ≥ 0.99 (at least neutral, ideally +1%)
- IC@5d < -0.05 (negative IC = contrarian working)
- Correlation with trend < 0.3 (orthogonal alpha)
- Turnover increase < 20% (not too noisy)

✅ **Combined (Test 4):**
- Sharpe ≥ 1.00 (+1% minimum vs baseline 0.99)
- MaxDD reduction ≥ 1%
- No implementation bugs (graceful NaN handling)

### Manual Checks

After running tests, verify:

1. **Reproduction:** Baseline exactly matches Sharpe 0.99 (±0.01)
2. **OI Scaling:** Check diagnostics for instruments with high funding
   - Should see position reductions during extreme funding periods
   - Check May 2021, Jun 2022, Nov 2022 (known cascade events)
3. **Relcarry IC:** Compute 5-day forward IC for relcarry signals
   - Should be negative (contrarian)
   - Should be < -0.05 to be useful
4. **Correlation:** Check correlation between relcarry and trend signals
   - Should be < 0.3 (orthogonal)

## Next Steps

### If Phase 1 Succeeds (Sharpe ≥ 1.00)

**Proceed to Phase 2:** True OI/LS Ratio Data
- Acquire historical OI data (Binance Public Data Archive or Tardis.dev)
- Extend `binance_api.py` with OI fetch methods
- Add `open_interest`, `long_short_ratio` columns to schema
- Update `get_oi_regime_multiplier()` to use true OI/Volume ratio
- Expected: OI outperforms funding proxy by +0.5% Sharpe

**Timeline:** 2-3 weeks (data acquisition 1 week, implementation 1 week, testing 3-5 days)

### If Phase 1 Fails (Sharpe < 1.00)

**Debugging steps:**
1. Check if relcarry IC is actually negative (confirming contrarian behavior)
2. Check if OI overlay actually reduced positions during known crashes
3. Try parameter sweeps:
   - OI threshold: [1.5, 2.0, 2.5, 3.0]
   - Relcarry weights: [0.01, 0.015, 0.02, 0.025]
4. Check for implementation bugs in overlay logic

**Fallback:** Keep as optional feature (doesn't hurt baseline)

## Files Modified/Created

### New Files (6)
- `systems/crypto_perps/crypto_portfolio_oi_overlay.py` (153 lines)
- `config/crypto_perps_oi_baseline.yaml` (649 lines)
- `config/crypto_perps_oi_overlay_only.yaml` (649 lines)
- `config/crypto_perps_oi_crowding_only.yaml` (649 lines)
- `config/crypto_perps_oi_test.yaml` (649 lines)
- `scripts/run_oi_mvp_tests.sh` (93 lines, executable)

### Modified Files (3)
- `sysdata/crypto/parquet_perps_sim_data.py` (+84 lines, new method)
- `scripts/run_dynamic_universe_backtest.py` (+12 lines, conditional portfolio stage)
- `config/crypto_perps_full_rules.yaml` (+32 lines, OI config section)

### Total Code Added
- Python: ~250 lines (153 new class + 84 data method + 12 runner)
- Config: ~32 lines (base config changes)
- Test configs: 4 files (copies of base with parameter changes)
- Shell script: 93 lines (automated test runner)

## Implementation Notes

### Design Decisions

**Why separate portfolio classes?**
- Keeps OI overlay logic isolated for testing
- Easy to disable (just change config flag)
- No risk of breaking baseline system
- Phase 2 can replace funding proxy with true OI without changing interface

**Why helper function pattern?**
- Avoids code duplication between static and dynamic portfolio classes
- Single source of truth for OI overlay logic
- Easy to test in isolation
- Future-proof for additional portfolio variants

**Why funding as OI proxy?**
- Zero new data acquisition (funding already in dataset)
- Funding rate correlates with leverage/OI (crowded positioning → high funding)
- Fast to implement and test (1-2 days vs 1-2 weeks for true OI data)
- Provides baseline for Phase 2 comparison

### Edge Cases Handled

1. **Missing funding data:** Returns multiplier = 1.0 (no scaling)
2. **Insufficient history:** Uses min_periods=30 for rolling stats
3. **Division by zero:** Replaces 0 std with 0.01
4. **NaN values:** fillna(1.0) ensures no NaN positions
5. **Config missing:** get_element_or_default() provides safe fallbacks

### Testing Checklist

Before considering Phase 1 complete:

- [ ] Baseline reproduces Sharpe 0.99 (±0.01)
- [ ] OI overlay reduces MaxDD by ≥1%
- [ ] Relcarry IC < -0.05 (computed separately)
- [ ] Combined Sharpe ≥ 1.00
- [ ] No NaN positions in any test run
- [ ] Crisis performance improved (May 2021, Jun 2022, Nov 2022)
- [ ] Correlation(relcarry, trend) < 0.3
- [ ] Turnover increase < 20%

---

*Implementation completed: 2026-02-20*
*Status: Ready for Phase 1 testing*
*Next step: Run `./scripts/run_oi_mvp_tests.sh`*
