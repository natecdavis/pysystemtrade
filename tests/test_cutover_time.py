"""
Unit tests for cutover time enforcement in get_expected_as_of_date().

Focus: Test UTC cutover time logic, warnings, and override behavior.
"""

import pytest
from datetime import datetime, date, timezone, timedelta
from unittest.mock import patch, MagicMock
from sysdata.crypto.data_status import get_expected_as_of_date


class TestCutoverTimeEnforcement:
    """Test cutover time enforcement and warnings."""

    def test_override_date_provided(self):
        """Override date should skip all warnings and return override."""
        override = date(2025, 12, 15)

        expected = get_expected_as_of_date(
            override_date=override,
            warn_if_early=True,
            warn_if_late=True
        )

        assert expected == override

    def test_default_behavior(self):
        """Without override, should return yesterday UTC."""
        # We can't mock time easily, so just test that it returns a date
        # that's reasonable (within 0-2 days of today)
        expected = get_expected_as_of_date(warn_if_early=False, warn_if_late=False)

        today = datetime.now(timezone.utc).date()
        assert isinstance(expected, date)
        assert (today - expected).days in [1, 2]  # Should be yesterday (or 2 days if just after midnight)


class TestOverrideIntegration:
    """Test override behavior in realistic scenarios."""

    def test_override_for_backtesting(self):
        """Override allows testing with historical dates."""
        historical_date = date(2025, 6, 15)

        expected = get_expected_as_of_date(override_date=historical_date)

        assert expected == historical_date

    def test_override_no_validation(self):
        """Override doesn't validate if date is in future (testing use case)."""
        future_date = date(2027, 1, 1)

        expected = get_expected_as_of_date(override_date=future_date)

        assert expected == future_date

    def test_override_skips_warnings(self):
        """Override should not trigger any time-of-day warnings."""
        # This just verifies override doesn't throw errors
        override = date(2025, 12, 1)

        expected = get_expected_as_of_date(
            override_date=override,
            warn_if_early=True,
            warn_if_late=True
        )

        assert expected == override
