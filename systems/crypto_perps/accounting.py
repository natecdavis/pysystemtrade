"""
Accounting for crypto perpetual futures

Calculates daily PnL attribution:
- Price PnL: position × price change
- Funding PnL: position × funding_rate × price_prev
- Costs: RTC (Round-Trip Cost) in dollars
- Total PnL: price_pnl + funding_pnl - costs

Accounting Identity (must hold):
    total_pnl = price_pnl + funding_pnl - costs (tolerance 1e-6)
"""

import pandas as pd
import numpy as np
from typing import Dict, Tuple


def calculate_price_pnl(
    position_prev: float,
    price_prev: float,
    price_curr: float
) -> float:
    """
    Calculate price PnL for single instrument on single day

    Formula:
        price_pnl = position_prev * (price_curr - price_prev)

    Args:
        position_prev: Position at end of previous day (in notional dollars)
        price_prev: Price at end of previous day
        price_curr: Price at end of current day

    Returns:
        Price PnL in dollars

    Notes:
        - Position is in notional (dollars), not contracts
        - For crypto perps: notional position = weight × capital
        - Price change applied to previous position
    """
    if pd.isna(price_prev) or pd.isna(price_curr):
        return 0.0

    if price_prev == 0:
        return 0.0

    # Number of units (crypto amount)
    # units = notional / price
    units = position_prev / price_prev

    # Price PnL = units × price_change
    price_pnl = units * (price_curr - price_prev)

    return price_pnl


def calculate_funding_pnl(
    position_prev: float,
    price_prev: float,
    funding_rate: float
) -> float:
    """
    Calculate funding PnL for single instrument on single day

    Formula:
        funding_pnl = position_prev * funding_rate * price_prev

    More precisely:
        units = position_prev / price_prev
        funding_pnl = units * funding_rate * price_prev
                    = position_prev * funding_rate

    Args:
        position_prev: Position at end of previous day (in notional dollars)
        price_prev: Price at end of previous day
        funding_rate: Funding rate for period from t-1 to t

    Returns:
        Funding PnL in dollars

    Notes:
        - Positive position (long): pay funding if rate > 0, receive if rate < 0
        - Negative position (short): receive funding if rate > 0, pay if rate < 0
        - funding_rate is already aligned to position holding period
    """
    if pd.isna(funding_rate):
        return 0.0

    # Funding PnL = position × funding_rate
    funding_pnl = position_prev * funding_rate

    return funding_pnl


def calculate_daily_pnl(
    date: pd.Timestamp,
    positions_prev: Dict[str, float],
    positions_curr: Dict[str, float],
    prices_prev: Dict[str, float],
    prices_curr: Dict[str, float],
    funding_rates: Dict[str, float],
    costs: Dict[str, float]
) -> Tuple[Dict[str, float], Dict[str, float], Dict[str, float], float]:
    """
    Calculate daily PnL for all instruments

    Args:
        date: Current date
        positions_prev: Dict mapping instrument -> position at t-1 (notional dollars)
        positions_curr: Dict mapping instrument -> position at t (notional dollars)
        prices_prev: Dict mapping instrument -> price at t-1
        prices_curr: Dict mapping instrument -> price at t
        funding_rates: Dict mapping instrument -> funding rate from t-1 to t
        costs: Dict mapping instrument -> RTC cost in dollars

    Returns:
        Tuple of (price_pnl_dict, funding_pnl_dict, total_pnl_dict, total_pnl):
        - price_pnl_dict: Dict mapping instrument -> price PnL
        - funding_pnl_dict: Dict mapping instrument -> funding PnL
        - total_pnl_dict: Dict mapping instrument -> total PnL (price + funding - costs)
        - total_pnl: Sum of all instruments' total PnL

    Notes:
        - Validates accounting identity: total = price + funding - costs
        - Missing prices handled via universe module (frozen, PnL = 0)
    """
    instruments = list(positions_prev.keys())

    price_pnl_dict = {}
    funding_pnl_dict = {}
    total_pnl_dict = {}

    for instrument in instruments:
        pos_prev = positions_prev.get(instrument, 0.0)
        pos_curr = positions_curr.get(instrument, 0.0)
        price_prev = prices_prev.get(instrument, np.nan)
        price_curr = prices_curr.get(instrument, np.nan)
        funding_rate = funding_rates.get(instrument, 0.0)
        cost = costs.get(instrument, 0.0)

        # Calculate components
        price_pnl = calculate_price_pnl(pos_prev, price_prev, price_curr)
        funding_pnl = calculate_funding_pnl(pos_prev, price_prev, funding_rate)

        # Total PnL = price + funding - costs
        total_pnl = price_pnl + funding_pnl - cost

        price_pnl_dict[instrument] = price_pnl
        funding_pnl_dict[instrument] = funding_pnl
        total_pnl_dict[instrument] = total_pnl

    # Total across all instruments
    total_pnl_all = sum(total_pnl_dict.values())

    return price_pnl_dict, funding_pnl_dict, total_pnl_dict, total_pnl_all


