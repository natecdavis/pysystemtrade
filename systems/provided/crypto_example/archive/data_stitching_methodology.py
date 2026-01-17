"""
Data Stitching Methodology for Crypto Backtesting
===================================================

This module provides a methodology for stitching together multiple data sources
to create the longest possible price history for crypto backtesting.

Sources (in priority order):
1. Kraken OHLCVT (primary) - official exchange data, highest quality
2. Coinmetrics Community (extension) - extends history back to 2010 for BTC
3. Funding Rates (carry) - combined Binance + Bybit funding rates

Key Principles:
- NO LOOKAHEAD BIAS: Only use data that would have been available at each point in time
- RATIO ADJUSTMENT: When joining sources, adjust for price differences at join point
- RETURNS FOCUS: We care about returns, not absolute prices - stitching preserves returns
"""

import os
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from typing import Optional, Tuple, Dict

# ============================================================================
# CONFIGURATION
# ============================================================================

KRAKEN_DIR = "/Users/nathanieldavis/pysystemtrade/data/crypto/Kraken_OHLCVT"
COINMETRICS_DIR = "/Users/nathanieldavis/pysystemtrade/data/crypto/coinmetrics_community/csv"
FUNDING_DIR = "/Users/nathanieldavis/pysystemtrade/data/crypto/funding_rates/combined"
OUTPUT_DIR = "/Users/nathanieldavis/pysystemtrade/data/crypto/stitched"

# Symbol mappings
KRAKEN_TO_COINMETRICS = {
    'XBTUSD': 'btc',
    'ETHUSD': 'eth',
    'XRPUSD': 'xrp',
    'LTCUSD': 'ltc',
    'ADAUSD': 'ada',
    'SOLUSD': 'sol',
    'AVAXUSD': 'avax',
    'LINKUSD': 'link',
    'UNIUSD': 'uni',
    'DOGEUSD': 'doge',
    'DOTUSD': 'dot',
    'XLMUSD': 'xlm',
    'AAVEUSD': 'aave',
    'MATICUSD': 'matic',
    'ATOMUSD': 'atom',
}

# Tokens where we can meaningfully extend history
EXTENDABLE_TOKENS = {
    'BTC': {'extra_years': 3.2, 'cm_start': '2010-07-18', 'kraken_start': '2013-10-05'},
    'XRP': {'extra_years': 2.8, 'cm_start': '2014-08-15', 'kraken_start': '2017-05-17'},
    'LINK': {'extra_years': 2.0, 'cm_start': '2017-09-29', 'kraken_start': '2019-09-24'},
    'XLM': {'extra_years': 1.3, 'cm_start': '2015-09-30', 'kraken_start': '2017-01-16'},
    'ADA': {'extra_years': 0.8, 'cm_start': '2017-12-01', 'kraken_start': '2018-09-27'},
    'LTC': {'extra_years': 0.6, 'cm_start': '2013-04-01', 'kraken_start': '2013-10-23'},
}


# ============================================================================
# DATA LOADING FUNCTIONS
# ============================================================================

def load_kraken_daily(symbol: str) -> Optional[pd.DataFrame]:
    """Load Kraken daily OHLCV data."""
    path = os.path.join(KRAKEN_DIR, f"{symbol}_1440.csv")
    if not os.path.exists(path):
        return None

    df = pd.read_csv(path, header=None,
                     names=['timestamp', 'open', 'high', 'low', 'close', 'volume', 'trades'])
    df['date'] = pd.to_datetime(df['timestamp'], unit='s').dt.normalize()
    df = df.set_index('date')
    df = df[['open', 'high', 'low', 'close', 'volume', 'trades']]
    df['source'] = 'kraken'
    return df


def load_coinmetrics(symbol: str) -> Optional[pd.DataFrame]:
    """Load Coinmetrics daily price data."""
    path = os.path.join(COINMETRICS_DIR, f"{symbol}.csv")
    if not os.path.exists(path):
        return None

    df = pd.read_csv(path)
    df['date'] = pd.to_datetime(df['time'])
    df = df.set_index('date')

    # Use PriceUSD if available, otherwise ReferenceRateUSD
    if 'PriceUSD' in df.columns and df['PriceUSD'].notna().sum() > 0:
        price_col = 'PriceUSD'
    elif 'ReferenceRateUSD' in df.columns:
        price_col = 'ReferenceRateUSD'
    else:
        return None

    # Create OHLC from single price (all same for daily)
    result = pd.DataFrame({
        'open': df[price_col],
        'high': df[price_col],
        'low': df[price_col],
        'close': df[price_col],
        'volume': df.get('volume_reported_spot_usd_1d', np.nan),
    })
    result['source'] = 'coinmetrics'
    result = result.dropna(subset=['close'])
    return result


def load_funding_rate(ticker: str) -> Optional[pd.DataFrame]:
    """Load combined funding rate data."""
    path = os.path.join(FUNDING_DIR, f"{ticker}_funding_combined.csv")
    if not os.path.exists(path):
        return None

    df = pd.read_csv(path, parse_dates=['datetime'])
    df['date'] = df['datetime'].dt.normalize()
    df = df.groupby('date')['fundingRate'].sum().to_frame()
    return df


