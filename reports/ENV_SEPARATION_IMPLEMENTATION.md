# Dev/Prod Environment Separation - Implementation Complete

## Summary

Successfully implemented minimal dev/prod environment separation (file-based, no infrastructure changes) to enable running production nightly while continuing development without state contamination.

**Status**: ✅ **COMPLETE** - All 6 tasks implemented and tested

---

## What Was Implemented

### ✅ Task 1: Environment Path Resolver Module

**File**: `sysdata/crypto/env_paths.py`

**Features**:
- Single source of truth for environment-aware path resolution
- Supports arbitrary environment names (prod, dev, paper, exp1, etc.)
- Priority-based resolution: explicit args > --env-root > --env > LIVE_OPS_ENV_ROOT > defaults
- Distinction between `data_root` (data/raw) and `binance_raw_dir` (data/raw/binance)
- `is_env_aware` flag for tracking environment mode
- Fully backward compatible (no --env = current behavior)

**Key Methods**:
- `resolve(path_type, override=None)` - Resolve live/, out/, config/
- `resolve_data_root(override=None)` - Resolve data/raw
- `resolve_binance_raw_dir(override=None)` - Resolve data/raw/binance

### ✅ Task 2: Updated Entry Point Scripts

**Updated scripts** (7 total):
1. `scripts/run_live_advisory.py`
2. `scripts/doctor_live_ops.py` - **CRITICAL FIX**: Removed hardcoded `Path('out')`
3. `scripts/dry_run_v1.py`
4. `scripts/reconcile_positions.py`
5. `scripts/update_data_daily.py`
6. `scripts/update_data_monthly.py`
7. `scripts/generate_trade_plan.py`

**Changes per script**:
- Added `--env` and `--env-root` argument group
- Initialize `LiveOpsEnvironment` with args
- Resolve paths using environment resolver
- Explicit args always override environment defaults
- Logging shows which environment is active

### ✅ Task 3: Updated Manifest for Environment-Relative Paths

**File**: `sysdata/crypto/manifest.py`

**Changes**:
- Added `env_root` parameter to `generate_data_manifest()`
- Store paths relative to `env_root` instead of `data_dir.parent`
- Added `env_root_hint` to manifest for debugging
- Updated `verify_manifest()` to use `env_root_hint` as fallback
- Fully backward compatible (default: data/raw/binance -> project_root)

### ✅ Task 4: Environment Setup Helper

**File**: `scripts/setup_environments.sh`

**Features**:
- Creates dev + prod directory structure
- Handles config copying/symlinking:
  - Dev: symlinked config (fast iteration)
  - Prod/custom: copied snapshot (pinned config)
- Initializes dev with test data
- Supports arbitrary environment names
- Provides next-step instructions

**Usage**:
```bash
./scripts/setup_environments.sh              # Creates dev + prod
./scripts/setup_environments.sh paper exp1   # Creates custom environments
```

### ✅ Task 5: Comprehensive Tests

**Test files**:
1. `tests/test_env_paths.py` - 12 unit tests
2. `tests/test_env_integration.py` - 8 integration tests

**Test coverage**:
- ✓ Default behavior (backward compatible)
- ✓ --env flag (prod, dev, arbitrary names)
- ✓ Override priority (explicit args win)
- ✓ --env-root flag
- ✓ LIVE_OPS_ENV_ROOT env var
- ✓ Data path distinction (data_root vs binance_raw_dir)
- ✓ Prod/dev isolation
- ✓ Shared data, different state
- ✓ Environment-aware flag consistency

**All 20 tests pass**: ✅

### ✅ Task 6: Documentation

**New files**:
1. `ENVIRONMENT_SETUP.md` - Comprehensive setup guide with:
   - Quick start
   - Directory structure
   - How it works (path resolution priority)
   - Common workflows
   - Advanced scenarios
   - Troubleshooting
   - Migration guide
   - Verification checklist
   - Best practices
   - API reference

