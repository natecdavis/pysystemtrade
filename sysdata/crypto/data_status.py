"""
Data status reporting for Binance Vision data freshness.

Generates reports on data availability, lag, and completeness for monthly advisory system.

V1 Extensions:
- Day-level precision (not just month-level)
- Two-date concept: expected_as_of_date vs dataset_as_of_date
- Per-instrument staleness tracking
"""

import json
from datetime import datetime, timedelta, date
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import logging

logger = logging.getLogger(__name__)


def get_last_available_month(data_dir: Path, symbol: str, data_type: str = "klines") -> Optional[str]:
    """
    Find the last available month for a symbol in raw data directory.

    Args:
        data_dir: Root data directory (e.g., data/raw/binance)
        symbol: Instrument symbol (e.g., BTCUSDT_PERP)
        data_type: Data type ('klines' or 'funding_rates')

    Returns:
        Last available month in YYYY-MM format, or None if no data
    """
    symbol_dir = data_dir / data_type / symbol
    if not symbol_dir.exists():
        return None

    # Look for monthly ZIP files matching pattern: {SYMBOL}-1d-YYYY-MM.zip
    zip_files = list(symbol_dir.glob(f"{symbol}-*-????-??.zip"))
    if not zip_files:
        return None

    # Extract YYYY-MM from filenames
    months = []
    for zip_file in zip_files:
        # Pattern: BTCUSDT_PERP-1d-2024-12.zip
        parts = zip_file.stem.split('-')
        if len(parts) >= 3:
            # Last two parts should be YYYY and MM
            year, month = parts[-2], parts[-1]
            if year.isdigit() and month.isdigit() and len(year) == 4 and len(month) == 2:
                months.append(f"{year}-{month}")

    if not months:
        return None

    # Return most recent
    return max(months)


def get_missing_months(data_dir: Path, symbol: str, start_month: str, end_month: str,
                       data_type: str = "klines") -> List[str]:
    """
    Find missing months in a date range for a symbol.

    Args:
        data_dir: Root data directory
        symbol: Instrument symbol
        start_month: Start month (YYYY-MM)
        end_month: End month (YYYY-MM)
        data_type: Data type ('klines' or 'funding_rates')

    Returns:
        List of missing months in YYYY-MM format
    """
    symbol_dir = data_dir / data_type / symbol
    if not symbol_dir.exists():
        # All months are missing
        start = datetime.strptime(start_month, "%Y-%m")
        end = datetime.strptime(end_month, "%Y-%m")
        months = []
        current = start
        while current <= end:
            months.append(current.strftime("%Y-%m"))
            # Move to next month
            if current.month == 12:
                current = current.replace(year=current.year + 1, month=1)
            else:
                current = current.replace(month=current.month + 1)
        return months

    # Find existing months
    zip_files = list(symbol_dir.glob(f"{symbol}-*-????-??.zip"))
    existing_months = set()
    for zip_file in zip_files:
        parts = zip_file.stem.split('-')
        if len(parts) >= 3:
            year, month = parts[-2], parts[-1]
            if year.isdigit() and month.isdigit() and len(year) == 4 and len(month) == 2:
                existing_months.add(f"{year}-{month}")

    # Generate expected months
    start = datetime.strptime(start_month, "%Y-%m")
    end = datetime.strptime(end_month, "%Y-%m")
    expected_months = []
    current = start
    while current <= end:
        month_str = current.strftime("%Y-%m")
        if month_str not in existing_months:
            expected_months.append(month_str)
        # Move to next month
        if current.month == 12:
            current = current.replace(year=current.year + 1, month=1)
        else:
            current = current.replace(month=current.month + 1)

    return expected_months


def calculate_data_lag_days(last_available_month: str, as_of_date: datetime) -> int:
    """
    Calculate number of days between last available data and current date.

    Args:
        last_available_month: Last month in YYYY-MM format
        as_of_date: Current date

    Returns:
        Number of days lag
    """
    # Last day of the last available month
    last_month = datetime.strptime(last_available_month, "%Y-%m")
    # Move to next month, then subtract one day
    if last_month.month == 12:
        end_of_month = datetime(last_month.year + 1, 1, 1) - timedelta(days=1)
    else:
        end_of_month = datetime(last_month.year, last_month.month + 1, 1) - timedelta(days=1)

    return (as_of_date.date() - end_of_month.date()).days


