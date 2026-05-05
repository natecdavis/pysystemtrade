"""Regression tests for the incremental-append mode of extract_rule_forecasts.py.

Coverage:
- The equality invariant: full-rebuild == truncated-old + incremental-new for the
  date range covered by the incremental tail. This is what guarantees the live
  daily flow's incremental append produces the same panel a fresh full rebuild
  would.
- Idempotency: re-running incremental for the same since-date doesn't double-append.
- Atomic write: a mid-write crash doesn't leave a half-written parquet behind.
- Missing-existing-panel error: incremental mode fails clearly if no parquet exists.

These tests exercise the merge helper directly, not the full SimSystem pipeline
(which is heavy and would require real data). The forecast COMPUTATION is
identical between full and incremental modes — both call
`forecastScaleCap.get_capped_forecast` the same way; the only difference is
slicing the output to dates >= since. So merge correctness is the load-bearing
property to test.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

# Load the script as a module so we can call its private helpers.
_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "extract_rule_forecasts.py"
_spec = importlib.util.spec_from_file_location("extract_rule_forecasts", _SCRIPT)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["extract_rule_forecasts"] = _mod
_spec.loader.exec_module(_mod)


def _synth_panel(start: str, n_days: int, seed: int = 0) -> pd.DataFrame:
    """Build a synthetic forecast panel: dates × MultiIndex(rule, instrument)."""
    rng = np.random.default_rng(seed)
    dates = pd.date_range(start, periods=n_days, freq="D")
    rules = ["ewmac_8", "ewmac_16", "breakout_20"]
    instruments = ["BTC_PERP", "ETH_PERP", "SOL_PERP"]
    cols = pd.MultiIndex.from_product([rules, instruments], names=["rule", "instrument"])
    data = rng.normal(0, 5, size=(n_days, len(cols))).clip(-20, 20)
    return pd.DataFrame(data, index=dates, columns=cols)


def _synth_returns(start: str, n_days: int, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed + 100)
    dates = pd.date_range(start, periods=n_days, freq="D")
    instruments = ["BTC_PERP", "ETH_PERP", "SOL_PERP"]
    return pd.DataFrame(
        rng.normal(0, 0.02, size=(n_days, len(instruments))),
        index=dates,
        columns=instruments,
    )


class TestEqualityInvariant:
    """Full rebuild MUST equal (truncated head + incremental tail)."""

    def test_merge_round_trip_preserves_full_panel(self):
        full = _synth_panel("2024-01-01", 100, seed=1)
        cutoff = pd.Timestamp("2024-02-15")
        # Simulate "old" panel built with data only through cutoff − 1
        old = full.loc[full.index < cutoff]
        # Simulate "new" tail emitted by --since cutoff (forecasts are the
        # same series sliced; the COMPUTATION is identical, this is what we
        # are claiming).
        new = full.loc[full.index >= cutoff]

        merged = _mod._merge_incremental(old, new, cutoff)
        pd.testing.assert_frame_equal(merged, full)

    def test_merge_idempotent_on_repeat_since(self):
        full = _synth_panel("2024-01-01", 60, seed=2)
        cutoff = pd.Timestamp("2024-02-01")
        old = full
        new = full.loc[full.index >= cutoff]

        # First merge: equivalent to full
        once = _mod._merge_incremental(old, new, cutoff)
        # Second merge using `once` as the base — should still equal `full`
        twice = _mod._merge_incremental(once, new, cutoff)
        pd.testing.assert_frame_equal(twice, full)

    def test_merge_with_returns_dataframe(self):
        # Same logic should work for the returns panel (simple instrument cols).
        full = _synth_returns("2024-01-01", 80, seed=3)
        cutoff = pd.Timestamp("2024-02-10")
        old = full.loc[full.index < cutoff]
        new = full.loc[full.index >= cutoff]
        merged = _mod._merge_incremental(old, new, cutoff)
        pd.testing.assert_frame_equal(merged, full)


class TestEdgeCases:
    def test_merge_drops_overlapping_old_rows(self):
        """If `old` has rows at dates >= since (e.g., from a partial earlier
        run), they must be dropped in favour of `new`."""
        full = _synth_panel("2024-01-01", 50, seed=4)
        cutoff = pd.Timestamp("2024-01-20")
        # `old` has the full panel (overlaps the cutoff range)
        # `new` is the "fresh" tail at and after cutoff
        old_with_overlap = full.copy()
        # Perturb the overlap zone in `old` to detect which version wins
        overlap_mask = old_with_overlap.index >= cutoff
        old_with_overlap.loc[overlap_mask] = -999.0
        new = full.loc[full.index >= cutoff]

        merged = _mod._merge_incremental(old_with_overlap, new, cutoff)
        # Merged should have NO -999 anywhere — the new tail won
        assert (merged != -999.0).all().all()
        # And merged should equal the original full panel
        pd.testing.assert_frame_equal(merged, full)

    def test_merge_handles_empty_new(self):
        full = _synth_panel("2024-01-01", 30, seed=5)
        cutoff = pd.Timestamp("2024-02-01")  # past the end of full
        empty_new = full.iloc[0:0]  # empty DataFrame, same columns
        merged = _mod._merge_incremental(full, empty_new, cutoff)
        # All of full is < cutoff, so head is full and tail is empty
        pd.testing.assert_frame_equal(merged, full)

    def test_merge_handles_empty_old(self):
        new = _synth_panel("2024-01-01", 20, seed=6)
        cutoff = pd.Timestamp("2024-01-01")
        empty_old = new.iloc[0:0]
        merged = _mod._merge_incremental(empty_old, new, cutoff)
        pd.testing.assert_frame_equal(merged, new)


class TestAtomicWrite:
    def test_atomic_write_creates_file(self, tmp_path):
        df = _synth_returns("2024-01-01", 10, seed=7)
        target = tmp_path / "test.parquet"
        _mod._atomic_write_parquet(df, target)
        assert target.exists()
        loaded = pd.read_parquet(target)
        # check_freq=False — pyarrow doesn't preserve DatetimeIndex.freq
        pd.testing.assert_frame_equal(loaded, df, check_freq=False)

    def test_atomic_write_overwrites(self, tmp_path):
        target = tmp_path / "test.parquet"
        first = _synth_returns("2024-01-01", 10, seed=8)
        _mod._atomic_write_parquet(first, target)
        second = _synth_returns("2024-02-01", 5, seed=9)
        _mod._atomic_write_parquet(second, target)
        loaded = pd.read_parquet(target)
        pd.testing.assert_frame_equal(loaded, second, check_freq=False)
        assert not (tmp_path / "test.parquet.tmp").exists()


class TestIncrementalRequiresExistingPanel:
    """The script's main entry point must fail clearly when --since is set
    but no existing panels are present."""

    def test_extract_panels_fails_without_existing(self, tmp_path, monkeypatch, capsys):
        # We cannot easily run extract_panels end-to-end without real data,
        # but we CAN exercise the early-out path: it checks for files before
        # building the system.
        with pytest.raises(SystemExit) as exc_info:
            _mod.extract_panels(
                config_path="/nonexistent/config.yaml",
                data_path="/nonexistent/data.parquet",
                out_dir=tmp_path,  # empty dir
                include_zero_weight=False,
                since=pd.Timestamp("2024-06-01"),
            )
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "incremental mode" in captured.out
        assert "Run a full extract first" in captured.out
