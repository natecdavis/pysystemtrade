"""
Binance announcements adapter.

Uses the public JSON API (no HTML scraping):
  https://www.binance.com/bapi/composite/v1/public/cms/article/list/query

Response: data.catalogs[] → each has catalogId, catalogName, articles[].
Article fields: id, code (dedup key), title, type, releaseDate (ms UTC).

Supports full historical pagination via pageNo increment.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

from sysdata.crypto.event_ingestion.dedupe import make_content_hash, make_title_hash

logger = logging.getLogger(__name__)

_API_URL = "https://www.binance.com/bapi/composite/v1/public/cms/article/list/query"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.binance.com/en/support/announcement",
}
_PAGE_SIZE = 50
_REQUEST_DELAY = 0.5  # seconds between pages


def fetch_announcements(
    max_pages: int = 10,
    snapshot_dir: str | Path | None = None,
) -> list[dict]:
    """
    Fetch Binance announcement articles.

    Args:
        max_pages: Maximum pages to fetch (each page = 50 articles).
                   Use a large number for full historical backfill.
        snapshot_dir: If provided, saves raw JSON response per page.

    Returns:
        List of raw-feed dicts (event_feed_raw schema).
    """
    rows: list[dict] = []
    fetched_at = datetime.now(timezone.utc)

    for page_no in range(1, max_pages + 1):
        params = {"pageNo": page_no, "pageSize": _PAGE_SIZE, "type": 1}
        try:
            resp = requests.get(
                _API_URL, params=params, headers=_HEADERS, timeout=15
            )
            resp.raise_for_status()
            payload = resp.json()
        except Exception as exc:
            logger.warning(f"Binance page {page_no} fetch failed: {exc}")
            break

        if snapshot_dir:
            snap = Path(snapshot_dir)
            snap.mkdir(parents=True, exist_ok=True)
            date_str = fetched_at.strftime("%Y-%m-%d")
            snap_file = snap / f"binance_p{page_no:03d}_{date_str}.json"
            snap_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2))

        data = payload.get("data") or {}
        catalogs = data.get("catalogs") or []

        if not catalogs:
            # Also check top-level articles list (alternate response format)
            articles = data.get("articles") or []
            if not articles:
                logger.debug(f"Binance page {page_no}: no catalogs or articles — stopping")
                break
            catalogs = [{"catalogName": "general", "articles": articles}]

        page_articles: list[dict] = []
        for catalog in catalogs:
            for article in catalog.get("articles") or []:
                page_articles.append(
                    {**article, "_catalog_name": catalog.get("catalogName", "")}
                )

        if not page_articles:
            logger.debug(f"Binance page {page_no}: empty — stopping pagination")
            break

        for art in page_articles:
            title = str(art.get("title") or "").strip()
            if not title:
                continue

            release_ms = art.get("releaseDate")
            if release_ms:
                try:
                    pub_dt = datetime.fromtimestamp(int(release_ms) / 1000, tz=timezone.utc)
                except Exception:
                    pub_dt = None
            else:
                pub_dt = None

            code = str(art.get("code") or art.get("id") or "")
            url = (
                f"https://www.binance.com/en/support/announcement/{code}"
                if code
                else f"https://www.binance.com/en/support/announcement"
            )

            rows.append(
                {
                    "source_id": code,
                    "source_type": "exchange_announcement",
                    "source_name": "binance",
                    "fetched_at_utc": fetched_at,
                    "published_at_utc": pub_dt,
                    "url": url,
                    "title": title,
                    "summary": None,
                    "author": None,
                    "raw_json": json.dumps(art, ensure_ascii=False),
                    "language": "en",
                    "content_hash": make_content_hash(url + code),
                    "title_hash": make_title_hash(title),
                    "parse_status": "ok",
                    "parse_error": None,
                }
            )

        logger.info(
            f"Binance page {page_no}: {len(page_articles)} articles "
            f"(total so far: {len(rows)})"
        )

        if len(page_articles) < _PAGE_SIZE:
            break  # last page

        time.sleep(_REQUEST_DELAY)

    return rows
