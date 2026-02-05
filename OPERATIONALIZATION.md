# Daily Live Ops V1: Operationalization Guide

**Status**: Production-ready with validation, reconciliation, and dry-run capabilities

**V1 Features**:
- Hybrid data ingestion (Vision + API)
- Two-date concept (expected vs dataset as_of_date)
- Staleness overlay with eligibility rules
- Day-granular freshness reporting
- Full backward compatibility with V0

**Operationalization Components**:
1. **Dry Run Script**: End-to-end validation with real data
2. **Doctor CLI**: Preflight health check before daily advisory
3. **Positions Reconciliation**: Catch operator errors in positions file
4. **Cutover Time Enforcement**: UTC time policy for consistent operations

---

## Quickstart: Daily Operations Checklist

**TIMING**: Daily bars are UTC-based. Run after 00:00 UTC (≈7pm ET winter / ≈8pm ET summer) so `expected_as_of_date = yesterday UTC` is fresh.

### Step 1: First-Time Setup (One-Time)

```bash
# 1. Download historical data (one-time)
python scripts/update_data_monthly.py \
    --config config/crypto_perps_baseline_v1.yaml \
    --data-dir data/raw/binance

# 2. Validate setup with dry run
python scripts/dry_run_v1.py \
    --mode recent-tail \
    --instruments BTCUSDT_PERP ETHUSDT_PERP \
    --tail-days 30 \
    --output-dir out/dry_run_initial \
    --current-equity 5000.0

# If dry run passes: ready for daily operations
```

### Step 2: Daily Operations (Every Day After 00:00 UTC)

```bash
# 1. Doctor preflight check (MANDATORY)
python scripts/doctor_live_ops.py \
    --config config/crypto_perps_baseline_v1.yaml \
    --actual-positions live/current_positions.csv \
    --current-equity-file live/current_equity.txt \
    --data-dir data/raw/binance \
    --cadence daily

# If doctor FAILS (exit code 2): STOP, fix issues, re-run

# 2. Run daily advisory
python scripts/run_live_advisory.py \
    --config config/crypto_perps_baseline_v1.yaml \
    --actual-positions live/current_positions.csv \
    --current-equity $(cat live/current_equity.txt) \
    --output-dir out/live_$(date +%Y%m%d) \
    --cadence daily \
    --tail-days 3

# 3. Review trade plan
cat out/live_$(date +%Y%m%d)/trade_plan_*.csv

# 4. Execute trades manually on exchange

# 5. Update positions with actual fills
# (Edit live/current_positions.csv)

# 6. Reconcile positions (MANDATORY after edits)
python scripts/reconcile_positions.py \
    --positions-file live/current_positions.csv \
    --current-equity <new_equity> \
    --config config/crypto_perps_baseline_v1.yaml \
    --fix-mode suggest

# If errors: use --fix-mode auto or fix manually

# 7. Update equity
echo "<new_equity>" > live/current_equity.txt

# 8. Commit to git
git add live/current_positions.csv live/current_equity.txt
git commit -m "Update positions after daily trade execution $(date +%Y-%m-%d)"
```

### Step 3: Before Code Changes (Ad-Hoc)

```bash
# Validate with dry run before deploying code changes
python scripts/dry_run_v1.py \
    --mode recent-tail \
    --instruments BTCUSDT_PERP ETHUSDT_PERP \
    --tail-days 30 \
    --output-dir out/dry_run_$(git rev-parse --short HEAD) \
    --current-equity 5000.0

# If passes: safe to deploy
```

---

## Table of Contents

