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

    def test_orphan_position_no_price_dict(self, tmp_path):
        """Non-zero contracts with no price in the prices dict must fail closed."""
        positions_csv = tmp_path / 'positions.csv'
        positions_csv.write_text("""instrument,contracts,timestamp
1000PEPE_PERP,150000,2026-01-28T00:00:00Z
BTCUSDT_PERP,0.003,2026-01-28T00:00:00Z
""")

        prices = {"BTCUSDT_PERP": 45000.0}  # 1000PEPE missing — orphan from HL migration

        with pytest.raises(ValueError, match="1000PEPE_PERP"):
            load_actual_positions(positions_csv, prices=prices)

    def test_orphan_position_zero_price_in_csv(self, tmp_path):
        """Non-zero contracts with explicit zero/NaN price in CSV must fail closed."""
        positions_csv = tmp_path / 'positions.csv'
        positions_csv.write_text("""instrument,contracts,mark_price_usd,timestamp
FAKE_PERP,10,0.0,2026-01-28T00:00:00Z
""")

        with pytest.raises(ValueError, match="FAKE_PERP"):
            load_actual_positions(positions_csv)

    def test_zero_contracts_orphan_is_safe(self, tmp_path):
        """Zero-contract rows with no price should NOT fail — they're flat positions."""
        positions_csv = tmp_path / 'positions.csv'
        positions_csv.write_text("""instrument,contracts,timestamp
DELISTED_PERP,0,2026-01-28T00:00:00Z
BTCUSDT_PERP,0.003,2026-01-28T00:00:00Z
""")

        prices = {"BTCUSDT_PERP": 45000.0}  # DELISTED missing but contracts=0 so harmless

        df = load_actual_positions(positions_csv, prices=prices)
        assert df.loc['DELISTED_PERP', 'mark_price_usd'] == 0.0
        assert df.loc['DELISTED_PERP', 'notional_usd'] == 0.0
        assert df.loc['BTCUSDT_PERP', 'notional_usd'] == pytest.approx(0.003 * 45000.0)


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


# ---------------------------------------------------------------------------
# Audit-trail fixes from the 2026-05-26 system audit
# ---------------------------------------------------------------------------

class TestLoadBacktestDiagnosticsLayout:
    """Regression: the loader must handle current long-form layout AND legacy
    MultiIndex. Pre-fix it assumed MultiIndex and silently returned empty
    frames on the current layout, which made the audit bundle's
    `forecasts_snapshot` always empty (audit 2026-05-26)."""

    def test_long_form_columns_layout(self, tmp_path):
        """Current writer: `date` + `instrument` as columns, RangeIndex."""
        from systems.crypto_perps.trade_plan import load_backtest_diagnostics

        df = pd.DataFrame({
            "date": pd.to_datetime(["2026-05-25", "2026-05-25", "2026-05-24"]),
            "instrument": ["BTCUSDT_PERP", "ETHUSDT_PERP", "BTCUSDT_PERP"],
            "position": [0.5, -1.2, 0.4],
            "combined_forecast": [8.5, -4.3, 7.1],
            "instrument_weight": [0.03, 0.03, 0.03],
            "fdm": [2.5, 2.5, 2.5],
            "idm": [2.15, 2.15, 2.15],
        })
        (tmp_path / "diagnostics.parquet").parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(tmp_path / "diagnostics.parquet")

        out = load_backtest_diagnostics(tmp_path, "2026-05-25")
        assert len(out) == 2, f"expected 2 May-25 rows, got {len(out)}"
        assert set(out.index) == {"BTCUSDT_PERP", "ETHUSDT_PERP"}
        assert "date" not in out.columns
        assert "combined_forecast" in out.columns
        assert float(out.loc["BTCUSDT_PERP", "combined_forecast"]) == 8.5

    def test_multiindex_legacy_layout(self, tmp_path):
        """Legacy: MultiIndex of (date, instrument). Must still work."""
        from systems.crypto_perps.trade_plan import load_backtest_diagnostics

        idx = pd.MultiIndex.from_tuples([
            (pd.Timestamp("2026-05-25"), "BTCUSDT_PERP"),
            (pd.Timestamp("2026-05-25"), "ETHUSDT_PERP"),
            (pd.Timestamp("2026-05-24"), "BTCUSDT_PERP"),
        ], names=["date", "instrument"])
        df = pd.DataFrame({
            "combined_forecast": [8.5, -4.3, 7.1],
            "position": [0.5, -1.2, 0.4],
        }, index=idx)
        df.to_parquet(tmp_path / "diagnostics.parquet")

        out = load_backtest_diagnostics(tmp_path, "2026-05-25")
        assert len(out) == 2
        # After reset_index(level=0, drop=True), instrument is the remaining index
        assert "BTCUSDT_PERP" in out.index.tolist()

    def test_missing_file_raises(self, tmp_path):
        from systems.crypto_perps.trade_plan import load_backtest_diagnostics
        with pytest.raises(FileNotFoundError):
            load_backtest_diagnostics(tmp_path, "2026-05-25")


class TestPopulateBacktestMetadata:
    """Regression: audit bundle's `backtest_metadata` must have real values,
    not 'unknown' for every field (audit 2026-05-26)."""

    def test_populates_hashes_and_paths(self, tmp_path):
        from systems.crypto_perps.trade_plan import populate_backtest_metadata

        # Plant a config and a dataset to hash
        cfg = tmp_path / "config.yaml"
        cfg.write_text("notional_trading_capital: 12000\n")
        ds = tmp_path / "dataset.parquet"
        pd.DataFrame({"date": [pd.Timestamp("2026-05-25")], "instrument": ["X"], "close": [1.0]}).to_parquet(ds)

        meta = {
            "config_path": str(cfg),
            "data_path": str(ds),
            "backtest_start_date": "2020-01-01",
            "backtest_end_date": "2026-05-25",
        }
        backtest_dir = tmp_path / "backtest_latest"
        backtest_dir.mkdir()
        out = populate_backtest_metadata(meta, backtest_dir)

        # Hashes are 64 hex chars (sha256) — not 'unknown'
        assert len(out["config_hash"]) == 64
        assert len(out["dataset_fingerprint"]) == 64
        assert out["config_hash"] != "unknown"
        assert out["dataset_fingerprint"] != "unknown"
        assert out["dataset_path"] == str(ds)
        assert out["dataset_date_range"] == ["2020-01-01", "2026-05-25"]
        assert out["backtest_dir"] == str(backtest_dir)
        # git_commit either real sha or 'unknown' (depending on whether the
        # test is being run inside a git checkout). Both are acceptable.
        assert out["git_commit"] is not None

    def test_handles_missing_paths_gracefully(self, tmp_path):
        from systems.crypto_perps.trade_plan import populate_backtest_metadata

        meta = {}  # no config_path, no data_path
        out = populate_backtest_metadata(meta, tmp_path)
        assert out["config_hash"] == "unknown"
        assert out["dataset_fingerprint"] == "unknown"
        assert out["dataset_path"] == "unknown"

    def test_missing_config_file_gives_missing_marker(self, tmp_path):
        from systems.crypto_perps.trade_plan import populate_backtest_metadata

        meta = {"config_path": str(tmp_path / "nonexistent.yaml"),
                "data_path": str(tmp_path / "nonexistent.parquet"),
                "backtest_start_date": "2020-01-01",
                "backtest_end_date": "2026-05-25"}
        out = populate_backtest_metadata(meta, tmp_path)
        assert out["config_hash"] == "missing"
        assert out["dataset_fingerprint"] == "missing"
