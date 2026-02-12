# Live Operations V0 (Advisory) - Implementation Summary

## Overview

This implementation provides a **monthly advisory system** for generating trade recommendations using the research_v1 backtest system. The system is designed for human-in-the-loop approval workflow with clear audit trails and risk checks.

**CRITICAL: This is a MONTHLY system (not daily)** due to Binance Vision's publication lag (~2-4 weeks after month end).

## Implementation Status

✅ **COMPLETE** - All components implemented and tested (47 tests passing)

## Components Delivered

### 1. Main Orchestrator: `scripts/run_live_advisory.py`

Single entry point for full monthly advisory workflow.

**What it does:**
1. Updates raw data (monthly batch through M-2)
2. Rebuilds processed dataset with latest data
3. Runs research_v1 backtest for fresh targets
4. Generates trade plan comparing targets to actual positions
5. Optionally generates human-readable report

**Usage:**
```bash
python scripts/run_live_advisory.py \
    --config config/crypto_perps_baseline_v1.yaml \
    --actual-positions live/current_positions.csv \
    --current-equity 5125.50 \
    --output-dir out/live_advisory_$(date +%Y%m%d)
```

**Key Features:**
- Single command for full workflow
- Dry-run mode for testing
- Progress logging
- Error recovery
- Exit codes: 0=success, 1=error, 2=warnings

### 2. Monthly Data Updater: `scripts/update_data_monthly.py`

Updates Binance raw data through last complete month (M-2 policy).

**What it does:**
- Loads config to extract universe
- Determines update range (current month - 2 months)
- Downloads missing months (klines + funding rates)
- Validates ZIP integrity
- Generates data status report

**Usage:**
```bash
python scripts/update_data_monthly.py \
    --config config/crypto_perps_baseline_v1.yaml \
    --data-dir data/raw/binance
```

**Key Features:**
- M-2 lag policy (conservative, accounts for Binance publication lag)
- Fail-fast on missing symbols
- Data gap detection
- 404 handling (expected for recent months)
- Incremental updates

**Module:** `sysdata/crypto/data_status.py`
- Data freshness reporting
- Lag calculation
- Missing month detection
- Validation logic

### 3. Trade Plan Generator: `scripts/generate_trade_plan.py`

Generates actionable trade recommendations with risk checks.

**What it does:**
- Validates as_of_date matches backtest end (ensures fresh targets)
- Loads backtest outputs (positions, diagnostics, metadata)
- Loads actual positions (with contracts, prices, timestamp)
- Calculates deltas (target - actual)
- Applies risk checks (gross leverage, min sizes, banned instruments)
- Estimates costs (marked as ESTIMATED)
- Writes outputs (trade_plan.csv, sanity_checks.json, audit_bundle.json)

**Usage:**
```bash
python scripts/generate_trade_plan.py \
    --backtest-dir out/live_advisory_20260128/backtest_latest \
    --actual-positions live/current_positions.csv \
    --current-equity 5125.50 \
    --as-of-date 2026-01-28 \
    --output-dir out/live_advisory_20260128
```

**Key Features:**
- Strict validation (as_of_date must match backtest end)
- Current equity used for all calculations (not initial capital)
- Prices snapshot in audit trail
- Trade classification (new_position, flatten_to_zero, target_increase, etc.)
- Priority ranking by absolute size
- Fail-fast on errors

**Module:** `systems/crypto_perps/trade_plan.py`
- Core delta calculation logic
- Risk checks (gross leverage, min sizes)
- Cost estimation
- Trade classification
- Audit bundle generation

### 4. Advisory Report Generator: `reports/advisory_report.py`

Human-readable terminal report with prominent warnings.

**What it does:**
- Reads all advisory outputs (trade plan, sanity checks, audit bundle, data status)
- Formats for terminal display
- Displays prominent monthly cadence warnings
- Shows key diagnostics (forecasts, constraints, states)
- Provides action items for next steps

**Usage:**
```bash
python reports/advisory_report.py \
    --advisory-dir out/live_advisory_20260128 \
    --output out/live_advisory_20260128/advisory_report.txt
```

### 5. Portfolio State Files

**Location:** `live/`

**Files:**
- `current_positions.csv` - Canonical portfolio state (manually maintained)
- `current_equity.txt` - Current account equity
- `README.md` - Update workflow documentation

**New Schema for `current_positions.csv`:**
```csv
instrument,contracts,mark_price_usd,notional_usd,timestamp,notes
BTCUSDT_PERP,0.003,45000.00,135.00,2026-01-28T00:00:00Z,filled_at_45250
```

**Validation:**
- `notional_usd == contracts × mark_price_usd` (within 1e-6)
- Timestamp in ISO 8601 UTC format
- All universe instruments present

## Workflow

### Monthly Advisory Cycle

1. **Run Advisory:**
   ```bash
   python scripts/run_live_advisory.py \
       --config config/crypto_perps_baseline_v1.yaml \
       --actual-positions live/current_positions.csv \
       --current-equity $(cat live/current_equity.txt) \
       --output-dir out/live_advisory_$(date +%Y%m%d)
   ```

