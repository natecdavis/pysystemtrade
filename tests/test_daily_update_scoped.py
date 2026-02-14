"""
Unit tests for daily update scoped extraction and atomic patching.
"""
import pytest
import pandas as pd
import tempfile
import os
from pathlib import Path
from datetime import datetime, timedelta

# Import functions to test
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.update_data_daily import (
    extract_symbols_by_scope,
    validate_tail_patch_schema,
    patch_tail_atomically
)


def test_scope_prod():
    """Test prod scope extracts only tradable instruments."""
    config = {
        'universe': {
            'layer_a_instruments': ['BTCUSDT_PERP', 'ETHUSDT_PERP']
        },
        'data_acquisition': {
            'candidate_instruments': ['BTCUSDT_PERP', 'ETHUSDT_PERP', 'SOLUSDT_PERP']
        }
    }
    symbols, strict = extract_symbols_by_scope(config, scope='prod')
    assert symbols == ['BTCUSDT', 'ETHUSDT']
    assert strict is True


def test_scope_explicit_candidates():
    """Test explicit_candidates scope uses data_acquisition list."""
    config = {
        'universe': {
            'layer_a_instruments': ['BTCUSDT_PERP']
        },
        'data_acquisition': {
            'candidate_instruments': ['BTCUSDT_PERP', 'ETHUSDT_PERP', 'SOLUSDT_PERP']
        }
    }
    symbols, strict = extract_symbols_by_scope(config, scope='explicit_candidates')
    assert symbols == ['BTCUSDT', 'ETHUSDT', 'SOLUSDT']
    assert strict is True


def test_scope_explicit_candidates_missing():
    """Test explicit_candidates raises error if config missing."""
    config = {
        'universe': {
            'layer_a_instruments': ['BTCUSDT_PERP']
        }
    }
    with pytest.raises(ValueError, match="requires data_acquisition.candidate_instruments"):
        extract_symbols_by_scope(config, scope='explicit_candidates')


def test_scope_registry_all_fallback():
    """Test registry_all falls back to layer_a when no registry."""
    config = {
        'universe': {
            'layer_a_instruments': ['BTCUSDT_PERP', 'ETHUSDT_PERP']
        },
        'data_acquisition': {
            'auto_discover': True
        }
    }
    # Without env_root or with missing registry, should fall back
    symbols, strict = extract_symbols_by_scope(config, scope='registry_all', env_root=None)
    assert symbols == ['BTCUSDT', 'ETHUSDT']
    assert strict is False


def test_scope_invalid():
    """Test invalid scope raises error."""
    config = {'universe': {'layer_a_instruments': ['BTCUSDT_PERP']}}
    with pytest.raises(ValueError, match="Invalid scope"):
        extract_symbols_by_scope(config, scope='invalid')


def test_schema_validation_pass():
    """Test schema validation passes for matching schemas."""
    existing = pd.DataFrame({
        'date': pd.date_range('2026-01-01', periods=5),
        'close': [100.0] * 5
    })
    new_rows = pd.DataFrame({
        'date': pd.date_range('2026-01-06', periods=3),
        'close': [101.0] * 3
    })
    # Should not raise
    validate_tail_patch_schema(existing, new_rows)


def test_schema_validation_fail_columns():
    """Test schema validation fails for column mismatch."""
    existing = pd.DataFrame({
        'date': pd.date_range('2026-01-01', periods=5),
        'close': [100.0] * 5
    })
    new_rows = pd.DataFrame({
        'date': pd.date_range('2026-01-06', periods=3),
        'price': [101.0] * 3
    })
    with pytest.raises(ValueError, match="Column mismatch"):
        validate_tail_patch_schema(existing, new_rows)


def test_schema_validation_fail_dtype():
    """Test schema validation fails for dtype mismatch."""
    existing = pd.DataFrame({
        'date': pd.date_range('2026-01-01', periods=5),
        'close': [100.0] * 5  # float64
    })
    new_rows = pd.DataFrame({
        'date': pd.date_range('2026-01-06', periods=3),
        'close': [101, 102, 103]  # int64
    })
    with pytest.raises(ValueError, match="Dtype mismatch"):
        validate_tail_patch_schema(existing, new_rows)


def test_atomic_patch_csv():
    """Test atomic tail patch replaces last N days for CSV."""
    # Create temp CSV with 10 days of data
    temp_fd, temp_path = tempfile.mkstemp(suffix='.csv')
    os.close(temp_fd)
    temp_path = Path(temp_path)

    existing = pd.DataFrame({
        'date': pd.date_range('2026-01-01', periods=10),
        'close': list(range(100, 110))
    })
    existing.to_csv(temp_path, index=False)

    # Patch last 3 days with new data
    new_rows = pd.DataFrame({
        'date': pd.date_range('2026-01-08', periods=3),
        'close': [200, 201, 202]
    })

    patch_tail_atomically(temp_path, new_rows, tail_days=3)

    # Verify result
    result = pd.read_csv(temp_path, parse_dates=['date'])
    assert len(result) == 10  # Still 10 rows
    assert result['close'].iloc[-3:].tolist() == [200, 201, 202]  # Last 3 patched
    assert result['close'].iloc[:7].tolist() == list(range(100, 107))  # First 7 unchanged

    # Cleanup
    os.remove(temp_path)


def test_atomic_patch_parquet():
    """Test atomic tail patch replaces last N days for Parquet."""
    # Create temp Parquet with 10 days of data
    temp_fd, temp_path = tempfile.mkstemp(suffix='.parquet')
    os.close(temp_fd)
    temp_path = Path(temp_path)

    existing = pd.DataFrame({
        'date': pd.date_range('2026-01-01', periods=10),
        'close': list(range(100, 110))
    })
    existing.to_parquet(temp_path, index=False)

    # Patch last 3 days with new data
    new_rows = pd.DataFrame({
        'date': pd.date_range('2026-01-08', periods=3),
        'close': [200, 201, 202]
    })

    patch_tail_atomically(temp_path, new_rows, tail_days=3)

    # Verify result
    result = pd.read_parquet(temp_path)
    assert len(result) == 10  # Still 10 rows
    assert result['close'].iloc[-3:].tolist() == [200, 201, 202]  # Last 3 patched
    assert result['close'].iloc[:7].tolist() == list(range(100, 107))  # First 7 unchanged

    # Cleanup
    os.remove(temp_path)


def test_atomic_patch_idempotent():
    """Test atomic patch is idempotent (can re-run same window safely)."""
    temp_fd, temp_path = tempfile.mkstemp(suffix='.csv')
    os.close(temp_fd)
    temp_path = Path(temp_path)

    existing = pd.DataFrame({
        'date': pd.date_range('2026-01-01', periods=10),
        'close': list(range(100, 110))
    })
    existing.to_csv(temp_path, index=False)

    # First patch
    new_rows = pd.DataFrame({
        'date': pd.date_range('2026-01-08', periods=3),
        'close': [200, 201, 202]
    })
    patch_tail_atomically(temp_path, new_rows, tail_days=3)

    # Second patch (same data)
    patch_tail_atomically(temp_path, new_rows, tail_days=3)

    # Should be identical result
    result = pd.read_csv(temp_path, parse_dates=['date'])
    assert len(result) == 10
    assert result['close'].iloc[-3:].tolist() == [200, 201, 202]

    # Cleanup
    os.remove(temp_path)


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
