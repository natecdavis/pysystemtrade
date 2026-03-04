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
            mode=params.get('mode', 'funding'),
            oi_volume_window=params.get('oi_volume_window', 7),
        )

        # Align multiplier with base position index
        oi_multiplier = oi_multiplier.reindex(base_position.index, method='ffill').fillna(1.0)

        # Apply scaling
        scaled_position = base_position * oi_multiplier

        # Log summary statistics
        avg_multiplier = float(oi_multiplier.mean())
        min_multiplier = float(oi_multiplier.min())
        pct_scaled = float((oi_multiplier < 1.0).sum() / max(len(oi_multiplier), 1) * 100)

        overlay_mode = params.get('mode', 'funding')
        mode_str = f"{overlay_mode}+trend-aware" if trend_aware else overlay_mode
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


def apply_fg_overlay(portfolio_instance, instrument_code: str, base_position: pd.Series) -> pd.Series:
    """
    Helper function to apply Fear & Greed regime overlay to a position series.

    Contrarian scaling: reduces positions during extreme greed (crowded/bubble conditions).
    Does not suppress positions during extreme fear (trend signals remain reliable).

    Separated as a function so it can be reused by both static and dynamic portfolio classes.

    Args:
        portfolio_instance: Instance of portfolio stage (with config, parent, log)
        instrument_code: Instrument code (same multiplier for all — F&G is a global index)
        base_position: Base position series (after OI overlay)

    Returns:
        pd.Series of positions scaled by F&G regime multiplier
    """
    if not portfolio_instance.config.get_element_or_default('use_fg_overlay', False):
        return base_position

    try:
        params = portfolio_instance.config.get_element_or_default('fg_overlay_params', {})
        fg_multiplier = portfolio_instance.parent.data.get_fg_regime_multiplier(
            greed_threshold=params.get('greed_threshold', 75),
            fear_threshold=params.get('fear_threshold', 25),
            min_scale=params.get('min_scale', 0.5),
        )

        if fg_multiplier.empty:
            portfolio_instance.log.warning(
                f"{instrument_code}: F&G overlay enabled but no data loaded — skipping",
                instrument_code=instrument_code,
            )
            return base_position

        # Align multiplier with base position index (forward-fill gaps)
        fg_multiplier = fg_multiplier.reindex(base_position.index, method='ffill').fillna(1.0)

        scaled_position = base_position * fg_multiplier

        # Log summary statistics (debug level to avoid noise)
        avg_multiplier = float(fg_multiplier.mean())
        min_multiplier = float(fg_multiplier.min())
        pct_scaled = float((fg_multiplier < 1.0).sum() / max(len(fg_multiplier), 1) * 100)
        portfolio_instance.log.debug(
            f"{instrument_code}: F&G overlay applied | "
            f"avg_mult={avg_multiplier:.3f} | min_mult={min_multiplier:.3f} | "
            f"scaled_days={pct_scaled:.1f}%",
            instrument_code=instrument_code,
        )

        return scaled_position

    except Exception as e:
        portfolio_instance.log.warning(
            f"{instrument_code}: F&G overlay failed ({e}), returning unscaled position",
            instrument_code=instrument_code,
        )
        return base_position


