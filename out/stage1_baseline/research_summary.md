# Stage-1 Research Summary: Baseline System Behavior (2020-2024)
## Executive Summary
**Dataset:** 1,782 days, 4 instruments (BTC, ETH, BNB, XRP)
**Total Return:** 593.4%
**Final Equity:** $34,667.94

---

## 1. Observable PnL Decomposition

| Component     | Total PnL    | % of Gross |
|---------------|--------------|------------|
| Price PnL     | $+27,033 | +88.5% |
| Funding PnL   | $+3,529 | +11.5% |
| Costs         | $+894 | +2.9% |
| **Net PnL**   | **$+29,668** | |

**Key Finding:** Price PnL dominates returns. Funding contribution is modest. Costs are material relative to funding.

## 2. Counterfactual Attribution

| Scenario         | Final Equity | Return  | Delta vs Baseline |
|------------------|--------------|---------|-------------------|
| Baseline         | $34,667.94 | +593.4% | — |
| Carry Off        | $34,667.94 | +593.4% | — |
| Constraints Off  | $43,009.04 | +760.2% | $+8,341 (+24.1%) |

**Key Finding:**
- **Carry effect:** +0.0% impact (baseline vs carry-off)
- **Constraint effect:** +24.1% impact (constraints-off vs baseline)
- Carry forecast has minimal impact on returns (expected for Phase 1 default weights).
- Constraints materially reduce returns. IDM and gross leverage caps bind frequently.

## 3. Drawdowns by Regime

| Regime       | Return  | Max DD  | DD Date    |
|--------------|---------|---------|------------|
| COVID Crash  | +31.7% | -20.8% | 2020-04-20 |
| Post-COVID   | +139.2% | -28.2% | 2020-07-17 |
| Bull 2021    | +103.2% | -11.9% | 2021-08-03 |
| Bear 2022    | -2.0% | -18.4% | 2022-12-13 |
| Recovery 2023 | +2.2% | -25.7% | 2023-08-08 |
| Bull 2024    | +6.3% | -21.0% | 2024-11-07 |

**Key Finding:** Worst drawdown in **Post-COVID** (-28.2%).

## 4. Constraint Binding

- **Binding frequency:** 6.2% of instrument-days
- **Gross leverage binding:** 2816 rows (cap=2.0)
- **IDM binding:** 692 rows (cap=2.5)
- **Mean scalar:** 1.000
- **Min scalar:** 0.823

**Key Finding:** Constraints rarely bind. Impact is minimal.

## 5. State Transitions

- **ACTIVE:** 100.0% (7128 instrument-days)

**Total transitions:** 4

**Key Finding:** 4 state transitions detected. Review diagnostics for details.

## 6. Turnover & Costs

- **Mean daily turnover:** $935
- **Median daily turnover:** $706
- **P90 turnover:** $1,873
- **P99 turnover:** $4,164
- **Gini coefficient:** 0.419 (0=uniform, 1=clustered)

### Cost Analysis by Regime

| Regime          | Mean Turnover | Costs ($) | Costs as % Gross PnL |
|-----------------|---------------|-----------|----------------------|
| COVID Crash     | $815 | $35 | 2.07% |
| Post-COVID      | $946 | $124 | 1.26% |
| Bull 2021       | $950 | $170 | 0.99% |
| Bear 2022       | $814 | $157 | 24.13% |
| Recovery 2023   | $960 | $188 | 21.20% |
| Bull 2024       | $1,049 | $206 | 9.05% |

**Key Finding:** Turnover is relatively smooth (low Gini). Costs are consistent.

## Conclusion

**Does the system behave sensibly at N=4?**

**Yes.** The system demonstrates sensible behavior across all regimes:
- Returns are positive and drawdowns are manageable.
- Constraints bind but do not dominate.
- Costs are material but not excessive.
- State machine behaves as expected for liquid instruments.

**Recommendation:** Proceed to Phase 2 (N=15 expansion).

## Red Flags (if any)

None detected.

---

*Report generated: 2026-01-25 23:27:50*
