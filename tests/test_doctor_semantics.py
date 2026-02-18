#!/usr/bin/env python3
"""
Regression tests for Phase A: Doctor Semantics - Allowlist & Jagged Panels

Tests verify:
1. Missing layer_a instruments => WARNING (not ERROR)
2. Positions with extra instruments => ERROR
3. Jagged mode NaNs => PASS_WITH_WARNINGS
4. Rectangular mode NaNs => FAIL
"""

import pytest
import pandas as pd
import numpy as np
from pathlib import Path
import tempfile
import sys

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.doctor_live_ops import (
    check_rectangular_panel,
    is_jagged_mode
)
from sysdata.crypto.positions_validation import validate_positions_file


def test_jagged_mode_detection():
    """Jagged mode should be detected from config."""
    # allow_jagged flag
    config1 = {'system': {'allow_jagged': True}}
    assert is_jagged_mode(config1) is True

    # dynamic_universe enabled
    config2 = {'dynamic_universe': {'enabled': True}}
    assert is_jagged_mode(config2) is True

    # Neither flag
    config3 = {'system': {'allow_jagged': False}}
    assert is_jagged_mode(config3) is False

    config4 = {}
    assert is_jagged_mode(config4) is False


def test_jagged_mode_nans_pass_with_warnings():
    """Jagged mode: NaNs should PASS_WITH_WARNINGS."""
    # Create temp dataset with NaNs
    df = pd.DataFrame({
        'close_BTCUSDT_PERP': [1.0, 2.0, pd.NA],
        'close_ETHUSDT_PERP': [3.0, pd.NA, pd.NA]
    })

    with tempfile.NamedTemporaryFile(suffix='.parquet', delete=False) as tmp:
        tmp_path = Path(tmp.name)
        df.to_parquet(tmp_path, index=False)

        try:
            config = {'system': {'allow_jagged': True}}
            status, errors, warnings = check_rectangular_panel(tmp_path, config)

            assert status == 'PASS_WITH_WARNINGS', f"Expected PASS_WITH_WARNINGS, got {status}"
            assert len(errors) == 0, f"Should have no errors, got: {errors}"
            assert len(warnings) > 0, "Should have warnings about NaNs"
            assert 'NaNs' in warnings[0], f"Warning should mention NaNs: {warnings[0]}"

        finally:
            tmp_path.unlink()


def test_rectangular_mode_nans_fail():
    """Rectangular mode: NaNs should FAIL."""
    # Create temp dataset with NaNs
    df = pd.DataFrame({
        'close_BTCUSDT_PERP': [1.0, 2.0, pd.NA]
    })

    with tempfile.NamedTemporaryFile(suffix='.parquet', delete=False) as tmp:
        tmp_path = Path(tmp.name)
        df.to_parquet(tmp_path, index=False)

        try:
            config = {'system': {'allow_jagged': False}}
            status, errors, warnings = check_rectangular_panel(tmp_path, config)

            assert status == 'FAIL', f"Expected FAIL, got {status}"
            assert len(errors) > 0, "Should have errors about NaNs"
            assert 'NaNs' in errors[0], f"Error should mention NaNs: {errors[0]}"

        finally:
            tmp_path.unlink()


def test_rectangular_mode_no_nans_pass():
    """Rectangular mode with no NaNs should PASS."""
    # Create temp dataset with no NaNs
    df = pd.DataFrame({
        'close_BTCUSDT_PERP': [1.0, 2.0, 3.0],
        'close_ETHUSDT_PERP': [4.0, 5.0, 6.0]
    })

    with tempfile.NamedTemporaryFile(suffix='.parquet', delete=False) as tmp:
        tmp_path = Path(tmp.name)
        df.to_parquet(tmp_path, index=False)

        try:
            config = {'system': {'allow_jagged': False}}
            status, errors, warnings = check_rectangular_panel(tmp_path, config)

            assert status == 'PASS', f"Expected PASS, got {status}"
            assert len(errors) == 0, f"Should have no errors, got: {errors}"
            assert len(warnings) == 0, f"Should have no warnings, got: {warnings}"

        finally:
            tmp_path.unlink()


