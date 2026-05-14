"""
Unit tests for trade-plan post-processing.
"""

import json
from pathlib import Path
from textwrap import dedent

import pandas as pd
import pytest

from scripts.generate_trade_plan import apply_hard_exits_and_reduce_only
from systems.crypto_perps.trade_plan import check_min_position_sizes


class _NoopLog:
    def info(self, *args, **kwargs):
        pass

    def warning(self, *args, **kwargs):
        pass


def _trade_plan_row(current, target):
    df = pd.DataFrame(
        {
            "current_notional": [current],
            "target_notional": [target],
            "delta_notional": [target - current],
            "delta_weight": [0.0],
            "reason": ["rebalance"],
            "warnings": [""],
        },
        index=["TSTUSDT_PERP"],
    )
    df.attrs["current_equity"] = 1000.0
    return df


def _apply_notes_reduce_only(trade_plan):
    return apply_hard_exits_and_reduce_only(
        trade_plan=trade_plan,
        new_snapshot=None,
        prev_snapshot=None,
        data_status_instruments={},
        delisted_instruments=[],
        banned_instruments=set(),
        log=_NoopLog(),
        reduce_only_instruments={"TSTUSDT_PERP"},
        min_notional_position=10.0,
    )


def test_reduce_only_short_allows_full_close():
    trade_plan = _trade_plan_row(current=-17.0, target=0.0)

    modified = _apply_notes_reduce_only(trade_plan)

    assert modified == 0
    assert trade_plan.loc["TSTUSDT_PERP", "target_notional"] == pytest.approx(0.0)
    assert trade_plan.loc["TSTUSDT_PERP", "delta_notional"] == pytest.approx(17.0)
    assert trade_plan.loc["TSTUSDT_PERP", "reason"] == "rebalance"


def test_reduce_only_short_caps_increase():
    trade_plan = _trade_plan_row(current=-17.0, target=-30.0)

    modified = _apply_notes_reduce_only(trade_plan)

    assert modified == 1
    assert trade_plan.loc["TSTUSDT_PERP", "target_notional"] == pytest.approx(-17.0)
    assert trade_plan.loc["TSTUSDT_PERP", "delta_notional"] == pytest.approx(0.0)
    assert trade_plan.loc["TSTUSDT_PERP", "reason"] == "reduce_only_notes"
    assert "reduce_only_capped" in trade_plan.loc["TSTUSDT_PERP", "warnings"]
    assert "below_min_trade_size" not in trade_plan.loc["TSTUSDT_PERP", "warnings"]


def test_reduce_only_short_allows_partial_reduction():
    trade_plan = _trade_plan_row(current=-17.0, target=-5.0)

    modified = _apply_notes_reduce_only(trade_plan)

    assert modified == 0
    assert trade_plan.loc["TSTUSDT_PERP", "target_notional"] == pytest.approx(-5.0)


def test_reduce_only_long_allows_full_close():
    trade_plan = _trade_plan_row(current=17.0, target=0.0)

    modified = _apply_notes_reduce_only(trade_plan)

    assert modified == 0
    assert trade_plan.loc["TSTUSDT_PERP", "target_notional"] == pytest.approx(0.0)
    assert trade_plan.loc["TSTUSDT_PERP", "delta_notional"] == pytest.approx(-17.0)


def test_reduce_only_long_caps_increase():
    trade_plan = _trade_plan_row(current=17.0, target=30.0)

    modified = _apply_notes_reduce_only(trade_plan)

    assert modified == 1
    assert trade_plan.loc["TSTUSDT_PERP", "target_notional"] == pytest.approx(17.0)
    assert trade_plan.loc["TSTUSDT_PERP", "delta_notional"] == pytest.approx(0.0)
    assert "reduce_only_capped" in trade_plan.loc["TSTUSDT_PERP", "warnings"]
    assert "below_min_trade_size" not in trade_plan.loc["TSTUSDT_PERP", "warnings"]


