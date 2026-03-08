"""
Carver static instrument selection via greedy correlation-aware optimization.

Implements the iterative greedy portfolio construction from Carver (Post 3):
- Computes net SR for each eligible instrument (assumed gross SR - cost SR)
- Greedily selects instruments to maximize equal-weight portfolio SR
  accounting for correlation structure
- Monthly rebalancing cadence with hysteresis to reduce churn

Usage:
    selector = CarverStaticInstrumentSelector(
        prices_df=prices_df,
        adv_df=adv_df,
        eligibility_df=eligibility_df,
        assumed_gross_sr=0.5,
        max_instruments=40,
    )
    selected_over_time = selector.get_tradable_over_time()
    daily_eligibility = selector.to_eligibility_df(
        selected_over_time, eligibility_df.index, list(eligibility_df.columns)
    )
"""

import pandas as pd
import numpy as np
from typing import Dict, List, Optional, Tuple

from syslogging.logger import get_logger


class CarverStaticInstrumentSelector:
    """
    Greedy correlation-aware instrument selection (Carver Post 3 method).

    At each month-end:
      1. Get eligible instruments from Stage 1 filter
      2. Compute net SR = assumed_gross_SR - annual_cost_SR for each
      3. Greedily add instruments that maximize equal-weight portfolio SR,
         stopping when portfolio SR drops below sr_tolerance × peak SR
      4. Apply hysteresis: limit changes to hysteresis_buffer instruments
         per direction (add/remove) vs previous month's selection

    Monthly selections are forward-filled to a daily boolean DataFrame
    for use by the portfolio stage.
    """

    def __init__(
        self,
        prices_df: pd.DataFrame,
        adv_df: Optional[pd.DataFrame],
        eligibility_df: pd.DataFrame,
        assumed_gross_sr: float = 0.5,
        sr_tolerance: float = 0.90,
        correlation_window: int = 252,
        correlation_shrinkage: float = 0.5,
        min_instruments: int = 5,
        max_instruments: int = 40,
        rebalance_freq: str = "ME",
        hysteresis_buffer: int = 3,
        stack_turnover: float = 15.0,
        fee_bps: float = 5.0,
        vol_window: int = 35,
        log=None,
    ):
        """
        Args:
            prices_df: Daily close prices (dates × instruments)
            adv_df: Average daily volume in USD (dates × instruments); used for
                    spread tier estimation. If None, uses conservative default spreads.
            eligibility_df: Stage 1 boolean eligibility DataFrame (dates × instruments)
            assumed_gross_sr: Assumed gross SR before costs (Carver: 0.5)
            sr_tolerance: Stop adding instruments when portfolio SR drops below
                          this fraction of the running peak (default 0.90 = 90%)
            correlation_window: Trailing days for correlation estimation (default 252)
            correlation_shrinkage: Shrinkage toward identity matrix, range [0,1].
                                   0 = raw correlations, 1 = identity (no correlation).
                                   Default 0.5 ensures PSD and reduces instability.
            min_instruments: Minimum instruments to select (floor)
            max_instruments: Maximum instruments to select (cap)
            rebalance_freq: Pandas frequency for rebalance dates (default 'ME' = month end)
            hysteresis_buffer: Max additions AND removals per rebalance (limits churn)
            stack_turnover: Expected annual round-trips for cost SR calculation
            fee_bps: One-way trading fee in basis points (default 5 bps = 0.05%)
            vol_window: Rolling window (days) for vol estimation
            log: Logger instance
        """
        self.prices_df = prices_df
        self.adv_df = adv_df
        self.eligibility_df = eligibility_df
        self.assumed_gross_sr = assumed_gross_sr
        self.sr_tolerance = sr_tolerance
        self.correlation_window = correlation_window
        self.correlation_shrinkage = correlation_shrinkage
        self.min_instruments = min_instruments
        self.max_instruments = max_instruments
        self.rebalance_freq = rebalance_freq
        self.hysteresis_buffer = hysteresis_buffer
        self.stack_turnover = stack_turnover
        self.fee_bps = fee_bps
        self.vol_window = vol_window
        self.log = log or get_logger("CarverStaticInstrumentSelector")

    # -------------------------------------------------------------------------
    # Public interface
    # -------------------------------------------------------------------------

    def get_tradable_over_time(self) -> Dict[pd.Timestamp, List[str]]:
        """
        Run greedy selection at each month-end.

        Returns:
            Dict mapping each rebalance date to the selected instrument list.
            Dates are snapped to the nearest actual trading day on or before
            the calendar month-end.
        """
        all_dates = self.eligibility_df.index

        # Generate rebalance dates snapped to actual trading days
        rebalance_dates = self._get_rebalance_dates(all_dates)

        self.log.info(
            f"Carver static selector: {len(rebalance_dates)} rebalance dates "
            f"({rebalance_dates[0].date()} → {rebalance_dates[-1].date()})"
        )

        selected_over_time: Dict[pd.Timestamp, List[str]] = {}
        prev_selected: List[str] = []

        for i, date in enumerate(rebalance_dates):
            selected = self._select_instruments_for_date(date, prev_selected)
            selected_over_time[date] = selected
            prev_selected = selected

            if i % 12 == 0 or i == len(rebalance_dates) - 1:
                self.log.info(
                    f"  {date.date()}: {len(selected)} instruments selected "
                    f"(prev: {len(prev_selected if i > 0 else [])})"
                )

        return selected_over_time

    def to_eligibility_df(
        self,
        selected_over_time: Dict[pd.Timestamp, List[str]],
        all_dates: pd.DatetimeIndex,
        all_instruments: List[str],
    ) -> pd.DataFrame:
        """
        Convert monthly selections to a daily boolean DataFrame via forward-fill.

        Args:
            selected_over_time: Output of get_tradable_over_time()
            all_dates: Full daily trading date index for the backtest
            all_instruments: All instrument codes (columns of the output)

        Returns:
            Boolean DataFrame (dates × instruments), True = selected for trading
        """
        # Build sparse monthly DataFrame
        monthly_dates = sorted(selected_over_time.keys())
        monthly_df = pd.DataFrame(False, index=monthly_dates, columns=all_instruments)

        for date, instruments in selected_over_time.items():
            for instr in instruments:
                if instr in monthly_df.columns:
                    monthly_df.loc[date, instr] = True

        # Reindex to all trading dates and forward-fill
        daily_df = monthly_df.reindex(all_dates).ffill().fillna(False)

        return daily_df

    # -------------------------------------------------------------------------
    # Selection logic
    # -------------------------------------------------------------------------

    def _select_instruments_for_date(
        self, date: pd.Timestamp, prev_selected: List[str]
    ) -> List[str]:
        """Run greedy selection for a single rebalance date."""
        # Get Stage 1 eligible instruments at this date
        if date not in self.eligibility_df.index:
            self.log.warning(f"{date.date()}: Date not in eligibility_df — keeping prev")
            return prev_selected

        eligible_row = self.eligibility_df.loc[date]
        eligible = [instr for instr in eligible_row.index if eligible_row[instr]]

        if len(eligible) == 0:
            self.log.warning(f"{date.date()}: No eligible instruments — keeping prev")
            return prev_selected

        # Compute net SR for all eligible instruments (vectorized cost lookup)
        spread_lookup = self._get_spread_lookup(date)
        vol_lookup = self._get_vol_lookup(date, eligible)
        net_srs = self._compute_net_sr_from_lookups(eligible, spread_lookup, vol_lookup)

        # Remove instruments with NaN net SR
        valid = [instr for instr in eligible if not np.isnan(net_srs.get(instr, np.nan))]

        if len(valid) == 0:
            self.log.warning(f"{date.date()}: No valid net SRs — keeping prev")
            return prev_selected

        # Compute shrunk correlation matrix
        corr_df = self._compute_shrunk_correlation(date, valid)

        # Greedy portfolio construction
        selected = self._greedy_selection(valid, net_srs, corr_df)

        # Apply hysteresis to limit churn
        selected = self._apply_hysteresis(prev_selected, selected, net_srs)

        self.log.debug(
            f"{date.date()}: {len(eligible)} eligible → {len(valid)} valid "
            f"→ {len(selected)} selected"
        )

        return selected

    def _greedy_selection(
        self,
        candidates: List[str],
        net_srs: Dict[str, float],
        corr_df: pd.DataFrame,
    ) -> List[str]:
        """
        Greedy portfolio construction maximizing equal-weight portfolio SR.

        Algorithm:
          1. Start with highest-net-SR instrument
          2. At each step, compute portfolio SR for every possible addition
             using vectorized numpy (batch over all remaining candidates)
          3. Add the best candidate if portfolio SR >= sr_tolerance × peak SR
          4. Stop when no candidate meets the tolerance, or max_instruments reached

        Portfolio SR formula (equal weights, unit vol):
            mean_SR = mean(net_srs[selected])
            port_var = (sum of all corr pairs in selected) / n^2
            port_SR  = mean_SR / sqrt(port_var)

        Incremental update for adding candidate c to set S of k instruments:
            new_mean = (sum_SR_S + sr_c) / (k+1)
            new_var  = (sum_corr_SS + 2*sum_corr_Sc + 1) / (k+1)^2

        The cross-correlation sums (sum_corr_Sc) are computed for all remaining
        candidates simultaneously via a single matrix slice → O(k*N) per step.

        Args:
            candidates: Eligible instruments with valid net SR (ordered list)
            net_srs: Dict of instrument → net SR
            corr_df: Shrunk correlation DataFrame indexed by candidates

        Returns:
            List of selected instrument codes
        """
        n = len(candidates)

        if n <= self.min_instruments:
            return list(candidates)

        # Arrays for fast computation
        net_sr_arr = np.array([net_srs.get(c, 0.0) for c in candidates])

        # Correlation matrix aligned with candidates
        corr_matrix = corr_df.reindex(index=candidates, columns=candidates).fillna(0.0).values
        np.fill_diagonal(corr_matrix, 1.0)

        # Sort candidates by net SR (descending) — greedy starts with best
        sorted_positions = np.argsort(-net_sr_arr)

        # Start with highest net SR instrument
        selected: List[int] = [int(sorted_positions[0])]
        remaining: List[int] = [int(i) for i in sorted_positions[1:]]

        peak_sr = float(net_sr_arr[selected[0]])

        while len(selected) < self.max_instruments and len(remaining) > 0:
            k = len(selected)
            sel_arr = np.array(selected, dtype=int)
            rem_arr = np.array(remaining, dtype=int)

            # Mean net SR for selected + each candidate
            sum_sel_sr = net_sr_arr[sel_arr].sum()
            mean_srs = (sum_sel_sr + net_sr_arr[rem_arr]) / (k + 1)

            # Sum of all correlations within current selected set (scalar)
            sum_corr_sel = corr_matrix[np.ix_(sel_arr, sel_arr)].sum()

            # Sum of cross-correlations: each remaining candidate vs all selected
            # cross_corr shape: (n_remaining, k) → sum over k → shape (n_remaining,)
            cross_sums = corr_matrix[np.ix_(rem_arr, sel_arr)].sum(axis=1)

            # Portfolio variance after adding each candidate (equal weights, unit vol)
            # port_var = (sum_corr_sel + 2*cross_sum_c + self_corr_c) / (k+1)^2
            # self_corr_c = 1.0 (diagonal)
            port_vars = (sum_corr_sel + 2.0 * cross_sums + 1.0) / (k + 1) ** 2
            port_vols = np.sqrt(np.maximum(port_vars, 1e-10))

            # Portfolio SR for each possible addition
            port_srs = mean_srs / port_vols

            # Best candidate
            best_rel = int(np.argmax(port_srs))
            best_sr = float(port_srs[best_rel])

            # Stopping condition: portfolio SR dropped below tolerance × peak
            if best_sr < self.sr_tolerance * peak_sr:
                break

            # Add best candidate
            selected.append(remaining[best_rel])
            remaining.pop(best_rel)
            peak_sr = max(peak_sr, best_sr)

        # Enforce minimum: add more instruments if needed (by net SR order)
        while len(selected) < self.min_instruments and len(remaining) > 0:
            selected.append(remaining.pop(0))

        # Convert integer indices back to instrument names
        return [candidates[i] for i in selected]

    def _apply_hysteresis(
        self,
        prev_selected: List[str],
        new_selected: List[str],
        net_srs: Dict[str, float],
    ) -> List[str]:
        """
        Limit month-to-month additions and removals to hysteresis_buffer each.

        Strategy:
          - Sort additions by net SR (descending): keep the best new entrants
          - Sort removals by net SR (ascending): remove the worst instruments first
          - Excess additions (beyond buffer) are not admitted
          - Excess removals (beyond buffer) are kept in the set
        """
        if not prev_selected:
            return new_selected

        prev = set(prev_selected)
        new = set(new_selected)

        additions = sorted(
            new - prev, key=lambda x: net_srs.get(x, 0.0), reverse=True
        )
        removals = sorted(
            prev - new, key=lambda x: net_srs.get(x, 0.0)  # worst (lowest SR) first
        )

        # If within buffer in both directions, accept the new selection as-is
        if len(additions) <= self.hysteresis_buffer and len(removals) <= self.hysteresis_buffer:
            return new_selected

        # Start from optimal new set, then undo excess changes
        result = set(new)

        # Revert excess additions (keep instrument out of set)
        for instr in additions[self.hysteresis_buffer :]:
            result.discard(instr)

        # Revert excess removals (keep instrument in set)
        for instr in removals[self.hysteresis_buffer :]:
            result.add(instr)

        return sorted(result)

    # -------------------------------------------------------------------------
    # Cost / spread / vol helpers
    # -------------------------------------------------------------------------

    def _get_rebalance_dates(self, all_dates: pd.DatetimeIndex) -> List[pd.Timestamp]:
        """Generate rebalance dates snapped to actual trading days."""
        calendar_dates = pd.date_range(
            start=all_dates[0], end=all_dates[-1], freq=self.rebalance_freq
        )
        snapped = []
        for d in calendar_dates:
            valid = all_dates[all_dates <= d]
            if len(valid) > 0:
                snapped.append(valid[-1])

        # Deduplicate (multiple calendar dates may snap to same trading day)
        return sorted(set(snapped))

    def _get_spread_lookup(self, date: pd.Timestamp) -> Dict[str, float]:
        """
        Get spread in bps for all instruments at date, using ADV rank tiers.

        Tier thresholds (matching WalkForwardCostEstimator with adv_panel):
          top 20  → 2 bps
          rank 21–70 → 5 bps
          rank 71+   → 12 bps
        """
        if self.adv_df is None or len(self.adv_df) == 0:
            return {}

        adv_at_date = self.adv_df.loc[self.adv_df.index <= date]
        if len(adv_at_date) == 0:
            return {}

        adv_row = adv_at_date.iloc[-1].fillna(0.0)
        ranks = adv_row.rank(ascending=False, method="first")

        spread_lookup: Dict[str, float] = {}
        for instr in adv_row.index:
            rank = ranks.get(instr, 999)
            if rank <= 20:
                spread_lookup[instr] = 2.0
            elif rank <= 70:
                spread_lookup[instr] = 5.0
            else:
                spread_lookup[instr] = 12.0

        return spread_lookup

    def _get_vol_lookup(
        self, date: pd.Timestamp, instruments: List[str]
    ) -> Dict[str, float]:
        """Get annualized volatility for each instrument from trailing prices."""
        if self.prices_df is None or len(self.prices_df) == 0:
            return {instr: np.nan for instr in instruments}

        # Get trailing prices up to date for all instruments at once
        available = [i for i in instruments if i in self.prices_df.columns]
        if not available:
            return {instr: np.nan for instr in instruments}

        prices_window = (
            self.prices_df.loc[self.prices_df.index <= date, available]
            .tail(self.vol_window + 1)
        )

        returns = prices_window.pct_change()
        # Count valid returns per instrument
        counts = returns.notna().sum()
        daily_vols = returns.std()

        vol_lookup: Dict[str, float] = {}
        for instr in instruments:
            if instr not in available or counts.get(instr, 0) < 5:
                vol_lookup[instr] = np.nan
            else:
                vol_lookup[instr] = float(daily_vols[instr]) * np.sqrt(252)

        return vol_lookup

    def _compute_net_sr_from_lookups(
        self,
        instruments: List[str],
        spread_lookup: Dict[str, float],
        vol_lookup: Dict[str, float],
    ) -> Dict[str, float]:
        """
        Compute net SR = assumed_gross_SR - annual_cost_SR.

        Annual cost SR = (spread_bps/10000 + 2*fee_bps/10000) / annual_vol * turnover
        """
        fee_fraction = self.fee_bps / 10_000

        net_srs: Dict[str, float] = {}
        for instr in instruments:
            annual_vol = vol_lookup.get(instr, np.nan)
            if np.isnan(annual_vol) or annual_vol <= 0:
                net_srs[instr] = np.nan
                continue

            spread_bps = spread_lookup.get(instr, 12.0)
            round_trip_cost = (spread_bps / 10_000) + 2.0 * fee_fraction
            annual_sr_cost = (round_trip_cost / annual_vol) * self.stack_turnover

            net_srs[instr] = self.assumed_gross_sr - annual_sr_cost

        return net_srs

    # -------------------------------------------------------------------------
    # Correlation estimation
    # -------------------------------------------------------------------------

    def _compute_shrunk_correlation(
        self, date: pd.Timestamp, instruments: List[str]
    ) -> pd.DataFrame:
        """
        Compute shrinkage-adjusted correlation matrix from trailing prices.

        Shrinkage formula: C' = (1-α)·C_raw + α·I
        where α = correlation_shrinkage (default 0.5).

        Instruments with insufficient data get identity rows/columns
        (zero correlation with all others, unit self-correlation).

        Args:
            date: Compute correlations using data up to this date
            instruments: List of instrument codes (defines matrix order)

        Returns:
            DataFrame with instruments as both index and columns
        """
        n = len(instruments)

        # Default: identity matrix (no correlation for any pair)
        full_corr = pd.DataFrame(
            np.eye(n), index=instruments, columns=instruments
        )

        available = [i for i in instruments if i in self.prices_df.columns]
        if len(available) < 2:
            return full_corr

        # Trailing prices
        prices_window = (
            self.prices_df.loc[self.prices_df.index <= date, available]
            .tail(self.correlation_window + 1)
        )

        # Require minimum observations per instrument (pairwise)
        min_obs = max(20, self.correlation_window // 10)
        obs_count = prices_window.notna().sum()
        available_valid = obs_count[obs_count >= min_obs].index.tolist()

        if len(available_valid) < 2:
            return full_corr

        # Log returns for correlation (pairwise complete observations)
        returns = prices_window[available_valid].pct_change().dropna(how="all")
        corr_raw = returns.corr()  # pairwise by default

        # Fill NaN pairs (insufficient co-observation) with zero correlation
        corr_raw = corr_raw.fillna(0.0)
        np.fill_diagonal(corr_raw.values, 1.0)

        # Apply shrinkage toward identity
        n_valid = len(available_valid)
        identity = np.eye(n_valid)
        corr_shrunk_arr = (
            (1 - self.correlation_shrinkage) * corr_raw.values
            + self.correlation_shrinkage * identity
        )

        # Place shrunk submatrix into the full instrument matrix
        corr_shrunk_df = pd.DataFrame(
            corr_shrunk_arr, index=available_valid, columns=available_valid
        )
        full_corr.loc[available_valid, available_valid] = corr_shrunk_df

        return full_corr
