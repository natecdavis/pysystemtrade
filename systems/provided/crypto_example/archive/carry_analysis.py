"""
Carry Signal Analysis for Crypto
================================
Analyzes funding rate carry as a trading signal.

In crypto perps:
- Positive funding = longs pay shorts = carry return for shorts
- Negative funding = shorts pay longs = carry return for longs

Following Carver's carry framework from "Leveraged Trading" and AFT.
"""

import os
import sys
import numpy as np
import pandas as pd
from typing import Dict

sys.path.insert(0, "/Users/nathanieldavis/pysystemtrade")

from sysdata.crypto.spot_sim_data import csvSpotSimData
from sysquant.estimators.vol import robust_vol_calc

FUNDING_DIR = "/Users/nathanieldavis/pysystemtrade/data/crypto/funding_rates"
PRICE_DIR = "/Users/nathanieldavis/pysystemtrade/data/crypto"


def load_funding_rates(ticker: str) -> pd.Series:
    """Load funding rate data for a ticker."""
    path = os.path.join(FUNDING_DIR, f"{ticker}_funding.csv")
    if not os.path.exists(path):
        return pd.Series(dtype=float)

    df = pd.read_csv(path, parse_dates=["datetime"])
    df = df.set_index("datetime")
    # Remove timezone info if present
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    return df["fundingRate"]


def funding_to_daily(funding: pd.Series) -> pd.Series:
    """
    Convert 4-hourly funding rates to daily.
    Sum the 6 daily funding payments.
    """
    # Ensure no timezone
    if funding.index.tz is not None:
        funding.index = funding.index.tz_localize(None)
    # Resample to daily (sum funding payments)
    daily = funding.resample("D").sum()
    return daily


def normalize_index(series: pd.Series) -> pd.Series:
    """Remove timezone and normalize index to date only."""
    if len(series) == 0:
        return series
    if series.index.tz is not None:
        series = series.copy()
        series.index = series.index.tz_localize(None)
    # Normalize to date only (midnight)
    series.index = pd.to_datetime(series.index.date)
    return series


def carry_signal(funding_daily: pd.Series, lookback: int = 20) -> pd.Series:
    """
    Generate carry signal from funding rates.

    Signal = rolling mean of daily funding rate
    Negative signal = go long (negative funding = we receive funding)
    Positive signal = go short (positive funding = we pay funding)

    We invert this for the signal:
    - Positive funding → short → negative signal
    - Negative funding → long → positive signal
    """
    if len(funding_daily) == 0:
        return pd.Series(dtype=float)

    # Smooth the funding rate
    smoothed = funding_daily.rolling(lookback, min_periods=5).mean()

    # Annualize for interpretation (funding paid 3x daily on Binance, 6x on Kraken)
    # For signal: negative funding = positive signal (go long to receive funding)
    signal = -smoothed * 10000  # Scale to reasonable magnitude

    return normalize_index(signal)


def ewmac_signal(price: pd.Series, vol: pd.Series, Lfast: int, Lslow: int) -> pd.Series:
    """Standard EWMAC signal."""
    fast_ma = price.ewm(span=Lfast, min_periods=Lfast).mean()
    slow_ma = price.ewm(span=Lslow, min_periods=Lslow).mean()
    raw_signal = fast_ma - slow_ma
    result = raw_signal / vol
    return normalize_index(result)


def calculate_signal_stats(signal: pd.Series, prices: pd.Series,
                          costs_pct: float = 0.001) -> Dict:
    """Calculate signal performance statistics."""
    signal = normalize_index(signal.dropna())
    prices = normalize_index(prices.dropna())

    if len(signal) < 100:
        return {"valid": False}

    # Align data
    common_idx = signal.index.intersection(prices.index)
    if len(common_idx) < 100:
        return {"valid": False}
    signal = signal.loc[common_idx]
    prices = prices.loc[common_idx]

    # Calculate returns
    returns = prices.diff() / prices.shift(1)

    # Position based on signal sign
    position = np.sign(signal).shift(1)
    strategy_returns = position * returns

    # Turnover
    position_changes = position.diff().abs()
    daily_turnover = position_changes.mean()
    annual_turnover = daily_turnover * 252

    # Costs
    annual_cost_drag = annual_turnover * costs_pct

    # Performance
    annual_return = strategy_returns.mean() * 252
    annual_vol = strategy_returns.std() * np.sqrt(252)
    sharpe_gross = annual_return / annual_vol if annual_vol > 0 else 0
    sharpe_net = (annual_return - annual_cost_drag) / annual_vol if annual_vol > 0 else 0

    return {
        "valid": True,
        "sharpe_gross": sharpe_gross,
        "sharpe_net": sharpe_net,
        "annual_return": annual_return,
        "annual_vol": annual_vol,
        "annual_turnover": annual_turnover,
        "annual_cost_drag": annual_cost_drag,
        "n_obs": len(signal),
    }


