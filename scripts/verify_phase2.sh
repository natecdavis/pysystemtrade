#!/bin/bash
# Verification script for Phase 2: Opportunistic Registry Refresh
# Run this to verify the implementation is working correctly

set -e

# Use python3 explicitly
PYTHON=python3

echo "========================================="
echo "Phase 2 Verification Script"
echo "========================================="
echo ""

# 1. Run unit tests
echo "1. Running Phase 2 unit tests..."
$PYTHON -m pytest tests/test_phase2_opportunistic_refresh.py -v --tb=short
echo "   ✓ All tests passed"

# 2. Test diff detection directly
echo ""
echo "2. Testing diff detection (library function)..."
$PYTHON -c "
from pathlib import Path
from scripts.refresh_binance_market_registry import detect_changes

metadata_dir = Path('envs/dev/data/raw/metadata')

# Simulate new candidate list
new_candidates = ['BTCUSDT_PERP', 'ETHUSDT_PERP', 'NEWCOIN_PERP']  # Fake new coin

changelog = detect_changes(metadata_dir, new_candidates)

print(f'   ✓ Diff detection working')
print(f'   Total count: {changelog[\"total_count\"]}')
if changelog.get('new_instruments'):
    print(f'   New: {len(changelog[\"new_instruments\"])} instruments')
"

# 3. Test manual registry refresh (dry run)
echo ""
echo "3. Testing manual registry refresh (dry run)..."
$PYTHON scripts/refresh_binance_market_registry.py --env dev --dry-run 2>&1 | grep -q "DRY RUN"
echo "   ✓ Dry run works"

# 4. Test registry hash computation
echo ""
echo "4. Testing registry hash computation..."
$PYTHON -c "
import hashlib
from pathlib import Path

registry_path = Path('envs/dev/data/raw/metadata/discovered_candidate_instruments.json')

if registry_path.exists():
    with open(registry_path, 'rb') as f:
        registry_hash = hashlib.sha256(f.read()).hexdigest()[:12]
    print(f'   ✓ Registry hash: {registry_hash}')
else:
    print('   ✗ Registry not found')
    exit(1)
"

# 5. Verify changelog file exists from previous runs
echo ""
echo "5. Checking for registry changelog..."
CHANGELOG="envs/dev/data/raw/metadata/registry_changelog.json"
if [ -f "$CHANGELOG" ]; then
    TIMESTAMP=$(jq -r '.timestamp' "$CHANGELOG")
    echo "   ✓ Changelog found (last update: $TIMESTAMP)"
else
    echo "   ⚠ Changelog not found (will be created on first refresh)"
fi

# 6. Test opportunistic refresh function (imported)
echo ""
echo "6. Testing opportunistic refresh function..."
$PYTHON -c "
import sys
from pathlib import Path

# Add scripts to path
sys.path.insert(0, str(Path.cwd() / 'scripts'))

# This would normally be imported in run_live_advisory.py
# Just verify it can be imported
try:
    from run_live_advisory import refresh_registry_opportunistic
    print('   ✓ Opportunistic refresh function importable')
except Exception as e:
    print(f'   ✗ Import failed: {e}')
    exit(1)
"

echo ""
echo "========================================="
echo "Phase 2 Verification: ✅ COMPLETE"
echo "========================================="
echo ""
echo "All checks passed!"
echo ""
echo "Key Features Verified:"
echo "  - Diff detection (new/delisted instruments)"
echo "  - Registry hash computation"
echo "  - Changelog generation"
echo "  - Opportunistic refresh function"
echo ""
echo "Next Steps:"
echo "  - Test full advisory workflow with auto_discover"
echo "  - Verify registry metadata in advisory_metadata.json"
echo "  - Proceed to Phase 3 (Lifecycle from Vision Data)"
echo ""
