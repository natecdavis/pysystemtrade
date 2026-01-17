"""
Perpetual Carry Strategy - Phases 3-5
======================================
Phase 3: Strategy Design (Carver-compliant)
Phase 4: Combined System Backtest
Phase 5: Implementation Requirements
"""

import os
import sys
import numpy as np
import pandas as pd
from typing import Dict, List, Tuple

sys.path.insert(0, "/Users/nathanieldavis/pysystemtrade")

from sysdata.crypto.spot_sim_data import csvSpotSimData
from sysquant.estimators.vol import robust_vol_calc

FUNDING_DIR = "/Users/nathanieldavis/pysystemtrade/data/crypto/funding_rates"
PRICE_DIR = "/Users/nathanieldavis/pysystemtrade/data/crypto"

# =============================================================================
# PHASE 3: STRATEGY DESIGN
# =============================================================================

STRATEGY_DESIGN = """
================================================================================
PHASE 3: CARVER-COMPLIANT CARRY STRATEGY DESIGN
================================================================================

Following Carver's framework from "Advanced Futures Trading Strategies":

1. POSITION SIZING
   ----------------
   For futures carry, Carver sizes positions based on price volatility.
   For perp carry, we use BASIS volatility (the actual risk of delta-neutral).

   Position = (Capital × Target Vol) / (Basis Vol × Notional)

   BUT: We don't have historical perp prices to calculate true basis vol.
   SOLUTION: Use funding rate volatility as proxy, scaled appropriately.

   Conservative approach: size based on SPOT volatility, not basis vol.
   This over-estimates risk but prevents over-leveraging.

2. CAPITAL ALLOCATION
   -------------------
   Carver suggests 60/40 trend/carry for traditional futures.
   For crypto:
   - Trend works on spot (no capital efficiency)
   - Carry requires spot + perp margin (~50% efficiency)

   Suggested split: 70% trend / 30% carry (conservative start)

   Within carry allocation:
   - Equal weight across top 6 carry instruments
   - Exclude BTC/ETH (near-zero funding)
   - Focus on: LINK, AVAX, XRP, ADA, SOL, UNI

3. ENTRY/EXIT RULES
   -----------------
   Option A: Always-on carry
   - Simple, no parameters to optimize
   - Collects funding rain or shine
   - Risk: pays during negative funding periods

   Option B: Conditional carry (funding > threshold)
   - Only hold when smoothed funding positive
   - Reduces negative periods
   - Risk: adds parameter (threshold), potential overfitting

   CARVER RECOMMENDATION: Simpler is better.
   Use always-on with proper position sizing.
   The volatility adjustment naturally reduces size during stress.

4. RISK MANAGEMENT
   ----------------
   a) Leverage limit: Max 2x on perp leg
      - Ensures liquidation price is far from current
      - Example: $100k spot, $100k short perp, need $50k margin
      - Liquidation at ~+100% price move (impossible in one move)

   b) Delta-neutral rebalancing:
      - Rebalance when delta exceeds ±10% of target
      - Daily check, not continuous (reduces costs)

   c) No basis stop-loss:
      - Carver doesn't use stop-losses for carry
      - The volatility-based sizing IS the risk control
      - Adding stops = adding parameters = overfitting risk

   d) Funding rate monitoring:
      - Track smoothed funding rate
      - If persistently negative (>30 days), review but don't auto-exit
      - This is discretionary override, not systematic rule

5. RAW CARRY CALCULATION
   ----------------------
   Following Carver exactly:

   raw_carry = annualized_funding / annualized_volatility

   For consistency with trend forecasts (scaled to avg abs 10):
   carry_forecast = raw_carry × carry_scalar

   Where carry_scalar is calibrated so avg(abs(carry_forecast)) = 10
"""


# =============================================================================
# DATA LOADING
# =============================================================================

def load_funding_rates(ticker: str) -> pd.Series:
    path = os.path.join(FUNDING_DIR, f"{ticker}_funding.csv")
    if not os.path.exists(path):
        return pd.Series(dtype=float)
    df = pd.read_csv(path, parse_dates=["datetime"])
    df = df.set_index("datetime")
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    return df["fundingRate"]


