"""
Lot size provider for crypto perpetuals.

Provides lot sizes based on Binance contract specifications and conversion
utilities between notional positions and integer lots.
"""

from typing import Dict
import pandas as pd
import numpy as np
from syslogging.logger import get_logger


# Binance perpetuals lot sizes (base-asset units)
# Extended from CryptoPortfolios.LOT_SIZES with top 100 instruments
LOT_SIZES: Dict[str, float] = {
    # Major pairs
    'BTCUSDT_PERP': 0.001,
    'ETHUSDT_PERP': 0.01,
    'BNBUSDT_PERP': 0.01,

    # Large caps
    'SOLUSDT_PERP': 0.1,
    'XRPUSDT_PERP': 1.0,
    'ADAUSDT_PERP': 1.0,

    # Mid caps
    'DOGEUSDT_PERP': 10.0,
    'LTCUSDT_PERP': 0.01,
    'BCHUSDT_PERP': 0.01,  # Bitcoin Cash, similar price range to LTC
    'LINKUSDT_PERP': 0.1,
    'AVAXUSDT_PERP': 0.1,
    'MATICUSDT_PERP': 1.0,
    'DOTUSDT_PERP': 0.1,
    'ATOMUSDT_PERP': 0.1,
    'UNIUSDT_PERP': 0.1,
    'ETCUSDT_PERP': 0.1,
    'XLMUSDT_PERP': 10.0,
    'FILUSDT_PERP': 0.1,
    'TRXUSDT_PERP': 10.0,
    'ICPUSDT_PERP': 0.1,
    'APTUSDT_PERP': 0.1,

    # Smaller caps (typical lot sizes)
    'NEARUSDT_PERP': 1.0,
    'ALGOUSDT_PERP': 1.0,
    'VETUSDT_PERP': 10.0,
    'SANDUSDT_PERP': 1.0,
    'MANAUSDT_PERP': 1.0,
    'AXSUSDT_PERP': 0.1,
    'THETAUSDT_PERP': 1.0,
    'XTZUSDT_PERP': 1.0,
    'EOSUSDT_PERP': 1.0,
    'AAVEUSDT_PERP': 0.01,
    'MKRUSDT_PERP': 0.01,
    'FTMUSDT_PERP': 1.0,
    'ROSEUSDT_PERP': 10.0,
    'KSMUSDT_PERP': 0.01,
    'ZILUSDT_PERP': 10.0,
    'ENJUSDT_PERP': 1.0,
    'CHZUSDT_PERP': 10.0,
    'SUSHIUSDT_PERP': 1.0,
    'BATUSDT_PERP': 1.0,
    'ZRXUSDT_PERP': 1.0,
    'YFIUSDT_PERP': 0.001,
    'COMPUSDT_PERP': 0.01,
    'SNXUSDT_PERP': 0.1,
    'CRVUSDT_PERP': 1.0,
    'RUNEUSDT_PERP': 1.0,
    'OCEANUSDT_PERP': 10.0,
    'BELUSDT_PERP': 1.0,
    'HBARUSDT_PERP': 10.0,
    'ONEUSDT_PERP': 100.0,
    'RENUSDT_PERP': 10.0,
    'IOTAUSDT_PERP': 1.0,
    'CELOUSDT_PERP': 1.0,
    '1INCHUSDT_PERP': 1.0,
    'RLCUSDT_PERP': 1.0,
    'LRCUSDT_PERP': 10.0,
    'COTIUSDT_PERP': 10.0,
    'STMXUSDT_PERP': 100.0,
    'HOTUSDT_PERP': 1000.0,
    'MTLUSDT_PERP': 1.0,
    'TOMOUSDT_PERP': 1.0,
    'CVCUSDT_PERP': 10.0,
    'ARPAUSDT_PERP': 10.0,
    'SKLUSDT_PERP': 10.0,
    'REEFUSDT_PERP': 100.0,

    # Stablecoins (typically 1.0)
    'USDCUSDT_PERP': 1.0,
    'DAIUSDT_PERP': 1.0,
    'BUSDUSDT_PERP': 1.0,
    'TUSDUSDT_PERP': 1.0,
}

# Default lot size for unknown instruments (conservative)
DEFAULT_LOT_SIZE = 1.0


