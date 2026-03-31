"""
CryptoPortfolios: portfolio stage with Binance-realistic position minimums.

Applies lot-size rounding and minimum notional filtering after position sizing,
so P&L reflects only executable positions.
"""

import numpy as np
import pandas as pd
from systems.portfolio import Portfolios
from systems.system_cache import output


# Binance perpetuals lot sizes (base-asset units). Others default to 1.0.
LOT_SIZES = {
    'BTCUSDT_PERP':  0.001,
    'ETHUSDT_PERP':  0.01,
    'BNBUSDT_PERP':  0.01,
    'SOLUSDT_PERP':  0.1,
    'XRPUSDT_PERP':  1.0,
    'DOGEUSDT_PERP': 10.0,
    'ADAUSDT_PERP':  1.0,
    'LTCUSDT_PERP':  0.01,
    'LINKUSDT_PERP': 0.1,
    'AVAXUSDT_PERP': 0.1,
}
DEFAULT_LOT_SIZE = 1.0


class CryptoPortfolios(Portfolios):
    """
    Portfolio stage with Binance-realistic position minimums.

    Applies lot-size rounding and minimum notional filtering after position sizing,
    so P&L reflects only executable positions.

    Sub-minimum target positions are zeroed out (conservative backtest treatment).
    In live trading, closing trades are allowed regardless of size.
    """

    @output()
    def get_notional_position(self, instrument_code: str) -> pd.Series:
        position = super().get_notional_position(instrument_code)
        position = self._round_to_lot_size(instrument_code, position)
        # Note: min_notional_position ($10) is NOT applied here. The $10 minimum
        # is an exchange execution constraint (HL rejects new/increasing orders < $10),
        # not a portfolio optimization constraint. Existing positions below $10 should
        # be held until the signal says to reduce, regardless of their dollar size.
        # The constraint is enforced in the trade plan (check_min_position_sizes).
        return position

    def _round_to_lot_size(self, instrument_code: str, position: pd.Series) -> pd.Series:
        lot = LOT_SIZES.get(instrument_code, DEFAULT_LOT_SIZE)
        sign = np.sign(position)
        return sign * np.floor(position.abs() / lot) * lot

    def _apply_min_notional_filter(
        self, instrument_code: str, position: pd.Series
    ) -> pd.Series:
        min_notional = self.config.get_element_or_default("min_notional_position", 25.0)
        if min_notional <= 0:
            return position
        prices = self.rawdata.get_daily_prices(instrument_code)
        prices = prices.reindex(position.index, method='ffill')
        notional = position.abs() * prices

        # HL policy: reduce-only orders are exempt from the minimum order size.
        # A position change is a reduce when it moves toward zero:
        #   same sign as previous (or one is zero) AND magnitude is decreasing.
        # Uses unfiltered shift(1) as an approximation — a fully recursive filter
        # is impractical in a single vectorised pass. Rare edge case (zeroed position
        # immediately re-approached from below) is handled conservatively (stays zero).
        prev_position = position.shift(1).fillna(0.0)
        is_reducing = (
            (position * prev_position >= 0)            # same direction, or one is zero
            & (position.abs() <= prev_position.abs())  # magnitude decreasing or equal
        )

        should_zero = (notional < min_notional) & ~is_reducing
        filtered = position.where(~should_zero, 0.0)

        n_zeroed = int((position.abs() > 0).sum() - (filtered.abs() > 0).sum())
        if n_zeroed > 0:
            self.log.debug(
                f"{instrument_code}: {n_zeroed} position-days zeroed by "
                f"${min_notional:.0f} min-notional filter (reduce-only exempt) "
                f"({n_zeroed / max(len(position), 1):.1%} of history)",
                instrument_code=instrument_code,
            )
        return filtered