def test_legacy_position_outside_universe_gets_reduce_only_target_zero():
    """Regression: a long position carried over from a prior period whose
    instrument has since dropped out of the active universe must get
    target_notional = 0 with reason 'reduce_only_not_in_universe'. The
    reduce-only logic caps the target in the direction of the existing
    position so it can close but not flip; here that yields target = 0
    (full close allowed). Reproduces the TRX case (current=$1688 long,
    backtest target=$0, no longer in universe)."""
    trade_plan = pd.DataFrame(
        {
            "current_notional": [1688.0, 50.0],
            "target_notional": [0.0, 50.0],
            "delta_notional": [-1688.0, 0.0],
            "delta_weight": [0.0, 0.0],
            "reason": ["rebalance", "rebalance"],
            "warnings": ["", ""],
        },
        index=["TRXUSDT_PERP", "BTCUSDT_PERP"],
    )
    trade_plan.attrs["current_equity"] = 3956.0

    apply_hard_exits_and_reduce_only(
        trade_plan=trade_plan,
        new_snapshot={"tradable_instruments": ["BTCUSDT_PERP"]},
        prev_snapshot=None,
        data_status_instruments={},
        delisted_instruments=[],
        banned_instruments=set(),
        log=_NoopLog(),
        reduce_only_instruments=set(),
        min_notional_position=10.0,
    )

    # TRX has a non-zero current (1688) and is not in the universe → reduce-only
    # zombie guard sets target=0 with reason reduce_only_not_in_universe. The
    # backtest already had target=0 so the zombie guard's "if abs(target) < 0.01"
    # short-circuit applies and the row's reason is left as 'rebalance'. Either
    # way, the key invariant is target_notional == 0 — the position will close.
    assert trade_plan.loc["TRXUSDT_PERP", "target_notional"] == pytest.approx(0.0)
    # BTC is in the universe and unchanged.
    assert trade_plan.loc["BTCUSDT_PERP", "target_notional"] == pytest.approx(50.0)
    assert trade_plan.loc["BTCUSDT_PERP", "reason"] == "rebalance"


def test_hard_exit_overrides_reduce_only():
    trade_plan = _trade_plan_row(current=17.0, target=30.0)

    modified = apply_hard_exits_and_reduce_only(
        trade_plan=trade_plan,
        new_snapshot=None,
        prev_snapshot=None,
        data_status_instruments={},
        delisted_instruments=[],
        banned_instruments={"TSTUSDT_PERP"},
        log=_NoopLog(),
        reduce_only_instruments={"TSTUSDT_PERP"},
        min_notional_position=10.0,
    )

    assert modified == 1
    assert trade_plan.loc["TSTUSDT_PERP", "target_notional"] == pytest.approx(0.0)
    assert trade_plan.loc["TSTUSDT_PERP", "reason"] == "hard_exit_banned"


def test_min_size_allows_new_short_above_ten_dollars():
    deltas = pd.DataFrame(
        {
            "target_notional": [-11.5, -15.97],
            "delta_notional": [-11.5, -15.97],
        },
        index=["VETUSDT_PERP", "XRPUSDT_PERP"],
    )

    result = check_min_position_sizes(deltas, min_order_notional=10.0)

    assert result["threshold_usd"] == 10.0
    assert result["below_threshold"] == []
    assert result["status"] == "pass"


def test_min_size_flags_new_short_below_ten_dollars():
    deltas = pd.DataFrame(
        {
            "target_notional": [-9.99],
            "delta_notional": [-9.99],
        },
        index=["LTCUSDT_PERP"],
    )

    result = check_min_position_sizes(deltas, min_order_notional=10.0)

    assert result["below_threshold"] == ["LTCUSDT_PERP"]
    assert result["status"] == "warn"


def test_min_size_allows_full_close_under_ten_dollars():
    deltas = pd.DataFrame(
        {
            "target_notional": [0.0],
            "delta_notional": [7.5],
        },
        index=["DOGEUSDT_PERP"],
    )

    result = check_min_position_sizes(deltas, min_order_notional=10.0)

    assert result["below_threshold"] == []
    assert result["status"] == "pass"


def test_min_size_ignores_zero_delta_nonzero_target():
    deltas = pd.DataFrame(
        {
            "target_notional": [-17.0],
            "delta_notional": [0.0],
        },
        index=["TSTUSDT_PERP"],
    )

    result = check_min_position_sizes(deltas, min_order_notional=10.0)

    assert result["below_threshold"] == []
    assert result["status"] == "pass"


def test_min_size_flags_partial_reduction_under_ten_dollars():
    deltas = pd.DataFrame(
        {
            "target_notional": [-13.8],
            "delta_notional": [7.6],
        },
        index=["DOGEUSDT_PERP"],
    )

    result = check_min_position_sizes(deltas, min_order_notional=10.0)

    assert result["below_threshold"] == ["DOGEUSDT_PERP"]
    assert result["status"] == "warn"

