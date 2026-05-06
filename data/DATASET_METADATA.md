# Crypto Perpetual Futures Dataset Metadata

This document describes the available dataset variants, their characteristics, known quality issues, and regime classification.

## Dataset Variants

### Baseline (1-year, 5 instruments)
- **File:** `example_crypto_perps.parquet`
- **Time range:** 2023-01-01 to 2024-12-31 (365 days)
- **Instruments:** BTCUSDT_PERP, ETHUSDT_PERP, BNBUSDT_PERP, SOLUSDT_PERP, XRPUSDT_PERP
- **Size:** ~50 KB
- **Use case:** Fast iteration, baseline smoke tests
- **Backtest runtime:** ~0.57s (with incremental EWMA engine)

### Phase 1: Time Horizon Expansion (5-year, 4 instruments)
- **File:** `example_crypto_perps_5yr.parquet`
- **Time range:** 2020-01-01 to 2024-12-31 (~1,825 days)
- **Instruments:** BTCUSDT_PERP, ETHUSDT_PERP, BNBUSDT_PERP, XRPUSDT_PERP
- **Size:** ~200 KB
- **Use case:** Regime diversity testing (COVID crash, DeFi summer, bear market 2022, recovery)
- **Backtest runtime:** ~1.8s (estimated via O(T·N²) scaling)
- **Critical decision:** SOL excluded to preserve full 2020 coverage (SOL perp launches mid-2020)

**Build command:**
```bash
python scripts/build_example_dataset.py \
  --source real \
  --start-year 2020 \
  --end-year 2024 \
  --instruments BTCUSDT_PERP ETHUSDT_PERP BNBUSDT_PERP XRPUSDT_PERP \
  --output-path data/example_crypto_perps_5yr.parquet \
  --min-coverage 0.80
```

### Phase 2: Cross-Section Expansion (4-year, 15 instruments)
- **File:** `example_crypto_perps_15x4yr.parquet`
- **Time range:** 2021-01-01 to 2024-12-31 (~1,460 days)
- **Instruments:** BTCUSDT_PERP, ETHUSDT_PERP, BNBUSDT_PERP, XRPUSDT_PERP, LTCUSDT_PERP, EOSUSDT_PERP, SOLUSDT_PERP, DOTUSDT_PERP, LINKUSDT_PERP, ADAUSDT_PERP, DOGEUSDT_PERP, MATICUSDT_PERP, AVAXUSDT_PERP, UNIUSDT_PERP, BCHUSDT_PERP
- **Size:** ~800 KB
- **Use case:** Diversification testing, IDM validation, relative momentum rule testing
- **Backtest runtime:** ~20s (estimated)
- **Start date rationale:** 2021-01-01 ensures all 15 symbols have full coverage (no ragged panel)

**Build command:**
```bash
python scripts/build_example_dataset.py \
  --source real \
  --start-year 2021 \
  --end-year 2024 \
  --instruments BTCUSDT_PERP ETHUSDT_PERP BNBUSDT_PERP XRPUSDT_PERP LTCUSDT_PERP \
               EOSUSDT_PERP SOLUSDT_PERP DOTUSDT_PERP LINKUSDT_PERP ADAUSDT_PERP \
               DOGEUSDT_PERP MATICUSDT_PERP AVAXUSDT_PERP UNIUSDT_PERP BCHUSDT_PERP \
  --output-path data/example_crypto_perps_15x4yr.parquet \
  --min-coverage 0.85
```

---

## Symbol Launch Dates and Data Availability

Reference: `data/raw/metadata/binance_symbol_lifecycle.json`

### Tier 1 (Binance Futures Launch - 2019-09-08)
- **BTCUSDT**: 2019-09-08
- **ETHUSDT**: 2019-09-08

### Tier 2 (Early 2019-09)
- **BNBUSDT**: 2019-09-08
- **XRPUSDT**: 2019-09-13
- **LTCUSDT**: 2019-09-13
- **EOSUSDT**: 2019-09-13
- **BCHUSDT**: 2019-09-13

### Tier 3 (DeFi Era - 2020)
- **LINKUSDT**: 2020-07-24
- **SOLUSDT**: 2020-07-27 (⚠️ mid-year launch)
- **DOTUSDT**: 2020-08-20
- **ADAUSDT**: 2020-08-27

### Tier 4 (Post-DeFi Summer - 2021)
- **UNIUSDT**: 2021-01-19
- **MATICUSDT**: 2021-02-11
- **DOGEUSDT**: 2021-05-10
- **AVAXUSDT**: 2021-07-13

