#!/usr/bin/env python3
"""
Execution-slip diagnostic: how much P&L do we miss by trading at ~02:00 UTC
instead of at 00:00 UTC (the daily-bar close)?

Pulls hourly klines from Binance Vision for the top-30 instruments by
non-zero position-days in `out/vol_floor_sweep/run_vf0.20/positions.csv`,
then computes:

    slip_t,i = -(P_t,i - P_{t-1,i}) * C_{0:00,t,i} * r_first_Nh,t,i

where P is the system's signed position (tokens), C is the official 00:00 UTC
close used by the daily backtest, and r_first_Nh is the price change in the
first N hours of UTC day t. The slip is summed across (t, i) and compared to
the strategy's cumulative P&L from `daily_returns.csv`.

No VPN required (Vision is public). Idempotent: cached ZIPs and parsed
parquets are reused across runs.
"""

from __future__ import annotations

import argparse
import io
import logging
import sys
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


VISION_URL_TEMPLATE = (
    "https://data.binance.vision/data/futures/um/monthly/klines/"
    "{symbol}/1h/{symbol}-1h-{year}-{month:02d}.zip"
)


# ---------------------------------------------------------------------------
# Download + parse
# ---------------------------------------------------------------------------


def download_zip(symbol: str, year: int, month: int, dest: Path) -> str:
    """Download one monthly 1h kline ZIP. Returns status string."""
    if dest.exists() and dest.stat().st_size > 0:
        return "cached"
    url = VISION_URL_TEMPLATE.format(symbol=symbol, year=year, month=month)
    try:
        r = requests.get(url, timeout=60)
    except Exception as exc:
        return f"error:{exc}"
    if r.status_code == 200:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(r.content)
        return "downloaded"
    if r.status_code == 404:
        return "missing"
    return f"http_{r.status_code}"


def parse_zip(zip_path: Path) -> pd.DataFrame:
    """Parse one monthly 1h kline ZIP into a DataFrame indexed by open_time (UTC)."""
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        if not names:
            return pd.DataFrame()
        with zf.open(names[0]) as f:
            raw = f.read()
    df = pd.read_csv(io.BytesIO(raw), header=None)
    # Binance Vision recent files sometimes ship a header row. Detect + drop.
    if isinstance(df.iloc[0, 0], str) and not df.iloc[0, 0].isdigit():
        df = df.iloc[1:].reset_index(drop=True)
    # Schema: open_time, open, high, low, close, volume, close_time, quote_volume,
    #         num_trades, taker_buy_base, taker_buy_quote, ignore
    df = df.iloc[:, :7].copy()
    df.columns = ["open_time", "open", "high", "low", "close", "volume", "close_time"]
    df["open_time"] = pd.to_numeric(df["open_time"], errors="coerce")
    # Heuristic: convert microseconds → ms if values look microsecond-scale (post-2024 ZIPs).
    # Real ms timestamps in our window are ~1.5e12; us values are ~1.5e15.
    if df["open_time"].max() > 1e14:
        df["open_time"] = df["open_time"] // 1000
    df["open"] = pd.to_numeric(df["open"], errors="coerce")
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df = df.dropna(subset=["open_time", "open", "close"])
    df = df.set_index("open_time").sort_index()
    return df[["open", "close"]]


def build_hourly_panel(
    symbol: str,
    months: list[tuple[int, int]],
    cache_dir: Path,
) -> pd.DataFrame:
    """Concatenate per-month parsed frames into one continuous hourly panel."""
    frames = []
    for year, month in months:
        zip_path = cache_dir / symbol / f"{symbol}-1h-{year}-{month:02d}.zip"
        if not zip_path.exists():
            continue
        try:
            df = parse_zip(zip_path)
            if not df.empty:
                frames.append(df)
        except Exception as exc:
            logger.warning(f"{symbol} {year}-{month:02d}: parse error {exc}")
    if not frames:
        return pd.DataFrame()
    full = pd.concat(frames).sort_index()
    return full[~full.index.duplicated(keep="first")]


# ---------------------------------------------------------------------------
# Slip computation
# ---------------------------------------------------------------------------


