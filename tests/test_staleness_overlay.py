"""
Unit tests for staleness overlay module.

Tests all eligibility rules:
- No-position rules (staleness 0 vs ≥1)
- Existing-position rules (staleness 0 vs 1 vs ≥2)
- Decay formula verification (0.5^(staleness-1))
- Edge cases and validation
"""

from datetime import date
import pandas as pd
import pytest

from systems.crypto_perps.staleness_overlay import (
    apply_staleness_overlay,
    compute_staleness_summary,
    validate_staleness_inputs
)


class TestNoPositionRules:
    """Test rules for instruments with no current position."""

    def test_no_position_staleness_zero_allows_opening(self):
        """Staleness=0, no position → allow opening (use research target)."""
        targets = pd.Series({'BTCUSDT_PERP': 100.0})
        actual = pd.Series({'BTCUSDT_PERP': 0.0})
        staleness = pd.Series({'BTCUSDT_PERP': 0})

        result, audit = apply_staleness_overlay(
            targets, actual, staleness, date(2026, 1, 27)
        )

        # Should use research target (no override)
        assert result['BTCUSDT_PERP'] == 100.0
        assert 'BTCUSDT_PERP' not in audit  # No override applied

    def test_no_position_staleness_one_blocks_opening(self):
        """Staleness=1, no position → force target=0."""
        targets = pd.Series({'BTCUSDT_PERP': 100.0})
        actual = pd.Series({'BTCUSDT_PERP': 0.0})
        staleness = pd.Series({'BTCUSDT_PERP': 1})

        result, audit = apply_staleness_overlay(
            targets, actual, staleness, date(2026, 1, 27)
        )

        # Should block opening
        assert result['BTCUSDT_PERP'] == 0.0

        # Check audit trail
        assert 'BTCUSDT_PERP' in audit
        assert audit['BTCUSDT_PERP']['reason'] == 'no_new_positions_on_stale_data'
        assert audit['BTCUSDT_PERP']['staleness_days'] == 1

    def test_no_position_staleness_multiple_blocks_opening(self):
        """Staleness≥2, no position → force target=0."""
        targets = pd.Series({'BTCUSDT_PERP': 100.0})
        actual = pd.Series({'BTCUSDT_PERP': 0.0})
        staleness = pd.Series({'BTCUSDT_PERP': 3})

        result, audit = apply_staleness_overlay(
            targets, actual, staleness, date(2026, 1, 27)
        )

        # Should block opening
        assert result['BTCUSDT_PERP'] == 0.0
        assert audit['BTCUSDT_PERP']['staleness_days'] == 3


