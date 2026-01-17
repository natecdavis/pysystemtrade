"""
CORRECTED BACKTEST V3: With Leverage (Full Vol Targeting)
==========================================================
Key change from V2: Removes instrument weight from position sizing.
This allows positions to exceed capital (implicit leverage) and
achieves the 25% vol target.

Carver's formula WITHOUT weight restriction:
  position = subsystem × IDM × (forecast/10)

This means total notional exposure CAN exceed capital.
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

# Trading costs
ROUND_TRIP_COST = 0.003  # 0.3%
CARRY_ANNUAL_COST = 0.02  # 2% for carry (opening + rebalancing)

# Walk-forward rules
MIN_HISTORY_DAYS = 252  # 1 year before entry
MIN_TOTAL_HISTORY_YEARS = 3  # Only include if 3+ years total history

# Exclude stablecoins and low-vol assets
EXCLUDED_INSTRUMENTS = {
    'USDT', 'USDT_OMNI', 'USDT_ETH', 'USDT_TRX', 'USDT_AVAXC',
    'USDC', 'USDC_ETH', 'USDC_TRX', 'USDC_AVAXC',
    'DAI', 'BUSD', 'TUSD', 'TUSD_ETH', 'TUSD_TRX',
    'PAX', 'GUSD', 'HUSD', 'SAI',
    'PAXG', 'XAUT',  # Gold-backed
    'AUD', 'EUR', 'GBP', 'CHF', 'CAD',  # Fiat
}

# Collapse dates for survivorship
COLLAPSE_DATES = {
    'LUNA': pd.Timestamp('2022-05-12'),
    'FTT': pd.Timestamp('2022-11-11'),
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
# TRADING RULES WITH PROPER CALIBRATION
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
    """
    Calculate forecasts with proper calibration for crypto.
    """
    forecasts = {}
    scalars = {}

    # Crypto-calibrated scalars
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

    # EWMAC
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

    # Breakout
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

    # Combine with equal weights and FDM
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
# POSITION SIZING - WITH LEVERAGE (NO WEIGHT CONSTRAINT)
# =============================================================================

def calculate_position_leveraged(
    forecast: float,
    price: float,
    vol: float,  # Daily vol in price terms
    capital: float,
    vol_target: float,
    idm: float
) -> float:
    """
    Calculate position WITH LEVERAGE - no instrument weight constraint.

    Formula: position = subsystem × IDM × (forecast/10)

    This allows notional exposure to exceed capital, achieving target vol.
    """
    if vol <= 0 or price <= 0:
        return 0.0

    daily_return_vol = vol / price
    annual_return_vol = daily_return_vol * np.sqrt(DAYS_PER_YEAR)

    # Subsystem position at forecast=10 (targets vol_target for this instrument alone)
    subsystem_position = (capital * vol_target) / (price * annual_return_vol)

    # Apply IDM only (NO instrument weight)
    # This allows positions to exceed capital allocation
    position = subsystem_position * idm * (forecast / 10.0)

    return position


# =============================================================================
# TREND BACKTEST WITH LEVERAGE
# =============================================================================

def run_trend_backtest_v3() -> Dict:
    """
    Run trend backtest with leverage (positions can exceed capital).
    """
    print("\n" + "=" * 80)
    print("TREND BACKTEST V3 (WITH LEVERAGE)")
    print("=" * 80)

    # Load all price data
    all_instruments = get_all_instruments()
    all_prices = {}
    for instr in all_instruments:
        prices = load_price_data(instr)
        if len(prices) >= MIN_HISTORY_DAYS:
            all_prices[instr] = prices

    print(f"\nLoaded price data for {len(all_prices)} instruments")

    # Filter to instruments with MIN_TOTAL_HISTORY_YEARS, excluding stablecoins
    min_days = MIN_TOTAL_HISTORY_YEARS * DAYS_PER_YEAR
    eligible_instruments = [
        instr for instr, prices in all_prices.items()
        if len(prices) >= min_days and instr not in EXCLUDED_INSTRUMENTS
    ]
    eligible_instruments.sort(key=lambda x: -len(all_prices[x]))

    print(f"Instruments with {MIN_TOTAL_HISTORY_YEARS}+ years history: {len(eligible_instruments)}")

    # Use top 15 by history length
    backtest_instruments = eligible_instruments[:15]
    print(f"\nUsing {len(backtest_instruments)} instruments:")
    for i, instr in enumerate(backtest_instruments):
        days = len(all_prices[instr])
        years = days / DAYS_PER_YEAR
        print(f"  {i+1}. {instr}: {years:.1f} years ({days} days)")

    n_instruments = len(backtest_instruments)

    # Calculate IDM (unchanged)
    avg_corr = 0.6
    idm = np.sqrt(n_instruments) / np.sqrt(1 + (n_instruments - 1) * avg_corr)
    idm = min(idm, 2.5)

    print(f"\nIDM: {idm:.3f}")
    print(f"NOTE: No instrument weight applied to position sizing (leverage enabled)")

    # Pre-calculate forecasts and volatilities
    print("\nCalculating forecasts...")
    all_forecasts = {}
    all_vols = {}
    forecast_diagnostics = {}

    for instr in backtest_instruments:
        prices = all_prices[instr]
        forecasts, diag = calculate_forecasts_with_calibration(prices)
        vol = robust_vol_calc(prices)

        all_forecasts[instr] = forecasts
        all_vols[instr] = vol
        forecast_diagnostics[instr] = diag

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

            # Calculate position WITH LEVERAGE
            position = calculate_position_leveraged(
                forecast=forecast,
                price=price_today,
                vol=vol,
                capital=CAPITAL,
                vol_target=VOL_TARGET,
                idm=idm
            )

            # Track turnover
            position_change = abs(position - prev_positions.get(instr, 0.0))
            turnover_value += position_change * price_today
            prev_positions[instr] = position

            # Calculate P&L
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

    # Convert to series
    returns_df = pd.DataFrame(portfolio_returns).set_index('date')
    gross_returns = returns_df['pnl'] / CAPITAL

    # Trading costs (higher due to larger positions)
    backtest_years = len(backtest_dates) / DAYS_PER_YEAR
    annual_turnover = turnover_value / (CAPITAL * backtest_years)
    annual_cost_pct = annual_turnover * (ROUND_TRIP_COST / 2)

    print(f"\nTrading costs:")
    print(f"  Annual turnover: {annual_turnover:.1f}x capital")
    print(f"  Annual cost drag: {annual_cost_pct*100:.2f}%")

    # Apply costs
    daily_cost = annual_cost_pct / DAYS_PER_YEAR
    net_returns = gross_returns - daily_cost

    # Statistics
    gross_ann_return = gross_returns.mean() * DAYS_PER_YEAR
    gross_ann_vol = gross_returns.std() * np.sqrt(DAYS_PER_YEAR)
    gross_sharpe = gross_ann_return / gross_ann_vol if gross_ann_vol > 0 else 0

    net_ann_return = net_returns.mean() * DAYS_PER_YEAR
    net_ann_vol = net_returns.std() * np.sqrt(DAYS_PER_YEAR)
    net_sharpe = net_ann_return / net_ann_vol if net_ann_vol > 0 else 0

    # Drawdown
    cumulative = (1 + net_returns).cumprod()
    max_dd = ((cumulative - cumulative.cummax()) / cumulative.cummax()).min()

    # Skew
    returns_skew = skew(net_returns.dropna())

    # Last 5 years
    five_years_ago = backtest_dates[-1] - timedelta(days=5*365)
    recent = net_returns[net_returns.index >= five_years_ago]
    recent_sharpe = (recent.mean() * DAYS_PER_YEAR) / (recent.std() * np.sqrt(DAYS_PER_YEAR)) if len(recent) > 0 else 0

    # Leverage stats
    avg_leverage = np.mean(daily_leverages)
    max_leverage = np.max(daily_leverages)

    print("\n" + "=" * 60)
    print("TREND BACKTEST RESULTS (WITH LEVERAGE)")
    print("=" * 60)

    print(f"\nConfiguration:")
    print(f"  Capital: ${CAPITAL:,}")
    print(f"  Vol Target: {VOL_TARGET*100:.0f}%")
    print(f"  Instruments: {n_instruments}")
    print(f"  IDM: {idm:.3f}")
    print(f"  Rules: 8 (4 EWMAC + 4 Breakout)")
    print(f"  LEVERAGE ENABLED: Yes (no weight constraint)")

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

    # Verify vol targeting
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
        'returns': net_returns,
        'instruments': backtest_instruments,
    }


# =============================================================================
# CARRY BACKTEST WITH LEVERAGE
# =============================================================================

def run_carry_backtest_v3() -> Dict:
    """
    Run carry backtest with leverage (positions can exceed capital).
    """
    print("\n" + "=" * 80)
    print("CARRY BACKTEST V3 (WITH LEVERAGE)")
    print("=" * 80)

    # Load data
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

    # Check combined directory
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

    # Filter to instruments with 3+ years
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

    # Calculate volatilities for position sizing
    all_vols = {}
    for instr in backtest_instruments:
        prices = all_prices[instr]
        vol = robust_vol_calc(prices)
        all_vols[instr] = vol

    # Calculate IDM for carry
    avg_corr = 0.5  # Funding rates are less correlated than prices
    idm = np.sqrt(n_instruments) / np.sqrt(1 + (n_instruments - 1) * avg_corr)
    idm = min(idm, 2.5)

    print(f"\nIDM: {idm:.3f}")
    print(f"NOTE: No instrument weight applied to position sizing (leverage enabled)")

    # Get backtest dates
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

    # Run backtest with leveraged positions
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

            # Get volatility for position sizing
            if date not in all_vols[instr].index:
                continue
            vol = all_vols[instr].loc[date]
            if pd.isna(vol) or vol <= 0:
                continue

            # Calculate annual return volatility
            daily_return_vol = vol / price
            annual_return_vol = daily_return_vol * np.sqrt(DAYS_PER_YEAR)

            # Carry forecast: funding rate annualized / volatility
            funding_annualized = funding_rate * DAYS_PER_YEAR
            raw_carry_forecast = funding_annualized / annual_return_vol
            carry_scalar = 5.0
            carry_forecast = raw_carry_forecast * carry_scalar
            carry_forecast = np.clip(carry_forecast, -20, 20)

            # Position sizing WITH LEVERAGE (no weight constraint)
            # subsystem = (capital * vol_target) / (price * annual_vol)
            # position_value = subsystem * price * IDM * (forecast/10)
            #                = (capital * vol_target) / annual_vol * IDM * (forecast/10)
            position_value = (CAPITAL * VOL_TARGET * idm) / annual_return_vol * (carry_forecast / 10.0)

            # Return from funding
            # For carry: we earn funding_rate on our position value
            carry_return = (abs(position_value) / CAPITAL) * funding_rate * np.sign(carry_forecast)

            daily_return += carry_return
            daily_pos_value += abs(position_value)

        portfolio_returns.append({'date': next_date, 'return': daily_return})
        daily_leverages.append(daily_pos_value / CAPITAL if CAPITAL > 0 else 0)

    # Convert to series
    returns_df = pd.DataFrame(portfolio_returns).set_index('date')
    gross_returns = returns_df['return']

    # Apply costs
    annual_cost = CARRY_ANNUAL_COST
    daily_cost = annual_cost / DAYS_PER_YEAR
    net_returns = gross_returns - daily_cost

    # Statistics
    net_ann_return = net_returns.mean() * DAYS_PER_YEAR
    net_ann_vol = net_returns.std() * np.sqrt(DAYS_PER_YEAR)
    net_sharpe = net_ann_return / net_ann_vol if net_ann_vol > 0 else 0

    cumulative = (1 + net_returns).cumprod()
    max_dd = ((cumulative - cumulative.cummax()) / cumulative.cummax()).min()

    returns_skew = skew(net_returns.dropna())

    # Last 5 years
    five_years_ago = backtest_dates[-1] - timedelta(days=5*365)
    recent = net_returns[net_returns.index >= five_years_ago]
    recent_sharpe = (recent.mean() * DAYS_PER_YEAR) / (recent.std() * np.sqrt(DAYS_PER_YEAR)) if len(recent) > 0 else 0

    # Leverage stats
    avg_leverage = np.mean(daily_leverages)
    max_leverage = np.max(daily_leverages)

    print("\n" + "=" * 60)
    print("CARRY BACKTEST RESULTS (WITH LEVERAGE)")
    print("=" * 60)

    print(f"\nConfiguration:")
    print(f"  Capital: ${CAPITAL:,}")
    print(f"  Vol Target: {VOL_TARGET*100:.0f}%")
    print(f"  Instruments: {n_instruments}")
    print(f"  IDM: {idm:.3f}")
    print(f"  Annual cost: {annual_cost*100:.1f}%")
    print(f"  LEVERAGE ENABLED: Yes (no weight constraint)")

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

    # Survivorship bias analysis
    print("\n" + "-" * 60)
    print("SURVIVORSHIP BIAS IMPACT ON SKEW")
    print("-" * 60)

    print(f"""
