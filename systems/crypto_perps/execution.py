"""
Execution and cost model for crypto perpetual futures

Handles:
1. Trading buffers (prevent unnecessary small trades)
2. Cost calculation (spread + fees)
3. Frozen position enforcement (no trades if ineligible)

Cost Types:
- RTC (Round-Trip Cost): actual dollar cost, subtracted from PnL
- SRcost (Sharpe Ratio cost): diagnostic metric (RTC / annual_vol / capital)
"""

import pandas as pd
import numpy as np
from typing import Dict, Tuple
from syscore.dateutils import BUSINESS_DAYS_IN_YEAR


def calculate_position_volatility(
    notional: float,
    price: float,
    daily_vol: float,
    capital: float
) -> float:
    """
    Calculate position volatility in capital terms

    Used for trading buffer calculations.

    Args:
        notional: Position size in dollars
        price: Current price
        daily_vol: Daily volatility in price units
        capital: Total capital

    Returns:
        Position volatility as fraction of capital

    Formula:
        position_vol = (notional / price) * daily_vol / capital
    """
    if price == 0 or capital == 0:
        return 0.0

    # Number of units (crypto amount)
    units = notional / price

    # Position volatility in dollars
    position_vol_dollars = units * daily_vol

    # Position volatility as fraction of capital
    position_vol_frac = position_vol_dollars / capital

    return position_vol_frac


def apply_trading_buffer(
    target_weights: Dict[str, float],
    current_weights: Dict[str, float],
    buffer_frac: float,
    prices: Dict[str, float],
    daily_vols: Dict[str, float],
    capital: float,
    eligible: Dict[str, bool]
) -> Dict[str, float]:
    """
    Apply trading buffers to prevent unnecessary trades

    No trade if |target - current| < buffer_frac * position_vol

    Args:
        target_weights: Dict mapping instrument -> target weight
        current_weights: Dict mapping instrument -> current weight
        buffer_frac: Buffer fraction (e.g., 0.1 = 10% of position vol)
        prices: Dict mapping instrument -> current price
        daily_vols: Dict mapping instrument -> daily volatility
        capital: Total capital
        eligible: Dict mapping instrument -> eligibility status (True/False)

    Returns:
        Dict mapping instrument -> trade weight
        (target_weight - current_weight if trade executed, else 0)

    Notes:
        - If instrument is ineligible (frozen), no trades allowed
        - Buffer prevents trading when delta is small relative to position risk
    """
    trades = {}

    for instrument in target_weights.keys():
        target = target_weights.get(instrument, 0.0)
        current = current_weights.get(instrument, 0.0)
        is_eligible = eligible.get(instrument, True)

        # If frozen, no trades allowed
        if not is_eligible:
            trades[instrument] = 0.0
            continue

        # Calculate position delta
        delta = target - current

        # If no meaningful delta, no trade
        if abs(delta) < 1e-10:
            trades[instrument] = 0.0
            continue

        # Calculate position volatility for buffer
        # Use average of target and current for position size
        avg_weight = (target + current) / 2.0
        avg_notional = avg_weight * capital

        position_vol = calculate_position_volatility(
            notional=abs(avg_notional),
            price=prices.get(instrument, 1.0),
            daily_vol=daily_vols.get(instrument, 0.0),
            capital=capital
        )

        # Calculate buffer threshold
        buffer_threshold = buffer_frac * position_vol

        # Trade only if delta exceeds buffer
        if abs(delta) >= buffer_threshold:
            trades[instrument] = delta
        else:
            trades[instrument] = 0.0

    return trades


