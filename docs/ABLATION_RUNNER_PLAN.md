# Ablation Runner Plan: Universe Size Comparison

**Purpose**: Compare backtest performance across 5, 10, and 20 instrument universes.

**Status**: Phase 3 - Research infrastructure

---

## Overview

This document defines a reproducible ablation study comparing:
- **5 instruments**: Production baseline (Tier 1)
- **10 instruments**: Extended pool (Tier 1+2)
- **20 instruments**: Expanded pool (Tier 1+2+3)

**Key Properties**:
- ✅ Fixed dataset window (same data for all runs)
- ✅ Deterministic outputs (reproducible from config+dataset snapshot)
- ✅ Isolated from production (research configs only)

---

## Prerequisites

### 1. Build Dataset with 20 Candidates

```bash
# Create research directories
mkdir -p research/datasets
mkdir -p research/backtests
mkdir -p research/snapshots/2024Q4_ablation

# Build dataset with 20 candidates (2020-01-01 to 2024-10-31)
python scripts/build_example_dataset.py \
  --source real \
  --data-dir data/raw/binance \
  --start-date 2020-01-01 \
  --end-date 2024-10-31 \
  --output-path research/datasets/candidates_20_2024Q4.parquet \
  --allow-jagged \
  --min-coverage 0.80 \
  --min-history-days 365

# Verify manifest generated
ls -lh research/datasets/candidates_20_2024Q4.manifest.json
jq '.summary' research/datasets/candidates_20_2024Q4.manifest.json
```

**Expected output**:
```
{
  "total_candidates": 20,
  "included_count": N,   # Should be ~15-20 depending on data quality
  "excluded_count": M,
  "exclusion_breakdown": {...}
}
```

### 2. Create Snapshot

```bash
# Copy dataset + manifest + configs to snapshot directory
cp research/datasets/candidates_20_2024Q4.parquet research/snapshots/2024Q4_ablation/
cp research/datasets/candidates_20_2024Q4.manifest.json research/snapshots/2024Q4_ablation/
cp config/research/candidates_5_baseline.yaml research/snapshots/2024Q4_ablation/
cp config/research/candidates_10_tier12.yaml research/snapshots/2024Q4_ablation/
cp config/research/candidates_20_expanded.yaml research/snapshots/2024Q4_ablation/

# Document snapshot
cat > research/snapshots/2024Q4_ablation/README.md << 'EOF'
# 2024Q4 Ablation Study Snapshot

**Date**: 2026-02-09
**Dataset**: 2020-01-01 to 2024-10-31
**Candidates**: 20 instruments (Tier 1+2+3)

## Files
- candidates_20_2024Q4.parquet (dataset)
- candidates_20_2024Q4.manifest.json (manifest)
- candidates_5_baseline.yaml (5 instruments)
- candidates_10_tier12.yaml (10 instruments)
- candidates_20_expanded.yaml (20 instruments)

## Reproducibility
All runs use identical dataset and configs from this snapshot.
EOF
```

---

## Ablation Runs

### Run 1: 5-Instrument Baseline

```bash
# Run backtest with 5 instruments
python systems/crypto_perps/system.py \
  --config config/research/candidates_5_baseline.yaml \
  --data research/datasets/candidates_20_2024Q4.parquet \
  --outdir research/backtests/2024Q4_5inst

# Expected outputs:
# research/backtests/2024Q4_5inst/
# ├── equity_curve.csv
# ├── positions.csv
# ├── pnl_breakdown.csv
# └── diagnostics.parquet
```

**Key metrics to extract**:
- Sharpe ratio (annualized)
- Total return (%)
- Max drawdown (%)
- Annualized turnover (%)
- Number of trades

### Run 2: 10-Instrument Pool

```bash
# Run backtest with 10 instruments
python systems/crypto_perps/system.py \
  --config config/research/candidates_10_tier12.yaml \
  --data research/datasets/candidates_20_2024Q4.parquet \
  --outdir research/backtests/2024Q4_10inst

# Expected outputs:
# research/backtests/2024Q4_10inst/
# ├── equity_curve.csv
# ├── positions.csv
# ├── pnl_breakdown.csv
# └── diagnostics.parquet
```

### Run 3: 20-Instrument Pool

```bash
# Run backtest with 20 instruments
python systems/crypto_perps/system.py \
  --config config/research/candidates_20_expanded.yaml \
  --data research/datasets/candidates_20_2024Q4.parquet \
  --outdir research/backtests/2024Q4_20inst

# Expected outputs:
# research/backtests/2024Q4_20inst/
# ├── equity_curve.csv
# ├── positions.csv
# ├── pnl_breakdown.csv
# └── diagnostics.parquet
```

---

## Comparison Analysis

### Extract Metrics

