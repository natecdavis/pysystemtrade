"""
Unit tests for data_status.py V1 extensions (day-level reporting).

Tests:
- get_last_available_date() with Vision + API cache
- compute_dates_and_staleness() two-date concept
- validate_as_of_date() tolerance checking
- generate_data_status_report_v1() day-level reports
"""

import tempfile
from datetime import date, timedelta
from pathlib import Path
import pytest

from sysdata.crypto.data_status import (
    get_last_available_date,
    compute_staleness_days,
    compute_dates_and_staleness,
    validate_as_of_date,
    generate_data_status_report_v1
)


class TestGetLastAvailableDate:
    """Test get_last_available_date() with multiple data sources."""

    def test_no_data_returns_none(self):
        """If no data exists, should return None."""
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)
            result = get_last_available_date(data_dir, 'BTCUSDT', 'klines')
            assert result is None

    def test_vision_monthly_only(self):
        """Should extract last day from monthly ZIP filename."""
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)

            # Create Vision monthly ZIP structure
            klines_dir = data_dir / 'klines' / 'BTCUSDT'
            klines_dir.mkdir(parents=True)

            # Create dummy ZIP files
            (klines_dir / 'BTCUSDT-1d-2025-11.zip').touch()
            (klines_dir / 'BTCUSDT-1d-2025-12.zip').touch()

            result = get_last_available_date(data_dir, 'BTCUSDT', 'klines')

            # Last day of December 2025
            assert result == date(2025, 12, 31)

    def test_api_cache_overrides_vision_monthly(self):
        """API cache should override Vision monthly if more recent."""
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)

            # Create Vision monthly (December 2025)
            klines_dir = data_dir / 'klines' / 'BTCUSDT'
            klines_dir.mkdir(parents=True)
            (klines_dir / 'BTCUSDT-1d-2025-12.zip').touch()

            # Create API cache (January 2026)
            api_cache_dir = data_dir / 'api_cache' / 'BTCUSDT'
            api_cache_dir.mkdir(parents=True)
            (api_cache_dir / '2026-01-15_klines.parquet').touch()
            (api_cache_dir / '2026-01-16_klines.parquet').touch()

            result = get_last_available_date(data_dir, 'BTCUSDT', 'klines')

            # Should pick API cache date
            assert result == date(2026, 1, 16)

    def test_api_cache_with_range_filenames(self):
        """Should handle API cache filenames with date ranges."""
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)

            # Create API cache with range pattern
            api_cache_dir = data_dir / 'api_cache' / 'BTCUSDT'
            api_cache_dir.mkdir(parents=True)
            (api_cache_dir / 'BTCUSDT_2026-01-10_2026-01-12_klines.parquet').touch()
            (api_cache_dir / 'BTCUSDT_2026-01-13_2026-01-15_klines.parquet').touch()

            result = get_last_available_date(data_dir, 'BTCUSDT', 'klines')

            # Should extract latest date from range
            assert result == date(2026, 1, 15)


class TestComputeStaleness:
    """Test staleness computation."""

    def test_staleness_zero_when_up_to_date(self):
        """Staleness should be 0 when data is current."""
        expected = date(2026, 1, 27)
        last_data = date(2026, 1, 27)
        assert compute_staleness_days(expected, last_data) == 0

    def test_staleness_one_when_one_day_behind(self):
        """Staleness should be 1 when data is 1 day behind."""
        expected = date(2026, 1, 27)
        last_data = date(2026, 1, 26)
        assert compute_staleness_days(expected, last_data) == 1

    def test_staleness_multiple_days(self):
        """Staleness should increase linearly with lag."""
        expected = date(2026, 1, 27)
        last_data = date(2026, 1, 24)
        assert compute_staleness_days(expected, last_data) == 3

    def test_staleness_zero_when_ahead(self):
        """Staleness should be 0 when data is ahead of expected."""
        expected = date(2026, 1, 27)
        last_data = date(2026, 1, 28)
        assert compute_staleness_days(expected, last_data) == 0


