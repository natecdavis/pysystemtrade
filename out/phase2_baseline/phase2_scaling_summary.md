# Phase 2 Scaling Analysis Summary

**Date**: 2026-01-26
**Analysis**: Cross-section scaling from N=4 to N=15 instruments

## Executive Summary

This report characterizes system behavior when scaling from:
- **Phase 1 (Depth)**: N=4 instruments, 2020-2024 (1,782 days, includes COVID crash)
- **Phase 2 (Breadth)**: N=15 instruments, 2021-2024 (~1,460 days, post-maturity regime)

**Key Insight**: This is a *depth vs breadth* comparison, not a performance ranking. 
Different regimes and cross-sections produce different economic outcomes by design.

## Engineering Success Criteria

- **Runtime**: Not recorded in metadata (backtest completed successfully based on outputs)

- **Stability**: ✓ No crashes or numeric instability

## Correlation Structure (N=15)

- **Median pairwise correlation**: 0.649
- **Mean pairwise correlation**: 0.638
- **Range**: [0.393, 0.824]

**Observation**: Moderate-high correlations typical for crypto assets.

## IDM Scaling Behavior

### Phase 2 (N=15)
- Mean IDM: 0.902
- Max IDM: 3.063

### Phase 1 (N=4)
- Mean IDM: 1.061
- Max IDM: 3.036

**IDM scaling**: 0.85x increase from N=4 to N=15
**Observation**: Limited IDM increase suggests high correlations reduce diversification benefit.

## Constraint Binding Patterns

### Phase 2 (N=15)

### Phase 1 (N=4)

## Position Concentration (N=15)

- **Herfindahl index**: 0.095 (0=equal weights, 1=all in one)
- **Top 1 position**: 15.6% of portfolio on average
- **Top 3 combined**: 38.7% of portfolio on average

**Observation**: Reasonable diversification across instruments.

## Regime Context: Depth vs Breadth

**Phase 1 (2020-2024, N=4)**:
- Includes COVID crash (March 2020) - extreme volatility regime
- Longer history (1,782 days)
- Depth-focused: fewer instruments, longer time series

**Phase 2 (2021-2024, N=15)**:
- Post-maturity regime, no COVID crash
- Shorter history (~1,460 days)
- Breadth-focused: more instruments, shorter time series

**Important**: Performance differences reflect regime and scale differences, not system quality. 
Any divergence in Sharpe, drawdown, or returns should be interpreted as regime characteristics.

## Engineering Issues Identified

✓ No engineering issues detected. System is stable at N=15.

## Next Steps

1. **If engineering issues**: Fix runtime/stability problems first
2. **If no engineering issues**: Proceed to rule inclusion/tuning decisions with full context
3. **Future work**: Regime analysis within 2021-2024 period (Bull/Bear/Recovery phases)

## Appendices

- `correlation_heatmap.png`: 15x15 correlation matrix visualization
- `correlation_distribution.png`: Distribution of pairwise correlations
- `idm_over_time.png`: IDM time series (N=4 vs N=15)
- `position_concentration.png`: Herfindahl index and top weights over time
- `constraint_comparison.csv`: Detailed constraint binding statistics
- `correlation_matrix.csv`: Full correlation matrix
