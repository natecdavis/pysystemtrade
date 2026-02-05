# Full Dataset Download Status

## Non-Membership Override Behavior (Clarified)

**Question:** When an instrument is not in `layer_a_today`, is its state forced to `INELIGIBLE_HOLD` or `BANNED_FLATTEN`?

**Answer:** **INELIGIBLE_HOLD** (not BANNED_FLATTEN)

**Code Reference:** `systems/crypto_perps/system.py` lines 262-279

```python
# Precedence 2: Non-membership forces INELIGIBLE_HOLD
if inst not in layer_a_today:
    # Only override if not already BANNED_FLATTEN
    if current_state != InstrumentState.BANNED_FLATTEN.value:
        state_df.loc[date, inst] = InstrumentState.INELIGIBLE_HOLD.value  # ← Line 278
    continue  # Skip eligibility check
```

**Semantics:**
- **State:** `INELIGIBLE_HOLD` (reduce-only)
- **Position:** Linear decay over `forced_exit_days` (default 5 days)
- **NOT immediate flatten** - allows graceful exit over 5 trading days
- **Recoverable:** If instrument re-enters Layer-A at next review, can resume trading

**Contrast with BANNED_FLATTEN:**
- Explicit ban via `universe.banned_instruments` config → `BANNED_FLATTEN` (line 271)
- Immediate flatten to zero (no decay period)
- Terminal state unless manually unbanned

**Key Insight:** Non-membership (Layer-A drop at review) is **graceful** (decay), explicit ban is **harsh** (immediate flatten).

---

## Download Progress

### Current Status: ✅ RUNNING

**Task ID:** b6124e0
**Log File:** `/private/tmp/claude/-Users-nathanieldavis-pysystemtrade-crypto-perps/tasks/b6124e0.output`

**Monitor Progress:**
```bash
# Watch live progress
tail -f /private/tmp/claude/-Users-nathanieldavis-pysystemtrade-crypto-perps/tasks/b6124e0.output

# Check file counts
find data/raw/binance -name "*.zip" | wc -l

# Check total size
du -sh data/raw/binance
```

### Download Plan

**Total Files:** 2,232 (1,116 klines + 1,116 funding)
**Existing Files:** 152 (will be skipped)
**New Files:** 2,080
**Expected Size:** ~10GB
**Estimated Time:** ~37 minutes (varies with network)

**Instruments:**
1. **Core 7 (2019-2025):** BTC, ETH, BNB, XRP, LTC, EOS, BCH
   - 7 symbols × 7 years × 12 months × 2 data types = 1,176 files

2. **2020 Launches (2020-2025):** LINK, SOL, DOT, ADA
   - 4 symbols × 6 years × 12 months × 2 data types = 576 files

3. **2021 Launches (2021-2025):** UNI, MATIC, DOGE, AVAX
   - 4 symbols × 5 years × 12 months × 2 data types = 480 files

### Robustness Features

1. **Retry Logic:**
   - 3 attempts per file
   - Exponential backoff: 2s, 4s, 8s delays
   - HTTP 429 (rate limit) detection

2. **Skip Existing:**
   - Existing files automatically skipped
   - Atomic writes prevent partial files

3. **Validation:**
   - ZIP integrity check after download
   - Checksum verification available (not enabled by default)

4. **Failure Logging:**
   - Failed downloads logged to `download_failures.log`
   - Easy retry command provided at end

5. **Politeness:**
   - 1-second delay between years
   - 2-second delay between symbols
   - User-Agent header set

---

## Next Steps After Download

### 1. Verify Download Completion

```bash
# Check for failure log
if [ -f download_failures.log ] && [ -s download_failures.log ]; then
    echo "Some downloads failed:"
    cat download_failures.log

    # Retry failed downloads
    cat download_failures.log | while read symbol year; do
        python3 scripts/download_binance_data.py --symbols $symbol --year $year
    done
else
    echo "All downloads successful!"
fi

# Final file count
echo "Total files:"
find data/raw/binance -name "*.zip" | wc -l
echo "Expected: 2232"

# Total size
du -sh data/raw/binance
```

### 2. Build Datasets

Run the automated dataset builder:

```bash
bash scripts/build_all_datasets.sh
```

This will create 3 datasets:

**a) 7-instrument rectangular (2019-2025)**
- File: `data/example_crypto_perps_7x6yr.parquet`
- Instruments: BTC, ETH, BNB, XRP, LTC, EOS, BCH
- Date range: 2019-09-08 to 2025-01-26 (~1,970 days)
- Type: Rectangular (all instruments have full coverage)
- Use case: Maximum time depth for regime analysis