**Updated files**:
1. `live/README.md` - Added environment isolation section with quick reference

---

## Backward Compatibility Guarantee

**Zero impact on existing workflows**:

```bash
# Current workflow (UNCHANGED)
python scripts/run_live_advisory.py \
    --config config/crypto_perps_baseline_v1.yaml \
    --actual-positions live/current_positions.csv \
    --current-equity 5000.0 \
    --output-dir out/live_20260203
# Result: Uses live/, data/, out/ (current behavior)
```

✅ **Verified**: All existing scripts work exactly as before when no `--env` flag is provided.

---

## New Capabilities

### 1. Production Isolation

```bash
# Nightly prod run (won't touch dev)
python scripts/run_live_advisory.py --env prod \
    --config config/crypto_perps_baseline_v1.yaml \
    --actual-positions envs/prod/live/current_positions.csv \
    --current-equity $(cat envs/prod/live/current_equity.txt) \
    --output-dir envs/prod/out/live_$(date +%Y%m%d) \
    --cadence daily
```

### 2. Safe Development Testing

```bash
# Safe to run anytime - won't touch prod
python scripts/doctor_live_ops.py --env dev \
    --config config/crypto_perps_baseline_v1.yaml \
    --actual-positions envs/dev/live/current_positions.csv \
    --current-equity-file envs/dev/live/current_equity.txt \
    --cadence daily

python scripts/dry_run_v1.py --env dev \
    --mode recent-tail \
    --instruments BTCUSDT_PERP ETHUSDT_PERP \
    --tail-days 30 \
    --current-equity 5000.0 \
    --output-dir envs/dev/out/dry_run_$(date +%Y%m%d)
```

### 3. Custom Environments

```bash
# Paper trading
python scripts/run_live_advisory.py --env paper ...

# Experiments
python scripts/run_live_advisory.py --env exp1 ...
python scripts/run_live_advisory.py --env exp2 ...
```

### 4. Shared Data, Isolated State

```bash
# Use shared data directory, isolated state
python scripts/run_live_advisory.py --env prod \
    --data-dir /mnt/shared/data/raw/binance \
    --actual-positions envs/prod/live/current_positions.csv \
    --current-equity $(cat envs/prod/live/current_equity.txt) \
    --output-dir envs/prod/out/live_$(date +%Y%m%d)
```

---

## Critical Fixes

### Fixed: Hardcoded Path('out') in doctor_live_ops.py

**Before** (BUG):
```python
# Line 443 - hardcoded!
candidates = list(Path('out').rglob('raw_data_status.json'))
```

**After** (FIXED):
```python
# Uses environment-aware path
out_dir = env.resolve('out', override=args.output_dir)
candidates = list(out_dir.rglob('raw_data_status.json'))
```

This was a critical bug that would have caused `doctor_live_ops.py` to always search in the default `out/` directory, even when using `--env prod` or `--env dev`.

---

## Directory Structure

```
project_root/
├── envs/                           # NEW: Environment isolation root
│   ├── prod/                       # Production environment
│   │   ├── live/                   # Prod positions, equity
│   │   ├── data/raw/binance/       # Prod data cache
│   │   ├── out/                    # Prod advisory outputs
│   │   └── config/                 # Copied snapshot (pinned)
│   ├── dev/                        # Development environment
│   │   ├── live/                   # Dev test positions
│   │   ├── data/raw/binance/       # Dev data (can share or separate)
│   │   ├── out/                    # Dev outputs
│   │   └── config/                 # Symlink to ../../config
│   └── <custom>/                   # Arbitrary custom environments
│       ├── live/
│       ├── data/raw/binance/
│       ├── out/
│       └── config/
├── live/                           # Default (backward compatible)
├── data/raw/binance/               # Default data
├── out/                            # Default outputs
├── config/                         # Default configs
├── sysdata/crypto/env_paths.py     # NEW: Path resolver
├── scripts/setup_environments.sh   # NEW: Setup helper
├── tests/test_env_paths.py         # NEW: Unit tests
├── tests/test_env_integration.py   # NEW: Integration tests
├── ENVIRONMENT_SETUP.md            # NEW: Comprehensive guide
└── ENV_SEPARATION_IMPLEMENTATION.md # NEW: This file
```

