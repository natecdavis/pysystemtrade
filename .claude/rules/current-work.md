# Current Work Context

## Last Session Summary (2026-01-14)

Implemented **Walk-Forward Dynamic Instrument Universe** system:
- Created `sysdata/crypto/walk_forward_costs.py` - ADV$-based spread estimation
- Created `sysdata/crypto/dynamic_universe.py` - Eligibility filtering with SR cost thresholds
- Created `systems/provided/crypto_example/analyze_universe.py` - Diagnostic script
- Updated `sysdata/crypto/spot_sim_data.py` with dynamic universe support
- Updated `sysdata/crypto/csv_spot_data.py` with volume data access

Analysis shows **~300 instruments** pass cost filters at latest date.

## Active Task

Setting up Claude Code workflow improvements:
- Context management via `.claude/rules/`
- macOS notifications via hooks

## Next Steps

1. ~~Configure notification hooks~~ (in progress)
2. Test the notification system
3. Integrate dynamic universe with portfolio stage (future)
4. Address volume data quality issues (some instruments have unrealistic ADV)

## Key Files Modified Recently

### New Files
- `sysdata/crypto/walk_forward_costs.py`
- `sysdata/crypto/dynamic_universe.py`
- `systems/provided/crypto_example/analyze_universe.py`
- `.claude/rules/crypto-backtest.md`
- `.claude/rules/current-work.md`

### Modified Files
- `sysdata/crypto/spot_sim_data.py` - Added dynamic universe support
- `sysdata/crypto/csv_spot_data.py` - Added volume/OHLCV methods
- `systems/provided/crypto_example/crypto_config_diversified.yaml` - Added dynamic universe docs

## Known Issues

- **Volume data quality**: Some instruments (WBTC, WETH) show unrealistic ADV values due to inconsistent volume units in CSV data
- **Portfolio integration**: Dynamic universe not yet fully integrated with pysystemtrade's portfolio stage - currently provides eligibility info that could be used manually

## Useful Commands

```bash
# Run universe analysis
python systems/provided/crypto_example/analyze_universe.py

# Run backtest
python systems/provided/crypto_example/run_crypto_backtest.py
```
