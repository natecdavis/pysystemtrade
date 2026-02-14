# Live Advisory Workflow with Dynamic Universe

**Date:** 2026-02-13
**Status:** ✅ Integrated and tested

## Overview

The live advisory workflow now supports **dynamic universe mode** using the parquet-backed data adapter. This enables walk-forward cost-based instrument filtering while maintaining the single canonical data format (parquet panel + manifests).

## Usage

### Basic Command

```bash
python scripts/run_live_advisory.py \
    --config config/crypto_perps_dynamic_universe_v1.yaml \
    --actual-positions live/current_positions.csv \
    --current-equity $(cat live/current_equity.txt) \
    --output-dir out/live_advisory_$(date +%Y%m%d) \
    --use-dynamic-universe  # NEW FLAG
```

### Comparison: Static vs Dynamic Universe

**Static Universe (research_v1 system):**
```bash
# Uses custom crypto_perps system with hardcoded instrument list
python scripts/run_live_advisory.py \
    --config config/crypto_perps_baseline_v1.yaml \
    --actual-positions live/current_positions.csv \
    --current-equity 5000 \
    --output-dir out/live_advisory_static
```

**Dynamic Universe (pysystemtrade framework):**
```bash
# Uses parquet adapter with cost-based filtering
python scripts/run_live_advisory.py \
    --config config/crypto_perps_dynamic_universe_v1.yaml \
    --actual-positions live/current_positions.csv \
    --current-equity 5000 \
    --output-dir out/live_advisory_dynamic \
    --use-dynamic-universe
```

## Workflow Differences

### Static Universe Workflow

1. **Update Data** → Monthly batch from Binance Vision
2. **Build Dataset** → Parquet panel for hardcoded 5 instruments
3. **Run Backtest** → `systems.crypto_perps.system` (custom)
4. **Generate Trade Plan** → Fixed 5-instrument universe
5. **Advisory Report**

### Dynamic Universe Workflow

1. **Update Data** → Monthly batch from Binance Vision (same)
2. **Build Dataset** → Parquet panel for ALL candidate instruments (e.g., 30)
3. **Run Backtest** → `run_dynamic_universe_backtest.py` (pysystemtrade + parquet adapter)
4. **Generate Trade Plan** → Variable instrument universe (filtered by cost thresholds)
5. **Advisory Report**

**Key Difference:** Dataset contains all candidates, but backtest filters based on walk-forward costs.

## Config Requirements

Dynamic universe configs must include:

```yaml
system:
  allow_jagged: true  # Required for dynamic universe

dynamic_universe:
  max_sr_cost_per_trade: 0.01  # 1% SR cost per trade threshold
  max_sr_cost_annual: 0.13     # 13% annual SR cost threshold
  stack_turnover: 15.0         # Expected round-trips/year
  adv_window: 30               # ADV calculation window (days)
  fee_bps: 5                   # One-way fee in bps

trading_rules:
  # pysystemtrade format (not research_v1 format)
  ewmac8_32:
    function: systems.provided.rules.ewmac.ewmac_forecast_with_defaults
    other_args:
      Lfast: 8
      Lslow: 32

forecast_scalars:
  ewmac8_32: 5.3

forecast_weights:
  ewmac8_32: 0.5

forecast_cap: 20.0
```

**Note:** `universe.layer_a_instruments` is still used for dataset building (backward compat), but actual tradable universe is determined by cost filters.

## Output Files

### Standard Outputs (both modes)
- `raw_data_status.json` - Data freshness
- `dataset_latest.parquet` - Processed dataset
- `dataset_build_log.txt` - Build log
- `trade_plan_{date}.csv` - Actionable trades
- `sanity_checks_{date}.json` - Risk validation
- `audit_bundle_{date}.json` - Full provenance

### Dynamic Universe Specific
- `backtest_latest/metadata.json` - Includes dynamic universe stats:
  ```json
  {
    "system_type": "dynamic_universe",
    "dynamic_universe_stats": {
      "min_active": 0,
      "max_active": 30,
      "avg_active": 27.5,
      "median_active": 30
    },
    "dynamic_universe_config": {
      "max_sr_cost_per_trade": 0.01,
      "max_sr_cost_annual": 0.13,
      "stack_turnover": 15.0
    }
  }
  ```