**b) 15-instrument rectangular (2023-2025)**
- File: `data/example_crypto_perps_15x2yr.parquet`
- Instruments: All 15 (core 7 + 2020 launches + 2021 launches)
- Date range: 2023-01-01 to 2025-01-26 (~757 days)
- Type: Rectangular (all instruments have full coverage)
- Use case: Recent data for production testing

**c) 15-instrument jagged (2019-2025)**
- File: `data/example_crypto_perps_15x6yr_jagged.parquet`
- Instruments: All 15 with natural launch dates
  - Core 7: 2019-09-08 start
  - 2020 launches: 2020-07 to 2020-08 start
  - 2021 launches: 2021-01 to 2021-07 start
- Date range: 2019-09-08 to 2025-01-26 (jagged)
- Type: Jagged (instruments have different date ranges)
- Use case: Test jagged panel support, lifecycle states, IDM eligibility

### 3. Run Backtests

Run the automated backtest suite:

```bash
bash scripts/run_all_backtests.sh
```

This will run 3 backtests:

**a) Phase 1 - 15x2yr Rectangular**
- Config: `crypto_perps_baseline_v1.yaml`
- Data: 15 instruments, 2023-2025 (rectangular)
- Universe: Static Layer-A (Phase 1)
- Output: `out/phase1_15x2yr_rectangular/`

**b) Phase 1 - 15x6yr Jagged**
- Config: `crypto_perps_baseline_v1.yaml` (with `allow_jagged: true`)
- Data: 15 instruments, 2019-2025 (jagged panel)
- Universe: Static Layer-A (Phase 1)
- Tests: Lifecycle states, warmup periods, IDM eligibility
- Output: `out/phase1_15x6yr_jagged/`

**c) Phase 2 - 15x6yr Jagged (Optional)**
- Config: `crypto_perps_phase2_v1.yaml` (with `review_freq: 'BMS'`)
- Data: 15 instruments, 2019-2025 (jagged panel)
- Universe: Dynamic Layer-A with monthly reviews (Phase 2)
- Tests: Membership freezing, review logic at scale
- Output: `out/phase2_15x6yr_jagged/`

---

## Manual Steps (If Needed)

### Build Individual Datasets

```bash
# 7-instrument rectangular (2019-2025)
python scripts/build_example_dataset.py \
  --source real \
  --start-date 2019-09-08 \
  --end-date 2025-01-26 \
  --instruments BTCUSDT_PERP ETHUSDT_PERP BNBUSDT_PERP XRPUSDT_PERP \
               LTCUSDT_PERP EOSUSDT_PERP BCHUSDT_PERP \
  --output-path data/example_crypto_perps_7x6yr.parquet \
  --min-coverage 0.90

# 15-instrument rectangular (2023-2025)
python scripts/build_example_dataset.py \
  --source real \
  --start-date 2023-01-01 \
  --end-date 2025-01-26 \
  --instruments BTCUSDT_PERP ETHUSDT_PERP BNBUSDT_PERP XRPUSDT_PERP \
               LTCUSDT_PERP EOSUSDT_PERP BCHUSDT_PERP LINKUSDT_PERP \
               SOLUSDT_PERP DOTUSDT_PERP ADAUSDT_PERP UNIUSDT_PERP \
               MATICUSDT_PERP DOGEUSDT_PERP AVAXUSDT_PERP \
  --output-path data/example_crypto_perps_15x2yr.parquet \
  --min-coverage 0.95

# 15-instrument jagged (2019-2025)
python scripts/build_example_dataset.py \
  --source real \
  --start-date 2019-09-08 \
  --end-date 2025-01-26 \
  --instruments BTCUSDT_PERP ETHUSDT_PERP BNBUSDT_PERP XRPUSDT_PERP \
               LTCUSDT_PERP EOSUSDT_PERP BCHUSDT_PERP LINKUSDT_PERP \
               SOLUSDT_PERP DOTUSDT_PERP ADAUSDT_PERP UNIUSDT_PERP \
               MATICUSDT_PERP DOGEUSDT_PERP AVAXUSDT_PERP \
  --output-path data/example_crypto_perps_15x6yr_jagged.parquet \
  --min-coverage 0.50 \
  --allow-jagged
```

### Run Individual Backtests

