"""
CORRECTED BACKTEST V3 FIXED: Proper Leverage Multiplier
========================================================
Previous V3 removed weight entirely, causing 100%+ portfolio vol.

CORRECT APPROACH: Keep weight but add a leverage multiplier to scale
from current ~7% vol to 25% target.

Formula: position = subsystem × IDM × weight × leverage_mult × (forecast/10)

Where leverage_mult = target_vol / realized_vol_without_leverage ≈ 3.6
"""

import os
import sys
import numpy as np
import pandas as pd
from typing import Dict, List, Tuple, Optional
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

# Leverage multiplier to achieve target vol
# Calculated as: target_vol / realized_vol_without_leverage
# From V2: realized_vol was ~7%, target is 25%, so mult = 25/7 ≈ 3.6
# We'll calculate this dynamically based on IDM, weights, and correlation
LEVERAGE_MULT_TREND = 3.6  # For trend (adjustable)
LEVERAGE_MULT_CARRY = 3.6  # For carry (adjustable)

# Trading costs
ROUND_TRIP_COST = 0.003  # 0.3%
CARRY_ANNUAL_COST = 0.02  # 2% for carry (opening + rebalancing)

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


def calculate_forecasts_with_calibration(prices: pd.Series) -> Tuple[pd.Series, Dict]:
    forecasts = {}
    scalars = {}

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
        if len(raw) < 300:
            continue

        scalar = CRYPTO_EWMAC_SCALARS[name]
        scalars[name] = scalar
        scaled = raw * scalar
        forecasts[name] = scaled.clip(-20, 20)

    for lookback, name in [(10, 'breakout10'), (20, 'breakout20'),
                           (40, 'breakout40'), (80, 'breakout80')]:
        raw = breakout(prices, lookback).dropna()
        if len(raw) < 300:
            continue

        scalar = CRYPTO_BREAKOUT_SCALARS[name]
        scalars[name] = scalar
        scaled = raw * scalar
        forecasts[name] = scaled.clip(-20, 20)

    if len(forecasts) == 0:
        return pd.Series(dtype=float), {}

    fc_df = pd.DataFrame(forecasts)
    combined_raw = fc_df.mean(axis=1)
    fdm = 1.35

    combined_final = (combined_raw * fdm).clip(-20, 20)

    diagnostics = {
        'scalars': scalars,
        'fdm': fdm,
        'avg_abs_combined': combined_final.abs().mean(),
    }

    return combined_final, diagnostics


# =============================================================================
# POSITION SIZING WITH LEVERAGE MULTIPLIER
# =============================================================================

def calculate_leverage_multiplier(n_instruments: int, avg_corr: float, idm: float) -> float:
    """
    Calculate the leverage multiplier needed to achieve target vol.

    Without leverage, portfolio vol is approximately:
      vol = vol_target × IDM × weight × sqrt(n × (1 + (n-1)×ρ))

    To achieve vol_target at portfolio level, we need:
      leverage_mult = 1 / (IDM × weight × sqrt(1/n + (n-1)/n × ρ))
    """
    weight = 1.0 / n_instruments

    # Expected vol scaling factor
    vol_scale = idm * weight * np.sqrt(1/n_instruments + (n_instruments-1)/n_instruments * avg_corr)

    # To achieve target, multiply by inverse
    leverage_mult = 1.0 / vol_scale

    return leverage_mult


