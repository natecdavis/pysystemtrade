#!/usr/bin/env python3
"""
Build example crypto perpetual futures dataset

For Phase 1 MVP, generates synthetic data with realistic characteristics.
In production, this would load from raw data files in data/raw/

Output: data/example_crypto_perps.parquet with schema:
    - date: UTC date
    - instrument: instrument code (e.g., BTCUSDT_PERP)
    - close: close price
    - funding_rate: funding rate (applies from close(t-1) to close(t))
    - adv_notional: average daily volume (notional)
    - spread_frac: bid-ask spread as fraction (fixed placeholder for Phase 1)
    - taker_fee_frac: taker fee as fraction (fixed placeholder for Phase 1)
"""

import pandas as pd
import numpy as np
from pathlib import Path
import zipfile
import json
import warnings
import logging

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Symbol mapping: internal ID -> Binance Data Vision symbol
BINANCE_SYMBOL_MAP = {
    'BTCUSDT_PERP': 'BTCUSDT',
    'ETHUSDT_PERP': 'ETHUSDT',
    'BNBUSDT_PERP': 'BNBUSDT',
    'SOLUSDT_PERP': 'SOLUSDT',
    'XRPUSDT_PERP': 'XRPUSDT',
    # Phase 2 symbols (first 15)
    'LTCUSDT_PERP': 'LTCUSDT',
    'EOSUSDT_PERP': 'EOSUSDT',
    'DOTUSDT_PERP': 'DOTUSDT',
    'LINKUSDT_PERP': 'LINKUSDT',
    'ADAUSDT_PERP': 'ADAUSDT',
    'DOGEUSDT_PERP': 'DOGEUSDT',
    'MATICUSDT_PERP': 'MATICUSDT',
    'AVAXUSDT_PERP': 'AVAXUSDT',
    'UNIUSDT_PERP': 'UNIUSDT',
    'BCHUSDT_PERP': 'BCHUSDT',
    # Additional 15 symbols (expansion to 30)
    'ATOMUSDT_PERP': 'ATOMUSDT',
    'TRXUSDT_PERP': 'TRXUSDT',
    'ETCUSDT_PERP': 'ETCUSDT',
    'XLMUSDT_PERP': 'XLMUSDT',
    'FILUSDT_PERP': 'FILUSDT',
    'AAVEUSDT_PERP': 'AAVEUSDT',
    'SANDUSDT_PERP': 'SANDUSDT',
    'MANAUSDT_PERP': 'MANAUSDT',
    'AXSUSDT_PERP': 'AXSUSDT',
    'ICPUSDT_PERP': 'ICPUSDT',
    'VETUSDT_PERP': 'VETUSDT',
    'THETAUSDT_PERP': 'THETAUSDT',
    'FTMUSDT_PERP': 'FTMUSDT',
    'ALGOUSDT_PERP': 'ALGOUSDT',
    'NEOUSDT_PERP': 'NEOUSDT',
    # Tier 4 expansion (22 additional symbols)
    'NEARUSDT_PERP': 'NEARUSDT',
    'APTUSDT_PERP': 'APTUSDT',
    'ARBUSDT_PERP': 'ARBUSDT',
    'OPUSDT_PERP': 'OPUSDT',
    'CRVUSDT_PERP': 'CRVUSDT',
    'SNXUSDT_PERP': 'SNXUSDT',
    'MKRUSDT_PERP': 'MKRUSDT',
    'COMPUSDT_PERP': 'COMPUSDT',
    'LDOUSDT_PERP': 'LDOUSDT',
    'RUNEUSDT_PERP': 'RUNEUSDT',
    'SUSHIUSDT_PERP': 'SUSHIUSDT',
    'GALAUSDT_PERP': 'GALAUSDT',
    'ENJUSDT_PERP': 'ENJUSDT',
    'IMXUSDT_PERP': 'IMXUSDT',
    'GRTUSDT_PERP': 'GRTUSDT',
    'RENDERUSDT_PERP': 'RENDERUSDT',
    '1INCHUSDT_PERP': '1INCHUSDT',
    'APEUSDT_PERP': 'APEUSDT',
    'CHZUSDT_PERP': 'CHZUSDT',
    'ZILUSDT_PERP': 'ZILUSDT',
    'ZRXUSDT_PERP': 'ZRXUSDT',
    'IOTAUSDT_PERP': 'IOTAUSDT'
}


def inspect_alignment(
    klines_path: Path,
    funding_path: Path,
    sample_days: int = 3
) -> None:
    """
    Sanity-check alignment between kline close_time and funding calcTime.

    Prints sample rows from kline and funding data to verify:
    1. What UTC date does kline 'date' represent?
    2. What calendar date do funding events belong to?
    3. Is there a natural alignment or do we need a shift?

    Args:
        klines_path: Path to a single kline ZIP file (e.g., BTCUSDT-1d-2023-01.zip)
        funding_path: Path to a single funding ZIP file (e.g., BTCUSDT-fundingRate-2023-01.zip)
        sample_days: Number of days to print for inspection

    Usage:
        inspect_alignment(
            Path('data/raw/binance/klines/BTCUSDT/BTCUSDT-1d-2023-01.zip'),
            Path('data/raw/binance/funding_rates/BTCUSDT/BTCUSDT-fundingRate-2023-01.zip'),
            sample_days=3
        )
    """
    # Load klines
    with zipfile.ZipFile(klines_path) as z:
        csv_name = klines_path.stem + '.csv'
        with z.open(csv_name) as f:
            klines = pd.read_csv(f, header=None, names=[
                'open_time', 'open', 'high', 'low', 'close', 'volume',
                'close_time', 'quote_volume', 'trades',
                'taker_buy_base_vol', 'taker_buy_quote_vol', 'ignore'
            ], nrows=sample_days)

    # Load funding
    with zipfile.ZipFile(funding_path) as z:
        csv_name = funding_path.stem + '.csv'
        with z.open(csv_name) as f:
            funding = pd.read_csv(f, header=None, names=[
                'calcTime', 'fundingRate', 'markPrice'
            ], nrows=sample_days * 3)  # 3 events per day

    # Parse timestamps
    klines['close_time_utc'] = pd.to_datetime(klines['close_time'], unit='ms', utc=True)
    klines['date'] = klines['close_time_utc'].dt.date
    funding['calcTime_utc'] = pd.to_datetime(funding['calcTime'], unit='ms', utc=True)
    funding['event_date'] = funding['calcTime_utc'].dt.date

    print("=" * 80)
    print(f"KLINE CLOSE TIMES (sample_days={sample_days})")
    print("=" * 80)
    print(klines[['date', 'close_time_utc', 'close']].to_string(index=False))

    print("\n" + "=" * 80)
    print(f"FUNDING EVENT TIMES (first {sample_days * 3} events)")
    print("=" * 80)
    print(funding[['event_date', 'calcTime_utc', 'fundingRate']].to_string(index=False))

    print("\n" + "=" * 80)
    print("ALIGNMENT VERIFICATION")
    print("=" * 80)
    print("Question: Should funding_rate[date=D] include events from calendar date D or D-1?")
    print("Inspect the timestamps above to determine the correct mapping.")
    print("=" * 80)


def normalize_kline_columns(df: pd.DataFrame, file_path: Path) -> pd.DataFrame:
    """
    Normalize Binance kline column names to canonical format

    Handles variations: close_time vs CloseTime vs closeTime

    Raises:
        ValueError: If required columns cannot be mapped
    """
    # Column mapping (lowercase for case-insensitive matching)
    col_map_lower = {col.lower(): col for col in df.columns}

    # Required columns and their variations
    required = {
        'close_time': ['close_time', 'closetime', 'close time'],
        'close': ['close', 'close price'],
        'volume': ['volume', 'base volume', 'base_volume'],
        'quote_volume': ['quote_volume', 'quotevolume', 'quote volume', 'quoteassetvolume', 'quote_asset_volume'],
    }

    # Map columns
    mapped = {}
    for canonical, variations in required.items():
        found = None
        for var in variations:
            if var.lower() in col_map_lower:
                found = col_map_lower[var.lower()]
                break

        if found is None:
            raise ValueError(
                f"Cannot find required column '{canonical}' in {file_path}. "
                f"Available columns: {list(df.columns)}. "
                f"Binance may have changed CSV schema."
            )
        mapped[found] = canonical

    return df.rename(columns=mapped)


