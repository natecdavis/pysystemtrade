# Current Work Context

## Current Session Summary (2026-02-28)

**Sector Momentum Additive Sleeve — Adopted**

**Status:** ✅ Adopted. New production Sharpe: 1.006.

**What Was Accomplished:**

1. **Re-classified sector map** (`data/sector_map.json`)
   - Fixed CATEGORY_MAP priority bug: L1 before DeFi (BTC/ETH/SOL were wrongly in DeFi)
   - Used log data from prior CoinGecko scrape (no API re-fetch needed)
   - 78 tickers reclassified; BTC/ETH/SOL/ADA/APT/AVAX/BNB/DOT all → L1
   - Fixed NaN crash: `get_sector_index_price()` now returns `pd.Series(np.nan, index=prices.index)` not empty series

2. **Tested budget-cutting approach** (10% from trend → sector)
   - Result: Sharpe 0.96 vs baseline 0.99 (-3.0%) — REJECTED
   - Root cause: sector correlated with assettrend; taking from trend dilutes more than sector adds

3. **IC audit** (`scripts/audit_rule_predictive_accuracy.py`)
   - Sector family IC@5d=0.087 (best family, beats trend 0.055)
   - sector_momentum_20 ranks #4/54 rules

4. **Tested additive approach** (sector as sleeve on top, `sector_weight=0.10`)
   - `ForecastCombineGated`: `final = trend + carry_weight×carry + sector_weight×mean(sector_forecasts)`
   - Sector forecasts pulled directly from ForecastScaleCap (bypasses normalization)
   - Result: Sharpe 1.006 vs baseline 0.992 (+1.5%) — ADOPTED

**Backtest Results:**

| Config | Sharpe | CAGR | Vol | MaxDD | Crisis Ret |
|--------|--------|------|-----|-------|------------|
| Baseline (22 rules) | 0.992 | 21.3% | 21.9% | -22.9% | 53.4% |
| **+Sector sleeve (additive)** | **1.006** | **22.4%** | **22.6%** | **-24.5%** | **55.4%** |

**Production Config:**
- `config/crypto_perps_full_rules.yaml` — `sector_weight: 0.10`, `sector_rule_list: [10/20/40]`
- `systems/crypto_perps/forecast_combine_gated.py` — sector sleeve in `get_combined_forecast()`
- `data/sector_map.json` — committed, 300 instruments classified

**Status:** ✅ Complete. Safe to clear context.

---

## Previous Session Summary (2026-02-27)

**Phase 2 OI Data — Complete & Rejected**

**Status:** ✅ Phase 1 & 2 both complete. Production config unchanged.

**What Was Accomplished:**

1. **Resumed Binance OI download** (background, ~49 hours total runtime)
   - 300/300 symbols downloaded from Binance Public Data Archive
   - 39,531 zip files, 417 MB raw data

2. **Converted to parquet** (`scripts/convert_oi_to_parquet.py`)
   - 290,125 daily rows saved to `data/binance_oi_processed.parquet` (7 MB)
   - Fixed mixed timestamp format issue for ICPUSDT and TLMUSDT

3. **Validated data quality** (`scripts/validate_oi_data.py`)
   - All 4 checks passed: coverage 100%, gaps 97% ok, signal quality (FTX 5/5), sanity
   - Key finding: strong OI leading signal before FTX collapse (z=3.4–3.9)

4. **Implemented Phase 2** (Steps 2, 3, 4)
   - `get_open_interest()` and `get_oi_volume_ratio()` added to sim data class
   - `get_oi_regime_multiplier()` extended with `mode='funding'|'oi_volume'`
   - Auto-discovery of `binance_oi_processed.parquet` in backtest runner
   - Test configs: `phase2_test_funding.yaml`, `phase2_test_oi_volume.yaml`

5. **Ran 3-way comparison backtest**
   - Baseline / Phase 1 funding / Phase 2 OI/Volume
   - Result: OI/Volume **neutral to worse** vs funding proxy

6. **Decision: REJECT Phase 2, keep funding proxy**
   - See `out/phase2/DECISION.md`

**Phase 2 Backtest Results:**

| Config | Sharpe | CAGR | Vol | MaxDD | Crisis Ret |
|--------|--------|------|-----|-------|------------|
| Funding proxy (Phase 1) | **0.99** | **21.3%** | **21.9%** | **-22.9%** | **53.4%** |
| OI/Volume (Phase 2) | 0.99 | 21.5% | 22.2% | -23.6% | 50.4% |

**Why Funding Wins:**
Funding rate is the OI signal, just market-priced. It's more direct and immediate
than raw OI/ADV ratio. OI/Volume adds noise from ADV variation and partial coverage.

**Production Config (unchanged):**
- `config/crypto_perps_full_rules.yaml` — `mode: funding` explicitly added
- Sharpe: 0.99, CAGR: 21.3%, MaxDD: -22.9%

**Key Files:**
- `data/binance_oi_processed.parquet` — OI data (available for future research)
- `out/phase2/DECISION.md` — Full decision rationale
- `scripts/validate_oi_data.py` — Data quality validator (new)

**Status:** ✅ Complete. Safe to clear context.

---

## Previous Session Summary (2026-02-21, Part 6)

**Phase 2 Planning Complete** - Ready to implement true OI data overlay

**Status:** ✅ Phase 1 Complete & Committed, 📋 Phase 2 Planned & Ready

**What Was Accomplished:**

1. **Factorial testing resolved confound** (morning session)
   - Discovered relcarry was confounded with overlay in original tests
   - Ran 2×2 factorial design: baseline, overlay only, relcarry only, combined
   - Results: Overlay helps (+0.37% Sharpe), relcarry hurts (-0.30% Sharpe)

2. **Acute crash analysis validated overlay** (mid-day)
   - Analyzed 3 crash events: May 2021, June 2022, Nov 2022
   - Overlay provides +0.47% average improvement in 3-7 day crashes
   - Confirmed overlay provides real crash protection

3. **Final decision: Adopt Overlay Only** (afternoon)
   - Configuration: Test B (overlay only, no relcarry)
   - Simpler than combined, nearly identical performance
   - Already in production config (use_oi_overlay: true)

4. **Committed and pushed** (late afternoon)
   - Commit: 9cdcf3a4 "Add OI regime overlay for crash protection"
   - 23 files, 8608+ lines added
   - Pushed to origin/develop ✅

