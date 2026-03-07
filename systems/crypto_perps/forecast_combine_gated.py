"""
Forecast combination with optional sleeve overrides for crypto perpetuals.

Post-2026-03-06 refactor: gated carry rules are now standard Carver trading
rules (gated_carry_10/30/60) that go through the standard pipeline. This class
only handles residual special-case logic that cannot be expressed as standard
rules:
- XSMOM long-only gate (disabled, kept for future research)
- Sector momentum additive sleeve (disabled at sector_weight=0.0)
- XS addr_growth dead code (disabled at xs_addr_growth_weight=0.0)
- Forecast tilt (disabled at forecast_tilt_offset=0.0)
"""

from systems.forecast_combine import ForecastCombine
from systems.system_cache import output
import pandas as pd
import numpy as np


class ForecastCombineGated(ForecastCombine):
    """
    Forecast combination with optional sleeve overrides.

    All carry logic has been moved into standard trading rules (gated_carry_10/30/60).
    This class now handles only the remaining special-case sleeves.
    """

    @output()
    def get_combined_forecast(self, instrument_code: str) -> pd.Series:
        """
        Override to apply optional sleeve overrides after standard weighted sum.

        Process:
        1. Get weighted forecasts (before FDM) — includes gated_carry_* rules
        2. XSMOM gate (disabled at xsmom_long_only: false)
        3. Standard sum of weighted forecasts
        4. Sector momentum sleeve (disabled at sector_weight: 0.0)
        5. XS addr_growth sleeve (disabled at xs_addr_growth_weight: 0.0)
        6. Forecast tilt (disabled at forecast_tilt_offset: 0.0)
        7. Apply FDM and capping
        """
        # Get weighted forecasts (before FDM)
        weighted_forecasts = self.get_weighted_forecasts_without_multiplier(
            instrument_code
        )

        # Get config parameters
        config = self.parent.config

        # XSMOM long-only gate: clip cross-sectional rule negative forecasts to zero.
        # Lit: Han et al. (2024) / Dobrynskaya — cross-sectional momentum alpha concentrated
        # in winners (long leg); loser portfolio reverts, not continues falling.
        # Only relmomentum/assettrend are true XSMOM — normmom/residual are time-series.
        xsmom_long_only = config.get_element_or_default('xsmom_long_only', False)
        xsmom_rules = config.get_element_or_default('xsmom_rule_list', [])
        if xsmom_long_only and xsmom_rules:
            xsmom_cols = [c for c in weighted_forecasts.columns if c in xsmom_rules]
            weighted_forecasts[xsmom_cols] = weighted_forecasts[xsmom_cols].clip(lower=0.0)

        # Standard sum of all weighted forecasts (gated_carry_* included here)
        final_forecast_raw = weighted_forecasts.sum(axis=1)

        # Additive sector sleeve: final += sector_weight × mean(sector_forecasts)
        # Sector forecasts come directly from ForecastScaleCap (already scaled+capped, ±20).
        # They bypass forecast_weights normalisation entirely so trend budget is untouched.
        sector_rules = config.get_element_or_default('sector_rule_list', [])
        sector_weight = config.get_element_or_default('sector_weight', 0.0)
        if sector_rules and sector_weight > 0:
            sector_series = []
            for rule in sector_rules:
                try:
                    fc = self.parent.forecastScaleCap.get_capped_forecast(
                        instrument_code, rule
                    )
                    if fc is not None and not fc.dropna().empty:
                        sector_series.append(fc.reindex(final_forecast_raw.index))
                except Exception:
                    pass
            if sector_series:
                sector_avg = pd.concat(sector_series, axis=1).mean(axis=1)
                # NaN where all sector forecasts are NaN (Other sector or <3 peers)
                sector_avg = sector_avg.fillna(0.0)
                final_forecast_raw = final_forecast_raw + sector_weight * sector_avg

        # XS Addr Growth sleeve: cross-sectional address growth rate (NET factor).
        # Lit: Cong et al. (2022) C-5 — adoption velocity (growth rate of AdrActCnt).
        # DISABLED: xs_addr_growth_weight=0.0 — ablation shows only -0.3% marginal ΔSharpe.
        xs_addr_growth_weight = config.get_element_or_default('xs_addr_growth_weight', 0.0)
        if xs_addr_growth_weight != 0.0:
            xs_addr_growth_lookback = config.get_element_or_default('xs_addr_growth_lookback', 30)
            xs_addr_growth_window = config.get_element_or_default('xs_addr_growth_window', 90)
            xs_addr_growth_fc = self._get_xs_addr_growth_forecast(
                instrument_code, lookback=xs_addr_growth_lookback, growth_window=xs_addr_growth_window
            )
            if xs_addr_growth_fc is not None and not xs_addr_growth_fc.empty:
                xs_addr_growth_fc = xs_addr_growth_fc.reindex(final_forecast_raw.index).fillna(0.0)
                final_forecast_raw = final_forecast_raw + xs_addr_growth_weight * xs_addr_growth_fc

        # Forecast tilt: constant offset to bias toward more predictive direction
        # Applied after all sleeves, before FDM and ±20 cap
        forecast_tilt_offset = config.get_element_or_default('forecast_tilt_offset', 0.0)
        if forecast_tilt_offset != 0.0:
            final_forecast_raw = final_forecast_raw + forecast_tilt_offset

        # Apply FDM and capping (existing logic)
        fdm = self.get_forecast_diversification_multiplier(instrument_code).reindex(
            final_forecast_raw.index
        ).ffill()
        final_multiplied = final_forecast_raw * fdm

        # Cap to forecast limits
        mapping_func, kwargs = self._get_forecast_mapping_function(instrument_code)
        final_forecast = mapping_func(final_multiplied, **kwargs)

        return final_forecast

    def _get_xs_addr_growth_panel(
        self, lookback: int = 30, growth_window: int = 90
    ) -> pd.DataFrame:
        """
        Build cross-sectional address growth rate forecast panel (cached per params).

        Computes rolling % growth of EWM-smoothed AdrActCnt per instrument, then
        ranks cross-sectionally at each date → ±20 forecast.

        Sign: high growth (pct≈1.0) → forecast +20 (LONG) — growing adoption = undervalued.
        Lit: Cong et al. (2022) C-5 NET factor — network adoption velocity predicts returns.

        Distinct from xs_activity (level): growth rate re-ranks dynamically across sector
        regimes (DeFi Summer, L1 season, AI tokens) rather than structurally favouring BTC/ETH.
        """
        cache_key = f'_xs_addr_growth_panel_{lookback}_{growth_window}'
        if hasattr(self, cache_key):
            return getattr(self, cache_key)

        instrument_list = self.parent.data.get_instrument_list()
        growth_dict = {}
        for instr in instrument_list:
            try:
                addr = self.parent.data.get_active_addresses(instr)
                if addr is None or len(addr.dropna()) < growth_window + lookback:
                    continue
                addr_smooth = addr.ewm(span=lookback, min_periods=1).mean()
                growth = addr_smooth.pct_change(periods=growth_window)
                growth_dict[instr] = growth
            except Exception:
                continue

        if not growth_dict:
            setattr(self, cache_key, pd.DataFrame())
            return pd.DataFrame()

        growth_df = pd.DataFrame(growth_dict)
        pct_rank = growth_df.rank(axis=1, pct=True)       # cross-sectional rank per date
        forecast_panel = (pct_rank - 0.5) * 40.0          # [0,1] → [-20, +20], high = LONG

        setattr(self, cache_key, forecast_panel)
        return forecast_panel

    def _get_xs_addr_growth_forecast(
        self, instrument_code: str, lookback: int = 30, growth_window: int = 90
    ) -> pd.Series:
        """Return cross-sectional address growth forecast for one instrument (±20 scale)."""
        panel = self._get_xs_addr_growth_panel(lookback=lookback, growth_window=growth_window)
        if panel.empty or instrument_code not in panel.columns:
            return pd.Series(dtype=float)
        return panel[instrument_code]
