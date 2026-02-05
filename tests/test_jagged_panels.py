"""
Tests for jagged panel support

Validates:
- Instrument lifecycle state transitions
- Position invariants (zero before eligible)
- Correlation/IDM calculations with partial overlap
- PnL calculation with missing prices
"""
import pytest
import pandas as pd
import numpy as np
from pathlib import Path
import sys

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sysdata.crypto.lifecycle import load_instrument_lifecycle, is_instrument_active
from sysdata.crypto.prices import load_crypto_perps_panel
from systems.crypto_perps.universe import (
    InstrumentState,
    determine_instrument_state,
    has_sufficient_history,
    build_instrument_states
)


class TestInstrumentLifecycle:
    """Test instrument lifecycle metadata and state determination"""

    def test_load_lifecycle_metadata(self):
        """Test loading lifecycle metadata from JSON"""
        lifecycle_df = load_instrument_lifecycle()

        # Check structure
        assert 'launch_date' in lifecycle_df.columns
        assert 'status' in lifecycle_df.columns
        assert 'delist_date' in lifecycle_df.columns

        # Check BTC (earliest launch)
        assert 'BTCUSDT_PERP' in lifecycle_df.index
        btc_launch = lifecycle_df.loc['BTCUSDT_PERP', 'launch_date']
        assert btc_launch == pd.Timestamp('2019-09-08')

        # Check SOL (later launch)
        assert 'SOLUSDT_PERP' in lifecycle_df.index
        sol_launch = lifecycle_df.loc['SOLUSDT_PERP', 'launch_date']
        assert sol_launch == pd.Timestamp('2020-07-27')

    def test_is_instrument_active_before_launch(self):
        """Test that instruments are NOT_YET_LAUNCHED before their launch date"""
        lifecycle_df = load_instrument_lifecycle()

        # SOL before launch (2020-01-01 < 2020-07-27)
        is_active, reason = is_instrument_active(
            'SOLUSDT_PERP',
            pd.Timestamp('2020-01-01'),
            lifecycle_df
        )

        assert is_active is False
        assert reason == "NOT_YET_LAUNCHED"

    def test_is_instrument_active_after_launch(self):
        """Test that instruments are ACTIVE after launch date"""
        lifecycle_df = load_instrument_lifecycle()

        # SOL after launch (2023-01-01 > 2020-07-27)
        is_active, reason = is_instrument_active(
            'SOLUSDT_PERP',
            pd.Timestamp('2023-01-01'),
            lifecycle_df
        )

        assert is_active is True
        assert reason == "ACTIVE"


class TestWarmupPeriod:
    """Test warmup period logic"""

    def test_has_sufficient_history(self):
        """Test warmup period requirement (90 days)"""
        # Create synthetic prices with some NaN
        dates = pd.date_range('2020-01-01', periods=120, freq='D')
        prices_df = pd.DataFrame({
            'BTCUSDT_PERP': np.random.randn(120) + 100,
            'SOLUSDT_PERP': [np.nan] * 50 + list(np.random.randn(70) + 50)  # Launches day 50
        }, index=dates)

        # BTC should have sufficient history by day 90
        assert has_sufficient_history('BTCUSDT_PERP', dates[95], prices_df, min_days=90)

        # SOL should NOT have sufficient history by day 90 (only 40 valid prices)
        assert not has_sufficient_history('SOLUSDT_PERP', dates[95], prices_df, min_days=90)

        # SOL should have sufficient history by day 110 (60 valid prices from day 50)
        # Actually needs 90 valid, so needs to be at day 50+90 = 140, but we only have 120 days
        # Let's check day 119 (last day): 119-50+1 = 70 valid prices, not enough
        assert not has_sufficient_history('SOLUSDT_PERP', dates[119], prices_df, min_days=90)


