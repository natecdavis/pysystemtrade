# Phase 1 MVP - OI Overlay & Crowding Signals - READY FOR TESTING

## ✅ Implementation Complete

All Phase 1 components have been implemented and verified. The system is ready for comprehensive backtesting.

## Summary of Changes

### New Files (7)
1. **`systems/crypto_perps/crypto_portfolio_oi_overlay.py`** (153 lines)
   - `CryptoPortfolioWithOIOverlay` - Static portfolio with OI overlay
   - `CryptoDynamicPortfolioWithOIOverlay` - Dynamic portfolio with OI overlay
   - `apply_oi_overlay()` - Helper function for code reuse

2. **`config/crypto_perps_oi_baseline.yaml`** (649 lines)
   - Test config: No OI overlay, no relcarry (reproduces baseline Sharpe 0.99)

3. **`config/crypto_perps_oi_overlay_only.yaml`** (649 lines)
   - Test config: OI overlay enabled, no relcarry (defensive overlay test)

4. **`config/crypto_perps_oi_crowding_only.yaml`** (649 lines)
   - Test config: No OI overlay, relcarry enabled (contrarian alpha test)

5. **`config/crypto_perps_oi_test.yaml`** (649 lines)
   - Test config: Both OI overlay and relcarry (full Phase 1 test)

6. **`scripts/run_oi_mvp_tests.sh`** (93 lines, executable)
   - Automated test runner for all 4 scenarios
   - Runtime: ~20 minutes total

7. **`scripts/verify_oi_implementation.py`** (297 lines, executable)
   - Verification suite (5 tests)
   - Status: ✅ All tests passed

### Modified Files (3)
1. **`sysdata/crypto/parquet_perps_sim_data.py`** (+87 lines)
   - Added `get_oi_regime_multiplier()` method
   - Uses funding rate z-score as OI proxy (Phase 1)
   - Returns position multiplier ∈ [min_scale, 1.0]

2. **`scripts/run_dynamic_universe_backtest.py`** (+15 lines)
   - Conditional portfolio stage selection based on `use_oi_overlay` flag
   - Supports both static and dynamic universe modes

3. **`config/crypto_perps_full_rules.yaml`** (+32 lines)
   - Added `use_oi_overlay: false` flag (disabled by default)
   - Added `oi_overlay_params:` configuration section
   - Documentation for Phase 1 testing

### Documentation (2)
1. **`OI_OVERLAY_IMPLEMENTATION.md`** (full implementation guide)
2. **`PHASE1_READY.md`** (this file)

## Quick Start - Running Tests

### Option 1: Automated Test Suite (Recommended)
```bash
./scripts/run_oi_mvp_tests.sh
```
- Runs all 4 test scenarios sequentially
- Total runtime: ~20 minutes
- Results saved to `out/oi_mvp/`

### Option 2: Individual Tests
```bash
# Baseline (current system)
python scripts/run_dynamic_universe_backtest.py \
  --config config/crypto_perps_oi_baseline.yaml \
  --data data/dataset_538registry_6yr_jagged.parquet \
  --outdir out/oi_mvp/baseline

# OI overlay only
python scripts/run_dynamic_universe_backtest.py \
  --config config/crypto_perps_oi_overlay_only.yaml \
  --data data/dataset_538registry_6yr_jagged.parquet \
  --outdir out/oi_mvp/overlay_only

# Crowding only
python scripts/run_dynamic_universe_backtest.py \
  --config config/crypto_perps_oi_crowding_only.yaml \
  --data data/dataset_538registry_6yr_jagged.parquet \
  --outdir out/oi_mvp/crowding_only

# Combined (full Phase 1)
python scripts/run_dynamic_universe_backtest.py \
  --config config/crypto_perps_oi_test.yaml \
  --data data/dataset_538registry_6yr_jagged.parquet \
  --outdir out/oi_mvp/combined
```

## Expected Results

### Baseline
- **Sharpe:** 0.99 (current system)
- **MaxDD:** -23.9%
- **CAGR:** 21.7%

### OI Overlay Only
- **Sharpe:** 0.96-0.98 (slightly lower, defensive overlay)
- **MaxDD:** -22.5% to -22.9% (1-1.4% improvement)
- **Crisis protection:** Better returns during May 2021, Jun 2022, Nov 2022

