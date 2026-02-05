"""
Integration test for live advisory workflow.

Tests end-to-end workflow using mock data:
1. Create mock backtest outputs
2. Create mock actual positions
3. Generate trade plan
4. Validate outputs
"""

import pytest
import pandas as pd
import numpy as np
from pathlib import Path
import json
import tempfile
import yaml

from systems.crypto_perps.trade_plan import generate_trade_plan


@pytest.fixture
def mock_config():
    """Mock system config."""
    return {
        'system': {
            'capital': 5000.0,
            'vol_target_ann': 0.25,
            'gross_leverage_cap': 2.0,
            'idm_cap': 2.5,
            'min_position_frac': 0.03,
            'spread_frac': 0.00025,
            'taker_fee_frac': 0.0004
        },
        'universe': {
            'layer_a_instruments': [
                'BTCUSDT_PERP',
                'ETHUSDT_PERP',
                'SOLUSDT_PERP'
            ]
        }
    }


@pytest.fixture
def mock_backtest_dir(tmp_path, mock_config):
    """Create mock backtest outputs."""
    backtest_dir = tmp_path / 'backtest_latest'
    backtest_dir.mkdir()

    # Create positions.csv
    dates = pd.date_range('2024-01-01', '2024-01-10', freq='D')
    positions = pd.DataFrame(
        index=dates,
        columns=['BTCUSDT_PERP', 'ETHUSDT_PERP', 'SOLUSDT_PERP'],
        data=np.random.randn(len(dates), 3) * 100  # Random positions
    )
    # Last date targets
    positions.iloc[-1] = [250.75, 100.50, 50.00]
    positions.to_csv(backtest_dir / 'positions.csv')

    # Create diagnostics.parquet
    diagnostics = []
    for date in dates:
        for inst in ['BTCUSDT_PERP', 'ETHUSDT_PERP', 'SOLUSDT_PERP']:
            diagnostics.append({
                'date': date,
                'instrument': inst,
                'combined_forecast': np.random.randn() * 10,
                'forecast_ewmac_8_32': np.random.randn() * 10,
                'forecast_ewmac_16_64': np.random.randn() * 10,
                'forecast_carry_funding': np.random.randn() * 10,
                'idm': 2.35,
                'gross_leverage': 1.5,
                'overall_scalar': 0.95,
                'state': 'ACTIVE'
            })

    df_diagnostics = pd.DataFrame(diagnostics)
    df_diagnostics = df_diagnostics.set_index(['date', 'instrument'])
    df_diagnostics.to_parquet(backtest_dir / 'diagnostics.parquet')

    # Create metadata.json
    metadata = {
        'config_hash': '22da856b',
        'dataset_fingerprint': 'a1b2c3d4',
        'git_commit': '59103977',
        'dataset_path': 'data/example_crypto_perps.parquet',
        'dataset_date_range': ['2024-01-01', '2024-01-10'],
        'config_path': 'config/crypto_perps_baseline_v1.yaml'
    }

    with open(backtest_dir / 'metadata.json', 'w') as f:
        json.dump(metadata, f)

    return backtest_dir


@pytest.fixture
def mock_actual_positions(tmp_path):
    """Create mock actual positions CSV."""
    positions_csv = tmp_path / 'current_positions.csv'
    positions_csv.write_text("""instrument,contracts,mark_price_usd,notional_usd,timestamp,notes
BTCUSDT_PERP,0.003,45000.00,135.00,2024-01-10T00:00:00Z,test
ETHUSDT_PERP,0.000,3000.00,0.00,2024-01-10T00:00:00Z,test
SOLUSDT_PERP,0.000,75.00,0.00,2024-01-10T00:00:00Z,test
""")
    return positions_csv