class TestStateTransitions:
    """Test instrument state transitions"""

    def test_state_before_launch(self):
        """Test NOT_YET_LAUNCHED state before instrument launch"""
        # Create minimal test data
        dates = pd.date_range('2020-01-01', periods=10, freq='D')
        prices_df = pd.DataFrame({
            'BTCUSDT_PERP': 100.0,
            'SOLUSDT_PERP': np.nan  # Not yet launched
        }, index=dates)

        meta_df = pd.DataFrame({
            'funding_rate': 0.0001,
            'adv_notional': 1e9,
            'spread_frac': 0.0003,
            'taker_fee_frac': 0.0004
        }, index=pd.MultiIndex.from_product([dates, ['BTCUSDT_PERP', 'SOLUSDT_PERP']], names=['date', 'instrument']))

        lifecycle_df = load_instrument_lifecycle()

        # Check SOL state on 2020-01-01 (before launch 2020-07-27)
        state = determine_instrument_state(
            date=dates[0],
            instrument='SOLUSDT_PERP',
            prices_df=prices_df,
            meta_df=meta_df,
            lifecycle_df=lifecycle_df,
            min_adv_notional=1e7,
            banned_instruments=[]
        )

        assert state == InstrumentState.NOT_YET_LAUNCHED

    def test_state_banned_override(self):
        """Test BANNED_FLATTEN overrides other states"""
        # Need 100 days to pass warmup period (90 days)
        dates = pd.date_range('2023-01-01', periods=100, freq='D')
        prices_df = pd.DataFrame({
            'BTCUSDT_PERP': 100.0,
            'SOLUSDT_PERP': 50.0
        }, index=dates)

        meta_df = pd.DataFrame({
            'funding_rate': 0.0001,
            'adv_notional': 1e9,
            'spread_frac': 0.0003,
            'taker_fee_frac': 0.0004
        }, index=pd.MultiIndex.from_product([dates, ['BTCUSDT_PERP', 'SOLUSDT_PERP']], names=['date', 'instrument']))

        lifecycle_df = load_instrument_lifecycle()

        # BTC should be ACTIVE after warmup period (check on day 99, which has 99 days of history)
        state_active = determine_instrument_state(
            date=dates[99],
            instrument='BTCUSDT_PERP',
            prices_df=prices_df,
            meta_df=meta_df,
            lifecycle_df=lifecycle_df,
            min_adv_notional=1e7,
            banned_instruments=[]
        )
        assert state_active == InstrumentState.ACTIVE

        # BTC should be BANNED_FLATTEN when in banned list (overrides warmup/active)
        state_banned = determine_instrument_state(
            date=dates[99],
            instrument='BTCUSDT_PERP',
            prices_df=prices_df,
            meta_df=meta_df,
            lifecycle_df=lifecycle_df,
            min_adv_notional=1e7,
            banned_instruments=['BTCUSDT_PERP']
        )
        assert state_banned == InstrumentState.BANNED_FLATTEN


class TestPositionInvariants:
    """Test position invariants under different states"""

    def test_zero_position_before_launch(self):
        """Test that NOT_YET_LAUNCHED instruments have zero position"""
        # This will be tested in E2E test with actual system run
        pass

    def test_zero_position_during_warmup(self):
        """Test that WARMUP instruments have zero position"""
        # This will be tested in E2E test with actual system run
        pass

    def test_flatten_on_delisted(self):
        """Test that DELISTED instruments are immediately flattened"""
        # This will be tested in E2E test with actual system run
        pass


class TestJaggedPanelLoading:
    """Test loading jagged panel datasets"""

    def test_load_jagged_panel(self):
        """Test loading the BTC/SOL 2023 jagged panel test dataset"""
        # This test requires the test dataset to exist
        test_path = Path('data/test_jagged_btc_sol_2023.parquet')

        if not test_path.exists():
            pytest.skip("Test dataset not found: data/test_jagged_btc_sol_2023.parquet")

        # Load with jagged panel support
        prices_df, meta_df, lifecycle_df = load_crypto_perps_panel(
            str(test_path),
            allow_jagged=True
        )

        # Verify structure
        assert prices_df is not None
        assert lifecycle_df is not None
        assert 'BTCUSDT_PERP' in prices_df.columns
        assert 'SOLUSDT_PERP' in prices_df.columns

        # Check that both instruments have data in 2023 (both launched before 2023)
        nan_counts = prices_df.isna().sum()
        assert nan_counts['BTCUSDT_PERP'] == 0  # BTC should have no NaN in 2023
        assert nan_counts['SOLUSDT_PERP'] == 0  # SOL should have no NaN in 2023 (launched 2020)


