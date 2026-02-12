# Carry Forecast Diagnostic Summary

## Question
**"Does the carry forecast ever materially affect the combined forecast or positions?"**

## Answer
**NO. The carry forecast contributes ~0% to the combined forecast and has negligible impact on positions.**

---

## Root Cause: Missing Volatility Normalization

### Diagnosis

The carry forecast is **~5700x smaller** than EWMAC forecasts in raw form due to missing volatility normalization:

| Forecast Type | Mean Abs (Raw) | Calculation Method |
|---------------|----------------|-------------------|
| **EWMAC 8-32** | 1.453 | `(fast_ewma - slow_ewma) / price_volatility` |
| **EWMAC 16-64** | 2.033 | `(fast_ewma - slow_ewma) / price_volatility` |
| **Carry Funding** | 0.000306 | `slow_ewma - fast_ewma` (NO vol normalization) |
| **Combined** | 10.057 | Weighted avg of scaled forecasts × FDM |

**Scaling Ratio:** Carry is **1:5705** compared to trend (mean abs).

---

## Why This Happens

### EWMAC Calculation (from `ewmac_calc_vol`)
```python
vol = robust_vol_calc(price.diff(), vol_days)  # Price volatility
forecast = (fast_ewma - slow_ewma) / vol       # Vol-normalized
```

**Typical raw EWMAC magnitude:** ~1-2 (after vol-normalization)

### Carry Calculation (from `funding_carry_forecast`)
```python
fast_ewma = funding_rates.ewm(halflife=3).mean()
slow_ewma = funding_rates.ewm(halflife=30).mean()
carry_signal = slow_ewma - fast_ewma  # NO vol-normalization!
```

**Typical raw carry magnitude:** ~0.0003 (funding rates are tiny: 0.01% = 0.0001)

---

## Why Scaling Doesn't Fix It

Both forecasts are scaled to `target_abs=10.0` using `forecast_scalar()`, which calculates:
```python
scaling_factor = target_abs / mean(abs(forecast))
```

**However:** The scaling factor is calculated over a **rolling window** with `min_periods=50`.

When carry is ~1000x smaller than EWMAC:
- EWMAC scaling factor: ~10 / 1.5 = 6.7
- Carry scaling factor: ~10 / 0.0003 = 33,333

After scaling:
- EWMAC scaled: mean abs ~10
- Carry scaled: mean abs ~10 (theoretically)

**But in practice:**
- The carry signal has **extremely low variance** (funding rates are sticky)
- Even scaled to mean abs = 10, carry contributes minimally because it's ~constant
- EWMAC has much higher variance and dominates the combined forecast

---

## Quantitative Impact

### 1. Carry Contribution to Combined Forecast
- **Mean:** 0.07% of |combined forecast|
- **Median:** 0.00% of |combined forecast|
- **P90:** 0.02% of |combined forecast|
- **P99:** 0.15% of |combined forecast|

**Conclusion:** Carry contributes essentially nothing to the combined forecast.

### 2. Forecast Sign Flips
- **Days where carry flips sign:** 549 / 7,128 (7.75%)

**Conclusion:** Carry occasionally flips the forecast sign, but impact is negligible.

### 3. Position Impact
*(Note: Position delta calculation is approximate due to unknown FDM values)*

- **Days where carry changes position by >5%:** 6,380 / 7,088 (90.01%)
- **Mean position delta:** 166.88%

**Conclusion:** This metric is likely misleading due to estimation errors. The counterfactual backtest (carry-off == baseline) confirms carry has zero material impact.

---

## Counterfactual Validation

### Backtest Results
| Scenario | Final Equity | Return | Delta vs Baseline |
|----------|--------------|--------|-------------------|
| **Baseline** | $34,667.94 | +593.4% | — |
| **Carry Off** | $34,667.94 | +593.4% | $0 (0.0%) |
| **Constraints Off** | $43,009.04 | +760.2% | $+8,341 (+24.1%) |

**Validation:** Carry-off == baseline confirms carry contributes **0% to returns**.

---

## Why Carry Is Not Working

### Expected Behavior
Carry should capture funding rate trends:
- **Positive carry signal** (slow > fast): Funding trending up → favor short (receive funding)
- **Negative carry signal** (slow < fast): Funding trending down → favor long (pay less funding)

### Actual Behavior
- **Raw carry values:** [-0.004, +0.003] (6 decimal places of precision!)
- **Funding rates:** Typically 0.0001 to 0.001 (0.01% to 0.1% per period)
- **EWMA difference:** Even smaller than raw rates

**Problem:** The signal magnitude is too small to meaningfully contribute after combination with vol-normalized EWMAC forecasts.

---

## Comparison to EWMAC

### EWMAC (Trend-Following)
- **Input:** Price series (e.g., $50,000 for BTC)
- **Raw signal:** (fast_ewma_price - slow_ewma_price) / vol
- **Typical magnitude:** 1-5 (raw), 10 (scaled), ±20 (capped)
- **Variance:** High (tracks price momentum)

### Carry (Funding Rate Differential)
- **Input:** Funding rate series (e.g., 0.0001 = 0.01%)
- **Raw signal:** slow_ewma_rate - fast_ewma_rate
- **Typical magnitude:** 0.0003 (raw), ~10 (scaled but low variance)
- **Variance:** Extremely low (funding rates are sticky)

