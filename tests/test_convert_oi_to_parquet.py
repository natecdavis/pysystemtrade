"""
Tests for `scripts/convert_oi_to_parquet.py`, including the incremental path.

Synthetic fixture: a fake `binance_oi_raw/` tree with 2-3 symbol subdirs, each
holding a few daily ZIPs of in-memory CSV data. The converter aggregates the
5-min rows in each ZIP to one daily row, so 1 ZIP → 1 row in the output parquet
for the date encoded in the filename.

Covers:
- Full-rebuild path is unchanged (regression guard for the refactor).
- `--incremental` with no existing parquet falls back to full rebuild + warns.
- Incremental filters ZIPs by per-symbol max date - safety_days; a `read_csv_from_zip`
  spy confirms the old ZIPs are never opened.
- Dedup-with-overlap: a re-read ZIP whose date already exists in the parquet
  overwrites the row (`keep='last'`).
- Delisted-symbol behavior: a symbol present in the existing parquet but with
  no new ZIPs survives the merge.
- New-symbol behavior: a symbol absent from the existing parquet has all its
  ZIPs read.
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

from convert_oi_to_parquet import OIDataConverter  # noqa: E402


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _make_csv_bytes(zdate: date, oi: float, lsr: float, tt_lsr: float) -> bytes:
    """One ZIP holds a single CSV with a few 5-min rows; the converter takes
    the LAST row per date, so only the value at the latest timestamp matters."""
    rows = []
    for hour in (0, 12, 23):
        for minute in (0, 30):
            ts = pd.Timestamp(zdate) + pd.Timedelta(hours=hour, minutes=minute)
            # Earlier rows hold sentinel -1 so a buggy aggregation would surface.
            is_last = (hour == 23 and minute == 30)
            rows.append({
                'create_time': ts.strftime('%Y-%m-%d %H:%M:%S'),
                'sum_open_interest_value': oi if is_last else -1.0,
                'sum_taker_long_short_vol_ratio': lsr if is_last else -1.0,
                'sum_toptrader_long_short_ratio': tt_lsr if is_last else -1.0,
            })
    df = pd.DataFrame(rows)
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    return buf.getvalue().encode()


def _write_zip(symbol_dir: Path, symbol: str, zdate: date,
               oi: float, lsr: float = 1.0, tt_lsr: float = 1.5) -> Path:
    symbol_dir.mkdir(parents=True, exist_ok=True)
    zpath = symbol_dir / f"{symbol}-metrics-{zdate.isoformat()}.zip"
    csv_bytes = _make_csv_bytes(zdate, oi, lsr, tt_lsr)
    with zipfile.ZipFile(zpath, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{symbol}-metrics-{zdate.isoformat()}.csv", csv_bytes)
    return zpath


def _build_tree(root: Path, layout: dict) -> Path:
    """layout = {symbol: [(zdate, oi), ...]}.  Returns the root."""
    raw = root / "binance_oi_raw"
    for symbol, rows in layout.items():
        sdir = raw / symbol
        for entry in rows:
            zdate, oi = entry[0], entry[1]
            lsr = entry[2] if len(entry) > 2 else 1.0
            tt_lsr = entry[3] if len(entry) > 3 else 1.5
            _write_zip(sdir, symbol, zdate, oi, lsr, tt_lsr)
    return raw


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_full_rebuild_writes_expected_schema(tmp_path):
    raw = _build_tree(tmp_path, {
        'BTCUSDT': [
            (date(2026, 5, 14), 100.0),
            (date(2026, 5, 15), 101.0),
            (date(2026, 5, 16), 102.0),
        ],
        'ETHUSDT': [
            (date(2026, 5, 15), 200.0),
            (date(2026, 5, 16), 201.0),
        ],
    })
    out = tmp_path / "oi.parquet"

    OIDataConverter(str(raw), str(out)).run()

    df = pd.read_parquet(out)
    assert list(df.columns) == [
        'date', 'instrument', 'open_interest',
        'long_short_ratio', 'toptrader_long_short_ratio',
    ]
    assert len(df) == 5  # 3 BTC + 2 ETH
    # Sort matches the upstream invariant downstream consumers rely on
    assert df.equals(df.sort_values(['date', 'instrument']).reset_index(drop=True))
    # Dedup-key uniqueness — required by parquet_perps_sim_data unstack()
    assert not df.duplicated(subset=['date', 'instrument']).any()
    # Aggregation took the last 5-min row's value (not the -1 sentinel)
    btc_2026_05_16 = df.query("instrument == 'BTCUSDT' and date == @pd.Timestamp('2026-05-16')")
    assert btc_2026_05_16['open_interest'].iloc[0] == 102.0


def test_incremental_falls_back_when_no_existing(tmp_path, caplog):
    raw = _build_tree(tmp_path, {
        'BTCUSDT': [(date(2026, 5, 16), 102.0)],
    })
    out = tmp_path / "oi.parquet"
    assert not out.exists()

    converter = OIDataConverter(str(raw), str(out), incremental=True)
    with caplog.at_level('WARNING'):
        converter.run()

    assert out.exists()
    assert converter.stats['mode'] == 'full'
    assert any('--incremental requested but' in rec.message for rec in caplog.records)


def test_incremental_skips_old_zips(tmp_path):
    raw = _build_tree(tmp_path, {
        'BTCUSDT': [(date(2026, 5, d), 100.0 + d) for d in range(1, 17)],  # 16 ZIPs
    })
    out = tmp_path / "oi.parquet"

    # Seed: full rebuild produces 16 rows for BTC.
    OIDataConverter(str(raw), str(out)).run()
    seeded = pd.read_parquet(out)
    assert len(seeded) == 16

    # Now add one new ZIP for 2026-05-17 and a no-op ZIP for an old date that
    # should NOT be re-read.
    _write_zip(raw / 'BTCUSDT', 'BTCUSDT', date(2026, 5, 17), 117.0)

    # Spy on read_csv_from_zip: only the trailing window should be read.
    converter = OIDataConverter(str(raw), str(out), incremental=True, safety_days=7)
    read_paths = []
    original = converter.read_csv_from_zip

    def spy(zip_path):
        read_paths.append(zip_path)
        return original(zip_path)

    converter.read_csv_from_zip = spy
    converter.run()

    # max_date = 2026-05-16, safety_days = 7 → threshold = 2026-05-09.
    # Eligible ZIPs are those with date > 2026-05-09: 5-10, 5-11, ..., 5-17 (8 ZIPs).
    read_dates = sorted(
        OIDataConverter._zip_date(p) for p in read_paths if OIDataConverter._zip_date(p)
    )
    assert read_dates == [date(2026, 5, d) for d in range(10, 18)]
    assert converter.stats['zips_skipped_incremental'] == 9  # 5-1 .. 5-9

    # Final parquet has 17 rows: 16 seeded + 1 new
    df = pd.read_parquet(out)
    assert len(df) == 17
    assert not df.duplicated(subset=['date', 'instrument']).any()


def test_incremental_dedup_overwrites_with_latest_value(tmp_path):
    raw = _build_tree(tmp_path, {
        'BTCUSDT': [(date(2026, 5, 15), 100.0), (date(2026, 5, 16), 101.0)],
    })
    out = tmp_path / "oi.parquet"

    OIDataConverter(str(raw), str(out)).run()
    seeded = pd.read_parquet(out)
    assert seeded.query("date == @pd.Timestamp('2026-05-16')")['open_interest'].iloc[0] == 101.0

    # Overwrite the 5-16 ZIP with a CORRECTED value (102.0). Incremental should
    # re-read it (within safety window) and the new value should win.
    _write_zip(raw / 'BTCUSDT', 'BTCUSDT', date(2026, 5, 16), 102.0)

    OIDataConverter(str(raw), str(out), incremental=True, safety_days=7).run()

    df = pd.read_parquet(out)
    # Still no dupes
    assert not df.duplicated(subset=['date', 'instrument']).any()
    # And the corrected value won
    assert df.query("date == @pd.Timestamp('2026-05-16')")['open_interest'].iloc[0] == 102.0


def test_incremental_preserves_delisted_symbol(tmp_path):
    # Seed with two symbols.
    raw = _build_tree(tmp_path, {
        'BTCUSDT': [(date(2026, 5, 14), 100.0), (date(2026, 5, 15), 101.0)],
        'OLDUSDT': [(date(2026, 5, 14), 50.0), (date(2026, 5, 15), 51.0)],
    })
    out = tmp_path / "oi.parquet"

    OIDataConverter(str(raw), str(out)).run()
    assert len(pd.read_parquet(out)) == 4

    # Simulate OLDUSDT being delisted: its dir is gone from raw (Vision pruned).
    import shutil
    shutil.rmtree(raw / 'OLDUSDT')

    # Add a new ZIP for BTC; run incremental.
    _write_zip(raw / 'BTCUSDT', 'BTCUSDT', date(2026, 5, 16), 102.0)
    OIDataConverter(str(raw), str(out), incremental=True, safety_days=7).run()

    df = pd.read_parquet(out)
    # OLDUSDT's 2 rows should still be there.
    assert (df['instrument'] == 'OLDUSDT').sum() == 2
    # BTC should have 3 rows (2 seeded + 1 new).
    assert (df['instrument'] == 'BTCUSDT').sum() == 3
    assert not df.duplicated(subset=['date', 'instrument']).any()


def test_incremental_reads_all_zips_for_new_symbol(tmp_path):
    raw = _build_tree(tmp_path, {
        'BTCUSDT': [(date(2026, 5, 15), 101.0), (date(2026, 5, 16), 102.0)],
    })
    out = tmp_path / "oi.parquet"

    OIDataConverter(str(raw), str(out)).run()

    # NEWUSDT lands with several months of history at once — none of it is
    # in the existing parquet, so all of it must be read.
    for d in range(1, 11):
        _write_zip(raw / 'NEWUSDT', 'NEWUSDT', date(2026, 5, d), 10.0 + d)

    converter = OIDataConverter(str(raw), str(out), incremental=True, safety_days=7)
    converter.run()

    df = pd.read_parquet(out)
    assert (df['instrument'] == 'NEWUSDT').sum() == 10
    assert (df['instrument'] == 'BTCUSDT').sum() == 2


def test_incremental_matches_full_rebuild_byte_for_byte(tmp_path):
    """End-to-end equivalence: after seed + tail update, incremental output must
    match what a from-scratch full rebuild would produce on the same raw tree."""
    layout_seed = {
        'BTCUSDT': [(date(2026, 5, d), 100.0 + d) for d in range(10, 16)],
        'ETHUSDT': [(date(2026, 5, d), 200.0 + d) for d in range(12, 16)],
    }
    raw_inc = _build_tree(tmp_path / "inc", layout_seed)
    out_inc = tmp_path / "inc" / "oi.parquet"

    # Seed = full rebuild on the seed layout.
    OIDataConverter(str(raw_inc), str(out_inc)).run()

    # Add a trailing day to both symbols.
    _write_zip(raw_inc / 'BTCUSDT', 'BTCUSDT', date(2026, 5, 16), 116.0)
    _write_zip(raw_inc / 'ETHUSDT', 'ETHUSDT', date(2026, 5, 16), 216.0)

    # Run incremental.
    OIDataConverter(str(raw_inc), str(out_inc), incremental=True, safety_days=7).run()

    # Build the same final layout from scratch in a separate tree and full-rebuild.
    full_layout = {sym: list(rows) for sym, rows in layout_seed.items()}
    full_layout['BTCUSDT'].append((date(2026, 5, 16), 116.0))
    full_layout['ETHUSDT'].append((date(2026, 5, 16), 216.0))
    raw_full = _build_tree(tmp_path / "full", full_layout)
    out_full = tmp_path / "full" / "oi.parquet"
    OIDataConverter(str(raw_full), str(out_full)).run()

    a = pd.read_parquet(out_inc).sort_values(['date', 'instrument']).reset_index(drop=True)
    b = pd.read_parquet(out_full).sort_values(['date', 'instrument']).reset_index(drop=True)
    pd.testing.assert_frame_equal(a, b)


# ---------------------------------------------------------------------------
# Regression: mixed-format `create_time` (date-only rows on certain Vision days)
# ---------------------------------------------------------------------------

def _make_date_only_csv_bytes(zdate: date, oi: float,
                              lsr: float = 1.0, tt_lsr: float = 1.5) -> bytes:
    """Vision file with a single row whose `create_time` is just the date string
    (no time component). Empirically observed for ICPUSDT 2022-10-30 and a few
    TLMUSDT days. Pre-fix the converter dropped these rows because pandas'
    strict format inference saw a mix of `YYYY-MM-DD` and `YYYY-MM-DD HH:MM:SS`
    and errored."""
    rows = [{
        'create_time': zdate.isoformat(),   # date-only, the toxic case
        'sum_open_interest_value': oi,
        'sum_taker_long_short_vol_ratio': lsr,
        'sum_toptrader_long_short_ratio': tt_lsr,
    }]
    df = pd.DataFrame(rows)
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    return buf.getvalue().encode()


def _write_date_only_zip(symbol_dir: Path, symbol: str, zdate: date, oi: float) -> Path:
    symbol_dir.mkdir(parents=True, exist_ok=True)
    zpath = symbol_dir / f"{symbol}-metrics-{zdate.isoformat()}.zip"
    with zipfile.ZipFile(zpath, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{symbol}-metrics-{zdate.isoformat()}.csv",
                    _make_date_only_csv_bytes(zdate, oi))
    return zpath


def test_mixed_create_time_formats_parse_cleanly(tmp_path):
    """ICPUSDT/TLMUSDT regression: a symbol with a mix of normal
    `YYYY-MM-DD HH:MM:SS` rows AND a few date-only `YYYY-MM-DD` rows must
    produce one row per ZIP date, not fail or silently drop the date-only
    days. Pre-fix this errored: `time data "2022-10-30" doesn't match format
    "%Y-%m-%d %H:%M:%S"`.
    """
    raw = tmp_path / "binance_oi_raw"
    icp_dir = raw / "ICPUSDT"

    # 2 days of normal data
    _write_zip(icp_dir, 'ICPUSDT', date(2022, 6, 1), 100.0)
    _write_zip(icp_dir, 'ICPUSDT', date(2022, 6, 2), 101.0)
    # 1 day of the toxic date-only format
    _write_date_only_zip(icp_dir, 'ICPUSDT', date(2022, 10, 30), 102.0)
    # And another normal day after
    _write_zip(icp_dir, 'ICPUSDT', date(2022, 11, 1), 103.0)

    out = tmp_path / "oi.parquet"
    OIDataConverter(str(raw), str(out)).run()

    df = pd.read_parquet(out)
    assert len(df) == 4, (
        f"Expected 4 ICPUSDT rows including the date-only 2022-10-30 day; "
        f"got {len(df)}. Dates present: {df['date'].tolist()}"
    )
    assert pd.Timestamp("2022-10-30") in df['date'].tolist(), (
        "The date-only 2022-10-30 row was dropped — partial-format regression."
    )
    # Value from the date-only row preserved
    row = df.query("date == @pd.Timestamp('2022-10-30')").iloc[0]
    assert row['open_interest'] == 102.0


def test_date_only_only_file_does_not_crash(tmp_path):
    """A symbol where the *only* ZIP is the date-only-format flavor must still
    parse without an exception. Belt-and-suspenders for the pure-edge-case."""
    raw = tmp_path / "binance_oi_raw"
    _write_date_only_zip(raw / "TLMUSDT", 'TLMUSDT', date(2022, 6, 14), 50.0)

    out = tmp_path / "oi.parquet"
    OIDataConverter(str(raw), str(out)).run()
    df = pd.read_parquet(out)
    assert len(df) == 1
    assert df.iloc[0]['instrument'] == 'TLMUSDT'
    assert df.iloc[0]['open_interest'] == 50.0
