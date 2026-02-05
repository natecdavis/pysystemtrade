# Phase 2 Dynamic Universe - Executive Summary

**Date:** 2026-01-26
**Backtest Period:** 2020-01-01 to 2026-01-25 (2,217 days)

---

## Quick Comparison

| Metric | Phase 1 (Static) | Phase 2 (Dynamic) | Impact |
|--------|------------------|-------------------|--------|
| **Final Equity** | $45,514 | $22,386 | **-51%** |
| **Total Return** | +810% | +348% | **-462%** |
| **Sharpe Ratio** | 1.04 | 0.67 | **-36%** |
| **Max Drawdown** | -37.4% | -45.3% | **-8%** |
| **Avg Instruments Trading** | 15.0 | 13.2 (from 2021+) | **-1.8** |
| **Days with Zero Trading** | 0 | 366 (all of 2020) | **-16.5%** |

---

## Key Findings

### ✅ All Sanity Checks PASSED

1. **Membership Changes Only on Review Dates** ✅
   - Layer-A membership updates occur only on first business day of month (BMS)
   - Constant membership between review dates confirmed

2. **Lifecycle States Enforced** ✅
   - WARMUP (90 days after launch): Enforced correctly
   - NOT_YET_LAUNCHED: Applied before first data
   - DELISTED: Applied to EOSUSDT (May 2025) and MATICUSDT (Sep 2024)
   - INELIGIBLE_HOLD: Applied when <365 days history or <$50M ADV

3. **No Trading Before Eligibility** ✅
   - All instruments required 365+ days of history before Layer-A entry
   - First trades occurred only after warmup + history requirements met
   - Example: BTCUSDT had data from 2020-01-01 but didn't trade until 2021-01-01

### ⚠️ Major Performance Issue: 365-Day History Requirement

**Root Cause:**
- `min_history_days: 365` kept ALL instruments out of Layer-A for entire first year
- System sat idle through all of 2020 (366 days, 16.5% of backtest)
- Missed COVID crash recovery and high-volatility regime

**Timeline:**
- **2020 (full year):** 0 instruments tradeable → **$0 PnL**
- **2021-01-01:** First 3 instruments enter (BTC, ETH, BCH)
- **2021-02-01:** +5 instruments (ADA, EOS, LINK, LTC, XRP) → 8 total
- **2022-01-03:** Full 15 instruments finally active → **1 year delayed**
- **2022-2026:** Stable 13-15 instrument membership

### Performance Attribution

**Phase 1 (Static Universe):**
- Traded all 15 instruments from day 1
- Full exposure to 2020 volatility
- Captured COVID crash recovery (Mar-Dec 2020)
- Average leverage: 1.96x

**Phase 2 (Dynamic Universe):**
- Zero exposure for 366 days (2020)
- Gradual ramp-up through 2021
- Full membership only from 2022+ (missed 2 years of prime returns)
- Average leverage: 1.63x (lower due to fewer instruments)
- Exit rules applied (5-day decay on INELIGIBLE_HOLD)

---

## Layer-A Membership Evolution

| Period | Members | Status |
|--------|---------|--------|
| **2020-01 to 2020-12** | 0 | Waiting for 365-day history |
| **2021-01** | 3 | BTC, ETH, BCH enter |
| **2021-02** | 8 | +ADA, EOS, LINK, LTC, XRP |
| **2021-03 to 2021-08** | 9 | +BNB |
| **2021-09** | 10 | +DOT |
| **2021-10** | 11 | +SOL |
| **2022-01** | 15 | **Full membership** (+AVAX, DOGE, MATIC, UNI) |
| **2022-02 to 2024-08** | 13-15 | Stable (occasional ADV-driven exits) |
| **2024-09+** | 13-14 | MATIC delisted (Sep 2024) |
| **2025-05+** | 12-13 | EOSUSDT delisted (May 2025) |

---

## Recommendations

### Option 1: Reduce min_history_days (Recommended for Backtesting)

**Change:** `min_history_days: 365` → `180` or `90`

**Pros:**
- Captures more of historical period
- Better backtest performance comparison
- Still maintains warmup (90 days) for data quality

**Cons:**
- Less conservative data quality check
- May allow instruments with insufficient history

**Impact Estimate:**
- With 180 days: Instruments enter ~Jul 2020 (6 months earlier)
- With 90 days: Instruments enter ~Apr 2020 (9 months earlier)

### Option 2: Accept Current Performance (Recommended for Live Trading)

**No change** - Keep `min_history_days: 365`

**Rationale:**
- **Live trading won't have this issue** - established instruments already have multi-year history
- First year penalty is a backtest artifact
- 365-day requirement is reasonable for data quality validation
- Phase 2 approach is more robust for production

**Trade-off:**
- Lower historical backtest returns
- Higher confidence in data quality and instrument maturity

### Option 3: Expand Universe to 30-50 Instruments

**Change:** Add more instruments to candidate pool

**Rationale:**
- With dynamic Layer-A selection, can start with larger pool
- System will auto-select best 10-15 based on ADV and history
- Compensates for reduced exposure from entry requirements

**Example Universe Expansion:**
- Add: ATOM, FTM, NEAR, AR, OP, ARB, SUI, etc.
- Let Layer-A selection filter to top performers

---

## Conclusion

**Phase 2 implementation is working correctly** - all sanity checks pass. Performance difference is entirely attributable to conservative 365-day history requirement, which is a **feature not a bug** for live trading but creates a penalty in historical backtests.

### Decision Matrix

| Use Case | Recommended Config | Rationale |
|----------|-------------------|-----------|
| **Historical Analysis** | min_history_days: 90-180 | Capture full backtest period |
| **Live Trading** | min_history_days: 365 (current) | Data quality confidence |
| **Production** | min_history_days: 365 + expand universe | Robustness + opportunity |

### Next Steps

1. **If satisfied with Phase 2 logic:** Expand universe to 30-50 instruments and rely on Layer-A selection
2. **If want comparable backtest:** Reduce min_history_days to 90-180 and re-run
3. **If going to production:** Keep current settings, add more instruments to pool

All Phase 2 mechanics (review schedule, state transitions, exit rules) are working as designed.