Missing tokens: LUNA, FTT

Impact on skew:
- Current skew: {returns_skew:.2f}
- These events would add extreme negative returns
- Estimated adjusted skew: {returns_skew - 0.5:.2f} to {returns_skew - 1.0:.2f}

Note: Without actual LUNA/FTT funding data, this is an estimate.
The carry strategy's reported skew is likely OVERSTATED by 0.5-1.0 points.
""")

    return {
        'net_sharpe': net_sharpe,
        'net_ann_return': net_ann_return,
        'net_ann_vol': net_ann_vol,
        'max_drawdown': max_dd,
        'skewness': returns_skew,
        'recent_sharpe': recent_sharpe,
        'avg_leverage': avg_leverage,
        'max_leverage': max_leverage,
        'returns': net_returns,
        'instruments': backtest_instruments,
    }


# =============================================================================
# COMBINED ANALYSIS
# =============================================================================

def run_combined_analysis(trend: Dict, carry: Dict):
    """Analyze combined portfolio."""
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
    print("CORRECTED BACKTEST V3 - WITH LEVERAGE")
    print("=" * 80)
    print(f"\nDate: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"Capital: ${CAPITAL:,}")
    print(f"Vol Target: {VOL_TARGET*100:.0f}%")
    print(f"Min history: {MIN_TOTAL_HISTORY_YEARS} years")
    print("\nKEY CHANGE: Leverage enabled (no instrument weight in position sizing)")
    print("This allows positions to exceed capital to achieve target volatility.")

    trend = run_trend_backtest_v3()
    carry = run_carry_backtest_v3()

    combined = {}
    if trend and carry:
        combined = run_combined_analysis(trend, carry)

    print("\n" + "=" * 80)
    print("FINAL SUMMARY")
    print("=" * 80)

    if trend:
        print(f"""
TREND (8 rules, WITH LEVERAGE):
  Sharpe: {trend['net_sharpe']:.3f}
  Annual Return: {trend['net_ann_return']*100:.2f}%
  Annual Vol: {trend['net_ann_vol']*100:.2f}%
  Max DD: {trend['max_drawdown']*100:.2f}%
  Skew: {trend['skewness']:.2f}
  Avg Leverage: {trend['avg_leverage']:.2f}x
  Max Leverage: {trend['max_leverage']:.2f}x
  Instruments: {len(trend['instruments'])}
""")

    if carry:
        print(f"""
CARRY (WITH LEVERAGE):
  Sharpe: {carry['net_sharpe']:.3f}
  Annual Return: {carry['net_ann_return']*100:.2f}%
  Annual Vol: {carry['net_ann_vol']*100:.2f}%
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
This backtest assumes you CAN use leverage (margin trading).
If trading spot-only without leverage, use V2 which caps positions to capital.

With leverage:
- Position sizes can exceed your capital
- Requires margin account or derivatives
- Higher risk during drawdowns
- Margin calls possible in extreme moves
""")


if __name__ == "__main__":
    main()
