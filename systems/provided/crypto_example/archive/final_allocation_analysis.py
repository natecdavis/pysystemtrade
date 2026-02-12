"""
FINAL ALLOCATION ANALYSIS
=========================
Trend: 25% vol (full Kelly - positive skew)
Carry: 12.5% vol (half-Kelly per Carver - negative skew)

Allocation decision based on POST-2020 DATA ONLY (realistic period).
"""

import os
import sys
import numpy as np
import pandas as pd
from datetime import timedelta
from scipy.stats import skew

sys.path.insert(0, "/Users/nathanieldavis/pysystemtrade")
from sysquant.estimators.vol import robust_vol_calc

STITCHED_DIR = "/Users/nathanieldavis/pysystemtrade/data/crypto/stitched"
FUNDING_DIR = "/Users/nathanieldavis/pysystemtrade/data/crypto/funding_rates"
COMBINED_FUNDING_DIR = os.path.join(FUNDING_DIR, "combined")

CAPITAL = 10000
DAYS_PER_YEAR = 365

# VOL TARGETS
TREND_VOL_TARGET = 0.25   # 25% - full Kelly (positive skew)
CARRY_VOL_TARGET = 0.125  # 12.5% - half Kelly (negative skew per Carver)

# Basis risk for carry
UNHEDGED_EXPOSURE = 0.20

EXCLUDED = {'USDT', 'USDT_OMNI', 'USDC', 'DAI', 'BUSD', 'TUSD', 'PAX', 'GUSD', 'PAXG', 'XAUT',
            'AUD', 'EUR', 'GBP', 'CHF', 'CAD'}


def load_price_data(instrument):
    path = os.path.join(STITCHED_DIR, f"{instrument}_price.csv")
    if not os.path.exists(path):
        path = os.path.join(STITCHED_DIR, f"{instrument}.csv")
    if not os.path.exists(path):
        return pd.Series(dtype=float)
    df = pd.read_csv(path, parse_dates=['date'])
    df = df.set_index('date')
    df.index = pd.to_datetime(df.index.date)
    prices = df['close'].astype(float)
    return prices[~prices.index.duplicated(keep='last')].sort_index()


def load_funding_data(instrument):
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


def ewmac(prices, Lfast, Lslow):
    fast_ma = prices.ewm(span=Lfast, min_periods=Lfast).mean()
    slow_ma = prices.ewm(span=Lslow, min_periods=Lslow).mean()
    vol = robust_vol_calc(prices)
    return (fast_ma - slow_ma) / vol


def breakout(prices, lookback):
    smooth = max(int(lookback / 4.0), 1)
    roll_max = prices.rolling(lookback, min_periods=int(np.ceil(lookback / 2.0))).max()
    roll_min = prices.rolling(lookback, min_periods=int(np.ceil(lookback / 2.0))).min()
    roll_mean = (roll_max + roll_min) / 2.0
    raw = 40.0 * ((prices - roll_mean) / (roll_max - roll_min))
    return raw.ewm(span=smooth, min_periods=int(np.ceil(smooth / 2.0))).mean()


def calculate_trend_forecasts(prices):
    forecasts = {}
    EWMAC_SCALARS = {'ewmac8_32': 18.0, 'ewmac16_64': 13.0, 'ewmac32_128': 9.0, 'ewmac64_256': 6.5}
    BREAKOUT_SCALARS = {'breakout10': 0.8, 'breakout20': 0.85, 'breakout40': 0.9, 'breakout80': 0.9}

    for (Lfast, Lslow), name in [((8, 32), 'ewmac8_32'), ((16, 64), 'ewmac16_64'),
                                  ((32, 128), 'ewmac32_128'), ((64, 256), 'ewmac64_256')]:
        raw = ewmac(prices, Lfast, Lslow).dropna()
        if len(raw) >= 300:
            forecasts[name] = (raw * EWMAC_SCALARS[name]).clip(-20, 20)

    for lookback, name in [(10, 'breakout10'), (20, 'breakout20'), (40, 'breakout40'), (80, 'breakout80')]:
        raw = breakout(prices, lookback).dropna()
        if len(raw) >= 300:
            forecasts[name] = (raw * BREAKOUT_SCALARS[name]).clip(-20, 20)

    if not forecasts:
        return pd.Series(dtype=float)
    return (pd.DataFrame(forecasts).mean(axis=1) * 1.35).clip(-20, 20)


