# Phase 2 Implementation Summary: Dataset Manifest Generation

**Status**: ✅ Complete
**Date**: 2026-02-09
**Commit**: 5f34ced2

## Objective

Add dataset manifest generation to track inclusion/exclusion audit trail with deterministic naming and atomic writes.

## What Was Implemented

### 1. Manifest Generation with Atomic Writes

#### Key Features

- **Deterministic naming**: `X.parquet` → `X.manifest.json` (same directory)
- **Atomic write**: Write to temp file then rename (ensures manifest always corresponds to current dataset)
- **Hard invariant**: `set(manifest.included) == set(dataset instruments)` (asserted at generation time)
- **Stable taxonomy**: `load_error`, `missing_funding`, `insufficient_coverage`, `schema_mismatch`

#### Function: `generate_dataset_manifest()`

```python
def generate_dataset_manifest(
    dataset_df: pd.DataFrame,
    instruments_included: dict,
    instruments_excluded: dict,
    start_date: str,
    end_date: str,
    output_path: Path
) -> dict:
    """
    Generate dataset manifest with inclusion/exclusion audit trail.

    - Verifies hard invariant: manifest included set == dataset instruments
    - Uses atomic write (temp + rename) for consistency
    - Returns manifest dict
    """
```

#### Manifest Structure

```json
{
  "generated_at": "2026-02-09T22:56:19.123456Z",
  "dataset_metadata": {
    "requested_start_date": "2020-01-01",
    "requested_end_date": "2024-12-31",
    "actual_start_date": "2020-01-01",
    "actual_end_date": "2024-10-31"
  },
  "date_range": {
    "start": "2020-01-01",
    "end": "2024-10-31",
    "total_days": 1765
  },
  "instruments": {
    "included": {
      "BTCUSDT_PERP": {
        "date_range": {"start": "2020-01-01", "end": "2024-10-31"},
        "coverage_days": 1765,
        "coverage_pct": 0.998,
        "funding_coverage_pct": 1.0,
        "schema_compliant": true
      }
    },
    "excluded": {
      "ETHUSDT_PERP": {
        "reason": "load_error"
      }
    }
  },
  "summary": {
    "total_candidates": 5,
    "included_count": 4,
    "excluded_count": 1,
    "exclusion_breakdown": {
      "load_error": 1
    }
  }
}
```

### 2. Dataset Builder Modifications

#### Return Type Change

```python
# Before (Phase 1)
def build_real_crypto_dataset(...) -> pd.DataFrame:
    return df

# After (Phase 2)
def build_real_crypto_dataset(...) -> tuple[pd.DataFrame, dict, dict]:
    return df, instruments_included, instruments_excluded
```

#### Tracking Logic

**Exclusion tracking** (during instrument load):

```python
instruments_excluded = {}

try:
    klines = load_binance_klines(inst, ...)
except FileNotFoundError:
    instruments_excluded[inst] = "load_error"
    continue
except Exception:
    instruments_excluded[inst] = "load_error"
    continue

try:
    funding = load_binance_funding_rates(inst, ...)
except FileNotFoundError:
    instruments_excluded[inst] = "missing_funding"
    continue
```

**Inclusion tracking** (after successful load):

```python
instruments_included[inst] = {
    "date_range": {
        "start": inst_df['date'].min().strftime('%Y-%m-%d'),
        "end": inst_df['date'].max().strftime('%Y-%m-%d')
    },
    "coverage_days": len(inst_df),
    "coverage_pct": len(inst_df) / requested_days,
    "funding_coverage_pct": funding_coverage_pct,  # % of days with funding present
    "schema_compliant": True  # Passed validation
}
```

### 3. Main Function Integration

```python
# Unpack tuple return
df, instruments_included, instruments_excluded = build_real_crypto_dataset(...)

# Generate manifest with deterministic naming
manifest_path = output_path.with_suffix('.manifest.json')
manifest = generate_dataset_manifest(
    dataset_df=df,
    instruments_included=instruments_included,
    instruments_excluded=instruments_excluded,
    start_date=start_date,
    end_date=end_date,
    output_path=manifest_path
)

# Save dataset
df.to_parquet(output_path, index=False)
```

