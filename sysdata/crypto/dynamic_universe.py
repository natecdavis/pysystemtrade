"""
Dynamic instrument universe for crypto trading.

Determines which instruments are eligible for trading based on:
1. Cost filters (SR-based, walk-forward)
2. Minimum data history (for at least one rule to produce a forecast)

Entry: Instrument joins universe when cost filter passes AND has enough history.
Exit: Instrument leaves universe only when aggregate forecast hits/crosses 0.
      (This is handled at the portfolio level, not here - we just track eligibility)
"""

import pandas as pd
import numpy as np
from typing import Dict, List, Optional, Set
from datetime import datetime

from syslogging.logger import get_logger

from sysdata.crypto.walk_forward_costs import (
    WalkForwardCostEstimator,
    calculate_stack_turnover,
    RULE_TURNOVER,
)


# Minimum history requirements (days) for each rule family
# Instrument is eligible when it has enough history for at least one rule
RULE_MIN_HISTORY = {
    "ewmac8_32": 40,       # Need ~32 days for slow EMA + buffer
    "ewmac16_64": 70,
    "ewmac32_128": 140,
    "ewmac64_256": 270,
    "breakout10": 15,
    "breakout20": 25,
    "breakout40": 50,
    "breakout80": 90,
    "tsmom63": 75,
    "tsmom126": 140,
    "tsmom252": 270,
    "accel16": 100,        # Needs extra for acceleration calc
    "accel32": 180,
    "relmomentum20": 30,
    "relmomentum40": 50,
}

# Minimum history for ANY rule to work
MIN_HISTORY_ANY_RULE = min(RULE_MIN_HISTORY.values())


