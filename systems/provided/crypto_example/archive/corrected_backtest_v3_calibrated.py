"""
CORRECTED BACKTEST V3 CALIBRATED
================================
Uses empirically-calibrated leverage multipliers based on V2 results.

V2 Results (no leverage):
- Trend: 7% realized vol vs 25% target → needs ~3.6x
- Carry: 3% realized vol vs 25% target → needs ~8x

This version applies these multipliers to achieve target vol.
"""

import os
import sys
import numpy as np
import pandas as pd
from typing import Dict, List, Tuple
from datetime import datetime, timedelta
from scipy.stats import skew

sys.path.insert(0, "/Users/nathanieldavis/pysystemtrade")

from sysquant.estimators.vol import robust_vol_calc

# =============================================================================
# CONFIGURATION
# =============================================================================

STITCHED_DIR = "/Users/nathanieldavis/pysystemtrade/data/crypto/stitched"
FUNDING_DIR = "/Users/nathanieldavis/pysystemtrade/data/crypto/funding_rates"
COMBINED_FUNDING_DIR = os.path.join(FUNDING_DIR, "combined")

# Capital and risk
CAPITAL = 10000
VOL_TARGET = 0.25  # 25%
DAYS_PER_YEAR = 365

# EMPIRICALLY CALIBRATED LEVERAGE MULTIPLIERS
# From V2 backtest results:
#   Trend realized vol: ~7% → to get 25%, need 25/7 = 3.57x
#   Carry realized vol: ~3% → 8x gave 36%, so 25/36*8 = 5.5x
TREND_LEVERAGE_MULT = 3.6   # Calibrated to achieve ~25% vol
CARRY_LEVERAGE_MULT = 5.5   # Calibrated to achieve ~25% vol

# Trading costs
ROUND_TRIP_COST = 0.003  # 0.3%
CARRY_ANNUAL_COST = 0.02  # 2%

# Walk-forward rules
MIN_HISTORY_DAYS = 252
MIN_TOTAL_HISTORY_YEARS = 3

# Exclude stablecoins
EXCLUDED_INSTRUMENTS = {
    'USDT', 'USDT_OMNI', 'USDT_ETH', 'USDT_TRX', 'USDT_AVAXC',
    'USDC', 'USDC_ETH', 'USDC_TRX', 'USDC_AVAXC',
    'DAI', 'BUSD', 'TUSD', 'TUSD_ETH', 'TUSD_TRX',
    'PAX', 'GUSD', 'HUSD', 'SAI',
    'PAXG', 'XAUT',
    'AUD', 'EUR', 'GBP', 'CHF', 'CAD',
}


# =============================================================================
# DATA LOADING
# =============================================================================

def load_price_data(instrument: str) -> pd.Series:
    path = os.path.join(STITCHED_DIR, f"{instrument}_price.csv")
    if not os.path.exists(path):
        path = os.path.join(STITCHED_DIR, f"{instrument}.csv")
    if not os.path.exists(path):
        return pd.Series(dtype=float)

    df = pd.read_csv(path, parse_dates=['date'])
    df = df.set_index('date')
    df.index = pd.to_datetime(df.index.date)
    prices = df['close'].astype(float)
    prices = prices[~prices.index.duplicated(keep='last')]
    return prices.sort_index()


def load_funding_data(instrument: str) -> pd.Series:
    path = os.path.join(COMBINED_FUNDING_DIR, f"{instrument}_funding_combined.csv")
    if not os.path.exists(path):
        path = os.path.join(FUNDING_DIR, f"{instrument}_funding.csv")
    if not os.path.exists(path):
        return pd.Series(dtype=float)

    df = pd.read_csv(path, parse_dates=['datetime'])
    df = df.set_index('datetime')
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    funding = df['fundingRate'].resample('D').sum()
    funding.index = pd.to_datetime(funding.index.date)
    return funding


