"""
CryptoPortfolioWithOIOverlay: defensive position scaling based on OI regime.

Extends CryptoPortfolios with an optional Open Interest (OI) regime overlay that
scales down positions when leverage/funding indicators suggest elevated cascade risk.

Phase 1 (MVP): Uses funding rate as OI proxy (zero new data acquisition).
Phase 2+: Will use true OI/LS ratio data when available.
"""

import pandas as pd
from systems.crypto_perps.crypto_portfolio import CryptoPortfolios
from systems.provided.crypto_example.core.dynamic_portfolio import CryptoDynamicPortfolio
from systems.system_cache import output


def apply_oi_overlay(portfolio_instance, instrument_code: str, base_position: pd.Series) -> pd.Series:
    """
    Helper function to apply OI regime overlay to a position series.

    Separated as a function so it can be reused by both static and dynamic portfolio classes.

    Supports two modes:
    1. Standard (bidirectional): Scale positions on any extreme funding
    2. Trend-aware: Only scale positions that fight the trend

    Args:
        portfolio_instance: Instance of portfolio stage (with config, parent, log)
        instrument_code: Instrument code
        base_position: Base position series (before OI scaling)

    Returns:
        pd.Series of positions scaled by OI regime multiplier
    """
    # Check if OI overlay is enabled
    if not portfolio_instance.config.get_element_or_default('use_oi_overlay', False):
        return base_position

    # Get OI regime multiplier from data layer
    try:
        params = portfolio_instance.config.get_element_or_default('oi_overlay_params', {})
        trend_aware = params.get('trend_aware', False)

        # If trend-aware mode, fetch combined forecast (trend signal)
        trend_forecast = None
        if trend_aware:
            try:
                # Get combined forecast from ForecastCombine stage
                # This represents the overall trend direction
                trend_forecast = portfolio_instance.parent.combForecast.get_combined_forecast(instrument_code)
            except Exception as e:
                portfolio_instance.log.warning(
                    f"{instrument_code}: Could not fetch trend forecast for trend-aware overlay ({e}), "
                    f"falling back to standard mode",
                    instrument_code=instrument_code,
                )
                trend_aware = False

        # Get OI regime multiplier
        oi_multiplier = portfolio_instance.parent.data.get_oi_regime_multiplier(
            instrument_code,
            lookback=params.get('lookback', 90),
            threshold=params.get('threshold', 2.0),
            min_scale=params.get('min_scale', 0.5),
            base_position=base_position if trend_aware else None,
            trend_forecast=trend_forecast if trend_aware else None,
            trend_aware=trend_aware,
        )

        # Align multiplier with base position index
        oi_multiplier = oi_multiplier.reindex(base_position.index, method='ffill').fillna(1.0)

        # Apply scaling
        scaled_position = base_position * oi_multiplier

        # Log summary statistics
        avg_multiplier = float(oi_multiplier.mean())
        min_multiplier = float(oi_multiplier.min())
        pct_scaled = float((oi_multiplier < 1.0).sum() / max(len(oi_multiplier), 1) * 100)

        mode_str = "trend-aware" if trend_aware else "standard"
        portfolio_instance.log.debug(
            f"{instrument_code}: OI overlay ({mode_str}) applied | "
            f"avg_mult={avg_multiplier:.3f} | min_mult={min_multiplier:.3f} | "
            f"scaled_days={pct_scaled:.1f}%",
            instrument_code=instrument_code,
        )

        return scaled_position

    except Exception as e:
        portfolio_instance.log.warning(
            f"{instrument_code}: OI overlay failed ({e}), returning unscaled position",
            instrument_code=instrument_code,
        )
        return base_position


class CryptoPortfolioWithOIOverlay(CryptoPortfolios):
    """
    Portfolio stage with OI regime overlay for defensive position scaling.

    Applies funding-based leverage indicator to scale positions down during
    periods of elevated funding (proxy for excessive leverage/positioning).

    The overlay is applied AFTER lot-size rounding and minimum notional filtering,
    so the final position reflects both Binance execution constraints and risk overlay.

    Configuration (in YAML):
        use_oi_overlay: true
        oi_overlay_params:
            lookback: 90           # Rolling window for z-score calculation
            threshold: 2.0         # Z-score threshold (positions scale at |z| > threshold)
            min_scale: 0.5         # Minimum position multiplier (max 50% reduction)

    Example:
        Normal funding (z < 2.0)     → multiplier = 1.0 (no scaling)
        Extreme funding (z = 3.0)    → multiplier = 0.5 (50% position reduction)
        Very extreme (z = 4.0+)      → multiplier = 0.5 (capped at min_scale)
    """

    @output()
    def get_notional_position(self, instrument_code: str) -> pd.Series:
        """
        Get notional position with optional OI regime overlay.

        First applies base portfolio logic (lot-size rounding, min notional filter),
        then applies OI regime multiplier if use_oi_overlay is enabled.

        Args:
            instrument_code: Instrument code

        Returns:
            pd.Series of notional positions (base-asset units), scaled by OI regime
        """
        # Get base position (with lot-size rounding + min notional filter)
        base_position = super().get_notional_position(instrument_code)

        # Apply OI overlay using helper function
        return apply_oi_overlay(self, instrument_code, base_position)


class CryptoDynamicPortfolioWithOIOverlay(CryptoDynamicPortfolio):
    """
    Dynamic portfolio stage with OI regime overlay for defensive position scaling.

    Combines walk-forward dynamic universe selection (from CryptoDynamicPortfolio)
    with OI regime overlay (defensive position scaling during leverage bubbles).

    See CryptoPortfolioWithOIOverlay docstring for OI overlay details.
    """

    @output()
    def get_notional_position(self, instrument_code: str) -> pd.Series:
        """
        Get notional position with dynamic universe filtering + OI regime overlay.

        First applies dynamic portfolio logic (universe filters, lot-size, min notional),
        then applies OI regime multiplier if use_oi_overlay is enabled.

        Args:
            instrument_code: Instrument code

        Returns:
            pd.Series of notional positions (base-asset units), scaled by OI regime
        """
        # Get base position (with dynamic universe + lot-size rounding + min notional)
        base_position = super().get_notional_position(instrument_code)

        # Apply OI overlay using helper function
        return apply_oi_overlay(self, instrument_code, base_position)