---

## Known Data Quality Issues

### General Issues
1. **Missing funding data:** Some instruments have <90% funding rate coverage (filled with 0.0)
2. **Price gaps:** Occasional gaps >7 days (logged during build, typically around symbol relaunch events)
3. **Funding anomalies:** Extreme funding stress events (>2% daily or <-0.5% daily) logged but not filtered

### Instrument-Specific Issues

#### SOLUSDT
- **Launch date:** 2020-07-27 (mid-year)
- **Impact:** Excluded from Phase 1 to preserve Jan-Jun 2020 coverage for all instruments
- **Notes:** Included in Phase 2 (2021+ datasets)

#### DOGEUSDT
- **Launch date:** 2021-05-10 (Elon pump era)
- **Notes:** High volatility in launch month, expected behavior

---

## Regime Classification

### 5-Year Dataset (2020-2024)

**Regime windows:**

1. **COVID Crash (2020-03)**
   - **Dates:** 2020-03-01 to 2020-03-31
   - **Characteristics:** Extreme volatility spike, -50%+ drawdown in days
   - **Funding:** Negative (backwardation), longs liquidated en masse

2. **DeFi Summer (2020-06 to 2020-08)**
   - **Dates:** 2020-06-01 to 2020-08-31
   - **Characteristics:** Strong uptrend, high positive funding (contango)
   - **Notes:** ETH outperformed BTC (DeFi narrative)

3. **Bull Market Peak (2021-04 to 2021-11)**
   - **Dates:** 2021-04-01 to 2021-11-30
   - **Characteristics:** BTC all-time high (~$69k), high funding rates
   - **Notes:** Leverage flush events (May 2021, Sep 2021)

4. **Bear Market (2022-01 to 2022-12)**
   - **Dates:** 2022-01-01 to 2022-12-31
   - **Characteristics:** Grinding downtrend, low volatility, negative funding
   - **Notes:** LUNA collapse (May 2022), FTX collapse (Nov 2022)

5. **Recovery (2023-01 to 2024-12)**
   - **Dates:** 2023-01-01 to 2024-12-31
   - **Characteristics:** Slow recovery, ETF approval (2024-01), renewed interest
   - **Notes:** Baseline dataset covers this period only

---

## Volatility Statistics

### 5-Year Dataset (2020-2024)
Expected volatility distribution (30-day rolling annualized):
- **Vol min:** ~0.20 (20% annualized in quiet periods)
- **Vol p10:** ~0.40
- **Vol median:** ~0.80
- **Vol p90:** ~1.50
- **Vol max:** >3.00 (COVID crash, leverage flushes)
- **Percentile spread (p90-p10):** >1.0 (indicates regime diversity)

### 15-Instrument Dataset (2021-2024)
Expected correlation distribution (pairwise):
- **Min:** ~0.20-0.40 (BTC vs altcoins in divergent periods)
- **Median:** ~0.60-0.80 (crypto market correlation generally high)
- **Max:** <1.0 (no perfect correlation expected)

---

## Funding Rate Characteristics

### Normal Conditions
- **Typical daily funding:** -0.05% to +0.15% (sum of 3x 8h events)
- **Annualized equivalent:** -20% to +50% (on notional)
- **Mean:** ~0.01% per day (slight contango bias)

### Stress Conditions
- **High stress (>2% daily):** Occurs during leverage flushes, mania phases
- **Low stress (<-0.5% daily):** Occurs during crashes, backwardation
- **Logged but not filtered:** Build script reports stress events, doesn't exclude them

---

## Survivorship Bias Considerations

### Phase 1 (4 instruments, 2020-2024)
- **Bias:** ACCEPTED for research universe
- **Rationale:** Regime diversity prioritized over unbiased selection
- **Selection:** Top-tier perps active since Binance futures launch (2019)

### Phase 2 (15 instruments, 2021-2024)
- **Bias:** ACKNOWLEDGED but not fully eliminated
- **Issue:** "Top ADV per year" requires as-of snapshots (not yet implemented)
- **Label:** "Survivorship-accepted research universe"
- **Future enhancement:** Record as-of ADV rankings per year for true point-in-time selection

---

## Data Validation Checklist

When building a new dataset variant, verify:

1. **Schema compliance:**
   - Required columns: date, instrument, close, funding_rate, adv_notional, spread_frac, taker_fee_frac
   - No NaN in close prices
   - funding_rate filled with 0.0 for missing values

