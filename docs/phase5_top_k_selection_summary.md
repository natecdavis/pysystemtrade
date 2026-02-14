# Phase 5: Top-K Selection with Hysteresis - Implementation Summary

**Date:** 2026-02-14
**Status:** ✅ Complete (Core Implementation)

## Overview

Successfully implemented top-K instrument selection with entry/exit hysteresis to prevent churn. The selector ranks instruments by liquidity (rolling ADV) and uses asymmetric thresholds to create stability in the tradable universe.

## Key Achievement

**Hysteresis prevents churn:** Instruments ranked 26-40 (for K=30) can stay in the tradable set but can't enter, creating a "gray zone" that reduces turnover without sacrificing liquidity.

## Implementation

### Core Module: `sysdata/crypto/top_k_selector.py` (NEW)

**Class: `TopKInstrumentSelector`**

```python
class TopKInstrumentSelector:
    """
    Select top K instruments by liquidity with entry/exit hysteresis.

    Parameters:
        K: Target number of tradable instruments (default: 30)
        entry_buffer: Buffer for entry threshold (default: 5)
        exit_buffer: Buffer for exit threshold (default: 10)
        adv_window: Rolling window for ADV calculation (default: 30 days)
        min_history_days: Minimum data history to compute ADV (default: 365)
    """
```

**Key Methods:**

1. **`compute_liquidity_metric()`** - Computes rolling ADV from Vision data
2. **`select_tradable_set()`** - Applies hysteresis to select tradable instruments
3. **`get_tradable_over_time()`** - Simulates tradable evolution over backtest period
4. **`to_eligibility_df()`** - Converts to boolean DataFrame for pysystemtrade

### Hysteresis Logic

**Entry Threshold:** `rank <= K - entry_buffer`
- Example (K=30, buffer=5): Must rank <= 25 to enter
- Conservative: Harder to enter (quality filter)

**Exit Threshold:** `rank > K + exit_buffer`
- Example (K=30, buffer=10): Exit if rank > 40
- Permissive: Easier to stay (reduces churn)

**Hysteresis Zone:** Ranks 26-40
- Can stay if already in tradable set
- Can't enter if not already in
- Creates stability without sacrificing top liquidity

### Liquidity Metric

**Primary: Rolling ADV from Vision Data**
```python
adv_usd = (recent_prices * recent_volumes).mean()
```

- Window: 30 days (configurable)
- Reproducible: Same Vision data → same ranking
- Stable: Less sensitive to single-day spikes

**Fallback: Registry volume_24h**
- Used for new symbols with <365 days history
- Approximates ADV for entry consideration
- Once entered, switches to Vision-based ADV

## Testing

### Unit Tests: `tests/test_phase5_top_k_selector.py` (NEW)

**Coverage:**
1. `test_top_k_selector_initialization()` - Parameter initialization
2. `test_compute_liquidity_metric()` - ADV computation
3. `test_entry_hysteresis()` - Entry threshold enforcement
4. `test_exit_hysteresis()` - Exit threshold enforcement
5. `test_hysteresis_prevents_churn()` - Gray zone behavior
6. `test_cap_at_k()` - Hard cap at K instruments
7. `test_eligibility_filtering()` - Non-eligible instruments exit
8. `test_get_tradable_over_time()` - Evolution over time
9. `test_to_eligibility_df()` - DataFrame conversion

**Results:** ✅ 9/9 tests passing

### Verification Script: `scripts/verify_phase5.sh` (NEW)

**Checks:**
1. Unit tests passing
2. TopKSelector initialization
3. Hysteresis logic
4. Liquidity computation
5. Tradable evolution over time
6. Eligibility DataFrame conversion

**Run:**
```bash
./scripts/verify_phase5.sh
```

## Configuration Example

```yaml
dynamic_universe:
  top_k: 30               # Target tradable size
  entry_buffer: 5         # Enter if rank <= 25 (conservative)
  exit_buffer: 10         # Exit if rank > 40 (permissive)
  adv_window: 30          # Rolling window for ADV (days)
  min_history_days: 365   # Minimum data to compute ADV
```

## Usage Example

```python
from sysdata.crypto.top_k_selector import TopKInstrumentSelector
import pandas as pd

# Initialize selector
selector = TopKInstrumentSelector(
    K=30,
    entry_buffer=5,
    exit_buffer=10,
    adv_window=30,
    min_history_days=365
)

# Select tradable set at date
new_tradable = selector.select_tradable_set(
    eligible_candidates=eligible_instruments,  # From cost filters
    current_tradable=current_tradable_set,
    prices_df=prices_dataframe,
    volumes_df=volumes_dataframe,
    date=current_date
)

# Simulate tradable evolution over time
tradable_over_time = selector.get_tradable_over_time(
    eligible_df=eligibility_dataframe,
    prices_df=prices_dataframe,
    volumes_df=volumes_dataframe
)

# Convert to eligibility DataFrame for pysystemtrade
eligibility_df = selector.to_eligibility_df(
    tradable_over_time,
    all_instruments=instrument_list
)
```

## Hysteresis Example

**Setup:** K=30, entry_buffer=5, exit_buffer=10

