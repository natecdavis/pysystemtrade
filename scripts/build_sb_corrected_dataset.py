#!/usr/bin/env python3
"""
Build survivorship-bias-corrected dataset by merging the base dataset with
graveyard (delisted) instruments downloaded by download_graveyard_data.py.

The corrected dataset includes all instruments from the base dataset plus
any graveyard instruments with ≥90 days of price data. Graveyard instruments
have their price series ending at their delist date (with NaN for subsequent
dates in the jagged panel), so the backtest will:
  - Trade them while they were active (including through crashes)
  - Automatically exit positions at the last valid price (DELISTED state)

Usage:
    python scripts/build_sb_corrected_dataset.py
    python scripts/build_sb_corrected_dataset.py \\
        --base-dataset data/dataset_538registry_6yr_jagged.parquet \\
        --graveyard-dir data/raw/graveyard \\
        --output data/dataset_sb_corrected_6yr_jagged.parquet
    python scripts/build_sb_corrected_dataset.py --min-days 90 --dry-run
"""
from __future__ import annotations

import argparse
import io
import json
import logging
import sys
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

DEFAULT_BASE = REPO_ROOT / "data/dataset_538registry_6yr_jagged.parquet"
DEFAULT_GRAVEYARD = REPO_ROOT / "data/raw/graveyard"
DEFAULT_OUTPUT = REPO_ROOT / "data/dataset_sb_corrected_6yr_jagged.parquet"
MIN_DAYS_DEFAULT = 90

SPREAD_FRAC_DEFAULT = 0.00025   # same as all active instruments
TAKER_FEE_DEFAULT = 0.00045     # same as all active instruments
ADV_WINDOW = 30

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("build_sb_corrected")


# ---------------------------------------------------------------------------
# Klines / funding loader for graveyard symbols
# (Custom version without the min_price > 0.0001 gate, so LUNA's ~$0.00009
# final price is kept rather than raising a validation error.)
# ---------------------------------------------------------------------------

def _read_kline_zip(zip_path: Path) -> pd.DataFrame | None:
    """Read a single kline ZIP and return a DataFrame with close/volume/date."""
    try:
        with zipfile.ZipFile(zip_path) as zf:
            csv_name = zip_path.stem + ".csv"
            csv_files = zf.namelist()
            if csv_name not in csv_files:
                # Accept any single CSV
                csv_name_fallback = [f for f in csv_files if f.endswith(".csv")]
                if len(csv_name_fallback) != 1:
                    logger.warning(f"Unexpected ZIP contents in {zip_path.name}: {csv_files}")
                    return None
                csv_name = csv_name_fallback[0]

            with zf.open(csv_name) as f:
                raw = f.read()

            first_line = raw.split(b"\n")[0].decode("utf-8", errors="replace")
            has_header = any(c.isalpha() for c in first_line)

            if has_header:
                df = pd.read_csv(io.BytesIO(raw), header=0)
                # Normalize column names (case-insensitive)
                lc = {c.lower(): c for c in df.columns}
                close_col = lc.get("close") or lc.get("close price")
                time_col = lc.get("close_time") or lc.get("closetime")
                vol_col = lc.get("quote_volume") or lc.get("quotevolume") or lc.get("quote_asset_volume")
                if not all([close_col, time_col, vol_col]):
                    logger.warning(f"Missing required columns in {zip_path.name}: {list(df.columns)}")
                    return None
                df = df.rename(columns={close_col: "close", time_col: "close_time", vol_col: "quote_volume"})
            else:
                df = pd.read_csv(io.BytesIO(raw), header=None)
                if df.shape[1] < 8:
                    return None
                # Standard Binance klines: col 4=close, col 6=close_time, col 7=quote_volume
                df = df.iloc[:, [4, 6, 7]]
                df.columns = ["close", "close_time", "quote_volume"]

            df["date"] = pd.to_datetime(df["close_time"], unit="ms", utc=True).dt.tz_convert(None).dt.normalize()
            df["close"] = pd.to_numeric(df["close"], errors="coerce")
            df["quote_volume"] = pd.to_numeric(df["quote_volume"], errors="coerce")
            df = df[["date", "close", "quote_volume"]].dropna(subset=["close"])
            return df

    except (zipfile.BadZipFile, KeyError, Exception) as e:
        logger.warning(f"Failed to read {zip_path.name}: {e}")
        return None


