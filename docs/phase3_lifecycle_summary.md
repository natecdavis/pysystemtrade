# Phase 3: Lifecycle from Vision Data Coverage - Implementation Summary

**Date:** 2026-02-14
**Status:** ✅ Complete

## Overview

Successfully implemented lifecycle tracking derived from Vision data availability. Instrument lifecycle metadata (first/last data date, status) is now automatically computed during dataset building and stored in manifests for reproducible backtesting.

## Key Changes

### 1. Lifecycle Derivation: `scripts/build_example_dataset.py` (MODIFIED)

**New Function: `derive_lifecycle_from_vision_data(dataset_df, stale_threshold_days=7)`**

Analyzes actual data availability in the dataset to determine lifecycle metadata:

```python
def derive_lifecycle_from_vision_data(
    dataset_df: pd.DataFrame,
    stale_threshold_days: int = 7
) -> dict:
    """
    Derive instrument lifecycle metadata from Vision data coverage.

    Returns:
        {
            'BTCUSDT_PERP': {
                'first_data_date': '2019-09-08',
                'last_data_date': '2026-02-13',
                'data_days': 2350,
                'status': 'ACTIVE',
                'days_since_last': 1
            },
            ...
        }
    """
```

**Status Determination:**
- **ACTIVE**: `days_since_last <= stale_threshold_days` (default: 7 days)
- **STALE**: `days_since_last > stale_threshold_days`
- **NO_DATA**: Empty dataset for this instrument
- **ERROR**: Lifecycle derivation failed (conservative fallback)

**Integrated in `generate_dataset_manifest()`:**

Lifecycle is automatically derived and added to manifests during dataset building:

```python
# In generate_dataset_manifest():
lifecycle_data = derive_lifecycle_from_vision_data(dataset_df)

lifecycle_summary = {
    'active': sum(1 for lc in lifecycle_data.values() if lc.get('status') == 'ACTIVE'),
    'stale': sum(1 for lc in lifecycle_data.values() if lc.get('status') == 'STALE'),
    'no_data': sum(1 for lc in lifecycle_data.values() if lc.get('status') == 'NO_DATA'),
    'error': sum(1 for lc in lifecycle_data.values() if lc.get('status') == 'ERROR'),
}

manifest = {
    ...
    "lifecycle": lifecycle_data,
    "lifecycle_summary": lifecycle_summary,
    ...
}
```

### 2. Manifest Enhancement

**New Fields in Dataset Manifest:**

```json
{
  "generated_at": "2026-02-14T11:00:00Z",
  "lifecycle": {
    "BTCUSDT_PERP": {
      "first_data_date": "2019-09-08",
      "last_data_date": "2026-02-13",
      "data_days": 2350,
      "status": "ACTIVE",
      "days_since_last": 1
    },
    "OLDCOINUSDT_PERP": {
      "first_data_date": "2019-01-01",
      "last_data_date": "2021-12-31",
      "data_days": 1096,
      "status": "STALE",
      "days_since_last": 1505
    }
  },
  "lifecycle_summary": {
    "active": 340,
    "stale": 150,
    "no_data": 51,
    "error": 0
  }
}
```

### 3. Dynamic Universe Integration: `sysdata/crypto/dynamic_universe.py` (MODIFIED)

**New Function: `load_lifecycle_from_manifest(manifest_path)`**

Loads lifecycle metadata from dataset manifest:

```python
def load_lifecycle_from_manifest(manifest_path) -> dict:
    """
    Load lifecycle metadata from dataset manifest.

    Returns:
        Lifecycle dict keyed by instrument ID

    Returns empty dict if manifest doesn't exist or has no lifecycle section.
    """
```

**New Function: `check_lifecycle_eligibility(instrument_code, date, lifecycle_data)`**

Filters instruments by lifecycle boundaries for backtesting:

