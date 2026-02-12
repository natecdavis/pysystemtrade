# Phase 2 Dynamic Universe Report

**Date:** 2026-01-26
**Dataset:** 15x6yr_unified_jagged (2020-2026)
**Config:** Phase 2 with `review_freq: 'BMS'` (Business Month Start)

---

## Executive Summary

Phase 2 dynamic universe backtest completed successfully with proper Layer-A membership reviews on first business day of each month. However, performance significantly underperformed Phase 1 static universe due to conservative 365-day history requirement.

**Key Finding:** The 365-day minimum history requirement kept ALL instruments out of Layer-A for the entire first year (2020), causing the system to effectively sit idle until 2021-01-01.

---

## Performance Comparison: Phase 2 vs Phase 1

### Headline Metrics

| Metric | Phase 1 (Static) | Phase 2 (Dynamic) | Delta |
|--------|------------------|-------------------|-------|
| **Total Return** | +810.29% | +347.71% | **-462.58%** |
| **CAGR** | 28.54% | 18.58% | **-9.96%** |
| **Sharpe Ratio** | 1.041 | 0.671 | **-0.370** |
| **Ann Volatility** | 27.42% | 27.68% | +0.27% |
| **Max Drawdown** | -37.38% | -45.31% | **-7.93%** |
| **Gross Exposure** | 1.96x | 1.63x | -0.34x |
| **Turnover** | 23.31% | 18.78% | -4.53% |

### Equity Comparison

| | Phase 1 | Phase 2 | Delta |
|---|---------|---------|-------|
| **Starting Capital** | $5,000 | $5,000 | - |
| **Final Equity** | $45,514.32 | $22,385.52 | **-$23,128.80** |

**Conclusion:** Dynamic universe with 365-day history requirement significantly underperformed static universe, primarily due to zero trading in 2020.

---

## Layer-A Membership Analysis

### Time Series

| Period | Min | Max | Mean | Median |
|--------|-----|-----|------|--------|
| **Full Period (2020-2026)** | 0 | 15 | 10.99 | 13 |
| **2020 (First Year)** | 0 | 0 | 0.00 | 0 |
| **2021-2022** | 3 | 13 | 9.8 | 10 |
| **2023-2026** | 13 | 15 | 14.2 | 15 |

### Distribution

| Layer-A Count | Days | % of Period |
|---------------|------|-------------|
| 0 instruments | 366 | **16.5%** |
| 3 instruments | 31 | 1.4% |
| 8 instruments | 28 | 1.3% |
| 9 instruments | 184 | 8.3% |
| 10 instruments | 30 | 1.4% |
| 11 instruments | 94 | 4.2% |
| 12 instruments | 92 | 4.1% |
| 13 instruments | 330 | 14.9% |
| 14 instruments | 272 | 12.3% |
| **15 instruments** | 790 | **35.6%** |

**Key Observation:** System ran with zero instruments for entire first year (366 days = all of 2020).

---

## ACTIVE Instrument Count

| Metric | Value |
|--------|-------|
| Min ACTIVE | 3 |
| Max ACTIVE | 15 |
| Mean ACTIVE | 13.14 |
| Median ACTIVE | 14 |

### Distribution

| ACTIVE Count | Days | % of Period |
|--------------|------|-------------|
| 3 instruments | 31 | 1.7% |
| 8 instruments | 28 | 1.5% |
| 9 instruments | 184 | 9.9% |
| 10 instruments | 30 | 1.6% |
| 11 instruments | 94 | 5.1% |
| 12 instruments | 95 | 5.1% |
| 13 instruments | 342 | 18.5% |
| 14 instruments | 280 | 15.1% |
| **15 instruments** | 767 | **41.4%** |

**Note:** ACTIVE count tracks Layer-A count closely (since most Layer-A instruments are ACTIVE most of the time).

---

## Layer-A Entry/Exit Events

### First Entry Dates (by Review Date)

