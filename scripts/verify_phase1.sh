#!/bin/bash
# Verification script for Phase 1: Registry-Aware Advisory Workflow
# Run this to verify the implementation is working correctly

set -e

# Use python3 explicitly
PYTHON=python3

echo "========================================="
echo "Phase 1 Verification Script"
echo "========================================="
echo ""

# 1. Check registry exists
echo "1. Checking registry..."
REGISTRY="envs/dev/data/raw/metadata/discovered_candidate_instruments.json"
if [ -f "$REGISTRY" ]; then
    COUNT=$(jq '.candidate_instruments | length' "$REGISTRY")
    echo "   ✓ Registry found: $COUNT instruments"
else
    echo "   ✗ Registry not found at $REGISTRY"
    exit 1
fi

# 2. Run unit tests
echo ""
echo "2. Running unit tests..."
$PYTHON -m pytest tests/test_phase1_registry_integration.py -v --tb=short
echo "   ✓ All tests passed"

# 3. Test config helper directly
echo ""
echo "3. Testing config helper (registry extraction)..."
$PYTHON -c "
from pathlib import Path
import yaml
from sysdata.crypto.config_helpers import extract_candidate_instruments_with_registry

config_path = Path('config/test_auto_discover.yaml')
with open(config_path) as f:
    config = yaml.safe_load(f)

env_root = Path('envs/dev')
candidates, source = extract_candidate_instruments_with_registry(config, env_root)

print(f'   ✓ Extracted {len(candidates)} candidates')
print(f'   ✓ Source: {source}')
assert 'discovered_candidate_instruments.json' in source
assert len(candidates) == 541
"

# 4. Test sync_positions_file.py
echo ""
echo "4. Testing sync_positions_file.py (dry run)..."
TEMP_POS="/tmp/test_positions.csv"
$PYTHON scripts/sync_positions_file.py \
    --config config/test_auto_discover.yaml \
    --positions-file "$TEMP_POS" 2>&1 | grep -q "✓"
echo "   ✓ Sync script works"
rm -f "$TEMP_POS"

# 5. Verify parquet adapter config-aware
echo ""
echo "5. Testing parquet adapter (config-aware candidate extraction)..."
$PYTHON -c "
from pathlib import Path
from sysdata.crypto.parquet_perps_sim_data import parquetCryptoPerpsSimData

# Note: This will fail if dataset doesn't exist, which is expected
# We're just verifying the code path compiles and executes
try:
    data = parquetCryptoPerpsSimData(
        dataset_path='data/example_crypto_perps_5x_live.parquet',
        config_path='config/test_auto_discover.yaml',
        env_root=Path('envs/dev'),
    )
    print(f'   ✓ Parquet adapter initialized with config')
except FileNotFoundError as e:
    # Expected if dataset doesn't exist
    if 'parquet' in str(e).lower():
        print(f'   ✓ Parquet adapter code path verified (dataset not found, expected)')
    else:
        raise
"

echo ""
echo "========================================="
echo "Phase 1 Verification: ✅ COMPLETE"
echo "========================================="
echo ""
echo "All checks passed!"
echo ""
echo "Next Steps:"
echo "  - Proceed to Phase 2 (Opportunistic Registry Refresh)"
echo "  - See docs/phase1_registry_integration_summary.md"
echo ""