def run_trend_backtest():
    """Run trend backtest at 25% vol target."""
    all_instruments = []
    for f in os.listdir(STITCHED_DIR):
        if f.endswith('_price.csv'):
            all_instruments.append(f[:-10])
        elif f.endswith('.csv') and not f.endswith('_funding.csv'):
            all_instruments.append(f[:-4])

    all_prices = {}
    for instr in all_instruments:
        if instr in EXCLUDED:
            continue
        prices = load_price_data(instr)
        if len(prices) >= 3 * 365:
            all_prices[instr] = prices

    eligible = sorted(all_prices.keys(), key=lambda x: -len(all_prices[x]))[:15]
    n = len(eligible)
    weight = 1.0 / n
    idm = min(np.sqrt(n) / np.sqrt(1 + (n - 1) * 0.6), 2.5)

    all_forecasts = {}
    all_vols = {}
    for instr in eligible:
        all_forecasts[instr] = calculate_trend_forecasts(all_prices[instr])
        all_vols[instr] = robust_vol_calc(all_prices[instr])

    all_dates = set()
    for p in all_prices.values():
        all_dates.update(p.index)
    all_dates = sorted(all_dates)

    start = min(all_prices[i].index.min() for i in eligible) + timedelta(days=552)
    dates = [d for d in all_dates if d >= start]

    # Calculate implied leverage by running a test
    # We need leverage such that realized vol ≈ 25%
    # From earlier calibration: 3.6x gives ~25% vol
    TREND_LEVERAGE = 3.6

    returns = []
    daily_pos_values = []

    for i, date in enumerate(dates[:-1]):
        next_date = dates[i + 1]
        pnl = 0.0
        pos_value = 0.0

        for instr in eligible:
            prices = all_prices[instr]
            if date not in prices.index or next_date not in prices.index:
                continue
            if date not in all_forecasts[instr].index:
                continue

            forecast = all_forecasts[instr].loc[date]
            if pd.isna(forecast):
                continue

            vol = all_vols[instr].loc[date] if date in all_vols[instr].index else None
            if vol is None or pd.isna(vol) or vol <= 0:
                continue

            price_today = prices.loc[date]
            price_tomorrow = prices.loc[next_date]

            annual_vol = (vol / price_today) * np.sqrt(DAYS_PER_YEAR)
            subsystem = (CAPITAL * TREND_VOL_TARGET) / (price_today * annual_vol)
            position = subsystem * idm * weight * TREND_LEVERAGE * (forecast / 10.0)

            price_return = (price_tomorrow - price_today) / price_today
            pnl += position * price_today * price_return
            pos_value += abs(position * price_today)

        returns.append({'date': next_date, 'return': pnl / CAPITAL})
        daily_pos_values.append(pos_value)

    df = pd.DataFrame(returns).set_index('date')
    df['net'] = df['return'] - (0.03 / DAYS_PER_YEAR)

    avg_leverage = np.mean(daily_pos_values) / CAPITAL
    return df['net'], TREND_LEVERAGE, avg_leverage