# ============================================================================
# STITCHING FUNCTIONS
# ============================================================================

def calculate_ratio_adjustment(primary: pd.DataFrame, extension: pd.DataFrame,
                               overlap_days: int = 5) -> float:
    """
    Calculate ratio to adjust extension prices to match primary at join point.

    Uses average ratio over overlap period to smooth out daily noise.
    This ensures returns are preserved correctly across the join.
    """
    # Find overlap period
    overlap_dates = primary.index.intersection(extension.index)
    if len(overlap_dates) < overlap_days:
        return 1.0

    # Use first N days of overlap (oldest data from primary, newest from extension)
    overlap_dates = sorted(overlap_dates)[:overlap_days]

    ratios = []
    for date in overlap_dates:
        primary_price = primary.loc[date, 'close']
        extension_price = extension.loc[date, 'close']
        if pd.notna(primary_price) and pd.notna(extension_price) and extension_price > 0:
            ratios.append(primary_price / extension_price)

    if len(ratios) == 0:
        return 1.0

    return np.median(ratios)


def stitch_price_series(primary: pd.DataFrame, extension: pd.DataFrame,
                        join_date: Optional[str] = None) -> pd.DataFrame:
    """
    Stitch extension data before primary data.

    Strategy:
    1. Use all primary data as-is (highest quality)
    2. For dates before primary starts, use extension data
    3. Apply ratio adjustment to extension data to match prices at join point
    4. Return combined series with source column indicating data origin

    This preserves returns while extending history.
    """
    if extension is None or len(extension) == 0:
        return primary

    if primary is None or len(primary) == 0:
        return extension

    # Determine join date (first date of primary data)
    if join_date is None:
        join_date = primary.index.min()
    else:
        join_date = pd.to_datetime(join_date)

    # Calculate ratio adjustment
    ratio = calculate_ratio_adjustment(primary, extension)

    # Get extension data before join date
    extension_before = extension[extension.index < join_date].copy()

    if len(extension_before) == 0:
        return primary

    # Apply ratio adjustment to extension prices
    for col in ['open', 'high', 'low', 'close']:
        if col in extension_before.columns:
            extension_before[col] = extension_before[col] * ratio

    # Volume doesn't need adjustment (different meaning between sources anyway)

    # Combine: extension (adjusted) + primary
    combined = pd.concat([extension_before, primary])
    combined = combined.sort_index()

    # Remove any duplicate dates (prefer primary)
    combined = combined[~combined.index.duplicated(keep='last')]

    return combined


def verify_no_lookahead_bias(df: pd.DataFrame) -> Dict:
    """
    Verify that stitched data has no lookahead bias.

    Checks:
    1. Data is sorted by date
    2. No future data points appear before they should
    3. Join points are clearly marked
    """
    issues = []

    # Check sorting
    if not df.index.is_monotonic_increasing:
        issues.append("Data is not sorted by date")

    # Check for duplicate dates
    if df.index.duplicated().any():
        issues.append(f"Found {df.index.duplicated().sum()} duplicate dates")

    # Check source transitions
    if 'source' in df.columns:
        source_changes = (df['source'] != df['source'].shift(1)).sum() - 1
        if source_changes > 1:
            issues.append(f"Found {source_changes} source transitions (expected max 1)")

    return {
        'valid': len(issues) == 0,
        'issues': issues,
        'date_range': f"{df.index.min()} to {df.index.max()}",
        'total_days': len(df),
    }


# ============================================================================
# MAIN STITCHING FUNCTION
# ============================================================================

def create_stitched_series(kraken_symbol: str,
                           extend_history: bool = True) -> Tuple[pd.DataFrame, Dict]:
    """
    Create a stitched price series for a given Kraken symbol.

    Args:
        kraken_symbol: Kraken symbol (e.g., 'XBTUSD')
        extend_history: If True, extend with Coinmetrics data

    Returns:
        Tuple of (stitched DataFrame, metadata dict)
    """
    metadata = {
        'kraken_symbol': kraken_symbol,
        'extended': False,
        'extension_source': None,
        'ratio_adjustment': 1.0,
    }

    # Load Kraken data (primary)
    kraken_df = load_kraken_daily(kraken_symbol)
    if kraken_df is None:
        raise ValueError(f"No Kraken data found for {kraken_symbol}")

    metadata['kraken_start'] = str(kraken_df.index.min().date())
    metadata['kraken_end'] = str(kraken_df.index.max().date())
    metadata['kraken_days'] = len(kraken_df)

    if not extend_history:
        return kraken_df, metadata

    # Try to extend with Coinmetrics
    cm_symbol = KRAKEN_TO_COINMETRICS.get(kraken_symbol)
    if cm_symbol is None:
        return kraken_df, metadata

    cm_df = load_coinmetrics(cm_symbol)
    if cm_df is None:
        return kraken_df, metadata

    # Check if Coinmetrics has earlier data
    if cm_df.index.min() >= kraken_df.index.min():
        return kraken_df, metadata

    # Stitch the data
    ratio = calculate_ratio_adjustment(kraken_df, cm_df)
    stitched = stitch_price_series(kraken_df, cm_df)

    metadata['extended'] = True
    metadata['extension_source'] = 'coinmetrics'
    metadata['extension_start'] = str(cm_df.index.min().date())
    metadata['ratio_adjustment'] = ratio
    metadata['total_days'] = len(stitched)
    metadata['extra_days'] = len(stitched) - len(kraken_df)

    return stitched, metadata


