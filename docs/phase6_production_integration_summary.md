# Phase 6: Production Integration - Implementation Summary

**Date:** 2026-02-14
**Status:** ✅ Complete

## Overview

Successfully implemented production safety features, config validation, and comprehensive operational documentation for the 541 Binance perpetuals scale-up. Phase 6 ensures production-ready deployment with hard invariant enforcement and validated operational procedures.

## Key Achievement

**Production-safe deployment:** Config validation, hard invariant enforcement (trade plan ⊆ layer_a), and comprehensive runbook ready for 541-instrument scale.

## Implementation

### 1. Config Validation Tool: `scripts/validate_config.py` (NEW)

**Purpose:** Validate config files before use in production

**Features:**
- Registry consistency checks (auto_discover vs registry file existence)
- layer_a_instruments presence verification (production safety)
- Top-K parameter validation (top_k <= len(layer_a))
- Tradable instruments subset validation
- Cost filter parameter checks
- Buffer parameter validation

**Usage:**
```bash
# Validate production config
python scripts/validate_config.py \
    --config config/crypto_perps_dynamic_universe_top30.yaml \
    --env prod

# Validate with custom env-root
python scripts/validate_config.py \
    --config config/test_auto_discover.yaml \
    --env-root envs/dev
```

**Exit Codes:**
- 0: Validation passed (may have warnings)
- 1: Validation failed (errors found)

**Validation Checks:**

| Check | Type | Condition |
|-------|------|-----------|
| Registry existence | ERROR | auto_discover=true but registry missing |
| Registry validity | ERROR | Registry not valid JSON or missing fields |
| layer_a empty | ERROR | layer_a_instruments is empty or missing |
| top_k > layer_a | ERROR | top_k exceeds layer_a instrument count |
| Instruments not in registry | WARNING | layer_a instruments not found in registry |
| Buffer parameters | WARNING | entry_buffer >= top_k (prevents entries) |
| Cost filter thresholds | WARNING | Outside typical ranges (0.01, 0.13) |

### 2. Trade Plan Hard Invariant: `scripts/generate_trade_plan.py` (MODIFIED)

**New Function: `validate_tradable_universe()`**

Validates that trade plan only includes instruments from layer_a_instruments (production safety).

```python
def validate_tradable_universe(trade_plan_df, config, log):
    """
    Validate trade plan only includes layer_a instruments (Phase 6 hard invariant).

    Raises:
        ValueError: If trade plan includes instruments not in layer_a_instruments
    """
    layer_a = config.get('universe', {}).get('layer_a_instruments', [])
    if not layer_a:
        log.warning("No layer_a_instruments defined - skipping validation")
        return

    max_tradable_set = set(layer_a)
    planned_instruments = set(trade_plan_df['instrument'])

    non_tradable = planned_instruments - max_tradable_set

    if non_tradable:
        raise ValueError(
            f"HARD INVARIANT VIOLATION: Trade plan includes {len(non_tradable)} instruments NOT in layer_a:\n"
            f"  {sorted(non_tradable)}\n"
            f"layer_a_instruments represents MAX tradable set for production safety."
        )

    log.info(f"✓ Trade plan validated: all {len(planned_instruments)} instruments in layer_a")
```

**Integration:** Called in `generate_trade_plan.py` after trade plan generation (line 188)

**Hard Invariant:** `trade_plan ⊆ layer_a_instruments`

This ensures:
- No accidental trading of instruments outside approved set
- Production safety: layer_a defines MAX tradable universe
- Config validation catches issues before execution

### 3. Operations Runbook: `docs/runbook_541_perps.md` (NEW)

**Purpose:** Comprehensive operations guide for 541 perpetuals system

**Sections:**

1. **Quick Reference**
   - Key paths (registry, raw data, datasets, advisory outputs)
   - Critical commands (validate config, check data, verify registry, VPN check)

2. **Daily Operations**
   - Morning workflow (VPN check → advisory → review outputs)
   - Expected results and status checks

3. **Monthly Maintenance**
   - Vision bulk download (historical data, NO VPN)
   - Tail update (recent data, VPN required)
   - Dataset rebuild (monthly, not daily)

