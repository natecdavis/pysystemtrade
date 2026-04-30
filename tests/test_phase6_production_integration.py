"""
Test Phase 6: Production Integration

Tests config validation and hard invariant enforcement for 541-perp registry integration.
"""

import pytest
import pandas as pd
import json
import yaml
import sys
import tempfile
from pathlib import Path
from datetime import datetime

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.validate_config import validate_registry_config
import logging

logger = logging.getLogger(__name__)


@pytest.fixture
def temp_env_root(tmp_path):
    """Create temporary environment structure."""
    env_root = tmp_path / 'test_env'
    metadata_dir = env_root / 'data/raw/metadata'
    metadata_dir.mkdir(parents=True, exist_ok=True)
    return env_root


@pytest.fixture
def sample_registry(temp_env_root):
    """Create sample registry file."""
    registry_data = {
        'candidate_instruments': [
            'BTCUSDT_PERP',
            'ETHUSDT_PERP',
            'SOLUSDT_PERP',
            'BNBUSDT_PERP',
            'XRPUSDT_PERP'
        ],
        'timestamp': datetime.utcnow().isoformat(),
        'total_count': 5
    }

    registry_path = temp_env_root / 'data/raw/metadata/discovered_candidate_instruments.json'
    with open(registry_path, 'w') as f:
        json.dump(registry_data, f, indent=2)

    return registry_path


@pytest.fixture
def sample_config_auto_discover():
    """Sample config with auto_discover enabled."""
    return {
        'data_acquisition': {
            'auto_discover': True
        },
        'universe': {
            'layer_a_instruments': [
                'BTCUSDT_PERP',
                'ETHUSDT_PERP',
                'SOLUSDT_PERP'
            ]
        },
        'dynamic_universe': {
            'top_k': 3,  # Equal to layer_a count (valid)
            'entry_buffer': 1,
            'exit_buffer': 2,
            'adv_window': 30,
            'min_history_days': 365,
            'max_sr_cost_per_trade': 0.01,
            'max_sr_cost_annual': 0.13
        }
    }


def test_validate_config_auto_discover_without_registry(temp_env_root, sample_config_auto_discover, tmp_path):
    """Test that auto_discover=true without registry produces error."""
    # Write config
    config_path = tmp_path / 'test_config.yaml'
    with open(config_path, 'w') as f:
        yaml.dump(sample_config_auto_discover, f)

    # Validate (registry doesn't exist yet)
    errors, warnings = validate_registry_config(config_path, temp_env_root)

    # Should error (no registry)
    assert len(errors) > 0
    assert any('registry not found' in err.lower() for err in errors)


def test_validate_config_auto_discover_with_registry(temp_env_root, sample_registry, sample_config_auto_discover, tmp_path):
    """Test that auto_discover=true with valid registry passes."""
    # Write config
    config_path = tmp_path / 'test_config.yaml'
    with open(config_path, 'w') as f:
        yaml.dump(sample_config_auto_discover, f)

    # Validate (registry exists)
    errors, warnings = validate_registry_config(config_path, temp_env_root)

    # Should pass (no errors)
    assert len(errors) == 0


def test_validate_config_empty_layer_a(temp_env_root, sample_registry, tmp_path):
    """Test that empty layer_a_instruments produces error."""
    config = {
        'data_acquisition': {
            'auto_discover': True
        },
        'universe': {
            'layer_a_instruments': []  # EMPTY
        }
    }

    config_path = tmp_path / 'test_config.yaml'
    with open(config_path, 'w') as f:
        yaml.dump(config, f)

    errors, warnings = validate_registry_config(config_path, temp_env_root)

    # Should error (empty layer_a)
    assert len(errors) > 0
    assert any('layer_a_instruments is empty' in err for err in errors)


def test_validate_config_top_k_exceeds_layer_a(temp_env_root, sample_registry, tmp_path):
    """Test that top_k > layer_a count produces error."""
    config = {
        'data_acquisition': {
            'auto_discover': True
        },
        'universe': {
            'layer_a_instruments': ['BTCUSDT_PERP', 'ETHUSDT_PERP']  # Only 2
        },
        'dynamic_universe': {
            'top_k': 30  # > 2
        }
    }

    config_path = tmp_path / 'test_config.yaml'
    with open(config_path, 'w') as f:
        yaml.dump(config, f)

    errors, warnings = validate_registry_config(config_path, temp_env_root)

    # Should error (top_k > layer_a count)
    assert len(errors) > 0
    assert any('top_k' in err and 'layer_a count' in err for err in errors)


def test_validate_config_instruments_not_in_registry(temp_env_root, sample_registry, tmp_path):
    """Test warning when layer_a instruments not in registry."""
    config = {
        'data_acquisition': {
            'auto_discover': True
        },
        'universe': {
            'layer_a_instruments': [
                'BTCUSDT_PERP',
                'ETHUSDT_PERP',
                'FAKECOINUSDT_PERP'  # NOT in registry
            ]
        }
    }

    config_path = tmp_path / 'test_config.yaml'
    with open(config_path, 'w') as f:
        yaml.dump(config, f)

    errors, warnings = validate_registry_config(config_path, temp_env_root)

    # Should warn (instrument not in registry)
    assert len(warnings) > 0
    assert any('not in registry' in warn for warn in warnings)


def test_validate_config_buffer_parameters(temp_env_root, sample_registry, tmp_path):
    """Test validation of entry/exit buffer parameters."""
    config = {
        'data_acquisition': {
            'auto_discover': True
        },
        'universe': {
            'layer_a_instruments': ['BTCUSDT_PERP', 'ETHUSDT_PERP', 'SOLUSDT_PERP']
        },
        'dynamic_universe': {
            'top_k': 3,
            'entry_buffer': 5,  # >= top_k (would make entry_threshold <= 0)
            'exit_buffer': 10
        }
    }

    config_path = tmp_path / 'test_config.yaml'
    with open(config_path, 'w') as f:
        yaml.dump(config, f)

    errors, warnings = validate_registry_config(config_path, temp_env_root)

    # Should warn (entry_buffer >= top_k)
    assert len(warnings) > 0
    assert any('entry_buffer' in warn for warn in warnings)


def test_validate_config_cost_filter_parameters(temp_env_root, sample_registry, sample_config_auto_discover, tmp_path):
    """Test validation of cost filter parameters."""
    config = sample_config_auto_discover.copy()
    config['dynamic_universe']['max_sr_cost_per_trade'] = 0.10  # Very high

    config_path = tmp_path / 'test_config.yaml'
    with open(config_path, 'w') as f:
        yaml.dump(config, f)

    errors, warnings = validate_registry_config(config_path, temp_env_root)

    # Should warn (cost threshold too high)
    assert len(warnings) > 0
    assert any('max_sr_cost_per_trade' in warn for warn in warnings)


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