def get_all_instruments() -> List[str]:
    instruments = set()
    for f in os.listdir(STITCHED_DIR):
        if f.endswith('_price.csv'):
            instruments.add(f[:-10])
        elif f.endswith('.csv') and not f.endswith('_funding.csv'):
            instruments.add(f[:-4])
    return sorted(instruments)


# =============================================================================
# TRADING RULES
# =============================================================================

def ewmac(prices: pd.Series, Lfast: int, Lslow: int) -> pd.Series:
    fast_ma = prices.ewm(span=Lfast, min_periods=Lfast).mean()
    slow_ma = prices.ewm(span=Lslow, min_periods=Lslow).mean()
    vol = robust_vol_calc(prices)
    return (fast_ma - slow_ma) / vol


def breakout(prices: pd.Series, lookback: int) -> pd.Series:
    smooth = max(int(lookback / 4.0), 1)
    roll_max = prices.rolling(lookback, min_periods=int(np.ceil(lookback / 2.0))).max()
    roll_min = prices.rolling(lookback, min_periods=int(np.ceil(lookback / 2.0))).min()
    roll_mean = (roll_max + roll_min) / 2.0
    raw = 40.0 * ((prices - roll_mean) / (roll_max - roll_min))
    return raw.ewm(span=smooth, min_periods=int(np.ceil(smooth / 2.0))).mean()


def calculate_forecasts(prices: pd.Series) -> Tuple[pd.Series, Dict]:
    forecasts = {}

    CRYPTO_EWMAC_SCALARS = {
        'ewmac8_32': 18.0,
        'ewmac16_64': 13.0,
        'ewmac32_128': 9.0,
        'ewmac64_256': 6.5,
    }

    CRYPTO_BREAKOUT_SCALARS = {
        'breakout10': 0.8,
        'breakout20': 0.85,
        'breakout40': 0.9,
        'breakout80': 0.9,
    }

    for (Lfast, Lslow), name in [((8, 32), 'ewmac8_32'),
                                  ((16, 64), 'ewmac16_64'),
                                  ((32, 128), 'ewmac32_128'),
                                  ((64, 256), 'ewmac64_256')]:
        raw = ewmac(prices, Lfast, Lslow).dropna()
        if len(raw) >= 300:
            scaled = raw * CRYPTO_EWMAC_SCALARS[name]
            forecasts[name] = scaled.clip(-20, 20)

    for lookback, name in [(10, 'breakout10'), (20, 'breakout20'),
                           (40, 'breakout40'), (80, 'breakout80')]:
        raw = breakout(prices, lookback).dropna()
        if len(raw) >= 300:
            scaled = raw * CRYPTO_BREAKOUT_SCALARS[name]
            forecasts[name] = scaled.clip(-20, 20)

    if len(forecasts) == 0:
        return pd.Series(dtype=float), {}

    fc_df = pd.DataFrame(forecasts)
    combined = (fc_df.mean(axis=1) * 1.35).clip(-20, 20)  # FDM = 1.35

    return combined, {'avg_abs': combined.abs().mean()}


# =============================================================================
# POSITION SIZING
# =============================================================================

def calculate_position(
    forecast: float,
    price: float,
    vol: float,
    capital: float,
    vol_target: float,
    idm: float,
    instrument_weight: float,
    leverage_mult: float
) -> float:
    """
    Position sizing with leverage multiplier.

    Base formula: position = subsystem × IDM × weight × (forecast/10)
    With leverage: position = base × leverage_mult
    """
    if vol <= 0 or price <= 0:
        return 0.0

    daily_return_vol = vol / price
    annual_return_vol = daily_return_vol * np.sqrt(DAYS_PER_YEAR)

    subsystem_position = (capital * vol_target) / (price * annual_return_vol)
    position = subsystem_position * idm * instrument_weight * leverage_mult * (forecast / 10.0)

    return position


# =============================================================================
# TREND BACKTEST
# =============================================================================