def normalize_funding_columns(df: pd.DataFrame, file_path: Path) -> pd.DataFrame:
    """
    Normalize Binance funding rate column names to canonical format

    Handles variations:
    - Time: calc_time, calcTime, funding_time, fundingTime
    - Rate: last_funding_rate, funding_rate, fundingRate, rate

    Raises:
        ValueError: If required columns cannot be mapped
    """
    col_map_lower = {col.lower(): col for col in df.columns}

    required = {
        'calcTime': ['calc_time', 'calctime', 'funding_time', 'fundingtime'],
        'fundingRate': ['last_funding_rate', 'funding_rate', 'fundingrate', 'lastfundingrate', 'rate'],
    }

    mapped = {}
    for canonical, variations in required.items():
        found = None
        for var in variations:
            if var.lower() in col_map_lower:
                found = col_map_lower[var.lower()]
                break

        if found is None:
            raise ValueError(
                f"Cannot find required column '{canonical}' in {file_path}. "
                f"Available columns: {list(df.columns)}. "
                f"Tried variations: {variations}. "
                f"Binance may have changed CSV schema."
            )
        mapped[found] = canonical

    return df.rename(columns=mapped)


def has_header(first_line: bytes) -> bool:
    """
    Detect if first line is a header (contains letters) or data (purely numeric/punctuation)

    Args:
        first_line: First line of CSV as bytes

    Returns:
        True if line contains alphabetic characters (header), False otherwise
    """
    try:
        line_str = first_line.decode('utf-8').strip()
        # Check if line contains any alphabetic characters
        return any(c.isalpha() for c in line_str)
    except Exception:
        # If decoding fails, assume no header
        return False


def load_binance_klines(
    instrument: str,
    data_dir: Path,
    fail_on_missing_close: bool = False,
    include_api_cache: bool = False
) -> pd.DataFrame:
    """
    Load all kline files for instrument using glob discovery with header autodetection

    Args:
        instrument: Internal instrument ID (e.g., 'BTCUSDT_PERP')
        data_dir: Root data directory (e.g., Path('data/raw'))
        fail_on_missing_close: If True, raise error if any rows with NaN close are dropped
        include_api_cache: If True, also load from api_cache directory and deduplicate (Vision > API)

    Returns:
        DataFrame with columns: date, close, volume, quote_volume

    Notes:
        - Autodetects header vs headerless CSV format
        - Works with daily or monthly archives (glob pattern matches both)
        - date is derived from close_time (end-of-day timestamp)
        - NaN close rows are dropped with logging
        - If include_api_cache=True, merges Vision ZIPs + API cache with deduplication
    """
    # Map internal ID to Binance symbol
    binance_symbol = BINANCE_SYMBOL_MAP[instrument]
    klines_dir = data_dir / 'klines' / binance_symbol

    # Glob discover all kline ZIP files (daily or monthly)
    all_data = []
    skipped_files = []

    zip_files = sorted(klines_dir.glob(f'{binance_symbol}-1d-*.zip'))
    logger.info(f"Found {len(zip_files)} kline files for {binance_symbol} in {klines_dir}")
    if len(zip_files) > 0:
        logger.info(f"  First file: {zip_files[0].name}, Last file: {zip_files[-1].name}")

    for zip_file in zip_files:
        with zipfile.ZipFile(zip_file) as z:
            csv_name = zip_file.stem + '.csv'  # Binance convention
            with z.open(csv_name) as f_raw:
                # Read all data
                from io import BytesIO
                data = f_raw.read()

                # Detect header by checking first line
                first_line = data.split(b'\n')[0]
                has_header_row = has_header(first_line)

                try:
                    if has_header_row:
                        # Parse with header
                        df = pd.read_csv(BytesIO(data), header=0)
                        df = normalize_kline_columns(df, zip_file)
                        logger.info(f"✓ Loaded {zip_file.name} (with header): {len(df)} rows")
                    else:
                        # Parse without header, assign standard Binance kline column names
                        df = pd.read_csv(BytesIO(data), header=None)
                        # Binance klines standard 12-column format:
                        # 0: open_time, 1: open, 2: high, 3: low, 4: close, 5: volume,
                        # 6: close_time, 7: quote_volume, 8: count, 9: taker_buy_volume,
                        # 10: taker_buy_quote_volume, 11: ignore
                        if df.shape[1] != 12:
                            raise ValueError(f"Expected 12 columns for headerless kline, got {df.shape[1]}")

                        df.columns = [
                            'open_time', 'open', 'high', 'low', 'close', 'volume',
                            'close_time', 'quote_volume', 'count', 'taker_buy_volume',
                            'taker_buy_quote_volume', 'ignore'
                        ]
                        logger.info(f"✓ Loaded {zip_file.name} (headerless): {len(df)} rows")

                    all_data.append(df)

                except Exception as e:
                    skip_reason = f"Parse error: {str(e)}"
                    skipped_files.append((zip_file, skip_reason))
                    logger.warning(f"SKIPPED {zip_file.name}: {skip_reason}")

    if skipped_files:
        logger.warning(f"{instrument}: Skipped {len(skipped_files)} files due to parsing errors")
        for path, reason in skipped_files[:5]:  # Show first 5
            logger.warning(f"  {path.name}: {reason}")

    if not all_data:
        raise FileNotFoundError(f"No kline files found for {binance_symbol} in {klines_dir}")

    klines = pd.concat(all_data, ignore_index=True)

    # Convert close_time to date
    # NOTE: close_time is end-of-day timestamp in milliseconds
    klines['date'] = pd.to_datetime(klines['close_time'], unit='ms', utc=True)
    klines['date'] = klines['date'].dt.date
    klines['date'] = pd.to_datetime(klines['date'])

    # Select relevant columns
    klines = klines[['date', 'close', 'volume', 'quote_volume']].copy()

    # Ensure date column is datetime (prevent set-intersection mismatches later)
    klines['date'] = pd.to_datetime(klines['date'], utc=True).dt.tz_convert(None)

    # Coerce to numeric (handle any non-numeric values in source CSV)
    klines['close'] = pd.to_numeric(klines['close'], errors='coerce')
    klines['volume'] = pd.to_numeric(klines['volume'], errors='coerce')
    klines['quote_volume'] = pd.to_numeric(klines['quote_volume'], errors='coerce')

    # Handle NaN close prices: drop rows and log
    nan_close_mask = klines['close'].isna()
    if nan_close_mask.any():
        nan_count = nan_close_mask.sum()
        nan_dates = klines[nan_close_mask]['date'].tolist()
        logger.warning(
            f"{instrument}: Dropping {nan_count} rows with NaN/missing close prices. "
            f"Sample dates: {nan_dates[:5]}"
        )

        if fail_on_missing_close:
            raise ValueError(
                f"{instrument}: {nan_count} rows have NaN close prices (--fail-on-missing-close enabled). "
                f"Sample dates: {nan_dates[:10]}"
            )

        klines = klines[~nan_close_mask].copy()

    # Validate monotonic dates, no duplicates
    if not klines['date'].is_monotonic_increasing:
        klines = klines.sort_values('date').reset_index(drop=True)

    if klines['date'].duplicated().any():
        logger.warning(f"{instrument}: Duplicate dates found, keeping first occurrence")
        klines = klines.drop_duplicates(subset='date', keep='first')

    # Validate price range (allow low-priced assets like DOGE)
    min_price, max_price = 0.0001, 1e6
    if not (klines['close'] >= min_price).all():
        raise ValueError(f"{instrument}: Prices below ${min_price}")
    if not (klines['close'] <= max_price).all():
        raise ValueError(f"{instrument}: Prices above ${max_price}")

    # Validate volume is non-negative
    if not (klines['quote_volume'] >= 0).all():
        raise ValueError(f"{instrument}: Negative volume found")

    vision_klines = klines.sort_values('date').reset_index(drop=True)

    # Load API cache if requested (V1 daily operations)
    if include_api_cache:
        api_cache_dir = data_dir / 'api_cache' / binance_symbol
        if api_cache_dir.exists():
            cache_files = list(api_cache_dir.glob('*_klines.parquet'))
            if cache_files:
                logger.info(f"Loading {len(cache_files)} API cache files for {binance_symbol}")

                api_cache_dfs = []
                for cache_file in cache_files:
                    try:
                        df = pd.read_parquet(cache_file)
                        # API cache has columns: date, open, high, low, close, volume, quote_volume
                        # Ensure date is datetime
                        if not pd.api.types.is_datetime64_any_dtype(df['date']):
                            df['date'] = pd.to_datetime(df['date'])
                        api_cache_dfs.append(df)
                    except Exception as e:
                        logger.warning(f"Failed to load API cache {cache_file.name}: {e}")

                if api_cache_dfs:
                    api_cache_klines = pd.concat(api_cache_dfs, ignore_index=True)

                    # Ensure date column format matches Vision data
                    api_cache_klines['date'] = pd.to_datetime(api_cache_klines['date'], utc=True).dt.tz_convert(None)

                    # Select columns matching Vision data
                    api_cache_klines = api_cache_klines[['date', 'close', 'volume', 'quote_volume']].copy()

                    # Coerce to numeric
                    api_cache_klines['close'] = pd.to_numeric(api_cache_klines['close'], errors='coerce')
                    api_cache_klines['volume'] = pd.to_numeric(api_cache_klines['volume'], errors='coerce')
                    api_cache_klines['quote_volume'] = pd.to_numeric(api_cache_klines['quote_volume'], errors='coerce')

                    # Drop NaN close
                    api_cache_klines = api_cache_klines[api_cache_klines['close'].notna()].copy()

                    # Merge with Vision data (Vision > API cache for duplicates)
                    logger.info(
                        f"Merging Vision ({len(vision_klines)} rows) + "
                        f"API cache ({len(api_cache_klines)} rows)"
                    )

                    # Concatenate
                    merged = pd.concat([vision_klines, api_cache_klines], ignore_index=True)

                    # Deduplicate by date (keep first = Vision priority)
                    merged = merged.sort_values('date')
                    duplicates = merged.duplicated(subset='date', keep='first')
                    if duplicates.any():
                        logger.info(
                            f"Deduplicating {duplicates.sum()} rows "
                            f"(Vision data takes precedence over API cache)"
                        )
                    merged = merged.drop_duplicates(subset='date', keep='first')

                    return merged.sort_values('date').reset_index(drop=True)
        else:
            logger.debug(f"No API cache directory found for {binance_symbol}")

    return vision_klines


