"""
Extract daily trading volume (USDT notional) from Binance Vision ZIPs + API cache
and write data/binance_volume_daily.parquet.

Reuses the existing load_binance_klines() pipeline from build_example_dataset.py.
Instrument names in the output use the _PERP suffix convention to match the rest
of the backtest stack (e.g. BTCUSDT_PERP).

Usage:
  python scripts/build_volume_dataset.py
  python scripts/build_volume_dataset.py --out data/binance_volume_daily.parquet
  python scripts/build_volume_dataset.py --data-dir data/raw/binance
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.build_example_dataset import load_binance_klines, get_binance_symbol

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("build_volume_dataset")


def _discover_instruments(data_dir: Path) -> list[str]:
    """Return instrument IDs (XUSDT_PERP form) for which Vision klines exist."""
    klines_dir = data_dir / "klines"
    if not klines_dir.exists():
        return []
    instruments = []
    for symbol_dir in sorted(klines_dir.iterdir()):
        if not symbol_dir.is_dir():
            continue
        if any(symbol_dir.glob("*.zip")):
            # Convert Binance symbol → internal instrument ID
            symbol = symbol_dir.name  # e.g. BTCUSDT
            instruments.append(symbol + "_PERP")
    return instruments


def build_volume_dataset(
    data_dir: Path = Path("data/raw/binance"),
    out_path: Path = Path("data/binance_volume_daily.parquet"),
) -> pd.DataFrame:
    instruments = _discover_instruments(data_dir)
    logger.info(f"Found {len(instruments)} instruments with Vision klines data")

    rows: list[pd.DataFrame] = []
    failed: list[str] = []

    for instrument in instruments:
        try:
            klines = load_binance_klines(
                instrument=instrument,
                data_dir=data_dir,
                include_api_cache=True,
            )
            if klines.empty or "quote_volume" not in klines.columns:
                logger.warning(f"{instrument}: no quote_volume column — skipping")
                failed.append(instrument)
                continue

            vol_df = klines[["date", "quote_volume"]].copy()
            vol_df = vol_df.dropna(subset=["quote_volume"])
            vol_df = vol_df[vol_df["quote_volume"] > 0]
            vol_df["instrument"] = instrument
            rows.append(vol_df)

            logger.info(
                f"{instrument}: {len(vol_df)} days, "
                f"{vol_df['date'].min().date()} → {vol_df['date'].max().date()}"
            )

        except Exception as exc:
            logger.warning(f"{instrument}: failed — {exc}")
            failed.append(instrument)

    if not rows:
        logger.error("No volume data extracted")
        return pd.DataFrame()

    df = pd.concat(rows, ignore_index=True)
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values(["instrument", "date"]).reset_index(drop=True)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out_path, index=False)
    logger.info(
        f"\nWrote {len(df)} rows, {df['instrument'].nunique()} instruments → {out_path}"
    )

    # Coverage summary
    coverage = (
        df.groupby("instrument")["date"]
        .agg(["min", "max", "count"])
        .rename(columns={"min": "first", "max": "last", "count": "days"})
    )
    logger.info(f"\nCoverage summary:\n{coverage.to_string()}")

    if failed:
        logger.warning(f"\nFailed instruments ({len(failed)}): {failed}")

    return df


def main() -> None:
    parser = argparse.ArgumentParser(description="Build daily volume dataset from klines")
    parser.add_argument("--data-dir", default="data/raw/binance", type=Path)
    parser.add_argument("--out", default="data/binance_volume_daily.parquet", type=Path)
    args = parser.parse_args()

    build_volume_dataset(data_dir=args.data_dir, out_path=args.out)


if __name__ == "__main__":
    main()
