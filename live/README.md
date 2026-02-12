# Live Portfolio State Files

This directory contains the canonical portfolio state for live trading operations.

---

## Daily Operations Checklist (Quick Reference)

**TIMING**: Daily bars are UTC-based. Run after 00:00 UTC (≈7pm ET winter / ≈8pm ET summer) so `expected_as_of_date = yesterday UTC` is fresh.

**First-time setup**:
1. Download historical data: `python scripts/update_data_monthly.py ...`
2. Validate with dry run: `python scripts/dry_run_v1.py --mode recent-tail ...`

**Daily workflow** (every day after 00:00 UTC):
1. **Doctor check** (MANDATORY): `python scripts/doctor_live_ops.py ...`
2. **Run advisory**: `python scripts/run_live_advisory.py --cadence daily ...`
3. **Review trade plan**: `cat out/live_YYYYMMDD/trade_plan_*.csv`
4. **Execute trades** manually on exchange
5. **Update positions**: Edit `live/current_positions.csv`
6. **Reconcile** (MANDATORY): `python scripts/reconcile_positions.py --fix-mode suggest ...`
7. **Update equity**: `echo "<new_equity>" > live/current_equity.txt`
8. **Commit to git**: `git add live/* && git commit ...`

**Before code changes**:
- Run dry run: `python scripts/dry_run_v1.py --mode recent-tail ...`

See [OPERATIONALIZATION.md](../OPERATIONALIZATION.md) for full details.

---

## Environment Isolation (Dev/Prod Separation)

**Problem**: Running prod nightly while developing can contaminate state.

**Solution**: Use `--env <name>` flag to isolate all stateful paths (accepts any environment name: prod, dev, paper, exp1, etc.).

### Quick Start

```bash
# Initialize environments (one-time)
./scripts/setup_environments.sh

# Run in dev (safe, won't touch prod)
python scripts/run_live_advisory.py --env dev \
    --config config/crypto_perps_baseline_v1.yaml \
    --actual-positions envs/dev/live/current_positions.csv \
    --current-equity 5000.0 \
    --output-dir envs/dev/out/live_$(date +%Y%m%d)

# Run in prod (nightly)
python scripts/run_live_advisory.py --env prod \
    --config config/crypto_perps_baseline_v1.yaml \
    --actual-positions envs/prod/live/current_positions.csv \
    --current-equity $(cat envs/prod/live/current_equity.txt) \
    --output-dir envs/prod/out/live_$(date +%Y%m%d)
```

### Directory Structure

```
envs/
├── prod/           # Production state (nightly runs)
│   ├── live/       # Prod positions, equity
│   ├── data/       # Prod data cache
│   ├── out/        # Prod advisory outputs
│   └── config/     # Copied snapshot (pinned, edit intentionally only)
├── dev/            # Development state (testing)
│   ├── live/       # Dev test positions
│   ├── data/       # Dev data (can share or separate)
│   ├── out/        # Dev outputs
│   └── config/     # Symlink to ../../config (fast iteration)
└── paper/          # Paper trading (example custom env)
    ├── live/
    ├── data/
    ├── out/
    └── config/
```

**See [ENVIRONMENT_SETUP.md](../ENVIRONMENT_SETUP.md) for complete guide.**

---

## Files

### current_positions.csv

Single source of truth for actual current positions (manually maintained after trade execution).

**Schema:**
```csv
instrument,contracts,mark_price_usd,notional_usd,timestamp,notes
BTCUSDT_PERP,0.003,45000.00,135.00,2026-01-28T00:00:00Z,filled_at_45250
```

**Columns:**
- `contracts`: Position size in contracts/base units (from exchange)
- `mark_price_usd`: Mark price used for valuation (specify source: mark vs last)
- `notional_usd`: Computed as `contracts × mark_price_usd` (positive = long, negative = short)
- `timestamp`: When this position/price was recorded (UTC, ISO 8601 format)
- `notes`: Optional execution notes (e.g., "filled_at_45250", "partial_fill")

**CRITICAL Validation Rules:**
- `notional_usd` MUST equal `contracts × mark_price_usd` (realistic tolerance: max($1, 0.1%))
- Sign consistency: `sign(notional) == sign(contracts)` (both positive = long, both negative = short)
- `timestamp` MUST be in ISO 8601 UTC format
- Use consistent price source (mark price recommended for unrealized P&L)
- All instruments in config universe should be present (0.0 if not traded)
- Timestamp staleness: warn if > 24-48 hours for daily cadence (not 7 days)

### current_equity.txt

Current account equity in USD (single line, plain text).

**Example:**
```
5125.50
```

This should reflect actual P&L from exchange, not initial capital.

## Update Workflow

After executing trades manually on exchange:

1. **Update current_positions.csv:**
   - Record actual fills in `contracts` column
   - Get mark price at time of fill from exchange
   - Compute `notional_usd = contracts × mark_price_usd`
   - Record `timestamp` of when position was updated (UTC)
   - Add execution notes in `notes` column if relevant

2. **Update current_equity.txt:**
   - Get actual equity from exchange (includes realized P&L)
   - Write single line with equity value

3. **Re-run advisory:**
   ```bash
   python scripts/run_live_advisory.py \
       --config config/crypto_perps_baseline_v1.yaml \
       --actual-positions live/current_positions.csv \
       --current-equity $(cat live/current_equity.txt) \
       --output-dir out/live_advisory_$(date +%Y%m%d)
   ```

