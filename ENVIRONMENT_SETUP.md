# Environment Setup Guide - Dev/Prod Separation

## Overview

The environment separation feature allows you to run production workflows nightly while continuing development in an isolated environment, preventing state contamination.

**Key benefits:**
- ✓ Run prod nightly without breaking dev workflows
- ✓ Test changes safely in dev without touching prod state
- ✓ Share data directories while isolating positions/equity/outputs
- ✓ Zero impact on existing workflows (backward compatible)

---

## Quick Start

### 1. Initialize environments

```bash
# Create dev + prod environments
./scripts/setup_environments.sh

# Or create custom environments
./scripts/setup_environments.sh paper exp1 exp2
```

### 2. Migrate existing state (if applicable)

```bash
# If you have existing live ops state, copy to prod
cp live/current_positions.csv envs/prod/live/
cp live/current_equity.txt envs/prod/live/

# Copy existing data (optional, can share or re-download)
cp -r data/raw/binance/* envs/prod/data/raw/binance/
```

### 3. Run workflows with environment isolation

```bash
# Prod (nightly cron job)
python scripts/run_live_advisory.py --env prod \
    --config config/crypto_perps_baseline_v1.yaml \
    --actual-positions envs/prod/live/current_positions.csv \
    --current-equity $(cat envs/prod/live/current_equity.txt) \
    --output-dir envs/prod/out/live_$(date +%Y%m%d) \
    --cadence daily

# Dev (safe testing)
python scripts/dry_run_v1.py --env dev \
    --mode recent-tail \
    --instruments BTCUSDT_PERP ETHUSDT_PERP \
    --tail-days 30 \
    --current-equity 5000.0 \
    --output-dir envs/dev/out/dry_run_$(date +%Y%m%d)
```

---

## Directory Structure

```
project_root/
├── envs/
│   ├── prod/           # Production environment
│   │   ├── live/       # Prod positions, equity
│   │   ├── data/       # Prod data cache (or symlink to shared)
│   │   ├── out/        # Prod advisory outputs
│   │   └── config/     # Copied snapshot (pinned, edit intentionally only)
│   ├── dev/            # Development environment
│   │   ├── live/       # Dev test positions
│   │   ├── data/       # Dev data (can share or separate)
│   │   ├── out/        # Dev outputs
│   │   └── config/     # Symlink to ../../config (fast iteration)
│   └── paper/          # Paper trading (example custom env)
│       ├── live/
│       ├── data/
│       ├── out/
│       └── config/
├── live/               # Default (backward compatible)
├── data/
├── out/
└── config/
```

**Config handling:**
- **Dev**: Symlinked to `../../config` for fast iteration
- **Prod**: Copied snapshot (pinned config, edit intentionally only)
- **Custom**: Copied snapshot by default

---

## How It Works

### Path Resolution Priority

The system resolves paths in this order (highest to lowest priority):

1. **Explicit CLI args** (`--data-dir`, `--output-dir`) - ALWAYS wins
2. **`--env-root` flag** (custom path)
3. **`--env <name>` flag** (uses `envs/<name>/` structure)
4. **`LIVE_OPS_ENV_ROOT` env var**
5. **Default paths** (backward compatible - current behavior)

### Examples

