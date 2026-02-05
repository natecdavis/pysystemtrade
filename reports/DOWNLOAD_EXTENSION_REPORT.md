# Download Extension Report (2025 + Jan 2026)

## Summary

Successfully extended data coverage through 2026-01-26 using monthly + daily download logic.

**Date:** 2026-01-26
**Script:** `scripts/download_binance_extended.py`
**Command:**
```bash
python3 scripts/download_binance_extended.py --all --start-date 2025-01-01 --end-date 2026-01-26
```

## Download Strategy

### Monthly Downloads (Complete Months)
- **Date range:** 2025-01-01 to 2025-12-31 (12 complete months)
- **Method:** Monthly ZIP files from `/monthly/` endpoint
- **Files per symbol:** 24 files (12 months × 2 types: klines + funding)

### Daily Downloads (Incomplete Month)
- **Date range:** 2026-01-01 to 2026-01-26 (26 days)
- **Method:** Daily ZIP files from `/daily/` endpoint
- **Files per symbol:** 26 klines files (1 per day)
- **Note:** Daily funding rate files returned 404 (Binance only publishes monthly funding aggregates)

## Download Results

### Overall Statistics
- ✓ **Downloaded:** 138 files (46.9 KB)
- ○ **Skipped (existing):** 528 files (524.3 KB) - 2025 monthly data already existed
- ⚠ **Skipped (404):** 474 files
  - All daily funding rate files for Jan 2026 (390 files)
  - Jan 26 klines (not yet available)
  - EOSUSDT and MATICUSDT 2026 data (delisted/unavailable)
- ✗ **Failed:** 0 files

### Per-Instrument Coverage (Jan 2026 Daily Klines)

| Instrument | Daily Files | Status |
|------------|-------------|--------|
| BTCUSDT | 25 | ✓ Complete (Jan 1-25) |
| ETHUSDT | 25 | ✓ Complete (Jan 1-25) |
| BNBUSDT | 25 | ✓ Complete (Jan 1-25) |
| XRPUSDT | 25 | ✓ Complete (Jan 1-25) |
| LTCUSDT | 25 | ✓ Complete (Jan 1-25) |
| EOSUSDT | 0 | ⚠ All 404 (likely delisted) |
| BCHUSDT | 25 | ✓ Complete (Jan 1-25) |
| LINKUSDT | 25 | ✓ Complete (Jan 1-25) |
| SOLUSDT | 25 | ✓ Complete (Jan 1-25) |
| DOTUSDT | 25 | ✓ Complete (Jan 1-25) |
| ADAUSDT | 25 | ✓ Complete (Jan 1-25) |
| UNIUSDT | 25 | ✓ Complete (Jan 1-25) |
| MATICUSDT | 0 | ⚠ All 404 (no klines since Sep 2024) |
| DOGEUSDT | 25 | ✓ Complete (Jan 1-25) |
| AVAXUSDT | 25 | ✓ Complete (Jan 1-25) |

**Total:** 13 of 15 instruments have complete Jan 2026 coverage through Jan 25

## Data Availability Issues

### 1. MATICUSDT: No Klines Since September 2024
- **Issue:** No kline files for 2025 or 2026 (all 404s)
- **Funding rates:** Available through Dec 2025
- **Root cause:** Binance stopped publishing MATICUSDT klines after Sep 2024 (confirmed in DOWNLOAD_REALITY.md)
- **Impact:** MATICUSDT cannot be included in datasets requiring 2025+ data

### 2. EOSUSDT: No Data for Jan 2026
- **Issue:** Monthly klines available through Dec 2025, but all Jan 2026 daily files return 404
- **Root cause:** Likely delisted or trading suspended in early 2026
- **Impact:** EOSUSDT should be excluded from datasets starting 2026-01-01

### 3. Daily Funding Rate Files: Not Published
- **Issue:** All daily funding rate files for Jan 2026 returned 404
- **Root cause:** Binance only publishes funding rates as monthly aggregates, not daily files
- **Impact:** Must use monthly funding rate files; daily granularity not available from Binance Data Vision

