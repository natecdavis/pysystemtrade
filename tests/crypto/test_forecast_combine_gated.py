"""Tests for the consumer-side hook of the C4 multiplier panel
(`ForecastCombineGated._apply_walk_forward_multiplier`). Covers the three
silent-fallback branches the 2026-05-06 audit (F11) flagged as untested:

  * Config key absent — return forecast unchanged (feature flag off).
  * Instrument not a column in the panel — return forecast unchanged.
  * NaN cells in the panel — fillna(1.0), so a sparse panel cannot
    silently zero out the forecast.

Plus the post-multiply ±20 cap that protects against a multiplier saturating
the band beyond the standard forecast range.

Tests do NOT load a real panel from disk (the ±30h staleness gate is
covered by tests/test_c4_xgboost_combiner.py::TestAssertMultiplierPanelFresh).
Instead they pre-seed `combiner._wf_multiplier_panel` to bypass the load
path and exercise the per-instrument logic directly.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from systems.crypto_perps.forecast_combine_gated import ForecastCombineGated


class _StubConfig:
    """Minimal Config stand-in: only `get_element_or_default` is needed by
    `_apply_walk_forward_multiplier`."""

    def __init__(self, data: dict):
        self._data = data

    def get_element_or_default(self, key, default):
        return self._data.get(key, default)


class _StubSystem:
    def __init__(self, config_data: dict):
        self.config = _StubConfig(config_data)


def _make_combiner(config_data: dict, panel: pd.DataFrame | None = None):
    """Build a ForecastCombineGated with a stub System parent and (optionally)
    a pre-seeded multiplier panel so we don't need a real parquet on disk."""
    combiner = ForecastCombineGated()
    combiner._parent = _StubSystem(config_data)
    if panel is not None:
        combiner._wf_multiplier_panel = panel
    return combiner


def _forecast(values, dates=None) -> pd.Series:
    if dates is None:
        dates = pd.date_range("2026-05-01", periods=len(values), freq="D")
    return pd.Series(values, index=dates, name="combined_forecast")


