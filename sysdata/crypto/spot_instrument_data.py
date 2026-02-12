"""
Instrument metadata for spot crypto.

Provides currency, asset class, and cost information for crypto instruments.
Can be configured via YAML or uses sensible defaults.
"""

import os
from typing import Dict, Optional

import yaml

from syscore.constants import arg_not_supplied
from syslogging.logger import get_logger
from sysobjects.instruments import instrumentMetaData, assetClassesAndInstruments


# Default values for crypto instruments
DEFAULT_CURRENCY = "USD"
DEFAULT_ASSET_CLASS = "Crypto"
DEFAULT_SPREAD_COST = 0.001  # 0.1% default spread
DEFAULT_POINTSIZE = 1.0  # 1 unit = 1 USD for spot


class csvSpotInstrumentData:
    """
    Instrument metadata for spot crypto.

    Can load from a YAML config file or use defaults.

    YAML format:
        BTC:
            currency: USD
            asset_class: Crypto
            spread_cost: 0.0005
            description: Bitcoin
        ETH:
            currency: USD
            asset_class: Crypto
            spread_cost: 0.001
            description: Ethereum
    """

    def __init__(
        self,
        instrument_config: Dict = arg_not_supplied,
        config_file: str = arg_not_supplied,
        log=get_logger("csvSpotInstrumentData"),
    ):
        self._log = log
        self._instrument_config = {}

        # Load from file if provided
        if config_file is not arg_not_supplied:
            self._load_config_file(config_file)

        # Override with explicit config
        if instrument_config is not arg_not_supplied:
            self._instrument_config.update(instrument_config)

    def __repr__(self):
        instruments = list(self._instrument_config.keys())
        return f"csvSpotInstrumentData with {len(instruments)} configured instruments"

    @property
    def log(self):
        return self._log

    def _load_config_file(self, config_file: str):
        """Load instrument config from YAML file."""
        if not os.path.exists(config_file):
            self.log.warning(f"Instrument config file not found: {config_file}")
            return

        try:
            with open(config_file, "r") as f:
                config = yaml.safe_load(f)
            if config:
                self._instrument_config.update(config)
        except Exception as e:
            self.log.warning(f"Error loading instrument config: {e}")

    def _get_config_for_instrument(self, instrument_code: str) -> Dict:
        """Get config dict for an instrument, with defaults."""
        config = self._instrument_config.get(instrument_code, {})
        return config

    def get_instrument_currency(self, instrument_code: str) -> str:
        """
        Get the currency an instrument is quoted in.

        Args:
            instrument_code: The instrument code (e.g., 'BTC')

        Returns:
            Currency code (default: 'USD')
        """
        config = self._get_config_for_instrument(instrument_code)
        return config.get("currency", DEFAULT_CURRENCY)

    def get_spread_cost(self, instrument_code: str) -> float:
        """
        Get the spread cost for an instrument.

        This represents half the bid-ask spread as a fraction of price.
        For example, 0.001 = 0.1% spread cost.

        Args:
            instrument_code: The instrument code

        Returns:
            Spread cost as a fraction
        """
        config = self._get_config_for_instrument(instrument_code)
        return float(config.get("spread_cost", DEFAULT_SPREAD_COST))

    def get_instrument_asset_class(self, instrument_code: str) -> str:
        """
        Get the asset class for an instrument.

        Args:
            instrument_code: The instrument code

        Returns:
            Asset class (default: 'Crypto')
        """
        config = self._get_config_for_instrument(instrument_code)
        return config.get("asset_class", DEFAULT_ASSET_CLASS)

    def get_description(self, instrument_code: str) -> str:
        """
        Get the description for an instrument.

        Args:
            instrument_code: The instrument code

        Returns:
            Description string
        """
        config = self._get_config_for_instrument(instrument_code)
        return config.get("description", instrument_code)

    def get_pointsize(self, instrument_code: str) -> float:
        """
        Get the point size for an instrument.

        For spot crypto, this is typically 1.0 (1 unit = 1 USD).

        Args:
            instrument_code: The instrument code

        Returns:
            Point size
        """
        config = self._get_config_for_instrument(instrument_code)
        return float(config.get("pointsize", DEFAULT_POINTSIZE))

    def get_instrument_meta_data(self, instrument_code: str) -> instrumentMetaData:
        """
        Get full instrument metadata object.

        Args:
            instrument_code: The instrument code

        Returns:
            instrumentMetaData object
        """
        config = self._get_config_for_instrument(instrument_code)

        return instrumentMetaData(
            Description=config.get("description", instrument_code),
            Pointsize=config.get("pointsize", DEFAULT_POINTSIZE),
            Currency=config.get("currency", DEFAULT_CURRENCY),
            AssetClass=config.get("asset_class", DEFAULT_ASSET_CLASS),
            PerBlock=0.0,  # Not used for spot
            Percentage=config.get("spread_cost", DEFAULT_SPREAD_COST),
            PerTrade=0.0,  # Not used for spot
            Region=config.get("region", ""),
        )

    def get_asset_classes_for_instruments(
        self, instrument_list: list
    ) -> assetClassesAndInstruments:
        """
        Get asset class mapping for a list of instruments.

        Args:
            instrument_list: List of instrument codes

        Returns:
            assetClassesAndInstruments dict mapping instrument -> asset_class
        """
        asset_class_dict = {
            instrument: self.get_instrument_asset_class(instrument)
            for instrument in instrument_list
        }
        return assetClassesAndInstruments(asset_class_dict)
