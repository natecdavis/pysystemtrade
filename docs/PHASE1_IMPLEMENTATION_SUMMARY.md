# Phase 1 Implementation Summary: Candidate Expansion Infrastructure

**Status**: ✅ Complete
**Date**: 2026-02-09
**Commit**: 70f847b0

## Objective

Expand instrument histories and prepare for broader tradable universe, while maintaining stability of the 3-step canonical production loop:

```
update_data_monthly → doctor_live_ops → run_live_advisory
```

**Core Constraint**: Do NOT auto-expand tradable universe just because more data exists. Keep research universe separate from production trading universe.

## What Was Implemented

### 1. Configurable Candidate Pool (Separate from Trading Universe)

#### New Config Section

```yaml
# NEW: Data acquisition candidate pool (optional, separate from tradable universe)
data_acquisition:
  candidate_instruments:
    - BTCUSDT_PERP
    - ETHUSDT_PERP
    # ... 20 instruments total

# UNCHANGED: Tradable universe (ALWAYS the source of truth for trading)
universe:
  layer_a_instruments:
    - BTCUSDT_PERP
    - ETHUSDT_PERP
    # ... 5 instruments (unchanged)
```

#### Priority Logic

1. If `data_acquisition.candidate_instruments` present and non-empty → use for downloads
2. If `data_acquisition` section missing → fallback to `universe.layer_a_instruments` (backward compatibility)
3. If `data_acquisition.candidate_instruments` present but empty → FAIL FAST with ValueError

#### Key Files

- **`sysdata/crypto/config_helpers.py`** (new): Canonical instrument ID ↔ symbol mapping utilities
  - `extract_candidate_instruments(config)`: Extract download candidates with priority logic
  - `extract_tradable_instruments(config)`: ALWAYS use `universe.layer_a_instruments` (trading only)
  - `instrument_id_to_symbol()`: BTCUSDT_PERP → BTCUSDT
  - `symbol_to_instrument_id()`: BTCUSDT → BTCUSDT_PERP

- **`scripts/update_data_monthly.py`**: Modified `extract_universe_symbols()` to use config_helpers

### 2. Enhanced V1 Status Report

#### New Per-Instrument Fields

```json
{
  "instrument_id": {
    "last_available_date": "2024-12-31",
    "staleness_days": 1,
    "status": "lagging",
    "warnings": ["Lagging by 1 day(s)"],

    // NEW FIELDS
    "missing_months": ["2023-05", "2023-06"],

    "funding_status": {
      "last_available_date": "2024-12-30",
      "staleness_days": 2,
      "coverage_pct": 0.95,  // % of klines days with funding observation
      "missing_days": 30,
      "zero_funding_days": 5,  // Days with explicit zero funding (not missing)
      "missing_months": ["2023-05"]
    },

    "data_quality_metrics": {
      "price_spikes_50pct": 2,
      "date_gaps_7d": 1
    },

    "lifecycle": {
      "launch_date": "2021-01-01",
      "delist_date": null,
      "status": "active",
      "expected_history_days": 1460,
      "actual_history_days": 1450,
      "coverage_pct": 0.993
    },

    "schema_compliant": true,
    "exclusion_recommendation": null  // or "insufficient_history", "missing_funding", "delisted", "stale"
  }
}
```

#### New Summary Fields

```json
{
  "summary": {
    "eligibility_classification": {
      "eligible": 15,
      "excluded_staleness": 1,
      "excluded_missing_funding": 2,
      "excluded_insufficient_history": 2,
      "excluded_data_quality": 0,
      "excluded_delisted": 0
    },
    "exclusion_reasons": {
      "insufficient_history": ["INST1", "INST2"],
      "missing_funding": ["INST3"],
      "data_quality": [],
      "delisted": [],
      "stale_>7d": ["INST4"]
    }
  }
}
```

#### Helper Functions Added

- `load_klines_dates(data_dir, symbol)`: Extract available dates from klines data
- `load_funding_dates(data_dir, symbol)`: Extract available dates from funding data
- `compute_funding_coverage(data_dir, symbol, klines_dates)`: Compute funding coverage metrics
- `load_lifecycle_metadata(metadata_dir, symbol)`: Load launch/delist dates from JSON
- `classify_instrument_exclusion(inst_status, ...)`: Determine exclusion reason with configurable thresholds

#### Key Design Decisions

1. **No Heavy Heuristics in Phase 1**: Raw metrics only (price_spikes, date_gaps reported but not gating)
2. **Distinguish Missing vs Zero Funding**: Don't fill missing with 0 (avoids mixing missing data with true zero funding regimes)
3. **Configurable Thresholds**: `min_history_days`, `min_funding_coverage`, `max_staleness_days` (not hardcoded)
4. **Efficient for 20-50 Instruments**: Cache loaded data, lightweight metrics

### 3. Test Configurations

- **`config/test_candidate_20_instruments.yaml`**: 20 candidates, 5 tradable
- **`config/test_backward_compat.yaml`**: No data_acquisition section (fallback to universe)

