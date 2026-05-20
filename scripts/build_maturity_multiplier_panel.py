#!/usr/bin/env python3
"""
Build the token-maturity multiplier panel for B7 walk-forward experiments.

Formula:
    days_since_listing(i, t) = max(0, (t − launch_date[i]).days)
    penalty(i, t)            = max(0, (T − days_since_listing(i, t)) / T)   ∈ [0, 1]
    multiplier(i, t)         = 1 − β × penalty(i, t)                        ∈ [1−β, 1]

Single canonical parameterisation: β = 0.5, T = 365.

`launch_date` is read from the parquet's derived lifecycle (Method A: first
non-NaN close date), via `sysdata/crypto/lifecycle.py:derive_lifecycle_from_data`.
This is the SAME helper that populates `parquetCryptoPerpsSimData._lifecycle_df`
at init time — we just call it directly without spinning up the whole sim-data class.

Pre-launch cells (t < launch_date[i]) are NaN so the harness's `.fillna(1.0)` at
`systems/crypto_perps/forecast_combine_gated.py:179` yields identity for any
instrument that doesn't exist yet on that date.

The panel includes ALL instruments from `_prices_df.columns` (even fully-mature
ones at multiplier=1.0) so the consumer never silently hits the "instrument not
in panel" branch at `forecast_combine_gated.py:174` (which short-circuits and
returns the forecast unchanged, indistinguishable from explicit-1.0 in logs).

Usage:
    python scripts/build_maturity_multiplier_panel.py
    python scripts/build_maturity_multiplier_panel.py --beta 0.5 --threshold 365 \
        --output data/research/maturity_multiplier_b50_t365.parquet
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from sysdata.crypto.prices import load_crypto_perps_panel


def build_panel(
    prices_df: pd.DataFrame,
    lifecycle_df: pd.DataFrame,
    beta: float = 0.5,
    threshold_days: int = 365,
) -> pd.DataFrame:
    """Build the maturity multiplier panel.

    Args:
        prices_df: wide DataFrame (dates × instruments) — defines the output grid.
        lifecycle_df: index=instrument, must contain `launch_date` column.
        beta: max penalty at age 0 (β=0.5 → newly-listed tokens get position halved).
        threshold_days: age at which the penalty fully phases out (multiplier returns to 1.0).

    Returns:
        DataFrame indexed by prices_df.index, columned by prices_df.columns. Values are
        the multiplier ∈ [1-β, 1] for cells at or after launch_date, NaN before.
    """
    instruments = list(prices_df.columns)
    dates = prices_df.index

    # Launch_date series aligned to prices_df.columns. Instruments with no entry
    # in lifecycle_df (e.g., entirely-NaN columns) get NaT → all cells NaN.
    launch_series = (
        lifecycle_df.reindex(instruments)["launch_date"]
        if "launch_date" in lifecycle_df.columns
        else pd.Series(pd.NaT, index=instruments)
    )

    # Vectorised days-since-listing computation:
    # date_grid[t, i] = dates[t]; launch_grid[t, i] = launch_series[i]
    date_grid = np.tile(dates.values, (len(instruments), 1)).T  # (T, N) datetime64
    launch_grid = np.tile(launch_series.values, (len(dates), 1))  # (T, N) datetime64

    # delta in days (float64 so we can have NaN where launch is NaT)
    delta_days = (date_grid - launch_grid).astype("timedelta64[D]").astype("float64")

    # Pre-launch cells stay NaN (delta_days will be negative there OR NaN if launch is NaT)
    pre_launch = (delta_days < 0) | np.isnan(delta_days)
    days_since = np.where(pre_launch, np.nan, delta_days)

    penalty = np.clip((threshold_days - days_since) / threshold_days, 0.0, 1.0)
    mult = 1.0 - beta * penalty

    return pd.DataFrame(mult, index=dates, columns=instruments)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--data", default=str(REPO_ROOT / "data" / "dataset_sb_corrected_6yr_jagged.parquet")
    )
    parser.add_argument("--beta", type=float, default=0.5, help="Max penalty at age 0.")
    parser.add_argument(
        "--threshold", type=int, default=365, help="Age (days) at which penalty phases out."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=REPO_ROOT / "data" / "research" / "maturity_multiplier_b50_t365.parquet",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    log = logging.getLogger(__name__)

    log.info(f"Loading dataset: {args.data}")
    prices_df, _, lifecycle_df = load_crypto_perps_panel(
        args.data, validate_schema=True, allow_jagged=True
    )
    log.info(
        f"  prices_df: {prices_df.shape[0]} dates × {prices_df.shape[1]} instruments"
    )
    log.info(f"  lifecycle_df: {len(lifecycle_df)} instruments with launch_date")

    log.info(f"Building panel (β={args.beta}, T={args.threshold}d)...")
    panel = build_panel(prices_df, lifecycle_df, beta=args.beta, threshold_days=args.threshold)

    # Summary stats: distribution of multiplier values today
    today = panel.index.max()
    today_row = panel.loc[today].dropna()
    log.info(f"  Multiplier as of {today.date()}:")
    log.info(f"    N instruments with data:       {len(today_row)}")
    log.info(f"    fraction with multiplier < 1:  {(today_row < 1.0).mean():.3f}")
    log.info(f"    fraction at floor ({1-args.beta:.2f}):    {(today_row <= 1.0 - args.beta + 1e-9).mean():.3f}")
    log.info(f"    mean:                          {today_row.mean():.4f}")
    for q in [0.05, 0.25, 0.50, 0.75, 0.95]:
        log.info(f"    P{int(q*100):02d}: {today_row.quantile(q):.4f}")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    panel.to_parquet(args.output)
    log.info(f"✓ Wrote {args.output}")
    log.info(f"  Shape: {panel.shape}; size on disk: ~{args.output.stat().st_size // 1024} KiB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
