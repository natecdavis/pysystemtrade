# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

pysystemtrade is Rob Carver's systematic futures trading system implementing the framework from "Systematic Trading". It provides both backtesting and live production trading via Interactive Brokers.

## Build and Development Commands

```bash
# Install in editable mode with dev dependencies
python -m pip install --editable '.[dev]'

# Run tests
pytest

# Run a single test module
pytest sysdata/tests/test_config.py

# Run tests including slow tests
pytest --runslow

# Skip a specific test
pytest --ignore=sysinit/futures/tests/test_sysinit_futures.py

# Format code (Black 23.11.0, line length 88)
black .
```

## Architecture Overview

### Package Structure

The codebase uses `sys*` prefixed packages:

- **syscore/** - Utilities (pandas helpers, dates, files, exceptions)
- **sysdata/** - Data layer with multiple backends (CSV, MongoDB, Parquet, Arctic)
- **sysobjects/** - Domain objects (instruments, contracts, prices, positions)
- **systems/** - Backtesting engine with stage-based pipeline
- **sysexecution/** - Order management (instrument → contract → broker orders)
- **sysproduction/** - Live trading orchestration and reporting
- **sysbrokers/** - Broker abstraction (Interactive Brokers implementation)
- **syslogging/** - Centralized logging

### Backtesting System

Stage-based pipeline where each stage is independently cacheable:

```
Data (csvFuturesSimData/dbFuturesSimData)
  → System (orchestrator with Config)
    → RawData (prices, volatility)
    → Rules (trading rule forecasts)
    → ForecastScaleCap (scale to vol target)
    → ForecastCombine (weight rule forecasts)
    → PositionSizing (capital-based sizing)
    → Portfolios (instrument weights)
    → Accounts (P&L calculation)
```

Key classes: `System` (systems/basesystem.py), `SystemStage` (systems/stage.py)

Example usage:
```python
from systems.provided.example.simplesystem import simplesystem
system = simplesystem()
system.portfolio.get_notional_position("SOFR")
system.accounts.portfolio().sharpe()
```

### Production System

Daily cycle: price updates → backtest signals → order generation → execution

**dataBlob** (sysdata/data_blob.py) aggregates all data sources with standardized naming (e.g., `data.broker_contract_price`, `data.db_adjusted_prices`).

**Order Stack Hierarchy:**
- Instrument orders (strategy-level)
- Contract orders (handles rolls)
- Broker orders (IB execution)

### Data Layer

Each data type has multiple storage implementations:
- `csv*` - CSV files (backtesting, human-readable)
- `mongo*` / `parquet*` - Production storage
- `ib*` - Direct from Interactive Brokers

Data hierarchy: Raw Contract Prices → Multiple Prices (roll management) → Adjusted Prices (continuous series)

### Configuration

Three layers: defaults (sysdata/config/defaults.py) → YAML configs (systems/provided/*/config.yaml) → runtime overrides

Trading rules defined in YAML with Python function paths:
```yaml
trading_rules:
  ewmac8:
    function: systems.provided.rules.ewmac.ewmac_forecast_with_defaults
    other_args:
      Lfast: 8
      Lslow: 32
```

## Coding Conventions

- Follow PEP 8 and Black formatting (Python 3.10 target)
- Use `arg_not_supplied` for optional parameters where relevant
- Classes use mixedCase; methods use prefixes: `get`, `calculate`, `read`, `write`
- Data classes inherit from `baseData`; stages inherit from `SystemStage`
- Use `@output()` decorator for cacheable stage methods

## Branch and PR Guidelines

- Branch from `develop` using `bug-<issue#>-<description>` or `feature-<issue#>-<description>`
- Open PRs against `develop` with clear summary and linked issue

## Key Files

- `systems/provided/example/simplesystem.py` - Simplest complete backtesting example
- `sysdata/sim/csv_futures_sim_data.py` - Backtesting data interface
- `sysdata/data_blob.py` - Production data aggregation
- `sysexecution/orders/` - Order class hierarchy
- `private/` - Local credentials/config (not committed)