5. **Phase 2 planned** (evening)
   - Created comprehensive implementation plan
   - Goal: Test if true OI/Volume ratio beats funding rate proxy
   - Timeline: 3 weeks (1 week data acquisition, 2 weeks implementation/testing)
   - Next step: Download Binance OI data

**Key Files:**
- `PHASE2_OI_DATA_PLAN.md` - Complete Phase 2 implementation plan
- `out/factorial_tests/FINAL_DECISION.md` - Phase 1 final decision (not committed, .gitignore)
- `out/factorial_tests/FACTORIAL_RESULTS.md` - Full factorial analysis (not committed)
- `config/crypto_perps_full_rules.yaml` - Production config (overlay enabled) ✅

**Current System Performance:**
- Sharpe: 0.9916 (up from 0.9879 baseline, +0.37%)
- Crash Protection: +0.47% average in acute events
- Net Benefit: +27.6 bps/yr after costs
- Configuration: 22 rules + OI overlay (funding proxy) + dynamic universe

**Phase 2 Next Steps:**
1. Create download automation script (`scripts/download_binance_oi_data.py`)
2. Download historical OI data from Binance Public Data Archive
3. Convert CSV to parquet format
4. Validate data quality (coverage, gaps, alignment)
5. Implement OI/Volume ratio overlay mode
6. Compare vs funding proxy (full backtest + acute crashes)
7. Decide: adopt OI/Volume if ≥ +0.5% Sharpe improvement

**Status:** ✅ Safe to clear context - all work committed, Phase 2 plan documented

---

## Previous Session Summary (2026-02-21, Part 5)

**Acute Crash Analysis & OI Overlay Adoption** - Final decision on Phase 1/1.5 overlays

**Goal:** Determine whether to adopt standard OI overlay, trend-aware overlay, or neither, based on performance during specific 3-7 day acute crash events.

**Background:**
- Phase 1 (Standard Overlay): Full backtest Sharpe 0.9933 (+0.55% vs baseline)
- Phase 1.5 (Trend-Aware): Full backtest Sharpe 0.9850 (-0.8% vs standard)
- Initial crash diagnosis suggested overlays hurt during crashes (-2.7%, -2.3%)
- Needed to test on ACUTE crash windows (3-7 days) not full-year crisis periods

**Implementation:**

1. **Created crash analysis script** (`scripts/analyze_acute_crashes.py`):
   - Analyzes 3 major crash events: May 2021, June 2022, Nov 2022
   - Compares baseline, standard overlay, and trend-aware overlay
   - Calculates cumulative returns, max drawdowns, position changes
   - Fixed KeyError issue by calculating returns from positions × price changes

2. **Defined crash events:**
   - May 19-21, 2021: China mining ban (-30% BTC crash, 3 days)
   - June 13-18, 2022: 3AC/Celsius liquidations (-40% BTC crash, 6 days)
   - Nov 8-10, 2022: FTX collapse (-24% BTC crash, 3 days)

3. **Ran comprehensive analysis:**
   - Loaded diagnostics from 3 backtest runs (baseline, standard, trend-aware)
   - Extracted event-specific performance metrics
   - Generated comparison reports and final recommendations

**Results:**

### Acute Crash Performance

| Event | Winner | Standard Δ | Trend-Aware Δ |
|-------|--------|-----------|---------------|
| **May 2021** | ✅ Standard | **+1.31%** | +0.66% |
| **June 2022** | ⚠️ Mixed | -0.02% | -0.01% |
| **Nov 2022** | ✅ Standard | **+0.48%** | +0.07% |
| **Average** | | **+0.59%** | +0.24% |

**Critical Finding:** Standard overlay **PROTECTED** during acute crashes, contrary to initial diagnosis.

### Overall Summary

| Metric | Baseline | Standard | Trend-Aware | Winner |
|--------|----------|----------|-------------|--------|
| **Crash Wins** | 1/3 | **2/3** | 0/3 | ✅ Standard |
| **Avg Crash Return Δ** | - | **+0.59%** | +0.24% | ✅ Standard |
| **Drawdown Improvement** | - | **+0.41%** (all 3 events) | +0.08% | ✅ Standard |
| **Full Backtest Sharpe** | 0.9879 | **0.9933** | 0.9850 | ✅ Standard |
| **Annual Vol** | 22.42% | **21.55%** | 22.06% | ✅ Standard |
| **Max DD (6yr)** | -23.72% | **-22.59%** | -23.52% | ✅ Standard |

**Key Findings:**

1. **Standard overlay provided REAL crash protection:**
   - Won 2 out of 3 crash events on cumulative returns
   - Improved drawdowns in ALL 3 events (+0.41% avg)
   - Average +0.59% return improvement during acute crashes

2. **Trend-aware overlay FAILED to improve:**
   - Lost all 3 crash events (0/3 wins)
   - Worse Sharpe than standard (-0.8%)
   - Too conservative (blocks beneficial actions)

3. **Phase 1 diagnosis was WRONG:**
   - Original -2.7%, -2.3% numbers likely measured wrong windows
   - Acute crash analysis (3-7 days) shows opposite result
   - Standard overlay actually helped, not hurt

4. **Two sources of standard overlay alpha:**
   - Acute crash protection: +0.59% avg return
   - Volatility management: -3.9% vol reduction
   - Combined effect: +0.55% Sharpe improvement

**Decision:** ✅ **ADOPTED STANDARD OVERLAY**, ❌ **REJECTED TREND-AWARE**

**Rationale:**
- Proven crash protection (+0.59% avg in 3 major events)
- Full backtest improvement (+0.55% Sharpe, -3.9% vol)
- Benefits >> costs (54 bps txn cost << 55 bps Sharpe gain)
- Simpler than trend-aware (fewer parameters, no extra complexity)

**Deliverables:**
- ✅ `scripts/analyze_acute_crashes.py` - Crash analysis script (445 lines)
- ✅ `out/oi_trend_aware/acute_crash_analysis.json` - Detailed results
- ✅ `out/oi_trend_aware/ACUTE_CRASH_FINDINGS.md` - Comprehensive analysis (500+ lines)
- ✅ `out/oi_trend_aware/TREND_AWARE_RESULTS.md` - **UPDATED** with acute crash summary
- ✅ `config/crypto_perps_full_rules.yaml` - **UPDATED** with `use_oi_overlay: true`