def run_trend_backtest() -> Dict:
    print("\n" + "=" * 80)
    print("TREND BACKTEST (With Calibrated Leverage)")
    print("=" * 80)

    all_instruments = get_all_instruments()
    all_prices = {}
    for instr in all_instruments:
        prices = load_price_data(instr)
        if len(prices) >= MIN_HISTORY_DAYS:
            all_prices[instr] = prices

    min_days = MIN_TOTAL_HISTORY_YEARS * DAYS_PER_YEAR
    eligible = [i for i, p in all_prices.items()
                if len(p) >= min_days and i not in EXCLUDED_INSTRUMENTS]
    eligible.sort(key=lambda x: -len(all_prices[x]))

    backtest_instruments = eligible[:15]
    print(f"\nUsing {len(backtest_instruments)} instruments")

    n_instruments = len(backtest_instruments)
    instrument_weight = 1.0 / n_instruments

    avg_corr = 0.6
    idm = np.sqrt(n_instruments) / np.sqrt(1 + (n_instruments - 1) * avg_corr)
    idm = min(idm, 2.5)

    print(f"\nRisk Parameters:")
    print(f"  IDM: {idm:.3f}")
    print(f"  Instrument weight: {instrument_weight:.4f}")
    print(f"  Leverage multiplier: {TREND_LEVERAGE_MULT:.1f}x (empirically calibrated)")
    print(f"  Target vol: {VOL_TARGET*100:.0f}%")

    # Calculate forecasts
    all_forecasts = {}
    all_vols = {}
    for instr in backtest_instruments:
        prices = all_prices[instr]
        forecasts, _ = calculate_forecasts(prices)
        vol = robust_vol_calc(prices)
        all_forecasts[instr] = forecasts
        all_vols[instr] = vol

    # Get dates
    all_dates = set()
    for prices in all_prices.values():
        all_dates.update(prices.index)
    all_dates = sorted(all_dates)

    start_date = min(all_prices[i].index.min() for i in backtest_instruments)
    start_date = start_date + timedelta(days=MIN_HISTORY_DAYS + 300)
    backtest_dates = [d for d in all_dates if d >= start_date]

    print(f"\nBacktest: {backtest_dates[0].date()} to {backtest_dates[-1].date()}")

    # Run backtest
    portfolio_returns = []
    turnover_value = 0.0
    prev_positions = {instr: 0.0 for instr in backtest_instruments}
    daily_leverages = []

    for i, date in enumerate(backtest_dates[:-1]):
        next_date = backtest_dates[i + 1]
        daily_pnl = 0.0
        daily_pos_value = 0.0

        for instr in backtest_instruments:
            prices = all_prices[instr]
            if date not in prices.index or next_date not in prices.index:
                continue

            price_today = prices.loc[date]
            price_tomorrow = prices.loc[next_date]

            if date not in all_forecasts[instr].index:
                continue
            forecast = all_forecasts[instr].loc[date]
            if pd.isna(forecast):
                continue

            if date not in all_vols[instr].index:
                continue
            vol = all_vols[instr].loc[date]
            if pd.isna(vol) or vol <= 0:
                continue

            position = calculate_position(
                forecast=forecast,
                price=price_today,
                vol=vol,
                capital=CAPITAL,
                vol_target=VOL_TARGET,
                idm=idm,
                instrument_weight=instrument_weight,
                leverage_mult=TREND_LEVERAGE_MULT
            )

            position_change = abs(position - prev_positions.get(instr, 0.0))
            turnover_value += position_change * price_today
            prev_positions[instr] = position

            price_return = (price_tomorrow - price_today) / price_today
            pnl = position * price_today * price_return

            daily_pnl += pnl
            daily_pos_value += abs(position * price_today)

        portfolio_returns.append({'date': next_date, 'pnl': daily_pnl})
        daily_leverages.append(daily_pos_value / CAPITAL)

    returns_df = pd.DataFrame(portfolio_returns).set_index('date')
    gross_returns = returns_df['pnl'] / CAPITAL

    # Costs
    backtest_years = len(backtest_dates) / DAYS_PER_YEAR
    annual_turnover = turnover_value / (CAPITAL * backtest_years)
    annual_cost_pct = annual_turnover * (ROUND_TRIP_COST / 2)

    daily_cost = annual_cost_pct / DAYS_PER_YEAR
    net_returns = gross_returns - daily_cost

    # Stats
    net_ann_return = net_returns.mean() * DAYS_PER_YEAR
    net_ann_vol = net_returns.std() * np.sqrt(DAYS_PER_YEAR)
    net_sharpe = net_ann_return / net_ann_vol if net_ann_vol > 0 else 0

    cumulative = (1 + net_returns).cumprod()
    max_dd = ((cumulative - cumulative.cummax()) / cumulative.cummax()).min()

    returns_skew = skew(net_returns.dropna())

    avg_leverage = np.mean(daily_leverages)
    max_leverage = np.max(daily_leverages)

    print(f"\nTrading: {annual_turnover:.1f}x turnover, {annual_cost_pct*100:.2f}% cost")

    print("\n" + "=" * 60)
    print("TREND RESULTS")
    print("=" * 60)
    print(f"\n  Sharpe: {net_sharpe:.3f}")
    print(f"  Annual Return: {net_ann_return*100:.2f}%")
    print(f"  Annual Vol: {net_ann_vol*100:.2f}% (target: 25%)")
    print(f"  Vol Achievement: {net_ann_vol/VOL_TARGET:.2f}x target")
    print(f"  Max Drawdown: {max_dd*100:.2f}%")
    print(f"  Skew: {returns_skew:.2f}")
    print(f"  Avg Leverage: {avg_leverage:.2f}x")
    print(f"  Max Leverage: {max_leverage:.2f}x")

    return {
        'net_sharpe': net_sharpe,
        'net_ann_return': net_ann_return,
        'net_ann_vol': net_ann_vol,
        'max_drawdown': max_dd,
        'skewness': returns_skew,
        'avg_leverage': avg_leverage,
        'max_leverage': max_leverage,
        'returns': net_returns,
        'instruments': backtest_instruments,
    }


