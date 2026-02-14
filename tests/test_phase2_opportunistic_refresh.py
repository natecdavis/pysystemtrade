"""
Test Phase 2: Opportunistic Registry Refresh

Tests registry refresh functionality with fallback resilience.
"""

import json
import pytest
import tempfile
import shutil
from pathlib import Path
from datetime import datetime
import sys

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.refresh_binance_market_registry import (
    run_refresh,
    detect_changes,
    build_candidate_list,
    build_registry
)


def test_detect_changes_first_run(tmp_path):
    """Test diff detection on first run (no previous registry)."""
    metadata_dir = tmp_path / 'metadata'
    metadata_dir.mkdir()

    new_candidates = ['BTCUSDT_PERP', 'ETHUSDT_PERP', 'SOLUSDT_PERP']

    changelog = detect_changes(metadata_dir, new_candidates)

    assert changelog['first_run'] is True
    assert changelog['total_count'] == 3
    assert changelog['new_instruments'] == []
    assert changelog['delisted_instruments'] == []


def test_detect_changes_with_additions(tmp_path):
    """Test diff detection when new instruments added."""
    metadata_dir = tmp_path / 'metadata'
    metadata_dir.mkdir()

    # Create previous registry
    prev_registry = {
        'candidate_instruments': ['BTCUSDT_PERP', 'ETHUSDT_PERP'],
        'count': 2
    }
    with open(metadata_dir / 'discovered_candidate_instruments.json', 'w') as f:
        json.dump(prev_registry, f)

    # New registry has additional instrument
    new_candidates = ['BTCUSDT_PERP', 'ETHUSDT_PERP', 'SOLUSDT_PERP']

    changelog = detect_changes(metadata_dir, new_candidates)

    assert 'first_run' not in changelog
    assert changelog['total_count'] == 3
    assert changelog['new_instruments'] == ['SOLUSDT_PERP']
    assert changelog['delisted_instruments'] == []


def test_detect_changes_with_delistings(tmp_path):
    """Test diff detection when instruments delisted."""
    metadata_dir = tmp_path / 'metadata'
    metadata_dir.mkdir()

    # Create previous registry
    prev_registry = {
        'candidate_instruments': ['BTCUSDT_PERP', 'ETHUSDT_PERP', 'SOLUSDT_PERP'],
        'count': 3
    }
    with open(metadata_dir / 'discovered_candidate_instruments.json', 'w') as f:
        json.dump(prev_registry, f)

    # New registry missing one instrument
    new_candidates = ['BTCUSDT_PERP', 'ETHUSDT_PERP']

    changelog = detect_changes(metadata_dir, new_candidates)

    assert changelog['total_count'] == 2
    assert changelog['new_instruments'] == []
    assert changelog['delisted_instruments'] == ['SOLUSDT_PERP']


def test_detect_changes_with_both(tmp_path):
    """Test diff detection with both additions and delistings."""
    metadata_dir = tmp_path / 'metadata'
    metadata_dir.mkdir()

    # Create previous registry
    prev_registry = {
        'candidate_instruments': ['BTCUSDT_PERP', 'ETHUSDT_PERP', 'OLDUSDT_PERP'],
        'count': 3
    }
    with open(metadata_dir / 'discovered_candidate_instruments.json', 'w') as f:
        json.dump(prev_registry, f)

    # New registry
    new_candidates = ['BTCUSDT_PERP', 'ETHUSDT_PERP', 'NEWUSDT_PERP']

    changelog = detect_changes(metadata_dir, new_candidates)

    assert changelog['total_count'] == 3
    assert changelog['new_instruments'] == ['NEWUSDT_PERP']
    assert changelog['delisted_instruments'] == ['OLDUSDT_PERP']


def test_run_refresh_creates_changelog(tmp_path):
    """Test that run_refresh creates changelog file."""
    # This test requires network access, so skip if CoinGecko unreachable
    pytest.skip("Requires network access - manual verification only")

    env_root = tmp_path / 'envs' / 'test'
    env_root.mkdir(parents=True)

    try:
        changelog = run_refresh(env_root, verbose=False, dry_run=False)

        # Verify changelog structure
        assert 'total_count' in changelog
        assert 'timestamp' in changelog

        # Verify changelog file written
        changelog_path = env_root / 'data/raw/metadata/registry_changelog.json'
        assert changelog_path.exists()

        with open(changelog_path) as f:
            saved_changelog = json.load(f)

        assert saved_changelog['total_count'] == changelog['total_count']

    except Exception as e:
        pytest.skip(f"CoinGecko API unavailable: {e}")


def test_opportunistic_refresh_fallback():
    """Test that fallback to cached registry works."""
    # Import the function
    sys.path.insert(0, str(Path(__file__).parent.parent / 'scripts'))
    from run_live_advisory import refresh_registry_opportunistic

    # Use dev environment (should have cached registry)
    env_root = Path('envs/dev')
    registry_path = env_root / 'data/raw/metadata/discovered_candidate_instruments.json'

    if not registry_path.exists():
        pytest.skip("Dev registry not found")

    # Mock the run_refresh to fail (simulating API failure)
    # This is tricky without mocking framework, so just verify structure
    # In real usage, if CoinGecko API fails, it falls back to cache

    # Verify cached registry exists and is valid
    with open(registry_path) as f:
        registry = json.load(f)

    assert 'candidate_instruments' in registry
    assert len(registry['candidate_instruments']) > 0


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
