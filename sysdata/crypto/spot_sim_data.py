"""
Main simulation data adapter for spot crypto.

This module provides the csvSpotSimData class which inherits from simData
and provides all the necessary methods for backtesting crypto trading strategies.

Example usage:
    from sysdata.crypto import csvSpotSimData

    data = csvSpotSimData(data_path='/path/to/crypto/csvs')

    # Use with a System
    from systems.basesystem import System
    system = System(stage_list, data=data, config=config)
"""

import datetime
from typing import List, Optional

import pandas as pd

from syscore.constants import arg_not_supplied
from syscore.dateutils import ARBITRARY_START
from syscore.exceptions import missingData
from sysdata.sim.sim_data import simData
from syslogging.logger import get_logger

from sysobjects.spot_fx_prices import fxPrices
from sysobjects.instruments import instrumentCosts, assetClassesAndInstruments

from sysdata.crypto.csv_spot_data import csvSpotPricesData
from sysdata.crypto.spot_instrument_data import csvSpotInstrumentData


class csvSpotSimData(simData):
    """
    Simulation data adapter for spot crypto using CSV files.

    Inherits from simData and implements all required methods for
    backtesting crypto trading strategies with pysystemtrade.

    Args:
        data_path: Path to directory containing CSV price files
        instrument_config: Optional dict of instrument configurations
        instrument_config_file: Optional path to YAML instrument config file
        log: Logger instance

    Example:
        data = csvSpotSimData(data_path='/data/crypto')
        prices = data.daily_prices('BTC')
        instruments = data.get_instrument_list()
    """

    def __init__(
        self,
        data_path: str,
        instrument_config: dict = arg_not_supplied,
        instrument_config_file: str = arg_not_supplied,
        log=get_logger("csvSpotSimData"),
    ):
        super().__init__(log=log)

        # Initialize price data reader
        self._prices_data = csvSpotPricesData(datapath=data_path, log=log)

        # Initialize instrument data
        self._instrument_data = csvSpotInstrumentData(
            instrument_config=instrument_config,
            config_file=instrument_config_file,
            log=log,
        )

        self._data_path = data_path

    def __repr__(self):
        return f"csvSpotSimData with {len(self.get_instrument_list())} instruments from {self._data_path}"

    # =========================================================================
    # REQUIRED METHODS - Must be implemented (from simData)
    # =========================================================================

    def get_instrument_list(self) -> List[str]:
        """
        Get list of all available instruments.

        Returns:
            List of instrument codes (e.g., ['BTC', 'ETH', 'SOL'])
        """
        return self._prices_data.get_list_of_instruments()

    def get_raw_price_from_start_date(
        self, instrument_code: str, start_date: datetime.datetime
    ) -> pd.Series:
        """
        Get raw price series from a specific start date.

        Args:
            instrument_code: Instrument code (e.g., 'BTC')
            start_date: Start date for the data

        Returns:
            pd.Series with datetime index and prices
        """
        prices = self._prices_data.get_spot_prices(instrument_code)

        if len(prices) == 0:
            self.log.warning(f"No price data for {instrument_code}")
            return pd.Series(dtype=float)

        # Filter from start date
        prices = prices[prices.index >= start_date]

        return prices

    def get_instrument_currency(self, instrument_code: str) -> str:
        """
        Get the currency an instrument is quoted in.

        For most crypto, this will be USD.

        Args:
            instrument_code: Instrument code

        Returns:
            Currency code (e.g., 'USD')
        """
        return self._instrument_data.get_instrument_currency(instrument_code)

    def _get_fx_data_from_start_date(
        self, currency1: str, currency2: str, start_date: datetime.datetime
    ) -> fxPrices:
        """
        Get FX rate between two currencies from a start date.

        For crypto quoted in USD with USD base currency, returns a series of 1.0.
        For other combinations, would need external FX data.

        Args:
            currency1: Numerator currency
            currency2: Denominator currency
            start_date: Start date for data

        Returns:
            fxPrices series (FX rate = currency1/currency2)
        """
        # If same currency, return 1.0
        if currency1 == currency2:
            return self._create_fx_series_of_ones(start_date)

        # Most crypto is quoted in USD, so if we're using USD as base,
        # we just return 1.0 for USD/USD
        if currency1 == "USD" and currency2 == "USD":
            return self._create_fx_series_of_ones(start_date)

        # For other currency pairs, we'd need actual FX data
        # For now, log a warning and return 1.0
        self.log.warning(
            f"FX rate {currency1}/{currency2} not available, using 1.0. "
            "Consider using USD as base_currency in config."
        )
        return self._create_fx_series_of_ones(start_date)

    def _create_fx_series_of_ones(
        self, start_date: datetime.datetime
    ) -> fxPrices:
        """
        Create an FX price series of 1.0 from start_date to today.

        Uses business day frequency to match expected pysystemtrade patterns.
        Covers the full date range of ALL instruments to support walk-forward
        instrument addition.
        """
        # Get date range based on available price data from ALL instruments
        instruments = self.get_instrument_list()
        if not instruments:
            # No instruments, return minimal series
            end_date = datetime.datetime.now()
            index = pd.bdate_range(start=start_date, end=end_date, freq="B")
            return fxPrices(pd.Series(1.0, index=index))

        # Find earliest and latest dates across ALL instruments
        # This ensures FX data covers the full range for walk-forward instrument addition
        earliest_date = None
        latest_date = None
        for instr in instruments:
            try:
                prices = self._prices_data.get_spot_prices(instr)
                if len(prices) > 0:
                    if earliest_date is None or prices.index.min() < earliest_date:
                        earliest_date = prices.index.min()
                    if latest_date is None or prices.index.max() > latest_date:
                        latest_date = prices.index.max()
            except Exception:
                continue

        if earliest_date is None:
            end_date = datetime.datetime.now()
            index = pd.bdate_range(start=start_date, end=end_date, freq="B")
        else:
            # Use the earliest instrument date as start (or provided start_date if later)
            actual_start = max(start_date, earliest_date) if start_date else earliest_date
            actual_end = latest_date if latest_date else datetime.datetime.now()
            index = pd.bdate_range(start=actual_start, end=actual_end, freq="B")

        fx_series = pd.Series(1.0, index=index)
        return fxPrices(fx_series)

    # =========================================================================
    # OPTIONAL METHODS - Have default implementations but can be overridden
    # =========================================================================

    def get_value_of_block_price_move(self, instrument_code: str) -> float:
        """
        Value of a 1-unit price move.

        For spot crypto, this is typically 1.0 (1 unit = 1 USD).

        Args:
            instrument_code: Instrument code

        Returns:
            Point size value
        """
        return self._instrument_data.get_pointsize(instrument_code)

    def get_raw_cost_data(self, instrument_code: str) -> instrumentCosts:
        """
        Get trading cost data for an instrument.

        Returns cost data including spread/slippage.

        Args:
            instrument_code: Instrument code

        Returns:
            instrumentCosts object
        """
        spread_cost = self._instrument_data.get_spread_cost(instrument_code)
        meta_data = self._instrument_data.get_instrument_meta_data(instrument_code)

        return instrumentCosts.from_meta_data_and_spread_cost(
            meta_data=meta_data, spread_cost=spread_cost
        )

    # =========================================================================
    # FUTURES COMPATIBILITY METHODS - Raise missingData to trigger fallbacks
    # =========================================================================

    def get_instrument_raw_carry_data(self, instrument_code: str):
        """
        Get raw carry data for an instrument.

        For spot crypto, carry data is not available. This raises missingData
        which causes RawData.daily_denominator_price() to fall back to using
        regular daily prices.

        Args:
            instrument_code: Instrument code

        Raises:
            missingData: Always, since spot crypto has no carry data
        """
        raise missingData(
            f"No carry data available for spot crypto instrument {instrument_code}"
        )

    # =========================================================================
    # ADDITIONAL METHODS - For compatibility with system stages
    # =========================================================================

    def get_instrument_asset_classes(self) -> assetClassesAndInstruments:
        """
        Get mapping of instruments to their asset classes.

        Used by RawData stage for grouping instruments.

        Returns:
            assetClassesAndInstruments dict
        """
        instruments = self.get_instrument_list()
        return self._instrument_data.get_asset_classes_for_instruments(instruments)

    def get_spread_cost(self, instrument_code: str) -> float:
        """
        Get spread cost for an instrument.

        Args:
            instrument_code: Instrument code

        Returns:
            Spread cost as fraction (e.g., 0.001 = 0.1%)
        """
        return self._instrument_data.get_spread_cost(instrument_code)

    def length_of_history_in_days_for_instrument(
        self, instrument_code: str
    ) -> int:
        """
        Get the number of days of history available for an instrument.

        Used by System for filtering instruments with insufficient data.

        Args:
            instrument_code: Instrument code

        Returns:
            Number of days of price history
        """
        prices = self._prices_data.get_spot_prices(instrument_code)
        if len(prices) == 0:
            return 0

        # Calculate business days between first and last price
        date_range = prices.index[-1] - prices.index[0]
        return date_range.days


def crypto_sim_data(
    data_path: str,
    instrument_config: dict = arg_not_supplied,
    instrument_config_file: str = arg_not_supplied,
) -> csvSpotSimData:
    """
    Factory function to create a csvSpotSimData instance.

    Args:
        data_path: Path to directory containing CSV price files
        instrument_config: Optional dict of instrument configurations
        instrument_config_file: Optional path to YAML instrument config file

    Returns:
        csvSpotSimData instance
    """
    return csvSpotSimData(
        data_path=data_path,
        instrument_config=instrument_config,
        instrument_config_file=instrument_config_file,
    )