**Scenario:** INST25 has rank 28 (between entry=25 and exit=40)

**Case 1: INST25 NOT in tradable set**
- Rank 28 > 25 (entry threshold)
- Cannot enter
- Remains outside

**Case 2: INST25 IS in tradable set**
- Rank 28 <= 40 (exit threshold)
- Can stay
- Remains inside

**Result:** Hysteresis creates stability (rank 28 can stay but can't enter)

## Architecture Benefits

### Prevents Churn

**Without Hysteresis (K=30, no buffers):**
- Entry: rank <= 30
- Exit: rank > 30
- Instrument at rank 29-31 flips in/out frequently
- High turnover costs

**With Hysteresis (K=30, entry_buffer=5, exit_buffer=10):**
- Entry: rank <= 25
- Exit: rank > 40
- Instrument at rank 26-40 stays stable
- Lower turnover costs

### Liquidity-Based Ranking

**Advantages of Rolling ADV:**
- Reproducible: Same data → same ranking
- Stable: 30-day window smooths volatility
- Vision-derived: No external API dependency

**vs min_position_frac (rejected):**
- min_position_frac is a position inclusion threshold (not universe selection)
- Causes churn when positions fluctuate near threshold
- Not designed for top-K selection

### Asymmetric Thresholds

**Entry Buffer (Conservative):**
- Harder to enter (must rank in top 25 for K=30)
- Quality filter: only high-liquidity instruments join

**Exit Buffer (Permissive):**
- Easier to stay (can rank up to 40 for K=30)
- Stability: reduces unnecessary exits

**Asymmetry creates hysteresis without sacrificing quality**

## Integration Points

### Current Phase (Phase 5): Core Implementation

**Completed:**
- ✅ TopKInstrumentSelector class
- ✅ Hysteresis logic (entry/exit thresholds)
- ✅ Liquidity metric (rolling ADV)
- ✅ Unit tests (9/9 passing)
- ✅ Verification script

### Future Phase (Phase 6): Production Integration

**Planned:**
1. Integration with dynamic portfolio stage
2. Config validation (top_k <= len(layer_a_instruments))
3. Hard invariant: trade plan ⊆ layer_a_instruments
4. layer_a_instruments as max tradable set (e.g., 30 names)

## Filter Application Order

**Complete Filter Stack:**

1. **Registry** (Phase 2) - 541 candidates from auto-discovery
2. **Lifecycle** (Phase 3) - Filter by data availability boundaries
3. **Cost Filters** (Existing) - SR-based cost thresholds
4. **Minimum History** (Existing) - Rule-based data requirements
5. **Top-K Selection** (Phase 5) - Liquidity-based ranking with hysteresis

**Example Flow:**
```
541 candidates (registry)
  ↓ lifecycle filter
340 with data coverage
  ↓ cost filters
120 passing SR thresholds
  ↓ min history filter
100 with sufficient data
  ↓ top-K selector (K=30, hysteresis)
~28 tradable (varies with hysteresis)
```

## Known Limitations

1. **No Volume Data Quality Checks**
   - Assumes volume data is accurate
   - Outliers could affect ADV ranking
   - Mitigation: Vision data generally reliable

2. **Hysteresis Doesn't Account for Costs**
   - Purely liquidity-based ranking
   - Doesn't factor in cost of rebalancing
   - Acceptable: cost filters applied before top-K

3. **Registry Fallback for New Symbols**
   - New symbols use volume_24h (less stable)
   - Risk: New symbol enters, then exits quickly
   - Mitigation: min_history_days=365 reduces this

4. **No Correlation-Based Diversification**
   - Top-K selects by liquidity only
   - Doesn't consider correlation structure
   - Acceptable: 30 instruments sufficient for diversification

## Files Created

### New Files
- `sysdata/crypto/top_k_selector.py` - TopKInstrumentSelector class
- `tests/test_phase5_top_k_selector.py` - Unit tests (9/9 passing)
- `scripts/verify_phase5.sh` - Verification script
- `docs/phase5_top_k_selection_summary.md` - This document

### No Modifications Required
- Phase 5 is a standalone module
- Integration with dynamic portfolio is Phase 6 work

## Verification Checklist

- [x] Unit tests passing (9/9)
- [x] Hysteresis logic working
- [x] Entry threshold enforcement
- [x] Exit threshold enforcement
- [x] Liquidity computation (ADV)
- [x] Gray zone behavior (can stay, can't enter)
- [x] Cap at K enforcement
- [x] Eligibility filtering
- [x] Tradable evolution over time
- [x] DataFrame conversion
- [x] Documentation complete

## Conclusion

Phase 5 successfully implements top-K selection with hysteresis, providing a robust mechanism for selecting tradable instruments while minimizing churn. The asymmetric entry/exit thresholds create a stability zone that reduces turnover costs without sacrificing liquidity.

**Key Achievement:** Hysteresis-based selection prevents churn while maintaining high-quality tradable universe.

**Implementation Status:**
- ✅ Core TopKInstrumentSelector module complete
- ✅ Unit tests passing (9/9)
- ✅ Verification complete
- ⚠️ Production integration (Phase 6 work)

**Next:** Phase 6 - Production integration (config validation, hard invariants, documentation).
