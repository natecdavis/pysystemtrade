"""
Unit tests for trade-plan post-processing.
"""

import json
from pathlib import Path
from textwrap import dedent

import pandas as pd
import pytest

from scripts.generate_trade_plan import (
    apply_hard_exits_and_reduce_only,
    compute_shadow_targets,
)
from systems.crypto_perps.trade_plan import check_min_position_sizes


class _NoopLog:
    def info(self, *args, **kwargs):
        pass

    def warning(self, *args, **kwargs):
        pass


class _CapturingLog:
    """Log mock that records messages so tests can assert which factor path
    fired (DIRECT vs back-out median) inside compute_shadow_targets."""

    def __init__(self):
        self.messages: list[tuple[str, str]] = []

    def info(self, msg, *args, **kwargs):
        self.messages.append(("info", str(msg)))

    def warning(self, msg, *args, **kwargs):
        self.messages.append(("warning", str(msg)))

    def debug(self, msg, *args, **kwargs):
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
        shadow_targets={},
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
        shadow_targets={},
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


def test_hysteresis_shadow_below_min_size_gets_flagged():
    """Regression: hysteresis-shadow injection rewrites target after the initial
    min-size check, so a sub-$10 delta produced by the shadow target must still
    receive the below_min_trade_size warning during post-processing.
    Reproduces the DOT case (current=-10.75, shadow target=-7.00, delta=+3.75)."""
    trade_plan = pd.DataFrame(
        {
            "current_notional": [-10.75],
            "target_notional": [-7.00],  # already injected by caller
            "delta_notional": [3.75],     # already recomputed by caller
            "delta_weight": [0.0],
            "reason": ["hysteresis_shadow"],
            "warnings": [""],
        },
        index=["DOTUSDT_PERP"],
    )
    trade_plan.attrs["current_equity"] = 3898.09

    apply_hard_exits_and_reduce_only(
        trade_plan=trade_plan,
        new_snapshot=None,
        prev_snapshot=None,
        data_status_instruments={},
        delisted_instruments=[],
        banned_instruments=set(),
        log=_NoopLog(),
        reduce_only_instruments=set(),
        shadow_targets={},
        min_notional_position=10.0,
    )

    assert trade_plan.loc["DOTUSDT_PERP", "reason"] == "hysteresis_shadow"
    assert "below_min_trade_size" in trade_plan.loc["DOTUSDT_PERP", "warnings"]
    # delta_weight should also have been recomputed against the injected delta
    assert trade_plan.loc["DOTUSDT_PERP", "delta_weight"] == pytest.approx(
        3.75 / 3898.09
    )


def test_hysteresis_shadow_above_min_size_not_flagged():
    """Counterpoint: a hysteresis-shadow row whose delta clears the minimum
    must NOT get the below_min_trade_size warning."""
    trade_plan = pd.DataFrame(
        {
            "current_notional": [905.15],
            "target_notional": [1078.71],
            "delta_notional": [173.56],
            "delta_weight": [0.0],
            "reason": ["hysteresis_shadow"],
            "warnings": [""],
        },
        index=["TRXUSDT_PERP"],
    )
    trade_plan.attrs["current_equity"] = 3898.09

    apply_hard_exits_and_reduce_only(
        trade_plan=trade_plan,
        new_snapshot=None,
        prev_snapshot=None,
        data_status_instruments={},
        delisted_instruments=[],
        banned_instruments=set(),
        log=_NoopLog(),
        reduce_only_instruments=set(),
        shadow_targets={},
        min_notional_position=10.0,
    )

    assert trade_plan.loc["TRXUSDT_PERP", "reason"] == "hysteresis_shadow"
    assert "below_min_trade_size" not in trade_plan.loc["TRXUSDT_PERP", "warnings"]


# ---------------------------------------------------------------------------
# compute_shadow_targets — direct-read sizing factor
# ---------------------------------------------------------------------------