def _read_funding_zip(zip_path: Path) -> pd.DataFrame | None:
    """Read a single funding rate ZIP and return a DataFrame with date/funding_rate."""
    try:
        with zipfile.ZipFile(zip_path) as zf:
            csv_name = zip_path.stem + ".csv"
            csv_files = zf.namelist()
            if csv_name not in csv_files:
                csv_name_fallback = [f for f in csv_files if f.endswith(".csv")]
                if len(csv_name_fallback) != 1:
                    return None
                csv_name = csv_name_fallback[0]

            with zf.open(csv_name) as f:
                raw = f.read()

            first_line = raw.split(b"\n")[0].decode("utf-8", errors="replace")
            has_header = any(c.isalpha() for c in first_line)

            if has_header:
                df = pd.read_csv(io.BytesIO(raw), header=0)
                lc = {c.lower(): c for c in df.columns}
                time_col = lc.get("calc_time") or lc.get("calctime") or lc.get("funding_time")
                rate_col = (
                    lc.get("last_funding_rate") or lc.get("funding_rate")
                    or lc.get("fundingrate") or lc.get("rate")
                )
                if not all([time_col, rate_col]):
                    return None
                df = df.rename(columns={time_col: "calcTime", rate_col: "fundingRate"})
            else:
                df = pd.read_csv(io.BytesIO(raw), header=None)
                if df.shape[1] < 2:
                    return None
                df = df.iloc[:, [0, -1]]
                df.columns = ["calcTime", "fundingRate"]

            df["calcTime"] = pd.to_datetime(df["calcTime"], unit="ms", utc=True).dt.tz_convert(None)
            df["fundingRate"] = pd.to_numeric(df["fundingRate"], errors="coerce")
            df = df.dropna(subset=["fundingRate"])
            return df[["calcTime", "fundingRate"]]

    except Exception as e:
        logger.warning(f"Failed to read funding {zip_path.name}: {e}")
        return None


def load_graveyard_klines(symbol: str, graveyard_dir: Path) -> pd.DataFrame | None:
    """Load all klines ZIPs for a graveyard symbol. No min_price gate."""
    klines_dir = graveyard_dir / "klines" / symbol
    if not klines_dir.exists():
        return None

    zips = sorted(klines_dir.glob(f"{symbol}-1d-*.zip"))
    if not zips:
        return None

    dfs = [_read_kline_zip(z) for z in zips]
    dfs = [d for d in dfs if d is not None and len(d) > 0]
    if not dfs:
        return None

    klines = pd.concat(dfs, ignore_index=True)
    klines = klines.sort_values("date").drop_duplicates(subset="date")
    klines = klines[klines["close"].notna()].copy()
    return klines


def load_graveyard_funding(symbol: str, graveyard_dir: Path) -> pd.DataFrame | None:
    """Load all funding rate ZIPs for a graveyard symbol, consolidated to daily."""
    funding_dir = graveyard_dir / "funding_rates" / symbol
    if not funding_dir.exists():
        return None

    zips = sorted(funding_dir.glob(f"{symbol}-fundingRate-*.zip"))
    if not zips:
        return None

    dfs = [_read_funding_zip(z) for z in zips]
    dfs = [d for d in dfs if d is not None and len(d) > 0]
    if not dfs:
        return None

    events = pd.concat(dfs, ignore_index=True)
    events["event_date"] = events["calcTime"].dt.date
    events["event_date"] = pd.to_datetime(events["event_date"])

    daily = events.groupby("event_date")["fundingRate"].sum().reset_index()
    daily = daily.rename(columns={"event_date": "date", "fundingRate": "funding_rate"})
    daily["date"] = pd.to_datetime(daily["date"], utc=True).dt.tz_convert(None)
    return daily.sort_values("date").reset_index(drop=True)


