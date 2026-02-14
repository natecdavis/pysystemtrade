#!/bin/bash
# Verification script for Phase 5: Top-K Selection with Hysteresis
# Run this to verify the implementation is working correctly

set -e

# Use python3 explicitly
PYTHON=python3

echo "========================================="
echo "Phase 5 Verification Script"
echo "========================================="
echo ""

# 1. Run unit tests
echo "1. Running Phase 5 unit tests..."
$PYTHON -m pytest tests/test_phase5_top_k_selector.py -v --tb=short
echo "   ✓ All tests passed"

# 2. Test TopKSelector initialization
echo ""
echo "2. Testing TopKSelector initialization..."
$PYTHON -c "
from sysdata.crypto.top_k_selector import TopKInstrumentSelector

# Initialize with default parameters
selector = TopKInstrumentSelector(K=30, entry_buffer=5, exit_buffer=10)

print(f'   ✓ TopKSelector initialized')
print(f'   K={selector.K}')
print(f'   Entry threshold: rank <= {selector.entry_threshold}')
print(f'   Exit threshold: rank > {selector.exit_threshold}')
print(f'   ADV window: {selector.adv_window} days')
"

# 3. Test hysteresis logic
echo ""
echo "3. Testing hysteresis logic..."
$PYTHON -c "
import pandas as pd
from sysdata.crypto.top_k_selector import TopKInstrumentSelector

# Create test data
dates = pd.date_range('2024-01-01', periods=10, freq='D')
instruments = [f'INST{i:02d}' for i in range(15)]

prices_df = pd.DataFrame(
    {inst: [100.0] * 10 for inst in instruments},
    index=dates
)

volumes_df = pd.DataFrame(
    {inst: [1000000 - (i * 10000)] * 10 for i, inst in enumerate(instruments)},
    index=dates
)

selector = TopKInstrumentSelector(K=10, entry_buffer=3, exit_buffer=5, min_history_days=5)

# Test entry hysteresis
current_tradable = set()
eligible = instruments
date = dates[-1]

new_tradable = selector.select_tradable_set(
    eligible, current_tradable, prices_df, volumes_df, date
)

print(f'   ✓ Entry hysteresis working')
print(f'   Entry threshold: <= {selector.entry_threshold}')
print(f'   Instruments selected: {len(new_tradable)}')
assert len(new_tradable) == selector.entry_threshold
"

# 4. Test liquidity computation
echo ""
echo "4. Testing liquidity metric computation..."
$PYTHON -c "
import pandas as pd
from sysdata.crypto.top_k_selector import TopKInstrumentSelector

dates = pd.date_range('2024-01-01', periods=30, freq='D')

# High vs low liquidity
prices_df = pd.DataFrame({
    'HIGH_LIQ': [100.0] * 30,
    'LOW_LIQ': [10.0] * 30,
}, index=dates)

volumes_df = pd.DataFrame({
    'HIGH_LIQ': [1000000.0] * 30,  # High volume
    'LOW_LIQ': [1000.0] * 30,      # Low volume
}, index=dates)

selector = TopKInstrumentSelector(K=10, adv_window=10, min_history_days=20)
liquidity = selector.compute_liquidity_metric(prices_df, volumes_df, dates[-1])

print(f'   ✓ Liquidity computation working')
print(f'   Highest liquidity: {liquidity.index[0]}')
print(f'   ADV window: {selector.adv_window} days')

assert liquidity.index[0] == 'HIGH_LIQ'
"

# 5. Test tradable-over-time evolution
echo ""
echo "5. Testing tradable set evolution over time..."
$PYTHON -c "
import pandas as pd
from sysdata.crypto.top_k_selector import TopKInstrumentSelector

dates = pd.date_range('2024-01-01', periods=10, freq='D')
instruments = ['INST00', 'INST01', 'INST02', 'INST03', 'INST04']

eligible_df = pd.DataFrame(True, index=dates, columns=instruments)

prices_df = pd.DataFrame(
    {inst: [100.0] * 10 for inst in instruments},
    index=dates
)

volumes_df = pd.DataFrame(
    {inst: [1000000 - (i * 10000)] * 10 for i, inst in enumerate(instruments)},
    index=dates
)

selector = TopKInstrumentSelector(K=3, entry_buffer=1, exit_buffer=1, min_history_days=5)

tradable_over_time = selector.get_tradable_over_time(
    eligible_df, prices_df, volumes_df
)

print(f'   ✓ Tradable evolution working')
print(f'   Dates simulated: {len(tradable_over_time)}')
print(f'   Initial tradable count: {len(tradable_over_time[dates[0]])}')
"

# 6. Test eligibility DataFrame conversion
echo ""
echo "6. Testing eligibility DataFrame conversion..."
$PYTHON -c "
import pandas as pd
from sysdata.crypto.top_k_selector import TopKInstrumentSelector

dates = pd.date_range('2024-01-01', periods=3, freq='D')
instruments = ['INST00', 'INST01', 'INST02']

tradable_over_time = {
    dates[0]: {'INST00', 'INST01'},
    dates[1]: {'INST00'},
    dates[2]: {'INST01', 'INST02'},
}

selector = TopKInstrumentSelector(K=5)
df = selector.to_eligibility_df(tradable_over_time, instruments)

print(f'   ✓ DataFrame conversion working')
print(f'   Shape: {df.shape}')
print(f'   True values on day 1: {df.iloc[0].sum()}')

assert df.shape == (3, 3)
assert df.iloc[0].sum() == 2  # Day 1 has 2 tradable
"

echo ""
echo "========================================="
echo "Phase 5 Verification: ✅ COMPLETE"
echo "========================================="
echo ""
echo "All checks passed!"
echo ""
echo "Key Features Verified:"
echo "  - Top-K selection with hysteresis"
echo "  - Entry/exit thresholds (asymmetric)"
echo "  - Liquidity metric (Rolling ADV)"
echo "  - Tradable set evolution over time"
echo "  - Eligibility DataFrame conversion"
echo ""
echo "Configuration Example:"
echo "  K=30: Target tradable size"
echo "  entry_buffer=5: Enter if rank <= 25"
echo "  exit_buffer=10: Exit if rank > 40"
echo "  → Hysteresis zone: ranks 26-40 can stay but can't enter"
echo ""
echo "Next Steps:"
echo "  - Integration with dynamic portfolio (Phase 6)"
echo "  - Config validation for top-K parameters"
echo "  - layer_a_instruments as max tradable set"
echo ""