```bash
# Backward compatible (no --env flag)
python scripts/run_live_advisory.py \
    --config config/crypto_perps_baseline_v1.yaml \
    --actual-positions live/current_positions.csv \
    --current-equity 5000.0 \
    --output-dir out/live_$(date +%Y%m%d)
# Result: Uses live/, data/, out/ (current behavior)

# Dev environment
python scripts/run_live_advisory.py --env dev \
    --config config/crypto_perps_baseline_v1.yaml \
    --actual-positions envs/dev/live/current_positions.csv \
    --current-equity 5000.0 \
    --output-dir envs/dev/out/live_$(date +%Y%m%d)
# Result: Uses envs/dev/live/, envs/dev/data/, envs/dev/out/

# Override in environment
python scripts/run_live_advisory.py --env dev \
    --data-dir /mnt/shared/data/raw/binance \
    --config config/crypto_perps_baseline_v1.yaml \
    --actual-positions envs/dev/live/current_positions.csv \
    --current-equity 5000.0 \
    --output-dir envs/dev/out/live_$(date +%Y%m%d)
# Result: data from /mnt/shared, live from envs/dev/live/

# Custom env root
python scripts/run_live_advisory.py \
    --env-root /mnt/production \
    --config config/crypto_perps_baseline_v1.yaml \
    --actual-positions /mnt/production/live/current_positions.csv \
    --current-equity 5000.0 \
    --output-dir /mnt/production/out/live_$(date +%Y%m%d)
# Result: All paths relative to /mnt/production

# Environment variable
export LIVE_OPS_ENV_ROOT=/mnt/production
python scripts/run_live_advisory.py \
    --config config/crypto_perps_baseline_v1.yaml \
    --actual-positions /mnt/production/live/current_positions.csv \
    --current-equity 5000.0 \
    --output-dir /mnt/production/out/live_$(date +%Y%m%d)
# Result: All paths relative to /mnt/production
```

---

## Common Workflows

### Nightly Production Run

```bash
#!/bin/bash
# cron: 0 1 * * *  (1am UTC daily)

cd /path/to/pysystemtrade-crypto-perps

python scripts/run_live_advisory.py \
    --env prod \
    --config config/crypto_perps_baseline_v1.yaml \
    --actual-positions envs/prod/live/current_positions.csv \
    --current-equity $(cat envs/prod/live/current_equity.txt) \
    --output-dir envs/prod/out/live_$(date +%Y%m%d) \
    --cadence daily
```

### Development Testing

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

### Paper Trading (Custom Environment)

```bash
# Create paper trading environment
./scripts/setup_environments.sh paper

# Initialize with test data
echo "10000.0" > envs/paper/live/current_equity.txt
echo "instrument,contracts,mark_price_usd,notional_usd,timestamp,notes" > envs/paper/live/current_positions.csv

# Run advisory
python scripts/run_live_advisory.py --env paper \
    --config config/crypto_perps_baseline_v1.yaml \
    --actual-positions envs/paper/live/current_positions.csv \
    --current-equity $(cat envs/paper/live/current_equity.txt) \
    --output-dir envs/paper/out/live_$(date +%Y%m%d) \
    --cadence daily
```

---

## Advanced Scenarios

### Shared Data, Isolated State

If you want to share market data (ZIPs, API cache) between environments while keeping positions/equity/outputs isolated:

```bash
# Create shared data directory
mkdir -p /mnt/shared/data/raw/binance

# Download data once
python scripts/update_data_monthly.py \
    --config config/crypto_perps_baseline_v1.yaml \
    --data-dir /mnt/shared/data/raw/binance

python scripts/update_data_daily.py \
    --config config/crypto_perps_baseline_v1.yaml \
    --data-dir /mnt/shared/data/raw/binance \
    --tail-days 3

# Use shared data in both environments
python scripts/run_live_advisory.py --env prod \
    --data-dir /mnt/shared/data/raw/binance \
    --config config/crypto_perps_baseline_v1.yaml \
    --actual-positions envs/prod/live/current_positions.csv \
    --current-equity $(cat envs/prod/live/current_equity.txt) \
    --output-dir envs/prod/out/live_$(date +%Y%m%d)

python scripts/run_live_advisory.py --env dev \
    --data-dir /mnt/shared/data/raw/binance \
    --config config/crypto_perps_baseline_v1.yaml \
    --actual-positions envs/dev/live/current_positions.csv \
    --current-equity 5000.0 \
    --output-dir envs/dev/out/live_$(date +%Y%m%d)
```

### Environment-Specific Configs