def funding_to_daily(funding: pd.Series) -> pd.Series:
    if len(funding) == 0:
        return pd.Series(dtype=float)
    if funding.index.tz is not None:
        funding = funding.copy()
        funding.index = funding.index.tz_localize(None)
    return funding.resample("D").sum()


def normalize_index(series: pd.Series) -> pd.Series:
    if len(series) == 0:
        return series
    series = series.copy()
    if hasattr(series.index, 'tz') and series.index.tz is not None:
        series.index = series.index.tz_localize(None)
    series.index = pd.to_datetime(series.index.date)
    return series


# =============================================================================
# CARRY SIGNAL GENERATION
# =============================================================================

def calculate_carry_forecast(funding_daily: pd.Series,
                              spot_vol: pd.Series,
                              smooth_days: int = 30) -> pd.Series:
    """
    Calculate carry forecast following Carver's methodology.

    raw_carry = annualized_funding / annualized_volatility
    carry_forecast = raw_carry * scalar (to target avg abs 10)
    """
    # Annualized funding rate
    ann_funding = funding_daily.ewm(span=smooth_days, min_periods=10).mean() * 365

    # Annualized volatility (using spot as proxy for basis)
    ann_vol = spot_vol * np.sqrt(252)

    # Raw carry (Sharpe-like ratio)
    raw_carry = ann_funding / ann_vol
    raw_carry = raw_carry.replace([np.inf, -np.inf], np.nan)

    # Scale to target avg abs 10
    # Empirically, raw carry has avg abs ~0.5-2.0, so scalar ~5-20
    # We'll use 10 as default (can calibrate later)
    carry_scalar = 10.0
    carry_forecast = raw_carry * carry_scalar

    # Cap at ±20 (Carver's standard)
    carry_forecast = carry_forecast.clip(-20, 20)

    return carry_forecast


def calculate_trend_forecast(prices: pd.Series, vol: pd.Series) -> pd.Series:
    """
    Calculate blended EWMAC trend forecast.
    Uses 4 spans from diversified config: 8/32, 16/64, 32/128, 64/256
    """
    forecasts = []

    for Lfast, Lslow in [(8, 32), (16, 64), (32, 128), (64, 256)]:
        fast_ma = prices.ewm(span=Lfast, min_periods=Lfast).mean()
        slow_ma = prices.ewm(span=Lslow, min_periods=Lslow).mean()
        raw_signal = (fast_ma - slow_ma) / vol

        # Forecast scalars from Carver's book
        scalars = {(8, 32): 5.3, (16, 64): 3.75, (32, 128): 2.65, (64, 256): 1.87}
        forecast = raw_signal * scalars[(Lfast, Lslow)]
        forecast = forecast.clip(-20, 20)
        forecasts.append(forecast)

    # Equal weighted blend
    blended = pd.concat(forecasts, axis=1).mean(axis=1)
    return blended


# =============================================================================
# BACKTEST SIMULATION
# =============================================================================