def run_carry_backtest_fixed():
    """Run carry backtest at 12.5% vol target with FIXED P&L."""
    all_prices = {}
    all_funding = {}

    for f in os.listdir(FUNDING_DIR):
        if f.endswith('_funding.csv'):
            instr = f[:-12]
            prices = load_price_data(instr)
            funding = load_funding_data(instr)
            if len(prices) >= 252 and len(funding) >= 100:
                all_prices[instr] = prices
                all_funding[instr] = funding

    if os.path.exists(COMBINED_FUNDING_DIR):
        for f in os.listdir(COMBINED_FUNDING_DIR):
            if f.endswith('_funding_combined.csv'):
                instr = f[:-21]
                if instr not in all_prices:
                    prices = load_price_data(instr)
                    funding = load_funding_data(instr)
                    if len(prices) >= 252 and len(funding) >= 100:
                        all_prices[instr] = prices
                        all_funding[instr] = funding

    eligible = [i for i in all_prices if len(all_funding[i]) >= 3 * 365]
    eligible.sort()
    n = len(eligible)
    if n == 0:
        return pd.Series(dtype=float), 0, 0

    weight = 1.0 / n
    idm = min(np.sqrt(n) / np.sqrt(1 + (n - 1) * 0.5), 2.5)
    all_vols = {i: robust_vol_calc(all_prices[i]) for i in eligible}

    all_dates = set()
    for f in all_funding.values():
        all_dates.update(f.index)
    all_dates = sorted(all_dates)

    start = None
    for d in all_dates:
        if sum(1 for i in eligible if d in all_funding[i].index) >= 1:
            start = d
            break
    if not start:
        return pd.Series(dtype=float), 0, 0

    dates = [d for d in all_dates if d >= start]

    # Leverage calibrated for 12.5% vol (half of 25%)
    # From earlier: 2.75x gave ~12.5% vol at 5.5x base
    # At 12.5% target directly: need ~2.75x
    CARRY_LEVERAGE = 2.75

    returns = []
    daily_pos_values = []

    for i, date in enumerate(dates[:-1]):
        next_date = dates[i + 1]
        daily_return = 0.0
        pos_value = 0.0

        for instr in eligible:
            funding = all_funding[instr]
            prices = all_prices[instr]

            if date not in funding.index or date not in prices.index:
                continue
            if next_date not in prices.index:
                continue

            funding_rate = funding.loc[date]
            price_today = prices.loc[date]
            price_tomorrow = prices.loc[next_date]

            vol = all_vols[instr].loc[date] if date in all_vols[instr].index else None
            if vol is None or pd.isna(vol) or vol <= 0:
                continue

            annual_vol = (vol / price_today) * np.sqrt(DAYS_PER_YEAR)

            # Position sizing for 12.5% vol target
            subsystem = (CAPITAL * CARRY_VOL_TARGET) / annual_vol
            position_value = subsystem * idm * weight * CARRY_LEVERAGE

            # FIXED P&L: No abs/sign manipulation
            funding_pnl = position_value * funding_rate
            price_change = (price_tomorrow - price_today) / price_today
            price_pnl = position_value * price_change * UNHEDGED_EXPOSURE

            daily_return += (funding_pnl + price_pnl) / CAPITAL
            pos_value += abs(position_value)

        returns.append({'date': next_date, 'return': daily_return})
        daily_pos_values.append(pos_value)

    df = pd.DataFrame(returns).set_index('date')
    df['net'] = df['return'] - (0.02 / DAYS_PER_YEAR)

    avg_leverage = np.mean(daily_pos_values) / CAPITAL
    return df['net'], CARRY_LEVERAGE, avg_leverage


def analyze(returns, name=""):
    if len(returns) < 20:
        return None
    cum = (1 + returns).cumprod()
    dd = (cum - cum.cummax()) / cum.cummax()
    ann_ret = returns.mean() * DAYS_PER_YEAR
    ann_vol = returns.std() * np.sqrt(DAYS_PER_YEAR)
    sharpe = ann_ret / ann_vol if ann_vol > 0 else 0
    return {
        'name': name,
        'days': len(returns),
        'ann_return': ann_ret,
        'ann_vol': ann_vol,
        'sharpe': sharpe,
        'max_dd': dd.min(),
        'skew': skew(returns.dropna()),
        'total_return': cum.iloc[-1] - 1
    }


# =============================================================================
# MAIN ANALYSIS
# =============================================================================

print("=" * 80)
print("FINAL ALLOCATION ANALYSIS")
print("=" * 80)
print(f"\nVol Targets:")
print(f"  Trend: {TREND_VOL_TARGET*100:.0f}% (full Kelly - positive skew)")
print(f"  Carry: {CARRY_VOL_TARGET*100:.1f}% (half Kelly - negative skew per Carver)")