class TestApplyWalkForwardMultiplier:
    def test_config_key_absent_returns_forecast_unchanged(self):
        """Feature-flag-off path: `walk_forward_multiplier_panel_path` not in
        config → return the forecast unchanged, do not load any panel."""
        combiner = _make_combiner({})  # no key
        original = _forecast([5.0, -3.0, 12.0])
        result = combiner._apply_walk_forward_multiplier("BTCUSDT_PERP", original)
        # Identity: object equality not required, but values must match exactly.
        pd.testing.assert_series_equal(result, original)
        # No panel should be loaded as a side effect.
        assert not hasattr(combiner, "_wf_multiplier_panel")

    def test_instrument_not_in_panel_returns_forecast_unchanged(self):
        """Panel exists but doesn't have a column for this instrument →
        return forecast unchanged. The combiner already loads the panel
        (cached state), so the load branch isn't re-traversed."""
        panel = pd.DataFrame(
            {"BTCUSDT_PERP": [1.0, 1.1, 1.2]},
            index=pd.date_range("2026-05-01", periods=3, freq="D"),
        )
        combiner = _make_combiner(
            {"walk_forward_multiplier_panel_path": "data/dummy.parquet"},
            panel=panel,
        )
        original = _forecast([5.0, -3.0, 12.0])
        # SOLUSDT_PERP is not a column in the panel
        result = combiner._apply_walk_forward_multiplier("SOLUSDT_PERP", original)
        pd.testing.assert_series_equal(result, original)

    def test_nan_cells_in_panel_become_identity_not_zero(self):
        """A sparse / partially-NaN multiplier column must NOT silently zero
        out the forecast. The fillna(1.0) is the load-bearing line — without
        it, a NaN cell would multiply the forecast by NaN (later treated as
        0) and silently kill that day's signal.

        Note: when forecast.index == panel.index, reindex(method='ffill')
        is a no-op (nothing to ffill INTO); NaN cells in-place are caught by
        fillna(1.0) and become identity multipliers — they do NOT inherit
        the prior day's multiplier. ffill propagation only fires when the
        panel index is sparser than the forecast index (see
        test_panel_index_misalignment_uses_ffill below)."""
        idx = pd.date_range("2026-05-01", periods=4, freq="D")
        # First and last day have multipliers; middle two are NaN.
        panel = pd.DataFrame(
            {"BTCUSDT_PERP": [1.5, np.nan, np.nan, 0.7]},
            index=idx,
        )
        combiner = _make_combiner(
            {"walk_forward_multiplier_panel_path": "data/dummy.parquet"},
            panel=panel,
        )
        # Constant +5 forecast across all four days.
        original = _forecast([5.0, 5.0, 5.0, 5.0], dates=idx)
        result = combiner._apply_walk_forward_multiplier("BTCUSDT_PERP", original)
        # Multipliers applied: [1.5, 1.0, 1.0, 0.7] → forecast [7.5, 5.0, 5.0, 3.5]
        # Critical assertion: NaN-day forecasts are 5.0 (identity), NOT 0.0
        # (which is what would happen if fillna(1.0) were missing).
        expected = _forecast([7.5, 5.0, 5.0, 3.5], dates=idx)
        pd.testing.assert_series_equal(
            result, expected, check_names=False
        )
        # And explicitly: no NaN-day cell silently became zero.
        assert (result != 0.0).all()

    def test_all_nan_column_uses_pure_identity(self):
        """If the entire instrument column is NaN (extreme sparse case),
        ffill has nothing to propagate; fillna(1.0) takes over and the
        forecast passes through unmodified."""
        idx = pd.date_range("2026-05-01", periods=3, freq="D")
        panel = pd.DataFrame(
            {"BTCUSDT_PERP": [np.nan, np.nan, np.nan]},
            index=idx,
        )
        combiner = _make_combiner(
            {"walk_forward_multiplier_panel_path": "data/dummy.parquet"},
            panel=panel,
        )
        original = _forecast([5.0, -3.0, 12.0], dates=idx)
        result = combiner._apply_walk_forward_multiplier("BTCUSDT_PERP", original)
        pd.testing.assert_series_equal(result, original, check_names=False)

    def test_post_multiplication_cap_enforced(self):
        """After multiplier × forecast, the result must be re-clipped to
        [-20, 20]. Constructed with a ceiling-saturated multiplier (1.5) on
        a forecast already at +18 — pre-clip product would be 27."""
        idx = pd.date_range("2026-05-01", periods=2, freq="D")
        panel = pd.DataFrame(
            {"BTCUSDT_PERP": [1.5, 1.5]},
            index=idx,
        )
        combiner = _make_combiner(
            {"walk_forward_multiplier_panel_path": "data/dummy.parquet"},
            panel=panel,
        )
        original = _forecast([18.0, -18.0], dates=idx)
        result = combiner._apply_walk_forward_multiplier("BTCUSDT_PERP", original)
        # Pre-clip: [27, -27] → clip to [20, -20]
        expected = _forecast([20.0, -20.0], dates=idx)
        pd.testing.assert_series_equal(result, expected, check_names=False)

    def test_normal_modulation_passes_through(self):
        """Sanity check: a well-behaved (panel × forecast) just multiplies
        through. A 0.8 multiplier on +10 forecast yields +8."""
        idx = pd.date_range("2026-05-01", periods=2, freq="D")
        panel = pd.DataFrame(
            {"BTCUSDT_PERP": [0.8, 1.2]},
            index=idx,
        )
        combiner = _make_combiner(
            {"walk_forward_multiplier_panel_path": "data/dummy.parquet"},
            panel=panel,
        )
        original = _forecast([10.0, 10.0], dates=idx)
        result = combiner._apply_walk_forward_multiplier("BTCUSDT_PERP", original)
        expected = _forecast([8.0, 12.0], dates=idx)
        pd.testing.assert_series_equal(result, expected, check_names=False)

    def test_panel_index_misalignment_uses_ffill(self):
        """Panel and forecast may not have identical DateTimeIndex (e.g.,
        the panel starts earlier or has gaps). reindex(method='ffill')
        carries the last-known multiplier forward."""
        # Panel sets multiplier on day 1 only.
        panel_idx = pd.DatetimeIndex(["2026-05-01"])
        panel = pd.DataFrame({"BTCUSDT_PERP": [1.5]}, index=panel_idx)
        combiner = _make_combiner(
            {"walk_forward_multiplier_panel_path": "data/dummy.parquet"},
            panel=panel,
        )
        # Forecast spans 3 days; days 2 and 3 inherit the day-1 multiplier.
        forecast_idx = pd.date_range("2026-05-01", periods=3, freq="D")
        original = _forecast([10.0, 10.0, 10.0], dates=forecast_idx)
        result = combiner._apply_walk_forward_multiplier("BTCUSDT_PERP", original)
        expected = _forecast([15.0, 15.0, 15.0], dates=forecast_idx)
        pd.testing.assert_series_equal(result, expected, check_names=False)