def consolidate_funding_to_daily(funding_events: pd.DataFrame) -> pd.DataFrame:
    """
    Consolidate 8-hourly funding events to daily with verified alignment

    Args:
        funding_events: DataFrame with columns ['calcTime', 'fundingRate']
                        where calcTime is UTC datetime

    Returns:
        DataFrame with columns ['date', 'funding_rate']

    Invariant: Mapping depends on verified alignment from inspect_alignment()
    - If no shift needed: funding_rate[D] = sum of events from calendar day D
    - If shift needed: funding_rate[D] = sum of events from calendar day D-1
    """
    # Extract calendar date from event timestamp
    funding_events['event_date'] = funding_events['calcTime'].dt.date
    funding_events['event_date'] = pd.to_datetime(funding_events['event_date'])

    # Sum by calendar date
    daily = funding_events.groupby('event_date')['fundingRate'].sum().reset_index()
    daily = daily.rename(columns={'event_date': 'date', 'fundingRate': 'funding_rate'})

    # Ensure output 'date' column is naive datetime64[ns] (MUST match klines dtype exactly)
    daily['date'] = pd.to_datetime(daily['date'], utc=True).dt.tz_convert(None)

    # Apply shift based on inspect_alignment() verification
    # EXPECTED: NO SHIFT (verify with inspect_alignment() before production use)
    # DEFAULT: NO SHIFT (uncomment if verification shows shift is needed)
    # daily['date'] = daily['date'] + pd.Timedelta(days=1)

    # Validate one row per date
    if daily['date'].duplicated().any():
        raise ValueError("Daily funding consolidation produced duplicate dates")

    return daily.sort_values('date').reset_index(drop=True)


def load_binance_funding_rates(instrument: str, data_dir: Path) -> pd.DataFrame:
    """
    Load and consolidate funding rates to daily with correct alignment

    Args:
        instrument: Internal instrument ID (e.g., 'BTCUSDT_PERP')
        data_dir: Root data directory (e.g., Path('data/raw'))

    Returns:
        DataFrame with columns: date, funding_rate
        where funding_rate[D] = sum of funding events from calendar day D
        (per default invariant - adjust if inspect_alignment() shows otherwise)
    """
    # Map internal ID to Binance symbol
    binance_symbol = BINANCE_SYMBOL_MAP[instrument]
    funding_dir = data_dir / 'funding_rates' / binance_symbol

    # Glob discover all funding ZIP files (daily or monthly)
    all_data = []
    skipped_files = []

    zip_files = sorted(funding_dir.glob(f'{binance_symbol}-fundingRate-*.zip'))
    logger.info(f"Found {len(zip_files)} funding files for {binance_symbol}")

    for zip_file in zip_files:
        with zipfile.ZipFile(zip_file) as z:
            csv_name = zip_file.stem + '.csv'  # Binance convention
            with z.open(csv_name) as f_raw:
                from io import BytesIO
                data = f_raw.read()

                # Detect header (funding files from 2020+ all have headers based on ground truth)
                first_line = data.split(b'\n')[0]
                has_header_row = has_header(first_line)

                try:
                    if has_header_row:
                        # Parse with header
                        df = pd.read_csv(BytesIO(data), header=0)
                        df = normalize_funding_columns(df, zip_file)
                        logger.info(f"✓ Funding {zip_file.name} (with header): {len(df)} rows")
                    else:
                        # Headerless funding files (if they exist)
                        df = pd.read_csv(BytesIO(data), header=None)
                        # Standard Binance funding format (3 columns):
                        # 0: calc_time, 1: funding_interval_hours, 2: funding_rate
                        if df.shape[1] >= 2:
                            df.columns = ['calcTime', 'funding_interval_hours', 'fundingRate'] + \
                                        [f'col_{i}' for i in range(3, df.shape[1])]
                        else:
                            raise ValueError(f"Expected at least 2 columns, got {df.shape[1]}")
                        logger.info(f"✓ Funding {zip_file.name} (headerless): {len(df)} rows")

                    all_data.append(df)

                except Exception as e:
                    skip_reason = f"Parse error: {str(e)}"
                    skipped_files.append((zip_file, skip_reason))
                    logger.warning(f"SKIPPED {zip_file.name}: {skip_reason}")

    if skipped_files:
        logger.warning(f"{instrument}: Skipped {len(skipped_files)} funding files due to parsing errors")
        for path, reason in skipped_files[:5]:
            logger.warning(f"  {path.name}: {reason}")

    if not all_data:
        raise FileNotFoundError(f"No funding rate files found for {binance_symbol} in {funding_dir}")

    funding_events = pd.concat(all_data, ignore_index=True)

    # Parse timestamps (ensure naive datetime for consistency)
    funding_events['calcTime'] = pd.to_datetime(funding_events['calcTime'], unit='ms', utc=True).dt.tz_convert(None)

    # Coerce fundingRate to numeric
    funding_events['fundingRate'] = pd.to_numeric(funding_events['fundingRate'], errors='coerce')

    # Drop rows with NaN funding rate (data quality issue)
    nan_funding_mask = funding_events['fundingRate'].isna()
    if nan_funding_mask.any():
        nan_count = nan_funding_mask.sum()
        logger.warning(f"{instrument}: Dropping {nan_count} funding events with NaN rates")
        funding_events = funding_events[~nan_funding_mask].copy()

    # Consolidate using helper function
    daily_funding = consolidate_funding_to_daily(funding_events)

    # Validate funding rate range (daily sum: typical range -1% to +3%)
    if not (daily_funding['funding_rate'] >= -0.01).all():
        logger.warning(f"{instrument}: Some funding rates < -1% daily (extreme market conditions)")
    if not (daily_funding['funding_rate'] <= 0.03).all():
        logger.warning(f"{instrument}: Some funding rates > 3% daily (extreme market conditions)")

    return daily_funding


