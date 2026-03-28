"""
Walk-forward cost estimation for crypto instruments.

Computes spread estimates and SR costs using only trailing data,
ensuring no lookahead bias in backtests.
"""

import pandas as pd
import numpy as np
from typing import Dict, Optional
from functools import lru_cache

from syslogging.logger import get_logger


# ADV$ to spread (bps) mapping - conservative bins
# These are deliberately conservative to avoid overfitting
ADV_SPREAD_BINS = [
    (50_000_000, 5),     # >$50M daily: 5 bps
    (10_000_000, 10),    # $10M-$50M: 10 bps
    (1_000_000, 20),     # $1M-$10M: 20 bps
    (0, 40),             # <$1M: 40 bps (often excluded by cost filter)
]

# Default trading fee (one-way) for major exchanges
DEFAULT_FEE_BPS = 4.5  # 4.5 bps — Hyperliquid taker fee (0.045% at standard capital level)

# Rule turnover estimates (round-trips per year)
# Based on empirical observations from Carver's work
RULE_TURNOVER = {
    "ewmac8_32": 30,
    "ewmac16_64": 18,
    "ewmac32_128": 10,
    "ewmac64_256": 6,
    "breakout10": 20,
    "breakout20": 15,
    "breakout40": 12,
    "breakout80": 8,
    "tsmom63": 10,
    "tsmom126": 8,
    "tsmom252": 6,
    "accel16": 25,
    "accel32": 18,
    "relmomentum20": 15,
    "relmomentum40": 10,
}