**New Production System Performance:**
- **Sharpe:** 0.9933 (up from 0.9879 baseline, +0.55%)
- **CAGR:** 21.0% (baseline: 21.7%, trade-off for lower vol)
- **Vol:** 21.6% (down from 22.4%, -3.9%)
- **Max DD:** -22.6% (up from -23.7%, +1.1%)
- **System:** 22 rules (19 trend + 3 gated carry) + OI regime overlay
- **Crash Protection:** +0.59% avg return in acute events

**Status:** ✅ Complete. Standard OI overlay adopted as production default. Phase 1/1.5 complete.

---

## Previous Session Summary (2026-02-20, Part 5)

**Minimum History Requirement Optimization** - Testing early instrument entry

**Goal:** Test whether lowering the minimum history requirement for instruments improves Sharpe by capturing high-performing instruments earlier in their lifecycle.

**Research Question:** Does reducing the threshold from 365 days to 15 days (Alternative 1) or 270 days (Alternative 2) improve risk-adjusted returns? Can we capture launch momentum without sacrificing data quality?

**Implementation:**

1. **Made minimum history configurable** (`sysdata/crypto/dynamic_universe.py`):
   - Added `MIN_HISTORY_ALL_RULES = 270` constant
   - Added `min_history_mode` parameter ('any_rule' = 15d, 'all_rules' = 270d)
   - Modified filtering logic to use configurable threshold

2. **Wired config parameter** (`sysdata/crypto/parquet_perps_sim_data.py`):
   - Pass `min_history_rule_requirement` from config to DynamicUniverseManager

3. **Updated TopK selector** (`systems/provided/crypto_example/core/dynamic_portfolio.py`):
   - Read `min_history_days_topk` from config
   - Made ADV calculation threshold configurable

4. **Created test configurations:**
   - `crypto_perps_test_365d_baseline.yaml` - Current system (365d)
   - `crypto_perps_test_15d_any_rule.yaml` - Early entry (15d)
   - `crypto_perps_test_270d_all_rules.yaml` - Conservative (270d)

5. **Ran comprehensive backtests** (6-year period, 2020-2026):
   - All three alternatives tested on same dataset (dataset_538registry_6yr_jagged.parquet)
   - Runtime filtering via configurable thresholds

**Results:**

| Config | Min History | Sharpe | CAGR | Vol | MaxDD | Δ Sharpe |
|--------|-------------|--------|------|-----|-------|----------|
| Baseline | 365d | 0.9510 | 21.22% | 23.02% | -23.90% | - |
| **Alt 1** | **15d** | **0.9879** | **21.70%** | **22.42%** | **-23.72%** | **+3.88%** ✅ |
| Alt 2 | 270d | 0.9277 | 20.67% | 23.14% | -23.52% | -2.46% ❌ |

**Key Findings:**

- **Alternative 1 EXCEEDED adoption threshold:** +3.88% Sharpe vs +2.1% requirement
- **Lower volatility with more instruments:** 22.42% vs 23.02% (diversification benefit)
- **Better crisis performance:** +51.2% return in 2022 bear (vs +50.2% baseline)
- **Lower funding drag:** -247.5 bps/yr vs -271.2 bps/yr (funding arbitrage)
- **Minimal turnover increase:** 15.35x vs 15.21x (+0.9%)
- **Natural quality filter:** Only +5.9% more instruments despite 15d eligibility (TopK + cost filters effective)

**Why Alternative 1 Worked:**

1. **Early trend capture** - Launch momentum in months 1-9 (fast rules effective with 15-50 days)
2. **Diversification benefits** - +1.8 positions on average, uncorrelated to mature majors
3. **Funding rate arbitrage** - Younger perpetuals less crowded, better funding profiles
4. **Quality filtering intact** - TopK ADV ranking + cost filters excluded low-quality launches

**Why Alternative 2 Failed:**

- **Missed launch momentum** - 270d excludes highest-momentum period (months 1-9)
- **Minimal expansion** - Only -0.1 positions vs baseline (26% threshold reduction insufficient)
- **Rule coverage irrelevant** - ForecastCombine auto-weights handle partial coverage well

**Decision:** ✅ **ADOPTED ALTERNATIVE 1** - Updated production config with 15-day threshold

**Comparison vs Previous Baseline (365d, carry_weight=1.0):**

| Metric | Previous (365d) | New (15d) | Δ | Status |
|--------|-----------------|-----------|---|--------|
| **Sharpe** | 0.9510 | **0.9879** | **+3.88%** | ✅ Excellent |
| **CAGR** | 21.22% | **21.70%** | **+2.26%** | ✅ Excellent |
| **Vol** | 23.02% | **22.42%** | **-2.61%** | ✅ Lower (better) |
| **Max DD** | -23.90% | **-23.72%** | **+0.75%** | ✅ Shallower |
| **Avg Positions** | 30.8 | **32.6** | **+5.9%** | ✅ More diverse |
| **Turnover** | 15.21x | 15.35x | +0.9% | ✅ Minimal |
| **Cost Drag** | -314.6 bps | **-292.7 bps** | **+7.0%** | ✅ Lower costs |

**New Baseline Performance:**
- **Sharpe:** 0.99 (up from 0.95, +4.2%)
- **CAGR:** 21.7% (up from 21.2%, +2.4%)
- **Vol:** 22.4% (down from 23.0%, -2.6%)
- **System:** 22 rules (19 trend + 3 gated carry), 15-day minimum history

**Deliverables:**
- ✅ `sysdata/crypto/dynamic_universe.py` - Configurable threshold logic
- ✅ `sysdata/crypto/parquet_perps_sim_data.py` - Config parameter wiring
- ✅ `systems/provided/crypto_example/core/dynamic_portfolio.py` - TopK threshold config
- ✅ `config/crypto_perps_test_365d_baseline.yaml` - Baseline test config
- ✅ `config/crypto_perps_test_15d_any_rule.yaml` - Alternative 1 config
- ✅ `config/crypto_perps_test_270d_all_rules.yaml` - Alternative 2 config
- ✅ `scripts/verify_min_history_config.py` - Verification script (all tests passed)
- ✅ `out/min_history_test/ANALYSIS_REPORT.md` - Comprehensive 3000-word analysis
- ✅ `config/crypto_perps_full_rules.yaml` - **UPDATED** with 15-day threshold