```python
def check_lifecycle_eligibility(
    instrument_code: str,
    date: pd.Timestamp,
    lifecycle_data: dict
) -> bool:
    """
    Check if instrument has data coverage at date.

    Filters out:
    - Before launch: date < first_data_date
    - After delisting: date > last_data_date
    - No data: status == 'NO_DATA'

    Returns:
        True if instrument has data coverage at date, False otherwise
    """
```

**Usage in Backtesting:**

```python
# Load lifecycle from manifest
manifest_path = dataset_path.with_suffix('.manifest.json')
lifecycle_data = load_lifecycle_from_manifest(manifest_path)

# Filter by lifecycle during backtest
for date in backtest_dates:
    for instrument in candidates:
        if check_lifecycle_eligibility(instrument, date, lifecycle_data):
            # Include in universe for this date
            ...
```

## Testing

### Unit Tests: `tests/test_phase3_lifecycle.py` (NEW)

**Coverage:**
1. `test_derive_lifecycle_basic()` - Basic lifecycle derivation
2. `test_derive_lifecycle_stale()` - Stale status detection
3. `test_derive_lifecycle_active()` - Active status detection
4. `test_derive_lifecycle_multiple_instruments()` - Multi-instrument handling
5. `test_load_lifecycle_from_manifest()` - Manifest loading
6. `test_load_lifecycle_missing_manifest()` - Missing manifest fallback
7. `test_check_lifecycle_eligibility()` - Eligibility filtering
8. `test_lifecycle_eligibility_edge_cases()` - Boundary conditions

**Results:** ✅ 8/8 tests passing

**Run Tests:**
```bash
python3 -m pytest tests/test_phase3_lifecycle.py -v
```

### Verification Script: `scripts/verify_phase3.sh` (NEW)

**Checks:**
1. Unit tests passing
2. Lifecycle derivation working (synthetic data)
3. Manifest loading working
4. Eligibility checking working
5. Lifecycle summary computation

**Run:**
```bash
./scripts/verify_phase3.sh
```

## Architecture Benefits

### More Accurate than CoinGecko

**CoinGecko Approach (rejected):**
- "First seen" dates unreliable (snapshot-based)
- No historical delisting information
- Manual maintenance required

**Vision Data Approach (implemented):**
- Derived from actual data availability
- Accurate first/last dates from Vision files
- Automatic detection of data gaps
- No external API dependency

### Historical Backtest Compatibility

**Correct Date Boundaries:**
- Instruments only appear in universe when they have data
- Before `first_data_date`: filtered out (not yet launched)
- After `last_data_date`: filtered out (delisted or stale)

**Reproducible:**
- Lifecycle snapshot in manifest (deterministic)
- Same manifest = same lifecycle filtering = same backtest results
- No time-dependent behavior

### Integrated with Dataset Pipeline

**Automatic Derivation:**
- Lifecycle computed during dataset build (no separate script)
- Always in sync with dataset contents
- Manifest hard invariant: `manifest.lifecycle.keys() ⊆ dataset.instruments`

**Single Source of Truth:**
- Vision data = ground truth for lifecycle
- No discrepancies between "registry lifecycle" vs "actual data"

## Usage Examples

### Building Dataset with Lifecycle

```bash
# Build dataset (lifecycle automatically derived)
python scripts/build_example_dataset.py \
    --source real \
    --data-dir envs/dev/data/raw/binance \
    --instruments BTCUSDT_PERP ETHUSDT_PERP SOLUSDT_PERP \
    --output-path data/test_lifecycle.parquet \
    --allow-jagged

# Verify lifecycle in manifest
cat data/test_lifecycle.manifest.json | jq '.lifecycle.BTCUSDT_PERP'
```

**Output:**
```json
{
  "first_data_date": "2019-09-08",
  "last_data_date": "2026-02-13",
  "data_days": 2350,
  "status": "ACTIVE",
  "days_since_last": 1
}
```

### Checking Lifecycle Summary

