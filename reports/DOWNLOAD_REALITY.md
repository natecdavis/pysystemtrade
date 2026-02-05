# Download Reality Check

## What Data Actually Exists

After attempting to download all expected historical data, we've discovered that **Binance Data Vision has limitations**:

### 2019 Data: NOT AVAILABLE

All core symbols (BTC, ETH, BNB, XRP, LTC, EOS, BCH) return **HTTP 404** for monthly klines and funding rate files for Sep-Dec 2019.

**Attempted URLs (all 404):**
```
https://data.binance.vision/data/futures/um/monthly/klines/BTCUSDT/1d/BTCUSDT-1d-2019-09.zip
https://data.binance.vision/data/futures/um/monthly/klines/BTCUSDT/1d/BTCUSDT-1d-2019-10.zip
https://data.binance.vision/data/futures/um/monthly/klines/BTCUSDT/1d/BTCUSDT-1d-2019-11.zip
https://data.binance.vision/data/futures/um/monthly/klines/BTCUSDT/1d/BTCUSDT-1d-2019-12.zip
```

**Impact:** Cannot build 2019-2025 datasets. Must start from **2020-01-01**.

### SOLUSDT 2020: Only Sep-Dec Available

SOLUSDT launched Jul 27, 2020, but Binance Data Vision only has data starting **Sep 2020**.

**404 Files:**
- `SOLUSDT-1d-2020-07.zip` (404)
- `SOLUSDT-1d-2020-08.zip` (404)

**Available:**
- `SOLUSDT-1d-2020-09.zip` ✓
- `SOLUSDT-1d-2020-10.zip` ✓
- `SOLUSDT-1d-2020-11.zip` ✓
- `SOLUSDT-1d-2020-12.zip` ✓

**Impact:** SOLUSDT jagged panel will start from 2020-09-01, not 2020-07-27.

### BNBUSDT 2020: Missing January

BNBUSDT has 11/12 months for 2020, missing **January 2020** (404).

**Impact:** Jagged panel will have gap for BNBUSDT in Jan 2020.

### MATICUSDT 2024: Only Jan-Sep Available

MATICUSDT klines exist only for **Jan-Sep 2024**. Oct-Dec 2024 return 404 (but funding rates exist!).

**404 Klines:**
- `MATICUSDT-1d-2024-10.zip` (404)
- `MATICUSDT-1d-2024-11.zip` (404)
- `MATICUSDT-1d-2024-12.zip` (404)

**Funding rates exist:**
- `MATICUSDT-fundingRate-2024-10.zip` ✓
- `MATICUSDT-fundingRate-2024-11.zip` ✓
- `MATICUSDT-fundingRate-2024-12.zip` ✓

**Impact:** MATICUSDT data ends Sep 2024, not Dec 2024.

### MATICUSDT 2025: No Klines

MATICUSDT has **no klines for Jan 2025** (404), but funding rate file exists.

**Impact:** MATICUSDT will be excluded from 2025 rectangular datasets.

### DOTUSDT 2020: Only Aug-Dec Available (Correct!)

DOTUSDT launched Aug 20, 2020. Data starts Aug 2020 (5 months), which matches expectations.

**Available:**
- `DOTUSDT-1d-2020-08.zip` ✓ (5 files total for Aug-Dec)

---

## Revised Dataset Plans

### Option 1: 7-instrument Rectangular (2020-2025)

**Original:** 2019-2025 (7 symbols, ~1,970 days)
**Revised:** 2020-2025 (7 symbols, ~1,827 days)

```bash
python scripts/build_example_dataset.py \
  --source real \
  --start-date 2020-01-01 \
  --end-date 2024-12-31 \
  --instruments BTCUSDT_PERP ETHUSDT_PERP BNBUSDT_PERP XRPUSDT_PERP \
               LTCUSDT_PERP EOSUSDT_PERP BCHUSDT_PERP \
  --output-path data/example_crypto_perps_7x5yr.parquet \
  --min-coverage 0.95
```

**Note:** Changed end date to 2024-12-31 instead of 2025-01-26 to avoid partial-month issues.

