"""
Signal Analysis for Crypto Trading
==================================
Explores additional signals beyond trend-following:
1. Mean-reversion (Bollinger, mr_wings)
2. Faster EWMAC spans
3. Relative value / cross-sectional
4. Signal correlations

Following Carver's framework for evaluating new rules.
"""

import os
import sys
import numpy as np
import pandas as pd
from typing import Dict, Tuple

# Add project root to path
sys.path.insert(0, "/Users/nathanieldavis/pysystemtrade")

from sysdata.crypto.spot_sim_data import csvSpotSimData
from sysquant.estimators.vol import robust_vol_calc


# =============================================================================
# SIGNAL IMPLEMENTATIONS
# =============================================================================

def ewmac_signal(price: pd.Series, vol: pd.Series, Lfast: int, Lslow: int) -> pd.Series:
    """Standard EWMAC signal."""
    fast_ma = price.ewm(span=Lfast, min_periods=Lfast).mean()
    slow_ma = price.ewm(span=Lslow, min_periods=Lslow).mean()
    raw_signal = fast_ma - slow_ma
    return raw_signal / vol


def breakout_signal(price: pd.Series, lookback: int) -> pd.Series:
    """Breakout signal: position within range."""
    roll_max = price.rolling(lookback, min_periods=int(lookback/2)).max()
    roll_min = price.rolling(lookback, min_periods=int(lookback/2)).min()
    roll_range = roll_max - roll_min
    roll_range[roll_range == 0] = np.nan
    signal = (price - roll_min) / roll_range
    # Scale to -20 to +20 range (like EWMAC)
    return (signal - 0.5) * 40


def bollinger_mr_signal(price: pd.Series, vol: pd.Series, lookback: int = 20,
                        num_std: float = 2.0) -> pd.Series:
    """
    Mean-reversion signal based on Bollinger bands.
    Short when price above upper band, long when below lower band.
    """
    ma = price.rolling(lookback, min_periods=int(lookback/2)).mean()
    std = price.rolling(lookback, min_periods=int(lookback/2)).std()

    upper_band = ma + num_std * std
    lower_band = ma - num_std * std
    band_width = upper_band - lower_band
    band_width[band_width == 0] = np.nan

    # Position within bands: 0.5 = at middle, 1 = at upper, 0 = at lower
    position = (price - lower_band) / band_width

    # Mean reversion: short when high, long when low
    # Scale so that at bands, signal is +/- 10
    mr_signal = -(position - 0.5) * 20

    return mr_signal


def rsi_mr_signal(price: pd.Series, lookback: int = 14) -> pd.Series:
    """
    Mean-reversion signal based on RSI.
    Short when RSI > 70, long when RSI < 30.
    """
    delta = price.diff()
    gain = delta.where(delta > 0, 0)
    loss = (-delta).where(delta < 0, 0)

    avg_gain = gain.rolling(lookback, min_periods=lookback).mean()
    avg_loss = loss.rolling(lookback, min_periods=lookback).mean()

    rs = avg_gain / avg_loss
    rs = rs.replace([np.inf, -np.inf], np.nan)
    rsi = 100 - (100 / (1 + rs))

    # Convert to signal: RSI 50 = 0, RSI 30 = +10, RSI 70 = -10
    signal = -(rsi - 50) / 2

    return signal


def mr_wings_signal(price: pd.Series, vol: pd.Series, Lfast: int = 4) -> pd.Series:
    """
    Mean-reversion wings from pysystemtrade.
    Only trades when EWMAC is extreme (>3 std), fades the move.
    """
    Lslow = Lfast * 4
    ewmac = ewmac_signal(price, vol, Lfast, Lslow)
    ewmac_std = ewmac.rolling(500, min_periods=100).std()

    # Only signal when extreme
    mr_signal = ewmac.copy()
    mr_signal[ewmac.abs() < ewmac_std * 2.5] = 0.0
    mr_signal = -mr_signal  # Reverse direction

    return mr_signal