class TestExistingPositionRules:
    """Test rules for instruments with existing positions."""

    def test_existing_position_staleness_zero_normal_ops(self):
        """Staleness=0, position exists → normal operations (use research target)."""
        targets = pd.Series({'BTCUSDT_PERP': 150.0})
        actual = pd.Series({'BTCUSDT_PERP': 100.0})
        staleness = pd.Series({'BTCUSDT_PERP': 0})

        result, audit = apply_staleness_overlay(
            targets, actual, staleness, date(2026, 1, 27)
        )

        # Should use research target (no override)
        assert result['BTCUSDT_PERP'] == 150.0
        assert 'BTCUSDT_PERP' not in audit

    def test_staleness_one_caps_adds(self):
        """Staleness=1, position exists, target > actual → cap to actual."""
        targets = pd.Series({'BTCUSDT_PERP': 150.0})
        actual = pd.Series({'BTCUSDT_PERP': 100.0})
        staleness = pd.Series({'BTCUSDT_PERP': 1})

        result, audit = apply_staleness_overlay(
            targets, actual, staleness, date(2026, 1, 27)
        )

        # Should cap to actual (no adds allowed)
        assert result['BTCUSDT_PERP'] == 100.0

        # Check audit trail
        assert 'BTCUSDT_PERP' in audit
        assert audit['BTCUSDT_PERP']['reason'] == 'no_adds_on_day1_staleness'
        assert audit['BTCUSDT_PERP']['staleness_days'] == 1

    def test_staleness_one_allows_reduces(self):
        """Staleness=1, position exists, target < actual → allow reduce."""
        targets = pd.Series({'BTCUSDT_PERP': 50.0})
        actual = pd.Series({'BTCUSDT_PERP': 100.0})
        staleness = pd.Series({'BTCUSDT_PERP': 1})

        result, audit = apply_staleness_overlay(
            targets, actual, staleness, date(2026, 1, 27)
        )

        # Should allow reduce (no override)
        assert result['BTCUSDT_PERP'] == 50.0
        assert 'BTCUSDT_PERP' not in audit

    def test_staleness_one_allows_flatten(self):
        """Staleness=1, position exists, target=0 → allow flatten."""
        targets = pd.Series({'BTCUSDT_PERP': 0.0})
        actual = pd.Series({'BTCUSDT_PERP': 100.0})
        staleness = pd.Series({'BTCUSDT_PERP': 1})

        result, audit = apply_staleness_overlay(
            targets, actual, staleness, date(2026, 1, 27)
        )

        # Should allow flatten
        assert result['BTCUSDT_PERP'] == 0.0
        assert 'BTCUSDT_PERP' not in audit

    def test_staleness_two_forced_wind_down(self):
        """Staleness=2, position exists → forced wind-down (decay = 0.5)."""
        targets = pd.Series({'BTCUSDT_PERP': 150.0})  # Research wants more
        actual = pd.Series({'BTCUSDT_PERP': 100.0})
        staleness = pd.Series({'BTCUSDT_PERP': 2})

        result, audit = apply_staleness_overlay(
            targets, actual, staleness, date(2026, 1, 27)
        )

        # Should wind down: actual * 0.5^(2-1) = 100 * 0.5 = 50
        assert result['BTCUSDT_PERP'] == pytest.approx(50.0)

        # Check audit trail
        assert 'BTCUSDT_PERP' in audit
        assert audit['BTCUSDT_PERP']['reason'] == 'forced_wind_down'
        assert audit['BTCUSDT_PERP']['staleness_days'] == 2
        assert audit['BTCUSDT_PERP']['decay_factor'] == 0.5

    def test_staleness_three_forced_wind_down(self):
        """Staleness=3 → decay = 0.5^2 = 0.25."""
        targets = pd.Series({'BTCUSDT_PERP': 150.0})
        actual = pd.Series({'BTCUSDT_PERP': 100.0})
        staleness = pd.Series({'BTCUSDT_PERP': 3})

        result, audit = apply_staleness_overlay(
            targets, actual, staleness, date(2026, 1, 27)
        )

        # Should wind down: actual * 0.5^(3-1) = 100 * 0.25 = 25
        assert result['BTCUSDT_PERP'] == pytest.approx(25.0)
        assert audit['BTCUSDT_PERP']['decay_factor'] == 0.25

    def test_staleness_four_forced_wind_down(self):
        """Staleness=4 → decay = 0.5^3 = 0.125."""
        targets = pd.Series({'BTCUSDT_PERP': 150.0})
        actual = pd.Series({'BTCUSDT_PERP': 100.0})
        staleness = pd.Series({'BTCUSDT_PERP': 4})

        result, audit = apply_staleness_overlay(
            targets, actual, staleness, date(2026, 1, 27)
        )

        # Should wind down: actual * 0.5^(4-1) = 100 * 0.125 = 12.5
        assert result['BTCUSDT_PERP'] == pytest.approx(12.5)
        assert audit['BTCUSDT_PERP']['decay_factor'] == 0.125


class TestMultipleInstruments:
    """Test overlay with multiple instruments having different staleness."""

    def test_mixed_staleness_levels(self):
        """Test overlay with instruments at different staleness levels."""
        targets = pd.Series({
            'BTCUSDT_PERP': 100.0,  # staleness=0, no position → allow
            'ETHUSDT_PERP': 150.0,  # staleness=1, position → cap
            'SOLUSDT_PERP': 200.0,  # staleness=2, position → wind-down
            'XRPUSDT_PERP': 50.0,   # staleness=1, no position → block
        })

        actual = pd.Series({
            'BTCUSDT_PERP': 0.0,
            'ETHUSDT_PERP': 100.0,
            'SOLUSDT_PERP': 100.0,
            'XRPUSDT_PERP': 0.0,
        })

        staleness = pd.Series({
            'BTCUSDT_PERP': 0,
            'ETHUSDT_PERP': 1,
            'SOLUSDT_PERP': 2,
            'XRPUSDT_PERP': 1,
        })

        result, audit = apply_staleness_overlay(
            targets, actual, staleness, date(2026, 1, 27)
        )

        # BTC: staleness=0, no position → allow opening
        assert result['BTCUSDT_PERP'] == 100.0
        assert 'BTCUSDT_PERP' not in audit

        # ETH: staleness=1, position, target > actual → cap to actual
        assert result['ETHUSDT_PERP'] == 100.0
        assert audit['ETHUSDT_PERP']['reason'] == 'no_adds_on_day1_staleness'

        # SOL: staleness=2, position → wind-down (100 * 0.5 = 50)
        assert result['SOLUSDT_PERP'] == pytest.approx(50.0)
        assert audit['SOLUSDT_PERP']['reason'] == 'forced_wind_down'

        # XRP: staleness=1, no position → block
        assert result['XRPUSDT_PERP'] == 0.0
        assert audit['XRPUSDT_PERP']['reason'] == 'no_new_positions_on_stale_data'