### Option 2: 15-instrument Rectangular (2023-2024)

**Original:** 2023-2025 (15 symbols)
**Revised:** 2023-01-01 to 2024-09-30 (excludes MATICUSDT Oct-Dec gap)

```bash
python scripts/build_example_dataset.py \
  --source real \
  --start-date 2023-01-01 \
  --end-date 2024-09-30 \
  --instruments BTCUSDT_PERP ETHUSDT_PERP BNBUSDT_PERP XRPUSDT_PERP \
               LTCUSDT_PERP EOSUSDT_PERP BCHUSDT_PERP LINKUSDT_PERP \
               SOLUSDT_PERP DOTUSDT_PERP ADAUSDT_PERP UNIUSDT_PERP \
               MATICUSDT_PERP DOGEUSDT_PERP AVAXUSDT_PERP \
  --output-path data/example_crypto_perps_15x2yr.parquet \
  --min-coverage 0.95
```

### Option 3: 15-instrument Jagged (2020-2024)

**Original:** 2019-2025 (15 symbols, jagged)
**Revised:** 2020-2024 (15 symbols, jagged)

```bash
python scripts/build_example_dataset.py \
  --source real \
  --start-date 2020-01-01 \
  --end-date 2024-09-30 \
  --instruments BTCUSDT_PERP ETHUSDT_PERP BNBUSDT_PERP XRPUSDT_PERP \
               LTCUSDT_PERP EOSUSDT_PERP BCHUSDT_PERP LINKUSDT_PERP \
               SOLUSDT_PERP DOTUSDT_PERP ADAUSDT_PERP UNIUSDT_PERP \
               MATICUSDT_PERP DOGEUSDT_PERP AVAXUSDT_PERP \
  --output-path data/example_crypto_perps_15x5yr_jagged.parquet \
  --min-coverage 0.60 \
  --allow-jagged
```

**Expected jagged starts:**
- Core 7: 2020-01-01
- LINKUSDT: 2020-07-24
- SOLUSDT: 2020-09-01 (not 2020-07-27 due to missing Jul-Aug)
- DOTUSDT: 2020-08-20
- ADAUSDT: 2020-08-27
- UNIUSDT: 2021-01-19
- MATICUSDT: 2021-02-11 (ends 2024-09-30)
- DOGEUSDT: 2021-05-10
- AVAXUSDT: 2021-07-13

---

## What We Have Now

**Total downloaded:** ~2,010 files

**Complete coverage (2020-2024):**
- BTCUSDT: 2020-2024 ✓
- ETHUSDT: 2020-2024 ✓
- BNBUSDT: 2020-2024 ✓ (except Jan 2020)
- XRPUSDT: 2020-2024 ✓
- LTCUSDT: 2020-2024 ✓
- EOSUSDT: 2020-2024 ✓
- BCHUSDT: 2020-2024 ✓
- LINKUSDT: 2020-2024 ✓
- SOLUSDT: 2020-2024 ✓ (starts Sep 2020)
- DOTUSDT: 2020-2024 ✓ (starts Aug 2020)
- ADAUSDT: 2020-2024 ✓ (starts Aug 2020)
- UNIUSDT: 2021-2024 ✓
- MATICUSDT: 2021-2024 ✓ (ends Sep 2024)
- DOGEUSDT: 2021-2024 ✓
- AVAXUSDT: 2021-2024 ✓

**2025 data:** Only Jan 2025, but incomplete for MATICUSDT (no klines).

---

## Recommendation

**Build 3 datasets with adjusted date ranges:**

1. **7x5yr rectangular:** 2020-2024 (core 7, ~1,827 days)
2. **15x2yr rectangular:** 2023-01 to 2024-09 (all 15, ~640 days)
3. **15x5yr jagged:** 2020-2024 (all 15 with natural launch dates, ~1,700 days)

**Proceed to:**
```bash
bash scripts/build_all_datasets.sh  # (needs updates for new date ranges)
```

**OR manually build with corrected parameters above.**
