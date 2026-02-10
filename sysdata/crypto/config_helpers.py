"""
Configuration parsing utilities for crypto perpetual futures trading.

Provides canonical instrument ID ↔ symbol mappings and config extraction helpers.
"""
from typing import List


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