class TestLiveAdvisoryIntegration:
    """Integration tests for live advisory workflow."""

    def test_full_trade_plan_generation(
        self, mock_backtest_dir, mock_actual_positions, mock_config, tmp_path
    ):
        """Test full trade plan generation workflow."""
        # Generate trade plan
        as_of_date = '2024-01-10'
        current_equity = 5125.50

        trade_plan, sanity_checks, audit_bundle = generate_trade_plan(
            mock_backtest_dir,
            mock_actual_positions,
            current_equity,
            as_of_date,
            mock_config
        )

        # Validate trade plan
        assert len(trade_plan) == 3  # Three instruments
        assert 'BTCUSDT_PERP' in trade_plan.index
        assert 'ETHUSDT_PERP' in trade_plan.index
        assert 'SOLUSDT_PERP' in trade_plan.index

        # Check required columns
        required_cols = [
            'current_contracts', 'mark_price_usd', 'current_notional',
            'target_notional', 'delta_notional', 'delta_weight',
            'estimated_cost', 'reason', 'state', 'priority', 'warnings'
        ]
        for col in required_cols:
            assert col in trade_plan.columns

        # Validate deltas
        btc_delta = trade_plan.loc['BTCUSDT_PERP', 'delta_notional']
        assert btc_delta == pytest.approx(250.75 - 135.0)

        eth_delta = trade_plan.loc['ETHUSDT_PERP', 'delta_notional']
        assert eth_delta == pytest.approx(100.50 - 0.0)

        sol_delta = trade_plan.loc['SOLUSDT_PERP', 'delta_notional']
        assert sol_delta == pytest.approx(50.0 - 0.0)

        # Validate sanity checks
        assert 'checks' in sanity_checks
        assert 'gross_leverage' in sanity_checks['checks']
        assert 'idm_target_portfolio' in sanity_checks['checks']
        assert 'min_position_sizes' in sanity_checks['checks']
        assert 'banned_instruments' in sanity_checks['checks']

        # Gross leverage should pass
        gross_lev = sanity_checks['checks']['gross_leverage']
        assert gross_lev['status'] == 'pass'
        assert gross_lev['after_trades'] <= gross_lev['cap']

        # Validate audit bundle
        assert 'timestamp_utc' in audit_bundle
        assert 'as_of_date' in audit_bundle
        assert audit_bundle['as_of_date'] == as_of_date
        assert 'backtest_metadata' in audit_bundle
        assert 'actual_positions' in audit_bundle
        assert 'equity_info' in audit_bundle
        assert 'forecasts_snapshot' in audit_bundle
        assert 'constraints_snapshot' in audit_bundle
        assert 'target_portfolio' in audit_bundle

        # Validate prices snapshot
        prices = audit_bundle['actual_positions']['prices_snapshot']
        assert 'BTCUSDT_PERP' in prices
        assert prices['BTCUSDT_PERP']['mark_price'] == 45000.0
        assert prices['BTCUSDT_PERP']['contracts'] == 0.003
        assert prices['BTCUSDT_PERP']['notional'] == 135.0

    def test_trade_plan_with_gross_leverage_violation(
        self, mock_backtest_dir, mock_actual_positions, mock_config, tmp_path
    ):
        """Test trade plan generation when gross leverage would be violated."""
        # Modify config to have very low cap
        config = mock_config.copy()
        config['system']['gross_leverage_cap'] = 0.05  # Very low cap

        as_of_date = '2024-01-10'
        current_equity = 5125.50

        trade_plan, sanity_checks, audit_bundle = generate_trade_plan(
            mock_backtest_dir,
            mock_actual_positions,
            current_equity,
            as_of_date,
            config
        )

        # Gross leverage should fail
        gross_lev = sanity_checks['checks']['gross_leverage']
        assert gross_lev['status'] == 'fail'
        assert gross_lev['after_trades'] > gross_lev['cap']
        assert gross_lev['headroom'] < 0

        # Overall status should be fail
        assert sanity_checks['overall_status'] == 'fail'

    def test_trade_plan_date_mismatch_fails(
        self, mock_backtest_dir, mock_actual_positions, mock_config
    ):
        """Test that date mismatch raises error."""
        # Try to use wrong as_of_date
        wrong_date = '2024-01-15'  # Backtest ends at 2024-01-10
        current_equity = 5125.50

        with pytest.raises(ValueError, match="Date mismatch"):
            generate_trade_plan(
                mock_backtest_dir,
                mock_actual_positions,
                current_equity,
                wrong_date,
                mock_config
            )

    def test_trade_plan_with_extra_instruments_in_actuals_fails(
        self, mock_backtest_dir, mock_config, tmp_path
    ):
        """Test that extra instruments in actuals (not in universe) raises error."""
        # Create actuals with instrument not in universe
        positions_csv = tmp_path / 'current_positions.csv'
        positions_csv.write_text("""instrument,contracts,mark_price_usd,notional_usd,timestamp,notes
BTCUSDT_PERP,0.003,45000.00,135.00,2024-01-10T00:00:00Z,test
FAKE_PERP,0.100,100.00,10.00,2024-01-10T00:00:00Z,test
""")

        as_of_date = '2024-01-10'
        current_equity = 5125.50

        with pytest.raises(ValueError, match="NOT in universe"):
            generate_trade_plan(
                mock_backtest_dir,
                positions_csv,
                current_equity,
                as_of_date,
                mock_config
            )

    def test_trade_classification(
        self, mock_backtest_dir, mock_actual_positions, mock_config
    ):
        """Test that trade reasons are correctly classified."""
        as_of_date = '2024-01-10'
        current_equity = 5125.50

        trade_plan, _, _ = generate_trade_plan(
            mock_backtest_dir,
            mock_actual_positions,
            current_equity,
            as_of_date,
            mock_config
        )

        # BTC: current=135, target=250.75 -> target_increase
        assert trade_plan.loc['BTCUSDT_PERP', 'reason'] == 'target_increase'

        # ETH: current=0, target=100.50 -> new_position
        assert trade_plan.loc['ETHUSDT_PERP', 'reason'] == 'new_position'

        # SOL: current=0, target=50.00 -> new_position
        assert trade_plan.loc['SOLUSDT_PERP', 'reason'] == 'new_position'

    def test_trade_priority_ranking(
        self, mock_backtest_dir, mock_actual_positions, mock_config
    ):
        """Test that trades are correctly prioritized by size."""
        as_of_date = '2024-01-10'
        current_equity = 5125.50

        trade_plan, _, _ = generate_trade_plan(
            mock_backtest_dir,
            mock_actual_positions,
            current_equity,
            as_of_date,
            mock_config
        )

        # Check that priority is assigned
        assert 'priority' in trade_plan.columns

        # Higher priority (lower number) should have larger absolute delta
        priorities = trade_plan['priority'].values
        abs_deltas = trade_plan['delta_notional'].abs().values

        # Priority 1 should have largest delta, priority 3 smallest
        assert abs_deltas[priorities.argmin()] == abs_deltas.max()

    def test_cost_estimation(
        self, mock_backtest_dir, mock_actual_positions, mock_config
    ):
        """Test that costs are estimated correctly."""
        as_of_date = '2024-01-10'
        current_equity = 5125.50

        trade_plan, sanity_checks, _ = generate_trade_plan(
            mock_backtest_dir,
            mock_actual_positions,
            current_equity,
            as_of_date,
            mock_config
        )

        # All trades should have non-negative costs
        assert (trade_plan['estimated_cost'] >= 0).all()

        # Total cost should be sum of individual costs (rounded to 2 decimals)
        total_cost = trade_plan['estimated_cost'].sum()
        assert sanity_checks['checks']['total_estimated_cost'] == pytest.approx(total_cost, abs=0.01)

        # Cost percentage should be calculated
        cost_pct = sanity_checks['checks']['cost_as_pct_of_equity']
        assert cost_pct == pytest.approx(total_cost / current_equity, abs=0.0001)


