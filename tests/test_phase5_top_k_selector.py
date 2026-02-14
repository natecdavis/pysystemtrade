"""
Test Phase 5: Top-K Selection with Hysteresis

Tests top-K instrument selection with entry/exit hysteresis.
"""

import pytest
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sysdata.crypto.top_k_selector import TopKInstrumentSelector


def test_top_k_selector_initialization():
    """Test TopKInstrumentSelector initialization."""
    selector = TopKInstrumentSelector(K=30, entry_buffer=5, exit_buffer=10)

    assert selector.K == 30
    assert selector.entry_buffer == 5
    assert selector.exit_buffer == 10
    assert selector.entry_threshold == 25  # 30 - 5
    assert selector.exit_threshold == 40   # 30 + 10


def test_compute_liquidity_metric():
    """Test ADV computation from price/volume data."""
    selector = TopKInstrumentSelector(K=10, adv_window=5, min_history_days=10)

    # Create sample data
    dates = pd.date_range('2024-01-01', periods=20, freq='D')

    # High liquidity instrument
    prices_df = pd.DataFrame({
        'HIGH_LIQ': [100.0] * 20,
        'LOW_LIQ': [10.0] * 20,
    }, index=dates)

    volumes_df = pd.DataFrame({
        'HIGH_LIQ': [1000000.0] * 20,  # High volume
        'LOW_LIQ': [1000.0] * 20,      # Low volume
    }, index=dates)

    # Compute liquidity at last date
    date = dates[-1]
    liquidity = selector.compute_liquidity_metric(prices_df, volumes_df, date)

    # Verify HIGH_LIQ ranks first
    assert liquidity.index[0] == 'HIGH_LIQ'
    assert liquidity['HIGH_LIQ'] > liquidity['LOW_LIQ']


def test_entry_hysteresis():
    """Test that entry requires rank <= K - entry_buffer."""
    selector = TopKInstrumentSelector(K=10, entry_buffer=3, exit_buffer=5, min_history_days=5)

    # Create liquidity data
    dates = pd.date_range('2024-01-01', periods=10, freq='D')

    # 15 instruments with varying liquidity
    instruments = [f'INST{i:02d}' for i in range(15)]

    prices_df = pd.DataFrame(
        {inst: [100.0] * 10 for inst in instruments},
        index=dates
    )

    # Volumes create clear ranking (INST00 = highest, INST14 = lowest)
    volumes_df = pd.DataFrame(
        {inst: [1000000 - (i * 10000)] * 10 for i, inst in enumerate(instruments)},
        index=dates
    )

    # Select tradable set (start empty)
    current_tradable = set()
    eligible = instruments  # All eligible

    date = dates[-1]
    new_tradable = selector.select_tradable_set(
        eligible_candidates=eligible,
        current_tradable=current_tradable,
        prices_df=prices_df,
        volumes_df=volumes_df,
        date=date
    )

    # Entry threshold = K - entry_buffer = 10 - 3 = 7
    # Should only include top 7 (INST00 through INST06)
    assert len(new_tradable) == 7
    assert 'INST00' in new_tradable
    assert 'INST06' in new_tradable
    assert 'INST07' not in new_tradable  # Rank 8, can't enter


def test_exit_hysteresis():
    """Test that exit requires rank > K + exit_buffer."""
    selector = TopKInstrumentSelector(K=10, entry_buffer=3, exit_buffer=5, min_history_days=5)

    dates = pd.date_range('2024-01-01', periods=10, freq='D')
    instruments = [f'INST{i:02d}' for i in range(20)]

    prices_df = pd.DataFrame(
        {inst: [100.0] * 10 for inst in instruments},
        index=dates
    )

    volumes_df = pd.DataFrame(
        {inst: [1000000 - (i * 10000)] * 10 for i, inst in enumerate(instruments)},
        index=dates
    )

    # Start with instruments 0-12 already tradable (13 instruments, > K but < exit threshold)
    # Exit threshold = 10 + 5 = 15
    # INST00-INST12 have ranks 1-13, all <= 15, so all should stay (not capped)
    current_tradable = set([f'INST{i:02d}' for i in range(13)])
    eligible = instruments

    date = dates[-1]
    new_tradable = selector.select_tradable_set(
        eligible_candidates=eligible,
        current_tradable=current_tradable,
        prices_df=prices_df,
        volumes_df=volumes_df,
        date=date
    )

    # All 13 should stay (ranks 1-13 are all <= exit threshold 15)
    # But will be capped at K=10, keeping top 10 by rank
    assert len(new_tradable) == 10  # Capped
    assert 'INST00' in new_tradable
    assert 'INST09' in new_tradable  # Top 10 by rank

    # Test that instrument at exit boundary stays if already in set
    # INST14 has rank 15, which is NOT > 15, so it should stay if it was in
    current_tradable = {'INST00', 'INST01', 'INST14'}  # INST14 rank=15
    new_tradable = selector.select_tradable_set(
        eligible_candidates=eligible,
        current_tradable=current_tradable,
        prices_df=prices_df,
        volumes_df=volumes_df,
        date=date
    )
    assert 'INST14' in new_tradable  # Rank 15 is NOT > 15, stays

    # But INST15 (rank 16) should exit
    current_tradable = {'INST00', 'INST01', 'INST15'}  # INST15 rank=16
    new_tradable = selector.select_tradable_set(
        eligible_candidates=eligible,
        current_tradable=current_tradable,
        prices_df=prices_df,
        volumes_df=volumes_df,
        date=date
    )
    assert 'INST15' not in new_tradable  # Rank 16 > 15, exits