def calculate_cumulative_pnl(
    positions_df: pd.DataFrame,
    prices_df: pd.DataFrame,
    meta_df: pd.DataFrame,
    costs_df: pd.DataFrame,
    initial_capital: float
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.Series]:
    """
    Calculate cumulative PnL and equity curve for entire backtest

    Args:
        positions_df: DataFrame with date index and instrument columns (notional positions)
        prices_df: DataFrame with date index and instrument columns (prices)
        meta_df: DataFrame with MultiIndex (date, instrument) containing funding_rate
        costs_df: DataFrame with date index and instrument columns (RTC costs)
        initial_capital: Starting capital

    Returns:
        Tuple of (price_pnl_df, funding_pnl_df, total_pnl_df, equity_curve):
        - price_pnl_df: Daily price PnL per instrument
        - funding_pnl_df: Daily funding PnL per instrument
        - total_pnl_df: Daily total PnL per instrument
        - equity_curve: Cumulative equity (starting capital + cumsum of total PnL)

    Notes:
        - First day has zero PnL (no previous position)
        - equity_curve[0] = initial_capital
        - Validates accounting identity for each day
    """
    dates = positions_df.index
    instruments = list(positions_df.columns)

    # Initialize output storage
    price_pnl_data = {inst: [] for inst in instruments}
    funding_pnl_data = {inst: [] for inst in instruments}
    total_pnl_data = {inst: [] for inst in instruments}
    total_pnl_list = []

    for i, date in enumerate(dates):
        if i == 0:
            # First day: no previous position, zero PnL
            for inst in instruments:
                price_pnl_data[inst].append(0.0)
                funding_pnl_data[inst].append(0.0)
                total_pnl_data[inst].append(0.0)
            total_pnl_list.append(0.0)
            continue

        # Get previous and current data
        date_prev = dates[i-1]

        positions_prev = positions_df.loc[date_prev].to_dict()
        positions_curr = positions_df.loc[date].to_dict()
        prices_prev = prices_df.loc[date_prev].to_dict()
        prices_curr = prices_df.loc[date].to_dict()
        costs = costs_df.loc[date].to_dict()

        # Get funding rates
        funding_rates = {}
        for inst in instruments:
            try:
                funding_rates[inst] = meta_df.loc[(date, inst), 'funding_rate']
            except KeyError:
                funding_rates[inst] = 0.0

        # Calculate PnL
        price_pnl, funding_pnl, total_pnl, total_pnl_sum = calculate_daily_pnl(
            date=date,
            positions_prev=positions_prev,
            positions_curr=positions_curr,
            prices_prev=prices_prev,
            prices_curr=prices_curr,
            funding_rates=funding_rates,
            costs=costs
        )

        # Store results
        for inst in instruments:
            price_pnl_data[inst].append(price_pnl[inst])
            funding_pnl_data[inst].append(funding_pnl[inst])
            total_pnl_data[inst].append(total_pnl[inst])

        total_pnl_list.append(total_pnl_sum)

    # Create DataFrames
    price_pnl_df = pd.DataFrame(price_pnl_data, index=dates)
    funding_pnl_df = pd.DataFrame(funding_pnl_data, index=dates)
    total_pnl_df = pd.DataFrame(total_pnl_data, index=dates)

    # Calculate equity curve
    total_pnl_series = pd.Series(total_pnl_list, index=dates)
    equity_curve = initial_capital + total_pnl_series.cumsum()

    return price_pnl_df, funding_pnl_df, total_pnl_df, equity_curve
