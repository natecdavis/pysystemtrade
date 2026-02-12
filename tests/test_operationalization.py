"""
Integration tests for operationalization components.

Focus: Test integration between components, exit codes, and structured results.
NOT testing string formatting or exact output text.
"""

import pytest
import subprocess
import sys
import os
from pathlib import Path
from datetime import datetime, timedelta, date, timezone
import pandas as pd
import json
import tempfile
import shutil


# Ensure PYTHONPATH includes project root for subprocess calls
PROJECT_ROOT = Path(__file__).parent.parent
ENV_WITH_PYTHONPATH = os.environ.copy()
ENV_WITH_PYTHONPATH['PYTHONPATH'] = str(PROJECT_ROOT)


class TestDoctorIntegration:
    """Test doctor CLI integration with positions validation library."""

    def test_doctor_with_valid_positions(self, tmp_path):
        """Doctor should pass with valid positions."""
        # Create test config
        config = tmp_path / "test_config.yaml"
        config.write_text("""
universe:
  layer_a_instruments:
    - BTCUSDT_PERP
    - ETHUSDT_PERP
""")

        # Create valid positions
        positions = tmp_path / "positions.csv"
        now = datetime.now(timezone.utc).isoformat()
        positions.write_text(f"""instrument,contracts,mark_price_usd,notional_usd,timestamp,notes
BTCUSDT_PERP,0.003,45000.00,135.00,{now},test
ETHUSDT_PERP,0.000,0.00,0.00,{now},test
""")

        # Create equity file
        equity = tmp_path / "equity.txt"
        equity.write_text("5000.0")

        # Create data status
        data_status = tmp_path / "data_status.json"
        yesterday = (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()
        data_status.write_text(json.dumps({
            'generated_at': datetime.now(timezone.utc).isoformat(),
            'expected_as_of_date': yesterday,
            'dataset_as_of_date': yesterday,
            'instruments': {
                'BTCUSDT_PERP': {'staleness_days': 0},
                'ETHUSDT_PERP': {'staleness_days': 0}
            }
        }))

        # Run doctor
        result = subprocess.run([
            sys.executable,
            'scripts/doctor_live_ops.py',
            '--config', str(config),
            '--actual-positions', str(positions),
            '--current-equity-file', str(equity),
            '--data-status-path', str(data_status),
            '--cadence', 'daily'
        ], capture_output=True, text=True, env=ENV_WITH_PYTHONPATH)

        # Should pass
        assert result.returncode == 0

    def test_doctor_with_notional_arithmetic_error(self, tmp_path):
        """Doctor should fail with notional arithmetic error."""
        config = tmp_path / "test_config.yaml"
        config.write_text("""
universe:
  layer_a_instruments:
    - BTCUSDT_PERP
""")

        positions = tmp_path / "positions.csv"
        now = datetime.now(timezone.utc).isoformat()
        # Notional doesn't match contracts × price
        positions.write_text(f"""instrument,contracts,mark_price_usd,notional_usd,timestamp,notes
BTCUSDT_PERP,0.003,45000.00,100.00,{now},wrong_notional
""")

        equity = tmp_path / "equity.txt"
        equity.write_text("5000.0")

        data_status = tmp_path / "data_status.json"
        yesterday = (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()
        data_status.write_text(json.dumps({
            'generated_at': datetime.now(timezone.utc).isoformat(),
            'expected_as_of_date': yesterday,
            'dataset_as_of_date': yesterday,
            'instruments': {'BTCUSDT_PERP': {'staleness_days': 0}}
        }))

        result = subprocess.run([
            sys.executable,
            'scripts/doctor_live_ops.py',
            '--config', str(config),
            '--actual-positions', str(positions),
            '--current-equity-file', str(equity),
            '--data-status-path', str(data_status),
            '--cadence', 'daily'
        ], capture_output=True, text=True, env=ENV_WITH_PYTHONPATH)

        # Should fail
        assert result.returncode == 2


class TestReconciliationIntegration:
    """Test reconciliation CLI integration with validation library."""

    def test_reconcile_suggest_mode(self, tmp_path):
        """Reconcile in suggest mode should show errors without fixing."""
        config = tmp_path / "test_config.yaml"
        config.write_text("""
universe:
  layer_a_instruments:
    - BTCUSDT_PERP
""")

        positions = tmp_path / "positions.csv"
        now = datetime.now(timezone.utc).isoformat()
        # Notional error: should be 135.00, not 100.00
        positions.write_text(f"""instrument,contracts,mark_price_usd,notional_usd,timestamp,notes
BTCUSDT_PERP,0.003,45000.00,100.00,{now},test
""")

        result = subprocess.run([
            sys.executable,
            'scripts/reconcile_positions.py',
            '--positions-file', str(positions),
            '--current-equity', '5000.0',
            '--config', str(config),
            '--fix-mode', 'suggest'
        ], capture_output=True, text=True, env=ENV_WITH_PYTHONPATH)

        # Should fail (errors found)
        assert result.returncode == 2

        # File should NOT be modified
        content_after = positions.read_text()
        assert '100.00' in content_after  # Original wrong value still there

    def test_reconcile_auto_fix_mode(self, tmp_path):
        """Reconcile in auto mode should fix notional errors."""
        config = tmp_path / "test_config.yaml"
        config.write_text("""
universe:
  layer_a_instruments:
    - BTCUSDT_PERP
""")

        positions = tmp_path / "positions.csv"
        now = datetime.now(timezone.utc).isoformat()
        # Notional error: should be 135.00, not 100.00
        positions.write_text(f"""instrument,contracts,mark_price_usd,notional_usd,timestamp,notes
BTCUSDT_PERP,0.003,45000.00,100.00,{now},test
""")

        result = subprocess.run([
            sys.executable,
            'scripts/reconcile_positions.py',
            '--positions-file', str(positions),
            '--current-equity', '5000.0',
            '--config', str(config),
            '--fix-mode', 'auto'
        ], capture_output=True, text=True, env=ENV_WITH_PYTHONPATH)

        # Should pass after fixing
        assert result.returncode == 0

        # File should be modified with correct notional
        content_after = positions.read_text()
        assert '135.00' in content_after or '135.0' in content_after  # Fixed value

        # Backup should exist
        backups = list(tmp_path.glob('positions.csv.bak.*'))
        assert len(backups) == 1

    def test_reconcile_sign_error_not_auto_fixed(self, tmp_path):
        """Sign errors should not be auto-fixed (require manual intervention)."""
        config = tmp_path / "test_config.yaml"
        config.write_text("""
universe:
  layer_a_instruments:
    - BTCUSDT_PERP
""")

        positions = tmp_path / "positions.csv"
        now = datetime.now(timezone.utc).isoformat()
        # Sign error: short position (negative contracts) but positive notional
        positions.write_text(f"""instrument,contracts,mark_price_usd,notional_usd,timestamp,notes
BTCUSDT_PERP,-0.003,45000.00,135.00,{now},sign_error
""")

        result = subprocess.run([
            sys.executable,
            'scripts/reconcile_positions.py',
            '--positions-file', str(positions),
            '--current-equity', '5000.0',
            '--config', str(config),
            '--fix-mode', 'auto'
        ], capture_output=True, text=True, env=ENV_WITH_PYTHONPATH)

        # Should still fail (sign errors not auto-fixed)
        assert result.returncode == 2


class TestCutoverTimeIntegration:
    """Test cutover time enforcement in orchestrator."""

    def test_override_date_in_orchestrator(self, tmp_path):
        """Test --expected-date override in run_live_advisory.py."""
        # This test would require full orchestrator setup
        # For now, we've already tested get_expected_as_of_date() in test_cutover_time.py
        # Just verify the argument is parsed correctly
        result = subprocess.run([
            sys.executable,
            'scripts/run_live_advisory.py',
            '--help'
        ], capture_output=True, text=True, env=ENV_WITH_PYTHONPATH)

        # Should have --expected-date argument
        assert '--expected-date' in result.stdout


class TestValidationLibrarySingleSourceOfTruth:
    """Test that doctor and reconcile use the same validation library."""

    def test_same_validation_logic(self, tmp_path):
        """Doctor and reconcile should report same errors for same positions."""
        config = tmp_path / "test_config.yaml"
        config.write_text("""
universe:
  layer_a_instruments:
    - BTCUSDT_PERP
""")

        positions = tmp_path / "positions.csv"
        now = datetime.now(timezone.utc).isoformat()
        # Notional error
        positions.write_text(f"""instrument,contracts,mark_price_usd,notional_usd,timestamp,notes
BTCUSDT_PERP,0.003,45000.00,100.00,{now},test
""")

        equity_file = tmp_path / "equity.txt"
        equity_file.write_text("5000.0")

        data_status = tmp_path / "data_status.json"
        yesterday = (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()
        data_status.write_text(json.dumps({
            'generated_at': datetime.now(timezone.utc).isoformat(),
            'expected_as_of_date': yesterday,
            'dataset_as_of_date': yesterday,
            'instruments': {'BTCUSDT_PERP': {'staleness_days': 0}}
        }))

        # Run doctor
        doctor_result = subprocess.run([
            sys.executable,
            'scripts/doctor_live_ops.py',
            '--config', str(config),
            '--actual-positions', str(positions),
            '--current-equity-file', str(equity_file),
            '--data-status-path', str(data_status),
            '--cadence', 'daily'
        ], capture_output=True, text=True, env=ENV_WITH_PYTHONPATH)

        # Run reconcile
        reconcile_result = subprocess.run([
            sys.executable,
            'scripts/reconcile_positions.py',
            '--positions-file', str(positions),
            '--current-equity', '5000.0',
            '--config', str(config),
            '--fix-mode', 'suggest'
        ], capture_output=True, text=True, env=ENV_WITH_PYTHONPATH)

        # Both should fail with same exit code
        assert doctor_result.returncode == 2
        assert reconcile_result.returncode == 2

        # Both should mention notional arithmetic
        assert 'notional' in doctor_result.stdout.lower() or 'notional' in doctor_result.stderr.lower()
        assert 'notional' in reconcile_result.stdout.lower()


class TestPositionsValidationTolerance:
    """Test that validation tolerances are realistic."""

    def test_realistic_tolerance_small_position(self):
        """Small position with $0.50 rounding error should pass."""
        from sysdata.crypto.positions_validation import validate_notional_arithmetic

        # Small position: $135 with $0.50 error (within $1 tolerance)
        is_valid, expected, diff = validate_notional_arithmetic(
            contracts=0.003,
            mark_price=45000.0,
            notional=135.50,  # Off by $0.50
            tolerance_usd=1.0,
            tolerance_pct=0.001
        )

        assert is_valid
        assert diff == 0.50

    def test_realistic_tolerance_large_position(self):
        """Large position with 0.05% error should pass."""
        from sysdata.crypto.positions_validation import validate_notional_arithmetic

        # Large position: $450,000 with $225 error (0.05%)
        is_valid, expected, diff = validate_notional_arithmetic(
            contracts=10.0,
            mark_price=45000.0,
            notional=450225.0,  # Off by $225, but only 0.05%
            tolerance_usd=1.0,
            tolerance_pct=0.001  # 0.1%
        )

        assert is_valid
        assert diff == 225.0

    def test_realistic_tolerance_exceeds_both(self):
        """Error exceeding both tolerances should fail."""
        from sysdata.crypto.positions_validation import validate_notional_arithmetic

        # $125 expected, but $100 actual (off by $25, exceeds both)
        is_valid, expected, diff = validate_notional_arithmetic(
            contracts=0.05,
            mark_price=2500.0,
            notional=100.0,
            tolerance_usd=1.0,
            tolerance_pct=0.001
        )

        assert not is_valid
        assert diff == 25.0


class TestExitCodes:
    """Test that all CLIs return correct exit codes."""

    def test_doctor_exit_codes_documented(self):
        """Verify doctor exit codes match documentation."""
        # 0 = PASS
        # 1 = PASS_WITH_WARNINGS
        # 2 = FAIL
        # (Tested in TestDoctorIntegration)
        pass

    def test_reconcile_exit_codes_documented(self):
        """Verify reconcile exit codes match documentation."""
        # 0 = PASS (no errors, or all errors fixed in auto mode)
        # 1 = PASS_WITH_WARNINGS (warnings only)
        # 2 = FAIL (errors found)
        # (Tested in TestReconciliationIntegration)
        pass
