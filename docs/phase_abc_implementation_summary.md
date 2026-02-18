# Phase A-B-C Implementation Summary

**Date:** 2026-02-15
**Status:** Implementation complete, tests passing

## Phase A: Doctor Semantics & Jagged Panels ✅

### Changes

1. **Allowlist Semantics** (`scripts/doctor_live_ops.py:251`)
   - Changed `allow_missing_instruments=False` → `allow_missing_instruments=True`
   - Missing layer_a instruments now generate **warnings** (not errors)
   - Positions with extra instruments (not in layer_a) are allowed but ignored

2. **Data-Status Source Resolver** (`scripts/doctor_live_ops.py:52-84`)
   - New function: `resolve_data_status_path()`
   - Priority: CLI arg → daily flow output (`envs/dev/out/raw_data_status_v1.json`) → fallback
   - Staleness detection: warns if lag > 7 days

3. **Jagged Panel Support** (`scripts/doctor_live_ops.py:311-370`)
   - New function: `is_jagged_mode()` - detects from `allow_jagged` or `dynamic_universe.enabled`
   - Updated: `check_rectangular_panel()` - mode-aware validation
   - Jagged mode: NaNs → PASS_WITH_WARNINGS (informational)
   - Rectangular mode: NaNs → FAIL (strict)

4. **Integration** (`scripts/doctor_live_ops.py:538-570`)
   - Uses resolver in main()
   - Passes config to rectangular panel check

### Tests

**File:** `tests/test_doctor_semantics.py`

7/7 tests passing:
- ✅ Jagged mode detection
- ✅ Jagged mode NaNs → PASS_WITH_WARNINGS
- ✅ Rectangular mode NaNs → FAIL
- ✅ Rectangular mode no NaNs → PASS
- ✅ Missing layer_a instruments → WARNING
- ✅ Extra position instruments → allowed (no error)
- ✅ All instruments present with low leverage → PASS

### Verification

```bash
# Smoke test: Doctor should PASS_WITH_WARNINGS (not FAIL) with jagged + missing instruments
python scripts/doctor_live_ops.py \
  --env dev \
  --config config/crypto_perps_dynamic_universe_top30.yaml \
  --actual-positions envs/dev/live/current_positions.csv \
  --current-equity-file envs/dev/live/current_equity.txt \
  --cadence daily

# Expected:
# - Exit code: 1 (PASS_WITH_WARNINGS)
# - Warnings: "X instruments in layer_a missing from dataset"
# - Warnings: "Dataset has X NaNs (jagged mode enabled)"
# - Log: "Using daily flow data status: envs/dev/out/raw_data_status_v1.json"
```

---

## Phase B: Positions Auto-Sync Schema ✅

### Changes

**File:** `scripts/sync_positions_file.py`

1. **6-Column Schema** (lines 50-80)
   - Old: `['instrument', 'position', 'entry_price']`
   - New: `['instrument', 'contracts', 'mark_price_usd', 'notional_usd', 'timestamp', 'notes']`

2. **Timestamp Handling** (lines 57-62)
   - **Preserves existing rows exactly** (never modifies timestamps)
   - **New rows only**: Uses sentinel `1970-01-01T00:00:00Z` + notes `"auto_added_zero_row"`
   - **Override**: `--timestamp ISO` flag sets custom timestamp for new rows

3. **CLI Flag** (line 104)
   - New: `--timestamp` parameter for explicit timestamp control

### Usage

```bash
# Add missing instruments with sentinel timestamp
python scripts/sync_positions_file.py \
    --config config/crypto_perps_dynamic_universe_top30.yaml \
    --positions-file envs/dev/live/current_positions.csv

# Add missing instruments with specific timestamp
python scripts/sync_positions_file.py \
    --config config/crypto_perps_dynamic_universe_top30.yaml \
    --positions-file envs/dev/live/current_positions.csv \
    --timestamp 2026-02-14T12:00:00Z

# Verify output
head -5 envs/dev/live/current_positions.csv
# Expected header: instrument,contracts,mark_price_usd,notional_usd,timestamp,notes
```

---

## Phase C: Vision Bulk Downloader ✅

### Raw Format Decision

**Document:** `docs/raw_format_decision.md`

**Decision:** ZIP files are canonical raw format (not parquet)

