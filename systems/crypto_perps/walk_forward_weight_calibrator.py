"""
Walk-forward forecast weight calibration and rule inclusion gating for crypto perpetuals.

Computes principled forecast weights using only past data at each quarterly
rebalancing date, avoiding look-ahead bias from weight grid-searches.

Weighting schemes (proportional allocation among all active rules):
  flat        — equal weight across all active rules (null model)
  ic_weighted — weight ∝ max(pooled rolling IC@ic_horizon, 0)
  gross_sr    — weight ∝ max(rolling gross SR shrunk toward equal, 0)
  risk_parity — family budgets ∝ 1/√var(family-avg-forecast); equal within families
  equal_family — equal budget across families, equal within families

Inclusion gate schemes (binary select then flat 1/N among selected):
  sr_gate         — include if rolling/expanding gross SR > 0
  ic_gate         — include if rolling/expanding pooled IC@ic_horizon > 0
  ic_tstat_gate   — include if per-instrument IC t-stat > tstat_threshold

No pysystemtrade dependencies — can be unit-tested in isolation.
"""

from __future__ import annotations

import warnings
from typing import Optional

import numpy as np
import pandas as pd
from scipy.cluster.hierarchy import linkage, leaves_list
from scipy.spatial.distance import squareform
from scipy.stats import pearsonr


