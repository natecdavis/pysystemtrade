"""
Tests for Binance perpetual futures discovery system.

Tests the automatic discovery, registry management, and config integration.
"""
import json
import pytest
from pathlib import Path
from datetime import datetime, timezone
from scripts.refresh_binance_market_registry import (
    filter_binance_usdt_perpetuals,
    normalize_symbol_info,
    build_registry,
    build_candidate_list,
)


# Fixtures
@pytest.fixture
def mock_coingecko_derivatives():
    """Mock CoinGecko derivatives API response."""
    return [
        {
            "market": "Binance (Futures)",
            "symbol": "BTCUSDT",
            "contract_type": "perpetual",
            "price": "43250.5",
            "volume_24h": 9876543210,
            "open_interest": 1234567890,
            "funding_rate": 0.0001,
            "last_traded_at": 1739293847,
            "expired_at": None,
        },
        {
            "market": "Binance (Futures)",
            "symbol": "ETHUSDT",
            "contract_type": "perpetual",
            "price": "2250.8",
            "volume_24h": 5432109876,
            "open_interest": 987654321,
            "funding_rate": 0.00008,
            "last_traded_at": 1739293847,
            "expired_at": None,
        },
        {
            "market": "Binance (Futures)",
            "symbol": "BTCUSD",  # Not USDT
            "contract_type": "perpetual",
            "price": "43250.0",
            "volume_24h": 1000000,
            "expired_at": None,
        },
        {
            "market": "Bybit",  # Different exchange
            "symbol": "BTCUSDT",
            "contract_type": "perpetual",
            "price": "43251.0",
            "volume_24h": 5000000000,
            "expired_at": None,
        },
        {
            "market": "Binance (Futures)",
            "symbol": "LUNAUSDT",  # Expired (delisted)
            "contract_type": "perpetual",
            "expired_at": 1652313600,
        },
    ]


# Tests
def test_filter_binance_usdt_perpetuals(mock_coingecko_derivatives):
    """Test filtering to Binance USDT perpetuals."""
    filtered = filter_binance_usdt_perpetuals(mock_coingecko_derivatives)

    assert len(filtered) == 2  # Only BTCUSDT and ETHUSDT
    assert all(d["market"] == "Binance (Futures)" for d in filtered)
    assert all("USDT" in d["symbol"] for d in filtered)
    assert all(d["contract_type"] == "perpetual" for d in filtered)
    assert all(d["expired_at"] is None for d in filtered)


def test_normalize_symbol_info():
    """Test symbol normalization."""
    raw = {
        "market": "Binance (Futures)",
        "symbol": "BTCUSDT",
        "contract_type": "perpetual",
        "volume_24h": 9876543210,
        "open_interest": 1234567890,
        "funding_rate": 0.0001,
        "last_traded_at": 1739293847,
    }

    normalized = normalize_symbol_info(raw)

    assert normalized["symbol"] == "BTCUSDT"
    assert normalized["status"] == "ACTIVE"
    assert normalized["base_asset"] == "BTC"
    assert normalized["quote_asset"] == "USDT"
    assert normalized["volume_24h"] == 9876543210


def test_build_registry(mock_coingecko_derivatives):
    """Test registry construction."""
    derivatives = filter_binance_usdt_perpetuals(mock_coingecko_derivatives)
    registry = build_registry(derivatives)

    assert "instruments" in registry
    assert "BTCUSDT" in registry["instruments"]
    assert "ETHUSDT" in registry["instruments"]
    assert registry["summary"]["total_instruments"] == 2


def test_build_candidate_list(mock_coingecko_derivatives):
    """Test candidate list with _PERP suffix."""
    derivatives = filter_binance_usdt_perpetuals(mock_coingecko_derivatives)
    registry = build_registry(derivatives)
    candidate_list = build_candidate_list(registry)

    assert candidate_list["count"] == 2
    assert "BTCUSDT_PERP" in candidate_list["candidate_instruments"]
    assert "ETHUSDT_PERP" in candidate_list["candidate_instruments"]


