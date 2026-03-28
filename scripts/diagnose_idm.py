#!/usr/bin/env python3
"""
Compute and report IDM statistics for the crypto perps backtest.

Two modes:
  1. --idm-csv PATH: read a pre-computed idm_history.csv (legacy system.py output)
  2. --diagnostics PATH --prices PATH: compute IDM from diagnostics.parquet + price data
     (for the current run_dynamic_universe_backtest.py output)

Usage:
    # Mode 1: legacy
    python scripts/diagnose_idm.py --idm-csv out/run/idm_history.csv

    # Mode 2: compute from current backtest output (default paths)
    python scripts/diagnose_idm.py \
        --diagnostics out/idm_diagnosis/diagnostics.parquet \
        --prices data/dataset_538registry_6yr_jagged.parquet

    # Mode 2 with defaults (looks for out/idm_diagnosis/ automatically)
    python scripts/diagnose_idm.py
"""
import argparse
import glob
from pathlib import Path
import pandas as pd
import numpy as np


IDM_CAP = 2.5
CORR_SPAN = 60     # EWMA span matching constraints engine
CORR_MIN_PERIODS = 20


def find_latest_diagnostics(search_root="out") -> Path | None:
    paths = glob.glob(f"{search_root}/**/diagnostics.parquet", recursive=True)
    if not paths:
        return None
    return Path(max(paths, key=lambda p: Path(p).stat().st_mtime))


def compute_idm_from_diagnostics(diag_path: Path, prices_path: Path) -> pd.DataFrame:
    """
    Derive a daily IDM time series from diagnostics.parquet + price returns.

    IDM(t) = min(1/sqrt(W(t)' * Corr(t) * W(t)), IDM_CAP)
    where Corr(t) is the EWMA correlation matrix of daily log-returns up to t,
    and W(t) is the equal-weight vector over active instruments on day t.
    """
    print(f"Loading diagnostics: {diag_path}")
    diag = pd.read_parquet(diag_path)

    print(f"Loading prices: {prices_path}")
    prices = pd.read_parquet(prices_path)

    # prices may be wide (date index, instrument columns) or long
    if "instrument" in prices.columns:
        # long format
        prices = prices.pivot(index="date", columns="instrument", values="close")
    prices.index = pd.to_datetime(prices.index)

    # daily log returns
    log_ret = np.log(prices / prices.shift(1)).dropna(how="all")

    # identify active instruments per day from diagnostics
    # active = has non-zero instrument_weight
    diag["date"] = pd.to_datetime(diag["date"])
    active_by_date = (
        diag[diag["instrument_weight"] > 0]
        .groupby("date")["instrument"]
        .apply(list)
    )

    dates = sorted(active_by_date.index)
    print(f"Computing IDM for {len(dates)} trading days...")

    idm_records = []
    # EWMA correlation state: use expanding EWMA via pandas ewm
    # We compute daily: use the full history up to each date with span=60

    for date in dates:
        instruments = active_by_date.loc[date]
        n = len(instruments)
        if n < 2:
            idm_records.append({"date": date, "idm": 1.0, "n_active_instruments": n})
            continue

        # Use returns up to and including this date
        hist = log_ret.loc[:date, instruments].dropna(how="all")

        # Need at least CORR_MIN_PERIODS of data
        if len(hist) < CORR_MIN_PERIODS:
            idm_records.append({"date": date, "idm": 1.0, "n_active_instruments": n})
            continue

        # EWMA covariance then correlation (span=60)
        ewm_cov = hist.ewm(span=CORR_SPAN, min_periods=CORR_MIN_PERIODS).cov().iloc[-n:]
        # Convert to correlation
        variances = np.diag(ewm_cov.values)
        if np.any(variances <= 0):
            idm_records.append({"date": date, "idm": 1.0, "n_active_instruments": n})
            continue
        std = np.sqrt(variances)
        corr = ewm_cov.values / np.outer(std, std)
        corr = np.clip(corr, -1.0, 1.0)

        # Equal weights
        w = np.ones(n) / n

        # IDM = 1 / sqrt(W' * Corr * W), capped
        portfolio_var = w @ corr @ w
        if portfolio_var <= 0:
            idm = 1.0
        else:
            idm = min(1.0 / np.sqrt(portfolio_var), IDM_CAP)

        idm_records.append({"date": date, "idm": idm, "n_active_instruments": n})

    return pd.DataFrame(idm_records)