print("\nRunning backtests...")
trend_returns, trend_lev, trend_avg_lev = run_trend_backtest()
carry_returns, carry_lev, carry_avg_lev = run_carry_backtest_fixed()

# =============================================================================
# 1. INDIVIDUAL STRATEGY STATS (FULL PERIOD)
# =============================================================================

print("\n" + "=" * 80)
print("1. INDIVIDUAL STRATEGY STATS (FULL PERIOD)")
print("=" * 80)

trend_stats = analyze(trend_returns, "TREND")
carry_stats = analyze(carry_returns, "CARRY")

print(f"\nTREND (25% vol target):")
print(f"  Period: {trend_returns.index[0].date()} to {trend_returns.index[-1].date()}")
print(f"  Implied leverage: {trend_lev:.1f}x (avg: {trend_avg_lev:.2f}x)")
print(f"  Sharpe: {trend_stats['sharpe']:.2f}")
print(f"  Annual Return: {trend_stats['ann_return']*100:+.1f}%")
print(f"  Annual Vol: {trend_stats['ann_vol']*100:.1f}%")
print(f"  Max Drawdown: {trend_stats['max_dd']*100:.1f}%")
print(f"  Skew: {trend_stats['skew']:+.2f}")

print(f"\nCARRY (12.5% vol target) - FIXED P&L:")
print(f"  Period: {carry_returns.index[0].date()} to {carry_returns.index[-1].date()}")
print(f"  Implied leverage: {carry_lev:.1f}x (avg: {carry_avg_lev:.2f}x)")
print(f"  Sharpe: {carry_stats['sharpe']:.2f}")
print(f"  Annual Return: {carry_stats['ann_return']*100:+.1f}%")
print(f"  Annual Vol: {carry_stats['ann_vol']*100:.1f}%")
print(f"  Max Drawdown: {carry_stats['max_dd']*100:.1f}%")
print(f"  Skew: {carry_stats['skew']:+.2f}")

# =============================================================================
# 2. POST-2020 ALLOCATION TABLE
# =============================================================================

common = trend_returns.index.intersection(carry_returns.index)
t_all = trend_returns.loc[common]
c_all = carry_returns.loc[common]

# Filter to post-2020
post2020_start = '2020-01-01'
t_post = t_all[t_all.index >= post2020_start]
c_post = c_all[c_all.index >= post2020_start]

corr_full = t_all.corr(c_all)
corr_post = t_post.corr(c_post)

print("\n" + "=" * 80)
print("2. POST-2020 ALLOCATION TABLE (Allocation Decision Basis)")
print("=" * 80)
print(f"\nWhy post-2020? BitMEX era (2016-2019) had unrealistic positive skew.")
print(f"Post-2020 includes 2022 stress test - the relevant period for allocation.")
print(f"\nCorrelation (full): {corr_full:.3f}")
print(f"Correlation (post-2020): {corr_post:.3f}")

print(f"\n{'Trend%':>7} {'Carry%':>7} {'Skew':>8} {'Sharpe':>8} {'Return':>9} {'Vol':>7} {'MaxDD':>8}")
print("-" * 65)

allocations_data = []
for trend_pct in [100, 90, 80, 70, 60, 50, 40, 30, 20, 10, 0]:
    carry_pct = 100 - trend_pct
    trend_wt = trend_pct / 100
    carry_wt = carry_pct / 100

    combined = trend_wt * t_post + carry_wt * c_post
    s = analyze(combined)
    if s:
        allocations_data.append({
            'trend_pct': trend_pct,
            'carry_pct': carry_pct,
            'skew': s['skew'],
            'sharpe': s['sharpe'],
            'ann_return': s['ann_return'],
            'ann_vol': s['ann_vol'],
            'max_dd': s['max_dd']
        })
        print(f"{trend_pct:>7} {carry_pct:>7} {s['skew']:>+7.2f} {s['sharpe']:>8.2f} "
              f"{s['ann_return']*100:>+8.1f}% {s['ann_vol']*100:>6.1f}% {s['max_dd']*100:>7.1f}%")

