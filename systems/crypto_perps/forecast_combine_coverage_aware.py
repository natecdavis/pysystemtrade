"""
Coverage-aware ForecastCombineGated stage.

Wraps the parent FDM with an explicit coverage-proportional scaling:

    FDM_eff(t) = FDM_base(t) × (n_active_rules(t) / n_total_rules) ** alpha

Where n_active_rules is the count of rules with non-NaN forecasts at date t,
and n_total_rules is the static count of rules in the trading_rule_list.

Rationale: the parent FDM is computed from a rolling correlation matrix over
all rules in the trading_rule_list regardless of how many actually fire that
day. Young instruments with most rules NaN therefore receive an FDM that
treats the rule panel as fully populated. This wrapper adds an explicit,
config-controlled dampening on top of whatever implicit coverage effect the
correlation calculation already captures.

Config keys (both opt-in; default behavior matches ForecastCombineGated):
    use_coverage_aware_fdm: true
    fdm_coverage_alpha: 0.5    # 0 = no dampening (smoke test); 1 = linear

Usage in scripts/run_dynamic_universe_backtest.py: select this class via
the `use_coverage_aware_fdm` config knob.
"""

import pandas as pd

from systems.crypto_perps.forecast_combine_gated import ForecastCombineGated
from systems.system_cache import dont_cache


class ForecastCombineCoverageAware(ForecastCombineGated):
    """ForecastCombineGated subclass that scales FDM by rule-coverage^alpha."""

    @dont_cache
    def get_forecast_diversification_multiplier(
        self, instrument_code: str
    ) -> pd.Series:
        base_fdm = super().get_forecast_diversification_multiplier(instrument_code)

        alpha = float(
            self.config.get_element_or_default("fdm_coverage_alpha", 0.0)
        )
        if alpha == 0.0:
            return base_fdm

        rule_list = self.get_trading_rule_list(instrument_code)
        forecasts = self.get_all_forecasts(instrument_code, rule_list)
        n_total = len(forecasts.columns)
        if n_total == 0:
            return base_fdm

        n_active = forecasts.notna().sum(axis=1)
        coverage = (n_active / n_total).clip(lower=1e-6, upper=1.0)
        coverage_aligned = coverage.reindex(base_fdm.index, method="ffill").fillna(0.0)
        scale = coverage_aligned.pow(alpha)

        return base_fdm * scale
