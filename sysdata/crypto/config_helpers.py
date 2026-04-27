"""
Configuration parsing utilities for crypto perpetual futures trading.

Provides canonical instrument ID ↔ symbol mappings and config extraction helpers.
"""
import json
import logging
from pathlib import Path
from typing import List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


def instrument_id_to_hl_symbol(instrument_id: str) -> str:
    """
    Convert internal instrument ID to Hyperliquid symbol (base asset only).

    Hyperliquid uses bare base asset names without quote currency or suffix.
    Some 1000x-denominated contracts use a 'k' prefix (e.g. kPEPE, kSHIB).

    Examples:
        BTCUSDT_PERP      → BTC
        ETHUSDT_PERP      → ETH
        SOLUSDT_PERP      → SOL
        1000SHIBUSDT_PERP → kSHIB
        1000PEPEUSDT_PERP → kPEPE

    Args:
        instrument_id: Internal instrument ID (e.g., BTCUSDT_PERP)

    Returns:
        Hyperliquid symbol (e.g., BTC)
    """
    # Remove _PERP suffix
    base = instrument_id[:-5] if instrument_id.endswith('_PERP') else instrument_id
    # Remove USDT quote suffix
    if base.endswith('USDT'):
        base = base[:-4]
    # Hyperliquid uses 'k' prefix for 1000x-denominated contracts
    if base.startswith('1000'):
        return 'k' + base[4:]
    return base


def load_hl_symbols(
    data_dir: Optional[Path] = None,
    path: Optional[Path] = None,
) -> Set[str]:
    """
    Load available Hyperliquid symbols from data/hyperliquid_instruments.json.

    Returns empty set if the file doesn't exist (caller should warn).

    Args:
        data_dir: Directory containing hyperliquid_instruments.json.
                  Defaults to <repo_root>/data/.
        path: Explicit hyperliquid_instruments.json path. Takes precedence.

    Returns:
        Set of Hyperliquid symbol strings (e.g., {'BTC', 'ETH', 'SOL', ...})
    """
    if path is not None:
        hl_path = Path(path)
    elif data_dir is None:
        # Resolve relative to this file: sysdata/crypto/ → repo root → data/
        data_dir = Path(__file__).parent.parent.parent / 'data'
        hl_path = Path(data_dir) / 'hyperliquid_instruments.json'
    else:
        hl_path = Path(data_dir) / 'hyperliquid_instruments.json'
    if not hl_path.exists():
        return set()

    try:
        with open(hl_path) as f:
            data = json.load(f)
        return set(data.get('symbols', []))
    except Exception as e:
        logger.warning(f"Failed to load {hl_path}: {e}")
        return set()


def instrument_id_to_symbol(instrument_id: str) -> str:
    """
    Convert internal instrument ID to Binance symbol.

    Examples:
        BTCUSDT_PERP → BTCUSDT
        ETHUSDT_PERP → ETHUSDT

    Args:
        instrument_id: Internal instrument ID (e.g., BTCUSDT_PERP)

    Returns:
        Binance symbol (e.g., BTCUSDT)
    """
    if instrument_id.endswith('_PERP'):
        return instrument_id[:-5]  # Remove '_PERP' suffix
    return instrument_id


def symbol_to_instrument_id(symbol: str) -> str:
    """
    Convert Binance symbol to internal instrument ID.

    Examples:
        BTCUSDT → BTCUSDT_PERP
        ETHUSDT → ETHUSDT_PERP

    Args:
        symbol: Binance symbol (e.g., BTCUSDT)

    Returns:
        Internal instrument ID (e.g., BTCUSDT_PERP)
    """
    if not symbol.endswith('_PERP'):
        return f"{symbol}_PERP"
    return symbol


