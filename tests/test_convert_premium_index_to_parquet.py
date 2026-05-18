"""
Tests for `scripts/convert_premium_index_to_parquet.py`, including the
incremental path. Mirrors the OI converter test suite.

Synthetic fixture: a fake `binance_premium_index_raw/` tree with 2-3 symbol
subdirs, each holding daily kline ZIPs of in-memory CSV data. The converter
treats one ZIP = one daily basis row for the date encoded in the filename.

Covers:
- Full-rebuild path is unchanged (regression guard for the refactor).
- `--incremental` with no existing parquet falls back to full rebuild + warns.
- Incremental filters ZIPs by per-instrument max date - safety_days; a
  `read_kline_csv_from_zip` spy confirms the old ZIPs are never opened.
- Dedup-with-overlap: a re-read ZIP whose date already exists in the parquet
  overwrites the row (`keep='last'`).
- Delisted-instrument behavior: an instrument present in the existing parquet
  but with no new ZIPs survives the merge.
- New-instrument behavior: a directory absent from the existing parquet has
  all its ZIPs read.
- Incremental ≡ full-rebuild equivalence.
"""

import io
import sys
import zipfile
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "scripts"))

import convert_premium_index_to_parquet as conv  # noqa: E402


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _make_csv_bytes(zdate: date, basis: float) -> bytes:
    """One ZIP = one daily kline row. open_time = midnight UTC of zdate (ms)."""
    open_time_ms = int(pd.Timestamp(zdate).value // 1_000_000)
    close_time_ms = open_time_ms + 86_399_999
    row = {
        'open_time': open_time_ms,
        'open': basis - 0.001,
        'high': basis + 0.001,
        'low': basis - 0.002,
        'close': basis,
        'volume': 1.0,
        'close_time': close_time_ms,
        'quote_volume': 1.0,
        'count': 1,
        'taker_buy_volume': 0.5,
        'taker_buy_quote_volume': 0.5,
        'ignore': 0,
    }
    df = pd.DataFrame([row])
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    return buf.getvalue().encode()


def _write_zip(symbol_dir: Path, symbol: str, zdate: date, basis: float) -> Path:
    symbol_dir.mkdir(parents=True, exist_ok=True)
    zpath = symbol_dir / f"{symbol}-1d-{zdate.isoformat()}.zip"
    csv_bytes = _make_csv_bytes(zdate, basis)
    with zipfile.ZipFile(zpath, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{symbol}-1d-{zdate.isoformat()}.csv", csv_bytes)
    return zpath


def _build_tree(root: Path, layout: dict) -> Path:
    """layout = {symbol: [(zdate, basis), ...]}.  Returns the raw root."""
    raw = root / "binance_premium_index_raw"
    for symbol, rows in layout.items():
        sdir = raw / symbol
        for zdate, basis in rows:
            _write_zip(sdir, symbol, zdate, basis)
    return raw


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_full_rebuild_writes_expected_schema(tmp_path):
    raw = _build_tree(tmp_path, {
        'BTCUSDT': [
            (date(2026, 5, 14), 0.0001),
            (date(2026, 5, 15), 0.0002),
            (date(2026, 5, 16), 0.0003),
        ],
        'ETHUSDT': [
            (date(2026, 5, 15), 0.0010),
            (date(2026, 5, 16), 0.0011),
        ],
    })
    out = tmp_path / "premium.parquet"

    rc = conv.convert_all(raw, out)
    assert rc == 0

    df = pd.read_parquet(out)
    assert list(df.columns) == ['date', 'basis', 'instrument']
    assert len(df) == 5  # 3 BTC + 2 ETH
    # _PERP suffix on instrument column
    assert set(df['instrument'].unique()) == {'BTCUSDT_PERP', 'ETHUSDT_PERP'}
    # Sort matches the upstream invariant (instrument, date)
    assert df.equals(df.sort_values(['instrument', 'date']).reset_index(drop=True))
    # Dedup-key uniqueness
    assert not df.duplicated(subset=['date', 'instrument']).any()
    # Per-date basis values land in the right rows
    btc_5_16 = df.query("instrument == 'BTCUSDT_PERP' and date == @pd.Timestamp('2026-05-16')")
    assert btc_5_16['basis'].iloc[0] == pytest.approx(0.0003)


def test_incremental_falls_back_when_no_existing(tmp_path, caplog):
    raw = _build_tree(tmp_path, {
        'BTCUSDT': [(date(2026, 5, 16), 0.0003)],
    })
    out = tmp_path / "premium.parquet"
    assert not out.exists()

    with caplog.at_level('WARNING'):
        rc = conv.convert_all(raw, out, incremental=True)
    assert rc == 0
    assert out.exists()
    assert any('--incremental requested but' in rec.message for rec in caplog.records)


def test_incremental_skips_old_zips(tmp_path, monkeypatch):
    raw = _build_tree(tmp_path, {
        'BTCUSDT': [(date(2026, 5, d), 0.0001 * d) for d in range(1, 17)],  # 16 ZIPs
    })
    out = tmp_path / "premium.parquet"

    # Seed: full rebuild produces 16 rows for BTC.
    assert conv.convert_all(raw, out) == 0
    seeded = pd.read_parquet(out)
    assert len(seeded) == 16

    # Add a new ZIP for 2026-05-17 — only the trailing window should be read.
    _write_zip(raw / 'BTCUSDT', 'BTCUSDT', date(2026, 5, 17), 0.0017)

    # Spy: count which ZIPs `read_kline_csv_from_zip` is called on.
    read_paths = []
    original = conv.read_kline_csv_from_zip

    def spy(zip_path):
        read_paths.append(zip_path)
        return original(zip_path)

    monkeypatch.setattr(conv, 'read_kline_csv_from_zip', spy)
    assert conv.convert_all(raw, out, incremental=True, safety_days=7) == 0

    # max_date for BTCUSDT_PERP = 2026-05-16, safety_days = 7 → threshold = 2026-05-09.
    # Eligible ZIPs are those with date > 2026-05-09: 5-10..5-17 (8 ZIPs).
    read_dates = sorted(
        conv._zip_date(p) for p in read_paths if conv._zip_date(p)
    )
    assert read_dates == [date(2026, 5, d) for d in range(10, 18)]

    # Final parquet has 17 rows: 16 seeded + 1 new
    df = pd.read_parquet(out)
    assert len(df) == 17
    assert not df.duplicated(subset=['date', 'instrument']).any()


def test_incremental_dedup_overwrites_with_latest_value(tmp_path):
    raw = _build_tree(tmp_path, {
        'BTCUSDT': [(date(2026, 5, 15), 0.0001), (date(2026, 5, 16), 0.0002)],
    })
    out = tmp_path / "premium.parquet"

    assert conv.convert_all(raw, out) == 0
    seeded = pd.read_parquet(out)
    assert seeded.query("date == @pd.Timestamp('2026-05-16')")['basis'].iloc[0] == pytest.approx(0.0002)

    # Overwrite the 5-16 ZIP with a corrected value (0.0099). Incremental should
    # re-read it (within safety window) and the new value should win.
    _write_zip(raw / 'BTCUSDT', 'BTCUSDT', date(2026, 5, 16), 0.0099)

    assert conv.convert_all(raw, out, incremental=True, safety_days=7) == 0

    df = pd.read_parquet(out)
    assert not df.duplicated(subset=['date', 'instrument']).any()
    assert df.query("date == @pd.Timestamp('2026-05-16')")['basis'].iloc[0] == pytest.approx(0.0099)


def test_incremental_preserves_delisted_instrument(tmp_path):
    # Seed with two symbols.
    raw = _build_tree(tmp_path, {
        'BTCUSDT': [(date(2026, 5, 14), 0.0001), (date(2026, 5, 15), 0.0002)],
        'OLDUSDT': [(date(2026, 5, 14), 0.0050), (date(2026, 5, 15), 0.0051)],
    })
    out = tmp_path / "premium.parquet"

    assert conv.convert_all(raw, out) == 0
    assert len(pd.read_parquet(out)) == 4

    # Simulate OLDUSDT being delisted: dir gone from raw.
    import shutil
    shutil.rmtree(raw / 'OLDUSDT')

    # Add a new ZIP for BTC; run incremental.
    _write_zip(raw / 'BTCUSDT', 'BTCUSDT', date(2026, 5, 16), 0.0003)
    assert conv.convert_all(raw, out, incremental=True, safety_days=7) == 0

    df = pd.read_parquet(out)
    assert (df['instrument'] == 'OLDUSDT_PERP').sum() == 2
    assert (df['instrument'] == 'BTCUSDT_PERP').sum() == 3
    assert not df.duplicated(subset=['date', 'instrument']).any()


def test_incremental_reads_all_zips_for_new_instrument(tmp_path):
    raw = _build_tree(tmp_path, {
        'BTCUSDT': [(date(2026, 5, 15), 0.0002), (date(2026, 5, 16), 0.0003)],
    })
    out = tmp_path / "premium.parquet"

    assert conv.convert_all(raw, out) == 0

    # NEWUSDT lands with 10 days of history at once — none of it is in the
    # existing parquet, so all of it must be read.
    for d in range(1, 11):
        _write_zip(raw / 'NEWUSDT', 'NEWUSDT', date(2026, 5, d), 0.0010 + 0.0001 * d)

    assert conv.convert_all(raw, out, incremental=True, safety_days=7) == 0

    df = pd.read_parquet(out)
    assert (df['instrument'] == 'NEWUSDT_PERP').sum() == 10
    assert (df['instrument'] == 'BTCUSDT_PERP').sum() == 2


def test_incremental_matches_full_rebuild(tmp_path):
    """After seed + tail update, incremental output must match a from-scratch
    full rebuild on the same raw tree."""
    layout_seed = {
        'BTCUSDT': [(date(2026, 5, d), 0.0001 * d) for d in range(10, 16)],
        'ETHUSDT': [(date(2026, 5, d), 0.0002 * d) for d in range(12, 16)],
    }
    raw_inc = _build_tree(tmp_path / "inc", layout_seed)
    out_inc = tmp_path / "inc" / "premium.parquet"

    assert conv.convert_all(raw_inc, out_inc) == 0

    _write_zip(raw_inc / 'BTCUSDT', 'BTCUSDT', date(2026, 5, 16), 0.0016)
    _write_zip(raw_inc / 'ETHUSDT', 'ETHUSDT', date(2026, 5, 16), 0.0032)

    assert conv.convert_all(raw_inc, out_inc, incremental=True, safety_days=7) == 0

    full_layout = {sym: list(rows) for sym, rows in layout_seed.items()}
    full_layout['BTCUSDT'].append((date(2026, 5, 16), 0.0016))
    full_layout['ETHUSDT'].append((date(2026, 5, 16), 0.0032))
    raw_full = _build_tree(tmp_path / "full", full_layout)
    out_full = tmp_path / "full" / "premium.parquet"
    assert conv.convert_all(raw_full, out_full) == 0

    a = pd.read_parquet(out_inc).sort_values(['instrument', 'date']).reset_index(drop=True)
    b = pd.read_parquet(out_full).sort_values(['instrument', 'date']).reset_index(drop=True)
    pd.testing.assert_frame_equal(a, b)
