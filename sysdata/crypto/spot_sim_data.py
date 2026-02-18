"""
Main simulation data adapter for spot crypto.

This module provides the csvSpotSimData class which inherits from simData
and provides all the necessary methods for backtesting crypto trading strategies.

Supports optional dynamic universe with walk-forward cost estimation.

Example usage:
    from sysdata.crypto import csvSpotSimData

    data = csvSpotSimData(data_path='/path/to/crypto/csvs')

    # Use with a System
    from systems.basesystem import System
    system = System(stage_list, data=data, config=config)

    # With dynamic universe
    data = csvSpotSimData(
        data_path='/path/to/crypto/csvs',
        use_dynamic_universe=True,
    )
"""

import datetime
from typing import List, Optional, Dict

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

    Supports optional dynamic universe with walk-forward cost estimation.

    Args:
        data_path: Path to directory containing CSV price files
        instrument_config: Optional dict of instrument configurations
        instrument_config_file: Optional path to YAML instrument config file
        use_dynamic_universe: Enable walk-forward universe filtering
        dynamic_universe_config: Config dict for dynamic universe
        log: Logger instance

    Example:
        data = csvSpotSimData(data_path='/data/crypto')
        prices = data.daily_prices('BTC')
        instruments = data.get_instrument_list()

        # With dynamic universe
        data = csvSpotSimData(
            data_path='/data/crypto',
            use_dynamic_universe=True,
            dynamic_universe_config={
                'max_sr_cost_per_trade': 0.01,
                'max_sr_cost_annual': 0.13,
                'stack_turnover': 15,
            }
        )
    """

    def __init__(
        self,
        data_path: str,
        instrument_config: dict = arg_not_supplied,
        instrument_config_file: str = arg_not_supplied,
        use_dynamic_universe: bool = False,
        dynamic_universe_config: dict = arg_not_supplied,
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
        self._use_dynamic_universe = use_dynamic_universe

        # Initialize walk-forward cost estimator if using dynamic universe
        self._cost_estimator = None
        self._universe_manager = None
        if use_dynamic_universe:
            self._init_dynamic_universe(dynamic_universe_config)

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

    def asset_class_for_instrument(self, instrument_code: str) -> str:
        """
        Get the asset class for an instrument.

        For spot crypto, all instruments are in the 'Crypto' asset class.
        This method enables RawData.normalised_price_for_asset_class() to work,
        which is needed for cross-sectional momentum (XSMOM) rules.

        Args:
            instrument_code: Instrument code

        Returns:
            Asset class name ('Crypto' for all spot crypto instruments)
        """
        return "Crypto"

    def all_instruments_in_asset_class(self, asset_class: str) -> List[str]:
        """
        Get all instruments belonging to an asset class.

        For spot crypto, returns all instruments if asset_class is 'Crypto'.
        This method enables RawData.normalised_price_for_asset_class() to work,
        which is needed for cross-sectional momentum (XSMOM) rules.

        Args:
            asset_class: Asset class name

        Returns:
            List of instrument codes in the asset class
        """
        if asset_class == "Crypto":
            return self.get_instrument_list()
        return []

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

    # =========================================================================
    # DYNAMIC UNIVERSE METHODS
    # =========================================================================

    def _init_dynamic_universe(self, config: dict):
        """Initialize walk-forward cost estimator and universe manager."""
        from sysdata.crypto.walk_forward_costs import WalkForwardCostEstimator
        from sysdata.crypto.dynamic_universe import DynamicUniverseManager

        if config is arg_not_supplied:
            config = {}

        # Create cost estimator
        self._cost_estimator = WalkForwardCostEstimator(
            prices_data=self._prices_data,
            adv_window=config.get('adv_window', 30),
            fee_bps=config.get('fee_bps', 5),
            log=self.log,
        )

        # Create universe manager
        self._universe_manager = DynamicUniverseManager(
            cost_estimator=self._cost_estimator,
            max_sr_cost_per_trade=config.get('max_sr_cost_per_trade', 0.01),
            max_sr_cost_annual=config.get('max_sr_cost_annual', 0.13),
            stack_turnover=config.get('stack_turnover', 15.0),
            forecast_weights=config.get('forecast_weights'),
            min_annual_vol=config.get('min_annual_vol', 0.0),
            vol_window=config.get('vol_window', 35),
            log=self.log,
        )

    def get_spot_volume(self, instrument_code: str) -> pd.Series:
        """
        Get volume data for an instrument.

        Args:
            instrument_code: Instrument code

        Returns:
            pd.Series with datetime index and volume values
        """
        return self._prices_data.get_spot_volume(instrument_code)

    def get_eligible_instruments_at_date(
        self,
        date: pd.Timestamp,
    ) -> List[str]:
        """
        Get list of instruments eligible for trading at a specific date.

        Only available when use_dynamic_universe=True.

        Args:
            date: Date to check

        Returns:
            List of eligible instrument codes
        """
        if not self._use_dynamic_universe:
            return self.get_instrument_list()

        all_instruments = self.get_instrument_list()
        price_data = {
            instr: self._prices_data.get_spot_prices(instr)
            for instr in all_instruments
        }

        return self._universe_manager.get_eligible_instruments(
            date=date,
            all_instruments=all_instruments,
            price_data=price_data,
        )

    def get_universe_eligibility_series(
        self,
        instrument_code: str,
    ) -> pd.Series:
        """
        Get time series of universe eligibility for an instrument.

        Only available when use_dynamic_universe=True.

        Args:
            instrument_code: Instrument code

        Returns:
            pd.Series of boolean values indicating eligibility at each date
        """
        if not self._use_dynamic_universe:
            prices = self._prices_data.get_spot_prices(instrument_code)
            return pd.Series(True, index=prices.index)

        prices = self._prices_data.get_spot_prices(instrument_code)
        return self._universe_manager.get_eligibility_series(
            instrument_code, prices
        )

    def get_universe_eligibility_df(
        self,
        instruments: List[str],
        dates: pd.DatetimeIndex,
    ) -> pd.DataFrame:
        """
        Get eligibility matrix for dynamic universe.

        Returns DataFrame with:
        - Index: dates (from input)
        - Columns: instrument codes
        - Values: boolean (True=eligible for entry)

        Only available when use_dynamic_universe=True.

        Args:
            instruments: List of instrument codes
            dates: DatetimeIndex of dates to check

        Returns:
            pd.DataFrame with dates as index, instruments as columns, boolean values
        """
        if not self._use_dynamic_universe:
            # If not using dynamic universe, all instruments eligible at all dates
            return pd.DataFrame(True, index=dates, columns=instruments)

        # Build eligibility matrix by getting series for each instrument
        eligibility_dict = {}
        for instrument in instruments:
            try:
                prices = self._prices_data.get_spot_prices(instrument)
                eligibility_series = self._universe_manager.get_eligibility_series(
                    instrument, prices
                )
                # Reindex to match requested dates, forward fill
                eligibility_dict[instrument] = eligibility_series.reindex(
                    dates, method='ffill'
                ).fillna(False)
            except Exception as e:
                self.log.warning(
                    f"Could not get eligibility for {instrument}: {str(e)}"
                )
                # If error, mark as not eligible
                eligibility_dict[instrument] = pd.Series(False, index=dates)

        return pd.DataFrame(eligibility_dict, index=dates)

    def get_walk_forward_spread(
        self,
        instrument_code: str,
    ) -> pd.Series:
        """
        Get walk-forward spread estimates for an instrument.

        Only available when use_dynamic_universe=True.

        Args:
            instrument_code: Instrument code

        Returns:
            pd.Series of spread estimates (in basis points) at each date
        """
        if not self._use_dynamic_universe or self._cost_estimator is None:
            # Return flat spread from instrument config
            spread = self._instrument_data.get_spread_cost(instrument_code)
            prices = self._prices_data.get_spot_prices(instrument_code)
            return pd.Series(spread * 10000, index=prices.index)  # Convert to bps

        return self._cost_estimator.get_spread_series(instrument_code)

    def get_cost_estimator(self):
        """Get the walk-forward cost estimator (if using dynamic universe)."""
        return self._cost_estimator

    def get_universe_manager(self):
        """Get the dynamic universe manager (if using dynamic universe)."""
        return self._universe_manager


def crypto_sim_data(
    data_path: str,
    instrument_config: dict = arg_not_supplied,
    instrument_config_file: str = arg_not_supplied,
    use_dynamic_universe: bool = False,
    dynamic_universe_config: dict = arg_not_supplied,
) -> csvSpotSimData:
    """
    Factory function to create a csvSpotSimData instance.

    Args:
        data_path: Path to directory containing CSV price files
        instrument_config: Optional dict of instrument configurations
        instrument_config_file: Optional path to YAML instrument config file
        use_dynamic_universe: Enable walk-forward universe filtering
        dynamic_universe_config: Config dict for dynamic universe

    Returns:
        csvSpotSimData instance
    """
    return csvSpotSimData(
        data_path=data_path,
        instrument_config=instrument_config,
        instrument_config_file=instrument_config_file,
        use_dynamic_universe=use_dynamic_universe,
        dynamic_universe_config=dynamic_universe_config,
    )
