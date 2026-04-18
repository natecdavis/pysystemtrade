"""
Build daily_attention_features.parquet from event_panel + trends_panel.

Produces one row per (instrument, date) with:
  headline_count, tier1_headline_count, exchange_announcement_count,
  max_severity, mean_sentiment_light,
  weighted_attention_score,
  attention_z_xs   (XS z-score at each date),
  attention_shock_ts (TS z-score vs trailing 63d),
  attention_z = 0.5 * attention_z_xs + 0.5 * attention_shock_ts
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

_DEFAULT_DATA_DIR = Path("data/event_ingestion")
_TS_WINDOW = 63


def _xs_zscore(series: pd.Series) -> float:
    """XS z-score of a value within a cross-section (passed as the full cross-section)."""
    mu = series.mean()
    sigma = series.std()
    if sigma < 1e-8:
        return 0.0
    return float((series - mu) / sigma)


def build_daily_features(
    panel_parquet: str | Path | None = None,
    trends_parquet: str | Path | None = None,
    features_parquet: str | Path | None = None,
    data_dir: str | Path = _DEFAULT_DATA_DIR,
) -> pd.DataFrame:
    """
    Aggregate event_panel + trends_panel → daily_attention_features.

    Returns the full features DataFrame.
    """
    data_dir = Path(data_dir)
    panel_path = Path(panel_parquet) if panel_parquet else data_dir / "event_panel.parquet"
    trends_path = (
        Path(trends_parquet) if trends_parquet else data_dir / "trends_panel.parquet"
    )
    features_path = (
        Path(features_parquet)
        if features_parquet
        else data_dir / "daily_attention_features.parquet"
    )

    # --- Load event panel ---
    agg_rows: list[dict] = []
    if panel_path.exists():
        panel = pd.read_parquet(panel_path)
        panel["event_date_utc"] = pd.to_datetime(panel["event_date_utc"], errors="coerce")
        panel = panel.dropna(subset=["event_date_utc", "instrument"])
        panel["date"] = panel["event_date_utc"].dt.normalize()

        for (instrument, date), grp in panel.groupby(["instrument", "date"]):
            n = len(grp)
            n_tier1 = int(grp["is_tier1_media"].sum()) if "is_tier1_media" in grp else 0
            n_exchange = int(grp["is_exchange_primary"].sum()) if "is_exchange_primary" in grp else 0
            max_sev = float(grp["severity_score"].max()) if "severity_score" in grp else 0.0
            mean_sent = float(grp["sentiment_score"].mean()) if "sentiment_score" in grp else 0.0
            # title_count_same_day bonus: average cluster size
            tcd = grp["title_count_same_day"].fillna(1).mean() if "title_count_same_day" in grp else 1.0
            raw_score = (
                1.5 * n_exchange
                + 1.0 * n_tier1
                + 0.5 * max(tcd - 1.0, 0.0)
            )
            agg_rows.append(
                {
                    "instrument": instrument,
                    "date": date,
                    "headline_count": n,
                    "tier1_headline_count": n_tier1,
                    "exchange_announcement_count": n_exchange,
                    "max_severity": max_sev,
                    "mean_sentiment_light": mean_sent,
                    "raw_attention_score": raw_score,
                    "trends_z_63d": np.nan,
                }
            )
        logger.info(f"Event panel: {len(agg_rows)} instrument-date rows aggregated")
    else:
        logger.warning(f"event_panel not found at {panel_path}")

    # --- Merge trends ---
    if trends_path.exists():
        trends = pd.read_parquet(trends_path)
        trends["date"] = pd.to_datetime(trends["date_utc"], errors="coerce").dt.normalize()
        trends_pivot = (
            trends.groupby(["instrument", "date"])["trends_z_63d"].mean().reset_index()
        )

        # Build a dict for fast lookup
        trends_lookup: dict[tuple, float] = {
            (r["instrument"], r["date"]): r["trends_z_63d"]
            for _, r in trends_pivot.iterrows()
        }

        for row in agg_rows:
            key = (row["instrument"], row["date"])
            if key in trends_lookup:
                row["trends_z_63d"] = trends_lookup[key]
    else:
        logger.info(f"trends_panel not found at {trends_path} — skipping trends signal")

    if not agg_rows:
        logger.info("No features to build")
        return pd.DataFrame()

    df = pd.DataFrame(agg_rows)

    # Add trends contribution to weighted score
    trends_contrib = df["trends_z_63d"].clip(lower=0).fillna(0.0) * 0.75
    df["weighted_attention_score"] = df["raw_attention_score"] + trends_contrib
    df = df.drop(columns=["raw_attention_score"])

    # --- TS z-score (per instrument, trailing 63d) ---
    df = df.sort_values(["instrument", "date"])
    ts_z_list = []
    for instrument, grp in df.groupby("instrument"):
        scores = grp["weighted_attention_score"].values.astype(float)
        dates = grp["date"].values
        ts_z = np.full(len(scores), np.nan)
        for i in range(len(scores)):
            window_start = max(0, i - _TS_WINDOW)
            window = scores[window_start:i]
            if len(window) < 10:
                ts_z[i] = 0.0
            else:
                mu = window.mean()
                sigma = max(window.std(), 0.1)  # floor prevents explosion when history is flat
                ts_z[i] = np.clip((scores[i] - mu) / sigma, -5.0, 5.0)
        ts_z_list.append(pd.Series(ts_z, index=grp.index))
    df["attention_shock_ts"] = pd.concat(ts_z_list)

    # --- XS z-score (across instruments at each date) ---
    xs_z_list = []
    for date, grp in df.groupby("date"):
        scores = grp["weighted_attention_score"]
        mu = scores.mean()
        sigma = max(float(scores.std(ddof=0)), 0.1)
        xs_z = (scores - mu) / sigma
        xs_z_list.append(xs_z.clip(-5.0, 5.0))
    df["attention_z_xs"] = pd.concat(xs_z_list)

    df["attention_z"] = (
        0.5 * df["attention_z_xs"].fillna(0.0) + 0.5 * df["attention_shock_ts"].fillna(0.0)
    ).clip(-5.0, 5.0)

    df = df.reset_index(drop=True)
    features_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(features_path, index=False)
    logger.info(f"daily_attention_features: {len(df)} rows → {features_path}")
    return df
