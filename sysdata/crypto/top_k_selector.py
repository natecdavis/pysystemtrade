"""
Top-K instrument selector with entry/exit hysteresis.

Selects top K instruments by liquidity (ADV) with hysteresis to prevent churn.

Key Design:
- Entry threshold: rank <= K - entry_buffer (harder to enter, conservative)
- Exit threshold: rank > K + exit_buffer (easier to stay, asymmetric)
- Liquidity metric: Rolling ADV from Vision data (reproducible, stable)
- Hysteresis prevents frequent turnover
"""

import pandas as pd
import numpy as np
from typing import Dict, List, Set, Optional
from pathlib import Path
import logging

logger = logging.getLogger(__name__)


class TopKInstrumentSelector:
    """
    Select top K instruments by liquidity with entry/exit hysteresis.

    Prevents churn by using asymmetric entry/exit thresholds:
    - Entry: Must rank in top (K - entry_buffer) to join
    - Exit: Only exit if rank drops below (K + exit_buffer)

    Example with K=30:
    - entry_buffer=5: Must rank <= 25 to enter (conservative)
    - exit_buffer=10: Exit if rank > 40 (easier to stay)

    This creates hysteresis: instruments rank 26-40 can stay but can't enter.
    """

    def __init__(
        self,
        K: int = 30,
        entry_buffer: int = 5,
        exit_buffer: int = 10,
        adv_window: int = 30,
        min_history_days: int = 365,
        log=None
    ):
        """
        Args:
            K: Target number of tradable instruments (default: 30)
            entry_buffer: Buffer for entry threshold (default: 5)
                         Entry when rank <= K - entry_buffer
            exit_buffer: Buffer for exit threshold (default: 10)
                        Exit when rank > K + exit_buffer
            adv_window: Rolling window for ADV calculation in days (default: 30)
            min_history_days: Minimum data history to compute ADV (default: 365)
            log: Logger instance
        """
        self.K = K
        self.entry_buffer = entry_buffer
        self.exit_buffer = exit_buffer
        self.adv_window = adv_window
        self.min_history_days = min_history_days
        self.log = log or logger

        # Derived thresholds
        self.entry_threshold = K - entry_buffer  # e.g., 25 for K=30
        self.exit_threshold = K + exit_buffer    # e.g., 40 for K=30

        self.log.info(f"TopKSelector initialized: K={K}, entry<={self.entry_threshold}, exit>{self.exit_threshold}")

    def compute_liquidity_metric(
        self,
        prices_df: pd.DataFrame,
        volumes_df: pd.DataFrame,
        date: pd.Timestamp,
        registry_volume_24h: Optional[Dict[str, float]] = None
    ) -> pd.Series:
        """
        Compute liquidity metric (ADV in USD) for all instruments at date.

        Primary: Rolling ADV from Vision-derived history (reproducible, stable)
        Fallback: Registry volume_24h for new symbols with insufficient history

        Args:
            prices_df: DataFrame of prices (instruments × dates)
            volumes_df: DataFrame of volumes (instruments × dates)
            date: Current date
            registry_volume_24h: Optional dict of {instrument: volume_24h} from registry

        Returns:
            Series of ADV in USD, sorted descending by liquidity
        """
        adv_series = {}

        for instrument in prices_df.columns:
            # Get price and volume history up to date
            try:
                prices_history = prices_df[instrument].loc[:date].dropna()
                volumes_history = volumes_df[instrument].loc[:date].dropna()

                # Check if we have enough history
                if len(prices_history) >= self.min_history_days:
                    # Compute rolling ADV from Vision history (PRIMARY)
                    recent_prices = prices_history.tail(self.adv_window)
                    recent_volumes = volumes_history.tail(self.adv_window)

                    # ADV = average daily notional volume
                    adv_usd = (recent_prices * recent_volumes).mean()
                    adv_series[instrument] = adv_usd

                else:
                    # Fallback to registry volume_24h (for new symbols)
                    if registry_volume_24h and instrument in registry_volume_24h:
                        # Use registry 24h volume as ADV approximation
                        adv_series[instrument] = registry_volume_24h[instrument]
                    else:
                        # No data available, assign zero (will rank last)
                        adv_series[instrument] = 0.0

            except Exception as e:
                self.log.warning(f"Failed to compute ADV for {instrument}: {e}")
                adv_series[instrument] = 0.0

        # Sort descending by liquidity
        return pd.Series(adv_series).sort_values(ascending=False)

    def select_tradable_set(
        self,
        eligible_candidates: List[str],
        current_tradable: Set[str],
        prices_df: pd.DataFrame,
        volumes_df: pd.DataFrame,
        date: pd.Timestamp,
        registry_volume_24h: Optional[Dict[str, float]] = None
    ) -> Set[str]:
        """
        Select tradable set at date with hysteresis.

        Args:
            eligible_candidates: Instruments passing cost filters
            current_tradable: Currently held tradable set
            prices_df: DataFrame of prices (for ADV calculation)
            volumes_df: DataFrame of volumes (for ADV calculation)
            date: Current date
            registry_volume_24h: Optional dict of {instrument: volume_24h} from registry

        Returns:
            Updated tradable set
        """
        # Compute liquidity metric (rolling ADV from Vision history)
        liquidity_series = self.compute_liquidity_metric(
            prices_df, volumes_df, date, registry_volume_24h
        )

        # Filter to eligible candidates only
        liquidity_series = liquidity_series[liquidity_series.index.isin(eligible_candidates)]

        # Rank by liquidity (1-indexed)
        ranked = liquidity_series.sort_values(ascending=False)
        ranks = {instr: i+1 for i, instr in enumerate(ranked.index)}

        # New tradable set (start with current)
        new_tradable = set(current_tradable)

        # Apply entry logic: add new instruments if they rank high enough
        for instrument in eligible_candidates:
            if instrument in new_tradable:
                continue  # Already in

            rank = ranks.get(instrument, 999999)
            if rank <= self.entry_threshold:
                new_tradable.add(instrument)
                self.log.info(f"{date.date()}: ENTRY {instrument} (rank {rank})")

        # Apply exit logic: remove instruments that drop too far
        for instrument in list(new_tradable):
            if instrument not in eligible_candidates:
                # Not eligible anymore (cost filter failed)
                new_tradable.remove(instrument)
                self.log.warning(f"{date.date()}: EXIT {instrument} (not eligible)")
                continue

            rank = ranks.get(instrument, 999999)
            if rank > self.exit_threshold:
                new_tradable.remove(instrument)
                self.log.info(f"{date.date()}: EXIT {instrument} (rank {rank})")

        # Cap at K (shouldn't be needed with proper thresholds, but defensive)
        if len(new_tradable) > self.K:
            # Rank current tradable set and keep top K by liquidity
            tradable_ranks = [(instr, ranks.get(instr, 999999)) for instr in new_tradable]
            tradable_ranks.sort(key=lambda x: x[1])  # Sort by rank ascending
            new_tradable = set([instr for instr, rank in tradable_ranks[:self.K]])
            self.log.warning(f"{date.date()}: Capped tradable set to K={self.K}")

        return new_tradable

    def get_tradable_over_time(
        self,
        eligible_df: pd.DataFrame,
        prices_df: pd.DataFrame,
        volumes_df: pd.DataFrame,
        registry_volume_24h: Optional[Dict[str, float]] = None
    ) -> Dict[pd.Timestamp, Set[str]]:
        """
        Compute tradable set for each date in backtest period.

        Args:
            eligible_df: Boolean DataFrame (dates × instruments) of eligibility
            prices_df: DataFrame of prices (dates × instruments)
            volumes_df: DataFrame of volumes (dates × instruments)
            registry_volume_24h: Optional dict of {instrument: volume_24h}

        Returns:
            Dict mapping date -> set of tradable instruments
        """
        tradable_over_time = {}
        current_tradable = set()

        for date in eligible_df.index:
            # Get eligible candidates at this date
            eligible_candidates = [
                instr for instr in eligible_df.columns
                if eligible_df.loc[date, instr]
            ]

            # Select tradable set with hysteresis
            current_tradable = self.select_tradable_set(
                eligible_candidates=eligible_candidates,
                current_tradable=current_tradable,
                prices_df=prices_df,
                volumes_df=volumes_df,
                date=date,
                registry_volume_24h=registry_volume_24h
            )

            tradable_over_time[date] = current_tradable.copy()

        return tradable_over_time

    def to_eligibility_df(
        self,
        tradable_over_time: Dict[pd.Timestamp, Set[str]],
        all_instruments: List[str]
    ) -> pd.DataFrame:
        """
        Convert tradable-over-time dict to boolean DataFrame.

        Args:
            tradable_over_time: Dict mapping date -> set of tradable instruments
            all_instruments: Full list of instruments (for columns)

        Returns:
            Boolean DataFrame (dates × instruments)
        """
        dates = sorted(tradable_over_time.keys())
        df = pd.DataFrame(False, index=dates, columns=all_instruments)

        for date, tradable in tradable_over_time.items():
            for instr in tradable:
                df.loc[date, instr] = True

        return df