def calculate_trade_costs(
    trades: Dict[str, float],
    prices: Dict[str, float],
    meta: Dict[str, Dict[str, float]],
    capital: float,
    daily_vols: Dict[str, float]
) -> Tuple[Dict[str, float], Dict[str, float]]:
    """
    Calculate trading costs (RTC and SRcost)

    Args:
        trades: Dict mapping instrument -> trade weight (delta)
        prices: Dict mapping instrument -> current price
        meta: Dict mapping instrument -> {'spread_frac', 'taker_fee_frac'}
        capital: Total capital
        daily_vols: Dict mapping instrument -> daily volatility (for SRcost)

    Returns:
        Tuple of (rtc_costs, srcosts):
        - rtc_costs: Dict mapping instrument -> RTC in dollars
        - srcosts: Dict mapping instrument -> SRcost (diagnostic)

    Formulas:
        - trade_notional = |trade_weight| * capital
        - RTC = trade_notional * (spread_frac + taker_fee_frac)
        - annual_vol = daily_vol * sqrt(BUSINESS_DAYS_IN_YEAR)
        - SRcost = RTC / (annual_vol * capital)  [diagnostic only]

    Notes:
        - RTC (Round-Trip Cost): actual dollar cost, subtracted from PnL
        - SRcost: diagnostic metric for cost analysis (not used in trading decisions)
    """
    rtc_costs = {}
    srcosts = {}

    for instrument, trade_weight in trades.items():
        if abs(trade_weight) < 1e-10:
            # No trade, no cost
            rtc_costs[instrument] = 0.0
            srcosts[instrument] = 0.0
            continue

        # Trade notional (absolute value)
        trade_notional = abs(trade_weight) * capital

        # Get cost parameters
        inst_meta = meta.get(instrument, {})
        spread_frac = inst_meta.get('spread_frac', 0.0003)  # Default 3 bps
        taker_fee_frac = inst_meta.get('taker_fee_frac', 0.0004)  # Default 4 bps

        # RTC (dollars)
        rtc = trade_notional * (spread_frac + taker_fee_frac)
        rtc_costs[instrument] = rtc

        # SRcost (diagnostic)
        # This is the cost expressed as a fraction of annual position volatility
        daily_vol = daily_vols.get(instrument, 0.0)
        price = prices.get(instrument, 1.0)

        if daily_vol > 0 and price > 0:
            # Annual volatility (percentage)
            annual_vol_pct = (daily_vol / price) * np.sqrt(BUSINESS_DAYS_IN_YEAR)

            # Position size in capital terms
            position_notional = abs(trade_weight) * capital

            # Annual volatility in dollars
            annual_vol_dollars = position_notional * annual_vol_pct

            if annual_vol_dollars > 0:
                # SRcost = cost / annual volatility
                srcost = rtc / annual_vol_dollars
            else:
                srcost = 0.0
        else:
            srcost = 0.0

        srcosts[instrument] = srcost

    return rtc_costs, srcosts