def build_graveyard_rows(
    symbol: str,
    graveyard_dir: Path,
    base_date_range: tuple[pd.Timestamp, pd.Timestamp],
    min_days: int = MIN_DAYS_DEFAULT,
) -> pd.DataFrame | None:
    """
    Build the long-format rows for a single graveyard symbol.

    Returns a DataFrame with columns matching the base dataset schema:
        date, instrument, close, funding_rate, adv_notional, spread_frac, taker_fee_frac

    Returns None if the symbol has insufficient data.
    """
    klines = load_graveyard_klines(symbol, graveyard_dir)
    if klines is None or len(klines) < min_days:
        logger.info(f"  {symbol}: insufficient klines data ({len(klines) if klines is not None else 0} days), skipping")
        return None

    # Restrict to base dataset's date range start (no point including data before backtest window)
    base_start, base_end = base_date_range
    klines = klines[(klines["date"] >= base_start) & (klines["date"] <= base_end)].copy()

    if len(klines) < min_days:
        logger.info(f"  {symbol}: only {len(klines)} days within backtest window, skipping")
        return None

    # ADV (30-day rolling mean of quote_volume)
    klines = klines.sort_values("date").reset_index(drop=True)
    klines["adv_notional"] = (
        klines.set_index("date")["quote_volume"]
        .rolling(window=ADV_WINDOW, min_periods=1)
        .mean()
        .values
    )

    # Funding rates
    funding = load_graveyard_funding(symbol, graveyard_dir)
    if funding is not None and len(funding) > 0:
        klines = klines.merge(funding, on="date", how="left")
    else:
        klines["funding_rate"] = np.nan

    klines["funding_rate"] = klines["funding_rate"].fillna(0.0)

    # Fixed metadata (same as all active instruments in the dataset)
    instrument_id = f"{symbol}_PERP"
    klines["instrument"] = instrument_id
    klines["spread_frac"] = SPREAD_FRAC_DEFAULT
    klines["taker_fee_frac"] = TAKER_FEE_DEFAULT

    # Select canonical columns
    result = klines[
        ["date", "instrument", "close", "funding_rate", "adv_notional", "spread_frac", "taker_fee_frac"]
    ].copy()

    last_price = result["close"].iloc[-1]
    last_date = result["date"].iloc[-1].date()
    logger.info(
        f"  {symbol}: {len(result)} days, "
        f"{result['date'].iloc[0].date()} → {last_date}, "
        f"last price=${last_price:.6g}"
    )
    return result


def merge_with_base(
    base_df: pd.DataFrame,
    graveyard_dfs: list[pd.DataFrame],
) -> pd.DataFrame:
    """
    Merge graveyard rows into the base dataset.

    The base dataset uses a jagged panel format (date union). Adding graveyard
    instruments extends it: the full date union is taken across all instruments,
    and each instrument only has rows for dates it was active (NaN elsewhere
    is handled by the parquet adapter).

    Returns the merged long-form DataFrame.
    """
    if not graveyard_dfs:
        return base_df

    all_dfs = [base_df] + graveyard_dfs
    merged = pd.concat(all_dfs, ignore_index=True)
    merged = merged.sort_values(["instrument", "date"]).reset_index(drop=True)

    # Verify no duplicate (instrument, date) pairs
    dupes = merged.duplicated(subset=["instrument", "date"])
    if dupes.any():
        n = dupes.sum()
        logger.warning(f"Dropping {n} duplicate (instrument, date) rows")
        merged = merged.drop_duplicates(subset=["instrument", "date"], keep="first")

    return merged


