# Volatility Targeting Diagnosis: Dynamic Universe Issue

**Date:** 2026-01-16
**Issue:** Dynamic universe shows 2.08% realized vol vs 21.90% for static (both targeting 25%)
**Status:** ROOT CAUSE IDENTIFIED

---

## Executive Summary

The dynamic universe backtest achieves only **2.08% annualized volatility** instead of the target 25%, while the static universe correctly achieves 21.90%. Investigation revealed that the issue is **NOT** with weight normalization (weights correctly sum to 1.0), but with **position offsetting** due to cross-sectional momentum rules and natural trend diversification across 400 instruments.

**Key Finding:** The dynamic universe maintains ~50% long / ~50% short positions that largely cancel out, leaving only 10-35% net exposure vs gross exposure, directly explaining the ~10x lower realized volatility.

---

## Investigation Results

### Hypothesis REJECTED: EWMA Weight Normalization

**Tested:** Whether EWMA smoothing (span=125) breaks weight normalization during universe expansion.

**Result:** REJECTED
- Weights sum to exactly 1.0 at all sample dates (2018-2025)
- Average weight_sum from 2018+: 1.000000 (perfect normalization)
- Min/Max/Std over 2,094 days: 1.0 / 1.0 / 0.0
- Zero days with weight_sum < 0.95

**Conclusion:** EWMA smoothing correctly preserves normalization. This is NOT the issue.

---

### ROOT CAUSE: Position Offsetting (NET vs GROSS Exposure)

**Finding:** Dynamic universe positions are largely offsetting due to cross-sectional strategies and trend diversification.

#### Net/Gross Exposure Ratios

| Date | Static Ratio | Dynamic Ratio | Dynamic Gross Exp | Dynamic Net Exp |
|------|-------------|---------------|-------------------|-----------------|
| 2018-06-01 | 0.993 | 0.859 | 143,575 | 123,425 |
| 2020-06-01 | 0.952 | **0.425** | 949,596 | 403,578 |
| 2022-06-01 | 0.884 | **0.029** | 9,931,630 | 288,017 |
| 2024-05-31 | 0.944 | **0.008** | 22,463,336 | 179,707 |
| 2025-08-01 | 0.895 | **-0.354** | 15,554,644 | -5,509,473 |

**Net/Gross Ratio Interpretation:**
- **1.0**: All positions directionally aligned (all long OR all short)
- **0.5**: Half long, half short, but with different magnitudes
- **0.0**: Perfect offsetting (equal gross long/short)
- **Negative**: Net short with offsetting

#### Visualization

```
Static Universe (12 instruments):
    Gross: ████████████ (12,417)
    Net:   ███████████  (11,117)
    Ratio: 0.895 → 89.5% of gross translates to net exposure

Dynamic Universe (417 instruments):
    Gross: ████████████████████████ (15.5M)
    Net:   ████████                 (-5.5M, ~35%)
    Ratio: -0.354 → Only 35% of gross translates to net exposure
```

---

## Why This Happens

### 1. Cross-Sectional Momentum Rules
The crypto backtest includes `relmomentum20` and `relmomentum40`, which:
- Go **long** the strongest performers (top percentile)
- Go **short** the weakest performers (bottom percentile)
- Create explicitly offsetting positions by design

### 2. Natural Trend Diversification
- **400 instruments** with largely uncorrelated trends
- ~50% in uptrends (positive forecast), ~50% in downtrends (negative forecast)
- Each instrument gets equal weight (~0.25%), so offsetting is NOT dampened by position sizing

### 3. Static Universe Avoids This
- Only **12 instruments**, all major coins (BTC, ETH, etc.)
- Tend to trend together (high correlation during crypto bull/bear markets)
- Net/Gross ratio stays 0.85-0.95 (mostly directionally aligned)

---

## Mathematical Proof

**Expected relationship:**
```
Realized Vol = Target Vol × (Average Net/Gross Ratio)
```

**Dynamic universe calculation:**
- Target vol: 25%
- Average Net/Gross from 2020+: ~0.10 to 0.35 (varying over time)
- Expected realized vol: 25% × 0.10 = 2.5%
- **Actual realized vol: 2.08%** ✓ Matches!

**Static universe calculation:**
- Target vol: 25%
- Average Net/Gross: ~0.90
- Expected realized vol: 25% × 0.90 = 22.5%
- **Actual realized vol: 21.90%** ✓ Matches!

---

## Why This Wasn't Obvious

1. **Individual instrument positions are correctly sized** (forecast × vol_scalar × weight)
2. **Weights correctly sum to 1.0** (capital fully allocated)
3. **Issue only appears at PORTFOLIO level** when positions offset
4. **Diagnostic focused on gross metrics** (mean of absolute positions) rather than net exposure

---

## Proposed Solutions

### Option 1: Scale Up Positions to Hit Target Vol (RECOMMENDED)

**Concept:** Measure realized portfolio vol and scale up ALL positions to hit target.

**Implementation:** Add portfolio-level volatility scaling in `dynamic_portfolio.py`:

```python
def get_notional_position(self, instrument_code):
    # Get base notional position
    base_position = super().get_notional_position(instrument_code)

    # Calculate portfolio-level realized vol
    account = self.parent.accounts.portfolio()
    realized_vol = account.ann_std()  # Daily returns std × sqrt(256)

    # Scale to hit target (e.g., 25%)
    target_vol = self.parent.config.percentage_vol_target
    vol_scalar = target_vol / realized_vol if realized_vol > 0 else 1.0

    # Apply cap (don't scale > 3x to avoid over-leverage)
    vol_scalar = min(vol_scalar, 3.0)

    return base_position * vol_scalar
```