def extract_candidate_instruments(config: dict) -> List[str]:
    """
    Extract candidate instruments for data acquisition from config.

    Priority:
    1. data_acquisition.candidate_instruments (if present and non-empty)
    2. universe.layer_a_instruments (fallback for backward compatibility)

    Args:
        config: System config dict

    Returns:
        List of instrument IDs (e.g., ['BTCUSDT_PERP', 'ETHUSDT_PERP'])

    Raises:
        ValueError: If data_acquisition.candidate_instruments is present but empty
    """
    # Check for data_acquisition section first
    data_acq = config.get('data_acquisition', {})

    # If section exists, candidate_instruments must be non-empty
    if 'candidate_instruments' in data_acq:
        candidate_ids = data_acq.get('candidate_instruments', [])
        if not candidate_ids:
            raise ValueError(
                "Config error: data_acquisition.candidate_instruments is present but empty. "
                "Either provide a non-empty list or remove the section to fallback to universe.layer_a_instruments."
            )
        return candidate_ids

    # Fallback to universe for backward compatibility
    universe_config = config.get('universe', {})
    candidate_ids = universe_config.get('layer_a_instruments', [])

    if not candidate_ids:
        raise ValueError(
            "Config error: No instruments found in data_acquisition.candidate_instruments "
            "or universe.layer_a_instruments"
        )

    return candidate_ids


def extract_candidate_instruments_with_registry(
    config: dict,
    env_root: Optional[Path] = None
) -> Tuple[List[str], str]:
    """
    Extract candidate instruments with registry fallback.

    Returns:
        (instrument_ids, source_description)

    Precedence:
    1. config.data_acquisition.candidate_instruments (explicit config)
    2. discovered_candidate_instruments.json (if auto_discover=true)
    3. config.universe.layer_a_instruments (fallback)

    Args:
        config: System config dict
        env_root: Optional environment root path (needed for registry lookup)

    Returns:
        Tuple of (instrument IDs, source description string)

    Raises:
        ValueError: If no instruments found in any source
    """
    data_acq = config.get('data_acquisition', {})

    # Priority 1: Explicit config
    if 'candidate_instruments' in data_acq:
        candidate_ids = data_acq.get('candidate_instruments', [])
        if not candidate_ids:
            raise ValueError(
                "Config error: data_acquisition.candidate_instruments is present but empty. "
                "Either provide a non-empty list or remove the section."
            )
        return candidate_ids, "config.data_acquisition.candidate_instruments"

    # Priority 2: Auto-discovery
    if data_acq.get('auto_discover', False):
        if not env_root:
            logger.warning("auto_discover=true but env_root not provided, skipping registry")
        else:
            registry_path = env_root / 'data/raw/metadata/discovered_candidate_instruments.json'
            if registry_path.exists():
                try:
                    with open(registry_path) as f:
                        registry = json.load(f)
                    candidate_ids = registry.get('candidate_instruments', [])
                    if candidate_ids:
                        logger.info(f"Using auto-discovered candidates: {len(candidate_ids)} instruments")
                        return candidate_ids, "discovered_candidate_instruments.json"
                except Exception as e:
                    logger.warning(f"Failed to load registry: {e}, falling back")
            else:
                logger.warning(f"Registry not found: {registry_path}, falling back")

    # Priority 3: Fallback
    universe_config = config.get('universe', {})
    candidate_ids = universe_config.get('layer_a_instruments', [])

    if not candidate_ids:
        raise ValueError(
            "Config error: No instruments found in any source. "
            "Check config.data_acquisition.candidate_instruments or universe.layer_a_instruments"
        )

    return candidate_ids, "universe.layer_a_instruments (fallback)"


def extract_tradable_instruments(config: dict) -> List[str]:
    """
    Extract tradable universe from config.

    This is ALWAYS sourced from universe.layer_a_instruments, NOT data_acquisition.

    Args:
        config: System config dict

    Returns:
        List of instrument IDs (e.g., ['BTCUSDT_PERP', 'ETHUSDT_PERP'])
    """
    universe_config = config.get('universe', {})
    return universe_config.get('layer_a_instruments', [])


def load_registry(env_root: Path) -> dict:
    """
    Load registry from discovered_candidate_instruments.json.

    Args:
        env_root: Environment root path (e.g., Path('envs/dev'))

    Returns:
        Registry dict with 'candidate_instruments' list

    Raises:
        FileNotFoundError: If registry file doesn't exist
    """
    registry_path = env_root / 'data/raw/metadata/discovered_candidate_instruments.json'

    if not registry_path.exists():
        raise FileNotFoundError(f"Registry not found: {registry_path}")

    with open(registry_path) as f:
        return json.load(f)
