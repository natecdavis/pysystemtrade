"""Unit tests for post-execution reconciliation."""

import json
from pathlib import Path

import pytest

from systems.crypto_perps.reconciliation import (
    reconcile_post_execution,
    write_reconciliation_report,
)


def _write_trade_plan(path: Path, rows: list[dict]) -> None:
    import pandas as pd
    pd.DataFrame(rows).to_csv(path, index=False)


def _write_positions(path: Path, rows: list[dict]) -> None:
    import pandas as pd
    if rows:
        pd.DataFrame(rows).to_csv(path, index=False)
    else:
        # Empty positions file still needs a header for pandas to parse it.
        path.write_text("instrument,contracts,timestamp\n")


class TestReconcilePostExecution:
    def test_perfect_match(self, tmp_path):
        plan = tmp_path / "trade_plan_2026-04-30.csv"
        positions = tmp_path / "current_positions.csv"

        _write_trade_plan(plan, [
            {"instrument": "BTCUSDT_PERP", "target_notional": 4500.0, "mark_price_usd": 45000.0,
             "current_notional": 0.0, "current_contracts": 0.0, "delta_notional": 4500.0},
            {"instrument": "ETHUSDT_PERP", "target_notional": -3000.0, "mark_price_usd": 3000.0,
             "current_notional": 0.0, "current_contracts": 0.0, "delta_notional": -3000.0},
        ])
        # Synced post-trade: matches plan exactly
        _write_positions(positions, [
            {"instrument": "BTCUSDT_PERP", "contracts": 0.1, "timestamp": "now"},
            {"instrument": "ETHUSDT_PERP", "contracts": -1.0, "timestamp": "now"},
        ])

        result = reconcile_post_execution(plan, positions, tolerance_usd=10.0)
        assert result.passed
        assert len(result.rows) == 2
        assert all(not r["exceeds_tolerance"] for r in result.rows)

    def test_partial_fill_flagged(self, tmp_path):
        plan = tmp_path / "trade_plan.csv"
        positions = tmp_path / "current_positions.csv"

        _write_trade_plan(plan, [
            {"instrument": "BTCUSDT_PERP", "target_notional": 4500.0, "mark_price_usd": 45000.0,
             "current_notional": 0.0, "current_contracts": 0.0, "delta_notional": 4500.0},
        ])
        # Only 0.05 BTC actually filled (half) — actual notional = 2250
        _write_positions(positions, [
            {"instrument": "BTCUSDT_PERP", "contracts": 0.05, "timestamp": "now"},
        ])

        result = reconcile_post_execution(plan, positions, tolerance_usd=10.0)
        assert not result.passed
        assert len(result.mismatches) == 1
        assert result.mismatches[0]["instrument"] == "BTCUSDT_PERP"
        assert result.mismatches[0]["delta_notional_usd"] == pytest.approx(-2250.0)

    def test_skipped_symbol_flagged(self, tmp_path):
        """A symbol the plan targeted but execute_trades skipped (e.g., not on network)
        should appear in the synced positions as zero contracts and be flagged."""
        plan = tmp_path / "trade_plan.csv"
        positions = tmp_path / "current_positions.csv"

        _write_trade_plan(plan, [
            {"instrument": "OBSCUREUSDT_PERP", "target_notional": 100.0, "mark_price_usd": 1.0,
             "current_notional": 0.0, "current_contracts": 0.0, "delta_notional": 100.0},
        ])
        # Symbol not present after sync (skipped during execution)
        _write_positions(positions, [])

        result = reconcile_post_execution(plan, positions, tolerance_usd=10.0)
        assert not result.passed
        assert result.mismatches[0]["delta_notional_usd"] == pytest.approx(-100.0)

    def test_orphan_on_hl_not_in_plan(self, tmp_path):
        plan = tmp_path / "trade_plan.csv"
        positions = tmp_path / "current_positions.csv"

        _write_trade_plan(plan, [
            {"instrument": "BTCUSDT_PERP", "target_notional": 0.0, "mark_price_usd": 45000.0,
             "current_notional": 0.0, "current_contracts": 0.0, "delta_notional": 0.0},
        ])
        # An old position lingers on HL that the plan never knew about
        _write_positions(positions, [
            {"instrument": "BTCUSDT_PERP", "contracts": 0.0, "timestamp": "now"},
            {"instrument": "ZOMBIE_PERP", "contracts": 1000, "mark_price_usd": 0.5, "timestamp": "now"},
        ])

        result = reconcile_post_execution(plan, positions, tolerance_usd=10.0)
        assert not result.passed
        zombie = next(r for r in result.mismatches if r["instrument"] == "ZOMBIE_PERP")
        assert zombie["note"] == "not_in_plan"
        assert zombie["actual_notional"] == pytest.approx(500.0)

    def test_new_position_with_plan_mark_zero_matches(self, tmp_path):
        """
        New positions have mark_price_usd=0 in the plan (because current_contracts=0,
        so there's no current notional to compute). After execution, the synced
        positions CSV has both contracts AND mark_price_usd. Reconciliation must
        fall back to the actuals' mark for valuation, otherwise every successfully-
        opened new position false-flags as MISMATCH.
        """
        plan = tmp_path / "trade_plan.csv"
        positions = tmp_path / "current_positions.csv"

        _write_trade_plan(plan, [
            # Plan target -$43.15 short on BNB. mark_price_usd=0 because it's a new position.
            {"instrument": "BNBUSDT_PERP", "target_notional": -43.15, "mark_price_usd": 0.0,
             "current_notional": 0.0, "current_contracts": 0.0, "delta_notional": -43.15},
        ])
        # After execution, sync pulled the live BNB position with its actual mark.
        _write_positions(positions, [
            {"instrument": "BNBUSDT_PERP", "contracts": -0.07, "mark_price_usd": 619.10,
             "timestamp": "now"},
        ])

        result = reconcile_post_execution(plan, positions, tolerance_usd=10.0)
        assert result.passed, f"Should match but mismatches were: {result.mismatches}"
        # Single row, valued via actuals' mark.
        row = result.rows[0]
        assert row["actual_contracts"] == pytest.approx(-0.07)
        # actual_notional = -0.07 × 619.10 = -43.337
        assert row["actual_notional"] == pytest.approx(-43.34, abs=0.01)
        # delta = actual - target = -43.34 - (-43.15) = -0.19, well within tolerance
        assert abs(row["delta_notional_usd"]) < 1.0
        # New "valued_with_actuals_mark" note distinguishes the fallback path.
        assert row["note"] == "valued_with_actuals_mark"

    def test_new_position_neither_mark_available_still_flags(self, tmp_path):
        """
        If both plan mark AND actuals mark are zero (or actuals row missing entirely),
        we genuinely can't value the position — fall through to the no_mark_in_plan flag.
        """
        plan = tmp_path / "trade_plan.csv"
        positions = tmp_path / "current_positions.csv"

        _write_trade_plan(plan, [
            {"instrument": "ZOMBIESYMBOL_PERP", "target_notional": -50.0, "mark_price_usd": 0.0,
             "current_notional": 0.0, "current_contracts": 0.0, "delta_notional": -50.0},
        ])
        # Synced positions don't include this symbol at all (e.g., trade was skipped).
        _write_positions(positions, [])

        result = reconcile_post_execution(plan, positions, tolerance_usd=10.0)
        assert not result.passed
        row = result.rows[0]
        assert row["note"] == "no_mark_in_plan"
        assert row["delta_notional_usd"] == pytest.approx(50.0)

    def test_within_tolerance_passes(self, tmp_path):
        """Tiny rounding mismatch (< $10) should NOT trip reconciliation."""
        plan = tmp_path / "trade_plan.csv"
        positions = tmp_path / "current_positions.csv"

        _write_trade_plan(plan, [
            {"instrument": "BTCUSDT_PERP", "target_notional": 4500.0, "mark_price_usd": 45000.0,
             "current_notional": 0.0, "current_contracts": 0.0, "delta_notional": 4500.0},
        ])
        # 0.0999 BTC = $4495.50, off by $4.50 (within $10 tolerance)
        _write_positions(positions, [
            {"instrument": "BTCUSDT_PERP", "contracts": 0.0999, "timestamp": "now"},
        ])

        result = reconcile_post_execution(plan, positions, tolerance_usd=10.0)
        assert result.passed

    def test_report_serializes_to_json(self, tmp_path):
        plan = tmp_path / "trade_plan.csv"
        positions = tmp_path / "current_positions.csv"
        report_path = tmp_path / "reconciliation.json"

        _write_trade_plan(plan, [
            {"instrument": "BTCUSDT_PERP", "target_notional": 4500.0, "mark_price_usd": 45000.0,
             "current_notional": 0.0, "current_contracts": 0.0, "delta_notional": 4500.0},
        ])
        _write_positions(positions, [
            {"instrument": "BTCUSDT_PERP", "contracts": 0.1, "timestamp": "now"},
        ])

        result = reconcile_post_execution(
            plan, positions, tolerance_usd=10.0,
            execution_summary={"submitted": 1, "skipped": 0, "errors": 0, "total_actionable": 1},
        )
        write_reconciliation_report(result, report_path)

        loaded = json.loads(report_path.read_text())
        assert loaded["passed"] is True
        assert loaded["total_symbols"] == 1
        assert loaded["mismatches_count"] == 0
        assert loaded["execution_summary"]["submitted"] == 1
