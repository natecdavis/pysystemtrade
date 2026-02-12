# Crypto Signal Research Summary

## Research Question
Can we improve on the diversified config (0.34 Sharpe) using additional signals while following Carver's anti-overfitting framework?

## Signals Evaluated

### 1. Mean-Reversion Signals ❌ NOT RECOMMENDED

| Signal | Sharpe | Trend Correlation | New Parameters |
|--------|--------|-------------------|----------------|
| Bollinger 10 | -0.68 | -0.44 | 2 |
| Bollinger 20 | -0.63 | -0.60 | 2 |
| Bollinger 40 | -0.88 | -0.72 | 2 |
| RSI 7 | -0.68 | -0.47 | 1 |
| RSI 14 | -0.85 | -0.62 | 1 |
| MR Wings 4 | -0.31 | -0.13 | 0 |
| MR Wings 8 | -0.14 | -0.08 | 0 |

**Verdict**: Despite good negative correlation with trend, ALL mean-reversion signals have negative Sharpe ratios on BTC. The diversification benefit cannot overcome the negative expected return. Crypto trends more than it mean-reverts at these timeframes.

### 2. Faster EWMAC Spans ⚠️ CAUTION

| Signal | BTC Sharpe | ETH | LTC | XRP | XLM | ADA |
|--------|------------|-----|-----|-----|-----|-----|
| EWMAC 2/8 | 0.48 | 0.62 | -0.30 | 0.45 | -0.19 | 0.50 |
| EWMAC 4/16 | 0.67 | 0.98 | 0.03 | 0.30 | -0.18 | 0.80 |
| EWMAC 8/32 | 0.71 | 0.85 | -0.05 | 0.12 | -0.20 | 0.60 |

**Verdict**: EWMAC 4/16 looks promising on BTC/ETH but fails on LTC/XLM. Not robust across instruments. Higher turnover increases cost drag. If adding, use conservative weight (10-15%).

### 3. Carry/Funding Signal ⚠️ FOR PERPS ONLY

| Ticker | Avg Funding (ann.) | Carry Sharpe | Trend Corr |
|--------|-------------------|--------------|------------|
| BTC | -0.0% | -0.05 | -0.39 |
| ETH | 0.0% | -0.37 | -0.26 |
| SOL | 829% | -0.20 | -0.60 |
| LINK | 129% | 0.02 | -0.49 |
| LTC | 246% | 0.15 | -0.37 |

**Portfolio Impact**:
- Trend only: 0.669 Sharpe
- With 25% carry: 0.676 Sharpe (+1%)

**Verdict**: Carry has good negative correlation with trend (-0.3 to -0.6) but near-zero standalone Sharpe. The signal helps marginally, BUT this analysis doesn't capture actual funding payments. **Only relevant if trading perpetual futures, not spot.**

### 4. Relative Value / Cross-Sectional ❌ NOT RECOMMENDED

| Instrument | Sharpe | Trend Corr |
|------------|--------|------------|
| BTC | -0.64 | -0.04 |
| ETH | 0.05 | -0.30 |
| LTC | 0.32 | -0.40 |
| LINK | 0.37 | -0.33 |
| Avg | ~0.10 | -0.35 |

**Verdict**: Low standalone Sharpe. Would need more instruments and longer history for statistical reliability. Not worth the added complexity.

### 5. Regime Filter ⚠️ NOT TESTED

Given that theoretically-motivated signals (carry, MR) showed minimal or negative contribution, regime filters (vol-of-vol, trend strength) were not pursued. Adding parameters without strong theoretical motivation violates Carver's framework.

## Key Insights

### Why Mean-Reversion Fails in Crypto
Yearly Sharpe breakdown (BTC, EWMAC 16/64 vs Bollinger MR 20):

| Year | EWMAC | MR | MR Wins? |
|------|-------|-----|----------|
| 2013 | -1.03 | -3.61 | No |
| 2014 | 0.45 | 0.75 | Yes |
| 2017 | 2.50 | -1.79 | No |
| 2020 | 0.79 | -0.73 | No |
| 2021 | 0.74 | 0.04 | No |
| 2023 | 0.26 | -0.05 | No |
| 2025 | 0.49 | 1.55 | Yes |

MR only wins in 2/13 years. Crypto trends strongly in bull/bear markets, destroying MR.

### Why Carry Has Potential (for Perps)
- Funding rates are highly persistent (autocorr 0.6-0.9)
- SOL/AAVE show 800%+ annualized funding = massive carry premium
- Negative correlation with trend provides diversification
- **BUT**: Need perps to actually capture funding payments

## Final Recommendations

### For Spot Trading (Current Setup)
**Keep the diversified config unchanged.**

```yaml
# crypto_config_diversified.yaml - RECOMMENDED
Rules: 4 EWMAC (8/16/32/64) + 4 Breakout (10/20/40/80)
Instruments: 12
Sharpe: 0.34
```

No additional signals justified:
- MR: Negative expected return
- Fast EWMAC: Not robust across instruments
- Carry: Requires perps
- Relative value: Too weak

### For Perpetual Futures (Future Work)
If/when adding perps, consider:
1. **Carry strategy** (20-30% allocation)
   - Long spot + short perp when funding positive
   - Captures funding rate as income
   - Low correlation with trend

2. **Implementation**:
   ```python
   # Pseudo-code for carry + trend blend
   carry_signal = -smoothed_funding_rate
   trend_signal = ewmac_blend
   combined = 0.75 * trend_signal + 0.25 * carry_signal
   ```

### What NOT to Do
❌ Add mean-reversion rules (negative expected return)
❌ Optimize lookback periods (overfitting)
❌ Select instruments by historical Sharpe (data mining)
❌ Add regime filters without strong theory (parameter inflation)

## Files Created
- `signal_analysis.py` - MR, fast EWMAC, relative value analysis
- `carry_analysis.py` - Funding rate carry signal analysis
- `download_kraken_funding.py` - Kraken funding rate data downloader

## Data Downloaded
- Funding rates for 13 instruments from Kraken (2018-2026 for BTC/ETH, 2022-2026 for others)
- Saved to `data/crypto/funding_rates/`

## Conclusion
The diversified config with EWMAC + breakout represents the best risk-adjusted approach for spot crypto trading following Carver's principles. Additional signals either don't work (MR) or require different instruments (perps for carry). The honest Sharpe of 0.34 reflects realistic expectations for a systematic trend strategy in a highly correlated asset class.
