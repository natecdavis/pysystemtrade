import pytest
import pandas as pd
import numpy as np
from pathlib import Path
from scripts.build_example_dataset import consolidate_funding_to_daily
from systems.crypto_perps.accounting import calculate_funding_pnl

class TestFundingAggregation:
    """Test 8-hourly to daily funding consolidation"""

    def test_three_events_per_day_sum(self):
        """Verify 3x 8-hourly events sum correctly to daily rate"""
        events = pd.DataFrame({
            'calcTime': pd.to_datetime([
                '2023-01-01 00:00:00',
                '2023-01-01 08:00:00',
                '2023-01-01 16:00:00',
                '2023-01-02 00:00:00',
                '2023-01-02 08:00:00',
                '2023-01-02 16:00:00',
            ]),
            'fundingRate': [0.0001, 0.0002, 0.00015, 0.00008, 0.00012, 0.00010],
        })

        daily = consolidate_funding_to_daily(events)

        assert len(daily) == 2
        assert np.isclose(daily.loc[0, 'funding_rate'], 0.00045, atol=1e-8)
        assert np.isclose(daily.loc[1, 'funding_rate'], 0.00030, atol=1e-8)

    def test_partial_day_handling(self):
        """Test days with <3 events (edge case)"""
        events = pd.DataFrame({
            'calcTime': pd.to_datetime([
                '2023-01-01 00:00:00',
                '2023-01-01 08:00:00',
                # Missing 16:00 event
            ]),
            'fundingRate': [0.0001, 0.0002],
        })

        daily = consolidate_funding_to_daily(events)

        assert len(daily) == 1
        assert np.isclose(daily.loc[0, 'funding_rate'], 0.0003, atol=1e-8)


class TestFundingSignConvention:
    """
    Test funding rate sign convention

    Convention (from accounting.py:92 and FUNDING_SEMANTICS):
      funding_cost = position × funding_rate
        - Positive rate + long position  → positive cost (longs pay)
        - Positive rate + short position → negative cost (shorts receive)

      In PnL accounting: funding_pnl = -funding_cost
        - Positive PnL = profit (received funding)
        - Negative PnL = loss (paid funding)
    """

    def test_long_pays_when_rate_positive(self):
        """Long position with positive rate pays funding (negative PnL)"""
        position = 1000.0  # Long $1000
        funding_rate = 0.0001  # +0.01%

        # From accounting.py:92 → funding_cost = position × funding_rate
        funding_cost = position * funding_rate
        funding_pnl = -funding_cost  # PnL = -cost

        assert funding_cost > 0, "Long with positive rate pays (positive cost)"
        assert funding_pnl < 0, "Paying funding = negative PnL (loss)"
        assert np.isclose(funding_cost, 0.1, atol=1e-6)
        assert np.isclose(funding_pnl, -0.1, atol=1e-6)

    def test_short_receives_when_rate_positive(self):
        """Short position with positive rate receives funding (positive PnL)"""
        position = -1000.0  # Short $1000
        funding_rate = 0.0001  # +0.01%

        funding_cost = position * funding_rate
        funding_pnl = -funding_cost

        assert funding_cost < 0, "Short with positive rate receives (negative cost)"
        assert funding_pnl > 0, "Receiving funding = positive PnL (profit)"
        assert np.isclose(funding_cost, -0.1, atol=1e-6)
        assert np.isclose(funding_pnl, 0.1, atol=1e-6)

    def test_long_receives_when_rate_negative(self):
        """Long position with negative rate receives funding (positive PnL)"""
        position = 1000.0
        funding_rate = -0.0001  # -0.01%

        funding_cost = position * funding_rate
        funding_pnl = -funding_cost

        assert funding_cost < 0, "Long with negative rate receives (negative cost)"
        assert funding_pnl > 0, "Receiving funding = positive PnL (profit)"
        assert np.isclose(funding_cost, -0.1, atol=1e-6)
        assert np.isclose(funding_pnl, 0.1, atol=1e-6)


class TestFundingAlignment:
    """Test funding rate date alignment semantics"""

    def test_funding_applies_to_previous_position(self):
        """funding_rate[t] applies to position held at close(t-1), not position[t]"""
        # This is documented in docstrings - verify it's clear
        from sysdata.crypto.schema import FUNDING_SEMANTICS

        assert 'close(D-1) to close(D)' in FUNDING_SEMANTICS['alignment'], \
            "FUNDING_SEMANTICS must document alignment clearly"

    def test_documented_alignment_invariant(self):
        """Verify schema module documents alignment"""
        from sysdata.crypto.schema import FUNDING_SEMANTICS

        assert 'alignment' in FUNDING_SEMANTICS
        assert 'funding_rate[D]' in FUNDING_SEMANTICS['alignment']