**Rationale:**
- Existing pipeline (`build_example_dataset.py`) expects ZIPs
- Native Binance Vision format (reproducibility)
- No parallel raw stores needed
- API cache remains parquet (for tail updates only)

### Implementation

**File:** `scripts/download_vision_bulk.py`

1. **Core Download Function** (lines 86-166)
   - Downloads monthly ZIPs from Binance Vision public data
   - Stores in canonical format:
     - `data/raw/binance/klines/{SYMBOL}/{SYMBOL}-1d-YYYY-MM.zip`
     - `data/raw/binance/funding_rates/{SYMBOL}/{SYMBOL}-fundingRate-YYYY-MM.zip`
   - Idempotent: skips existing files
   - Graceful 404 handling (pre-launch instruments)

2. **Layer-A Priority** (lines 189-200)
   - New flag: `--config` to prioritize layer_a instruments
   - Downloads layer_a first, then rest of registry
   - Ensures 30-instrument config works before full 541 download

3. **Progress Tracking** (lines 47-70, 232-237)
   - Saves to `envs/dev/data/raw/vision_download_progress.json`
   - Resumable with `--resume-from SYMBOL`
   - Incremental with `--instruments-limit N`

4. **Dependency** (`requirements.txt:24`)
   - Added `requests>=2.28.0`

### Usage

```bash
# Dry run single instrument
python scripts/download_vision_bulk.py --env dev --instruments-limit 1 --dry-run

# Download layer_a top 30 first
python scripts/download_vision_bulk.py \
    --env dev \
    --config config/crypto_perps_dynamic_universe_top30.yaml \
    --instruments-limit 30

# Resume from specific instrument
python scripts/download_vision_bulk.py \
    --env dev \
    --resume-from ARBUSDT_PERP

# Full 541 batch download
python scripts/download_vision_bulk.py --env dev
```

### Verification

```bash
# Test single instrument
python scripts/download_vision_bulk.py --env dev --instruments-limit 1

# Verify ZIPs created
ls -lh envs/dev/data/raw/binance/klines/*/
ls -lh envs/dev/data/raw/binance/funding_rates/*/

# Check data (requires zipfile parsing)
python -c "
import zipfile
from pathlib import Path

# Find first klines ZIP
klines_dir = Path('envs/dev/data/raw/binance/klines')
zip_files = list(klines_dir.rglob('*.zip'))
if zip_files:
    with zipfile.ZipFile(zip_files[0]) as z:
        print(f'ZIP: {zip_files[0].name}')
        print(f'Files: {z.namelist()}')
"
```

---

## Critical Path Completed

✅ **Phase A:** Doctor semantics (allowlist + jagged panels)
✅ **Phase B:** Positions auto-sync (6-column schema)
✅ **Phase C:** Vision bulk downloader (ZIP canonical format)

**Next Steps:**

1. **Smoke test Phase A** with actual config/data
2. **Download layer_a top 30** with Vision downloader
3. **Phase D:** Turnover diagnostics (after units verification)

---

## Files Modified

### Phase A
- `scripts/doctor_live_ops.py` (+85 lines)
- `tests/test_doctor_semantics.py` (new, 200 lines)

### Phase B
- `scripts/sync_positions_file.py` (+25 lines, schema migration)

### Phase C
- `scripts/download_vision_bulk.py` (+70 lines, full implementation)
- `requirements.txt` (+1 line: requests)
- `docs/raw_format_decision.md` (new, decision rationale)

### Documentation
- `docs/phase_abc_implementation_summary.md` (this file)

---

## Regression Test Summary

**Phase A Tests:** 7/7 passing
```bash
pytest tests/test_doctor_semantics.py -v
```

**Expected output:**
```
tests/test_doctor_semantics.py::test_jagged_mode_detection PASSED
tests/test_doctor_semantics.py::test_jagged_mode_nans_pass_with_warnings PASSED
tests/test_doctor_semantics.py::test_rectangular_mode_nans_fail PASSED
tests/test_doctor_semantics.py::test_rectangular_mode_no_nans_pass PASSED
tests/test_doctor_semantics.py::test_missing_layer_a_instruments_warning PASSED
tests/test_doctor_semantics.py::test_extra_position_instrument_warning PASSED
tests/test_doctor_semantics.py::test_positions_all_present_low_leverage PASSED

7 passed in 0.37s
```
