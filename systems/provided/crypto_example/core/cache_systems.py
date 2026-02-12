"""
Cache Management for Portfolio Experiments
===========================================
Save and load account curves to avoid re-running expensive backtests (60-90 minutes).

Cache Structure:
    backtest_cache/
        carry_returns.pkl
        trend_static_returns.pkl
        trend_dynamic_returns.pkl
        btc_returns.pkl (for beta calculation)
        metadata.json (dates, parameters, etc.)
"""

import os
import pickle
import json
import pandas as pd
from datetime import datetime


# Cache directory
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(SCRIPT_DIR, 'backtest_cache')


def ensure_cache_dir():
    """Create cache directory if it doesn't exist."""
    os.makedirs(CACHE_DIR, exist_ok=True)


def get_cache_path(name: str) -> str:
    """Get full path for a cache file."""
    return os.path.join(CACHE_DIR, f"{name}.pkl")


def get_metadata_path() -> str:
    """Get path for metadata file."""
    return os.path.join(CACHE_DIR, 'metadata.json')


def save_returns(returns: pd.Series, name: str, metadata: dict = None):
    """
    Save returns to cache.

    Args:
        returns: Daily percentage returns series
        name: Cache key (e.g., 'carry_returns', 'trend_static_returns')
        metadata: Optional metadata dict (dates, parameters, etc.)
    """
    ensure_cache_dir()

    # Save returns
    cache_path = get_cache_path(name)
    with open(cache_path, 'wb') as f:
        pickle.dump(returns, f)

    print(f"✓ Cached {name} to {cache_path}")
    print(f"  Date range: {returns.index.min().date()} to {returns.index.max().date()}")
    print(f"  Days: {len(returns)}")

    # Save/update metadata
    if metadata is not None:
        save_metadata(name, metadata)


def load_returns(name: str) -> pd.Series:
    """
    Load returns from cache.

    Args:
        name: Cache key (e.g., 'carry_returns', 'trend_static_returns')

    Returns:
        pd.Series: Daily percentage returns

    Raises:
        FileNotFoundError: If cache doesn't exist
    """
    cache_path = get_cache_path(name)

    if not os.path.exists(cache_path):
        raise FileNotFoundError(f"Cache not found: {cache_path}")

    with open(cache_path, 'rb') as f:
        returns = pickle.load(f)

    print(f"✓ Loaded {name} from cache")
    print(f"  Date range: {returns.index.min().date()} to {returns.index.max().date()}")
    print(f"  Days: {len(returns)}")

    return returns


def cache_exists(name: str) -> bool:
    """Check if cache exists for a given name."""
    return os.path.exists(get_cache_path(name))


def save_metadata(name: str, metadata: dict):
    """
    Save or update metadata for a cached item.

    Args:
        name: Cache key
        metadata: Dict with metadata (dates, parameters, etc.)
    """
    ensure_cache_dir()
    metadata_path = get_metadata_path()

    # Load existing metadata
    if os.path.exists(metadata_path):
        with open(metadata_path, 'r') as f:
            all_metadata = json.load(f)
    else:
        all_metadata = {}

    # Update metadata for this cache item
    all_metadata[name] = {
        **metadata,
        'cached_at': datetime.now().isoformat()
    }

    # Save
    with open(metadata_path, 'w') as f:
        json.dump(all_metadata, f, indent=2)


def load_metadata(name: str = None) -> dict:
    """
    Load metadata.

    Args:
        name: Optional cache key. If None, returns all metadata.

    Returns:
        dict: Metadata for the specified cache key, or all metadata
    """
    metadata_path = get_metadata_path()

    if not os.path.exists(metadata_path):
        return {} if name is None else None

    with open(metadata_path, 'r') as f:
        all_metadata = json.load(f)

    if name is None:
        return all_metadata
    else:
        return all_metadata.get(name, None)