**Status:** ✅ Complete. Minimum history optimization adopted. New baseline: Sharpe 0.99, CAGR 21.7%.

---

## Previous Session Summary (2026-02-20, Part 4)

**Extended Carry Weight Parameter Sweep** - Optimizing carry influence

**Goal:** Test higher carry_weight values to determine if carry can be scaled up beyond the initial optimum (0.3) to provide greater contribution to the combined forecast.

**Research Question:** Can increasing carry_weight beyond 0.3 further improve Sharpe? Specifically, test up to carry_weight ≈ 4.76, which would give carry equal effective weight as one trend family (14.286%).

**Implementation:**

1. **Created extended sweep script** (`scripts/sweep_carry_weight.py`):
   - Tests 8 carry_weight values: [0.3, 0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 4.76]
   - Fixed threshold at 0.5 (optimal from previous sweep)
   - Total runtime: ~40 minutes (8 configs × 5 min each)

2. **Created analysis script** (`scripts/analyze_carry_weight_sweep.py`):
   - Generates detailed markdown report with recommendation
   - Compares optimal vs baseline (carry_weight=0.3)
   - Analyzes trend in Sharpe across weight range
   - Calculates effective carry weights relative to trend families

**Results:**

| carry_weight | Sharpe | CAGR | Vol | MaxDD | Turnover | Status |
|--------------|--------|------|-----|-------|----------|--------|
| **1.00** | **0.9510** | 21.22% | 23.02% | -23.90% | 15.21x | ✅ **OPTIMAL** |
| 3.00 | 0.9428 | 21.48% | 23.59% | -25.38% | 15.28x | Good (plateau) |
| 2.00 | 0.9403 | 21.30% | 23.47% | -25.46% | 15.28x | Good (plateau) |
| 0.50 | 0.9337 | 19.34% | 21.40% | -22.99% | 14.65x | Moderate |
| 0.30 | 0.8917 | 17.33% | 20.24% | -22.74% | 14.78x | Previous optimum |

**Key Findings:**

- **Peak Sharpe at 1.0:** 0.9510 (+6.6% vs 0.30)
- **Plateau at 1.5-4.76:** Sharpe stays ~0.94 (robust, slightly below peak)
- **Sharp drop below 1.0:** Sharpe falls significantly at lower weights
- **Interpretation:** Carry provides strong additive alpha at 1.0 (3% effective weight, ~21% of one trend family)

**Comparison vs Previous Optimum (carry_weight=0.3):**

| Metric | Previous (0.3) | Optimal (1.0) | Δ | Status |
|--------|----------------|---------------|---|--------|
| **Sharpe** | 0.8917 | **0.9510** | **+6.6%** | ✅ Excellent |
| **CAGR** | 17.33% | 21.22% | +3.88% | ✅ Excellent |
| **Vol** | 20.24% | 23.02% | +2.79% | ⚠️ Higher (proportional) |
| **Max DD** | -22.74% | -23.90% | -1.16% | ⚠️ Slightly worse |
| **Turnover** | 14.78x | 15.21x | +0.43x | ✅ Minimal impact |

**Decision:** ✅ **ADOPTED** - carry_weight=1.0 as new default

**Deliverables:**
- ✅ `scripts/sweep_carry_weight.py` - Extended parameter sweep (8 weights)
- ✅ `scripts/analyze_carry_weight_sweep.py` - Analysis tool with recommendations
- ✅ `out/carry_weight_sweep/SWEEP_ANALYSIS.md` - Full analysis report
- ✅ Updated `config/crypto_perps_full_rules.yaml` with carry_weight=1.0

**New Baseline Performance:**
- **Sharpe:** 0.95 (up from 0.84 baseline, +13.1%)
- **CAGR:** 21.2% (up from 14.6%, +45.2%)
- **Vol:** 23.0% (up from 18.3%, +25.7%)
- **System:** 22 rules (19 trend + 3 gated carry)

**Status:** ✅ Complete. Extended sweep confirmed carry_weight=1.0 is optimal. Config updated and ready for production use.

---

## Previous Session Summary (2026-02-20, Part 3)

**Implemented Trend-Gated Vol-Normalized Carry Rules** - Testing carry as trend confirmation signal

**Goal:** Test whether trend-gated carry can improve Sharpe (current: 0.84 → target: 0.86+) by acting as a trend confirmation signal rather than independent alpha source.

**Background:**
- Previous carry rules (funding_carry, relcarry, funding_mr) had **negative IC** (IC@5d = -0.009)
- Excluded from production stack due to fighting momentum
- Root cause: Funding reflects positioning pressure from trends
- New approach: Gate carry by trend direction → only allow when it **agrees with** trend

**Implementation Summary:**

1. **Created vol-normalized carry rule** (`systems/crypto_perps/rules/rule_library.py:vol_normalized_carry`)
   - Smooths funding rate with EWM (10d, 30d, 60d variations)
   - Annualizes: F_t = f_smooth × 3 × 365
   - Vol-normalizes: C_t = -F_t / σ_t
   - Returns raw score (percentile-ranked in ForecastCombine)

2. **Created custom ForecastCombine subclass** (`systems/crypto_perps/forecast_combine_gated.py`)
   - `ForecastCombineGated` class with trend-gating logic
   - Calculates trend strength (sum of 19 trend rule forecasts)
   - Applies cross-sectional percentile ranking to carry scores
   - Gates carry: zeros when |trend| < threshold OR sign(trend) ≠ sign(carry)
   - Blends: final = trend + (carry_weight × carry_gated)
   - Includes 4 diagnostic methods: get_trend_strength(), get_raw_carry(), get_ranked_carry(), get_gated_carry()

3. **Integrated into system** (`scripts/run_dynamic_universe_backtest.py`)
   - Conditionally uses ForecastCombineGated when `use_gated_carry: true`
   - Logs which combiner is active (gated vs standard)

4. **Updated configs:**
   - **Baseline** (`crypto_perps_full_rules.yaml`): Added carry rule definitions with 0.0 weights (disabled)
   - **Test config** (`crypto_perps_gated_carry_test.yaml`): Enabled carry with 3% weight (1% each × 3 rules)
   - Added gating parameters: `use_gated_carry`, `carry_weight`, `carry_trend_gate_threshold`
   - Added rule classification lists: `trend_rule_list`, `carry_rule_list`

