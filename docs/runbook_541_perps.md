# Operations Runbook: 541 Binance Perpetuals

**Version:** 1.0
**Last Updated:** 2026-02-14
**Status:** Production Ready

## Table of Contents

1. [Quick Reference](#quick-reference)
2. [Daily Operations](#daily-operations)
3. [Monthly Maintenance](#monthly-maintenance)
4. [Registry Management](#registry-management)
5. [Data Management](#data-management)
6. [Troubleshooting](#troubleshooting)
7. [Emergency Procedures](#emergency-procedures)

---

## Quick Reference

### Key Paths

```bash
# Registry artifacts
envs/{env}/data/raw/metadata/discovered_candidate_instruments.json
envs/{env}/data/raw/metadata/registry_changelog.json

# Raw data store
envs/{env}/data/raw/binance/klines/
envs/{env}/data/raw/binance/funding/

# Datasets
envs/{env}/data/datasets/monthly_541_instruments.parquet
envs/{env}/data/datasets/monthly_541_instruments.manifest.json

# Advisory outputs
out/live_advisory_{date}/
```

### Critical Commands

```bash
# Validate config before use
python scripts/validate_config.py \
    --config config/crypto_perps_dynamic_universe_top30.yaml \
    --env prod

# Check data freshness
cat envs/prod/data/raw/raw_data_status.json | jq '.summary'

# Verify last registry refresh
cat envs/prod/data/raw/metadata/registry_changelog.json | jq '.timestamp'

# Check VPN connectivity (required for Binance API)
curl https://fapi.binance.com/fapi/v1/ping
# Expected: {}
```

---

## Daily Operations

### Morning Workflow (Daily Advisory)

**Prerequisite:** VPN connected (required for Binance REST API in geo-blocked regions)

**Step 1: Check VPN Connectivity**
```bash
# Should return {} if VPN working
curl https://fapi.binance.com/fapi/v1/ping

# If fails, connect VPN before proceeding
```

**Step 2: Run Daily Advisory**
```bash
python scripts/run_live_advisory.py \
    --config config/crypto_perps_dynamic_universe_top30.yaml \
    --actual-positions live/current_positions.csv \
    --current-equity 5000 \
    --output-dir out/live_advisory_$(date +%Y%m%d) \
    --use-dynamic-universe \
    --skip-dataset-rebuild  # Reuse monthly dataset
```

**What happens:**
- ✅ Registry refreshed opportunistically (cached fallback if CoinGecko fails)
- ✅ Tail data updated (last 7 days via REST API - requires VPN)
- ✅ Existing monthly dataset reused (no rebuild)
- ✅ Trade plan generated with top-K selection

**Step 3: Review Advisory Outputs**
```bash
# Check trade plan
cat out/live_advisory_$(date +%Y%m%d)/trade_plan_*.csv

# Check sanity checks
cat out/live_advisory_$(date +%Y%m%d)/sanity_checks_*.json | jq '.overall_status'

# Check registry snapshot
cat out/live_advisory_$(date +%Y%m%d)/audit_bundle_*.json | jq '.registry_snapshot'
```

**Expected Results:**
- Trade plan contains <= 30 instruments (all from layer_a)
- Sanity checks status: `pass` or `pass_with_warnings`
- Registry snapshot includes hash for reproducibility

---

## Monthly Maintenance

### Monthly Data Refresh Workflow

**Frequency:** First weekend of each month
**Duration:** 1-2 hours (Vision download) + 15 min (dataset rebuild)

**Step 1: Vision Bulk Download (NO VPN REQUIRED)**

First-time setup (downloads full 6-year history for 541 instruments):
```bash
# Dry run to see plan
python scripts/download_vision_bulk.py \
    --env prod \
    --dry-run

# Download in batches (resumable if interrupted)
python scripts/download_vision_bulk.py \
    --env prod \
    --instruments-limit 100  # 100 per batch

# Check progress
cat envs/prod/data/raw/vision_download_progress.json | jq '.count'
```

**Note:** Vision download is **one-time** for historical data. After initial setup, only run monthly for new instrument additions.

**Step 2: Tail Update (VPN REQUIRED)**

Update recent data (last 7-30 days) via Binance REST API:
```bash
# Requires VPN in geo-blocked regions (e.g., MA)
python scripts/update_data_monthly.py \
    --config config/crypto_perps_dynamic_universe_top30.yaml \
    --data-dir envs/prod/data/raw/binance \
    --tail-days 30  # Update last 30 days
```

**Step 3: Rebuild Monthly Dataset**
```bash
python scripts/build_example_dataset.py \
    --source real \
    --data-dir envs/prod/data/raw/binance \
    --output-path envs/prod/data/datasets/monthly_541_instruments.parquet \
    --allow-jagged \
    --min-history-days 365 \
    --min-coverage 0.50
```

**Step 4: Verify Dataset**
```bash
# Check manifest
cat envs/prod/data/datasets/monthly_541_instruments.manifest.json | jq '.lifecycle_summary'

# Expected: active ~340, stale ~50, no_data ~150

# Check dataset size
ls -lh envs/prod/data/datasets/monthly_541_instruments.parquet
# Expected: ~100-200MB
```

---

## Registry Management

### Registry Refresh (Automatic)

Registry refreshes automatically during daily advisory workflow with cached fallback.

**Manual Refresh:**
```bash
python scripts/refresh_binance_market_registry.py --env prod
```

**Verify Refresh:**
```bash
# Check timestamp
cat envs/prod/data/raw/metadata/registry_changelog.json | jq '.timestamp'

# Check for new/delisted instruments
cat envs/prod/data/raw/metadata/registry_changelog.json | jq '{new: .new_instruments, delisted: .delisted_instruments}'

# Verify count (should be ~541)
cat envs/prod/data/raw/metadata/discovered_candidate_instruments.json | jq '.candidate_instruments | length'
```

### Adding New Instruments to Trading Universe

**Scenario:** New perpetual launches on Binance, you want to add to tradable set

**Step 1: Verify in Registry**
```bash
# Check if instrument in registry
cat envs/prod/data/raw/metadata/discovered_candidate_instruments.json | \
    jq '.candidate_instruments[] | select(. == "NEWUSDT_PERP")'
```

**Step 2: Download Historical Data**
```bash
# Download Vision history for new instrument
python scripts/download_vision_bulk.py \
    --env prod \
    --resume-from NEWUSDT_PERP \
    --instruments-limit 1
```

**Step 3: Check Data Quality**
```bash
# Build test dataset with new instrument
python scripts/build_example_dataset.py \
    --source real \
    --data-dir envs/prod/data/raw/binance \
    --instruments NEWUSDT_PERP \
    --output-path data/test_new_instrument.parquet \
    --allow-jagged

# Check lifecycle
cat data/test_new_instrument.manifest.json | jq '.lifecycle.NEWUSDT_PERP'
```

**Step 4: Add to Config (if eligible)**
```yaml
universe:
  layer_a_instruments:
    - BTCUSDT_PERP
    - ETHUSDT_PERP
    # ... existing ...
    - NEWUSDT_PERP  # ← ADD HERE
```

**Step 5: Validate Config**
```bash
python scripts/validate_config.py \
    --config config/crypto_perps_dynamic_universe_top30.yaml \
    --env prod
```

**Step 6: Sync Positions File**
```bash
# Auto-add new instrument to positions file with zero position
python scripts/sync_positions_file.py \
    --config config/crypto_perps_dynamic_universe_top30.yaml \
    --positions-file live/current_positions.csv
```

---

## Data Management

### Storage Structure

```
envs/{env}/
├── data/
│   ├── raw/
│   │   ├── binance/
│   │   │   ├── klines/           # OHLCV CSVs (Vision downloads)
│   │   │   │   └── {SYMBOL}/
│   │   │   │       └── {SYMBOL}.csv
│   │   │   ├── funding/          # Funding rate CSVs (Vision downloads)
│   │   │   │   └── {SYMBOL}/
│   │   │   │       └── {SYMBOL}.csv
│   │   │   └── api_cache/        # REST API cache (tail updates)
│   │   └── metadata/
│   │       ├── discovered_candidate_instruments.json  # Registry
│   │       ├── registry_changelog.json                # Diff tracker
│   │       └── vision_download_progress.json          # Download tracker
│   └── datasets/
│       ├── monthly_541_instruments.parquet            # Monthly dataset
│       └── monthly_541_instruments.manifest.json      # Lifecycle metadata
```

### Disk Space Planning

**Raw Data (Vision + API cache):**
- 541 instruments × 6 years × 2 types (klines + funding) = ~500MB
- API cache (last 30 days) = ~5MB
- **Total:** ~500MB

**Datasets:**
- Monthly dataset (541 instruments, jagged) = ~100-200MB
- Manifests = ~1MB

**Advisory Outputs:**
- Per day = ~5MB (backtest + trade plan + audit trail)
- Per month = ~150MB

**Recommended:** 10GB free space minimum

### Data Cleanup

**Clean Old Advisory Outputs (keep last 30 days):**
```bash
find out/live_advisory_* -type d -mtime +30 -exec rm -rf {} +
```

**Clean API Cache (regenerate on next update):**
```bash
rm -rf envs/prod/data/raw/binance/api_cache/*
```

**Note:** Do NOT delete Vision downloads or monthly datasets unless re-downloading

---

## Troubleshooting

### VPN Issues (Binance API Unreachable)

**Symptom:** Advisory fails with "FAIL FAST: Binance REST API unreachable"

**Cause:** Binance Futures API is geo-blocked in some regions (e.g., MA)

**Solution:**
```bash
# 1. Check connectivity
curl https://fapi.binance.com/fapi/v1/ping

# 2. If fails, connect VPN and retry
# Expected after VPN: {}

# 3. Re-run advisory (will skip tail update if --skip-data-update)
python scripts/run_live_advisory.py \
    --config config/crypto_perps_dynamic_universe_top30.yaml \
    ... \
    --skip-data-update  # Use existing data if VPN unavailable
```

**Prevention:** Keep VPN connected during trading hours for fresh data

### Registry Refresh Failure

**Symptom:** Advisory shows "Registry refresh failed: ..."

**Cause:** CoinGecko API rate limit or network issue

**Impact:** Low (advisory uses cached registry with fallback)

**Solution:**
```bash
# Verify cached registry exists and is recent
ls -lh envs/prod/data/raw/metadata/discovered_candidate_instruments.json

# Manual refresh after rate limit expires
python scripts/refresh_binance_market_registry.py --env prod
```

**Note:** Advisory continues with cached registry, hash included in metadata for reproducibility

### Lifecycle Status: STALE

**Symptom:** Instrument shows `status: STALE` in manifest

**Cause:** Last data update > 7 days ago

**Solution:**
```bash
# Check which instruments are stale
cat envs/prod/data/datasets/monthly_541_instruments.manifest.json | \
    jq '.lifecycle | to_entries | map(select(.value.status == "STALE")) | .[].key'

# Update tail data
python scripts/update_data_monthly.py \
    --config config/crypto_perps_dynamic_universe_top30.yaml \
    --data-dir envs/prod/data/raw/binance \
    --tail-days 30

# Rebuild dataset
python scripts/build_example_dataset.py ...
```

### Trade Plan Validation Failure

**Symptom:** `HARD INVARIANT VIOLATION: Trade plan includes instruments NOT in layer_a`

**Cause:** Config mismatch (top_k > layer_a count or instruments not in layer_a)

**Solution:**
```bash
# 1. Validate config
python scripts/validate_config.py \
    --config config/crypto_perps_dynamic_universe_top30.yaml \
    --env prod

# 2. Fix errors (e.g., expand layer_a if top_k too large)

# 3. Sync positions file
python scripts/sync_positions_file.py \
    --config config/crypto_perps_dynamic_universe_top30.yaml \
    --positions-file live/current_positions.csv
```

### Dataset Build Failures

**Symptom:** `build_example_dataset.py` fails with missing instruments

**Cause:** Vision downloads incomplete or missing data

**Solution:**
```bash
# 1. Check download progress
cat envs/prod/data/raw/vision_download_progress.json | jq '.count'

# 2. Resume downloads from last completed
python scripts/download_vision_bulk.py \
    --env prod \
    --instruments-limit 100

# 3. Rebuild dataset after downloads complete
python scripts/build_example_dataset.py ...
```

---

## Emergency Procedures

### Circuit Breaker: Stop All Trading

**Scenario:** Major system issue, need to halt trading immediately

```bash
# 1. Generate empty trade plan (no changes)
# Edit live/current_positions.csv - set all contracts to 0

# 2. Re-run advisory to confirm no trades
python scripts/run_live_advisory.py ...

# 3. Verify trade plan is empty or only closes positions
cat out/live_advisory_*/trade_plan_*.csv

# 4. DO NOT EXECUTE until issue resolved
```

### Rollback to Previous Dataset

**Scenario:** New dataset has issues, rollback to previous month

```bash
# 1. Find previous dataset
ls -lt envs/prod/data/datasets/

# 2. Symlink to use as "monthly_541_instruments.parquet"
cd envs/prod/data/datasets/
ln -sf monthly_541_instruments_BACKUP.parquet monthly_541_instruments.parquet

# 3. Re-run advisory with rolled-back dataset
python scripts/run_live_advisory.py \
    ... \
    --skip-dataset-rebuild  # Use symlinked dataset
```

### Registry Corruption Recovery

**Scenario:** Registry file corrupted or invalid

```bash
# 1. Backup corrupted registry
mv envs/prod/data/raw/metadata/discovered_candidate_instruments.json \
   envs/prod/data/raw/metadata/discovered_candidate_instruments.json.BAD

# 2. Refresh from CoinGecko
python scripts/refresh_binance_market_registry.py --env prod

# 3. Validate new registry
cat envs/prod/data/raw/metadata/discovered_candidate_instruments.json | jq '.candidate_instruments | length'
# Expected: ~541

# 4. Re-run advisory
python scripts/run_live_advisory.py ...
```

---

## Monitoring Checklist

### Daily (Pre-Trading)

- [ ] VPN connected and Binance API reachable
- [ ] Current positions file up to date
- [ ] Last advisory run successful (check logs)
- [ ] Trade plan sanity checks passed
- [ ] No stale instruments in trade plan

### Weekly

- [ ] Registry refreshed in last 7 days
- [ ] Tail data updated (last data < 7 days old)
- [ ] Advisory outputs archived
- [ ] Disk space > 5GB free

### Monthly

- [ ] Vision downloads completed (if new instruments added)
- [ ] Monthly dataset rebuilt
- [ ] Lifecycle summary reviewed (active/stale/no_data counts)
- [ ] Config validation passed
- [ ] layer_a_instruments updated (if needed)

---

## Support Contacts

**Documentation:**
- Phase 1-6 Implementation Summaries: `docs/phase*_summary.md`
- Automatic Perp Discovery: `docs/automatic_perp_discovery.md`

**Verification Scripts:**
- `scripts/verify_phase*.sh` - Run after changes to verify system health

**Logs Location:**
- Advisory logs: `out/live_advisory_{date}/backtest_latest/`
- Error logs: Check stdout/stderr from script runs

---

## Version History

| Version | Date       | Changes                                      |
|---------|------------|----------------------------------------------|
| 1.0     | 2026-02-14 | Initial runbook for 541 perps scale-up       |

---

**Last Reviewed:** 2026-02-14
**Next Review Due:** 2026-03-14
