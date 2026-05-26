"""
Regression tests for scripts/download_macro_factors.py.

Pinning to D-1 UTC: the script's `end` argument fed to yfinance must be
today-UTC (yfinance treats `end` as exclusive, so this fetches through
yesterday-UTC). Pre-2026-05-25 the script used `date.today()` (local TZ),
which on non-UTC shells made the output file content depend on what
local-clock hour the script ran — a partial-day-bug generalization that
surfaced during the dev↔prod parity test.
"""

from datetime import date, datetime, timezone
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch, MagicMock

import pandas as pd
import pytest

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.download_macro_factors import download_macro_factors  # noqa: E402


class TestEndDatePinning:
    """The `end` arg fed to yfinance.download must be today-UTC by default."""

    @staticmethod
    def _stub_yf_download(captured_calls):
        """Return a mock yf.download that records its kwargs and returns a
        minimal valid DataFrame so the surrounding parsing logic doesn't error."""

        def fake_download(ticker, start=None, end=None, **kwargs):
            captured_calls.append({"ticker": ticker, "start": start, "end": end})
            # Return a tiny but valid yfinance-shaped frame so downstream
            # parsing / dropna / parquet write don't error.
            idx = pd.DatetimeIndex(["2024-01-02", "2024-01-03"])
            return pd.DataFrame({"Close": [100.0, 101.0]}, index=idx)

        return fake_download

    def test_default_end_is_today_utc_not_local(self, tmp_path):
        """When no `end` is passed, the script should use today-UTC.

        Concretely: `end = datetime.now(timezone.utc).date().strftime('%Y-%m-%d')`.
        This is what makes the output deterministic regardless of local TZ.
        """
        captured = []
        out = tmp_path / "macro_factors.parquet"

        # We can't fix "now" perfectly, but we can assert the captured end
        # equals today-UTC at the time of the call. Compute it the same way
        # the script does, immediately before invoking.
        expected_end = datetime.now(timezone.utc).date().strftime("%Y-%m-%d")

        with patch("yfinance.download", side_effect=self._stub_yf_download(captured)):
            download_macro_factors(
                start="2024-01-01",
                output_path=str(out),
            )

        # Every per-ticker call should have used the same end, equal to today-UTC.
        ends = {c["end"] for c in captured}
        assert ends == {expected_end}, (
            f"Expected all calls to use end={expected_end!r} (today-UTC), "
            f"got {ends}. Likely regression to `date.today()` local-TZ."
        )

    def test_explicit_end_is_honored(self, tmp_path):
        """When `end` is passed explicitly, the script must use it verbatim."""
        captured = []
        out = tmp_path / "macro_factors.parquet"
        explicit_end = "2024-06-15"

        with patch("yfinance.download", side_effect=self._stub_yf_download(captured)):
            download_macro_factors(
                start="2024-01-01",
                output_path=str(out),
                end=explicit_end,
            )

        ends = {c["end"] for c in captured}
        assert ends == {explicit_end}, (
            f"Explicit end={explicit_end!r} not honored — got {ends}."
        )

    def test_end_is_string_not_date_object(self, tmp_path):
        """yfinance accepts both string and date but the script's contract is
        YYYY-MM-DD strings. Defensive check that we don't accidentally pass
        a `datetime.date` object that downstream code might mishandle."""
        captured = []
        out = tmp_path / "macro_factors.parquet"

        with patch("yfinance.download", side_effect=self._stub_yf_download(captured)):
            download_macro_factors(start="2024-01-01", output_path=str(out))

        assert all(isinstance(c["end"], str) for c in captured), (
            "end should be a YYYY-MM-DD string, not a date object."
        )
        # And the format should be parseable
        for c in captured:
            datetime.strptime(c["end"], "%Y-%m-%d")