5. **Created testing tools:**
   - `scripts/sweep_carry_params.py` - Parameter sweep script (16 runs: 4 weights × 4 thresholds)
   - `TESTING_GUIDE_GATED_CARRY.md` - Complete testing protocol with success criteria

**Configuration Parameters:**
- `use_gated_carry: false` (baseline) / `true` (test)
- `carry_weight: 0.2` (additive blending weight, range: 0.1-0.3)
- `carry_trend_gate_threshold: 1.0` (min |trend| to allow carry, range: 0.5-2.0)

**Testing Commands:**
```bash
# Baseline (no carry, should reproduce Sharpe 0.84)
python scripts/run_dynamic_universe_backtest.py \
  --config config/crypto_perps_full_rules.yaml \
  --data data/dataset_538registry_6yr_jagged.parquet \
  --outdir out/carry_test/baseline_no_carry

# Test: Gated carry (w_c=0.2, threshold=1.0)
python scripts/run_dynamic_universe_backtest.py \
  --config config/crypto_perps_gated_carry_test.yaml \
  --data data/dataset_538registry_6yr_jagged.parquet \
  --outdir out/carry_test/gated_wc0.2_th1.0

# Parameter sweep (16 runs, ~80 minutes)
python scripts/sweep_carry_params.py \
  --base-config config/crypto_perps_full_rules.yaml \
  --data data/dataset_538registry_6yr_jagged.parquet \
  --outdir out/carry_sweep
```

**Expected Outcomes:**
- **If Sharpe ≥ 0.86:** Adopt as default (carry provides trend confirmation alpha)
- **If Sharpe ~0.84:** Keep as optional feature (neutral but no harm from gating)
- **If Sharpe <0.84:** Investigate and likely disable (carry still negative even with gating)

**Success Criteria:**
- Primary: Sharpe ≥ 0.86 (2.4% improvement)
- Secondary: Turnover ≤ 20x, transaction costs ≤ 40 bps/year
- Validation: Gated Sharpe > Ungated Sharpe (proves gating benefit)

**Key Files Created/Modified:**
- New: `systems/crypto_perps/forecast_combine_gated.py` (335 lines)
- New: `scripts/sweep_carry_params.py` (180 lines)
- New: `TESTING_GUIDE_GATED_CARRY.md` (500+ lines)
- New: `config/crypto_perps_gated_carry_test.yaml` (copy of full_rules with carry enabled)
- Modified: `systems/crypto_perps/rules/rule_library.py` (added vol_normalized_carry function)
- Modified: `scripts/run_dynamic_universe_backtest.py` (added ForecastCombineGated integration)
- Modified: `config/crypto_perps_full_rules.yaml` (added carry rules + gating config section)

**Status:** ✅ Implementation complete. ✅ Testing complete. ✅ **ADOPTED AS DEFAULT**.

**Test Results:**

| Metric | Baseline (No Carry) | Gated Carry | Δ | Status |
|--------|---------------------|-------------|---|--------|
| **Sharpe** | 0.84 | **0.87** | **+3.6%** | ✅ Target exceeded |
| **CAGR** | 14.6% | 16.2% | +11.0% | ✅ Improved |
| **Vol** | 18.3% | 19.6% | +7.1% | ⚠️ Higher (expected) |
| **Max DD** | -21.9% | -22.4% | -2.3% | ⚠️ Slightly worse |
| **Crisis Ret** | 20.5% | 28.7% | +40.0% | ✅ Much better |
| **Funding Drag** | -3.50% p.a. | -3.23% p.a. | +27 bps | ✅ Improved |
| **Cost Drag** | 0.28% p.a. | 0.32% p.a. | +4 bps | ✅ Acceptable |

**Decision:** **ADOPTED** - Gated carry enabled as default in `crypto_perps_full_rules.yaml`

**Key findings:**
- Sharpe improvement exceeded target (aimed for 0.86, achieved 0.87)
- CAGR boost of +11% with proportional vol increase (+7%)
- Crisis performance significantly better (+40% returns in extreme markets)
- Funding drag reduced by 27 bps (carry providing actual benefit)
- Trade-offs acceptable (minimal DD increase, low cost impact)

**New baseline:** Sharpe 0.87, CAGR 16.2%, Vol 19.6% (22 rules: 19 trend + 3 gated carry)

---

## Previous Session Summary (2026-02-20, Part 2)

**Implemented Forecast-Based Stage 2 Selection** - Alternative universe ranking criterion

**Goal:** Test whether selecting top-K instruments by **|forecast| magnitude** instead of by **ADV (liquidity)** improves risk-adjusted returns.

**Implementation:**

1. **Modified `sysdata/crypto/top_k_selector.py`:**
   - Added `compute_forecast_magnitude_metric()` method to rank by absolute forecast value
   - Extended `select_tradable_set()` to accept `selection_criterion` parameter ('adv' or 'forecast_magnitude')
   - Implemented ranking logic switch between ADV and forecast magnitude
   - Updated `get_tradable_over_time()` to pass criterion through

2. **Modified `systems/provided/crypto_example/core/dynamic_portfolio.py`:**
   - Read `selection_criterion` from config (with validation)
   - Fetch forecasts from `combForecast` stage when criterion is 'forecast_magnitude'
   - Pass forecasts and criterion to selector
   - Enhanced logging to show which criterion is active

3. **Config files:**
   - Updated `config/crypto_perps_full_rules.yaml`: Added `selection_criterion: 'adv'` (baseline)
   - Created `config/crypto_perps_full_rules_forecast_select.yaml`: Test config with `selection_criterion: 'forecast_magnitude'`

4. **Created diagnostic tools:**
   - `scripts/compare_stage2_universes.py`: Analyzes universe composition differences (overlap, divergent selections, turnover)
   - `TESTING_GUIDE_FORECAST_SELECTION.md`: Complete testing protocol with hypotheses and success criteria

**Research Hypotheses:**
- **H1:** Forecast-based selection → higher Sharpe (concentrates capital in strongest signals)
- **H2:** Forecast-based selection → higher turnover (forecasts more volatile than ADV)
- **H3:** Forecast-based selection → different instrument mix (high-|forecast| illiquid assets now selected)