4. **Verify positions:**
   - Check that trade deltas are now close to zero
   - Verify sanity checks pass

5. **Commit to git:**
   ```bash
   git add live/current_positions.csv live/current_equity.txt
   git commit -m "Update positions after trade execution ($(date +%Y-%m-%d))"
   ```

## Validation

On load, the system will validate:
- Sum of `|notional_usd|` does not exceed `gross_leverage_cap × current_equity`
- All instruments in config universe are present (error if missing)
- `notional_usd == contracts × mark_price_usd` (within 1e-6)
- `timestamp` is recent (warn if >7 days old for monthly cadence)

## Example: Filling a Trade

Before trade:
```csv
BTCUSDT_PERP,0.000,0.00,0.00,2026-01-28T00:00:00Z,
```

After buying 0.003 BTC at mark price 45000:
```csv
BTCUSDT_PERP,0.003,45000.00,135.00,2026-01-28T14:30:00Z,filled_market
```

## Notes

- This is a **manual workflow** by design (human-in-loop approval)
- No automatic synchronization with exchange (V0 scope)
- Supports both monthly (V0) and daily (V1) cadence
- Positions stored in notional USD (not base contracts) for consistency with backtest

---

## Daily Cadence V1: Cutover Time Policy

### Expected as_of_date: Yesterday UTC (D-1)

**Default behavior**: `expected_as_of_date = yesterday UTC`

**Rationale**: Binance daily klines close at 23:59:59 UTC, so yesterday's bar is complete at 00:00:00 UTC today.

### Safe Operating Window: 00:30 - 06:00 UTC

- **Minimum wait**: 00:05 UTC (allow 5 min buffer for API cache propagation)
- **Optimal time**: 00:30 - 06:00 UTC (gives time to execute trades during Asian/European session)
- **Late warning**: After 12:00 UTC (trading on yesterday's close prices)

### Warnings

The system will warn if running at suboptimal times:

- **Before 00:05 UTC**: "Running very early - API cache may not be available yet"
- **After 12:00 UTC**: "Running late in day - trading on yesterday's close prices"

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

### Daily Workflow

**Recommended workflow** (run between 00:30-06:00 UTC):

```bash
# 1. Run preflight health check (MANDATORY)
python scripts/doctor_live_ops.py \
    --config config/crypto_perps_baseline_v1.yaml \
    --actual-positions live/current_positions.csv \
    --current-equity-file live/current_equity.txt \
    --data-dir data/raw/binance \
    --cadence daily

# If doctor FAILS (exit code 2): STOP, fix issues, re-run doctor

# 2. Run daily advisory
python scripts/run_live_advisory.py \
    --config config/crypto_perps_baseline_v1.yaml \
    --actual-positions live/current_positions.csv \
    --current-equity $(cat live/current_equity.txt) \
    --output-dir out/live_advisory_$(date +%Y%m%d) \
    --cadence daily \
    --tail-days 3

# 3. Review trade plan
cat out/live_advisory_$(date +%Y%m%d)/trade_plan_*.csv

# 4. Execute trades manually on exchange

# 5. Update positions with actual fills
# Edit live/current_positions.csv

# 6. Run positions reconciliation (MANDATORY after edits)
python scripts/reconcile_positions.py \
    --positions-file live/current_positions.csv \
    --current-equity <new_equity> \
    --config config/crypto_perps_baseline_v1.yaml \
    --fix-mode suggest

# If errors found, use --fix-mode auto or fix manually

# 7. Update equity
echo "<new_equity>" > live/current_equity.txt

# 8. Commit to git
git add live/current_positions.csv live/current_equity.txt
git commit -m "Update positions after daily trade execution $(date +%Y-%m-%d)"
```

### Operationalization Tools

**Doctor CLI** - Preflight health check:
```bash
python scripts/doctor_live_ops.py \
    --config config/crypto_perps_baseline_v1.yaml \
    --actual-positions live/current_positions.csv \
    --current-equity-file live/current_equity.txt \
    --data-dir data/raw/binance \
    --cadence daily
```

Exit codes:
- 0 = PASS (all checks green)
- 1 = PASS_WITH_WARNINGS (non-critical warnings)
- 2 = FAIL (critical errors, do not proceed)

**Positions Reconciliation** - Catch operator errors:
```bash
# Suggest mode (show fixes)
python scripts/reconcile_positions.py \
    --positions-file live/current_positions.csv \
    --current-equity 5237.50 \
    --config config/crypto_perps_baseline_v1.yaml \
    --fix-mode suggest

# Auto-fix mode (apply fixes, creates backup)
python scripts/reconcile_positions.py \
    --positions-file live/current_positions.csv \
    --current-equity 5237.50 \
    --config config/crypto_perps_baseline_v1.yaml \
    --fix-mode auto
```

**Dry Run** - End-to-end validation:
```bash
# Mode A: Recent tail (recommended, fast)
python scripts/dry_run_v1.py \
    --mode recent-tail \
    --instruments BTCUSDT_PERP ETHUSDT_PERP \
    --tail-days 30 \
    --output-dir out/dry_run_$(date +%Y%m%d) \
    --current-equity 5000.0
```

See `OPERATIONALIZATION.md` for comprehensive operationalization guide.
