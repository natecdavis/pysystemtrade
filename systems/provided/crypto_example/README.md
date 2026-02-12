# Crypto Backtesting System

Systematic trading backtest framework for cryptocurrency spot markets using pysystemtrade.

## Quick Start

### Run Main Portfolio Experiment (9 portfolio comparison)
```bash
cd systems/provided/crypto_example
python -m analysis.run_portfolio_experiment
```

### Run Static vs Dynamic Universe Comparison
```bash
python -m analysis.compare_static_vs_dynamic
```

### Analyze Tail Risk (ES95/ES99 metrics)
```bash
python -m analysis.analyze_tail_risk
```

### Interactive Python Session
```python
from systems.provided.crypto_example import crypto_system
system = crypto_system(data_path='data/crypto')
account = system.accounts.portfolio()
print(f"Sharpe: {account.sharpe():.2f}")
```

## Directory Structure

- **core/** - Reusable library components (metrics, portfolio combination, caching)
- **analysis/** - Daily-use analysis scripts (portfolio experiments, tail risk)
- **diagnostics/** - Investigation tools (risk analytics, weight concentration)
- **validation/** - Component validation scripts
- **utilities/** - Helper scripts and report generation
- **docs/** - All documentation (implementation summaries, findings)
- **legacy/** - Deprecated code (historical reference)
- **output/** - Generated results (CSVs, reports) - gitignored
- **backtest_cache/** - Cached portfolio returns for fast re-analysis

## Documentation

See `docs/README.md` for detailed documentation index.

Key documents:
- `docs/TAIL_RISK_ANALYSIS.md` - Portfolio recommendations by risk tolerance
- `docs/IMPLEMENTATION_SUMMARY.md` - Portfolio evaluation methodology
- `docs/RISK_ANALYTICS_FINDINGS.md` - IDM fix and volatility targeting investigation

## Configuration

- `crypto_config_diversified.yaml` - Main config (15 rules, 25% vol target)
- `crypto_config_no_xsmom.yaml` - Variant without cross-sectional momentum

## Latest Results (2026-01-17)

**Best Overall Portfolio:** E1_DYNAMIC_80_20 (80% TREND Dynamic / 20% CARRY)
- Sharpe: 1.50 (tied for best)
- ES95: -0.60% (excellent tail protection)
- MaxDD: -7.3% (shallow drawdowns)
- CAGR: 7.6%

See `docs/TAIL_RISK_ANALYSIS.md` for full findings.