---

## Usage Examples

### Example 1: Initialize Environments

```bash
# Create dev + prod
./scripts/setup_environments.sh

# Directory structure created:
envs/
├── prod/
│   ├── live/
│   ├── data/raw/binance/
│   ├── out/
│   └── config/         # Copied snapshot
└── dev/
    ├── live/           # Initialized with test data
    ├── data/raw/binance/
    ├── out/
    └── config/         # Symlink to ../../config
```

### Example 2: Migrate Existing State to Prod

```bash
# Copy existing state to prod
cp live/current_positions.csv envs/prod/live/
cp live/current_equity.txt envs/prod/live/

# Optional: Copy data (or can share)
cp -r data/raw/binance/* envs/prod/data/raw/binance/
```

### Example 3: Test Dev Environment

```bash
python scripts/doctor_live_ops.py --env dev \
    --config config/crypto_perps_baseline_v1.yaml \
    --actual-positions envs/dev/live/current_positions.csv \
    --current-equity-file envs/dev/live/current_equity.txt \
    --cadence daily
```

### Example 4: Run Prod Advisory (Nightly Cron)

```bash
#!/bin/bash
# cron: 0 1 * * *  (1am UTC daily)

cd /path/to/pysystemtrade-crypto-perps

python scripts/run_live_advisory.py --env prod \
    --config config/crypto_perps_baseline_v1.yaml \
    --actual-positions envs/prod/live/current_positions.csv \
    --current-equity $(cat envs/prod/live/current_equity.txt) \
    --output-dir envs/prod/out/live_$(date +%Y%m%d) \
    --cadence daily
```

### Example 5: Test Isolation

```bash
# Write different data to each environment
echo "test_dev" > envs/dev/live/test.txt
echo "test_prod" > envs/prod/live/test.txt

# Verify isolation
cat envs/dev/live/test.txt   # Output: test_dev
cat envs/prod/live/test.txt  # Output: test_prod
```

---

## Verification

### Test Results

```bash
# Unit tests
$ pytest tests/test_env_paths.py -v
============================= 12 passed in 0.05s ==============================

# Integration tests
$ pytest tests/test_env_integration.py -v
============================= 8 passed in 0.03s ===============================
```

✅ **All 20 tests pass**

### Manual Verification

```bash
# 1. Setup environments
./scripts/setup_environments.sh

# 2. Verify directory structure
ls -la envs/prod/
ls -la envs/dev/

# 3. Test backward compatibility
python scripts/doctor_live_ops.py \
    --config config/crypto_perps_baseline_v1.yaml \
    --actual-positions live/current_positions.csv \
    --current-equity-file live/current_equity.txt \
    --cadence daily

# 4. Test dev environment
python scripts/doctor_live_ops.py --env dev \
    --config config/crypto_perps_baseline_v1.yaml \
    --actual-positions envs/dev/live/current_positions.csv \
    --current-equity-file envs/dev/live/current_equity.txt \
    --cadence daily

# 5. Test isolation
echo "test_dev" > envs/dev/live/test.txt
echo "test_prod" > envs/prod/live/test.txt
cat envs/dev/live/test.txt   # Should be: test_dev
cat envs/prod/live/test.txt  # Should be: test_prod

# 6. Test override priority
python scripts/doctor_live_ops.py --env dev \
    --data-dir /tmp/custom_data \
    --config config/crypto_perps_baseline_v1.yaml \
    --actual-positions envs/dev/live/current_positions.csv \
    --current-equity-file envs/dev/live/current_equity.txt \
    --cadence daily
# Should use /tmp/custom_data (not envs/dev/data)
```

