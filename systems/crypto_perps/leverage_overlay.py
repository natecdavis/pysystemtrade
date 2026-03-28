"""
Drawdown-contingent leverage scaling overlay.

Simulates path-dependent leverage scaling on a daily returns series —
equivalent to what would happen in live trading where yesterday's equity
determines today's leverage scalar.
"""

import numpy as np
import pandas as pd


DAYS_PER_YEAR = 365


def simulate_dd_overlay(
    returns: pd.Series,
    base_leverage: float,
    dd_threshold: float,
    min_scale: float,
) -> tuple[pd.Series, pd.Series]:
    """
    Simulate path-dependent drawdown-contingent leverage scaling.

    At each day t:
      1. equity[t-1] → drawdown[t-1] from peak
      2. scalar[t] = max(min_scale, 1 + dd[t-1] / dd_threshold)
         (linear: 1.0 at 0% DD, min_scale at -dd_threshold, clamped there)
      3. leveraged_return[t] = returns[t] * base_leverage * scalar[t]

    Args:
        returns: Daily returns as decimals (not scaled by leverage yet)
        base_leverage: Capital multiplier applied at all times
        dd_threshold: Drawdown level at which scalar hits min_scale (e.g. 0.10 = -10%)
        min_scale: Minimum leverage scalar (0.0 = go flat, 0.25 = retain 25% exposure)

    Returns:
        (leveraged_returns, scalar_series) — both indexed like `returns`
    """
    returns = returns.dropna()
    n = len(returns)
    ret_vals = returns.values

    equity = 1.0
    peak = 1.0
    scalars = np.empty(n)
    leveraged = np.empty(n)

    for i in range(n):
        # Compute drawdown from yesterday's equity
        dd = (equity - peak) / peak  # <= 0

        if dd_threshold > 0:
            raw_scalar = 1.0 + dd / dd_threshold  # linear ramp down
            scalar = max(min_scale, min(1.0, raw_scalar))
        else:
            scalar = 1.0  # no threshold → always full leverage

        scalars[i] = scalar
        leveraged[i] = ret_vals[i] * base_leverage * scalar

        # Update equity path
        equity *= 1.0 + leveraged[i]
        if equity > peak:
            peak = equity

    return (
        pd.Series(leveraged, index=returns.index),
        pd.Series(scalars, index=returns.index),
    )


def compute_overlay_metrics(
    base_returns: pd.Series,
    base_leverage: float,
    dd_threshold: float,
    min_scale: float,
) -> dict:
    """
    Run simulate_dd_overlay and compute Sharpe/Calmar/CAGR/MaxDD/AvgLeverage.

    Args:
        base_returns: Unleveraged daily returns (as decimals)
        base_leverage: Capital multiplier
        dd_threshold: DD level (fraction) where scalar hits min_scale
        min_scale: Minimum scalar (0 = go flat)

    Returns:
        dict with keys: sharpe, calmar, cagr, max_dd, avg_leverage
    """
    lev_returns, scalars = simulate_dd_overlay(
        base_returns, base_leverage, dd_threshold, min_scale
    )
    r = lev_returns.dropna()

    cum = (1 + r).cumprod()
    years = len(r) / DAYS_PER_YEAR
    cagr = (cum.iloc[-1]) ** (1 / years) - 1
    ann_return = r.mean() * DAYS_PER_YEAR
    ann_vol = r.std() * np.sqrt(DAYS_PER_YEAR)
    sharpe = ann_return / ann_vol if ann_vol > 0 else 0.0
    running_max = cum.cummax()
    drawdown = (cum - running_max) / running_max
    max_dd = float(drawdown.min())
    calmar = cagr / abs(max_dd) if max_dd != 0 else 0.0
    avg_leverage = float(scalars.mean()) * base_leverage

    return {
        "sharpe": sharpe,
        "calmar": calmar,
        "cagr": cagr,
        "max_dd": max_dd,
        "avg_leverage": avg_leverage,
    }
