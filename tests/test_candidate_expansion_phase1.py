"""
Test candidate expansion Phase 1: Data acquisition decoupling.
"""
import pytest
from pathlib import Path
import yaml
from sysdata.crypto.config_helpers import (
    extract_candidate_instruments,
    extract_tradable_instruments,
    instrument_id_to_symbol,
    symbol_to_instrument_id
)


def test_data_acquisition_priority():
    """Verify data_acquisition.candidate_instruments takes priority over universe."""
    config = {
        'data_acquisition': {
            'candidate_instruments': ['BTCUSDT_PERP', 'ETHUSDT_PERP', 'XRPUSDT_PERP']
        },
        'universe': {
            'layer_a_instruments': ['BTCUSDT_PERP', 'ETHUSDT_PERP']
        }
    }

    candidate_ids = extract_candidate_instruments(config)

    assert len(candidate_ids) == 3
    assert 'XRPUSDT_PERP' in candidate_ids


def test_backward_compatibility_fallback():
    """Verify fallback to universe.layer_a_instruments when data_acquisition missing."""
    config = {
        'universe': {
            'layer_a_instruments': ['BTCUSDT_PERP', 'ETHUSDT_PERP']
        }
    }

    candidate_ids = extract_candidate_instruments(config)

    assert len(candidate_ids) == 2
    assert candidate_ids == ['BTCUSDT_PERP', 'ETHUSDT_PERP']


def test_empty_candidate_list_fails_fast():
    """Verify empty candidate list raises ValueError (fail fast)."""
    config = {
        'data_acquisition': {
            'candidate_instruments': []
        },
        'universe': {
            'layer_a_instruments': ['BTCUSDT_PERP']
        }
    }

    with pytest.raises(ValueError, match="present but empty"):
        extract_candidate_instruments(config)


def test_tradable_instruments_ignores_candidate_list():
    """Verify tradable universe is ALWAYS from universe.layer_a_instruments."""
    config = {
        'data_acquisition': {
            'candidate_instruments': ['BTCUSDT_PERP', 'ETHUSDT_PERP', 'XRPUSDT_PERP']
        },
        'universe': {
            'layer_a_instruments': ['BTCUSDT_PERP', 'ETHUSDT_PERP']
        }
    }

    tradable_ids = extract_tradable_instruments(config)

    # Should use universe.layer_a_instruments ONLY
    assert len(tradable_ids) == 2
    assert tradable_ids == ['BTCUSDT_PERP', 'ETHUSDT_PERP']


def test_instrument_id_to_symbol_mapping():
    """Verify canonical instrument ID to symbol mapping."""
    assert instrument_id_to_symbol('BTCUSDT_PERP') == 'BTCUSDT'
    assert instrument_id_to_symbol('ETHUSDT_PERP') == 'ETHUSDT'
    assert instrument_id_to_symbol('XRPUSDT') == 'XRPUSDT'  # Already without suffix


def test_symbol_to_instrument_id_mapping():
    """Verify canonical symbol to instrument ID mapping."""
    assert symbol_to_instrument_id('BTCUSDT') == 'BTCUSDT_PERP'
    assert symbol_to_instrument_id('ETHUSDT') == 'ETHUSDT_PERP'
    assert symbol_to_instrument_id('XRPUSDT_PERP') == 'XRPUSDT_PERP'  # Already with suffix


def test_missing_both_sections_fails():
    """Verify error when both data_acquisition and universe are missing."""
    config = {}

    with pytest.raises(ValueError, match="No instruments found"):
        extract_candidate_instruments(config)


def test_empty_universe_fallback_fails():
    """Verify error when universe.layer_a_instruments is empty (fallback case)."""
    config = {
        'universe': {
            'layer_a_instruments': []
        }
    }

    with pytest.raises(ValueError, match="No instruments found"):
        extract_candidate_instruments(config)


def test_real_config_20_candidates():
    """Test against actual test_candidate_20_instruments.yaml config."""
    config_path = Path(__file__).parent.parent / "config" / "test_candidate_20_instruments.yaml"

    if not config_path.exists():
        pytest.skip(f"Config file not found: {config_path}")

    with open(config_path) as f:
        config = yaml.safe_load(f)

    candidate_ids = extract_candidate_instruments(config)
    tradable_ids = extract_tradable_instruments(config)

    # Should have 20 candidates
    assert len(candidate_ids) == 20

    # Should have 5 tradable
    assert len(tradable_ids) == 5

    # Tradable should be subset of candidates
    assert set(tradable_ids).issubset(set(candidate_ids))

    # Verify specific instruments
    assert 'BTCUSDT_PERP' in candidate_ids
    assert 'BTCUSDT_PERP' in tradable_ids
    assert 'SANDUSDT_PERP' in candidate_ids
    assert 'SANDUSDT_PERP' not in tradable_ids


def test_real_config_backward_compat():
    """Test against actual test_backward_compat.yaml config."""
    config_path = Path(__file__).parent.parent / "config" / "test_backward_compat.yaml"

    if not config_path.exists():
        pytest.skip(f"Config file not found: {config_path}")

    with open(config_path) as f:
        config = yaml.safe_load(f)

    candidate_ids = extract_candidate_instruments(config)
    tradable_ids = extract_tradable_instruments(config)

    # Should fallback to universe.layer_a_instruments
    assert len(candidate_ids) == 2
    assert len(tradable_ids) == 2

    # Should be identical in backward compat case
    assert candidate_ids == tradable_ids

    # Verify specific instruments
    assert 'BTCUSDT_PERP' in candidate_ids
    assert 'ETHUSDT_PERP' in candidate_ids