def get_expected_last_month(as_of_date: datetime, lag_months: int = 2) -> str:
    """
    Get expected last available month given Binance Vision publication lag.

    Args:
        as_of_date: Current date
        lag_months: Conservative lag in months (default: 2 for M-2 policy)

    Returns:
        Expected last month in YYYY-MM format
    """
    # Conservative: expect data through month M-lag_months where M is current month
    year = as_of_date.year
    month = as_of_date.month

    # Subtract lag_months
    for _ in range(lag_months):
        if month == 1:
            month = 12
            year -= 1
        else:
            month -= 1

    return f"{year:04d}-{month:02d}"


def generate_data_status_report(
    data_dir: Path,
    instruments: List[str],
    as_of_date: Optional[datetime] = None,
    lag_months: int = 2
) -> Dict:
    """
    Generate comprehensive data status report for all instruments.

    Args:
        data_dir: Root data directory (e.g., data/raw/binance)
        instruments: List of instrument symbols
        as_of_date: Current date (default: now)
        lag_months: Conservative lag policy in months (default: 2)

    Returns:
        Dictionary with data status for all instruments
    """
    if as_of_date is None:
        as_of_date = datetime.utcnow()

    expected_last_month = get_expected_last_month(as_of_date, lag_months)

    instrument_status = {}
    total_up_to_date = 0
    total_lagging = 0
    total_missing = 0
    max_lag_days = 0

    for symbol in instruments:
        # Check klines data
        last_month = get_last_available_month(data_dir, symbol, "klines")

        if last_month is None:
            # No data at all
            status = "missing_data"
            total_missing += 1
            lag_days = 999  # Large number to indicate missing
            missing_months = get_missing_months(
                data_dir, symbol, "2020-01", expected_last_month, "klines"
            )
            warnings = [f"No data found for {symbol}"]
        else:
            # Calculate lag
            lag_days = calculate_data_lag_days(last_month, as_of_date)
            max_lag_days = max(max_lag_days, lag_days)

            # Check if up to date (within expected lag)
            if last_month >= expected_last_month:
                status = "up_to_date"
                total_up_to_date += 1
                missing_months = []
                warnings = []
            else:
                status = "lagging"
                total_lagging += 1
                missing_months = get_missing_months(
                    data_dir, symbol, last_month, expected_last_month, "klines"
                )
                warnings = [
                    f"Data lag: {lag_days} days (last month: {last_month}, expected: {expected_last_month})"
                ]

        instrument_status[symbol] = {
            "last_available_month": last_month,
            "months_downloaded": [],  # Populated during download
            "status": status,
            "data_lag_days": lag_days,
            "missing_months": missing_months,
            "warnings": warnings
        }

    return {
        "as_of_date": as_of_date.strftime("%Y-%m-%d"),
        "expected_last_month": expected_last_month,
        "lag_policy_months": lag_months,
        "instruments": instrument_status,
        "summary": {
            "total_instruments": len(instruments),
            "up_to_date": total_up_to_date,
            "lagging": total_lagging,
            "missing_data": total_missing,
            "max_lag_days": max_lag_days
        }
    }


def save_data_status_report(report: Dict, output_path: Path) -> None:
    """Save data status report to JSON file."""
    with open(output_path, 'w') as f:
        json.dump(report, f, indent=2)
    logger.info(f"Data status report saved to {output_path}")


def validate_data_completeness(report: Dict, fail_on_missing: bool = False, allow_missing_data: bool = False) -> bool:
    """
    Validate data completeness and raise errors if critical issues found.

    Args:
        report: Data status report
        fail_on_missing: If True, fail on any missing expected data
        allow_missing_data: If True, allow instruments with NO data (for initial setup)

    Returns:
        True if validation passes

    Raises:
        ValueError: If critical data issues found
    """
    summary = report["summary"]

    # Critical: Any instruments with NO data at all (unless explicitly allowed)
    if summary["missing_data"] > 0 and not allow_missing_data:
        missing_instruments = [
            inst for inst, status in report["instruments"].items()
            if status["status"] == "missing_data"
        ]
        raise ValueError(
            f"CRITICAL: {summary['missing_data']} instrument(s) have NO data: {missing_instruments}. "
            f"Cannot proceed without data for all instruments in universe."
        )

    # Check for lagging instruments
    if summary["lagging"] > 0:
        lagging_instruments = [
            (inst, status["data_lag_days"])
            for inst, status in report["instruments"].items()
            if status["status"] == "lagging"
        ]
        if fail_on_missing:
            raise ValueError(
                f"{summary['lagging']} instrument(s) are lagging: {lagging_instruments}. "
                f"Expected data through {report['expected_last_month']}."
            )
        else:
            logger.warning(
                f"{summary['lagging']} instrument(s) are lagging (max: {summary['max_lag_days']} days). "
                f"This is expected due to Binance Vision publication lag."
            )

    return True


