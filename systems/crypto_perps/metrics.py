"""
Research metrics for crypto perpetual futures trading system

Calculates performance metrics from backtest outputs for analysis and comparison.
"""

import pandas as pd
import numpy as np
from typing import Dict, Optional


def calculate_metrics(
    equity_curve: pd.Series,
    weights_df: pd.DataFrame,
    trades_df: pd.DataFrame,
    capital: float,
    state_df: Optional[pd.DataFrame] = None,
    constraint_scalars: Optional[pd.Series] = None
) -> Dict[str, float]:
    """
    Calculate research metrics from backtest outputs

    Args:
        equity_curve: Time series of portfolio equity
        weights_df: DataFrame of position weights (date x instrument)
                    Units: fraction of capital (e.g., 0.05 = 5% of capital)
        trades_df: DataFrame of weight changes (date x instrument)
                   Represents DELTA WEIGHTS, not notional or contracts
                   Units: fraction of capital (e.g., 0.05 = 5% weight change)
                   Formula: trade_weight = target_weight - current_weight
        capital: Initial capital amount
        state_df: Optional DataFrame of instrument states (date x instrument)
                  States: 'ACTIVE', 'INELIGIBLE_HOLD', 'BANNED_FLATTEN'
        constraint_scalars: Optional Series of portfolio-level constraint scalars
                            (one value per date, same for all instruments)
                            Values < 1.0 indicate constraints were active

    Returns:
        Dictionary containing:
            ann_return: Annualized geometric return
            ann_vol: Annualized volatility
            sharpe: Sharpe ratio (ann_return / ann_vol)
            max_drawdown: Maximum peak-to-trough drawdown (negative fraction)
            gross_exposure: Mean daily gross exposure (sum of abs weights)
            turnover: Mean daily turnover (sum of abs weight changes)
            days_constrained: Count of days where constraints were active
            fraction_days_constrained: Fraction of days constrained
            exit_flattens: Count of instrument-days in BANNED_FLATTEN state
            exit_decays: Count of instrument-days in INELIGIBLE_HOLD state

    Notes:
        Turnover Definition:
        - trades_df contains DELTA WEIGHTS (not notional, not contracts)
        - If trades_df[date, inst] = 0.05, this represents a 5% weight change
        - Turnover = mean(sum(abs(trades_df), axis=1))
        - Units: fraction of capital per day
        - Example: turnover=0.15 means 15% of capital turned over per day on average

        Constraint Scalars:
        - constraint_scalars are portfolio-level (same for all instruments on a date)
        - Values < 1.0 indicate gross leverage or IDM caps were active
        - If not provided, days_constrained metrics will be 0

        State DataFrame:
        - If not provided, exit metrics will be 0
        - States must match InstrumentState enum values
    """
    # Calculate daily returns
    daily_returns = equity_curve.pct_change().dropna()

    if len(daily_returns) == 0:
        # Not enough data for metrics
        return {
            'ann_return': 0.0,
            'ann_vol': 0.0,
            'sharpe': 0.0,
            'max_drawdown': 0.0,
            'gross_exposure': 0.0,
            'turnover': 0.0,
            'days_constrained': 0,
            'fraction_days_constrained': 0.0,
            'exit_flattens': 0,
            'exit_decays': 0
        }

    # Annualized return (geometric)
    total_return = (equity_curve.iloc[-1] / capital) - 1.0
    num_days = len(equity_curve)
    ann_return = (1 + total_return) ** (252 / num_days) - 1.0

    # Annualized volatility
    daily_vol = daily_returns.std()
    ann_vol = daily_vol * np.sqrt(252)

    # Sharpe ratio
    if ann_vol > 0:
        sharpe = ann_return / ann_vol
    else:
        sharpe = 0.0

    # Maximum drawdown
    cumulative_returns = (1 + daily_returns).cumprod()
    running_max = cumulative_returns.expanding().max()
    drawdown = (cumulative_returns - running_max) / running_max
    max_drawdown = drawdown.min()

    # Gross exposure (mean daily sum of absolute weights)
    gross_exposure_series = weights_df.abs().sum(axis=1)
    gross_exposure = gross_exposure_series.mean()

    # Turnover (mean daily sum of absolute weight changes)
    # trades_df represents delta weights, so summing abs gives daily turnover
    turnover_series = trades_df.abs().sum(axis=1)
    turnover = turnover_series.mean()

    # Days constrained (if constraint scalars provided)
    if constraint_scalars is not None:
        # Count days where scalar < 1.0 (constraints active)
        days_constrained = int((constraint_scalars < 1.0).sum())
        fraction_days_constrained = days_constrained / len(constraint_scalars)
    else:
        days_constrained = 0
        fraction_days_constrained = 0.0

    # Exit activity (if state_df provided)
    if state_df is not None:
        # Count instrument-days in exit states
        # Note: State values are strings matching InstrumentState enum
        exit_flattens = int((state_df == 'BANNED_FLATTEN').sum().sum())
        exit_decays = int((state_df == 'INELIGIBLE_HOLD').sum().sum())
    else:
        exit_flattens = 0
        exit_decays = 0

    return {
        'ann_return': ann_return,
        'ann_vol': ann_vol,
        'sharpe': sharpe,
        'max_drawdown': max_drawdown,
        'gross_exposure': gross_exposure,
        'turnover': turnover,
        'days_constrained': days_constrained,
        'fraction_days_constrained': fraction_days_constrained,
        'exit_flattens': exit_flattens,
        'exit_decays': exit_decays
    }