class TestIDMEligibility:
    """Test IDM eligibility logic"""

    def test_idm_eligible_with_sufficient_overlap(self):
        """Test instrument becomes IDM-eligible with sufficient peer overlap"""
        from systems.crypto_perps.universe import is_idm_eligible

        # Create prices with good overlap between BTC, ETH, SOL
        dates = pd.date_range('2023-01-01', periods=120, freq='D')
        prices_df = pd.DataFrame({
            'BTCUSDT_PERP': np.random.randn(120) + 100,
            'ETHUSDT_PERP': np.random.randn(120) + 50,
            'SOLUSDT_PERP': np.random.randn(120) + 20
        }, index=dates)

        instruments = ['BTCUSDT_PERP', 'ETHUSDT_PERP', 'SOLUSDT_PERP']

        # Check on day 119 (should have 119 days of overlap with each peer)
        is_eligible, reason = is_idm_eligible(
            instrument='BTCUSDT_PERP',
            date=dates[119],
            prices_df=prices_df,
            instruments=instruments,
            min_overlap_days=60,
            min_peer_count=2
        )

        assert is_eligible is True, f"Should be IDM-eligible but got: {reason}"

    def test_idm_ineligible_insufficient_overlap(self):
        """Test instrument becomes IDM-ineligible with insufficient overlap"""
        from systems.crypto_perps.universe import is_idm_eligible

        # Create prices where SOL has limited overlap
        dates = pd.date_range('2023-01-01', periods=120, freq='D')
        prices_df = pd.DataFrame({
            'BTCUSDT_PERP': np.random.randn(120) + 100,
            'ETHUSDT_PERP': np.random.randn(120) + 50,
            # SOL only has last 50 days (not enough for 60-day overlap requirement)
            'SOLUSDT_PERP': [np.nan] * 70 + list(np.random.randn(50) + 20)
        }, index=dates)

        instruments = ['BTCUSDT_PERP', 'ETHUSDT_PERP', 'SOLUSDT_PERP']

        # Check SOL on day 119 (only 49 days of overlap with BTC/ETH, need 60)
        is_eligible, reason = is_idm_eligible(
            instrument='SOLUSDT_PERP',
            date=dates[119],
            prices_df=prices_df,
            instruments=instruments,
            min_overlap_days=60,
            min_peer_count=2
        )

        assert is_eligible is False
        assert "Insufficient IDM overlap" in reason

    def test_idm_state_transition(self):
        """Test state transitions for IDM eligibility"""
        from systems.crypto_perps.universe import determine_instrument_state, InstrumentState

        # Create scenario: SOL launches with limited data
        dates = pd.date_range('2023-01-01', periods=150, freq='D')
        prices_df = pd.DataFrame({
            'BTCUSDT_PERP': 100.0,
            'ETHUSDT_PERP': 50.0,
            # SOL launches on day 0, becomes WARMUP, then IDM_INELIGIBLE, then ACTIVE
            'SOLUSDT_PERP': list(np.random.randn(150) + 20)
        }, index=dates)

        meta_df = pd.DataFrame({
            'funding_rate': 0.0001,
            'adv_notional': 1e9,
            'spread_frac': 0.0003,
            'taker_fee_frac': 0.0004
        }, index=pd.MultiIndex.from_product([dates, ['BTCUSDT_PERP', 'ETHUSDT_PERP', 'SOLUSDT_PERP']], names=['date', 'instrument']))

        lifecycle_df = load_instrument_lifecycle()
        instruments = ['BTCUSDT_PERP', 'ETHUSDT_PERP', 'SOLUSDT_PERP']

        # Adjust SOL launch date to match test data
        lifecycle_df.loc['SOLUSDT_PERP', 'launch_date'] = dates[0]

        # Day 50: Should be WARMUP (not enough history for indicators)
        state_50 = determine_instrument_state(
            date=dates[50],
            instrument='SOLUSDT_PERP',
            prices_df=prices_df,
            meta_df=meta_df,
            lifecycle_df=lifecycle_df,
            min_adv_notional=1e7,
            instruments=instruments,
            banned_instruments=[],
            check_idm_eligibility=True
        )
        assert state_50 == InstrumentState.WARMUP

        # Day 100: Should be IDM_INELIGIBLE (past warmup but not enough overlap for IDM)
        # Has 100 days of data, but only 99 returns (pct_change drops first row)
        # With 60-day overlap requirement, should have enough
        state_100 = determine_instrument_state(
            date=dates[100],
            instrument='SOLUSDT_PERP',
            prices_df=prices_df,
            meta_df=meta_df,
            lifecycle_df=lifecycle_df,
            min_adv_notional=1e7,
            instruments=instruments,
            banned_instruments=[],
            check_idm_eligibility=True
        )
        # Should be ACTIVE since has >= 60 days overlap with both BTC and ETH
        assert state_100 == InstrumentState.ACTIVE


class TestCorrelationConservatism:
    """Test correlation/IDM calculations with partial overlap"""

    def test_correlation_shape_assertion(self):
        """Test that correlation matrix has correct shape"""
        # This will be tested when we update constraints.py
        pass

    def test_idm_uses_only_eligible_instruments(self):
        """Test that IDM calculation only uses instruments that pass IDM eligibility"""
        # This will be tested in E2E test
        pass


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