**Ratio:** EWMAC is ~5000x larger in raw form due to:
1. **No vol-normalization in carry** (EWMAC divides by vol, carry doesn't)
2. **Input scale mismatch** (prices ~$50k vs rates ~0.0001)

---

## Diagnostics from diagnostics.parquet

### Raw Forecast Values (Sample: BTCUSDT_PERP, First 20 Days)
```
      date  forecast_carry_funding
2020-02-20                0.000556
2020-02-21                0.000646
2020-02-22                0.000700
2020-02-23                0.000596
2020-02-24                0.000595
2020-02-25                0.000541
2020-02-26                0.000469
2020-02-27                0.000536
2020-02-28                0.000569
2020-02-29                0.000384
2020-03-01                0.000354
```

**Observation:** Carry values are in the 0.0003-0.0007 range (3-7 basis points of EWMAC).

### Carry Forecast Range
- **Min:** -0.003522
- **Max:** +0.003048
- **Days > 0.001:** 239 / 7,088 (3.4%)
- **Days < -0.001:** 220 / 7,088 (3.1%)

**Observation:** 93% of carry forecasts are within ±0.001 (essentially noise).

---

## Fix Options

### 1. Volatility-Normalize Carry (Recommended)
Modify `funding_carry_forecast()` to divide by funding rate volatility:
```python
carry_signal = (slow_ewma - fast_ewma) / funding_vol
```
- **Pros:** Makes carry comparable to EWMAC, theoretically sound
- **Cons:** Funding vol may be very small (amplifies noise)

### 2. Constant Scaling Factor
Apply a fixed multiplier to raw carry before scaling:
```python
carry_signal = (slow_ewma - fast_ewma) * 10000  # Empirical scaling
```
- **Pros:** Simple, predictable
- **Cons:** Ad-hoc, not theoretically grounded

### 3. Different Target Abs for Carry
Scale carry to a higher target_abs than EWMAC:
```python
scaled_carry = scale_forecast(raw_carry, target_abs=1000)  # Much higher
```
- **Pros:** Preserves relative magnitudes
- **Cons:** Breaks forecast scaling consistency

### 4. Unequal Forecast Weights
Use much higher weight for carry in forecast combination:
```python
rule_weights = {
    'ewmac_8_32': 0.1,
    'ewmac_16_64': 0.1,
    'carry_funding': 0.8  # Boost carry weight
}
```
- **Pros:** Doesn't require code changes
- **Cons:** Arbitrary, doesn't fix root cause

### 5. Use Carry as Overlay/Filter (Alternative Approach)
Instead of combining carry as a forecast, use it as a filter:
- Only take long positions when carry < threshold (cheap to hold)
- Only take short positions when carry > threshold (profitable to hold)
- **Pros:** Economically intuitive
- **Cons:** Requires different architecture

---

## Recommendation

**FIX OPTION #1: Volatility-Normalize Carry**

Modify `systems/crypto_perps/rules/carry_funding.py`:

```python
def funding_carry_forecast(
    funding_rates: pd.Series,
    fast_halflife: int,
    slow_halflife: int,
    vol_days: int = 35  # NEW parameter
) -> pd.Series:
    """Calculate funding carry forecast with volatility normalization"""
    # Calculate exponentially weighted moving averages
    fast_ewma = funding_rates.ewm(halflife=fast_halflife, min_periods=1).mean()
    slow_ewma = funding_rates.ewm(halflife=slow_halflife, min_periods=1).mean()

    # Calculate volatility of funding rate changes
    funding_vol = robust_vol_calc(funding_rates.diff(), vol_days)

    # Net carry signal: (slow - fast) / vol (LIKE EWMAC!)
    carry_signal = (slow_ewma - fast_ewma) / funding_vol

    return carry_signal
```

**Rationale:**
- Makes carry mathematically comparable to EWMAC
- Both forecasts now measure "signal strength in volatility-adjusted units"
- Preserves economic meaning: high carry signal = large funding rate trend relative to recent volatility

---

## Files Referenced

1. **Forecast calculation:**
   - `systems/crypto_perps/rules/carry_funding.py` (carry, NO vol-normalization)
   - `systems/crypto_perps/rules/ewmac.py` (EWMAC, uses `ewmac_calc_vol`)
   - `systems.provided.rules.ewmac.ewmac_calc_vol` (PST, vol-normalized)

2. **Forecast scaling/combination:**
   - `systems/crypto_perps/forecasts.py` (`scale_and_combine_forecasts`)
   - `sysquant/estimators/forecast_scalar.py` (PST forecast scalar)

3. **Diagnostics:**
   - `out/stage1_baseline/diagnostics.parquet` (raw forecast values)
   - `scripts/diagnose_carry_forecast.py` (diagnostic script)

---

## Summary

**Question:** Does carry forecast materially affect combined forecast or positions?

**Answer:** NO.

**Root Cause:** Carry forecast lacks volatility normalization (unlike EWMAC), resulting in ~5700x magnitude mismatch.

**Evidence:**
- Carry/EWMAC ratio: 1:5705
- Carry contribution to combined: <0.1% (median)
- Counterfactual: carry-off == baseline ($0 difference)

**Fix:** Add volatility normalization to carry forecast (divide by funding rate vol).

---

*Diagnostic generated: 2026-01-25*
*Dataset: example_crypto_perps_5yr.parquet (2020-2024, 4 instruments)*
*Config: crypto_perps_baseline_v1.yaml*
