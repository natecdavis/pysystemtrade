# Phase 2 Hardening Summary

**Status**: ✅ Complete
**Date**: 2026-02-09
**Commit**: 2deef39b

## Hardening Changes

### 1. Fixed Atomic Write Implementation ✅

**Issue**: Used `os.rename()` which has weaker atomicity guarantees than `os.replace()`.

**Fix**:
```python
# Before
os.rename(temp_path, output_path)

# After
os.replace(temp_path, output_path)
```

**Verification**: Temp file correctly created in `output_path.parent` (verified in code review).

**Rationale**: `os.replace()` provides stronger atomicity guarantees and works cross-platform. It ensures the target is atomically replaced even if it exists.

---

### 2. Added Min-History-Days Gating ✅

**Issue**: No gating on minimum history requirement. Instruments with short history could be included.

**Fix**: Added `--min-history-days` parameter (default: 365)

#### Code Changes

**Function signature**:
```python
def build_real_crypto_dataset(
    ...
    min_history_days: int = 365  # NEW
) -> tuple[pd.DataFrame, dict, dict]:
```

**Gating logic** (after instrument load):
```python
coverage_days = len(inst_df)

# Gate on minimum history requirement
if coverage_days < min_history_days:
    logger.warning(
        f"{inst}: Insufficient history - {coverage_days} days < {min_history_days} days"
    )
    instruments_excluded[inst] = "insufficient_history"
    continue
```

**CLI flag**:
```python
parser.add_argument(
    '--min-history-days',
    type=int,
    default=365,
    help='Minimum days of history required per instrument (default: 365). '
         'Instruments with fewer days will be excluded with reason "insufficient_history".'
)
```

**Updated exclusion taxonomy**:
- `load_error`: Failed to load klines or other required data
- `missing_funding`: Failed to load funding rates
- `insufficient_history`: coverage_days < min_history_days (NEW)

---

### 3. Added Regression Test ✅

**New test**: `test_manifest_written_alongside_dataset_and_overwrites_cleanly`

**What it verifies**:
1. Manifest written to same directory as dataset
2. Manifest path uses deterministic naming (`X.parquet` → `X.manifest.json`)
3. Clean overwrite on rerun (timestamps differ, mtime updated)

**Key assertions**:
```python
# Verify manifest in same directory as dataset
assert manifest_path.parent == dataset_path.parent
assert manifest_path.exists()

# Verify clean overwrite (new timestamp)
assert second_timestamp != first_timestamp

# Verify file contents match second write (atomic overwrite)
with open(manifest_path) as f:
    loaded = json.load(f)
assert loaded['generated_at'] == second_timestamp

# Verify mtime was updated
assert second_mtime > first_mtime
```

---

## Test Results

All 17 tests passing (10 Phase 1 + 7 Phase 2):

```
tests/test_candidate_expansion_phase1.py::test_data_acquisition_priority PASSED
tests/test_candidate_expansion_phase1.py::test_backward_compatibility_fallback PASSED
tests/test_candidate_expansion_phase1.py::test_empty_candidate_list_fails_fast PASSED
tests/test_candidate_expansion_phase1.py::test_tradable_instruments_ignores_candidate_list PASSED
tests/test_candidate_expansion_phase1.py::test_instrument_id_to_symbol_mapping PASSED
tests/test_candidate_expansion_phase1.py::test_symbol_to_instrument_id_mapping PASSED
tests/test_candidate_expansion_phase1.py::test_missing_both_sections_fails PASSED
tests/test_candidate_expansion_phase1.py::test_empty_universe_fallback_fails PASSED
tests/test_candidate_expansion_phase1.py::test_real_config_20_candidates PASSED
tests/test_candidate_expansion_phase1.py::test_real_config_backward_compat PASSED
tests/test_dataset_manifest_phase2.py::test_manifest_structure PASSED
tests/test_dataset_manifest_phase2.py::test_exclusion_breakdown PASSED
tests/test_dataset_manifest_phase2.py::test_manifest_invariant_holds PASSED
tests/test_dataset_manifest_phase2.py::test_manifest_invariant_violation_detected PASSED
tests/test_dataset_manifest_phase2.py::test_atomic_write PASSED
tests/test_dataset_manifest_phase2.py::test_deterministic_naming PASSED
tests/test_dataset_manifest_phase2.py::test_manifest_written_alongside_dataset_and_overwrites_cleanly PASSED
```

---

## Updated Command Sequence (env=dev)

### Build Dataset with Min-History-Days Gating

```bash
# Build dataset with min-history-days gating (default: 365 days)
python scripts/build_example_dataset.py \
  --source real \
  --data-dir envs/dev/data/raw/binance \
  --start-date 2020-01-01 \
  --end-date 2024-10-31 \
  --output-path envs/dev/out/dataset_5inst_hardened.parquet \
  --allow-jagged \
  --min-coverage 0.80 \
  --min-history-days 365

# Expected output (if some instruments excluded):
# Processing BTCUSDT_PERP...
# Processing ETHUSDT_PERP...
# ... (some may be excluded)
# WARNING: SOLUSDT_PERP: Insufficient history - 300 days < 365 days
# Generating manifest: envs/dev/out/dataset_5inst_hardened.manifest.json...
#
# Excluded 1 instrument(s):
#   SOLUSDT_PERP: insufficient_history
```

### Verify Exclusion Taxonomy

