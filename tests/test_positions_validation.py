"""
Unit tests for positions validation library.

Focus: Test validation logic and invariants, not formatting.
"""

import pytest
import pandas as pd
from datetime import datetime, timedelta, timezone
from sysdata.crypto.positions_validation import (
    validate_notional_arithmetic,
    validate_sign_consistency,
    check_units_confusion,
    check_stale_timestamps,
    validate_gross_leverage,
    check_concentration_risk,
    validate_positions_file,
    ValidationResult,
    ValidationIssue
)


class TestNotionalArithmetic:
    """Test notional arithmetic validation with realistic tolerances."""

    def test_exact_match(self):
        """Exact notional should pass."""
        contracts = 0.003
        mark_price = 45000.0
        notional = 135.0  # Exact

        is_valid, expected, diff = validate_notional_arithmetic(
            contracts, mark_price, notional
        )

        assert is_valid
        assert expected == 135.0
        assert diff == 0.0

    def test_within_absolute_tolerance(self):
        """Notional within $1.00 should pass."""
        contracts = 0.003
        mark_price = 45000.0
        notional = 135.50  # Off by $0.50

        is_valid, expected, diff = validate_notional_arithmetic(
            contracts, mark_price, notional, tolerance_usd=1.0
        )

        assert is_valid
        assert expected == 135.0
        assert diff == 0.50

    def test_within_relative_tolerance(self):
        """Large position with 0.1% error should pass."""
        contracts = 10.0
        mark_price = 45000.0
        notional = 450040.0  # Off by $40, but only 0.0089%

        is_valid, expected, diff = validate_notional_arithmetic(
            contracts, mark_price, notional,
            tolerance_usd=1.0,
            tolerance_pct=0.001  # 0.1%
        )

        assert is_valid
        assert expected == 450000.0
        assert diff == 40.0

    def test_exceeds_both_tolerances(self):
        """Error exceeding both absolute and relative tolerance should fail."""
        contracts = 0.05
        mark_price = 2500.0
        notional = 100.0  # Expected 125.0, off by $25.00 (20%)

        is_valid, expected, diff = validate_notional_arithmetic(
            contracts, mark_price, notional
        )

        assert not is_valid
        assert expected == 125.0
        assert diff == 25.0

    def test_negative_contracts_short_position(self):
        """Short position should use negative contracts."""
        contracts = -0.5
        mark_price = 350.0
        notional = -175.0

        is_valid, expected, diff = validate_notional_arithmetic(
            contracts, mark_price, notional
        )

        assert is_valid
        assert expected == -175.0

    def test_zero_position(self):
        """Zero position should pass."""
        is_valid, expected, diff = validate_notional_arithmetic(
            0.0, 0.0, 0.0
        )

        assert is_valid
        assert expected == 0.0
        assert diff == 0.0


class TestSignConsistency:
    """Test sign consistency validation."""

    def test_both_positive_long(self):
        """Long position: both positive."""
        is_valid, msg = validate_sign_consistency(0.5, 175.0)
        assert is_valid
        assert msg == ""

    def test_both_negative_short(self):
        """Short position: both negative."""
        is_valid, msg = validate_sign_consistency(-0.5, -175.0)
        assert is_valid
        assert msg == ""

    def test_both_zero(self):
        """Zero position: both zero."""
        is_valid, msg = validate_sign_consistency(0.0, 0.0)
        assert is_valid

    def test_positive_contracts_negative_notional(self):
        """Inconsistent: positive contracts, negative notional."""
        is_valid, msg = validate_sign_consistency(0.5, -175.0)
        assert not is_valid
        assert "long" in msg.lower() and "short" in msg.lower()

    def test_negative_contracts_positive_notional(self):
        """Inconsistent: negative contracts, positive notional."""
        is_valid, msg = validate_sign_consistency(-0.5, 175.0)
        assert not is_valid
        assert "short" in msg.lower() and "long" in msg.lower()


