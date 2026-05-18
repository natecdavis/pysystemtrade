#!/usr/bin/env python3
"""
Convert downloaded Binance premium-index daily kline ZIPs to a unified parquet panel.

Reads `{input_dir}/{SYMBOL}/{SYMBOL}-1d-{DATE}.zip` files (one row per day each)
and produces a single parquet with schema:

    date (datetime64), instrument (str — perp code with _PERP suffix), basis (float)

`basis` is the day's `close` of the premium-index kline = (mark - index) / index
at end-of-day UTC. Positive = mark trading above spot (longs paying funding);
negative = mark below spot (shorts paying).

Used by the C2c basis_mr_5 rule via parquet_perps_sim_data.get_premium_index().

Usage:
    python scripts/convert_premium_index_to_parquet.py \\
        --input-dir envs/dev/data/binance_premium_index_raw \\
        --output envs/dev/data/binance_premium_index_processed.parquet
"""

from __future__ import annotations

import argparse
import io
import logging
import re
import sys
import zipfile
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
from tqdm import tqdm

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


# Vision daily premium-index ZIPs are named {SYMBOL}-1d-{YYYY-MM-DD}.zip
_ZIP_DATE_RE = re.compile(r"-1d-(\d{4}-\d{2}-\d{2})\.zip$")


def symbol_to_instrument(symbol: str) -> str:
    """Convert raw Binance symbol (BTCUSDT) to perp instrument code (BTCUSDT_PERP)."""
    if symbol.endswith("_PERP"):
        return symbol
    return f"{symbol}_PERP"


def _zip_date(zip_path: Path) -> Optional[date]:
    """Parse the YYYY-MM-DD date out of a Vision daily kline ZIP filename."""
    match = _ZIP_DATE_RE.search(zip_path.name)
    if match is None:
        return None
    try:
        return date.fromisoformat(match.group(1))
    except ValueError:
        return None


def _load_existing_max_dates(output_path: Path) -> Dict[str, pd.Timestamp]:
    """Return {instrument: max_date} from the existing parquet, or {}."""
    if not output_path.exists():
        return {}
    existing = pd.read_parquet(output_path, columns=["date", "instrument"])
    existing["date"] = pd.to_datetime(existing["date"]).dt.normalize()
    return existing.groupby("instrument")["date"].max().to_dict()


def read_kline_csv_from_zip(zip_path: Path) -> Optional[pd.DataFrame]:
    """Extract the single CSV inside a Vision daily zip."""
    try:
        with zipfile.ZipFile(zip_path) as z:
            csv_name = next((n for n in z.namelist() if n.endswith(".csv")), None)
            if csv_name is None:
                return None
            with z.open(csv_name) as f:
                # Vision recently started prepending a header row; older files
                # are header-less. Detect by sniffing the first byte.
                content = f.read()
                first_line = content.split(b"\n", 1)[0].decode(errors="ignore")
                has_header = "open_time" in first_line
                df = pd.read_csv(
                    io.BytesIO(content),
                    header=0 if has_header else None,
                    names=None if has_header else [
                        "open_time", "open", "high", "low", "close", "volume",
                        "close_time", "quote_volume", "count", "taker_buy_volume",
                        "taker_buy_quote_volume", "ignore",
                    ],
                )
                return df
    except (zipfile.BadZipFile, FileNotFoundError, KeyError) as e:
        logger.debug(f"Skip {zip_path}: {e}")
        return None


def process_symbol(
    symbol_dir: Path,
    since: Optional[date] = None,
) -> Optional[pd.DataFrame]:
    """Read daily zips for one symbol, return a DataFrame with date, basis.

    Args:
        symbol_dir: Path to symbol's ZIP directory.
        since: If provided, only ZIPs whose filename-date is strictly greater
            than `since` are read. When None, every ZIP is read (full rebuild).

    Returns:
        DataFrame with [date, basis, instrument], or None if no eligible ZIPs
        produced valid rows.
    """
    symbol = symbol_dir.name
    all_zips = sorted(symbol_dir.glob(f"{symbol}-1d-*.zip"))
    if not all_zips:
        return None

    if since is None:
        zips = all_zips
    else:
        zips = []
        for zpath in all_zips:
            zdate = _zip_date(zpath)
            if zdate is None or zdate > since:
                zips.append(zpath)
        if not zips:
            return None

    rows = []
    for z in zips:
        df = read_kline_csv_from_zip(z)
        if df is None or df.empty:
            continue
        # The "close" column = end-of-day premium index value.
        for _, r in df.iterrows():
            try:
                # open_time is ms-epoch; date = midnight UTC of that day.
                ts = pd.to_datetime(int(r["open_time"]), unit="ms", utc=True).normalize().tz_localize(None)
                basis = float(r["close"])
                rows.append((ts, basis))
            except (ValueError, KeyError):
                continue
    if not rows:
        return None
    df = pd.DataFrame(rows, columns=["date", "basis"])
    df["instrument"] = symbol_to_instrument(symbol)
    df = df.drop_duplicates(subset=["date", "instrument"], keep="last")
    return df


