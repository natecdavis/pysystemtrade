"""
Unit tests for Binance REST API client.

Tests rate limiting, caching, retry logic, and funding aggregation.
"""

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
import tempfile

import pandas as pd
import pytest

from sysdata.crypto.binance_api import (
    BinanceAPIClient,
    RateLimiter,
    aggregate_funding_to_daily
)


class TestRateLimiter:
    """Test rate limiter with fixed sleep."""

    def test_first_request_no_wait(self):
        """First request should not wait."""
        limiter = RateLimiter(sleep_ms=100)

        start = time.time()
        limiter.wait_if_needed()
        elapsed = time.time() - start

        # Should be nearly instant (< 10ms)
        assert elapsed < 0.01

    def test_subsequent_requests_wait(self):
        """Subsequent requests should wait for sleep duration."""
        limiter = RateLimiter(sleep_ms=50)  # 50ms sleep

        # First request
        limiter.wait_if_needed()

        # Second request should wait ~50ms
        start = time.time()
        limiter.wait_if_needed()
        elapsed = time.time() - start

        # Should be approximately 50ms (allow ±20ms tolerance)
        assert 0.03 < elapsed < 0.07

    def test_wait_only_needed_delay(self):
        """Should only wait remaining time if some time already elapsed."""
        limiter = RateLimiter(sleep_ms=100)

        # First request
        limiter.wait_if_needed()

        # Wait 60ms manually
        time.sleep(0.06)

        # Second request should only wait ~40ms more
        start = time.time()
        limiter.wait_if_needed()
        elapsed = time.time() - start

        # Should be approximately 40ms (allow ±20ms tolerance)
        assert 0.02 < elapsed < 0.06

    def test_update_from_response_parses_used_weight_header(self):
        """X-MBX-USED-WEIGHT-1M from a real Binance response must update the
        adaptive throttle's known used-weight value."""
        limiter = RateLimiter(sleep_ms=50)
        assert limiter.last_used_weight == 0

        mock_resp = Mock()
        mock_resp.headers = {"X-MBX-USED-WEIGHT-1M": "1850"}
        limiter.update_from_response(mock_resp)
        assert limiter.last_used_weight == 1850

    def test_update_from_response_no_header_is_noop(self):
        """When Binance does not return the weight header (e.g., non-fapi
        endpoint or a 5xx that strips headers), used-weight stays at its
        prior value rather than resetting to 0 spuriously."""
        limiter = RateLimiter(sleep_ms=50)
        limiter.last_used_weight = 1200  # seed
        mock_resp = Mock()
        mock_resp.headers = {}  # header absent
        limiter.update_from_response(mock_resp)
        assert limiter.last_used_weight == 1200

    def test_adaptive_throttle_extends_sleep_when_weight_near_cap(self):
        """Regression for 2026-05-11: when used-weight exceeds the threshold
        fraction of cap, the limiter must wait longer than the base sleep
        to let the 1-min window slide off some weight before adding more."""
        limiter = RateLimiter(sleep_ms=10, weight_cap=2400, weight_threshold=0.75)

        # Seed with used_weight = 2300 of 2400 cap → only 100 weight headroom.
        # safe_sleep = 60.0 / 100 = 0.6s, must dominate the 10ms base.
        limiter.last_used_weight = 2300
        limiter.wait_if_needed()  # first call sets last_request_time

        start = time.time()
        limiter.wait_if_needed()
        elapsed = time.time() - start

        # Should sleep approximately 0.6s, well above the 10ms base.
        assert elapsed > 0.5, (
            f"expected adaptive throttle to dominate base sleep when used_weight≈cap, "
            f"got elapsed={elapsed:.3f}s"
        )

    def test_adaptive_throttle_uses_base_sleep_when_weight_low(self):
        """When used-weight is below the threshold, sleep should match the base
        sleep — adaptive scaling must not kick in spuriously."""
        limiter = RateLimiter(sleep_ms=20, weight_cap=2400, weight_threshold=0.75)
        # Threshold = 0.75 * 2400 = 1800. Setting weight to 1000 → below threshold.
        limiter.last_used_weight = 1000
        limiter.wait_if_needed()

        start = time.time()
        limiter.wait_if_needed()
        elapsed = time.time() - start

        # Should sleep ~20ms (the base), well under 100ms.
        assert elapsed < 0.1, (
            f"expected base sleep when used_weight far below cap, got elapsed={elapsed:.3f}s"
        )