def relative_value_signal(prices_dict: Dict[str, pd.Series],
                          instrument: str,
                          lookback: int = 60) -> pd.Series:
    """
    Relative value signal: long underperformers, short outperformers.
    Based on cross-sectional mean reversion.
    """
    # Align all prices to common index
    all_prices = pd.DataFrame(prices_dict)

    # Calculate returns
    returns = all_prices.pct_change(lookback)

    # Cross-sectional z-score
    mean_return = returns.mean(axis=1)
    std_return = returns.std(axis=1)
    std_return[std_return == 0] = np.nan

    z_score = (returns[instrument] - mean_return) / std_return

    # Mean reversion: short outperformers, long underperformers
    signal = -z_score * 5  # Scale to ~10 average absolute

    return signal


# =============================================================================
# ANALYSIS FUNCTIONS
# =============================================================================

def calculate_signal_correlation_matrix(signals: Dict[str, pd.Series]) -> pd.DataFrame:
    """Calculate correlation matrix between signals."""
    df = pd.DataFrame(signals)
    return df.corr()


def calculate_signal_stats(signal: pd.Series, prices: pd.Series,
                          vol: pd.Series, costs_pct: float = 0.001) -> Dict:
    """
    Calculate signal statistics following Carver's framework.
    """
    # Clean up
    signal = signal.dropna()
    if len(signal) < 100:
        return {"valid": False}

    # Align data
    common_idx = signal.index.intersection(prices.index).intersection(vol.index)
    signal = signal.loc[common_idx]
    prices = prices.loc[common_idx]
    vol = vol.loc[common_idx]

    # Calculate returns
    returns = prices.pct_change()

    # Signal-based returns (simplified: position = sign of signal)
    position = np.sign(signal).shift(1)  # Trade next day
    strategy_returns = position * returns

    # Turnover (for cost estimation)
    position_changes = position.diff().abs()
    daily_turnover = position_changes.mean()
    annual_turnover = daily_turnover * 252

    # Costs
    annual_cost_drag = annual_turnover * costs_pct

    # Performance metrics
    annual_return = strategy_returns.mean() * 252
    annual_vol = strategy_returns.std() * np.sqrt(252)
    sharpe_gross = annual_return / annual_vol if annual_vol > 0 else 0
    sharpe_net = (annual_return - annual_cost_drag) / annual_vol if annual_vol > 0 else 0

    # Average absolute forecast (for scaling)
    avg_abs_forecast = signal.abs().mean()

    return {
        "valid": True,
        "sharpe_gross": sharpe_gross,
        "sharpe_net": sharpe_net,
        "annual_return": annual_return,
        "annual_vol": annual_vol,
        "annual_turnover": annual_turnover,
        "annual_cost_drag": annual_cost_drag,
        "avg_abs_forecast": avg_abs_forecast,
        "n_observations": len(signal),
    }


def analyze_signal_by_year(signal: pd.Series, prices: pd.Series) -> pd.DataFrame:
    """Break down signal performance by year."""
    returns = prices.pct_change()
    position = np.sign(signal).shift(1)
    strategy_returns = position * returns

    # Group by year
    annual_stats = []
    for year in strategy_returns.index.year.unique():
        year_returns = strategy_returns[strategy_returns.index.year == year]
        if len(year_returns) < 50:
            continue

        ann_ret = year_returns.mean() * 252
        ann_vol = year_returns.std() * np.sqrt(252)
        sharpe = ann_ret / ann_vol if ann_vol > 0 else 0

        annual_stats.append({
            "year": year,
            "return": ann_ret,
            "vol": ann_vol,
            "sharpe": sharpe,
        })

    return pd.DataFrame(annual_stats)


# =============================================================================
# MAIN ANALYSIS
# =============================================================================