# ============================================================================
# V1 Extensions: Day-Level Precision and Staleness Tracking
# ============================================================================


def get_expected_as_of_date(
    override_date: Optional[date] = None,
    warn_if_early: bool = True,
    warn_if_late: bool = True
) -> date:
    """
    Get expected as_of_date with UTC cutover time enforcement.

    Default behavior: expected_as_of_date = yesterday UTC (D-1)

    Safe operating window: 00:30 - 06:00 UTC
    - After UTC midnight, yesterday's data is complete
    - API cache should be available by 00:05 UTC
    - Earlier is better (less market movement since yesterday's close)

    Args:
        override_date: Manual override (for testing). If provided, skip all warnings.
        warn_if_early: If True, warn if running before 00:05 UTC
        warn_if_late: If True, warn if running after 12:00 UTC

    Returns:
        Expected as_of_date (usually yesterday UTC)
    """
    from datetime import timezone

    if override_date:
        logger.info(f"Using override expected_as_of_date: {override_date}")
        return override_date

    now_utc = datetime.now(timezone.utc)
    current_hour = now_utc.hour
    current_minute = now_utc.minute

    # Warn if running too early (today's data not available yet)
    if warn_if_early and (current_hour == 0 and current_minute < 5):
        logger.warning(
            f"Running at {now_utc.strftime('%H:%M')} UTC (very early). "
            f"Recommended to wait until after 00:05 UTC to ensure API cache is available."
        )

    # Warn if running late (trading on stale intraday prices)
    if warn_if_late and current_hour >= 12:
        logger.warning(
            f"Running at {now_utc.strftime('%H:%M')} UTC (late in the day). "
            f"Trading on yesterday's close prices. Consider running earlier (00:30-06:00 UTC)."
        )

    # Expected: yesterday UTC
    expected = now_utc.date() - timedelta(days=1)
    logger.info(
        f"Expected as_of_date: {expected} (yesterday UTC, "
        f"computed at {now_utc.strftime('%Y-%m-%d %H:%M:%S')} UTC)"
    )
    return expected


def get_last_available_date(data_dir: Path, symbol: str, data_type: str = "klines") -> Optional[date]:
    """
    Find the last available DATE (not month) for a symbol.

    Checks multiple sources and returns the most recent:
    1. Vision monthly ZIPs (infer last day of month)
    2. Vision daily ZIPs (if available)
    3. API cache parquet files (most recent, overrides Vision if present)

    Args:
        data_dir: Root data directory (e.g., data/raw/binance)
        symbol: Instrument symbol (e.g., BTCUSDT_PERP)
        data_type: Data type ('klines' or 'funding_rates')

    Returns:
        Last available date, or None if no data
    """
    last_date = None

    # Check 1: Vision monthly ZIPs (base historical data)
    symbol_dir = data_dir / data_type / symbol
    if symbol_dir.exists():
        # Look for monthly ZIPs: {SYMBOL}-1d-YYYY-MM.zip
        monthly_zips = list(symbol_dir.glob(f"{symbol}-*-????-??.zip"))
        if monthly_zips:
            months = []
            for zip_file in monthly_zips:
                parts = zip_file.stem.split('-')
                if len(parts) >= 3:
                    year, month = parts[-2], parts[-1]
                    if year.isdigit() and month.isdigit() and len(year) == 4 and len(month) == 2:
                        months.append(f"{year}-{month}")

            if months:
                last_month_str = max(months)
                year, month = map(int, last_month_str.split('-'))

                # Infer last day of month
                if month == 12:
                    vision_monthly_date = date(year, 12, 31)
                else:
                    vision_monthly_date = date(year, month + 1, 1) - timedelta(days=1)

                last_date = vision_monthly_date
                logger.debug(f"{symbol}: last Vision monthly date = {vision_monthly_date}")

    # Check 2: Vision daily ZIPs (if available, overrides monthly)
    if symbol_dir.exists():
        # Look for daily ZIPs: {SYMBOL}-1d-YYYY-MM-DD.zip
        daily_zips = list(symbol_dir.glob(f"{symbol}-*-????-??-??.zip"))
        if daily_zips:
            daily_dates = []
            for zip_file in daily_zips:
                parts = zip_file.stem.split('-')
                # Last 3 parts should be YYYY, MM, DD
                if len(parts) >= 4:
                    try:
                        year = int(parts[-3])
                        month = int(parts[-2])
                        day = int(parts[-1])
                        daily_dates.append(date(year, month, day))
                    except ValueError:
                        continue
            if daily_dates:
                vision_daily_date = max(daily_dates)
                if last_date is None or vision_daily_date > last_date:
                    last_date = vision_daily_date
                    logger.debug(f"{symbol}: last Vision daily date = {vision_daily_date}")

    # Check 3: API cache (most recent, always overrides if present)
    api_cache_dir = data_dir / 'api_cache' / symbol
    if api_cache_dir.exists():
        # Look for pattern: YYYY-MM-DD_klines.parquet
        cache_files = list(api_cache_dir.glob(f"*_{data_type}.parquet"))
        if cache_files:
            # Extract dates from filenames
            cache_dates = []
            for f in cache_files:
                # Pattern: BTCUSDT_2021-01-01_2021-01-03_klines.parquet
                # OR: 2021-01-01_klines.parquet (simpler pattern)
                parts = f.stem.split('_')
                for part in parts:
                    # Look for YYYY-MM-DD pattern
                    if len(part) == 10 and part[4] == '-' and part[7] == '-':
                        try:
                            d = datetime.strptime(part, '%Y-%m-%d').date()
                            cache_dates.append(d)
                        except ValueError:
                            continue
            if cache_dates:
                api_cache_date = max(cache_dates)
                # API cache always wins (most specific and recent)
                last_date = api_cache_date
                logger.debug(f"{symbol}: last API cache date = {api_cache_date} (overrides Vision)")

    return last_date


