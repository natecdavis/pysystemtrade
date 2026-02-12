# Long-Only Constraint Implementation

**Date:** 2026-01-17
**Status:** ✅ COMPLETE

## Problem Statement

The retail crypto backtest had a **critical unrealistic assumption**: it allowed short positions using spot prices.

**Why this is unrealistic:**
- **Retail spot traders CANNOT short** on most exchanges (Coinbase, Binance US, Kraken spot)
- **Missing costs**: If using perpetuals for shorts, funding rates add 10-55% annual costs
- **Impact**: Backtest results were completely unrealistic for retail traders

## Solution

Added a **long-only constraint** that forces all positions to be >= 0 (no shorts).

### Implementation Changes

#### 1. Updated Retail Configs (3 files)

All three retail configs now have `long_only_instruments: True`:

- `crypto_config_retail_conservative.yaml`
- `crypto_config_retail.yaml` (moderate, DEFAULT)
- `crypto_config_retail_aggressive.yaml`

**Added line:**
```yaml
long_only_instruments: True  # Force long-only (realistic for spot trading)
```

#### 2. Enhanced Position Sizing Code

**File:** `systems/positionsizing.py`

**Updated method:** `_is_instrument_long_only()` (lines 144-165)

**New functionality:**
- **Global mode:** `long_only_instruments: True` → ALL instruments are long-only
- **List mode:** `long_only_instruments: [BTC, ETH]` → Specific instruments are long-only
- **Backward compatible:** Existing list-based configs still work

**Code logic:**
```python
def _is_instrument_long_only(self, instrument_code: str) -> bool:
    config = self.config
    long_only_config = config.get_element_or_default("long_only_instruments", [])

    # Global mode: apply to all instruments
    if long_only_config is True:
        return True

    # List mode: check if instrument is in the list
    if isinstance(long_only_config, list):
        return instrument_code in long_only_config

    # Default: no constraint
    return False
```

**Position constraint:**
```python
def _apply_long_only_constraint_to_position(self, position, instrument_code):
    instrument_long_only = self._is_instrument_long_only(instrument_code)
    if instrument_long_only:
        position[position < 0.0] = 0.0  # Force negatives to zero
    return position
```

## Verification

**Verification script:** `quick_verify_long_only.py`

**Test results:** ✅ ALL TESTS PASSED

```
======================================================================
TEST 1: Config Loading
======================================================================
✓ Conservative: long_only_instruments = True
✓ Moderate: long_only_instruments = True
✓ Aggressive: long_only_instruments = True

======================================================================
TEST 2: Position Sizing Logic
======================================================================
✓ Config value is True (global mode)
✓ BTC: would be long-only = True
✓ ETH: would be long-only = True
✓ All positions >= 0 (constraint working)
✓ Positive positions unchanged

======================================================================
TEST 3: List Mode Backward Compatibility
======================================================================
✓ BTC: would be long-only = True (expected True)
✓ ETH: would be long-only = True (expected True)
✓ XRP: would be long-only = False (expected False)
✓ SOL: would be long-only = False (expected False)
```

## Impact

### Performance Changes (Expected)

**Long-only vs shorts-allowed:**
- **Sharpe:** Expected 0.9-1.1 (reduction of 0.1-0.2)
  - Portfolio becomes directional, loses market-neutral tail protection
- **Volatility:** May be slightly higher in bull markets
- **Returns:** CAGR may vary more with market direction

**Trade-off:** Realistic implementability >> 0.1-0.2 Sharpe reduction

**Better to model what you can actually trade than optimize unrealistic assumptions.**

### Realistic Trading

✅ **Benefits:**
- No unrealistic shorting assumptions
- Matches Coinbase, Binance US, Kraken spot reality
- No missing funding cost errors
- Portfolio is implementable with retail spot trading

⚠️ **Trade-offs:**
- Portfolio becomes directional (higher volatility in bull markets)
- Loses market-neutral tail protection from offsetting positions
- Slightly lower risk-adjusted returns (expected)

## Usage

### Running a Backtest with Long-Only

All retail configs automatically use long-only mode (no changes needed):

```python
from systems.provided.crypto_example.crypto_system import crypto_system_with_dynamic_universe
from sysdata.config.configdata import Config

# Load retail config (long-only enabled by default)
config = Config('systems/provided/crypto_example/crypto_config_retail.yaml')

# Create system
system = crypto_system_with_dynamic_universe(
    data_path='data/crypto',
    config=config
)

# Verify no shorts
btc_pos = system.portfolio.get_notional_position('BTC')
print(f"BTC minimum position: {btc_pos.min():.2f}")  # Should be >= 0
```

### Verification Command

```bash
# Run quick verification test
python systems/provided/crypto_example/quick_verify_long_only.py
```

## Advanced: Perpetuals Variant (Optional - NOT IMPLEMENTED)

**For advanced users who understand funding costs:**

If you want to trade perpetual futures (Binance global) and allow shorts with realistic funding costs:

1. Create `crypto_config_retail_perp.yaml`:
   ```yaml
   long_only_instruments: False  # Allow shorts
   cost_calculation:
     exchange: "binance_perpetual"
     include_funding: True
   ```

2. Implement funding rate data loader (not included in this implementation)
3. Update cost model to include funding rates (10-55% annual!)

**CRITICAL:** Only use if you:
- Have access to perpetual futures (not available in US)
- Understand funding costs (can be 10-55% annually)
- Have historical funding rate data
- Can manage liquidation risk (need 2-3x collateral)

## Files Modified/Created

### Modified:
1. `crypto_config_retail_conservative.yaml` (1 line added)
2. `crypto_config_retail.yaml` (1 line added)
3. `crypto_config_retail_aggressive.yaml` (1 line added)
4. `systems/positionsizing.py` (~40 lines modified)

### Created:
5. `quick_verify_long_only.py` (~200 lines) - Verification script
6. `LONG_ONLY_IMPLEMENTATION.md` - This document

## Testing Checklist

- ✅ All three retail configs have `long_only_instruments: True`
- ✅ Config parameter loads correctly
- ✅ Position sizing logic handles boolean True (global mode)
- ✅ Position sizing logic handles list mode (backward compatible)
- ✅ Positions are constrained to >= 0
- ✅ Positive positions unchanged
- ✅ Verification test passes

## Next Steps (Optional)

1. **Run full backtest** with long-only constraint to measure actual performance impact
2. **Compare vs shorts-allowed** to quantify Sharpe reduction (expected 0.1-0.2)
3. **Update documentation** with long-only performance results
4. **Consider perpetuals variant** if advanced users request it (requires funding rate data)

## References

**Related documents:**
- `IMPLEMENTATION_SUMMARY.md` - Retail backtest implementation overview
- `README_RETAIL.md` - Retail trading guide
- `diagnose_retail_feasibility.py` - Retail feasibility diagnostic

**Key insight:**
> "The implementation assumes spot prices but allows shorts. This is unrealistic. Retail spot traders CANNOT short (no margin in most jurisdictions). If using perpetuals for shorts, funding rates add 10-55% annual costs (!). Current backtest results are completely unrealistic for retail traders."
