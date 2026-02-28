#!/usr/bin/env python3
"""
Build sector classification map for crypto perpetual instruments.

Fetches CoinGecko categories for each instrument's base asset and maps
them to a custom 8-sector taxonomy (L1, L2, DeFi, Meme, AI, Gaming, Infra, Exchange).

Output: data/sector_map.json
  {"BTCUSDT_PERP": "L1", "ETHUSDT_PERP": "L1", "UNIUSDT_PERP": "DeFi", ...}

Runtime: ~8-10 minutes (250 requests × 2s sleep, CoinGecko free tier).
Run once, commit result.

Usage:
    python scripts/build_sector_map.py \\
        --dataset data/dataset_538registry_6yr_365d.parquet \\
        --output data/sector_map.json

    # Dry-run (no API calls, uses cached stub data):
    python scripts/build_sector_map.py --dry-run
"""

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# ============================================================================
# SECTOR TAXONOMY
# ============================================================================
# Priority-ordered mapping: first matching keyword wins.
# All comparisons are lower-cased before matching.

CATEGORY_MAP: List[Tuple[str, List[str]]] = [
    # Priority order: first match wins. Ordered from most-specific to least-specific.
    # L2 before L1 because some L2s also mention "Smart Contract Platform"
    # L1 before DeFi because major L1s (BTC, ETH, SOL) host DeFi ecosystems and
    # therefore appear in CoinGecko's DeFi category — but they ARE L1 chains.
    ("Meme", [
        "meme", "dog", "cat coin", "frog", "meme token",
    ]),
    ("AI", [
        "artificial intelligence", "ai agents", "machine learning",
        "ai meme", " ai ",  # space-padded to avoid partial matches like "TRAIL"
    ]),
    ("L2", [
        "layer 2", "layer-2", "optimistic rollup", "zk rollup",
        "rollup", "scaling", "ethereum scaling",
    ]),
    ("L1", [
        "layer 1", "layer-1", "smart contract platform",
        "proof of work", "proof of stake",
    ]),
    ("DeFi", [
        "decentralized finance", "defi", "decentralized exchange",
        "dex", "lending", "yield", "amm", "liquid staking",
        "derivatives", "perpetual", "stablecoin",
    ]),
    ("Gaming", [
        "gaming", "metaverse", "play-to-earn", "nft gaming", "gamefi",
        "play to earn",
    ]),
    ("Exchange", [
        "centralized exchange", "cex token", "exchange-based tokens",
        "exchange token",
    ]),
    ("Infra", [
        "oracle", "interoperability", "cross-chain", "storage",
        "infrastructure", "privacy", "identity", "data availability",
        "modular blockchain", "bridging", "wallet",
    ]),
]

# Minimum number of unique instruments a sector must have in the active
# dataset after ex-self exclusion. Sectors smaller than this are collapsed
# to "Other" so that ex-self computation always has ≥ 3 peers.
MIN_SECTOR_SIZE = 3

# Manual overrides for tickers where CoinGecko resolves to the wrong coin.
# Keyed by uppercase base ticker (after stripping USDT/_PERP).
TICKER_OVERRIDES: Dict[str, str] = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
    "BNB": "binancecoin",
    "XRP": "ripple",
    "DOGE": "dogecoin",
    "ADA": "cardano",
    "AVAX": "avalanche-2",
    "DOT": "polkadot",
    "LINK": "chainlink",
    "LTC": "litecoin",
    "BCH": "bitcoin-cash",
    "APT": "aptos",
    "NEAR": "near",
    "ICP": "internet-computer",
    "ETC": "ethereum-classic",
    "FIL": "filecoin",
    "ATOM": "cosmos",
    "XLM": "stellar",
    "VET": "vechain",
    "ALGO": "algorand",
    "SUI": "sui",
    "SEI": "sei-network",
    "TIA": "celestia",
    "INJ": "injective-protocol",
    "MKR": "maker",
    "AAVE": "aave",
    "UNI": "uniswap",
    "CRV": "curve-dao-token",
    "SNX": "havven",
    "COMP": "compound-governance-token",
    "BAT": "basic-attention-token",
    "ARB": "arbitrum",
    "OP": "optimism",
    "MATIC": "matic-network",
    "FTM": "fantom",
    "SAND": "the-sandbox",
    "MANA": "decentraland",
    "AXS": "axie-infinity",
    "GALA": "gala",
    "IMX": "immutable-x",
    "GMT": "stepn",
    "SHIB": "shiba-inu",
    "PEPE": "pepe",
    "FLOKI": "floki",
    "WIF": "dogwifcoin",
    "BONK": "bonk",
    "TRUMP": "official-trump",
    "AKT": "akash-network",
}