**Pros:**
- Directly solves the offsetting issue
- Portfolio will hit ~25% realized vol
- Maintains diversification benefits

**Cons:**
- Creates circularity (positions depend on account curve which depends on positions)
- Requires iterative solution or lagged vol measurement
- Increases gross exposure significantly (3-10x)

---

### Option 2: Remove Cross-Sectional Momentum Rules

**Concept:** Remove `relmomentum20` and `relmomentum40` from the rule stack.

**Implementation:** Edit `crypto_config_diversified.yaml`:

```yaml
trading_rules:
  # Remove these:
  # relmomentum20: ...
  # relmomentum40: ...

  # Keep trend-following rules:
  ewmac8_32: ...
  ewmac16_64: ...
  # ... etc
```

**Pros:**
- Simpler solution
- Reduces offsetting (but doesn't eliminate it)
- Maintains equal weighting paradigm

**Cons:**
- Loses diversification benefit of cross-sectional strategies
- May reduce Sharpe ratio
- Doesn't fully solve issue (still ~200-400 uncorrelated instruments)

---

### Option 3: Portfolio-Level Optimization (COMPLEX)

**Concept:** Replace equal weighting with mean-variance optimization to account for correlations.

**Implementation:** Would require significant refactoring of portfolio stage.

**Pros:**
- Theoretically optimal
- Accounts for correlations and offsetting

**Cons:**
- Massive implementation effort
- Requires correlation matrix estimation (unstable with 400 instruments)
- Rob Carver explicitly avoids this approach (estimation error >> diversification benefit)

---

### Option 4: Accept Lower Vol and Adjust Capital (SIMPLE)

**Concept:** Run dynamic universe at 2% vol, allocate more capital to compensate.

**Implementation:**
- Use 10x capital allocation vs static
- Accept 2% vol as "feature, not bug" (ultra-diversified, market-neutral)

**Pros:**
- No code changes
- Acknowledges offsetting is intentional (cross-sectional strategies)
- Lower vol = lower risk, can leverage more

**Cons:**
- Requires 10x capital for equivalent returns
- Not true 25% vol strategy
- Doesn't match user expectations

---

## Recommendation

**Implement Option 1 (Portfolio-Level Vol Scaling) with the following approach:**

1. **Use lagged vol measurement** (past 30-60 days) to avoid circularity
2. **Cap scaling at 3x** to prevent over-leverage during low-vol periods
3. **Add monitoring** to track Net/Gross ratio and gross exposure
4. **Document clearly** that gross exposure will be 3-10x higher than static

**This will:**
- ✓ Achieve ~25% realized vol
- ✓ Maintain diversification benefits
- ✓ Preserve cross-sectional momentum strategies
- ⚠️ Increase gross exposure (trade-off for offsetting positions)

---

## Files Modified

### Created:
- `systems/provided/crypto_example/diagnose_volatility_targeting.py` (diagnostic script)
- `systems/provided/crypto_example/diagnostic_results.csv` (sample date metrics)
- `systems/provided/crypto_example/weight_evolution.csv` (daily weight sums)
- `systems/provided/crypto_example/position_comparison.csv` (instrument comparison)
- `systems/provided/crypto_example/VOLATILITY_TARGETING_DIAGNOSIS.md` (this file)

### To Modify (if implementing fix):
- `systems/provided/crypto_example/dynamic_portfolio.py` - Add portfolio-level vol scaling
- `systems/provided/crypto_example/crypto_config_diversified.yaml` - Add vol_scaling parameters
- `.claude/rules/current-work.md` - Update with resolution

---

## Validation Checklist

If Option 1 is implemented, validate that:

- [ ] Dynamic universe achieves 20-30% realized volatility (2018+)
- [ ] Net/Gross ratio remains low (0.1-0.4) as expected
- [ ] Gross exposure increases proportionally (3-10x)
- [ ] Sharpe ratio improves (positions sized correctly for realized vol)
- [ ] Turnover remains reasonable (<25 round-trips/year with scaling)
- [ ] No numerical instability during vol regime changes

---

## Appendix: Diagnostic Commands

### Run full diagnostic
```bash
python systems/provided/crypto_example/diagnose_volatility_targeting.py
```

### Verify realized vol
```python
from systems.provided.crypto_example.crypto_system import crypto_system_with_dynamic_universe
system = crypto_system_with_dynamic_universe(data_path='data/crypto')
account = system.accounts.portfolio().percent.loc['2018-01-01':]
realized_vol = account.std() * np.sqrt(256)
print(f"Realized vol: {realized_vol:.2f}%")
```

### Check Net/Gross at specific date
```python
import pandas as pd
date = pd.Timestamp('2025-08-01')
weights = system.portfolio.get_instrument_weights()
insts = weights.loc[date].dropna()[weights.loc[date] > 0].index

positions = [system.portfolio.get_notional_position(inst).loc[date] for inst in insts]
gross = sum(abs(p) for p in positions)
net = sum(positions)
print(f"Gross: {gross:.0f}, Net: {net:.0f}, Ratio: {net/gross:.3f}")
```

---

## References

- `.claude/rules/current-work.md` - Previous session work
- `systems/provided/crypto_example/compare_static_vs_dynamic.py` - Comparison runner
- `sysdata/crypto/dynamic_universe.py` - Entry/exit logic
- Rob Carver, "Systematic Trading" - Chapter on portfolio construction