1. [Daily Workflow](#daily-workflow)
2. [Cutover Time Policy](#cutover-time-policy)
3. [Dry Run Validation](#dry-run-validation)
4. [Preflight Health Check (Doctor CLI)](#preflight-health-check-doctor-cli)
5. [Positions Reconciliation](#positions-reconciliation)
6. [Troubleshooting Guide](#troubleshooting-guide)
7. [Common Scenarios](#common-scenarios)

---

## Daily Workflow

**Recommended daily workflow** (run between 00:30-06:00 UTC):

```bash
# Step 1: Run preflight health check
python scripts/doctor_live_ops.py \
    --config config/crypto_perps_baseline_v1.yaml \
    --actual-positions live/current_positions.csv \
    --current-equity-file live/current_equity.txt \
    --data-dir data/raw/binance \
    --cadence daily

# If doctor FAILS (exit code 2): STOP, fix issues, re-run doctor

# Step 2: Run daily advisory
python scripts/run_live_advisory.py \
    --config config/crypto_perps_baseline_v1.yaml \
    --actual-positions live/current_positions.csv \
    --current-equity $(cat live/current_equity.txt) \
    --output-dir out/live_advisory_$(date +%Y%m%d) \
    --cadence daily \
    --tail-days 3

# Step 3: Review trade plan
cat out/live_advisory_$(date +%Y%m%d)/trade_plan_*.csv
cat out/live_advisory_$(date +%Y%m%d)/sanity_checks_*.json

# Step 4: Execute trades manually on exchange

# Step 5: Update positions with actual fills
# Edit live/current_positions.csv with actual contracts, prices, notional

# Step 6: Run positions reconciliation
python scripts/reconcile_positions.py \
    --positions-file live/current_positions.csv \
    --current-equity <new_equity> \
    --config config/crypto_perps_baseline_v1.yaml \
    --fix-mode suggest

# If reconciliation finds errors: fix them or use --fix-mode auto

# Step 7: Update equity
echo "<new_equity>" > live/current_equity.txt

# Step 8: Commit positions to git
git add live/current_positions.csv live/current_equity.txt
git commit -m "Update positions after daily trade execution $(date +%Y-%m-%d)"
```

---

## Cutover Time Policy

### Expected as_of_date: Yesterday UTC (D-1)

**Default behavior**: `expected_as_of_date = yesterday UTC`

**Daily bars are UTC-based**: Binance daily klines close at 23:59:59 UTC, so yesterday's bar is complete at 00:00:00 UTC today.

### Safe Operating Window: 00:30 - 06:00 UTC

**Timezone equivalents**:
- **00:30 UTC** = 7:30pm ET (winter EST) / 8:30pm ET (summer EDT) *previous evening*
- **06:00 UTC** = 1:00am ET (winter EST) / 2:00am ET (summer EDT)

**Why this window?**
- **Minimum wait**: 00:05 UTC (allows 5 min buffer for API cache propagation)
- **Optimal time**: 00:30 - 06:00 UTC (gives time to execute trades during Asian/European session)
- **Late warning**: After 12:00 UTC (trading on stale yesterday close prices)

**Recommended for US traders**: Run around **7-8pm ET** (= 00:00-01:00 UTC next day) so you have fresh yesterday data and can execute before markets move overnight.

### Warnings

- **Before 00:05 UTC**: Warns that today's data may not be available yet
- **After 12:00 UTC**: Warns that you're trading on stale data (yesterday's close)

### Override for Testing

Use `--expected-date` flag to test with historical dates:

```bash
python scripts/run_live_advisory.py \
    --cadence daily \
    --expected-date 2026-01-15 \
    --config config/crypto_perps_baseline_v1.yaml \
    --actual-positions live/current_positions.csv \
    --current-equity 5000.0 \
    --output-dir out/test_override
```

---

## Dry Run Validation

End-to-end validation of V1 workflow with real data.

### Mode A: Recent Tail (RECOMMENDED)

Tests last N days using existing Vision base + API tail. **No downloads required** if Vision base is current.

```bash
python scripts/dry_run_v1.py \
    --mode recent-tail \
    --instruments BTCUSDT_PERP ETHUSDT_PERP BNBUSDT_PERP \
    --tail-days 30 \
    --output-dir out/dry_run_$(date +%Y%m%d) \
    --data-dir data/raw/binance \
    --current-equity 5000.0
```

**When to use**:
- Routine validation before going live
- Testing after code changes
- Verifying data pipeline is working
- Quick validation (30-60 seconds)

### Mode B: Historical Window

Tests explicit date range, automatically downloads missing Vision ZIPs if needed.

```bash
python scripts/dry_run_v1.py \
    --mode historical \
    --instruments BTCUSDT_PERP ETHUSDT_PERP \
    --start-date 2025-12-01 \
    --end-date 2026-01-15 \
    --output-dir out/dry_run_historical \
    --data-dir data/raw/binance \
    --current-equity 5000.0
```

**When to use**:
- Testing historical windows
- Validating backtest reproducibility
- Investigating specific date ranges
- Takes longer (downloads may be required)

### Validation Checks

Dry run validates:
1. **Data update completes** (API tail or Vision monthly)
2. **Date validation**: expected vs dataset as_of_date, staleness computation
3. **Dataset build**: rectangular panel, no NaNs, manifest integrity
4. **Backtest**: runs successfully, last date matches dataset
5. **Trade plan**: staleness overlay applied if any staleness detected

### Exit Codes

- **0 = PASS**: All checks green
- **1 = PASS_WITH_WARNINGS**: Non-critical warnings (e.g., 1 instrument stale)
- **2 = FAIL**: Critical errors (e.g., dataset build failed, NaNs in panel)

---

## Preflight Health Check (Doctor CLI)

Comprehensive validation before running daily advisory. **Run this first** to catch issues early.

### Basic Usage

```bash
# With explicit data status path
python scripts/doctor_live_ops.py \
    --config config/crypto_perps_baseline_v1.yaml \
    --actual-positions live/current_positions.csv \
    --current-equity-file live/current_equity.txt \
    --data-status-path out/latest/raw_data_status.json \
    --cadence daily

# Auto-discover latest data status
python scripts/doctor_live_ops.py \
    --config config/crypto_perps_baseline_v1.yaml \
    --actual-positions live/current_positions.csv \
    --current-equity-file live/current_equity.txt \
    --data-dir data/raw/binance \
    --cadence daily
```

### Checks Performed

1. **Data Recency** (PRIMARY SOURCE: data_status.json)
   - Extracts expected_as_of_date, dataset_as_of_date from JSON
   - Verifies expected = yesterday UTC (not today, not 2+ days ago)
   - Verifies dataset within tolerance (≤ 1 day lag)
   - Reports per-instrument staleness summary
   - **FYI only**: Mentions API cache file mtime as supplementary info

2. **Manifest Integrity** (optional)
   - Verifies all file checksums match
   - Checks for missing files
   - Warns if API cache files are > 7 days old

3. **Positions Sanity** (DELEGATES to validation library)
   - Calls `validate_positions_file()` from `sysdata.crypto.positions_validation`
   - Notional arithmetic: `contracts × price = notional` (tolerance: max($1, 0.1%))
   - Sign consistency: both positive (long) or both negative (short)
   - Gross leverage: sum(|notional|) / equity (warns if > 1.8x, errors if > 2.0x)
   - Missing instruments: verify all config universe instruments present
   - Stale timestamps: warns if > 24-48 hours (NOT 7 days)
   - Units confusion: warnings for suspicious values (not errors)

4. **Equity Staleness**
   - Verifies equity > 0 and reasonable
   - **FYI only**: Mentions equity file mtime as supplementary info

5. **Rectangular Panel** (if dataset exists)
   - Verifies no NaNs in close prices
   - Verifies all instruments have same date count

### Exit Codes

- **0 = PASS**: All checks green
- **1 = PASS_WITH_WARNINGS**: Non-critical warnings
- **2 = FAIL**: Critical errors (DO NOT PROCEED)

### When to Run

- **Before every daily advisory** (recommended)
- After manual edits to positions file
- After data updates
- When troubleshooting issues

---

## Positions Reconciliation

Helper to catch operator errors in `live/current_positions.csv`.

### Basic Usage

```bash
# Suggest mode (show errors and suggested fixes)
python scripts/reconcile_positions.py \
    --positions-file live/current_positions.csv \
    --current-equity 5237.50 \
    --config config/crypto_perps_baseline_v1.yaml \
    --fix-mode suggest

# Auto-fix mode (fix notional arithmetic errors automatically)
python scripts/reconcile_positions.py \
    --positions-file live/current_positions.csv \
    --current-equity 5237.50 \
    --config config/crypto_perps_baseline_v1.yaml \
    --fix-mode auto
```

### Validation Checks

Uses the **same positions validation library** as doctor CLI (single source of truth):

1. **Notional Arithmetic**
   - Verifies: `notional_usd = contracts × mark_price_usd`
   - **Realistic tolerance**: `abs(diff) ≤ max($1.00, 0.1% of |expected_notional|)`
   - Example: BTCUSDT_PERP with contracts=0.003, price=45000 → notional must be 135.00 ± $1.00

2. **Sign Consistency**
   - Verifies: `sign(notional) = sign(contracts)`
   - Long: both positive, Short: both negative
   - Common error: forgot to negate notional when entering short position

3. **Gross Leverage**
   - Computes: `sum(|notional|) / equity`
   - Warns if > 1.8x (approaching 2.0x cap)
   - Errors if > 2.0x (violates cap)

4. **Missing Instruments**
   - Verifies all config universe instruments present in CSV

5. **Stale Timestamps**
   - **Warns if > 24-48 hours** (daily trading cadence)
   - Errors if > 7 days (definitely stale)

6. **Units Confusion** (WARNINGS only, not errors)
   - If `|contracts| > 100`: may have entered notional in contracts column
   - If `|notional| < 10`: may have entered contracts in notional column
   - If `mark_price` very low/high: may be stale

### Auto-Fix Mode

Auto-fix only handles **notional arithmetic errors**. Other errors must be fixed manually.

```bash
python scripts/reconcile_positions.py \
    --positions-file live/current_positions.csv \
    --current-equity 5237.50 \
    --config config/crypto_perps_baseline_v1.yaml \
    --fix-mode auto
```

**Safety features**:
- Creates backup: `live/current_positions.csv.bak.YYYYMMDD_HHMMSS`
- Re-validates after fixing
- Prints before/after comparison

### When to Run

- After manually editing positions file
- Before committing positions to git
- When doctor CLI reports positions errors
- After executing trades on exchange

---

## Troubleshooting Guide

### Issue: Doctor fails with "Data status file not found"

**Cause**: data_status.json not generated yet

**Fix**:
```bash
# Run data update first
python scripts/update_data_daily.py \
    --config config/crypto_perps_baseline_v1.yaml \
    --data-dir data/raw/binance \
    --tail-days 3 \
    --output-report out/raw_data_status.json

# Then run doctor
python scripts/doctor_live_ops.py \
    --config config/crypto_perps_baseline_v1.yaml \
    --actual-positions live/current_positions.csv \
    --current-equity-file live/current_equity.txt \
    --data-status-path out/raw_data_status.json \
    --cadence daily
```

### Issue: Doctor fails with "Expected != dataset dates but max_staleness=0"

**Cause**: Invariant violation (bug in staleness computation)

**Fix**: Report this as a bug. Workaround: check data_status.json manually

### Issue: Reconciliation reports notional arithmetic error

**Cause**: Manually entered notional doesn't match `contracts × price`

**Fix**:
```bash
# Option 1: Auto-fix (creates backup)
python scripts/reconcile_positions.py \
    --positions-file live/current_positions.csv \
    --current-equity 5237.50 \
    --config config/crypto_perps_baseline_v1.yaml \
    --fix-mode auto

# Option 2: Manually edit positions file
# Update notional_usd to match contracts × mark_price_usd
```

### Issue: Reconciliation reports sign consistency error

**Cause**: Contracts and notional have mismatched signs (e.g., negative contracts but positive notional)

**Fix**: Manually edit positions file. Check if position is long (both positive) or short (both negative).

Example:
```csv
# WRONG: short position with positive notional
BNBUSDT_PERP,-0.500,350.00,175.00,...

# CORRECT: short position with negative notional
BNBUSDT_PERP,-0.500,350.00,-175.00,...
```

### Issue: Reconciliation reports gross leverage > 2.0x

**Cause**: Total |notional| exceeds 2.0x equity cap

**Fix**:
1. Verify equity is current (not stale from days ago)
2. Reduce position sizes
3. Update current_equity.txt with actual equity (may have grown)

### Issue: Dry run fails in Mode A with "missing ZIP"

**Cause**: Vision base doesn't cover the start_date of the tail window

**Fix**:
```bash
# Option 1: Use Mode B to download missing months
python scripts/dry_run_v1.py \
    --mode historical \
    --instruments BTCUSDT_PERP ETHUSDT_PERP \
    --start-date 2025-12-01 \
    --end-date 2026-01-15 \
    --output-dir out/dry_run_historical \
    --current-equity 5000.0

# Option 2: Reduce --tail-days in Mode A
python scripts/dry_run_v1.py \
    --mode recent-tail \
    --instruments BTCUSDT_PERP ETHUSDT_PERP \
    --tail-days 7 \  # Reduced from 30
    --output-dir out/dry_run_test \
    --current-equity 5000.0
```

### Issue: Running at 00:02 UTC, get "very early" warning

**Cause**: Running before 00:05 UTC cutover time

**Fix**: Wait until 00:05 UTC or later. API cache may not be available yet.

### Issue: Running at 14:00 UTC, get "late in day" warning

**Cause**: Running after 12:00 UTC (trading on yesterday's close prices)

**Fix**: Run earlier next time (00:30-06:00 UTC recommended). For now, verify market hasn't moved significantly since yesterday's close.

---

## Common Scenarios

### Scenario 1: First-Time Setup

```bash
# 1. Download historical data (one-time)
python scripts/update_data_monthly.py \
    --config config/crypto_perps_baseline_v1.yaml \
    --data-dir data/raw/binance

# 2. Run dry run to validate setup
python scripts/dry_run_v1.py \
    --mode recent-tail \
    --instruments BTCUSDT_PERP ETHUSDT_PERP \
    --tail-days 30 \
    --output-dir out/dry_run_initial \
    --current-equity 5000.0

# 3. If dry run passes, ready for daily operations
```

### Scenario 2: Daily Operations (Routine)

```bash
# Morning workflow (00:30-06:00 UTC)
# 1. Doctor check
python scripts/doctor_live_ops.py \
    --config config/crypto_perps_baseline_v1.yaml \
    --actual-positions live/current_positions.csv \
    --current-equity-file live/current_equity.txt \
    --data-dir data/raw/binance \
    --cadence daily

# 2. If doctor passes, run advisory
python scripts/run_live_advisory.py \
    --config config/crypto_perps_baseline_v1.yaml \
    --actual-positions live/current_positions.csv \
    --current-equity $(cat live/current_equity.txt) \
    --output-dir out/live_$(date +%Y%m%d) \
    --cadence daily \
    --tail-days 3

# 3. Review and execute trades
cat out/live_$(date +%Y%m%d)/trade_plan_*.csv

# 4. After execution, reconcile positions
python scripts/reconcile_positions.py \
    --positions-file live/current_positions.csv \
    --current-equity <new_equity> \
    --config config/crypto_perps_baseline_v1.yaml \
    --fix-mode auto
```

### Scenario 3: Investigating Historical Period

```bash
# Test a specific historical window
python scripts/dry_run_v1.py \
    --mode historical \
    --instruments BTCUSDT_PERP ETHUSDT_PERP SOLUSDT_PERP \
    --start-date 2025-11-01 \
    --end-date 2025-12-31 \
    --output-dir out/investigation_nov_dec \
    --current-equity 10000.0

# Review outputs
cat out/investigation_nov_dec/dry_run_report.txt
cat out/investigation_nov_dec/trade_plan_*.csv
```

### Scenario 4: Testing Code Changes

```bash
# Before deploying code changes, validate with dry run
python scripts/dry_run_v1.py \
    --mode recent-tail \
    --instruments BTCUSDT_PERP ETHUSDT_PERP \
    --tail-days 30 \
    --output-dir out/dry_run_test_$(git rev-parse --short HEAD) \
    --current-equity 5000.0

# If passes, safe to deploy
```

### Scenario 5: Recovering from Operator Error

```bash
# Accidentally edited positions file with wrong values
# 1. Reconcile to find errors
python scripts/reconcile_positions.py \
    --positions-file live/current_positions.csv \
    --current-equity 5237.50 \
    --config config/crypto_perps_baseline_v1.yaml \
    --fix-mode suggest

# 2. If errors, try auto-fix
python scripts/reconcile_positions.py \
    --positions-file live/current_positions.csv \
    --current-equity 5237.50 \
    --config config/crypto_perps_baseline_v1.yaml \
    --fix-mode auto

# 3. If auto-fix doesn't work, restore from git
git checkout live/current_positions.csv

# 4. Re-enter positions manually
```

---

## Best Practices

1. **Run doctor before every daily advisory** - catches issues early
2. **Use dry run after code changes** - validates end-to-end before going live
3. **Reconcile positions after manual edits** - prevents operator errors
4. **Run during safe operating window** (00:30-06:00 UTC) - ensures fresh data
5. **Commit positions to git daily** - provides audit trail and recovery mechanism
6. **Use auto-fix for notional errors** - faster and less error-prone than manual editing
7. **Monitor warnings** - they indicate potential issues even if not critical
8. **Keep equity file current** - stale equity leads to incorrect leverage calculations

---

## File Locations

**Inputs**:
- `live/current_positions.csv` - Actual positions (manually maintained)
- `live/current_equity.txt` - Current account equity (manually maintained)
- `config/crypto_perps_baseline_v1.yaml` - System configuration

**Outputs**:
- `out/live_YYYYMMDD/raw_data_status.json` - Data freshness report
- `out/live_YYYYMMDD/dataset_latest.parquet` - Processed dataset
- `out/live_YYYYMMDD/trade_plan_YYYY-MM-DD.csv` - Trade recommendations
- `out/live_YYYYMMDD/sanity_checks_YYYY-MM-DD.json` - Risk validation
- `out/live_YYYYMMDD/audit_bundle_YYYY-MM-DD.json` - Full provenance

**Backups**:
- `live/current_positions.csv.bak.YYYYMMDD_HHMMSS` - Auto-created by reconcile --fix-mode auto

---

## Support

**Issues**: Report bugs and issues at the repository issue tracker

**Questions**: See `live/README.md` for additional operational details

**Design Spec**: See `docs/crypto_perps_design_spec_agent_ready.md` for system design