# ============================================================================
# COINGECKO API HELPERS
# ============================================================================

COINGECKO_BASE = "https://api.coingecko.com/api/v3"

# Session for connection pooling
_session = requests.Session()
_session.headers.update({
    "Accept": "application/json",
    "User-Agent": "pysystemtrade-crypto-perps/1.0",
})


def fetch_coin_list() -> List[Dict]:
    """
    Fetch the full CoinGecko coin list.

    Returns:
        List of dicts: [{id, symbol, name}, ...]
    """
    url = f"{COINGECKO_BASE}/coins/list"
    resp = _session.get(url, timeout=30)
    resp.raise_for_status()
    return resp.json()


def build_ticker_to_id_map(coin_list: List[Dict]) -> Dict[str, str]:
    """
    Build a mapping from uppercase ticker to CoinGecko ID.

    When multiple coins share a ticker (very common), we keep all candidates
    and resolve ambiguity later by checking market cap rank.

    Returns:
        Dict[ticker_upper → coingecko_id]
        Only one ID per ticker (highest ranked / most common).
    """
    # First pass: collect all IDs for each ticker
    from collections import defaultdict
    ticker_to_ids: Dict[str, List[str]] = defaultdict(list)
    for coin in coin_list:
        ticker = coin.get("symbol", "").upper()
        if ticker:
            ticker_to_ids[ticker].append(coin["id"])

    # Second pass: prefer well-known IDs over obscure ones
    # Heuristic: shorter IDs (e.g. "bitcoin" vs "bitcoin-sv") tend to be more canonical
    result = {}
    for ticker, ids in ticker_to_ids.items():
        # Prefer the ID that exactly matches the lowercase ticker or is shortest
        exact = [i for i in ids if i.lower() == ticker.lower()]
        if exact:
            result[ticker] = exact[0]
        else:
            result[ticker] = min(ids, key=len)

    return result


def fetch_coin_categories(coin_id: str) -> List[str]:
    """
    Fetch the categories list for a single CoinGecko coin ID.

    Args:
        coin_id: CoinGecko coin ID (e.g. 'bitcoin')

    Returns:
        List of category strings (may be empty).
        Returns [] on 404 or other errors.
    """
    url = f"{COINGECKO_BASE}/coins/{coin_id}"
    params = {
        "localization": "false",
        "tickers": "false",
        "market_data": "false",
        "community_data": "false",
        "developer_data": "false",
        "sparkline": "false",
    }
    try:
        resp = _session.get(url, params=params, timeout=30)
        if resp.status_code == 404:
            logger.debug(f"  {coin_id}: 404 Not Found")
            return []
        if resp.status_code == 429:
            logger.warning(f"  {coin_id}: Rate limited (429) — sleeping 60s")
            time.sleep(60)
            # Retry once
            resp = _session.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        return data.get("categories", []) or []
    except requests.RequestException as e:
        logger.warning(f"  {coin_id}: Request failed: {e}")
        return []


def classify_categories(categories: List[str]) -> str:
    """
    Map a list of CoinGecko categories to a sector using CATEGORY_MAP.

    Priority: first match wins (CATEGORY_MAP order).

    Args:
        categories: List of category strings from CoinGecko

    Returns:
        Sector string, or 'Other' if no match found.
    """
    if not categories:
        return "Other"

    lowered = " | ".join(cat.lower() for cat in categories)

    for sector, keywords in CATEGORY_MAP:
        for kw in keywords:
            if kw in lowered:
                return sector

    return "Other"


# ============================================================================
# INSTRUMENT EXTRACTION
# ============================================================================

