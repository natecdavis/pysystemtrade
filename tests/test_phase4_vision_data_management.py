"""
Test Phase 4: Vision-First Data Management

Tests Vision bulk downloader workflow and VPN connectivity checks.
"""

import pytest
import json
from pathlib import Path
import sys

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.update_data_monthly import check_binance_api_connectivity
from scripts.download_vision_bulk import load_progress, save_progress


def test_binance_api_connectivity():
    """Test Binance API connectivity check."""
    # This test requires network access
    is_reachable, error = check_binance_api_connectivity(timeout=10)

    # Either should work (VPN or non-VPN region)
    # Just verify the function returns proper structure
    assert isinstance(is_reachable, bool)
    if not is_reachable:
        assert error is not None
        assert isinstance(error, str)


def test_vision_progress_tracking(tmp_path):
    """Test Vision download progress persistence."""
    env_root = tmp_path / 'test_env'
    env_root.mkdir()

    # Initially empty
    progress = load_progress(env_root)
    assert progress['completed'] == []
    assert progress['last_updated'] is None

    # Save progress
    completed = ['BTCUSDT_PERP', 'ETHUSDT_PERP']
    save_progress(env_root, completed)

    # Load again
    progress = load_progress(env_root)
    assert set(progress['completed']) == set(completed)
    assert progress['count'] == 2
    assert progress['last_updated'] is not None


def test_vision_progress_idempotency(tmp_path):
    """Test that saving progress multiple times is idempotent."""
    env_root = tmp_path / 'test_env'
    env_root.mkdir()

    completed = ['BTCUSDT_PERP']

    # Save twice
    save_progress(env_root, completed)
    save_progress(env_root, completed)

    # Should still have just one entry
    progress = load_progress(env_root)
    assert progress['completed'] == completed
    assert progress['count'] == 1


def test_vision_progress_incremental(tmp_path):
    """Test incremental progress updates."""
    env_root = tmp_path / 'test_env'
    env_root.mkdir()

    # Save initial progress
    completed = ['BTCUSDT_PERP', 'ETHUSDT_PERP']
    save_progress(env_root, completed)

    # Add more instruments
    completed.append('SOLUSDT_PERP')
    save_progress(env_root, completed)

    # Verify
    progress = load_progress(env_root)
    assert len(progress['completed']) == 3
    assert 'SOLUSDT_PERP' in progress['completed']


def test_vpn_check_structure():
    """Test VPN check returns correct structure."""
    is_reachable, error = check_binance_api_connectivity(timeout=5)

    # Verify return types
    assert isinstance(is_reachable, bool)

    if is_reachable:
        assert error is None
    else:
        assert error is not None
        assert isinstance(error, str)
        assert len(error) > 0


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