def apply_mvrv_overlay(portfolio_instance, instrument_code: str, base_position: pd.Series) -> pd.Series:
    """
    Helper function to apply MVRV on-chain regime overlay to a position series.

    Contrarian scaling: reduces positions during overheated on-chain conditions
    (high MVRV = market value well above realized value = bubble risk).

    Separated as a function so it can be reused by both static and dynamic portfolio classes.

    Args:
        portfolio_instance: Instance of portfolio stage (with config, parent, log)
        instrument_code: Instrument code (same multiplier for all — MVRV is a global BTC ratio)
        base_position: Base position series (after OI and F&G overlays)

    Returns:
        pd.Series of positions scaled by MVRV regime multiplier
    """
    if not portfolio_instance.config.get_element_or_default('use_mvrv_overlay', False):
        return base_position

    try:
        params = portfolio_instance.config.get_element_or_default('mvrv_overlay_params', {})
        mvrv_multiplier = portfolio_instance.parent.data.get_mvrv_regime_multiplier(
            overbought_threshold=params.get('overbought_threshold', 3.0),
            oversold_threshold=params.get('oversold_threshold', 1.0),
            min_scale=params.get('min_scale', 0.5),
            max_mvrv=params.get('max_mvrv', 5.0),
        )

        if mvrv_multiplier.empty:
            portfolio_instance.log.warning(
                f"{instrument_code}: MVRV overlay enabled but no data loaded — skipping",
                instrument_code=instrument_code,
            )
            return base_position

        # Align multiplier with base position index (forward-fill gaps)
        mvrv_multiplier = mvrv_multiplier.reindex(base_position.index, method='ffill').fillna(1.0)

        scaled_position = base_position * mvrv_multiplier

        # Log summary statistics (debug level to avoid noise)
        avg_multiplier = float(mvrv_multiplier.mean())
        min_multiplier = float(mvrv_multiplier.min())
        pct_scaled = float((mvrv_multiplier < 1.0).sum() / max(len(mvrv_multiplier), 1) * 100)
        portfolio_instance.log.debug(
            f"{instrument_code}: MVRV overlay applied | "
            f"avg_mult={avg_multiplier:.3f} | min_mult={min_multiplier:.3f} | "
            f"scaled_days={pct_scaled:.1f}%",
            instrument_code=instrument_code,
        )

        return scaled_position

    except Exception as e:
        portfolio_instance.log.warning(
            f"{instrument_code}: MVRV overlay failed ({e}), returning unscaled position",
            instrument_code=instrument_code,
        )
        return base_position


def apply_downside_beta_overlay(
    portfolio_instance, instrument_code: str, base_position: pd.Series
) -> pd.Series:
    """
    Per-instrument downside beta position scalar.

    Reduces positions in instruments that amplify crypto bear-market crashes.
    β_down is ranked cross-sectionally per date — highest β_down → min_scale,
    lowest → 1.0. The scalar is always-on and continuous (not threshold-based).

    Orthogonal to OI overlay: OI fires episodically (portfolio-level); downside
    beta fires continuously (per-instrument). Compound crash risk → compound
    de-sizing when both are active.

    Args:
        portfolio_instance: Instance of portfolio stage (with config, parent, log)
        instrument_code: Instrument code
        base_position: Base position series (after OI/F&G/MVRV overlays)

    Returns:
        pd.Series of positions scaled by cross-sectional downside beta scalar
    """
    if not portfolio_instance.config.get_element_or_default(
        'use_downside_beta_overlay', False
    ):
        return base_position

    try:
        params = portfolio_instance.config.get_element_or_default(
            'downside_beta_params', {}
        )
        min_scale = params.get('min_scale', 0.5)

        scalar = portfolio_instance.parent.data.get_downside_beta_scalar(
            instrument_code, min_scale=min_scale
        )

        if scalar.empty:
            portfolio_instance.log.warning(
                f"{instrument_code}: downside beta overlay enabled but panel not "
                f"loaded — skipping (check use_downside_beta_overlay in config)",
                instrument_code=instrument_code,
            )
            return base_position

        scalar = scalar.reindex(base_position.index, method='ffill').fillna(1.0)
        scaled_position = base_position * scalar

        portfolio_instance.log.debug(
            f"{instrument_code}: β_down overlay | "
            f"avg_scalar={float(scalar.mean()):.3f} "
            f"min_scalar={float(scalar.min()):.3f}",
            instrument_code=instrument_code,
        )
        return scaled_position

    except Exception as e:
        portfolio_instance.log.warning(
            f"{instrument_code}: β_down overlay failed ({e}), returning unscaled",
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

        # Apply overlays in sequence: OI → F&G → MVRV → downside beta
        position = apply_oi_overlay(self, instrument_code, base_position)
        position = apply_fg_overlay(self, instrument_code, position)
        position = apply_mvrv_overlay(self, instrument_code, position)
        position = apply_downside_beta_overlay(self, instrument_code, position)
        return position


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

        # Apply overlays in sequence: OI → F&G → MVRV → downside beta
        position = apply_oi_overlay(self, instrument_code, base_position)
        position = apply_fg_overlay(self, instrument_code, position)
        position = apply_mvrv_overlay(self, instrument_code, position)
        position = apply_downside_beta_overlay(self, instrument_code, position)
        return position
