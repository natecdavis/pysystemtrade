#!/usr/bin/env bash
set -euo pipefail

DATASET="data/dataset_538registry_6yr_jagged.parquet"
MANIFEST="data/dataset_538registry_6yr_jagged.manifest.json"

echo "Checking manifest consistency..."

python3 -c "
import pandas as pd
import json
import sys

df = pd.read_parquet('$DATASET')
dataset_instruments = set(df['instrument'].unique())

with open('$MANIFEST') as f:
    manifest = json.load(f)
manifest_instruments = set(manifest['instruments']['included'].keys())

if dataset_instruments != manifest_instruments:
    diff = dataset_instruments ^ manifest_instruments
    print(f'FAIL: Manifest ↔ dataset mismatch: {diff}', file=sys.stderr)
    sys.exit(1)

print(f'✓ Manifest consistency: {len(dataset_instruments)} instruments')
"