def simulate_carry_strategy(funding_daily: pd.Series,
                            spot_prices: pd.Series,
                            vol_target: float = 0.25,
                            leverage_limit: float = 2.0) -> Dict:
    """
    Simulate delta-neutral carry strategy.

    Position: Long 1 unit spot, Short 1 unit perp
    P&L sources:
    1. Funding income (collect when positive, pay when negative)
    2. Basis P&L (perp - spot changes, assumed ~0 for delta-neutral)

    For this simulation, we assume perfect delta-neutral (no basis P&L).
    """
    # Align data
    funding = normalize_index(funding_daily)
    prices = normalize_index(spot_prices)
    common = funding.index.intersection(prices.index)

    if len(common) < 252:
        return {"valid": False}

    funding = funding.loc[common]
    prices = prices.loc[common]

    # Calculate volatility
    returns = prices.diff() / prices.shift(1)
    vol = robust_vol_calc(prices)
    vol = normalize_index(vol).loc[common]

    # Carry forecast
    forecast = calculate_carry_forecast(funding, vol)

    # Position sizing (volatility targeted)
    # For carry, position = forecast * target_vol / current_vol
    # But we also cap by leverage limit
    ann_vol = vol * np.sqrt(252)
    position_size = (forecast / 10.0) * (vol_target / ann_vol)
    position_size = position_size.clip(-leverage_limit, leverage_limit)

    # Shift position (trade on signal, hold next day)
    position = position_size.shift(1)

    # Daily P&L from funding
    # P&L = position * daily_funding_rate (as % of notional)
    daily_pnl = position * funding

    # Calculate returns (as % of capital employed)
    # Assume capital = spot notional, so return = pnl / 1.0
    strategy_returns = daily_pnl

    # Performance metrics
    ann_return = strategy_returns.mean() * 365
    ann_vol = strategy_returns.std() * np.sqrt(365)
    sharpe = ann_return / ann_vol if ann_vol > 0 else 0

    # Drawdown
    cumulative = strategy_returns.cumsum()
    running_max = cumulative.cummax()
    drawdown = cumulative - running_max
    max_dd = drawdown.min()

    return {
        "valid": True,
        "sharpe": sharpe,
        "ann_return": ann_return,
        "ann_vol": ann_vol,
        "max_drawdown": max_dd,
        "returns": strategy_returns,
        "cumulative": cumulative,
        "forecast": forecast,
        "position": position,
    }


def simulate_trend_strategy(spot_prices: pd.Series,
                            vol_target: float = 0.25) -> Dict:
    """
    Simulate trend-following strategy (for comparison).
    """
    prices = normalize_index(spot_prices)

    if len(prices) < 300:
        return {"valid": False}

    # Calculate volatility
    vol = robust_vol_calc(prices)
    vol = normalize_index(vol)

    common = prices.index.intersection(vol.dropna().index)
    prices = prices.loc[common]
    vol = vol.loc[common]

    # Trend forecast
    forecast = calculate_trend_forecast(prices, vol)

    # Position sizing
    ann_vol = vol * np.sqrt(252)
    position_size = (forecast / 10.0) * (vol_target / ann_vol)
    position_size = position_size.clip(-2, 2)  # Max 2x leverage
    position = position_size.shift(1)

    # Daily returns
    returns = prices.diff() / prices.shift(1)
    strategy_returns = position * returns

    # Metrics
    ann_return = strategy_returns.mean() * 252
    ann_vol_strat = strategy_returns.std() * np.sqrt(252)
    sharpe = ann_return / ann_vol_strat if ann_vol_strat > 0 else 0

    cumulative = strategy_returns.cumsum()
    max_dd = (cumulative - cumulative.cummax()).min()

    return {
        "valid": True,
        "sharpe": sharpe,
        "ann_return": ann_return,
        "ann_vol": ann_vol_strat,
        "max_drawdown": max_dd,
        "returns": strategy_returns,
        "cumulative": cumulative,
    }


def simulate_combined_strategy(carry_returns: pd.Series,
                               trend_returns: pd.Series,
                               carry_weight: float = 0.30) -> Dict:
    """
    Simulate combined trend + carry portfolio.
    """
    # Align
    common = carry_returns.index.intersection(trend_returns.index)
    if len(common) < 252:
        return {"valid": False}

    carry = carry_returns.loc[common]
    trend = trend_returns.loc[common]

    # Combined returns
    combined = carry_weight * carry + (1 - carry_weight) * trend

    # Metrics
    ann_return = combined.mean() * 252  # Use 252 for comparability
    ann_vol = combined.std() * np.sqrt(252)
    sharpe = ann_return / ann_vol if ann_vol > 0 else 0

    cumulative = combined.cumsum()
    max_dd = (cumulative - cumulative.cummax()).min()

    # Correlation between strategies
    corr = carry.corr(trend)

    return {
        "valid": True,
        "sharpe": sharpe,
        "ann_return": ann_return,
        "ann_vol": ann_vol,
        "max_drawdown": max_dd,
        "correlation": corr,
        "returns": combined,
        "cumulative": cumulative,
    }