### Crowding Only
- **Sharpe:** 0.99-1.01 (neutral to +1%, contrarian alpha)
- **IC@5d:** < -0.05 (negative = contrarian working)
- **Correlation:** < 0.3 with trend signals (orthogonal alpha)

### Combined
- **Sharpe:** 1.00-1.04 (+1-5% improvement)
- **MaxDD:** -22.0% to -22.9% (1-2% improvement)
- **Crisis returns:** +15-25% vs baseline

## Success Criteria (Phase 1)

Phase 1 is considered successful if **any** of these conditions are met:

✅ **Primary Goal:** Combined Sharpe ≥ 1.00 (+1% minimum)
✅ **Alternative 1:** OI overlay reduces MaxDD by ≥1% with Sharpe ≥ 0.97
✅ **Alternative 2:** Crowding adds alpha with Sharpe ≥ 1.00 and IC < -0.05

**If successful → Proceed to Phase 2 (true OI data acquisition)**
**If unsuccessful → Keep as optional feature or abandon**

## Verification Status

All pre-flight checks passed:

✅ Config files load correctly
✅ OI regime multiplier returns valid values (no NaN)
✅ Portfolio classes instantiate properly
✅ Helper function signature correct
✅ Config variations match expected parameters

```
VERIFICATION SUMMARY
================================================================================
✓ PASS: Config Loading
✓ PASS: Data Method
✓ PASS: Portfolio Classes
✓ PASS: Helper Function
✓ PASS: Config Variations

✓✓✓ ALL TESTS PASSED ✓✓✓
```

## Key Features Implemented

### 1. OI Regime Overlay (Defensive)
- **Type:** Portfolio-level position scaling
- **Trigger:** |funding_z_score| > threshold (default 2.0σ)
- **Effect:** Scale positions down to 50-100% of base size
- **Goal:** Reduce drawdown during leverage cascades

### 2. Crowding Signal (Contrarian)
- **Type:** Standalone trading rule (relcarry)
- **Logic:** Fade instruments with extreme funding vs median
- **Weight:** 6% total (3 rules × 2% each)
- **Goal:** Contrarian alpha from positioning extremes

### 3. Graceful Degradation
- If no funding data → multiplier = 1.0 (no scaling)
- If insufficient history → uses min_periods=30
- NaN values filled with 1.0 (no scaling)
- Never crashes, always returns valid positions

## What Happens Next

### After Running Tests

1. **Compare metrics across all 4 runs:**
   - Sharpe ratio (primary metric)
   - Max drawdown (defensive overlay goal)
   - CAGR vs volatility (risk-adjusted returns)
   - Turnover increase (check for excessive trading)

2. **Compute signal ICs:**
   - Relcarry IC@5d (should be negative)
   - Correlation with trend signals (should be < 0.3)

3. **Crisis event analysis:**
   - May 2021 crash (BTC -30% in 24h)
   - June 2022 liquidations (3AC, Celsius)
   - Nov 2022 FTX collapse
   - Did OI overlay protect? Did crowding profit?

4. **Decision:**
   - If successful → Proceed to Phase 2
   - If unsuccessful → Debug or abandon

### Phase 2 (If Phase 1 Succeeds)

**Timeline:** 2-3 weeks

**Data Acquisition:**
- Binance Public Data Archive (free) OR Tardis.dev ($50-200/month)
- Add `open_interest`, `long_short_ratio` columns to dataset

**Implementation:**
- Extend `binance_api.py` with OI fetch methods
- Update `get_oi_regime_multiplier()` to use true OI/Volume ratio
- Implement `crowding_indicator()` using LS ratio
- Test liquidation proximity signal (Phase 3 preview)

**Expected Improvement:**
- True OI outperforms funding proxy by +0.5% Sharpe
- LS ratio crowding IC < -0.10 (stronger contrarian signal)
- Combined Sharpe ≥ 1.02 (+3% vs baseline)

## Contact / Support

For questions or issues:
- Read `OI_OVERLAY_IMPLEMENTATION.md` for detailed implementation guide
- Run `python scripts/verify_oi_implementation.py` to diagnose problems
- Check plan document for full Phase 1/2/3 roadmap

---

**Status:** ✅ READY FOR TESTING
**Date:** 2026-02-20
**Implementation:** Phase 1 MVP (funding-based proxy)
**Next Step:** `./scripts/run_oi_mvp_tests.sh`
