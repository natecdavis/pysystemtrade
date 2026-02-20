"""
Dynamic portfolio stage for crypto backtesting with walk-forward cost-based universe selection.

This module provides a portfolio stage that implements time-varying instrument weights based on:
- Walk-forward cost filters (SR cost thresholds)
- Signal-based exits (forecast crosses zero)
- Equal weighting (1/N) among eligible instruments
"""

import pandas as pd
import numpy as np
from systems.system_cache import diagnostic
from systems.crypto_perps.crypto_portfolio import CryptoPortfolios


class CryptoDynamicPortfolio(CryptoPortfolios):
    """
    Portfolio stage with dynamic instrument universe based on walk-forward cost filters.

    Entry Logic:
        - Instrument enters when cost filter passes (SR thresholds met)
        - Must have minimum history required for rules

    Exit Logic:
        - Instrument exits when aggregate forecast crosses zero (signal exhausted)
        - Does NOT force exit when cost filter fails (avoids exiting profitable trends)

    Weighting:
        - Equal weight (1/N) among active instruments at each date
        - Existing instrument_weight_ewma_span config smooths transitions

    Example:
        from systems.provided.crypto_example.crypto_system import crypto_system_with_dynamic_universe

        system = crypto_system_with_dynamic_universe(data_path='data/crypto')
        weights = system.portfolio.get_instrument_weights()

        # View universe size over time
        universe_size = (weights > 0).sum(axis=1)
        print(universe_size.describe())
    """

    def get_instrument_list(self, for_instrument_weights=False, auto_remove_bad_instruments=False) -> list:
        """
        Get instrument list for dynamic universe.

        Unlike standard portfolio which gets instruments from config, we use ALL
        instruments available in the data layer. The dynamic universe logic will
        filter these based on cost thresholds.

        Args:
            for_instrument_weights: Whether getting list for weight calculation (ignored)
            auto_remove_bad_instruments: Whether to auto-remove bad instruments (ignored)

        Returns:
            List of all available instrument codes
        """
        # Get ALL instruments from data layer, not just those in config
        all_instruments = self.data.get_instrument_list()

        # Still apply system-level filters (duplicates, ignored, etc.) if configured
        filtered_instruments = self.parent._remove_instruments_from_instrument_list(
            all_instruments,
            remove_duplicates=True,
            remove_ignored=True,
        )

        self.log.debug(
            f"Dynamic universe: Using {len(filtered_instruments)} instruments "
            f"(from {len(all_instruments)} available)"
        )

        return filtered_instruments

    @diagnostic()
    def get_raw_fixed_instrument_weights(self) -> pd.DataFrame:
        """
        Get time-varying instrument weights based on dynamic universe eligibility.

        This overrides the standard portfolio method to implement dynamic universe selection.
        Instead of fixed weights from config, weights change over time based on:
        - Cost filter eligibility (can enter)
        - Forecast signals (can exit)

        Returns:
            pd.DataFrame with dates as index, instruments as columns
            - Equal weights (1/N) for active instruments at each date
            - Zero weights for inactive instruments
        """
        self.log.debug("Calculating dynamic instrument weights")

        # Get instrument list and date range from subsystem positions
        instrument_list = self.get_instrument_list()
        subsystem_positions = self._get_all_subsystem_positions()
        position_series_index = subsystem_positions.index

        # Get eligibility matrix from data layer (Stage 1: cost filter)
        eligibility_df = self.data.get_universe_eligibility_df(
            instruments=instrument_list,
            dates=position_series_index,
        )

        # Stage 2: Top-K Liquidity Selection with Hysteresis (if configured)
        eligibility_df = self._apply_top_k_selection(eligibility_df)

        # Calculate equal weights among eligible instruments with entry/exit logic
        weights_df = self._calculate_dynamic_weights(eligibility_df)

        self.log.info(
            f"Dynamic universe: {len(instrument_list)} instruments available, "
            f"average active: {(weights_df > 0).sum(axis=1).mean():.1f}"
        )

        return weights_df

    def _apply_top_k_selection(self, eligibility_df: pd.DataFrame) -> pd.DataFrame:
        """
        Apply Stage 2 top-K ADV selection with hysteresis.

        Only runs if dynamic_universe.top_k is set in config.
        Stashes the tradable_over_time dict as self._tradable_over_time for
        post-run snapshot extraction.

        Args:
            eligibility_df: Stage 1 boolean DataFrame (dates × instruments)

        Returns:
            Filtered eligibility_df (Stage 2 output) or original if Stage 2 not configured
        """
        config = self.parent.config
        du_config = config.get_element_or_default('dynamic_universe', {}) or {}

        if not du_config.get('top_k'):
            self.log.debug("Stage 2 (Top-K) not configured — using Stage 1 eligibility only")
            return eligibility_df

        from sysdata.crypto.top_k_selector import TopKInstrumentSelector

        K = du_config.get('top_k', 30)
        entry_buffer = du_config.get('entry_buffer', 5)
        exit_buffer = du_config.get('exit_buffer', 10)
        adv_window = du_config.get('adv_window', 30)

        # Read selection criterion (default: ADV)
        selection_criterion = du_config.get('selection_criterion', 'adv')

        if selection_criterion not in ['adv', 'forecast_magnitude']:
            raise ValueError(f"Invalid selection_criterion: {selection_criterion}. "
                             f"Must be 'adv' or 'forecast_magnitude'")

        self.log.info(
            f"Stage 2: Top-K selection K={K}, entry<={K - entry_buffer}, exit>{K + exit_buffer}, "
            f"criterion={selection_criterion}"
        )

        selector = TopKInstrumentSelector(
            K=K,
            entry_buffer=entry_buffer,
            exit_buffer=exit_buffer,
            adv_window=adv_window,
            log=self.log,
        )

        # Get prices and ADV data from data layer
        prices_df = self.data.get_prices_df(list(eligibility_df.columns))
        adv_df = self.data.get_adv_notional_df(list(eligibility_df.columns))

        # Derive volumes_df so that price * volume = adv_notional
        # TopKSelector computes ADV as (recent_prices * recent_volumes).mean()
        if adv_df.empty:
            self.log.warning(
                "ADV notional data not available — using price-only liquidity proxy"
            )
            volumes_df = pd.DataFrame(
                1.0, index=prices_df.index, columns=prices_df.columns
            )
        else:
            prices_aligned = prices_df.reindex(adv_df.index)
            volumes_df = (
                adv_df.divide(prices_aligned)
                .fillna(0.0)
                .replace([float('inf'), float('-inf')], 0.0)
            )

        # Get forecast data if using forecast-based selection
        forecasts_df = None
        if selection_criterion == 'forecast_magnitude':
            self.log.info("Fetching combined forecasts for Stage 2 selection")
            forecasts_dict = {}
            for instrument in eligibility_df.columns:
                try:
                    forecast = self.parent.combForecast.get_combined_forecast(instrument)
                    forecasts_dict[instrument] = forecast
                except Exception as e:
                    self.log.warning(f"Could not get forecast for {instrument}: {str(e)}")
                    forecasts_dict[instrument] = pd.Series(0, index=eligibility_df.index)

            forecasts_df = pd.DataFrame(forecasts_dict, index=eligibility_df.index)
            self.log.info(f"Fetched forecasts for {len(forecasts_dict)} instruments")

        tradable_over_time = selector.get_tradable_over_time(
            eligible_df=eligibility_df,
            prices_df=prices_df,
            volumes_df=volumes_df,
            selection_criterion=selection_criterion,
            forecasts_df=forecasts_df,
        )

        # Apply manual pins: add pinned instruments to each date's tradable set
        # (only if they pass Stage 1 cost filter; BANNED_FLATTEN still overrides)
        pinned = set(config.get_element_or_default('pinned_instruments', []) or [])
        if pinned:
            for date in list(tradable_over_time.keys()):
                eligible_on_date = set(
                    eligibility_df.columns[eligibility_df.loc[date]]
                )
                tradable_over_time[date] = (
                    tradable_over_time[date] | (pinned & eligible_on_date)
                )
            self.log.info(f"Applied {len(pinned)} pinned instruments to tradable sets")

        # Convert back to boolean DataFrame for _calculate_dynamic_weights
        eligibility_df = selector.to_eligibility_df(
            tradable_over_time, list(eligibility_df.columns)
        )

        # Stash for post-run snapshot extraction
        self._tradable_over_time = tradable_over_time

        last_tradable_count = len(tradable_over_time[eligibility_df.index[-1]])
        self.log.info(
            f"Stage 2 complete: {last_tradable_count} instruments tradable on last date"
        )

        return eligibility_df

    def _calculate_dynamic_weights(self, eligibility_df: pd.DataFrame) -> pd.DataFrame:
        """
        Calculate 1/N weights with entry/exit logic.

        Entry Rule:
            - Cost filter passes AND in top-K (eligibility_df = True)
            - Not currently held

        Exit Rule (ineligible instruments):
            - Fell out of top-K AND (|forecast| < 0.1 OR forecast switches sign)
            - OR forecast is NaN
            - Two triggers: signal fades to near-zero, OR signal reverses direction.
              Prevents drifting into an unintended position after ranking out.

        Exit Rule (eligible instruments):
            - Forecast is NaN
            - No forced exit on weak signal; position sizing scales exposure naturally

        Hold Rule:
            - Keep weight even if cost filter or top-K eligibility fails;
              position sizing reduces exposure as signal decays

        Args:
            eligibility_df: DataFrame with dates as index, instruments as columns,
                           boolean values (True=eligible for entry, False=not eligible)

        Returns:
            DataFrame with dates as index, instruments as columns,
            float values (equal weight for active instruments, 0 for inactive)
        """
        # Get combined forecasts to determine exits
        self.log.debug("Fetching combined forecasts for exit logic")
        forecasts_dict = {}
        for instrument in eligibility_df.columns:
            try:
                # Access forecast from combForecast stage via parent system
                forecast = self.parent.combForecast.get_combined_forecast(instrument)
                forecasts_dict[instrument] = forecast
            except Exception as e:
                # Instrument may not have forecast yet (early dates or missing data)
                self.log.warning(f"Could not get forecast for {instrument}: {str(e)}")
                forecasts_dict[instrument] = pd.Series(0, index=eligibility_df.index)

        forecasts_df = pd.DataFrame(forecasts_dict, index=eligibility_df.index)

        # Phase 1.1: Expected Max Weight Calculation
        # CORRECT benchmark: compute per-day tradable universe
        # Do NOT use N_eligible alone - use N_tradable (eligible ∩ forecast_valid ∩ position_valid)
        self.log.info("Computing tradable universe metrics...")
        N_eligible = eligibility_df.sum(axis=1)
        N_forecast_valid = (~forecasts_df.isna()).sum(axis=1)

        # Note: We don't have subsystem_positions here yet, so we'll log this in Phase 2
        # For now, log eligibility vs forecast availability gap
        self.log.info(
            f"Eligibility vs Forecast Gap:\n"
            f"  N_eligible: min={N_eligible.min():.0f}, max={N_eligible.max():.0f}, avg={N_eligible.mean():.1f}\n"
            f"  N_forecast_valid: min={N_forecast_valid.min():.0f}, max={N_forecast_valid.max():.0f}, avg={N_forecast_valid.mean():.1f}\n"
            f"  Gap (eligible - forecast_valid): avg={(N_eligible - N_forecast_valid).mean():.1f}"
        )

        # Initialize weights DataFrame
        weights = pd.DataFrame(0.0, index=eligibility_df.index, columns=eligibility_df.columns)

        # Track instruments for logging
        entry_count = 0
        exit_count = 0

        # Iterate through dates to track entry/exit state
        for i, date in enumerate(eligibility_df.index):
            if i == 0:
                # First date: enter all eligible instruments
                eligible = eligibility_df.loc[date]
                active_instruments = eligible[eligible].index.tolist()
                entry_count += len(active_instruments)
            else:
                prev_date = eligibility_df.index[i-1]
                prev_weights = weights.loc[prev_date]
                currently_held = prev_weights[prev_weights > 0].index.tolist()

                # Entry: cost filter passes and not currently held
                eligible = eligibility_df.loc[date]
                new_entries = [
                    instr for instr in eligible[eligible].index
                    if instr not in currently_held
                ]
                entry_count += len(new_entries)

                # Exit rules:
                #   - NaN forecast: always exit (data gone)
                #   - Ineligible (fell out of top-K): exit when |forecast| < 0.1
                #     OR forecast switches sign vs previous day
                #   - Eligible (still in top-K): no forced exit; position sizing
                #     naturally scales exposure down as signal weakens
                exits = []
                forecast_zero_exits = []
                forecast_nan_exits = []

                for instr in currently_held:
                    forecast_value = forecasts_df.loc[date, instr]

                    # EXPLICIT NaN handling - do NOT treat NaN as zero implicitly
                    if pd.isna(forecast_value):
                        exits.append(instr)
                        forecast_nan_exits.append(instr)
                        continue

                    # Only force-exit for instruments that have fallen out of the
                    # eligible set (no longer in top-K). Two triggers:
                    #   (a) |forecast| < 0.1 — signal too weak to maintain
                    #   (b) forecast switches sign vs previous day — signal reversed
                    # Either condition closes the position, preventing it from
                    # drifting into an unintended direction after ranking out.
                    # Eligible instruments stay in; position sizing handles the rest.
                    is_eligible = eligibility_df.loc[date, instr]
                    if not is_eligible:
                        prev_forecast = forecasts_df.loc[prev_date, instr]
                        sign_switched = (
                            not pd.isna(prev_forecast)
                            and np.sign(forecast_value) != np.sign(prev_forecast)
                        )
                        if abs(forecast_value) < 0.1 or sign_switched:
                            exits.append(instr)
                            forecast_zero_exits.append(instr)

                exit_count += len(exits)

                # Log NaN vs zero exits separately every 100 days
                if i % 100 == 0:
                    self.log.info(
                        f"Day {i}/{len(eligibility_df)}: {len(active_instruments)} active, "
                        f"{len(new_entries)} entries, {len(exits)} exits "
                        f"(NaN: {len(forecast_nan_exits)}, Zero: {len(forecast_zero_exits)})"
                    )
                    if len(exits) > 0:
                        # Sample exit forecasts to understand why instruments exiting
                        exit_sample = exits[:5]
                        exit_forecasts = []
                        for instr in exit_sample:
                            fcst_val = forecasts_df.loc[date, instr]
                            if pd.isna(fcst_val):
                                exit_forecasts.append(f"{instr}: NaN")
                            else:
                                exit_forecasts.append(f"{instr}: {fcst_val:.2f}")
                        self.log.info(f"  Sample exit forecasts: {exit_forecasts}")

                # Active = currently held - exits + new entries
                active_instruments = [
                    instr for instr in currently_held
                    if instr not in exits
                ] + new_entries

            # Calculate equal weights among active instruments
            if len(active_instruments) > 0:
                weight_per_instrument = 1.0 / len(active_instruments)
                for instr in active_instruments:
                    weights.loc[date, instr] = weight_per_instrument

        # Phase 1.2: Invariant Checks at Raw Weight Stage
        num_active = (weights > 0).sum(axis=1)
        max_weight = weights.max(axis=1)
        sum_weights = weights.sum(axis=1)
        weights_squared = (weights ** 2).sum(axis=1)
        N_eff = 1.0 / weights_squared  # Effective universe size

        # Check: sum should be ~1.0 when N_active > 0
        sum_violations = sum_weights[(num_active > 0) & (abs(sum_weights - 1.0) > 0.001)]
        if len(sum_violations) > 0:
            self.log.warning(
                f"RAW WEIGHTS INVARIANT VIOLATION: {len(sum_violations)} days with sum != 1.0\n"
                f"  Sample violations: {sum_violations.head()}"
            )

        self.log.info(
            f"RAW WEIGHTS (Stage 1):\n"
            f"  N_active: min={num_active.min():.0f}, max={num_active.max():.0f}, avg={num_active.mean():.1f}\n"
            f"  Sum: min={sum_weights.min():.4f}, max={sum_weights.max():.4f}, avg={sum_weights.mean():.4f}\n"
            f"  Max weight: min={max_weight.min():.4f}, max={max_weight.max():.4f}, avg={max_weight.mean():.4f}\n"
            f"  N_effective: avg={N_eff.mean():.1f} (concentration metric: 1/sum(w^2))\n"
            f"  Expected max weight: avg={1.0/num_active.mean():.4f} (for equal weighting)"
        )

        # Phase 1.3: Sample Date Analysis
        sample_dates = [weights.index[len(weights)//4], weights.index[len(weights)//2], weights.index[-100]]
        for date in sample_dates:
            active = weights.loc[date][weights.loc[date] > 0]
            n_eligible = eligibility_df.loc[date].sum()
            n_forecast_valid = (~forecasts_df.loc[date].isna()).sum()

            self.log.info(
                f"\nSAMPLE DATE {date.date()}:\n"
                f"  N_eligible: {n_eligible:.0f}\n"
                f"  N_forecast_valid: {n_forecast_valid:.0f}\n"
                f"  N_active (weight > 0): {len(active)}\n"
                f"  Weights sum: {weights.loc[date].sum():.4f}\n"
                f"  Max weight: {active.max():.4f}\n"
                f"  Expected max (1/N_active): {1.0/len(active) if len(active) > 0 else 0:.4f}"
            )

            # Verify top-weighted instruments exist in forecasts
            if len(active) > 0:
                top_5 = active.nlargest(min(5, len(active)))
                self.log.info(f"  Top 5 weighted instruments:")
                for instr, wt in top_5.items():
                    fcst = forecasts_df.loc[date, instr]
                    eligible = eligibility_df.loc[date, instr]
                    self.log.info(f"    {instr}: weight={wt:.4f}, forecast={fcst:.2f}, eligible={eligible}")

        # Phase 1.5: Entry/Exit Summary Logging
        self.log.info(
            f"\nENTRY/EXIT AUDIT (RAW, before any smoothing):\n"
            f"  Total entries: {entry_count} over {len(eligibility_df)} days\n"
            f"  Total exits: {exit_count} over {len(eligibility_df)} days\n"
            f"  Avg entries per day: {entry_count / len(eligibility_df):.1f}\n"
            f"  Avg exits per day: {exit_count / len(eligibility_df):.1f}\n"
            f"  Balance (entries - exits): {entry_count - exit_count}"
        )

        # Compute epsilon weights (tiny weights from numerical artifacts)
        epsilon_threshold = 1e-6
        epsilon_weights = ((weights > 0) & (weights < epsilon_threshold)).sum()
        self.log.info(
            f"  Epsilon weights (0 < w < 1e-6): {epsilon_weights.sum()} total occurrences\n"
            f"  (May indicate numerical artifacts vs real trading intent)"
        )

        return weights