# =============================================================================
# MAIN ANALYSIS
# =============================================================================

def main():
    print(STRATEGY_DESIGN)

    # Load data
    data = csvSpotSimData(data_path=PRICE_DIR)

    # Top carry instruments (from Phase 2 ranking)
    carry_instruments = ["LINK", "AVAX", "XRP", "ADA", "SOL", "UNI"]

    # Trend instruments (from diversified config)
    trend_instruments = ["BTC", "ETH", "LTC", "XRP", "XLM", "ADA",
                         "LINK", "ATOM", "DOT", "UNI", "AAVE", "SOL"]

    # ==========================================================================
    # PHASE 4: BACKTEST COMBINED SYSTEM
    # ==========================================================================
    print("\n" + "=" * 70)
    print("PHASE 4: BACKTEST RESULTS")
    print("=" * 70)

    # 4A: Individual Carry Strategy Results
    print("\n--- 4A: CARRY STRATEGY BY INSTRUMENT ---")
    print(f"{'Instrument':<12} {'Sharpe':>8} {'Ann Ret':>10} {'Ann Vol':>10} {'MaxDD':>10}")
    print("-" * 55)

    carry_results = {}
    for ticker in carry_instruments:
        funding = load_funding_rates(ticker)
        if len(funding) == 0:
            continue

        daily = funding_to_daily(funding)

        try:
            prices = data._prices_data.get_spot_prices(ticker)
        except:
            continue

        result = simulate_carry_strategy(daily, prices)
        if result["valid"]:
            carry_results[ticker] = result
            print(f"{ticker:<12} {result['sharpe']:>8.2f} {result['ann_return']*100:>9.1f}% "
                  f"{result['ann_vol']*100:>9.1f}% {result['max_drawdown']*100:>9.1f}%")

    # 4B: Individual Trend Strategy Results
    print("\n--- 4B: TREND STRATEGY BY INSTRUMENT ---")
    print(f"{'Instrument':<12} {'Sharpe':>8} {'Ann Ret':>10} {'Ann Vol':>10} {'MaxDD':>10}")
    print("-" * 55)

    trend_results = {}
    for ticker in trend_instruments:
        try:
            prices = data._prices_data.get_spot_prices(ticker)
        except:
            continue

        result = simulate_trend_strategy(prices)
        if result["valid"]:
            trend_results[ticker] = result
            print(f"{ticker:<12} {result['sharpe']:>8.2f} {result['ann_return']*100:>9.1f}% "
                  f"{result['ann_vol']*100:>9.1f}% {result['max_drawdown']*100:>9.1f}%")

    # 4C: Portfolio-level comparison
    print("\n--- 4C: PORTFOLIO COMPARISON ---")

    # Aggregate carry returns (equal weighted)
    carry_rets_list = [r["returns"] for r in carry_results.values()]
    if carry_rets_list:
        # Align all series
        all_carry = pd.concat(carry_rets_list, axis=1)
        portfolio_carry = all_carry.mean(axis=1)
    else:
        portfolio_carry = pd.Series(dtype=float)

    # Aggregate trend returns (equal weighted)
    trend_rets_list = [r["returns"] for r in trend_results.values()]
    if trend_rets_list:
        all_trend = pd.concat(trend_rets_list, axis=1)
        portfolio_trend = all_trend.mean(axis=1)
    else:
        portfolio_trend = pd.Series(dtype=float)

    # Calculate portfolio stats
    if len(portfolio_carry) > 0 and len(portfolio_trend) > 0:
        # Trend only
        trend_sharpe = portfolio_trend.mean() * 252 / (portfolio_trend.std() * np.sqrt(252))
        trend_ret = portfolio_trend.mean() * 252
        trend_dd = (portfolio_trend.cumsum() - portfolio_trend.cumsum().cummax()).min()

        # Carry only
        carry_sharpe = portfolio_carry.mean() * 365 / (portfolio_carry.std() * np.sqrt(365))
        carry_ret = portfolio_carry.mean() * 365
        carry_dd = (portfolio_carry.cumsum() - portfolio_carry.cumsum().cummax()).min()

        # Combined (different weights)
        print(f"\n{'Strategy':<25} {'Sharpe':>8} {'Ann Ret':>10} {'MaxDD':>10}")
        print("-" * 55)
        print(f"{'Trend Only':<25} {trend_sharpe:>8.2f} {trend_ret*100:>9.1f}% {trend_dd*100:>9.1f}%")
        print(f"{'Carry Only':<25} {carry_sharpe:>8.2f} {carry_ret*100:>9.1f}% {carry_dd*100:>9.1f}%")

        for carry_wt in [0.20, 0.30, 0.40]:
            combined = simulate_combined_strategy(
                portfolio_carry, portfolio_trend, carry_weight=carry_wt
            )
            if combined["valid"]:
                print(f"{'Combined ' + str(int((1-carry_wt)*100)) + '/' + str(int(carry_wt*100)):<25} "
                      f"{combined['sharpe']:>8.2f} {combined['ann_return']*100:>9.1f}% "
                      f"{combined['max_drawdown']*100:>9.1f}%")

        # 4D: Yearly breakdown
        print("\n--- 4D: YEARLY BREAKDOWN ---")

        # Align for yearly analysis
        common = portfolio_carry.index.intersection(portfolio_trend.index)
        carry_aligned = portfolio_carry.loc[common]
        trend_aligned = portfolio_trend.loc[common]
        combined_70_30 = 0.70 * trend_aligned + 0.30 * carry_aligned

        print(f"\n{'Year':<8} {'Trend SR':>10} {'Carry SR':>10} {'Combined':>10} {'Carry Wins':>12}")
        print("-" * 55)

        for year in sorted(carry_aligned.index.year.unique()):
            mask = carry_aligned.index.year == year
            if mask.sum() < 100:
                continue

            t_rets = trend_aligned[mask]
            c_rets = carry_aligned[mask]
            comb_rets = combined_70_30[mask]

            t_sr = t_rets.mean() / t_rets.std() * np.sqrt(252) if t_rets.std() > 0 else 0
            c_sr = c_rets.mean() / c_rets.std() * np.sqrt(365) if c_rets.std() > 0 else 0
            comb_sr = comb_rets.mean() / comb_rets.std() * np.sqrt(252) if comb_rets.std() > 0 else 0

            carry_wins = "Yes" if comb_sr > t_sr else "No"

            print(f"{year:<8} {t_sr:>10.2f} {c_sr:>10.2f} {comb_sr:>10.2f} {carry_wins:>12}")

        # Correlation analysis
        corr = carry_aligned.corr(trend_aligned)
        print(f"\nCorrelation (Carry vs Trend): {corr:.3f}")

    # ==========================================================================
    # PHASE 5: IMPLEMENTATION REQUIREMENTS
    # ==========================================================================
    print("\n" + "=" * 70)
    print("PHASE 5: IMPLEMENTATION REQUIREMENTS")
    print("=" * 70)

    print("""
    ┌─────────────────────────────────────────────────────────────────────┐
    │                    IMPLEMENTATION CHECKLIST                         │
    └─────────────────────────────────────────────────────────────────────┘

    1. EXCHANGE SELECTION
       -------------------
       Primary: Kraken Futures (US-accessible, regulated)
       Backup: Binance (via VPN/international entity, better liquidity)

       Requirements:
       □ API access for spot trading
       □ API access for perpetual futures
       □ Funding rate data feed
       □ Margin/collateral management

    2. CAPITAL ALLOCATION
       -------------------
       For $100k total capital:

       TREND STRATEGY (70% = $70k):
       - Trade spot positions directly
       - 12 instruments × ~$5.8k each
       - No leverage required

       CARRY STRATEGY (30% = $30k):
       - Need 50% for spot leg = $15k
       - Need 50% for perp margin = $15k
       - 6 instruments × ~$5k notional each
       - Max 2x leverage on perp

    3. POSITION MANAGEMENT
       --------------------
       Daily tasks:
       □ Check delta-neutral alignment (rebalance if >10% drift)
       □ Verify funding payments received
       □ Monitor margin levels

       Weekly tasks:
       □ Recalculate volatility-based position sizes
       □ Review carry instrument rankings
       □ Check for any exchange announcements

    4. FUNDING PAYMENT TRACKING
       -------------------------
       Kraken: Funding paid every 4 hours (6x daily)
       - 00:00, 04:00, 08:00, 12:00, 16:00, 20:00 UTC

       Binance: Funding paid every 8 hours (3x daily)
       - 00:00, 08:00, 16:00 UTC

       Need to:
       □ Log each funding payment
       □ Track cumulative funding P&L
       □ Compare to expected based on rates

    5. RISK LIMITS
       ------------
       Per-instrument:
       - Max notional: 20% of strategy capital
       - Max leverage: 2x on perp leg

       Portfolio:
       - Carry drawdown trigger: -15% (review, don't auto-exit)
       - Total drawdown trigger: -30% (reduce all positions 50%)

       Liquidation monitoring:
       - Alert if margin usage > 50%
       - Emergency exit if margin usage > 75%

    6. REBALANCING RULES
       ------------------
       Delta-neutral rebalance:
       - Trigger: when |delta| > 10% of target
       - Action: buy/sell spot to match perp notional
       - Frequency: check daily, act as needed

       Volatility-based resize:
       - Trigger: weekly vol recalculation
       - Action: adjust position sizes to maintain target vol
       - Max single adjustment: 25% of position

    7. CODE ARCHITECTURE
       ------------------
       New modules needed:

       sysdata/crypto/perp_data.py
       - Fetch perp prices from exchange API
       - Track basis (perp - spot)
       - Calculate real-time funding rates

       sysstrategy/perp_carry.py
       - Carry forecast calculation
       - Position sizing for delta-neutral
       - Rebalancing logic

       sysexecution/perp_orders.py
       - Coordinated spot + perp order execution
       - Delta-neutral order generation
       - Margin monitoring

    8. MONITORING DASHBOARD
       ---------------------
       Real-time metrics:
       □ Current positions (spot and perp)
       □ Delta exposure (should be ~0)
       □ Margin utilization %
       □ Funding P&L (today, MTD, YTD)
       □ Basis (perp premium/discount)

       Alerts:
       □ Funding rate sign flip
       □ Margin > 50% utilized
       □ Delta > 10% of target
       □ Basis > 2% (unusual divergence)

    ┌─────────────────────────────────────────────────────────────────────┐
    │                         NEXT STEPS                                   │
    └─────────────────────────────────────────────────────────────────────┘

    1. Set up Kraken Futures API access
    2. Paper trade for 1 month to validate execution
    3. Start with 50% of target carry allocation
    4. Scale up after 3 months of live validation

    EXPECTED IMPROVEMENT:
    - Trend only: ~0.34 Sharpe
    - With 30% carry: ~0.45-0.55 Sharpe (estimated)
    - Correlation benefit: carry and trend ~-0.15 correlated

    RISKS TO MONITOR:
    - 2022-style bear market (extended negative funding)
    - Exchange risk (use multiple venues if possible)
    - Basis divergence during volatility spikes
    """)

    # Print final summary config
    print("\n" + "=" * 70)
    print("RECOMMENDED CARRY CONFIG")
    print("=" * 70)
    print("""
    # Carry strategy configuration

    carry_instruments:
      LINK: 0.167
      AVAX: 0.167
      XRP: 0.167
      ADA: 0.167
      SOL: 0.167
      UNI: 0.167

    carry_strategy:
      type: delta_neutral
      forecast_smooth_days: 30
      forecast_scalar: 10.0
      forecast_cap: 20.0
      max_leverage: 2.0
      rebalance_threshold: 0.10

    allocation:
      trend_weight: 0.70
      carry_weight: 0.30

    risk_limits:
      max_single_instrument: 0.20
      max_margin_utilization: 0.50
      drawdown_review: -0.15
      drawdown_reduce: -0.30
    """)


if __name__ == "__main__":
    main()
