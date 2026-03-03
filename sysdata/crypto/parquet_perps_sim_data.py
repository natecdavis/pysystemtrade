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
import json
from typing import List, Optional, Dict, Tuple
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


TAKER_FEE_FRAC = 0.0005  # 5 bps — Binance standard taker (no BNB discount)


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
        macro_data_path: str = arg_not_supplied,
        oi_data_path: str = arg_not_supplied,
        sector_map_path: str = arg_not_supplied,
        fg_data_path: str = arg_not_supplied,
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

        # Load macro factors if path provided (used by residual_momentum rule family)
        self._macro_df: Optional[pd.DataFrame] = None
        if macro_data_path is not arg_not_supplied:
            self._macro_df = pd.read_parquet(macro_data_path)
            self.log.info(
                f"Loaded macro factors from {macro_data_path}: {list(self._macro_df.columns)}"
            )

        # Load OI data if path provided (used by Phase 2 OI/Volume overlay)
        # OI parquet has columns: date, instrument (no _PERP suffix), open_interest
        self._oi_df: Optional[pd.DataFrame] = None
        if oi_data_path is not arg_not_supplied:
            raw = pd.read_parquet(oi_data_path)
            raw['date'] = pd.to_datetime(raw['date']).dt.normalize()
            # Pivot to wide format: index=date, columns=instrument (bare names, no _PERP)
            self._oi_df = raw.set_index(['date', 'instrument'])['open_interest'].unstack('instrument')
            self.log.info(
                f"Loaded OI data from {oi_data_path}: "
                f"{self._oi_df.shape[1]} instruments, "
                f"{self._oi_df.index.min().date()} to {self._oi_df.index.max().date()}"
            )

        # Load sector map if path provided (used by sector_momentum rule family)
        # Format: {"BTCUSDT_PERP": "L1", "ETHUSDT_PERP": "L1", ...}
        self._sector_map: Optional[Dict[str, str]] = None
        if sector_map_path is not arg_not_supplied:
            with open(sector_map_path) as f:
                self._sector_map = json.load(f)
            self.log.info(
                f"Loaded sector map from {sector_map_path}: "
                f"{len(self._sector_map)} instruments"
            )

        # Load Fear & Greed index if path provided (used by F&G regime overlay)
        # Parquet columns: fg_value (int 0-100), classification (str); index: date
        self._fg_df: Optional[pd.DataFrame] = None
        if fg_data_path is not arg_not_supplied and Path(fg_data_path).exists():
            self._fg_df = pd.read_parquet(fg_data_path)
            self._fg_df.index = pd.DatetimeIndex(self._fg_df.index).normalize()
            self.log.info(
                f"Loaded F&G index from {fg_data_path}: "
                f"{len(self._fg_df)} days, "
                f"{self._fg_df.index.min().date()} to {self._fg_df.index.max().date()}"
            )

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

        Uses ADV-tiered spread (time-averaged cross-sectional rank) + 5 bps taker fee.

        Args:
            instrument_code: Instrument code

        Returns:
            instrumentCosts object
        """
        spread_bps = self._get_adv_tiered_spread_bps(instrument_code)

        # Build instrumentCosts using percentage_cost only (fraction of trade value).
        #
        # pysystemtrade's price_slippage is an *absolute price* quantity (e.g. $50/contract
        # for BTC). We store spread as a *fraction* (e.g. 0.0005 = 5 bps), so setting
        # price_slippage=fraction would make it negligible for high-price instruments and
        # catastrophically large for micro-cap instruments with thousands of contracts.
        #
        # Instead we fold the half-spread into percentage_cost so all costs scale with
        # notional trade value:
        #   one-way cost = half_spread + fee
        #   round-trip   = spread + 2×fee
        one_way_cost_frac = (spread_bps / 2.0 / 10000.0) + TAKER_FEE_FRAC
        return instrumentCosts(
            price_slippage=0.0,
            value_of_block_commission=0.0,
            percentage_cost=one_way_cost_frac,
        )

    def _get_adv_tiered_spread_bps(self, instrument_code: str) -> float:
        """
        Return spread in bps based on time-averaged cross-sectional ADV rank.

        Tier: top 20 → 2 bps, rank 21-70 → 5 bps, rest → 12 bps.
        """
        try:
            adv_wide = self._meta_df['adv_notional'].unstack('instrument')
            avg_adv = adv_wide.mean()
            rank = avg_adv.rank(ascending=False, na_option='bottom')
            instr_rank = rank.get(instrument_code, float('inf'))
            if instr_rank <= 20:
                return 2.0
            elif instr_rank <= 70:
                return 5.0
            else:
                return 12.0
        except Exception:
            self.log.warning(
                f"ADV rank failed for {instrument_code}, using 5 bps spread"
            )
            return 5.0

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

        # Build ADV panel for cross-sectional rank spread model
        try:
            adv_panel = self._meta_df['adv_notional'].unstack('instrument')
        except (KeyError, Exception):
            adv_panel = None

        # Create cost estimator with cross-sectional ADV panel for time-varying spread
        self._cost_estimator = WalkForwardCostEstimator(
            prices_data=price_adapter,
            adv_window=config.get('adv_window', 30),
            fee_bps=config.get('fee_bps', 5),
            adv_panel=adv_panel,
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
            min_history_mode=config.get('min_history_rule_requirement', 'any_rule'),
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

    # =========================================================================
    # CROSS-SECTIONAL DATA METHODS
    # Referenced in trading rule YAML via ``data.method_name``
    # =========================================================================

    def _compute_adv_weighted_index(
        self,
        prices: pd.DataFrame,
        adv: pd.DataFrame,
    ) -> pd.Series:
        """
        Shared helper: build ADV-weighted price index rebased to 100 at first valid date.

        Used by both ``get_asset_class_index_price`` (whole-market index) and
        ``get_sector_index_price`` (sector-specific ex-self index).

        Args:
            prices: dates × instruments close prices (NaN where not yet listed).
            adv: dates × instruments ADV notional (aligned to same index as prices).

        Returns:
            pd.Series with the same DatetimeIndex as prices, rebased to 100 at first valid.
        """
        adv_aligned = adv.reindex(prices.index, method="ffill")
        adv_smooth = adv_aligned.rolling(30, min_periods=5).mean()

        # Zero-out ADV where price is missing so those instruments don't contribute
        adv_smooth = adv_smooth.where(prices.notna(), other=0.0)
        adv_smooth = adv_smooth.clip(lower=0.0)

        # Normalise weights row-wise
        row_sums = adv_smooth.sum(axis=1).replace(0.0, np.nan)
        weights = adv_smooth.div(row_sums, axis=0)

        # Weighted average price
        index_price = (prices * weights).sum(axis=1)
        index_price = index_price.replace(0.0, np.nan).dropna()

        # Rebase to 100 at first valid date
        first_valid = index_price.first_valid_index()
        if first_valid is not None:
            index_price = index_price / index_price[first_valid] * 100.0

        return index_price

    def get_asset_class_index_price(self, instrument_code: str) -> pd.Series:
        """
        ADV-weighted crypto asset-class price index, rebased to 100 at first date.

        The ``instrument_code`` argument is ignored — the same index is returned
        for every instrument. This supports the ``assettrend`` and ``mrinasset``
        trading rules which treat the whole asset class as a single entity.

        The index is built from daily close prices weighted by each instrument's
        30-day rolling average ADV notional, normalised to sum to 1 each day.
        Only instruments with both price and ADV data on a given date contribute.

        Returns:
            pd.Series with the same DatetimeIndex as the price panel, values ≥ 0.
        """
        if hasattr(self, "_asset_index_cache"):
            return self._asset_index_cache

        prices = self._prices_df.copy()

        # Build ADV weight matrix (dates × instruments)
        try:
            adv_wide = self._meta_df["adv_notional"].unstack("instrument")
        except KeyError:
            self.log.warning(
                "adv_notional not in meta_df; falling back to equal-weight index"
            )
            adv_wide = pd.DataFrame(1.0, index=prices.index, columns=prices.columns)

        self._asset_index_cache = self._compute_adv_weighted_index(prices, adv_wide)
        return self._asset_index_cache

    def _build_sector_components(self) -> Dict[str, Dict[str, Tuple[pd.Series, pd.Series]]]:
        """
        Pre-build price and ADV series per sector for efficient ex-self lookup.

        Called once on first ``get_sector_index_price`` invocation and cached.

        Returns:
            {sector: {instrument_code: (price_series, adv_series)}}
        """
        components: Dict[str, Dict[str, Tuple[pd.Series, pd.Series]]] = {}

        if self._sector_map is None:
            return components

        # Build ADV panel once
        try:
            adv_wide = self._meta_df["adv_notional"].unstack("instrument")
        except KeyError:
            # Fall back to equal-weight (all ADV = 1.0)
            adv_wide = pd.DataFrame(1.0, index=self._prices_df.index, columns=self._prices_df.columns)

        for instrument_code, sector in self._sector_map.items():
            if sector == "Other":
                continue
            if instrument_code not in self._prices_df.columns:
                continue

            price_series = self._prices_df[instrument_code]
            adv_series = adv_wide.get(instrument_code, pd.Series(1.0, index=self._prices_df.index))

            if sector not in components:
                components[sector] = {}
            components[sector][instrument_code] = (price_series, adv_series)

        return components

    def get_sector_index_price(self, instrument_code: str) -> pd.Series:
        """
        ADV-weighted sector price index for instrument_code's sector, EXCLUDING
        the queried instrument itself (ex-self computation prevents self-reference).

        Returns empty pd.Series if:
        - Sector map not loaded (sector_map_path not provided at construction)
        - Instrument classified as 'Other'
        - Sector has fewer than 3 members after excluding self

        Caches pre-built sector component data on first call.

        Args:
            instrument_code: Instrument code (e.g. 'UNIUSDT_PERP')

        Returns:
            pd.Series rebased to 100 at first valid date, same DatetimeIndex
            as the price panel. Empty Series if sector data unavailable.
        """
        # NaN-filled series with the price panel's DatetimeIndex.
        # Must use the price panel's index, NOT an empty series, because
        # pysystemtrade's robust_vol_calc does `vol_min.iloc[0] = 0.0` which
        # crashes on a zero-length series.
        _nan_series = pd.Series(np.nan, index=self._prices_df.index)

        if self._sector_map is None:
            return _nan_series

        sector = self._sector_map.get(instrument_code, "Other")
        if sector == "Other":
            return _nan_series

        # Build cache on first call
        if not hasattr(self, "_sector_components_cache"):
            self._sector_components_cache = self._build_sector_components()

        components = self._sector_components_cache.get(sector, {})
        # Ex-self: exclude the queried instrument from its own sector index
        peers = {k: v for k, v in components.items() if k != instrument_code}

        if len(peers) < 3:
            # Fewer than 3 peers → NaN forecast → rule contributes nothing
            return _nan_series

        # Build DataFrames for the shared helper
        prices_df = pd.DataFrame(
            {code: series[0] for code, series in peers.items()}
        )
        adv_df = pd.DataFrame(
            {code: series[1] for code, series in peers.items()}
        )

        return self._compute_adv_weighted_index(prices_df, adv_df)

    def get_cross_sectional_median_funding(self, instrument_code: str) -> pd.Series:
        """
        Cross-sectional median of annualised funding rates across all instruments.

        The ``instrument_code`` argument is ignored — the same series is returned
        for every instrument. Used by the ``relcarry`` trading rule.

        Funding is annualised as ``rate × 3 × 365`` (assumes 8-hourly payments).

        Returns:
            pd.Series with a DatetimeIndex (union of all instruments' dates).
        """
        if hasattr(self, "_median_funding_cache"):
            return self._median_funding_cache

        try:
            funding_wide = (
                self._meta_df["funding_rate"]
                .unstack("instrument")
                .astype(float)
            )
        except KeyError:
            self.log.warning("funding_rate not in meta_df; returning zeros")
            idx = self._prices_df.index
            self._median_funding_cache = pd.Series(0.0, index=idx)
            return self._median_funding_cache

        ann_funding_wide = funding_wide * 3 * 365
        median_series = ann_funding_wide.median(axis=1)

        self._median_funding_cache = median_series
        return self._median_funding_cache

    def get_open_interest(self, instrument_code: str) -> pd.Series:
        """
        Get daily open interest (USD notional) for an instrument.

        Looks up the bare instrument name (strips _PERP suffix) in the OI dataset.
        Returns empty series if OI data is not loaded or instrument not found.

        Args:
            instrument_code: Instrument code (e.g., 'BTCUSDT_PERP')

        Returns:
            pd.Series with datetime index and USD notional OI values
        """
        if self._oi_df is None:
            return pd.Series(dtype=float)

        # Strip _PERP suffix: OI data uses bare names (BTCUSDT, not BTCUSDT_PERP)
        bare_code = instrument_code.replace('_PERP', '')

        if bare_code not in self._oi_df.columns:
            return pd.Series(dtype=float)

        return self._oi_df[bare_code].dropna()

    def get_oi_volume_ratio(self, instrument_code: str, window: int = 7) -> pd.Series:
        """
        Calculate OI/Volume ratio as a leverage indicator.

        High OI/Volume → large outstanding positions relative to trading activity
        → elevated leverage → higher liquidation cascade risk.

        Uses ADV (average daily volume notional) from the main dataset as the
        volume denominator. ADV is already a smoothed metric, so this naturally
        gives a stable leverage ratio without additional smoothing.

        Args:
            instrument_code: Instrument code (e.g., 'BTCUSDT_PERP')
            window: Rolling window for volume smoothing (days). Default 7.
                    Since adv_notional is already smoothed, this adds minimal
                    extra stability.

        Returns:
            pd.Series with datetime index and OI/Volume ratio values
        """
        oi = self.get_open_interest(instrument_code)

        if len(oi) == 0:
            return pd.Series(dtype=float)

        # Get ADV from main dataset metadata
        try:
            meta = self._meta_df.xs(instrument_code, level='instrument')
            adv = meta['adv_notional'].dropna()
        except (KeyError, Exception):
            return pd.Series(dtype=float)

        if len(adv) == 0:
            return pd.Series(dtype=float)

        # Align OI and ADV to common dates
        common_idx = oi.index.intersection(adv.index)
        if len(common_idx) == 0:
            return pd.Series(dtype=float)

        oi_aligned = oi.reindex(common_idx)
        adv_aligned = adv.reindex(common_idx)

        # Apply rolling window to ADV for extra smoothness
        adv_smoothed = adv_aligned.rolling(window, min_periods=max(window // 2, 1)).mean()

        # Compute ratio, clipping ADV to avoid division by near-zero
        min_adv = adv_smoothed[adv_smoothed > 0].quantile(0.05) if (adv_smoothed > 0).any() else 1e6
        ratio = oi_aligned / adv_smoothed.clip(lower=max(min_adv, 1e6))

        return ratio.dropna()

    def get_oi_regime_multiplier(
        self,
        instrument_code: str,
        lookback: int = 90,
        threshold: float = 2.0,
        min_scale: float = 0.5,
        base_position: pd.Series = None,
        trend_forecast: pd.Series = None,
        trend_aware: bool = False,
        mode: str = 'funding',
        oi_volume_window: int = 7,
    ) -> pd.Series:
        """
        Position scaler based on funding rate z-score (OI proxy for Phase 1 MVP).

        Uses funding rate as a proxy for open interest regime. High positive/negative
        funding indicates crowded positioning and elevated liquidation cascade risk.

        The multiplier scales positions down when |z-score| exceeds threshold:
            - Normal funding (|z| < threshold)   → multiplier = 1.0 (no scaling)
            - Extreme funding (|z| ≥ threshold)  → multiplier ∈ [min_scale, 1.0]

        Linear interpolation between threshold and threshold + sensitivity range.

        Phase 1:   mode='funding'   — funding rate z-score (no new data required)
        Phase 1.5: trend_aware=True — only reduces counter-trend positions
        Phase 2:   mode='oi_volume' — OI/Volume ratio z-score (requires OI data)

        Args:
            instrument_code: Instrument code
            lookback: Rolling window for z-score calculation (days)
            threshold: Z-score threshold where scaling begins (σ units)
            min_scale: Minimum position multiplier (0.5 = max 50% reduction)
            base_position: Current position series (for trend-aware mode)
            trend_forecast: Trend forecast series (for trend-aware mode)
            trend_aware: If True, only reduce counter-trend positions (Phase 1.5)
            mode: Signal source — 'funding' (Phase 1) or 'oi_volume' (Phase 2)
            oi_volume_window: Rolling window for ADV smoothing in oi_volume mode

        Returns:
            pd.Series with values in [min_scale, 1.0]
                - 1.0 = normal regime (no scaling)
                - min_scale = extreme regime (max reduction)

        Trend-Aware Mode (trend_aware=True):
            Only applies scaling when position fights the trend:
            - Position aligned with trend → multiplier = 1.0 (keep position)
            - Position fights trend → multiplier ∈ [min_scale, 1.0] (allow reduction)

            This avoids whipsaw during bounces (trend keeps profitable positions intact).

        Example:
            lookback=90, threshold=2.0, min_scale=0.5:
                z=0.0  → mult=1.0 (no scaling)
                z=2.0  → mult=1.0 (threshold, scaling starts)
                z=3.0  → mult=0.5 (max scaling)
                z=4.0+ → mult=0.5 (capped at min_scale)

            Trend-aware example:
                position=+100, trend_forecast=+10 → aligned → mult=1.0 (keep)
                position=+100, trend_forecast=-10 → counter-trend → allow scaling
        """
        # Build the signal series depending on mode
        if mode == 'oi_volume':
            signal = self.get_oi_volume_ratio(instrument_code, window=oi_volume_window)
            if len(signal) == 0:
                # No OI data — fall back to funding proxy and warn
                self.log.warning(
                    f"{instrument_code}: No OI data for oi_volume mode, "
                    f"falling back to funding proxy"
                )
                mode = 'funding'

        if mode == 'funding':
            funding = self.get_funding_rate(instrument_code)
            if len(funding) == 0:
                self.log.warning(
                    f"{instrument_code}: No funding data, OI multiplier = 1.0 (no scaling)"
                )
                prices = self.daily_prices(instrument_code)
                return pd.Series(1.0, index=prices.index)
            # Annualize: funding paid 3x per day (8-hourly) × 365 days
            signal = funding * 3 * 365

        # Rolling mean and std for z-score
        rolling_mean = signal.rolling(lookback, min_periods=30).mean()
        rolling_std = signal.rolling(lookback, min_periods=30).std()

        # Avoid division by zero
        rolling_std = rolling_std.replace(0.0, 0.01)

        # Z-score (standardized signal)
        z_score = (signal - rolling_mean) / rolling_std

        # Absolute z-score (bidirectional: extreme long OR short funding triggers scaling)
        z_abs = z_score.abs()

        # Linear scaling: multiplier decreases from 1.0 → min_scale as z increases
        # Sensitivity: how much multiplier decreases per σ above threshold
        sensitivity = (1.0 - min_scale) / threshold

        # Calculate base multiplier (same as original logic)
        # When z_abs < threshold: mult = 1.0 (no scaling)
        # When z_abs ≥ threshold: mult = 1.0 - (z_abs - threshold) × sensitivity
        base_multiplier = 1.0 - (z_abs - threshold) * sensitivity
        base_multiplier = base_multiplier.clip(lower=min_scale, upper=1.0)
        base_multiplier = base_multiplier.fillna(1.0)

        # Apply trend-aware logic if enabled
        if trend_aware and base_position is not None and trend_forecast is not None:
            # Align all series to common index
            common_index = base_position.index.intersection(trend_forecast.index).intersection(base_multiplier.index)

            if len(common_index) == 0:
                self.log.warning(
                    f"{instrument_code}: No common dates for trend-aware overlay, using base multiplier"
                )
                return base_multiplier

            # Subset to common index
            pos = base_position.reindex(common_index)
            trend = trend_forecast.reindex(common_index)
            mult = base_multiplier.reindex(common_index)

            # Calculate position-trend alignment
            # Aligned: both positive OR both negative (product > 0)
            # Counter-trend: opposite signs (product < 0)
            alignment = pos * trend

            # Only apply scaling when position fights trend (alignment <= 0)
            # When aligned (alignment > 0), set multiplier to 1.0 (no scaling)
            trend_aware_multiplier = pd.Series(1.0, index=common_index)
            counter_trend_mask = alignment <= 0
            trend_aware_multiplier.loc[counter_trend_mask] = mult.loc[counter_trend_mask]

            # Log trend-aware behavior
            n_aligned = (alignment > 0).sum()
            n_counter = (alignment <= 0).sum()
            n_scaled = ((counter_trend_mask) & (mult < 1.0)).sum()

            self.log.debug(
                f"{instrument_code}: Trend-aware overlay | "
                f"aligned={n_aligned} | counter-trend={n_counter} | scaled={n_scaled}",
                instrument_code=instrument_code,
            )

            return trend_aware_multiplier
        else:
            # Standard (bidirectional) mode - scale on any extreme funding
            return base_multiplier

    def get_fg_index(self) -> pd.Series:
        """
        Return the Fear & Greed Index as a daily pd.Series (values 0–100).

        Returns an empty Series if no F&G data was loaded.

        Returns:
            pd.Series with DatetimeIndex and float values in [0, 100].
        """
        if self._fg_df is None:
            return pd.Series(dtype=float)
        return self._fg_df['fg_value'].astype(float)

    def get_fg_regime_multiplier(
        self,
        greed_threshold: int = 75,
        fear_threshold: int = 25,
        min_scale: float = 0.5,
    ) -> pd.Series:
        """
        Position scaler based on the Fear & Greed Index.

        Contrarian scaling: greed indicates crowded/bubble conditions where we
        reduce exposure; fear indicates panic where trend signals are reliable
        and we do NOT suppress positions.

        Multiplier logic:
            F&G > greed_threshold → linear scale-down from 1.0 → min_scale as F&G → 100
            F&G ≤ greed_threshold → 1.0 (no scaling)

        Note: fear zone (F&G < fear_threshold) currently returns 1.0 (no boost).
        A fear boost can be added in a future iteration if greed-only filter proves effective.

        Args:
            greed_threshold: F&G level above which scaling begins (default: 75 = Greed zone)
            fear_threshold:  F&G level below which we do nothing (default: 25, reserved)
            min_scale:       Minimum multiplier in extreme greed (default: 0.5 = 50% of position)

        Returns:
            pd.Series with DatetimeIndex, values in [min_scale, 1.0].
            Returns 1.0 constant if no F&G data loaded.
        """
        fg = self.get_fg_index()
        if fg.empty:
            return pd.Series(dtype=float)

        multiplier = pd.Series(1.0, index=fg.index)

        # Greed zone: linear scale-down from threshold to 100
        greed_mask = fg > greed_threshold
        if greed_mask.any():
            scale = 1.0 - (fg - greed_threshold) / (100 - greed_threshold) * (1.0 - min_scale)
            multiplier[greed_mask] = scale[greed_mask].clip(lower=min_scale, upper=1.0)

        # Strip timezone so that reindex() works against tz-naive position indexes
        if multiplier.index.tz is not None:
            multiplier.index = multiplier.index.tz_localize(None)

        return multiplier

    def get_btc_price(self, instrument_code: str) -> pd.Series:
        """
        BTC daily price series, available from the earliest date in the dataset.

        The ``instrument_code`` argument is ignored. Used by the ``btc_lead_lag``
        trading rule to obtain the BTC signal independently of the target instrument.

        Returns:
            pd.Series with DatetimeIndex.

        Raises:
            missingData: if BTCUSDT_PERP is not present in the dataset.
        """
        btc_code = "BTCUSDT_PERP"
        if btc_code not in self._prices_df.columns:
            raise missingData(f"{btc_code} not found in dataset; btc_lead_lag unavailable")
        return self._prices_df[btc_code].dropna()

    def get_adv_notional(self, instrument_code: str) -> pd.Series:
        """
        ADV notional (USD) time series for a single instrument.

        Used by the ``illiquidity`` trading rule. Mirrors the pattern of
        ``get_funding_rate``.

        Args:
            instrument_code: Instrument code (e.g. 'SOLUSDT_PERP').

        Returns:
            pd.Series with DatetimeIndex and values in USD.
        """
        try:
            meta_for_instr = self._meta_df.xs(instrument_code, level="instrument")
            return meta_for_instr["adv_notional"]
        except (KeyError, IndexError):
            self.log.warning(f"No ADV notional data for {instrument_code}")
            return pd.Series(dtype=float)

    def get_normalised_price_this_instrument(
        self, instrument_code: str
    ) -> pd.Series:
        """
        Cumulative vol-normalised return for this instrument.

        Used by ``relmomentum`` (pysystemtrade's ``relative_momentum`` function)
        as the first data argument.

        Returns:
            pd.Series: cumulative sum of daily_return / daily_vol.
        """
        if not hasattr(self, "_norm_price_cache"):
            self._norm_price_cache: dict = {}

        if instrument_code in self._norm_price_cache:
            return self._norm_price_cache[instrument_code]

        prices = self.get_raw_price_from_start_date(
            instrument_code, pd.Timestamp("2000-01-01")
        )
        if len(prices) == 0:
            return pd.Series(dtype=float)

        from sysquant.estimators.vol import robust_vol_calc

        vol = robust_vol_calc(prices.diff())
        daily_ret = prices.diff()
        vol_filled = vol.ffill().replace(0.0, np.nan).ffill()
        norm_ret = (daily_ret / vol_filled).fillna(0.0)
        result = norm_ret.cumsum()

        self._norm_price_cache[instrument_code] = result
        return result

    def get_normalised_price_for_asset_class(
        self, instrument_code: str
    ) -> pd.Series:
        """
        Cross-sectional median of cumulative vol-normalised returns across all instruments.

        Used by ``relmomentum`` (pysystemtrade's ``relative_momentum`` function)
        as the second data argument. The ``instrument_code`` is ignored.

        Returns:
            pd.Series: cross-sectional median of per-instrument normalised prices.
        """
        if hasattr(self, "_cs_norm_price_cache"):
            return self._cs_norm_price_cache

        instruments = self.get_instrument_list()
        all_norm: dict = {}
        for inst in instruments:
            try:
                series = self.get_normalised_price_this_instrument(inst)
                if len(series) > 0:
                    all_norm[inst] = series
            except Exception:
                pass

        if not all_norm:
            self._cs_norm_price_cache = pd.Series(dtype=float)
            return self._cs_norm_price_cache

        norm_df = pd.DataFrame(all_norm)
        self._cs_norm_price_cache = norm_df.median(axis=1)
        return self._cs_norm_price_cache

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

    def get_funding_rates_df(self, instruments: List[str] = None) -> pd.DataFrame:
        """
        Return funding rates as wide DataFrame (dates × instruments).

        Args:
            instruments: Subset of instruments to return. If None, returns all.

        Returns:
            pd.DataFrame with dates as index and instruments as columns,
            or empty DataFrame if funding_rate not available.
        """
        if 'funding_rate' not in self._meta_df.columns:
            self.log.warning("funding_rate not in meta_df, returning empty DataFrame")
            return pd.DataFrame()
        try:
            panel = self._meta_df['funding_rate'].unstack('instrument')
            if instruments:
                panel = panel[[c for c in instruments if c in panel.columns]]
            return panel
        except (KeyError, AttributeError):
            self.log.warning("Could not unstack funding_rate, returning empty DataFrame")
            return pd.DataFrame()

    def get_annual_vol_df(self, instruments: List[str], vol_window: int = 35) -> pd.DataFrame:
        """
        Daily rolling annualised vol panel (dates × instruments).

        Computes log-return volatility, annualised by sqrt(252).

        Args:
            instruments: List of instrument codes.
            vol_window: Rolling window size in days (default 35).

        Returns:
            pd.DataFrame (dates × instruments) of annualised volatility.
        """
        prices = self._prices_df.reindex(columns=instruments)
        log_ret = np.log(prices / prices.shift(1))
        return log_ret.rolling(vol_window, min_periods=min(10, vol_window)).std() * np.sqrt(252)

    def get_smoothed_funding_df(self, instruments: List[str], window: int = 45) -> pd.DataFrame:
        """
        Trailing-mean annualised funding rate panel (dates × instruments).

        Returns the signed funding rate (positive = funding paid by longs).
        Annualises by ×365 (daily funding rate × 365).

        Args:
            instruments: List of instrument codes.
            window: Rolling mean window in days (default 45).

        Returns:
            pd.DataFrame (dates × instruments) of smoothed annualised funding rates.
        """
        try:
            fr = self._meta_df['funding_rate'].unstack('instrument').reindex(columns=instruments)
        except KeyError:
            self.log.warning("funding_rate not in meta_df; returning zeros for smoothed funding")
            idx = self._prices_df.index
            return pd.DataFrame(0.0, index=idx, columns=instruments)
        return fr.rolling(window, min_periods=min(10, window)).mean() * 365

    # =========================================================================
    # MACRO FACTOR DATA METHODS
    # Used by the residual_momentum rule family. All three methods ignore
    # instrument_code and return the same market-wide series.
    # Requires macro_data_path to be set in __init__.
    # =========================================================================

    def get_spx_price(self, instrument_code: str) -> pd.Series:
        """
        S&P 500 daily close price series.

        The ``instrument_code`` argument is ignored — the same series is
        returned for every instrument. Used by ``residual_momentum``.

        Returns:
            pd.Series with DatetimeIndex (empty if macro data not loaded).
        """
        return self._get_macro_column('spx', instrument_code)

    def get_dxy_price(self, instrument_code: str) -> pd.Series:
        """
        US Dollar Index (DXY) daily close price series.

        The ``instrument_code`` argument is ignored — the same series is
        returned for every instrument. Used by ``residual_momentum``.

        Returns:
            pd.Series with DatetimeIndex (empty if macro data not loaded).
        """
        return self._get_macro_column('dxy', instrument_code)

    def get_us10y_yield(self, instrument_code: str) -> pd.Series:
        """
        US 10-year Treasury yield daily series (values in %, e.g. 4.25 = 4.25%).

        The ``instrument_code`` argument is ignored — the same series is
        returned for every instrument. Used by ``residual_momentum``.

        Returns:
            pd.Series with DatetimeIndex (empty if macro data not loaded).
        """
        return self._get_macro_column('us10y', instrument_code)

    def _get_macro_column(self, col: str, instrument_code: str) -> pd.Series:
        """
        Internal helper: return a single column from the macro factors DataFrame.

        Args:
            col: Column name ('spx', 'dxy', or 'us10y').
            instrument_code: Ignored (same data returned for all instruments).

        Returns:
            pd.Series (empty float series if macro data not available).
        """
        if self._macro_df is None or col not in self._macro_df.columns:
            self.log.warning(
                f"Macro factor '{col}' not available — "
                "set macro_data_path in constructor or run download_macro_factors.py"
            )
            return pd.Series(dtype=float)
        return self._macro_df[col].dropna()