2. **Time series quality:**
   - Gap analysis: Log warnings for gaps >7 days
   - Price outliers: Log warnings for jumps >50% day-over-day
   - Funding coverage: Log if <90% coverage per instrument

3. **Rectangular panel:**
   - All instruments have same date count
   - Pivot operation produces no NaN
   - Dates monotonic and unique per instrument

4. **Regime coverage (5-year dataset):**
   - COVID crash window present (2020-03)
   - Volatility percentile spread >0.3

5. **Diversification (15-instrument dataset):**
   - Pairwise correlation max <1.0
   - Median correlation <0.85 (preferred, not required)

---

## Performance Benchmarks

| Dataset | Days | Instruments | Expected Runtime | Scaling Factor |
|---------|------|-------------|------------------|----------------|
| Baseline | 365 | 5 | 0.57s | 1x |
| Phase 1 (5yr) | 1,825 | 4 | ~1.8s | 3.2x |
| Phase 2 (15x4yr) | 1,460 | 15 | ~20s | 35x |

**Runtime notes:**
- O(T·N²) scaling via incremental EWMA engine
- Target: <5s for Phase 1, <30s for Phase 2
- If exceeded: Profile and optimize (likely candidates: universe filters, relative momentum rule)

---

## Next Steps

### Phase 1 (Time Horizon Expansion)
1. Download 2020-2024 data for 4 instruments (BTCUSDT, ETHUSDT, BNBUSDT, XRPUSDT)
2. Build 5-year dataset: `python scripts/build_example_dataset.py --source real --start-year 2020 --end-year 2024 --output-path data/example_crypto_perps_5yr.parquet`
3. Validate dataset: `python scripts/validate_real_data.py data/example_crypto_perps_5yr.parquet`
4. Run extended tests: `pytest tests/test_crypto_perps_smoke.py -m extended`

### Phase 2 (Cross-Section Expansion)
1. Download 2021-2024 data for 15 instruments
2. Build 15-instrument dataset
3. Validate diversification metrics
4. Update config with expanded universe (if needed for testing)

---

## Research Configurations

### Stage-1 Research: Baseline System Behavior Analysis

**Purpose:** Validate baseline system behavior across diverse market regimes at N=4 instruments before expanding to N=15.

**Dataset:** `example_crypto_perps_5yr.parquet` (2020-2024, 4 instruments)

**Config Variants:**

1. **`config/crypto_perps_baseline_v1.yaml`** (Frozen Baseline)
   - Purpose: Immutable baseline for Stage-1 research
   - Changes from default: `diagnostics.enabled: true`
   - Status: FROZEN - any changes require new version (v2, v3, etc.)

2. **`config/research/crypto_perps_baseline_v1_carry_off.yaml`** (Counterfactual)
   - Purpose: Quantify carry effect via counterfactual analysis
   - Changes: `forecasts.rule_weights.carry_funding: 0.0`
   - Attribution: `equity_baseline - equity_carry_off = carry effect`

3. **`config/research/crypto_perps_baseline_v1_constraints_off.yaml`** (Counterfactual)
   - Purpose: Quantify constraint binding effect via counterfactual analysis
   - Changes: `gross_leverage_cap: 999.0`, `idm_cap: 999.0`
   - Attribution: `equity_constraints_off - equity_baseline = constraint effect`

**Research Outputs:**

Located in `out/stage1_*/` directories:
- `equity_curve.csv`: Daily equity
- `positions.csv`: Daily notional positions
- `pnl_breakdown.csv`: Daily PnL (price, funding, costs)
- `diagnostics.parquet`: Granular daily data
- `metadata.json`: Summary metrics
- `research_summary.md`: Executive summary with findings

**Research Questions:**

1. **PnL Attribution:** How much return comes from trend vs carry vs constraint effects?
2. **Regime Drawdowns:** Where are major drawdowns? (COVID crash, 2022 bear, recovery)
3. **Constraint Binding:** When and why do constraints bind? (IDM cap vs gross leverage cap)
4. **State Transitions:** Do instruments transition states appropriately during stress?
5. **Turnover Clustering:** Is turnover smooth or clustered around regime changes?

**Analysis Script:** `scripts/stage1_report.py`

---

## References

- **Design Spec:** See `AGENT_INSTRUCTIONS_README` and Crypto Perps Design Spec
- **Symbol Lifecycle:** `data/raw/metadata/binance_symbol_lifecycle.json`
- **Market Info:** `data/raw/metadata/binance_market_info.json`
- **Build Script:** `scripts/build_example_dataset.py`
- **Download Script:** `scripts/download_multi_year.sh`