def main():
    parser = argparse.ArgumentParser(
        description="Build survivorship-bias-corrected dataset"
    )
    parser.add_argument(
        "--base-dataset",
        type=Path,
        default=DEFAULT_BASE,
        help=f"Base dataset parquet (default: {DEFAULT_BASE})",
    )
    parser.add_argument(
        "--graveyard-dir",
        type=Path,
        default=DEFAULT_GRAVEYARD,
        help=f"Graveyard data directory (default: {DEFAULT_GRAVEYARD})",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Output parquet path (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--min-days",
        type=int,
        default=MIN_DAYS_DEFAULT,
        help=f"Minimum days required for a graveyard symbol (default: {MIN_DAYS_DEFAULT})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be added without writing output",
    )
    args = parser.parse_args()

    # Load base dataset
    logger.info(f"Loading base dataset: {args.base_dataset}")
    base_df = pd.read_parquet(args.base_dataset)
    base_df["date"] = pd.to_datetime(base_df["date"])
    base_instruments = set(base_df["instrument"].unique())
    base_start = base_df["date"].min()
    base_end = base_df["date"].max()

    logger.info(
        f"Base dataset: {len(base_instruments)} instruments, "
        f"{base_df['date'].nunique()} dates, "
        f"{base_start.date()} → {base_end.date()}"
    )

    # Discover graveyard symbols
    graveyard_dir = args.graveyard_dir
    klines_root = graveyard_dir / "klines"
    if not klines_root.exists():
        logger.error(f"Graveyard klines directory not found: {klines_root}")
        logger.error("Run download_graveyard_data.py first.")
        sys.exit(1)

    graveyard_symbols = sorted(
        d.name for d in klines_root.iterdir() if d.is_dir()
    )
    logger.info(f"Found {len(graveyard_symbols)} graveyard symbols in {klines_root}")

    # Filter out symbols already in base dataset
    already_present = {
        sym for sym in graveyard_symbols if f"{sym}_PERP" in base_instruments
    }
    if already_present:
        logger.info(f"Skipping {len(already_present)} symbols already in base dataset: {sorted(already_present)}")
        graveyard_symbols = [s for s in graveyard_symbols if s not in already_present]

    # Process each graveyard symbol
    graveyard_dfs = []
    skipped = []
    for symbol in graveyard_symbols:
        rows = build_graveyard_rows(
            symbol, graveyard_dir, (base_start, base_end), args.min_days
        )
        if rows is not None:
            graveyard_dfs.append(rows)
        else:
            skipped.append(symbol)

    print(f"\n{'='*60}")
    print(f"GRAVEYARD SUMMARY")
    print(f"{'='*60}")
    print(f"Symbols discovered:  {len(graveyard_symbols) + len(already_present)}")
    print(f"Already in base:     {len(already_present)}")
    print(f"Qualifying (≥{args.min_days}d): {len(graveyard_dfs)}")
    print(f"Skipped (too few):   {len(skipped)}")

    if graveyard_dfs:
        print(f"\nGraveyard instruments to add:")
        for df in sorted(graveyard_dfs, key=lambda d: d["date"].max(), reverse=True):
            inst = df["instrument"].iloc[0]
            last_date = df["date"].max().date()
            last_price = df["close"].iloc[-1]
            n_days = len(df)
            print(f"  {inst:30s} last={last_date}  last_price=${last_price:.6g}  ({n_days}d)")

    if args.dry_run:
        print("\n[dry-run] Output not written.")
        return

    if not graveyard_dfs:
        logger.warning("No graveyard instruments qualified. Writing base dataset unchanged.")
        base_df.to_parquet(args.output, index=False)
        logger.info(f"Wrote (unchanged): {args.output}")
        return

    # Merge and write
    logger.info("Merging graveyard rows with base dataset...")
    merged = merge_with_base(base_df, graveyard_dfs)

    n_instruments = merged["instrument"].nunique()
    n_dates = merged["date"].nunique()
    n_added = n_instruments - len(base_instruments)

    logger.info(f"Merged: {n_instruments} instruments (+{n_added}), {n_dates} dates")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    merged.to_parquet(args.output, index=False)

    print(f"\nOutput: {args.output}")
    print(f"  Base instruments:       {len(base_instruments)}")
    print(f"  Graveyard added:        {n_added}")
    print(f"  Total instruments:      {n_instruments}")
    print(f"  Total rows:             {len(merged):,}")

    # Verify: spot-check known crashed tokens
    graveyard_insts = [df["instrument"].iloc[0] for df in graveyard_dfs]
    known_crashed = ["LUNAUSDT_PERP", "FTTUSDT_PERP"]
    for inst in known_crashed:
        if inst in graveyard_insts:
            inst_rows = merged[merged["instrument"] == inst]
            last_price = inst_rows["close"].dropna().iloc[-1]
            last_date = inst_rows["date"].max().date()
            print(f"  ✓ {inst}: last_price=${last_price:.6g} on {last_date}")


if __name__ == "__main__":
    main()