def validate_time_series_quality(df: pd.DataFrame, symbol: str, max_gap_days: int = 7, max_price_jump: float = 0.5) -> list:
    """
    Check for data quality issues in time series.

    Args:
        df: DataFrame with date index and close prices
        symbol: Symbol name for logging
        max_gap_days: Max acceptable gap (warn if exceeded)
        max_price_jump: Max 1-day price change fraction (warn if exceeded)

    Returns:
        List of warning messages
    """
    issues = []

    # Ensure df is sorted by date
    df = df.sort_values('date')

    # Check for gaps
    date_diffs = df['date'].diff()
    gaps = date_diffs[date_diffs > pd.Timedelta(days=max_gap_days)]
    if len(gaps) > 0:
        gap_dates = df.loc[gaps.index, 'date'].tolist()
        issues.append(f"{symbol}: {len(gaps)} gaps >{max_gap_days} days (sample: {gap_dates[:3]})")

    # Check for price jumps
    returns = df['close'].pct_change()
    jumps = returns[abs(returns) > max_price_jump]
    if len(jumps) > 0:
        jump_dates = df.loc[jumps.index, 'date'].tolist()
        issues.append(f"{symbol}: {len(jumps)} price jumps >{max_price_jump*100}% (sample: {jump_dates[:3]})")

    # Check funding coverage (descriptive, report stats)
    funding_missing = df['funding_rate'].isna().sum()
    if funding_missing / len(df) > 0.10:
        issues.append(f"{symbol}: {funding_missing/len(df)*100:.1f}% missing funding")

    # Report funding stress events (descriptive, not asserting)
    # Note: funding_rate is daily sum of 8h events (not avg)
    funding_valid = df['funding_rate'].dropna()
    if len(funding_valid) > 0:
        funding_stats = {
            'min': funding_valid.min(),
            'p01': funding_valid.quantile(0.01),
            'p99': funding_valid.quantile(0.99),
            'max': funding_valid.max(),
            'high_stress_days': (funding_valid > 0.02).sum(),  # >2% daily
            'low_stress_days': (funding_valid < -0.005).sum()  # <-0.5% daily
        }
        # Log funding stress metrics (don't fail)
        if funding_stats['high_stress_days'] > 0 or funding_stats['low_stress_days'] > 0:
            issues.append(
                f"{symbol}: Funding stress - "
                f"high: {funding_stats['high_stress_days']} days, "
                f"low: {funding_stats['low_stress_days']} days, "
                f"range: [{funding_stats['min']:.4f}, {funding_stats['max']:.4f}]"
            )

    return issues


def report_regime_coverage(prices_df: pd.DataFrame) -> dict:
    """
    Report volatility regime coverage (descriptive, not asserting)

    Checks:
    1. Specific known high-vol window present (2020-03: COVID crash)
    2. Volatility percentile spread (p10 vs p90) is wide
    3. Reports metrics, avoids hard asserts (definitions not yet stable)

    Args:
        prices_df: DataFrame with date index and price columns (one per instrument)

    Returns:
        Dict with regime coverage statistics
    """
    daily_vols = {}
    for col in prices_df.columns:
        returns = prices_df[col].pct_change()
        vol = returns.rolling(30).std() * np.sqrt(365)
        daily_vols[col] = vol

    vol_df = pd.DataFrame(daily_vols)

    # Compute distribution statistics
    vol_values = vol_df.values.flatten()
    vol_values = vol_values[~np.isnan(vol_values)]

    stats = {
        'vol_min': vol_values.min(),
        'vol_p10': np.percentile(vol_values, 10),
        'vol_p50': np.percentile(vol_values, 50),
        'vol_p90': np.percentile(vol_values, 90),
        'vol_max': vol_values.max(),
        'percentile_spread': np.percentile(vol_values, 90) - np.percentile(vol_values, 10)
    }

    # Check for specific known windows (COVID crash Mar 2020)
    covid_window = ('2020-03-01', '2020-03-31')
    has_covid = (
        pd.Timestamp(covid_window[0]) in prices_df.index and
        pd.Timestamp(covid_window[1]) in prices_df.index
    )
    stats['has_covid_crash_window'] = has_covid

    # Log results (descriptive, not failing)
    logger.info("=" * 80)
    logger.info("Regime Coverage Report:")
    logger.info("=" * 80)
    logger.info(f"  Vol min: {stats['vol_min']:.2f}")
    logger.info(f"  Vol p10: {stats['vol_p10']:.2f}")
    logger.info(f"  Vol p50 (median): {stats['vol_p50']:.2f}")
    logger.info(f"  Vol p90: {stats['vol_p90']:.2f}")
    logger.info(f"  Vol max: {stats['vol_max']:.2f}")
    logger.info(f"  Percentile spread (p90-p10): {stats['percentile_spread']:.2f}")
    logger.info(f"  Includes COVID crash window (2020-03): {has_covid}")

    # Sanity checks (log warnings, don't fail)
    if stats['percentile_spread'] < 0.3:
        logger.warning("  WARNING: Narrow volatility spread, limited regime diversity")
    if not has_covid:
        logger.warning("  WARNING: Missing COVID crash window (Mar 2020)")

    logger.info("=" * 80)

    return stats


def calculate_adv(klines: pd.DataFrame, window: int = 30) -> pd.DataFrame:
    """
    Calculate average daily volume (notional) proxy

    Args:
        klines: DataFrame with 'date' and 'quote_volume' columns
        window: Rolling window size in days (default: 30)

    Returns:
        DataFrame with 'date' and 'adv_notional' columns
    """
    # quote_volume = total notional traded in quote currency (USDT)
    adv = klines.set_index('date')['quote_volume'].rolling(window=window, min_periods=1).mean()
    return adv.reset_index().rename(columns={'quote_volume': 'adv_notional'})


def derive_lifecycle_from_vision_data(
    dataset_df: pd.DataFrame,
    stale_threshold_days: int = 7
) -> dict:
    """
    Derive instrument lifecycle metadata from Vision data coverage.

    Analyzes actual data availability in the dataset to determine:
    - First data date (launch or data availability start)
    - Last data date (current or delisted)
    - Data coverage days
    - Status (ACTIVE, STALE, NO_DATA)

    Args:
        dataset_df: Dataset DataFrame with 'date' and 'instrument' columns
        stale_threshold_days: Days since last data to mark as STALE

    Returns:
        Lifecycle dict keyed by instrument ID:
        {
            'BTCUSDT_PERP': {
                'first_data_date': '2019-09-08',
                'last_data_date': '2026-02-13',
                'data_days': 2350,
                'status': 'ACTIVE',
                'days_since_last': 1
            },
            ...
        }
    """
    from datetime import datetime

    lifecycle = {}
    current_date = datetime.utcnow().date()

    for instrument in dataset_df['instrument'].unique():
        try:
            # Filter to this instrument
            inst_data = dataset_df[dataset_df['instrument'] == instrument].copy()

            if inst_data.empty:
                lifecycle[instrument] = {
                    'first_data_date': None,
                    'last_data_date': None,
                    'data_days': 0,
                    'status': 'NO_DATA',
                    'days_since_last': None
                }
                continue

            # Get date coverage
            dates = pd.to_datetime(inst_data['date']).dt.date
            first_date = dates.min()
            last_date = dates.max()
            data_days = len(dates.unique())

            # Calculate days since last data
            days_since_last = (current_date - last_date).days

            # Determine status
            if days_since_last > stale_threshold_days:
                status = 'STALE'
            else:
                status = 'ACTIVE'

            lifecycle[instrument] = {
                'first_data_date': first_date.isoformat(),
                'last_data_date': last_date.isoformat(),
                'data_days': data_days,
                'status': status,
                'days_since_last': days_since_last
            }

        except Exception as e:
            logger.warning(f"Could not derive lifecycle for {instrument}: {e}")
            lifecycle[instrument] = {
                'status': 'ERROR',
                'error': str(e)
            }

    return lifecycle