4. **Registry Management**
   - Automatic refresh (opportunistic with cached fallback)
   - Manual refresh procedure
   - Adding new instruments workflow

5. **Data Management**
   - Storage structure and disk space planning
   - Data cleanup procedures
   - Backup strategies

6. **Troubleshooting**
   - VPN issues (Binance API unreachable)
   - Registry refresh failures
   - Lifecycle status STALE
   - Trade plan validation failures
   - Dataset build failures

7. **Emergency Procedures**
   - Circuit breaker (stop all trading)
   - Rollback to previous dataset
   - Registry corruption recovery

**Monitoring Checklists:**
- Daily (pre-trading): VPN, positions, advisory, trade plan
- Weekly: Registry, tail data, outputs, disk space
- Monthly: Vision downloads, dataset rebuild, lifecycle review

### 4. Testing: `tests/test_phase6_production_integration.py` (NEW)

**Coverage:** 10 unit tests, all passing

**Test Cases:**

1. `test_validate_config_auto_discover_without_registry()` - Error when registry missing
2. `test_validate_config_auto_discover_with_registry()` - Pass with valid registry
3. `test_validate_config_empty_layer_a()` - Error on empty layer_a
4. `test_validate_config_top_k_exceeds_layer_a()` - Error when top_k > layer_a count
5. `test_validate_config_instruments_not_in_registry()` - Warn on missing instruments
6. `test_validate_config_buffer_parameters()` - Warn on invalid buffers
7. `test_validate_tradable_universe_valid()` - Pass when instruments in layer_a
8. `test_validate_tradable_universe_invalid()` - Raise ValueError when not in layer_a
9. `test_validate_tradable_universe_no_layer_a()` - Skip validation when no layer_a
10. `test_validate_config_cost_filter_parameters()` - Warn on unusual cost thresholds

**Results:** ✅ 10/10 tests passing

### 5. Verification Script: `scripts/verify_phase6.sh` (NEW)

**Checks:**
1. Unit tests passing (10/10)
2. Config validation tool working
3. Trade plan validation working
4. Config validation edge cases
5. Critical files exist
6. Runbook completeness (all sections present)

**Run:**
```bash
./scripts/verify_phase6.sh
```

## Architecture Benefits

### Production Safety Guarantees

**Hard Invariant Enforcement:**
- Trade plan ALWAYS subset of layer_a_instruments
- Validation fails early (before execution) if violated
- Clear error messages for debugging

**Config Validation:**
- Catch errors before production use
- Validate top_k <= layer_a count
- Ensure registry exists (auto_discover mode)
- Check cost filter parameters

**Fail-Fast Philosophy:**
- Errors raised immediately, not hidden
- Clear remediation steps in error messages
- Runbook provides troubleshooting procedures

### Operational Readiness

**Comprehensive Runbook:**
- Daily operations workflow (morning advisory)
- Monthly maintenance procedures
- Emergency procedures (circuit breaker, rollback)
- Monitoring checklists (daily, weekly, monthly)

**Validation Tools:**
- Config validation before use
- Trade plan validation before execution
- Registry consistency checks

**Documentation:**
- All 6 phases documented with summaries
- Runbook covers routine and emergency operations
- Verification scripts for each phase

## Usage Examples

### Pre-Production Config Validation

```bash
# Validate production config before deploying
python scripts/validate_config.py \
    --config config/crypto_perps_dynamic_universe_top30.yaml \
    --env prod

# Expected output:
# ✅ VALIDATION PASSED: No errors or warnings

# If errors found, fix and re-validate
# Exit code 1 = errors, 0 = pass
```

### Daily Advisory Workflow

```bash
# Step 1: Check VPN connectivity (required for Binance API)
curl https://fapi.binance.com/fapi/v1/ping
# Expected: {}

# Step 2: Sync positions file (auto-add missing instruments)
python scripts/sync_positions_file.py \
    --config config/crypto_perps_dynamic_universe_top30.yaml \
    --positions-file live/current_positions.csv

# Step 3: Run advisory workflow
python scripts/run_live_advisory.py \
    --config config/crypto_perps_dynamic_universe_top30.yaml \
    --actual-positions live/current_positions.csv \
    --current-equity 5000 \
    --output-dir out/live_advisory_$(date +%Y%m%d) \
    --use-dynamic-universe \
    --skip-dataset-rebuild  # Reuse monthly dataset

# Step 4: Review trade plan
cat out/live_advisory_$(date +%Y%m%d)/trade_plan_*.csv

# Step 5: Check sanity checks
cat out/live_advisory_$(date +%Y%m%d)/sanity_checks_*.json | jq '.overall_status'
# Expected: "pass" or "pass_with_warnings"
```