def test_hysteresis_prevents_churn():
    """Test that hysteresis creates stability (instruments in 'gray zone' stay but can't enter)."""
    selector = TopKInstrumentSelector(K=10, entry_buffer=3, exit_buffer=5, min_history_days=5)

    dates = pd.date_range('2024-01-01', periods=10, freq='D')
    instruments = [f'INST{i:02d}' for i in range(20)]

    prices_df = pd.DataFrame(
        {inst: [100.0] * 10 for inst in instruments},
        index=dates
    )

    volumes_df = pd.DataFrame(
        {inst: [1000000 - (i * 10000)] * 10 for i, inst in enumerate(instruments)},
        index=dates
    )

    eligible = instruments
    date = dates[-1]

    # Case 1: INST08 (rank 9) is NOT in tradable set
    # Entry threshold = 7, so rank 9 can't enter
    current_tradable = set()
    new_tradable = selector.select_tradable_set(
        eligible, current_tradable, prices_df, volumes_df, date
    )
    assert 'INST08' not in new_tradable  # Rank 9 > 7, can't enter

    # Case 2: INST08 (rank 9) IS in tradable set
    # Exit threshold = 15, so rank 9 stays (9 <= 15)
    current_tradable = {'INST08'}
    new_tradable = selector.select_tradable_set(
        eligible, current_tradable, prices_df, volumes_df, date
    )
    assert 'INST08' in new_tradable  # Rank 9 <= 15, stays

    # This is hysteresis: rank 9 can stay but can't enter


def test_cap_at_k():
    """Test that tradable set is capped at K instruments."""
    selector = TopKInstrumentSelector(K=5, entry_buffer=0, exit_buffer=0, min_history_days=5)

    dates = pd.date_range('2024-01-01', periods=10, freq='D')
    instruments = [f'INST{i:02d}' for i in range(10)]

    prices_df = pd.DataFrame(
        {inst: [100.0] * 10 for inst in instruments},
        index=dates
    )

    volumes_df = pd.DataFrame(
        {inst: [1000000 - (i * 10000)] * 10 for i, inst in enumerate(instruments)},
        index=dates
    )

    # Entry threshold = 5 - 0 = 5
    # Should select exactly top 5
    current_tradable = set()
    eligible = instruments
    date = dates[-1]

    new_tradable = selector.select_tradable_set(
        eligible, current_tradable, prices_df, volumes_df, date
    )

    assert len(new_tradable) == 5
    assert new_tradable == {'INST00', 'INST01', 'INST02', 'INST03', 'INST04'}


def test_eligibility_filtering():
    """Test that non-eligible instruments exit even if rank is good."""
    selector = TopKInstrumentSelector(K=10, entry_buffer=0, exit_buffer=0, min_history_days=5)

    dates = pd.date_range('2024-01-01', periods=10, freq='D')
    instruments = [f'INST{i:02d}' for i in range(10)]

    prices_df = pd.DataFrame(
        {inst: [100.0] * 10 for inst in instruments},
        index=dates
    )

    volumes_df = pd.DataFrame(
        {inst: [1000000 - (i * 10000)] * 10 for i, inst in enumerate(instruments)},
        index=dates
    )

    # INST00 is tradable but becomes ineligible
    current_tradable = {'INST00', 'INST01'}
    eligible = ['INST01', 'INST02', 'INST03', 'INST04', 'INST05']  # INST00 NOT eligible

    date = dates[-1]
    new_tradable = selector.select_tradable_set(
        eligible, current_tradable, prices_df, volumes_df, date
    )

    # INST00 should exit (not eligible)
    assert 'INST00' not in new_tradable

    # INST01 should stay (eligible and high rank)
    assert 'INST01' in new_tradable


def test_get_tradable_over_time():
    """Test tradable set evolution over time."""
    selector = TopKInstrumentSelector(K=3, entry_buffer=1, exit_buffer=1, min_history_days=5)

    dates = pd.date_range('2024-01-01', periods=10, freq='D')
    instruments = ['INST00', 'INST01', 'INST02', 'INST03', 'INST04']

    # Create eligibility DataFrame (all eligible all the time)
    eligible_df = pd.DataFrame(True, index=dates, columns=instruments)

    prices_df = pd.DataFrame(
        {inst: [100.0] * 10 for inst in instruments},
        index=dates
    )

    volumes_df = pd.DataFrame(
        {inst: [1000000 - (i * 10000)] * 10 for i, inst in enumerate(instruments)},
        index=dates
    )

    tradable_over_time = selector.get_tradable_over_time(
        eligible_df, prices_df, volumes_df
    )

    # Should have entry for each date
    assert len(tradable_over_time) == 10

    # Entry threshold = 3 - 1 = 2
    # Should select top 2 initially
    first_date = dates[0]
    assert len(tradable_over_time[first_date]) == 2
    assert 'INST00' in tradable_over_time[first_date]
    assert 'INST01' in tradable_over_time[first_date]


def test_to_eligibility_df():
    """Test conversion to eligibility DataFrame."""
    selector = TopKInstrumentSelector(K=5)

    dates = pd.date_range('2024-01-01', periods=3, freq='D')
    instruments = ['INST00', 'INST01', 'INST02', 'INST03', 'INST04']

    tradable_over_time = {
        dates[0]: {'INST00', 'INST01'},
        dates[1]: {'INST00', 'INST01', 'INST02'},
        dates[2]: {'INST01', 'INST02'},
    }

    df = selector.to_eligibility_df(tradable_over_time, instruments)

    # Verify shape
    assert df.shape == (3, 5)

    # Verify values
    assert df.loc[dates[0], 'INST00'] == True
    assert df.loc[dates[0], 'INST02'] == False

    assert df.loc[dates[1], 'INST02'] == True
    assert df.loc[dates[1], 'INST03'] == False

    assert df.loc[dates[2], 'INST00'] == False
    assert df.loc[dates[2], 'INST01'] == True


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