2. **Review Outputs:**
   - `trade_plan_{date}.csv` - Actionable trade list
   - `sanity_checks_{date}.json` - Risk validation
   - `audit_bundle_{date}.json` - Full provenance
   - `advisory_report.txt` - Human-readable summary

3. **Execute Trades Manually:**
   - Verify live prices on exchange
   - Execute trades on exchange
   - Record actual fills

4. **Update Portfolio State:**
   - Update `live/current_positions.csv` with actual fills
   - Update `live/current_equity.txt` with actual equity
   - Commit to git for audit trail

5. **Re-run Advisory (optional):**
   - Verify deltas are now close to zero
   - Confirm sanity checks pass

### Next Advisory Run

After next month's Binance data is published (~1st week of next month).

## Output Files

### Per Advisory Run

Directory structure: `out/live_advisory_{date}/`

```
out/live_advisory_20260128/
├── raw_data_status.json        # Data freshness report
├── dataset_latest.parquet      # Processed dataset
├── dataset_build_log.txt       # Dataset build log
├── backtest_latest/            # Fresh backtest outputs
│   ├── positions.csv
│   ├── diagnostics.parquet
│   └── metadata.json
├── trade_plan_2026-01-28.csv   # Actionable trade list
├── sanity_checks_2026-01-28.json  # Risk validation
├── audit_bundle_2026-01-28.json   # Full provenance
└── advisory_report.txt         # Human-readable summary
```

### Trade Plan CSV Schema

```csv
instrument,current_contracts,current_notional,target_notional,delta_notional,delta_weight,estimated_cost,priority,reason,state,warnings
BTCUSDT_PERP,0.003,135.00,250.75,115.75,0.0226,0.75,1,target_increase,ACTIVE,
```

### Sanity Checks JSON Schema

```json
{
  "as_of_date": "2026-01-28",
  "current_equity": 5125.50,
  "checks": {
    "gross_leverage": {
      "actual_current": 1.45,
      "after_trades": 1.68,
      "cap": 2.0,
      "status": "pass",
      "note": "Using current_equity, not initial_capital"
    },
    "idm_target_portfolio": {
      "value": 2.35,
      "cap": 2.5,
      "status": "pass",
      "note": "IDM from target portfolio only"
    }
  },
  "overall_status": "pass",
  "warnings": [...]
}
```

### Audit Bundle JSON Schema

```json
{
  "timestamp_utc": "2026-01-28T15:30:00Z",
  "system_version": "research_v1",
  "as_of_date": "2026-01-28",
  "advisory_cadence": "monthly",
  "backtest_metadata": {...},
  "actual_positions": {
    "prices_snapshot": {
      "BTCUSDT_PERP": {
        "mark_price": 45000.00,
        "contracts": 0.003,
        "notional": 135.00
      }
    }
  },
  "forecasts_snapshot": {...},
  "constraints_snapshot": {...},
  "target_portfolio": {...}
}
```

## Testing

### Test Coverage

**47 tests total (all passing)**

1. **Data Status Tests:** `tests/test_data_status.py` (19 tests)
   - Month detection and lag calculation
   - Missing month identification
   - Data validation and completeness checks

2. **Trade Plan Tests:** `tests/test_trade_plan.py` (18 tests)
   - Position loading and validation
   - Delta calculation
   - Risk checks (gross leverage, min sizes)
   - Cost estimation
   - Trade classification and ranking

3. **Integration Tests:** `tests/test_live_advisory_integration.py` (10 tests)
   - Full workflow end-to-end
   - Error handling and edge cases
   - Date validation
   - Gross leverage violations

### Running Tests

```bash
# All tests
python -m pytest tests/test_data_status.py tests/test_trade_plan.py tests/test_live_advisory_integration.py -v

# Specific test file
python -m pytest tests/test_trade_plan.py -v

# Specific test
python -m pytest tests/test_trade_plan.py::TestCheckGrossLeverage::test_within_cap -v
```

## Key Design Decisions

### 1. Monthly Cadence (Not Daily)

**Rationale:** Binance Vision publishes monthly ZIPs ~2-4 weeks after month end. Daily advisory would require real-time data feeds (out of scope for V0).

**Implementation:** M-2 lag policy (conservative - update through current_month - 2).

### 2. Fresh Targets (Not Stale Backtest)

**Rationale:** Ensures targets reflect latest data, not old backtest outputs.

**Implementation:**
- Orchestrator runs full pipeline: data update → dataset rebuild → backtest → trade plan
- Trade plan validates as_of_date matches backtest end (fail if stale)

### 3. Current Equity (Not Initial Capital)

**Rationale:** Gross leverage and position sizing should reflect actual P&L, not starting capital.

**Implementation:**
- All calculations use `current_equity` parameter
- Sanity checks note "using current_equity, not initial_capital"

### 4. Prices Snapshot in Audit Trail

**Rationale:** Critical for reproducibility and verification of position valuations.