## Trade Plan Differences

### Static Universe
- Always 5 instruments (hardcoded)
- All instruments always in plan
- Positions = 0 if no signal

### Dynamic Universe
- Variable N instruments (0 to max candidates)
- Instruments enter when cost filters pass
- Instruments exit when forecast crosses zero
- Trade plan includes only active instruments

**Example:**
```csv
# Static (5 instruments always)
instrument,current_position,target_position,delta
BTCUSDT_PERP,0.05,0.08,+0.03
ETHUSDT_PERP,0.8,0.0,-0.8
SOLUSDT_PERP,5.0,5.2,+0.2
BNBUSDT_PERP,0.0,0.0,0.0
XRPUSDT_PERP,0.0,0.0,0.0

# Dynamic (28 active instruments this cycle)
instrument,current_position,target_position,delta
BTCUSDT_PERP,0.05,0.08,+0.03
ETHUSDT_PERP,0.8,0.0,-0.8
SOLUSDT_PERP,5.0,5.2,+0.2
AAVEUSDT_PERP,0.0,0.15,+0.15  # NEW entry
ATOMUSDT_PERP,2.0,2.5,+0.5
...  # 23 more instruments
```

## Integration with Existing Scripts

### Doctor Validation
```bash
python scripts/doctor.py \
    --data-dir data/raw/binance \
    --config config/crypto_perps_dynamic_universe_v1.yaml
```

**Behavior:** Doctor uses `universe.layer_a_instruments` for validation (not dynamic universe). This is intentional - doctor validates data quality for ALL candidates, not just currently tradable ones.

### Data Updates
```bash
python scripts/update_data_monthly.py \
    --config config/crypto_perps_dynamic_universe_v1.yaml \
    --data-dir data/raw/binance
```

**Behavior:** Downloads data for all instruments in `universe.layer_a_instruments` (or `data_acquisition.candidate_instruments` if specified). No changes needed.

### Dataset Builder
```bash
python scripts/build_example_dataset.py \
    --source real \
    --data-dir data/raw/binance \
    --instruments BTCUSDT_PERP ETHUSDT_PERP SOLUSDT_PERP ... \
    --output-path data/dataset_latest.parquet \
    --allow-jagged
```

**Behavior:** Builds dataset for all specified instruments. Dynamic filtering happens at backtest time, not dataset build time.

## Testing

### Unit Test: Dynamic Universe Backtest
```bash
python scripts/run_dynamic_universe_backtest.py \
    --config config/crypto_perps_dynamic_universe_v1.yaml \
    --data data/example_crypto_perps_30x6yr_jagged.parquet \
    --outdir out/test_backtest
```

**Expected output:**
- `positions.csv` with variable instrument count over time
- `metadata.json` with dynamic universe stats
- `diagnostics.parquet` with full system state

### Integration Test: Full Advisory Workflow
```bash
bash /path/to/test_advisory_integration.sh
```

**What it tests:**
1. Loads 30-instrument parquet dataset
2. Runs dynamic universe backtest
3. Verifies metadata includes dynamic universe stats
4. Checks positions for all instruments
5. Confirms universe size varies over time

## Troubleshooting

### Issue: "Config error: No instruments found"

**Cause:** Dynamic universe config missing `universe.layer_a_instruments`

**Fix:** Add fallback instrument list to config:
```yaml
universe:
  layer_a_instruments:
    - BTCUSDT_PERP
    - ETHUSDT_PERP
    # ... more instruments
```

### Issue: "AttributeError: get_notional_position_df"

**Cause:** Using wrong backtest script or old version

**Fix:** Ensure using `run_dynamic_universe_backtest.py`, not `systems.crypto_perps.system`

### Issue: Universe size = 0 on all dates

**Cause:** Cost filters too strict (no instruments pass)