def clear_cache(name: str = None):
    """
    Clear cache files.

    Args:
        name: Optional cache key. If None, clears all cache.
    """
    if name is None:
        # Clear all cache
        if os.path.exists(CACHE_DIR):
            import shutil
            shutil.rmtree(CACHE_DIR)
            print(f"✓ Cleared all cache from {CACHE_DIR}")
    else:
        # Clear specific cache
        cache_path = get_cache_path(name)
        if os.path.exists(cache_path):
            os.remove(cache_path)
            print(f"✓ Cleared cache for {name}")

            # Remove from metadata
            metadata_path = get_metadata_path()
            if os.path.exists(metadata_path):
                with open(metadata_path, 'r') as f:
                    all_metadata = json.load(f)
                if name in all_metadata:
                    del all_metadata[name]
                    with open(metadata_path, 'w') as f:
                        json.dump(all_metadata, f, indent=2)


def list_cache() -> list:
    """
    List all cached items.

    Returns:
        list: List of cache keys
    """
    if not os.path.exists(CACHE_DIR):
        return []

    cache_files = [f for f in os.listdir(CACHE_DIR) if f.endswith('.pkl')]
    return [f.replace('.pkl', '') for f in cache_files]


def print_cache_summary():
    """Print summary of cached items."""
    cached_items = list_cache()

    if len(cached_items) == 0:
        print("No cached items found")
        return

    print("=" * 90)
    print("CACHE SUMMARY")
    print("=" * 90)

    metadata = load_metadata()

    for name in sorted(cached_items):
        print(f"\n✓ {name}")

        # Try to load returns to get date range
        try:
            returns = load_returns(name)
            print(f"  Date range: {returns.index.min().date()} to {returns.index.max().date()}")
            print(f"  Days: {len(returns)}")

            # Show metadata if available
            item_meta = metadata.get(name, {})
            if 'cached_at' in item_meta:
                print(f"  Cached at: {item_meta['cached_at']}")

        except Exception as e:
            print(f"  Error loading: {e}")

    print("\n" + "=" * 90)


# =============================================================================
# MAIN (for testing)
# =============================================================================

if __name__ == "__main__":
    print("=" * 90)
    print("TESTING CACHE SYSTEM")
    print("=" * 90)

    # Create dummy returns
    dates = pd.date_range('2020-01-01', '2025-12-31', freq='D')
    import numpy as np
    np.random.seed(42)
    test_returns = pd.Series(
        np.random.normal(0.001, 0.013, len(dates)),
        index=dates
    )

    # Test save
    print("\n[1] Testing save...")
    save_returns(
        test_returns,
        'test_strategy',
        metadata={
            'strategy': 'test',
            'vol_target': 0.25,
            'start_date': str(test_returns.index.min().date())
        }
    )

    # Test load
    print("\n[2] Testing load...")
    loaded = load_returns('test_strategy')
    assert len(loaded) == len(test_returns), "Loaded returns length mismatch"
    assert (loaded == test_returns).all(), "Loaded returns don't match original"
    print("✓ Load successful, data matches")

    # Test exists
    print("\n[3] Testing cache_exists...")
    assert cache_exists('test_strategy'), "cache_exists failed"
    assert not cache_exists('nonexistent'), "cache_exists false positive"
    print("✓ cache_exists working correctly")

    # Test metadata
    print("\n[4] Testing metadata...")
    meta = load_metadata('test_strategy')
    print(f"  Metadata: {meta}")
    assert meta is not None, "Metadata not found"
    assert meta['strategy'] == 'test', "Metadata doesn't match"

    # Test list
    print("\n[5] Testing list_cache...")
    cached = list_cache()
    print(f"  Cached items: {cached}")
    assert 'test_strategy' in cached, "test_strategy not in list"

    # Test summary
    print("\n[6] Testing cache summary...")
    print_cache_summary()

    # Test clear
    print("\n[7] Testing clear...")
    clear_cache('test_strategy')
    assert not cache_exists('test_strategy'), "Cache not cleared"
    print("✓ Clear successful")

    print("\n" + "=" * 90)
    print("✓ Cache system tests complete")
    print("=" * 90)