class TestBinanceAPIClient:
    """Test Binance API client with mocking."""

    @pytest.fixture
    def temp_cache_dir(self):
        """Create temporary cache directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    @pytest.fixture
    def client(self, temp_cache_dir):
        """Create client with temp cache."""
        return BinanceAPIClient(
            cache_dir=temp_cache_dir,
            sleep_ms=1,  # Fast for tests
            max_retries=3
        )

    def test_fetch_klines_success(self, client, temp_cache_dir):
        """Test successful klines fetch."""
        # Mock response data
        mock_data = [
            [
                1609459200000,  # 2021-01-01 00:00:00 UTC
                "29000.0", "29500.0", "28500.0", "29200.0",  # OHLC
                "1000.5",  # volume
                1609545599999,  # close_time
                "29100000.0",  # quote_volume
                5000, "500.0", "14550000.0", "0"
            ],
            [
                1609545600000,  # 2021-01-02 00:00:00 UTC
                "29200.0", "30000.0", "29000.0", "29800.0",
                "1200.3",
                1609631999999,
                "35760000.0",
                6000, "600.0", "17880000.0", "0"
            ]
        ]

        with patch.object(client, '_request_with_retry', return_value=mock_data):
            df = client.fetch_klines('BTCUSDT', date(2021, 1, 1), date(2021, 1, 2), use_cache=False)

        # Verify structure
        assert len(df) == 2
        assert list(df.columns) == ['date', 'open', 'high', 'low', 'close', 'volume', 'quote_volume']

        # Verify values
        assert df.iloc[0]['date'] == date(2021, 1, 1)
        assert df.iloc[0]['close'] == 29200.0
        assert df.iloc[0]['volume'] == 1000.5

        assert df.iloc[1]['date'] == date(2021, 1, 2)
        assert df.iloc[1]['close'] == 29800.0

    def test_fetch_funding_success(self, client):
        """Test successful funding rates fetch."""
        # Mock response data
        mock_data = [
            {
                'symbol': 'BTCUSDT',
                'fundingTime': 1609459200000,  # 2021-01-01 00:00:00 UTC
                'fundingRate': '0.00010000'
            },
            {
                'symbol': 'BTCUSDT',
                'fundingTime': 1609488000000,  # 2021-01-01 08:00:00 UTC
                'fundingRate': '0.00012000'
            },
            {
                'symbol': 'BTCUSDT',
                'fundingTime': 1609516800000,  # 2021-01-01 16:00:00 UTC
                'fundingRate': '0.00011000'
            }
        ]

        with patch.object(client, '_request_with_retry', return_value=mock_data):
            df = client.fetch_funding_rates('BTCUSDT', date(2021, 1, 1), date(2021, 1, 1), use_cache=False)

        # Verify structure
        assert len(df) == 3
        assert list(df.columns) == ['timestamp', 'funding_rate', 'symbol']

        # Verify values
        assert df.iloc[0]['funding_rate'] == 0.0001
        assert df.iloc[1]['funding_rate'] == 0.00012
        assert df.iloc[2]['funding_rate'] == 0.00011
        assert all(df['symbol'] == 'BTCUSDT')

    def test_caching_writes_and_reads(self, client, temp_cache_dir):
        """Test that caching writes and reads correctly with checksum."""
        # Create test DataFrame
        test_df = pd.DataFrame({
            'date': [date(2021, 1, 1), date(2021, 1, 2)],
            'close': [29200.0, 29800.0]
        })

        # Cache it
        cache_key = "test_key"
        client._cache_response(cache_key, test_df)

        # Verify files exist
        cache_path = temp_cache_dir / f"{cache_key}.parquet"
        checksum_path = temp_cache_dir / f"{cache_key}.parquet.sha256"
        assert cache_path.exists()
        assert checksum_path.exists()

        # Load from cache
        loaded_df = client._load_cached(cache_key)
        assert loaded_df is not None
        pd.testing.assert_frame_equal(loaded_df, test_df)

    def test_cache_checksum_validation_fails(self, client, temp_cache_dir):
        """Test that corrupted cache is rejected."""
        # Create and cache test DataFrame
        test_df = pd.DataFrame({
            'date': [date(2021, 1, 1)],
            'close': [29200.0]
        })
        cache_key = "test_key"
        client._cache_response(cache_key, test_df)

        # Corrupt the checksum file
        checksum_path = temp_cache_dir / f"{cache_key}.parquet.sha256"
        with open(checksum_path, 'w') as f:
            f.write("0000000000000000")  # Invalid checksum

        # Load should return None
        loaded_df = client._load_cached(cache_key)
        assert loaded_df is None

    def test_cache_hit_skips_api_request(self, client):
        """Test that cache hit prevents API request."""
        # Pre-cache data
        test_df = pd.DataFrame({
            'date': [date(2021, 1, 1)],
            'close': [29200.0]
        })
        cache_key = "BTCUSDT_2021-01-01_2021-01-01_klines"
        client._cache_response(cache_key, test_df)

        # Fetch with cache enabled (should NOT call API)
        with patch.object(client, '_request_with_retry') as mock_request:
            df = client.fetch_klines('BTCUSDT', date(2021, 1, 1), date(2021, 1, 1), use_cache=True)

            # API should not be called
            mock_request.assert_not_called()

            # Should return cached data
            assert len(df) == 1
            assert df.iloc[0]['close'] == 29200.0

    def test_retry_on_429(self, client):
        """Test exponential backoff on rate limit (429)."""
        # Mock: First call returns 429, second succeeds
        mock_response_429 = Mock()
        mock_response_429.status_code = 429
        mock_response_429.raise_for_status.side_effect = None

        mock_response_ok = Mock()
        mock_response_ok.status_code = 200
        mock_response_ok.json.return_value = []

        with patch.object(client.session, 'request', side_effect=[mock_response_429, mock_response_ok]):
            start = time.time()
            result = client._request_with_retry('GET', '/test')
            elapsed = time.time() - start

            # Should have waited ~1 second (2^0 backoff on first retry)
            assert 0.9 < elapsed < 1.2
            assert result == []

    def test_retry_max_exceeded(self, client):
        """Test that max retries raises error."""
        # Mock: Always return 429
        mock_response = Mock()
        mock_response.status_code = 429
        mock_response.headers = {}

        with patch.object(client.session, 'request', return_value=mock_response):
            with pytest.raises(RuntimeError, match="Max retries"):
                client._request_with_retry('GET', '/test')

    def test_retry_on_403_uses_longer_backoff(self, client):
        """Regression for 2026-05-11: 403 Forbidden (IP-ban after weight
        exhaustion) must be retried with a longer cooldown than 429.
        Before this fix the fetcher raised immediately on 403, leaving
        207 instruments with stale data and triggering the staleness-
        overlay / shadow-target interaction bug downstream.
        """
        mock_response_403 = Mock()
        mock_response_403.status_code = 403
        mock_response_403.headers = {}
        mock_response_403.raise_for_status.side_effect = None

        mock_response_ok = Mock()
        mock_response_ok.status_code = 200
        mock_response_ok.headers = {}
        mock_response_ok.json.return_value = []

        # Use small backoffs in this test to keep it fast — patch the sleep
        # so we can verify the 30s base is being chosen for 403 without
        # actually waiting 30s.
        with patch.object(
            client.session, "request",
            side_effect=[mock_response_403, mock_response_ok],
        ), patch("sysdata.crypto.binance_api.time.sleep") as mock_sleep:
            result = client._request_with_retry("GET", "/test")

        # First call after the 403 must sleep ~30s (30 * 2^0); 429 would have
        # been 1s (2^0).
        assert result == []
        sleep_calls = [args[0] for args, _ in mock_sleep.call_args_list]
        assert any(arg >= 30 for arg in sleep_calls), (
            f"expected at least one sleep of >= 30s for 403 retry, "
            f"got sleep calls: {sleep_calls}"
        )

    def test_used_weight_header_propagates_to_ratelimiter(self, client):
        """After every successful response, the rate limiter must be updated
        with the server-reported used-weight so subsequent requests can
        throttle adaptively. This is the wiring that prevents the 2026-05-11
        budget-exhaustion failure mode."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.headers = {"X-MBX-USED-WEIGHT-1M": "1900"}
        mock_response.json.return_value = []

        with patch.object(client.session, "request", return_value=mock_response):
            client._request_with_retry("GET", "/test")

        assert client.rate_limiter.last_used_weight == 1900