def calculate_position_with_leverage(
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
    Calculate position with leverage multiplier.

    position = subsystem × IDM × weight × leverage_mult × (forecast/10)
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
    print("TREND BACKTEST V3 FIXED (With Leverage Multiplier)")
    print("=" * 80)

    # Load data
    all_instruments = get_all_instruments()
    all_prices = {}
    for instr in all_instruments:
        prices = load_price_data(instr)
        if len(prices) >= MIN_HISTORY_DAYS:
            all_prices[instr] = prices

    print(f"\nLoaded price data for {len(all_prices)} instruments")

    min_days = MIN_TOTAL_HISTORY_YEARS * DAYS_PER_YEAR
    eligible_instruments = [
        instr for instr, prices in all_prices.items()
        if len(prices) >= min_days and instr not in EXCLUDED_INSTRUMENTS
    ]
    eligible_instruments.sort(key=lambda x: -len(all_prices[x]))

    backtest_instruments = eligible_instruments[:15]
    print(f"\nUsing {len(backtest_instruments)} instruments:")
    for i, instr in enumerate(backtest_instruments):
        days = len(all_prices[instr])
        years = days / DAYS_PER_YEAR
        print(f"  {i+1}. {instr}: {years:.1f} years")

    n_instruments = len(backtest_instruments)
    instrument_weight = 1.0 / n_instruments

    avg_corr = 0.6
    idm = np.sqrt(n_instruments) / np.sqrt(1 + (n_instruments - 1) * avg_corr)
    idm = min(idm, 2.5)

    # Calculate leverage multiplier dynamically
    leverage_mult = calculate_leverage_multiplier(n_instruments, avg_corr, idm)

    print(f"\nRisk Parameters:")
    print(f"  IDM: {idm:.3f}")
    print(f"  Instrument weight: {instrument_weight:.4f}")
    print(f"  Leverage multiplier: {leverage_mult:.2f}x")
    print(f"  (This achieves 25% portfolio vol from ~{VOL_TARGET/leverage_mult*100:.1f}% base)")

    # Pre-calculate forecasts
    print("\nCalculating forecasts...")
    all_forecasts = {}
    all_vols = {}

    for instr in backtest_instruments:
        prices = all_prices[instr]
        forecasts, diag = calculate_forecasts_with_calibration(prices)
        vol = robust_vol_calc(prices)
        all_forecasts[instr] = forecasts
        all_vols[instr] = vol

    # Get backtest dates
    all_dates = set()
    for prices in all_prices.values():
        all_dates.update(prices.index)
    all_dates = sorted(all_dates)

    start_date = min(all_prices[i].index.min() for i in backtest_instruments)
    start_date = start_date + timedelta(days=MIN_HISTORY_DAYS + 300)
    backtest_dates = [d for d in all_dates if d >= start_date]

    print(f"\nBacktest period: {backtest_dates[0]} to {backtest_dates[-1]}")
    print(f"Total days: {len(backtest_dates)}")

    # Run backtest
    print("\nRunning backtest...")
    portfolio_returns = []
    turnover_value = 0.0
    prev_positions = {instr: 0.0 for instr in backtest_instruments}
    daily_position_values = []
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

            position = calculate_position_with_leverage(
                forecast=forecast,
                price=price_today,
                vol=vol,
                capital=CAPITAL,
                vol_target=VOL_TARGET,
                idm=idm,
                instrument_weight=instrument_weight,
                leverage_mult=leverage_mult
            )

            position_change = abs(position - prev_positions.get(instr, 0.0))
            turnover_value += position_change * price_today
            prev_positions[instr] = position

            price_return = (price_tomorrow - price_today) / price_today
            pnl = position * price_today * price_return

            daily_pnl += pnl
            daily_pos_value += abs(position * price_today)

        portfolio_returns.append({
            'date': next_date,
            'pnl': daily_pnl,
            'pos_value': daily_pos_value
        })
        daily_position_values.append(daily_pos_value)
        daily_leverages.append(daily_pos_value / CAPITAL)

    returns_df = pd.DataFrame(portfolio_returns).set_index('date')
    gross_returns = returns_df['pnl'] / CAPITAL

    backtest_years = len(backtest_dates) / DAYS_PER_YEAR
    annual_turnover = turnover_value / (CAPITAL * backtest_years)
    annual_cost_pct = annual_turnover * (ROUND_TRIP_COST / 2)

    print(f"\nTrading costs:")
    print(f"  Annual turnover: {annual_turnover:.1f}x capital")
    print(f"  Annual cost drag: {annual_cost_pct*100:.2f}%")

    daily_cost = annual_cost_pct / DAYS_PER_YEAR
    net_returns = gross_returns - daily_cost

    gross_ann_return = gross_returns.mean() * DAYS_PER_YEAR
    gross_ann_vol = gross_returns.std() * np.sqrt(DAYS_PER_YEAR)

    net_ann_return = net_returns.mean() * DAYS_PER_YEAR
    net_ann_vol = net_returns.std() * np.sqrt(DAYS_PER_YEAR)
    net_sharpe = net_ann_return / net_ann_vol if net_ann_vol > 0 else 0

    cumulative = (1 + net_returns).cumprod()
    max_dd = ((cumulative - cumulative.cummax()) / cumulative.cummax()).min()

    returns_skew = skew(net_returns.dropna())

    five_years_ago = backtest_dates[-1] - timedelta(days=5*365)
    recent = net_returns[net_returns.index >= five_years_ago]
    recent_sharpe = (recent.mean() * DAYS_PER_YEAR) / (recent.std() * np.sqrt(DAYS_PER_YEAR)) if len(recent) > 0 else 0

    avg_leverage = np.mean(daily_leverages)
    max_leverage = np.max(daily_leverages)

    print("\n" + "=" * 60)
    print("TREND BACKTEST RESULTS")
    print("=" * 60)

    print(f"\nConfiguration:")
    print(f"  Capital: ${CAPITAL:,}")
    print(f"  Vol Target: {VOL_TARGET*100:.0f}%")
    print(f"  Instruments: {n_instruments}")
    print(f"  IDM: {idm:.3f}")
    print(f"  Leverage Multiplier: {leverage_mult:.2f}x")
    print(f"  Rules: 8 (4 EWMAC + 4 Breakout)")

    print(f"\nLeverage Statistics:")
    print(f"  Average leverage: {avg_leverage:.2f}x")
    print(f"  Max leverage: {max_leverage:.2f}x")

    print(f"\nNet Performance:")
    print(f"  Sharpe Ratio: {net_sharpe:.3f}")
    print(f"  Annual Return: {net_ann_return*100:.2f}%")
    print(f"  Annual Volatility: {net_ann_vol*100:.2f}%")
    print(f"  Max Drawdown: {max_dd*100:.2f}%")
    print(f"  Skewness: {returns_skew:.2f}")

    print(f"\nLast 5 Years:")
    print(f"  Sharpe: {recent_sharpe:.3f}")

    print("\n" + "-" * 60)
    print("VOLATILITY TARGETING VERIFICATION")
    print("-" * 60)

    avg_position_value = np.mean(daily_position_values)
    position_as_pct = avg_position_value / CAPITAL * 100

    print(f"  Average total position value: ${avg_position_value:,.2f} ({position_as_pct:.1f}% of capital)")
    print(f"  Realized annual vol: {net_ann_vol*100:.2f}%")
    print(f"  Target vol: {VOL_TARGET*100:.0f}%")
    print(f"  Vol achievement ratio: {net_ann_vol/VOL_TARGET:.2f}")

    return {
        'net_sharpe': net_sharpe,
        'net_ann_return': net_ann_return,
        'net_ann_vol': net_ann_vol,
        'max_drawdown': max_dd,
        'skewness': returns_skew,
        'recent_sharpe': recent_sharpe,
        'avg_leverage': avg_leverage,
        'max_leverage': max_leverage,
        'leverage_mult': leverage_mult,
        'returns': net_returns,
        'instruments': backtest_instruments,
    }


# =============================================================================
# CARRY BACKTEST
# =============================================================================

def run_carry_backtest() -> Dict:
    print("\n" + "=" * 80)
    print("CARRY BACKTEST V3 FIXED (With Leverage Multiplier)")
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
    backtest_instruments = [
        instr for instr in all_prices.keys()
        if len(all_funding[instr]) >= min_days
    ]
    backtest_instruments.sort()

    print(f"\nInstruments with {MIN_TOTAL_HISTORY_YEARS}+ years funding data: {len(backtest_instruments)}")
    print(f"  {', '.join(backtest_instruments)}")

    n_instruments = len(backtest_instruments)
    if n_instruments == 0:
        print("No instruments meet criteria")
        return {}

    all_vols = {}
    for instr in backtest_instruments:
        prices = all_prices[instr]
        vol = robust_vol_calc(prices)
        all_vols[instr] = vol

    avg_corr = 0.5
    idm = np.sqrt(n_instruments) / np.sqrt(1 + (n_instruments - 1) * avg_corr)
    idm = min(idm, 2.5)
    instrument_weight = 1.0 / n_instruments

    # Calculate leverage multiplier
    leverage_mult = calculate_leverage_multiplier(n_instruments, avg_corr, idm)

    print(f"\nRisk Parameters:")
    print(f"  IDM: {idm:.3f}")
    print(f"  Instrument weight: {instrument_weight:.4f}")
    print(f"  Leverage multiplier: {leverage_mult:.2f}x")

    all_dates = set()
    for funding in all_funding.values():
        all_dates.update(funding.index)
    all_dates = sorted(all_dates)

    start_date = None
    for date in all_dates:
        count = sum(1 for instr in backtest_instruments if date in all_funding[instr].index)
        if count >= 1:
            start_date = date
            break

    if start_date is None:
        return {}

    backtest_dates = [d for d in all_dates if d >= start_date]

    print(f"\nBacktest period: {backtest_dates[0]} to {backtest_dates[-1]}")
    print(f"Total days: {len(backtest_dates)}")

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

            funding_annualized = funding_rate * DAYS_PER_YEAR
            raw_carry_forecast = funding_annualized / annual_return_vol
            carry_scalar = 5.0
            carry_forecast = raw_carry_forecast * carry_scalar
            carry_forecast = np.clip(carry_forecast, -20, 20)

            # Position with leverage multiplier
            subsystem_value = (CAPITAL * VOL_TARGET) / annual_return_vol
            position_value = subsystem_value * idm * instrument_weight * leverage_mult * (carry_forecast / 10.0)

            carry_return = (abs(position_value) / CAPITAL) * funding_rate * np.sign(carry_forecast)

            daily_return += carry_return
            daily_pos_value += abs(position_value)

        portfolio_returns.append({'date': next_date, 'return': daily_return})
        daily_leverages.append(daily_pos_value / CAPITAL if CAPITAL > 0 else 0)

    returns_df = pd.DataFrame(portfolio_returns).set_index('date')
    gross_returns = returns_df['return']

    annual_cost = CARRY_ANNUAL_COST
    daily_cost = annual_cost / DAYS_PER_YEAR
    net_returns = gross_returns - daily_cost

    net_ann_return = net_returns.mean() * DAYS_PER_YEAR
    net_ann_vol = net_returns.std() * np.sqrt(DAYS_PER_YEAR)
    net_sharpe = net_ann_return / net_ann_vol if net_ann_vol > 0 else 0

    cumulative = (1 + net_returns).cumprod()
    max_dd = ((cumulative - cumulative.cummax()) / cumulative.cummax()).min()

    returns_skew = skew(net_returns.dropna())

    five_years_ago = backtest_dates[-1] - timedelta(days=5*365)
    recent = net_returns[net_returns.index >= five_years_ago]
    recent_sharpe = (recent.mean() * DAYS_PER_YEAR) / (recent.std() * np.sqrt(DAYS_PER_YEAR)) if len(recent) > 0 else 0

    avg_leverage = np.mean(daily_leverages)
    max_leverage = np.max(daily_leverages)

    print("\n" + "=" * 60)
    print("CARRY BACKTEST RESULTS")
    print("=" * 60)

    print(f"\nConfiguration:")
    print(f"  Capital: ${CAPITAL:,}")
    print(f"  Vol Target: {VOL_TARGET*100:.0f}%")
    print(f"  Instruments: {n_instruments}")
    print(f"  IDM: {idm:.3f}")
    print(f"  Leverage Multiplier: {leverage_mult:.2f}x")
    print(f"  Annual cost: {annual_cost*100:.1f}%")

    print(f"\nLeverage Statistics:")
    print(f"  Average leverage: {avg_leverage:.2f}x")
    print(f"  Max leverage: {max_leverage:.2f}x")

    print(f"\nNet Performance:")
    print(f"  Sharpe Ratio: {net_sharpe:.3f}")
    print(f"  Annual Return: {net_ann_return*100:.2f}%")
    print(f"  Annual Volatility: {net_ann_vol*100:.2f}%")
    print(f"  Max Drawdown: {max_dd*100:.2f}%")
    print(f"  Skewness: {returns_skew:.2f}")

    print(f"\nLast 5 Years:")
    print(f"  Sharpe: {recent_sharpe:.3f}")

    print("\n" + "-" * 60)
    print("VOL TARGETING VERIFICATION")
    print("-" * 60)
    print(f"  Realized annual vol: {net_ann_vol*100:.2f}%")
    print(f"  Target vol: {VOL_TARGET*100:.0f}%")
    print(f"  Vol achievement ratio: {net_ann_vol/VOL_TARGET:.2f}")

    return {
        'net_sharpe': net_sharpe,
        'net_ann_return': net_ann_return,
        'net_ann_vol': net_ann_vol,
        'max_drawdown': max_dd,
        'skewness': returns_skew,
        'recent_sharpe': recent_sharpe,
        'avg_leverage': avg_leverage,
        'max_leverage': max_leverage,
        'leverage_mult': leverage_mult,
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

    trend_returns = trend['returns']
    carry_returns = carry['returns']

    common = trend_returns.index.intersection(carry_returns.index)
    if len(common) < 252:
        print("Insufficient overlap")
        return {}

    t = trend_returns.loc[common]
    c = carry_returns.loc[common]

    corr = t.corr(c)
    print(f"\nCorrelation (Trend vs Carry): {corr:.3f}")
    print(f"Overlapping days: {len(common)}")

    print(f"\n{'Allocation':<15} {'Sharpe':>10} {'Ann Ret':>12} {'Ann Vol':>10} {'Skew':>8}")
    print("-" * 60)

    for carry_wt in [0.0, 0.2, 0.3, 0.4, 0.5, 0.6, 0.8, 1.0]:
        trend_wt = 1.0 - carry_wt
        combined = trend_wt * t + carry_wt * c

        ann_ret = combined.mean() * DAYS_PER_YEAR
        ann_vol = combined.std() * np.sqrt(DAYS_PER_YEAR)
        sharpe = ann_ret / ann_vol if ann_vol > 0 else 0
        sk = skew(combined.dropna())

        label = f"T{int(trend_wt*100)}/C{int(carry_wt*100)}"
        print(f"{label:<15} {sharpe:>10.3f} {ann_ret*100:>11.2f}% {ann_vol*100:>9.2f}% {sk:>+8.2f}")

    return {'correlation': corr}


# =============================================================================
# MAIN
# =============================================================================

def main():
    print("=" * 80)
    print("CORRECTED BACKTEST V3 FIXED - PROPER LEVERAGE MULTIPLIER")
    print("=" * 80)
    print(f"\nDate: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"Capital: ${CAPITAL:,}")
    print(f"Vol Target: {VOL_TARGET*100:.0f}%")
    print(f"Min history: {MIN_TOTAL_HISTORY_YEARS} years")
    print("\nApproach: Keep instrument weights, add leverage multiplier to scale")
    print("from base ~7% vol to 25% target.")

    trend = run_trend_backtest()
    carry = run_carry_backtest()

    combined = {}
    if trend and carry:
        combined = run_combined_analysis(trend, carry)

    print("\n" + "=" * 80)
    print("FINAL SUMMARY")
    print("=" * 80)

    if trend:
        print(f"""
TREND (8 rules, with {trend['leverage_mult']:.1f}x leverage):
  Sharpe: {trend['net_sharpe']:.3f}
  Annual Return: {trend['net_ann_return']*100:.2f}%
  Annual Vol: {trend['net_ann_vol']*100:.2f}% (target: 25%)
  Max DD: {trend['max_drawdown']*100:.2f}%
  Skew: {trend['skewness']:.2f}
  Avg Leverage: {trend['avg_leverage']:.2f}x
  Max Leverage: {trend['max_leverage']:.2f}x
  Instruments: {len(trend['instruments'])}
""")

    if carry:
        print(f"""
CARRY (with {carry['leverage_mult']:.1f}x leverage):
  Sharpe: {carry['net_sharpe']:.3f}
  Annual Return: {carry['net_ann_return']*100:.2f}%
  Annual Vol: {carry['net_ann_vol']*100:.2f}% (target: 25%)
  Max DD: {carry['max_drawdown']*100:.2f}%
  Skew: {carry['skewness']:.2f} (adjusted for survivorship: ~{carry['skewness']-0.7:.2f})
  Avg Leverage: {carry['avg_leverage']:.2f}x
  Max Leverage: {carry['max_leverage']:.2f}x
  Instruments: {len(carry['instruments'])}
""")

    if combined:
        print(f"CORRELATION: {combined['correlation']:.3f}")

    if trend and carry:
        print(f"""
INSTRUMENTS:
  Trend: {', '.join(trend['instruments'])}
  Carry: {', '.join(carry['instruments'])}
""")

    print("\n" + "=" * 80)
    print("LEVERAGE WARNING")
    print("=" * 80)
    print("""
This backtest uses leverage to achieve target volatility.
Positions can exceed capital, requiring margin/derivatives.

Risk considerations:
- Max leverage can reach 5x+ during strong trends
- Margin calls possible during extreme moves
- Consider position limits in live trading
""")


if __name__ == "__main__":
    main()
