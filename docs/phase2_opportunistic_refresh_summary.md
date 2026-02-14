# Phase 2: Opportunistic Registry Refresh - Implementation Summary

**Date:** 2026-02-14
**Status:** ✅ Complete

## Overview

Successfully implemented opportunistic registry refresh in the advisory workflow with cached fallback resilience. The registry now refreshes automatically when running advisory with `auto_discover: true`, and falls back to cached registry if CoinGecko API is unreachable.

## Key Changes

### 1. Enhanced Registry Script: `scripts/refresh_binance_market_registry.py` (MODIFIED)

**New Features:**
- Refactored to support library usage (not just CLI)
- Added diff detection between old and new registry
- Returns changelog dict with new/delisted instruments
- Writes changelog to `registry_changelog.json`

**New Function: `run_refresh(env_root, verbose=True, dry_run=False)`**

Can be called from other scripts (e.g., advisory workflow):

```python
from scripts.refresh_binance_market_registry import run_refresh

changelog = run_refresh(env_root=Path('envs/dev'), verbose=False)
# Returns: {'new_instruments': [...], 'delisted_instruments': [...], 'total_count': 541}
```

**New Function: `detect_changes(metadata_dir, new_candidate_ids)`**

Compares previous and new registry to detect changes:

```python
changelog = detect_changes(metadata_dir, new_candidates)
# Returns diff: new instruments, delisted instruments, total count
```

**Changelog Structure:**
```json
{
  "timestamp": "2026-02-14T10:54:00Z",
  "new_instruments": ["NEWTOKENUSDT_PERP"],
  "delisted_instruments": ["OLDTOKENUSDT_PERP"],
  "total_count": 541
}
```

**CLI Usage (unchanged):**
```bash
# Manual refresh
python scripts/refresh_binance_market_registry.py --env dev

# Dry run
python scripts/refresh_binance_market_registry.py --env dev --dry-run
```

### 2. Advisory Integration: `scripts/run_live_advisory.py` (MODIFIED)

**New Function: `refresh_registry_opportunistic(env_root)`**

Best-effort refresh with cached fallback:

```python
def refresh_registry_opportunistic(env_root: Path) -> tuple:
    """
    Refresh registry with best-effort + cache fallback.

    Returns:
        (success: bool, registry_hash: str, changelog: dict)
    """
    try:
        # Attempt CoinGecko API refresh
        changelog = run_refresh(env_root, verbose=False)
        registry_hash = compute_hash(registry_file)
        return True, registry_hash, changelog
    except Exception as e:
        # Fallback to cached registry
        if cached_registry_exists:
            registry_hash = compute_hash(cached_registry)
            return False, registry_hash, {'cached': True, 'error': str(e)}
        else:
            raise RuntimeError("No cached registry available")
```

**Integration in Workflow:**

Opportunistic refresh runs early in advisory workflow (before candidate extraction):

```python
# In main(), after output directory creation:
if args.use_dynamic_universe and config.auto_discover:
    refresh_success, registry_hash, changelog = refresh_registry_opportunistic(env.root)

    registry_metadata = {
        'hash': registry_hash,
        'refresh_success': refresh_success,
        'timestamp': datetime.utcnow().isoformat(),
        'changelog': changelog
    }
```

**Advisory Metadata File:**

New file: `advisory_metadata_{as_of_date}.json`

```json
{
  "workflow": "live_advisory",
  "timestamp": "2026-02-14T10:54:00Z",
  "config": "config/test_auto_discover.yaml",
  "mode": "dynamic_universe",
  "as_of_date": "2026-02-13",
  "candidate_count": 541,
  "registry_snapshot": {
    "hash": "632c71b9bb2d",
    "refresh_success": true,
    "timestamp": "2026-02-14T10:54:00Z",
    "changelog": {
      "new_instruments": [],
      "delisted_instruments": [],
      "total_count": 541
    }
  }
}
```

**Fallback Behavior:**

If CoinGecko API fails:
```json
{
  "registry_snapshot": {
    "hash": "632c71b9bb2d",
    "refresh_success": false,
    "timestamp": "2026-02-14T10:54:00Z",
    "changelog": {
      "cached": true,
      "error": "HTTPError: 429 Rate Limit"
    }
  }
}
```

**Logged Output:**

```
INFO: Config has auto_discover=true, refreshing registry...
INFO: Refreshing registry from CoinGecko...
INFO: ✓ Registry refreshed (hash: 632c71b9bb2d)
INFO:   Registry updated: 2 new instruments
INFO:   Registry updated: 1 delisted instruments
```

Or on fallback:
```
WARNING: Registry refresh failed: HTTPError: 429 Rate Limit
INFO: ✓ Using cached registry (hash: 632c71b9bb2d)
```

### 3. Artifacts Written

**New File: `registry_changelog.json`**

Location: `envs/{env}/data/raw/metadata/registry_changelog.json`

```json
{
  "timestamp": "2026-02-14T10:54:00Z",
  "new_instruments": ["ARBUSDT_PERP", "OPUSDT_PERP"],
  "delisted_instruments": ["OLDCOINUSDT_PERP"],
  "total_count": 541
}
```

**New File: `advisory_metadata_{date}.json`**

Location: `{output_dir}/advisory_metadata_{as_of_date}.json`

Contains full provenance including registry snapshot hash.

## Testing

### Unit Tests: `tests/test_phase2_opportunistic_refresh.py` (NEW)

**Coverage:**
1. `test_detect_changes_first_run()` - First run (no previous registry)
2. `test_detect_changes_with_additions()` - New instruments added
3. `test_detect_changes_with_delistings()` - Instruments delisted
4. `test_detect_changes_with_both()` - Both additions and delistings
5. `test_opportunistic_refresh_fallback()` - Fallback to cached registry