class TestFundingAggregation:
    """Test funding rate aggregation to daily."""

    def test_aggregate_three_events_per_day(self):
        """Test aggregation of standard 3× 8-hourly events per day."""
        # Synthetic data: 3 events per day (00:00, 08:00, 16:00 UTC)
        funding_df = pd.DataFrame({
            'timestamp': pd.to_datetime([
                '2021-01-01 00:00:00',
                '2021-01-01 08:00:00',
                '2021-01-01 16:00:00',
                '2021-01-02 00:00:00',
                '2021-01-02 08:00:00',
                '2021-01-02 16:00:00',
            ]),
            'funding_rate': [0.0001, 0.0001, 0.0001, 0.00015, 0.00015, 0.00015],
            'symbol': ['BTCUSDT'] * 6
        })

        daily_df = aggregate_funding_to_daily(funding_df)

        # Should have 2 days
        assert len(daily_df) == 2

        # Day 1: sum = 0.0003
        day1 = daily_df[daily_df['date'] == date(2021, 1, 1)]
        assert len(day1) == 1
        assert day1.iloc[0]['funding_rate'] == pytest.approx(0.0003)

        # Day 2: sum = 0.00045
        day2 = daily_df[daily_df['date'] == date(2021, 1, 2)]
        assert len(day2) == 1
        assert day2.iloc[0]['funding_rate'] == pytest.approx(0.00045)

    def test_aggregate_missing_event(self):
        """Test aggregation when one 8h event is missing."""
        # Missing 08:00 event on 2021-01-01
        funding_df = pd.DataFrame({
            'timestamp': pd.to_datetime([
                '2021-01-01 00:00:00',
                '2021-01-01 16:00:00',  # Missing 08:00
            ]),
            'funding_rate': [0.0001, 0.0001],
            'symbol': ['BTCUSDT'] * 2
        })

        daily_df = aggregate_funding_to_daily(funding_df)

        # Should still have 1 day, but sum only includes 2 events
        assert len(daily_df) == 1
        assert daily_df.iloc[0]['funding_rate'] == pytest.approx(0.0002)
        assert daily_df.iloc[0]['date'] == date(2021, 1, 1)

    def test_aggregate_multiple_instruments(self):
        """Test aggregation with multiple instruments."""
        funding_df = pd.DataFrame({
            'timestamp': pd.to_datetime([
                '2021-01-01 00:00:00',
                '2021-01-01 08:00:00',
                '2021-01-01 16:00:00',
                '2021-01-01 00:00:00',
                '2021-01-01 08:00:00',
                '2021-01-01 16:00:00',
            ]),
            'funding_rate': [0.0001, 0.0001, 0.0001, 0.0002, 0.0002, 0.0002],
            'symbol': ['BTCUSDT', 'BTCUSDT', 'BTCUSDT', 'ETHUSDT', 'ETHUSDT', 'ETHUSDT']
        })

        daily_df = aggregate_funding_to_daily(funding_df)

        # Should have 2 rows (one per symbol)
        assert len(daily_df) == 2

        btc = daily_df[daily_df['symbol'] == 'BTCUSDT']
        assert btc.iloc[0]['funding_rate'] == pytest.approx(0.0003)

        eth = daily_df[daily_df['symbol'] == 'ETHUSDT']
        assert eth.iloc[0]['funding_rate'] == pytest.approx(0.0006)

    def test_aggregate_empty_dataframe(self):
        """Test aggregation of empty DataFrame."""
        funding_df = pd.DataFrame(columns=['timestamp', 'funding_rate', 'symbol'])
        daily_df = aggregate_funding_to_daily(funding_df)

        # Should return empty DataFrame with correct columns
        assert len(daily_df) == 0
        assert list(daily_df.columns) == ['date', 'funding_rate', 'symbol']

    def test_utc_day_boundaries(self):
        """Test that UTC day boundaries are respected."""
        # Event at 23:00 on day 1 should belong to day 1
        # Event at 01:00 on day 2 should belong to day 2
        funding_df = pd.DataFrame({
            'timestamp': pd.to_datetime([
                '2021-01-01 23:00:00',
                '2021-01-02 01:00:00',
            ]),
            'funding_rate': [0.0001, 0.0002],
            'symbol': ['BTCUSDT', 'BTCUSDT']
        })

        daily_df = aggregate_funding_to_daily(funding_df)

        # Should have 2 days
        assert len(daily_df) == 2

        day1 = daily_df[daily_df['date'] == date(2021, 1, 1)]
        assert day1.iloc[0]['funding_rate'] == 0.0001

        day2 = daily_df[daily_df['date'] == date(2021, 1, 2)]
        assert day2.iloc[0]['funding_rate'] == 0.0002