```bash
cat data/test_lifecycle.manifest.json | jq '.lifecycle_summary'
```

**Output:**
```json
{
  "active": 3,
  "stale": 0,
  "no_data": 0,
  "error": 0
}
```

### Using Lifecycle in Backtesting

```python
from pathlib import Path
import pandas as pd
from sysdata.crypto.dynamic_universe import (
    load_lifecycle_from_manifest,
    check_lifecycle_eligibility
)

# Load lifecycle from manifest
manifest_path = Path('data/dataset_541.manifest.json')
lifecycle_data = load_lifecycle_from_manifest(manifest_path)

# Filter candidates by lifecycle
tradable_at_date = []
backtest_date = pd.Timestamp('2024-01-01')

for instrument in all_candidates:
    if check_lifecycle_eligibility(instrument, backtest_date, lifecycle_data):
        tradable_at_date.append(instrument)

print(f"Tradable at {backtest_date}: {len(tradable_at_date)} instruments")
```

### Lifecycle Statistics

```python
# Compute lifecycle stats from manifest
import json
from pathlib import Path

manifest_path = Path('data/dataset_541.manifest.json')
with open(manifest_path) as f:
    manifest = json.load(f)

lifecycle = manifest['lifecycle']

# Find longest-running instruments
sorted_by_days = sorted(
    lifecycle.items(),
    key=lambda x: x[1].get('data_days', 0),
    reverse=True
)

print("Top 10 longest-running instruments:")
for inst, lc in sorted_by_days[:10]:
    print(f"  {inst}: {lc['data_days']} days ({lc['first_data_date']} → {lc['last_data_date']})")
```

## Manifest Lifecycle Section Structure

### Field Definitions

**Per-Instrument Fields:**
- `first_data_date` (str): ISO date of first data point (e.g., "2019-09-08")
- `last_data_date` (str): ISO date of last data point (e.g., "2026-02-13")
- `data_days` (int): Count of unique dates with data
- `status` (str): One of ACTIVE, STALE, NO_DATA, ERROR
- `days_since_last` (int): Days from last_data_date to manifest generation time

**Summary Fields:**
- `active` (int): Count of ACTIVE instruments
- `stale` (int): Count of STALE instruments
- `no_data` (int): Count of NO_DATA instruments
- `error` (int): Count of ERROR instruments

### Example Full Manifest

```json
{
  "generated_at": "2026-02-14T11:00:00Z",
  "dataset_metadata": {
    "requested_start_date": "2020-01-01",
    "requested_end_date": "2026-02-13",
    "actual_start_date": "2020-01-01",
    "actual_end_date": "2026-02-13"
  },
  "lifecycle": {
    "BTCUSDT_PERP": {
      "first_data_date": "2020-01-01",
      "last_data_date": "2026-02-13",
      "data_days": 2235,
      "status": "ACTIVE",
      "days_since_last": 1
    },
    "ETHUSDT_PERP": {
      "first_data_date": "2020-01-01",
      "last_data_date": "2026-02-13",
      "data_days": 2235,
      "status": "ACTIVE",
      "days_since_last": 1
    }
  },
  "lifecycle_summary": {
    "active": 2,
    "stale": 0,
    "no_data": 0,
    "error": 0
  },
  "instruments": {
    "included": {
      "BTCUSDT_PERP": {...},
      "ETHUSDT_PERP": {...}
    },
    "excluded": {}
  },
  "summary": {
    "total_candidates": 2,
    "included_count": 2,
    "excluded_count": 0
  }
}
```

## Integration with Dynamic Universe

### Current Dynamic Universe Filters

**Existing (Phase 1/2):**
1. Cost filters (SR-based, walk-forward)
2. Minimum data history (rule-based)

**New (Phase 3):**
3. **Lifecycle boundaries** (first/last data date)

### Filter Application Order

