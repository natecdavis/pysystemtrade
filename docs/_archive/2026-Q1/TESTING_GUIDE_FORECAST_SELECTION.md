# Testing Guide: Forecast-Based vs ADV-Based Stage 2 Selection

**Created:** 2026-02-20
**Status:** Implementation complete, ready for testing
**Estimated runtime:** ~20 minutes (10 min per backtest)

## Overview

This guide walks through testing an alternative Stage 2 selection criterion for the dynamic universe model:
- **Current (baseline):** Select top-K instruments by **ADV (liquidity)**
- **Test:** Select top-K instruments by **|forecast| (signal strength)**

**Research Question:** Does selecting instruments with the strongest signals (regardless of liquidity) improve risk-adjusted returns?

## Implementation Summary

### Code Changes

1. **`sysdata/crypto/top_k_selector.py`**
   - Added `compute_forecast_magnitude_metric()` to rank by |forecast|
   - Modified `select_tradable_set()` to accept `selection_criterion` parameter
   - Implemented ranking logic switch between 'adv' and 'forecast_magnitude'

2. **`systems/provided/crypto_example/core/dynamic_portfolio.py`**
   - Added config parameter validation for `selection_criterion`
   - Fetches forecasts when criterion is 'forecast_magnitude'
   - Passes forecasts to selector

3. **`config/crypto_perps_full_rules.yaml`**
   - Added `selection_criterion: 'adv'` (default, baseline behavior)

4. **`config/crypto_perps_full_rules_forecast_select.yaml`**
   - Test config with `selection_criterion: 'forecast_magnitude'`

5. **`scripts/compare_stage2_universes.py`**
   - Diagnostic tool to compare universe composition between runs

## Testing Steps

### Step 1: Run ADV-Based Baseline (verify reproduction)

```bash
python scripts/run_dynamic_universe_backtest.py \
  --config config/crypto_perps_full_rules.yaml \
  --data data/dataset_538registry_6yr_jagged.parquet \
  --outdir out/stage2_comparison/adv_baseline
```

**Expected results** (should match historical baseline):
- Sharpe: ~0.84
- Annual Vol: ~17.9%
- Avg Positions: ~24.9
- Transaction Costs: ~28 bps/yr
- Runtime: ~10 minutes

### Step 2: Run Forecast-Based Test

```bash
python scripts/run_dynamic_universe_backtest.py \
  --config config/crypto_perps_full_rules_forecast_select.yaml \
  --data data/dataset_538registry_6yr_jagged.parquet \
  --outdir out/stage2_comparison/forecast_magnitude
```

**Expected behavior:**
- Different universe composition (instruments with high |forecast| but low ADV may now be selected)
- Potentially higher turnover (forecasts change faster than ADV)
- Unknown Sharpe (that's what we're testing!)
- Runtime: ~10 minutes

### Step 3: Compare Performance Metrics

```bash
# Compare backtest results
echo "=== ADV-BASED BASELINE ==="
tail -n 20 out/stage2_comparison/adv_baseline/backtest.log | grep -E "(Sharpe|CAGR|Vol|Max DD|Avg Pos|Turnover|Costs)"

echo ""
echo "=== FORECAST-BASED TEST ==="
tail -n 20 out/stage2_comparison/forecast_magnitude/backtest.log | grep -E "(Sharpe|CAGR|Vol|Max DD|Avg Pos|Turnover|Costs)"
```

**Key metrics to compare:**

| Metric | ADV-Based (Baseline) | Forecast-Based | Δ | Interpretation |
|--------|---------------------|----------------|---|----------------|
| **Sharpe** | 0.84 | ? | ? | Primary goal: improve risk-adjusted returns |
| **CAGR** | 14.4% | ? | ? | Return component |
| **Annual Vol** | 17.9% | ? | ? | Risk component |
| **Max DD** | -21.9% | ? | ? | Tail risk |
| **Avg Positions** | 24.9 | ? | ? | Portfolio concentration |
| **Turnover** | 15.3x | ? | ? | Forecasts may be more volatile than ADV |
| **Txn Costs** | 28 bps/yr | ? | ? | Higher if selecting illiquid assets |