class LotSizeProvider:
    """
    Provides lot sizes and conversion utilities for crypto perpetuals.

    Lot sizes are based on Binance contract specifications, representing the
    minimum tradable quantity in base-asset units. For example:
    - BTCUSDT_PERP: 0.001 BTC (≈$85 at $85k BTC)
    - ETHUSDT_PERP: 0.01 ETH (≈$30 at $3k ETH)

    Usage:
        provider = LotSizeProvider()
        lot_size = provider.get_lot_size('BTCUSDT_PERP')  # 0.001
        lot_value = provider.get_lot_value('BTCUSDT_PERP', price=85000)  # 85.0
        lots = provider.convert_notional_to_lots(2.374, 0.001)  # 2374.0
        notional = provider.convert_lots_to_notional(2374, 0.001)  # 2.374
    """

    def __init__(
        self,
        lot_sizes: Dict[str, float] = None,
        default_lot_size: float = DEFAULT_LOT_SIZE,
        log=None
    ):
        """
        Args:
            lot_sizes: Optional dict of instrument_code -> lot_size
                      If None, uses built-in LOT_SIZES mapping
            default_lot_size: Lot size for unknown instruments
            log: Logger instance
        """
        self._lot_sizes = lot_sizes if lot_sizes is not None else LOT_SIZES
        self._default_lot_size = default_lot_size
        self._log = log if log is not None else get_logger("LotSizeProvider")

        # Track which instruments used default lot size (for diagnostics)
        self._used_default = set()

    def get_lot_size(self, instrument_code: str) -> float:
        """
        Get lot size for an instrument.

        Args:
            instrument_code: Instrument code (e.g., 'BTCUSDT_PERP')

        Returns:
            Lot size in base-asset units
        """
        lot_size = self._lot_sizes.get(instrument_code, self._default_lot_size)

        if instrument_code not in self._lot_sizes and instrument_code not in self._used_default:
            self._used_default.add(instrument_code)
            self._log.warning(
                f"No lot size mapping for {instrument_code}, using default {self._default_lot_size}"
            )

        return lot_size

    def get_lot_value(self, instrument_code: str, price: float) -> float:
        """
        Get dollar value of one lot at a given price.

        Args:
            instrument_code: Instrument code
            price: Current price

        Returns:
            Dollar value of one lot (lot_size × price)
        """
        lot_size = self.get_lot_size(instrument_code)
        return lot_size * price

    def convert_notional_to_lots(self, notional: float, lot_size: float) -> float:
        """
        Convert notional position to fractional lots.

        Used to convert ideal positions to optimizer input space.

        Args:
            notional: Position size in base-asset units (e.g., 2.374 BTC)
            lot_size: Lot size for the instrument (e.g., 0.001 BTC)

        Returns:
            Fractional lots (e.g., 2374.0)
        """
        if lot_size <= 0:
            raise ValueError(f"Lot size must be positive, got {lot_size}")

        return notional / lot_size

    def convert_lots_to_notional(self, lots: float, lot_size: float) -> float:
        """
        Convert lots (integer or fractional) to notional position.

        Used to convert optimizer output back to position space.

        Args:
            lots: Number of lots (e.g., 2374)
            lot_size: Lot size for the instrument (e.g., 0.001 BTC)

        Returns:
            Notional position in base-asset units (e.g., 2.374 BTC)
        """
        return lots * lot_size

    def get_lot_values_for_instruments(
        self,
        instrument_codes: list,
        prices: pd.Series
    ) -> pd.Series:
        """
        Get lot values for multiple instruments.

        Args:
            instrument_codes: List of instrument codes
            prices: Series of prices indexed by instrument code

        Returns:
            Series of lot values indexed by instrument code
        """
        lot_values = {}

        for instrument_code in instrument_codes:
            if instrument_code in prices.index:
                price = prices[instrument_code]
                lot_values[instrument_code] = self.get_lot_value(instrument_code, price)
            else:
                # Price not available, use NaN
                lot_values[instrument_code] = np.nan

        return pd.Series(lot_values)

    def convert_positions_to_lots(
        self,
        positions: pd.Series
    ) -> pd.Series:
        """
        Convert notional positions to fractional lots for multiple instruments.

        Args:
            positions: Series of positions indexed by instrument code

        Returns:
            Series of fractional lots indexed by instrument code
        """
        lots = {}

        for instrument_code, position in positions.items():
            lot_size = self.get_lot_size(instrument_code)
            lots[instrument_code] = self.convert_notional_to_lots(position, lot_size)

        return pd.Series(lots)

    def convert_lots_to_positions(
        self,
        lots: pd.Series
    ) -> pd.Series:
        """
        Convert lots to notional positions for multiple instruments.

        Args:
            lots: Series of lots (integer or fractional) indexed by instrument code

        Returns:
            Series of notional positions indexed by instrument code
        """
        positions = {}

        for instrument_code, lot_count in lots.items():
            lot_size = self.get_lot_size(instrument_code)
            positions[instrument_code] = self.convert_lots_to_notional(lot_count, lot_size)

        return pd.Series(positions)

    @property
    def instruments_with_mappings(self) -> list:
        """Get list of instruments with explicit lot size mappings."""
        return list(self._lot_sizes.keys())

    @property
    def instruments_using_default(self) -> set:
        """Get set of instruments that used default lot size."""
        return self._used_default.copy()
