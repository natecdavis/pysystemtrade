"""
Test Phase 3: Lifecycle from Vision Data Coverage

Tests lifecycle derivation from Vision data availability.
"""

import json
import pytest
import pandas as pd
import tempfile
from pathlib import Path
from datetime import datetime, timedelta
import sys

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.build_example_dataset import derive_lifecycle_from_vision_data
from sysdata.crypto.dynamic_universe import (
    load_lifecycle_from_manifest,
    check_lifecycle_eligibility
)


def test_derive_lifecycle_basic():
    """Test basic lifecycle derivation from dataset."""
    # Create sample dataset
    dates = pd.date_range('2024-01-01', '2024-01-10', freq='D')
    data = []

    for date in dates:
        data.append({
            'date': date,
            'instrument': 'BTCUSDT_PERP',
            'close': 50000 + (date.day * 100)
        })

    df = pd.DataFrame(data)

    # Derive lifecycle
    lifecycle = derive_lifecycle_from_vision_data(df, stale_threshold_days=7)

    # Verify structure
    assert 'BTCUSDT_PERP' in lifecycle
    btc_lc = lifecycle['BTCUSDT_PERP']

    assert btc_lc['first_data_date'] == '2024-01-01'
    assert btc_lc['last_data_date'] == '2024-01-10'
    assert btc_lc['data_days'] == 10
    assert btc_lc['status'] in ['ACTIVE', 'STALE']
    assert 'days_since_last' in btc_lc


def test_derive_lifecycle_stale():
    """Test lifecycle marks old data as STALE."""
    # Create dataset with old data (more than 7 days ago)
    old_date = datetime.utcnow().date() - timedelta(days=30)
    dates = pd.date_range(old_date, periods=10, freq='D')

    data = []
    for date in dates:
        data.append({
            'date': date,
            'instrument': 'OLDCOINUSDT_PERP',
            'close': 1.0
        })

    df = pd.DataFrame(data)

    # Derive lifecycle
    lifecycle = derive_lifecycle_from_vision_data(df, stale_threshold_days=7)

    # Should be marked as STALE
    assert lifecycle['OLDCOINUSDT_PERP']['status'] == 'STALE'
    assert lifecycle['OLDCOINUSDT_PERP']['days_since_last'] > 7


def test_derive_lifecycle_active():
    """Test lifecycle marks recent data as ACTIVE."""
    # Create dataset with recent data
    recent_date = datetime.utcnow().date() - timedelta(days=1)
    dates = pd.date_range(recent_date, periods=10, freq='D')

    data = []
    for date in dates:
        data.append({
            'date': date,
            'instrument': 'ACTIVECOINUSDT_PERP',
            'close': 100.0
        })

    df = pd.DataFrame(data)

    # Derive lifecycle
    lifecycle = derive_lifecycle_from_vision_data(df, stale_threshold_days=7)

    # Should be marked as ACTIVE
    assert lifecycle['ACTIVECOINUSDT_PERP']['status'] == 'ACTIVE'
    assert lifecycle['ACTIVECOINUSDT_PERP']['days_since_last'] <= 7


def test_derive_lifecycle_multiple_instruments():
    """Test lifecycle with multiple instruments."""
    dates = pd.date_range('2024-01-01', '2024-01-10', freq='D')
    data = []

    instruments = ['BTCUSDT_PERP', 'ETHUSDT_PERP', 'SOLUSDT_PERP']

    for date in dates:
        for inst in instruments:
            data.append({
                'date': date,
                'instrument': inst,
                'close': 1000.0
            })

    df = pd.DataFrame(data)

    # Derive lifecycle
    lifecycle = derive_lifecycle_from_vision_data(df)

    # All three instruments should be present
    assert len(lifecycle) == 3
    for inst in instruments:
        assert inst in lifecycle
        assert lifecycle[inst]['data_days'] == 10


def test_load_lifecycle_from_manifest(tmp_path):
    """Test loading lifecycle from manifest file."""
    # Create sample manifest
    manifest = {
        'lifecycle': {
            'BTCUSDT_PERP': {
                'first_data_date': '2019-09-08',
                'last_data_date': '2026-02-13',
                'data_days': 2350,
                'status': 'ACTIVE',
                'days_since_last': 1
            },
            'ETHUSDT_PERP': {
                'first_data_date': '2020-01-01',
                'last_data_date': '2026-02-13',
                'data_days': 2235,
                'status': 'ACTIVE',
                'days_since_last': 1
            }
        },
        'lifecycle_summary': {
            'active': 2,
            'stale': 0,
            'no_data': 0
        }
    }

    manifest_path = tmp_path / 'test_manifest.json'
    with open(manifest_path, 'w') as f:
        json.dump(manifest, f)

    # Load lifecycle
    lifecycle = load_lifecycle_from_manifest(manifest_path)

    # Verify
    assert len(lifecycle) == 2
    assert 'BTCUSDT_PERP' in lifecycle
    assert lifecycle['BTCUSDT_PERP']['status'] == 'ACTIVE'


def test_load_lifecycle_missing_manifest():
    """Test loading lifecycle from non-existent manifest."""
    lifecycle = load_lifecycle_from_manifest('/nonexistent/path.json')

    # Should return empty dict
    assert lifecycle == {}


def test_check_lifecycle_eligibility():
    """Test lifecycle eligibility checking."""
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
        },
        'NOCOINUSDT_PERP': {
            'status': 'NO_DATA'
        }
    }

    # Test within coverage window
    assert check_lifecycle_eligibility('BTCUSDT_PERP', pd.Timestamp('2024-01-01'), lifecycle_data) == True

    # Test before launch
    assert check_lifecycle_eligibility('BTCUSDT_PERP', pd.Timestamp('2019-01-01'), lifecycle_data) == False

    # Test after delisting
    assert check_lifecycle_eligibility('OLDCOINUSDT_PERP', pd.Timestamp('2024-01-01'), lifecycle_data) == False

    # Test NO_DATA status
    assert check_lifecycle_eligibility('NOCOINUSDT_PERP', pd.Timestamp('2024-01-01'), lifecycle_data) == False

    # Test unknown instrument (conservative fallback = True)
    assert check_lifecycle_eligibility('UNKNOWNUSDT_PERP', pd.Timestamp('2024-01-01'), lifecycle_data) == True


def test_lifecycle_eligibility_edge_cases():
    """Test lifecycle eligibility edge cases."""
    lifecycle_data = {
        'EXACTUSDT_PERP': {
            'first_data_date': '2024-01-01',
            'last_data_date': '2024-01-31',
            'status': 'ACTIVE'
        }
    }

    # Test exact boundaries
    assert check_lifecycle_eligibility('EXACTUSDT_PERP', pd.Timestamp('2024-01-01'), lifecycle_data) == True
    assert check_lifecycle_eligibility('EXACTUSDT_PERP', pd.Timestamp('2024-01-31'), lifecycle_data) == True

    # Test just outside boundaries
    assert check_lifecycle_eligibility('EXACTUSDT_PERP', pd.Timestamp('2023-12-31'), lifecycle_data) == False
    assert check_lifecycle_eligibility('EXACTUSDT_PERP', pd.Timestamp('2024-02-01'), lifecycle_data) == False


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