def _merge_with_existing(new_df: pd.DataFrame, output_path: Path) -> pd.DataFrame:
    """Concat new rows with the existing parquet and dedup on (date, instrument).

    Newer rows win on overlap (`keep='last'`), so a re-read ZIP that produces
    a corrected value supersedes whatever was in the previous parquet. Mirrors
    the OI converter's incremental merge pattern.
    """
    existing = pd.read_parquet(output_path)
    existing["date"] = pd.to_datetime(existing["date"]).dt.normalize()

    if new_df.empty:
        return existing.sort_values(["instrument", "date"]).reset_index(drop=True)

    new_df = new_df.copy()
    new_df["date"] = pd.to_datetime(new_df["date"]).dt.normalize()

    combined = pd.concat([existing, new_df], ignore_index=True)
    combined = (
        combined
        .drop_duplicates(subset=["date", "instrument"], keep="last")
        .sort_values(["instrument", "date"])
        .reset_index(drop=True)
    )
    return combined


def convert_all(
    input_dir: Path,
    output_path: Path,
    dry_run: bool = False,
    incremental: bool = False,
    safety_days: int = 7,
) -> int:
    symbol_dirs = sorted(p for p in input_dir.iterdir() if p.is_dir())
    if not symbol_dirs:
        logger.error(f"No symbol directories in {input_dir}")
        return 1

    do_incremental = incremental and output_path.exists()
    if incremental and not output_path.exists():
        logger.warning(
            f"--incremental requested but {output_path} does not exist — "
            "falling back to full rebuild"
        )

    max_dates: Dict[str, pd.Timestamp] = {}
    if do_incremental:
        max_dates = _load_existing_max_dates(output_path)
        logger.info(
            f"Incremental mode: existing parquet has {len(max_dates)} instruments; "
            f"safety_days={safety_days}"
        )

    mode = "incremental" if do_incremental else "full"
    logger.info(f"Converting {len(symbol_dirs)} symbols from {input_dir} ({mode})...")
    frames: List[pd.DataFrame] = []
    n_with_data = 0
    for symdir in tqdm(symbol_dirs, desc="Converting"):
        since: Optional[date] = None
        if do_incremental:
            instrument_key = symbol_to_instrument(symdir.name)
            sym_max = max_dates.get(instrument_key)
            if sym_max is not None:
                since = (
                    pd.Timestamp(sym_max).normalize().date()
                    - timedelta(days=safety_days)
                )
        df = process_symbol(symdir, since=since)
        if df is not None and not df.empty:
            frames.append(df)
            n_with_data += 1

    if not frames and not do_incremental:
        logger.error("No usable basis data found.")
        return 1

    if frames:
        new_df = pd.concat(frames, ignore_index=True)
    else:
        new_df = pd.DataFrame(columns=["date", "basis", "instrument"])

    if do_incremental:
        combined = _merge_with_existing(new_df, output_path)
        new_rows = len(new_df)
        existing_rows = len(combined) - len(new_df) + new_df.duplicated(
            subset=["date", "instrument"]
        ).sum()
    else:
        combined = new_df.sort_values(["instrument", "date"]).reset_index(drop=True)
        new_rows = len(new_df)
        existing_rows = 0

    logger.info("=" * 60)
    logger.info(f"Mode:                     {mode}")
    logger.info(f"Symbols processed:        {len(symbol_dirs):,}")
    logger.info(f"Symbols with data:        {n_with_data:,}")
    if do_incremental:
        logger.info(f"New / re-read rows:       {new_rows:,}")
    logger.info(f"Total rows:               {len(combined):,}")
    if len(combined):
        logger.info(f"Date range:               {combined['date'].min().date()} → {combined['date'].max().date()}")
        logger.info(f"Basis range:              {combined['basis'].min():.6f} → {combined['basis'].max():.6f}")
        logger.info(f"Median absolute basis:    {combined['basis'].abs().median():.6f}")
    logger.info("=" * 60)

    if dry_run:
        logger.info("--dry-run: not writing parquet.")
        return 0

    output_path.parent.mkdir(parents=True, exist_ok=True)
    # Atomic write
    import os, tempfile
    fd, tmp = tempfile.mkstemp(prefix=f".{output_path.name}.", suffix=".tmp", dir=str(output_path.parent))
    os.close(fd)
    combined.to_parquet(tmp, index=False)
    os.replace(tmp, str(output_path))
    logger.info(f"✓ Written to {output_path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--incremental",
        action="store_true",
        help=(
            "Update tail only: read just ZIPs newer than "
            "(per-instrument max date in existing parquet) - safety-days, then "
            "merge into the existing parquet. Falls back to full rebuild if "
            "the output parquet does not yet exist."
        ),
    )
    parser.add_argument(
        "--safety-days",
        type=int,
        default=7,
        help=(
            "Incremental mode: re-read this many days behind each instrument's "
            "max date to cover late-arriving / corrected ZIPs (default: 7)."
        ),
    )
    args = parser.parse_args()
    return convert_all(
        args.input_dir,
        args.output,
        dry_run=args.dry_run,
        incremental=args.incremental,
        safety_days=args.safety_days,
    )


if __name__ == "__main__":
    sys.exit(main())
