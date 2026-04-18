"""
Google Trends adapter.

Fetch methods in priority order:
  1. pytrends (TrendReq) — 90-day trailing window, daily granularity
  2. manual_csv — any .csv files in trends_manual_dir are auto-ingested

Output: list of trends_panel dicts (one per instrument-date).

Graceful degradation: if pytrends fails or is rate-limited, falls back to
manual CSVs. If neither works, returns empty list (does not raise).
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import yaml

logger = logging.getLogger(__name__)

_TERMS_YAML = Path(__file__).parent / "trends_terms.yaml"
_PYTRENDS_DELAY = 1.0  # seconds between requests to avoid rate-limiting
_WINDOW_DAYS = 90


def _load_terms() -> dict[str, dict]:
    with open(_TERMS_YAML) as f:
        return yaml.safe_load(f).get("terms", {})


def _compute_derived(series: pd.Series) -> pd.Series:
    """Add z-score, 7d change, spike flag columns to a value series."""
    return series  # returned as-is; derived columns added in _rows_from_series


def _rows_from_series(
    instrument: str,
    term: str,
    series: pd.Series,
    fetch_method: str,
) -> list[dict]:
    if series is None or series.empty:
        return []

    series = series.sort_index()
    values = series.values.astype(float)

    # z-score vs trailing 63 days
    z63 = pd.Series(values, index=series.index).rolling(63, min_periods=10).apply(
        lambda x: (x[-1] - x[:-1].mean()) / (x[:-1].std() + 1e-8) if len(x) > 1 else 0.0,
        raw=True,
    )

    # 7d change
    chg7 = series.pct_change(periods=7)

    rows = []
    for date in series.index:
        raw_val = float(series.loc[date])
        z_val = float(z63.loc[date]) if date in z63.index else None
        c7_val = float(chg7.loc[date]) if date in chg7.index else None
        spike = bool(z_val is not None and z_val > 2.0)
        rows.append(
            {
                "date_utc": date.date() if hasattr(date, "date") else date,
                "instrument": instrument,
                "trends_term": term,
                "trends_value_raw": raw_val,
                "trends_value_scaled": raw_val / 100.0,
                "trends_z_63d": z_val,
                "trends_change_7d": c7_val,
                "trends_spike_flag": spike,
                "source_name": "google_trends",
                "fetch_method": fetch_method,
            }
        )
    return rows


def _fetch_pytrends(terms_map: dict[str, dict]) -> list[dict]:
    """Fetch using pytrends library."""
    try:
        from pytrends.request import TrendReq  # type: ignore
    except ImportError:
        logger.warning("pytrends not installed")
        return []

    rows: list[dict] = []
    end_date = datetime.now(timezone.utc)
    start_date = end_date - timedelta(days=_WINDOW_DAYS)
    timeframe = (
        f"{start_date.strftime('%Y-%m-%d')} {end_date.strftime('%Y-%m-%d')}"
    )

    pytrends = TrendReq(hl="en-US", tz=0, timeout=(10, 25))

    for instrument, cfg in terms_map.items():
        term = cfg.get("primary", "")
        if not term:
            continue
        try:
            pytrends.build_payload([term], cat=0, timeframe=timeframe, geo="", gprop="")
            df = pytrends.interest_over_time()
            if df.empty or term not in df.columns:
                logger.debug(f"pytrends: no data for '{term}'")
                continue
            series = df[term].astype(float)
            rows.extend(_rows_from_series(instrument, term, series, "pytrends"))
            logger.debug(f"pytrends: {len(series)} rows for {instrument} ({term})")
            time.sleep(_PYTRENDS_DELAY)
        except Exception as exc:
            logger.warning(f"pytrends failed for '{term}': {exc}")
            time.sleep(_PYTRENDS_DELAY * 2)

    return rows


def _fetch_manual_csvs(trends_manual_dir: Path) -> list[dict]:
    """
    Import any CSV files dropped into trends_manual_dir.

    Expected CSV format (exported from Google Trends UI):
      - Two-column: date, value (or multi-column with date as first column)
      - Date column can be named: 'Day', 'Week', 'Month', or 'date'
      - Filename pattern: {instrument}_{term}.csv  (e.g. BTCUSDT_PERP_bitcoin.csv)
        OR  {term}.csv  (instrument inferred from terms_terms.yaml)
    """
    terms_map = _load_terms()
    # Build reverse map: primary term → instrument
    term_to_instrument = {
        cfg["primary"]: instr for instr, cfg in terms_map.items()
    }

    rows: list[dict] = []
    for csv_path in sorted(trends_manual_dir.glob("*.csv")):
        try:
            # Try to infer instrument from filename
            stem = csv_path.stem.lower().replace("-", " ")
            instrument = None
            term = None

            # Try INSTRUMENT_term pattern
            for instr in terms_map:
                if stem.startswith(instr.lower()):
                    instrument = instr
                    term = stem[len(instr) + 1:].strip("_").strip()
                    break

            if not instrument:
                for t, instr in term_to_instrument.items():
                    if t.lower() in stem:
                        instrument = instr
                        term = t
                        break

            if not instrument:
                logger.warning(f"Could not map {csv_path.name} to an instrument — skipping")
                continue

            df = pd.read_csv(csv_path, skiprows=1)  # Google Trends CSVs have 1 header row
            df.columns = [c.strip() for c in df.columns]

            date_col = next(
                (c for c in df.columns if c.lower() in ("day", "week", "month", "date")),
                df.columns[0],
            )
            val_col = df.columns[1] if len(df.columns) > 1 else df.columns[0]

            df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
            df = df.dropna(subset=[date_col])
            df = df.set_index(date_col).sort_index()

            series = pd.to_numeric(df[val_col], errors="coerce").dropna()
            if series.empty:
                continue

            rows.extend(_rows_from_series(instrument, term or csv_path.stem, series, "manual_csv"))
            logger.info(f"Imported {len(series)} rows from {csv_path.name} → {instrument}")

        except Exception as exc:
            logger.warning(f"Failed to import {csv_path.name}: {exc}")

    return rows


def fetch_trends(
    trends_manual_dir: str | Path = "data/event_ingestion/trends_manual",
) -> list[dict]:
    """
    Fetch Google Trends data using pytrends (primary) or manual CSVs (fallback).
    Returns list of trends_panel dicts.
    """
    manual_dir = Path(trends_manual_dir)
    manual_dir.mkdir(parents=True, exist_ok=True)

    terms_map = _load_terms()
    rows: list[dict] = []

    # Try pytrends first
    try:
        pytrends_rows = _fetch_pytrends(terms_map)
        if pytrends_rows:
            rows.extend(pytrends_rows)
            logger.info(f"pytrends: {len(pytrends_rows)} rows fetched")
        else:
            logger.info("pytrends returned no data — will use manual CSVs if available")
    except Exception as exc:
        logger.warning(f"pytrends error: {exc}")

    # Always also ingest manual CSVs (supplements pytrends or replaces it)
    manual_rows = _fetch_manual_csvs(manual_dir)
    if manual_rows:
        rows.extend(manual_rows)
        logger.info(f"manual_csv: {len(manual_rows)} rows ingested")

    # Deduplicate on (instrument, date_utc, fetch_method), preferring pytrends over manual
    if rows:
        df = pd.DataFrame(rows)
        df["date_utc"] = pd.to_datetime(df["date_utc"]).dt.date
        df = df.sort_values(
            "fetch_method", key=lambda s: s.map({"pytrends": 0, "manual_csv": 1}).fillna(2)
        )
        df = df.drop_duplicates(subset=["instrument", "date_utc"], keep="first")
        rows = df.to_dict("records")

    return rows
