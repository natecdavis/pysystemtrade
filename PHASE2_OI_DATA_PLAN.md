# Phase 2: True OI Data Implementation Plan

**Date:** 2026-02-21
**Status:** 🚀 IN PROGRESS - Download running (Day 1/5)
**Goal:** Test whether true OI/Volume ratio provides better crash detection than funding rate proxy

---

## ⚠️ CRITICAL UPDATE: Data Availability (2026-02-21)

**FINDING:** Binance metrics data only starts from **2021-12-01**, not 2020-01-01.

**Impact:**
- ❌ **May 2021 crash**: NO DATA (China mining ban - data doesn't exist)
- ✅ **June 2022 crash**: Full coverage (3AC/Celsius liquidations)
- ✅ **Nov 2022 crash**: Full coverage (FTX collapse)

**Revised Plan:**
- **Original**: 6 years (2020-2026), 3 crash events
- **Actual**: 4.2 years (Dec 2021-Jan 2026), 2 crash events
- **Coverage**: ~70% of original timeline, 67% of crash events

**Data Quality - Better than Expected:**
- ✅ 5-minute granularity (vs expected daily)
- ✅ OI in USD notional (`sum_open_interest_value`)
- ✅ Bonus: Long/Short ratios (all traders + top traders)
- ✅ Taker buy/sell volume ratio

**Feasibility Assessment: PROCEED**
- 2 crash events still statistically significant
- Different crash types (contagion vs exchange failure)
- 4.2 years sufficient for full backtest Sharpe comparison
- Success criteria adjusted: ≥1/2 crashes (was ≥2/3)

---

## Hypothesis

**Current (Phase 1):** Funding rate is a **lagging indicator**
- Spikes during/after crashes
- Still provides +0.47% crash protection on average

**Phase 2 Hypothesis:** OI/Volume ratio is a **leading indicator**
- OI rises BEFORE crashes (leverage building)
- Should provide earlier detection → better timing
- Expected improvement: +0.5% to +1.0% additional Sharpe

---

## Data Requirements

### What We Need

**Primary:**
- Open Interest (OI) time series (daily or more frequent)
- Volume time series (already have from price data)

**Optional (nice to have):**
- Long/Short Ratio (retail positioning)
- Top Trader Long/Short Ratio (institutional positioning)

### Coverage Requirements

**Time Period:** ~~2020-01-01~~ **2021-12-01 to 2026-01-31** (~4.2 years) ⚠️ UPDATED
- Binance metrics data only available from Dec 2021
- 5-minute granularity (better than expected daily)

**Instruments:** All Binance USDT perpetuals
- Current dataset: 300 instruments (active in backtest period)
- Need OI for as many as possible (target: ≥240 = 80%)

**Data Quality:**
- No gaps longer than 3 days
- Aligned with price/funding data
- Validated against ~~May 2021~~, June 2022, Nov 2022 crashes ⚠️ UPDATED

---

## Data Source Options

### Option A: Binance Public Data Archive (FREE) ✅ RECOMMENDED

**Source:** https://github.com/binance/binance-public-data

**What's Available:**
- Daily ZIP files containing 5-minute CSV data
- Open Interest data: **Dec 2021-present** for all USDT perpetuals ⚠️ UPDATED
- Updated daily (next-day availability)

**Pros:**
- ✅ Free
- ✅ Official Binance data
- ✅ Complete historical coverage
- ✅ All instruments

**Cons:**
- ❌ Manual download required
- ❌ Monthly aggregation (need to download many files)
- ❌ CSV format (need to convert to parquet)
- ❌ ~5-7 days effort for data acquisition

**Estimated Effort:** 5-7 days
- 1-2 days: Download automation script
- 2-3 days: CSV to parquet conversion
- 1-2 days: Data validation and alignment

### Option B: Tardis.dev API (PAID)

**Source:** https://tardis.dev/

**Pricing:** $50-200/month (depends on usage)

**What's Available:**
- Complete historical OI data via API
- Real-time updates
- Long/Short ratio data
- Top trader data

**Pros:**
- ✅ Automated API access
- ✅ Clean, normalized data
- ✅ Additional metrics (LS ratio, etc.)
- ✅ ~2 days effort

**Cons:**
- ❌ Costs $50-200/month
- ❌ Requires API key management
- ❌ Ongoing subscription cost

**Estimated Effort:** 2-3 days
- 1 day: API integration
- 1 day: Data download and conversion
- 0.5 day: Validation

### Recommendation: Start with Option A

**Why:**
- Free (no ongoing costs)
- Official Binance data (most reliable)
- Good learning exercise (understand data format)
- Can switch to Tardis later if we need real-time updates

**If Option A fails:**
- Coverage <80% of instruments → upgrade to Option B
- Data quality issues → upgrade to Option B

---

## Implementation Design

### Step 1: Data Acquisition (5-7 days)

**1.1 Download Script**

Create `scripts/download_binance_oi_data.py`:
- Automate downloading monthly OI CSV files
- Date range: 2020-01 to 2026-01
- All USDT perpetual instruments

**1.2 Conversion Pipeline**

Create `scripts/convert_oi_to_parquet.py`:
- Read monthly CSV files
- Combine into single time series per instrument
- Convert to parquet format
- Align with existing price data dates

**1.3 Validation**

Create `scripts/validate_oi_data.py`:
- Check coverage (% of instruments with OI data)
- Check gaps (missing dates)
- Validate against known events (OI should spike before crashes)
- Generate data quality report

### Step 2: Schema Extension (1 day)

**2.1 Update Schema**

Modify `sysdata/crypto/schema.py`:
```python
REQUIRED_COLUMNS = [
    # Existing columns
    'date', 'instrument', 'open', 'high', 'low', 'close', 'volume',
    'funding_rate',

    # New columns (Phase 2)
    'open_interest',        # OI in USD notional (optional for instruments without data)
    # Future: 'long_short_ratio', 'top_trader_ls_ratio'
]
```

**2.2 Update Dataset Builder**

Modify dataset build process to include OI:
- Merge OI data with price/funding data
- Handle missing OI (fill with NaN or exclude instrument)
- Maintain backward compatibility (old datasets still work)

### Step 3: OI/Volume Ratio Calculation (2 days)

**3.1 Add Data Access Method**

Extend `sysdata/crypto/parquet_perps_sim_data.py`:
```python
def get_open_interest(self, instrument_code: str) -> pd.Series:
    """Get open interest series for an instrument."""
    meta = self._meta_df.xs(instrument_code, level='instrument')
    return meta['open_interest']

def get_oi_volume_ratio(self, instrument_code: str, window: int = 7) -> pd.Series:
    """
    Calculate OI/Volume ratio as leverage indicator.

    High OI/Volume → high leverage → crash risk

    Args:
        instrument_code: Instrument to analyze
        window: Rolling window for volume average (days)

    Returns:
        OI/Volume ratio series
    """
    oi = self.get_open_interest(instrument_code)
    volume = self.get_daily_volume(instrument_code)  # Need to add this

    # Use rolling average volume to smooth
    avg_volume = volume.rolling(window, min_periods=max(window // 2, 1)).mean()

    # OI / Volume ratio
    ratio = oi / avg_volume.clip(lower=1e6)  # Avoid division by zero

    return ratio
```

**3.2 Modify OI Regime Multiplier**

Update `get_oi_regime_multiplier()` to support both modes:
```python
def get_oi_regime_multiplier(
    self,
    instrument_code: str,
    mode: str = 'funding',  # 'funding' or 'oi_volume'
    lookback: int = 90,
    threshold: float = 2.0,
    min_scale: float = 0.5,
    **kwargs
) -> pd.Series:
    """
    OI regime multiplier with multiple modes.

    Args:
        mode: 'funding' (Phase 1) or 'oi_volume' (Phase 2)
    """
    if mode == 'funding':
        # Existing Phase 1 logic
        funding = self.get_funding_rate(instrument_code)
        funding_ann = funding * 3 * 365
        z_score = self._calculate_z_score(funding_ann, lookback)

    elif mode == 'oi_volume':
        # Phase 2: OI/Volume ratio
        oi_vol_ratio = self.get_oi_volume_ratio(instrument_code, window=7)
        z_score = self._calculate_z_score(oi_vol_ratio, lookback)

    else:
        raise ValueError(f"Unknown mode: {mode}")

    # Common scaling logic
    z_abs = z_score.abs()
    sensitivity = (1.0 - min_scale) / threshold
    multiplier = 1.0 - (z_abs - threshold) * sensitivity
    return multiplier.clip(lower=min_scale, upper=1.0)

def _calculate_z_score(self, series: pd.Series, lookback: int) -> pd.Series:
    """Helper to calculate rolling z-score."""
    rolling_mean = series.rolling(lookback, min_periods=30).mean()
    rolling_std = series.rolling(lookback, min_periods=30).std()
    return (series - rolling_mean) / rolling_std.clip(lower=1e-8)
```

### Step 4: Configuration (1 day)

**4.1 Create Test Configs**

Create comparison configs:

**`config/phase2_test_funding.yaml`** (baseline - Phase 1)
```yaml
use_oi_overlay: true
oi_overlay_params:
  mode: 'funding'          # Phase 1 mode
  lookback: 90
  threshold: 2.0
  min_scale: 0.5
```

**`config/phase2_test_oi_volume.yaml`** (Phase 2)
```yaml
use_oi_overlay: true
oi_overlay_params:
  mode: 'oi_volume'        # Phase 2 mode
  lookback: 90
  threshold: 2.0
  min_scale: 0.5
  oi_volume_window: 7      # Volume rolling window
```

**4.2 Update Portfolio Stage**

Modify `systems/crypto_perps/crypto_portfolio_oi_overlay.py`:
- Pass `mode` parameter to get_oi_regime_multiplier()
- Read from config: `params.get('mode', 'funding')`

### Step 5: Testing & Comparison (3-5 days)

**5.1 Run Comparison Backtests**

Create `scripts/compare_phase1_vs_phase2.sh`:
```bash
# Baseline (no overlay)
python scripts/run_dynamic_universe_backtest.py \
  --config config/crypto_perps_full_rules.yaml \
  --data data/dataset_with_oi_6yr.parquet \
  --outdir out/phase2/baseline

# Phase 1 (funding proxy)
python scripts/run_dynamic_universe_backtest.py \
  --config config/phase2_test_funding.yaml \
  --data data/dataset_with_oi_6yr.parquet \
  --outdir out/phase2/phase1_funding

# Phase 2 (OI/Volume ratio)
python scripts/run_dynamic_universe_backtest.py \
  --config config/phase2_test_oi_volume.yaml \
  --data data/dataset_with_oi_6yr.parquet \
  --outdir out/phase2/phase2_oi_volume
```

**5.2 Acute Crash Comparison**

Create `scripts/compare_phase2_crashes.py`:
- Analyze same 3 crash events (May 2021, June 2022, Nov 2022)
- Compare funding vs OI/Volume on crash detection
- Measure timing differences (did OI trigger earlier?)

**5.3 Success Criteria**

Phase 2 is worth adopting if:
- OI/Volume Sharpe ≥ Funding Sharpe + 0.5% (material improvement)
- OI/Volume crash protection ≥ Funding + 0.2% avg (better crash performance)
- OI coverage ≥ 80% of instruments (sufficient data)
- OI triggers earlier than funding in **≥1 out of 2 crashes** (leading indicator) ⚠️ UPDATED

---

## Data Validation Checkpoints

### Checkpoint 1: Data Coverage

**After download:**
- % instruments with OI data: target ≥ 80%
- Date range coverage: 2020-01-01 to 2026-01-31
- Maximum gap length: ≤ 3 days

**If fails:**
- <80% coverage → consider Tardis.dev
- Large gaps → need interpolation strategy

### Checkpoint 2: Data Quality

**Validation tests:**
- OI aligns with known events (should spike before May 2021 crash)
- OI/Volume ratio is reasonable (not negative, not infinity)
- No obvious data errors (sudden jumps, flat lines)

**If fails:**
- Identify problematic instruments
- Exclude or fix bad data
- Document limitations

### Checkpoint 3: Signal Quality

**Before full backtest:**
- Calculate OI z-score for known crash events
- Compare timing vs funding z-score
- Visualize: Does OI lead or lag funding?

**If fails:**
- OI doesn't lead → may not improve on funding
- Consider parameter tuning (lookback, threshold)

---

## Risk Mitigation

### Risk 1: Insufficient OI Coverage

**Probability:** Medium
**Impact:** High (can't test properly)

**Mitigation:**
- Start with Binance Public Data (free)
- If <80% coverage, upgrade to Tardis.dev
- Fallback: Test on subset of instruments with good coverage

### Risk 2: OI Doesn't Lead

**Probability:** Medium
**Impact:** High (Phase 2 provides no benefit)

**Mitigation:**
- Validate timing on known events first (Checkpoint 3)
- If OI doesn't lead, consider other indicators (LS ratio, liquidation data)
- Don't force adoption if benefit isn't clear

### Risk 3: Data Quality Issues

**Probability:** Low
**Impact:** Medium

**Mitigation:**
- Comprehensive validation scripts
- Visual inspection of key instruments (BTC, ETH, SOL)
- Cross-check against external sources (TradingView OI charts)

### Risk 4: Implementation Complexity

**Probability:** Low
**Impact:** Medium

**Mitigation:**
- Maintain backward compatibility (funding mode still works)
- Graceful degradation (missing OI → fall back to funding)
- Comprehensive testing before replacing Phase 1

---

## Timeline

### Week 1: Data Acquisition
- Day 1-2: Download automation script
- Day 3-4: CSV to parquet conversion
- Day 5: Data validation (Checkpoint 1, 2)

### Week 2: Implementation
- Day 6-7: Schema updates, data access methods
- Day 8-9: OI/Volume ratio calculation, overlay modification
- Day 10: Configuration and testing

### Week 3: Testing & Comparison
- Day 11-12: Full backtests (baseline, funding, OI/Volume)
- Day 13-14: Acute crash analysis, signal quality validation
- Day 15: Decision and documentation

**Total Timeline:** 15 days (~3 weeks)

---

## Success Criteria

### Minimum Requirements (Adoption Threshold)

**1. Data Quality:**
- ✅ OI coverage ≥ 80% of instruments (≥240 out of 300)
- ✅ Date range: **2021-12 to 2026-01** (~4.2 years) ⚠️ UPDATED
- ✅ No major gaps (≤ 3 days)

**2. Performance (Full Backtest):**
- ✅ OI/Volume Sharpe ≥ Funding Sharpe + 0.5%
- ✅ OI/Volume crash protection ≥ Funding + 0.2% avg

**3. Timing (Leading Indicator):**
- ✅ OI triggers earlier than funding in **≥1 out of 2 crashes** ⚠️ UPDATED
- ✅ OI z-score peaks BEFORE crash, not during/after
- Note: May 2021 crash excluded (no OI data available)

### Decision Matrix

| Criteria Met | Decision |
|--------------|----------|
| All 3 | ✅ **ADOPT Phase 2** (replace funding with OI/Volume) |
| 2 out of 3 | ⚠️ **TEST FURTHER** (investigate which criterion failed) |
| 1 or fewer | ❌ **REJECT Phase 2** (funding proxy is sufficient) |

---

## Deliverables

### Code

1. `scripts/download_binance_oi_data.py` - Download automation
2. `scripts/convert_oi_to_parquet.py` - Data conversion pipeline
3. `scripts/validate_oi_data.py` - Data quality validation
4. `sysdata/crypto/schema.py` - Extended schema (OI column)
5. `sysdata/crypto/parquet_perps_sim_data.py` - OI data access methods
6. `systems/crypto_perps/crypto_portfolio_oi_overlay.py` - Multi-mode support
7. `scripts/compare_phase1_vs_phase2.sh` - Comparison test runner
8. `scripts/compare_phase2_crashes.py` - Acute crash comparison

### Configs

1. `config/phase2_test_funding.yaml` - Phase 1 baseline
2. `config/phase2_test_oi_volume.yaml` - Phase 2 test config

### Data

1. `data/dataset_with_oi_6yr.parquet` - Extended dataset with OI column
2. `data/oi_data_quality_report.json` - Validation results

### Documentation

1. `PHASE2_OI_DATA_IMPLEMENTATION.md` - Implementation guide
2. `out/phase2/COMPARISON_RESULTS.md` - Phase 1 vs Phase 2 analysis
3. `out/phase2/DECISION.md` - Adoption decision rationale

---

## Open Questions

1. **Volume source:** Use close-to-close volume or integrate intraday volume?
   - Recommendation: Use daily volume from existing dataset

2. **OI normalization:** Raw OI or OI as % of market cap?
   - Recommendation: Use OI/Volume ratio (relative measure)

3. **Fallback strategy:** What if instrument has no OI data?
   - Recommendation: Fall back to funding proxy (graceful degradation)

4. **Window sizes:** Same lookback (90d) for OI as funding?
   - Recommendation: Test both 90d and 60d, use better performer

5. **Threshold:** Same threshold (2.0σ) for OI as funding?
   - Recommendation: Start with 2.0σ, tune if needed

---

## Next Steps

### Immediate (This Session) - ✅ COMPLETE
1. ✅ Create this plan document
2. ✅ Create download automation script (`scripts/download_binance_oi_data.py`)
3. ✅ Create conversion script (`scripts/convert_oi_to_parquet.py`)
4. ✅ Start downloading Binance OI data (running in background, ~4-6 days)

### This Week
4. Convert CSV to parquet
5. Validate data quality
6. Extend schema and data access methods

### Next Week
7. Implement OI/Volume overlay mode
8. Run comparison backtests
9. Analyze results and decide

---

**Date:** 2026-02-21
**Status:** 🚀 READY TO EXECUTE
**Next Action:** Create download automation script
