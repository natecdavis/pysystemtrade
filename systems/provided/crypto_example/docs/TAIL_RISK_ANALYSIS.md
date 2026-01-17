# TAIL RISK ANALYSIS: TREND + CARRY PORTFOLIOS
================================================================================

## Methodology

This analysis uses robust tail risk metrics instead of skew:

- **Expected Shortfall (ES95)**: Mean of worst 5% of daily returns
- **Expected Shortfall (ES99)**: Mean of worst 1% of daily returns
- **Max DD Duration**: Longest period (days) from peak to recovery
- **Worst Month**: Worst 30-day compounded return (NOT sum)

**Why ES instead of skew?**
- Skew is noisy in crypto (high kurtosis)
- Skew doesn't combine linearly across portfolios
- ES directly measures tail loss severity

## Summary Table

| Case | CAGR | Sharpe | MaxDD | DD Days | ES95 | ES99 | Worst Mo |
| --- | --- | --- | --- | --- | --- | --- | --- |
| A_CARRY_ONLY | 21.9% | 1.50 | -33.6% | 842 | -1.6% | -2.7% | -10.9% |
| B_TREND_STATIC | 33.5% | 0.96 | -32.0% | 631 | -4.8% | -7.7% | -17.3% |
| C_TREND_DYNAMIC | 4.6% | 1.16 | -3.1% | 899 | -0.5% | -0.9% | -2.2% |
| D1_STATIC_80_20 | 31.5% | 1.03 | -28.7% | 910 | -4.0% | -6.5% | -14.7% |
| D2_STATIC_50_50 | 28.0% | 1.23 | -26.3% | 847 | -2.8% | -4.7% | -10.8% |
| D3_STATIC_20_80 | 23.1% | 1.40 | -26.1% | 848 | -1.9% | -3.3% | -9.1% |
| E1_DYNAMIC_80_20 | 7.6% | 1.50 | -7.3% | 850 | -0.6% | -1.1% | -2.5% |
| E2_DYNAMIC_50_50 | 12.0% | 1.42 | -16.3% | 850 | -1.0% | -1.7% | -5.8% |
| E3_DYNAMIC_20_80 | 16.3% | 1.31 | -24.8% | 850 | -1.4% | -2.4% | -9.1% |

## Key Findings

### 1. Expected Shortfall Analysis (ES95)

- **CARRY Only**: -1.62% (baseline tail risk)
- **TREND Static**: -4.83%
- **TREND Dynamic**: -0.49%

**Static Combinations (TREND/CARRY):**
- 80/20: -3.99%
- 50/50: -2.83%
- 20/80: -1.95%

**Dynamic Combinations (TREND/CARRY):**
- 80/20: -0.60%
- 50/50: -0.98%
- 20/80: -1.42%

**Best tail protection**: B_TREND_STATIC with ES95 = -4.83%
**Worst tail protection**: C_TREND_DYNAMIC with ES95 = -0.49%

### 2. Drawdown Duration Analysis

- **CARRY Only**: 842 days
- **TREND Static**: 631 days
- **TREND Dynamic**: 899 days

**Static Combinations:**
- 80/20: 910 days
- 50/50: 847 days
- 20/80: 848 days

**Dynamic Combinations:**
- 80/20: 850 days
- 50/50: 850 days
- 20/80: 850 days

**Fastest recovery**: B_TREND_STATIC with 631 days
**Slowest recovery**: D1_STATIC_80_20 with 910 days

### 3. Extreme Tail Events (ES99)

- **CARRY Only**: -2.71% (worst 1% of days)

**Static vs Dynamic comparison at 50/50 allocation:**
- Static 50/50: -4.73%
- Dynamic 50/50: -1.67%
- Dynamic is -64.8% better in extreme tail

## Recommendations Based on Tail Risk

### Portfolio Selection by Risk Tolerance

**Conservative (minimize tail losses):**
- Choose: B_TREND_STATIC
- ES95: -4.83%, Sharpe: 0.96, MaxDD Duration: 631 days

**Balanced (optimize Sharpe with moderate tail risk):**
- Choose: A_CARRY_ONLY
- ES95: -1.62%, Sharpe: 1.50, MaxDD Duration: 842 days

**Aggressive (maximize Sharpe, accept tail risk):**
- Choose: E1_DYNAMIC_80_20
- ES95: -0.60%, Sharpe: 1.50, MaxDD Duration: 850 days

### Static vs Dynamic Universe

**Average ES95 (across 80/20, 50/50, 20/80 allocations):**
- Static: -2.92%
- Dynamic: -1.00%
- **Dynamic provides -65.8% better tail protection**

**Average Max DD Duration:**
- Static: 868 days
- Dynamic: 850 days
- **Dynamic recovers 2.1% faster**

## Summary

**Key Takeaways:**

1. **Expected Shortfall (ES95/ES99) is a more robust tail risk measure than skew**
   - Directly measures average severity of tail losses
   - Less noisy than skew in high-kurtosis crypto returns

2. **CARRY allocation creates tail risk trade-offs:**
   - Higher CARRY allocation (80%) → better tail protection
   - Lower CARRY allocation (20%) → worse tail protection

3. **Dynamic universe vs Static:**
   - Dynamic provides better tail protection (lower ES95/ES99)
   - Dynamic recovers from drawdowns faster

---
*Analysis generated using 9 portfolios*