def report(df: pd.DataFrame, source: str) -> None:
    idm = df["idm"]
    n = df["n_active_instruments"]

    print(f"\nSource: {source}")
    print(f"Period: {df['date'].min().date()} → {df['date'].max().date()}  ({len(df)} days)\n")

    # 1. Overall statistics
    print("=== IDM Statistics (full period) ===")
    print(f"  Mean:     {idm.mean():.3f}")
    print(f"  Median:   {idm.median():.3f}")
    print(f"  5th pct:  {idm.quantile(0.05):.3f}")
    print(f"  95th pct: {idm.quantile(0.95):.3f}")
    print(f"  Min:      {idm.min():.3f}")
    print(f"  Max:      {idm.max():.3f}")
    print()

    # 2. By-year table
    print("=== By Year ===")
    df = df.copy()
    df["year"] = pd.to_datetime(df["date"]).dt.year
    annual = df.groupby("year").agg(
        idm_mean=("idm", "mean"),
        n_mean=("n_active_instruments", "mean"),
    )
    print(f"{'Year':>6}  {'Mean IDM':>9}  {'Mean N':>7}")
    for year, row in annual.iterrows():
        print(f"{year:>6}  {row['idm_mean']:>9.3f}  {row['n_mean']:>7.1f}")
    print()

    # 3. Theory cross-check
    mean_idm = idm.mean()
    mean_n = n.mean()
    implied_rho = (mean_n / mean_idm**2 - 1) / (mean_n - 1) if mean_n > 1 else float("nan")
    print("=== Theory Cross-Check ===")
    print(f"  Mean N active: {mean_n:.1f}")
    print(f"  Mean IDM:      {mean_idm:.3f}")
    print(f"  Implied mean pairwise correlation (rho): {implied_rho:.3f}")
    print(f"  (Theoretical IDM at rho={implied_rho:.2f}, N={mean_n:.0f}: "
          f"{np.sqrt(mean_n / (1 + (mean_n - 1) * implied_rho)):.3f})")
    print()

    # 4. Vol decomposition note
    print("=== Structural Vol Note ===")
    print(f"  IDM ≈ {mean_idm:.2f} → portfolio vol ≈ per-instrument vol × {mean_idm:.2f} / N")
    print(f"  With N≈{mean_n:.0f} equal-weight instruments and IDM={mean_idm:.2f}:")
    print(f"    Effective independent bets ≈ {mean_idm**2:.1f}  (vs N={mean_n:.0f} actual)")
    print(f"    Effective diversification: {mean_idm**2/mean_n*100:.0f}% of maximum possible")
    print("  (The 10% actual vol comes from $10K capital + position sizing, not IDM alone.)")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--idm-csv", type=Path, default=None,
                        help="Pre-computed idm_history.csv (legacy mode)")
    parser.add_argument("--diagnostics", type=Path, default=None,
                        help="diagnostics.parquet from run_dynamic_universe_backtest.py")
    parser.add_argument("--prices", type=Path,
                        default=Path("data/dataset_538registry_6yr_jagged.parquet"),
                        help="Price parquet file")
    args = parser.parse_args()

    if args.idm_csv:
        # Legacy mode
        if not args.idm_csv.exists():
            print(f"File not found: {args.idm_csv}")
            return
        df = pd.read_csv(args.idm_csv, parse_dates=["date"])
        report(df, str(args.idm_csv))

    else:
        # Compute mode
        diag_path = args.diagnostics or find_latest_diagnostics()
        if diag_path is None or not diag_path.exists():
            print("No diagnostics.parquet found. Pass --diagnostics PATH or run a backtest first.")
            return
        df = compute_idm_from_diagnostics(diag_path, args.prices)
        report(df, str(diag_path))


if __name__ == "__main__":
    main()