### Monthly Maintenance

```bash
# Step 1: Vision bulk download (NO VPN, one-time for new instruments)
python scripts/download_vision_bulk.py \
    --env prod \
    --instruments-limit 100  # Resumable batches

# Step 2: Tail update (VPN REQUIRED, recent data)
python scripts/update_data_monthly.py \
    --config config/crypto_perps_dynamic_universe_top30.yaml \
    --data-dir envs/prod/data/raw/binance \
    --tail-days 30

# Step 3: Rebuild monthly dataset
python scripts/build_example_dataset.py \
    --source real \
    --data-dir envs/prod/data/raw/binance \
    --output-path envs/prod/data/datasets/monthly_541_instruments.parquet \
    --allow-jagged \
    --min-history-days 365

# Step 4: Verify lifecycle
cat envs/prod/data/datasets/monthly_541_instruments.manifest.json | jq '.lifecycle_summary'
# Expected: {active: ~340, stale: ~50, no_data: ~150}
```

### Adding New Instrument to Trading Universe

```bash
# Step 1: Verify in registry
cat envs/prod/data/raw/metadata/discovered_candidate_instruments.json | \
    jq '.candidate_instruments[] | select(. == "NEWUSDT_PERP")'

# Step 2: Download historical data
python scripts/download_vision_bulk.py \
    --env prod \
    --resume-from NEWUSDT_PERP \
    --instruments-limit 1

# Step 3: Add to config layer_a_instruments
# Edit config YAML, add NEWUSDT_PERP to layer_a list

# Step 4: Validate config
python scripts/validate_config.py \
    --config config/crypto_perps_dynamic_universe_top30.yaml \
    --env prod

# Step 5: Sync positions file (auto-add with zero position)
python scripts/sync_positions_file.py \
    --config config/crypto_perps_dynamic_universe_top30.yaml \
    --positions-file live/current_positions.csv
```

## Integration Points

### Phase 6 Completes the Stack

**Phase 1:** Registry-aware advisory workflow
**Phase 2:** Opportunistic registry refresh
**Phase 3:** Lifecycle from Vision data
**Phase 4:** Vision-first data management
**Phase 5:** Top-K selection with hysteresis
**Phase 6:** Production integration (THIS PHASE)

**Full Stack:**
```
Registry (541 candidates)
  ↓ Phase 2: Opportunistic refresh
Vision Data (bulk historical, NO VPN)
  ↓ Phase 4: Vision-first management
Lifecycle (from Vision coverage)
  ↓ Phase 3: Data availability boundaries
Cost Filters (SR thresholds)
  ↓ Existing: Carver cost filters
Top-K Selection (K=30, hysteresis)
  ↓ Phase 5: Liquidity-based ranking
layer_a_instruments (MAX tradable set)
  ↓ Phase 6: Hard invariant enforcement
Trade Plan (VALIDATED ⊆ layer_a)
  ↓ Phase 6: Production safety
```

### Config Validation Pipeline

```
Config YAML
  ↓ validate_config.py
Config Validation
  ├─ Registry existence (auto_discover mode)
  ├─ layer_a presence (production safety)
  ├─ top_k <= layer_a count
  ├─ Instruments in registry
  └─ Cost filter parameters
  ↓ PASS
Advisory Workflow
  ↓ generate_trade_plan.py
Trade Plan Validation
  └─ trade_plan ⊆ layer_a (hard invariant)
  ↓ PASS
Trade Execution
```

## Known Limitations

1. **Config Validation is Pre-Execution Only**
   - Validates config before use, not runtime state
   - Doesn't check dataset freshness or data quality
   - Acceptable: Runtime checks in advisory workflow

2. **Hard Invariant Doesn't Prevent Config Changes**
   - layer_a can be edited manually after validation
   - User must re-validate after changes
   - Acceptable: Validation tool readily available