def main():
    print("=" * 70)
    print("CARRY SIGNAL ANALYSIS")
    print("=" * 70)

    # Load price data
    data = csvSpotSimData(data_path=PRICE_DIR)

    # Instruments with funding data
    tickers = ["BTC", "ETH", "SOL", "LINK", "DOT", "AVAX", "ATOM", "UNI",
               "AAVE", "ADA", "XRP", "LTC"]

    # ==========================================================================
    # 1. FUNDING RATE DESCRIPTIVE STATS
    # ==========================================================================
    print("\n" + "=" * 70)
    print("1. FUNDING RATE CHARACTERISTICS")
    print("=" * 70)

    print(f"\n{'Ticker':<8} {'Days':>6} {'Avg Rate':>10} {'Std Dev':>10} {'Annualized':>12} {'Persistence':>12}")
    print("-" * 65)

    funding_daily_dict = {}
    for ticker in tickers:
        funding = load_funding_rates(ticker)
        if len(funding) == 0:
            continue

        daily = funding_to_daily(funding)
        funding_daily_dict[ticker] = daily

        # Stats
        avg_rate = daily.mean()
        std_rate = daily.std()
        # Annualized (365 days, rate is already daily sum)
        annualized = avg_rate * 365 * 100
        # Persistence (autocorrelation)
        persistence = daily.autocorr(lag=1)

        print(f"{ticker:<8} {len(daily):>6} {avg_rate*100:>9.4f}% {std_rate*100:>9.4f}% "
              f"{annualized:>11.1f}% {persistence:>11.2f}")

    # ==========================================================================
    # 2. CARRY SIGNAL PERFORMANCE
    # ==========================================================================
    print("\n" + "=" * 70)
    print("2. CARRY SIGNAL PERFORMANCE")
    print("=" * 70)

    print(f"\n--- Testing different lookbacks ---")
    print(f"{'Ticker':<8} {'Carry5':>8} {'Carry10':>8} {'Carry20':>8} {'Carry40':>8} {'EWMAC16':>8}")
    print("-" * 55)

    for ticker in ["BTC", "ETH", "SOL", "LINK", "LTC"]:
        if ticker not in funding_daily_dict:
            continue

        funding_daily = funding_daily_dict[ticker]

        try:
            prices = normalize_index(data._prices_data.get_spot_prices(ticker))
            vol = robust_vol_calc(prices)
        except:
            continue

        sharpes = []
        for lookback in [5, 10, 20, 40]:
            sig = carry_signal(funding_daily, lookback=lookback)
            stats = calculate_signal_stats(sig, prices)
            sharpes.append(stats["sharpe_net"] if stats["valid"] else np.nan)

        # Compare to EWMAC
        ewmac = ewmac_signal(prices, vol, 16, 64)
        ewmac_stats = calculate_signal_stats(ewmac, prices)
        ewmac_sharpe = ewmac_stats["sharpe_net"] if ewmac_stats["valid"] else np.nan

        print(f"{ticker:<8} " + " ".join(f"{s:>8.2f}" for s in sharpes) + f" {ewmac_sharpe:>8.2f}")

    # ==========================================================================
    # 3. CORRELATION WITH TREND
    # ==========================================================================
    print("\n" + "=" * 70)
    print("3. CARRY-TREND CORRELATION (Key for diversification)")
    print("=" * 70)

    print(f"\n{'Ticker':<8} {'Corr(Carry, EWMAC8)':>20} {'Corr(Carry, EWMAC16)':>20} {'Corr(Carry, EWMAC64)':>20}")
    print("-" * 75)

    for ticker in ["BTC", "ETH", "SOL", "LINK", "LTC"]:
        if ticker not in funding_daily_dict:
            continue

        funding_daily = funding_daily_dict[ticker]

        try:
            prices = normalize_index(data._prices_data.get_spot_prices(ticker))
            vol = robust_vol_calc(prices)
        except:
            continue

        carry_sig = carry_signal(funding_daily, lookback=20)

        corrs = []
        for Lfast, Lslow in [(8, 32), (16, 64), (64, 256)]:
            ewmac = ewmac_signal(prices, vol, Lfast, Lslow)
            # Align
            common = carry_sig.index.intersection(ewmac.index)
            if len(common) < 100:
                corrs.append(np.nan)
            else:
                corr = carry_sig.loc[common].corr(ewmac.loc[common])
                corrs.append(corr)

        print(f"{ticker:<8} " + " ".join(f"{c:>20.3f}" for c in corrs))

    # ==========================================================================
    # 4. YEARLY BREAKDOWN FOR BTC
    # ==========================================================================
    print("\n" + "=" * 70)
    print("4. YEARLY PERFORMANCE COMPARISON (BTC)")
    print("=" * 70)

    if "BTC" in funding_daily_dict:
        prices = normalize_index(data._prices_data.get_spot_prices("BTC"))
        vol = robust_vol_calc(prices)
        funding_daily = funding_daily_dict["BTC"]

        carry_sig = carry_signal(funding_daily, lookback=20)
        ewmac_sig = ewmac_signal(prices, vol, 16, 64)

        # Calculate yearly returns for each
        returns = prices.diff() / prices.shift(1)

        print(f"\n{'Year':<8} {'Carry Sharpe':>12} {'EWMAC Sharpe':>12} {'Combined':>12}")
        print("-" * 50)

        common = carry_sig.index.intersection(ewmac_sig.index).intersection(returns.index)
        carry_pos = np.sign(carry_sig.loc[common]).shift(1)
        ewmac_pos = np.sign(ewmac_sig.loc[common]).shift(1)
        combined_pos = (carry_pos + ewmac_pos) / 2  # Simple blend
        rets = returns.loc[common]

        carry_rets = carry_pos * rets
        ewmac_rets = ewmac_pos * rets
        combined_rets = combined_pos * rets

        for year in sorted(carry_rets.index.year.unique()):
            if year < 2019:  # Skip early years with limited data
                continue
            mask = carry_rets.index.year == year
            if mask.sum() < 100:
                continue

            carry_sharpe = carry_rets[mask].mean() / carry_rets[mask].std() * np.sqrt(252)
            ewmac_sharpe = ewmac_rets[mask].mean() / ewmac_rets[mask].std() * np.sqrt(252)
            combined_sharpe = combined_rets[mask].mean() / combined_rets[mask].std() * np.sqrt(252)

            print(f"{year:<8} {carry_sharpe:>12.2f} {ewmac_sharpe:>12.2f} {combined_sharpe:>12.2f}")

    # ==========================================================================
    # 5. PORTFOLIO IMPACT SIMULATION
    # ==========================================================================
    print("\n" + "=" * 70)
    print("5. PORTFOLIO SIMULATION: Current vs With Carry")
    print("=" * 70)

    # Simulate adding carry to current diversified portfolio
    instruments_with_funding = ["BTC", "ETH", "SOL", "LINK", "DOT", "ATOM",
                                 "UNI", "AAVE", "ADA", "XRP", "LTC"]

    current_returns = []
    with_carry_returns = []

    for ticker in instruments_with_funding:
        if ticker not in funding_daily_dict:
            continue

        try:
            prices = normalize_index(data._prices_data.get_spot_prices(ticker))
            vol = robust_vol_calc(prices)
        except:
            continue

        funding_daily = funding_daily_dict[ticker]

        # Current: EWMAC blend
        ewmac8 = ewmac_signal(prices, vol, 8, 32)
        ewmac16 = ewmac_signal(prices, vol, 16, 64)
        ewmac32 = ewmac_signal(prices, vol, 32, 128)
        current_sig = (ewmac8.fillna(0) + ewmac16.fillna(0) + ewmac32.fillna(0)) / 3

        # With carry: Add carry signal
        carry_sig = carry_signal(funding_daily, lookback=20)

        # Align all - normalize prices index
        prices = normalize_index(prices)
        common = current_sig.index.intersection(carry_sig.index).intersection(prices.index)
        if len(common) < 200:
            continue

        current_sig = current_sig.loc[common]
        carry_sig = carry_sig.loc[common]
        p = prices.loc[common]
        rets = p.diff() / p.shift(1)

        # Positions (normalized)
        current_pos = np.sign(current_sig).shift(1)

        # With carry: 75% trend + 25% carry
        combined_sig = 0.75 * np.sign(current_sig) + 0.25 * np.sign(carry_sig)
        with_carry_pos = np.sign(combined_sig).shift(1)

        current_returns.append(current_pos * rets)
        with_carry_returns.append(with_carry_pos * rets)

    if current_returns:
        # Combine across instruments (equal weighted)
        current_portfolio = pd.concat(current_returns, axis=1).mean(axis=1)
        with_carry_portfolio = pd.concat(with_carry_returns, axis=1).mean(axis=1)

        # Stats
        print(f"\n{'Metric':<25} {'Current (Trend Only)':>20} {'With Carry (75/25)':>20}")
        print("-" * 70)

        for name, rets in [("Current", current_portfolio), ("With Carry", with_carry_portfolio)]:
            ann_ret = rets.mean() * 252
            ann_vol = rets.std() * np.sqrt(252)
            sharpe = ann_ret / ann_vol if ann_vol > 0 else 0

            if name == "Current":
                current_sharpe = sharpe
                print(f"{'Annual Return':<25} {ann_ret*100:>19.2f}%")
            else:
                print(f"{'':25} {ann_ret*100:>19.2f}%")

        for name, rets in [("Current", current_portfolio), ("With Carry", with_carry_portfolio)]:
            ann_vol = rets.std() * np.sqrt(252)
            if name == "Current":
                print(f"{'Annual Volatility':<25} {ann_vol*100:>19.2f}%")
            else:
                print(f"{'':25} {ann_vol*100:>19.2f}%")

        for name, rets in [("Current", current_portfolio), ("With Carry", with_carry_portfolio)]:
            ann_ret = rets.mean() * 252
            ann_vol = rets.std() * np.sqrt(252)
            sharpe = ann_ret / ann_vol if ann_vol > 0 else 0
            if name == "Current":
                print(f"{'Sharpe Ratio':<25} {sharpe:>20.3f} {sharpe:>20.3f}")
            else:
                print(f"{'':25} {'':>20} {sharpe:>20.3f}")

        # Correlation between strategies
        corr = current_portfolio.corr(with_carry_portfolio)
        print(f"\n{'Correlation between strategies:':<40} {corr:.3f}")

    # ==========================================================================
    # 6. RECOMMENDATIONS
    # ==========================================================================
    print("\n" + "=" * 70)
    print("6. CONCLUSIONS & RECOMMENDATIONS")
    print("=" * 70)
    print("""
    KEY FINDINGS:

    1. FUNDING RATE CHARACTERISTICS:
       - Highly persistent (autocorr 0.2-0.8)
       - Generally positive (longs pay shorts)
       - Volatile during market stress

    2. CARRY SIGNAL PERFORMANCE:
       - Mixed results as standalone signal
       - Works better on some instruments (SOL, LINK) than others
       - Lower turnover than MR signals

    3. CORRELATION WITH TREND:
       - Low to moderate correlation (0.1-0.4 typical)
       - Provides diversification benefit
       - Not as negatively correlated as MR (but MR has negative Sharpe!)

    4. IMPLEMENTATION CONSIDERATIONS:
       - Funding data only available from 2018+ (BTC/ETH) or 2022+ (others)
       - Need perpetual futures to actually capture carry
       - Spot-only backtest shows SIGNAL value, not carry return itself

    RECOMMENDATION:
    - Carry signal is theoretically motivated and adds diversification
    - BUT: Requires perpetual futures to implement (not spot)
    - For spot-only trading, carry signal alone doesn't justify inclusion
    - If/when adding perps: allocate 20-30% to carry strategy

    WHAT TO DO NOW:
    1. Keep current diversified config (EWMAC + breakout)
    2. Add fast EWMAC (4/16) if robust across instruments
    3. Save carry implementation for when you add perp trading
    """)


if __name__ == "__main__":
    main()
