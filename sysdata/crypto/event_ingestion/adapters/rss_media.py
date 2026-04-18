"""
RSS / archive-HTML media adapter.

Fetches headlines from crypto media sources configured in feeds.yaml.
Uses feedparser for RSS feeds and requests+BeautifulSoup for archive pages.
Browser-like User-Agent header to avoid 403 rejections.

V1: headline metadata only (title, link, published, summary).
No full article body fetching.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import feedparser
import requests
import yaml
from bs4 import BeautifulSoup

from sysdata.crypto.event_ingestion.dedupe import make_content_hash, make_title_hash

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
_TIMEOUT = 15

_FEEDS_YAML = Path(__file__).parent / "feeds.yaml"


def _load_feeds() -> list[dict]:
    with open(_FEEDS_YAML) as f:
        data = yaml.safe_load(f)
    return [fd for fd in data.get("feeds", []) if fd.get("enabled", True)]


def _parse_date(date_str: str | None) -> datetime | None:
    if not date_str:
        return None
    try:
        return parsedate_to_datetime(date_str).astimezone(timezone.utc)
    except Exception:
        pass
    try:
        return datetime.fromisoformat(date_str.replace("Z", "+00:00")).astimezone(
            timezone.utc
        )
    except Exception:
        return None


def _fetch_rss(source_name: str, url: str) -> list[dict]:
    fetched_at = datetime.now(timezone.utc)
    rows: list[dict] = []
    try:
        # Fetch with requests so we control headers/decompression, then pass raw content
        resp = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
        resp.raise_for_status()
        feed = feedparser.parse(resp.content)
        entries = feed.get("entries") or []
        if not entries and feed.get("bozo"):
            logger.warning(f"{source_name} RSS bozo exception: {feed.get('bozo_exception')}")
        for entry in entries:
            title = (entry.get("title") or "").strip()
            link = entry.get("link") or ""
            if not title or not link:
                continue
            pub_dt = _parse_date(entry.get("published"))
            summary = (entry.get("summary") or entry.get("description") or "")[:500]
            rows.append(
                _make_row(source_name, "media_rss", fetched_at, pub_dt, link, title, summary)
            )
        logger.info(f"{source_name} RSS: {len(rows)} entries")
    except Exception as exc:
        logger.warning(f"{source_name} RSS failed: {exc}")
    return rows


def _fetch_archive_html(source_name: str, url: str) -> list[dict]:
    fetched_at = datetime.now(timezone.utc)
    rows: list[dict] = []
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        # Generic card extractor: look for <a> tags with substantial link text
        # that sit inside article-like containers.
        candidates = []
        for tag in soup.find_all("a", href=True):
            text = tag.get_text(strip=True)
            href = tag["href"]
            if len(text) < 20 or len(text) > 300:
                continue
            if any(skip in href for skip in ["/tag/", "/author/", "/category/", "#", "javascript"]):
                continue
            # Must look like an article link (has a slug)
            if href.count("/") < 2:
                continue
            candidates.append((text, href))

        # Deduplicate by href
        seen_hrefs: set[str] = set()
        for title, href in candidates:
            if href in seen_hrefs:
                continue
            seen_hrefs.add(href)
            if not href.startswith("http"):
                base = url.rstrip("/").rsplit("/", 1)[0]
                href = base + "/" + href.lstrip("/")
            rows.append(
                _make_row(source_name, "media_rss", fetched_at, None, href, title, None)
            )

        logger.info(f"{source_name} archive HTML: {len(rows)} articles")
    except Exception as exc:
        logger.warning(f"{source_name} archive HTML failed: {exc}")
    return rows


def _make_row(
    source_name: str,
    source_type: str,
    fetched_at: datetime,
    pub_dt: datetime | None,
    url: str,
    title: str,
    summary: str | None,
) -> dict:
    return {
        "source_id": make_content_hash(url),
        "source_type": source_type,
        "source_name": source_name,
        "fetched_at_utc": fetched_at,
        "published_at_utc": pub_dt,
        "url": url,
        "title": title,
        "summary": summary,
        "author": None,
        "raw_json": None,
        "language": "en",
        "content_hash": make_content_hash(url),
        "title_hash": make_title_hash(title),
        "parse_status": "ok",
        "parse_error": None,
    }


def fetch_all_media(snapshot_dir: str | Path | None = None) -> list[dict]:
    """
    Fetch from all enabled feeds in feeds.yaml.
    Returns list of raw-feed dicts.
    Errors per feed are logged but do not stop other feeds.
    """
    feeds = _load_feeds()
    all_rows: list[dict] = []

    for fd in feeds:
        name = fd["source_name"]
        url = fd["url"]
        fetch_type = fd.get("fetch_type", "rss")

        try:
            if fetch_type == "rss":
                rows = _fetch_rss(name, url)
            else:
                rows = _fetch_archive_html(name, url)
        except Exception as exc:
            logger.error(f"{name}: unexpected error: {exc}")
            rows = []

        all_rows.extend(rows)

    logger.info(f"Media total: {len(all_rows)} items from {len(feeds)} feeds")
    return all_rows