def compute_staleness_days(expected_date: date, last_data_date: date) -> int:
    """
    Compute staleness in UTC days relative to EXPECTED date.

    Args:
        expected_date: Expected freshness date (usually yesterday UTC)
        last_data_date: Actual last available date for instrument

    Returns:
        max(0, (expected_date - last_data_date).days)
    """
    if last_data_date >= expected_date:
        return 0
    return (expected_date - last_data_date).days


def compute_dates_and_staleness(
    data_dir: Path,
    instruments: List[str],
    expected_as_of_date: Optional[date] = None
) -> Tuple[date, date, Dict]:
    """
    Compute expected vs actual as_of_date and per-instrument staleness.

    Implements the two-date concept:
    - expected_as_of_date: Target freshness date (D-1 by default)
    - dataset_as_of_date: min(last_available_date) for rectangular panel
    - staleness_days: per-instrument lag vs expected date

    Args:
        data_dir: Root data directory
        instruments: List of instrument symbols
        expected_as_of_date: Target freshness date (default: yesterday UTC)

    Returns:
        tuple: (expected_as_of_date, dataset_as_of_date, staleness_report)
            expected_as_of_date: Target freshness date (D-1)
            dataset_as_of_date: min(last_available_date) for rectangular panel
            staleness_report: dict with per-instrument:
                - last_available_date: date
                - staleness_days: int (expected_as_of_date - last_available_date)

    Raises:
        ValueError: If any instrument has no data
    """
    # Default: yesterday UTC
    if expected_as_of_date is None:
        expected_as_of_date = (datetime.utcnow().date() - timedelta(days=1))

    staleness_report = {}
    last_dates = []

    for symbol in instruments:
        last_date = get_last_available_date(data_dir, symbol, "klines")

        if last_date is None:
            logger.debug(f"No data found for {symbol} — skipping in V1 staleness report")
            staleness_report[symbol] = {
                'last_available_date': None,
                'staleness_days': None,
                'status': 'no_data'
            }
            continue

        last_dates.append(last_date)
        staleness_days = compute_staleness_days(expected_as_of_date, last_date)

        staleness_report[symbol] = {
            'last_available_date': last_date,
            'staleness_days': staleness_days
        }

    # Dataset as_of_date = min across instruments that have data (for rectangular panel)
    dataset_as_of_date = min(last_dates) if last_dates else None

    return expected_as_of_date, dataset_as_of_date, staleness_report


