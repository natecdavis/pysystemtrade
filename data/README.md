# Data Directory

This directory contains datasets for the crypto perpetual futures backtest system.

## Directory Structure

```
data/
├── raw/                          # Raw downloaded data (NOT in git)
│   ├── binance/
│   │   ├── klines/              # Daily OHLCV ZIPs
│   │   └── funding_rates/       # Funding rate ZIPs
│   └── metadata/
│       └── binance_market_info.json  # Instrument metadata (IN git)
├── test_fixtures/                # Small test datasets (IN git)
│   └── btc_eth_jan2023.parquet  # 31 days × 2 instruments (~8 KB)
└── example_crypto_perps.parquet  # Full example dataset (IN git, ~100 KB)
```

## Git Policy

### Tracked in Git
- ✅ `test_fixtures/` - Small test datasets (< 100 KB each)
- ✅ `example_crypto_perps.parquet` - Full example dataset (< 1 MB)
- ✅ `raw/metadata/*.json` - Instrument metadata

### NOT Tracked in Git
- ❌ `raw/binance/klines/` - Multi-GB of historical klines
- ❌ `raw/binance/funding_rates/` - Multi-GB of funding rate data
- ❌ User-generated parquet files

### Why This Policy?
- Test fixtures enable CI/CD testing without external downloads
- Example dataset provides "clone and run" experience
- Raw data is too large for git (users download on demand)

## Data Refresh Workflow

### Test Fixture (btc_eth_jan2023.parquet)
**When to refresh:** Only when schema changes

```bash
python scripts/build_example_dataset.py \
    --source real \
    --instruments BTCUSDT_PERP ETHUSDT_PERP \
    --start-date 2023-01-01 --end-date 2023-01-31
mv data/example_crypto_perps.parquet data/test_fixtures/btc_eth_jan2023.parquet
git add data/test_fixtures/btc_eth_jan2023.parquet
git commit -m "Update test fixture after schema change"
```

### Example Dataset (example_crypto_perps.parquet)
**When to refresh:** Quarterly or on major system changes

```bash
python scripts/build_example_dataset.py \
    --source real \
    --instruments BTCUSDT_PERP ETHUSDT_PERP BNBUSDT_PERP SOLUSDT_PERP XRPUSDT_PERP \
    --start-date 2022-01-01 --end-date 2023-12-31
git add data/example_crypto_perps.parquet
git commit -m "Update example dataset (Q1 2024 refresh)"
```

### Raw Data
**User-managed:** Download on demand using `scripts/download_binance_data.py`

```bash
# Download 2023 data for backtest
python scripts/download_binance_data.py \
    --symbols BTCUSDT ETHUSDT \
    --year 2023 --months 1 2 3 4 5 6 7 8 9 10 11 12
```

## Size Guidelines

- Test fixture: < 100 KB per file
- Example dataset: < 1 MB
- Raw data: Unlimited (not in git)

## CI/CD Considerations

Tests use:
- `test_fixtures/btc_eth_jan2023.parquet` in `test_real_data_smoke.py`
- `example_crypto_perps.parquet` in `test_crypto_perps_smoke.py`

Both are committed to git, so CI runs without external dependencies.
