"""
Unit tests for trade plan generation.
"""

import pytest
import pandas as pd
import numpy as np
from pathlib import Path
import tempfile
import json

from systems.crypto_perps.trade_plan import (
    load_actual_positions,
    calculate_position_deltas,
    estimate_trade_costs,
    check_min_position_sizes,
    classify_trade_reason,
    rank_trades_by_priority
)


class TestLoadActualPositions:
    """Test load_actual_positions function."""

    def test_valid_positions_file(self, tmp_path):
        """Should load valid positions CSV correctly."""
        positions_csv = tmp_path / 'positions.csv'
        positions_csv.write_text("""instrument,contracts,mark_price_usd,notional_usd,timestamp,notes
BTCUSDT_PERP,0.003,45000.00,135.00,2026-01-28T00:00:00Z,test
ETHUSDT_PERP,-0.025,3000.00,-75.00,2026-01-28T00:00:00Z,test
""")

        df = load_actual_positions(positions_csv)

        assert len(df) == 2
        assert 'BTCUSDT_PERP' in df.index
        assert 'ETHUSDT_PERP' in df.index
        assert df.loc['BTCUSDT_PERP', 'contracts'] == 0.003
        assert df.loc['BTCUSDT_PERP', 'notional_usd'] == 135.00

    def test_missing_required_columns(self, tmp_path):
        """Should raise ValueError if required columns missing."""
        positions_csv = tmp_path / 'positions.csv'
        positions_csv.write_text("""instrument,contracts
BTCUSDT_PERP,0.003
""")

        with pytest.raises(ValueError, match="Missing required columns"):
            load_actual_positions(positions_csv)


class TestCalculatePositionDeltas:
    """Test calculate_position_deltas function."""

    def test_basic_delta_calculation(self):
        """Should correctly calculate deltas for all instruments."""
        # Targets from backtest
        targets = pd.Series({
            'BTCUSDT_PERP': 250.75,
            'ETHUSDT_PERP': 0.00,
            'SOLUSDT_PERP': 50.00
        })

        # Actuals
        actuals = pd.DataFrame({
            'contracts': [0.003, -0.025, 0.000],
            'mark_price_usd': [45000.0, 3000.0, 75.0],
            'notional_usd': [135.0, -75.0, 0.0],
            'timestamp': ['2026-01-28T00:00:00Z'] * 3
        }, index=['BTCUSDT_PERP', 'ETHUSDT_PERP', 'SOLUSDT_PERP'])

        current_equity = 5125.50

        deltas = calculate_position_deltas(targets, actuals, current_equity)

        # Check deltas
        assert deltas.loc['BTCUSDT_PERP', 'delta_notional'] == pytest.approx(250.75 - 135.0)
        assert deltas.loc['ETHUSDT_PERP', 'delta_notional'] == pytest.approx(0.0 - (-75.0))
        assert deltas.loc['SOLUSDT_PERP', 'delta_notional'] == pytest.approx(50.0 - 0.0)

        # Check weights
        assert deltas.loc['BTCUSDT_PERP', 'delta_weight'] == pytest.approx((250.75 - 135.0) / 5125.50)

    def test_missing_actual_defaults_to_zero(self):
        """Should default to 0 for instruments not in actuals."""
        targets = pd.Series({
            'BTCUSDT_PERP': 250.75,
            'ETHUSDT_PERP': 100.00
        })

        # Only BTCUSDT in actuals
        actuals = pd.DataFrame({
            'contracts': [0.003],
            'mark_price_usd': [45000.0],
            'notional_usd': [135.0],
            'timestamp': ['2026-01-28T00:00:00Z']
        }, index=['BTCUSDT_PERP'])

        current_equity = 5000.0

        deltas = calculate_position_deltas(targets, actuals, current_equity)

        # ETHUSDT should have current_notional = 0.0
        assert deltas.loc['ETHUSDT_PERP', 'current_notional'] == 0.0
        assert deltas.loc['ETHUSDT_PERP', 'delta_notional'] == 100.0


