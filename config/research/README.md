# Research Configs

**Purpose**: Experimental configurations for research, backtesting, and candidate evaluation.

## Directory Structure

```
config/research/
├── README.md (this file)
├── candidates_5_baseline.yaml        # 5-instrument baseline (matches prod)
├── candidates_10_tier12.yaml         # 10-instrument pool (tier 1+2)
├── candidates_20_expanded.yaml       # 20-instrument pool (tier 1+2+3)
└── candidates_N_custom.yaml          # Custom research configs
```

## Naming Convention

**Format**: `{purpose}_{variant}.yaml`

- **purpose**: `candidates` (universe expansion research)
- **variant**:
  - `5_baseline`: 5 instruments (production baseline)
  - `10_tier12`: 10 instruments (tier 1+2)
  - `20_expanded`: 20 instruments (tier 1+2+3)
  - `N_custom`: Custom research configurations

## Usage

Research configs are for **backtesting and analysis only**. They do NOT affect:
- Production trading (`config/crypto_perps_baseline_v1.yaml`)
- Live operations (`scripts/run_live_advisory.py`)
- Doctor validation (`scripts/doctor_live_ops.py`)

### Typical Workflow

1. **Build dataset** with candidate pool:
   ```bash
   python scripts/build_example_dataset.py \
     --source real \
     --data-dir data/raw/binance \
     --start-date 2020-01-01 \
     --end-date 2024-10-31 \
     --output-path research/datasets/candidates_20_2024Q4.parquet \
     --allow-jagged \
     --min-coverage 0.80 \
     --min-history-days 365
   ```

2. **Run backtest** with research config:
   ```bash
   python systems/crypto_perps/system.py \
     --config config/research/candidates_20_expanded.yaml \
     --data research/datasets/candidates_20_2024Q4.parquet \
     --outdir research/backtests/candidates_20_2024Q4
   ```

3. **Analyze results** and compare across universes
4. **Shortlist candidates** for potential promotion to prod

## Isolation from Production

Research configs:
- ✅ Use separate dataset directories (`research/datasets/`)
- ✅ Use separate output directories (`research/backtests/`)
- ✅ Do NOT affect prod trading universe
- ✅ Do NOT affect live ops or doctor validation

Production remains on:
- Config: `config/crypto_perps_baseline_v1.yaml`
- Universe: 5 instruments (BTCUSDT, ETHUSDT, BNBUSDT, SOLUSDT, XRPUSDT)

## Reproducibility

All research runs should be reproducible:
- **Dataset snapshot**: Fixed dataset file (`.parquet` + `.manifest.json`)
- **Config snapshot**: Fixed config file (versioned)
- **Seed**: Use fixed random seeds where applicable
- **Environment**: Document Python version, library versions

Example snapshot:
```
research/snapshots/2024Q4_ablation/
├── dataset_candidates_20.parquet
├── dataset_candidates_20.manifest.json
├── candidates_5_baseline.yaml
├── candidates_10_tier12.yaml
├── candidates_20_expanded.yaml
└── README.md (experiment documentation)
```

## Config Parameters

Research configs should match production parameters where possible:
- Same `vol_target_ann`, `gross_leverage_cap`, `idm_cap`
- Same `ewmac_pairs`, `carry_halflife` values
- Same `spread_estimate`, `taker_fee_frac`

Only universe size differs.

## Promotion Process

**Research configs DO NOT auto-promote to production.**

Promotion checklist (manual):
1. Review backtest results (Sharpe, drawdowns, turnover)
2. Check data quality (V1 status report, manifest)
3. Review diversification contribution
4. Test in `env=dev` for 1 week
5. Test in `env=paper` for 2 weeks
6. Update prod config manually (with approval)

See `docs/PROMOTION_CHECKLIST.md` (Phase 4) for full process.