```bash
# Check exclusion breakdown in manifest
jq '.summary.exclusion_breakdown' envs/dev/out/dataset_5inst_hardened.manifest.json
# Expected: {"insufficient_history": N, "load_error": M, "missing_funding": K}

# Check specific excluded instruments
jq '.instruments.excluded' envs/dev/out/dataset_5inst_hardened.manifest.json
# Expected: {"INST_ID": {"reason": "insufficient_history"}}
```

### Verify Atomic Write and Overwrite

```bash
# Build dataset twice (second should overwrite atomically)
python scripts/build_example_dataset.py \
  --source real \
  --data-dir envs/dev/data/raw/binance \
  --start-date 2020-01-01 \
  --end-date 2024-10-31 \
  --output-path envs/dev/out/dataset_overwrite_test.parquet \
  --allow-jagged \
  --min-coverage 0.80

# Check first timestamp
jq '.generated_at' envs/dev/out/dataset_overwrite_test.manifest.json
# Record timestamp

# Wait and rebuild
sleep 2
python scripts/build_example_dataset.py \
  --source real \
  --data-dir envs/dev/data/raw/binance \
  --start-date 2020-01-01 \
  --end-date 2024-10-31 \
  --output-path envs/dev/out/dataset_overwrite_test.parquet \
  --allow-jagged \
  --min-coverage 0.80

# Check second timestamp (should differ)
jq '.generated_at' envs/dev/out/dataset_overwrite_test.manifest.json
# Should show later timestamp (atomic overwrite worked)
```

### Test Min-History-Days with Different Thresholds

```bash
# Relax to 180 days (more instruments included)
python scripts/build_example_dataset.py \
  --source real \
  --data-dir envs/dev/data/raw/binance \
  --start-date 2020-01-01 \
  --end-date 2024-10-31 \
  --output-path envs/dev/out/dataset_180d.parquet \
  --allow-jagged \
  --min-coverage 0.80 \
  --min-history-days 180

# Compare included counts
jq '.summary.included_count' envs/dev/out/dataset_5inst_hardened.manifest.json
jq '.summary.included_count' envs/dev/out/dataset_180d.manifest.json
# Should see more instruments with lower threshold
```

---

## Exact Diffs

### 1. Atomic Write (os.rename → os.replace)

```diff
- os.rename(temp_path, output_path)
+ # Atomic replace (overwrites existing manifest atomically)
+ # os.replace() provides stronger atomicity guarantees than os.rename()
+ os.replace(temp_path, output_path)
```

### 2. Min-History-Days Gating

**Function signature**:
```diff
 def build_real_crypto_dataset(
     data_dir: Path,
     start_date: str,
     end_date: str,
     instruments: list = None,
     fail_on_missing_close: bool = False,
     min_coverage: float = 0.95,
     verify_checksums: bool = False,
     allow_jagged: bool = False,
     include_api_cache: bool = False,
-    metadata_dir: Path = None
+    metadata_dir: Path = None,
+    min_history_days: int = 365
 ) -> tuple[pd.DataFrame, dict, dict]:
```

**Gating logic**:
```diff
         # Requested date range vs actual coverage
         start_ts = pd.Timestamp(start_date).tz_localize(None)
         end_ts = pd.Timestamp(end_date).tz_localize(None)
         requested_days = (end_ts - start_ts).days + 1
+        coverage_days = len(inst_df)
+
+        # Gate on minimum history requirement
+        if coverage_days < min_history_days:
+            logger.warning(
+                f"{inst}: Insufficient history - {coverage_days} days < {min_history_days} days"
+            )
+            instruments_excluded[inst] = "insufficient_history"
+            continue

         instruments_included[inst] = {
             "date_range": {
                 "start": inst_df['date'].min().strftime('%Y-%m-%d'),
                 "end": inst_df['date'].max().strftime('%Y-%m-%d')
             },
-            "coverage_days": len(inst_df),
+            "coverage_days": coverage_days,
-            "coverage_pct": len(inst_df) / requested_days,
+            "coverage_pct": coverage_days / requested_days,
```

**CLI flag**:
```diff
+    parser.add_argument(
+        '--min-history-days',
+        type=int,
+        default=365,
+        help='Minimum days of history required per instrument (default: 365). '
+             'Instruments with fewer days will be excluded with reason "insufficient_history".'
+    )
```

### 3. Regression Test

```python
def test_manifest_written_alongside_dataset_and_overwrites_cleanly():
    """Verify manifest is written alongside dataset and overwrites cleanly on rerun."""
    # ... setup ...

    # First write
    manifest1 = generate_dataset_manifest(...)
    assert manifest_path.parent == dataset_path.parent
    assert manifest_path.exists()

    # Second write (overwrite)
    manifest2 = generate_dataset_manifest(...)

    # Verify clean overwrite
    assert second_timestamp != first_timestamp
    assert second_mtime > first_mtime
```

---

## Hardening Checklist

- [x] **Atomic write**: Uses `os.replace()` (stronger than `os.rename()`)
- [x] **Temp file location**: Created in `output_path.parent` (verified)
- [x] **Min-history-days gating**: Implemented with CLI flag (default: 365)
- [x] **Stable taxonomy**: `load_error`, `missing_funding`, `insufficient_history`
- [x] **Regression test**: Manifest location and clean overwrite verified
- [x] **All tests passing**: 17/17 (10 Phase 1 + 7 Phase 2)

---

## Summary

Phase 2 hardening complete. All constraints satisfied:

1. ✅ Atomic write uses `os.replace()` for stronger guarantees
2. ✅ Min-history-days gating implemented with stable taxonomy
3. ✅ Regression test verifies manifest location and clean overwrite

**Phase 2 is production-ready and hardened.**