# =============================================================================
# CARRY BACKTEST
# =============================================================================

def run_carry_backtest() -> Dict:
    print("\n" + "=" * 80)
    print("CARRY BACKTEST (With Calibrated Leverage)")
    print("=" * 80)

    all_prices = {}
    all_funding = {}

    for f in os.listdir(FUNDING_DIR):
        if f.endswith('_funding.csv'):
            instr = f[:-12]
            prices = load_price_data(instr)
            funding = load_funding_data(instr)
            if len(prices) >= MIN_HISTORY_DAYS and len(funding) >= 100:
                all_prices[instr] = prices
                all_funding[instr] = funding

    if os.path.exists(COMBINED_FUNDING_DIR):
        for f in os.listdir(COMBINED_FUNDING_DIR):
            if f.endswith('_funding_combined.csv'):
                instr = f[:-21]
                if instr not in all_prices:
                    prices = load_price_data(instr)
                    funding = load_funding_data(instr)
                    if len(prices) >= MIN_HISTORY_DAYS and len(funding) >= 100:
                        all_prices[instr] = prices
                        all_funding[instr] = funding

    min_days = MIN_TOTAL_HISTORY_YEARS * DAYS_PER_YEAR
    backtest_instruments = [i for i in all_prices if len(all_funding[i]) >= min_days]
    backtest_instruments.sort()

    print(f"\nUsing {len(backtest_instruments)} instruments: {', '.join(backtest_instruments)}")

    n_instruments = len(backtest_instruments)
    if n_instruments == 0:
        return {}

    all_vols = {}
    for instr in backtest_instruments:
        all_vols[instr] = robust_vol_calc(all_prices[instr])

    avg_corr = 0.5
    idm = np.sqrt(n_instruments) / np.sqrt(1 + (n_instruments - 1) * avg_corr)
    idm = min(idm, 2.5)
    instrument_weight = 1.0 / n_instruments

    print(f"\nRisk Parameters:")
    print(f"  IDM: {idm:.3f}")
    print(f"  Instrument weight: {instrument_weight:.4f}")
    print(f"  Leverage multiplier: {CARRY_LEVERAGE_MULT:.1f}x (empirically calibrated)")

    all_dates = set()
    for funding in all_funding.values():
        all_dates.update(funding.index)
    all_dates = sorted(all_dates)

    start_date = None
    for date in all_dates:
        if sum(1 for i in backtest_instruments if date in all_funding[i].index) >= 1:
            start_date = date
            break

    if start_date is None:
        return {}

    backtest_dates = [d for d in all_dates if d >= start_date]
    print(f"\nBacktest: {backtest_dates[0].date()} to {backtest_dates[-1].date()}")

    portfolio_returns = []
    daily_leverages = []

    for i, date in enumerate(backtest_dates[:-1]):
        next_date = backtest_dates[i + 1]
        daily_return = 0.0
        daily_pos_value = 0.0

        for instr in backtest_instruments:
            funding = all_funding[instr]
            prices = all_prices[instr]

            if date not in funding.index or date not in prices.index:
                continue

            funding_rate = funding.loc[date]
            price = prices.loc[date]

            if date not in all_vols[instr].index:
                continue
            vol = all_vols[instr].loc[date]
            if pd.isna(vol) or vol <= 0:
                continue

            daily_return_vol = vol / price
            annual_return_vol = daily_return_vol * np.sqrt(DAYS_PER_YEAR)

            # Carry forecast
            funding_annualized = funding_rate * DAYS_PER_YEAR
            raw_carry_forecast = funding_annualized / annual_return_vol
            carry_forecast = np.clip(raw_carry_forecast * 5.0, -20, 20)

            # Position with leverage
            subsystem_value = (CAPITAL * VOL_TARGET) / annual_return_vol
            position_value = subsystem_value * idm * instrument_weight * CARRY_LEVERAGE_MULT * (carry_forecast / 10.0)

            carry_return = (abs(position_value) / CAPITAL) * funding_rate * np.sign(carry_forecast)

            daily_return += carry_return
            daily_pos_value += abs(position_value)

        portfolio_returns.append({'date': next_date, 'return': daily_return})
        daily_leverages.append(daily_pos_value / CAPITAL if CAPITAL > 0 else 0)

    returns_df = pd.DataFrame(portfolio_returns).set_index('date')
    gross_returns = returns_df['return']

    daily_cost = CARRY_ANNUAL_COST / DAYS_PER_YEAR
    net_returns = gross_returns - daily_cost

    net_ann_return = net_returns.mean() * DAYS_PER_YEAR
    net_ann_vol = net_returns.std() * np.sqrt(DAYS_PER_YEAR)
    net_sharpe = net_ann_return / net_ann_vol if net_ann_vol > 0 else 0

    cumulative = (1 + net_returns).cumprod()
    max_dd = ((cumulative - cumulative.cummax()) / cumulative.cummax()).min()

    returns_skew = skew(net_returns.dropna())

    avg_leverage = np.mean(daily_leverages)
    max_leverage = np.max(daily_leverages)

    print("\n" + "=" * 60)
    print("CARRY RESULTS")
    print("=" * 60)
    print(f"\n  Sharpe: {net_sharpe:.3f}")
    print(f"  Annual Return: {net_ann_return*100:.2f}%")
    print(f"  Annual Vol: {net_ann_vol*100:.2f}% (target: 25%)")
    print(f"  Vol Achievement: {net_ann_vol/VOL_TARGET:.2f}x target")
    print(f"  Max Drawdown: {max_dd*100:.2f}%")
    print(f"  Skew: {returns_skew:.2f}")
    print(f"  Avg Leverage: {avg_leverage:.2f}x")
    print(f"  Max Leverage: {max_leverage:.2f}x")

    return {
        'net_sharpe': net_sharpe,
        'net_ann_return': net_ann_return,
        'net_ann_vol': net_ann_vol,
        'max_drawdown': max_dd,
        'skewness': returns_skew,
        'avg_leverage': avg_leverage,
        'max_leverage': max_leverage,
        'returns': net_returns,
        'instruments': backtest_instruments,
    }