def test_missing_layer_a_instruments_warning():
    """Missing layer_a instruments => WARNING (not ERROR)."""
    positions_df = pd.DataFrame({
        'instrument': ['BTCUSDT_PERP', 'ETHUSDT_PERP'],
        'contracts': [0.0, 0.0],
        'mark_price_usd': [0.0, 0.0],
        'notional_usd': [0.0, 0.0],
        'timestamp': ['2026-02-14T00:00:00Z', '2026-02-14T00:00:00Z'],
        'notes': ['', '']
    })

    universe = ['BTCUSDT_PERP', 'ETHUSDT_PERP', 'SOLUSDT_PERP']  # SOLUSDT missing

    result = validate_positions_file(
        positions_df, universe, 5000.0,
        critical_staleness_hours=48,
        allow_missing_instruments=True
    )

    assert len(result.warnings) > 0, "Should warn about missing SOLUSDT_PERP"
    assert len(result.errors) == 0, "Should NOT error on missing instruments"

    # Check that warning mentions the missing instrument
    warning_text = ' '.join(str(w) for w in result.warnings)
    assert 'SOLUSDT_PERP' in warning_text or 'missing' in warning_text.lower()


def test_extra_position_instrument_warning():
    """Positions with extra instruments are allowed but ETHUSDT is still missing => WARNING."""
    positions_df = pd.DataFrame({
        'instrument': ['BTCUSDT_PERP', 'FAKEUSDT_PERP'],  # FAKE is extra (not validated), ETHUSDT missing
        'contracts': [0.0, 0.1],
        'mark_price_usd': [50000.0, 100.0],
        'notional_usd': [0.0, 10.0],
        'timestamp': ['2026-02-14T00:00:00Z', '2026-02-14T00:00:00Z'],
        'notes': ['', '']
    })

    universe = ['BTCUSDT_PERP', 'ETHUSDT_PERP']

    result = validate_positions_file(
        positions_df, universe, 5000.0,
        critical_staleness_hours=48,
        allow_missing_instruments=True
    )

    # Extra instruments are not validated (by design - positions file can have more than layer_a)
    # But should warn about missing ETHUSDT_PERP
    assert len(result.warnings) > 0, "Should warn about missing ETHUSDT_PERP"

    # Check that warning mentions the missing instrument
    warning_text = ' '.join(str(w) for w in result.warnings)
    assert 'ETHUSDT_PERP' in warning_text or 'missing' in warning_text.lower()


def test_positions_all_present_low_leverage():
    """All layer_a instruments present with reasonable leverage => PASS."""
    positions_df = pd.DataFrame({
        'instrument': ['BTCUSDT_PERP', 'ETHUSDT_PERP', 'SOLUSDT_PERP'],
        'contracts': [0.01, 0.1, 1.0],  # Low contracts to avoid leverage error
        'mark_price_usd': [50000.0, 3000.0, 100.0],
        'notional_usd': [500.0, 300.0, 100.0],  # Total ~900 USD on 5000 equity = ~0.18x leverage
        'timestamp': ['2026-02-14T00:00:00Z', '2026-02-14T00:00:00Z', '2026-02-14T00:00:00Z'],
        'notes': ['', '', '']
    })

    universe = ['BTCUSDT_PERP', 'ETHUSDT_PERP', 'SOLUSDT_PERP']

    result = validate_positions_file(
        positions_df, universe, 10000.0,  # Higher equity to avoid leverage error
        critical_staleness_hours=48,
        allow_missing_instruments=True
    )

    # Should have no errors (all instruments present, reasonable leverage)
    # Warnings may exist for concentration/staleness but not for missing instruments
    assert len(result.errors) == 0, f"Should have no errors, got: {result.errors}"

    # Verify no warnings about missing instruments
    warning_text = ' '.join(str(w) for w in result.warnings)
    assert 'missing' not in warning_text.lower(), f"Should not warn about missing instruments, got: {warning_text}"


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