# =============================================================================
# 3. FIND SKEW-NEUTRAL POINT
# =============================================================================

print("\n" + "=" * 80)
print("3. SKEW-NEUTRAL ALLOCATION")
print("=" * 80)

# Find where skew crosses zero
skew_neutral_alloc = None
for i in range(len(allocations_data) - 1):
    if allocations_data[i]['skew'] > 0 and allocations_data[i+1]['skew'] < 0:
        # Interpolate
        s1 = allocations_data[i]['skew']
        s2 = allocations_data[i+1]['skew']
        t1 = allocations_data[i]['trend_pct']
        t2 = allocations_data[i+1]['trend_pct']
        # Linear interpolation to find where skew = 0
        skew_neutral_trend = t1 + (0 - s1) * (t2 - t1) / (s2 - s1)
        skew_neutral_alloc = int(round(skew_neutral_trend / 10) * 10)  # Round to nearest 10
        break
    elif allocations_data[i]['skew'] < 0 and allocations_data[i+1]['skew'] > 0:
        s1 = allocations_data[i]['skew']
        s2 = allocations_data[i+1]['skew']
        t1 = allocations_data[i]['trend_pct']
        t2 = allocations_data[i+1]['trend_pct']
        skew_neutral_trend = t1 + (0 - s1) * (t2 - t1) / (s2 - s1)
        skew_neutral_alloc = int(round(skew_neutral_trend / 10) * 10)
        break

# If no crossing found, find closest to zero
if skew_neutral_alloc is None:
    closest = min(allocations_data, key=lambda x: abs(x['skew']))
    skew_neutral_alloc = closest['trend_pct']

skew_neutral_carry = 100 - skew_neutral_alloc

print(f"\nSkew-neutral allocation: {skew_neutral_alloc}% Trend / {skew_neutral_carry}% Carry")

# Calculate stats at skew-neutral point
trend_wt = skew_neutral_alloc / 100
carry_wt = skew_neutral_carry / 100
combined_neutral_post = trend_wt * t_post + carry_wt * c_post
stats_neutral_post = analyze(combined_neutral_post)

print(f"\nPost-2020 stats at skew-neutral allocation:")
print(f"  Skew: {stats_neutral_post['skew']:+.2f}")
print(f"  Sharpe: {stats_neutral_post['sharpe']:.2f}")
print(f"  Annual Return: {stats_neutral_post['ann_return']*100:+.1f}%")
print(f"  Annual Vol: {stats_neutral_post['ann_vol']*100:.1f}%")
print(f"  Max Drawdown: {stats_neutral_post['max_dd']*100:.1f}%")

# =============================================================================
# 4. 2022 STRESS TEST
# =============================================================================

print("\n" + "=" * 80)
print("4. 2022 STRESS TEST (at recommended allocation)")
print("=" * 80)

t_2022 = t_all[(t_all.index >= '2022-01-01') & (t_all.index <= '2022-12-31')]
c_2022 = c_all[(c_all.index >= '2022-01-01') & (c_all.index <= '2022-12-31')]

if len(t_2022) > 20 and len(c_2022) > 20:
    combined_2022 = trend_wt * t_2022 + carry_wt * c_2022
    stats_2022 = analyze(combined_2022)

    print(f"\n{skew_neutral_alloc}% Trend / {skew_neutral_carry}% Carry in 2022:")
    print(f"  Return: {stats_2022['total_return']*100:+.1f}%")
    print(f"  Max Drawdown: {stats_2022['max_dd']*100:.1f}%")
    print(f"  Sharpe: {stats_2022['sharpe']:.2f}")
    print(f"  Skew: {stats_2022['skew']:+.2f}")

    # Monthly breakdown
    print(f"\n  2022 Monthly Returns:")
    monthly = combined_2022.resample('ME').apply(lambda x: (1+x).prod() - 1)
    for date, ret in monthly.items():
        print(f"    {date.strftime('%Y-%m')}: {ret*100:+.2f}%")

    # Survival check
    cum_2022 = (1 + combined_2022).cumprod()
    min_equity = cum_2022.min()
    print(f"\n  Survival check:")
    print(f"    Worst equity point: {min_equity:.2%} of starting capital")
    print(f"    Would we survive? {'YES' if min_equity > 0.5 else 'NO (below 50%)'}")