class WalkForwardWeightCalibrator:
    """
    Computes quarterly walk-forward forecast weight schedules from pre-extracted
    forecast panels. No look-ahead bias: each rebalance date uses only data from
    [date - lookback_days, date).

    Parameters
    ----------
    lookback_days : int
        Calendar days of history to use for each estimate (504 ≈ 2 years of crypto
        data, which trades 365 days/year without weekends off).
    rebalance_freq : str
        Pandas offset string for rebalancing frequency (default 'QS' = quarter-start).
    ic_horizon : int
        Forward return horizon in days for IC computation.
    shrinkage : float
        Shrinkage coefficient toward equal weight for gross_sr scheme
        (0 = pure data-driven, 1 = equal weights).
    """

    FAMILY_MAP: dict[str, str] = {
        # Trend families
        "ewmac_4": "ewmac",      "ewmac_8": "ewmac",     "ewmac_16": "ewmac",
        "ewmac_32": "ewmac",     "ewmac_64": "ewmac",
        "breakout_10": "breakout", "breakout_20": "breakout",
        "breakout_40": "breakout", "breakout_80": "breakout",
        "breakout_160": "breakout", "breakout_320": "breakout",
        "normmom_4": "normmom",  "normmom_8": "normmom",
        "normmom_16": "normmom", "normmom_32": "normmom",  "normmom_64": "normmom",
        "accel_16": "accel",     "accel_32": "accel",     "accel_64": "accel",
        "assettrend_8": "assettrend",  "assettrend_16": "assettrend",
        "assettrend_32": "assettrend", "assettrend_64": "assettrend",
        "relmomentum_10": "relmomentum", "relmomentum_20": "relmomentum",
        "relmomentum_40": "relmomentum", "relmomentum_80": "relmomentum",
        "residual_momentum_16": "resmom",
        "residual_momentum_32": "resmom",
        "residual_momentum_64": "resmom",
        "round_number_break_10": "round_number",
        "round_number_break_20": "round_number",
        "round_number_break_40": "round_number",
        # Carry families
        "gated_carry_10": "gated_carry",
        "gated_carry_30": "gated_carry",
        "gated_carry_60": "gated_carry",
        "gated_carry_90": "gated_carry",
        "gated_carry_180": "gated_carry",
        "demeaned_carry_10": "demeaned_carry",
        "demeaned_carry_30": "demeaned_carry",
        "demeaned_carry_60": "demeaned_carry",
        "funding_mr": "funding_mr",
        "funding_crowd_5": "funding_crowd",
        "funding_crowd_10": "funding_crowd",
        # XS families
        "xs_carry": "xs",
        "xs_activity": "xs",
        "xs_val": "xs",
        "inter_sector": "xs",
        # Skew families
        "skew_abs_90": "skew",   "skew_abs_180": "skew",  "skew_abs_365": "skew",
        "skew_rv_90": "skew",    "skew_rv_180": "skew",   "skew_rv_365": "skew",
    }

    VALID_SCHEMES = frozenset({
        "flat", "ic_weighted", "gross_sr", "risk_parity", "equal_family",
        "sr_gate", "ic_gate", "ic_tstat_gate", "bayes_hrp",
    })

    def __init__(
        self,
        lookback_days: int = 504,
        rebalance_freq: str = "QS",
        ic_horizon: int = 5,
        shrinkage: float = 0.5,
        expanding: bool = False,
        tstat_threshold: float = 1.0,
    ) -> None:
        self.lookback_days = lookback_days
        self.rebalance_freq = rebalance_freq
        self.ic_horizon = ic_horizon
        self.shrinkage = shrinkage
        self.expanding = expanding
        self.tstat_threshold = tstat_threshold

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def compute_schedule(
        self,
        forecast_df: pd.DataFrame,
        return_df: pd.DataFrame,
        scheme: str,
        active_rules: Optional[list] = None,
    ) -> pd.DataFrame:
        """
        Compute walk-forward weight schedule.

        Parameters
        ----------
        forecast_df : DataFrame
            MultiIndex columns (rule, instrument), date index.
            Values are capped forecasts on ±20 scale.
        return_df : DataFrame
            Instrument columns, date index. Daily log-returns.
        scheme : str
            One of 'flat', 'ic_weighted', 'gross_sr', 'risk_parity'.
        active_rules : list, optional
            Rules to include. If None, uses all rules present in forecast_df.

        Returns
        -------
        DataFrame
            Index: quarterly rebalance dates (QS-aligned, starting after first lookback).
            Columns: active rule names.
            Values: weights; each row sums to 1.0, all values ≥ 0.
        """
        if scheme not in self.VALID_SCHEMES:
            raise ValueError(
                f"Unknown scheme {scheme!r}. "
                f"Choose from: {', '.join(sorted(self.VALID_SCHEMES))}"
            )

        available_rules = set(forecast_df.columns.get_level_values("rule"))
        if active_rules is None:
            active_rules = sorted(available_rules)
        else:
            active_rules = [r for r in active_rules if r in available_rules]

        if not active_rules:
            raise ValueError("No active rules found in forecast_df columns")

        lookback = pd.Timedelta(days=self.lookback_days)
        first_possible = forecast_df.index.min() + lookback
        last_date = forecast_df.index.max()

        if first_possible > last_date:
            raise ValueError(
                f"Dataset spans {(last_date - forecast_df.index.min()).days} calendar days "
                f"but lookback_days={self.lookback_days} requires more history."
            )

        rebalance_dates = pd.date_range(first_possible, last_date, freq=self.rebalance_freq)
        if len(rebalance_dates) == 0:
            raise ValueError("No rebalance dates generated — dataset may be too short.")

        data_start = forecast_df.index.min()

        rows = []
        for rebalance_date in rebalance_dates:
            # equal_family is purely structural — no historical data needed
            if scheme == "equal_family":
                weights = self._equal_family_weights(active_rules)
                rows.append(pd.Series(weights, name=rebalance_date))
                continue

            # Window: expanding uses all data from the start; rolling uses fixed lookback
            if self.expanding:
                lookback_start = data_start
            else:
                lookback_start = rebalance_date - lookback

            # Strict past-only slice: [lookback_start, rebalance_date)
            end_excl = rebalance_date - pd.Timedelta(days=1)
            fc_w = forecast_df.loc[lookback_start:end_excl]
            ret_w = return_df.loc[lookback_start:end_excl]

            if len(fc_w) < 20:
                weights = self._flat_weights(active_rules)
            elif scheme == "flat":
                weights = self._flat_weights(active_rules)
            elif scheme == "ic_weighted":
                weights = self._ic_weighted(fc_w, ret_w, active_rules)
            elif scheme == "gross_sr":
                weights = self._gross_sr_weighted(fc_w, ret_w, active_rules)
            elif scheme == "risk_parity":
                weights = self._risk_parity(fc_w, active_rules)
            elif scheme == "sr_gate":
                weights = self._sr_gate(fc_w, ret_w, active_rules)
            elif scheme == "ic_gate":
                weights = self._ic_gate(fc_w, ret_w, active_rules)
            elif scheme == "ic_tstat_gate":
                weights = self._ic_tstat_gate(fc_w, ret_w, active_rules)
            else:  # bayes_hrp
                weights = self._bayesian_hrp(fc_w, ret_w, active_rules)

            rows.append(pd.Series(weights, name=rebalance_date))

        schedule = pd.DataFrame(rows)

        # Ensure all active rules are columns (fill missing with 0)
        for r in active_rules:
            if r not in schedule.columns:
                schedule[r] = 0.0
        schedule = schedule[active_rules].fillna(0.0)

        # Normalize rows to sum exactly to 1.0
        row_sums = schedule.sum(axis=1).replace(0, np.nan)
        schedule = schedule.div(row_sums, axis=0).fillna(0.0)

        return schedule

    # ------------------------------------------------------------------
    # Scheme implementations
    # ------------------------------------------------------------------

    def _flat_weights(self, rules: list) -> dict:
        """1/N equal weight for every active rule."""
        n = len(rules)
        return {r: 1.0 / n for r in rules}

    def _equal_family_weights(self, rules: list) -> dict:
        """
        Equal budget across families, equal weight within each family.

        Each family (as defined by FAMILY_MAP) receives 1/num_families of the
        total budget, then splits that equally among its rules. Rules whose family
        is not in FAMILY_MAP are treated as their own singleton family.

        Unlike flat 1/N, this gives the same total exposure to a 2-rule family
        (relmomentum) as to a 6-rule family (skew), rather than penalising small
        families by rule count.
        """
        families: dict[str, list] = {}
        for rule in rules:
            fam = self.FAMILY_MAP.get(rule, rule)
            families.setdefault(fam, []).append(rule)

        n_families = len(families)
        weights: dict = {}
        for fam, fam_rules in families.items():
            budget = 1.0 / n_families
            for rule in fam_rules:
                weights[rule] = budget / len(fam_rules)

        return weights

    def _ic_weighted(
        self,
        fc_window: pd.DataFrame,
        ret_window: pd.DataFrame,
        rules: list,
    ) -> dict:
        """
        Weight by pooled IC at ic_horizon.

        IC is computed by pooling all (date, instrument) pairs in the lookback window
        and computing Pearson corr(forecast, vol-normalized fwd cumulative return).
        Vol-normalization prevents high-vol instruments from dominating the IC estimate.

        Rules with non-positive IC get weight 0. If all ICs ≤ 0, returns flat weights.
        """
        raw: dict = {}
        for rule in rules:
            fc_stacked, fwd_stacked = self._build_pandl_pairs(
                fc_window, ret_window, rule, self.ic_horizon
            )
            n = len(fc_stacked)
            if n < 60:
                raw[rule] = 0.0
                continue
            if float(fc_stacked.std()) < 1e-6:
                raw[rule] = 0.0
                continue
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    ic, _ = pearsonr(fc_stacked.to_numpy(), fwd_stacked.to_numpy())
                raw[rule] = max(float(ic), 0.0)
            except Exception:
                raw[rule] = 0.0

        return self._normalize_weights(raw, rules)

    def _gross_sr_weighted(
        self,
        fc_window: pd.DataFrame,
        ret_window: pd.DataFrame,
        rules: list,
    ) -> dict:
        """
        Weight by rolling gross Sharpe ratio, shrunk toward equal weight.

        Daily P&L proxy: (forecast / 20) × vol_normalized_next_day_return,
        pooled across all instruments.

        Shrinkage: SR_shrunk = (1 - α) × SR_raw + α × mean(all SRs)
        where α = self.shrinkage.

        Rules with shrunk SR ≤ 0 get weight 0. Falls back to flat if all ≤ 0.
        """
        # Precompute vol-normalized 1-day forward returns (shared across rules)
        vol = ret_window.rolling(63, min_periods=21).std()
        vol_norm_ret = ret_window.div(vol.replace(0, np.nan))
        fwd_ret_1d = vol_norm_ret.shift(-1)

        srs: dict = {}
        available_rules = set(fc_window.columns.get_level_values("rule"))
        for rule in rules:
            if rule not in available_rules:
                srs[rule] = np.nan
                continue

            rule_fc = fc_window[rule] / 20.0  # normalize forecast to [-1, 1]
            common_inst = rule_fc.columns.intersection(fwd_ret_1d.columns)
            if common_inst.empty:
                srs[rule] = np.nan
                continue

            pandl = (rule_fc[common_inst] * fwd_ret_1d[common_inst]).stack().dropna()
            if len(pandl) < 60:
                srs[rule] = np.nan
                continue

            daily_std = float(pandl.std())
            if daily_std < 1e-8:
                srs[rule] = np.nan
                continue

            srs[rule] = float(pandl.mean() / daily_std * np.sqrt(252))

        # Shrink toward mean of finite values
        finite_vals = [v for v in srs.values() if np.isfinite(v)]
        if not finite_vals:
            return self._flat_weights(rules)

        mean_sr = float(np.mean(finite_vals))
        shrunk: dict = {}
        for r in rules:
            sr_raw = srs.get(r, np.nan)
            sr_base = sr_raw if np.isfinite(sr_raw) else mean_sr
            shrunk[r] = (1.0 - self.shrinkage) * sr_base + self.shrinkage * mean_sr

        raw = {r: max(v, 0.0) for r, v in shrunk.items()}
        return self._normalize_weights(raw, rules)

    def _risk_parity(self, fc_window: pd.DataFrame, rules: list) -> dict:
        """
        Family budgets ∝ 1/√var(family-avg-forecast); equal weight within families.

        Family-average forecast variance captures how concentrated/active each family's
        signal is across instruments and time. Families with more persistent signals
        (e.g. carry) have higher variance and are relatively downweighted; families
        with noisier, mean-reverting signals (e.g. skew) get more budget.

        This is distinct from portfolio risk parity — it's parity over the forecast
        signal, not over P&L or position variance.
        """
        # Group active rules by family
        families: dict[str, list] = {}
        for rule in rules:
            fam = self.FAMILY_MAP.get(rule, rule)
            families.setdefault(fam, []).append(rule)

        available_rules = set(fc_window.columns.get_level_values("rule"))

        family_vars: dict[str, float] = {}
        for fam, fam_rules in families.items():
            fam_rules_avail = [r for r in fam_rules if r in available_rules]
            if not fam_rules_avail:
                family_vars[fam] = 1e-8
                continue

            # Stack each rule's forecasts into a Series[(date, instrument) → value]
            series_list = [
                fc_window[r].stack().dropna() for r in fam_rules_avail
            ]
            # Family-average forecast across rules at each (date, instrument)
            family_df = pd.concat(series_list, axis=1)
            family_avg = family_df.mean(axis=1)
            var = float(family_avg.var())
            family_vars[fam] = max(var, 1e-8)

        # Budget ∝ 1/√var (lower variance → more budget)
        budgets = {fam: 1.0 / np.sqrt(v) for fam, v in family_vars.items()}
        total_budget = sum(budgets.values())
        if total_budget < 1e-8:
            return self._flat_weights(rules)
        budgets_norm = {fam: b / total_budget for fam, b in budgets.items()}

        # Equal split within each family
        raw: dict = {}
        for rule in rules:
            fam = self.FAMILY_MAP.get(rule, rule)
            fam_active = [r for r in families.get(fam, [rule]) if r in rules]
            raw[rule] = budgets_norm.get(fam, 0.0) / max(len(fam_active), 1)

        return self._normalize_weights(raw, rules)

    # ------------------------------------------------------------------
    # Inclusion gate schemes (binary select → flat 1/N among included)
    # ------------------------------------------------------------------

    def _sr_gate(
        self,
        fc_window: pd.DataFrame,
        ret_window: pd.DataFrame,
        rules: list,
    ) -> dict:
        """
        Include rule if gross SR > 0 over the lookback window; 1/N among included.

        Same P&L proxy as _gross_sr_weighted() but no shrinkage and binary in/out
        rather than proportional. Rules with insufficient data (<60 pairs) are
        included by default (benefit of the doubt) so early expanding windows
        don't silently drop rules that just lack history.
        """
        vol = ret_window.rolling(63, min_periods=21).std()
        vol_norm_ret = ret_window.div(vol.replace(0, np.nan))
        fwd_ret_1d = vol_norm_ret.shift(-1)

        available_rules = set(fc_window.columns.get_level_values("rule"))
        included = []
        for rule in rules:
            if rule not in available_rules:
                continue  # no forecast data at all — exclude

            rule_fc = fc_window[rule] / 20.0
            common_inst = rule_fc.columns.intersection(fwd_ret_1d.columns)
            if common_inst.empty:
                continue

            pandl = (rule_fc[common_inst] * fwd_ret_1d[common_inst]).stack().dropna()
            if len(pandl) < 60:
                included.append(rule)  # too little data — include by default
                continue

            daily_std = float(pandl.std())
            if daily_std < 1e-8:
                included.append(rule)  # flat signal — include by default
                continue

            sr = float(pandl.mean() / daily_std * np.sqrt(252))
            if sr > 0:
                included.append(rule)

        if not included:
            included = list(rules)  # fallback: include all

        n = len(included)
        result = {r: 0.0 for r in rules}
        result.update({r: 1.0 / n for r in included})
        return result

    def _ic_gate(
        self,
        fc_window: pd.DataFrame,
        ret_window: pd.DataFrame,
        rules: list,
    ) -> dict:
        """
        Include rule if pooled IC@ic_horizon > 0 over the lookback window; 1/N among included.

        Uses the same pooled (date, instrument) pairs as _ic_weighted() but applies a
        binary gate instead of proportional weighting. Rules with insufficient data
        (<60 pairs) or near-constant forecasts are included by default.
        """
        included = []
        for rule in rules:
            fc_stacked, fwd_stacked = self._build_pandl_pairs(
                fc_window, ret_window, rule, self.ic_horizon
            )
            n = len(fc_stacked)
            if n < 60:
                included.append(rule)  # too little data — include by default
                continue
            if float(fc_stacked.std()) < 1e-6:
                included.append(rule)  # near-constant forecast — include by default
                continue
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    ic, _ = pearsonr(fc_stacked.to_numpy(), fwd_stacked.to_numpy())
                if float(ic) > 0:
                    included.append(rule)
            except Exception:
                included.append(rule)  # error — include by default

        if not included:
            included = list(rules)  # fallback: include all

        n = len(included)
        result = {r: 0.0 for r in rules}
        result.update({r: 1.0 / n for r in included})
        return result

    def _ic_tstat_gate(
        self,
        fc_window: pd.DataFrame,
        ret_window: pd.DataFrame,
        rules: list,
    ) -> dict:
        """
        Include rule if per-instrument IC t-stat > tstat_threshold; 1/N among included.

        Computes IC separately for each instrument (time-series correlation of
        forecast vs vol-normalized forward return), then tests:
            t = mean(per_instrument_ICs) / (std(per_instrument_ICs) / sqrt(n_instruments))

        Using per-instrument ICs (not raw pooled pair count) avoids the inflated
        effective-N problem: pooled n_pairs ≈ 319K makes IC=0.002 yield t≈35.
        Here n_instruments ≈ 42–319 gives a meaningful cross-sectional test.

        Rules with fewer than 5 valid instruments in the window are included by
        default (too little cross-sectional evidence to gate on).
        """
        included = []
        for rule in rules:
            ics = self._per_instrument_ics(fc_window, ret_window, rule)
            if len(ics) < 5:
                included.append(rule)  # too few instruments — include by default
                continue

            mean_ic = float(np.mean(ics))
            std_ic = float(np.std(ics, ddof=1))
            n = len(ics)
            if std_ic < 1e-8:
                t_stat = 0.0
            else:
                t_stat = mean_ic / (std_ic / np.sqrt(n))

            if t_stat > self.tstat_threshold:
                included.append(rule)

        if not included:
            included = list(rules)  # fallback: include all

        n = len(included)
        result = {r: 0.0 for r in rules}
        result.update({r: 1.0 / n for r in included})
        return result

    # ------------------------------------------------------------------
    # Bayesian HRP scheme
    # ------------------------------------------------------------------

    def _bayesian_hrp(
        self,
        fc_window: pd.DataFrame,
        ret_window: pd.DataFrame,
        rules: list,
    ) -> dict:
        """
        Bayesian SR shrinkage + HRP on forecast correlation matrix.

        Algorithm:
          1. Raw gross SR per rule (same P&L proxy as _gross_sr_weighted, no shrinkage)
          2. Bayesian posterior SR: normal-normal conjugate, shrinks each rule's raw SR
             toward the pool mean, weighting by data-driven estimation variance
             σ²_obs = (1 + SR²/2) / T_days × 252
          3. Forecast correlation matrix: instrument-averaged rule forecasts, linear
             shrinkage β=0.1 toward identity
          4. HRP weights: Ward linkage → quasi-diagonalization → recursive bisection
             by inverse equal-weight cluster variance
          5. SR tilt: w_final ∝ w_hrp × (SR_posterior − min_posterior + ε)

        Falls back to flat weights on any error.
        """
        try:
            return self._bayesian_hrp_impl(fc_window, ret_window, rules)
        except Exception:
            return self._flat_weights(rules)

    def _bayesian_hrp_impl(
        self,
        fc_window: pd.DataFrame,
        ret_window: pd.DataFrame,
        rules: list,
    ) -> dict:
        T_days = max(len(ret_window), 1)

        # Step 1: Raw gross SR per rule (same P&L proxy as _gross_sr_weighted)
        vol = ret_window.rolling(63, min_periods=21).std()
        vol_norm_ret = ret_window.div(vol.replace(0, np.nan))
        fwd_ret_1d = vol_norm_ret.shift(-1)

        available_rules = set(fc_window.columns.get_level_values("rule"))
        srs_raw: dict = {}
        for rule in rules:
            if rule not in available_rules:
                srs_raw[rule] = np.nan
                continue
            rule_fc = fc_window[rule] / 20.0
            common_inst = rule_fc.columns.intersection(fwd_ret_1d.columns)
            if common_inst.empty:
                srs_raw[rule] = np.nan
                continue
            pandl = (rule_fc[common_inst] * fwd_ret_1d[common_inst]).stack().dropna()
            if len(pandl) < 60:
                srs_raw[rule] = np.nan
                continue
            daily_std = float(pandl.std())
            if daily_std < 1e-8:
                srs_raw[rule] = np.nan
                continue
            srs_raw[rule] = float(pandl.mean() / daily_std * np.sqrt(252))

        # Step 2: Bayesian SR shrinkage (normal-normal conjugate)
        finite_srs = [v for v in srs_raw.values() if np.isfinite(v)]
        if not finite_srs:
            return self._flat_weights(rules)

        mu_pool = float(np.mean(finite_srs))
        sigma2_prior = float(np.var(finite_srs)) + 1e-6  # cross-sectional SR variance

        srs_posterior: dict = {}
        for rule in rules:
            sr_raw = srs_raw.get(rule, np.nan)
            if not np.isfinite(sr_raw):
                srs_posterior[rule] = mu_pool  # no evidence → pure prior
                continue
            # Asymptotic SR estimation variance
            sigma2_obs = max((1.0 + sr_raw ** 2 / 2.0) / T_days * 252.0, 1e-8)
            prec_obs = 1.0 / sigma2_obs
            prec_prior = 1.0 / sigma2_prior
            srs_posterior[rule] = (
                (sr_raw * prec_obs + mu_pool * prec_prior) / (prec_obs + prec_prior)
            )

        # Step 3: Forecast correlation matrix
        corr_matrix = self._compute_forecast_correlations(fc_window, rules)
        if corr_matrix is None:
            return self._flat_weights(rules)

        # Step 4: HRP weights
        hrp_weights = self._hrp_weights_from_corr(corr_matrix, rules)

        # Step 5: SR tilt — shift all posteriors to positive, multiply by HRP weight
        posterior_vals = np.array([srs_posterior[r] for r in rules])
        sr_shifted = posterior_vals - float(np.min(posterior_vals)) + 1e-6
        hrp_arr = np.array([hrp_weights.get(r, 0.0) for r in rules])
        tilted = hrp_arr * sr_shifted
        total = float(np.sum(tilted))
        if total < 1e-8:
            return self._flat_weights(rules)

        return {r: float(w / total) for r, w in zip(rules, tilted)}

    def _compute_forecast_correlations(
        self,
        fc_window: pd.DataFrame,
        rules: list,
    ) -> Optional[np.ndarray]:
        """
        Build (n_rules × n_rules) forecast correlation matrix.

        Per-rule: average forecasts across instruments at each date → daily Series.
        Correlate pairwise across rules. Linear shrinkage β=0.1 toward identity.
        Rules with no valid data are treated as uncorrelated (off-diagonal = 0).

        Returns None if fewer than 2 rules have valid forecast series.
        """
        available_rules = set(fc_window.columns.get_level_values("rule"))
        rule_series: dict = {}
        for rule in rules:
            if rule not in available_rules:
                continue
            avg_fc = fc_window[rule].mean(axis=1, skipna=True).dropna()
            if len(avg_fc) < 20:
                continue
            rule_series[rule] = avg_fc

        valid_rules = [r for r in rules if r in rule_series]
        if len(valid_rules) < 2:
            return None

        # Pairwise correlations among valid rules
        fc_df = pd.DataFrame({r: rule_series[r] for r in valid_rules})
        corr_valid = np.clip(fc_df.corr().values, -1.0, 1.0)
        np.fill_diagonal(corr_valid, 1.0)

        # Linear shrinkage toward identity (β=0.1)
        beta = 0.1
        corr_shrunk = (1.0 - beta) * corr_valid + beta * np.eye(len(valid_rules))

        # Expand to full n_rules × n_rules, padding missing with identity (uncorrelated)
        n = len(rules)
        if len(valid_rules) == n:
            return corr_shrunk

        full_corr = np.eye(n)
        valid_idx = [i for i, r in enumerate(rules) if r in rule_series]
        for ii, gi in enumerate(valid_idx):
            for jj, gj in enumerate(valid_idx):
                full_corr[gi, gj] = corr_shrunk[ii, jj]

        return full_corr

    def _hrp_weights_from_corr(
        self,
        corr_matrix: np.ndarray,
        rules: list,
    ) -> dict:
        """
        HRP weights from correlation matrix (López de Prado 2016).

        Distance d_ij = √((1 − ρ_ij) / 2), Ward linkage, quasi-diagonalization via
        leaves_list(), then recursive bisection allocating ∝ inverse equal-weight
        cluster variance.

        Returns {rule: weight} summing to 1.0.
        """
        n = len(rules)
        if n == 1:
            return {rules[0]: 1.0}

        # Distance matrix and Ward clustering
        dist = np.sqrt(np.clip((1.0 - corr_matrix) / 2.0, 0.0, 1.0))
        np.fill_diagonal(dist, 0.0)
        condensed = squareform(dist, checks=False)
        link = linkage(condensed, method="ward")

        # Quasi-diagonalization: permuted order of leaf indices
        sorted_idx = list(leaves_list(link))

        def cluster_var(idx_list: list) -> float:
            sub = corr_matrix[np.ix_(idx_list, idx_list)]
            w = np.ones(len(idx_list)) / len(idx_list)
            return max(float(w @ sub @ w), 1e-8)

        # Recursive bisection
        weights = np.ones(n)
        clusters = [sorted_idx]
        while clusters:
            next_clusters = []
            for cluster in clusters:
                if len(cluster) <= 1:
                    continue
                mid = len(cluster) // 2
                left = cluster[:mid]
                right = cluster[mid:]

                var_left = cluster_var(left)
                var_right = cluster_var(right)
                alpha = 1.0 - var_left / (var_left + var_right)

                weights[left] *= alpha
                weights[right] *= (1.0 - alpha)
                next_clusters.extend([left, right])
            clusters = next_clusters

        weights = np.maximum(weights, 0.0)
        total = float(np.sum(weights))
        if total < 1e-8:
            return {r: 1.0 / n for r in rules}

        return {r: float(weights[i] / total) for i, r in enumerate(rules)}

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _per_instrument_ics(
        self,
        fc_window: pd.DataFrame,
        ret_window: pd.DataFrame,
        rule: str,
    ) -> list:
        """
        Compute per-instrument time-series IC for a rule over the lookback window.

        Returns a list of IC values (one per instrument with sufficient data).
        Instruments with fewer than 30 valid (forecast, fwd_return) pairs are skipped.
        """
        available_rules = set(fc_window.columns.get_level_values("rule"))
        if rule not in available_rules:
            return []

        rule_fc = fc_window[rule]  # date × instrument
        vol = ret_window.rolling(63, min_periods=21).std()
        vol_norm_ret = ret_window.div(vol.replace(0, np.nan))
        fwd_ret = vol_norm_ret.rolling(self.ic_horizon).sum().shift(-self.ic_horizon)

        common_inst = rule_fc.columns.intersection(fwd_ret.columns)
        ics = []
        for inst in common_inst:
            fc_s = rule_fc[inst].dropna()
            fwd_s = fwd_ret[inst].dropna()
            common_idx = fc_s.index.intersection(fwd_s.index)
            fc_v = fc_s.loc[common_idx]
            fwd_v = fwd_s.loc[common_idx]
            valid = fc_v.notna() & fwd_v.notna()
            fc_v = fc_v[valid]
            fwd_v = fwd_v[valid]
            if len(fc_v) < 30 or float(fc_v.std()) < 1e-6:
                continue
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    ic, _ = pearsonr(fc_v.to_numpy(), fwd_v.to_numpy())
                if np.isfinite(ic):
                    ics.append(float(ic))
            except Exception:
                pass

        return ics

    def _build_pandl_pairs(
        self,
        fc_window: pd.DataFrame,
        ret_window: pd.DataFrame,
        rule: str,
        horizon: int,
    ) -> tuple[pd.Series, pd.Series]:
        """
        Build pooled (forecast, fwd_vol_norm_return) pairs for one rule.

        Vol-normalizes forward returns by rolling 63-day std so that high-volatility
        instruments don't dominate the IC estimate.

        Returns (fc_stacked, fwd_stacked) — flat Series with matching index,
        all NaN pairs dropped. Both may be empty if the rule has no data in window.
        """
        available_rules = set(fc_window.columns.get_level_values("rule"))
        if rule not in available_rules:
            return pd.Series(dtype=float), pd.Series(dtype=float)

        rule_fc = fc_window[rule]  # DataFrame[date × instrument]
        vol = ret_window.rolling(63, min_periods=21).std()
        vol_norm_ret = ret_window.div(vol.replace(0, np.nan))

        # Cumulative forward return over `horizon` days
        fwd_ret = vol_norm_ret.rolling(horizon).sum().shift(-horizon)

        common_inst = rule_fc.columns.intersection(fwd_ret.columns)
        if common_inst.empty:
            return pd.Series(dtype=float), pd.Series(dtype=float)

        fc_stacked = rule_fc[common_inst].stack().dropna()
        fwd_stacked = fwd_ret[common_inst].stack().dropna()

        common_idx = fc_stacked.index.intersection(fwd_stacked.index)
        fc_aligned = fc_stacked.loc[common_idx]
        fwd_aligned = fwd_stacked.loc[common_idx]

        # Drop pairs where either value is NaN
        valid = fc_aligned.notna() & fwd_aligned.notna()
        return fc_aligned[valid], fwd_aligned[valid]

    def _normalize_weights(self, raw: dict, fallback_rules: list) -> dict:
        """
        Normalize positive raw weights to sum to 1.0.

        If all values are non-positive (e.g., all ICs ≤ 0), falls back to flat
        weights across fallback_rules rather than returning all-zero weights that
        would kill the combined forecast.
        """
        positive = {r: v for r, v in raw.items() if v > 0 and r in fallback_rules}
        if not positive:
            n = len(fallback_rules)
            return {r: 1.0 / n for r in fallback_rules}

        total = sum(positive.values())
        result = {r: 0.0 for r in fallback_rules}
        result.update({r: v / total for r, v in positive.items()})
        return result