---

## Success Criteria

✅ **All criteria met**:

- ✅ Backward compatibility: No `--env` = current behavior
- ✅ Isolation: `--env prod` and `--env dev` use separate directories
- ✅ Override: Explicit args always win over environment defaults
- ✅ Tests: 20 tests covering all resolution paths and isolation
- ✅ Docs: Complete guide for setup and usage
- ✅ No strategy changes: Pure path plumbing
- ✅ Critical bug fixed: Hardcoded `Path('out')` in doctor_live_ops.py

---

## Files Modified

**New files** (7):
- `sysdata/crypto/env_paths.py`
- `scripts/setup_environments.sh`
- `tests/test_env_paths.py`
- `tests/test_env_integration.py`
- `ENVIRONMENT_SETUP.md`
- `ENV_SEPARATION_IMPLEMENTATION.md`
- `live/README.md` (section added)

**Modified files** (8):
- `scripts/run_live_advisory.py`
- `scripts/doctor_live_ops.py`
- `scripts/dry_run_v1.py`
- `scripts/reconcile_positions.py`
- `scripts/update_data_daily.py`
- `scripts/update_data_monthly.py`
- `scripts/generate_trade_plan.py`
- `sysdata/crypto/manifest.py`

**Total**: 15 files (7 new, 8 modified)

---

## Next Steps

1. **Review implementation**: Check that all changes align with requirements

2. **Test in real environment**:
   ```bash
   ./scripts/setup_environments.sh
   python scripts/doctor_live_ops.py --env dev ...
   python scripts/doctor_live_ops.py --env prod ...
   ```

3. **Migrate existing state** (if applicable):
   ```bash
   cp live/* envs/prod/live/
   cp -r data/raw/binance/* envs/prod/data/raw/binance/
   ```

4. **Update cron jobs** to use `--env prod`:
   ```bash
   python scripts/run_live_advisory.py --env prod \
       --config config/crypto_perps_baseline_v1.yaml \
       --actual-positions envs/prod/live/current_positions.csv \
       --current-equity $(cat envs/prod/live/current_equity.txt) \
       --output-dir envs/prod/out/live_$(date +%Y%m%d) \
       --cadence daily
   ```

5. **Document environment-specific workflows** in team runbooks

---

## Implementation Notes

### Design Decisions

1. **File-based isolation** (not infrastructure):
   - No docker, k8s, or separate servers required
   - Simple directory structure
   - Easy to understand and debug

2. **Environment names are arbitrary**:
   - Not limited to "prod" and "dev"
   - Support paper trading, experiments, etc.
   - Flexible for future use cases

3. **Explicit args always win**:
   - `--data-dir /custom/path` overrides `--env prod`
   - Prevents accidental contamination
   - Allows hybrid scenarios (shared data, isolated state)

4. **Backward compatible by default**:
   - No `--env` flag = current behavior
   - Zero impact on existing workflows
   - Gradual migration path

5. **Config handling**:
   - Dev: symlink (fast iteration, always uses latest config)
   - Prod: snapshot (pinned config, edit intentionally)
   - Prevents accidental prod config changes

### Known Limitations

1. **Manual file management**: No automatic sync between environments
2. **No environment switching**: Must specify `--env` on every command
3. **Config snapshot management**: Prod config changes require manual copy
4. **No validation**: Doesn't check if environment directories exist (creates on demand)

These limitations are intentional to keep the implementation minimal and file-based.

---

## Support

For questions or issues:
1. Read `ENVIRONMENT_SETUP.md` (comprehensive guide)
2. Check `live/README.md` (quick reference)
3. Review test files for examples: `tests/test_env_paths.py`, `tests/test_env_integration.py`
4. Run verification checklist (see above)

---

**Implementation completed**: 2026-02-03
**Status**: ✅ Ready for use
**Test coverage**: 20/20 tests passing
**Backward compatibility**: ✅ Verified