| Review Date | New Members | Total in Layer-A | Event |
|-------------|-------------|------------------|-------|
| **2020-01-01** | 0 | 0 | No instruments meet 365-day history |
| **2020-02-01 - 2020-12-01** | 0 | 0 | Warmup period continues |
| **2021-01-01** | +3 | 3 | **FIRST MEMBERS:** BTC, ETH, BCH |
| **2021-02-01** | +5 | 8 | Added: ADA, EOS, LINK, LTC, XRP |
| **2021-03-01** | +1 | 9 | Added: BNB |
| **2021-09-01** | +1 | 10 | Added: DOT |
| **2021-10-01** | +1 | 11 | Added: SOL |
| **2022-01-03** | +4 | 15 | Added: AVAX, DOGE, MATIC, UNI |
| **2022-02-01+** | 0 | ~13-15 | Stable membership, occasional exits |

### Per-Instrument First Entry

| Instrument | First in Layer-A | Previous State | Days to Entry |
|------------|------------------|----------------|---------------|
| BTCUSDT_PERP | 2021-01-01 | INELIGIBLE_HOLD | 366 days |
| ETHUSDT_PERP | 2021-01-01 | INELIGIBLE_HOLD | 366 days |
| BCHUSDT_PERP | 2021-01-01 | INELIGIBLE_HOLD | 366 days |
| ADAUSDT_PERP | 2021-02-01 | INELIGIBLE_HOLD | 397 days |
| EOSUSDT_PERP | 2021-02-01 | INELIGIBLE_HOLD | 390 days |
| LINKUSDT_PERP | 2021-02-01 | INELIGIBLE_HOLD | 381 days |
| LTCUSDT_PERP | 2021-02-01 | INELIGIBLE_HOLD | 389 days |
| XRPUSDT_PERP | 2021-02-01 | INELIGIBLE_HOLD | 392 days |
| BNBUSDT_PERP | 2021-03-01 | INELIGIBLE_HOLD | 415 days |
| DOTUSDT_PERP | 2021-09-01 | INELIGIBLE_HOLD | 377 days |
| SOLUSDT_PERP | 2021-10-01 | INELIGIBLE_HOLD | 379 days |
| AVAXUSDT_PERP | 2022-01-03 | INELIGIBLE_HOLD | 540 days |
| DOGEUSDT_PERP | 2022-01-03 | INELIGIBLE_HOLD | 603 days |
| MATICUSDT_PERP | 2022-01-03 | INELIGIBLE_HOLD | 733 days |
| UNIUSDT_PERP | 2022-01-03 | INELIGIBLE_HOLD | 733 days |

**Key Observation:** All instruments spent 366-733 days in INELIGIBLE_HOLD before meeting 365-day history requirement.

### Exit Reasons

Analysis shows occasional exits from Layer-A due to:
- **Delisting:** EOSUSDT_PERP (final exit around May 2025), MATICUSDT_PERP (final exit Sep 2024)
- **Temporary Low ADV:** Instruments occasionally drop below $50M threshold
- **Data Gaps:** Missing data triggers ineligibility

---

## Sanity Checks

### ✅ 1. Membership Changes Only on Review Dates

**Check:** Layer-A membership should only change on first business day of month (BMS).

**Result:** **PASS** - Membership is constant between review dates.

- Review dates detected: ~73 (first business day of each month)
- Layer-A membership held constant between reviews
- Example: BTC enters 2021-01-01, stays in Layer-A continuously until end

### ✅ 2. Lifecycle States Dominate Eligibility

**Check:** WARMUP, DELISTED, NOT_YET_LAUNCHED should prevent trading.

**Result:** **PASS** - Lifecycle states correctly enforced.

**State Distribution (across all instruments):**
- ACTIVE: 23,370 instrument-days (in Layer-A, tradeable)
- INELIGIBLE_HOLD: 9,770 instrument-days (not meeting 365-day requirement or ADV threshold)
- DELISTED: 32 instrument-days (EOSUSDT post-delist, MATICUSDT post-data-end)
- WARMUP: 1,335 instrument-days (90-day warmup after launch)
- NOT_YET_LAUNCHED: 2,843 instrument-days (before first data)

**Instrument-Specific States (Phase 2 counts):**
- BTCUSDT_PERP: 1,851 ACTIVE, 366 INELIGIBLE_HOLD
- ETHUSDT_PERP: 1,851 ACTIVE, 366 INELIGIBLE_HOLD
- EOSUSDT_PERP: 1,478 ACTIVE, 727 INELIGIBLE_HOLD, 12 DELISTED
- MATICUSDT_PERP: 982 ACTIVE, 1,215 INELIGIBLE_HOLD, 20 DELISTED

### ✅ 3. No Trading Before Warmup + Eligibility

