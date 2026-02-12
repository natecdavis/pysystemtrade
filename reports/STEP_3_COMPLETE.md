# Step 3: Download Extension Complete

## Summary

Successfully extended data coverage through 2026-01-26 and built all datasets.

**Date:** 2026-01-26
**Status:** ✅ COMPLETE

## What Was Accomplished

### 1. Downloaded Extended Data (2025 + Jan 2026)

**Command:**
```bash
python3 scripts/download_binance_extended.py --all --start-date 2025-01-01 --end-date 2026-01-26
```

**Results:**
- ✓ Downloaded: 138 files (46.9 KB) - Daily klines for Jan 2026
- ○ Skipped (existing): 528 files (524.3 KB) - 2025 monthly data already existed
- ⚠ Skipped (404): 474 files - Daily funding rates (not published), EOSUSDT/MATICUSDT 2026 data (delisted)
- ✗ Failed: 0 files

**Coverage:** 13 of 15 instruments have complete data through Jan 25, 2026

**Delistings Discovered:**
- EOSUSDT: Delisted 2026-01-01 (all Jan 2026 files return 404)
- MATICUSDT: No klines since Sep 2024 (funding rates continue through Dec 2025)

### 2. Fixed Critical Bugs

#### Bug 1: Jagged Panel Validation (Step 1)
**Issue:** `--allow-jagged` flag was ignored during validation
**Fix:** Updated `scripts/build_example_dataset.py` lines 821-945
- Per-instrument coverage validation for jagged panels
- Conditional final pivot NaN check based on allow_jagged flag
**Documented in:** `JAGGED_PANEL_FIX.md`

#### Bug 2: Date Dtype Mismatch
**Issue:** Funding rate merge was failing with "ALL dates missing" error
**Root Cause:** Funding date dtype used `.dt.tz_localize(None)` while klines used `.dt.tz_convert(None)`
**Fix:** Line 381 - Changed to match klines: `pd.to_datetime(daily['date'], utc=True).dt.tz_convert(None)`

#### Bug 3: Duplicate Data Directories
**Issue:** Script was looking in `data/raw/klines/` (old 2020-2024 data) instead of `data/raw/binance/klines/` (new 2025-2026 data)
**Fix:** Merged directories using rsync to consolidate all data in `data/raw/`

### 3. Built All Datasets

| Dataset | Date Range | Instruments | Rows | Size | Status |
|---------|------------|-------------|------|------|--------|
| 7x5yr | 2020-2024 | 7 | 12,775 | 360 KB | ✅ Created |
| 15x2yr | 2023-2024 | 15 | 9,885 | 219 KB | ✅ Created |
| 15x5yr_jagged | 2020-2024 | 15 (jagged) | 26,025 | 554 KB | ✅ Exists |
| 13x13mo | 2025-2026 | 13 | 5,070 | 128 KB | ✅ Created |

**Note:** 13x13mo excludes EOSUSDT and MATICUSDT due to delistings

## Files Created/Modified

### New Scripts
- `scripts/download_binance_extended.py` - Monthly + daily download logic
- `scripts/build_all_datasets.sh` - Unified dataset builder

### Modified Scripts
- `scripts/build_example_dataset.py` - Fixed jagged panel validation, date dtype, added debug logging

### Documentation
- `DOWNLOAD_EXTENSION_REPORT.md` - Download results and data availability analysis
- `JAGGED_PANEL_FIX.md` - Jagged panel bug fix documentation
- `STEP_3_COMPLETE.md` - This file

### Data
- `data/raw/metadata/binance_symbol_lifecycle.json` - Updated with EOSUSDT and MATICUSDT delistings

### Datasets
- `data/example_crypto_perps_7x5yr.parquet`
- `data/example_crypto_perps_15x2yr.parquet`
- `data/example_crypto_perps_15x5yr_jagged.parquet` (previously built)
- `data/example_crypto_perps_13x13mo.parquet`

## Key Findings

### Data Availability Issues
1. **2019 Data:** Not available on Binance Data Vision (all 404s)
2. **Daily Funding Rates:** Not published (only monthly aggregates)
3. **EOSUSDT:** Delisted 2026-01-01
4. **MATICUSDT:** Klines stopped Sep 2024 (funding continues)

### Technical Issues Resolved
1. Jagged panel validation was incorrectly enforcing rectangular constraints
2. Date dtype mismatch prevented funding rate merges
3. Duplicate data directory structure caused incomplete file loading

## Validation

All datasets validated successfully:
- ✓ Correct row counts
- ✓ No NaN in rectangular panels (except Jan 2026 funding - expected)
- ✓ Jagged panel allows NaN for pre-launch dates
- ✓ Date ranges match expectations
- ✓ All instruments present (excluding delistings)

## Next Steps

Per user's original plan:
1. ✅ Fixed jagged panel validation bug
2. ✅ Kept all 15 instruments (with documented exceptions)
3. ✅ Extended downloads through 2026-01-26

**Ready for:**
- Run Phase 1 backtests on new datasets
- Optional: Run Phase 2 backtests with dynamic universe

**Commands:**
```bash
# Run all backtests
bash scripts/run_all_backtests.sh

# Or run individual backtests
PYTHONPATH=. python systems/crypto_perps/system.py \
  --config config/crypto_perps_baseline_v1.yaml \
  --data data/example_crypto_perps_7x5yr.parquet \
  --outdir out/baseline_7x5yr
```