class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_negative_actual_position(self):
        """Test with short position (negative actual)."""
        targets = pd.Series({'BTCUSDT_PERP': -150.0})
        actual = pd.Series({'BTCUSDT_PERP': -100.0})
        staleness = pd.Series({'BTCUSDT_PERP': 1})

        result, audit = apply_staleness_overlay(
            targets, actual, staleness, date(2026, 1, 27)
        )

        # staleness=1, |target| > |actual| → cap to actual (-100)
        assert result['BTCUSDT_PERP'] == -100.0

    def test_position_tolerance_threshold(self):
        """Test that position_tolerance works correctly."""
        targets = pd.Series({'BTCUSDT_PERP': 100.0})
        actual = pd.Series({'BTCUSDT_PERP': 1e-7})  # Below default tolerance
        staleness = pd.Series({'BTCUSDT_PERP': 1})

        result, audit = apply_staleness_overlay(
            targets, actual, staleness, date(2026, 1, 27)
        )

        # Should treat as no position → block
        assert result['BTCUSDT_PERP'] == 0.0
        assert audit['BTCUSDT_PERP']['reason'] == 'no_new_positions_on_stale_data'

    def test_actual_position_missing(self):
        """Test when actual position not in Series (treat as 0)."""
        targets = pd.Series({'BTCUSDT_PERP': 100.0})
        actual = pd.Series({})  # Empty
        staleness = pd.Series({'BTCUSDT_PERP': 1})

        result, audit = apply_staleness_overlay(
            targets, actual, staleness, date(2026, 1, 27)
        )

        # Should treat as no position → block
        assert result['BTCUSDT_PERP'] == 0.0


class TestStalenessSummary:
    """Test staleness summary statistics."""

    def test_summary_computation(self):
        """Test compute_staleness_summary."""
        staleness = pd.Series({
            'BTCUSDT_PERP': 0,
            'ETHUSDT_PERP': 0,
            'SOLUSDT_PERP': 1,
            'XRPUSDT_PERP': 2,
            'BNBUSDT_PERP': 3,
        })

        summary = compute_staleness_summary(staleness)

        assert summary['total_instruments'] == 5
        assert summary['up_to_date'] == 2
        assert summary['lagging_1day'] == 1
        assert summary['lagging_2plus_days'] == 2
        assert summary['max_staleness'] == 3
        assert summary['mean_staleness'] == pytest.approx(1.2)


class TestValidation:
    """Test input validation."""

    def test_validate_missing_staleness(self):
        """Should raise if staleness data missing for some instruments."""
        targets = pd.Series({'BTCUSDT_PERP': 100.0, 'ETHUSDT_PERP': 150.0})
        actual = pd.Series({'BTCUSDT_PERP': 0.0, 'ETHUSDT_PERP': 0.0})
        staleness = pd.Series({'BTCUSDT_PERP': 0})  # Missing ETHUSDT

        with pytest.raises(ValueError, match="Missing staleness data"):
            validate_staleness_inputs(targets, actual, staleness)

    def test_validate_negative_staleness(self):
        """Should raise if staleness is negative (data bug)."""
        targets = pd.Series({'BTCUSDT_PERP': 100.0})
        actual = pd.Series({'BTCUSDT_PERP': 0.0})
        staleness = pd.Series({'BTCUSDT_PERP': -1})  # Invalid

        with pytest.raises(ValueError, match="Negative staleness"):
            validate_staleness_inputs(targets, actual, staleness)

    def test_validate_warns_on_high_staleness(self):
        """Should warn if staleness is very high (>7 days)."""
        targets = pd.Series({'BTCUSDT_PERP': 100.0})
        actual = pd.Series({'BTCUSDT_PERP': 0.0})
        staleness = pd.Series({'BTCUSDT_PERP': 10})  # Very high

        # Should not raise, but will log warning
        validate_staleness_inputs(targets, actual, staleness)


class TestAuditTrail:
    """Test audit trail completeness."""

    def test_audit_includes_all_overrides(self):
        """Audit should include all instruments with overrides."""
        targets = pd.Series({
            'BTCUSDT_PERP': 100.0,  # No override
            'ETHUSDT_PERP': 100.0,  # Override
        })
        actual = pd.Series({
            'BTCUSDT_PERP': 0.0,
            'ETHUSDT_PERP': 0.0,
        })
        staleness = pd.Series({
            'BTCUSDT_PERP': 0,
            'ETHUSDT_PERP': 1,
        })

        _, audit = apply_staleness_overlay(
            targets, actual, staleness, date(2026, 1, 27)
        )

        # Only ETHUSDT should be in audit (blocked)
        assert 'BTCUSDT_PERP' not in audit
        assert 'ETHUSDT_PERP' in audit

    def test_audit_contains_required_fields(self):
        """Audit entries should have all required fields."""
        targets = pd.Series({'BTCUSDT_PERP': 100.0})
        actual = pd.Series({'BTCUSDT_PERP': 0.0})
        staleness = pd.Series({'BTCUSDT_PERP': 1})

        _, audit = apply_staleness_overlay(
            targets, actual, staleness, date(2026, 1, 27)
        )

        entry = audit['BTCUSDT_PERP']
        assert 'original_target' in entry
        assert 'overridden_target' in entry
        assert 'actual_position' in entry
        assert 'staleness_days' in entry
        assert 'reason' in entry
        assert 'rule' in entry