def extract_base_assets_from_dataset(dataset_path: str) -> List[str]:
    """
    Extract unique base asset tickers from a parquet dataset.

    Strips the USDT suffix and _PERP suffix from instrument codes.
    E.g. 'BTCUSDT_PERP' → 'BTC', 'SOLUSDT_PERP' → 'SOL'.

    Supports both wide format (instrument codes as column names) and
    long format (stacked with an 'instrument' column).

    Returns:
        Sorted list of unique base asset tickers.
    """
    import pandas as pd

    logger.info(f"Reading instrument list from: {dataset_path}")
    instruments = extract_instruments_from_dataset(dataset_path)
    logger.info(f"  Found {len(instruments)} instruments")

    base_assets = set()
    for instr in instruments:
        # Strip _PERP suffix
        code = instr.replace("_PERP", "")
        # Strip USDT suffix
        if code.endswith("USDT"):
            code = code[:-4]  # Remove 'USDT'
        elif code.endswith("USD"):
            code = code[:-3]
        base_assets.add(code)

    result = sorted(base_assets)
    logger.info(f"  Unique base assets: {len(result)}")
    return result


def extract_instruments_from_dataset(dataset_path: str) -> List[str]:
    """
    Extract the full instrument codes from the dataset.

    Supports both wide format (instrument codes as column names) and
    long/stacked format (with an 'instrument' column).
    """
    import pandas as pd
    import pyarrow.parquet as pq

    # Read column names from parquet metadata (no data loaded)
    schema = pq.read_schema(dataset_path)
    cols = schema.names

    if 'instrument' in cols:
        # Long (stacked) format: instrument codes are in the 'instrument' column
        ser = pd.read_parquet(dataset_path, columns=['instrument'])['instrument']
        return sorted(ser.unique().tolist())
    else:
        # Wide format: instrument codes are column names
        return cols


# ============================================================================
# SECTOR MAP BUILDER
# ============================================================================

def build_sector_map(
    instruments: List[str],
    base_assets: List[str],
    dry_run: bool = False,
    progress_path: Optional[Path] = None,
    existing_ticker_map: Optional[Dict[str, str]] = None,
) -> Dict[str, str]:
    """
    Build sector map by querying CoinGecko for each base asset's categories.

    Args:
        instruments: Full instrument codes (e.g. ['BTCUSDT_PERP', ...])
        base_assets: Unique base asset tickers (e.g. ['BTC', 'ETH', ...])
        dry_run: If True, skip API calls and assign 'Other' to all.
        progress_path: Path to save incremental ticker→sector progress after each fetch.
            Allows resuming from interruption. If None, no incremental saves.
        existing_ticker_map: Pre-populated ticker→sector from a previous interrupted run.

    Returns:
        Dict mapping instrument_code → sector_string.
    """
    if dry_run:
        logger.info("DRY RUN: Assigning 'Other' to all instruments")
        return {instr: "Other" for instr in instruments}

    logger.info("Fetching CoinGecko coin list...")
    coin_list = fetch_coin_list()
    logger.info(f"  {len(coin_list)} coins in CoinGecko database")

    time.sleep(12)

    ticker_to_id = build_ticker_to_id_map(coin_list)
    logger.info(f"  {len(ticker_to_id)} unique tickers mapped")

    # Fetch categories for each base asset
    # Start with any pre-existing ticker map (for resume support)
    ticker_to_sector: Dict[str, str] = dict(existing_ticker_map or {})

    n = len(base_assets)
    for i, ticker in enumerate(base_assets):
        # Skip already-classified tickers (resume mode)
        if ticker in ticker_to_sector:
            logger.info(f"  [{i+1}/{n}] {ticker}: already classified as {ticker_to_sector[ticker]} — skip")
            continue

        # Use manual override if available, otherwise fall back to heuristic ID resolution
        coin_id = TICKER_OVERRIDES.get(ticker) or ticker_to_id.get(ticker)
        if coin_id is None:
            logger.warning(f"  [{i+1}/{n}] {ticker}: Not found in CoinGecko — assigning Other")
            ticker_to_sector[ticker] = "Other"
        else:
            source = "(override)" if ticker in TICKER_OVERRIDES else ""
            logger.info(f"  [{i+1}/{n}] {ticker} → {coin_id} {source}")
            categories = fetch_coin_categories(coin_id)
            sector = classify_categories(categories)
            ticker_to_sector[ticker] = sector

            if categories:
                logger.info(f"    Categories: {categories[:3]}{'...' if len(categories) > 3 else ''}")
            logger.info(f"    → {sector}")

            # Rate limit: CoinGecko free tier is ~5 req/min; 12s sleep keeps us safely under
            time.sleep(12.0)

        # Save incremental progress after each ticker
        if progress_path is not None:
            with open(progress_path, "w") as f:
                json.dump(ticker_to_sector, f)

    # Build instrument → sector map
    sector_map: Dict[str, str] = {}
    for instr in instruments:
        code = instr.replace("_PERP", "")
        if code.endswith("USDT"):
            ticker = code[:-4]
        elif code.endswith("USD"):
            ticker = code[:-3]
        else:
            ticker = code

        sector_map[instr] = ticker_to_sector.get(ticker, "Other")

    return sector_map


