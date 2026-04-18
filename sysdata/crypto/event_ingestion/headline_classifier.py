"""
Classifies headline text into event_type, severity_score, and direction_prior
using regex patterns only (no NLP in v1).

Pattern priority is top-to-bottom — first match wins.
"""

from __future__ import annotations

import re

from sysdata.crypto.event_ingestion.schemas import EVENT_TYPE_DEFAULTS

# ---------------------------------------------------------------------------
# Pattern table: (regex, event_type)
# Each entry is tried in order; first match wins.
# ---------------------------------------------------------------------------

_PATTERNS: list[tuple[re.Pattern, str]] = [
    # Security / exploit (highest priority — very specific language)
    (re.compile(r"hack|exploit|drain|breach|stolen|compromise|vulnerabilit|attack.*protocol|protocol.*attack|rug pull|exit scam", re.I), "security_exploit"),

    # Stablecoin / peg stress
    (re.compile(r"depeg|de-peg|depegged|reserve concern|freeze.*redemption|bank.*restrict|usdc|usdt.*concern|tether.*concern", re.I), "stablecoin_custody_stress"),

    # Exchange listing / delisting (Binance-style phrasing first)
    (re.compile(r"will delist|scheduled.*delist|delist.*schedul|removal.*trading|suspend.*trading", re.I), "delisting"),
    (re.compile(r"will list|will add|adds support for|add support for|listing.*announcement|new listing|spot listing|spot trading.*launch", re.I), "listing"),

    # Futures / perpetuals
    (re.compile(r"perpetual.*futures|usd[s⊕]-m.*perpetual|coin-m.*perpetual|launches.*futures|futures.*launch|perp.*launch|launch.*perp", re.I), "futures_launch"),

    # ETF / TradFi / regulatory
    (re.compile(r"\betf\b|etp\b|spot.*bitcoin.*fund|blackrock.*bitcoin|fidelity.*bitcoin|sec.*approv|sec.*deny|sec.*reject|etf.*approv|etf.*deny|cftc.*action|regulator.*approv|license.*approv|court.*rul|legal.*clarity", re.I), "regulatory_legal"),
    (re.compile(r"sec.*lawsuit|cftc.*charges|doj.*charges|enforcement.*action|securities.*fraud|indictment|arrest.*crypto", re.I), "regulatory_legal"),

    # Token unlock / supply
    (re.compile(r"token unlock|vesting.*cliff|vesting.*schedul|treasury.*sale|treasury.*transfer|foundation.*sell|insider.*unlock|unlock.*million|large.*unlock", re.I), "unlock_supply_event"),

    # Protocol upgrade / mainnet
    (re.compile(r"mainnet.*launch|hard fork|network.*upgrade|major.*upgrade|ethereum.*upgrade|consensus.*upgrade|validator.*update|protocol.*upgrade|v\d+\.\d+.*launch|shapella|dencun|pectra", re.I), "protocol_upgrade"),

    # Macro / rates
    (re.compile(r"\bcpi\b|federal reserve|fed rate|interest rate.*hike|interest rate.*cut|tariff.*crypto|treasury.*yield|inflation.*data|jobs report", re.I), "macro_linkage"),

    # Meme / influencer
    (re.compile(r"elon musk|meme coin|viral|tiktok.*crypto|social media.*surge|celebrity.*endors", re.I), "influencer_meme"),

    # Whale / exchange flow
    (re.compile(r"whale.*moved|large.*transfer.*exchange|bitcoin.*moved.*exchange|eth.*moved.*exchange|on-chain.*transfer.*\$\d{2,}[mb]", re.I), "whale_exchange_flow"),

    # Exchange contract parameter changes
    (re.compile(r"tick size|leverage.*adjust|max leverage|position limit|margin.*ratio|collateral.*tier|funding rate.*cap", re.I), "contract_parameter_change"),
    (re.compile(r"monitoring tag|trading.*restriction|reduce.*only|margin.*suspend", re.I), "exchange_risk_flag"),
    (re.compile(r"margin.*collateral|margin.*tier|cross.*margin|isolated.*margin", re.I), "margin_change"),

    # Airdrop
    (re.compile(r"airdrop|token.*distribution|claim.*reward", re.I), "airdrop"),

    # Promo noise (lowest priority, broad patterns)
    (re.compile(r"trading competition|trading.*tournament|reward.*campaign|promo.*reward|trade.*win|share.*\$\d+.*reward", re.I), "promo_noise"),
]


def classify_title(title: str) -> tuple[str, int, int]:
    """
    Returns (event_type, severity_score, direction_prior).

    Uses first matching pattern; unmatched → ("other_announcement", 1, 0).
    Severity and direction come from EVENT_TYPE_DEFAULTS table.
    """
    for pattern, event_type in _PATTERNS:
        if pattern.search(title):
            severity, direction = EVENT_TYPE_DEFAULTS.get(event_type, (1, 0))
            return event_type, severity, direction

    return "other_announcement", 1, 0