Prod and custom environments get config snapshots. To update prod config intentionally:

```bash
# Edit prod config
vim envs/prod/config/crypto_perps_baseline_v1.yaml

# Test with dry run first
python scripts/dry_run_v1.py --env prod \
    --mode recent-tail \
    --instruments BTCUSDT_PERP ETHUSDT_PERP \
    --tail-days 30 \
    --current-equity $(cat envs/prod/live/current_equity.txt) \
    --output-dir envs/prod/out/dry_run_$(date +%Y%m%d)

# Run prod advisory with updated config
python scripts/run_live_advisory.py --env prod \
    --config envs/prod/config/crypto_perps_baseline_v1.yaml \
    --actual-positions envs/prod/live/current_positions.csv \
    --current-equity $(cat envs/prod/live/current_equity.txt) \
    --output-dir envs/prod/out/live_$(date +%Y%m%d)
```

---

## Troubleshooting

### Issue: "Data status file not found"

**Cause:** Output dir doesn't exist or script can't auto-discover data status.

**Fix:**
```bash
# Ensure output dir exists
mkdir -p envs/prod/out/latest

# Or provide explicit path
python scripts/doctor_live_ops.py --env prod \
    --data-status-path envs/prod/out/latest/raw_data_status.json \
    ...
```

### Issue: "Positions file not found"

**Cause:** Positions file doesn't exist in environment directory.

**Fix:**
```bash
# Create initial positions file
echo "instrument,contracts,mark_price_usd,notional_usd,timestamp,notes" > envs/prod/live/current_positions.csv

# Or copy from default location
cp live/current_positions.csv envs/prod/live/
```

### Issue: "Config symlink broken in dev"

**Cause:** Dev config symlink points to non-existent config directory.

**Fix:**
```bash
# Recreate symlink
rm envs/dev/config
ln -sf ../../config envs/dev/config
```

### Issue: "Prod config changes not taking effect"

**Cause:** Prod config is a snapshot, not a symlink. Edits to `config/` don't affect `envs/prod/config/`.

**Fix:** Edit `envs/prod/config/` directly, or re-copy from `config/`:
```bash
cp -r config/* envs/prod/config/
```

### Issue: "Environment variable conflicts"

**Cause:** `LIVE_OPS_ENV_ROOT` env var is set and overriding --env flag.

**Fix:**
```bash
# Clear env var
unset LIVE_OPS_ENV_ROOT

# Or use --env-root to override
python scripts/run_live_advisory.py \
    --env-root /custom/path \
    ...
```

---

## Migration Guide

### From Single Environment to Multi-Environment

1. **Initialize environments:**
   ```bash
   ./scripts/setup_environments.sh
   ```

2. **Copy existing state to prod:**
   ```bash
   cp live/* envs/prod/live/
   cp -r data/raw/binance/* envs/prod/data/raw/binance/
   ```

3. **Update cron jobs:**
   ```bash
   # Before
   python scripts/run_live_advisory.py \
       --config config/crypto_perps_baseline_v1.yaml \
       --actual-positions live/current_positions.csv \
       --current-equity $(cat live/current_equity.txt) \
       --output-dir out/live_$(date +%Y%m%d)

   # After
   python scripts/run_live_advisory.py --env prod \
       --config config/crypto_perps_baseline_v1.yaml \
       --actual-positions envs/prod/live/current_positions.csv \
       --current-equity $(cat envs/prod/live/current_equity.txt) \
       --output-dir envs/prod/out/live_$(date +%Y%m%d)
   ```

4. **Verify isolation:**
   ```bash
   # Test dev (should not touch prod)
   python scripts/doctor_live_ops.py --env dev \
       --config config/crypto_perps_baseline_v1.yaml \
       --actual-positions envs/dev/live/current_positions.csv \
       --current-equity-file envs/dev/live/current_equity.txt \
       --cadence daily

   # Verify prod state unchanged
   ls -la envs/prod/live/
   ls -la envs/dev/live/
   ```

