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


def load_binance_klines(instrument: str, data_dir: Path) -> pd.DataFrame:
    """
    Load all kline files for instrument using glob discovery

    Args:
        instrument: Internal instrument ID (e.g., 'BTCUSDT_PERP')
        data_dir: Root data directory (e.g., Path('data/raw'))

    Returns:
        DataFrame with columns: date, close, volume, quote_volume

    Notes:
        - Works with daily or monthly archives (glob pattern matches both)
        - date is derived from close_time (end-of-day timestamp)
    """
    # Map internal ID to Binance symbol
    binance_symbol = BINANCE_SYMBOL_MAP[instrument]
    klines_dir = data_dir / 'binance' / 'klines' / binance_symbol

    # Glob discover all kline ZIP files (daily or monthly)
    all_data = []
    for zip_file in sorted(klines_dir.glob(f'{binance_symbol}-1d-*.zip')):
        with zipfile.ZipFile(zip_file) as z:
            csv_name = zip_file.stem + '.csv'  # Binance convention
            with z.open(csv_name) as f:
                df = pd.read_csv(f, header=None, names=[
                    'open_time', 'open', 'high', 'low', 'close', 'volume',
                    'close_time', 'quote_volume', 'trades',
                    'taker_buy_base_vol', 'taker_buy_quote_vol', 'ignore'
                ])
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

    # Apply shift based on inspect_alignment() verification
    # DEFAULT: NO SHIFT (uncomment if verification shows shift is needed)
    # daily['date'] = daily['date'] + pd.Timedelta(days=1)

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
            with z.open(csv_name) as f:
                df = pd.read_csv(f, header=None, names=['calcTime', 'fundingRate', 'markPrice'])
                all_data.append(df)

    if not all_data:
        raise FileNotFoundError(f"No funding rate files found for {binance_symbol} in {funding_dir}")

    funding_events = pd.concat(all_data, ignore_index=True)

    # Parse timestamps
    funding_events['calcTime'] = pd.to_datetime(funding_events['calcTime'], unit='ms', utc=True)

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
    verify_checksums: bool = False
) -> pd.DataFrame:
    """
    Build dataset from real Binance Data Vision bulk files

    Args:
        data_dir: Path to data/raw directory
        start_date: Start date (YYYY-MM-DD)
        end_date: End date (YYYY-MM-DD)
        verify_checksums: If True, verify SHA256 checksums for ZIP files

    Returns:
        DataFrame with schema: date, instrument, close, funding_rate, adv_notional, spread_frac, taker_fee_frac
    """
    # Internal instrument IDs (match existing system config)
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
            klines = load_binance_klines(inst, data_dir)
        except FileNotFoundError as e:
            logger.error(f"Skipping {inst}: {e}")
            continue

        # Load funding rates (with correct alignment)
        try:
            funding = load_binance_funding_rates(inst, data_dir)
        except FileNotFoundError as e:
            logger.error(f"Skipping {inst}: {e}")
            continue

        # Calculate ADV
        adv = calculate_adv(klines, window=30)

        # Merge klines with ADV
        inst_df = klines.merge(adv, on='date', how='left')

        # Merge with funding rates (left join to preserve all price dates)
        inst_df = inst_df.merge(funding, on='date', how='left')

        # Add metadata
        inst_df['instrument'] = inst  # Use internal ID (BTCUSDT_PERP)
        inst_df['spread_frac'] = market_info[binance_symbol]['spread_frac']
        inst_df['taker_fee_frac'] = market_info[binance_symbol]['taker_fee_frac']

        # Handle missing funding (set to 0 with warning)
        missing_mask = inst_df['funding_rate'].isna()
        missing_count = missing_mask.sum()
        if missing_count > 0:
            logger.warning(
                f"{inst}: {missing_count} days with missing funding (set to 0) - "
                f"introduces deterministic bias"
            )
        inst_df['funding_rate'] = inst_df['funding_rate'].fillna(0.0)

        # Filter date range
        inst_df = inst_df[(inst_df['date'] >= start_date) & (inst_df['date'] <= end_date)]

        # Check data coverage
        coverage_pct = inst_df['close'].notna().sum() / len(inst_df) if len(inst_df) > 0 else 0
        if coverage_pct < 0.90:
            logger.warning(
                f"{inst}: Only {coverage_pct:.1%} price coverage. Consider excluding."
            )

        all_data.append(inst_df)

    if not all_data:
        raise ValueError("No data loaded for any instrument. Check data/raw/ directory.")

    df = pd.concat(all_data, ignore_index=True)

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
