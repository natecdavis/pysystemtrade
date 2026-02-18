# Raw Format Decision

**Date:** 2026-02-15
**Decision:** ZIP files are the canonical raw format

## Rationale

The existing pipeline in `scripts/build_example_dataset.py` expects:
- **Klines**: `data/raw/binance/klines/{SYMBOL}/{SYMBOL}-1d-YYYY-MM.zip` containing CSV files
- **Funding rates**: `data/raw/binance/funding_rates/{SYMBOL}/{SYMBOL}-fundingRate-YYYY-MM.zip` containing CSV files

This is the native format from Binance Data Vision and is already implemented in:
- `load_binance_klines()` (line 214): Uses `zipfile.ZipFile()` to read monthly ZIPs
- `load_binance_funding()` (line 485): Uses `zipfile.ZipFile()` to read funding ZIPs
- API cache integration (lines 365, 563): Parquet format for tail updates only

## Implications for Vision Bulk Downloader

The Vision bulk downloader (`scripts/download_vision_bulk.py`) should:

1. **Download and store ZIPs as-is** (no parsing into parquet during download)
2. **Match existing directory structure**:
   ```
   data/raw/binance/
   ├── klines/
   │   └── BTCUSDT/
   │       ├── BTCUSDT-1d-2019-09.zip
   │       ├── BTCUSDT-1d-2019-10.zip
   │       └── ...
   └── funding_rates/
       └── BTCUSDT/
           ├── BTCUSDT-fundingRate-2019-09.zip
           └── ...
   ```
3. **Let `build_example_dataset.py` handle parsing** (it already has robust header detection, deduplication, etc.)

## Benefits

- ✅ No parallel raw stores (single source of truth)
- ✅ Minimal changes to existing pipeline
- ✅ Preserves exact Vision data format (reproducibility)
- ✅ API cache remains parquet (for tail updates only)
- ✅ Dataset builder already has all parsing logic

## Migration Not Required

The 49 legacy instruments already have ZIPs in the correct format. No migration needed.