class TestRateLimiterThreadSafety:
    """RateLimiter must serialize state mutation + per-request spacing across
    threads. Without the internal lock, N concurrent workers see the same
    last_request_time, sleep for the same delta, and burst-fire — defeating
    the per-IP weight-budget protection."""

    def test_concurrent_wait_if_needed_serializes_requests(self):
        # 8 threads each call wait_if_needed twice with sleep_ms=50. The
        # limiter should enforce ≥50ms between successive issuances, so 16
        # total invocations take ≥ 15 × 0.05 s = 0.75 s.
        limiter = RateLimiter(sleep_ms=50)
        N_THREADS = 8
        N_CALLS_PER_THREAD = 2
        timestamps: list[float] = []
        timestamps_lock = threading.Lock()

        def worker():
            for _ in range(N_CALLS_PER_THREAD):
                limiter.wait_if_needed()
                with timestamps_lock:
                    timestamps.append(time.time())

        t0 = time.time()
        with ThreadPoolExecutor(max_workers=N_THREADS) as pool:
            futures = [pool.submit(worker) for _ in range(N_THREADS)]
            for fut in futures:
                fut.result()
        wall = time.time() - t0

        # 16 total issuances need at least 15 gaps × 50ms = 750ms.
        # Tight check: allow scheduling jitter under 25% (covers CI variance).
        assert wall >= 0.75 * 0.75, f"wall={wall:.3f}s too short — limiter not serializing"

        # Issuances must be monotonically spaced. Sort observations, then any
        # adjacent pair where BOTH are non-cache-hits (i.e., both went through
        # wait_if_needed) should be ≥ sleep_ms apart — minus a small jitter
        # tolerance for the clock between time.time() inside wait_if_needed
        # and time.time() back in the worker.
        timestamps.sort()
        jitter = 0.02
        gaps = [b - a for a, b in zip(timestamps, timestamps[1:])]
        # All but a tiny number of gaps should be ≥ 50ms - jitter.
        small_gaps = [g for g in gaps if g < 0.05 - jitter]
        assert len(small_gaps) <= 1, (
            f"{len(small_gaps)} adjacent gaps < 30ms — limiter not serializing "
            f"(gaps={[round(g*1000, 1) for g in gaps]})"
        )

    def test_concurrent_update_from_response_no_torn_state(self):
        # Hammer update_from_response from many threads with valid + garbage
        # headers. Final last_used_weight must be one of the valid values
        # (never a partial integer / Mock leftover).
        limiter = RateLimiter(sleep_ms=1)
        valid_weights = list(range(1, 100))

        def worker(weights):
            for w in weights:
                resp = MagicMock()
                resp.headers = {'X-MBX-USED-WEIGHT-1M': str(w)}
                limiter.update_from_response(resp)
                resp2 = MagicMock()
                resp2.headers = {'X-MBX-USED-WEIGHT-1M': 'not-a-number'}
                limiter.update_from_response(resp2)
                resp3 = MagicMock()
                resp3.headers = {}  # No header
                limiter.update_from_response(resp3)

        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(worker, valid_weights) for _ in range(8)]
            for fut in futures:
                fut.result()

        assert limiter.last_used_weight in valid_weights, (
            f"final weight={limiter.last_used_weight} is not one of the values "
            "written by any thread — state was torn"
        )

    def test_wait_if_needed_adaptive_sleep_under_concurrency(self):
        # Set last_used_weight above the 75% threshold so adaptive backoff
        # kicks in; concurrent callers should all see the extended sleep
        # because the threshold check happens under the same lock.
        limiter = RateLimiter(sleep_ms=10, weight_cap=2400, weight_threshold=0.75)
        limiter.last_used_weight = 2000  # 83% of cap → adaptive should engage

        # safe_sleep = 60 / (2400 - 2000) = 0.15s = 150ms per request.
        # 3 threads × 1 call = 2 gaps → minimum 0.30s.
        durations: list[float] = []

        def worker():
            t0 = time.time()
            limiter.wait_if_needed()
            durations.append(time.time() - t0)

        t0 = time.time()
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = [pool.submit(worker) for _ in range(3)]
            for fut in futures:
                fut.result()
        wall = time.time() - t0

        # First call returns ~immediately (last_request_time = 0); each
        # subsequent call waits ≥150ms. So 3 calls ≥ ~0.30s total.
        assert wall >= 0.25, (
            f"wall={wall:.3f}s — adaptive backoff did not engage under "
            "concurrency"
        )