class DynamicUniverseManager:
    """
    Manages dynamic instrument universe with walk-forward cost filtering.

    Determines which instruments are tradeable at each date based on:
    1. Walk-forward cost filters (SR per trade, annual SR)
    2. Minimum data availability (at least one rule can produce forecast)
    """

    def __init__(
        self,
        cost_estimator: WalkForwardCostEstimator,
        max_sr_cost_per_trade: float = 0.01,
        max_sr_cost_annual: float = 0.13,
        stack_turnover: float = 15.0,
        forecast_weights: Optional[Dict[str, float]] = None,
        min_annual_vol: float = 0.0,
        log=get_logger("DynamicUniverseManager"),
    ):
        """
        Args:
            cost_estimator: WalkForwardCostEstimator instance
            max_sr_cost_per_trade: Maximum SR cost per trade (default 0.01)
            max_sr_cost_annual: Maximum annual SR cost (default 0.13)
            stack_turnover: Expected turnover if not using forecast_weights
            forecast_weights: Dict of rule -> weight for turnover calculation
            min_annual_vol: Minimum annualised vol floor (default 0.0 = disabled).
                            Rejects stablecoins and semi-pegged tokens.
            log: Logger instance
        """
        self._cost_estimator = cost_estimator
        self._max_sr_per_trade = max_sr_cost_per_trade
        self._max_sr_annual = max_sr_cost_annual
        self._min_annual_vol = min_annual_vol
        self._log = log

        # Calculate turnover from weights if provided
        if forecast_weights is not None:
            self._stack_turnover = calculate_stack_turnover(forecast_weights)
        else:
            self._stack_turnover = stack_turnover

        # Cache for eligibility series
        self._eligibility_cache: Dict[str, pd.Series] = {}

    def is_eligible(
        self,
        instrument_code: str,
        date: pd.Timestamp,
        prices: Optional[pd.Series] = None,
    ) -> bool:
        """
        Check if an instrument is eligible for trading at a specific date.

        Args:
            instrument_code: Instrument code
            date: Date to check
            prices: Optional price series (for history check)

        Returns:
            True if instrument passes all filters
        """
        # Check cost filter
        if not self._passes_cost_filter(instrument_code, date):
            return False

        # Check minimum history
        if prices is not None:
            if not self._has_min_history(prices, date):
                return False

        return True

    def get_eligibility_series(
        self,
        instrument_code: str,
        prices: pd.Series,
    ) -> pd.Series:
        """
        Get time series of eligibility for an instrument.

        Args:
            instrument_code: Instrument code
            prices: Price series for the instrument

        Returns:
            pd.Series of boolean eligibility values
        """
        if instrument_code in self._eligibility_cache:
            return self._eligibility_cache[instrument_code]

        # Get cost filter series
        sr_per_trade = self._get_sr_cost_series(instrument_code, prices)

        # Cost filter: SR per trade <= threshold
        cost_ok = sr_per_trade <= self._max_sr_per_trade

        # Annual cost filter
        annual_sr = sr_per_trade * self._stack_turnover
        annual_ok = annual_sr <= self._max_sr_annual

        # History filter: must have enough history for at least one rule
        history_ok = self._get_history_filter_series(prices)

        # Volatility floor: exclude stablecoins and semi-pegged tokens
        vol_ok = self._get_vol_floor_series(prices)

        # Combined eligibility
        eligible = cost_ok & annual_ok & history_ok & vol_ok

        self._eligibility_cache[instrument_code] = eligible
        return eligible

    def get_eligible_instruments(
        self,
        date: pd.Timestamp,
        all_instruments: List[str],
        price_data: Dict[str, pd.Series],
    ) -> List[str]:
        """
        Get list of eligible instruments at a specific date.

        Args:
            date: Date to check
            all_instruments: List of all possible instruments
            price_data: Dict of instrument -> price series

        Returns:
            List of eligible instrument codes
        """
        eligible = []
        for instr in all_instruments:
            if instr not in price_data:
                continue
            prices = price_data[instr]
            if self.is_eligible(instr, date, prices):
                eligible.append(instr)
        return eligible

    def get_universe_over_time(
        self,
        all_instruments: List[str],
        price_data: Dict[str, pd.Series],
        dates: Optional[pd.DatetimeIndex] = None,
    ) -> pd.DataFrame:
        """
        Get universe membership over time as a DataFrame.

        Args:
            all_instruments: List of all possible instruments
            price_data: Dict of instrument -> price series
            dates: Optional specific dates to check

        Returns:
            pd.DataFrame with dates as index, instruments as columns,
            boolean values indicating membership
        """
        # Get union of all dates if not provided
        if dates is None:
            all_dates = set()
            for prices in price_data.values():
                all_dates.update(prices.index)
            dates = pd.DatetimeIndex(sorted(all_dates))

        # Build eligibility matrix
        result = pd.DataFrame(index=dates, columns=all_instruments, dtype=bool)

        for instr in all_instruments:
            if instr not in price_data:
                result[instr] = False
                continue

            eligibility = self.get_eligibility_series(instr, price_data[instr])
            # Reindex to match dates, forward fill eligibility
            result[instr] = eligibility.reindex(dates, method='ffill').fillna(False)

        return result

    def _passes_cost_filter(
        self,
        instrument_code: str,
        date: pd.Timestamp,
    ) -> bool:
        """Check if instrument passes cost filter at date."""
        sr_cost = self._cost_estimator.get_sr_cost_per_trade(
            instrument_code, date
        )

        if np.isinf(sr_cost) or np.isnan(sr_cost):
            return False

        # Check per-trade threshold
        if sr_cost > self._max_sr_per_trade:
            return False

        # Check annual threshold
        annual_cost = sr_cost * self._stack_turnover
        if annual_cost > self._max_sr_annual:
            return False

        return True

    def _has_min_history(
        self,
        prices: pd.Series,
        date: pd.Timestamp,
    ) -> bool:
        """Check if instrument has enough history at date."""
        # Get prices up to date
        valid_prices = prices[prices.index <= date]

        if len(valid_prices) < MIN_HISTORY_ANY_RULE:
            return False

        return True

    def _get_sr_cost_series(
        self,
        instrument_code: str,
        prices: pd.Series,
    ) -> pd.Series:
        """Get time series of SR cost per trade."""
        spread_series = self._cost_estimator.get_spread_series(instrument_code)

        if len(spread_series) == 0:
            return pd.Series(index=prices.index, data=float('inf'))

        # Calculate daily volatility
        returns = np.log(prices / prices.shift(1))
        daily_vol = returns.rolling(35, min_periods=10).std()
        annual_vol = daily_vol * np.sqrt(252)

        # Align spread to price index
        spread_aligned = spread_series.reindex(prices.index, method='ffill')

        # Calculate SR cost
        spread_cost = spread_aligned / 10000
        fee_cost = 2 * (self._cost_estimator._fee_bps / 10000)
        total_cost = spread_cost + fee_cost

        sr_cost = total_cost / annual_vol
        sr_cost = sr_cost.replace([np.inf, -np.inf], np.nan)

        return sr_cost

    def _get_history_filter_series(
        self,
        prices: pd.Series,
    ) -> pd.Series:
        """Get time series of whether min history requirement is met."""
        # Count cumulative observations
        cum_count = pd.Series(range(1, len(prices) + 1), index=prices.index)

        # True when we have enough history
        return cum_count >= MIN_HISTORY_ANY_RULE

    def _get_vol_floor_series(
        self,
        prices: pd.Series,
    ) -> pd.Series:
        """
        Get time series of whether annualised vol exceeds the minimum floor.

        Uses the same 35-day rolling window as the SR cost calculation for
        consistency. Returns all-True when min_annual_vol == 0.0 (disabled).
        """
        if self._min_annual_vol <= 0.0:
            return pd.Series(True, index=prices.index)

        log_ret = np.log(prices / prices.shift(1))
        daily_vol = log_ret.rolling(35, min_periods=10).std()
        annual_vol = daily_vol * np.sqrt(252)

        # NaN before warmup → treat as ineligible (same conservative logic as cost filter)
        return annual_vol >= self._min_annual_vol

    def clear_cache(self):
        """Clear cached computations."""
        self._eligibility_cache.clear()