**Results:** ✅ 5/6 tests passing (1 skipped - requires network)

**Run Tests:**
```bash
python3 -m pytest tests/test_phase2_opportunistic_refresh.py -v
```

### Verification Script: `scripts/verify_phase2.sh` (NEW)

**Checks:**
1. Unit tests passing
2. Diff detection working
3. Manual refresh (dry run)
4. Registry hash computation
5. Changelog file exists
6. Opportunistic refresh function importable

**Run:**
```bash
./scripts/verify_phase2.sh
```

## Architecture Benefits

### Reproducibility

**Registry Hash in Metadata:**
- Every advisory run records exact registry snapshot used
- Hash computed from `discovered_candidate_instruments.json`
- Enables exact reproduction of advisory outputs

**Example:**
```json
{
  "registry_snapshot": {
    "hash": "632c71b9bb2d",
    "timestamp": "2026-02-14T10:54:00Z"
  }
}
```

### Resilience

**Cached Fallback:**
- If CoinGecko API unreachable, falls back to cached registry
- Advisory workflow NEVER bricks due to registry refresh failure
- Logged as warning (not error)

**Fail-Safe:**
- Only raises error if BOTH refresh fails AND no cache exists
- Prevents production advisory from failing on API issues

### Change Tracking

**Diff Detection:**
- Automatic detection of new instruments
- Automatic detection of delisted instruments
- Logged in both changelog file and advisory metadata

**Benefits:**
- Monitor Binance market evolution over time
- Alert on new trading opportunities
- Detect delistings early

## Usage Examples

### Manual Registry Refresh

```bash
# Refresh registry
python scripts/refresh_binance_market_registry.py --env dev

# Check changelog
cat envs/dev/data/raw/metadata/registry_changelog.json | jq '.new_instruments'

# View registry hash
cat envs/dev/data/raw/metadata/discovered_candidate_instruments.json | sha256sum | cut -c1-12
```

### Advisory with Auto-Discover

```bash
# Run advisory (auto-refreshes registry if auto_discover=true)
python scripts/run_live_advisory.py \
    --config config/test_auto_discover.yaml \
    --actual-positions live/current_positions.csv \
    --current-equity 5000 \
    --output-dir out/advisory_20260214 \
    --use-dynamic-universe

# Check advisory metadata
cat out/advisory_20260214/advisory_metadata_2026-02-13.json | jq '.registry_snapshot'
```

### Library Usage

```python
from pathlib import Path
from scripts.refresh_binance_market_registry import run_refresh

# Refresh registry programmatically
env_root = Path('envs/dev')
changelog = run_refresh(env_root, verbose=True, dry_run=False)

print(f"Total instruments: {changelog['total_count']}")
print(f"New: {len(changelog['new_instruments'])}")
print(f"Delisted: {len(changelog['delisted_instruments'])}")
```

## Known Limitations

1. **CoinGecko Rate Limits**
   - Free tier: 10-30 calls/minute
   - Refresh typically succeeds but may fail during high usage
   - Fallback to cache prevents workflow disruption

2. **No Scheduled Refresh**
   - Registry only refreshes when advisory runs
   - Phase 2 is opportunistic (not cron-based)
   - Acceptable for monthly advisory cadence

3. **Changelog History Not Retained**
   - Only latest changelog kept in `registry_changelog.json`
   - Historical changes not tracked (future enhancement)
   - Advisory metadata preserves snapshot-in-time

## Next Steps (Phase 3)

### Lifecycle from Vision Data Coverage

**Goal:** Derive instrument lifecycle metadata from Vision data availability

**Key Features:**
- Track first/last data date for each instrument
- Derive status (ACTIVE, STALE, NO_DATA) from freshness
- Integrate lifecycle filtering in dynamic universe
- Store lifecycle in dataset manifest

**Benefits:**
- More accurate than CoinGecko "first seen" dates
- Historical backtest compatibility (correct date boundaries)
- Reproducible (lifecycle snapshot in manifest)

## Files Modified

### New Files
- `tests/test_phase2_opportunistic_refresh.py` - Unit tests (5/6 passing)
- `scripts/verify_phase2.sh` - Verification script
- `docs/phase2_opportunistic_refresh_summary.md` - This document

### Modified Files
- `scripts/refresh_binance_market_registry.py`
  - Added `run_refresh()` function (library usage)
  - Added `detect_changes()` function (diff detection)
  - Writes `registry_changelog.json` (4th artifact)
- `scripts/run_live_advisory.py`
  - Added `refresh_registry_opportunistic()` function
  - Integrated refresh in workflow (before candidate extraction)
  - Writes `advisory_metadata_{date}.json` with registry snapshot

### New Artifacts
- `envs/{env}/data/raw/metadata/registry_changelog.json` - Diff log
- `{output_dir}/advisory_metadata_{date}.json` - Advisory metadata

## Verification Checklist

- [x] Unit tests passing (5/6)
- [x] Diff detection working (new/delisted)
- [x] Changelog generation working
- [x] Registry hash computation working
- [x] Opportunistic refresh function working
- [x] Cached fallback working
- [x] Advisory metadata includes registry snapshot
- [x] Backward compatibility (non-auto_discover configs work)
- [x] Documentation complete

## Conclusion

Phase 2 successfully implements opportunistic registry refresh with cached fallback resilience. The advisory workflow now automatically keeps the registry fresh while remaining resilient to CoinGecko API failures.

**Key Achievement:** Self-updating registry with reproducibility through snapshot hashing.

**Next:** Phase 3 - Lifecycle tracking from Vision data coverage.
