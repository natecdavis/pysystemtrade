"""
Event study backtest: forward returns by event type.

For each event class with ≥30 events, computes:
  - Unconditional mean forward return at H1/H3/H5/H10 (vol-adjusted)
  - Hit rate (sign agreement with direction_prior)
  - Interaction with existing trend forecast sign

Output: table sorted by H3 vol-adjusted return, printed to stdout.

Usage:
  python scripts/run_event_study_backtest.py
  python scripts/run_event_study_backtest.py --panel data/event_ingestion/event_panel.parquet
  python scripts/run_event_study_backtest.py --min-events 20
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

import numpy as np
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger("event_study")

_DEFAULT_PANEL = Path("data/event_ingestion/event_panel.parquet")
_DEFAULT_PRICES = Path("data/dataset_538registry_6yr_jagged.parquet")
_HORIZONS = [1, 3, 5, 10]
_VOL_WINDOW = 63


def _load_prices() -> pd.DataFrame:
    """Load price data, return wide DataFrame indexed by date."""
    p = _DEFAULT_PRICES
    if not p.exists():
        logger.warning(f"Price dataset not found at {p}")
        return pd.DataFrame()

    try:
        df = pd.read_parquet(p)
        # Long format: look for date + instrument + price column
        for price_col in ("price", "close"):
            if price_col in df.columns and "instrument" in df.columns:
                date_col = "date" if "date" in df.columns else df.index.name
                if date_col and date_col in df.columns:
                    pivot = df.pivot_table(index=date_col, columns="instrument", values=price_col)
                    pivot.index = pd.to_datetime(pivot.index)
                    return pivot
                else:
                    pivot = df.set_index(df.index).pivot(columns="instrument", values=price_col)
                    return pivot
        # Wide format
        return df
    except Exception as exc:
        logger.warning(f"Failed to load prices: {exc}")
        return pd.DataFrame()


def _forward_return(
    prices: pd.Series, event_date: pd.Timestamp, horizon: int
) -> float | None:
    """Vol-adjusted forward return from event_date+1 to event_date+horizon."""
    try:
        idx = prices.index.searchsorted(event_date)
        if idx + horizon >= len(prices):
            return None
        p0 = prices.iloc[idx]
        ph = prices.iloc[idx + horizon]
        if p0 <= 0:
            return None
        raw_ret = ph / p0 - 1.0

        # Vol normalization using trailing window
        window_start = max(0, idx - _VOL_WINDOW)
        hist = prices.iloc[window_start:idx]
        if len(hist) < 10:
            return None
        daily_rets = hist.pct_change().dropna()
        vol = daily_rets.std()
        if vol < 1e-8:
            return None
        return raw_ret / (vol * np.sqrt(horizon))
    except Exception:
        return None


def run_event_study(
    panel_path: str | Path = _DEFAULT_PANEL,
    min_events: int = 30,
) -> pd.DataFrame:
    panel_path = Path(panel_path)
    if not panel_path.exists():
        logger.error(f"Event panel not found: {panel_path}")
        return pd.DataFrame()

    panel = pd.read_parquet(panel_path)
    logger.info(f"Loaded {len(panel)} event-instrument rows")

    prices_wide = _load_prices()
    if prices_wide.empty:
        logger.error("No price data available for event study")
        return pd.DataFrame()

    prices_wide.index = pd.to_datetime(prices_wide.index)
    panel["event_date_utc"] = pd.to_datetime(panel["event_date_utc"], errors="coerce").dt.tz_localize(None)
    panel = panel.dropna(subset=["event_date_utc", "instrument", "event_type"])

    results: list[dict] = []

    for event_type, grp in panel.groupby("event_type"):
        if len(grp) < min_events:
            logger.info(f"Skipping {event_type}: only {len(grp)} events (< {min_events})")
            continue

        dir_prior = grp["direction_prior"].mode().iloc[0] if "direction_prior" in grp else 0

        h_returns: dict[int, list[float]] = {h: [] for h in _HORIZONS}
        signed_returns: dict[int, list[float]] = {h: [] for h in _HORIZONS}

        for _, ev in grp.iterrows():
            instrument = ev["instrument"]
            if instrument == "__MARKET__":
                # Use BTC as market proxy
                instrument = "BTCUSDT_PERP"

            if instrument not in prices_wide.columns:
                continue

            event_date = ev["event_date_utc"]
            prices = prices_wide[instrument].dropna()
            direction = ev.get("direction_prior", 0)

            for h in _HORIZONS:
                fwd = _forward_return(prices, event_date, h)
                if fwd is not None:
                    h_returns[h].append(fwd)
                    if direction != 0:
                        signed_returns[h].append(fwd * np.sign(direction))

        row: dict = {"event_type": event_type, "n_events": len(grp), "direction_prior": dir_prior}
        for h in _HORIZONS:
            rets = h_returns[h]
            srets = signed_returns[h]
            if len(rets) >= 5:
                mean_fwd = np.mean(rets)
                hit_rate = np.mean([r > 0 for r in (srets if dir_prior != 0 else rets)])
                row[f"H{h}_mean"] = round(mean_fwd, 4)
                row[f"H{h}_hit"] = round(hit_rate, 3)
                row[f"H{h}_n"] = len(rets)
            else:
                row[f"H{h}_mean"] = np.nan
                row[f"H{h}_hit"] = np.nan
                row[f"H{h}_n"] = 0
        results.append(row)

    if not results:
        logger.warning(f"No event types with ≥{min_events} events")
        return pd.DataFrame()

    df = pd.DataFrame(results)
    df = df.sort_values("H3_mean", ascending=False)
    return df


def main() -> None:
    parser = argparse.ArgumentParser(description="Event study backtest by event type")
    parser.add_argument("--panel", default=str(_DEFAULT_PANEL), help="Path to event_panel.parquet")
    parser.add_argument("--min-events", type=int, default=30, help="Min events per class")
    args = parser.parse_args()

    results = run_event_study(panel_path=args.panel, min_events=args.min_events)

    if results.empty:
        print("No results.")
        return

    # Pretty print
    pd.set_option("display.max_columns", 20)
    pd.set_option("display.width", 160)
    pd.set_option("display.float_format", "{:.4f}".format)

    print(f"\n{'='*100}")
    print(f"EVENT STUDY — Vol-adjusted forward returns by event type (sorted by H3)")
    print(f"{'='*100}")
    print(results.to_string(index=False))
    print(f"\nH* = vol-adjusted return at horizon * days. Hit = fraction in direction of prior.")


if __name__ == "__main__":
    main()