class WalkForwardCostEstimator:
    """
    Estimates trading costs walk-forward using only trailing data.

    At each date, computes:
    1. Trailing ADV$ (30-day median dollar volume)
    2. Spread estimate from ADV$ bins
    3. SR cost per trade
    4. Annual SR cost using stack-weighted turnover
    """

    def __init__(
        self,
        prices_data,
        adv_window: int = 30,
        fee_bps: float = DEFAULT_FEE_BPS,
        adv_panel: Optional[pd.DataFrame] = None,
        log=get_logger("WalkForwardCostEstimator"),
    ):
        """
        Args:
            prices_data: csvSpotPricesData instance for accessing price/volume
            adv_window: Rolling window for ADV calculation (default 30 days)
            fee_bps: One-way trading fee in basis points
            adv_panel: Optional wide DataFrame (dates × instruments) of ADV notional.
                       When provided, get_spread_series() uses cross-sectional rank tiers
                       (top 20 → 2 bps, rank 21-70 → 5 bps, rest → 12 bps).
            log: Logger instance
        """
        self._prices_data = prices_data
        self._adv_window = adv_window
        self._fee_bps = fee_bps
        self._adv_panel = adv_panel
        self._log = log

        # Cache for computed series
        self._adv_cache: Dict[str, pd.Series] = {}
        self._spread_cache: Dict[str, pd.Series] = {}

    def get_trailing_adv(self, instrument_code: str) -> pd.Series:
        """
        Get trailing average daily volume in USD.

        Uses 30-day median of (price × volume) to be robust to outliers.

        Args:
            instrument_code: Instrument code

        Returns:
            pd.Series of ADV$ values with datetime index
        """
        if instrument_code in self._adv_cache:
            return self._adv_cache[instrument_code]

        prices = self._prices_data.get_spot_prices(instrument_code)
        volume = self._prices_data.get_spot_volume(instrument_code)

        if len(prices) == 0 or len(volume) == 0:
            self._log.warning(f"No price/volume data for {instrument_code}")
            return pd.Series(dtype=float)

        # Align price and volume on common index
        combined = pd.DataFrame({"price": prices, "volume": volume})
        combined = combined.dropna()

        if len(combined) == 0:
            return pd.Series(dtype=float)

        # Dollar volume = price × volume
        dollar_volume = combined["price"] * combined["volume"]

        # Trailing median ADV$ (more robust than mean)
        adv = dollar_volume.rolling(
            window=self._adv_window,
            min_periods=min(10, self._adv_window)
        ).median()

        self._adv_cache[instrument_code] = adv
        return adv

    def get_spread_series(self, instrument_code: str) -> pd.Series:
        """
        Get time series of spread estimates in basis points.

        When adv_panel is provided, uses time-varying cross-sectional rank tiers:
          top 20 → 2 bps, rank 21-70 → 5 bps, rest → 12 bps.

        Otherwise, maps trailing ADV$ to spread using absolute bins.
        Higher volume = lower spread.

        Args:
            instrument_code: Instrument code

        Returns:
            pd.Series of spread estimates (bps) with datetime index
        """
        if instrument_code in self._spread_cache:
            return self._spread_cache[instrument_code]

        if self._adv_panel is not None and instrument_code in self._adv_panel.columns:
            rank = self._adv_panel.rank(axis=1, ascending=False)
            rank_series = rank[instrument_code]
            spread = rank_series.copy()
            spread[rank_series <= 20] = 2.0
            spread[(rank_series > 20) & (rank_series <= 70)] = 5.0
            spread[rank_series > 70] = 12.0
            spread[rank_series.isna()] = 12.0
            self._spread_cache[instrument_code] = spread
            return spread

        adv = self.get_trailing_adv(instrument_code)

        if len(adv) == 0:
            return pd.Series(dtype=float)

        # Use np.select for clean bin assignment
        # Conditions are checked in order, first match wins
        conditions = [
            adv >= 50_000_000,   # >$50M: 5 bps
            adv >= 10_000_000,   # $10M-$50M: 10 bps
            adv >= 1_000_000,    # $1M-$10M: 20 bps
        ]
        choices = [5, 10, 20]
        default = 40  # <$1M: 40 bps

        spread_values = np.select(conditions, choices, default=default)
        spread = pd.Series(spread_values, index=adv.index, dtype=float)

        self._spread_cache[instrument_code] = spread
        return spread

    def get_spread_at_date(
        self,
        instrument_code: str,
        date: pd.Timestamp
    ) -> float:
        """
        Get spread estimate at a specific date.

        Args:
            instrument_code: Instrument code
            date: Date to get spread for

        Returns:
            Spread in basis points
        """
        spread_series = self.get_spread_series(instrument_code)

        if len(spread_series) == 0:
            return ADV_SPREAD_BINS[-1][1]  # Default to highest spread

        # Get most recent spread on or before date
        valid = spread_series[spread_series.index <= date]
        if len(valid) == 0:
            return ADV_SPREAD_BINS[-1][1]

        return valid.iloc[-1]

    def get_sr_cost_per_trade(
        self,
        instrument_code: str,
        date: pd.Timestamp,
        annual_vol: Optional[float] = None,
    ) -> float:
        """
        Calculate SR cost per trade at a specific date.

        SR_cost = (spread_bps/10000 + 2×fee) / annual_volatility

        Args:
            instrument_code: Instrument code
            date: Date to calculate for
            annual_vol: Annual volatility (if None, estimates from data)

        Returns:
            SR cost per round-trip trade
        """
        spread_bps = self.get_spread_at_date(instrument_code, date)

        if annual_vol is None:
            annual_vol = self._estimate_annual_vol(instrument_code, date)

        if annual_vol <= 0 or np.isnan(annual_vol):
            return float('inf')  # Cannot trade with zero vol

        # Total round-trip cost = spread + 2×fee (entry + exit)
        spread_cost = spread_bps / 10000
        fee_cost = 2 * (self._fee_bps / 10000)
        total_cost = spread_cost + fee_cost

        # SR cost = total cost / annual vol
        sr_cost = total_cost / annual_vol

        return sr_cost

    def get_annual_sr_cost(
        self,
        instrument_code: str,
        date: pd.Timestamp,
        turnover: float,
        annual_vol: Optional[float] = None,
    ) -> float:
        """
        Calculate annual SR cost at a specific date.

        Annual_SR_cost = SR_cost_per_trade × turnover

        Args:
            instrument_code: Instrument code
            date: Date to calculate for
            turnover: Expected round-trips per year
            annual_vol: Annual volatility (if None, estimates from data)

        Returns:
            Annual SR cost
        """
        sr_per_trade = self.get_sr_cost_per_trade(
            instrument_code, date, annual_vol
        )
        return sr_per_trade * turnover

    def _estimate_annual_vol(
        self,
        instrument_code: str,
        date: pd.Timestamp,
        window: int = 35,
    ) -> float:
        """
        Estimate annualized volatility using trailing data.

        Uses same window as main volatility calculation (35 days).
        """
        prices = self._prices_data.get_spot_prices(instrument_code)

        if len(prices) == 0:
            return np.nan

        # Filter to data before date
        prices = prices[prices.index <= date]
        if len(prices) < 10:
            return np.nan

        # Daily returns
        returns = np.log(prices / prices.shift(1))

        # Trailing volatility
        daily_vol = returns.iloc[-window:].std()

        # Annualize
        annual_vol = daily_vol * np.sqrt(252)

        return annual_vol

    def clear_cache(self):
        """Clear cached computations."""
        self._adv_cache.clear()
        self._spread_cache.clear()


def calculate_stack_turnover(
    forecast_weights: Dict[str, float],
    rule_turnover: Dict[str, float] = None,
) -> float:
    """
    Calculate weighted average turnover for a forecast stack.

    Args:
        forecast_weights: Dict of rule_name -> weight
        rule_turnover: Dict of rule_name -> turnover (uses defaults if None)

    Returns:
        Weighted average turnover (round-trips per year)
    """
    if rule_turnover is None:
        rule_turnover = RULE_TURNOVER

    total_weight = 0
    weighted_turnover = 0

    for rule, weight in forecast_weights.items():
        if rule in rule_turnover:
            turnover = rule_turnover[rule]
        else:
            # Default to moderate turnover for unknown rules
            turnover = 15

        weighted_turnover += weight * turnover
        total_weight += weight

    if total_weight == 0:
        return 15  # Default fallback

    return weighted_turnover / total_weight
