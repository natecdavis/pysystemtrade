"""
Maps token mentions in headline text to PERP instrument codes used by the system.

Priority:
  1. Parenthesised uppercase tickers:  "Will Delist TRU (TRU)" → TRUUSDT_PERP
  2. Longest alias match in title text
  3. Fallback: __MARKET__ if no match but market-wide language detected

Returns a list — one headline can map to multiple instruments.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

# ---------------------------------------------------------------------------
# Alias map: lowercase token name / ticker → instrument code
# ---------------------------------------------------------------------------

# Base map covers majors + common aliases
_BASE_ALIASES: dict[str, str] = {
    "bitcoin": "BTCUSDT_PERP",
    "btc": "BTCUSDT_PERP",
    "ethereum": "ETHUSDT_PERP",
    "ether": "ETHUSDT_PERP",
    "eth": "ETHUSDT_PERP",
    "solana": "SOLUSDT_PERP",
    "sol": "SOLUSDT_PERP",
    "xrp": "XRPUSDT_PERP",
    "ripple": "XRPUSDT_PERP",
    "dogecoin": "DOGEUSDT_PERP",
    "doge": "DOGEUSDT_PERP",
    "bnb": "BNBUSDT_PERP",
    "binance coin": "BNBUSDT_PERP",
    "cardano": "ADAUSDT_PERP",
    "ada": "ADAUSDT_PERP",
    "tron": "TRXUSDT_PERP",
    "trx": "TRXUSDT_PERP",
    "avalanche": "AVAXUSDT_PERP",
    "avax": "AVAXUSDT_PERP",
    "chainlink": "LINKUSDT_PERP",
    "link": "LINKUSDT_PERP",
    "polkadot": "DOTUSDT_PERP",
    "dot": "DOTUSDT_PERP",
    "polygon": "MATICUSDT_PERP",
    "matic": "MATICUSDT_PERP",
    "shiba inu": "SHIBUSDT_PERP",
    "shib": "SHIBUSDT_PERP",
    "litecoin": "LTCUSDT_PERP",
    "ltc": "LTCUSDT_PERP",
    "uniswap": "UNIUSDT_PERP",
    "uni": "UNIUSDT_PERP",
    "stellar": "XLMUSDT_PERP",
    "xlm": "XLMUSDT_PERP",
    "atom": "ATOMUSDT_PERP",
    "cosmos": "ATOMUSDT_PERP",
    "near": "NEARUSDT_PERP",
    "aptos": "APTUSDT_PERP",
    "apt": "APTUSDT_PERP",
    "sui": "SUIUSDT_PERP",
    "arbitrum": "ARBUSDT_PERP",
    "arb": "ARBUSDT_PERP",
    "optimism": "OPUSDT_PERP",
    "op": "OPUSDT_PERP",
    "injective": "INJUSDT_PERP",
    "inj": "INJUSDT_PERP",
    "filecoin": "FILUSDT_PERP",
    "fil": "FILUSDT_PERP",
    "internet computer": "ICPUSDT_PERP",
    "icp": "ICPUSDT_PERP",
    "hedera": "HBARUSDT_PERP",
    "hbar": "HBARUSDT_PERP",
    "vechain": "VETUSDT_PERP",
    "vet": "VETUSDT_PERP",
    "tezos": "XTZUSDT_PERP",
    "xtz": "XTZUSDT_PERP",
    "aave": "AAVEUSDT_PERP",
    "compound": "COMPUSDT_PERP",
    "comp": "COMPUSDT_PERP",
    "maker": "MKRUSDT_PERP",
    "mkr": "MKRUSDT_PERP",
    "curve": "CRVUSDT_PERP",
    "crv": "CRVUSDT_PERP",
    "sandbox": "SANDUSDT_PERP",
    "sand": "SANDUSDT_PERP",
    "decentraland": "MANAUSDT_PERP",
    "mana": "MANAUSDT_PERP",
    "axie infinity": "AXSUSDT_PERP",
    "axs": "AXSUSDT_PERP",
    "gala": "GALAUSDT_PERP",
    "flow": "FLOWUSDT_PERP",
    "eos": "EOSUSDT_PERP",
    "iota": "IOTAUSDT_PERP",
    "neo": "NEOUSDT_PERP",
    "dash": "DASHUSDT_PERP",
    "zcash": "ZECUSDT_PERP",
    "zec": "ZECUSDT_PERP",
    "monero": "XMRUSDT_PERP",
    "xmr": "XMRUSDT_PERP",
    "trust wallet": "TWUSDT_PERP",
    "tru": "TRUUSDT_PERP",
    "dego": "DEGOUSDT_PERP",
    "dent": "DENTUSDT_PERP",
    "pepe": "PEPEUSDT_PERP",
    "floki": "FLOKIUSDT_PERP",
    "bonk": "BONKUSDT_PERP",
    "wif": "WIFUSDT_PERP",
    "dogwifhat": "WIFUSDT_PERP",
    "render": "RENDERUSDT_PERP",
    "rndr": "RENDERUSDT_PERP",
    "fetch.ai": "FETUSDT_PERP",
    "fet": "FETUSDT_PERP",
    "worldcoin": "WLDUSDT_PERP",
    "wld": "WLDUSDT_PERP",
    "pyth": "PYTHUSDT_PERP",
    "jito": "JITOUSHDT_PERP",
    "jup": "JUPUSDT_PERP",
    "jupiter": "JUPUSDT_PERP",
    "wormhole": "WUSDT_PERP",
    "pengu": "PENGUUSDT_PERP",
    "pudgy penguins": "PENGUUSDT_PERP",
}

# Words that appear as crypto tickers but are common English words — skip them
_FALSE_POSITIVE_SKIP = {"op", "link", "sol", "uni", "near", "flow", "dash", "comp"}

# Market-wide event phrases
_MARKET_WIDE_PHRASES = [
    "crypto market", "cryptocurrency market", "digital asset",
    "bitcoin market", "altcoin market", "defi", "nft market",
    "fed ", "cpi", "tariff", "stablecoin market",
]


def build_alias_map(hl_instruments_path: str | None = None) -> dict[str, str]:
    """
    Build alias → instrument_code mapping.

    Augments _BASE_ALIASES with bare tickers derived from the HL instruments list
    (e.g., AAVEUSDT_PERP → alias 'aave' if not already present).
    """
    alias_map = dict(_BASE_ALIASES)

    if hl_instruments_path and Path(hl_instruments_path).exists():
        try:
            with open(hl_instruments_path) as f:
                hl_data = json.load(f)
            # hl_instruments.json maps HL symbol → PERP code or is a list/dict
            if isinstance(hl_data, dict):
                for perp_code in hl_data.values():
                    if isinstance(perp_code, str) and perp_code.endswith("USDT_PERP"):
                        ticker = perp_code.replace("USDT_PERP", "").lower()
                        if ticker not in alias_map and len(ticker) >= 2:
                            alias_map[ticker] = perp_code
        except Exception:
            pass

    # Sort by length descending so longest match wins
    return dict(sorted(alias_map.items(), key=lambda x: -len(x[0])))


# Compiled once at module load
_PAREN_TICKER_RE = re.compile(r"\(([A-Z0-9]{2,10})\)")
_WORD_BOUNDARY_RE: dict[str, re.Pattern] = {}


def _get_word_re(alias: str) -> re.Pattern:
    if alias not in _WORD_BOUNDARY_RE:
        _WORD_BOUNDARY_RE[alias] = re.compile(
            r"(?<![a-zA-Z0-9])" + re.escape(alias) + r"(?![a-zA-Z0-9])",
            re.IGNORECASE,
        )
    return _WORD_BOUNDARY_RE[alias]


def map_title_to_instruments(
    title: str,
    alias_map: dict[str, str],
    max_instruments: int = 5,
) -> list[str]:
    """
    Return list of PERP instrument codes mentioned in title.

    Returns ["__MARKET__"] for market-wide events with no specific asset.
    Returns [] if no match at all (caller decides what to do).
    """
    title_lower = title.lower()
    found: list[str] = []
    seen_codes: set[str] = set()

    # Priority 1: parenthesised tickers, e.g. "(TRU)"
    for m in _PAREN_TICKER_RE.finditer(title):
        ticker_upper = m.group(1)
        ticker_lower = ticker_upper.lower()
        if ticker_lower in alias_map:
            code = alias_map[ticker_lower]
            if code not in seen_codes:
                found.append(code)
                seen_codes.add(code)

    # Priority 2: longest alias match in text
    for alias, code in alias_map.items():
        if code in seen_codes:
            continue
        if len(alias) < 3 and alias in _FALSE_POSITIVE_SKIP:
            continue
        if _get_word_re(alias).search(title_lower):
            found.append(code)
            seen_codes.add(code)
        if len(found) >= max_instruments:
            break

    if found:
        return found

    # Fallback: market-wide
    for phrase in _MARKET_WIDE_PHRASES:
        if phrase in title_lower:
            return ["__MARKET__"]

    return []