class TestEdgeCases:
    """Test edge cases and error handling."""

    def test_missing_backtest_positions(self, tmp_path, mock_actual_positions, mock_config):
        """Should fail if backtest positions.csv missing."""
        backtest_dir = tmp_path / 'backtest_empty'
        backtest_dir.mkdir()

        with pytest.raises(FileNotFoundError):
            generate_trade_plan(
                backtest_dir,
                mock_actual_positions,
                5000.0,
                '2024-01-10',
                mock_config
            )

    def test_missing_actual_positions(self, mock_backtest_dir, mock_config, tmp_path):
        """Should fail if actual positions file missing."""
        fake_path = tmp_path / 'nonexistent.csv'

        # This should fail at file read stage
        with pytest.raises(FileNotFoundError):
            generate_trade_plan(
                mock_backtest_dir,
                fake_path,
                5000.0,
                '2024-01-10',
                mock_config
            )

    def test_zero_equity(self, mock_backtest_dir, mock_actual_positions, mock_config):
        """Should handle zero equity gracefully."""
        trade_plan, sanity_checks, _ = generate_trade_plan(
            mock_backtest_dir,
            mock_actual_positions,
            0.0,  # Zero equity
            '2024-01-10',
            mock_config
        )

        # All weights should be 0
        assert (trade_plan['delta_weight'] == 0.0).all()

        # Gross leverage checks should handle zero equity
        gross_lev = sanity_checks['checks']['gross_leverage']
        assert gross_lev['actual_current'] == 0.0
        assert gross_lev['after_trades'] == 0.0