**Fix:** Relax thresholds in config:
```yaml
dynamic_universe:
  max_sr_cost_per_trade: 0.02  # Was 0.01
  max_sr_cost_annual: 0.25     # Was 0.13
```

### Issue: Trade plan has different instruments than expected

**Expected behavior!** This is dynamic universe working correctly. Instrument membership changes based on cost filters.

**Check:** Review `metadata.json` for universe stats. If `avg_active` is reasonable (e.g., 20-25 for 30 candidates), system is working correctly.

## Performance Implications

### Dataset Build Time
- **Static (5 instruments):** ~30 seconds
- **Dynamic (30 instruments):** ~3 minutes
- **Scaling:** Linear with instrument count

### Backtest Time
- **Static (research_v1):** ~15 seconds
- **Dynamic (pysystemtrade):** ~2 minutes (first run), ~30 seconds (cached)
- **Note:** pysystemtrade has heavier caching overhead

### Memory Usage
- **Static dataset:** ~200KB parquet
- **Dynamic dataset (30 instruments):** ~1.3MB parquet
- **Backtest memory:** ~500MB (pysystemtrade framework)

## Future Enhancements

### Phase 1: Current Implementation ✅
- Parquet-backed data adapter
- Dynamic universe with cost filtering
- Integration with live advisory workflow

### Phase 2: Config-Based Candidate Pool (Planned)
- Read candidate instruments from `data_acquisition.candidate_instruments`
- Support auto-discovery from registry (`auto_discover: true`)
- Filter dataset build by candidate pool

### Phase 3: Registry Integration (Planned)
- Build dataset for all 541 Binance perps
- Monthly review of discovered instruments
- Automatic candidate pool expansion

### Phase 4: Advanced Filtering (Planned)
- Lifecycle-based entry/exit rules (launch/delist dates)
- Volume-based eligibility (not just ADV from metadata)
- Dynamic forecast weight estimation

## References

- **Parquet Adapter:** `sysdata/crypto/parquet_perps_sim_data.py`
- **Dynamic Portfolio Stage:** `systems/provided/crypto_example/core/dynamic_portfolio.py`
- **Cost Estimation:** `sysdata/crypto/walk_forward_costs.py`
- **Universe Filtering:** `sysdata/crypto/dynamic_universe.py`
- **Backtest Runner:** `scripts/run_dynamic_universe_backtest.py`
- **Advisory Integration:** `scripts/run_live_advisory.py` (lines 221-223, 258-264, 390-403)

## Example: Full Workflow

```bash
# 1. Update data (monthly batch)
python scripts/update_data_monthly.py \
    --config config/crypto_perps_dynamic_universe_v1.yaml \
    --data-dir data/raw/binance \
    --output-report out/raw_data_status.json

# 2. Run advisory with dynamic universe
python scripts/run_live_advisory.py \
    --config config/crypto_perps_dynamic_universe_v1.yaml \
    --actual-positions live/current_positions.csv \
    --current-equity $(cat live/current_equity.txt) \
    --output-dir out/live_advisory_$(date +%Y%m%d) \
    --use-dynamic-universe \
    --cadence monthly

# 3. Review trade plan
cat out/live_advisory_*/trade_plan_*.csv

# 4. Check dynamic universe stats
python -c "
import json
with open('out/live_advisory_*/backtest_latest/metadata.json') as f:
    meta = json.load(f)
stats = meta['dynamic_universe_stats']
print(f'Universe size: {stats[\"avg_active\"]:.1f} avg ({stats[\"min_active\"]}-{stats[\"max_active\"]} range)')
"

# 5. Execute trades manually
# 6. Update live/current_positions.csv with fills
# 7. Update live/current_equity.txt with new equity
```

## Conclusion

Dynamic universe mode enables cost-based instrument filtering while maintaining the single canonical data format. The integration with the live advisory workflow is seamless - just add the `--use-dynamic-universe` flag to switch modes.

**Key Achievement:** Parquet-first architecture avoids CSV proliferation and ensures doctor/advisory/backtest all read from the same dataset source.