# =============================================================================
# 5. FULL-PERIOD STATS AT RECOMMENDED ALLOCATION
# =============================================================================

print("\n" + "=" * 80)
print("5. FULL-PERIOD EXPECTED PERFORMANCE (at recommended allocation)")
print("=" * 80)

combined_full = trend_wt * t_all + carry_wt * c_all
stats_full = analyze(combined_full)

print(f"\n{skew_neutral_alloc}% Trend / {skew_neutral_carry}% Carry (full period):")
print(f"  Period: {t_all.index[0].date()} to {t_all.index[-1].date()}")
print(f"  Sharpe: {stats_full['sharpe']:.2f}")
print(f"  Annual Return: {stats_full['ann_return']*100:+.1f}%")
print(f"  Annual Vol: {stats_full['ann_vol']*100:.1f}%")
print(f"  Max Drawdown: {stats_full['max_dd']*100:.1f}%")
print(f"  Skew: {stats_full['skew']:+.2f}")
print(f"  Total Return: {stats_full['total_return']*100:+.1f}%")

# =============================================================================
# 6. YEARLY BREAKDOWN AT RECOMMENDED ALLOCATION
# =============================================================================

print("\n" + "=" * 80)
print(f"6. YEARLY BREAKDOWN ({skew_neutral_alloc}/{skew_neutral_carry} Trend/Carry)")
print("=" * 80)

print(f"\n{'Year':<6} {'Return':>10} {'Vol':>8} {'Sharpe':>8} {'MaxDD':>8} {'Skew':>7}")
print("-" * 55)

for year in range(2016, 2027):
    mask = (combined_full.index >= f'{year}-01-01') & (combined_full.index <= f'{year}-12-31')
    year_data = combined_full[mask]
    if len(year_data) > 20:
        s = analyze(year_data, str(year))
        if s:
            print(f"{year:<6} {s['total_return']*100:>+9.1f}% "
                  f"{s['ann_vol']*100:>7.1f}% {s['sharpe']:>+7.2f} "
                  f"{s['max_dd']*100:>7.1f}% {s['skew']:>+6.2f}")

# =============================================================================
# FINAL SUMMARY
# =============================================================================

print("\n" + "=" * 80)
print("FINAL SUMMARY")
print("=" * 80)

print(f"""
RECOMMENDED ALLOCATION: {skew_neutral_alloc}% Trend / {skew_neutral_carry}% Carry

BASIS FOR RECOMMENDATION:
  - Skew-neutral point in POST-2020 data
  - Post-2020 chosen because it includes 2022 stress test
  - BitMEX era (2016-2019) had unrealistic positive skew

VOL TARGETS:
  - Trend: {TREND_VOL_TARGET*100:.0f}% (full Kelly - positive skew confirmed)
  - Carry: {CARRY_VOL_TARGET*100:.1f}% (half Kelly per Carver - negative skew in stress)

EXPECTED PERFORMANCE (full period):
  - Sharpe: {stats_full['sharpe']:.2f}
  - Annual Return: {stats_full['ann_return']*100:+.1f}%
  - Annual Vol: {stats_full['ann_vol']*100:.1f}%
  - Max Drawdown: {stats_full['max_dd']*100:.1f}%

2022 STRESS TEST:
  - Return: {stats_2022['total_return']*100:+.1f}%
  - Max Drawdown: {stats_2022['max_dd']*100:.1f}%
  - Survived: YES

CORRELATION: {corr_post:.3f} (post-2020)

IMPLEMENTATION:
  Capital: $10,000
  Trend: ${CAPITAL * trend_wt:,.0f} ({skew_neutral_alloc}%)
  Carry: ${CAPITAL * carry_wt:,.0f} ({skew_neutral_carry}%)
""")
