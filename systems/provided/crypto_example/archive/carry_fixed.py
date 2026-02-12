"""
CARRY BACKTEST - FIXED P&L CALCULATION
======================================
Bug fix: Remove abs() and sign() manipulation that made carry always profitable.

Correct model (Option A - Unidirectional):
- Always long spot, short perp (when forecast > 0)
- Positive funding = receive payment (profit)
- Negative funding = pay funding (loss)

This is the realistic model where you CAN'T magically always be on the receiving end.
"""

import os
import sys
import numpy as np
import pandas as pd
from datetime import datetime
from scipy.stats import skew

sys.path.insert(0, "/Users/nathanieldavis/pysystemtrade")
from sysquant.estimators.vol import robust_vol_calc

STITCHED_DIR = "/Users/nathanieldavis/pysystemtrade/data/crypto/stitched"
FUNDING_DIR = "/Users/nathanieldavis/pysystemtrade/data/crypto/funding_rates"
COMBINED_FUNDING_DIR = os.path.join(FUNDING_DIR, "combined")

CAPITAL = 10000
DAYS_PER_YEAR = 365

# Settings
CARRY_VOL_TARGET = 0.10  # 10% conservative
CARRY_LEVERAGE = 2.2
UNHEDGED_EXPOSURE = 0.20  # 20% basis risk


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


def run_carry_backtest_fixed():
    """
    Run carry backtest with CORRECT P&L calculation.

    FIXED: No abs() or sign() manipulation.
    When funding is negative, you PAY - this is the real risk.
    """
    print("=" * 80)
    print("CARRY BACKTEST - FIXED P&L CALCULATION")
    print("=" * 80)
    print(f"\nSettings:")
    print(f"  Vol target: {CARRY_VOL_TARGET*100:.0f}%")
    print(f"  Leverage: {CARRY_LEVERAGE:.1f}x")
    print(f"  Unhedged exposure: {UNHEDGED_EXPOSURE*100:.0f}%")

    # Load data
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

    # Filter to 3+ years
    min_days = 3 * 365
    eligible = [i for i in all_prices if len(all_funding[i]) >= min_days]
    eligible.sort()

    n = len(eligible)
    if n == 0:
        print("No instruments found!")
        return None

    print(f"\nInstruments ({n}): {', '.join(eligible)}")

    weight = 1.0 / n
    idm = min(np.sqrt(n) / np.sqrt(1 + (n - 1) * 0.5), 2.5)

    print(f"IDM: {idm:.3f}, Weight: {weight:.4f}")

    # Calculate vols
    all_vols = {i: robust_vol_calc(all_prices[i]) for i in eligible}

    # Get dates
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
        return None

    dates = [d for d in all_dates if d >= start]
    print(f"\nBacktest: {dates[0].date()} to {dates[-1].date()} ({len(dates)} days)")

    # Run backtest with FIXED P&L calculation
    returns = []

    for i, date in enumerate(dates[:-1]):
        next_date = dates[i + 1]
        daily_return = 0.0

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

            # Position sizing based on volatility
            # We hold the carry trade when we expect positive funding
            # Position is sized based on vol target
            subsystem = (CAPITAL * CARRY_VOL_TARGET) / annual_vol
            position_value = subsystem * idm * weight * CARRY_LEVERAGE

            # FIXED P&L CALCULATION:
            # Option A: Unidirectional carry (always long spot, short perp)
            # - Positive funding = we receive = profit
            # - Negative funding = we pay = loss
            # No sign manipulation!

            funding_pnl = position_value * funding_rate

            # Price exposure from imperfect hedge (basis risk)
            price_change = (price_tomorrow - price_today) / price_today
            # We're long spot, so positive price change = profit on unhedged portion
            price_pnl = position_value * price_change * UNHEDGED_EXPOSURE

            daily_return += (funding_pnl + price_pnl) / CAPITAL

        returns.append({'date': next_date, 'return': daily_return})

    df = pd.DataFrame(returns).set_index('date')
    df['net'] = df['return'] - (0.02 / DAYS_PER_YEAR)  # 2% annual costs

    return df['net']


def analyze(returns, name):
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
        'total_return': cum.iloc[-1] - 1,
        'cumulative': cum,
        'drawdown': dd
    }


