"""
Diagnostics data collection for crypto perpetual futures trading system

Provides optional diagnostic output for research and debugging.
Uses O(1) dict storage to avoid quadratic behavior on large datasets.
"""

import pandas as pd
import numpy as np
from typing import Dict, Tuple, Any, Optional
from pathlib import Path


class DiagnosticsCollector:
    """
    Accumulate daily diagnostics data using O(1) dict storage

    CRITICAL: Uses dict keyed by (date, instrument) to avoid quadratic behavior.
    All record_* methods perform O(1) dict lookups, not linear scans.

    Schema (dynamic based on enabled features):
        # Universe/State (always present)
        - date, instrument, in_layer_a, eligible, state, ban_source,
          days_in_state, entry_weight

        # Forecasts (dynamic based on enabled rules)
        - forecast_combined (always present)
        - forecast_<rule_name> (one column per enabled rule)
          Examples: forecast_ewmac_8_32, forecast_carry_funding, forecast_relative_momentum
          Disabled rules: columns omitted (not NaN-filled)

        # Weights (always present)
        - target_weight_unconstrained, target_weight_after_exits,
          target_weight_constrained, current_weight

        # Trading (always present)
        - trade_weight, trade_reason, buffer_threshold

        # Constraints (portfolio-level, always present)
        - gross_leverage, idm, overall_scalar
          NOTE: Same for all instruments on a given date (redundantly stored for easy querying)

        # PnL (always present)
        - pnl_price, pnl_funding, pnl_costs, pnl_total

    Usage:
        collector = DiagnosticsCollector()

        # In system.py daily loop:
        collector.record_state(date, inst, state, in_layer_a, eligible, ...)
        collector.record_forecasts(date, inst, forecast_combined=10.0, ewmac_8_32=5.0, carry_funding=-2.0)
        collector.record_weights(date, inst, unconstrained, after_exits, constrained, current)
        collector.record_trade(date, inst, trade, reason, buffer_threshold)
        collector.record_constraints(date, inst, gross_lev, idm, overall_scalar)
        collector.record_pnl(date, inst, price_pnl, funding_pnl, costs_pnl)

        # At end:
        collector.write_parquet(outdir / 'diagnostics.parquet')
    """

    def __init__(self):
        """
        Initialize diagnostics collector with O(1) dict storage

        Storage structure:
            self.rows: Dict[(date, instrument), Dict[str, Any]]
            - Keyed by (date, instrument) tuple for O(1) lookup
            - Values are dicts of field -> value
            - DataFrame built once at end from dict.values()
        """
        # CRITICAL: Dict keyed by (date, instrument) for O(1) access
        # Avoids quadratic behavior from linear scans
        self.rows: Dict[Tuple[pd.Timestamp, str], Dict[str, Any]] = {}

    def _get_or_create_row(self, date, instrument) -> Dict[str, Any]:
        """
        Get or create row dict for (date, instrument) - O(1) operation

        Args:
            date: Date (will be converted to pd.Timestamp)
            instrument: Instrument name

        Returns:
            Dict for this (date, instrument) key
        """
        key = (pd.Timestamp(date), instrument)
        if key not in self.rows:
            self.rows[key] = {'date': date, 'instrument': instrument}
        return self.rows[key]

    def record_state(
        self,
        date,
        instrument: str,
        state: str,
        in_layer_a: bool,
        eligible: bool,
        days_in_state: int,
        entry_weight: float,
        ban_source: Optional[str]
    ):
        """
        Record state fields for an instrument on a date

        Args:
            date: Date
            instrument: Instrument name
            state: State string (e.g., 'ACTIVE', 'INELIGIBLE_HOLD', 'BANNED_FLATTEN')
            in_layer_a: Whether instrument is in Layer A
            eligible: Whether instrument is eligible on this date
            days_in_state: Number of consecutive days in current state
            entry_weight: Weight when instrument entered current state (NaN if N/A)
            ban_source: Source of ban if BANNED_FLATTEN ('explicit', 'membership', or None)

        Complexity: O(1) - dict key lookup
        """
        row = self._get_or_create_row(date, instrument)
        row.update({
            'state': state,
            'in_layer_a': in_layer_a,
            'eligible': eligible,
            'days_in_state': days_in_state,
            'entry_weight': entry_weight,
            'ban_source': ban_source if ban_source else ''
        })

    def record_forecasts(
        self,
        date,
        instrument: str,
        forecast_combined: float,
        **per_rule_forecasts
    ):
        """
        Record forecasts dynamically based on enabled rules

        Args:
            date: Date
            instrument: Instrument name
            forecast_combined: Combined forecast (required, always recorded)
            **per_rule_forecasts: Dict of rule_name -> forecast value
                                  e.g., ewmac_8_32=5.0, carry_funding=-2.0, relative_momentum=1.0

        Implementation:
            - forecast_combined is always recorded
            - Per-rule forecasts recorded as forecast_<rule_name>
            - Disabled rules: columns omitted (not NaN-filled)
            - Dynamic schema adapts to enabled rules

        Complexity: O(1) - dict key lookup + fixed number of field updates

        Examples:
            # Only EWMAC and carry enabled:
            collector.record_forecasts(date, 'BTC', forecast_combined=10.0,
                                      ewmac_8_32=5.0, carry_funding=-2.0)
            # Columns: forecast_combined, forecast_ewmac_8_32, forecast_carry_funding

            # All rules enabled:
            collector.record_forecasts(date, 'BTC', forecast_combined=10.0,
                                      ewmac_8_32=5.0, carry_funding=-2.0, relative_momentum=1.0)
            # Columns: forecast_combined, forecast_ewmac_8_32, forecast_carry_funding,
            #          forecast_relative_momentum
        """
        row = self._get_or_create_row(date, instrument)

        # Always record combined forecast
        row['forecast_combined'] = forecast_combined

        # Dynamically record per-rule forecasts (prefixed with forecast_)
        for rule_name, forecast_value in per_rule_forecasts.items():
            row[f'forecast_{rule_name}'] = forecast_value

    def record_weights(
        self,
        date,
        instrument: str,
        unconstrained: float,
        after_exits: float,
        constrained: float,
        current: float
    ):
        """
        Record weight fields at different stages

        Args:
            date: Date
            instrument: Instrument name
            unconstrained: Target weight before exits and constraints
            after_exits: Target weight after exit rules applied
            constrained: Final target weight after portfolio constraints
            current: Current weight (from yesterday's position)

        Complexity: O(1) - dict key lookup
        """
        row = self._get_or_create_row(date, instrument)
        row.update({
            'target_weight_unconstrained': unconstrained,
            'target_weight_after_exits': after_exits,
            'target_weight_constrained': constrained,
            'current_weight': current
        })

    def record_trade(
        self,
        date,
        instrument: str,
        trade: float,
        reason: str,
        buffer_threshold: float
    ):
        """
        Record trade execution fields

        Args:
            date: Date
            instrument: Instrument name
            trade: Trade weight (delta weight executed)
            reason: Trade reason ('flatten_banned', 'decay_ineligible', 'buffer_trade', 'buffer_no_trade')
            buffer_threshold: Buffer threshold for this instrument

        Complexity: O(1) - dict key lookup
        """
        row = self._get_or_create_row(date, instrument)
        row.update({
            'trade_weight': trade,
            'trade_reason': reason,
            'buffer_threshold': buffer_threshold
        })

    def record_constraints(
        self,
        date,
        instrument: str,
        gross_lev: float,
        idm: float,
        overall_scalar: float
    ):
        """
        Record constraint fields (portfolio-level)

        NOTE: gross_lev, idm, overall_scalar are the SAME for all instruments on a given date.
        This is redundantly stored per (date, instrument) for easier DataFrame querying.

        If constraints vary per instrument in the future, this would need refactoring.

        Args:
            date: Date
            instrument: Instrument name
            gross_lev: Gross leverage (portfolio-level)
            idm: IDM value (portfolio-level)
            overall_scalar: Overall constraint scalar (portfolio-level, <1.0 means constrained)

        Complexity: O(1) - dict key lookup
        """
        row = self._get_or_create_row(date, instrument)
        row.update({
            'gross_leverage': gross_lev,
            'idm': idm,
            'overall_scalar': overall_scalar
        })

    def record_pnl(
        self,
        date,
        instrument: str,
        pnl_price: float,
        pnl_funding: float,
        pnl_costs: float
    ):
        """
        Record PnL components

        Args:
            date: Date
            instrument: Instrument name
            pnl_price: Price PnL (mark-to-market)
            pnl_funding: Funding PnL
            pnl_costs: Trading costs

        Complexity: O(1) - dict key lookup
        """
        row = self._get_or_create_row(date, instrument)

        # Calculate total PnL (accounting identity)
        pnl_total = pnl_price + pnl_funding - pnl_costs

        row.update({
            'pnl_price': pnl_price,
            'pnl_funding': pnl_funding,
            'pnl_costs': pnl_costs,
            'pnl_total': pnl_total
        })

    def write_parquet(self, output_path: Path):
        """
        Build DataFrame once from dict values and write to Parquet

        Complexity: O(N) where N = number of (date, instrument) rows

        Output:
            - Parquet file with dynamic schema based on recorded fields
            - Sorted by date and instrument for cleaner output
            - No duplicate (date, instrument) rows (guaranteed by dict keys)
        """
        if len(self.rows) == 0:
            # No data collected, write empty DataFrame
            df = pd.DataFrame()
        else:
            # Convert dict to list of rows (O(N))
            df = pd.DataFrame(list(self.rows.values()))

            # Sort by date and instrument for cleaner output
            df = df.sort_values(['date', 'instrument'])

        # Write to Parquet
        output_path.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(output_path, index=False, engine='pyarrow')

    def get_dataframe(self) -> pd.DataFrame:
        """
        Get current diagnostics as DataFrame (for testing/inspection)

        Returns:
            DataFrame with all collected diagnostics
        """
        if len(self.rows) == 0:
            return pd.DataFrame()

        df = pd.DataFrame(list(self.rows.values()))
        df = df.sort_values(['date', 'instrument'])
        return df