# =============================================================================
# COMBINED ANALYSIS
# =============================================================================

def run_combined_analysis(trend: Dict, carry: Dict):
    print("\n" + "=" * 80)
    print("COMBINED ANALYSIS")
    print("=" * 80)

    t = trend['returns']
    c = carry['returns']

    common = t.index.intersection(c.index)
    if len(common) < 252:
        return {}

    t = t.loc[common]
    c = c.loc[common]

    corr = t.corr(c)
    print(f"\nCorrelation: {corr:.3f}")

    print(f"\n{'Allocation':<12} {'Sharpe':>8} {'Return':>10} {'Vol':>8} {'Skew':>7}")
    print("-" * 50)

    for carry_wt in [0.0, 0.3, 0.5, 0.7, 1.0]:
        trend_wt = 1.0 - carry_wt
        combined = trend_wt * t + carry_wt * c

        ann_ret = combined.mean() * DAYS_PER_YEAR
        ann_vol = combined.std() * np.sqrt(DAYS_PER_YEAR)
        sharpe = ann_ret / ann_vol if ann_vol > 0 else 0
        sk = skew(combined.dropna())

        label = f"T{int(trend_wt*100)}/C{int(carry_wt*100)}"
        print(f"{label:<12} {sharpe:>8.2f} {ann_ret*100:>9.1f}% {ann_vol*100:>7.1f}% {sk:>+7.2f}")

    return {'correlation': corr}