def generate_dataset_manifest(
    dataset_df: pd.DataFrame,
    instruments_included: dict,
    instruments_excluded: dict,
    start_date: str,
    end_date: str,
    output_path: Path
) -> dict:
    """
    Generate dataset manifest with inclusion/exclusion audit trail.

    Args:
        dataset_df: Final dataset DataFrame
        instruments_included: {inst_id: metadata_dict}
        instruments_excluded: {inst_id: exclusion_reason}
        start_date: Requested start date (YYYY-MM-DD)
        end_date: Requested end date (YYYY-MM-DD)
        output_path: Where to save manifest JSON (atomic write)

    Returns:
        Manifest dict

    Raises:
        RuntimeError: If manifest included set != dataset instruments set (hard invariant)
    """
    from datetime import datetime
    import tempfile
    import os

    # Get dataset instruments
    dataset_instruments = set(dataset_df['instrument'].unique())

    # Compute date range from dataset
    all_dates = dataset_df['date'].unique()
    actual_start = pd.Timestamp(all_dates.min()).strftime('%Y-%m-%d')
    actual_end = pd.Timestamp(all_dates.max()).strftime('%Y-%m-%d')
    total_days = len(all_dates)

    # Derive lifecycle from Vision data coverage
    lifecycle_data = derive_lifecycle_from_vision_data(dataset_df)

    # Compute lifecycle summary
    lifecycle_summary = {
        'active': sum(1 for lc in lifecycle_data.values() if lc.get('status') == 'ACTIVE'),
        'stale': sum(1 for lc in lifecycle_data.values() if lc.get('status') == 'STALE'),
        'no_data': sum(1 for lc in lifecycle_data.values() if lc.get('status') == 'NO_DATA'),
        'error': sum(1 for lc in lifecycle_data.values() if lc.get('status') == 'ERROR'),
    }

    manifest = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "dataset_metadata": {
            "requested_start_date": start_date,
            "requested_end_date": end_date,
            "actual_start_date": actual_start,
            "actual_end_date": actual_end
        },
        "date_range": {
            "start": actual_start,
            "end": actual_end,
            "total_days": total_days
        },
        "instruments": {
            "included": {},
            "excluded": {}
        },
        "lifecycle": lifecycle_data,
        "lifecycle_summary": lifecycle_summary,
        "summary": {
            "total_candidates": len(instruments_included) + len(instruments_excluded),
            "included_count": len(instruments_included),
            "excluded_count": len(instruments_excluded),
            "exclusion_breakdown": {}
        }
    }

    # Populate included instruments
    for inst_id, metadata in instruments_included.items():
        manifest["instruments"]["included"][inst_id] = metadata

    # Populate excluded instruments
    for inst_id, reason in instruments_excluded.items():
        manifest["instruments"]["excluded"][inst_id] = {
            "reason": reason
        }

        # Count by reason
        manifest["summary"]["exclusion_breakdown"][reason] = \
            manifest["summary"]["exclusion_breakdown"].get(reason, 0) + 1

    # CRITICAL INVARIANT: manifest included set MUST equal dataset instruments
    manifest_included = set(manifest["instruments"]["included"].keys())

    if manifest_included != dataset_instruments:
        raise RuntimeError(
            f"Manifest consistency check failed: "
            f"included={sorted(manifest_included)} != dataset={sorted(dataset_instruments)}"
        )

    # Atomic write: write to temp file then replace
    # Ensures manifest always corresponds to the current dataset
    output_dir = output_path.parent
    output_dir.mkdir(parents=True, exist_ok=True)

    # Create temp file in same directory (ensures atomic replace on same filesystem)
    fd, temp_path = tempfile.mkstemp(dir=output_dir, suffix='.json', prefix='.manifest_tmp_')
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(manifest, f, indent=2)

        # Atomic replace (overwrites existing manifest atomically)
        # os.replace() provides stronger atomicity guarantees than os.rename()
        os.replace(temp_path, output_path)
        logger.info(f"Dataset manifest saved (atomic): {output_path}")
    except:
        # Clean up temp file on error
        try:
            os.unlink(temp_path)
        except:
            pass
        raise

    return manifest


