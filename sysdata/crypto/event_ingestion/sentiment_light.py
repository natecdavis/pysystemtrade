"""
Lightweight keyword-based sentiment scorer for headline titles.

Returns float in [-1, +1].  No NLP or ML — title text only.
"""

from __future__ import annotations

import re

_POSITIVE = [
    "approval", "approved", "approves", "launches", "launch", "wins", "win",
    "inflows", "inflow", "partnership", "adoption", "adopts", "support",
    "license", "licensed", "milestone", "upgrade", "breakthrough", "bullish",
    "record", "all-time high", "ath", "rally", "surge", "integration",
    "listing", "lists", "adds", "gain", "positive", "growth", "expands",
    "leading", "new high", "innovation", "institutional", "etf approv",
]

_NEGATIVE = [
    "hack", "hacked", "exploit", "exploited", "drain", "delist", "delists",
    "sued", "lawsuit", "charges", "charged", "crackdown", "depeg", "depegged",
    "breach", "stolen", "ban", "banned", "crash", "collapse", "loses",
    "loss", "outflows", "outflow", "warning", "concern", "risk", "fraud",
    "scam", "vulnerability", "attack", "seized", "shut down", "bankruptcy",
    "insolvency", "investigation", "arrested", "indicted", "penalty",
    "fine", "sanction", "downgrade", "bearish", "all-time low",
]

_POS_RE = re.compile("|".join(re.escape(t) for t in _POSITIVE), re.I)
_NEG_RE = re.compile("|".join(re.escape(t) for t in _NEGATIVE), re.I)


def score_title(title: str) -> float:
    """
    Returns sentiment in [-1, +1].
    Formula: (pos_count - neg_count) / max(pos_count + neg_count, 1)
    """
    pos = len(_POS_RE.findall(title))
    neg = len(_NEG_RE.findall(title))
    denom = max(pos + neg, 1)
    raw = (pos - neg) / denom
    return max(-1.0, min(1.0, raw))
