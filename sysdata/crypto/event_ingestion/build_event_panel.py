"""
Build event_panel.parquet from event_feed_raw.parquet.

Pipeline:
  1. Load event_feed_raw.parquet
  2. Run headline_classifier → event_type, severity_score, direction_prior
  3. Run instrument_mapper → explode to one row per instrument
  4. Run dedupe → dedupe_cluster_id, title_count_same_day
  5. Append-safe write to event_panel.parquet (dedup on content_hash + instrument)
"""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from sysdata.crypto.event_ingestion.dedupe import assign_cluster_ids, make_title_hash
from sysdata.crypto.event_ingestion.headline_classifier import classify_title
from sysdata.crypto.event_ingestion.instrument_mapper import (
    build_alias_map,
    map_title_to_instruments,
)
from sysdata.crypto.event_ingestion.sentiment_light import score_title

logger = logging.getLogger(__name__)

_HL_INSTRUMENTS = Path(__file__).parents[3] / "data/hyperliquid_instruments.json"
_DEFAULT_DATA_DIR = Path("data/event_ingestion")


def build_event_panel(
    raw_parquet: str | Path | None = None,
    panel_parquet: str | Path | None = None,
    data_dir: str | Path = _DEFAULT_DATA_DIR,
) -> pd.DataFrame:
    """
    Load event_feed_raw, classify, map to instruments, dedup, write event_panel.

    Returns the final event_panel DataFrame.
    """
    data_dir = Path(data_dir)
    raw_path = Path(raw_parquet) if raw_parquet else data_dir / "event_feed_raw.parquet"
    panel_path = Path(panel_parquet) if panel_parquet else data_dir / "event_panel.parquet"

    if not raw_path.exists():
        logger.warning(f"event_feed_raw not found at {raw_path}")
        return pd.DataFrame()

    raw_df = pd.read_parquet(raw_path)
    logger.info(f"Loaded {len(raw_df)} raw events from {raw_path}")

    alias_map = build_alias_map(_HL_INSTRUMENTS if _HL_INSTRUMENTS.exists() else None)

    rows: list[dict] = []
    for _, row in raw_df.iterrows():
        title = str(row.get("title") or "")
        event_type, severity, direction = classify_title(title)
        sentiment = score_title(title)
        instruments = map_title_to_instruments(title, alias_map)

        pub_dt = row.get("published_at_utc") or row.get("fetched_at_utc")
        if pub_dt is not None:
            try:
                event_date = pd.Timestamp(pub_dt).normalize()
            except Exception:
                event_date = None
        else:
            event_date = None

        is_exchange = row.get("source_type") == "exchange_announcement"
        is_tier1 = row.get("source_type") == "media_rss"
        tier_weight = 1.5 if is_exchange else 1.0
        confidence = 0.95 if is_exchange else 0.9

        for instrument in instruments:
            rows.append(
                {
                    "content_hash": row.get("content_hash"),
                    "title_hash": make_title_hash(title),
                    "instrument": instrument,
                    "event_date_utc": event_date,
                    "event_type": event_type,
                    "severity_score": severity,
                    "direction_prior": direction,
                    "sentiment_score": sentiment,
                    "source_name": row.get("source_name"),
                    "source_type": row.get("source_type"),
                    "title": title,
                    "url": row.get("url"),
                    "published_at_utc": pub_dt,
                    "fetched_at_utc": row.get("fetched_at_utc"),
                    "is_exchange_primary": is_exchange,
                    "is_tier1_media": is_tier1,
                    "tier_weight": tier_weight,
                    "confidence_score": confidence,
                }
            )

    if not rows:
        logger.info("No events to panel-ize")
        return pd.DataFrame()

    panel_df = pd.DataFrame(rows)
    panel_df = assign_cluster_ids(panel_df)

    # Append-safe: load existing panel, merge, dedup
    if panel_path.exists():
        existing = pd.read_parquet(panel_path)
        combined = pd.concat([existing, panel_df], ignore_index=True)
        combined = combined.drop_duplicates(
            subset=["content_hash", "instrument"], keep="last"
        )
    else:
        combined = panel_df

    panel_path.parent.mkdir(parents=True, exist_ok=True)
    combined.to_parquet(panel_path, index=False)
    logger.info(f"event_panel: {len(combined)} rows → {panel_path}")
    return combined