def get_equal_weights(instruments: List[str]) -> Dict[str, float]:
    """
    Calculate equal weights for a list of instruments.

    Args:
        instruments: List of instrument codes

    Returns:
        Dict of instrument -> weight (1/N for each)
    """
    if not instruments:
        return {}

    weight = 1.0 / len(instruments)
    return {instr: weight for instr in instruments}


def load_lifecycle_from_manifest(manifest_path) -> dict:
    """
    Load lifecycle metadata from dataset manifest.

    Args:
        manifest_path: Path to dataset manifest JSON file (can be Path or str)

    Returns:
        Lifecycle dict keyed by instrument ID:
        {
            'BTCUSDT_PERP': {
                'first_data_date': '2019-09-08',
                'last_data_date': '2026-02-13',
                'data_days': 2350,
                'status': 'ACTIVE',
                'days_since_last': 1
            },
            ...
        }

    Returns empty dict if manifest doesn't exist or has no lifecycle section.
    """
    import json
    from pathlib import Path

    manifest_path = Path(manifest_path)

    if not manifest_path.exists():
        return {}

    try:
        with open(manifest_path) as f:
            manifest = json.load(f)

        lifecycle = manifest.get('lifecycle', {})
        return lifecycle

    except Exception as e:
        logger = get_logger("lifecycle")
        logger.warning(f"Failed to load lifecycle from manifest: {e}")
        return {}


def check_lifecycle_eligibility(
    instrument_code: str,
    date: pd.Timestamp,
    lifecycle_data: dict
) -> bool:
    """
    Check if instrument has data coverage at date based on lifecycle metadata.

    This filters out instruments that either:
    - Haven't launched yet (date < first_data_date)
    - Have been delisted (date > last_data_date)
    - Have no data (status == 'NO_DATA')

    Args:
        instrument_code: Instrument ID
        date: Date to check
        lifecycle_data: Lifecycle dict from load_lifecycle_from_manifest()

    Returns:
        True if instrument has data coverage at date, False otherwise
    """
    if instrument_code not in lifecycle_data:
        # No lifecycle info = assume active (conservative fallback)
        return True

    lc = lifecycle_data[instrument_code]

    # Check status
    if lc.get('status') == 'NO_DATA':
        return False

    if lc.get('status') == 'ERROR':
        # Lifecycle derivation failed, assume active (conservative)
        return True

    # Check if within data coverage window
    first_date = lc.get('first_data_date')
    last_date = lc.get('last_data_date')

    if first_date:
        if date < pd.Timestamp(first_date):
            return False  # Before data launch

    if last_date:
        if date > pd.Timestamp(last_date):
            return False  # After data ends (delisted or stale)

    return True
