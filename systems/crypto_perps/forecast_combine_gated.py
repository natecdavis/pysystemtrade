"""
Trend-gated forecast combination for crypto perpetuals.

Implements the user specification:
- Gate carry forecasts by trend strength
- Only allow carry when sign(trend) == sign(carry)
- Blend carry as small additive sleeve (w_c ∈ [0.1, 0.3])

Architecture:
- Layer 1: Individual rules produce forecasts (trend + carry)
- Layer 2: Trend-gated combination (this module)
  - Calculate trend strength (sum of trend rule forecasts)
  - Apply cross-sectional percentile ranking to carry scores
  - Gate carry: zero out when sign(trend) ≠ sign(carry)
  - Apply small weight w_c ∈ [0.1, 0.3]
- Layer 3: Standard position sizing (unchanged)
"""

from systems.forecast_combine import ForecastCombine
from systems.system_cache import diagnostic, output
import pandas as pd
import numpy as np


class ForecastCombineGated(ForecastCombine):
    """
    Forecast combination with trend-gated carry logic.

    Overrides get_combined_forecast() to:
    1. Calculate trend strength (sum of trend rule forecasts)
    2. Apply cross-sectional percentile ranking to carry scores
    3. Gate carry: zero out when sign(trend) ≠ sign(carry)
    4. Blend with small carry weight w_c
    """

    @output()
    def get_combined_forecast(self, instrument_code: str) -> pd.Series:
        """
        Override to implement trend-gated carry blending.

        Process:
        1. Get weighted forecasts (before FDM)
        2. Separate trend vs carry rule forecasts
        3. Calculate trend strength
        4. Apply cross-sectional percentile ranking to carry
        5. Gate carry by trend (zero when conflicting)
        6. Blend: final = trend + (carry_weight × carry_gated)
        7. Apply FDM and capping
        """
        # Get weighted forecasts (before FDM)
        weighted_forecasts = self.get_weighted_forecasts_without_multiplier(
            instrument_code
        )

        # Get config parameters
        config = self.parent.config
        use_gated_carry = config.get_element_or_default('use_gated_carry', False)

        # If gating is disabled, use standard combination
        if not use_gated_carry:
            return super().get_combined_forecast(instrument_code)

        carry_weight = config.get_element_or_default('carry_weight', 0.2)
        trend_gate_threshold = config.get_element_or_default('carry_trend_gate_threshold', 1.0)

        # Identify trend vs carry rules
        trend_rules = config.get_element_or_default(
            'trend_rule_list',
            ['ewmac_8', 'ewmac_16', 'ewmac_32', 'normmom_8', 'normmom_16', 'normmom_32',
             'breakout_20', 'breakout_40', 'breakout_80', 'breakout_160',
             'accel_16', 'accel_32', 'accel_64',
             'assettrend_8', 'assettrend_16', 'assettrend_32', 'assettrend_64',
             'relmomentum_20', 'relmomentum_40',
             'residual_momentum_16', 'residual_momentum_32', 'residual_momentum_64']
        )

        carry_rules = config.get_element_or_default(
            'carry_rule_list',
            ['vol_norm_carry_10', 'vol_norm_carry_30', 'vol_norm_carry_60']
        )

        # Extract trend and carry columns
        trend_cols = [c for c in weighted_forecasts.columns if c in trend_rules]
        carry_cols = [c for c in weighted_forecasts.columns if c in carry_rules]

        if not carry_cols:
            # No carry rules active, use standard combination
            return super().get_combined_forecast(instrument_code)

        # Calculate trend strength (sum of weighted trend forecasts)
        trend_strength = weighted_forecasts[trend_cols].sum(axis=1)

        # Get raw carry scores (sum of weighted carry forecasts)
        carry_raw = weighted_forecasts[carry_cols].sum(axis=1)

        # carry_gate_mode controls how carry is combined:
        #   'additive_sleeve' (default): percentile-rank carry, add with carry_weight multiplier
        #   'weighted': gate carry_raw directly, carry contributes via its forecast_weight
        carry_gate_mode = config.get_element_or_default('carry_gate_mode', 'additive_sleeve')

        if carry_gate_mode == 'weighted':
            # Equal-weight mode: no percentile ranking, gate carry_raw directly
            carry_for_gate = carry_raw
        else:
            # Additive sleeve mode: cross-sectional percentile ranking
            carry_for_gate = self._apply_percentile_ranking_to_carry(carry_raw, instrument_code)

        # Gate condition: abs(trend) < threshold OR sign mismatch
        weak_trend_mask = abs(trend_strength) < trend_gate_threshold
        sign_mismatch_mask = np.sign(trend_strength) != np.sign(carry_for_gate)
        gate_mask = weak_trend_mask | sign_mismatch_mask

        carry_gated = carry_for_gate.copy()
        carry_gated[gate_mask] = 0.0

        trend_forecast = trend_strength
        if carry_gate_mode == 'weighted':
            # Carry contributes via its forecast_weight allocation, no extra multiplier
            final_forecast_raw = trend_forecast + carry_gated
        else:
            # Additive sleeve: carry_weight scales the percentile-ranked carry
            final_forecast_raw = trend_forecast + (carry_weight * carry_gated)

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

        # Apply FDM and capping (existing logic)
        fdm = self.get_forecast_diversification_multiplier(instrument_code).reindex(
            final_forecast_raw.index
        ).ffill()
        final_multiplied = final_forecast_raw * fdm

        # Cap to forecast limits
        mapping_func, kwargs = self._get_forecast_mapping_function(instrument_code)
        final_forecast = mapping_func(final_multiplied, **kwargs)

        return final_forecast

    def _apply_percentile_ranking_to_carry(
        self, carry_raw: pd.Series, instrument_code: str
    ) -> pd.Series:
        """
        Apply cross-sectional percentile ranking to carry scores.

        For each date, rank this instrument's carry score against all other
        instruments' carry scores, then map percentile to forecast ∈ [-20, +20].

        Args:
            carry_raw: Raw carry score for this instrument (sum of carry rule forecasts)
            instrument_code: Current instrument being processed

        Returns:
            Percentile-ranked carry forecast ∈ [-20, +20]
        """
        config = self.parent.config
        carry_rules = config.get_element_or_default(
            'carry_rule_list',
            ['vol_norm_carry_10', 'vol_norm_carry_30', 'vol_norm_carry_60']
        )

        # Get all instruments
        instrument_list = self.parent.data.get_instrument_list()

        # Build panel of all instruments' carry scores
        carry_panel = pd.DataFrame(index=carry_raw.index)

        for instr in instrument_list:
            try:
                # Get weighted forecasts for this instrument
                weighted = self.get_weighted_forecasts_without_multiplier(instr)
                carry_cols = [c for c in weighted.columns if c in carry_rules]
                if carry_cols:
                    carry_panel[instr] = weighted[carry_cols].sum(axis=1)
            except Exception:
                # Skip instruments with missing data
                continue

        # Apply percentile ranking date-by-date
        ranked = pd.Series(0.0, index=carry_raw.index)

        for date in carry_raw.index:
            try:
                # Get all instruments' carry scores at this date
                scores = carry_panel.loc[date].dropna()

                if len(scores) < 2:
                    # Not enough instruments to rank
                    ranked.loc[date] = 0.0
                    continue

                if instrument_code not in scores.index:
                    # This instrument doesn't have a score today
                    ranked.loc[date] = 0.0
                    continue

                # Percentile rank (0 to 1)
                percentile = scores.rank(pct=True)[instrument_code]

                # Map to [-20, +20]: forecast = 40 × (percentile - 0.5)
                ranked.loc[date] = 40 * (percentile - 0.5)

            except (KeyError, IndexError):
                ranked.loc[date] = 0.0

        return ranked

    @diagnostic()
    def get_trend_strength(self, instrument_code: str) -> pd.Series:
        """
        Diagnostic: Calculate trend strength for analysis.

        Returns:
            Sum of weighted trend rule forecasts
        """
        weighted_forecasts = self.get_weighted_forecasts_without_multiplier(
            instrument_code
        )
        config = self.parent.config
        trend_rules = config.get_element_or_default('trend_rule_list', [])
        trend_cols = [c for c in weighted_forecasts.columns if c in trend_rules]
        return weighted_forecasts[trend_cols].sum(axis=1)

    @diagnostic()
    def get_gated_carry(self, instrument_code: str) -> pd.Series:
        """
        Diagnostic: Get carry forecast after trend gating.

        Returns:
            Carry forecast (percentile-ranked and gated by trend)
        """
        use_gated_carry = self.parent.config.get_element_or_default('use_gated_carry', False)

        if not use_gated_carry:
            return pd.Series(0.0, index=self.get_combined_forecast(instrument_code).index)

        weighted_forecasts = self.get_weighted_forecasts_without_multiplier(
            instrument_code
        )
        config = self.parent.config
        carry_rules = config.get_element_or_default('carry_rule_list', [])
        trend_gate_threshold = config.get_element_or_default('carry_trend_gate_threshold', 1.0)

        carry_cols = [c for c in weighted_forecasts.columns if c in carry_rules]
        if not carry_cols:
            return pd.Series(0.0, index=weighted_forecasts.index)

        carry_raw = weighted_forecasts[carry_cols].sum(axis=1)
        carry_ranked = self._apply_percentile_ranking_to_carry(carry_raw, instrument_code)
        trend_strength = self.get_trend_strength(instrument_code)

        carry_gated = carry_ranked.copy()
        weak_trend_mask = abs(trend_strength) < trend_gate_threshold
        sign_mismatch_mask = np.sign(trend_strength) != np.sign(carry_ranked)
        gate_mask = weak_trend_mask | sign_mismatch_mask
        carry_gated[gate_mask] = 0.0

        return carry_gated

    @diagnostic()
    def get_raw_carry(self, instrument_code: str) -> pd.Series:
        """
        Diagnostic: Get raw carry score before percentile ranking.

        Returns:
            Sum of weighted carry rule forecasts (before ranking)
        """
        weighted_forecasts = self.get_weighted_forecasts_without_multiplier(
            instrument_code
        )
        config = self.parent.config
        carry_rules = config.get_element_or_default('carry_rule_list', [])
        carry_cols = [c for c in weighted_forecasts.columns if c in carry_rules]

        if not carry_cols:
            return pd.Series(0.0, index=weighted_forecasts.index)

        return weighted_forecasts[carry_cols].sum(axis=1)

    @diagnostic()
    def get_ranked_carry(self, instrument_code: str) -> pd.Series:
        """
        Diagnostic: Get carry after percentile ranking but before gating.

        Returns:
            Percentile-ranked carry ∈ [-20, +20] (before gating)
        """
        use_gated_carry = self.parent.config.get_element_or_default('use_gated_carry', False)

        if not use_gated_carry:
            return pd.Series(0.0, index=self.get_combined_forecast(instrument_code).index)

        weighted_forecasts = self.get_weighted_forecasts_without_multiplier(
            instrument_code
        )
        config = self.parent.config
        carry_rules = config.get_element_or_default('carry_rule_list', [])

        carry_cols = [c for c in weighted_forecasts.columns if c in carry_rules]
        if not carry_cols:
            return pd.Series(0.0, index=weighted_forecasts.index)

        carry_raw = weighted_forecasts[carry_cols].sum(axis=1)
        return self._apply_percentile_ranking_to_carry(carry_raw, instrument_code)
