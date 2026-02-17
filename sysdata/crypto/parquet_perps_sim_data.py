"""
Parquet-backed simulation data adapter for crypto perpetual futures.

This adapter provides the simData interface required by pysystemtrade backtesting,
loading data from the canonical parquet panel format used throughout the crypto
perps pipeline.

Supports dynamic universe with walk-forward cost filtering, reading from the
same parquet datasets, manifests, and status files used by:
- update_data_monthly.py (data acquisition)
- doctor validation
- live advisory workflow

Key design principles:
- Single canonical data format (parquet panel + manifests)
- No CSV conversion or data format drift
- Deterministic candidate pool from config (candidate_instruments or registry)
- Reuses existing cost estimation and universe filtering logic
"""

import datetime
from typing import List, Optional, Dict
from pathlib import Path

import pandas as pd
import numpy as np

from syscore.constants import arg_not_supplied
from syscore.exceptions import missingData
from sysdata.sim.sim_data import simData
from syslogging.logger import get_logger

from sysobjects.spot_fx_prices import fxPrices
from sysobjects.instruments import instrumentCosts, assetClassesAndInstruments

from sysdata.crypto.prices import load_crypto_perps_panel
from sysdata.crypto.config_helpers import extract_candidate_instruments_with_registry


class parquetCryptoPerpsSimData(simData):
    """
    Simulation data adapter for crypto perpetual futures using parquet panels.

    Loads data from the canonical parquet dataset format used throughout the
    crypto perps pipeline. Supports both static and dynamic instrument universes.

    Args:
        dataset_path: Path to parquet dataset file (e.g., 'data/example_crypto_perps_30x6yr_jagged.parquet')
        config_path: Optional path to config YAML (for candidate_instruments or auto_discover)
        env_root: Optional environment root path (needed for registry lookup when auto_discover=true)
        use_dynamic_universe: Enable walk-forward universe filtering
        dynamic_universe_config: Config dict for dynamic universe (SR thresholds, etc.)
        log: Logger instance

    Example:
        # Static universe (all instruments in dataset)
        data = parquetCryptoPerpsSimData(
            dataset_path='data/example_crypto_perps_5x_live.parquet'
        )

        # Dynamic universe with config-specified candidates
        data = parquetCryptoPerpsSimData(
            dataset_path='data/example_crypto_perps_30x6yr_jagged.parquet',
            config_path='config/crypto_perps_dynamic_universe_v1.yaml',
            use_dynamic_universe=True,
            dynamic_universe_config={
                'max_sr_cost_per_trade': 0.01,
                'max_sr_cost_annual': 0.13,
                'stack_turnover': 15.0,
            }
        )
    """

    def __init__(
        self,
        dataset_path: str,
        config_path: str = arg_not_supplied,
        env_root: Path = arg_not_supplied,
        use_dynamic_universe: bool = False,
        dynamic_universe_config: dict = arg_not_supplied,
        log=get_logger("parquetCryptoPerpsSimData"),
    ):
        super().__init__(log=log)

        self._dataset_path = Path(dataset_path)
        self._config_path = config_path
        self._env_root = env_root
        self._use_dynamic_universe = use_dynamic_universe

        # Load parquet panel
        self.log.info(f"Loading parquet dataset: {self._dataset_path}")
        self._prices_df, self._meta_df, self._lifecycle_df = load_crypto_perps_panel(
            str(self._dataset_path),
            validate_schema=True,
            allow_jagged=True,  # Dynamic universe requires jagged panels
        )

        # Determine candidate instrument pool
        self._candidate_instruments = self._determine_candidate_pool()

        # Initialize dynamic universe if enabled
        self._cost_estimator = None
        self._universe_manager = None
        if use_dynamic_universe:
            self._init_dynamic_universe(dynamic_universe_config)

        self.log.info(
            f"Loaded {len(self._candidate_instruments)} instruments from dataset"
        )

    def __repr__(self):
        return f"parquetCryptoPerpsSimData with {len(self._candidate_instruments)} instruments from {self._dataset_path.name}"

    # =========================================================================
    # REQUIRED METHODS - Must be implemented (from simData)
    # =========================================================================

    def get_instrument_list(self) -> List[str]:
        """
        Get list of candidate instruments.

        Returns:
            List of instrument codes from candidate pool
        """
        return self._candidate_instruments

    def get_raw_price_from_start_date(
        self, instrument_code: str, start_date: datetime.datetime
    ) -> pd.Series:
        """
        Get raw price series from a specific start date.

        Args:
            instrument_code: Instrument code (e.g., 'BTCUSDT_PERP')
            start_date: Start date for the data

        Returns:
            pd.Series with datetime index and prices
        """
        if instrument_code not in self._prices_df.columns:
            self.log.warning(f"Instrument {instrument_code} not in dataset")
            return pd.Series(dtype=float)

        prices = self._prices_df[instrument_code].dropna()

        if len(prices) == 0:
            self.log.warning(f"No price data for {instrument_code}")
            return pd.Series(dtype=float)

        # Filter from start date
        prices = prices[prices.index >= start_date]

        return prices

    def get_instrument_currency(self, instrument_code: str) -> str:
        """
        Get the currency an instrument is quoted in.

        For crypto perps, all are quoted in USDT (USD).

        Args:
            instrument_code: Instrument code

        Returns:
            Currency code ('USD')
        """
        return "USD"

    def _get_fx_data_from_start_date(
        self, currency1: str, currency2: str, start_date: datetime.datetime
    ) -> fxPrices:
        """
        Get FX rate between two currencies from a start date.

        For crypto quoted in USD with USD base currency, returns a series of 1.0.

        Args:
            currency1: Numerator currency
            currency2: Denominator currency
            start_date: Start date for data

        Returns:
            fxPrices series (FX rate = currency1/currency2)
        """
        if currency1 == currency2:
            return self._create_fx_series_of_ones(start_date)

        self.log.warning(
            f"FX rate {currency1}/{currency2} not available, using 1.0. "
            "Consider using USD as base_currency in config."
        )
        return self._create_fx_series_of_ones(start_date)

    def _create_fx_series_of_ones(
        self, start_date: datetime.datetime
    ) -> fxPrices:
        """
        Create an FX price series of 1.0 from start_date to latest data date.
        """
        # Get date range from prices DataFrame
        if len(self._prices_df) == 0:
            end_date = datetime.datetime.now()
            index = pd.bdate_range(start=start_date, end=end_date, freq="B")
            return fxPrices(pd.Series(1.0, index=index))

        actual_start = max(start_date, self._prices_df.index.min()) if start_date else self._prices_df.index.min()
        actual_end = self._prices_df.index.max()
        index = pd.bdate_range(start=actual_start, end=actual_end, freq="B")

        fx_series = pd.Series(1.0, index=index)
        return fxPrices(fx_series)

    # =========================================================================
    # OPTIONAL METHODS - Have default implementations but can be overridden
    # =========================================================================

    def get_value_of_block_price_move(self, instrument_code: str) -> float:
        """
        Value of a 1-unit price move.

        For crypto perps, this is typically 1.0 (1 unit = 1 USD).

        Args:
            instrument_code: Instrument code

        Returns:
            Point size value (1.0 for crypto perps)
        """
        return 1.0

    def get_raw_cost_data(self, instrument_code: str) -> instrumentCosts:
        """
        Get trading cost data for an instrument.

        Reads spread and taker fee from parquet metadata.

        Args:
            instrument_code: Instrument code

        Returns:
            instrumentCosts object
        """
        # Get latest spread and fee from metadata
        try:
            meta_for_instr = self._meta_df.xs(instrument_code, level='instrument')
            latest_spread = meta_for_instr['spread_frac'].iloc[-1]
            latest_fee = meta_for_instr['taker_fee_frac'].iloc[-1]
        except (KeyError, IndexError):
            self.log.warning(f"No cost metadata for {instrument_code}, using defaults")
            latest_spread = 0.0005  # 5 bps
            latest_fee = 0.0004  # 4 bps

        # Create instrumentCosts with spread cost
        # pysystemtrade expects spread as full round-trip cost
        return instrumentCosts(
            price_slippage=latest_spread * 2,  # Half-spread * 2 for round trip
            value_of_block_commission=0.0,  # Included in percentage_cost
            percentage_cost=latest_fee,  # One-way fee (applied on both entry and exit)
        )

    # =========================================================================
    # FUTURES COMPATIBILITY METHODS - Raise missingData to trigger fallbacks
    # =========================================================================

    def get_instrument_raw_carry_data(self, instrument_code: str):
        """
        Get raw carry data for an instrument.

        For crypto perps, we have funding rates which can be used for carry.
        However, for now we raise missingData to use the standard price-based fallback.

        Args:
            instrument_code: Instrument code

        Raises:
            missingData: Always, since we use price-based carry for now
        """
        raise missingData(
            f"Carry data delegated to funding rate rules for {instrument_code}"
        )

    # =========================================================================
    # ADDITIONAL METHODS - For compatibility with system stages
    # =========================================================================

    def get_instrument_asset_classes(self) -> assetClassesAndInstruments:
        """
        Get mapping of instruments to their asset classes.

        Returns:
            assetClassesAndInstruments dict
        """
        instruments = self.get_instrument_list()
        return assetClassesAndInstruments({'Crypto': instruments})

    def asset_class_for_instrument(self, instrument_code: str) -> str:
        """
        Get the asset class for an instrument.

        Args:
            instrument_code: Instrument code

        Returns:
            Asset class name ('Crypto' for all crypto perps)
        """
        return "Crypto"

    def all_instruments_in_asset_class(self, asset_class: str) -> List[str]:
        """
        Get all instruments belonging to an asset class.

        Args:
            asset_class: Asset class name

        Returns:
            List of instrument codes in the asset class
        """
        if asset_class == "Crypto":
            return self.get_instrument_list()
        return []

    def length_of_history_in_days_for_instrument(
        self, instrument_code: str
    ) -> int:
        """
        Get the number of days of history available for an instrument.

        Args:
            instrument_code: Instrument code

        Returns:
            Number of days of price history
        """
        if instrument_code not in self._prices_df.columns:
            return 0

        prices = self._prices_df[instrument_code].dropna()
        if len(prices) == 0:
            return 0

        date_range = prices.index[-1] - prices.index[0]
        return date_range.days

    # =========================================================================
    # DYNAMIC UNIVERSE METHODS
    # =========================================================================

    def _determine_candidate_pool(self) -> List[str]:
        """
        Determine candidate pool from config (if auto_discover) or dataset.

        Priority:
        1. If config_path provided and auto_discover=true, use registry
        2. If config_path provided, use candidate_instruments
        3. Fallback: all instruments in dataset

        Returns:
            List of candidate instrument codes
        """
        # If config provided and has auto_discover, use registry-aware extraction
        if self._config_path is not arg_not_supplied:
            import yaml

            try:
                with open(self._config_path) as f:
                    config = yaml.safe_load(f)

                # Check if using registry or explicit candidates
                data_acq = config.get('data_acquisition', {})

                if 'candidate_instruments' in data_acq or data_acq.get('auto_discover', False):
                    # Use registry-aware extraction
                    env_root = self._env_root if self._env_root is not arg_not_supplied else None
                    candidate_ids, source = extract_candidate_instruments_with_registry(
                        config, env_root
                    )

                    # Filter to instruments actually in dataset
                    available = set(self._prices_df.columns)
                    filtered = [instr for instr in candidate_ids if instr in available]

                    self.log.info(f"Registry-aware candidates: {len(filtered)}/{len(candidate_ids)} from {source}")
                    if len(filtered) < len(candidate_ids):
                        missing = set(candidate_ids) - available
                        self.log.warning(f"  {len(missing)} candidates not in dataset: {sorted(list(missing))[:5]}...")

                    return filtered

            except Exception as e:
                self.log.warning(f"Failed to extract candidates from config: {e}, falling back to all dataset instruments")

        # Fallback: all instruments in dataset
        all_instruments = list(self._prices_df.columns)
        self.log.info(f"Using all {len(all_instruments)} instruments from dataset")
        return all_instruments

    def _init_dynamic_universe(self, config: dict):
        """Initialize walk-forward cost estimator and universe manager."""
        from sysdata.crypto.walk_forward_costs import WalkForwardCostEstimator
        from sysdata.crypto.dynamic_universe import DynamicUniverseManager

        if config is arg_not_supplied:
            config = {}

        # Create a minimal adapter that provides the interface WalkForwardCostEstimator expects
        class ParquetPriceAdapter:
            """Adapter to provide CSV-like interface to parquet data."""
            def __init__(self, prices_df, meta_df, log):
                self._prices_df = prices_df
                self._meta_df = meta_df
                self._log = log

            def get_spot_prices(self, instrument_code: str) -> pd.Series:
                if instrument_code not in self._prices_df.columns:
                    return pd.Series(dtype=float)
                return self._prices_df[instrument_code].dropna()

            def get_spot_volume(self, instrument_code: str) -> pd.Series:
                # Volume not in current schema, but ADV is in metadata
                # For now, return empty series and rely on ADV from metadata
                self._log.debug(f"Volume data not available for {instrument_code}, using ADV from metadata")
                return pd.Series(dtype=float)

            def get_adv_notional(self, instrument_code: str) -> pd.Series:
                """Get ADV notional from metadata."""
                try:
                    meta_for_instr = self._meta_df.xs(instrument_code, level='instrument')
                    return meta_for_instr['adv_notional']
                except (KeyError, IndexError):
                    return pd.Series(dtype=float)

        price_adapter = ParquetPriceAdapter(self._prices_df, self._meta_df, self.log)

        # Create cost estimator (override to use metadata ADV instead of calculating from volume)
        self._cost_estimator = WalkForwardCostEstimator(
            prices_data=price_adapter,
            adv_window=config.get('adv_window', 30),
            fee_bps=config.get('fee_bps', 5),
            log=self.log,
        )

        # Override spread calculation to use metadata
        original_get_spread = self._cost_estimator.get_spread_series

        def get_spread_from_metadata(instrument_code: str) -> pd.Series:
            """Use spread from metadata instead of calculating from ADV."""
            try:
                meta_for_instr = self._meta_df.xs(instrument_code, level='instrument')
                # Convert spread_frac to bps
                spread_bps = meta_for_instr['spread_frac'] * 10000
                return spread_bps
            except (KeyError, IndexError):
                # Fallback to original calculation
                return original_get_spread(instrument_code)

        self._cost_estimator.get_spread_series = get_spread_from_metadata

        # Create universe manager
        self._universe_manager = DynamicUniverseManager(
            cost_estimator=self._cost_estimator,
            max_sr_cost_per_trade=config.get('max_sr_cost_per_trade', 0.01),
            max_sr_cost_annual=config.get('max_sr_cost_annual', 0.13),
            stack_turnover=config.get('stack_turnover', 15.0),
            forecast_weights=config.get('forecast_weights'),
            log=self.log,
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
                if instrument not in self._prices_df.columns:
                    eligibility_dict[instrument] = pd.Series(False, index=dates)
                    continue

                prices = self._prices_df[instrument].dropna()
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

    def get_funding_rate(self, instrument_code: str) -> pd.Series:
        """
        Get funding rate series for an instrument.

        Args:
            instrument_code: Instrument code

        Returns:
            pd.Series with datetime index and funding rates
        """
        try:
            meta_for_instr = self._meta_df.xs(instrument_code, level='instrument')
            return meta_for_instr['funding_rate']
        except (KeyError, IndexError):
            self.log.warning(f"No funding rate data for {instrument_code}")
            return pd.Series(dtype=float)

    def get_prices_df(self, instruments: List[str] = None) -> pd.DataFrame:
        """
        Get prices as wide DataFrame (dates × instruments).

        Args:
            instruments: Subset of instruments to return. If None, returns all.

        Returns:
            pd.DataFrame with dates as index and instruments as columns.
        """
        if instruments is None:
            return self._prices_df
        cols = [c for c in instruments if c in self._prices_df.columns]
        return self._prices_df[cols]

    def get_adv_notional_df(self, instruments: List[str] = None) -> pd.DataFrame:
        """
        Get ADV notional as wide DataFrame (dates × instruments).

        Unstacks adv_notional from the multi-indexed metadata DataFrame.

        Args:
            instruments: Subset of instruments to return. If None, returns all.

        Returns:
            pd.DataFrame with dates as index and instruments as columns,
            or empty DataFrame if adv_notional not available.
        """
        try:
            adv_df = self._meta_df['adv_notional'].unstack('instrument')
            if instruments is not None:
                cols = [c for c in instruments if c in adv_df.columns]
                adv_df = adv_df[cols]
            return adv_df
        except (KeyError, AttributeError):
            self.log.warning("ADV notional not available in meta_df, returning empty DataFrame")
            return pd.DataFrame(dtype=float)