class TestEstimateTradeCosts:
    """Test estimate_trade_costs function."""

    def test_cost_calculation(self):
        """Should correctly estimate round-trip costs."""
        deltas = pd.DataFrame({
            'delta_notional': [100.0, -50.0, 200.0]
        }, index=['A', 'B', 'C'])

        # Default: spread = 0.00025, taker_fee = 0.0004
        # RTC = 0.00025/2 + 0.0004 = 0.000525
        costs = estimate_trade_costs(deltas)

        assert costs['A'] == pytest.approx(100.0 * 0.000525)
        assert costs['B'] == pytest.approx(50.0 * 0.000525)  # Absolute value
        assert costs['C'] == pytest.approx(200.0 * 0.000525)

    def test_custom_fees(self):
        """Should use custom spread and fee fractions."""
        deltas = pd.DataFrame({
            'delta_notional': [100.0]
        }, index=['A'])

        costs = estimate_trade_costs(deltas, spread_frac=0.001, taker_fee_frac=0.0005)
        # RTC = 0.001/2 + 0.0005 = 0.001
        assert costs['A'] == pytest.approx(100.0 * 0.001)


class TestCheckMinPositionSizes:
    """Test check_min_position_sizes function."""

    def test_all_above_threshold(self):
        """Should pass if all trades above minimum."""
        deltas = pd.DataFrame({
            'delta_notional': [100.0, 50.0, 75.0],
            'target_notional': [100.0, 50.0, 75.0],
        }, index=['A', 'B', 'C'])

        min_order_notional = 30.0  # 30 USD minimum

        result = check_min_position_sizes(deltas, min_order_notional)

        assert result['threshold_usd'] == 30.0
        assert result['below_threshold'] == []
        assert result['status'] == 'pass'

    def test_some_below_threshold(self):
        """Should warn if any trades below minimum."""
        deltas = pd.DataFrame({
            'delta_notional': [100.0, 10.0, 75.0],  # B is below threshold
            'target_notional': [100.0, 10.0, 75.0],
        }, index=['A', 'B', 'C'])

        min_order_notional = 30.0  # 30 USD minimum

        result = check_min_position_sizes(deltas, min_order_notional)

        assert result['threshold_usd'] == 30.0
        assert 'B' in result['below_threshold']
        assert result['status'] == 'warn'


class TestClassifyTradeReason:
    """Test classify_trade_reason function."""

    def test_new_position(self):
        """Should identify new position (current=0, target>0)."""
        row = pd.Series({
            'current_notional': 0.0,
            'target_notional': 100.0,
            'delta_notional': 100.0
        })

        reason = classify_trade_reason(row)
        assert reason == 'new_position'

    def test_flatten_to_zero(self):
        """Should identify flatten (current>0, target=0)."""
        row = pd.Series({
            'current_notional': 100.0,
            'target_notional': 0.0,
            'delta_notional': -100.0
        })

        reason = classify_trade_reason(row)
        assert reason == 'flatten_to_zero'

    def test_target_increase(self):
        """Should identify target increase (same sign, larger magnitude)."""
        row = pd.Series({
            'current_notional': 100.0,
            'target_notional': 150.0,
            'delta_notional': 50.0
        })

        reason = classify_trade_reason(row)
        assert reason == 'target_increase'

    def test_target_decrease(self):
        """Should identify target decrease (same sign, smaller magnitude)."""
        row = pd.Series({
            'current_notional': 150.0,
            'target_notional': 100.0,
            'delta_notional': -50.0
        })

        reason = classify_trade_reason(row)
        assert reason == 'target_decrease'

    def test_rebalance(self):
        """Should identify rebalance (sign flip)."""
        row = pd.Series({
            'current_notional': 100.0,
            'target_notional': -50.0,
            'delta_notional': -150.0
        })

        reason = classify_trade_reason(row)
        assert reason == 'rebalance'


class TestRankTradesByPriority:
    """Test rank_trades_by_priority function."""

    def test_priority_by_absolute_size(self):
        """Should rank trades by absolute delta size (largest first)."""
        deltas = pd.DataFrame({
            'delta_notional': [50.0, 200.0, -100.0, 25.0],
            'target_notional': [50.0, 200.0, -100.0, 25.0]
        }, index=['A', 'B', 'C', 'D'])

        ranked = rank_trades_by_priority(deltas)

        # Priority order: B (200), C (100), A (50), D (25)
        assert ranked.loc['B', 'priority'] == 1
        assert ranked.loc['C', 'priority'] == 2
        assert ranked.loc['A', 'priority'] == 3
        assert ranked.loc['D', 'priority'] == 4

    def test_sorted_by_priority(self):
        """Should return dataframe sorted by priority."""
        deltas = pd.DataFrame({
            'delta_notional': [50.0, 200.0, -100.0],
            'target_notional': [50.0, 200.0, -100.0]
        }, index=['A', 'B', 'C'])

        ranked = rank_trades_by_priority(deltas)

        # Should be sorted by priority (1, 2, 3)
        priorities = ranked['priority'].tolist()
        assert priorities == sorted(priorities)