def execute_trades(
    target_weights_df: pd.DataFrame,
    current_weights_df: pd.DataFrame,
    prices_df: pd.DataFrame,
    meta_df: pd.DataFrame,
    eligibility_df: pd.DataFrame,
    daily_vols_df: pd.DataFrame,
    capital: float,
    buffer_frac: float,
    state_df: pd.DataFrame = None  # NEW: Phase 2
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    Execute trades with buffers and cost calculation

    Phase 2 Addition:
    - If state_df provided and state == BANNED_FLATTEN:
      Force trade to target regardless of buffer (immediate flatten)

    Args:
        target_weights_df: DataFrame with date index and instrument columns (target weights)
        current_weights_df: DataFrame with date index and instrument columns (current weights)
        prices_df: DataFrame with date index and instrument columns (prices)
        meta_df: DataFrame with MultiIndex (date, instrument) containing cost params
        eligibility_df: DataFrame with date index and instrument columns (eligibility)
        daily_vols_df: DataFrame with date index and instrument columns (daily vols)
        capital: Total capital
        buffer_frac: Buffer fraction (e.g., 0.1)
        state_df: Optional DataFrame with instrument states (Phase 2)

    Returns:
        Tuple of (trades_df, rtc_costs_df, srcosts_df):
        - trades_df: Actual trades executed (weights)
        - rtc_costs_df: RTC costs in dollars
        - srcosts_df: SRcost (diagnostic)

    Notes:
        - Iterates through each date
        - Applies buffers and eligibility checks
        - Phase 2: BANNED_FLATTEN bypass forces immediate trade to target
        - Calculates costs for executed trades
    """
    dates = target_weights_df.index
    instruments = list(target_weights_df.columns)

    trades_data = {inst: [] for inst in instruments}
    rtc_data = {inst: [] for inst in instruments}
    srcost_data = {inst: [] for inst in instruments}

    for date in dates:
        # Get data for this date
        target = target_weights_df.loc[date].to_dict()
        current = current_weights_df.loc[date].to_dict()
        prices = prices_df.loc[date].to_dict()
        eligible = eligibility_df.loc[date].to_dict()
        daily_vols = daily_vols_df.loc[date].to_dict()

        # Get metadata (spread, fees)
        meta = {}
        for inst in instruments:
            try:
                inst_meta = meta_df.loc[(date, inst)]
                meta[inst] = {
                    'spread_frac': inst_meta['spread_frac'],
                    'taker_fee_frac': inst_meta['taker_fee_frac']
                }
            except KeyError:
                # Use defaults if metadata missing
                meta[inst] = {
                    'spread_frac': 0.0003,
                    'taker_fee_frac': 0.0004
                }

        # Phase 2: Check for BANNED_FLATTEN bypass (before buffer logic)
        # If instrument is BANNED_FLATTEN, force trade to target regardless of buffer
        if state_df is not None:
            from systems.crypto_perps.universe import InstrumentState

            # Get states for this date
            states = state_df.loc[date].to_dict()

            # Override trades for BANNED_FLATTEN instruments
            for inst in instruments:
                state = states.get(inst, InstrumentState.ACTIVE.value)
                if state == InstrumentState.BANNED_FLATTEN.value:
                    # Force trade to target (bypass buffer)
                    target_val = target.get(inst, 0.0)
                    current_val = current.get(inst, 0.0)
                    desired_trade = target_val - current_val

                    # Only record trade if meaningful (avoid spurious trades when already flat)
                    if abs(desired_trade) > 1e-10:
                        target[inst] = target_val  # Ensure target is set for cost calc
                        # Mark as eligible for trading (override ineligibility)
                        eligible[inst] = True
                    else:
                        # Already flat, no trade needed
                        target[inst] = current_val  # No delta

        # Apply trading buffers (for non-BANNED instruments)
        trades = apply_trading_buffer(
            target_weights=target,
            current_weights=current,
            buffer_frac=buffer_frac,
            prices=prices,
            daily_vols=daily_vols,
            capital=capital,
            eligible=eligible
        )

        # Phase 2: Override trades for BANNED_FLATTEN (explicit bypass)
        # This ensures immediate execution even if buffer would prevent it
        if state_df is not None:
            from systems.crypto_perps.universe import InstrumentState

            states = state_df.loc[date].to_dict()
            for inst in instruments:
                state = states.get(inst, InstrumentState.ACTIVE.value)
                if state == InstrumentState.BANNED_FLATTEN.value:
                    target_val = target.get(inst, 0.0)
                    current_val = current.get(inst, 0.0)
                    desired_trade = target_val - current_val

                    # Force trade regardless of buffer
                    if abs(desired_trade) > 1e-10:
                        trades[inst] = desired_trade
                    else:
                        trades[inst] = 0.0

        # Calculate costs for all trades (including bypass trades)
        rtc_costs, srcosts = calculate_trade_costs(
            trades=trades,
            prices=prices,
            meta=meta,
            capital=capital,
            daily_vols=daily_vols
        )

        # Store results
        for inst in instruments:
            trades_data[inst].append(trades.get(inst, 0.0))
            rtc_data[inst].append(rtc_costs.get(inst, 0.0))
            srcost_data[inst].append(srcosts.get(inst, 0.0))

    # Create DataFrames
    trades_df = pd.DataFrame(trades_data, index=dates)
    rtc_costs_df = pd.DataFrame(rtc_data, index=dates)
    srcosts_df = pd.DataFrame(srcost_data, index=dates)

    return trades_df, rtc_costs_df, srcosts_df
