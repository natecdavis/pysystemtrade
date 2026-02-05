"""
Unit tests for data status reporting.
"""

import pytest
from pathlib import Path
from datetime import datetime
import tempfile
import zipfile

from sysdata.crypto.data_status import (
    get_last_available_month,
    get_missing_months,
    calculate_data_lag_days,
    get_expected_last_month,
    generate_data_status_report,
    validate_data_completeness
)


class TestGetLastAvailableMonth:
    """Test get_last_available_month function."""

    def test_no_data_directory(self, tmp_path):
        """Should return None if symbol directory doesn't exist."""
        result = get_last_available_month(tmp_path, 'BTCUSDT', 'klines')
        assert result is None

    def test_empty_directory(self, tmp_path):
        """Should return None if directory exists but has no ZIP files."""
        symbol_dir = tmp_path / 'klines' / 'BTCUSDT'
        symbol_dir.mkdir(parents=True)
        result = get_last_available_month(tmp_path, 'BTCUSDT', 'klines')
        assert result is None

    def test_single_month(self, tmp_path):
        """Should return month from single ZIP file."""
        symbol_dir = tmp_path / 'klines' / 'BTCUSDT'
        symbol_dir.mkdir(parents=True)

        # Create mock ZIP file
        zip_path = symbol_dir / 'BTCUSDT-1d-2024-01.zip'
        with zipfile.ZipFile(zip_path, 'w') as z:
            z.writestr('test.csv', 'dummy')

        result = get_last_available_month(tmp_path, 'BTCUSDT', 'klines')
        assert result == '2024-01'

    def test_multiple_months_returns_latest(self, tmp_path):
        """Should return latest month when multiple ZIPs present."""
        symbol_dir = tmp_path / 'klines' / 'BTCUSDT'
        symbol_dir.mkdir(parents=True)

        # Create multiple ZIP files
        for month in ['2024-01', '2024-02', '2024-03']:
            zip_path = symbol_dir / f'BTCUSDT-1d-{month}.zip'
            with zipfile.ZipFile(zip_path, 'w') as z:
                z.writestr('test.csv', 'dummy')

        result = get_last_available_month(tmp_path, 'BTCUSDT', 'klines')
        assert result == '2024-03'


class TestGetExpectedLastMonth:
    """Test get_expected_last_month function."""

    def test_m2_policy_january(self):
        """In January 2026, M-2 should be November 2025."""
        as_of = datetime(2026, 1, 15)
        result = get_expected_last_month(as_of, lag_months=2)
        assert result == '2025-11'

    def test_m2_policy_march(self):
        """In March 2026, M-2 should be January 2026."""
        as_of = datetime(2026, 3, 15)
        result = get_expected_last_month(as_of, lag_months=2)
        assert result == '2026-01'

    def test_m1_policy(self):
        """M-1 policy (less conservative)."""
        as_of = datetime(2026, 1, 15)
        result = get_expected_last_month(as_of, lag_months=1)
        assert result == '2025-12'

    def test_year_boundary(self):
        """Should handle year boundary correctly."""
        as_of = datetime(2026, 1, 1)
        result = get_expected_last_month(as_of, lag_months=2)
        assert result == '2025-11'


class TestCalculateDataLagDays:
    """Test calculate_data_lag_days function."""

    def test_lag_calculation(self):
        """Should calculate correct number of days lag."""
        last_month = '2025-11'  # November 2025 (ends 2025-11-30)
        as_of = datetime(2026, 1, 15)  # January 15, 2026

        lag = calculate_data_lag_days(last_month, as_of)
        # From Nov 30, 2025 to Jan 15, 2026:
        # December: 31 days
        # Jan 1-15: 15 days
        # Total: 46 days
        assert lag == 46

    def test_zero_lag(self):
        """Should return 0 if data is from same month end."""
        last_month = '2026-01'  # January 2026 (ends 2026-01-31)
        as_of = datetime(2026, 1, 31)  # Same day

        lag = calculate_data_lag_days(last_month, as_of)
        assert lag == 0