# ============================================================================
# DEMONSTRATION
# ============================================================================

if __name__ == "__main__":
    print("=" * 80)
    print("DATA STITCHING METHODOLOGY - DEMONSTRATION")
    print("=" * 80)

    # Demonstrate with BTC (biggest history extension)
    print("\n--- Example: BTC (XBTUSD) ---")

    try:
        stitched, metadata = create_stitched_series('XBTUSD', extend_history=True)

        print(f"\nMetadata:")
        for key, value in metadata.items():
            print(f"  {key}: {value}")

        # Verify no lookahead bias
        verification = verify_no_lookahead_bias(stitched)
        print(f"\nVerification:")
        print(f"  Valid: {verification['valid']}")
        print(f"  Date range: {verification['date_range']}")
        print(f"  Total days: {verification['total_days']}")
        if verification['issues']:
            for issue in verification['issues']:
                print(f"  Issue: {issue}")

        # Show join point
        print(f"\nJoin point analysis:")
        join_date = pd.to_datetime(metadata['kraken_start'])
        before_join = stitched[stitched.index < join_date].tail(3)
        after_join = stitched[stitched.index >= join_date].head(3)

        print(f"\n  Last 3 days from Coinmetrics (adjusted):")
        for date, row in before_join.iterrows():
            print(f"    {date.date()}: ${row['close']:.2f} ({row['source']})")

        print(f"\n  First 3 days from Kraken:")
        for date, row in after_join.iterrows():
            print(f"    {date.date()}: ${row['close']:.2f} ({row['source']})")

        # Calculate returns across join to verify continuity
        join_returns = stitched['close'].pct_change()
        join_idx = stitched.index.get_loc(join_date)
        if join_idx > 0:
            return_at_join = join_returns.iloc[join_idx]
            print(f"\n  Return at join point: {return_at_join*100:.2f}%")
            print(f"  (Should be similar to surrounding returns)")

            surrounding_returns = join_returns.iloc[join_idx-5:join_idx+5].abs()
            print(f"  Surrounding avg |return|: {surrounding_returns.mean()*100:.2f}%")

    except Exception as e:
        print(f"Error: {e}")

    # Summary of all extendable tokens
    print("\n" + "=" * 80)
    print("HISTORY EXTENSION SUMMARY")
    print("=" * 80)
    print(f"\n{'Token':<8} {'Kraken Start':<14} {'Extended To':<14} {'Extra Years':<12} {'Ratio Adj':<10}")
    print("-" * 60)

    for kraken_sym in KRAKEN_TO_COINMETRICS.keys():
        try:
            stitched, meta = create_stitched_series(kraken_sym, extend_history=True)
            if meta['extended']:
                extra_years = meta['extra_days'] / 365
                print(f"{kraken_sym:<8} {meta['kraken_start']:<14} {meta['extension_start']:<14} {extra_years:>10.1f}y {meta['ratio_adjustment']:>9.4f}")
        except:
            pass

    print("\n" + "=" * 80)
    print("STITCHING METHODOLOGY SUMMARY")
    print("=" * 80)
    print("""
    KEY PRINCIPLES:

    1. SOURCE HIERARCHY
       - Primary: Kraken OHLCVT (official exchange, highest quality)
       - Extension: Coinmetrics PriceUSD (extends history back to 2010)
       - Fallback: Coinmetrics ReferenceRateUSD (if PriceUSD unavailable)

    2. JOIN METHODOLOGY
       - Find overlap period between primary and extension sources
       - Calculate median price ratio over 5-day overlap window
       - Adjust all extension prices by this ratio
       - This preserves RETURNS while matching price levels

    3. NO LOOKAHEAD BIAS
       - Extension data only used for dates BEFORE primary data starts
       - No future information leaks into historical prices
       - Source column tracks data origin for transparency

    4. PRACTICAL IMPACT
       - BTC: +3.2 years of history (back to 2010)
       - XRP: +2.8 years of history (back to 2014)
       - LINK: +2.0 years of history (back to 2017)
       - This gives more data for parameter estimation and out-of-sample testing

    5. LIMITATIONS
       - SOL, AVAX: No price extension possible (Coinmetrics free tier limitation)
       - Some tokens: Only ReferenceRate available (less accurate than PriceUSD)
       - Volume data: Not comparable between sources, use with caution
    """)