### Step 4: Compare Universe Composition

```bash
python scripts/compare_stage2_universes.py \
  --adv out/stage2_comparison/adv_baseline/universe_snapshot.json \
  --forecast out/stage2_comparison/forecast_magnitude/universe_snapshot.json \
  --output out/stage2_comparison/universe_comparison.json
```

**Analysis questions:**
1. **Overlap:** What % of instruments are common to both universes each day?
2. **Divergent selections:** Which instruments appear in forecast-based but not ADV-based (and vice versa)?
3. **Turnover:** How often does the universe change in forecast-based vs ADV-based?
4. **Liquidity profile:** Are forecast-based selections less liquid on average?

### Step 5: Analyze Equity Curves

```python
import pandas as pd
import matplotlib.pyplot as plt

# Load equity curves
adv_eq = pd.read_csv('out/stage2_comparison/adv_baseline/equity_curve.csv', index_col=0, parse_dates=True)
forecast_eq = pd.read_csv('out/stage2_comparison/forecast_magnitude/equity_curve.csv', index_col=0, parse_dates=True)

# Plot comparison
fig, axes = plt.subplots(2, 1, figsize=(14, 10))

# Cumulative returns
axes[0].plot(adv_eq.index, adv_eq['cum_return'], label='ADV-based (Baseline)', linewidth=1.5)
axes[0].plot(forecast_eq.index, forecast_eq['cum_return'], label='Forecast-based (Test)', linewidth=1.5)
axes[0].set_title('Cumulative Returns: ADV vs Forecast Selection')
axes[0].set_ylabel('Cumulative Return')
axes[0].legend()
axes[0].grid(True, alpha=0.3)

# Drawdown comparison
axes[1].plot(adv_eq.index, adv_eq['drawdown'], label='ADV-based', linewidth=1.5)
axes[1].plot(forecast_eq.index, forecast_eq['drawdown'], label='Forecast-based', linewidth=1.5)
axes[1].set_title('Drawdown Comparison')
axes[1].set_ylabel('Drawdown')
axes[1].legend()
axes[1].grid(True, alpha=0.3)

plt.tight_layout()
plt.savefig('out/stage2_comparison/equity_comparison.png', dpi=150)
print("✓ Saved to out/stage2_comparison/equity_comparison.png")
```

## Hypotheses

### Hypothesis 1: Higher Sharpe with Forecast-Based Selection
**Rationale:** Selecting instruments with strongest signals (highest |forecast|) concentrates capital in highest-conviction trades.

**Counter-argument:** High |forecast| may correlate with high volatility, potentially degrading risk-adjusted returns.

**Test:** Compare Sharpe ratios. If forecast-based > ADV-based, hypothesis is supported.

### Hypothesis 2: Higher Turnover with Forecast-Based Selection
**Rationale:** Forecasts are more volatile than ADV (which is smoothed over 30 days), leading to more frequent universe changes.

**Counter-argument:** Hysteresis (entry/exit buffers) should dampen turnover regardless of criterion.

**Test:** Compare turnover metrics. Expected: forecast-based turnover > ADV-based turnover.

### Hypothesis 3: Different Instrument Mix
**Rationale:** Illiquid but volatile instruments (e.g., small-caps with high beta) may have high |forecast| but low ADV.

**Risk:** Higher transaction costs if slippage scales with illiquidity.

**Test:** Use `compare_stage2_universes.py` to identify divergent selections. Check if forecast-preferred instruments have lower ADV.

## Success Criteria

**Primary Goal:**
- ✅ Successfully implement and test forecast-based Stage 2 selection
- ✅ Produce clean comparison of ADV vs forecast criterion