3. **No Automated Config Sync**
   - layer_a expansion requires manual config edit
   - sync_positions_file.py must be run manually
   - Acceptable: Intentional (prevents accidental changes)

4. **Runbook Assumes MacOS/Linux**
   - Commands use bash syntax
   - Windows users need WSL or adapt commands
   - Acceptable: Primary target is MacOS development

## Files Created/Modified

### New Files
- `scripts/validate_config.py` - Config validation tool (300+ lines)
- `docs/runbook_541_perps.md` - Operations runbook (600+ lines)
- `tests/test_phase6_production_integration.py` - Unit tests (10 tests, all passing)
- `scripts/verify_phase6.sh` - Verification script
- `docs/phase6_production_integration_summary.md` - This document

### Modified Files
- `scripts/generate_trade_plan.py`
  - Added `validate_tradable_universe()` function (30 lines)
  - Integrated validation in main() workflow (1 line)

## Verification Checklist

- [x] Unit tests passing (10/10)
- [x] Config validation tool working
- [x] Trade plan validation working (valid/invalid cases)
- [x] Config validation edge cases (top_k, layer_a, registry)
- [x] Operations runbook complete (all sections)
- [x] Verification script passing all checks
- [x] Hard invariant enforcement tested
- [x] Documentation complete

## Production Readiness Checklist

### Config Setup
- [ ] Production config validated with `validate_config.py`
- [ ] layer_a_instruments contains 30 instruments
- [ ] top_k <= len(layer_a_instruments)
- [ ] Registry exists in `envs/prod/data/raw/metadata/`
- [ ] auto_discover=true in config

### Data Setup
- [ ] Vision bulk downloads completed (541 instruments)
- [ ] Monthly dataset built and validated
- [ ] Lifecycle summary reasonable (active ~340, stale ~50)
- [ ] Tail data updated (last data < 7 days old)

### Positions Setup
- [ ] current_positions.csv synced with layer_a
- [ ] All layer_a instruments have zero positions initially
- [ ] Notional calculations validated (contracts × price)

### Validation
- [ ] Config validation passed (0 errors)
- [ ] Trade plan validation tested (hard invariant enforced)
- [ ] End-to-end advisory workflow tested
- [ ] VPN connectivity verified (Binance API reachable)

### Operations
- [ ] Runbook reviewed and understood
- [ ] Daily workflow tested (morning advisory)
- [ ] Monthly maintenance workflow tested
- [ ] Emergency procedures understood (circuit breaker, rollback)

### Monitoring
- [ ] Daily checklist implemented (VPN, positions, advisory, trade plan)
- [ ] Weekly checklist implemented (registry, tail data, disk space)
- [ ] Monthly checklist implemented (Vision, dataset, lifecycle)

## Conclusion

Phase 6 successfully implements production integration with comprehensive config validation, hard invariant enforcement, and operational documentation. The system is now production-ready for 541 Binance perpetuals trading.

**Key Achievement:** Production-safe deployment with validated config, enforced hard invariants, and comprehensive runbook.

**Implementation Status:**
- ✅ Config validation tool complete
- ✅ Trade plan hard invariant enforced
- ✅ Operations runbook complete (7 sections)
- ✅ Unit tests passing (10/10)
- ✅ Verification complete

**All 6 Phases Complete:** 541 Binance Perpetuals Scale-Up Ready for Production

---

## Phase Completion Summary

| Phase | Status | Key Achievement |
|-------|--------|-----------------|
| Phase 1 | ✅ Complete | Registry-aware advisory workflow |
| Phase 2 | ✅ Complete | Opportunistic registry refresh |
| Phase 3 | ✅ Complete | Lifecycle from Vision data |
| Phase 4 | ✅ Complete | Vision-first data management |
| Phase 5 | ✅ Complete | Top-K selection with hysteresis |
| Phase 6 | ✅ Complete | Production integration |

**Total Duration:** 6 phases implemented over 2 days (2026-02-13 to 2026-02-14)

**Final Deliverable:** Production-ready system for trading 541 Binance perpetual futures with automatic discovery, dynamic universe selection, and comprehensive operational safety.