def validate_as_of_date(
    as_of_date: date,
    expected_date: date,
    tolerance_days: int = 1
) -> None:
    """
    Validate as_of_date within tolerance of expected date.

    Args:
        as_of_date: Actual dataset as_of_date (min across instruments)
        expected_date: Expected as_of_date (target freshness)
        tolerance_days: Maximum allowed lag in days (default: 1)

    Raises:
        ValueError: If lag > tolerance_days
    """
    lag_days = (expected_date - as_of_date).days

    if lag_days < 0:
        logger.warning(
            f"as_of_date {as_of_date} is AHEAD of expected {expected_date} by {abs(lag_days)} days. "
            f"This is unusual but not an error."
        )
    elif lag_days > tolerance_days:
        raise ValueError(
            f"as_of_date lag too large: {lag_days} days (tolerance: {tolerance_days}). "
            f"Expected {expected_date}, got {as_of_date}. "
            f"Run update_data_daily.py to fetch recent data."
        )
    else:
        logger.info(f"as_of_date validation passed: lag={lag_days} days (within tolerance)")


def load_klines_dates(data_dir: Path, symbol: str) -> Optional[List[date]]:
    """
    Load klines data and extract available dates.

    Args:
        data_dir: Root data directory
        symbol: Binance symbol (without _PERP)

    Returns:
        List of dates with klines data, or None if no data
    """
    import zipfile
    import pandas as pd

    klines_dir = data_dir / 'klines' / symbol
    if not klines_dir.exists():
        return None

    zip_files = sorted(klines_dir.glob(f"{symbol}-*.zip"))
    if not zip_files:
        return None

    all_dates = []
    for zip_file in zip_files:
        try:
            with zipfile.ZipFile(zip_file) as z:
                csv_name = zip_file.stem + '.csv'
                with z.open(csv_name) as f:
                    df = pd.read_csv(f, header=None, usecols=[6])  # Column 6 is close_time
                    df['close_time'] = pd.to_datetime(df[6], unit='ms', utc=True)
                    df['date'] = df['close_time'].dt.date
                    all_dates.extend(df['date'].tolist())
        except Exception as e:
            logger.warning(f"Failed to load {zip_file.name}: {e}")
            continue

    return sorted(set(all_dates)) if all_dates else None


def load_funding_dates(data_dir: Path, symbol: str) -> Optional[List[date]]:
    """
    Load funding rate data and extract available dates.

    Args:
        data_dir: Root data directory
        symbol: Binance symbol (without _PERP)

    Returns:
        List of dates with funding data, or None if no data
    """
    import zipfile
    import pandas as pd

    funding_dir = data_dir / 'funding_rates' / symbol
    if not funding_dir.exists():
        return None

    zip_files = sorted(funding_dir.glob(f"{symbol}-*.zip"))
    if not zip_files:
        return None

    all_dates = []
    for zip_file in zip_files:
        try:
            with zipfile.ZipFile(zip_file) as z:
                csv_name = zip_file.stem + '.csv'
                with z.open(csv_name) as f:
                    df = pd.read_csv(f, header=0)
                    # Handle various column names
                    time_col = None
                    for col in ['calcTime', 'calc_time', 'funding_time', 'fundingTime']:
                        if col in df.columns:
                            time_col = col
                            break
                    if time_col is None:
                        continue

                    df['calc_time'] = pd.to_datetime(df[time_col], unit='ms', utc=True)
                    df['date'] = df['calc_time'].dt.date
                    all_dates.extend(df['date'].tolist())
        except Exception as e:
            logger.warning(f"Failed to load {zip_file.name}: {e}")
            continue

    return sorted(set(all_dates)) if all_dates else None