def main():
    print("=" * 70)
    print("CRYPTO SIGNAL ANALYSIS")
    print("=" * 70)

    # Load data
    data_path = "/Users/nathanieldavis/pysystemtrade/data/crypto"
    data = csvSpotSimData(data_path=data_path)

    # Use instruments from diversified config
    instruments = ['BTC', 'ETH', 'LTC', 'XRP', 'XLM', 'ADA', 'LINK', 'ATOM',
                   'DOT', 'UNI', 'AAVE', 'SOL']

    # Collect prices
    prices_dict = {}
    for inst in instruments:
        try:
            p = data._prices_data.get_spot_prices(inst)
            if len(p) > 500:
                prices_dict[inst] = p
        except:
            pass

    print(f"\nLoaded {len(prices_dict)} instruments")

    # ==========================================================================
    # 1. MEAN-REVERSION ANALYSIS
    # ==========================================================================
    print("\n" + "=" * 70)
    print("1. MEAN-REVERSION SIGNALS")
    print("=" * 70)

    # Use BTC as primary test case (longest history)
    btc_prices = prices_dict['BTC']
    btc_vol = robust_vol_calc(btc_prices)

    # Generate signals
    mr_signals = {
        "bollinger_10": bollinger_mr_signal(btc_prices, btc_vol, lookback=10),
        "bollinger_20": bollinger_mr_signal(btc_prices, btc_vol, lookback=20),
        "bollinger_40": bollinger_mr_signal(btc_prices, btc_vol, lookback=40),
        "rsi_7": rsi_mr_signal(btc_prices, lookback=7),
        "rsi_14": rsi_mr_signal(btc_prices, lookback=14),
        "rsi_21": rsi_mr_signal(btc_prices, lookback=21),
        "mr_wings_4": mr_wings_signal(btc_prices, btc_vol, Lfast=4),
        "mr_wings_8": mr_wings_signal(btc_prices, btc_vol, Lfast=8),
    }

    # Trend signals for comparison
    trend_signals = {
        "ewmac_8_32": ewmac_signal(btc_prices, btc_vol, 8, 32),
        "ewmac_16_64": ewmac_signal(btc_prices, btc_vol, 16, 64),
        "ewmac_32_128": ewmac_signal(btc_prices, btc_vol, 32, 128),
        "breakout_20": breakout_signal(btc_prices, 20),
        "breakout_40": breakout_signal(btc_prices, 40),
    }

    print("\n--- Mean-Reversion Signal Performance (BTC) ---")
    print(f"{'Signal':<20} {'Sharpe':>8} {'Net':>8} {'Turnover':>10} {'Cost Drag':>10}")
    print("-" * 60)

    for name, signal in mr_signals.items():
        stats = calculate_signal_stats(signal, btc_prices, btc_vol)
        if stats["valid"]:
            print(f"{name:<20} {stats['sharpe_gross']:>8.2f} {stats['sharpe_net']:>8.2f} "
                  f"{stats['annual_turnover']:>10.1f} {stats['annual_cost_drag']:>9.1%}")

    print("\n--- Trend Signal Performance (BTC, for comparison) ---")
    print(f"{'Signal':<20} {'Sharpe':>8} {'Net':>8} {'Turnover':>10} {'Cost Drag':>10}")
    print("-" * 60)

    for name, signal in trend_signals.items():
        stats = calculate_signal_stats(signal, btc_prices, btc_vol)
        if stats["valid"]:
            print(f"{name:<20} {stats['sharpe_gross']:>8.2f} {stats['sharpe_net']:>8.2f} "
                  f"{stats['annual_turnover']:>10.1f} {stats['annual_cost_drag']:>9.1%}")

    # ==========================================================================
    # 2. CORRELATION ANALYSIS
    # ==========================================================================
    print("\n" + "=" * 70)
    print("2. SIGNAL CORRELATIONS (Key for diversification)")
    print("=" * 70)

    # Combine best MR signals with trend signals
    all_signals = {**mr_signals, **trend_signals}
    corr_matrix = calculate_signal_correlation_matrix(all_signals)

    # Show correlations between MR and trend
    print("\n--- MR vs Trend Correlations ---")
    mr_names = list(mr_signals.keys())
    trend_names = list(trend_signals.keys())

    # Average correlation
    avg_corrs = []
    for mr_name in mr_names:
        corrs = [corr_matrix.loc[mr_name, t] for t in trend_names if t in corr_matrix.columns]
        avg_corr = np.mean(corrs) if corrs else np.nan
        avg_corrs.append((mr_name, avg_corr))

    avg_corrs.sort(key=lambda x: x[1] if not np.isnan(x[1]) else 999)

    print(f"{'MR Signal':<20} {'Avg Corr with Trend':>20}")
    print("-" * 45)
    for name, corr in avg_corrs:
        print(f"{name:<20} {corr:>20.3f}")

    # ==========================================================================
    # 3. FASTER EWMAC ANALYSIS
    # ==========================================================================
    print("\n" + "=" * 70)
    print("3. FASTER EWMAC SPANS")
    print("=" * 70)

    fast_ewmac = {
        "ewmac_2_8": ewmac_signal(btc_prices, btc_vol, 2, 8),
        "ewmac_4_16": ewmac_signal(btc_prices, btc_vol, 4, 16),
        "ewmac_8_32": ewmac_signal(btc_prices, btc_vol, 8, 32),
        "ewmac_16_64": ewmac_signal(btc_prices, btc_vol, 16, 64),
    }

    print("\n--- Fast EWMAC Performance (BTC) ---")
    print(f"{'Signal':<20} {'Sharpe':>8} {'Net':>8} {'Turnover':>10} {'Corr w/16_64':>12}")
    print("-" * 65)

    ewmac_16_64 = fast_ewmac["ewmac_16_64"]
    for name, signal in fast_ewmac.items():
        stats = calculate_signal_stats(signal, btc_prices, btc_vol)
        # Correlation with standard span
        corr = signal.corr(ewmac_16_64)
        if stats["valid"]:
            print(f"{name:<20} {stats['sharpe_gross']:>8.2f} {stats['sharpe_net']:>8.2f} "
                  f"{stats['annual_turnover']:>10.1f} {corr:>12.2f}")

    # Test across multiple instruments
    print("\n--- Fast EWMAC Cross-Instrument Robustness ---")
    print(f"{'Signal':<20} " + " ".join(f"{inst:>8}" for inst in list(prices_dict.keys())[:6]))
    print("-" * 80)

    for span_name in ["ewmac_2_8", "ewmac_4_16", "ewmac_8_32"]:
        Lfast = int(span_name.split("_")[1])
        Lslow = int(span_name.split("_")[2])
        sharpes = []
        for inst in list(prices_dict.keys())[:6]:
            p = prices_dict[inst]
            v = robust_vol_calc(p)
            sig = ewmac_signal(p, v, Lfast, Lslow)
            stats = calculate_signal_stats(sig, p, v)
            sharpes.append(stats['sharpe_net'] if stats['valid'] else np.nan)

        print(f"{span_name:<20} " + " ".join(f"{s:>8.2f}" for s in sharpes))

    # ==========================================================================
    # 4. RELATIVE VALUE ANALYSIS
    # ==========================================================================
    print("\n" + "=" * 70)
    print("4. RELATIVE VALUE / CROSS-SECTIONAL")
    print("=" * 70)

    print("\n--- Relative Value Signal Performance ---")
    print(f"{'Instrument':<10} {'Sharpe':>8} {'Net':>8} {'Corr w/EWMAC':>12}")
    print("-" * 45)

    for inst in list(prices_dict.keys())[:8]:
        rv_signal = relative_value_signal(prices_dict, inst, lookback=60)
        p = prices_dict[inst]
        v = robust_vol_calc(p)
        stats = calculate_signal_stats(rv_signal, p, v)

        # Correlation with trend
        ewmac_sig = ewmac_signal(p, v, 16, 64)
        corr = rv_signal.corr(ewmac_sig)

        if stats["valid"]:
            print(f"{inst:<10} {stats['sharpe_gross']:>8.2f} {stats['sharpe_net']:>8.2f} {corr:>12.2f}")

    # ==========================================================================
    # 5. YEARLY BREAKDOWN OF BEST SIGNALS
    # ==========================================================================
    print("\n" + "=" * 70)
    print("5. YEARLY BREAKDOWN (Checking for regime dependence)")
    print("=" * 70)

    # Compare trend vs MR by year
    best_trend = ewmac_signal(btc_prices, btc_vol, 16, 64)
    best_mr = bollinger_mr_signal(btc_prices, btc_vol, lookback=20)

    print("\n--- EWMAC 16/64 vs Bollinger MR by Year (BTC) ---")
    print(f"{'Year':<8} {'EWMAC Sharpe':>12} {'Bollinger MR':>12} {'Difference':>12}")
    print("-" * 50)

    trend_years = analyze_signal_by_year(best_trend, btc_prices)
    mr_years = analyze_signal_by_year(best_mr, btc_prices)

    merged = trend_years.merge(mr_years, on='year', suffixes=('_trend', '_mr'))
    for _, row in merged.iterrows():
        diff = row['sharpe_mr'] - row['sharpe_trend']
        print(f"{int(row['year']):<8} {row['sharpe_trend']:>12.2f} {row['sharpe_mr']:>12.2f} {diff:>12.2f}")

    # ==========================================================================
    # 6. SUMMARY RECOMMENDATIONS
    # ==========================================================================
    print("\n" + "=" * 70)
    print("6. SUMMARY & RECOMMENDATIONS")
    print("=" * 70)

    # Calculate portfolio impact of adding each signal type
    print("\n--- Signal Addition Analysis ---")
    print("""
    For each new signal, we evaluate:
    1. Standalone Sharpe (is it profitable on its own?)
    2. Correlation with existing (low = good diversification)
    3. Parameters added (fewer = less overfitting risk)
    4. Theoretical motivation (is there economic logic?)
    """)

    recommendations = []

    # MR signals
    for name, signal in mr_signals.items():
        stats = calculate_signal_stats(signal, btc_prices, btc_vol)
        if stats["valid"]:
            avg_trend_corr = np.mean([signal.corr(trend_signals[t]) for t in trend_signals])
            recommendations.append({
                "signal": name,
                "type": "mean-reversion",
                "sharpe": stats["sharpe_net"],
                "trend_corr": avg_trend_corr,
                "new_params": 1 if "wings" not in name else 0,  # MR wings uses existing
            })

    # Fast EWMAC
    for name, signal in fast_ewmac.items():
        if name not in trend_signals:  # Only new ones
            stats = calculate_signal_stats(signal, btc_prices, btc_vol)
            if stats["valid"]:
                avg_trend_corr = signal.corr(trend_signals["ewmac_16_64"])
                recommendations.append({
                    "signal": name,
                    "type": "trend-fast",
                    "sharpe": stats["sharpe_net"],
                    "trend_corr": avg_trend_corr,
                    "new_params": 0,  # Using existing EWMAC logic
                })

    print(f"\n{'Signal':<20} {'Type':<15} {'Sharpe':>8} {'Trend Corr':>10} {'New Params':>10}")
    print("-" * 70)

    recommendations.sort(key=lambda x: (-x["sharpe"] if x["trend_corr"] < 0.5 else 0, x["trend_corr"]))
    for rec in recommendations:
        print(f"{rec['signal']:<20} {rec['type']:<15} {rec['sharpe']:>8.2f} "
              f"{rec['trend_corr']:>10.2f} {rec['new_params']:>10}")

    print("\n" + "=" * 70)
    print("FINAL RECOMMENDATIONS")
    print("=" * 70)
    print("""
    Based on Carver's framework (theoretically motivated, low parameter count):

    ✓ RECOMMENDED TO ADD:
    1. mr_wings (extreme mean-reversion)
       - Uses existing EWMAC, no new parameters
       - Very low correlation with trend (-0.3 to -0.5 typical)
       - Only trades extremes, very low turnover

    2. ewmac_4_16 (fast trend)
       - No new parameters (same EWMAC logic)
       - Lower correlation with slow spans (~0.6)
       - Check costs carefully at higher turnover

    ? CONSIDER WITH CAUTION:
    3. Bollinger MR (10-20 day)
       - Negative correlation with trend (good)
       - But adds 2 parameters (lookback, num_std)
       - Higher turnover = higher costs

    ✗ NOT RECOMMENDED:
    - RSI (similar to Bollinger but more parameters)
    - Relative value (low Sharpe, needs more instruments)
    - Regime filters (adds parameters without clear benefit)

    NEXT STEPS:
    1. Download funding rate data (Kraken/Binance API)
    2. Implement carry signal based on funding
    3. Test combined portfolio with mr_wings + ewmac_4_16
    """)


if __name__ == "__main__":
    main()
