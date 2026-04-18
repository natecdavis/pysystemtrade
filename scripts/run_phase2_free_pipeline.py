"""
Phase 2 free event/attention ingestion pipeline.

Orchestrates:
  1. Binance announcements adapter
  2. RSS/media adapter
  3. Google Trends adapter
  4. Merge raw feeds → event_feed_raw.parquet
  5. Build event_panel.parquet
  6. Build daily_attention_features.parquet

Usage:
  python scripts/run_phase2_free_pipeline.py              # full run
  python scripts/run_phase2_free_pipeline.py --test       # fetch 5 items/source, no writes
  python scripts/run_phase2_free_pipeline.py --backfill 50
  python scripts/run_phase2_free_pipeline.py --skip-trends
  python scripts/run_phase2_free_pipeline.py --outdir data/event_ingestion_dev
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("phase2_pipeline")

# ---------------------------------------------------------------------------
# Adapter imports (lazy to avoid crashing if optional deps missing)
# ---------------------------------------------------------------------------

def _import_adapters():
    from sysdata.crypto.event_ingestion.adapters.binance import fetch_announcements
    from sysdata.crypto.event_ingestion.adapters.rss_media import fetch_all_media
    from sysdata.crypto.event_ingestion.adapters.google_trends import fetch_trends
    return fetch_announcements, fetch_all_media, fetch_trends


def _import_builders():
    from sysdata.crypto.event_ingestion.build_event_panel import build_event_panel
    from sysdata.crypto.event_ingestion.build_daily_features import build_daily_features
    return build_event_panel, build_daily_features


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(
    test: bool = False,
    backfill_pages: int = 10,
    skip_trends: bool = False,
    outdir: str = "data/event_ingestion",
) -> None:
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)

    snapshot_dir = out / "raw_snapshots" / datetime.now(timezone.utc).strftime("%Y-%m-%d")

    fetch_announcements, fetch_all_media, fetch_trends = _import_adapters()

    # ---- 1. Binance ----
    logger.info("=== Binance announcements ===")
    pages = 1 if test else backfill_pages
    binance_rows = fetch_announcements(max_pages=pages, snapshot_dir=snapshot_dir)
    logger.info(f"Binance: {len(binance_rows)} articles")

    # ---- 2. Media ----
    logger.info("=== RSS / media feeds ===")
    media_rows = fetch_all_media(snapshot_dir=snapshot_dir)
    logger.info(f"Media: {len(media_rows)} articles")

    # ---- 3. Google Trends ----
    trends_rows: list[dict] = []
    if not skip_trends:
        logger.info("=== Google Trends ===")
        trends_manual_dir = out / "trends_manual"
        try:
            trends_rows = fetch_trends(trends_manual_dir=trends_manual_dir)
            logger.info(f"Trends: {len(trends_rows)} rows")
        except Exception as exc:
            logger.warning(f"Google Trends failed: {exc}")

    # ---- Summary in test mode ----
    if test:
        print(f"\n--- TEST MODE SUMMARY ---")
        print(f"Binance:   {len(binance_rows)} articles")
        print(f"Media:     {len(media_rows)} articles")
        print(f"Trends:    {len(trends_rows)} rows")
        total = len(binance_rows) + len(media_rows)
        print(f"Total raw: {total} items")
        if binance_rows:
            print(f"\nSample Binance title: {binance_rows[0]['title'][:80]}")
        if media_rows:
            print(f"Sample media title:   {media_rows[0]['title'][:80]}")
        return

    # ---- 4. Write event_feed_raw ----
    all_raw = binance_rows + media_rows
    if all_raw:
        raw_df = pd.DataFrame(all_raw)
        raw_path = out / "event_feed_raw.parquet"
        if raw_path.exists():
            existing = pd.read_parquet(raw_path)
            combined = pd.concat([existing, raw_df], ignore_index=True)
            combined = combined.drop_duplicates(subset=["content_hash"], keep="last")
        else:
            combined = raw_df
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        combined.to_parquet(raw_path, index=False)
        logger.info(f"event_feed_raw: {len(combined)} rows → {raw_path}")

    # ---- 5. Write trends_panel ----
    if trends_rows:
        trends_df = pd.DataFrame(trends_rows)
        trends_path = out / "trends_panel.parquet"
        if trends_path.exists():
            existing_trends = pd.read_parquet(trends_path)
            combined_trends = pd.concat([existing_trends, trends_df], ignore_index=True)
            combined_trends = combined_trends.drop_duplicates(
                subset=["instrument", "date_utc"], keep="last"
            )
        else:
            combined_trends = trends_df
        combined_trends.to_parquet(trends_path, index=False)
        logger.info(f"trends_panel: {len(combined_trends)} rows → {trends_path}")

    # ---- 6. Build event_panel ----
    build_event_panel, build_daily_features = _import_builders()
    logger.info("=== Building event_panel ===")
    event_panel = build_event_panel(data_dir=out)

    # ---- 7. Build daily features ----
    if not event_panel.empty:
        logger.info("=== Building daily_attention_features ===")
        build_daily_features(data_dir=out)

    logger.info("=== Phase 2 pipeline complete ===")


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 2 free event ingestion pipeline")
    parser.add_argument("--test", action="store_true", help="Dry run: fetch minimal data, no writes")
    parser.add_argument("--backfill", type=int, default=10, metavar="N",
                        help="Binance pages to fetch (default 10)")
    parser.add_argument("--skip-trends", action="store_true", help="Skip Google Trends")
    parser.add_argument("--outdir", default="data/event_ingestion",
                        help="Output directory (default: data/event_ingestion)")
    args = parser.parse_args()

    run(
        test=args.test,
        backfill_pages=args.backfill,
        skip_trends=args.skip_trends,
        outdir=args.outdir,
    )


if __name__ == "__main__":
    main()