def compute_funding_coverage(
    data_dir: Path,
    symbol: str,
    klines_dates: Optional[List[date]] = None
) -> dict:
    """
    Compute funding rate coverage relative to klines availability.

    Distinguishes between:
    - Missing data (no observation)
    - Present but zero (legitimate low funding regime)

    Args:
        data_dir: Root data directory
        symbol: Binance symbol (without _PERP)
        klines_dates: Pre-loaded klines dates (optional, will load if None)

    Returns:
        {
            "last_available_date": date|None,
            "staleness_days": int,
            "coverage_pct": float,  # % of klines days with funding observation present
            "missing_days": int,     # Days without any funding observation
            "zero_funding_days": int,  # Days with explicit zero funding
            "missing_months": List[str]
        }
    """
    if klines_dates is None:
        klines_dates = load_klines_dates(data_dir, symbol)

    if not klines_dates:
        return {
            "last_available_date": None,
            "staleness_days": 0,
            "coverage_pct": 0.0,
            "missing_days": 0,
            "zero_funding_days": 0,
            "missing_months": []
        }

    funding_dates = load_funding_dates(data_dir, symbol)

    if not funding_dates:
        # No funding data at all
        klines_months = set(d.strftime('%Y-%m') for d in klines_dates)
        return {
            "last_available_date": None,
            "staleness_days": (date.today() - max(klines_dates)).days if klines_dates else 0,
            "coverage_pct": 0.0,
            "missing_days": len(klines_dates),
            "zero_funding_days": 0,
            "missing_months": sorted(klines_months)
        }

    # Convert to sets for efficient operations
    klines_set = set(klines_dates)
    funding_set = set(funding_dates)

    # Compute coverage
    present_days = klines_set & funding_set
    missing_days = klines_set - funding_set

    coverage_pct = len(present_days) / len(klines_set) if klines_set else 0.0

    # Compute missing months
    klines_months = set(d.strftime('%Y-%m') for d in klines_dates)
    funding_months = set(d.strftime('%Y-%m') for d in funding_dates)
    missing_months = sorted(klines_months - funding_months)

    # Note: We can't easily compute zero_funding_days without loading actual values
    # For now, set to 0 (can enhance later if needed)
    zero_funding_days = 0

    last_funding_date = max(funding_dates) if funding_dates else None
    staleness_days = (date.today() - last_funding_date).days if last_funding_date else 0

    return {
        "last_available_date": str(last_funding_date) if last_funding_date else None,
        "staleness_days": staleness_days,
        "coverage_pct": coverage_pct,
        "missing_days": len(missing_days),
        "zero_funding_days": zero_funding_days,
        "missing_months": missing_months
    }


def load_lifecycle_metadata(
    metadata_dir: Path,
    symbol: str
) -> dict:
    """
    Load launch/delist dates from binance_symbol_lifecycle.json.

    Args:
        metadata_dir: Metadata directory path
        symbol: Binance symbol (without _PERP)

    Returns:
        {
            "launch_date": date|None,
            "delist_date": date|None,
            "status": "active"|"delisted"
        }
    """
    lifecycle_path = metadata_dir / "binance_symbol_lifecycle.json"
    if not lifecycle_path.exists():
        return {"launch_date": None, "delist_date": None, "status": "active"}

    try:
        with open(lifecycle_path) as f:
            lifecycle_data = json.load(f)
    except Exception as e:
        logger.warning(f"Failed to load lifecycle metadata: {e}")
        return {"launch_date": None, "delist_date": None, "status": "active"}

    # Symbol format in JSON is without _PERP
    symbol_key = symbol.replace('_PERP', '')

    if symbol_key not in lifecycle_data:
        return {"launch_date": None, "delist_date": None, "status": "active"}

    entry = lifecycle_data[symbol_key]

    result = {
        "launch_date": None,
        "delist_date": None,
        "status": entry.get('status', 'active')
    }

    if entry.get('launch_date'):
        try:
            result["launch_date"] = datetime.strptime(entry['launch_date'], '%Y-%m-%d').date()
        except ValueError:
            pass

    if entry.get('delist_date'):
        try:
            result["delist_date"] = datetime.strptime(entry['delist_date'], '%Y-%m-%d').date()
        except ValueError:
            pass

    return result


def classify_instrument_exclusion(
    inst_status: dict,
    min_history_days: int = 365,
    min_funding_coverage: float = 0.80,
    max_staleness_days: int = 7
) -> Optional[str]:
    """
    Determine if instrument should be excluded and why.

    Conservative classification based on explicit thresholds only.
    Avoids heavy heuristics in Phase 1.

    Args:
        inst_status: Instrument status dict from V1 report
        min_history_days: Minimum days of history required (configurable)
        min_funding_coverage: Minimum funding coverage % (configurable)
        max_staleness_days: Maximum staleness tolerance (configurable)

    Returns:
        None if eligible, else exclusion reason code
    """
    # Check delisted (hard fail)
    if inst_status.get('lifecycle', {}).get('status') == 'delisted':
        return "delisted"

    # Check staleness (configurable threshold)
    if inst_status.get('staleness_days', 0) > max_staleness_days:
        return "stale"

    # Check history (configurable threshold)
    lifecycle = inst_status.get('lifecycle', {})
    if lifecycle.get('actual_history_days', 0) < min_history_days:
        return "insufficient_history"

    # Check funding coverage (configurable threshold)
    funding_status = inst_status.get('funding_status', {})
    if funding_status.get('coverage_pct', 0.0) < min_funding_coverage:
        return "missing_funding"

    # NO data quality heuristics in Phase 1
    # (price_spikes, date_gaps are reported but not gating)
    # Will add quality-based exclusions in later phases after observing false positives

    return None  # Eligible