### 4. Tests

**File**: `tests/test_dataset_manifest_phase2.py`

Six tests, all passing:

1. **test_manifest_structure** - Verifies all required sections present
2. **test_exclusion_breakdown** - Verifies exclusion reasons counted correctly
3. **test_manifest_invariant_holds** - Verifies included set == dataset instruments
4. **test_manifest_invariant_violation_detected** - Verifies RuntimeError on violation
5. **test_atomic_write** - Verifies atomic write (temp + rename) works
6. **test_deterministic_naming** - Verifies X.parquet → X.manifest.json convention

## Design Decisions

### 1. Deterministic Naming (Constraint #1)

**Requirement**: Manifest name tied to dataset name (not just date)

**Implementation**:
```python
manifest_path = output_path.with_suffix('.manifest.json')
# Example: dataset_5inst.parquet → dataset_5inst.manifest.json
```

**Rationale**: Ensures manifest always corresponds to specific dataset version.

### 2. Dataset Builder is Source of Truth (Constraint #2)

**Requirement**: Don't reuse exclusion_recommendation from data_status

**Implementation**:
- Explicit inclusion checks in `build_real_crypto_dataset()`
- Stable taxonomy: `load_error`, `missing_funding`, `insufficient_coverage`, `schema_mismatch`
- Track exclusions during build, not from external report

**Rationale**: Dataset builder knows exactly why instruments were excluded during build.

### 3. Minimal Metadata (Constraint #3)

**Requirement**: Track lightweight per-instrument metadata

**Implementation**:
```python
{
    "date_range": {"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"},
    "coverage_days": int,
    "coverage_pct": float,  # Relative to requested window
    "funding_coverage_pct": float,  # Presence-based
    "schema_compliant": bool
}
```

**Rationale**: Keep manifest generation fast, avoid expensive diagnostics.

### 4. Hard Invariant Assertion (Constraint #2)

**Requirement**: `set(manifest.included) == set(dataset instruments)`

**Implementation**:
```python
manifest_included = set(manifest["instruments"]["included"].keys())
dataset_instruments = set(dataset_df['instrument'].unique())

if manifest_included != dataset_instruments:
    raise RuntimeError(
        f"Manifest consistency check failed: "
        f"included={sorted(manifest_included)} != dataset={sorted(dataset_instruments)}"
    )
```

**Rationale**: Ensures manifest is always accurate representation of dataset contents.

### 5. Atomic Write

**Requirement**: Manifest always corresponds to current dataset

**Implementation**:
```python
# Write to temp file
fd, temp_path = tempfile.mkstemp(dir=output_dir, suffix='.json', prefix='.manifest_tmp_')
with os.fdopen(fd, 'w') as f:
    json.dump(manifest, f, indent=2)

# Atomic rename (overwrites existing)
os.rename(temp_path, output_path)
```

**Rationale**: Prevents partial writes or inconsistent state if process crashes.

## Command Sequence: Build Dataset with Manifest (env=dev)

### Minimal Test (5 instruments, already downloaded)

```bash
# Build dataset from existing dev data (5 instruments)
python scripts/build_example_dataset.py \
  --source real \
  --data-dir envs/dev/data/raw/binance \
  --start-date 2020-01-01 \
  --end-date 2024-10-31 \
  --output-path envs/dev/out/dataset_5inst_test.parquet \
  --allow-jagged \
  --min-coverage 0.80

# Expected output:
#   Building dataset from real Binance Data Vision files...
#   Processing BTCUSDT_PERP...
#   Processing ETHUSDT_PERP...
#   ... (5 instruments)
#   Generating manifest: envs/dev/out/dataset_5inst_test.manifest.json...
#   Dataset manifest saved (atomic): envs/dev/out/dataset_5inst_test.manifest.json
#   Saving to envs/dev/out/dataset_5inst_test.parquet...
#   Dataset created successfully!
```

### Verify Manifest

