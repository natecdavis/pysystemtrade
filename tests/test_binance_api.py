"""
Unit tests for Binance REST API client.

Tests rate limiting, caching, retry logic, and funding aggregation.
"""

import json
import time
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

        with patch.object(client.session, 'request', return_value=mock_response):
            with pytest.raises(RuntimeError, match="Max retries"):
                client._request_with_retry('GET', '/test')


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