def generate_data_status_report_v1(
    data_dir: Path,
    instruments: List[str],
    expected_as_of_date: Optional[date] = None,
    include_staleness: bool = True
) -> Dict:
    """
    Generate day-level data status report (V1).

    Enhanced version of generate_data_status_report with:
    - Day-level precision (not month-level)
    - Two-date concept (expected vs dataset as_of_date)
    - Per-instrument staleness tracking

    Args:
        data_dir: Root data directory (e.g., data/raw/binance)
        instruments: List of instrument IDs (e.g., BTCUSDT_PERP) or Binance symbols (BTCUSDT)
        expected_as_of_date: Expected freshness date (default: yesterday UTC)
        include_staleness: If True, include staleness tracking (default: True)

    Returns:
        Dictionary with day-level data status for all instruments (keyed by instrument IDs)
    """
    if expected_as_of_date is None:
        expected_as_of_date = (datetime.utcnow().date() - timedelta(days=1))

    # Convert instrument IDs to Binance symbols for filesystem lookups
    # Map: instrument_id -> binance_symbol
    instrument_to_symbol = {}
    symbols_list = []
    for inst in instruments:
        if inst.endswith('_PERP'):
            symbol = inst.replace('_PERP', '')
            instrument_to_symbol[inst] = symbol
        else:
            # Already a symbol, use as-is
            symbol = inst
            instrument_to_symbol[inst] = symbol
        symbols_list.append(symbol)

    # Compute dates and staleness using Binance symbols
    _, dataset_as_of_date, staleness_report = compute_dates_and_staleness(
        data_dir,
        symbols_list,
        expected_as_of_date
    )

    instrument_status = {}
    total_up_to_date = 0
    total_lagging = 0
    max_staleness = 0

    # Eligibility tracking
    eligibility_counts = {
        "eligible": 0,
        "excluded_staleness": 0,
        "excluded_missing_funding": 0,
        "excluded_insufficient_history": 0,
        "excluded_data_quality": 0,
        "excluded_delisted": 0
    }
    exclusion_reasons = {
        "insufficient_history": [],
        "missing_funding": [],
        "data_quality": [],
        "delisted": [],
        "stale_>7d": []
    }

    # Get metadata directory (sibling to data_dir)
    metadata_dir = data_dir.parent.parent / 'metadata'

    # Use original instrument IDs (with _PERP) in output
    for inst_id, symbol in instrument_to_symbol.items():
        last_date = staleness_report[symbol]['last_available_date']
        staleness_days = staleness_report[symbol]['staleness_days']

        # New instrument with no Vision data yet — record and skip
        if last_date is None:
            instrument_status[inst_id] = {
                "last_available_date": None,
                "staleness_days": None,
                "status": "no_data",
                "warnings": ["No Vision data found — instrument may be newly listed"],
            }
            continue

        # Load klines dates (cached for funding coverage computation)
        klines_dates = load_klines_dates(data_dir, symbol)

        # Compute funding coverage
        funding_status = compute_funding_coverage(data_dir, symbol, klines_dates)

        # Load lifecycle metadata
        lifecycle_meta = load_lifecycle_metadata(metadata_dir, symbol)

        # Compute lifecycle history metrics
        if klines_dates:
            actual_history_days = len(klines_dates)
            first_date = min(klines_dates)
            last_date_klines = max(klines_dates)

            # Expected history: from launch or config start (use 2020-01-01 as default)
            start_date = lifecycle_meta.get("launch_date") or date(2020, 1, 1)
            expected_history_days = (last_date_klines - start_date).days + 1
            coverage_pct = actual_history_days / expected_history_days if expected_history_days > 0 else 0.0
        else:
            actual_history_days = 0
            coverage_pct = 0.0
            expected_history_days = 0

        lifecycle_status = {
            "launch_date": str(lifecycle_meta.get("launch_date")) if lifecycle_meta.get("launch_date") else None,
            "delist_date": str(lifecycle_meta.get("delist_date")) if lifecycle_meta.get("delist_date") else None,
            "status": lifecycle_meta.get("status", "active"),
            "expected_history_days": expected_history_days,
            "actual_history_days": actual_history_days,
            "coverage_pct": coverage_pct
        }

        # Compute data quality metrics (raw counts only)
        data_quality_metrics = {
            "price_spikes_50pct": 0,  # TODO: implement if needed
            "date_gaps_7d": 0  # TODO: implement if needed
        }

        # Compute missing months for klines
        if klines_dates:
            klines_months = set(d.strftime('%Y-%m') for d in klines_dates)
            # Find gaps in month sequence
            first_month = min(klines_months)
            last_month = max(klines_months)
            missing_months = []
            current = datetime.strptime(first_month, '%Y-%m')
            end = datetime.strptime(last_month, '%Y-%m')
            while current <= end:
                month_str = current.strftime('%Y-%m')
                if month_str not in klines_months:
                    missing_months.append(month_str)
                # Move to next month
                if current.month == 12:
                    current = current.replace(year=current.year + 1, month=1)
                else:
                    current = current.replace(month=current.month + 1)
        else:
            missing_months = []

        # Determine status (expanded classification)
        if staleness_days == 0:
            status = "up_to_date"
            total_up_to_date += 1
        else:
            status = "lagging"
            total_lagging += 1

        max_staleness = max(max_staleness, staleness_days)

        # Determine data sources
        data_sources = {}

        # Check Vision monthly
        last_month = get_last_available_month(data_dir, symbol, "klines")
        if last_month:
            data_sources['vision_monthly_through'] = last_month
        else:
            data_sources['vision_monthly_through'] = None

        # Check Vision daily (not commonly used, but check)
        data_sources['vision_daily_through'] = None  # TODO: implement if needed

        # Check API cache
        api_cache_dir = data_dir / 'api_cache' / symbol
        if api_cache_dir.exists():
            cache_files = list(api_cache_dir.glob("*_klines.parquet"))
            if cache_files:
                # Find latest API cache date
                cache_dates = []
                for f in cache_files:
                    parts = f.stem.split('_')
                    for part in parts:
                        if len(part) == 10 and part[4] == '-' and part[7] == '-':
                            try:
                                d = datetime.strptime(part, '%Y-%m-%d').date()
                                cache_dates.append(d)
                            except ValueError:
                                continue
                if cache_dates:
                    data_sources['api_cache_through'] = str(max(cache_dates))
                else:
                    data_sources['api_cache_through'] = None
            else:
                data_sources['api_cache_through'] = None
        else:
            data_sources['api_cache_through'] = None

        warnings = []
        if status == "lagging":
            warnings.append(
                f"Lagging by {staleness_days} day(s) (eligibility rules will apply)"
            )

        # Build instrument status entry
        inst_status_entry = {
            "last_available_date": str(last_date),
            "staleness_days": staleness_days,
            "data_sources": data_sources,
            "status": status,
            "warnings": warnings,
            "missing_months": missing_months,
            "funding_status": funding_status,
            "data_quality_metrics": data_quality_metrics,
            "lifecycle": lifecycle_status,
            "schema_compliant": True  # TODO: implement schema validation if needed
        }

        # Classify exclusion
        exclusion_reason = classify_instrument_exclusion(inst_status_entry)
        inst_status_entry["exclusion_recommendation"] = exclusion_reason

        # Update eligibility counts
        if exclusion_reason is None:
            eligibility_counts["eligible"] += 1
        else:
            if exclusion_reason == "stale":
                eligibility_counts["excluded_staleness"] += 1
                exclusion_reasons["stale_>7d"].append(inst_id)
            elif exclusion_reason == "insufficient_history":
                eligibility_counts["excluded_insufficient_history"] += 1
                exclusion_reasons["insufficient_history"].append(inst_id)
            elif exclusion_reason == "missing_funding":
                eligibility_counts["excluded_missing_funding"] += 1
                exclusion_reasons["missing_funding"].append(inst_id)
            elif exclusion_reason == "delisted":
                eligibility_counts["excluded_delisted"] += 1
                exclusion_reasons["delisted"].append(inst_id)

        # Use instrument ID (with _PERP) as key in output
        instrument_status[inst_id] = inst_status_entry

    return {
        "generated_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "expected_as_of_date": str(expected_as_of_date),
        "dataset_as_of_date": str(dataset_as_of_date),
        "lag_policy_days": 1,
        "cadence": "daily",
        "instruments": instrument_status,
        "summary": {
            "total_instruments": len(instruments),
            "up_to_date": total_up_to_date,
            "lagging": total_lagging,
            "max_staleness_days": max_staleness,
            "as_of_date_alignment": "strict_pass" if max_staleness == 0 else "strict_fail",
            "eligibility_classification": eligibility_counts,
            "exclusion_reasons": exclusion_reasons
        }
    }