def _build_shadow_target_fixture(tmp_path: Path, with_idm: bool):
    """Build a minimal backtest-output dir + dataset + config sufficient for
    compute_shadow_targets to run end-to-end. Returns the backtest dir."""
    backtest_dir = tmp_path / "backtest_latest"
    backtest_dir.mkdir()

    # Config with notional_trading_capital
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        dedent(
            """
            notional_trading_capital: 9745.23
            percentage_vol_target: 25.0
            """
        ).strip()
    )

    # Dataset with enough close history for vol calc (need >= vol_days+5 = 68)
    dates = pd.date_range("2026-01-01", periods=120, freq="D")
    rows = []
    for inst, base_price in [("ACTIVE_PERP", 100.0), ("TRXUSDT_PERP", 0.32)]:
        for i, d in enumerate(dates):
            # Mild noise so robust_vol_calc returns a non-degenerate value
            rows.append(
                {
                    "date": d,
                    "instrument": inst,
                    "close": base_price * (1.0 + 0.005 * ((i % 7) - 3)),
                }
            )
    dataset_path = tmp_path / "dataset.parquet"
    pd.DataFrame(rows).to_parquet(dataset_path, index=False)

    # Diagnostics: one active row and one hysteresis-zone row
    last_date = dates[-1]
    diag_cols = {
        "date": [last_date, last_date],
        "instrument": ["ACTIVE_PERP", "TRXUSDT_PERP"],
        "position": [50.0, 0.0],         # hysteresis target = 0
        "combined_forecast": [10.0, 20.0],
        "instrument_weight": [0.02778, 0.0],
        "fdm": [2.5, 2.5],
    }
    if with_idm:
        diag_cols["idm"] = [2.224, 2.224]
    pd.DataFrame(diag_cols).to_parquet(backtest_dir / "diagnostics.parquet", index=False)

    # Metadata pointing at the dataset + config
    meta = {
        "data_path": str(dataset_path),
        "config_path": str(config_path),
        "dynamic_universe_config": {"vol_window": 63},
    }
    (backtest_dir / "metadata.json").write_text(json.dumps(meta))

    return backtest_dir


def test_compute_shadow_targets_uses_direct_read_when_idm_present(tmp_path):
    """When diagnostics expose `idm` and `instrument_weight`, the sizing factor
    must be computed directly as capital × IDM × instrument_weight, not backed
    out from active positions (which is biased low by forecast caps)."""
    backtest_dir = _build_shadow_target_fixture(tmp_path, with_idm=True)

    targets = compute_shadow_targets(
        {"TRXUSDT_PERP"}, backtest_dir, log=_NoopLog()
    )

    assert "TRXUSDT_PERP" in targets
    # Expected direct factor = 9745.23 × 2.224 × 0.02778 ≈ 602.1
    # Shadow = factor × (fc/10) × (vol_target / vpa)
    # The exact vpa depends on the synthetic series; we just need the
    # *directly-read* computation to be used (so the result must scale
    # proportionally with capital, IDM, and inst_weight — not with the
    # back-out median which would deviate). Verify by re-running with
    # capital halved and confirming the target halves.
    config_path = next(tmp_path.glob("config.yaml"))
    config_path.write_text(
        dedent(
            """
            notional_trading_capital: 4872.615
            percentage_vol_target: 25.0
            """
        ).strip()
    )
    targets_half = compute_shadow_targets(
        {"TRXUSDT_PERP"}, backtest_dir, log=_NoopLog()
    )
    assert targets_half["TRXUSDT_PERP"] == pytest.approx(
        targets["TRXUSDT_PERP"] / 2.0, rel=1e-6
    )


def test_compute_shadow_targets_falls_back_to_back_out_without_idm(tmp_path):
    """When `idm`/`instrument_weight` are missing (older diagnostics schema),
    must fall back to the median back-out so historical re-reads still work."""
    backtest_dir = _build_shadow_target_fixture(tmp_path, with_idm=False)

    targets = compute_shadow_targets(
        {"TRXUSDT_PERP"}, backtest_dir, log=_NoopLog()
    )

    # Must still return *something* — fallback path engaged
    assert "TRXUSDT_PERP" in targets
    assert targets["TRXUSDT_PERP"] != 0.0


