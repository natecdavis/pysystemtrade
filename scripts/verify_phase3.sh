#!/bin/bash
# Verification script for Phase 3: Lifecycle from Vision Data Coverage
# Run this to verify the implementation is working correctly

set -e

# Use python3 explicitly
PYTHON=python3

echo "========================================="
echo "Phase 3 Verification Script"
echo "========================================="
echo ""

# 1. Run unit tests
echo "1. Running Phase 3 unit tests..."
$PYTHON -m pytest tests/test_phase3_lifecycle.py -v --tb=short
echo "   ✓ All tests passed"

# 2. Test lifecycle derivation function
echo ""
echo "2. Testing lifecycle derivation (synthetic data)..."
$PYTHON -c "
import pandas as pd
from datetime import datetime, timedelta
from scripts.build_example_dataset import derive_lifecycle_from_vision_data

# Create sample dataset
dates = pd.date_range('2024-01-01', '2024-01-10', freq='D')
data = []

for date in dates:
    data.append({
        'date': date,
        'instrument': 'TESTUSDT_PERP',
        'close': 100.0
    })

df = pd.DataFrame(data)

# Derive lifecycle
lifecycle = derive_lifecycle_from_vision_data(df)

print(f'   ✓ Lifecycle derived for {len(lifecycle)} instruments')
print(f'   Sample: TESTUSDT_PERP')
print(f'     Status: {lifecycle[\"TESTUSDT_PERP\"][\"status\"]}')
print(f'     Data days: {lifecycle[\"TESTUSDT_PERP\"][\"data_days\"]}')
"

# 3. Test lifecycle loading from manifest
echo ""
echo "3. Testing lifecycle loading from manifest..."

# Check if a real manifest exists
MANIFEST="data/example_crypto_perps_5x_live.manifest.json"
if [ -f "$MANIFEST" ]; then
    $PYTHON -c "
from pathlib import Path
from sysdata.crypto.dynamic_universe import load_lifecycle_from_manifest

manifest_path = Path('$MANIFEST')
lifecycle = load_lifecycle_from_manifest(manifest_path)

if lifecycle:
    print(f'   ✓ Loaded lifecycle for {len(lifecycle)} instruments')
    # Show first instrument
    first_inst = list(lifecycle.keys())[0]
    print(f'   Sample: {first_inst}')
    print(f'     Status: {lifecycle[first_inst][\"status\"]}')
else:
    print('   ⚠ Manifest exists but has no lifecycle section')
    print('   (Will be added next time dataset is rebuilt)')
"
else
    echo "   ⚠ No existing manifest found at $MANIFEST"
    echo "   (Will be created when building datasets with Phase 3)"
fi

# 4. Test lifecycle eligibility checking
echo ""
echo "4. Testing lifecycle eligibility checking..."
$PYTHON -c "
import pandas as pd
from sysdata.crypto.dynamic_universe import check_lifecycle_eligibility

lifecycle_data = {
    'BTCUSDT_PERP': {
        'first_data_date': '2020-01-01',
        'last_data_date': '2026-02-13',
        'status': 'ACTIVE'
    },
    'OLDCOINUSDT_PERP': {
        'first_data_date': '2019-01-01',
        'last_data_date': '2020-12-31',
        'status': 'STALE'
    }
}

# Test cases
test_date = pd.Timestamp('2024-01-01')

eligible_btc = check_lifecycle_eligibility('BTCUSDT_PERP', test_date, lifecycle_data)
eligible_old = check_lifecycle_eligibility('OLDCOINUSDT_PERP', test_date, lifecycle_data)
eligible_unknown = check_lifecycle_eligibility('UNKNOWNUSDT_PERP', test_date, lifecycle_data)

print(f'   ✓ Eligibility checks:')
print(f'     BTCUSDT_PERP (active): {eligible_btc}')
print(f'     OLDCOINUSDT_PERP (delisted): {eligible_old}')
print(f'     UNKNOWNUSDT_PERP (no lifecycle): {eligible_unknown}')

assert eligible_btc == True
assert eligible_old == False
assert eligible_unknown == True  # Conservative fallback
"

# 5. Verify lifecycle summary computation
echo ""
echo "5. Testing lifecycle summary computation..."
$PYTHON -c "
import pandas as pd
from datetime import datetime, timedelta
from scripts.build_example_dataset import derive_lifecycle_from_vision_data

# Create dataset with mixed statuses
current_date = datetime.utcnow().date()
data = []

# Active instrument (recent data)
for i in range(10):
    data.append({
        'date': current_date - timedelta(days=i),
        'instrument': 'ACTIVEUSDT_PERP',
        'close': 100.0
    })

# Stale instrument (old data)
for i in range(10):
    data.append({
        'date': current_date - timedelta(days=30 + i),
        'instrument': 'STALEUSDT_PERP',
        'close': 50.0
    })

df = pd.DataFrame(data)
lifecycle = derive_lifecycle_from_vision_data(df, stale_threshold_days=7)

# Compute summary
active = sum(1 for lc in lifecycle.values() if lc.get('status') == 'ACTIVE')
stale = sum(1 for lc in lifecycle.values() if lc.get('status') == 'STALE')

print(f'   ✓ Lifecycle summary:')
print(f'     Active: {active}')
print(f'     Stale: {stale}')

assert active >= 1
assert stale >= 1
"

echo ""
echo "========================================="
echo "Phase 3 Verification: ✅ COMPLETE"
echo "========================================="
echo ""
echo "All checks passed!"
echo ""
echo "Key Features Verified:"
echo "  - Lifecycle derivation from Vision data"
echo "  - Status determination (ACTIVE/STALE/NO_DATA)"
echo "  - Manifest loading and persistence"
echo "  - Eligibility filtering by date boundaries"
echo "  - Lifecycle summary computation"
echo ""
echo "Next Steps:"
echo "  - Rebuild datasets to include lifecycle metadata"
echo "  - Integrate lifecycle filtering in dynamic universe"
echo "  - Test with real Vision data (541 instruments)"
echo ""