def test_precedence_explicit_config(tmp_path):
    """Test precedence: explicit config wins."""
    from sysdata.crypto.config_helpers import extract_candidate_instruments_with_registry

    config = {
        "data_acquisition": {
            "candidate_instruments": ["BTCUSDT_PERP", "ETHUSDT_PERP"],
            "auto_discover": True,  # Should be ignored
        }
    }

    instruments, source = extract_candidate_instruments_with_registry(config, tmp_path)

    assert instruments == ["BTCUSDT_PERP", "ETHUSDT_PERP"]
    assert source == "config.data_acquisition.candidate_instruments"


def test_precedence_registry_fallback(tmp_path):
    """Test precedence: registry if auto_discover=true."""
    from sysdata.crypto.config_helpers import extract_candidate_instruments_with_registry

    # Create mock registry
    registry_dir = tmp_path / "data/raw/metadata"
    registry_dir.mkdir(parents=True)

    registry_file = registry_dir / "discovered_candidate_instruments.json"
    registry_file.write_text(json.dumps({
        "candidate_instruments": ["BTCUSDT_PERP", "ETHUSDT_PERP", "BNBUSDT_PERP"],
        "count": 3,
    }))

    config = {
        "data_acquisition": {
            "auto_discover": True,
        },
        "universe": {
            "layer_a_instruments": ["BTCUSDT_PERP"],  # Should not be used
        }
    }

    instruments, source = extract_candidate_instruments_with_registry(config, tmp_path)

    assert len(instruments) == 3
    assert "BNBUSDT_PERP" in instruments
    assert source == "discovered_candidate_instruments.json"


def test_precedence_universe_fallback(tmp_path):
    """Test precedence: universe.layer_a_instruments as last resort."""
    from sysdata.crypto.config_helpers import extract_candidate_instruments_with_registry

    config = {
        "universe": {
            "layer_a_instruments": ["BTCUSDT_PERP", "ETHUSDT_PERP"],
        }
    }

    instruments, source = extract_candidate_instruments_with_registry(config, tmp_path)

    assert instruments == ["BTCUSDT_PERP", "ETHUSDT_PERP"]
    assert source == "universe.layer_a_instruments (fallback)"


def test_backward_compatibility():
    """Test that existing code still works."""
    from sysdata.crypto.config_helpers import extract_candidate_instruments

    # Without data_acquisition section
    config = {
        "universe": {
            "layer_a_instruments": ["BTCUSDT_PERP", "ETHUSDT_PERP"],
        }
    }

    instruments = extract_candidate_instruments(config)
    assert instruments == ["BTCUSDT_PERP", "ETHUSDT_PERP"]

    # With data_acquisition section
    config = {
        "data_acquisition": {
            "candidate_instruments": ["BTCUSDT_PERP"],
        }
    }

    instruments = extract_candidate_instruments(config)
    assert instruments == ["BTCUSDT_PERP"]


def test_registry_missing_with_autodiscover(tmp_path):
    """Test fallback when registry missing but auto_discover=true."""
    from sysdata.crypto.config_helpers import extract_candidate_instruments_with_registry

    config = {
        "data_acquisition": {
            "auto_discover": True,
        },
        "universe": {
            "layer_a_instruments": ["BTCUSDT_PERP", "ETHUSDT_PERP"],
        }
    }

    # Registry doesn't exist, should fall back to universe
    instruments, source = extract_candidate_instruments_with_registry(config, tmp_path)

    assert instruments == ["BTCUSDT_PERP", "ETHUSDT_PERP"]
    assert source == "universe.layer_a_instruments (fallback)"


def test_empty_config_error():
    """Test error when no instruments found in any source."""
    from sysdata.crypto.config_helpers import extract_candidate_instruments_with_registry

    config = {
        "universe": {
            "layer_a_instruments": [],
        }
    }

    with pytest.raises(ValueError, match="No instruments found"):
        extract_candidate_instruments_with_registry(config, None)


def test_empty_candidate_list_error():
    """Test error when candidate_instruments present but empty."""
    from sysdata.crypto.config_helpers import extract_candidate_instruments_with_registry

    config = {
        "data_acquisition": {
            "candidate_instruments": [],
        }
    }

    with pytest.raises(ValueError, match="present but empty"):
        extract_candidate_instruments_with_registry(config, None)