**Performance Target (for adoption):**
- Forecast-based Sharpe > ADV-based Sharpe (e.g., 0.84 → 0.90+)
- No catastrophic increase in transaction costs (<50 bps/yr)
- Reasonable turnover (<25x per year)

**If Test Succeeds:**
- Consider making forecast-based selection the default criterion
- Investigate optimal hysteresis parameters for forecast-based selection
- Test hybrid approaches (e.g., weight by both ADV and |forecast|)

**If Test Fails (ADV-based is superior):**
- Document why liquidity matters more than signal strength
- Keep forecast-based as optional for specific strategies
- Consider using |forecast| as a secondary tiebreaker within ADV bands

## Validation Checklist

After running both backtests, verify:

**Code Correctness:**
- [ ] Baseline run reproduces Sharpe 0.84 (confirms no regression)
- [ ] Forecast-based run uses different universe (confirms criterion is applied)
- [ ] No errors or warnings in logs
- [ ] Forecasts are available for all dates (check logs for "Could not get forecast" warnings)

**Performance Comparison:**
- [ ] Sharpe ratio: forecast-based vs ADV-based
- [ ] Volatility: check if forecast-based increases vol
- [ ] Max drawdown: check for tail risk differences
- [ ] Transaction costs: check if forecast-based incurs higher costs

**Universe Composition:**
- [ ] Average universe overlap (% of instruments in common each day)
- [ ] Turnover comparison (rebalances per year)
- [ ] Examples of divergent selections (forecast-ranked but not ADV-ranked)
- [ ] Liquidity profile of divergent selections

**Data Quality:**
- [ ] Verify forecasts are available for all dates
- [ ] Check for NaN/inf in forecast rankings (should be handled gracefully)
- [ ] Ensure hysteresis still works correctly with forecast criterion

## Troubleshooting

### Issue: "Could not get forecast for {instrument}" warnings
**Cause:** Instrument doesn't have sufficient data to compute forecasts.
**Impact:** Instrument gets assigned forecast=0, ranks last.
**Fix:** Expected behavior for new instruments. No action needed.

### Issue: Baseline doesn't reproduce Sharpe 0.84
**Cause:** Wrong config file or data file used.
**Fix:** Ensure you're using `crypto_perps_full_rules.yaml` and `dataset_538registry_6yr_jagged.parquet`.

### Issue: Forecast-based run is very slow
**Cause:** Fetching forecasts for all instruments adds overhead.
**Impact:** Expected ~10% slowdown.
**Mitigation:** Forecasts are cached; subsequent runs should be faster.

### Issue: Universe comparison shows 100% overlap
**Cause:** `selection_criterion` parameter not being read correctly.
**Fix:** Check logs for "criterion=forecast_magnitude" message. Verify config file has correct `selection_criterion` value.

## Next Steps After Testing

1. **Document results** in `.claude/rules/current-work.md`:
   - Performance comparison table
   - Universe composition findings
   - Recommendation (adopt forecast-based or keep ADV-based)

2. **If forecast-based is superior:**
   - Update `crypto_perps_full_rules.yaml` to use `selection_criterion: 'forecast_magnitude'`
   - Run parameter sweep on hysteresis buffers (entry_buffer, exit_buffer)
   - Test on longer backtest period if available

3. **If ADV-based is superior:**
   - Archive forecast-based config as `crypto_perps_full_rules_forecast_select.yaml.bak`
   - Document findings: "Liquidity matters more than signal strength for crypto perps"
   - Consider hybrid: use ADV for ranking, |forecast| as tiebreaker

4. **If results are mixed:**
   - Investigate regime-dependent performance (bull vs bear markets)
   - Test on subsets (high-cap vs low-cap, BTC/ETH vs alts)
   - Consider ensemble: equal weight ADV-based and forecast-based portfolios

---

**Contact:** See `.claude/rules/current-work.md` for session notes and findings.