**Implementation:**
- Actual positions CSV requires: contracts, mark_price_usd, notional_usd, timestamp
- Audit bundle includes full prices snapshot
- Validation: notional == contracts × price (within 1e-6)

### 5. File-Based State (No Database)

**Rationale:** Simple, git-friendly, deterministic, easy to replay.

**Trade-offs:**
- No concurrency protection (acceptable for single-user)
- Manual updates required (intentional for human-in-loop)

### 6. Fail-Fast Validation

**Rationale:** Prevent silent errors and ensure data integrity.

**Implementation:**
- Date mismatch → FAIL
- Missing symbols → FAIL
- Invalid notional calculation → FAIL
- Extra instruments in actuals → FAIL

## Critical Warnings

### For Users

⚠ **MONTHLY CADENCE ONLY** - Do NOT use for intraday decisions
⚠ **Binance Vision Lag** - Data can be 30-60 days stale
⚠ **Estimated Costs** - Verify live spreads before executing
⚠ **Manual Workflow** - No automatic trade execution (V0 scope)
⚠ **Fresh Targets Required** - Always use latest backtest (not old outputs)

### For Developers

⚠ **Current Equity Usage** - All calculations use current_equity, not initial_capital
⚠ **Date Validation** - as_of_date MUST match backtest end (strict check)
⚠ **Prices Snapshot** - Must be recorded in actual positions CSV
⚠ **IDM Calculation** - IDM from target portfolio only (cannot compute from actual)
⚠ **No Double-Buffering** - Targets already buffered in backtest (don't re-apply)

## Future Enhancements (Out of Scope for V0)

- Real-time data feeds (WebSocket)
- Automatic position synchronization from exchange API
- Trade execution module (order routing, fills tracking)
- Multi-account portfolio aggregation
- Alerting (email, Slack)
- Historical performance tracking (actual vs backtest)
- Streamlit dashboard with interactive charts

## Files Created

### Core Scripts
- `scripts/run_live_advisory.py` (383 lines)
- `scripts/update_data_monthly.py` (249 lines)
- `scripts/generate_trade_plan.py` (153 lines)
- `reports/advisory_report.py` (280 lines)

### Core Modules
- `sysdata/crypto/data_status.py` (301 lines)
- `systems/crypto_perps/trade_plan.py` (624 lines)

### Portfolio State
- `live/current_positions.csv` (template)
- `live/current_equity.txt` (template)
- `live/README.md` (documentation)

### Tests
- `tests/test_data_status.py` (268 lines)
- `tests/test_trade_plan.py` (289 lines)
- `tests/test_live_advisory_integration.py` (378 lines)

### Documentation
- `LIVE_OPS_V0_IMPLEMENTATION.md` (this file)

## Total Lines of Code

- **Core Implementation:** ~1,990 lines
- **Tests:** ~935 lines
- **Documentation:** ~500 lines
- **Total:** ~3,425 lines

## Verification Checklist

✅ All scripts have `--help` with clear usage
✅ Deterministic: same inputs → same outputs
✅ Logs written to stdout/stderr
✅ Exit codes: 0 = success, 1 = error, 2 = warnings
✅ JSON outputs are valid (parseable by `jq`)
✅ CSV outputs have headers and consistent formatting
✅ All unit tests pass (37/37)
✅ Integration test passes (10/10)
✅ No dependency on research_v1 internals (only outputs)

## Quick Start

### Initial Setup

1. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

2. **Initialize portfolio state:**
   ```bash
   # Edit live/current_positions.csv with initial positions (or zeros)
   # Edit live/current_equity.txt with initial capital
   ```

3. **Run first advisory:**
   ```bash
   python scripts/run_live_advisory.py \
       --config config/crypto_perps_baseline_v1.yaml \
       --actual-positions live/current_positions.csv \
       --current-equity $(cat live/current_equity.txt) \
       --output-dir out/live_advisory_$(date +%Y%m%d)
   ```

### After Trade Execution

1. **Update portfolio state:**
   ```bash
   # Edit live/current_positions.csv with actual fills
   # Edit live/current_equity.txt with actual equity from exchange
   ```

2. **Commit changes:**
   ```bash
   git add live/current_positions.csv live/current_equity.txt
   git commit -m "Update positions after trade execution ($(date +%Y-%m-%d))"
   ```

3. **Verify (optional):**
   ```bash
   # Re-run advisory to verify deltas are now close to zero
   python scripts/run_live_advisory.py \
       --config config/crypto_perps_baseline_v1.yaml \
       --actual-positions live/current_positions.csv \
       --current-equity $(cat live/current_equity.txt) \
       --output-dir out/live_advisory_verify_$(date +%Y%m%d)
   ```

## Support

For issues or questions:
- Check `live/README.md` for portfolio state documentation
- Run `python scripts/run_live_advisory.py --help` for usage
- Review test files for examples
- Check existing backtest outputs in `out/` for reference formats

## License

Same as main pysystemtrade-crypto-perps repository.
