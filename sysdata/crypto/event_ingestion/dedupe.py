"""
Deduplication for event_feed_raw rows.

Two-pass dedupe:
  1. Exact content_hash (URL-based) — drop identical fetches
  2. Title-hash clustering — group same story from multiple sources,
     compute title_count_same_day as cross-source coverage metric.
"""

from __future__ import annotations

import hashlib
import re

import pandas as pd


def _normalize_title(title: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""
    t = title.lower()
    t = re.sub(r"[^\w\s]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def make_content_hash(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:16]


def make_title_hash(title: str) -> str:
    return hashlib.sha256(_normalize_title(title).encode()).hexdigest()[:16]


def dedupe_raw(df: pd.DataFrame) -> pd.DataFrame:
    """
    Remove duplicate rows by content_hash. Keeps first occurrence.
    Input: event_feed_raw DataFrame.
    """
    if df.empty:
        return df
    return df.drop_duplicates(subset=["content_hash"], keep="first").reset_index(drop=True)


def assign_cluster_ids(df: pd.DataFrame) -> pd.DataFrame:
    """
    Group rows by (title_hash, event_date_utc) to find cross-source clusters.
    Adds columns:
      dedupe_cluster_id      — title_hash + date string
      title_count_same_day   — number of unique sources in cluster on that date
    """
    if df.empty:
        df["dedupe_cluster_id"] = pd.Series(dtype=str)
        df["title_count_same_day"] = pd.Series(dtype=int)
        return df

    df = df.copy()
    date_col = pd.to_datetime(df["event_date_utc"]).dt.date.astype(str)
    df["dedupe_cluster_id"] = df["title_hash"] + "_" + date_col

    cluster_counts = (
        df.groupby("dedupe_cluster_id")["source_name"]
        .nunique()
        .rename("title_count_same_day")
    )
    df = df.join(cluster_counts, on="dedupe_cluster_id")
    return df