---

## Verification Checklist

After setup, verify everything works:

```bash
# 1. Check directory structure
ls -la envs/prod/
ls -la envs/dev/

# 2. Test backward compatibility (should work unchanged)
python scripts/doctor_live_ops.py \
    --config config/crypto_perps_baseline_v1.yaml \
    --actual-positions live/current_positions.csv \
    --current-equity-file live/current_equity.txt \
    --cadence daily

# 3. Test dev environment
python scripts/doctor_live_ops.py --env dev \
    --config config/crypto_perps_baseline_v1.yaml \
    --actual-positions envs/dev/live/current_positions.csv \
    --current-equity-file envs/dev/live/current_equity.txt \
    --cadence daily

# 4. Test prod environment
python scripts/doctor_live_ops.py --env prod \
    --config config/crypto_perps_baseline_v1.yaml \
    --actual-positions envs/prod/live/current_positions.csv \
    --current-equity-file envs/prod/live/current_equity.txt \
    --cadence daily

# 5. Test isolation
echo "test_dev" > envs/dev/live/test.txt
echo "test_prod" > envs/prod/live/test.txt
cat envs/dev/live/test.txt   # Should be: test_dev
cat envs/prod/live/test.txt  # Should be: test_prod

# 6. Run unit tests
pytest tests/test_env_paths.py -v
pytest tests/test_env_integration.py -v

# 7. Test override priority
python scripts/doctor_live_ops.py --env dev \
    --data-dir /tmp/custom_data \
    --config config/crypto_perps_baseline_v1.yaml \
    --actual-positions envs/dev/live/current_positions.csv \
    --current-equity-file envs/dev/live/current_equity.txt \
    --cadence daily
# Should use /tmp/custom_data (not envs/dev/data)
```

---

## Best Practices

1. **Never edit prod config by accident:** Prod config is a snapshot. Edit `envs/prod/config/` intentionally only.

2. **Test in dev first:** Always test changes in dev before deploying to prod.

3. **Share data when possible:** Use `--data-dir /mnt/shared/data/raw/binance` to avoid duplicate downloads.

4. **Use explicit paths in cron:** Don't rely on default paths in production cron jobs.

5. **Monitor prod outputs:** Check `envs/prod/out/` regularly for advisory outputs.

6. **Version prod config:** Commit `envs/prod/config/` changes to git when you update prod config intentionally.

7. **Backup prod state:** Regularly backup `envs/prod/live/` (positions, equity).

8. **Test migration thoroughly:** When migrating from single to multi-environment, test extensively in dev first.

---

## Reference

### Supported Scripts

All entry point scripts support `--env` and `--env-root` flags:

- `scripts/run_live_advisory.py`
- `scripts/doctor_live_ops.py`
- `scripts/dry_run_v1.py`
- `scripts/reconcile_positions.py`
- `scripts/update_data_daily.py`
- `scripts/update_data_monthly.py`
- `scripts/generate_trade_plan.py`

### Environment Resolver API

For programmatic usage:

```python
from sysdata.crypto.env_paths import LiveOpsEnvironment

# Create environment
env = LiveOpsEnvironment(env='prod')  # or env_root=Path('/custom')

# Resolve paths
live_dir = env.resolve('live')
out_dir = env.resolve('out')
config_dir = env.resolve('config')
data_root = env.resolve_data_root()
binance_dir = env.resolve_binance_raw_dir()

# Override paths
custom_data = env.resolve_binance_raw_dir(override=Path('/custom/data'))

# Check if environment-aware
if env.is_env_aware:
    print(f"Using environment: {env}")
```

---

## Support

For issues or questions:
1. Check the troubleshooting section above
2. Run verification checklist
3. Review test files: `tests/test_env_paths.py`, `tests/test_env_integration.py`
4. File an issue with reproduction steps