```bash
# Check manifest structure
jq 'keys' envs/dev/out/dataset_5inst_test.manifest.json
# Expected: ["date_range", "dataset_metadata", "generated_at", "instruments", "summary"]

# Check summary
jq '.summary' envs/dev/out/dataset_5inst_test.manifest.json
# Expected: {"total_candidates": N, "included_count": M, "excluded_count": K, "exclusion_breakdown": {...}}

# Check included instruments
jq '.instruments.included | keys' envs/dev/out/dataset_5inst_test.manifest.json
# Expected: ["BTCUSDT_PERP", "ETHUSDT_PERP", "BNBUSDT_PERP", "SOLUSDT_PERP", "XRPUSDT_PERP"]
# (or subset if some excluded due to data issues)

# Check excluded instruments (if any)
jq '.instruments.excluded' envs/dev/out/dataset_5inst_test.manifest.json
# Expected: {} or {"INST_ID": {"reason": "load_error"}}

# Verify hard invariant (manifest included == dataset instruments)
python -c "
import pandas as pd
import json
from pathlib import Path

df = pd.read_parquet('envs/dev/out/dataset_5inst_test.parquet')
dataset_insts = sorted(df['instrument'].unique())

with open('envs/dev/out/dataset_5inst_test.manifest.json') as f:
    manifest = json.load(f)

manifest_insts = sorted(manifest['instruments']['included'].keys())

print(f'Dataset instruments: {dataset_insts}')
print(f'Manifest included:   {manifest_insts}')
print(f'Invariant holds: {dataset_insts == manifest_insts}')
assert dataset_insts == manifest_insts, 'Invariant violated!'
print('✓ Hard invariant verified')
"
# Expected: ✓ Hard invariant verified
```

### Verify Atomic Write

```bash
# Build dataset twice (second should overwrite atomically)
python scripts/build_example_dataset.py \
  --source real \
  --data-dir envs/dev/data/raw/binance \
  --start-date 2020-01-01 \
  --end-date 2024-10-31 \
  --output-path envs/dev/out/dataset_atomic_test.parquet \
  --allow-jagged \
  --min-coverage 0.80

# Wait a second
sleep 2

# Build again (overwrites)
python scripts/build_example_dataset.py \
  --source real \
  --data-dir envs/dev/data/raw/binance \
  --start-date 2020-01-01 \
  --end-date 2024-10-31 \
  --output-path envs/dev/out/dataset_atomic_test.parquet \
  --allow-jagged \
  --min-coverage 0.80

# Verify manifest was overwritten (check generated_at timestamp)
jq '.generated_at' envs/dev/out/dataset_atomic_test.manifest.json
# Expected: timestamp from second build (not first)
```

## Files Modified

### New Files
- `tests/test_dataset_manifest_phase2.py` (200+ lines)

### Modified Files
- `scripts/build_example_dataset.py`:
  - Added `generate_dataset_manifest()` function (110 lines)
  - Modified `build_real_crypto_dataset()` return type
  - Added tracking for `instruments_included` and `instruments_excluded`
  - Modified main() to generate manifest
  - Total: +450 lines

## Success Criteria Met ✅

- [x] Deterministic manifest naming: X.parquet → X.manifest.json
- [x] Atomic write (temp + rename)
- [x] Hard invariant: `set(manifest.included) == set(dataset instruments)` (asserted)
- [x] Stable taxonomy: load_error, missing_funding, insufficient_coverage, schema_mismatch
- [x] Minimal per-instrument metadata tracked
- [x] Unit tests for manifest structure + exclusion breakdown
- [x] Integration test for hard invariant
- [x] All 6 Phase 2 tests passing

## Next Steps

### Phase 3: Research Universe Expansion (Deferred)

- Separate research configs from production configs
- Run backtests on broader candidate pool (15-30 instruments)
- Document performance across broader universe

### Phase 4: Controlled Promotion to Production (Deferred)

- Define promotion checklist (data quality, liquidity, backtest criteria)
- Staged rollout (dev → paper → prod)
- Audit trail for universe changes

## References

- Implementation Plan: Provided by user
- Phase 1 Commit: 70f847b0, 1d0e0c94
- Phase 2 Commit: 5f34ced2
- Branch: develop