def compute_slip_for_instrument(
    instrument_perp: str,
    hourly: pd.DataFrame,
    positions: pd.Series,
) -> pd.DataFrame:
    """
    For one instrument: return per-date frame with
    [delta_pos, open_00, open_01, open_02, r_0_to_1h, r_0_to_2h, slip_1h, slip_2h, gross_2h].
    """
    if hourly.empty:
        return pd.DataFrame()

    # Per-day extraction. Use the OPEN of the 00:00 / 01:00 / 02:00 UTC bars.
    # Binance bars are labeled by open_time; open(00:00) = first tick of the day,
    # open(01:00) = first tick of the 01:00-02:00 hour, etc.
    hourly = hourly.copy()
    hourly["utc_date"] = hourly.index.tz_convert("UTC").normalize().date
    hourly["hour"] = hourly.index.tz_convert("UTC").hour

    sub = hourly[hourly["hour"].isin([0, 1, 2])]
    wide = sub.pivot_table(
        index="utc_date",
        columns="hour",
        values="open",
        aggfunc="first",
    )
    wide.columns = [f"open_{int(h):02d}" for h in wide.columns]
    wide = wide.dropna(subset=["open_00"])  # need the 00:00 anchor

    # Returns (open-to-open, intra-day, in fractional units)
    wide["r_0_to_1h"] = wide["open_01"] / wide["open_00"] - 1.0
    wide["r_0_to_2h"] = wide["open_02"] / wide["open_00"] - 1.0

    # Align positions (positions.csv is indexed by string date)
    positions = positions.copy()
    positions.index = pd.to_datetime(positions.index).date
    positions.name = "position"
    out = wide.join(positions, how="inner")
    out["delta_pos"] = out["position"].diff().fillna(out["position"])

    # Slip in USD per (date, instrument). Signed (negative = lost P&L).
    out["slip_1h"] = -out["delta_pos"] * out["open_00"] * out["r_0_to_1h"]
    out["slip_2h"] = -out["delta_pos"] * out["open_00"] * out["r_0_to_2h"]
    # Gross magnitude (absolute), useful as denominator-free sanity check
    out["gross_2h"] = (out["delta_pos"] * out["open_00"]).abs() * out["r_0_to_2h"].abs()

    out["instrument"] = instrument_perp
    return out.reset_index().rename(columns={"index": "date"})


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--positions",
        default="out/vol_floor_sweep/run_vf0.20/positions.csv",
        help="Path to the baseline positions.csv (signed tokens by date × instrument).",
    )
    parser.add_argument(
        "--daily-returns",
        default="out/vol_floor_sweep/run_vf0.20/daily_returns.csv",
        help="Path to the baseline daily_returns.csv (strategy total daily P&L).",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=30,
        help="Number of top instruments by position-days to include.",
    )
    parser.add_argument(
        "--start",
        default="2020-01",
        help="First month to pull (YYYY-MM).",
    )
    parser.add_argument(
        "--end",
        default=None,
        help="Last month to pull (YYYY-MM). Defaults to last full month in positions.",
    )
    parser.add_argument(
        "--cache-dir",
        default="data/raw/binance/klines_1h",
        help="Where to cache monthly ZIPs.",
    )
    parser.add_argument(
        "--out-dir",
        default=None,
        help="Output directory. Defaults to out/execution_slip_<today>/",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=16,
        help="Concurrent downloads.",
    )
    parser.add_argument(
        "--skip-download",
        action="store_true",
        help="Don't fetch ZIPs (use cache only).",
    )
    args = parser.parse_args()

    positions_path = REPO_ROOT / args.positions
    returns_path = REPO_ROOT / args.daily_returns
    cache_dir = REPO_ROOT / args.cache_dir
    out_dir = (
        Path(args.out_dir)
        if args.out_dir
        else REPO_ROOT / f"out/execution_slip_{datetime.now().strftime('%Y%m%d')}"
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    # ---- Pick top-K instruments by non-zero position-days
    positions_df = pd.read_csv(positions_path, index_col=0)
    activity = (positions_df != 0).sum().sort_values(ascending=False)
    top = activity.head(args.top_k)
    logger.info(f"Top {args.top_k} by non-zero position-days (sum={top.sum()}):")
    for inst, days in top.items():
        logger.info(f"  {inst:25s} {days:6d}")

    instruments_perp = list(top.index)
    # Strip _PERP for Binance symbol (e.g., XRPUSDT_PERP → XRPUSDT)
    symbol_map = {inst: inst.replace("_PERP", "") for inst in instruments_perp}

    # ---- Determine month range
    if args.end is None:
        last_date = pd.to_datetime(positions_df.index[-1])
        end_year, end_month = last_date.year, last_date.month
    else:
        end_year, end_month = map(int, args.end.split("-"))
    start_year, start_month = map(int, args.start.split("-"))

    months: list[tuple[int, int]] = []
    y, m = start_year, start_month
    while (y, m) <= (end_year, end_month):
        months.append((y, m))
        m += 1
        if m > 12:
            m = 1
            y += 1
    logger.info(f"Month range: {start_year}-{start_month:02d} to {end_year}-{end_month:02d} ({len(months)} months)")

    # ---- Download (parallel)
    if not args.skip_download:
        tasks: list[tuple[str, int, int, Path]] = []
        for inst, symbol in symbol_map.items():
            for year, month in months:
                dest = cache_dir / symbol / f"{symbol}-1h-{year}-{month:02d}.zip"
                tasks.append((symbol, year, month, dest))
        logger.info(f"Submitting {len(tasks)} download tasks ({args.max_workers} workers)...")
        n_cached = n_downloaded = n_missing = n_error = 0
        with ThreadPoolExecutor(max_workers=args.max_workers) as pool:
            futures = {
                pool.submit(download_zip, sym, yr, mo, dest): (sym, yr, mo)
                for sym, yr, mo, dest in tasks
            }
            for i, fut in enumerate(as_completed(futures), 1):
                status = fut.result()
                if status == "cached":
                    n_cached += 1
                elif status == "downloaded":
                    n_downloaded += 1
                elif status == "missing":
                    n_missing += 1
                else:
                    n_error += 1
                if i % 200 == 0:
                    logger.info(
                        f"  progress {i}/{len(tasks)}: "
                        f"cached={n_cached} new={n_downloaded} 404={n_missing} err={n_error}"
                    )
        logger.info(
            f"Downloads done: cached={n_cached} new={n_downloaded} 404={n_missing} err={n_error}"
        )

    # ---- Build hourly panels + slip
    all_slip_frames: list[pd.DataFrame] = []
    for inst in instruments_perp:
        symbol = symbol_map[inst]
        hourly = build_hourly_panel(symbol, months, cache_dir)
        if hourly.empty:
            logger.warning(f"{inst} ({symbol}): no hourly data, skipping")
            continue
        positions = positions_df[inst].astype(float)
        slip = compute_slip_for_instrument(inst, hourly, positions)
        if slip.empty:
            logger.warning(f"{inst}: no overlap between positions and hourly data")
            continue
        logger.info(
            f"{inst}: hourly_rows={len(hourly)}, position_days={len(slip)}, "
            f"sum_slip_2h=${slip['slip_2h'].sum():,.0f}, "
            f"sum_|slip_2h|=${slip['slip_2h'].abs().sum():,.0f}"
        )
        all_slip_frames.append(slip)

    if not all_slip_frames:
        logger.error("No slip data produced — abort.")
        return 1

    slip_panel = pd.concat(all_slip_frames, ignore_index=True)
    slip_panel.to_parquet(out_dir / "slip_panel.parquet", index=False)

    # ---- Aggregate vs baseline P&L
    returns = pd.read_csv(returns_path, index_col=0).iloc[:, 0].astype(float)
    returns.index = pd.to_datetime(returns.index).date
    cum_pnl = returns.sum()
    logger.info(f"Baseline cumulative daily return (sum): {cum_pnl:.4f} fractional")

    # daily_returns.csv stores fractional returns vs capital — translate slip to fraction.
    # capital from config (use the most recent live value as the headline reference).
    # Pull capital from positions.csv header? No — we'll just report slip in dollars
    # AND as a ratio against the equivalent dollar baseline:
    #   $ baseline = sum(daily_return * capital)
    # Use capital = 9776.95 (the current live capital).
    capital_live = 9776.95
    dollar_baseline = (returns * capital_live).sum()

    total_slip_1h = slip_panel["slip_1h"].sum()
    total_slip_2h = slip_panel["slip_2h"].sum()
    abs_slip_2h = slip_panel["slip_2h"].abs().sum()
    long_slip_2h = slip_panel.loc[slip_panel["delta_pos"] > 0, "slip_2h"].sum()
    short_slip_2h = slip_panel.loc[slip_panel["delta_pos"] < 0, "slip_2h"].sum()

    by_year = slip_panel.assign(year=pd.to_datetime(slip_panel["date"]).dt.year).groupby("year")[
        ["slip_1h", "slip_2h"]
    ].sum()
    by_inst = slip_panel.groupby("instrument")[["slip_1h", "slip_2h"]].sum().sort_values("slip_2h")

    summary_lines = [
        "Execution-slip diagnostic",
        "=" * 72,
        f"Top-{args.top_k} instruments (top {top.sum()} position-days of {(positions_df!=0).sum().sum()}, "
        f"{100*top.sum()/(positions_df!=0).sum().sum():.1f}% coverage)",
        f"Date range: {slip_panel['date'].min()} → {slip_panel['date'].max()}",
        "",
        f"Baseline strategy total $ P&L (live config, capital=${capital_live:,.2f}):"
        f"  ${dollar_baseline:,.0f}",
        "",
        "Total execution slip (signed; negative = lost P&L):",
        f"  1h slip:  ${total_slip_1h:,.0f}   "
        f"({100*total_slip_1h/dollar_baseline:+.2f}% of baseline P&L)",
        f"  2h slip:  ${total_slip_2h:,.0f}   "
        f"({100*total_slip_2h/dollar_baseline:+.2f}% of baseline P&L)",
        f"  2h |gross|: ${abs_slip_2h:,.0f}   "
        f"(magnitude-only sanity check)",
        "",
        f"  Long-trade 2h slip:  ${long_slip_2h:,.0f}",
        f"  Short-trade 2h slip: ${short_slip_2h:,.0f}",
        "",
        "Slip by year:",
        by_year.to_string(float_format=lambda v: f"${v:,.0f}"),
        "",
        "Slip by instrument (sorted by 2h slip, ascending = worst):",
        by_inst.to_string(float_format=lambda v: f"${v:,.0f}"),
    ]
    summary = "\n".join(summary_lines)
    print()
    print(summary)
    (out_dir / "summary.txt").write_text(summary + "\n")
    by_year.to_csv(out_dir / "slip_by_year.csv")
    by_inst.to_csv(out_dir / "slip_by_instrument.csv")
    logger.info(f"Outputs written to {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