class TestUnitsConfusion:
    """Test units confusion heuristics (warnings only)."""

    def test_large_contract_count_warning(self):
        """Large contract count may indicate swapped columns."""
        warnings = check_units_confusion(
            contracts=5000.0,  # Suspiciously large
            notional=5000.0,
            mark_price=1.0
        )

        assert len(warnings) > 0
        assert any("Large contract count" in w for w in warnings)

    def test_small_notional_warning(self):
        """Small notional may indicate swapped columns."""
        warnings = check_units_confusion(
            contracts=0.1,
            notional=5.0,  # Suspiciously small
            mark_price=50.0
        )

        assert len(warnings) > 0
        assert any("Small notional" in w for w in warnings)

    def test_suspicious_low_price(self):
        """Very low price may indicate stale data."""
        warnings = check_units_confusion(
            contracts=100.0,
            notional=0.50,
            mark_price=0.005  # Very low
        )

        assert len(warnings) > 0
        assert any("Very low mark price" in w for w in warnings)

    def test_normal_position_no_warnings(self):
        """Normal position should have no warnings."""
        warnings = check_units_confusion(
            contracts=0.5,
            notional=175.0,
            mark_price=350.0
        )

        assert len(warnings) == 0


class TestStaleTimestamps:
    """Test stale timestamp detection."""

    def test_fresh_timestamp(self):
        """Recent timestamp should pass."""
        now = datetime.now(timezone.utc)
        timestamp = (now - timedelta(hours=1)).isoformat()

        error, warning = check_stale_timestamps(timestamp, critical_hours=48)

        assert error is None
        assert warning is None

    def test_critical_staleness_warning(self):
        """Timestamp older than 48h should warn."""
        now = datetime.now(timezone.utc)
        timestamp = (now - timedelta(hours=72)).isoformat()  # 3 days old

        error, warning = check_stale_timestamps(timestamp, critical_hours=48)

        assert error is None
        assert warning is not None
        assert "3 days" in warning

    def test_extreme_staleness_error(self):
        """Timestamp older than 7 days should error."""
        now = datetime.now(timezone.utc)
        timestamp = (now - timedelta(days=10)).isoformat()

        error, warning = check_stale_timestamps(
            timestamp, critical_hours=48, error_hours=168
        )

        assert error is not None
        assert "10 days" in error
        assert warning is None

    def test_invalid_timestamp_format(self):
        """Invalid timestamp should error."""
        error, warning = check_stale_timestamps("not-a-timestamp")

        assert error is not None
        assert "Invalid timestamp" in error


class TestGrossLeverage:
    """Test gross leverage validation."""

    def test_safe_leverage(self):
        """Leverage well below cap should pass."""
        error, warning = validate_gross_leverage(
            total_notional=4000.0,
            equity=5000.0,  # 0.8x
            warn_threshold=1.8,
            error_threshold=2.0
        )

        assert error is None
        assert warning is None

    def test_approaching_cap_warning(self):
        """Leverage between warning and error threshold should warn."""
        error, warning = validate_gross_leverage(
            total_notional=9200.0,
            equity=5000.0,  # 1.84x
            warn_threshold=1.8,
            error_threshold=2.0
        )

        assert error is None
        assert warning is not None
        assert "1.84x" in warning
        assert "approaching" in warning.lower()

    def test_exceeds_cap_error(self):
        """Leverage above cap should error."""
        error, warning = validate_gross_leverage(
            total_notional=10500.0,
            equity=5000.0,  # 2.1x
            warn_threshold=1.8,
            error_threshold=2.0
        )

        assert error is not None
        assert "2.10x" in error
        assert "exceeds" in error.lower()
        assert warning is None

    def test_zero_equity_error(self):
        """Zero equity should error."""
        error, warning = validate_gross_leverage(
            total_notional=1000.0,
            equity=0.0
        )

        assert error is not None
        assert "must be > 0" in error.lower()


