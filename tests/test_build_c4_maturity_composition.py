"""
Integration tests for the maturity-penalty composition in
scripts/build_c4_multiplier_panel.py.

These cover the live-wiring path that composes the daily C4 multiplier panel
with the token-maturity multiplier (β=0.5, T=365d, live ADOPT 2026-05-19).
"""

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def build_c4_module():
    """Load scripts/build_c4_multiplier_panel.py as a module (no scripts/__init__.py)."""
    spec = importlib.util.spec_from_file_location(
        "build_c4_multiplier_panel",
        REPO_ROOT / "scripts" / "build_c4_multiplier_panel.py",
    )
    module = importlib.util.module_from_spec(spec)
    # Make REPO_ROOT importable for the module's own internal imports
    sys.path.insert(0, str(REPO_ROOT))
    spec.loader.exec_module(module)
    return module


def _make_c4_panel(dates, instruments, fill: float = 1.0):
    """Helper: a C4-like multiplier panel with a constant fill value."""
    return pd.DataFrame(fill, index=dates, columns=instruments)


def _make_returns_panel(dates, instruments, launch_dates):
    """Helper: a synthetic returns panel where each column has NaN before
    its launch_date and a small synthetic return value afterwards."""
    returns = pd.DataFrame(np.nan, index=dates, columns=instruments)
    for inst, launch in launch_dates.items():
        mask = dates >= launch
        returns.loc[mask, inst] = 0.001  # placeholder positive value
    return returns


def test_mature_instruments_unaffected(build_c4_module):
    """Instruments launched > 365 days ago should pass through C4 unchanged."""
    today = pd.Timestamp("2026-05-20")
    dates = pd.date_range(end=today, periods=400, freq="D")
    instruments = ["OLDAUSDT_PERP", "OLDBUSDT_PERP"]
    launch_dates = {
        "OLDAUSDT_PERP": today - pd.Timedelta(days=400),
        "OLDBUSDT_PERP": today - pd.Timedelta(days=400),
    }
    returns = _make_returns_panel(dates, instruments, launch_dates)
    c4_panel = _make_c4_panel(dates, instruments, fill=1.25)

    composed = build_c4_module._apply_maturity_penalty(c4_panel, returns)
    today_row = composed.loc[today]
    assert today_row["OLDAUSDT_PERP"] == pytest.approx(1.25, abs=1e-12)
    assert today_row["OLDBUSDT_PERP"] == pytest.approx(1.25, abs=1e-12)


def test_brand_new_instrument_halved(build_c4_module):
    """An instrument launched today gets multiplier × 0.5 (β=0.5)."""
    today = pd.Timestamp("2026-05-20")
    # The "first non-NaN return" convention puts launch one day after the
    # first dataset row. Use 30 days of warm-up to keep the math clear.
    dates = pd.date_range(end=today, periods=30, freq="D")
    instruments = ["NEWUSDT_PERP"]
    launch_dates = {"NEWUSDT_PERP": today}  # first return on `today`
    returns = _make_returns_panel(dates, instruments, launch_dates)
    c4_panel = _make_c4_panel(dates, instruments, fill=1.20)

    composed = build_c4_module._apply_maturity_penalty(c4_panel, returns)
    # `today` is age-0 from the returns perspective (first non-NaN today) → maturity = 0.5
    assert composed.loc[today, "NEWUSDT_PERP"] == pytest.approx(0.5 * 1.20, abs=1e-12)


def test_midramp_instrument(build_c4_module):
    """Instrument launched 182 days ago gets ~0.75x multiplier (1 - 0.5*(365-182)/365)."""
    today = pd.Timestamp("2026-05-20")
    dates = pd.date_range(end=today, periods=400, freq="D")
    instruments = ["MIDUSDT_PERP"]
    launch_dates = {"MIDUSDT_PERP": today - pd.Timedelta(days=182)}
    returns = _make_returns_panel(dates, instruments, launch_dates)
    c4_panel = _make_c4_panel(dates, instruments, fill=1.0)

    composed = build_c4_module._apply_maturity_penalty(c4_panel, returns)
    expected = 1.0 - 0.5 * (365 - 182) / 365
    assert composed.loc[today, "MIDUSDT_PERP"] == pytest.approx(expected, abs=1e-12)
    # Sanity: should land near 0.75
    assert 0.745 < composed.loc[today, "MIDUSDT_PERP"] < 0.755


def test_pre_launch_unchanged(build_c4_module):
    """For dates BEFORE an instrument's launch_date, the composed value must
    equal the original C4 value (maturity=1.0 fillna applied to NaN cells)."""
    today = pd.Timestamp("2026-05-20")
    dates = pd.date_range(end=today, periods=400, freq="D")
    instruments = ["LATEUSDT_PERP"]
    # Launch 100 days ago — first 300 days are pre-launch
    launch_dates = {"LATEUSDT_PERP": today - pd.Timedelta(days=100)}
    returns = _make_returns_panel(dates, instruments, launch_dates)
    c4_panel = _make_c4_panel(dates, instruments, fill=1.30)

    composed = build_c4_module._apply_maturity_penalty(c4_panel, returns)
    # Pre-launch cell: must equal the original C4 cell (no penalty applied
    # because maturity multiplier is filled to 1.0 there).
    pre_date = today - pd.Timedelta(days=300)
    assert composed.loc[pre_date, "LATEUSDT_PERP"] == pytest.approx(1.30, abs=1e-12)


def test_missing_from_returns_unaffected(build_c4_module):
    """If C4 has an instrument that's not in the returns panel, composition
    leaves it unchanged (maturity=1.0 by default)."""
    today = pd.Timestamp("2026-05-20")
    dates = pd.date_range(end=today, periods=400, freq="D")
    c4_panel = _make_c4_panel(dates, ["GHOSTUSDT_PERP"], fill=1.10)
    # Returns panel has DIFFERENT instrument — GHOST is not present.
    returns = _make_returns_panel(
        dates, ["OTHERUSDT_PERP"], {"OTHERUSDT_PERP": today - pd.Timedelta(days=400)}
    )

    composed = build_c4_module._apply_maturity_penalty(c4_panel, returns)
    assert composed.loc[today, "GHOSTUSDT_PERP"] == pytest.approx(1.10, abs=1e-12)


def test_composition_is_elementwise_product(build_c4_module):
    """Sanity: composed[i,t] = c4[i,t] × maturity[i,t] for all cells."""
    today = pd.Timestamp("2026-05-20")
    dates = pd.date_range(end=today, periods=400, freq="D")
    rng = np.random.default_rng(0)
    instruments = [f"INST{i:03d}" for i in range(5)]
    launch_offsets_days = [400, 200, 100, 50, 0]
    launch_dates = {
        inst: today - pd.Timedelta(days=int(off))
        for inst, off in zip(instruments, launch_offsets_days)
    }
    returns = _make_returns_panel(dates, instruments, launch_dates)
    # Use a random C4 panel (in [0.5, 1.5] like real C4)
    c4_values = 0.5 + rng.random((len(dates), len(instruments)))
    c4_panel = pd.DataFrame(c4_values, index=dates, columns=instruments)

    composed = build_c4_module._apply_maturity_penalty(c4_panel, returns)

    # Compute the expected maturity panel independently and verify product
    today_row_expected = []
    for inst, off in zip(instruments, launch_offsets_days):
        penalty = max(0.0, (365 - off) / 365)
        mult = 1.0 - 0.5 * penalty
        today_row_expected.append(c4_panel.loc[today, inst] * mult)
    actual = composed.loc[today].values
    np.testing.assert_allclose(actual, today_row_expected, atol=1e-12)