def build_real_crypto_dataset(
    data_dir: Path,
    start_date: str,
    end_date: str,
    instruments: list = None,
    fail_on_missing_close: bool = False,
    min_coverage: float = 0.95,
    verify_checksums: bool = False,
    allow_jagged: bool = False,
    include_api_cache: bool = False,
    metadata_dir: Path = None,
    min_history_days: int = 365
) -> tuple[pd.DataFrame, dict, dict]:
    """
    Build dataset from real Binance Data Vision bulk files

    Args:
        data_dir: Path to data/raw directory
        start_date: Start date (YYYY-MM-DD)
        end_date: End date (YYYY-MM-DD)
        instruments: List of instrument IDs (default: all 5 Layer A instruments)
        fail_on_missing_close: If True, raise error if any rows with NaN close are dropped
        min_coverage: Minimum coverage ratio for common_dates intersection (default: 0.95)
        verify_checksums: If True, verify SHA256 checksums for ZIP files
        allow_jagged: If True, allow jagged panels (use date union instead of intersection)
        include_api_cache: If True, include API cache data (V1 daily operations)
        metadata_dir: Path to metadata directory. Precedence: explicit > {data_dir}/metadata > data/raw/metadata
        min_history_days: Minimum days of history required per instrument (default: 365)

    Returns:
        Tuple of (DataFrame, instruments_included, instruments_excluded)
        - DataFrame with schema: date, instrument, close, funding_rate, adv_notional, spread_frac, taker_fee_frac
        - instruments_included: {inst_id: metadata_dict} for included instruments
        - instruments_excluded: {inst_id: exclusion_reason} for excluded instruments

    Exclusion Taxonomy:
        - load_error: Failed to load klines or other required data
        - missing_funding: Failed to load funding rates
        - insufficient_history: coverage_days < min_history_days

    Policy:
        - Single-instrument builds (e.g. BTC-only) are expected to be complete
          and may use --fail-on-missing-close to enforce exact day counts.
        - Multi-instrument builds prioritize rectangular panel consistency
          and may drop dates via common_dates intersection (unless allow_jagged=True).
        - Jagged panels allow instruments with different date ranges (NaN for missing dates).
    """
    # Internal instrument IDs (match existing system config)
    if instruments is None:
        instruments = ['BTCUSDT_PERP', 'ETHUSDT_PERP', 'BNBUSDT_PERP', 'SOLUSDT_PERP', 'XRPUSDT_PERP']

    # Resolve metadata directory with precedence rules
    if metadata_dir is None:
        # Try {data_dir}/metadata first
        candidate1 = data_dir / 'metadata'
        if (candidate1 / 'binance_market_info.json').exists():
            metadata_dir = candidate1
        else:
            # Fall back to legacy location
            candidate2 = Path('data/raw/metadata')
            if (candidate2 / 'binance_market_info.json').exists():
                metadata_dir = candidate2
            else:
                raise FileNotFoundError(
                    f"Metadata not found. Tried: {candidate1}, {candidate2}. "
                    f"Use --metadata-dir to specify explicit location."
                )

    # Load metadata (spread and fee estimates)
    metadata_file = metadata_dir / 'binance_market_info.json'
    with open(metadata_file, 'r') as f:
        market_info = json.load(f)

    all_data = []
    instruments_included = {}  # Track included instruments with metadata
    instruments_excluded = {}  # Track excluded instruments with reasons

    for inst in instruments:
        # Map internal ID to Binance symbol
        binance_symbol = BINANCE_SYMBOL_MAP[inst]

        logger.info(f"Processing {inst} ({binance_symbol})...")

        # Load klines
        try:
            klines = load_binance_klines(inst, data_dir, fail_on_missing_close, include_api_cache)
        except FileNotFoundError as e:
            logger.error(f"Skipping {inst}: {e}")
            instruments_excluded[inst] = "load_error"
            continue
        except Exception as e:
            logger.error(f"Skipping {inst}: load error - {e}")
            instruments_excluded[inst] = "load_error"
            continue

        # Convert date strings to timestamps for type-safe comparison (ensure naive)
        start = pd.Timestamp(start_date).tz_localize(None)
        end = pd.Timestamp(end_date).tz_localize(None)

        # Filter klines to date range IMMEDIATELY (before merges)
        klines_filtered = klines[(klines['date'] >= start) & (klines['date'] <= end)].copy()

        # DEBUG: Log filtering results
        logger.info(f"{inst}: Klines loaded: {len(klines)} rows, date range {klines['date'].min()} to {klines['date'].max()}")
        logger.info(f"{inst}: Filter range: {start} to {end}")
        logger.info(f"{inst}: Klines filtered: {len(klines_filtered)} rows")

        # Sort by date to ensure monotonic order (CSV row order may vary)
        klines_filtered = klines_filtered.sort_values('date')
        original_row_count = len(klines_filtered)

        # Load funding rates (with correct alignment)
        try:
            funding = load_binance_funding_rates(inst, data_dir)
        except FileNotFoundError as e:
            logger.error(f"Skipping {inst}: {e}")
            instruments_excluded[inst] = "missing_funding"
            continue
        except Exception as e:
            logger.error(f"Skipping {inst}: funding load error - {e}")
            instruments_excluded[inst] = "missing_funding"
            continue

        # Calculate ADV on pre-aligned per-instrument data
        # After common_dates restriction, ADV values are treated as valid
        # even if early warmup days were excluded by intersection
        adv = calculate_adv(klines_filtered, window=30)

        # Validate join keys are unique (prevents row explosion)
        if funding['date'].duplicated().any():
            raise ValueError(f"{inst}: duplicate dates in funding")
        if adv['date'].duplicated().any():
            raise ValueError(f"{inst}: duplicate dates in adv")

        # Merge: klines defines the date set, funding is left-joined
        inst_df = klines_filtered.merge(adv, on='date', how='left')
        inst_df = inst_df.merge(funding, on='date', how='left')

        # CRITICAL: Validate no row explosion from merges
        if len(inst_df) != original_row_count:
            raise ValueError(
                f"{inst}: Row count changed during merge (expected {original_row_count}, got {len(inst_df)})"
            )

        # Sort after merges (merges can reorder rows)
        inst_df = inst_df.sort_values('date').reset_index(drop=True)

        # Add metadata
        inst_df['instrument'] = inst  # Use internal ID (BTCUSDT_PERP)
        inst_df['spread_frac'] = market_info[binance_symbol]['spread_frac']
        inst_df['taker_fee_frac'] = market_info[binance_symbol]['taker_fee_frac']

        # Handle missing funding: log count BEFORE fill, then fill with 0.0
        missing_funding_mask = inst_df['funding_rate'].isna()
        missing_funding_count = missing_funding_mask.sum()

        # DEBUG: Log merge details
        logger.info(f"{inst}: inst_df shape={inst_df.shape}, missing_funding={missing_funding_count}/{len(inst_df)}")
        logger.info(f"{inst}: inst_df date dtype={inst_df['date'].dtype}, funding date dtype={funding['date'].dtype}")
        logger.info(f"{inst}: inst_df date range: {inst_df['date'].min()} to {inst_df['date'].max()}")
        logger.info(f"{inst}: funding date range: {funding['date'].min()} to {funding['date'].max()}")

        # Sanity check: if ALL funding is missing, likely join key mismatch
        if missing_funding_count == len(inst_df):
            raise ValueError(
                f"{inst}: funding_rate missing for ALL dates (likely date key mismatch). "
                f"Check that funding['date'] dtype matches klines['date']."
            )

        if missing_funding_count > 0:
            logger.warning(
                f"{inst}: {missing_funding_count}/{len(inst_df)} days missing funding rates (filling with 0.0)"
            )
        inst_df['funding_rate'] = inst_df['funding_rate'].fillna(0.0)

        # Validate no NaN after fill
        if inst_df['funding_rate'].isna().any():
            raise ValueError(f"{inst}: NaN in funding_rate after fill")

        # Validate this instrument's data before adding to all_data
        if inst_df['close'].isna().any():
            raise ValueError(
                f"{inst}: NaN in close after processing (should have been dropped in load_binance_klines)"
            )
        if inst_df['date'].duplicated().any():
            raise ValueError(f"{inst}: Duplicate dates after merges")
        if not inst_df['date'].is_monotonic_increasing:
            raise ValueError(f"{inst}: Dates not monotonic after merges")

        # Run time series quality validation
        issues = validate_time_series_quality(inst_df, inst)
        for issue in issues:
            logger.warning(issue)

        # Track inclusion metadata
        # Compute funding coverage (% of days with funding data present)
        funding_present = (~missing_funding_mask).sum()
        funding_coverage_pct = funding_present / len(inst_df) if len(inst_df) > 0 else 0.0

        # Requested date range vs actual coverage
        start_ts = pd.Timestamp(start_date).tz_localize(None)
        end_ts = pd.Timestamp(end_date).tz_localize(None)
        requested_days = (end_ts - start_ts).days + 1
        coverage_days = len(inst_df)

        # Gate on minimum history requirement
        if coverage_days < min_history_days:
            logger.warning(
                f"{inst}: Insufficient history - {coverage_days} days < {min_history_days} days (min_history_days)"
            )
            instruments_excluded[inst] = "insufficient_history"
            continue

        instruments_included[inst] = {
            "date_range": {
                "start": inst_df['date'].min().strftime('%Y-%m-%d'),
                "end": inst_df['date'].max().strftime('%Y-%m-%d')
            },
            "coverage_days": coverage_days,
            "coverage_pct": coverage_days / requested_days,
            "funding_coverage_pct": funding_coverage_pct,
            "schema_compliant": True  # Passed validation
        }

        all_data.append(inst_df)

    if not all_data:
        raise ValueError("No data loaded for any instrument. Check data/raw/ directory.")

    # Step 1: Compute common dates (intersection for rectangular, union for jagged)
    if allow_jagged:
        logger.info("Computing date union across all instruments (jagged panel mode)...")
    else:
        logger.info("Computing common date intersection across all instruments...")

    # Defensive check: ensure at least one instrument produced data
    if not all_data:
        raise ValueError("No instruments produced data; check --instruments and input files.")

    date_sets = {}
    for inst_df in all_data:
        instrument = inst_df['instrument'].iloc[0]
        date_sets[instrument] = set(inst_df['date'])

    if allow_jagged:
        # Union of all date sets (jagged panel)
        common_dates_set = set.union(*date_sets.values())
    else:
        # Intersection of all date sets (rectangular panel)
        common_dates_set = set.intersection(*date_sets.values())

        # Fail if intersection is empty (mismatched ranges or no overlap)
        if not common_dates_set:
            date_ranges = {inst: (sorted(dates)[0], sorted(dates)[-1]) for inst, dates in date_sets.items()}
            raise ValueError(
                f"common_dates intersection is empty (no overlapping dates). "
                f"Date ranges per instrument: {date_ranges}"
            )

    # Sort common_dates once for deterministic behavior
    common_dates = sorted(common_dates_set)

    # Calculate coverage
    # expected_days is calendar-day coverage over the requested range.
    # Crypto trades 7 days/week; expected_days is calendar days, not trading days.
    # For instruments with later launch dates (e.g. SOL), min_coverage may need
    # to be relaxed (e.g. 0.80), or start_date adjusted accordingly.
    start = pd.Timestamp(start_date).tz_localize(None)
    end = pd.Timestamp(end_date).tz_localize(None)
    expected_days = (end - start).days + 1
    coverage_ratio = len(common_dates) / expected_days

    logger.info(
        f"Common dates: {len(common_dates)}/{expected_days} days ({coverage_ratio:.1%} coverage)"
    )

    # Validate coverage meets minimum threshold
    if allow_jagged:
        # For jagged panels, check per-instrument coverage over their active window
        logger.info("Checking per-instrument coverage for jagged panel...")
        for instrument, dates in date_sets.items():
            # Calculate expected days based on instrument's actual data range (lifecycle window)
            sorted_dates = sorted(dates)
            if len(sorted_dates) > 0:
                inst_start = sorted_dates[0]
                inst_end = sorted_dates[-1]
                inst_expected_days = (inst_end - inst_start).days + 1
                inst_coverage = len(dates) / inst_expected_days

                if inst_coverage < min_coverage:
                    logger.warning(
                        f"{instrument}: Coverage {inst_coverage:.1%} < min_coverage={min_coverage:.1%} "
                        f"({len(dates)}/{inst_expected_days} days over active window {inst_start.date()} to {inst_end.date()}). "
                        f"May have data gaps."
                    )
                else:
                    logger.info(
                        f"{instrument}: Coverage {inst_coverage:.1%} "
                        f"({len(dates)}/{inst_expected_days} days over {inst_start.date()} to {inst_end.date()})"
                    )
        # Note: For jagged panels, global coverage check is not meaningful (union is always ~100%)
    else:
        # For rectangular panels, check global coverage (intersection)
        if coverage_ratio < min_coverage:
            raise ValueError(
                f"Insufficient coverage: {len(common_dates)}/{expected_days} days ({coverage_ratio:.1%}) "
                f"< min_coverage={min_coverage:.1%}. Check for partial downloads or data gaps."
            )

    for instrument, dates in date_sets.items():
        excluded_count = len(dates) - len(common_dates_set)
        if excluded_count > 0:
            excluded_dates = sorted(dates - common_dates_set)
            logger.warning(
                f"{instrument}: {excluded_count} dates excluded from common set. "
                f"Sample: {excluded_dates[:3]}"
            )

    # Step 2: Align each instrument to common_dates
    aligned_data = []
    for inst_df in all_data:
        instrument = inst_df['instrument'].iloc[0]
        inst_aligned = inst_df[inst_df['date'].isin(common_dates)].copy()

        # Ensure monotonic ordering independent of earlier operations
        inst_aligned = inst_aligned.sort_values('date')

        if allow_jagged:
            # For jagged panels, fill missing dates with NaN
            # Create full date range DataFrame
            full_dates_df = pd.DataFrame({'date': sorted(common_dates)})
            # Merge with actual data (left join to preserve all dates)
            inst_aligned = full_dates_df.merge(inst_aligned, on='date', how='left')
            # Fill instrument column for all rows
            inst_aligned['instrument'] = instrument
        else:
            # Rectangular panel: validate exact match
            if len(inst_aligned) != len(common_dates):
                raise ValueError(
                    f"{instrument}: After alignment, expected {len(common_dates)} rows, got {len(inst_aligned)}"
                )

        aligned_data.append(inst_aligned)

    # Step 3: Concatenate aligned data
    df = pd.concat(aligned_data, ignore_index=True)

    if allow_jagged:
        # Jagged panel validation
        logger.info("Validating jagged panel...")
        instruments_list = df['instrument'].unique()

        # Validate per-instrument: monotonic unique dates
        for instrument in instruments_list:
            inst_df = df[df['instrument'] == instrument]
            if inst_df['date'].duplicated().any():
                raise ValueError(f"{instrument}: Duplicate dates in jagged panel")
            if not inst_df['date'].is_monotonic_increasing:
                raise ValueError(f"{instrument}: Dates not monotonic in jagged panel")

        # Log NaN summary
        nan_counts = df.groupby('instrument')['close'].apply(lambda x: x.isna().sum())
        non_nan_instruments = nan_counts[nan_counts > 0]
        if len(non_nan_instruments) > 0:
            logger.info(f"Jagged panel: NaN close prices per instrument:\n{non_nan_instruments}")

        logger.info(f"✓ Jagged panel validated: {len(instruments_list)} instruments with varying date coverage")
    else:
        # CRITICAL: Validate rectangular panel (no NaN after pivot)
        logger.info("Validating rectangular panel...")

        # Check no NaN in close (should be impossible after alignment + NaN drops)
        if df['close'].isna().any():
            nan_summary = df[df['close'].isna()].groupby('instrument').size()
            raise ValueError(f"NaN in close prices (should not happen):\n{nan_summary}")

        # Validate per-instrument: same date count
        instruments_list = df['instrument'].unique()
        date_counts = df.groupby('instrument')['date'].nunique()
        if not (date_counts == len(common_dates)).all():
            raise ValueError(f"Instruments have different date counts:\n{date_counts}")

        # Validate per-instrument: monotonic unique dates
        for instrument in instruments_list:
            inst_df = df[df['instrument'] == instrument]
            if inst_df['date'].duplicated().any():
                raise ValueError(f"{instrument}: Duplicate dates in final parquet")
            if not inst_df['date'].is_monotonic_increasing:
                raise ValueError(f"{instrument}: Dates not monotonic in final parquet")

        logger.info(f"✓ Rectangular panel validated: {len(instruments_list)} instruments × {len(common_dates)} dates")

    # Create pivot for downstream validation and regime reporting
    prices_df = df.pivot(index='date', columns='instrument', values='close')

    # Final NaN check: replicate exact adapter validation (only for rectangular panels)
    if not allow_jagged:
        logger.info("Final pivot NaN check (replicating adapter validation)...")
        if prices_df.isna().any().any():
            nan_summary = prices_df.isna().sum()
            nan_instruments = nan_summary[nan_summary > 0]
            raise ValueError(
                f"NaN produced by pivot (rectangular panel violated):\n{nan_instruments}"
            )
    else:
        logger.info("Skipping final pivot NaN check (jagged panel allows NaN for dates before launch)")

    # Report regime coverage (descriptive validation)
    regime_stats = report_regime_coverage(prices_df)

    # Select and order columns to match schema
    df = df[['date', 'instrument', 'close', 'funding_rate', 'adv_notional', 'spread_frac', 'taker_fee_frac']]

    logger.info(f"Dataset built successfully: {len(df)} rows, {df['instrument'].nunique()} instruments")

    return df, instruments_included, instruments_excluded