def apply_minimum_sector_size(
    sector_map: Dict[str, str],
    min_size: int = MIN_SECTOR_SIZE,
) -> Dict[str, str]:
    """
    Reclassify instruments in undersized sectors to 'Other'.

    If a sector has fewer than min_size unique instruments in the dataset,
    all its instruments are reclassified to 'Other'. This ensures ex-self
    computation always has at least (min_size - 1) peers.

    Args:
        sector_map: instrument → sector map.
        min_size: Minimum instruments per sector (default 3).

    Returns:
        Updated sector_map with small sectors collapsed to 'Other'.
    """
    from collections import Counter
    sector_counts = Counter(sector_map.values())

    logger.info("\nSector distribution before minimum size guard:")
    for sector, count in sorted(sector_counts.items(), key=lambda x: -x[1]):
        flag = "" if count >= min_size or sector == "Other" else " ← UNDERSIZED"
        logger.info(f"  {sector:12s}: {count:4d}{flag}")

    small_sectors = {s for s, c in sector_counts.items() if c < min_size and s != "Other"}
    if small_sectors:
        logger.info(f"\nReclassifying undersized sectors to 'Other': {small_sectors}")
        sector_map = {
            instr: ("Other" if sector in small_sectors else sector)
            for instr, sector in sector_map.items()
        }

    return sector_map


# ============================================================================
# MAIN
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Build sector classification map from CoinGecko API",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path("data/dataset_538registry_6yr_365d.parquet"),
        help="Parquet dataset to extract instrument list from",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/sector_map.json"),
        help="Output JSON file path",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Skip API calls, assign 'Other' to all (for testing)",
    )
    # Note: resume is automatic via the _progress.json sidecar file.
    # Just re-run the same command and it will skip already-fetched tickers.
    args = parser.parse_args()

    # Extract instruments and base assets
    if not args.dataset.exists():
        logger.error(f"Dataset not found: {args.dataset}")
        sys.exit(1)

    instruments = extract_instruments_from_dataset(str(args.dataset))
    base_assets = extract_base_assets_from_dataset(str(args.dataset))

    # Progress file: saves ticker→sector incrementally; enables resume after interruption
    progress_path = args.output.parent / (args.output.stem + "_progress.json")
    existing_ticker_map: Dict[str, str] = {}
    if progress_path.exists():
        with open(progress_path) as f:
            existing_ticker_map = json.load(f)
        logger.info(
            f"Resuming from progress file: {progress_path} "
            f"({len(existing_ticker_map)} tickers already fetched)"
        )

    # Build sector map (API calls, with incremental progress saves)
    logger.info(f"\nBuilding sector map for {len(base_assets)} base assets...")
    logger.info(f"  Rate limit: 12s sleep between requests (~5 req/min, free tier safe)")
    logger.info(f"  Estimated runtime: {len(base_assets) * 12 / 60:.0f} min (no rate-limit hits)")
    logger.info(f"  Progress file: {progress_path} (safe to Ctrl-C and restart)")
    new_map = build_sector_map(
        instruments,
        base_assets,
        dry_run=args.dry_run,
        progress_path=progress_path,
        existing_ticker_map=existing_ticker_map,
    )

    sector_map = new_map

    # Apply minimum sector size guard
    sector_map = apply_minimum_sector_size(sector_map)

    # Print final distribution
    from collections import Counter
    sector_counts = Counter(sector_map.values())
    logger.info("\nFinal sector distribution:")
    for sector, count in sorted(sector_counts.items(), key=lambda x: -x[1]):
        logger.info(f"  {sector:12s}: {count:4d}")

    # Write output
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w") as f:
        json.dump(sector_map, f, indent=2, sort_keys=True)

    logger.info(f"\n✓ Sector map written to {args.output}")
    logger.info(f"  {len(sector_map)} instruments classified")

    return 0


if __name__ == "__main__":
    sys.exit(main())