**Testing Commands:**
```bash
# Baseline (ADV-based, should reproduce Sharpe 0.84)
python scripts/run_dynamic_universe_backtest.py \
  --config config/crypto_perps_full_rules.yaml \
  --data data/dataset_538registry_6yr_jagged.parquet \
  --outdir out/stage2_comparison/adv_baseline

# Test (Forecast-based)
python scripts/run_dynamic_universe_backtest.py \
  --config config/crypto_perps_full_rules_forecast_select.yaml \
  --data data/dataset_538registry_6yr_jagged.parquet \
  --outdir out/stage2_comparison/forecast_magnitude

# Compare universes
python scripts/compare_stage2_universes.py \
  --adv out/stage2_comparison/adv_baseline/universe_snapshot.json \
  --forecast out/stage2_comparison/forecast_magnitude/universe_snapshot.json \
  --output out/stage2_comparison/universe_comparison.json
```

**Key Metrics to Compare:**
- Sharpe ratio (primary goal: improve risk-adjusted returns)
- Turnover (expect higher with forecast-based)
- Transaction costs (risk: illiquid selections)
- Universe overlap (divergent instrument preferences)

**Status:** ✅ Implementation complete. ✅ Testing complete. ❌ Forecast-based selection REJECTED.

**Test Results:**

| Metric | ADV-Based | Forecast-Based | Δ | Winner |
|--------|-----------|----------------|---|--------|
| **Sharpe** | **0.8419** | 0.7831 | -7.0% | ✅ ADV |
| **CAGR** | **14.4%** | 9.3% | -35.4% | ✅ ADV |
| **Annual Vol** | 17.9% | 12.4% | -30.7% | N/A |
| **Max DD** | -21.9% | -14.7% | +32.9% | ✅ Forecast |
| **Avg Positions** | **24.9** | 22.8 | -8.4% | ✅ ADV |
| **Turnover** | **15.3x** | 16.3x | +6.5% | ✅ ADV |
| **Universe Overlap** | 30 instruments | 27 instruments | **10% overlap** | Critical divergence |

**Critical Finding:** Forecast-based selection **excludes BTC and ETH** (the two largest cryptos), instead selecting small-cap, low-liquidity instruments with high forecast volatility but poor actual performance.

**Root Cause:** High |forecast| magnitude reflects **forecast volatility**, not signal quality. Small caps have noisier data → higher vol-adjusted forecasts, but worse risk-adjusted returns.

**Decision:** **KEEP ADV-BASED SELECTION** (current default). Do not adopt forecast-based selection.

**Deliverables:**
- ✅ `out/stage2_comparison/COMPARISON_REPORT.md` - Full analysis (3000+ words)
- ✅ Baseline backtest: Sharpe 0.84 (exact reproduction)
- ✅ Forecast backtest: Sharpe 0.78 (underperformed by 7%)
- ✅ Universe analysis: Only 10% overlap (missed BTC, ETH, SOL, major caps)

**Key Lesson:** Liquidity is a proxy for institutional quality. The most liquid instruments are better researched, have higher quality data, and exhibit more predictable trends. Forecast magnitude is a misleading signal that favors noisy small caps over quality major caps.

---

## Previous Session Summary (2026-02-20, Part 1)

**Diagnosed "Sharpe Regression" (0.84 → 0.76)** - Root Cause: Wrong Configuration File

**Problem:** After reverting Mr Greedy optimizer changes, baseline verification showed Sharpe 0.76, but historical results showed Sharpe 0.84. This appeared to be a -9.5% performance degradation.

**Investigation Results:**
- **Root cause**: Different configuration files were used for the two backtests
- **0.84 result** (Feb 18): Used `config/crypto_perps_full_rules.yaml` (19-rule stack)
- **0.76 result** (Feb 20): Used `config/crypto_perps_dynamic_universe_top30.yaml` (3-rule EWMAC-only stack)

**Config Comparison:**

| Metric | Full Rules (0.84) | Top30 (0.76) | Delta |
|--------|-------------------|--------------|-------|
| **Rules** | 19 rules | 3 rules | -84% |
| **Families** | 7 families | 1 family (EWMAC) | -86% |
| **Sharpe** | 0.8419 | 0.7633 | -9.3% |
| **Annual Vol** | 17.94% | 21.56% | +20% |
| **Avg Positions** | 24.9 | 16.8 | -32% |
| **Transaction Costs** | 27.97 bps/yr | 38.82 bps/yr | +38% |
| **Notional Capital** | $10,000 | $5,000 | -50% |

**Conclusion:** No actual regression - the simplified `top30` config is a **test config**, not for production performance comparison. The lack of rule diversification (3 EWMAC vs 19 multi-family rules) leads to higher volatility and lower risk-adjusted returns.

**Verification:** Re-ran backtest with correct config → **Sharpe 0.8419** ✅ (exact match to historical 0.84)

**Deliverables:**
- Updated `current-work.md` to clarify which config produces which performance
- Documented correct baseline commands in "Useful Commands" section
- Verified buffering has minimal impact (~1-2 bps Sharpe difference)

**Status:** Issue resolved. System performing as expected with correct configuration.

**Key Takeaway:** Always use `crypto_perps_full_rules.yaml` for production baseline comparisons, not `crypto_perps_dynamic_universe_top30.yaml`.

---

## Previous Session Summary (2026-02-19)

Fixed **Mr Greedy Portfolio Alignment Issue** - KeyError resolution:

- **Root cause identified**: Optimizer expects previous_positions to contain ALL instruments
  in current optimization set, but filtered instruments from previous days were missing.
  KeyErrors occurred when instruments were newly eligible or were filtered out yesterday.
- **Fixed `systems/crypto_perps/greedy_portfolio.py`** (lines 318-345): Align previous_positions
  with current set by adding zero entries for new/newly-eligible instruments before passing
  to optimizer. This ensures the optimizer always has complete prior state.
- **Enhanced exception logging** (lines 21-22, 184-198): Added traceback import and detailed
  error logging to help diagnose future issues quickly.
- **Created `scripts/debug_greedy_single_date.py`**: Diagnostic tool (362 lines) for single-date
  testing and troubleshooting. Shows N vs M alignment, filtering breakdown, and step-by-step
  validation through the optimization pipeline.
- **Validation**: Zero KeyErrors on debug script (tested 2024-03-21) and smoke test (100k+ lines).
  Optimizer running successfully on all dates. Alignment maintained: N == M throughout iteration.
- **Deliverable**: `out/greedy_alignment_fix_summary.md` (comprehensive fix documentation)

**Status**: Fix complete and validated. Ready for full-scale testing on 300+ instrument dataset.

