# Crypto Backtest Documentation

## Latest Research & Findings

### Tail Risk Analysis (2026-01-17)
- **TAIL_RISK_ANALYSIS.md** - Portfolio recommendations by risk tolerance
  - 9 portfolio configurations tested (TREND/CARRY, static/dynamic allocations)
  - Expected Shortfall (ES95/ES99) metrics for tail risk
  - Recommendation: E1_DYNAMIC_80_20 for best overall risk-adjusted returns

- **TAIL_RISK_IMPLEMENTATION.md** - Technical implementation of ES/CVaR metrics
  - How Expected Shortfall is calculated
  - Drawdown duration methodology
  - Code examples and formulas

- **TAIL_RISK_IMPLEMENTATION_SUMMARY.md** - Quick reference

### Portfolio Framework (2026-01-17)
- **IMPLEMENTATION_SUMMARY.md** - Portfolio evaluation methodology
  - 9-portfolio experiment design
  - TREND vs CARRY allocation testing
  - Static (12 instruments) vs dynamic (185+ instruments) universe

- **PORTFOLIO_COMPARISON_REPORT.md** - Performance summary
  - Sharpe ratios, CAGR, volatility
  - Drawdown metrics
  - Risk-adjusted comparisons

- **VALIDATION_RESULTS.md** - Portfolio combination framework validation
  - Position calculation verification
  - Weight normalization checks

### Investigation History (2026-01-16)
- **RISK_ANALYTICS_FINDINGS.md** - Root cause analysis of IDM/volatility issue
  - Fixed IDM now scales correctly (1.22 → 1.977 with 185+ instruments)
  - Dynamic universe intentionally low-volatility (market-neutral design)
  - Verification that system works correctly

- **VOLATILITY_TARGETING_DIAGNOSIS.md** - Volatility targeting investigation
  - Why dynamic universe has 3.71% vol vs 25% target
  - Net/gross exposure analysis
  - Market-neutral positioning explanation

### Signal Research (Earlier)
- **SIGNAL_RESEARCH_SUMMARY.md** - Trading signal evaluation
  - 15-rule ensemble design
  - EWMAC, Breakout, TSMOM, Accel, RelMomentum families

## Implementation Details

### Core Components
See `../core/` directory for implementation:
- `portfolio_metrics.py` - Sharpe, CAGR, ES95/ES99, drawdown duration
- `portfolio_combiner.py` - Multi-portfolio combination logic
- `dynamic_portfolio.py` - Dynamic universe portfolio stage
- `cache_systems.py` - System caching for faster re-runs

### Analysis Scripts
See `../analysis/` directory:
- `run_portfolio_experiment.py` - PRIMARY: 9-portfolio comparison
- `analyze_tail_risk.py` - Tail risk analysis
- `compare_static_vs_dynamic.py` - Static vs dynamic comparison

### Data Layer
See `/sysdata/crypto/`:
- `spot_sim_data.py` - Simulation data adapter
- `walk_forward_costs.py` - Walk-forward cost estimation
- `dynamic_universe.py` - Eligibility filtering

## Configuration

### Main Config: `crypto_config_diversified.yaml`
- 15 trading rules (EWMAC, Breakout, TSMOM, Accel, RelMomentum)
- 25% volatility target
- Walk-forward forecast scaling and FDM estimation

### Variant: `crypto_config_no_xsmom.yaml`
- 13 rules (removes RelMomentum cross-sectional rules)
- For A/B testing market-neutral vs directional strategies

## Quick Reference

### Best Portfolios by Risk Tolerance
- **Conservative:** C_TREND_DYNAMIC (ES95: -0.49%, MaxDD: -3.1%)
- **Balanced:** E2_DYNAMIC_50_50 (ES95: -0.98%, Sharpe: 1.42)
- **Aggressive:** D3_STATIC_20_80 (CAGR: 23.1%, Sharpe: 1.40)
- **Best Overall:** E1_DYNAMIC_80_20 (Sharpe: 1.50, ES95: -0.60%)

### Key Metrics
- ES95: Expected loss on worst 5% of days
- ES99: Expected loss on worst 1% of days
- MaxDD: Maximum drawdown from peak
- DD Duration: Days from peak to recovery
