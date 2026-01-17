# Tail Risk Metrics Implementation

## Overview

This implementation replaces noisy skew-based tail risk analysis with robust Expected Shortfall (CVaR) and drawdown duration metrics for evaluating CARRY allocation in TREND+CARRY portfolios.

## Changes Made

### 1. Updated `portfolio_metrics.py`

**New Functions:**

- `calculate_expected_shortfall(returns, confidence)` - Calculates VaR and Expected Shortfall (CVaR)
  - ES95: Mean of worst 5% of daily returns
  - ES99: Mean of worst 1% of daily returns
  - More robust than VaR alone - captures average severity of tail losses

- `calculate_drawdown_duration(returns)` - Calculates drawdown duration metrics
  - Max DD Duration: Longest period (days) from peak to recovery
  - Avg DD Duration: Average length of all drawdown periods
  - Num Drawdowns: Count of distinct drawdown periods

**Fixed Functions:**

- `calculate_tail_metrics(returns)` - Fixed to use compounded returns instead of rolling sums
  - **Before**: `weekly_rets = returns.rolling(7).sum()` (WRONG)
  - **After**: `weekly_rets = returns.rolling(7).apply(lambda x: (1 + x).prod() - 1)` (CORRECT)
  - This applies to both worst_week and worst_month calculations

**Updated Functions:**

- `calculate_all_metrics()` - Now includes ES95, ES99, VaR95, VaR99, max_dd_duration, avg_dd_duration, num_drawdowns
- `format_metrics_table()` - Updated column definitions to include new metrics:
  - MaxDD Days (integer)
  - ES95 (percentage)
  - ES99 (percentage)

### 2. Created `analyze_tail_risk.py`

New script for focused tail risk comparison across all 9 portfolios.

**Features:**
- Loads cached portfolio returns from `run_portfolio_experiment.py`
- Calculates tail risk metrics for all portfolios
- Generates comprehensive tail risk analysis report
- Saves results to `TAIL_RISK_ANALYSIS.md`

**Output Sections:**
1. Methodology explanation (why ES > skew)
2. Summary table with tail risk metrics
3. Key findings (ES95/ES99 analysis, DD duration, extreme tail events)
4. Recommendations by risk tolerance (conservative, balanced, aggressive)
5. Static vs Dynamic universe comparison

### 3. Updated `run_portfolio_experiment.py`

**Changes:**
- `save_results()` now saves individual portfolio returns to `backtest_cache/` directory
- Each portfolio's returns saved as `{PORTFOLIO_NAME}_returns.csv`
- This enables `analyze_tail_risk.py` to load and analyze results

## Usage

### Step 1: Run Portfolio Experiment (if not already done)

```bash
cd systems/provided/crypto_example
python run_portfolio_experiment.py --start-date 2020-01-01
```

This will:
- Calculate returns for all 9 portfolios (uses cache if available)
- Calculate metrics including new tail risk measures
- Save individual portfolio returns to `backtest_cache/`
- Generate `portfolio_comparison.md` and `portfolio_comparison.csv`

**Expected runtime**: 5-10 minutes (with cached TREND/CARRY sleeves)

### Step 2: Generate Tail Risk Analysis

```bash
python analyze_tail_risk.py
```

This will:
- Load cached portfolio returns
- Calculate tail risk metrics for all portfolios
- Generate focused tail risk report
- Save to `TAIL_RISK_ANALYSIS.md`

**Expected runtime**: < 1 minute

### Step 3: Review Results

Read the generated reports:
- `portfolio_comparison.md` - Full metrics table with new columns
- `TAIL_RISK_ANALYSIS.md` - Focused tail risk analysis with recommendations

## New Metrics Explained

### Expected Shortfall (ES95 / ES99)

**What it is:**
- ES95: Average return of the worst 5% of daily returns
- ES99: Average return of the worst 1% of daily returns

**Why it's better than skew:**
- Directly measures tail loss severity (not just asymmetry)
- Less noisy in high-kurtosis distributions (like crypto)
- Easier to interpret: "On worst 5% of days, expect to lose X%"
- Combines linearly across portfolios (unlike skew)

**Example interpretation:**
- ES95 = -2.5% means: "On the worst 5% of days, average loss is 2.5%"
- More negative = worse tail risk
- Compare across portfolios to assess tail protection

