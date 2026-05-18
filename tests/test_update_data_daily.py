"""
Unit tests for `scripts/update_data_daily.py`, focused on the parallel fetch loop.

`_fetch_one_symbol` is the per-symbol worker that runs inside the
ThreadPoolExecutor. It must:
- Return a result dict on success with kline/funding counts.
- Catch exceptions and return ok=False with the error message (never raise),
  so a single symbol failure doesn't poison the executor.
- Be safe to call concurrently against a shared BinanceAPIClient (the
  client's RateLimiter is now thread-safe).
"""

import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd
import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "scripts"))

from update_data_daily import _fetch_one_symbol  # noqa: E402


def _make_client(klines_rows=3, funding_rows=24, raises=None):
    """Build a fake BinanceAPIClient stub for the worker to call."""
    client = MagicMock()
    if raises is not None:
        client.fetch_klines.side_effect = raises
        return client
    client.fetch_klines.return_value = pd.DataFrame({
        'date': pd.date_range('2026-05-14', periods=klines_rows, freq='D'),
        'close': [100.0 + i for i in range(klines_rows)],
    })
    # Funding rows: 8h cadence → 3 per day. Generate `funding_rows` events.
    base = pd.Timestamp('2026-05-14 00:00:00')
    client.fetch_funding_rates.return_value = pd.DataFrame({
        'timestamp': [base + pd.Timedelta(hours=8 * i) for i in range(funding_rows)],
        'funding_rate': [0.0001] * funding_rows,
        'symbol': ['BTCUSDT'] * funding_rows,
    })
    return client


def test_fetch_one_symbol_happy_path():
    client = _make_client(klines_rows=3, funding_rows=9)  # 3 days × 3 events/day
    result = _fetch_one_symbol(
        'BTCUSDT',
        date(2026, 5, 14),
        date(2026, 5, 16),
        date(2026, 5, 16),
        client,
    )
    assert result['ok'] is True
    assert result['symbol'] == 'BTCUSDT'
    assert result['kline_rows'] == 3
    assert result['funding_events'] == 9
    assert result['funding_days'] == 3  # aggregated
    assert result['end_date'] == date(2026, 5, 16)
    assert result['error'] is None


def test_fetch_one_symbol_empty_dataframes():
    """Empty klines/funding (e.g., listed-but-no-trades) should not raise."""
    client = MagicMock()
    client.fetch_klines.return_value = pd.DataFrame()
    client.fetch_funding_rates.return_value = pd.DataFrame()
    result = _fetch_one_symbol(
        'EMPTYUSDT',
        date(2026, 5, 14),
        date(2026, 5, 16),
        date(2026, 5, 16),
        client,
    )
    assert result['ok'] is True
    assert result['kline_rows'] == 0
    assert result['funding_events'] == 0
    assert result['funding_days'] == 0


def test_fetch_one_symbol_kline_exception_returns_ok_false():
    """Any exception from the client must be caught and returned, not raised."""
    client = _make_client(raises=RuntimeError("403 Forbidden"))
    result = _fetch_one_symbol(
        'BANNEDUSDT',
        date(2026, 5, 14),
        date(2026, 5, 16),
        date(2026, 5, 16),
        client,
    )
    assert result['ok'] is False
    assert result['error'] == "403 Forbidden"
    # Don't trust counts when ok=False; explicit zeros for downstream safety.
    assert result['kline_rows'] == 0
    assert result['funding_events'] == 0


def test_fetch_one_symbol_funding_exception_after_klines_succeed():
    """If klines succeed but funding raises, the symbol still reports ok=False —
    the live system needs BOTH series to update the dataset consistently."""
    client = _make_client(klines_rows=3, funding_rows=9)
    client.fetch_funding_rates.side_effect = RuntimeError("connection reset")
    result = _fetch_one_symbol(
        'BTCUSDT',
        date(2026, 5, 14),
        date(2026, 5, 16),
        date(2026, 5, 16),
        client,
    )
    assert result['ok'] is False
    assert "connection reset" in result['error']


def test_parallel_dispatch_returns_one_result_per_symbol():
    """End-to-end smoke: 20 symbols through a real ThreadPoolExecutor should
    each return their own result dict with the right symbol field. Catches
    shared-state bugs at the worker layer."""
    client = _make_client(klines_rows=3, funding_rows=9)
    symbols = [f"SYM{i:02d}USDT" for i in range(20)]

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [
            pool.submit(
                _fetch_one_symbol,
                sym,
                date(2026, 5, 14), date(2026, 5, 16),
                date(2026, 5, 16), client,
            )
            for sym in symbols
        ]
        results = [f.result() for f in futures]

    assert len(results) == 20
    # Each result has its own symbol — no cross-contamination.
    assert sorted(r['symbol'] for r in results) == sorted(symbols)
    assert all(r['ok'] for r in results)
    # MagicMock returned the same DataFrame to all calls — counts are identical.
    assert all(r['kline_rows'] == 3 for r in results)