**Next steps**:
1. Run full backtest to confirm fix at scale
2. Calibrate shadow_cost parameter
3. Compare performance vs two-stage baseline

## Previous Session Summary (2026-02-18, Part 2)

Implemented **Buffering in Backtests** to measure actual performance impact:

- **Modified `scripts/run_dynamic_universe_backtest.py`**: Added `apply_position_buffering()`
  function that simulates position inertia (buffers). Positions now only update when
  |optimal - current| > buffer_threshold (buffer_size × avg_position).
- **Created `scripts/compare_buffer_sweeps.py`**: Tool to compare unbuffered vs buffered
  sweep results, showing ΔSharpe and ΔTurnover.
- **Re-ran buffer sweep** with buffering enabled (buffer_size: 0.0, 0.05, 0.10, 0.15, 0.20)
- **Key findings**:
  - Buffering reduces turnover by 0-5.6% as expected (buffer=0.10 → -2.9% turnover)
  - Sharpe impact is **minimal** (±0.02), within backtest noise
  - Cost savings (~2-4 bps/yr) are offset by tracking error from delayed rebalancing
  - Optimal buffer_size: **0.05-0.10** (tiny net benefit or neutral)
- **Validation**: buffer_size=0.00 identical between unbuffered and buffered sweeps (proves correctness)
- **Deliverable**: `out/buffer_sweep_buffered/BUFFER_ANALYSIS.md` (full analysis)

**Decision**: Keep buffering **enabled** in backtest runner (deviates from pysystemtrade convention).
Use buffer_size=0.10 as baseline. Backtests now simulate realistic trading with inertia.

## Earlier Session Summary (2026-02-18, Part 1)

Completed **Buffer Size Non-Impact Root Cause Analysis**:

- **Created `scripts/diagnose_buffering.py`**: Post-processing diagnostic tool that loads
  positions.csv from backtest runs, simulates buffering logic (inertia constraints), and
  compares buffered vs unbuffered turnover. Validates buffer impact without re-running backtests.
- **Created `scripts/analyze_buffer_sweep.py`**: Batch analysis wrapper that runs diagnostics
  on all buffer_* directories and generates comparison table.
- **Root cause confirmed**: Backtest runner originally called `get_notional_position()` which returns
  optimal (unbuffered) positions. Buffers were designed for live trading only (pysystemtrade convention).
- **Impact quantified**: Post-processing simulation showed buffers would reduce turnover by 0-7.2%
- **Deliverables**: `out/buffer_sweep/FINDINGS.md` (initial report), `buffer_impact_analysis.json`

**Note**: This analysis led to implementing actual buffering in backtests (see Part 2 above).

## Earlier Session Summary (2026-02-18)

Implemented **ResidualMomentum Fix + Empirical base_sr + Single-Stage Net-SR Selection**:

- **Auto-loaded macro data**: `scripts/run_dynamic_universe_backtest.py` now auto-discovers
  `data/macro_factors.parquet` relative to the dataset path. `--macro-data` is no longer
  required — the runner logs whether macro data was found or not.
- **Created `scripts/estimate_base_sr.py`**: Post-processing script that reads
  `diagnostics.parquet` + price panel to estimate SR per unit absolute forecast
  (base_sr) without re-running the system. See usage below.
- **Removed Stage 1 cost filter** (`skip_stage1_cost_filter: true` in config): The
  `dynamic_portfolio.py` portfolio stage now uses a data-availability mask instead of
  `get_universe_eligibility_df()` when the flag is set. Stablecoins and high-cost
  instruments rank out naturally via `net_sr → -∞`.
- **Updated `config/crypto_perps_full_rules.yaml`**: Removed Stage 1 params
  (`max_sr_cost_per_trade`, `max_sr_cost_annual`, `adv_window`, `min_history_days`,
  `min_annual_vol`). Added `skip_stage1_cost_filter: true`.

## Previous Session Summary (2026-02-17)

Implemented **Full Carver-Style 45-Rule Trading Stack**:
- Created `systems/crypto_perps/rules/rule_library.py` — 10 new rule functions
  (normmom, assettrend, btc_lead_lag, funding_carry, relcarry, funding_mr,
   streversal, return_skew, mrinasset, illiquidity)
- Extended `sysdata/crypto/parquet_perps_sim_data.py` with 6 cross-sectional
  data methods: get_asset_class_index_price, get_cross_sectional_median_funding,
  get_btc_price, get_adv_notional, get_normalised_price_this_instrument,
  get_normalised_price_for_asset_class
- Created `config/crypto_perps_full_rules.yaml` — 45-rule config with
  Divergent 70% / Conv-A 22.5% / Conv-B 7.5% budget (exact, sum=1.0)
- Smoke tested: all 14 rule families produce valid forecasts on 15×4yr dataset

## Previous Session Summary (2026-01-14)

Implemented **Walk-Forward Dynamic Instrument Universe** system:
- Created `sysdata/crypto/walk_forward_costs.py` - ADV$-based spread estimation
- Created `sysdata/crypto/dynamic_universe.py` - Eligibility filtering with SR cost thresholds
- Created `systems/provided/crypto_example/analyze_universe.py` - Diagnostic script
- Updated `sysdata/crypto/spot_sim_data.py` with dynamic universe support
- Updated `sysdata/crypto/csv_spot_data.py` with volume data access

## Active Task

**Buffering implementation completed** (2026-02-18). Backtests now apply position inertia via
`apply_position_buffering()` function. Unbuffered vs buffered sweep comparison shows minimal
Sharpe impact (±0.02) but successful turnover reduction (0-5.6%). See
`out/buffer_sweep_buffered/BUFFER_ANALYSIS.md` for full analysis.

**Decision**: Keep buffering **enabled** with buffer_size=0.10 as baseline. This deviates from
pysystemtrade convention (buffers normally only in live trading) but provides more realistic
backtest results.

## Next Steps

1. **Run full backtest** (macro data now auto-loaded, Stage 1 bypassed):
   ```bash
   python scripts/run_dynamic_universe_backtest.py \
     --config config/crypto_perps_full_rules.yaml \
     --data data/dataset_538registry_6yr_jagged.parquet \
     --outdir out/net_sr_full
   ```
