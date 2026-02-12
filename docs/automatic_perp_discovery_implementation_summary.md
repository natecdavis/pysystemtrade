# Automatic Perp Discovery - Implementation Summary

**Date:** 2026-02-11
**Status:** ✅ Complete (Phase 1)

## What Was Implemented

Automatic discovery of Binance USDT-margined perpetual futures using CoinGecko API (not geo-blocked in Massachusetts).

### Key Features

1. **Discovery Script** (`scripts/refresh_binance_market_registry.py`)
   - Fetches derivatives from CoinGecko API
   - Filters to Binance USDT-margined perpetuals
   - Writes 3 deterministic artifacts (atomic writes)
   - Discovered 541 active perpetuals (as of Feb 2026)

2. **Config Integration** (`sysdata/crypto/config_helpers.py`)
   - New function: `extract_candidate_instruments_with_registry()`
   - 3-tier precedence: explicit config → registry → fallback
   - Fully backward compatible

3. **Data Pipeline Integration** (`scripts/update_data_monthly.py`)
   - Updated to use registry if `auto_discover: true`
   - Passes `env_root` for registry lookup
   - Logs data source for auditability

4. **Comprehensive Tests** (`tests/test_perp_discovery.py`)
   - 11 unit tests, all passing
   - Tests precedence logic, filtering, normalization
   - Validates backward compatibility

### Artifacts Created

**Location:** `envs/{env}/data/raw/metadata/`

1. `coingecko_derivatives_snapshot.json` (8.8 MB)
   - Full API response (19,880 total derivatives)
   - Complete audit trail

2. `binance_perp_registry.json` (147 KB)
   - Normalized registry with 541 Binance USDT perpetuals
   - Includes volume, open interest, funding rate

3. `discovered_candidate_instruments.json` (11 KB)
   - 541 instrument IDs with `_PERP` suffix
   - Direct input for `update_data_monthly.py`

## Usage

### Enable Auto-Discovery

```yaml
# config/my_config.yaml
data_acquisition:
  auto_discover: true

universe:
  layer_a_instruments:
    - BTCUSDT_PERP
    - ETHUSDT_PERP
```

**Result:**
- Downloads data for 541 instruments
- Trading universe remains 2 instruments

### Refresh Registry

```bash
# Dry run
python scripts/refresh_binance_market_registry.py --env dev --dry-run

# Actual run
python scripts/refresh_binance_market_registry.py --env dev
```

## Test Results

### All Tests Passing

```bash
# New discovery tests
pytest tests/test_perp_discovery.py -v
# Result: 11 passed in 0.03s

# Existing tests (backward compatibility)
pytest tests/test_candidate_expansion_phase1.py -v
# Result: 10 passed in 0.03s
```

### Live Test

```bash
python scripts/refresh_binance_market_registry.py --env dev
```

**Output:**
```
Total derivatives fetched: 19880
Binance USDT perpetuals: 541
TRADING instruments: 541
Candidate instruments: 541
✓ Wrote envs/dev/data/raw/metadata/coingecko_derivatives_snapshot.json
✓ Wrote envs/dev/data/raw/metadata/binance_perp_registry.json
✓ Wrote envs/dev/data/raw/metadata/discovered_candidate_instruments.json
```

### Integration Test

```bash
python scripts/update_data_monthly.py \
  --config config/test_auto_discover.yaml \
  --env dev \
  --dry-run
```

**Output:**
```
Using auto-discovered candidates: 541 instruments
Using candidate instruments from: discovered_candidate_instruments.json
  Count: 541 instruments
Universe: 541 instruments
```

## Files Modified

### New Files (3)
- `scripts/refresh_binance_market_registry.py` (233 lines)
- `tests/test_perp_discovery.py` (213 lines)
- `config/test_auto_discover.yaml` (test config)
- `docs/automatic_perp_discovery.md` (comprehensive guide)

### Modified Files (2)
- `sysdata/crypto/config_helpers.py` (+60 lines)
  - Added `extract_candidate_instruments_with_registry()`
