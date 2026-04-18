"""
Canonical schemas for the Phase 2 event ingestion pipeline.

Three tables:
  event_feed_raw  — one row per fetched item, before dedup/classification
  event_panel     — one row per instrument-event-day, normalized
  trends_panel    — one row per instrument-date from Google Trends
"""

from __future__ import annotations

EVENT_FEED_RAW_COLS = [
    "source_id",
    "source_type",       # exchange_announcement | media_rss | google_trends
    "source_name",       # binance | coindesk | blockworks | ...
    "fetched_at_utc",
    "published_at_utc",
    "url",
    "title",
    "summary",
    "author",
    "raw_json",
    "language",
    "content_hash",      # sha256(url) — primary dedup key
    "title_hash",        # sha256(normalized_title) — cross-source dedup
    "parse_status",      # ok | failed
    "parse_error",
]

EVENT_PANEL_COLS = [
    "event_date_utc",
    "event_time_utc",
    "instrument",                # BTCUSDT_PERP | __MARKET__ | null
    "source_name",
    "source_type",
    "title",
    "url",
    "event_type",                # listing | delisting | futures_launch | ...
    "severity_score",            # 1–5
    "direction_prior",           # +1 | -1 | 0
    "confidence_score",          # 0.0–1.0
    "attention_weight",
    "sentiment_light",           # [-1, +1]
    "is_exchange_primary",
    "is_tier1_media",
    "title_count_same_day",      # dedupe cluster size (cross-source coverage)
    "dedupe_cluster_id",
    "content_hash",
]

TRENDS_PANEL_COLS = [
    "date_utc",
    "instrument",
    "trends_term",
    "trends_value_raw",
    "trends_value_scaled",
    "trends_z_63d",
    "trends_change_7d",
    "trends_spike_flag",
    "source_name",               # google_trends
    "fetch_method",              # official_alpha | pytrends | manual_csv
]

# Event type → (severity_default, direction_prior)
EVENT_TYPE_DEFAULTS: dict[str, tuple[int, int]] = {
    "delisting":                  (5, -1),
    "listing":                    (4, +1),
    "futures_launch":             (4, +1),
    "regulatory_legal":           (4,  0),
    "security_exploit":           (5, -1),
    "unlock_supply_event":        (4, -1),
    "protocol_upgrade":           (3, +1),
    "stablecoin_custody_stress":  (4, -1),
    "macro_linkage":              (3,  0),
    "influencer_meme":            (3,  0),
    "whale_exchange_flow":        (3,  0),
    "contract_parameter_change":  (3,  0),
    "margin_change":              (3,  0),
    "exchange_risk_flag":         (3, -1),
    "airdrop":                    (2, +1),
    "promo_noise":                (1,  0),
    "other_announcement":         (1,  0),
}
