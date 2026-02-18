# Current Work Context

## Last Session Summary (2026-02-17)

Implemented **Full Carver-Style 45-Rule Trading Stack**:
- Created `systems/crypto_perps/rules/rule_library.py` — 10 new rule functions
  (normmom, assettrend, btc_lead_lag, funding_carry, relcarry, funding_mr,
   streversal, return_skew, mrinasset, illiquidity)
- Extended `sysdata/crypto/parquet_perps_sim_data.py` with 6 cross-sectional
  data methods: get_asset_class_index_price, get_cross_sectional_median_funding,
  get_btc_price, get_adv_notional, get_normalised_price_this_instrument,
  get_normalised_price_for_asset_class
- Created `config/crypto_perps_full_rules.yaml` — 45-rule config with
  Divergent 70% / Conv-A 22.5% / Conv-B 7.5% budget (exact, sum=1.0)
- Smoke tested: all 14 rule families produce valid forecasts on 15×4yr dataset

## Previous Session Summary (2026-01-14)

Implemented **Walk-Forward Dynamic Instrument Universe** system:
- Created `sysdata/crypto/walk_forward_costs.py` - ADV$-based spread estimation
- Created `sysdata/crypto/dynamic_universe.py` - Eligibility filtering with SR cost thresholds
- Created `systems/provided/crypto_example/analyze_universe.py` - Diagnostic script
- Updated `sysdata/crypto/spot_sim_data.py` with dynamic universe support
- Updated `sysdata/crypto/csv_spot_data.py` with volume data access

## Active Task

Full 45-rule backtest integration complete. Next: run full backtest.

## Next Steps

1. **Run full backtest** with `crypto_perps_full_rules.yaml` on the 30x6yr dataset
   ```bash
   python scripts/run_dynamic_universe_backtest.py \
     --config config/crypto_perps_full_rules.yaml \
     --data data/example_crypto_perps_30x6yr_jagged.parquet \
     --outdir out/full_rules_backtest
   ```
2. **Per-instrument weight overrides** for BTC/ETH (no relmomentum/relcarry/
   btc_lead_lag for BTC; mrinasset for BTC+ETH). Currently all instruments use
   the "default" flat weights. Requires either:
   - A custom `ForecastCombine` subclass that reads a `default` key in nested weights, or
   - Generating explicit weight dicts for every top-30 instrument in the YAML
3. Address volume data quality issues

## Key Files Modified This Session

### New Files
- `systems/crypto_perps/rules/rule_library.py` — 10 rule functions
- `config/crypto_perps_full_rules.yaml` — 45-rule config

### Modified Files
- `sysdata/crypto/parquet_perps_sim_data.py` — +6 cross-sectional data methods

## Known Issues

- **Per-instrument weights**: BTC gets relmomentum/relcarry/btc_lead_lag with
  non-zero weights in current implementation (weights are flat for all instruments).
  The plan's per-instrument weight design requires custom code — tracked as TODO.
- **FundingMR low coverage**: Only 92 non-NaN values on 15x4yr dataset for BTC
  (extreme funding episodes are rare). This is expected behaviour, not a bug.
- **Volume data quality**: Some instruments (WBTC, WETH) show unrealistic ADV values

## Useful Commands

```bash
# Run full 45-rule backtest
python scripts/run_dynamic_universe_backtest.py \
  --config config/crypto_perps_full_rules.yaml \
  --data data/example_crypto_perps_30x6yr_jagged.parquet \
  --outdir out/full_rules_backtest

# Smoke test (quick, 15 instruments)
python scripts/run_dynamic_universe_backtest.py \
  --config config/crypto_perps_full_rules.yaml \
  --data data/example_crypto_perps_15x4yr.parquet \
  --outdir out/smoke_full_rules \
  --static-universe

# Previous backtest
python scripts/run_dynamic_universe_backtest.py \
  --config config/crypto_perps_dynamic_universe_top30.yaml \
  --data data/dataset_538registry_6yr_jagged.parquet \
  --outdir out/backtest_top30
```
