"""
Mr Greedy Minimum Tracking Error Variance Portfolio for crypto perpetuals.

Implements Robert Carver's greedy algorithm to select integer lot portfolios
that minimize tracking error versus the ideal fractional portfolio, subject to
transaction cost penalties.

Key Features:
- Works with ALL instruments in universe (no pre-filtering)
- Optimizes integer lot positions (executable by construction)
- Minimizes tracking error + shadow_cost × transaction_costs
- Portfolio-level tracking error buffer prevents overtrading
- Replaces two-stage universe selection with optimal subset selection

References:
- Carver blog: "Mr Greedy and the Tale of the Minimum Tracking Error Variance"
- Implementation: systems/provided/dynamic_small_system_optimise/
"""

import pandas as pd
import numpy as np
import traceback
from typing import Dict, Optional

from syscore.constants import arg_not_supplied
from syscore.exceptions import missingData

from systems.portfolio import Portfolios
from systems.system_cache import output, diagnostic

from sysdata.crypto.lot_size_provider import LotSizeProvider

from sysquant.estimators.covariance import covarianceEstimate
from sysquant.estimators.mean_estimator import meanEstimates
from sysquant.optimisation.weights import portfolioWeights

from systems.provided.dynamic_small_system_optimise.optimisation import (
    objectiveFunctionForGreedy,
    constraintsForDynamicOpt,
)
from systems.provided.dynamic_small_system_optimise.buffering import (
    speedControlForDynamicOpt,
)


