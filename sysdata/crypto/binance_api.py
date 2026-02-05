"""
Binance REST API client for fetching recent klines and funding rates.

Provides rate-limited API access with caching and retry logic for daily data updates.
"""

import hashlib
import json
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import logging

import pandas as pd
import requests

logger = logging.getLogger(__name__)


class RateLimiter:
    """
    Conservative fixed-sleep rate limiter.

    Uses fixed sleep between requests rather than tracking headers,
    which may not be present or consistent on public endpoints.
    """

    def __init__(self, sleep_ms: float = 50):
        """
        Args:
            sleep_ms: Sleep duration in milliseconds between requests (default: 50ms)
        """
        self.sleep_seconds = sleep_ms / 1000.0
        self.last_request_time = 0.0

    def wait_if_needed(self):
        """Sleep if needed to maintain rate limit."""
        if self.last_request_time > 0:
            elapsed = time.time() - self.last_request_time
            if elapsed < self.sleep_seconds:
                time.sleep(self.sleep_seconds - elapsed)
        self.last_request_time = time.time()


class BinanceAPIClient:
    """
    Binance Futures REST API client for public market data.

    Features:
    - Rate limiting with conservative fixed sleep
    - Automatic retry with exponential backoff on 429/418
    - Response caching with SHA256 checksums
    - No API key required (public endpoints only)
    """

    BASE_URL = "https://fapi.binance.com"

    def __init__(
        self,
        cache_dir: Path,
        sleep_ms: float = 50,
        max_retries: int = 3
    ):
        """
        Args:
            cache_dir: Directory for caching API responses
            sleep_ms: Sleep between requests in milliseconds (default: 50ms)
            max_retries: Maximum retry attempts on errors (default: 3)
        """
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.rate_limiter = RateLimiter(sleep_ms=sleep_ms)
        self.max_retries = max_retries
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'pysystemtrade-crypto-perps/1.0'
        })

    def fetch_klines(
        self,
        symbol: str,
        start_date: date,
        end_date: date,
        use_cache: bool = True
    ) -> pd.DataFrame:
        """
        Fetch daily klines for a symbol and date range.

        Args:
            symbol: Symbol (e.g., 'BTCUSDT')
            start_date: Start date (inclusive)
            end_date: End date (inclusive)
            use_cache: If True, use cached responses when available

        Returns:
            DataFrame with columns: [date, open, high, low, close, volume, quote_volume]
        """
        cache_key = f"{symbol}_{start_date}_{end_date}_klines"

        # Try to load from cache
        if use_cache:
            cached_df = self._load_cached(cache_key)
            if cached_df is not None:
                logger.debug(f"Cache hit for {cache_key}")
                return cached_df

        # Fetch from API
        logger.info(f"Fetching klines for {symbol} from {start_date} to {end_date}")

        # Convert dates to millisecond timestamps
        start_ts = int(datetime.combine(start_date, datetime.min.time()).timestamp() * 1000)
        end_ts = int(datetime.combine(end_date + timedelta(days=1), datetime.min.time()).timestamp() * 1000)

        params = {
            'symbol': symbol,
            'interval': '1d',
            'startTime': start_ts,
            'endTime': end_ts,
            'limit': 1500  # Max allowed
        }

        data = self._request_with_retry('GET', '/fapi/v1/klines', params=params)

        # Parse response
        # Binance klines format: [open_time, open, high, low, close, volume, close_time, quote_volume, ...]
        if not data:
            logger.warning(f"No klines data returned for {symbol} {start_date} to {end_date}")
            return pd.DataFrame()

        df = pd.DataFrame(data, columns=[
            'open_time', 'open', 'high', 'low', 'close', 'volume',
            'close_time', 'quote_volume', 'num_trades', 'taker_buy_base',
            'taker_buy_quote', 'ignore'
        ])

        # Convert types
        df['date'] = pd.to_datetime(df['open_time'], unit='ms').dt.date
        for col in ['open', 'high', 'low', 'close', 'volume', 'quote_volume']:
            df[col] = pd.to_numeric(df[col], errors='coerce')

        # Select and order columns
        df = df[['date', 'open', 'high', 'low', 'close', 'volume', 'quote_volume']]

        # Cache result
        self._cache_response(cache_key, df)

        return df

    def fetch_funding_rates(
        self,
        symbol: str,
        start_date: date,
        end_date: date,
        use_cache: bool = True
    ) -> pd.DataFrame:
        """
        Fetch funding rate history for a symbol and date range.

        Binance funding occurs every 8 hours (00:00, 08:00, 16:00 UTC).

        Args:
            symbol: Symbol (e.g., 'BTCUSDT')
            start_date: Start date (inclusive)
            end_date: End date (inclusive)
            use_cache: If True, use cached responses when available

        Returns:
            DataFrame with columns: [timestamp, funding_rate, symbol]
        """
        cache_key = f"{symbol}_{start_date}_{end_date}_funding"

        # Try to load from cache
        if use_cache:
            cached_df = self._load_cached(cache_key)
            if cached_df is not None:
                logger.debug(f"Cache hit for {cache_key}")
                return cached_df

        # Fetch from API
        logger.info(f"Fetching funding rates for {symbol} from {start_date} to {end_date}")

        # Convert dates to millisecond timestamps
        start_ts = int(datetime.combine(start_date, datetime.min.time()).timestamp() * 1000)
        end_ts = int(datetime.combine(end_date + timedelta(days=1), datetime.min.time()).timestamp() * 1000)

        params = {
            'symbol': symbol,
            'startTime': start_ts,
            'endTime': end_ts,
            'limit': 1000  # Max allowed
        }

        data = self._request_with_retry('GET', '/fapi/v1/fundingRate', params=params)

        # Parse response
        if not data:
            logger.warning(f"No funding data returned for {symbol} {start_date} to {end_date}")
            return pd.DataFrame()

        df = pd.DataFrame(data)

        # Convert types
        df['timestamp'] = pd.to_datetime(df['fundingTime'], unit='ms')
        df['funding_rate'] = pd.to_numeric(df['fundingRate'], errors='coerce')
        df['symbol'] = df['symbol']

        # Select and order columns
        df = df[['timestamp', 'funding_rate', 'symbol']]

        # Cache result
        self._cache_response(cache_key, df)

        return df

    def _request_with_retry(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict] = None,
        retry_count: int = 0
    ) -> List:
        """
        Execute HTTP request with automatic retry on rate limit errors.

        Args:
            method: HTTP method (GET, POST, etc.)
            endpoint: API endpoint (e.g., '/fapi/v1/klines')
            params: Query parameters
            retry_count: Current retry attempt (internal)

        Returns:
            Parsed JSON response

        Raises:
            requests.HTTPError: On non-recoverable errors
            RuntimeError: If max retries exceeded
        """
        # Apply rate limiting
        self.rate_limiter.wait_if_needed()

        url = f"{self.BASE_URL}{endpoint}"

        try:
            response = self.session.request(method, url, params=params, timeout=30)

            # Handle rate limiting
            if response.status_code in [418, 429]:
                if retry_count >= self.max_retries:
                    raise RuntimeError(
                        f"Max retries ({self.max_retries}) exceeded for {endpoint}. "
                        f"Status: {response.status_code}"
                    )

                # Exponential backoff: 2^retry_count seconds
                backoff_seconds = 2 ** retry_count
                logger.warning(
                    f"Rate limited (status {response.status_code}). "
                    f"Retrying in {backoff_seconds}s (attempt {retry_count + 1}/{self.max_retries})"
                )
                time.sleep(backoff_seconds)
                return self._request_with_retry(method, endpoint, params, retry_count + 1)

            # Raise on other errors
            response.raise_for_status()

            return response.json()

        except requests.RequestException as e:
            logger.error(f"Request failed for {endpoint}: {e}")
            raise

    def _cache_response(self, cache_key: str, df: pd.DataFrame) -> None:
        """
        Write DataFrame to cache with SHA256 checksum.

        Args:
            cache_key: Unique cache key
            df: DataFrame to cache
        """
        cache_path = self.cache_dir / f"{cache_key}.parquet"
        checksum_path = self.cache_dir / f"{cache_key}.parquet.sha256"

        # Write to temp file first (atomic)
        temp_path = cache_path.with_suffix('.parquet.tmp')
        df.to_parquet(temp_path, index=False)

        # Compute checksum
        sha256_hash = hashlib.sha256()
        with open(temp_path, 'rb') as f:
            for chunk in iter(lambda: f.read(8192), b''):
                sha256_hash.update(chunk)
        checksum = sha256_hash.hexdigest()

        # Write checksum
        with open(checksum_path, 'w') as f:
            f.write(checksum)

        # Atomic rename
        temp_path.rename(cache_path)

        logger.debug(f"Cached {cache_key} with checksum {checksum[:8]}...")

    def _load_cached(self, cache_key: str) -> Optional[pd.DataFrame]:
        """
        Load DataFrame from cache if checksum valid.

        Args:
            cache_key: Unique cache key

        Returns:
            DataFrame if cache hit and valid, None otherwise
        """
        cache_path = self.cache_dir / f"{cache_key}.parquet"
        checksum_path = self.cache_dir / f"{cache_key}.parquet.sha256"

        if not cache_path.exists() or not checksum_path.exists():
            return None

        # Read expected checksum
        with open(checksum_path, 'r') as f:
            expected_checksum = f.read().strip()

        # Compute actual checksum
        sha256_hash = hashlib.sha256()
        with open(cache_path, 'rb') as f:
            for chunk in iter(lambda: f.read(8192), b''):
                sha256_hash.update(chunk)
        actual_checksum = sha256_hash.hexdigest()

        # Verify
        if actual_checksum != expected_checksum:
            logger.warning(
                f"Cache checksum mismatch for {cache_key}. "
                f"Expected {expected_checksum[:8]}..., got {actual_checksum[:8]}..."
            )
            return None

        # Load DataFrame
        df = pd.read_parquet(cache_path)
        return df


def aggregate_funding_to_daily(funding_df: pd.DataFrame) -> pd.DataFrame:
    """
    Aggregate 8-hourly funding rates to daily using UTC day boundaries.

    Daily bucket = UTC day [00:00:00, 23:59:59]
    Includes all funding events with timestamps in this window.

    Args:
        funding_df: DataFrame with columns [timestamp, funding_rate, symbol]
                   timestamp should be in UTC

    Returns:
        DataFrame with columns [date, funding_rate, symbol]
        where funding_rate is the sum of all 8h events in that UTC day
    """
    if funding_df.empty:
        return pd.DataFrame(columns=['date', 'funding_rate', 'symbol'])

    # Ensure timestamp is datetime
    funding_df = funding_df.copy()
    if not pd.api.types.is_datetime64_any_dtype(funding_df['timestamp']):
        funding_df['timestamp'] = pd.to_datetime(funding_df['timestamp'])

    # Extract UTC date
    funding_df['date'] = funding_df['timestamp'].dt.date

    # Sum by (date, symbol)
    daily_df = funding_df.groupby(['date', 'symbol'], as_index=False).agg({
        'funding_rate': 'sum'
    })

    return daily_df