# Run the fixed backtest
returns = run_carry_backtest_fixed()

if returns is not None:
    # Full history analysis
    stats = analyze(returns, "CARRY (FIXED)")

    print("\n" + "=" * 80)
    print("FULL HISTORY RESULTS (FIXED)")
    print("=" * 80)
    print(f"\n  Sharpe: {stats['sharpe']:.2f}")
    print(f"  Annual Return: {stats['ann_return']*100:+.1f}%")
    print(f"  Annual Vol: {stats['ann_vol']*100:.1f}%")
    print(f"  Max Drawdown: {stats['max_dd']*100:.1f}%")
    print(f"  Skew: {stats['skew']:.2f}")
    print(f"  Total Return: {stats['total_return']*100:+.1f}%")

    # 2022 specifically
    print("\n" + "=" * 80)
    print("2022 PERFORMANCE (THE PAIN YEAR)")
    print("=" * 80)

    returns_2022 = returns[(returns.index >= '2022-01-01') & (returns.index <= '2022-12-31')]
    if len(returns_2022) > 20:
        stats_2022 = analyze(returns_2022, "2022")
        print(f"\n  2022 Return: {stats_2022['total_return']*100:+.1f}%")
        print(f"  2022 Vol: {stats_2022['ann_vol']*100:.1f}%")
        print(f"  2022 Sharpe: {stats_2022['sharpe']:.2f}")
        print(f"  2022 Max DD: {stats_2022['max_dd']*100:.1f}%")
        print(f"  2022 Skew: {stats_2022['skew']:.2f}")

        # Monthly breakdown for 2022
        print("\n  2022 Monthly Returns:")
        monthly = returns_2022.resample('ME').apply(lambda x: (1+x).prod() - 1)
        for date, ret in monthly.items():
            print(f"    {date.strftime('%Y-%m')}: {ret*100:+.2f}%")

    # Post-2020 analysis
    print("\n" + "=" * 80)
    print("POST-2020 STATISTICS")
    print("=" * 80)

    returns_post2020 = returns[returns.index >= '2020-01-01']
    if len(returns_post2020) > 100:
        stats_post2020 = analyze(returns_post2020, "Post-2020")
        print(f"\n  Sharpe: {stats_post2020['sharpe']:.2f}")
        print(f"  Annual Return: {stats_post2020['ann_return']*100:+.1f}%")
        print(f"  Annual Vol: {stats_post2020['ann_vol']*100:.1f}%")
        print(f"  Max Drawdown: {stats_post2020['max_dd']*100:.1f}%")
        print(f"  Skew: {stats_post2020['skew']:.2f}")

    # Yearly breakdown
    print("\n" + "=" * 80)
    print("YEARLY BREAKDOWN")
    print("=" * 80)
    print(f"\n{'Year':<6} {'Return':>10} {'Vol':>8} {'Sharpe':>8} {'MaxDD':>8} {'Skew':>7}")
    print("-" * 55)

    for year in range(2016, 2027):
        mask = (returns.index >= f'{year}-01-01') & (returns.index <= f'{year}-12-31')
        year_data = returns[mask]
        if len(year_data) > 20:
            s = analyze(year_data, str(year))
            if s:
                print(f"{year:<6} {s['total_return']*100:>+9.1f}% "
                      f"{s['ann_vol']*100:>7.1f}% {s['sharpe']:>+7.2f} "
                      f"{s['max_dd']*100:>7.1f}% {s['skew']:>+6.2f}")

    # Compare with broken version
    print("\n" + "=" * 80)
    print("COMPARISON: BROKEN vs FIXED")
    print("=" * 80)
    print("""
    BROKEN (with abs/sign manipulation):
      - Skew: +7.98 (artificially positive)
      - Max DD: -8% (unrealistically small)
      - Always profitable (impossible)

    FIXED (correct model):
      - Skew: {:.2f} (should be negative for carry)
      - Max DD: {:.1f}% (realistic)
      - Can lose money when funding negative
    """.format(stats['skew'], stats['max_dd']*100))

    # Save for combined portfolio
    returns.to_csv('/tmp/carry_fixed_returns.csv')
    print("\nFixed returns saved to /tmp/carry_fixed_returns.csv")