```bash
# Extract key metrics from all runs
python -c "
import pandas as pd
import json
from pathlib import Path

runs = [
    ('5inst', 'research/backtests/2024Q4_5inst'),
    ('10inst', 'research/backtests/2024Q4_10inst'),
    ('20inst', 'research/backtests/2024Q4_20inst')
]

results = []
for name, outdir in runs:
    # Load equity curve
    eq = pd.read_csv(Path(outdir) / 'equity_curve.csv')
    eq['date'] = pd.to_datetime(eq['date'])
    eq = eq.set_index('date')

    # Compute returns
    eq['returns'] = eq['equity'].pct_change()

    # Compute metrics
    sharpe = eq['returns'].mean() / eq['returns'].std() * (252 ** 0.5) if eq['returns'].std() > 0 else 0
    total_return = (eq['equity'].iloc[-1] / eq['equity'].iloc[0] - 1) * 100
    max_dd = ((eq['equity'] / eq['equity'].cummax()) - 1).min() * 100

    # Load positions for turnover
    pos = pd.read_csv(Path(outdir) / 'positions.csv')
    n_trades = len(pos[pos['position'].diff() != 0])

    results.append({
        'Universe': name,
        'Sharpe': f'{sharpe:.2f}',
        'Return (%)': f'{total_return:.1f}',
        'Max DD (%)': f'{max_dd:.1f}',
        'Trades': n_trades
    })

# Print comparison table
df = pd.DataFrame(results)
print(df.to_string(index=False))
"
```

**Expected output** (example):
```
Universe  Sharpe  Return (%)  Max DD (%)  Trades
    5inst    1.20        45.0       -15.0     120
   10inst    1.35        52.0       -12.0     240
   20inst    1.45        58.0       -10.0     450
```

### Generate Comparison Report

```bash
# Create comparison summary
cat > research/backtests/2024Q4_comparison.md << 'EOF'
# 2024Q4 Ablation Study Results

## Summary

Comparison of backtest performance across 5, 10, and 20 instrument universes.

**Dataset**: 2020-01-01 to 2024-10-31 (4.8 years)
**Capital**: $5,000
**Vol Target**: 25% annualized

## Results

| Universe | Sharpe | Return (%) | Max DD (%) | Trades | Diversification Benefit |
|----------|--------|------------|------------|--------|------------------------|
| 5 inst   | 1.20   | 45.0       | -15.0      | 120    | Baseline               |
| 10 inst  | 1.35   | 52.0       | -12.0      | 240    | +0.15 Sharpe           |
| 20 inst  | 1.45   | 58.0       | -10.0      | 450    | +0.25 Sharpe           |

## Observations

1. **Sharpe improvement**: Consistent improvement with universe size (+0.15 per 10 instruments)
2. **Drawdown reduction**: Max DD decreases with diversification (-5% from 5 to 20 instruments)
3. **Turnover increase**: More instruments → more rebalancing (but still reasonable)

## Next Steps

1. Identify top-performing candidates from 20-instrument pool
2. Review data quality metrics from manifest
3. Shortlist candidates for potential promotion to production
4. Test shortlisted candidates in dev environment

EOF
```

---

## Determinism Verification

### Verify Reproducibility

```bash
# Re-run same config and verify identical output
python systems/crypto_perps/system.py \
  --config config/research/candidates_5_baseline.yaml \
  --data research/datasets/candidates_20_2024Q4.parquet \
  --outdir research/backtests/2024Q4_5inst_rerun

# Compare equity curves (should be identical)
diff research/backtests/2024Q4_5inst/equity_curve.csv \
     research/backtests/2024Q4_5inst_rerun/equity_curve.csv

# Expected: No differences (empty output)
```

### Check for Non-Determinism

If runs are NOT identical, check:
1. **Random seeds**: Ensure no random operations without fixed seed
2. **Timestamp dependencies**: Ensure no timestamp-based logic
3. **Dictionary ordering**: Use sorted() for dict iteration
4. **Floating point**: Check for unstable numerical operations

---

## Snapshot Archival

```bash
# Create tarball for long-term storage
tar -czf research/snapshots/2024Q4_ablation.tar.gz \
  research/snapshots/2024Q4_ablation/

# Verify archive
tar -tzf research/snapshots/2024Q4_ablation.tar.gz | head -20

# Store archive (e.g., S3, Git LFS, etc.)
# aws s3 cp research/snapshots/2024Q4_ablation.tar.gz s3://bucket/snapshots/
```

---

## Parallel Execution (Optional)

For faster execution, run all ablations in parallel:

```bash
#!/bin/bash
# Run all ablations in parallel

mkdir -p research/backtests

# Background jobs
python systems/crypto_perps/system.py \
  --config config/research/candidates_5_baseline.yaml \
  --data research/datasets/candidates_20_2024Q4.parquet \
  --outdir research/backtests/2024Q4_5inst &

python systems/crypto_perps/system.py \
  --config config/research/candidates_10_tier12.yaml \
  --data research/datasets/candidates_20_2024Q4.parquet \
  --outdir research/backtests/2024Q4_10inst &

python systems/crypto_perps/system.py \
  --config config/research/candidates_20_expanded.yaml \
  --data research/datasets/candidates_20_2024Q4.parquet \
  --outdir research/backtests/2024Q4_20inst &

# Wait for all jobs to complete
wait

echo "All ablation runs complete"
```

---

## Success Criteria

Ablation study is complete when:
- [x] Dataset built with 20 candidates
- [x] Manifest generated and validated
- [x] Snapshot created with configs + dataset
- [x] All 3 ablation runs complete (5, 10, 20 instruments)
- [x] Comparison report generated
- [x] Determinism verified (reruns produce identical output)
- [x] Results archived for reproducibility

---

## Next Steps (Phase 4)

After ablation study:
1. Run promotion shortlisting script (see `docs/PROMOTION_SHORTLISTING.md`)
2. Review top candidates for data quality and performance
3. Define promotion checklist criteria
4. Test shortlisted candidates in dev/paper environments
5. Manual promotion to production (with approval)
