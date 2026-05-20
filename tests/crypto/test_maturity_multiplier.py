"""
Unit tests for token-maturity multiplier formula.

Tests the mathematical invariants directly via a formula-replication helper —
no parquet loading, no parquetCryptoPerpsSimData instantiation (mirrors the
pattern in test_funding_oi_conc_mr.py).
"""

import numpy as np
import pandas as pd
import pytest


# ---------------------------------------------------------------------------
# Helper: replicate the maturity multiplier formula. If the formula in
# scripts/build_maturity_multiplier_panel.py changes, this helper must
# change too; the formula-divergence detection comes from running both
# against the same fixture.
#
#   penalty(i, t)    = max(0, (T - days_since_listing(i, t)) / T)      ∈ [0, 1]
#   multiplier(i, t) = 1 - β × penalty(i, t)                            ∈ [1-β, 1]
#
# Pre-launch dates (t < launch_date[i]) return NaN so the downstream
# harness's `.fillna(1.0)` yields identity for any instrument that doesn't
# exist yet on that date.
# ---------------------------------------------------------------------------

def _maturity_multiplier_formula(
    launch_dates: pd.Series,
    index: pd.DatetimeIndex,
    T: int = 365,
    beta: float = 0.5,
) -> pd.DataFrame:
    """Replicate scripts/build_maturity_multiplier_panel.py:build_panel()."""
    instruments = list(launch_dates.index)
    # days_since: rows = dates, cols = instruments
    date_grid = np.tile(index.values, (len(instruments), 1)).T  # (T, N)
    launch_grid = np.tile(launch_dates.values, (len(index), 1))   # (T, N)
    delta_days = (date_grid - launch_grid).astype("timedelta64[D]").astype("float64")
    pre_launch = delta_days < 0
    days_since = np.where(pre_launch, np.nan, delta_days)
    penalty = np.clip((T - days_since) / T, 0.0, 1.0)
    mult = 1.0 - beta * penalty
    return pd.DataFrame(mult, index=index, columns=instruments)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

T = 365
BETA = 0.5


@pytest.fixture(scope="module")
def fixed_today():
    """Anchor date so the tests are deterministic regardless of wall-clock."""
    return pd.Timestamp("2026-05-19")


@pytest.fixture(scope="module")
def date_index(fixed_today):
    """3 years of daily dates ending at fixed_today."""
    return pd.date_range(end=fixed_today, periods=3 * 365, freq="D")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_identity_for_mature_instrument(fixed_today, date_index):
    """An instrument launched ≥365 days ago must have multiplier = 1.0 today."""
    launch = pd.Series(
        {"OLDUSDT_PERP": fixed_today - pd.Timedelta(days=366)},
    )
    panel = _maturity_multiplier_formula(launch, date_index, T=T, beta=BETA)
    today_value = panel.loc[fixed_today, "OLDUSDT_PERP"]
    assert today_value == pytest.approx(1.0, abs=1e-12)


def test_max_penalty_at_listing_day(fixed_today, date_index):
    """An instrument listed today must have multiplier = 1 - β = 0.5."""
    launch = pd.Series(
        {"NEWUSDT_PERP": fixed_today},
    )
    panel = _maturity_multiplier_formula(launch, date_index, T=T, beta=BETA)
    today_value = panel.loc[fixed_today, "NEWUSDT_PERP"]
    assert today_value == pytest.approx(1.0 - BETA, abs=1e-12)


def test_linear_ramp_at_midpoint(fixed_today, date_index):
    """An instrument launched T/2 days ago must have multiplier = 1 - β/2 = 0.75."""
    launch = pd.Series(
        {"MIDUSDT_PERP": fixed_today - pd.Timedelta(days=T // 2)},
    )
    panel = _maturity_multiplier_formula(launch, date_index, T=T, beta=BETA)
    today_value = panel.loc[fixed_today, "MIDUSDT_PERP"]
    # T=365, T//2=182, so days_since=182, penalty=(365-182)/365=0.5014, mult=1-0.25*~1
    # Exactly: penalty = (365 - 182) / 365 = 183/365 ≈ 0.50137
    expected = 1.0 - BETA * (T - (T // 2)) / T
    assert today_value == pytest.approx(expected, abs=1e-12)
    # Sanity: value should be very close to 0.75
    assert 0.745 < today_value < 0.755


def test_panel_bounds(fixed_today, date_index):
    """All non-NaN multiplier values must sit in [1-β, 1.0]."""
    rng = np.random.default_rng(13)
    n = 50
    # Mix of ancient (many years), recent, and brand-new launches
    offsets = rng.integers(low=0, high=5 * 365, size=n)
    launch = pd.Series(
        {f"INST{i:03d}": fixed_today - pd.Timedelta(days=int(off)) for i, off in enumerate(offsets)}
    )
    panel = _maturity_multiplier_formula(launch, date_index, T=T, beta=BETA)
    finite = panel.stack().dropna()
    assert len(finite) > 0
    assert finite.min() >= 1.0 - BETA - 1e-12
    assert finite.max() <= 1.0 + 1e-12


def test_pre_launch_returns_nan(fixed_today, date_index):
    """Dates strictly before launch_date must be NaN so the harness's
    `.fillna(1.0)` at forecast_combine_gated.py:179 yields identity instead
    of applying a penalty to a non-existent instrument."""
    # Launch ~2 years before fixed_today so we can also check a post-365d cell
    # that's still inside date_index (which ends at fixed_today).
    launch_date = fixed_today - pd.Timedelta(days=2 * 365 + 10)
    launch = pd.Series({"INSTUSDT_PERP": launch_date})
    panel = _maturity_multiplier_formula(launch, date_index, T=T, beta=BETA)
    # All cells before launch_date must be NaN.
    pre = panel.loc[panel.index < launch_date, "INSTUSDT_PERP"]
    assert pre.isna().all()
    # The launch_date itself is age 0 → multiplier = 0.5 (not NaN).
    at_launch = panel.loc[launch_date, "INSTUSDT_PERP"]
    assert at_launch == pytest.approx(1.0 - BETA, abs=1e-12)
    # 365 days after launch → multiplier = 1.0.
    post = panel.loc[launch_date + pd.Timedelta(days=365), "INSTUSDT_PERP"]
    assert post == pytest.approx(1.0, abs=1e-12)
