"""
Test Phase 1: Registry-Aware Advisory Workflow

Tests that the advisory workflow correctly uses registry-based discovery.
"""

import json
import yaml
import pytest
from pathlib import Path

from sysdata.crypto.config_helpers import extract_candidate_instruments_with_registry


def test_registry_aware_extraction():
    """Test that registry-aware extraction works with auto_discover."""
    # Load test config with auto_discover
    config_path = Path('config/test_auto_discover.yaml')

    if not config_path.exists():
        pytest.skip("Test config not found")

    with open(config_path) as f:
        config = yaml.safe_load(f)

    # Test with dev environment
    env_root = Path('envs/dev')
    registry_path = env_root / 'data/raw/metadata/discovered_candidate_instruments.json'

    if not registry_path.exists():
        pytest.skip("Registry not found")

    # Extract candidates
    candidates, source = extract_candidate_instruments_with_registry(config, env_root)

    # Verify we got registry candidates
    assert 'discovered_candidate_instruments.json' in source
    assert len(candidates) == 541  # Expected registry size

    # Verify all are instrument IDs (end with _PERP)
    assert all(c.endswith('_PERP') for c in candidates)


def test_fallback_to_layer_a():
    """Test fallback to layer_a when auto_discover is false."""
    config = {
        'data_acquisition': {
            'auto_discover': False
        },
        'universe': {
            'layer_a_instruments': ['BTCUSDT_PERP', 'ETHUSDT_PERP']
        }
    }

    candidates, source = extract_candidate_instruments_with_registry(config, None)

    assert 'fallback' in source
    assert candidates == ['BTCUSDT_PERP', 'ETHUSDT_PERP']


def test_explicit_config_takes_priority():
    """Test that explicit candidate_instruments takes priority over registry."""
    config = {
        'data_acquisition': {
            'auto_discover': True,  # This should be ignored
            'candidate_instruments': ['BTCUSDT_PERP', 'ETHUSDT_PERP', 'SOLUSDT_PERP']
        },
        'universe': {
            'layer_a_instruments': ['BTCUSDT_PERP']
        }
    }

    env_root = Path('envs/dev')
    candidates, source = extract_candidate_instruments_with_registry(config, env_root)

    # Explicit config should take priority
    assert 'data_acquisition.candidate_instruments' in source
    assert len(candidates) == 3
    assert candidates == ['BTCUSDT_PERP', 'ETHUSDT_PERP', 'SOLUSDT_PERP']


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