```bash
# Phase 1 - 15x2yr rectangular
python -m systems.crypto_perps.system \
  --config config/crypto_perps_baseline_v1.yaml \
  --data data/example_crypto_perps_15x2yr.parquet \
  --outdir out/phase1_15x2yr_rectangular

# Phase 1 - 15x6yr jagged (requires allow_jagged in config)
python -m systems.crypto_perps.system \
  --config config/crypto_perps_baseline_v1.yaml \
  --data data/example_crypto_perps_15x6yr_jagged.parquet \
  --outdir out/phase1_15x6yr_jagged

# Phase 2 - 15x6yr jagged (requires review_freq in config)
python -m systems.crypto_perps.system \
  --config config/crypto_perps_phase2_v1.yaml \
  --data data/example_crypto_perps_15x6yr_jagged.parquet \
  --outdir out/phase2_15x6yr_jagged
```

---

## Expected Results

### Jagged Panel Validation

**Instrument States (15x6yr jagged backtest):**

Expected state counts per instrument:
- **Core 7 (BTC, ETH, BNB, XRP, LTC, EOS, BCH):**
  - WARMUP: ~90 days (2019-09-08 to 2019-12-06)
  - IDM_INELIGIBLE: ~0-60 days (if insufficient peer overlap initially)
  - ACTIVE: ~1,880 days (majority of 2019-2025)

- **2020 Launches (LINK, SOL, DOT, ADA):**
  - NOT_YET_LAUNCHED: ~340 days (2019-09-08 to 2020-07-27)
  - WARMUP: ~90 days after launch
  - IDM_INELIGIBLE: ~0-60 days (if insufficient overlap with core 7)
  - ACTIVE: ~1,300 days (rest of 2020-2025)

- **2021 Launches (UNI, MATIC, DOGE, AVAX):**
  - NOT_YET_LAUNCHED: ~500 days (2019-09-08 to 2021-01-19)
  - WARMUP: ~90 days after launch
  - IDM_INELIGIBLE: ~0-60 days
  - ACTIVE: ~1,000 days (rest of 2021-2025)

**IDM Values:**
- Early 2019: IDM ~1.0 (only BTC active, no diversification)
- Late 2019: IDM ~1.2-1.5 (7 core instruments active)
- 2020: IDM ~1.3-1.8 (7-11 instruments active as 2020 launches come online)
- 2021+: IDM ~1.5-2.0 (11-15 instruments active as 2021 launches come online)
- Capped at `idm_cap: 1.5` per baseline config

**Verification Queries:**

```python
import pandas as pd

# Load diagnostics
diag = pd.read_parquet('out/phase1_15x6yr_jagged/diagnostics.parquet')

# Check state distribution
print("State distribution per instrument:")
print(diag.groupby('instrument')['state'].value_counts())

# Check IDM eligibility timing
sol_states = diag[diag['instrument'] == 'SOLUSDT_PERP'][['date', 'state']].head(200)
print("\\nSOL state transitions (first 200 days):")
print(sol_states[sol_states['state'].shift() != sol_states['state']])

# Check IDM values over time
idm_by_date = diag.groupby('date')['idm'].first()
print("\\nIDM statistics:")
print(idm_by_date.describe())
```

---

## Troubleshooting

### Download Failures

If downloads fail with rate limiting (HTTP 429):
- Wait specified `Retry-After` seconds
- Retry failed downloads from `download_failures.log`
- Increase `INTER_SYMBOL_SLEEP` in download script

### Build Failures

If dataset build fails with "Insufficient coverage":
- Check `--min-coverage` parameter (use 0.50 for jagged panels)
- Verify raw ZIP files exist in `data/raw/binance/`
- Check for corrupt ZIP files (redownload with `--force`)

### Backtest Failures

If backtest fails with "Missing lifecycle metadata":
- Ensure `data/raw/metadata/binance_symbol_lifecycle.json` exists
- Check `allow_jagged: true` in config for jagged panels
- Verify instrument names match lifecycle metadata

---

## Summary

**Download:** ✅ Running in background
**Task ID:** b6124e0
**Monitor:** `tail -f /private/tmp/claude/-Users-nathanieldavis-pysystemtrade-crypto-perps/tasks/b6124e0.output`

**After download completes:**
1. Run `bash scripts/build_all_datasets.sh` (builds 3 datasets)
2. Run `bash scripts/run_all_backtests.sh` (runs 3 backtests)
3. Analyze results in `out/` directory

**Estimated completion:** ~30-40 minutes from start (many files already exist, so faster than initial estimate)