### 4. Jan 26, 2026 Klines: Not Yet Available
- **Issue:** `*-2026-01-26.zip` files returned 404 for all instruments
- **Root cause:** Data may not be finalized/published yet (today is Jan 26)
- **Impact:** Latest available data is through Jan 25, 2026

## Recommended Dataset Configurations

Based on actual data availability:

### Option 1: 13-Instrument Extended (2025 + Jan 2026)
**Instruments:** All except EOSUSDT and MATICUSDT
- BTCUSDT, ETHUSDT, BNBUSDT, XRPUSDT, LTCUSDT, BCHUSDT, LINKUSDT, SOLUSDT, DOTUSDT, ADAUSDT, UNIUSDT, DOGEUSDT, AVAXUSDT

**Date range:** 2025-01-01 to 2026-01-25

**Build command:**
```bash
python3 scripts/build_example_dataset.py \
  --source real \
  --start-date 2025-01-01 \
  --end-date 2026-01-25 \
  --instruments BTCUSDT_PERP ETHUSDT_PERP BNBUSDT_PERP XRPUSDT_PERP \
               LTCUSDT_PERP BCHUSDT_PERP LINKUSDT_PERP \
               SOLUSDT_PERP DOTUSDT_PERP ADAUSDT_PERP UNIUSDT_PERP \
               DOGEUSDT_PERP AVAXUSDT_PERP \
  --output-path data/example_crypto_perps_13x13mo.parquet \
  --min-coverage 0.95
```

### Option 2: 14-Instrument (2025 Only)
**Instruments:** All except MATICUSDT
- Includes EOSUSDT (has complete 2025 data)

**Date range:** 2025-01-01 to 2025-12-31

**Build command:**
```bash
python3 scripts/build_example_dataset.py \
  --source real \
  --start-date 2025-01-01 \
  --end-date 2025-12-31 \
  --instruments BTCUSDT_PERP ETHUSDT_PERP BNBUSDT_PERP XRPUSDT_PERP \
               LTCUSDT_PERP EOSUSDT_PERP BCHUSDT_PERP LINKUSDT_PERP \
               SOLUSDT_PERP DOTUSDT_PERP ADAUSDT_PERP UNIUSDT_PERP \
               DOGEUSDT_PERP AVAXUSDT_PERP \
  --output-path data/example_crypto_perps_14x1yr.parquet \
  --min-coverage 0.95
```

### Option 3: 15-Instrument Jagged (2020-2024)
**Instruments:** All 15 (using previously downloaded 2020-2024 data)

**Date range:** 2020-01-01 to 2024-09-30 (avoids MATICUSDT gap)

**Build command:**
```bash
python3 scripts/build_example_dataset.py \
  --source real \
  --start-date 2020-01-01 \
  --end-date 2024-09-30 \
  --instruments BTCUSDT_PERP ETHUSDT_PERP BNBUSDT_PERP XRPUSDT_PERP \
               LTCUSDT_PERP EOSUSDT_PERP BCHUSDT_PERP LINKUSDT_PERP \
               SOLUSDT_PERP DOTUSDT_PERP ADAUSDT_PERP UNIUSDT_PERP \
               MATICUSDT_PERP DOGEUSDT_PERP AVAXUSDT_PERP \
  --output-path data/example_crypto_perps_15x5yr_jagged.parquet \
  --allow-jagged \
  --min-coverage 0.60
```

**Note:** This dataset was already built successfully (see JAGGED_PANEL_FIX.md)

## Files Modified/Created

### New Scripts
- `scripts/download_binance_extended.py` - Extended downloader with monthly + daily logic

### Documentation
- `DOWNLOAD_EXTENSION_REPORT.md` - This file

## Next Steps

1. **Update lifecycle metadata** to mark EOSUSDT as delisted 2026-01-01, MATICUSDT as delisted 2024-10-01
2. **Build revised datasets** using the recommended configurations above
3. **Update completeness checks** to account for instrument delistings
4. **Run Phase 1 backtests** on the new datasets

## Completeness Check Recommendations

Update `scripts/check_data_completeness.py` to:
1. Skip MATICUSDT for date ranges after 2024-09-30
2. Skip EOSUSDT for date ranges after 2025-12-31
3. Handle daily vs monthly file logic for partial months
4. Validate that funding rates use monthly files only (no daily files expected)
