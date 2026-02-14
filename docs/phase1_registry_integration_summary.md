# Phase 1: Registry-Aware Advisory Workflow - Implementation Summary

**Date:** 2026-02-14
**Status:** ✅ Complete

## Overview

Successfully implemented registry-aware candidate extraction throughout the advisory workflow, enabling the system to use all 541 Binance perpetual futures from the registry when `auto_discover: true`.

## Key Changes

### 1. Friction Removal: `scripts/sync_positions_file.py` (NEW)

**Purpose:** Auto-regenerate `current_positions.csv` from config with zeros for missing instruments

**Why:** Prevents doctor failures when layer_a expands to 30 instruments (users don't need to manually edit CSV)

**Usage:**
```bash
python scripts/sync_positions_file.py \
    --config config/crypto_perps_dynamic_universe_top30.yaml \
    --positions-file live/current_positions.csv
```

**Features:**
- Adds missing instruments with zero positions
- Warns about extra instruments not in layer_a
- Atomic writes (safe for concurrent use)
- Sorts output for readability

### 2. Advisory Workflow: `scripts/run_live_advisory.py` (MODIFIED)

**Changes:**
1. Replaced `extract_universe_from_config()` with registry-aware `extract_candidate_instruments()`
2. Added `--skip-dataset-rebuild` flag for reusing existing datasets
3. Threads `env_root` to dataset builder for registry lookup
4. Logs candidate source (config vs registry vs fallback)

**Backward Compatible:** Static universe mode still works (no breaking changes)

**New Flags:**
- `--skip-dataset-rebuild`: Skip dataset rebuild, use existing parquet (requires `--use-dynamic-universe`)

**Example Usage:**
```bash
# With registry (541 candidates)
python scripts/run_live_advisory.py \
    --config config/test_auto_discover.yaml \
    --actual-positions live/current_positions.csv \
    --current-equity 5000 \
    --output-dir out/test_registry \
    --use-dynamic-universe

# Skip dataset rebuild (faster iteration)
python scripts/run_live_advisory.py \
    --config config/test_auto_discover.yaml \
    --actual-positions live/current_positions.csv \
    --current-equity 5000 \
    --output-dir out/test_registry \
    --use-dynamic-universe \
    --skip-dataset-rebuild
```

### 3. Parquet Adapter: `sysdata/crypto/parquet_perps_sim_data.py` (MODIFIED)

**Changes:**
1. Added `env_root` parameter to `__init__()`
2. Enhanced `_determine_candidate_pool()` to be config-aware
3. Uses registry-aware extraction when `config_path` provided

**Candidate Pool Priority:**
1. If `config_path` provided and `auto_discover=true`: Use registry (541 instruments)
2. If `config_path` provided: Use explicit `candidate_instruments`
3. Fallback: All instruments in dataset

**Filters:** Only returns instruments actually present in the dataset (intersection logic)

**Example:**
```python
# Registry-aware mode
data = parquetCryptoPerpsSimData(
    dataset_path='data/dataset_541.parquet',
    config_path='config/test_auto_discover.yaml',
    env_root=Path('envs/dev'),
    use_dynamic_universe=True,
)
# Returns: 541 candidates (filtered to those in dataset)
```

### 4. Backtest Runner: `scripts/run_dynamic_universe_backtest.py` (MODIFIED)

**Changes:**
1. Passes `config_path` to parquet adapter (was missing before)
2. Passes `env_root` from environment variable or current directory
3. Enables registry-aware candidate extraction in backtests

**Before:**
```python
data = parquetCryptoPerpsSimData(
    dataset_path=data_path,
    use_dynamic_universe=True,
)
# Used ALL dataset instruments (no config awareness)
```

**After:**
```python
data = parquetCryptoPerpsSimData(
    dataset_path=data_path,
    config_path=config_path,
    env_root=env_root,
    use_dynamic_universe=True,
)
# Uses registry/config candidates (filtered to dataset)
```

## Testing

### Unit Tests: `tests/test_phase1_registry_integration.py` (NEW)

**Coverage:**
1. `test_registry_aware_extraction()`: Verifies 541 instruments loaded from registry
2. `test_fallback_to_layer_a()`: Verifies fallback when auto_discover=false
3. `test_explicit_config_takes_priority()`: Verifies precedence rules

**Results:** ✅ All 3 tests passing

**Run Tests:**
```bash
python -m pytest tests/test_phase1_registry_integration.py -v
```

### Integration Test

**Manual Verification:**
```bash
# Test with registry (541 candidates)
python scripts/run_live_advisory.py \
    --config config/test_auto_discover.yaml \
    --actual-positions live/current_positions.csv \
    --current-equity 5000 \
    --output-dir out/test_registry_541 \
    --use-dynamic-universe \
    --skip-data-update

# Verify logs show:
# - "Using 541 candidates from: discovered_candidate_instruments.json"
# - Dataset contains 541 instruments (or subset with data)
# - Backtest filters to ~300 eligible (via cost thresholds)
# - Trade plan only includes layer_a instruments
```

## Architecture Benefits

### Single Source of Truth

**Config Precedence (3-tier):**
1. `data_acquisition.candidate_instruments` (explicit override)
2. Registry (if `auto_discover=true` and registry exists)
3. `universe.layer_a_instruments` (fallback for backward compat)

**Enforced by:** `sysdata/crypto/config_helpers.py::extract_candidate_instruments_with_registry()`

### Separation of Concerns

**Candidates vs Tradable:**
- **Candidates:** 541 instruments for data acquisition and research (`data_acquisition`)
- **Tradable:** Max 30 instruments for production trading (`universe.layer_a_instruments`)

**Safety:** Trade plan generation ALWAYS uses `layer_a_instruments` (not candidates)

### Config-Aware Data Layer

**Before:** Parquet adapter ignored config, used ALL dataset instruments
**After:** Parquet adapter respects config scope, filters to candidates

**Why:** Prevents backtests from accidentally using instruments not in config

## Backward Compatibility

### Existing Configs Work Unchanged

**Static universe (no auto_discover):**
```yaml
universe:
  layer_a_instruments:
    - BTCUSDT_PERP
    - ETHUSDT_PERP
```
✅ Falls back to `layer_a_instruments` (no registry lookup)

**Explicit candidates (no auto_discover):**
```yaml
data_acquisition:
  candidate_instruments:
    - BTCUSDT_PERP
    - ETHUSDT_PERP
    - SOLUSDT_PERP
universe:
  layer_a_instruments:
    - BTCUSDT_PERP
    - ETHUSDT_PERP
```
✅ Uses explicit candidates (ignores registry)

### No Breaking Changes

- `run_live_advisory.py`: Static mode still works (no `--use-dynamic-universe`)
- `parquet_perps_sim_data.py`: Works without `config_path` (uses all dataset instruments)
- `run_dynamic_universe_backtest.py`: Works with existing scripts

## Known Limitations

1. **Registry must exist for auto_discover=true**
   - If registry missing, falls back to `layer_a_instruments`
   - Logged as warning (not error)

2. **Dataset rebuild required after registry changes**
   - Adding new instruments to registry doesn't auto-update existing datasets
   - Workaround: Use `--skip-dataset-rebuild=false` (default)

3. **No automatic registry refresh yet**
   - Phase 2 will add opportunistic refresh in advisory workflow
   - Manual refresh: `python scripts/refresh_binance_market_registry.py --env dev`

## Next Steps (Phase 2)

### Opportunistic Registry Refresh

**Goal:** Embed registry refresh in advisory workflow with fallback resilience

**Key Features:**
- Best-effort refresh from CoinGecko API
- Cached fallback if API fails
- Registry snapshot hash in advisory metadata (reproducibility)
- Diff detection (new/delisted instruments logged)

**Implementation:**
1. Add `refresh_registry_opportunistic()` to `run_live_advisory.py`
2. Enhance `refresh_binance_market_registry.py` with diff detection
3. Store registry hash in audit bundle metadata

**Success Criteria:**
- ✅ Registry refreshed during advisory
- ✅ Cached fallback if CoinGecko unreachable
- ✅ Hash in metadata for reproducibility

## Files Modified

### New Files
- `scripts/sync_positions_file.py` - Friction removal for 30-instrument universe
- `tests/test_phase1_registry_integration.py` - Unit tests
- `docs/phase1_registry_integration_summary.md` - This document

### Modified Files
- `scripts/run_live_advisory.py` - Registry-aware candidate extraction, `--skip-dataset-rebuild` flag
- `sysdata/crypto/parquet_perps_sim_data.py` - Config-aware candidate pool determination
- `scripts/run_dynamic_universe_backtest.py` - Pass config/env_root to adapter

### Unchanged (Reused)
- `sysdata/crypto/config_helpers.py` - Already had `extract_candidate_instruments_with_registry()`
- `scripts/refresh_binance_market_registry.py` - Already generates registry
- Registry: `envs/dev/data/raw/metadata/discovered_candidate_instruments.json` - 541 instruments

## Verification Checklist

- [x] Unit tests passing (3/3)
- [x] Registry extraction works (541 instruments)
- [x] Fallback logic works (auto_discover=false)
- [x] Explicit config takes priority
- [x] Parquet adapter filters to dataset instruments
- [x] Backward compatibility (existing configs work)
- [x] Friction removal script created (`sync_positions_file.py`)
- [x] Documentation complete

## Conclusion

Phase 1 successfully implements registry-aware advisory workflow, enabling the system to scale from 5 instruments to 541 candidates while maintaining production safety through the `layer_a_instruments` tradable universe constraint.

**Key Achievement:** End-to-end registry integration with zero breaking changes.