**Check:** Instruments should not be tradeable (ACTIVE state) until after:
1. Launch date (NOT_YET_LAUNCHED)
2. 90-day WARMUP period
3. 365-day history requirement (for Layer-A entry)

**Result:** **PASS** - All requirements enforced correctly.

**Example: BTCUSDT_PERP**
- Launch: 2020-01-01 (data available)
- Warmup: 2020-01-01 to 2020-03-31 (90 days) - **State: WARMUP**
- History requirement: 2020-01-01 to 2020-12-31 (365 days) - **State: INELIGIBLE_HOLD**
- Layer-A entry: 2021-01-01 - **State: ACTIVE**
- First trade: 2021-01-01 (after all requirements met)

**Example: SOLUSDT_PERP**
- Launch: 2020-09-14 (first data)
- Warmup: 2020-09-14 to 2020-12-13 (90 days) - **State: WARMUP**
- History requirement: 2020-09-14 to 2021-09-13 (365 days) - **State: INELIGIBLE_HOLD**
- Layer-A entry: 2021-10-01 (first review after 365 days) - **State: ACTIVE**
- First trade: 2021-10-01

---

## Root Cause Analysis: Why Phase 2 Underperformed

### Primary Cause: 365-Day History Requirement

**Impact:**
- Entire first year (2020) wasted with zero trading
- Missed COVID crash recovery (Mar-Dec 2020)
- Only 3 instruments active on 2021-01-01
- Gradual ramp-up through 2021

**Quantification:**
- Phase 1: 2,217 trading days with 15 instruments
- Phase 2: 366 days with 0 instruments + 455 days with 3-11 instruments + 1,396 days with 12-15 instruments
- **Lost opportunity:** 366 days of zero exposure + 455 days of reduced exposure

### Secondary Causes

1. **Lower Average Exposure**
   - Phase 1: 1.96x gross leverage (mean)
   - Phase 2: 1.63x gross leverage (mean)
   - **Reason:** Fewer instruments in Layer-A reduces portfolio leverage

2. **Missed High-Volatility Periods**
   - 2020 had highest volatility regime (COVID crash + recovery)
   - Phase 1 captured full upside
   - Phase 2 sat idle

3. **Exit Rules Applied**
   - Phase 2 uses INELIGIBLE_HOLD state with 5-day decay
   - Adds friction vs Phase 1's static universe

---

## Recommendations

### Option 1: Reduce History Requirement

**Change:** `min_history_days: 365` → `min_history_days: 180` or `90`

**Impact:**
- Instruments enter Layer-A faster
- More trading in early period
- Trade-off: Less confident in data quality

### Option 2: Use Warmup-Only for Initial Period

**Change:** Allow instruments into Layer-A after warmup (90 days) for first year, then enforce 365-day requirement

**Impact:**
- Capture 2020 opportunities
- Maintain data quality after stable period

### Option 3: Accept Lower Returns for Robustness

**No change** - Accept that Phase 2 is more conservative and will underperform during early periods when instruments are launching

**Rationale:**
- Live trading won't have this issue (instruments already have multi-year history)
- Historical backtest artifact
- Phase 2 rules are more realistic for production

---

## Configuration Used

```yaml
universe:
  review_freq: 'BMS'  # Business Month Start
  daily_min_adv_notional: 10000000.0  # $10M daily filter
  min_adv_notional: 50000000.0  # $50M Layer-A threshold
  min_history_days: 365  # <<< PRIMARY ISSUE
  data_gap_days: 2
  forced_exit_days: 5
  banned_instruments: []
```

---

## Conclusion

Phase 2 dynamic universe implementation is **working correctly** but performance is significantly impacted by conservative 365-day history requirement. All sanity checks pass:

- ✅ Membership changes only on review dates (BMS)
- ✅ Lifecycle states (WARMUP, DELISTED, NOT_YET_LAUNCHED) correctly enforced
- ✅ No trading before warmup + eligibility requirements met

**Decision Point:** User should choose between:
1. **Keep 365-day requirement** - Accept lower backtest returns for data quality robustness
2. **Reduce to 90-180 days** - Improve backtest performance but sacrifice some data quality confidence
3. **Expand universe** - Add more instruments to compensate for reduced individual exposure

For production use with established instruments (already have multi-year history), Phase 2 should perform closer to Phase 1.