# =============================================================================
# MAIN
# =============================================================================

def main():
    print("=" * 80)
    print("CRYPTO BACKTEST V3 - CALIBRATED LEVERAGE")
    print("=" * 80)
    print(f"\nCapital: ${CAPITAL:,}  |  Vol Target: {VOL_TARGET*100:.0f}%")
    print(f"Trend leverage: {TREND_LEVERAGE_MULT}x  |  Carry leverage: {CARRY_LEVERAGE_MULT}x")

    trend = run_trend_backtest()
    carry = run_carry_backtest()

    combined = {}
    if trend and carry:
        combined = run_combined_analysis(trend, carry)

    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)

    if trend:
        vol_ratio = trend['net_ann_vol'] / VOL_TARGET
        print(f"""
TREND ({TREND_LEVERAGE_MULT}x leverage):
  Sharpe: {trend['net_sharpe']:.2f}  |  Return: {trend['net_ann_return']*100:.1f}%  |  Vol: {trend['net_ann_vol']*100:.1f}% ({vol_ratio:.1f}x target)
  Max DD: {trend['max_drawdown']*100:.1f}%  |  Skew: {trend['skewness']:.2f}  |  Leverage: {trend['avg_leverage']:.1f}x avg, {trend['max_leverage']:.1f}x max
""")

    if carry:
        vol_ratio = carry['net_ann_vol'] / VOL_TARGET
        print(f"""CARRY ({CARRY_LEVERAGE_MULT}x leverage):
  Sharpe: {carry['net_sharpe']:.2f}  |  Return: {carry['net_ann_return']*100:.1f}%  |  Vol: {carry['net_ann_vol']*100:.1f}% ({vol_ratio:.1f}x target)
  Max DD: {carry['max_drawdown']*100:.1f}%  |  Skew: {carry['skewness']:.2f}  |  Leverage: {carry['avg_leverage']:.1f}x avg, {carry['max_leverage']:.1f}x max
""")

    if combined:
        print(f"Correlation: {combined['correlation']:.3f}")


if __name__ == "__main__":
    main()
