# Directory Structure

## Overview

```
pysystemtrade-crypto-perps/
├── config/                  # System configurations (versioned in git)
├── data/
│   ├── raw/
│   │   └── binance/        # DATA_ROOT: Canonical data directory
│   │       ├── klines/     # Historical OHLCV (ZIP files, NOT in git)
│   │       ├── funding_rates/  # Funding rates (ZIP files, NOT in git)
│   │       └── metadata/   # Instrument metadata (IN git)
│   │           ├── binance_market_info.json
│   │           └── binance_symbol_lifecycle.json
│   ├── example_*.parquet   # Small example datasets (IN git)
│   └── dataset_*.parquet   # Derived datasets (NOT in git)
├── out/                     # Backtest outputs (NOT in git)
│   └── {config_name}_{hash}/
│       ├── equity_curve.csv
│       ├── positions.csv
│       ├── layer_a_membership.csv  # Phase 2 only
│       ├── idm_history.csv
│       └── metadata.json
├── systems/crypto_perps/    # System code (IN git)
├── tests/                   # Tests (IN git)
└── scripts/                 # Build scripts (IN git)
```

## Environment Variables

- `DATA_ROOT`: Override default data directory (default: `data/raw/binance`)
  - Must contain: `klines/`, `funding_rates/`, `metadata/`
- `OUTPUT_ROOT`: Override default output directory (default: `out`)

## Git Policy

**Versioned (tracked in git):**
- All code (`systems/`, `sysdata/`, `scripts/`, `tests/`)
- Configs (`config/`)
- Metadata (`data/raw/binance/metadata/`)
- Small example datasets (< 1 MB)
- Documentation

**Not versioned:**
- Raw Binance data (`data/raw/binance/klines/`, `data/raw/binance/funding_rates/`)
- Derived datasets > 1 MB (`data/dataset_*.parquet`)
- Backtest outputs (`out/`)
- Python cache (`__pycache__/`, `.pytest_cache/`)

## Running from Different Directories

**Recommended:** Always run from project root:
```bash
cd /path/to/pysystemtrade-crypto-perps
python scripts/run_backtest_e2e.py --config config/...
```

**Alternative:** Set environment variables:
```bash
export DATA_ROOT=/path/to/data
export OUTPUT_ROOT=/path/to/outputs
python scripts/run_backtest_e2e.py --config /path/to/config.yaml
```

## Data Directory Layout

The canonical data directory (`data/raw/binance/`) has this structure:

```
data/raw/binance/
├── klines/                     # Historical OHLCV data
│   ├── BTCUSDT-1d-2020-01.zip  # Binance monthly archives
│   ├── BTCUSDT-1d-2020-02.zip
│   └── ...
├── funding_rates/              # Historical funding rates
│   ├── BTCUSDT-fundingRate-2020-01.zip
│   ├── BTCUSDT-fundingRate-2020-02.zip
│   └── ...
└── metadata/                   # Instrument metadata (versioned)
    ├── binance_market_info.json
    └── binance_symbol_lifecycle.json
```

This structure is created by `scripts/download_binance_data.py`.
