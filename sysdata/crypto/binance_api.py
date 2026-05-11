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
    Adaptive rate limiter that respects Binance's per-IP weight budget.

    Maintains a base inter-request sleep PLUS adaptive backoff once the
    server-reported `X-MBX-USED-WEIGHT-1M` approaches the cap (default
    2400 for fapi). Without the adaptive layer, bursty serial requests
    can exhaust the per-IP budget mid-run and earn the IP a 403 ban —
    observed live 2026-05-11 where 207 of 497 funding-rate fetches
    returned 403 after the budget was silently drained.

    The fixed-sleep fallback (used when no header has been seen yet)
    stays in place so first-request behaviour is unchanged.
    """

    def __init__(
        self,
        sleep_ms: float = 100,
        weight_cap: int = 2400,
        weight_threshold: float = 0.75,
    ):
        """
        Args:
            sleep_ms: Base sleep between requests (default: 100ms). Below
                this rate weight rises faster than it sheds, so any value
                under ~50ms exhausts the cap on large universes.
            weight_cap: Binance's per-IP weight budget per minute window.
                Default 2400 matches fapi public endpoints.
            weight_threshold: Fraction of cap above which adaptive sleep
                extends to keep used-weight under the cap (default 0.75).
        """
        self.base_sleep = sleep_ms / 1000.0
        self.last_request_time = 0.0
        self.weight_cap = weight_cap
        self.weight_threshold = weight_threshold
        # Most recent X-MBX-USED-WEIGHT-1M; 0 means no header observed yet.
        self.last_used_weight: int = 0

    def wait_if_needed(self):
        """Sleep before next request. Sleep extends if used-weight is near cap."""
        sleep_seconds = self.base_sleep
        # Adaptive scaling: if the server reports we're past the threshold,
        # extend each sleep so the 1-minute window has time to slide off
        # used weight before we add more.
        if self.last_used_weight > self.weight_cap * self.weight_threshold:
            headroom = max(self.weight_cap - self.last_used_weight, 1)
            safe_sleep = 60.0 / headroom  # spread remaining budget over 60s
            sleep_seconds = max(sleep_seconds, safe_sleep)
        if self.last_request_time > 0:
            elapsed = time.time() - self.last_request_time
            if elapsed < sleep_seconds:
                time.sleep(sleep_seconds - elapsed)
        self.last_request_time = time.time()

    def update_from_response(self, response) -> None:
        """Parse X-MBX-USED-WEIGHT-1M from response headers. Updates the
        adaptive sleep input so the next wait_if_needed() can throttle."""
        # Header names are case-insensitive on requests.Response.headers.
        weight = response.headers.get("X-MBX-USED-WEIGHT-1M")
        if weight is not None:
            try:
                self.last_used_weight = int(weight)
            except (TypeError, ValueError):
                pass


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
        sleep_ms: float = 100,
        max_retries: int = 3
    ):
        """
        Args:
            cache_dir: Directory for caching API responses
            sleep_ms: Base sleep between requests in milliseconds (default: 100ms).
                The RateLimiter extends this adaptively when X-MBX-USED-WEIGHT-1M
                approaches Binance's per-IP cap.
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

            # Feed observed weight back to the rate limiter so the next call
            # can adapt before we exhaust the per-IP budget.
            self.rate_limiter.update_from_response(response)

            # Handle rate limiting AND IP-level bans. 403 (Forbidden) is
            # what Binance issues when an IP gets ban-listed after budget
            # exhaustion or anti-bot heuristics — distinct from 429 (soft
            # rate limit) and 418 (teapot ban). All three are retryable
            # but 403 needs a much longer cooldown to clear.
            if response.status_code in (403, 418, 429):
                if retry_count >= self.max_retries:
                    raise RuntimeError(
                        f"Max retries ({self.max_retries}) exceeded for {endpoint}. "
                        f"Status: {response.status_code}"
                    )

                if response.status_code == 403:
                    # IP ban: 30s, 60s, 120s. Empirically Binance lifts most
                    # weight-triggered 403s within a couple of minutes.
                    backoff_seconds = 30 * (2 ** retry_count)
                    reason = "IP forbidden (likely budget exhaustion)"
                else:
                    # 429/418: standard 2^n exponential backoff.
                    backoff_seconds = 2 ** retry_count
                    reason = "rate limited"
                logger.warning(
                    f"{reason} (status {response.status_code}). "
                    f"Retrying in {backoff_seconds}s "
                    f"(attempt {retry_count + 1}/{self.max_retries}) "
                    f"[used_weight={self.rate_limiter.last_used_weight}/{self.rate_limiter.weight_cap}]"
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