```python
def get_eligible_instruments(date, candidates, lifecycle_data, cost_estimator):
    """Determine eligible instruments at date."""
    eligible = []

    for instrument in candidates:
        # 1. Lifecycle filter (Phase 3)
        if not check_lifecycle_eligibility(instrument, date, lifecycle_data):
            continue

        # 2. Minimum history filter
        if not has_min_history(instrument, date):
            continue

        # 3. Cost filter
        if not passes_cost_filter(instrument, date, cost_estimator):
            continue

        eligible.append(instrument)

    return eligible
```

### Future Enhancement (Phase 5)

Top-K selection with hysteresis will operate on lifecycle-filtered candidates:

```python
# Phase 3: Lifecycle filtering
lifecycle_eligible = [
    instr for instr in candidates
    if check_lifecycle_eligibility(instr, date, lifecycle_data)
]

# Phase 5: Top-K selection (operates on lifecycle-eligible set)
tradable = select_top_k_with_hysteresis(
    lifecycle_eligible,
    liquidity_metric,
    K=30
)
```

## Known Limitations

1. **Stale Threshold Hardcoded**
   - Currently 7 days (hardcoded in `derive_lifecycle_from_vision_data()`)
   - Future: Make configurable via manifest generation params

2. **No Intraday Lifecycle Events**
   - Lifecycle at daily granularity only
   - Vision data is daily klines (not tick-level)
   - Acceptable for monthly/daily advisory workflows

3. **Lifecycle Snapshot Time-Dependent**
   - `days_since_last` computed at manifest generation time
   - Manifests don't auto-update (static snapshots)
   - Acceptable: rebuild dataset monthly

4. **No Historical Delisting Tracking**
   - Only knows "last data date" (not delisting reason)
   - Can't distinguish: voluntarily delisted vs data gap vs stale
   - Acceptable: status=STALE covers all "no recent data" cases

## Next Steps (Phase 4)

### Vision-First Data Management

**Goal:** Establish Vision bulk files as data backbone, REST API for tail updates only

**Key Features:**
- One-time bulk download from Vision (NO VPN, ~500MB for 541 instruments)
- REST API only for last 7 days (requires VPN, small updates)
- Don't rebuild 541-instrument parquet daily
- Canonical raw store with lifecycle-aware status

**Benefits:**
- Lifecycle derived during dataset build (not separate process)
- Vision data = single source of truth
- No VPN dependency for historical data

## Files Modified

### New Files
- `tests/test_phase3_lifecycle.py` - Unit tests (8/8 passing)
- `scripts/verify_phase3.sh` - Verification script
- `docs/phase3_lifecycle_summary.md` - This document

### Modified Files
- `scripts/build_example_dataset.py`
  - Added `derive_lifecycle_from_vision_data()` function
  - Enhanced `generate_dataset_manifest()` to include lifecycle
- `sysdata/crypto/dynamic_universe.py`
  - Added `load_lifecycle_from_manifest()` function
  - Added `check_lifecycle_eligibility()` function

### No Changes Required
- Dataset manifests (backward compatible - old manifests work, just lack lifecycle section)
- Advisory workflow (lifecycle optional - works without it)
- Backtest runner (lifecycle filtering opt-in)

## Verification Checklist

- [x] Unit tests passing (8/8)
- [x] Lifecycle derivation working
- [x] Status determination working (ACTIVE/STALE/NO_DATA)
- [x] Manifest persistence working
- [x] Lifecycle loading working
- [x] Eligibility filtering working
- [x] Lifecycle summary computation
- [x] Backward compatibility (old manifests work)
- [x] Documentation complete

## Conclusion

Phase 3 successfully implements lifecycle tracking derived from Vision data availability. Instrument lifecycle metadata is now automatically computed during dataset building and stored in manifests for reproducible backtesting.

**Key Achievement:** More accurate lifecycle tracking than CoinGecko approach, integrated with dataset pipeline.

**Next:** Phase 4 - Vision-first data management (separate bulk download from tail updates).