class TestGetMissingMonths:
    """Test get_missing_months function."""

    def test_all_months_present(self, tmp_path):
        """Should return empty list if all months present."""
        symbol_dir = tmp_path / 'klines' / 'BTCUSDT'
        symbol_dir.mkdir(parents=True)

        # Create ZIP files for Jan, Feb, Mar 2024
        for month in ['2024-01', '2024-02', '2024-03']:
            zip_path = symbol_dir / f'BTCUSDT-1d-{month}.zip'
            with zipfile.ZipFile(zip_path, 'w') as z:
                z.writestr('test.csv', 'dummy')

        missing = get_missing_months(tmp_path, 'BTCUSDT', '2024-01', '2024-03', 'klines')
        assert missing == []

    def test_missing_middle_month(self, tmp_path):
        """Should detect missing month in sequence."""
        symbol_dir = tmp_path / 'klines' / 'BTCUSDT'
        symbol_dir.mkdir(parents=True)

        # Create Jan and Mar, skip Feb
        for month in ['2024-01', '2024-03']:
            zip_path = symbol_dir / f'BTCUSDT-1d-{month}.zip'
            with zipfile.ZipFile(zip_path, 'w') as z:
                z.writestr('test.csv', 'dummy')

        missing = get_missing_months(tmp_path, 'BTCUSDT', '2024-01', '2024-03', 'klines')
        assert missing == ['2024-02']

    def test_all_months_missing(self, tmp_path):
        """Should return all months if none present."""
        # Don't create directory at all
        missing = get_missing_months(tmp_path, 'BTCUSDT', '2024-01', '2024-03', 'klines')
        assert missing == ['2024-01', '2024-02', '2024-03']


class TestGenerateDataStatusReport:
    """Test generate_data_status_report function."""

    def test_missing_data_status(self, tmp_path):
        """Should mark instruments with no data as missing."""
        instruments = ['BTCUSDT', 'ETHUSDT']
        report = generate_data_status_report(tmp_path, instruments, lag_months=2)

        assert report['summary']['missing_data'] == 2
        assert report['summary']['up_to_date'] == 0
        assert report['instruments']['BTCUSDT']['status'] == 'missing_data'
        assert report['instruments']['ETHUSDT']['status'] == 'missing_data'

    def test_up_to_date_status(self, tmp_path):
        """Should mark instruments with recent data as up_to_date."""
        # Create data for expected last month
        as_of = datetime(2026, 1, 15)
        expected_month = get_expected_last_month(as_of, lag_months=2)  # 2025-11

        symbol_dir = tmp_path / 'klines' / 'BTCUSDT'
        symbol_dir.mkdir(parents=True)

        zip_path = symbol_dir / f'BTCUSDT-1d-{expected_month}.zip'
        with zipfile.ZipFile(zip_path, 'w') as z:
            z.writestr('test.csv', 'dummy')

        instruments = ['BTCUSDT']
        report = generate_data_status_report(
            tmp_path, instruments, as_of_date=as_of, lag_months=2
        )

        assert report['instruments']['BTCUSDT']['status'] == 'up_to_date'
        assert report['summary']['up_to_date'] == 1
        assert report['summary']['missing_data'] == 0


class TestValidateDataCompleteness:
    """Test validate_data_completeness function."""

    def test_fails_on_missing_data(self):
        """Should raise ValueError if any instrument has no data."""
        report = {
            'instruments': {
                'BTCUSDT': {'status': 'missing_data'},
                'ETHUSDT': {'status': 'up_to_date'}
            },
            'summary': {
                'missing_data': 1,
                'up_to_date': 1,
                'lagging': 0
            }
        }

        with pytest.raises(ValueError, match="CRITICAL.*NO data"):
            validate_data_completeness(report, fail_on_missing=False)

    def test_warns_on_lagging_data(self):
        """Should log warning (not fail) on lagging data by default."""
        report = {
            'instruments': {
                'BTCUSDT': {'status': 'lagging', 'data_lag_days': 45}
            },
            'summary': {
                'missing_data': 0,
                'up_to_date': 0,
                'lagging': 1,
                'max_lag_days': 45
            },
            'expected_last_month': '2025-11'
        }

        # Should not raise
        result = validate_data_completeness(report, fail_on_missing=False)
        assert result is True

    def test_fails_on_lagging_data_strict(self):
        """Should raise ValueError on lagging data if fail_on_missing=True."""
        report = {
            'instruments': {
                'BTCUSDT': {'status': 'lagging', 'data_lag_days': 45}
            },
            'summary': {
                'missing_data': 0,
                'up_to_date': 0,
                'lagging': 1,
                'max_lag_days': 45
            },
            'expected_last_month': '2025-11'
        }

        with pytest.raises(ValueError, match="lagging"):
            validate_data_completeness(report, fail_on_missing=True)

    def test_passes_on_complete_data(self):
        """Should pass validation if all data is up to date."""
        report = {
            'instruments': {
                'BTCUSDT': {'status': 'up_to_date'},
                'ETHUSDT': {'status': 'up_to_date'}
            },
            'summary': {
                'missing_data': 0,
                'up_to_date': 2,
                'lagging': 0
            }
        }

        result = validate_data_completeness(report, fail_on_missing=True)
        assert result is True