### Drawdown Duration

**What it is:**
- Max DD Duration: Longest time (days) from peak to recovery
- Avg DD Duration: Average recovery time across all drawdowns

**Why it matters:**
- Long drawdowns are psychologically difficult even if shallow
- Indicates how quickly strategy recovers from losses
- Important for live trading capital allocation

**Example interpretation:**
- Max DD Duration = 180 days means: "Longest drawdown took 180 days to recover"
- Lower = better (faster recovery)

### Compounded Tail Returns

**What changed:**
- Worst week/month now use compounded returns instead of sum
- **Sum method**: -1% + -1% + -1% + -1% + -1% = -5% (WRONG)
- **Compounded**: (0.99 × 0.99 × 0.99 × 0.99 × 0.99) - 1 = -4.9% (CORRECT)

**Why it matters:**
- Sum overstates losses for multi-period returns
- Compounding is mathematically correct
- Difference is small for single-digit returns but important for accuracy

## Testing

To test the new metrics work correctly:

```bash
# Test portfolio_metrics.py (should show ES95, ES99, DD duration)
python portfolio_metrics.py

# Run a single portfolio and check new metrics are present
python -c "
from portfolio_metrics import calculate_all_metrics
import pandas as pd
import numpy as np

# Generate dummy returns
dates = pd.date_range('2020-01-01', '2025-12-31', freq='D')
returns = pd.Series(np.random.normal(0.001, 0.013, len(dates)), index=dates)

# Calculate metrics
metrics = calculate_all_metrics(returns, name='Test')

# Check new metrics exist
print('New metrics present:')
print(f\"  ES95: {metrics['es95']*100:.2f}%\")
print(f\"  ES99: {metrics['es99']*100:.2f}%\")
print(f\"  Max DD Duration: {metrics['max_dd_duration']} days\")
print(f\"  Avg DD Duration: {metrics['avg_dd_duration']:.1f} days\")
"
```

## Expected Results

### Hypothesis: CARRY Allocation Trade-offs

Based on CARRY being a positive-skew, low-volatility strategy:

**Low CARRY (20%)**: Best tail protection
- Lower ES95/ES99 (smaller tail losses)
- Shorter drawdown durations
- Lower absolute returns

**Medium CARRY (50%)**: Balanced
- Moderate tail risk
- Best Sharpe ratio
- Medium drawdown duration

**High CARRY (80%)**: Highest risk
- Highest ES95/ES99 (largest tail losses)
- Longest drawdown durations
- Highest absolute returns

### Hypothesis: STATIC vs DYNAMIC

**DYNAMIC expected advantages:**
- Lower volatility → smaller absolute tail losses
- Market-neutral → less exposed to crypto bear markets
- Shorter drawdown durations (faster recovery)

**STATIC expected advantages:**
- Higher absolute returns
- Better ES95/ES99 *relative to volatility*
- Simpler to understand and implement

## Files Modified

1. `portfolio_metrics.py` (~580 lines)
   - Added `calculate_expected_shortfall()` (~35 lines)
   - Added `calculate_drawdown_duration()` (~50 lines)
   - Fixed `calculate_tail_metrics()` to use compounded returns
   - Updated `calculate_all_metrics()` to include new metrics
   - Updated `format_metrics_table()` formatting

2. `run_portfolio_experiment.py` (~430 lines)
   - Updated `save_results()` to save individual portfolio returns

3. `analyze_tail_risk.py` (NEW, ~380 lines)
   - Load cached returns
   - Calculate tail risk metrics
   - Generate focused analysis report

4. `TAIL_RISK_IMPLEMENTATION.md` (NEW, this file)
   - Documentation of changes
   - Usage instructions
   - Methodology explanation

## Next Steps

After reviewing the initial results:

1. **Validate hypotheses** - Do CARRY allocations behave as expected?
2. **Compare to skew-based analysis** - Is ES95/ES99 more stable?
3. **Assess STATIC vs DYNAMIC** - Which provides better tail protection?
4. **Refine recommendations** - Update based on empirical results

## References

- **Expected Shortfall**: Acerbi & Tasche (2002), "On the coherence of expected shortfall"
- **Drawdown Duration**: Magdon-Ismail et al. (2004), "On the maximum drawdown of a Brownian motion"
- **Compounded Returns**: Basic financial mathematics - returns compound, not add