### 4. Unit Tests

**`tests/test_candidate_expansion_phase1.py`**: 10 tests, all passing

- Config priority logic (data_acquisition vs universe)
- Backward compatibility fallback
- Empty candidate list fails fast
- Tradable instruments ignore candidate list
- Canonical mapping utilities
- Real config file integration tests

## Verification Commands

### Test Config Extraction

```bash
python -c "
import yaml
from pathlib import Path
from scripts.update_data_monthly import extract_universe_symbols

# 20-candidate config
with open('config/test_candidate_20_instruments.yaml') as f:
    config = yaml.safe_load(f)
symbols = extract_universe_symbols(config)
print(f'20-candidate config: {len(symbols)} symbols')

# Backward compat config
with open('config/test_backward_compat.yaml') as f:
    config = yaml.safe_load(f)
symbols = extract_universe_symbols(config)
print(f'Backward compat config: {len(symbols)} symbols')
"
```

Expected output:
```
20-candidate config: 20 symbols
Backward compat config: 2 symbols
```

### Run Unit Tests

```bash
pytest tests/test_candidate_expansion_phase1.py -v
# Expected: 10 passed
```

### Test Update Data Monthly (Dry Run)

```bash
# Test with 20 candidates (dry run)
python scripts/update_data_monthly.py \
  --config config/test_candidate_20_instruments.yaml \
  --env dev \
  --expected-date 2024-12-31 \
  --dry-run

# Should report: "Using data_acquisition.candidate_instruments: 20 instruments"
```

## Critical Invariants

1. **Tradable Universe Isolation**: `universe.layer_a_instruments` is ALWAYS the source of truth for trading
   - Doctor validation uses tradable universe only
   - Trade plan generation uses tradable universe only
   - Position sizing uses tradable universe only

2. **Backward Compatibility**: Configs without `data_acquisition` section work unchanged

3. **Fail-Fast on Config Errors**: Empty candidate list raises ValueError (loud failure, not silent no-op)

4. **Canonical Mapping**: All instrument ID ↔ symbol conversions use config_helpers module (single source of truth)

## Files Modified

### New Files
- `sysdata/crypto/config_helpers.py` (85 lines)
- `config/test_candidate_20_instruments.yaml` (69 lines)
- `config/test_backward_compat.yaml` (45 lines)
- `tests/test_candidate_expansion_phase1.py` (170 lines)

### Modified Files
- `scripts/update_data_monthly.py`: Use config_helpers for candidate extraction (lines 54-71)
- `sysdata/crypto/data_status.py`: Enhanced V1 report generation (+400 lines of helpers and enhancements)

## What Was NOT Implemented (Deferred to Phase 2+)

### Phase 2: Dataset Manifest Generation
- `dataset_manifest.json` with inclusion/exclusion audit trail
- Track exclusion reasons during dataset build
- Hard invariant: manifest included set == dataset instruments set
- CLI flags: `--min-history-days`, `--min-funding-coverage`

### Phase 3: Research Universe Expansion
- Separate research configs from production configs
- Run backtests on 15-30 instrument pools
- Document performance across broader universe

### Phase 4: Controlled Promotion to Production
- Promotion checklist (data quality, liquidity, backtest criteria)
- Staged rollout (dev → paper → prod)
- Audit trail for universe changes

## Success Criteria Met ✅

- [x] Config with `data_acquisition.candidate_instruments` downloads 20 instruments
- [x] Config without `data_acquisition` section falls back to `universe.layer_a_instruments`
- [x] V1 status report includes new fields (funding_status, lifecycle, exclusion_recommendation)
- [x] Doctor validation runs on 5-instrument tradable universe (ignores 15 additional candidates)
- [x] All Phase 1 unit tests pass
- [x] Canonical pipeline works unchanged with original 5-instrument configs

## Risk Mitigation Validated

1. **No Accidental Trading**: `universe.layer_a_instruments` is ALWAYS trading source (verified by tests)
2. **No Memory Issues**: Efficient loading (cache funding dataframes, load klines dates once)
3. **No Silent Failures**: Empty candidate list fails fast with clear error message

## Next Steps

### Phase 2 Implementation (In Progress)

1. Add `generate_dataset_manifest()` function to `scripts/build_example_dataset.py`
2. Track `instruments_excluded` dict during dataset build
3. Generate `dataset_manifest.json` with deterministic naming
4. Add CLI flags: `--min-history-days`, `--min-funding-coverage`
5. Create `tests/test_dataset_manifest_generation.py`

### Usage in Production

To expand candidate pool in production:

1. Add instruments to `data_acquisition.candidate_instruments` in config
2. Run `update_data_monthly.py` to fetch history
3. Check V1 status report for eligibility classification
4. If eligible, consider adding to research configs (Phase 3)
5. Promotion to prod requires Phase 4 checklist (not yet implemented)

## References

- Design Plan: Implementation plan provided by user
- Commit: 70f847b0 "Implement Phase 1: Candidate expansion infrastructure"
- Branch: develop