class TestComputeDatesAndStaleness:
    """Test two-date concept computation."""

    def test_all_instruments_aligned(self):
        """When all instruments have same last date, dataset_as_of_date = expected."""
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)

            # Create data for 3 instruments, all with same last date
            for symbol in ['BTCUSDT', 'ETHUSDT', 'SOLUSDT']:
                klines_dir = data_dir / 'klines' / symbol
                klines_dir.mkdir(parents=True)
                (klines_dir / f'{symbol}-1d-2026-01.zip').touch()

                # Add API cache to bring up to date
                api_cache_dir = data_dir / 'api_cache' / symbol
                api_cache_dir.mkdir(parents=True)
                (api_cache_dir / '2026-01-27_klines.parquet').touch()

            expected_date = date(2026, 1, 27)
            instruments = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT']

            expected, dataset, staleness = compute_dates_and_staleness(
                data_dir, instruments, expected_date
            )

            # All aligned
            assert expected == date(2026, 1, 27)
            assert dataset == date(2026, 1, 27)
            assert staleness['BTCUSDT']['staleness_days'] == 0
            assert staleness['ETHUSDT']['staleness_days'] == 0
            assert staleness['SOLUSDT']['staleness_days'] == 0

    def test_one_instrument_lagging(self):
        """When one instrument lags, dataset_as_of_date = min, staleness reflects lag."""
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)

            # BTCUSDT and ETHUSDT up to date
            for symbol in ['BTCUSDT', 'ETHUSDT']:
                klines_dir = data_dir / 'klines' / symbol
                klines_dir.mkdir(parents=True)
                (klines_dir / f'{symbol}-1d-2026-01.zip').touch()

                api_cache_dir = data_dir / 'api_cache' / symbol
                api_cache_dir.mkdir(parents=True)
                (api_cache_dir / '2026-01-27_klines.parquet').touch()

            # SOLUSDT lagging by 1 day
            klines_dir = data_dir / 'klines' / 'SOLUSDT'
            klines_dir.mkdir(parents=True)
            (klines_dir / 'SOLUSDT-1d-2026-01.zip').touch()

            api_cache_dir = data_dir / 'api_cache' / 'SOLUSDT'
            api_cache_dir.mkdir(parents=True)
            (api_cache_dir / '2026-01-26_klines.parquet').touch()  # One day behind

            expected_date = date(2026, 1, 27)
            instruments = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT']

            expected, dataset, staleness = compute_dates_and_staleness(
                data_dir, instruments, expected_date
            )

            # Dataset as_of_date = min (2026-01-26)
            assert expected == date(2026, 1, 27)
            assert dataset == date(2026, 1, 26)

            # Staleness is relative to EXPECTED, not dataset
            assert staleness['BTCUSDT']['staleness_days'] == 0
            assert staleness['ETHUSDT']['staleness_days'] == 0
            assert staleness['SOLUSDT']['staleness_days'] == 1  # Lagging vs expected

    def test_missing_instrument_raises(self):
        """Should raise ValueError if any instrument has no data."""
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)

            # Only create data for BTCUSDT, not ETHUSDT
            klines_dir = data_dir / 'klines' / 'BTCUSDT'
            klines_dir.mkdir(parents=True)
            (klines_dir / 'BTCUSDT-1d-2026-01.zip').touch()

            instruments = ['BTCUSDT', 'ETHUSDT']
            expected_date = date(2026, 1, 27)

            with pytest.raises(ValueError, match="No data found for ETHUSDT"):
                compute_dates_and_staleness(data_dir, instruments, expected_date)


class TestValidateAsOfDate:
    """Test as_of_date validation with tolerance."""

    def test_pass_when_exact_match(self):
        """Validation should pass when as_of_date matches expected."""
        as_of = date(2026, 1, 27)
        expected = date(2026, 1, 27)

        # Should not raise
        validate_as_of_date(as_of, expected, tolerance_days=1)

    def test_pass_when_within_tolerance(self):
        """Validation should pass when lag is within tolerance."""
        as_of = date(2026, 1, 26)
        expected = date(2026, 1, 27)

        # 1 day lag, tolerance=1 → pass
        validate_as_of_date(as_of, expected, tolerance_days=1)

    def test_fail_when_exceeds_tolerance(self):
        """Validation should fail when lag exceeds tolerance."""
        as_of = date(2026, 1, 25)
        expected = date(2026, 1, 27)

        # 2 day lag, tolerance=1 → fail
        with pytest.raises(ValueError, match="as_of_date lag too large"):
            validate_as_of_date(as_of, expected, tolerance_days=1)

    def test_warn_when_ahead(self):
        """Should warn (but not fail) when as_of_date is ahead of expected."""
        as_of = date(2026, 1, 28)
        expected = date(2026, 1, 27)

        # Should not raise (just warn)
        validate_as_of_date(as_of, expected, tolerance_days=1)


