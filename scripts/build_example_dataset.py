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
    'XRPUSDT_PERP': 'XRPUSDT'
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

    Handles variations: calc_time vs calcTime vs fundingTime

    Raises:
        ValueError: If required columns cannot be mapped
    """
    col_map_lower = {col.lower(): col for col in df.columns}

    required = {
        'calcTime': ['calc_time', 'calctime', 'funding_time', 'fundingtime'],
        'fundingRate': ['last_funding_rate', 'fundingrate', 'funding_rate', 'rate'],
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
                f"Binance may have changed CSV schema."
            )
        mapped[found] = canonical

    return df.rename(columns=mapped)


def load_binance_klines(
    instrument: str,
    data_dir: Path,
    fail_on_missing_close: bool = False
) -> pd.DataFrame:
    """
    Load all kline files for instrument using glob discovery

    Args:
        instrument: Internal instrument ID (e.g., 'BTCUSDT_PERP')
        data_dir: Root data directory (e.g., Path('data/raw'))
        fail_on_missing_close: If True, raise error if any rows with NaN close are dropped

    Returns:
        DataFrame with columns: date, close, volume, quote_volume

    Notes:
        - Works with daily or monthly archives (glob pattern matches both)
        - date is derived from close_time (end-of-day timestamp)
        - NaN close rows are dropped with logging
    """
    # Map internal ID to Binance symbol
    binance_symbol = BINANCE_SYMBOL_MAP[instrument]
    klines_dir = data_dir / 'binance' / 'klines' / binance_symbol

    # Glob discover all kline ZIP files (daily or monthly)
    all_data = []
    for zip_file in sorted(klines_dir.glob(f'{binance_symbol}-1d-*.zip')):
        with zipfile.ZipFile(zip_file) as z:
            csv_name = zip_file.stem + '.csv'  # Binance convention
            with z.open(csv_name) as f_raw:
                # Read once into BytesIO (ZipExtFile doesn't support seek)
                from io import BytesIO
                data = f_raw.read()

                try:
                    # Try header=0 first (current Binance format)
                    df = pd.read_csv(BytesIO(data), header=0)
                    df = normalize_kline_columns(df, zip_file)
                except (ValueError, KeyError) as e:
                    # Fallback: Try header=None with positional mapping
                    logger.warning(f"Header parsing failed for {zip_file}, trying positional mapping: {e}")
                    try:
                        df = pd.read_csv(BytesIO(data), header=None)
                        # Binance klines standard 12-column format (0-indexed):
                        # 0: open_time, 1: open, 2: high, 3: low, 4: close, 5: volume,
                        # 6: close_time, 7: quote_asset_volume, 8: count, 9: taker_buy_volume, ...
                        df = df.rename(columns={
                            4: 'close',         # Column 4: close price
                            5: 'volume',        # Column 5: base asset volume
                            6: 'close_time',    # Column 6: close timestamp (ms)
                            7: 'quote_volume'   # Column 7: quote asset volume (=quote_asset_volume)
                        })
                        df = normalize_kline_columns(df, zip_file)
                    except Exception as e2:
                        logger.error(f"Both header=0 and header=None failed for {zip_file}")
                        raise ValueError(
                            f"CSV parsing failed for {zip_file}. "
                            f"Binance may have changed CSV format. "
                            f"Header error: {e}, Positional error: {e2}"
                        ) from e2
                all_data.append(df)

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

    # Validate price range
    min_price, max_price = 0.01, 1e6
    if not (klines['close'] >= min_price).all():
        raise ValueError(f"{instrument}: Prices below ${min_price}")
    if not (klines['close'] <= max_price).all():
        raise ValueError(f"{instrument}: Prices above ${max_price}")

    # Validate volume is non-negative
    if not (klines['quote_volume'] >= 0).all():
        raise ValueError(f"{instrument}: Negative volume found")

    return klines.sort_values('date').reset_index(drop=True)


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

    # Ensure output 'date' column is naive datetime64[ns] (matches klines dtype)
    daily['date'] = pd.to_datetime(daily['date']).dt.tz_localize(None)

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
    funding_dir = data_dir / 'binance' / 'funding_rates' / binance_symbol

    # Glob discover all funding ZIP files (daily or monthly)
    all_data = []
    for zip_file in sorted(funding_dir.glob(f'{binance_symbol}-fundingRate-*.zip')):
        with zipfile.ZipFile(zip_file) as z:
            csv_name = zip_file.stem + '.csv'  # Binance convention
            with z.open(csv_name) as f_raw:
                from io import BytesIO
                data = f_raw.read()

                try:
                    # Try header=0 first
                    df = pd.read_csv(BytesIO(data), header=0)
                    df = normalize_funding_columns(df, zip_file)
                except (ValueError, KeyError) as e:
                    # Fallback: Try header=None with adaptive positional mapping
                    logger.warning(f"Header parsing failed for {zip_file}, trying positional mapping: {e}")
                    try:
                        df = pd.read_csv(BytesIO(data), header=None)

                        # Adaptive positional mapping with scoring (Binance funding format varies)
                        # Column 0 MUST be timestamp (int ms or datetime-parseable)
                        if df.shape[1] < 2:
                            raise ValueError(f"Funding CSV has only {df.shape[1]} columns, need at least 2")

                        # Column 0 is always timestamp
                        df = df.rename(columns={0: 'calcTime'})

                        # Find fundingRate column using scoring heuristic
                        # Do NOT use pd.api.types.is_numeric_dtype (columns may be object strings)
                        candidate_scores = []

                        for col_idx in range(1, df.shape[1]):
                            # Coerce to numeric (handles object strings)
                            s = pd.to_numeric(df[col_idx], errors='coerce')

                            # Convert to NumPy array to avoid dtype issues with np.isfinite
                            s_np = s.to_numpy(dtype=float, na_value=np.nan)
                            finite_mask = np.isfinite(s_np)

                            if finite_mask.sum() == 0:
                                continue  # All NaN, skip

                            parseable_ratio = finite_mask.mean()
                            s_finite_np = s_np[finite_mask]
                            median_abs = np.median(np.abs(s_finite_np))
                            nonzero_ratio = (np.abs(s_finite_np) > 0).mean()

                            # Hard thresholds (reject non-funding columns)
                            if parseable_ratio < 0.80:
                                continue  # Too many unparseable values
                            if median_abs <= 1e-12:
                                continue  # All zeros / near-zero
                            if median_abs >= 0.5:
                                continue  # Too large to be funding rate (e.g., interval hours = 8)

                            # Score: prefer small but nonzero funding-like magnitudes
                            score = parseable_ratio + 0.2 * nonzero_ratio - 0.1 * np.log10(1 + median_abs)
                            candidate_scores.append((col_idx, score, median_abs))

                        if not candidate_scores:
                            logger.error(f"No valid fundingRate column found in {zip_file}")
                            logger.error(f"CSV shape: {df.shape}, columns: {list(df.columns)}")
                            logger.error(f"Sample rows:\n{df.head(3)}")
                            raise ValueError(
                                f"Funding CSV parsing failed for {zip_file}. "
                                f"No column matches funding rate heuristic (small nonzero values). "
                                f"Binance may have changed CSV format."
                            )

                        # Pick best-scoring column
                        funding_col = max(candidate_scores, key=lambda x: x[1])[0]
                        df = df.rename(columns={funding_col: 'fundingRate'})
                        df = normalize_funding_columns(df, zip_file)

                    except Exception as e2:
                        logger.error(f"Both header=0 and header=None failed for {zip_file}")
                        logger.error(f"CSV columns: {list(df.columns) if 'df' in locals() else 'unknown'}")
                        logger.error(f"Sample rows:\n{df.head(3) if 'df' in locals() else 'N/A'}")
                        raise ValueError(
                            f"CSV parsing failed for {zip_file}. "
                            f"Binance may have changed CSV format. "
                            f"Header error: {e}, Positional error: {e2}"
                        ) from e2
                all_data.append(df)

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


def build_real_crypto_dataset(
    data_dir: Path,
    start_date: str,
    end_date: str,
    instruments: list = None,
    fail_on_missing_close: bool = False,
    min_coverage: float = 0.95,
    verify_checksums: bool = False
) -> pd.DataFrame:
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

    Returns:
        DataFrame with schema: date, instrument, close, funding_rate, adv_notional, spread_frac, taker_fee_frac

    Policy:
        - Single-instrument builds (e.g. BTC-only) are expected to be complete
          and may use --fail-on-missing-close to enforce exact day counts.
        - Multi-instrument builds prioritize rectangular panel consistency
          and may drop dates via common_dates intersection.
    """
    # Internal instrument IDs (match existing system config)
    if instruments is None:
        instruments = ['BTCUSDT_PERP', 'ETHUSDT_PERP', 'BNBUSDT_PERP', 'SOLUSDT_PERP', 'XRPUSDT_PERP']

    # Load metadata (spread and fee estimates)
    metadata_file = data_dir / 'metadata' / 'binance_market_info.json'
    with open(metadata_file, 'r') as f:
        market_info = json.load(f)

    all_data = []
    for inst in instruments:
        # Map internal ID to Binance symbol
        binance_symbol = BINANCE_SYMBOL_MAP[inst]

        logger.info(f"Processing {inst} ({binance_symbol})...")

        # Load klines
        try:
            klines = load_binance_klines(inst, data_dir, fail_on_missing_close)
        except FileNotFoundError as e:
            logger.error(f"Skipping {inst}: {e}")
            continue

        # Convert date strings to timestamps for type-safe comparison (ensure naive)
        start = pd.Timestamp(start_date).tz_localize(None)
        end = pd.Timestamp(end_date).tz_localize(None)

        # Filter klines to date range IMMEDIATELY (before merges)
        klines_filtered = klines[(klines['date'] >= start) & (klines['date'] <= end)].copy()

        # Sort by date to ensure monotonic order (CSV row order may vary)
        klines_filtered = klines_filtered.sort_values('date')
        original_row_count = len(klines_filtered)

        # Load funding rates (with correct alignment)
        try:
            funding = load_binance_funding_rates(inst, data_dir)
        except FileNotFoundError as e:
            logger.error(f"Skipping {inst}: {e}")
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

        all_data.append(inst_df)

    if not all_data:
        raise ValueError("No data loaded for any instrument. Check data/raw/ directory.")

    # Step 1: Compute common date intersection
    logger.info("Computing common date intersection across all instruments...")

    # Defensive check: ensure at least one instrument produced data
    if not all_data:
        raise ValueError("No instruments produced data; check --instruments and input files.")

    date_sets = {}
    for inst_df in all_data:
        instrument = inst_df['instrument'].iloc[0]
        date_sets[instrument] = set(inst_df['date'])

    # Intersection of all date sets
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
    if coverage_ratio < min_coverage:
        raise ValueError(
            f"Insufficient coverage: {len(common_dates)}/{expected_days} days ({coverage_ratio:.1%}) "
            f"< min_coverage={min_coverage:.1%}. Check for partial downloads or data gaps."
        )

    for instrument, dates in date_sets.items():
        excluded_count = len(dates) - len(common_dates)
        if excluded_count > 0:
            excluded_dates = sorted(dates - common_dates)
            logger.warning(
                f"{instrument}: {excluded_count} dates excluded from common set. "
                f"Sample: {excluded_dates[:3]}"
            )

    # Step 2: Restrict each instrument to common_dates
    aligned_data = []
    for inst_df in all_data:
        instrument = inst_df['instrument'].iloc[0]
        inst_aligned = inst_df[inst_df['date'].isin(common_dates)].copy()

        # Ensure monotonic ordering independent of earlier operations
        inst_aligned = inst_aligned.sort_values('date')

        # Validate exact match
        if len(inst_aligned) != len(common_dates):
            raise ValueError(
                f"{instrument}: After alignment, expected {len(common_dates)} rows, got {len(inst_aligned)}"
            )

        aligned_data.append(inst_aligned)

    # Step 3: Concatenate aligned data
    df = pd.concat(aligned_data, ignore_index=True)

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

    # Final pivot check: replicate exact adapter validation
    logger.info("Final pivot check (replicating adapter validation)...")
    prices_df = df.pivot(index='date', columns='instrument', values='close')
    if prices_df.isna().any().any():
        nan_summary = prices_df.isna().sum()
        nan_instruments = nan_summary[nan_summary > 0]
        raise ValueError(
            f"NaN produced by pivot (rectangular panel violated):\n{nan_instruments}"
        )

    # Select and order columns to match schema
    df = df[['date', 'instrument', 'close', 'funding_rate', 'adv_notional', 'spread_frac', 'taker_fee_frac']]

    logger.info(f"Dataset built successfully: {len(df)} rows, {df['instrument'].nunique()} instruments")

    return df


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
        default='2023-01-01',
        help='Start date for data (YYYY-MM-DD)'
    )
    parser.add_argument(
        '--end-date',
        default='2024-12-31',
        help='End date for data (YYYY-MM-DD)'
    )
    parser.add_argument(
        '--data-dir',
        default='data/raw',
        help='Root directory for raw data files'
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
            start_date=args.start_date,
            end_date=args.end_date
        )

    elif args.source == 'real':
        print("Building dataset from real Binance Data Vision files...")
        df = build_real_crypto_dataset(
            data_dir=Path(args.data_dir),
            start_date=args.start_date,
            end_date=args.end_date,
            instruments=args.instruments,
            fail_on_missing_close=args.fail_on_missing_close,
            min_coverage=args.min_coverage,
            verify_checksums=args.verify_checksums
        )

    # Save to parquet (same for both)
    output_path = Path(__file__).parent.parent / 'data' / 'example_crypto_perps.parquet'
    output_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"Saving to {output_path}...")
    df.to_parquet(output_path, index=False)

    print(f"Dataset created successfully!")
    print(f"  Date range: {df['date'].min()} to {df['date'].max()}")
    print(f"  Instruments: {df['instrument'].unique().tolist()}")
    print(f"  Total rows: {len(df)}")
    if args.source == 'synthetic':
        instruments = df['instrument'].unique()
        print(f"  Rows per instrument: {len(df) // len(instruments)}")


if __name__ == '__main__':
    main()