def test_compute_shadow_targets_handles_ffill_lagged_weights(tmp_path):
    """Regression for P1-1: ``system.portfolio.get_instrument_weights()``
    has a 1–2 day terminal NaN lag in production. The diagnostic writer at
    ``scripts/run_dynamic_universe_backtest.py`` must reindex
    ``instrument_weight`` with ``method='ffill'`` so the parquet has non-NaN
    values at the last date — without it, ``compute_shadow_targets`` falls
    through to the back-out median fallback and shadow targets swing 5×
    day-to-day (observed live 2026-05-09 → 2026-05-10).

    Two checks:

    1. Source trip-wire: the writer line that reindexes ``instrument_weight``
       must include ``method='ffill'``. If a future refactor drops it, this
       fires immediately.
    2. End-state behaviour: with diagnostics produced as the post-fix writer
       would emit them (non-NaN ``instrument_weight`` at last_date), the
       DIRECT factor path is logged and the back-out fallback is not.
    """
    import re

    # 1. Source trip-wire on the producer side.
    writer_src = (
        Path(__file__).parent.parent / "scripts" / "run_dynamic_universe_backtest.py"
    ).read_text()
    assert re.search(
        r"['\"]instrument_weight['\"]\s*:\s*instrument_weight\.reindex\([^)]*method=['\"]ffill['\"]",
        writer_src,
    ), (
        "P1-1 regression: writer must reindex instrument_weight with method='ffill' "
        "(scripts/run_dynamic_universe_backtest.py)"
    )

    # 2. End-state behaviour: DIRECT path fires when diagnostics are populated.
    backtest_dir = _build_shadow_target_fixture(tmp_path, with_idm=True)
    log = _CapturingLog()
    targets = compute_shadow_targets({"TRXUSDT_PERP"}, backtest_dir, log=log)

    factor_logs = [m for _, m in log.messages if "Shadow target implied factor" in m]
    assert factor_logs, "compute_shadow_targets should log its implied factor"
    assert "(direct)" in factor_logs[0], (
        f"DIRECT factor path should fire when idm and instrument_weight are populated; "
        f"got: {factor_logs[0]!r}"
    )
    assert "back-out" not in factor_logs[0], (
        f"Back-out fallback must not fire when DIRECT inputs are present; "
        f"got: {factor_logs[0]!r}"
    )
    assert targets.get("TRXUSDT_PERP", 0.0) != 0.0


def test_compute_shadow_targets_stable_across_repeated_calls(tmp_path):
    """Direct-read sizing factor must be deterministic: two calls with the
    same inputs must produce identical shadow targets to the cent. The old
    median-back-out approach satisfied this only because all inputs were
    truly identical; a meaningful regression test is that the direct path
    is invariant to spurious differences in active-instrument *positions*
    (which used to shift the back-out median)."""
    backtest_dir = _build_shadow_target_fixture(tmp_path, with_idm=True)

    targets_a = compute_shadow_targets(
        {"TRXUSDT_PERP"}, backtest_dir, log=_NoopLog()
    )

    # Perturb the active row's position — under the old back-out, this would
    # shift the median and therefore the shadow target. Under direct read,
    # it must not.
    diag_path = backtest_dir / "diagnostics.parquet"
    diag = pd.read_parquet(diag_path)
    diag.loc[diag["instrument"] == "ACTIVE_PERP", "position"] *= 1.5
    diag.to_parquet(diag_path, index=False)

    targets_b = compute_shadow_targets(
        {"TRXUSDT_PERP"}, backtest_dir, log=_NoopLog()
    )

    assert targets_b["TRXUSDT_PERP"] == pytest.approx(
        targets_a["TRXUSDT_PERP"], rel=1e-9
    )
