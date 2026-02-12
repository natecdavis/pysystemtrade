# Portfolio Comparison Report

**Generated:** 2026-01-17 11:54:06
**Analysis Period:** 2020-01-01 to 2026-01-06

## Executive Summary

### Best Performers

- **Highest Sharpe:** TREND Dynamic Only (1.84)
- **Highest CAGR:** TREND Static Only (137.3%)
- **Best Calmar:** TREND Static Only (2.73)
- **Smallest Drawdown:** Dynamic 80/20 (-7.3%)

### Key Findings

1. **CARRY Strategy:**
   - Sharpe: 1.50, CAGR: 21.9%, Vol: 13.9%
   - Skew: -0.33 (slightly negative - tail risk present)

2. **TREND STATIC vs DYNAMIC:**
   - STATIC: Sharpe 1.84, CAGR 137.3%, Vol 54.8%
   - DYNAMIC: Sharpe 1.84, CAGR 77.8%, Vol 34.7%
   - DYNAMIC is market-neutral (low vol, low beta) vs STATIC (directional)

3. **Combined Portfolios:**
   - 80/20 allocations balance returns with skew management
   - 50/50 allocations provide balanced exposure
   - 20/80 allocations test high CARRY exposure (skew risk)


## Master Comparison Table

| Case | CAGR | Vol | Sharpe | MaxDD | Calmar | Skew | Worst Mo | Crisis Ret | Corr BTC | Beta BTC |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| CARRY Only | 21.9% | 13.9% | 1.50 | -33.6% | 0.65 | -0.33 | -0.11 | -29.6% | 0.77 | 0.17 |
| TREND Static Only | 137.3% | 54.8% | 1.84 | -50.4% | 2.73 | 6.26 | -0.38 | 5.9% | 0.18 | 0.12 |
| TREND Dynamic Only | 77.8% | 34.7% | 1.84 | -50.4% | 1.55 | -0.51 | -0.30 | 1.0% | 0.23 | 0.05 |
| Static 80/20 | 31.5% | 31.2% | 1.03 | -28.7% | 1.10 | -0.11 | -0.15 | -0.0% | 0.28 | 0.13 |
| Static 50/50 | 28.0% | 22.2% | 1.23 | -26.3% | 1.07 | -0.20 | -0.11 | -9.3% | 0.44 | 0.14 |
| Static 20/80 | 23.1% | 15.8% | 1.40 | -26.1% | 0.89 | -0.34 | -0.09 | -18.7% | 0.68 | 0.15 |
| Dynamic 80/20 | 7.6% | 4.9% | 1.50 | -7.3% | 1.03 | -0.33 | -0.03 | -4.6% | 0.60 | 0.04 |
| Dynamic 50/50 | 12.0% | 8.2% | 1.42 | -16.3% | 0.73 | -0.45 | -0.06 | -12.6% | 0.76 | 0.09 |
| Dynamic 20/80 | 16.3% | 12.1% | 1.31 | -24.8% | 0.66 | -0.47 | -0.09 | -20.2% | 0.78 | 0.13 |

## Marginal Contribution Analysis

**Baseline:** CARRY Only
- CAGR: 21.9%
- Sharpe: 1.50
- Max DD: -33.6%

**Question:** What does adding TREND (static vs dynamic) provide?

| Strategy | Δ Sharpe | Δ CAGR | Δ MaxDD | Δ Calmar |
|----------|----------|---------|---------|----------|
| STATIC 80/20         | -0.47 | +9.5% | +4.9% | +0.44 |
| STATIC 50/50         | -0.27 | +6.1% | +7.3% | +0.41 |
| STATIC 20/80         | -0.10 | +1.2% | +7.5% | +0.23 |
| DYNAMIC 80/20        | +0.00 | -14.4% | +26.2% | +0.38 |
| DYNAMIC 50/50        | -0.08 | -10.0% | +17.3% | +0.08 |
| DYNAMIC 20/80        | -0.19 | -5.6% | +8.8% | +0.01 |

**Interpretation:**
- Δ Sharpe: Positive = improvement in risk-adjusted returns
- Δ CAGR: Positive = higher absolute returns
- Δ MaxDD: Negative = smaller drawdown (better)
- Δ Calmar: Positive = better return/drawdown ratio

## Diversification Analysis

### Correlation to CARRY

- **TREND STATIC:** +0.281
- **TREND DYNAMIC:** +0.282
- **Improvement:** -0.000 (lower correlation = better diversification)

### Beta to BTC (Market Neutrality)

- **CARRY:** +0.166 (correlation: +0.766)
- **TREND STATIC:** +0.119 (correlation: +0.183)
- **TREND DYNAMIC:** +0.053 (correlation: +0.225)

**Interpretation:**
- Beta ≈ 0: Market-neutral (uncorrelated with BTC)
- Beta > 0.5: Directional exposure to BTC
- TREND DYNAMIC's low beta suggests market-neutral profile

## Recommendations

### For Different Objectives:

1. **Maximum Sharpe Ratio:**
   - Choose portfolio with highest Sharpe from table above
   - Balance risk-adjusted returns

2. **Maximum Absolute Returns (CAGR):**
   - TREND STATIC provides higher returns but higher volatility
   - Accept higher drawdowns for higher CAGR

3. **Risk Management (Low Drawdown):**
   - TREND DYNAMIC provides lower volatility and smaller drawdowns
   - Market-neutral profile reduces correlation to BTC crashes

4. **Diversification Benefit:**
   - TREND DYNAMIC has lower correlation to CARRY
   - Better diversification for multi-strategy portfolios

5. **Small Capital ($10k):**
   - CARRY and TREND STATIC more capital-efficient
   - TREND DYNAMIC may require reducing vol target or more capital