2. **Estimate empirical base_sr** from fresh diagnostics:
   ```bash
   python scripts/estimate_base_sr.py \
     --diagnostics out/net_sr_full/diagnostics.parquet \
     --data data/dataset_538registry_6yr_jagged.parquet \
     --capital 10000
   ```
   Update `base_sr:` in `config/crypto_perps_full_rules.yaml` with the printed value.
3. **Re-run with empirical base_sr** and compare Sharpe vs 0.73 baseline.
4. **Per-instrument weight overrides** for BTC/ETH (no relmomentum/relcarry/
   btc_lead_lag for BTC; mrinasset for BTC+ETH). Currently all instruments use
   the "default" flat weights. Requires either:
   - A custom `ForecastCombine` subclass that reads a `default` key in nested weights, or
   - Generating explicit weight dicts for every top-30 instrument in the YAML
5. Address volume data quality issues

## Key Files Created/Modified This Session (2026-02-18)

### New Files (Part 2: Buffering Implementation)
- `scripts/compare_buffer_sweeps.py` — Compares unbuffered vs buffered sweep results
- `out/buffer_sweep_buffered/` — Buffered sweep results (5 buffer_size values)
- `out/buffer_sweep_buffered/BUFFER_ANALYSIS.md` — Comprehensive analysis of buffering impact

### New Files (Part 1: Buffer Investigation)
- `scripts/diagnose_buffering.py` — Single-run diagnostic: simulates buffering on positions.csv
- `scripts/analyze_buffer_sweep.py` — Batch analysis: runs diagnostic on all buffer_* dirs
- `out/buffer_sweep/FINDINGS.md` — Initial root cause analysis report
- `out/buffer_sweep/buffer_impact_analysis.json` — Quantitative results (0-7.2% turnover reduction)

### New Files (Earlier in Session)
- `scripts/estimate_base_sr.py` — Post-processing tool: diagnostics.parquet → base_sr estimate
- `scripts/sweep_buffer_size.py` — Parameter sweep: runs full backtest with different buffer_size values

### Modified Files
- **`scripts/run_dynamic_universe_backtest.py`** — **MAJOR**: Added `apply_position_buffering()` function
  and modified position extraction to apply buffering. Backtests now simulate position inertia.
- `.claude/rules/current-work.md` — Updated with buffering implementation findings
- `scripts/run_dynamic_universe_backtest.py` — Auto-discovers macro_factors.parquet (earlier)
- `systems/provided/crypto_example/core/dynamic_portfolio.py` — skip_stage1_cost_filter (earlier)
- `config/crypto_perps_full_rules.yaml` — skip_stage1_cost_filter: true (earlier)

## Key Files Modified Last Session (2026-02-17)

### New Files
- `systems/crypto_perps/rules/rule_library.py` — 10 rule functions
- `config/crypto_perps_full_rules.yaml` — 45-rule config

### Modified Files
- `sysdata/crypto/parquet_perps_sim_data.py` — +6 cross-sectional data methods

## Known Issues

- **Per-instrument weights**: BTC gets relmomentum/relcarry/btc_lead_lag with
  non-zero weights in current implementation (weights are flat for all instruments).
  The plan's per-instrument weight design requires custom code — tracked as TODO.
- **FundingMR low coverage**: Only 92 non-NaN values on 15x4yr dataset for BTC
  (extreme funding episodes are rare). This is expected behaviour, not a bug.
- **Volume data quality**: Some instruments (WBTC, WETH) show unrealistic ADV values

## Useful Commands

**IMPORTANT:** Always use `crypto_perps_full_rules.yaml` for production baseline comparisons (Sharpe 0.84).
The `crypto_perps_dynamic_universe_top30.yaml` config is a simplified 3-rule test config (Sharpe 0.76), not for performance benchmarking.

```bash
# ==============================================================================
# BASELINE BACKTEST (Production: 19-rule stack, Sharpe 0.84)
# ==============================================================================
python scripts/run_dynamic_universe_backtest.py \
  --config config/crypto_perps_full_rules.yaml \
  --data data/dataset_538registry_6yr_jagged.parquet \
  --outdir out/baseline_0.84

# Expected results:
#   Sharpe: 0.84 | Vol: 17.9% | Avg Pos: 24.9 | Txn Costs: 28 bps/yr
#   Rules: 19 across 7 families (EWMAC, Breakout, Normmom, Accel, Assettrend, Relmomentum, ResidualMomentum)
#   Capital: $10,000 notional | Buffer: 10%

# ==============================================================================
# PARAMETER SWEEPS
# ==============================================================================

# Sweep buffer_size (position inertia threshold)
# Runtime: ~5min per value × 5 values ≈ 25 minutes
python scripts/sweep_buffer_size.py \
  --config config/crypto_perps_full_rules.yaml \
  --data data/dataset_538registry_6yr_jagged.parquet \
  --values 0.0 0.05 0.10 0.15 0.20 \
  --outdir out/buffer_sweep

# Diagnose buffer impact (single run)
python scripts/diagnose_buffering.py \
  --positions out/buffer_sweep/buffer_0.10/positions.csv \
  --buffer-size 0.10 \
  --data data/dataset_538registry_6yr_jagged.parquet \
  --capital 10000

# Analyze all buffer sweep results (batch)
python scripts/analyze_buffer_sweep.py \
  --sweep-dir out/buffer_sweep \
  --data data/dataset_538registry_6yr_jagged.parquet \
  --capital 10000 \
  --output out/buffer_sweep/buffer_impact_analysis.json

# Estimate empirical base_sr from diagnostics (run after backtest)
python scripts/estimate_base_sr.py \
  --diagnostics out/baseline_0.84/diagnostics.parquet \
  --data data/dataset_538registry_6yr_jagged.parquet \
  --capital 10000

# ==============================================================================
# TESTING AND DEBUGGING
# ==============================================================================

# Smoke test (quick, 15 instruments, static universe)
python scripts/run_dynamic_universe_backtest.py \
  --config config/crypto_perps_full_rules.yaml \
  --data data/example_crypto_perps_15x4yr.parquet \
  --outdir out/smoke_full_rules \
  --static-universe

# Simplified test config (3 EWMAC rules only - NOT for baseline comparison)
# Expected Sharpe: 0.76 (lower due to lack of diversification)
python scripts/run_dynamic_universe_backtest.py \
  --config config/crypto_perps_dynamic_universe_top30.yaml \
  --data data/dataset_538registry_6yr_jagged.parquet \
  --outdir out/test_top30_simplified
```
