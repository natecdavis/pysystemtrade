# Binance Data Vision - Historical Data

This directory contains historical crypto perpetual futures data downloaded from [Binance Data Vision](https://data.binance.vision/).

## Data Sources

### Klines (OHLCV Data)
- **Source**: `https://data.binance.vision/data/futures/um/daily/klines/{SYMBOL}/1d/`
- **Format**: ZIP archives containing CSV files (no header)
- **Interval**: 1 day (daily candles)
- **Coverage**: See table below

### Funding Rates
- **Source**: `https://data.binance.vision/data/futures/um/daily/fundingRate/{SYMBOL}/`
- **Format**: ZIP archives containing CSV files (no header)
- **Frequency**: 8-hourly observations (00:00, 08:00, 16:00 UTC)
- **Coverage**: See table below

## Download Instructions

### Manual Download

For each instrument symbol (BTCUSDT, ETHUSDT, BNBUSDT, SOLUSDT, XRPUSDT):

1. **Klines**:
   ```bash
   # Navigate to the web interface
   https://data.binance.vision/?prefix=data/futures/um/daily/klines/{SYMBOL}/1d/

   # Download ZIP files for desired date range
   # Example: BTCUSDT-1d-2023-01.zip, BTCUSDT-1d-2023-02.zip, etc.

   # Save to: data/raw/binance/klines/{SYMBOL}/
   ```

2. **Funding Rates**:
   ```bash
   # Navigate to the web interface
   https://data.binance.vision/?prefix=data/futures/um/daily/fundingRate/{SYMBOL}/

   # Download ZIP files for desired date range
   # Example: BTCUSDT-fundingRate-2023-01.zip, etc.

   # Save to: data/raw/binance/funding_rates/{SYMBOL}/
   ```

3. **Optional Checksums**:
   - Download corresponding `.CHECKSUM` files if you want to verify data integrity
   - Use `--verify-checksums` flag when building dataset

### Example wget Script

```bash
#!/bin/bash
# Download script for Binance historical data

SYMBOLS=("BTCUSDT" "ETHUSDT" "BNBUSDT" "SOLUSDT" "XRPUSDT")
BASE_URL="https://data.binance.vision/data/futures/um/daily"
START_YEAR=2023
END_YEAR=2024

for symbol in "${SYMBOLS[@]}"; do
    echo "Downloading $symbol..."

    # Download klines
    mkdir -p data/raw/binance/klines/$symbol
    for year in $(seq $START_YEAR $END_YEAR); do
        for month in {01..12}; do
            wget -P data/raw/binance/klines/$symbol \
                "$BASE_URL/klines/$symbol/1d/$symbol-1d-$year-$month.zip" \
                2>/dev/null || true
        done
    done

    # Download funding rates
    mkdir -p data/raw/binance/funding_rates/$symbol
    for year in $(seq $START_YEAR $END_YEAR); do
        for month in {01..12}; do
            wget -P data/raw/binance/funding_rates/$symbol \
                "$BASE_URL/fundingRate/$symbol/$symbol-fundingRate-$year-$month.zip" \
                2>/dev/null || true
        done
    done
done

echo "Download complete!"
```

## Data Coverage

| Instrument | Symbol | Klines Start | Funding Start | Notes |
|------------|--------|--------------|---------------|-------|
| BTCUSDT_PERP | BTCUSDT | TBD | TBD | To be filled after download |
| ETHUSDT_PERP | ETHUSDT | TBD | TBD | To be filled after download |
| BNBUSDT_PERP | BNBUSDT | TBD | TBD | To be filled after download |
| SOLUSDT_PERP | SOLUSDT | TBD | TBD | May have shorter history (later launch) |
| XRPUSDT_PERP | XRPUSDT | TBD | TBD | To be filled after download |

## Known Issues

- **Coverage Gaps**: Some instruments may have gaps due to exchange downtime or data issues
- **Launch Dates**: SOL and other newer instruments may not have full 2-year history
- **File Naming**: Binance uses various naming conventions; our script uses glob patterns to discover all files

## File Structure

```
data/raw/binance/
├── klines/
│   ├── BTCUSDT/
│   │   └── BTCUSDT-1d-*.zip  (discovered by glob pattern)
│   ├── ETHUSDT/
│   ├── BNBUSDT/
│   ├── SOLUSDT/
│   └── XRPUSDT/
├── funding_rates/
│   ├── BTCUSDT/
│   │   └── BTCUSDT-fundingRate-*.zip  (discovered by glob pattern)
│   ├── ETHUSDT/
│   ├── BNBUSDT/
│   ├── SOLUSDT/
│   └── XRPUSDT/
└── README.md (this file)
```

## Validation

After downloading, verify files are present:

```bash
# Check klines
ls -lh data/raw/binance/klines/*/

# Check funding rates
ls -lh data/raw/binance/funding_rates/*/

# Count files per instrument
for symbol in BTCUSDT ETHUSDT BNBUSDT SOLUSDT XRPUSDT; do
    echo "$symbol klines: $(ls data/raw/binance/klines/$symbol/*.zip 2>/dev/null | wc -l)"
    echo "$symbol funding: $(ls data/raw/binance/funding_rates/$symbol/*.zip 2>/dev/null | wc -l)"
done
```

## Last Updated

- **Date**: TBD (fill in after initial download)
- **Data Range**: 2023-01-01 to 2024-12-31 (target)
- **Downloaded By**: TBD

## Building Dataset

Once files are downloaded, build the parquet dataset:

```bash
# Using real data
python scripts/build_example_dataset.py --source real \
    --start-date 2023-01-01 \
    --end-date 2024-12-31 \
    --data-dir data/raw

# With checksum verification
python scripts/build_example_dataset.py --source real \
    --start-date 2023-01-01 \
    --end-date 2024-12-31 \
    --data-dir data/raw \
    --verify-checksums

# Inspect alignment before first build (recommended)
python scripts/build_example_dataset.py --inspect-alignment \
    --klines data/raw/binance/klines/BTCUSDT/BTCUSDT-1d-2023-01.zip \
    --funding data/raw/binance/funding_rates/BTCUSDT/BTCUSDT-fundingRate-2023-01.zip \
    --sample-days 3
```

## References

- [Binance Data Vision](https://data.binance.vision/)
- [Binance Futures API Documentation](https://developers.binance.com/docs/derivatives/usds-margined-futures)
- [System Design Spec](../../CRYPTO_PERPETUAL_FUTURES_TRADING_SYSTEM_PLANNING_DESIGN_DOCUMENT_AGENT_READY_REVISION.md)