def generate_synthetic_crypto_data(
    instruments: list,
    start_date: str = "2023-01-01",
    end_date: str = "2024-12-31",
    seed: int = 42
) -> pd.DataFrame:
    """
    Generate synthetic crypto perpetual futures data for testing

    Args:
        instruments: List of instrument codes
        start_date: Start date for data
        end_date: End date for data
        seed: Random seed for reproducibility

    Returns:
        DataFrame with all required fields in long format
    """
    np.random.seed(seed)

    # Generate daily date range (UTC)
    dates = pd.date_range(start=start_date, end=end_date, freq='D', tz='UTC')
    dates = dates.tz_localize(None)  # Remove timezone for simplicity

    # Initial prices for each instrument (approximate realistic values)
    initial_prices = {
        'BTCUSDT_PERP': 20000.0,
        'ETHUSDT_PERP': 1500.0,
        'BNBUSDT_PERP': 300.0,
        'SOLUSDT_PERP': 20.0,
        'XRPUSDT_PERP': 0.4
    }

    # Generate data for each instrument
    all_data = []

    for inst in instruments:
        n_days = len(dates)
        initial_price = initial_prices.get(inst, 100.0)

        # Generate realistic daily returns (crypto-like volatility)
        # Annual vol ~80%, daily vol ~5%
        daily_vol = 0.05
        daily_returns = np.random.normal(0.0001, daily_vol, n_days)  # Slight upward drift

        # Generate price series
        log_prices = np.cumsum(daily_returns)
        prices = initial_price * np.exp(log_prices)

        # Generate funding rates (typically small, mean-reverting around 0.01% per 8h)
        # Daily funding = 3x 8-hour funding periods
        # Typical range: -0.05% to +0.15% per day (annualized ~-20% to +50%)
        funding_mean = 0.0001  # 0.01% per day
        funding_vol = 0.0005   # Small volatility
        funding_rates = np.random.normal(funding_mean, funding_vol, n_days)

        # Generate ADV (average daily volume in notional)
        # Larger for BTC/ETH, smaller for others
        base_adv = {
            'BTCUSDT_PERP': 1e10,  # $10B
            'ETHUSDT_PERP': 5e9,   # $5B
            'BNBUSDT_PERP': 1e9,   # $1B
            'SOLUSDT_PERP': 5e8,   # $500M
            'XRPUSDT_PERP': 3e8    # $300M
        }
        mean_adv = base_adv.get(inst, 1e8)
        # Add some variation (±30%)
        adv_notional = mean_adv * (1 + np.random.uniform(-0.3, 0.3, n_days))

        # Fixed cost parameters for Phase 1 (placeholders)
        spread_frac = np.full(n_days, 0.0003)  # 3 bps
        taker_fee_frac = np.full(n_days, 0.0004)  # 4 bps (typical Binance taker fee)

        # Create DataFrame for this instrument
        inst_df = pd.DataFrame({
            'date': dates,
            'instrument': inst,
            'close': prices,
            'funding_rate': funding_rates,
            'adv_notional': adv_notional,
            'spread_frac': spread_frac,
            'taker_fee_frac': taker_fee_frac
        })

        all_data.append(inst_df)

    # Concatenate all instruments
    df = pd.concat(all_data, ignore_index=True)

    return df