class TestConcentrationRisk:
    """Test concentration risk detection."""

    def test_normal_position_no_warning(self):
        """Position < 50% of equity should not warn."""
        warning = check_concentration_risk(
            notional=2000.0,
            equity=5000.0,  # 40%
            warn_threshold=0.5
        )

        assert warning is None

    def test_concentrated_position_warning(self):
        """Position > 50% of equity should warn."""
        warning = check_concentration_risk(
            notional=3000.0,
            equity=5000.0,  # 60%
            warn_threshold=0.5
        )

        assert warning is not None
        assert "60" in warning
        assert "concentration" in warning.lower()


class TestValidatePositionsFile:
    """Test full positions file validation."""

    def test_valid_positions_file(self):
        """Valid positions file should pass."""
        now = datetime.now(timezone.utc).isoformat()
        positions_df = pd.DataFrame([
            {
                'instrument': 'BTCUSDT_PERP',
                'contracts': 0.003,
                'mark_price_usd': 45000.0,
                'notional_usd': 135.0,
                'timestamp': now,
                'notes': ''
            },
            {
                'instrument': 'ETHUSDT_PERP',
                'contracts': 0.0,
                'mark_price_usd': 0.0,
                'notional_usd': 0.0,
                'timestamp': now,
                'notes': ''
            }
        ])

        universe = ['BTCUSDT_PERP', 'ETHUSDT_PERP']
        result = validate_positions_file(positions_df, universe, equity=5000.0)

        assert result.passed
        assert result.overall_status == 'PASS'
        assert len(result.errors) == 0
        assert len(result.warnings) == 0

    def test_missing_columns_error(self):
        """Missing required columns should error immediately."""
        positions_df = pd.DataFrame([
            {'instrument': 'BTCUSDT_PERP', 'contracts': 0.1}
            # Missing mark_price_usd, notional_usd, timestamp
        ])

        result = validate_positions_file(
            positions_df, universe=['BTCUSDT_PERP'], equity=5000.0
        )

        assert not result.passed
        assert len(result.errors) == 1
        assert result.errors[0].check == 'schema'

    def test_notional_arithmetic_error(self):
        """Incorrect notional should error."""
        now = datetime.now(timezone.utc).isoformat()
        positions_df = pd.DataFrame([
            {
                'instrument': 'BTCUSDT_PERP',
                'contracts': 0.05,
                'mark_price_usd': 2500.0,
                'notional_usd': 100.0,  # Should be 125.0
                'timestamp': now,
                'notes': ''
            }
        ])

        result = validate_positions_file(
            positions_df, universe=['BTCUSDT_PERP'], equity=5000.0
        )

        assert not result.passed
        errors = [e for e in result.errors if e.check == 'notional_arithmetic']
        assert len(errors) == 1
        assert 'BTCUSDT_PERP' in errors[0].instrument

    def test_sign_consistency_error(self):
        """Inconsistent signs should error."""
        now = datetime.now(timezone.utc).isoformat()
        positions_df = pd.DataFrame([
            {
                'instrument': 'BNBUSDT_PERP',
                'contracts': -0.5,
                'mark_price_usd': 350.0,
                'notional_usd': 175.0,  # Should be negative!
                'timestamp': now,
                'notes': ''
            }
        ])

        result = validate_positions_file(
            positions_df, universe=['BNBUSDT_PERP'], equity=5000.0
        )

        assert not result.passed
        errors = [e for e in result.errors if e.check == 'sign_consistency']
        assert len(errors) == 1

    def test_missing_instruments_error(self):
        """Missing instruments should error (default behavior)."""
        now = datetime.now(timezone.utc).isoformat()
        positions_df = pd.DataFrame([
            {
                'instrument': 'BTCUSDT_PERP',
                'contracts': 0.0,
                'mark_price_usd': 0.0,
                'notional_usd': 0.0,
                'timestamp': now,
                'notes': ''
            }
        ])

        universe = ['BTCUSDT_PERP', 'ETHUSDT_PERP', 'BNBUSDT_PERP']
        result = validate_positions_file(
            positions_df, universe, equity=5000.0,
            allow_missing_instruments=False
        )

        assert not result.passed
        errors = [e for e in result.errors if e.check == 'missing_instruments']
        assert len(errors) == 1

    def test_missing_instruments_warning_when_allowed(self):
        """Missing instruments should warn when allowed."""
        now = datetime.now(timezone.utc).isoformat()
        positions_df = pd.DataFrame([
            {
                'instrument': 'BTCUSDT_PERP',
                'contracts': 0.0,
                'mark_price_usd': 0.0,
                'notional_usd': 0.0,
                'timestamp': now,
                'notes': ''
            }
        ])

        universe = ['BTCUSDT_PERP', 'ETHUSDT_PERP']
        result = validate_positions_file(
            positions_df, universe, equity=5000.0,
            allow_missing_instruments=True
        )

        assert result.passed  # Warnings don't fail
        assert result.overall_status == 'PASS_WITH_WARNINGS'
        warnings = [w for w in result.warnings if w.check == 'missing_instruments']
        assert len(warnings) == 1

    def test_stale_timestamp_warning(self):
        """Stale timestamp should warn."""
        stale_time = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        positions_df = pd.DataFrame([
            {
                'instrument': 'BTCUSDT_PERP',
                'contracts': 0.1,
                'mark_price_usd': 45000.0,
                'notional_usd': 4500.0,
                'timestamp': stale_time,
                'notes': ''
            }
        ])

        result = validate_positions_file(
            positions_df, universe=['BTCUSDT_PERP'], equity=5000.0,
            critical_staleness_hours=48
        )

        assert result.passed  # Warnings don't fail
        warnings = [w for w in result.warnings if w.check == 'stale_timestamp']
        assert len(warnings) == 1

    def test_gross_leverage_error(self):
        """Excessive gross leverage should error."""
        now = datetime.now(timezone.utc).isoformat()
        positions_df = pd.DataFrame([
            {
                'instrument': 'BTCUSDT_PERP',
                'contracts': 0.25,
                'mark_price_usd': 45000.0,
                'notional_usd': 11250.0,  # 2.25x leverage on 5000 equity
                'timestamp': now,
                'notes': ''
            }
        ])

        result = validate_positions_file(
            positions_df, universe=['BTCUSDT_PERP'], equity=5000.0
        )

        assert not result.passed
        errors = [e for e in result.errors if e.check == 'gross_leverage']
        assert len(errors) == 1
        assert '2.25x' in errors[0].message

    def test_metadata_populated(self):
        """Result metadata should be populated."""
        now = datetime.now(timezone.utc).isoformat()
        positions_df = pd.DataFrame([
            {
                'instrument': 'BTCUSDT_PERP',
                'contracts': 0.1,
                'mark_price_usd': 45000.0,
                'notional_usd': 4500.0,
                'timestamp': now,
                'notes': ''
            }
        ])

        result = validate_positions_file(
            positions_df, universe=['BTCUSDT_PERP'], equity=5000.0
        )

        assert result.metadata['equity'] == 5000.0
        assert result.metadata['universe_size'] == 1
        assert result.metadata['positions_count'] == 1
        assert result.metadata['total_abs_notional'] == 4500.0
        assert result.metadata['gross_leverage'] == 0.9


class TestValidationResult:
    """Test ValidationResult helper methods."""

    def test_passed_property(self):
        """Passed should be True when no errors."""
        result = ValidationResult()
        assert result.passed

        result.add_warning('TEST', 'test', 'warning')
        assert result.passed  # Warnings don't fail

        result.add_error('TEST', 'test', 'error')
        assert not result.passed

    def test_overall_status(self):
        """Overall status should reflect errors/warnings."""
        result = ValidationResult()
        assert result.overall_status == 'PASS'

        result.add_warning('TEST', 'test', 'warning')
        assert result.overall_status == 'PASS_WITH_WARNINGS'

        result.add_error('TEST', 'test', 'error')
        assert result.overall_status == 'FAIL'