- `scripts/update_data_monthly.py` (+15 lines)
  - Updated to accept and pass `env_root`

**Total Lines Added:** ~521 lines (including tests and docs)

## Known Limitations

### 1. Symbol Length Validation (23 of 541 symbols)

**Issue:** Current validator rejects symbols >12 characters.

**Affected:** `1000000BOBUSDT` (14 chars), `BROCCOLI714USDT` (15 chars), etc.

**Workaround:** Update validator in `scripts/download_binance_data.py:normalize_and_validate_symbol()` to accept up to 18 characters.

**Impact:** 518 of 541 symbols (95.7%) work with current validation.

### 2. Lifecycle Tracking

**Issue:** CoinGecko doesn't provide launch dates.

**Workaround:** Manual maintenance of `binance_symbol_lifecycle.json` or snapshot comparison to detect new listings.

**Impact:** Auto-discovery works for data acquisition, but launch dates must be added manually for backtest eligibility.

### 3. VPN/Proxy for Daily Updates

**Issue:** Binance REST API is geo-blocked in Massachusetts.

**Solution:** Use VPN or proxy for `update_data_daily.py` to fetch:
- TODAY's closing price (00:00 UTC / 7pm ET)
- LATEST funding rate (8-hourly updates)

**Why CoinGecko Isn't Enough:**
- Provides current snapshot (not aligned to candle close)
- No historical time series (needed for EWMA calculations)

## Design Decisions

### 1. CoinGecko vs Binance exchangeInfo

**Chosen:** CoinGecko

**Reasons:**
- ✅ Not geo-blocked in Massachusetts
- ✅ No API key required (free tier)
- ✅ Better coverage (541 vs ~280 perps)
- ✅ Comprehensive market data (volume, OI, funding)

### 2. Artifact Structure

**Chosen:** 3 separate JSON files

**Reasons:**
- Raw snapshot for audit trail
- Normalized registry for filtering
- Candidate list for direct consumption

**Alternative Considered:** Single combined file
- Rejected: Less flexible, harder to parse

### 3. Precedence Logic

**Chosen:** 3-tier (explicit → registry → fallback)

**Reasons:**
- Backward compatible (existing configs work)
- Opt-in (explicit config always wins)
- Fail-safe (fallback to universe if registry missing)

### 4. Atomic Writes

**Chosen:** Write to `.tmp`, then rename

**Reasons:**
- No partial writes visible to readers
- Crash-safe (old file remains if write fails)

## Backward Compatibility

### Existing Configs Work Unchanged

```yaml
# No data_acquisition section
universe:
  layer_a_instruments:
    - BTCUSDT_PERP
    - ETHUSDT_PERP
```

**Result:** Same behavior as before (downloads 2 instruments).

### Existing Tests Pass

All 10 tests in `test_candidate_expansion_phase1.py` pass without modification.

## Next Steps (Future Enhancements)

### Phase 2: Lifecycle Tracking
- Snapshot comparison to detect new listings
- Heuristic launch date approximation from Binance Vision
- Alert system for delistings

### Phase 3: Market-Cap Filtering
- Cross-reference with CoinMarketCap for market caps
- Filter by minimum threshold
- Exclude micro-cap / meme coins

### Phase 4: Symbol Length Fix
- Update validator to accept 4-18 character symbols
- Validate against actual Binance symbol format
- Add tests for edge cases

## Summary

✅ **Working:** Automatic discovery of 541 Binance perpetuals via CoinGecko
✅ **Working:** Registry-based candidate expansion with `auto_discover: true`
✅ **Working:** Full backward compatibility (existing configs unchanged)
✅ **Working:** Atomic writes, deterministic artifacts, comprehensive tests

⚠️ **Known Issue:** 23 symbols rejected by length validator (95.7% work)
⚠️ **Known Issue:** No launch dates (manual maintenance required)
⚠️ **Known Issue:** Daily updates need VPN/proxy (Binance API geo-blocked)

**Recommendation:** Use for research/exploration with 500+ instruments. For production trading, manually vet symbols and add to explicit `candidate_instruments` list.
