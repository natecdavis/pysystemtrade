# Crypto Backtest Conventions

## Key Files

### Configuration
- **Main config**: `systems/provided/crypto_example/crypto_config_diversified.yaml`
- **No-XSMOM config**: `systems/provided/crypto_example/crypto_config_no_xsmom.yaml`
- **Instrument config**: `data/crypto/instrument_config.yaml`

### Data Layer
- **Sim data adapter**: `sysdata/crypto/spot_sim_data.py`
- **CSV price reader**: `sysdata/crypto/csv_spot_data.py`
- **Instrument data**: `sysdata/crypto/spot_instrument_data.py`
- **Walk-forward costs**: `sysdata/crypto/walk_forward_costs.py`
- **Dynamic universe**: `sysdata/crypto/dynamic_universe.py`

### Scripts
- **Backtest runner**: `systems/provided/crypto_example/run_crypto_backtest.py`
- **Universe analysis**: `systems/provided/crypto_example/analyze_universe.py`

## Trading Rules Stack (15 rules)

| Family | Rules | Function |
|--------|-------|----------|
| EWMAC | ewmac8_32, ewmac16_64, ewmac32_128, ewmac64_256 | `systems.provided.rules.ewmac` |
| Breakout | breakout10, breakout20, breakout40, breakout80 | `systems.provided.rules.breakout` |
| TSMOM | tsmom63, tsmom126, tsmom252 | `systems.provided.rules.tsmom` |
| Accel | accel16, accel32 | `systems.provided.rules.accel` |
| RelMomentum | relmomentum20, relmomentum40 | `systems.provided.rules.rel_mom` |

## Walk-Forward Features
- Forecast scalars: `use_forecast_scale_estimates: True`
- FDM: `use_forecast_div_mult_estimates: True`
- Instrument weights: Currently hardcoded (12 instruments)

## Running Backtests

```python
from systems.provided.crypto_example.run_crypto_backtest import run_backtest
results = run_backtest()  # Uses crypto_config_diversified.yaml
```

## Data Location
- Price CSVs: `data/crypto/`
- Format: `{INSTRUMENT}.csv` with columns: date, open, high, low, close, volume

## Carver Cost Filters
- SR per trade threshold: ≤ 0.01
- Annual SR threshold: ≤ 0.13
- Stack-weighted turnover: ~15 round-trips/year