class TestGenerateDataStatusReportV1:
    """Test day-level data status report generation."""

    def test_report_structure(self):
        """Report should have correct structure."""
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)

            # Create data for 2 instruments
            for symbol in ['BTCUSDT', 'ETHUSDT']:
                klines_dir = data_dir / 'klines' / symbol
                klines_dir.mkdir(parents=True)
                (klines_dir / f'{symbol}-1d-2026-01.zip').touch()

                api_cache_dir = data_dir / 'api_cache' / symbol
                api_cache_dir.mkdir(parents=True)
                (api_cache_dir / '2026-01-27_klines.parquet').touch()

            instruments = ['BTCUSDT', 'ETHUSDT']
            expected_date = date(2026, 1, 27)

            report = generate_data_status_report_v1(
                data_dir, instruments, expected_date
            )

            # Check top-level fields
            assert 'generated_at' in report
            assert report['expected_as_of_date'] == '2026-01-27'
            assert report['dataset_as_of_date'] == '2026-01-27'
            assert report['lag_policy_days'] == 1
            assert report['cadence'] == 'daily'

            # Check instruments
            assert 'instruments' in report
            assert 'BTCUSDT' in report['instruments']
            assert 'ETHUSDT' in report['instruments']

            # Check instrument fields
            btc = report['instruments']['BTCUSDT']
            assert btc['last_available_date'] == '2026-01-27'
            assert btc['staleness_days'] == 0
            assert btc['status'] == 'up_to_date'
            assert 'data_sources' in btc

            # Check summary
            summary = report['summary']
            assert summary['total_instruments'] == 2
            assert summary['up_to_date'] == 2
            assert summary['lagging'] == 0
            assert summary['max_staleness_days'] == 0
            assert summary['as_of_date_alignment'] == 'strict_pass'

    def test_report_with_lagging_instrument(self):
        """Report should correctly identify lagging instruments."""
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)

            # BTCUSDT up to date
            klines_dir = data_dir / 'klines' / 'BTCUSDT'
            klines_dir.mkdir(parents=True)
            (klines_dir / 'BTCUSDT-1d-2026-01.zip').touch()

            api_cache_dir = data_dir / 'api_cache' / 'BTCUSDT'
            api_cache_dir.mkdir(parents=True)
            (api_cache_dir / '2026-01-27_klines.parquet').touch()

            # ETHUSDT lagging
            klines_dir = data_dir / 'klines' / 'ETHUSDT'
            klines_dir.mkdir(parents=True)
            (klines_dir / 'ETHUSDT-1d-2026-01.zip').touch()

            api_cache_dir = data_dir / 'api_cache' / 'ETHUSDT'
            api_cache_dir.mkdir(parents=True)
            (api_cache_dir / '2026-01-25_klines.parquet').touch()  # 2 days behind

            instruments = ['BTCUSDT', 'ETHUSDT']
            expected_date = date(2026, 1, 27)

            report = generate_data_status_report_v1(
                data_dir, instruments, expected_date
            )

            # Check ETHUSDT is lagging
            eth = report['instruments']['ETHUSDT']
            assert eth['staleness_days'] == 2
            assert eth['status'] == 'lagging'
            assert len(eth['warnings']) > 0

            # Check summary
            summary = report['summary']
            assert summary['up_to_date'] == 1
            assert summary['lagging'] == 1
            assert summary['max_staleness_days'] == 2
            assert summary['as_of_date_alignment'] == 'strict_fail'

            # Dataset as_of_date should be min (2026-01-25)
            assert report['dataset_as_of_date'] == '2026-01-25'

    def test_data_sources_tracking(self):
        """Report should track which data sources were used."""
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)

            # Create Vision monthly + API cache
            klines_dir = data_dir / 'klines' / 'BTCUSDT'
            klines_dir.mkdir(parents=True)
            (klines_dir / 'BTCUSDT-1d-2025-12.zip').touch()

            api_cache_dir = data_dir / 'api_cache' / 'BTCUSDT'
            api_cache_dir.mkdir(parents=True)
            (api_cache_dir / '2026-01-27_klines.parquet').touch()

            instruments = ['BTCUSDT']
            expected_date = date(2026, 1, 27)

            report = generate_data_status_report_v1(
                data_dir, instruments, expected_date
            )

            sources = report['instruments']['BTCUSDT']['data_sources']
            assert sources['vision_monthly_through'] == '2025-12'
            assert sources['api_cache_through'] == '2026-01-27'