class TestPartialDayBarClipping:
    """Regression: fetch_klines / fetch_funding_rates must NOT return bars or
    events past the requested `end_date`.

    Pre-2026-05-24 the endTime millis were computed with naive
    `datetime.combine(...).timestamp()`, which interpreted the value as LOCAL
    time. On EDT (UTC-4) this pushed endTime past 00:00 UTC of (end_date + 1)
    and swept in the still-open daily bar, making trade-plan output depend on
    what UTC hour the daily flow ran (the "partial-day bug"). Fix:
    UTC-explicit endTime + post-fetch / post-cache-load `_clip_*_end` filters.
    """

    @pytest.fixture
    def temp_cache_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    @pytest.fixture
    def client(self, temp_cache_dir):
        return BinanceAPIClient(cache_dir=temp_cache_dir, sleep_ms=1, max_retries=3)

    @staticmethod
    def _kline_row(open_time_ms: int, close_str: str = "100.0", volume_str: str = "1.0"):
        """Build a Binance-format kline row (12 fields) for the given open_time."""
        return [
            open_time_ms,
            "100.0", "100.0", "100.0", close_str,  # OHLC
            volume_str,                              # volume
            open_time_ms + 86_400_000 - 1,           # close_time = open + 1d - 1ms
            "100.0", 1, "0.5", "50.0", "0",          # quote_volume + remaining
        ]

    def test_fetch_klines_drops_tomorrow_partial_bar(self, client):
        """API returns a partial-tomorrow bar; fetch_klines must filter it out
        so end_date acts as a hard cap regardless of Binance's inclusivity."""
        # Three bars: 2024-01-01, 2024-01-02 (= end_date), 2024-01-03 (partial,
        # the leaky bar Binance might return when endTime drifts past 00:00 UTC).
        mock_data = [
            self._kline_row(1704067200000),   # 2024-01-01 00:00 UTC
            self._kline_row(1704153600000),   # 2024-01-02 00:00 UTC
            self._kline_row(1704240000000, close_str="999.0"),  # 2024-01-03 partial
        ]
        with patch.object(client, '_request_with_retry', return_value=mock_data):
            df = client.fetch_klines(
                'BTCUSDT', date(2024, 1, 1), date(2024, 1, 2), use_cache=False
            )
        assert len(df) == 2, f"partial 2024-01-03 bar leaked: {df['date'].tolist()}"
        assert df['date'].max() == date(2024, 1, 2)

    def test_fetch_klines_clips_past_end_date_from_cache(self, client, temp_cache_dir):
        """A pre-fix cached parquet may already contain a partial-tomorrow row;
        the post-load filter must drop it so callers never see it."""
        # Pre-poison cache: fits the new key shape but has a row past end_date.
        cache_key = "BTCUSDT_2024-01-01_2024-01-02_klines"
        poisoned = pd.DataFrame({
            'date': [date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 3)],
            'open': [100.0, 100.0, 100.0],
            'high': [100.0, 100.0, 100.0],
            'low': [100.0, 100.0, 100.0],
            'close': [100.0, 100.0, 999.0],
            'volume': [1.0, 1.0, 1.0],
            'quote_volume': [100.0, 100.0, 100.0],
        })
        client._cache_response(cache_key, poisoned)

        df = client.fetch_klines(
            'BTCUSDT', date(2024, 1, 1), date(2024, 1, 2), use_cache=True
        )
        assert len(df) == 2
        assert date(2024, 1, 3) not in df['date'].tolist()

    def test_fetch_klines_end_ts_is_utc_not_local(self, client):
        """endTime millis must equal end_date 23:59:59.999 UTC regardless of
        the runner's local timezone. Pre-fix this used local TZ via
        `.timestamp()` on a naive datetime."""
        captured = {}

        def capture_params(method, endpoint, params=None):
            captured.update(params)
            return []

        with patch.object(client, '_request_with_retry', side_effect=capture_params):
            client.fetch_klines(
                'BTCUSDT', date(2024, 1, 1), date(2024, 1, 2), use_cache=False
            )
        # Expected: 2024-01-03 00:00:00 UTC - 1us = 1704239999999 (ms)
        # = end_date + 1 day midnight UTC minus 1 microsecond, rounded to ms.
        expected_end_ms = 1704239999999
        assert captured['endTime'] == expected_end_ms, (
            f"endTime={captured['endTime']} != {expected_end_ms} (UTC end-of-day). "
            "Likely the local-TZ bug regressed."
        )
        # And startTime is exactly 2024-01-01 00:00 UTC = 1704067200000 ms.
        assert captured['startTime'] == 1704067200000

    def test_fetch_funding_rates_drops_past_end_date_events(self, client):
        """Funding events past end_date (UTC) must be clipped."""
        mock_data = [
            {'symbol': 'BTCUSDT', 'fundingTime': 1704067200000, 'fundingRate': '0.0001'},   # 2024-01-01 00:00 UTC
            {'symbol': 'BTCUSDT', 'fundingTime': 1704153600000, 'fundingRate': '0.0002'},   # 2024-01-02 00:00 UTC
            {'symbol': 'BTCUSDT', 'fundingTime': 1704182400000, 'fundingRate': '0.0003'},   # 2024-01-02 08:00 UTC
            {'symbol': 'BTCUSDT', 'fundingTime': 1704240000000, 'fundingRate': '0.0099'},   # 2024-01-03 00:00 UTC (partial day)
        ]
        with patch.object(client, '_request_with_retry', return_value=mock_data):
            df = client.fetch_funding_rates(
                'BTCUSDT', date(2024, 1, 1), date(2024, 1, 2), use_cache=False
            )
        assert len(df) == 3
        assert df['timestamp'].dt.date.max() == date(2024, 1, 2)
        assert 0.0099 not in df['funding_rate'].tolist()