def main():
    """
    Build crypto perpetual futures dataset from synthetic or real data
    """
    import argparse

    parser = argparse.ArgumentParser(
        description='Build crypto perpetual futures dataset from synthetic or real data'
    )

    # Data source selection
    parser.add_argument(
        '--source',
        choices=['synthetic', 'real'],
        default='synthetic',
        help='Data source: synthetic (generated) or real (Binance Data Vision)'
    )
    parser.add_argument(
        '--start-date',
        default=None,
        help='Start date for data (YYYY-MM-DD). Overrides --start-year if both provided.'
    )
    parser.add_argument(
        '--end-date',
        default=None,
        help='End date for data (YYYY-MM-DD). Overrides --end-year if both provided.'
    )
    parser.add_argument(
        '--start-year',
        type=int,
        default=2023,
        help='Start year for data (convenience wrapper, uses YYYY-01-01). Default: 2023'
    )
    parser.add_argument(
        '--end-year',
        type=int,
        default=2024,
        help='End year for data (convenience wrapper, uses YYYY-12-31). Default: 2024'
    )
    parser.add_argument(
        '--output-path',
        default=None,
        help='Output path for parquet file (default: data/example_crypto_perps.parquet). '
             'Use this to create dataset variants (e.g., data/example_crypto_perps_5yr.parquet)'
    )
    parser.add_argument(
        '--data-dir',
        default='data/raw',
        help='Root directory for raw data files'
    )
    parser.add_argument(
        '--metadata-dir',
        type=Path,
        default=None,
        help='Metadata directory. Precedence: explicit > {data-dir}/metadata > data/raw/metadata'
    )
    parser.add_argument(
        '--instruments',
        nargs='+',
        default=None,
        help='Instrument list (default: all 5 Layer A instruments if not specified)'
    )
    parser.add_argument(
        '--fail-on-missing-close',
        action='store_true',
        help='Raise error if any rows with NaN close are dropped (default: log warning only)'
    )
    parser.add_argument(
        '--min-coverage',
        type=float,
        default=0.95,
        help='Minimum coverage ratio for common_dates intersection (default: 0.95). '
             'Fails if len(common_dates) < expected_days * min_coverage. '
             'NOTE: Applies to the INTERSECTION across all instruments. '
             'For multi-instrument scaling, may need to relax to 0.80 if instruments have different launch dates.'
    )
    parser.add_argument(
        '--min-history-days',
        type=int,
        default=365,
        help='Minimum days of history required per instrument (default: 365). '
             'Instruments with fewer days will be excluded with reason "insufficient_history".'
    )
    parser.add_argument(
        '--allow-jagged',
        action='store_true',
        help='Allow instruments to have different date ranges (jagged panel). '
             'Uses date UNION instead of intersection. NaN prices allowed for dates outside instrument lifecycle.'
    )
    parser.add_argument(
        '--include-api-cache',
        action='store_true',
        help='Include API cache data (for V1 daily operations). '
             'Merges Vision ZIPs + API cache with deduplication (Vision > API cache for duplicates).'
    )

    # Optional checksum verification
    parser.add_argument(
        '--verify-checksums',
        action='store_true',
        help='Verify SHA256 checksums for all ZIP files (requires .CHECKSUM files)'
    )

    # Alignment inspection mode (mutually exclusive with normal build)
    parser.add_argument(
        '--inspect-alignment',
        action='store_true',
        help='Run alignment inspection helper (requires --klines and --funding)'
    )
    parser.add_argument(
        '--klines',
        type=str,
        help='Path to sample klines ZIP file for alignment inspection'
    )
    parser.add_argument(
        '--funding',
        type=str,
        help='Path to sample funding ZIP file for alignment inspection'
    )
    parser.add_argument(
        '--sample-days',
        type=int,
        default=3,
        help='Number of days to sample for alignment inspection'
    )

    args = parser.parse_args()

    # Handle alignment inspection mode
    if args.inspect_alignment:
        if not args.klines or not args.funding:
            parser.error("--inspect-alignment requires --klines and --funding arguments")
        inspect_alignment(
            klines_path=Path(args.klines),
            funding_path=Path(args.funding),
            sample_days=args.sample_days
        )
        return  # Exit after inspection

    # Derive start/end dates from years if explicit dates not provided
    if args.start_date is None:
        start_date = f"{args.start_year}-01-01"
    else:
        start_date = args.start_date

    if args.end_date is None:
        end_date = f"{args.end_year}-12-31"
    else:
        end_date = args.end_date

    # Determine output path
    if args.output_path is None:
        output_path = Path(__file__).parent.parent / 'data' / 'example_crypto_perps.parquet'
        print("=" * 80)
        print("WARNING: Using default output path (for backward compatibility):")
        print(f"  {output_path}")
        print("RECOMMENDED: Specify explicit --output-path for production use")
        print("=" * 80)
        print()
    else:
        output_path = Path(args.output_path)
        # Ensure absolute path
        if not output_path.is_absolute():
            output_path = Path(__file__).parent.parent / output_path

    # Normal build mode
    if args.source == 'synthetic':
        # Define Layer A instruments (top 5 by ADV for Phase 1)
        instruments = [
            'BTCUSDT_PERP',
            'ETHUSDT_PERP',
            'BNBUSDT_PERP',
            'SOLUSDT_PERP',
            'XRPUSDT_PERP'
        ]

        print("Generating synthetic crypto perpetual futures data...")
        df = generate_synthetic_crypto_data(
            instruments,
            start_date=start_date,
            end_date=end_date
        )

    elif args.source == 'real':
        print("Building dataset from real Binance Data Vision files...")
        df, instruments_included, instruments_excluded = build_real_crypto_dataset(
            data_dir=Path(args.data_dir),
            start_date=start_date,
            end_date=end_date,
            instruments=args.instruments,
            fail_on_missing_close=args.fail_on_missing_close,
            min_coverage=args.min_coverage,
            verify_checksums=args.verify_checksums,
            allow_jagged=args.allow_jagged,
            include_api_cache=args.include_api_cache,
            metadata_dir=args.metadata_dir,
            min_history_days=args.min_history_days
        )

        # Generate manifest with deterministic naming: X.parquet → X.manifest.json
        # Manifest is tied to dataset, not just today's date
        manifest_path = output_path.with_suffix('.manifest.json')
        print(f"Generating manifest: {manifest_path}...")
        manifest = generate_dataset_manifest(
            dataset_df=df,
            instruments_included=instruments_included,
            instruments_excluded=instruments_excluded,
            start_date=start_date,
            end_date=end_date,
            output_path=manifest_path
        )

        # Report exclusions
        if instruments_excluded:
            print(f"\nExcluded {len(instruments_excluded)} instrument(s):")
            for inst_id, reason in sorted(instruments_excluded.items()):
                print(f"  {inst_id}: {reason}")

    # Save to parquet
    output_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"\nSaving to {output_path}...")
    df.to_parquet(output_path, index=False)

    print(f"\nDataset created successfully!")
    print(f"  Date range: {df['date'].min()} to {df['date'].max()}")
    print(f"  Instruments: {df['instrument'].unique().tolist()}")
    print(f"  Total rows: {len(df)}")
    if args.source == 'synthetic':
        instruments = df['instrument'].unique()
        print(f"  Rows per instrument: {len(df) // len(instruments)}")


if __name__ == '__main__':
    main()
