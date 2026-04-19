#!/usr/bin/env python3
"""
Download macro factor data (SPX, DXY, US 10Y yield) for use with
the residual_momentum trading rule family.

Fetches daily closes from 2019-01-01 to today via yfinance and saves
to data/macro_factors.parquet with columns: spx, dxy, us10y.

Usage:
    python scripts/download_macro_factors.py
    python scripts/download_macro_factors.py --output data/macro_factors.parquet
    python scripts/download_macro_factors.py --start 2018-01-01
"""

import argparse
import sys
from pathlib import Path
from datetime import date

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))


def download_macro_factors(
    start: str = "2019-01-01",
    output_path: str = "data/macro_factors.parquet",
) -> pd.DataFrame:
    """
    Download SPX, DXY, and US 10Y yield from Yahoo Finance.

    Args:
        start: Start date string (YYYY-MM-DD). Defaults to 2019-01-01 to provide
               warm-up data before the 2020 backtest start.
        output_path: Path to write parquet output.

    Returns:
        DataFrame with columns: spx, dxy, us10y.
    """
    try:
        import yfinance as yf
    except ImportError:
        print("ERROR: yfinance not installed. Run: pip install yfinance")
        sys.exit(1)

    end = date.today().strftime("%Y-%m-%d")
    print(f"Downloading macro factors: {start} → {end}")

    tickers = {
        "spx": "^GSPC",
        "dxy": "DX-Y.NYB",
        "us10y": "^TNX",
        "gold": "GC=F",
        "vix": "^VIX",
        "oil": "CL=F",
    }

    series_dict = {}
    for col, ticker in tickers.items():
        print(f"  Fetching {ticker} ({col})...")
        try:
            raw = yf.download(
                ticker,
                start=start,
                end=end,
                auto_adjust=True,
                progress=False,
            )
            if raw.empty:
                print(f"  WARNING: No data returned for {ticker}")
                series_dict[col] = pd.Series(dtype=float)
                continue

            # yfinance returns a MultiIndex column when multiple tickers are used,
            # but single-ticker download returns a flat DataFrame.
            close = raw["Close"]
            if isinstance(close, pd.DataFrame):
                close = close.iloc[:, 0]
            close.index = pd.to_datetime(close.index).tz_localize(None)
            close.name = col
            series_dict[col] = close
            print(f"    {len(close)} rows, {close.index[0].date()} → {close.index[-1].date()}")
        except Exception as e:
            print(f"  ERROR fetching {ticker}: {e}")
            series_dict[col] = pd.Series(dtype=float)

    # Combine into a single DataFrame on the union of all dates
    df = pd.DataFrame(series_dict)

    # Drop rows where ALL columns are NaN (weekends with no data at all)
    df = df.dropna(how="all")

    # Forward-fill individual NaN values (e.g., US holidays where DXY trades
    # but SPX is closed, or vice versa). At most 1–2 consecutive days.
    df = df.ffill()

    # Drop any remaining NaN rows (leading rows before data begins)
    df = df.dropna(how="any")

    print(f"\nMacro factor dataset: {len(df)} rows")
    print(f"  Date range: {df.index[0].date()} → {df.index[-1].date()}")
    print(f"  Columns: {list(df.columns)}")
    print(f"  NaN counts: {df.isna().sum().to_dict()}")

    # Write output
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out)
    print(f"\n✓ Written to {out}")

    return df


def main():
    parser = argparse.ArgumentParser(description="Download macro factors for residual momentum rule")
    parser.add_argument(
        "--start",
        default="2019-01-01",
        help="Start date YYYY-MM-DD (default: 2019-01-01)",
    )
    parser.add_argument(
        "--output",
        default="data/macro_factors.parquet",
        help="Output parquet path (default: data/macro_factors.parquet)",
    )
    args = parser.parse_args()

    df = download_macro_factors(start=args.start, output_path=args.output)

    # Basic sanity checks
    assert len(df) > 1000, f"Expected >1000 rows, got {len(df)}"
    assert set(df.columns) == {"spx", "dxy", "us10y", "gold", "vix", "oil"}, f"Unexpected columns: {df.columns.tolist()}"
    assert df.isna().sum().sum() == 0, "Unexpected NaN values in output"
    print("\n✓ Sanity checks passed")


if __name__ == "__main__":
    main()