class MrGreedyPortfolio(Portfolios):
    """
    Portfolio stage using Mr Greedy optimizer to select integer lot positions.

    Replaces standard instrument weight logic with greedy optimization:
    1. Get ideal fractional positions from PositionSizing stage
    2. Build covariance matrix from instrument returns (60-day EWMA)
    3. Get lot sizes and SR costs for each instrument
    4. Run greedy optimizer to select integer lot portfolio
    5. Apply portfolio-level tracking error buffer
    6. Return integer lot-sized, executable positions

    Typical results:
    - 10-15 positions (vs 24 from two-stage system)
    - Lower tracking error (optimal selection vs random filtering)
    - 100% execution rate (all positions above $25 minimum)

    Config params (in greedy_params section):
        shadow_cost: Trade-off between tracking error and costs (default: 100)
        tracking_error_buffer: Portfolio-level buffer (default: 0.0125)
        correlation_span: EWMA span for covariance (default: 60)
        long_only: No shorting (default: True)
        max_position_fraction: Max position size (default: 0.25)
        min_history_days: Minimum history for covariance (default: 30)

    Example:
        from systems.crypto_perps.greedy_portfolio import MrGreedyPortfolio
        from systems.basesystem import System

        system = System(
            [RawData(), Rules(), ..., MrGreedyPortfolio(), Accounts()],
            data,
            config
        )

        positions = system.portfolio.get_notional_position("BTCUSDT_PERP")
        diagnostics = system.portfolio.get_greedy_diagnostics()
    """

    def __init__(self):
        super().__init__()
        self._lot_size_provider = None

        # Cache for daily optimization results
        self._optimization_cache: Dict[pd.Timestamp, portfolioWeights] = {}

        # Memoize full optimization result (same across all instruments)
        self._all_positions_memo: Optional[pd.DataFrame] = None

        # Cache for expensive intermediate calculations
        self._covariance_cache: Dict[str, covarianceEstimate] = {}  # key: f"{date}_{instrument_hash}"
        self._sr_cost_cache: Dict[str, meanEstimates] = {}  # key: f"{date}_{instrument_hash}"
        self._price_cache: Dict[str, pd.Series] = {}  # key: instrument_code

    @property
    def lot_size_provider(self) -> LotSizeProvider:
        """Get or create lot size provider."""
        if self._lot_size_provider is None:
            self._lot_size_provider = LotSizeProvider(log=self.log)
        return self._lot_size_provider

    @property
    def lot_size_notional_override(self) -> float | None:
        """
        If set via config key `lot_size_notional_override`, use a fixed USD
        lot value for every instrument instead of the Binance lot-size table.

        This simulates venues (e.g. Hyperliquid) where minimum contract sizes
        are negligible. A value of 1.0 means each 'lot' is worth $1, so
        fractional_lots ≈ position_usd — giving ~33-333 optimizer steps per
        instrument at $1K capital, which is fast and accurate.

        Leave unset (None) for normal Binance lot-size behaviour.
        """
        val = getattr(self.config, 'lot_size_notional_override', None)
        return float(val) if val is not None else None


    @output()
    def get_actual_position(self, instrument_code: str) -> pd.Series:
        """
        Override base class to bypass capital_multiplier.

        The base class multiplies notional_position by capital_multiplier() from
        the accounts stage.  For the greedy this is wrong: greedy positions are in
        integer lot counts, not token units, so the accounts stage computes wildly
        incorrect daily P&L (lot_counts × Δtoken_price instead of lots × $lot_usd ×
        return).  That sends capital to near-zero on the first big drawdown day,
        making capital_multiplier ≈ 0 and zeroing out all subsequent positions.

        Since notional_capital == actual_capital for our backtests the multiplier
        should be 1.0 throughout; just return the notional position directly.
        """
        return self.get_notional_position(instrument_code)

    def get_notional_position(self, instrument_code: str) -> pd.Series:
        """
        Get notional position using Mr Greedy optimizer.

        This overrides the base portfolio method to use greedy optimization
        instead of standard instrument weights × subsystem positions.

        Returns integer lot-sized positions that minimize tracking error
        versus the ideal fractional portfolio.

        Args:
            instrument_code: Instrument code

        Returns:
            pd.Series of integer lot-sized positions
        """
        self.log.debug(
            f"Calculating greedy-optimized position for {instrument_code}",
            instrument_code=instrument_code,
        )

        # Get optimized positions for all instruments on all dates
        optimized_positions = self._get_all_optimized_positions()

        # Extract this instrument's positions
        if instrument_code not in optimized_positions.columns:
            # Instrument not selected by optimizer
            return pd.Series(0.0, index=optimized_positions.index)

        position = optimized_positions[instrument_code]

        # Apply minimum notional filter ($25 exchange minimum)
        position = self._apply_min_notional_filter(instrument_code, position)

        return position

    def _get_all_optimized_positions(self) -> pd.DataFrame:
        """
        Get optimized positions for all instruments across all dates.

        Runs greedy optimizer for each date, caching results.

        Returns:
            pd.DataFrame with dates as index, instruments as columns
        """
        # Memoize: this method is called once per instrument by get_notional_position()
        # but the result is the same for all instruments, so cache it on first call.
        if hasattr(self, '_all_positions_memo') and self._all_positions_memo is not None:
            return self._all_positions_memo

        # Get ideal fractional positions from PositionSizing stage
        ideal_positions_df = self._get_ideal_fractional_positions()

        if ideal_positions_df.empty:
            self.log.warning("No ideal positions available, returning empty DataFrame")
            return pd.DataFrame()

        # Initialize output DataFrame
        instruments = ideal_positions_df.columns
        dates = ideal_positions_df.index
        optimized_df = pd.DataFrame(0.0, index=dates, columns=instruments)

        # Rebalance frequency: only run optimizer on scheduled dates.
        # Between rebalance dates, carry forward the previous positions unchanged.
        # "D" = daily (every date), "W" = weekly, "ME" = month-end, "QS" = quarterly.
        config = self.parent.config
        greedy_params = config.get_element_or_default('greedy_params', {})
        rebalance_freq = greedy_params.get('rebalance_freq', 'D')

        if rebalance_freq == 'D':
            rebalance_set = set(dates)
        else:
            scheduled = pd.date_range(start=dates[0], end=dates[-1], freq=rebalance_freq)
            # Snap each scheduled date to the nearest actual date in the index
            rebalance_set = set()
            dates_arr = np.array(dates, dtype='datetime64[ns]')
            for sched_date in scheduled:
                idx = np.searchsorted(dates_arr, np.datetime64(sched_date, 'ns'))
                idx = min(idx, len(dates) - 1)
                rebalance_set.add(dates[idx])
            # Always include the first date
            rebalance_set.add(dates[0])

        n_rebalance = len(rebalance_set)
        self.log.info(
            f"Greedy: rebalance_freq='{rebalance_freq}', "
            f"optimising on {n_rebalance}/{len(dates)} dates"
        )

        # Optimize for each date
        previous_positions = None
        for i, date in enumerate(dates):
            is_rebalance = date in rebalance_set

            if not is_rebalance:
                # Hold previous positions unchanged
                if previous_positions is not None:
                    for instrument_code in instruments:
                        optimized_df.loc[date, instrument_code] = previous_positions.get(
                            instrument_code, 0.0
                        )
                continue

            if i % 100 == 0 or rebalance_freq != 'D':
                self.log.info(f"Optimizing positions for date {i+1}/{len(dates)}: {date.date()}")

            try:
                optimal_positions = self._optimize_integer_positions(
                    date=date,
                    ideal_positions_df=ideal_positions_df,
                    previous_positions=previous_positions,
                )

                # Convert portfolioWeights to Series and assign to DataFrame
                for instrument_code in instruments:
                    optimized_df.loc[date, instrument_code] = optimal_positions.get(
                        instrument_code, 0.0
                    )

                # Update previous for next iteration
                previous_positions = optimal_positions

            except Exception as e:
                # Log detailed error information for debugging
                self.log.error(
                    f"Optimization failed for {date.date()}: {type(e).__name__}: {str(e)}"
                )
                self.log.error(f"Full traceback:\n{traceback.format_exc()}")

                # Log state at failure
                self.log.error(f"Instruments declared: {len(instruments)}")
                if previous_positions is not None:
                    self.log.error(f"Previous positions count: {len(previous_positions)}")
                    self.log.error(f"Previous instruments: {list(previous_positions.keys())[:10]}")

                # Keep previous positions (no trade)
                if previous_positions is not None:
                    for instrument_code in instruments:
                        optimized_df.loc[date, instrument_code] = previous_positions.get(
                            instrument_code, 0.0
                        )

        self.log.info(
            f"Greedy optimization complete. "
            f"Avg positions per day: {(optimized_df != 0).sum(axis=1).mean():.1f}"
        )

        self._all_positions_memo = optimized_df
        return optimized_df

    def _get_ideal_fractional_positions(self) -> pd.DataFrame:
        """
        Get ideal fractional positions from PositionSizing stage.

        These are the subsystem positions scaled by instrument weights and IDM,
        before lot rounding or filtering.

        Returns:
            pd.DataFrame with dates as index, instruments as columns
        """
        self.log.debug("Fetching ideal fractional positions from PositionSizing")

        instruments = self.get_instrument_list()
        positions_dict = {}

        for instrument_code in instruments:
            try:
                # Get subsystem position from PositionSizing
                subsystem_position = self.parent.positionSize.get_subsystem_position(
                    instrument_code
                )

                # Get instrument weight and IDM
                instrument_weight = self.get_instrument_weight_for_code(instrument_code)
                idm = self.get_instrument_diversification_multiplier()

                # Align on subsystem position index
                instrument_weight = instrument_weight.reindex(
                    subsystem_position.index, method='ffill'
                )
                idm = idm.reindex(subsystem_position.index, method='ffill')

                # Calculate ideal position: subsystem × weight × IDM
                ideal_position = subsystem_position * instrument_weight * idm

                positions_dict[instrument_code] = ideal_position

            except Exception as e:
                self.log.warning(
                    f"Could not get ideal position for {instrument_code}: {str(e)}"
                )
                continue

        if not positions_dict:
            self.log.error("No ideal positions computed for any instrument")
            return pd.DataFrame()

        positions_df = pd.DataFrame(positions_dict)

        # Fill NaN with 0 (instrument not tradable at that date)
        positions_df = positions_df.fillna(0.0)

        self.log.debug(
            f"Ideal positions shape: {positions_df.shape}, "
            f"avg non-zero per day: {(positions_df != 0).sum(axis=1).mean():.1f}"
        )

        return positions_df

    def _optimize_integer_positions(
        self,
        date: pd.Timestamp,
        ideal_positions_df: pd.DataFrame,
        previous_positions: Optional[portfolioWeights] = None,
    ) -> portfolioWeights:
        """
        Run greedy optimizer to select integer lot positions for a single date.

        Args:
            date: Date to optimize for
            ideal_positions_df: DataFrame of ideal fractional positions
            previous_positions: Previous day's positions (for cost calculation)

        Returns:
            portfolioWeights with integer lot positions
        """
        # Get ideal positions for this date
        ideal_positions_series = ideal_positions_df.loc[date]

        # Filter to non-zero positions (instruments with signals).
        # Use a near-zero threshold in TOKEN units — not 0.001, which incorrectly
        # excludes high-priced tokens (BTC at $50K: ideal_pos ≈ 0.0004 BTC tokens,
        # which is $20 of exposure, well worth trading, but below 0.001 tokens).
        # The fractional-lot filter inside _build_optimization_inputs handles the
        # real minimum (instruments whose USD position rounds to < 0.001 lots).
        active_instruments = ideal_positions_series[ideal_positions_series.abs() > 1e-9].index.tolist()

        if len(active_instruments) == 0:
            self.log.debug(f"{date.date()}: No active signals, returning zero positions")
            return portfolioWeights.from_weights_and_keys([], [])

        # Get current prices for lot value calculation
        prices = self._get_prices_at_date(date, active_instruments)

        # Build optimization inputs
        try:
            (
                contracts_optimal,
                per_contract_value,
                costs,
                covariance_matrix,
                constraints,
            ) = self._build_optimization_inputs(
                date=date,
                ideal_positions=ideal_positions_series,
                active_instruments=active_instruments,
                prices=prices,
            )
        except Exception as e:
            self.log.error(f"Failed to build optimization inputs for {date.date()}: {e}")
            # Return previous positions (no trade)
            if previous_positions is not None:
                return previous_positions
            else:
                return portfolioWeights.from_weights_and_keys([], [])

        # Get speed control parameters
        speed_control = self._get_speed_control()

        # Align previous_positions with current optimization set
        # The optimizer expects previous_positions to contain ALL instruments in the current set
        # If an instrument is new or was filtered out yesterday, set its previous weight to 0.0
        current_instruments = set(contracts_optimal.keys())

        if previous_positions is not None:
            # Start with instruments from previous positions that are still in current set
            aligned_previous = {
                k: v for k, v in previous_positions.items()
                if k in current_instruments
            }
            # Add zero entries for instruments in current set but not in previous positions
            for instrument in current_instruments:
                if instrument not in aligned_previous:
                    aligned_previous[instrument] = 0.0

            previous_positions_filtered = portfolioWeights(aligned_previous)
        else:
            # First day: create portfolioWeights with zero entries for all current instruments
            previous_positions_filtered = portfolioWeights({
                instrument: 0.0 for instrument in current_instruments
            })

        # Create objective function
        objective = objectiveFunctionForGreedy(
            contracts_optimal=contracts_optimal,
            covariance_matrix=covariance_matrix,
            per_contract_value=per_contract_value,
            costs=costs,
            speed_control=speed_control,
            previous_positions=previous_positions_filtered,
            constraints=constraints,
            log=self.log,
        )

        # Run greedy optimizer
        optimal_positions = objective.optimise_positions()

        return optimal_positions

    def _build_optimization_inputs(
        self,
        date: pd.Timestamp,
        ideal_positions: pd.Series,
        active_instruments: list,
        prices: pd.Series,
    ) -> tuple:
        """
        Build inputs for greedy optimizer.

        Returns:
            (contracts_optimal, per_contract_value, costs, covariance_matrix, constraints)
        """
        # 1. Convert ideal positions to fractional lots (optimizer input space)
        contracts_optimal_dict = {}
        per_contract_value_dict = {}

        notional_override = self.lot_size_notional_override

        for instrument_code in active_instruments:
            price = prices.get(instrument_code, np.nan)

            if np.isnan(price) or price <= 0:
                continue

            notional_position = ideal_positions[instrument_code]

            if notional_override is not None:
                # Venue has negligible minimum lot size (e.g. Hyperliquid).
                # Treat each $notional_override as one lot. per_contract_value must
                # be expressed as a FRACTION OF CAPITAL so that portfolio weights
                # (contracts × per_contract_value) are dimensionless fractions and
                # tracking error is in annualised-return units — comparable to the
                # tracking_error_buffer of 0.0125.  Using raw USD here makes TE
                # ~10,000× too large and renders the buffer inoperative.
                trading_capital = self.parent.positionSize.get_notional_trading_capital()
                position_usd = notional_position * price
                fractional_lots = position_usd / notional_override
                lot_value = notional_override / trading_capital  # fraction of capital
            else:
                lot_size = self.lot_size_provider.get_lot_size(instrument_code)
                fractional_lots = self.lot_size_provider.convert_notional_to_lots(
                    notional_position, lot_size
                )
                lot_value = self.lot_size_provider.get_lot_value(instrument_code, price)

            # Skip instruments whose ideal position rounds to zero lots.
            # This keeps the optimization tractable without excluding high-priced
            # tokens: BTC ideal ≈ 1.9 fractional lots ($19 USD) >> 0.1 threshold.
            if abs(fractional_lots) < 0.1:
                continue

            contracts_optimal_dict[instrument_code] = fractional_lots
            per_contract_value_dict[instrument_code] = lot_value

        contracts_optimal = portfolioWeights(contracts_optimal_dict)
        per_contract_value = portfolioWeights(per_contract_value_dict)

        # 2. Build covariance matrix
        covariance_matrix = self._get_covariance_matrix(date, list(contracts_optimal.keys()))

        # 3. Filter to instruments actually in covariance matrix
        # Some instruments may have forecasts but lack sufficient history for covariance
        instruments_in_cov = set(list(covariance_matrix.columns))

        # Filter contracts and values to only those in covariance matrix
        filtered_contracts_optimal = {
            k: v for k, v in contracts_optimal_dict.items()
            if k in instruments_in_cov
        }
        filtered_per_contract_value = {
            k: v for k, v in per_contract_value_dict.items()
            if k in instruments_in_cov
        }

        if len(filtered_contracts_optimal) == 0:
            raise Exception(f"No instruments with sufficient history for optimization at {date}")

        # 4. Get SR costs (only for instruments in covariance matrix)
        costs = self._get_sr_costs(date, list(filtered_contracts_optimal.keys()))

        # 5. Final alignment: ensure all three have EXACTLY the same keys
        # (costs might filter out some instruments too)
        instruments_in_costs = set(costs.keys())
        final_instruments = instruments_in_cov.intersection(instruments_in_costs)

        if len(final_instruments) == 0:
            raise Exception(f"No instruments with both covariance and cost data at {date}")

        final_contracts_optimal = {
            k: v for k, v in filtered_contracts_optimal.items()
            if k in final_instruments
        }
        final_per_contract_value = {
            k: v for k, v in filtered_per_contract_value.items()
            if k in final_instruments
        }
        final_costs = {
            k: v for k, v in costs.items()
            if k in final_instruments
        }

        contracts_optimal = portfolioWeights(final_contracts_optimal)
        per_contract_value = portfolioWeights(final_per_contract_value)
        costs = meanEstimates(final_costs)

        # 6. Subset covariance matrix to final instruments
        final_covariance_matrix = covariance_matrix.subset(list(final_instruments))

        # 7. Set up constraints
        constraints = self._get_constraints()

        return (
            contracts_optimal,
            per_contract_value,
            costs,
            final_covariance_matrix,
            constraints,
        )

    def _get_covariance_matrix(
        self,
        date: pd.Timestamp,
        instruments: list,
    ) -> covarianceEstimate:
        """
        Build covariance matrix from instrument returns using EWMA.
        Results are cached to avoid expensive recalculation.

        Args:
            date: Date to estimate covariance for
            instruments: List of instruments

        Returns:
            covarianceEstimate
        """
        # Check cache first
        cache_key = f"{date.date()}_{hash(tuple(sorted(instruments)))}"
        if cache_key in self._covariance_cache:
            return self._covariance_cache[cache_key]

        config = self.parent.config
        greedy_params = config.get_element_or_default('greedy_params', {})
        correlation_span = greedy_params.get('correlation_span', 60)
        min_history = greedy_params.get('min_history_days', 30)

        # Get instrument returns
        returns_dict = {}

        for instrument_code in instruments:
            try:
                # Use cached prices to avoid repeated data loads
                if instrument_code not in self._price_cache:
                    self._price_cache[instrument_code] = self.rawdata.get_daily_prices(instrument_code)

                prices = self._price_cache[instrument_code]

                # Filter to data before date
                prices = prices[prices.index < date]

                if len(prices) < min_history:
                    # Suppress verbose logging during bulk processing
                    continue

                # Log returns
                returns = np.log(prices / prices.shift(1)).dropna()

                # Use trailing window
                returns = returns.iloc[-correlation_span:]

                if len(returns) > 0:
                    returns_dict[instrument_code] = returns

            except Exception as e:
                # Only log warnings, not every missing instrument
                continue

        if len(returns_dict) == 0:
            raise Exception(f"No returns available for covariance estimation at {date}")

        # Build aligned returns DataFrame.
        # With 100+ instruments in a jagged universe, joint dropna() discards most
        # rows (one gap in any instrument drops the whole day), leaving < min_history
        # rows and causing the optimizer to fall back to empty positions on most dates.
        #
        # Fix: filter to instruments with at least half the window of observations
        # (handles recently-listed coins and data quality gaps), then fill residual
        # NaN with 0 (neutral day) so EWMA always has a full window to work with.
        returns_df = pd.DataFrame(returns_dict)

        min_obs_in_window = max(min_history, correlation_span // 2)
        obs_count = returns_df.notna().sum()
        instruments_with_data = obs_count[obs_count >= min_obs_in_window].index.tolist()

        if len(instruments_with_data) == 0:
            raise Exception(
                f"No instruments with sufficient history for covariance at {date}"
            )

        returns_df = returns_df[instruments_with_data]
        returns_df = returns_df.fillna(0.0)

        # Calculate EWMA covariance matrix
        # Using pandas ewm with span parameter
        cov_matrix = returns_df.ewm(span=correlation_span, min_periods=min_history).cov()

        # Extract the last covariance matrix (most recent estimate)
        last_date = cov_matrix.index.get_level_values(0)[-1]
        cov_matrix_slice = cov_matrix.loc[last_date]

        # Annualize (252 trading days)
        cov_matrix_annualized = cov_matrix_slice * 252

        # Convert to covarianceEstimate
        cov_estimate = covarianceEstimate(
            cov_matrix_annualized.values,
            columns=list(returns_df.columns)
        )

        # Store in cache
        self._covariance_cache[cache_key] = cov_estimate

        return cov_estimate

    def _get_sr_costs(
        self,
        date: pd.Timestamp,
        instruments: list,
    ) -> meanEstimates:
        """
        Get cost per unit of weight change for each instrument.

        Returns cost as a fraction of value traded (e.g. 0.002 for 20 bps),
        matching Carver's cost_per_notional_weight_as_proportion_of_capital
        convention.  Do NOT divide by vol: the objective function compares this
        against tracking error in annualised-return units; shadow_cost handles
        the time-scaling.

        Fixed model: 10 bps spread + 5 bps taker fee × 2 (round-trip) = 20 bps.

        Args:
            date: Date to get costs for (unused; costs are time-invariant here)
            instruments: List of instruments

        Returns:
            meanEstimates with cost fractions
        """
        cache_key = f"{date.date()}_{hash(tuple(sorted(instruments)))}"
        if cache_key in self._sr_cost_cache:
            return self._sr_cost_cache[cache_key]

        spread_bps = 10.0
        fee_bps = 5.0
        total_cost_fraction = (spread_bps + 2 * fee_bps) / 10000  # 0.002

        cost_dict = {instr: total_cost_fraction for instr in instruments}
        costs = meanEstimates(cost_dict)
        self._sr_cost_cache[cache_key] = costs
        return costs

    def _get_constraints(self) -> constraintsForDynamicOpt:
        """
        Get constraints for optimizer.

        Returns:
            constraintsForDynamicOpt
        """
        config = self.parent.config
        greedy_params = config.get_element_or_default('greedy_params', {})

        # Long-only constraint (no shorting for crypto)
        long_only = greedy_params.get('long_only', True)

        # Get all instruments for long-only constraint
        instruments = self.get_instrument_list()

        constraints = constraintsForDynamicOpt(
            long_only_keys=instruments if long_only else arg_not_supplied,
            reduce_only_keys=arg_not_supplied,
            no_trade_keys=arg_not_supplied,
        )

        return constraints

    def _get_speed_control(self) -> speedControlForDynamicOpt:
        """
        Get speed control parameters.

        Returns:
            speedControlForDynamicOpt with shadow_cost and buffer
        """
        config = self.parent.config
        greedy_params = config.get_element_or_default('greedy_params', {})

        shadow_cost = greedy_params.get('shadow_cost', 100)
        tracking_error_buffer = greedy_params.get('tracking_error_buffer', 0.0125)

        return speedControlForDynamicOpt(
            trade_shadow_cost=shadow_cost,
            tracking_error_buffer=tracking_error_buffer,
        )

    def _get_prices_at_date(
        self,
        date: pd.Timestamp,
        instruments: list,
    ) -> pd.Series:
        """
        Get prices at a specific date for multiple instruments.

        Args:
            date: Date to get prices for
            instruments: List of instruments

        Returns:
            pd.Series of prices indexed by instrument code
        """
        prices_dict = {}

        for instrument_code in instruments:
            try:
                prices = self.rawdata.get_daily_prices(instrument_code)
                # Get most recent price on or before date
                valid_prices = prices[prices.index <= date]

                if len(valid_prices) > 0:
                    prices_dict[instrument_code] = valid_prices.iloc[-1]
                else:
                    prices_dict[instrument_code] = np.nan

            except Exception as e:
                self.log.warning(f"Could not get price for {instrument_code}: {e}")
                prices_dict[instrument_code] = np.nan

        return pd.Series(prices_dict)

    def _apply_min_notional_filter(
        self,
        instrument_code: str,
        position: pd.Series,
    ) -> pd.Series:
        """
        Apply minimum notional filter ($25 exchange minimum).

        Reuses logic from CryptoPortfolios.

        Args:
            instrument_code: Instrument code
            position: Position series

        Returns:
            Filtered position series
        """
        min_notional = self.config.get_element_or_default("min_notional_position", 25.0)

        if min_notional <= 0:
            return position

        # Greedy positions are in integer lots, not token units.
        # notional = lots × $/lot (NOT lots × token_price — that gives nonsense for
        # high-priced tokens like BTC and wrongly zeros low-priced alts like DOGE).
        lot_usd = self.config.get_element_or_default("lot_size_notional_override", None)
        if lot_usd is not None:
            notional = position.abs() * float(lot_usd)
        else:
            prices = self.rawdata.get_daily_prices(instrument_code)
            prices = prices.reindex(position.index, method='ffill')
            notional = position.abs() * prices

        filtered = position.where(notional >= min_notional, 0.0)

        n_zeroed = int((position.abs() > 0).sum() - (filtered.abs() > 0).sum())
        if n_zeroed > 0:
            self.log.debug(
                f"{instrument_code}: {n_zeroed} position-days zeroed by "
                f"${min_notional:.0f} min-notional filter "
                f"({n_zeroed / max(len(position), 1):.1%} of history)",
                instrument_code=instrument_code,
            )

        return filtered

    @diagnostic()
    def get_greedy_diagnostics(self) -> pd.DataFrame:
        """
        Get diagnostic metrics for greedy optimization.

        Returns:
            pd.DataFrame with columns:
            - num_positions: Number of active positions each day
            - tracking_error: Daily tracking error vs ideal portfolio
            - buffer_triggered: Whether buffer prevented trading
        """
        self.log.debug("Computing greedy diagnostics (not yet implemented)")

        # TODO: Implement diagnostics extraction from objective function
        # This would require storing objective values during optimization

        return pd.DataFrame()

    def get_instrument_list(
        self,
        for_instrument_weights=False,
        auto_remove_bad_instruments=False
    ) -> list:
        """
        Get instrument list for greedy universe.

        Unlike two-stage system, we use ALL instruments available in the data
        layer. The greedy optimizer will select the optimal subset.

        Args:
            for_instrument_weights: Whether getting list for weight calculation (ignored)
            auto_remove_bad_instruments: Whether to auto-remove bad instruments (ignored)

        Returns:
            List of all available instrument codes
        """
        # Get ALL instruments from data layer
        all_instruments = self.data.get_instrument_list()

        # Apply system-level filters (duplicates, ignored, etc.) if configured
        filtered_instruments = self.parent._remove_instruments_from_instrument_list(
            all_instruments,
            remove_duplicates=True,
            remove_ignored=True,
        )

        self.log.debug(
            f"Greedy universe: Using {len(filtered_instruments)} instruments "
            f"(from {len(all_instruments)} available)"
        )

        return filtered_instruments

    def get_instrument_weight_for_code(self, instrument_code: str) -> pd.Series:
        """
        Get instrument weight for a single instrument.

        For greedy portfolio, we use equal weights as the starting point.
        The optimizer will select which instruments to hold.

        NOTE: using 1/top_k here causes over-leverage: with 149 instruments
        having non-zero signals at weight=1/30 each, the ideal portfolio is
        5× leveraged and the greedy selects ~65 instruments (2.15× leverage).
        The shadow cost can't limit positions because cost_per_lot is too small
        ($10 lot / $10K capital = 0.001; cost = 0.00045 × 0.001 = 4.5e-7 per
        lot) relative to TE improvements.  Using 1/N_universe keeps ideal
        positions at ~1 lot each so the shadow cost naturally terminates at K≈30.

        Args:
            instrument_code: Instrument code

        Returns:
            pd.Series of weights (constant 1/N)
        """
        # Get all instruments
        instruments = self.get_instrument_list()

        # Equal weight over full universe
        weight = 1.0 / len(instruments)

        # Create constant weight series
        # Use subsystem position dates as reference
        subsystem_position = self.parent.positionSize.get_subsystem_position(instrument_code)

        weight_series = pd.Series(weight, index=subsystem_position.index)

        return weight_series
