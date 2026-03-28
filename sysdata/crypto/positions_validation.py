"""
Positions validation library: Single source of truth for positions file validation.

This module provides comprehensive validation of positions CSV files to catch
operator errors before they cause problems in live trading.

Used by:
- doctor_live_ops.py: Preflight health check
- reconcile_positions.py: Positions reconciliation CLI
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Dict, Any
import pandas as pd
import logging

logger = logging.getLogger(__name__)


@dataclass
class ValidationIssue:
    """Single validation error or warning."""
    level: str  # 'error' or 'warning'
    instrument: str
    check: str  # 'notional_arithmetic', 'sign_consistency', etc.
    message: str
    suggested_fix: Optional[str] = None

    def __str__(self) -> str:
        symbol = "✗" if self.level == "error" else "⚠"
        fix_msg = f"\n  Suggested fix: {self.suggested_fix}" if self.suggested_fix else ""
        return f"{symbol} {self.instrument}: {self.message}{fix_msg}"


@dataclass
class ValidationResult:
    """Result of validating positions file."""
    errors: List[ValidationIssue] = field(default_factory=list)
    warnings: List[ValidationIssue] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        """True if no errors (warnings are OK)."""
        return len(self.errors) == 0

    @property
    def overall_status(self) -> str:
        """Overall status string."""
        if self.errors:
            return 'FAIL'
        elif self.warnings:
            return 'PASS_WITH_WARNINGS'
        else:
            return 'PASS'

    def add_error(self, instrument: str, check: str, message: str, suggested_fix: Optional[str] = None):
        """Add an error."""
        self.errors.append(ValidationIssue(
            level='error',
            instrument=instrument,
            check=check,
            message=message,
            suggested_fix=suggested_fix
        ))

    def add_warning(self, instrument: str, check: str, message: str, suggested_fix: Optional[str] = None):
        """Add a warning."""
        self.warnings.append(ValidationIssue(
            level='warning',
            instrument=instrument,
            check=check,
            message=message,
            suggested_fix=suggested_fix
        ))


def validate_notional_arithmetic(
    contracts: float,
    mark_price: float,
    notional: float,
    tolerance_usd: float = 1.0,
    tolerance_pct: float = 0.001
) -> tuple[bool, float, float]:
    """
    Validate notional = contracts × price with realistic tolerance.

    Tolerance is the MAXIMUM of:
    - Absolute: $1.00 (handles rounding errors)
    - Relative: 0.1% of |expected_notional| (handles large positions)

    Args:
        contracts: Number of contracts (can be negative for short)
        mark_price: Mark price in USD
        notional: Reported notional in USD
        tolerance_usd: Absolute tolerance (default $1.00)
        tolerance_pct: Relative tolerance (default 0.001 = 0.1%)

    Returns:
        (is_valid, expected_notional, diff)
    """
    expected = contracts * mark_price
    diff = abs(notional - expected)
    threshold = max(tolerance_usd, tolerance_pct * abs(expected))
    is_valid = diff <= threshold
    return is_valid, expected, diff


def validate_sign_consistency(
    contracts: float,
    notional: float
) -> tuple[bool, str]:
    """
    Validate that contracts and notional have consistent signs.

    Both should be positive (long) or both negative (short).

    Args:
        contracts: Number of contracts
        notional: Notional in USD

    Returns:
        (is_valid, error_message)
    """
    # Handle zero positions
    if contracts == 0.0 and notional == 0.0:
        return True, ""

    # Check sign consistency
    if (contracts > 0 and notional < 0) or (contracts < 0 and notional > 0):
        position_type = "short" if contracts < 0 else "long"
        notional_type = "short" if notional < 0 else "long"
        return False, f"Contracts indicate {position_type} but notional indicates {notional_type}"

    return True, ""


def check_units_confusion(
    contracts: float,
    notional: float,
    mark_price: float
) -> List[str]:
    """
    Detect potential units confusion (contracts vs notional swapped).

    Returns list of warning messages (NOT errors - these are heuristics).

    Heuristics:
    - If |contracts| > 100: may have entered notional in contracts column
    - If |notional| < 10: may have entered contracts in notional column
    - If mark_price is suspiciously low/high: may indicate stale price

    Args:
        contracts: Number of contracts
        notional: Notional in USD
        mark_price: Mark price in USD

    Returns:
        List of warning messages (empty if no issues)
    """
    warnings = []

    # Check for large contract count (may be notional)
    if abs(contracts) > 100:
        warnings.append(
            f"Large contract count ({contracts:.2f}). "
            f"Verify you didn't enter notional in contracts column."
        )

    # Check for small notional (may be contracts)
    if abs(notional) > 0 and abs(notional) < 10:
        warnings.append(
            f"Small notional (${notional:.2f}). "
            f"Verify you didn't enter contracts in notional column."
        )

    # Check for suspicious mark price (very low or very high)
    if mark_price > 0:
        if mark_price < 0.01:
            warnings.append(
                f"Very low mark price (${mark_price:.4f}). "
                f"Verify price is correct and up-to-date."
            )
        elif mark_price > 1000000:
            warnings.append(
                f"Very high mark price (${mark_price:.2f}). "
                f"Verify price is correct and up-to-date."
            )

    return warnings


def check_stale_timestamps(
    timestamp_str: str,
    critical_hours: int = 48,
    error_hours: int = 168  # 7 days
) -> tuple[Optional[str], Optional[str]]:
    """
    Check if position timestamp is stale.

    Args:
        timestamp_str: ISO format timestamp string
        critical_hours: Warn if older than this (default 48h = 2 days)
        error_hours: Error if older than this (default 168h = 7 days)

    Returns:
        (error_message, warning_message) - both None if OK
    """
    try:
        # Parse timestamp and ensure it's timezone-aware
        timestamp_str_clean = timestamp_str.replace('Z', '+00:00')
        timestamp = datetime.fromisoformat(timestamp_str_clean)

        # If naive, assume UTC
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)

        now = datetime.now(timezone.utc)
        age = now - timestamp
        age_hours = age.total_seconds() / 3600
        age_days = age.days

        if age_hours > error_hours:
            return f"Position timestamp is {age_days} days old (> {error_hours//24} days)", None
        elif age_hours > critical_hours:
            return None, f"Position timestamp is {age_days} days old (> {critical_hours//24} days)"
        else:
            return None, None
    except (ValueError, AttributeError) as e:
        return f"Invalid timestamp format: {timestamp_str}", None


def validate_gross_leverage(
    total_notional: float,
    equity: float,
    warn_threshold: float = 2.5,
    error_threshold: float = 3.0
) -> tuple[Optional[str], Optional[str]]:
    """
    Validate gross leverage against caps.

    Args:
        total_notional: Sum of |notional| across all positions
        equity: Current equity
        warn_threshold: Warn if gross leverage > this (default 1.8x)
        error_threshold: Error if gross leverage > this (default 2.0x)

    Returns:
        (error_message, warning_message) - both None if OK
    """
    if equity <= 0:
        return "Equity must be > 0", None

    gross_leverage = total_notional / equity

    if gross_leverage > error_threshold:
        return (
            f"Gross leverage {gross_leverage:.2f}x exceeds cap of {error_threshold:.1f}x",
            None
        )
    elif gross_leverage > warn_threshold:
        return (
            None,
            f"Gross leverage {gross_leverage:.2f}x approaching cap of {error_threshold:.1f}x "
            f"(threshold: {warn_threshold:.1f}x)"
        )
    else:
        return None, None


def check_concentration_risk(
    notional: float,
    equity: float,
    warn_threshold: float = 0.5
) -> Optional[str]:
    """
    Check if a single position is too concentrated.

    Args:
        notional: Position notional
        equity: Current equity
        warn_threshold: Warn if position > this fraction of equity (default 50%)

    Returns:
        Warning message if concentrated, None otherwise
    """
    if equity <= 0:
        return None

    fraction = abs(notional) / equity
    if fraction > warn_threshold:
        return (
            f"Position is {fraction*100:.1f}% of equity "
            f"(> {warn_threshold*100:.0f}% concentration threshold)"
        )
    return None


def validate_positions_file(
    positions_df: pd.DataFrame,
    universe: List[str],
    equity: float,
    critical_staleness_hours: int = 48,
    allow_missing_instruments: bool = False
) -> ValidationResult:
    """
    Validate positions CSV with all checks.

    Args:
        positions_df: DataFrame with columns: instrument, contracts, mark_price_usd,
                     notional_usd, timestamp, notes
        universe: Expected list of instruments from config
        equity: Current equity
        critical_staleness_hours: Warn if position older than this (default 48h)
        allow_missing_instruments: If True, missing instruments are warnings not errors

    Returns:
        ValidationResult with errors and warnings
    """
    result = ValidationResult()

    # Store metadata
    result.metadata['equity'] = equity
    result.metadata['universe_size'] = len(universe)
    result.metadata['positions_count'] = len(positions_df)

    # Check 1: Verify required columns (mark_price_usd/notional_usd are optional — auto-derived)
    required_cols = {'instrument', 'contracts', 'timestamp'}
    missing_cols = required_cols - set(positions_df.columns)
    if missing_cols:
        result.add_error(
            instrument='FILE',
            check='schema',
            message=f"Missing required columns: {missing_cols}"
        )
        return result  # Can't continue without required columns

    has_price_cols = 'mark_price_usd' in positions_df.columns and 'notional_usd' in positions_df.columns

    # Check 2: Missing instruments
    positions_instruments = set(positions_df['instrument'].unique())
    missing_instruments = set(universe) - positions_instruments
    if missing_instruments:
        msg = f"Missing {len(missing_instruments)} instruments from universe: {sorted(missing_instruments)}"
        if allow_missing_instruments:
            result.add_warning(
                instrument='FILE',
                check='missing_instruments',
                message=msg,
                suggested_fix="Add missing instruments with 0.0 positions"
            )
        else:
            result.add_error(
                instrument='FILE',
                check='missing_instruments',
                message=msg,
                suggested_fix="Add missing instruments with 0.0 positions"
            )

    # Check 3: Per-instrument validation
    total_abs_notional = 0.0
    for idx, row in positions_df.iterrows():
        instrument = row['instrument']
        contracts = float(row['contracts'])
        mark_price = float(row['mark_price_usd']) if has_price_cols else 0.0
        notional = float(row['notional_usd']) if has_price_cols else 0.0
        timestamp = row['timestamp']

        total_abs_notional += abs(notional)

        # Skip zero positions for most checks
        is_zero_position = (contracts == 0.0) if not has_price_cols else (contracts == 0.0 and notional == 0.0)

        if not is_zero_position and has_price_cols:
            # 3a. Notional arithmetic
            is_valid, expected, diff = validate_notional_arithmetic(
                contracts, mark_price, notional
            )
            if not is_valid:
                result.add_error(
                    instrument=instrument,
                    check='notional_arithmetic',
                    message=f"Notional off by ${diff:.2f} (expected {expected:.2f}, got {notional:.2f})",
                    suggested_fix=f"Update notional to {expected:.2f}"
                )

            # 3b. Sign consistency
            sign_valid, sign_msg = validate_sign_consistency(contracts, notional)
            if not sign_valid:
                result.add_error(
                    instrument=instrument,
                    check='sign_consistency',
                    message=sign_msg,
                    suggested_fix="Ensure both contracts and notional have same sign"
                )

            # 3c. Units confusion (warnings only)
            units_warnings = check_units_confusion(contracts, notional, mark_price)
            for warn_msg in units_warnings:
                result.add_warning(
                    instrument=instrument,
                    check='units_confusion',
                    message=warn_msg
                )

            # 3d. Concentration risk
            concentration_warn = check_concentration_risk(notional, equity)
            if concentration_warn:
                result.add_warning(
                    instrument=instrument,
                    check='concentration_risk',
                    message=concentration_warn
                )

        # 3e. Stale timestamps (check even for zero positions)
        stale_error, stale_warning = check_stale_timestamps(
            timestamp, critical_hours=critical_staleness_hours
        )
        if stale_error:
            result.add_error(
                instrument=instrument,
                check='stale_timestamp',
                message=stale_error
            )
        elif stale_warning:
            result.add_warning(
                instrument=instrument,
                check='stale_timestamp',
                message=stale_warning
            )

    # Store summary metadata
    result.metadata['total_abs_notional'] = total_abs_notional
    result.metadata['gross_leverage'] = total_abs_notional / equity if equity > 0 else 0.0

    return result


def format_validation_report(result: ValidationResult) -> str:
    """
    Format validation result as human-readable report.

    Args:
        result: ValidationResult to format

    Returns:
        Formatted report string
    """
    lines = []
    lines.append("=" * 60)
    lines.append("POSITIONS VALIDATION REPORT")
    lines.append("=" * 60)
    lines.append(f"Status: {result.overall_status}")
    lines.append(f"Errors: {len(result.errors)}, Warnings: {len(result.warnings)}")

    if result.metadata:
        lines.append("")
        lines.append("Summary:")
        if 'equity' in result.metadata:
            lines.append(f"  Equity: ${result.metadata['equity']:.2f}")
        if 'gross_leverage' in result.metadata:
            lines.append(f"  Gross leverage: {result.metadata['gross_leverage']:.2f}x")
        if 'total_abs_notional' in result.metadata:
            lines.append(f"  Total |notional|: ${result.metadata['total_abs_notional']:.2f}")

    if result.errors:
        lines.append("")
        lines.append("ERRORS:")
        for error in result.errors:
            lines.append(f"  {error}")

    if result.warnings:
        lines.append("")
        lines.append("WARNINGS:")
        for warning in result.warnings:
            lines.append(f"  {warning}")

    if not result.errors and not result.warnings:
        lines.append("")
        lines.append("✓ All checks passed")

    lines.append("=" * 60)
    return "\n".join(lines)
