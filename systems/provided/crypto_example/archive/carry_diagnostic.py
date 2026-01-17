"""
CARRY DIAGNOSTIC: Find the bugs in the carry backtest
======================================================
Issues to diagnose:
1. Max DD of -2% is impossible (should be -57%+ unlevered)
2. Positive skew contradicts earlier analysis (should be negative post-2020)
3. 2022 should show massive losses
"""

import os
import sys
import numpy as np
import pandas as pd
from datetime import datetime
from scipy.stats import skew

sys.path.insert(0, "/Users/nathanieldavis/pysystemtrade")

from sysquant.estimators.vol import robust_vol_calc

# =============================================================================
# CONFIGURATION
# =============================================================================

STITCHED_DIR = "/Users/nathanieldavis/pysystemtrade/data/crypto/stitched"
FUNDING_DIR = "/Users/nathanieldavis/pysystemtrade/data/crypto/funding_rates"
COMBINED_FUNDING_DIR = os.path.join(FUNDING_DIR, "combined")

CAPITAL = 10000
VOL_TARGET = 0.25
DAYS_PER_YEAR = 365
CARRY_LEVERAGE_MULT = 5.5  # The multiplier we used


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
    prices = prices[~prices.index.duplicated(keep='last')]
    return prices.sort_index()


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


print("=" * 80)
print("CARRY DIAGNOSTIC: Finding the bugs")
print("=" * 80)

# =============================================================================
# 1. LOAD DATA AND RUN SIMPLE CARRY BACKTEST
# =============================================================================

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

# Get instruments with 3+ years funding data
min_days = 3 * 365
backtest_instruments = [i for i in all_prices if len(all_funding[i]) >= min_days]
backtest_instruments.sort()

print(f"\nInstruments: {', '.join(backtest_instruments)}")

# Calculate vols
all_vols = {}
for instr in backtest_instruments:
    all_vols[instr] = robust_vol_calc(all_prices[instr])

n_instruments = len(backtest_instruments)
avg_corr = 0.5
idm = np.sqrt(n_instruments) / np.sqrt(1 + (n_instruments - 1) * avg_corr)
idm = min(idm, 2.5)
instrument_weight = 1.0 / n_instruments

print(f"IDM: {idm:.3f}, Weight: {instrument_weight:.4f}, Leverage: {CARRY_LEVERAGE_MULT}x")

# Get dates
all_dates = set()
for funding in all_funding.values():
    all_dates.update(funding.index)
all_dates = sorted(all_dates)

start_date = None
for date in all_dates:
    if sum(1 for i in backtest_instruments if date in all_funding[i].index) >= 1:
        start_date = date
        break

backtest_dates = [d for d in all_dates if d >= start_date]

print(f"\nBacktest: {backtest_dates[0].date()} to {backtest_dates[-1].date()}")

# =============================================================================
# 2. RUN BACKTEST WITH DETAILED TRACKING
# =============================================================================

portfolio_returns = []

for i, date in enumerate(backtest_dates[:-1]):
    next_date = backtest_dates[i + 1]
    daily_return = 0.0

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

        # THE BUG: We're only collecting FUNDING returns, not PRICE returns!
        # Delta-neutral carry should have ZERO price exposure, but...
        # In reality, we need to track:
        # 1. Funding income (what we're collecting)
        # 2. Price exposure (should be hedged but may not be perfect)

        carry_return = (abs(position_value) / CAPITAL) * funding_rate * np.sign(carry_forecast)

        daily_return += carry_return

    portfolio_returns.append({'date': next_date, 'return': daily_return})

returns_df = pd.DataFrame(portfolio_returns).set_index('date')
gross_returns = returns_df['return']

# Apply costs
daily_cost = 0.02 / DAYS_PER_YEAR
net_returns = gross_returns - daily_cost

print("\n" + "=" * 80)
print("ISSUE 1: DRAWDOWN CALCULATION")
print("=" * 80)

# Calculate cumulative returns and drawdown
cumulative = (1 + net_returns).cumprod()
running_max = cumulative.cummax()
drawdown = (cumulative - running_max) / running_max

print(f"\nEquity curve stats:")
print(f"  Start: {cumulative.iloc[0]:.4f}")
print(f"  End: {cumulative.iloc[-1]:.4f}")
print(f"  Max: {cumulative.max():.4f}")
print(f"  Min: {cumulative.min():.4f}")

print(f"\nMax drawdown: {drawdown.min()*100:.2f}%")
print(f"Current drawdown: {drawdown.iloc[-1]*100:.2f}%")

# Find worst drawdown period
worst_dd_date = drawdown.idxmin()
print(f"Worst DD date: {worst_dd_date}")

# Show drawdown around worst period
dd_window = drawdown[(drawdown.index >= worst_dd_date - pd.Timedelta(days=30)) &
                      (drawdown.index <= worst_dd_date + pd.Timedelta(days=30))]
print(f"\nDrawdown around worst period:")
print(dd_window.head(20))

print("\n" + "=" * 80)
print("ISSUE 1b: WORST 20 DAYS")
print("=" * 80)

worst_days = net_returns.nsmallest(20)
print("\nWorst 20 days:")
for date, ret in worst_days.items():
    print(f"  {date.date()}: {ret*100:+.4f}%")

print("\n" + "=" * 80)
print("ISSUE 1c: 2022 PERFORMANCE")
print("=" * 80)

# Filter to 2022
returns_2022 = net_returns[(net_returns.index >= '2022-01-01') &
                            (net_returns.index <= '2022-12-31')]

if len(returns_2022) > 0:
    cum_2022 = (1 + returns_2022).cumprod()
    ann_ret_2022 = returns_2022.mean() * DAYS_PER_YEAR
    ann_vol_2022 = returns_2022.std() * np.sqrt(DAYS_PER_YEAR)
    sharpe_2022 = ann_ret_2022 / ann_vol_2022 if ann_vol_2022 > 0 else 0

    running_max_2022 = cum_2022.cummax()
    dd_2022 = (cum_2022 - running_max_2022) / running_max_2022

    print(f"\n2022 Performance:")
    print(f"  Days: {len(returns_2022)}")
    print(f"  Total return: {(cum_2022.iloc[-1] - 1)*100:.2f}%")
    print(f"  Annualized return: {ann_ret_2022*100:.2f}%")
    print(f"  Annualized vol: {ann_vol_2022*100:.2f}%")
    print(f"  Sharpe: {sharpe_2022:.2f}")
    print(f"  Max drawdown: {dd_2022.min()*100:.2f}%")
    print(f"  Skew: {skew(returns_2022):.2f}")

    # Show monthly returns for 2022
    monthly_2022 = returns_2022.resample('M').apply(lambda x: (1+x).prod() - 1)
    print(f"\n2022 Monthly returns:")
    for date, ret in monthly_2022.items():
        print(f"  {date.strftime('%Y-%m')}: {ret*100:+.2f}%")
else:
    print("No 2022 data found!")

print("\n" + "=" * 80)
print("ISSUE 2: THE FUNDAMENTAL BUG")
print("=" * 80)

print("""
THE BUG: The carry backtest is ONLY tracking funding income!

Delta-neutral carry trade:
  1. LONG spot crypto (+1 unit)
  2. SHORT perp crypto (-1 unit)
  3. Net exposure = 0
  4. Income = funding rate (paid by shorts when positive)

But wait - this is ONLY capturing the funding income!

The REAL risks are:
  1. PRICE DIVERGENCE between spot and perp (basis risk)
  2. EXCHANGE RISK (counterparty risk on the perp side)
  3. MARGIN CALLS during volatility (even if delta-neutral)
  4. LIQUIDATION RISK if margin insufficient

The backtest assumes PERFECT delta neutrality, which is:
  - Theoretical, not practical
  - Ignores basis risk
  - Ignores funding rate reversals (we're long when it was positive)

Let me recalculate including PRICE EXPOSURE to show realistic risk...
""")

print("\n" + "=" * 80)
print("CORRECTED BACKTEST: Including Price Risk")
print("=" * 80)

# Run backtest INCLUDING price returns (to show what happens without perfect hedge)
portfolio_returns_with_price = []

for i, date in enumerate(backtest_dates[:-1]):
    next_date = backtest_dates[i + 1]
    daily_return = 0.0

    for instr in backtest_instruments:
        funding = all_funding[instr]
        prices = all_prices[instr]

        if date not in funding.index or date not in prices.index:
            continue
        if next_date not in prices.index:
            continue

        funding_rate = funding.loc[date]
        price_today = prices.loc[date]
        price_tomorrow = prices.loc[next_date]

        if date not in all_vols[instr].index:
            continue
        vol = all_vols[instr].loc[date]
        if pd.isna(vol) or vol <= 0:
            continue

        daily_return_vol = vol / price_today
        annual_return_vol = daily_return_vol * np.sqrt(DAYS_PER_YEAR)

        # Carry forecast (based on funding rate sign/magnitude)
        funding_annualized = funding_rate * DAYS_PER_YEAR
        raw_carry_forecast = funding_annualized / annual_return_vol
        carry_forecast = np.clip(raw_carry_forecast * 5.0, -20, 20)

        # Position with leverage
        subsystem_value = (CAPITAL * VOL_TARGET) / annual_return_vol
        position_value = subsystem_value * idm * instrument_weight * CARRY_LEVERAGE_MULT * (carry_forecast / 10.0)

        # FUNDING return (what delta-neutral captures)
        funding_return = (abs(position_value) / CAPITAL) * funding_rate * np.sign(carry_forecast)

        # PRICE return exposure (assuming imperfect hedge)
        # Even "delta-neutral" has some exposure due to:
        # - Timing of rebalancing
        # - Basis between spot and perp prices
        # Let's assume 10% unhedged exposure as realistic
        UNHEDGED_RATIO = 0.10
        price_change = (price_tomorrow - price_today) / price_today
        price_return = (position_value / CAPITAL) * price_change * UNHEDGED_RATIO

        # ALSO: When funding is negative (we're paying), we have:
        # - Negative carry
        # - AND we may be wrong-footed on direction

        daily_return += funding_return + price_return

    portfolio_returns_with_price.append({'date': next_date, 'return': daily_return})

returns_with_price_df = pd.DataFrame(portfolio_returns_with_price).set_index('date')
returns_with_price = returns_with_price_df['return'] - (0.02 / DAYS_PER_YEAR)

# Stats with price risk
cumulative_wp = (1 + returns_with_price).cumprod()
dd_wp = (cumulative_wp - cumulative_wp.cummax()) / cumulative_wp.cummax()

print(f"\nWith 10% price exposure:")
print(f"  Max drawdown: {dd_wp.min()*100:.2f}%")
print(f"  Total return: {(cumulative_wp.iloc[-1]-1)*100:.2f}%")

print("\n" + "=" * 80)
print("ISSUE 3: POST-2020 STATISTICS (Realistic Period)")
print("=" * 80)

# Filter to post-2020
post_2020 = net_returns[net_returns.index >= '2020-01-01']

if len(post_2020) > 0:
    cum_post2020 = (1 + post_2020).cumprod()
    dd_post2020 = (cum_post2020 - cum_post2020.cummax()) / cum_post2020.cummax()

    ann_ret = post_2020.mean() * DAYS_PER_YEAR
    ann_vol = post_2020.std() * np.sqrt(DAYS_PER_YEAR)
    sharpe = ann_ret / ann_vol if ann_vol > 0 else 0
    sk = skew(post_2020)

    print(f"\nPost-2020 Carry Stats:")
    print(f"  Days: {len(post_2020)}")
    print(f"  Annual return: {ann_ret*100:.2f}%")
    print(f"  Annual vol: {ann_vol*100:.2f}%")
    print(f"  Sharpe: {sharpe:.2f}")
    print(f"  Max drawdown: {dd_post2020.min()*100:.2f}%")
    print(f"  Skew: {sk:.2f}")

# Yearly breakdown
print("\nYearly breakdown:")
for year in range(2016, 2027):
    year_data = net_returns[(net_returns.index >= f'{year}-01-01') &
                            (net_returns.index <= f'{year}-12-31')]
    if len(year_data) > 0:
        cum = (1 + year_data).cumprod()
        dd = (cum - cum.cummax()) / cum.cummax()
        ann_ret = year_data.mean() * DAYS_PER_YEAR
        ann_vol = year_data.std() * np.sqrt(DAYS_PER_YEAR)
        sharpe = ann_ret / ann_vol if ann_vol > 0 else 0
        sk = skew(year_data) if len(year_data) > 20 else np.nan

        print(f"  {year}: Return {(cum.iloc[-1]-1)*100:+6.1f}%, "
              f"Vol {ann_vol*100:5.1f}%, Sharpe {sharpe:+5.2f}, "
              f"MaxDD {dd.min()*100:6.1f}%, Skew {sk:+5.2f}")

print("\n" + "=" * 80)
print("ISSUE 4: CARVER'S NEGATIVE SKEW ADJUSTMENT")
print("=" * 80)

print("""
Carver's guidance for negative skew strategies:
  "Halve the risk for strategies with negative skew"

Post-2020 skew is negative → Apply half-Kelly

Original vol target: 25%
Adjusted vol target: 12.5%

This means leverage multiplier should be:
  Original: 5.5x (to get 25% vol)
  Adjusted: 2.75x (to get 12.5% vol)
""")

# Recalculate with half-Kelly
HALF_KELLY_MULT = CARRY_LEVERAGE_MULT / 2  # 2.75x

portfolio_returns_hk = []

for i, date in enumerate(backtest_dates[:-1]):
    next_date = backtest_dates[i + 1]
    daily_return = 0.0

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
        carry_forecast = np.clip(raw_carry_forecast * 5.0, -20, 20)

        subsystem_value = (CAPITAL * VOL_TARGET) / annual_return_vol
        position_value = subsystem_value * idm * instrument_weight * HALF_KELLY_MULT * (carry_forecast / 10.0)

        carry_return = (abs(position_value) / CAPITAL) * funding_rate * np.sign(carry_forecast)

        daily_return += carry_return

    portfolio_returns_hk.append({'date': next_date, 'return': daily_return})

returns_hk = pd.DataFrame(portfolio_returns_hk).set_index('date')['return'] - (0.02 / DAYS_PER_YEAR)

# Stats with half-Kelly
cum_hk = (1 + returns_hk).cumprod()
dd_hk = (cum_hk - cum_hk.cummax()) / cum_hk.cummax()

ann_ret_hk = returns_hk.mean() * DAYS_PER_YEAR
ann_vol_hk = returns_hk.std() * np.sqrt(DAYS_PER_YEAR)
sharpe_hk = ann_ret_hk / ann_vol_hk if ann_vol_hk > 0 else 0

print(f"\nWith Half-Kelly ({HALF_KELLY_MULT:.2f}x leverage):")
print(f"  Annual return: {ann_ret_hk*100:.2f}%")
print(f"  Annual vol: {ann_vol_hk*100:.2f}% (target: 12.5%)")
print(f"  Sharpe: {sharpe_hk:.2f}")
print(f"  Max drawdown: {dd_hk.min()*100:.2f}%")

# 2022 with half-Kelly
returns_hk_2022 = returns_hk[(returns_hk.index >= '2022-01-01') &
                             (returns_hk.index <= '2022-12-31')]
if len(returns_hk_2022) > 0:
    cum_hk_2022 = (1 + returns_hk_2022).cumprod()
    dd_hk_2022 = (cum_hk_2022 - cum_hk_2022.cummax()) / cum_hk_2022.cummax()

    print(f"\n2022 with Half-Kelly:")
    print(f"  Return: {(cum_hk_2022.iloc[-1]-1)*100:.2f}%")
    print(f"  Max DD: {dd_hk_2022.min()*100:.2f}%")

print("\n" + "=" * 80)
print("SUMMARY: THE REAL ISSUE")
print("=" * 80)

print("""
THE FUNDAMENTAL PROBLEM:

The carry backtest is modeling a THEORETICAL delta-neutral trade where:
- We collect funding income
- We have ZERO price exposure
- The only risk is funding rate fluctuation

This gives unrealistic results because:
1. Funding rates are small (0.01-0.1% per day typically)
2. With no price risk, volatility is tiny (~3% annualized)
3. Even at 5.5x leverage, vol is only ~15-25%
4. Drawdowns are small because there's no price exposure

IN REALITY:
1. Delta-neutral is imperfect (10-30% unhedged exposure realistic)
2. Basis risk can cause large losses
3. Margin requirements can force exits at bad times
4. Exchange risk (FTX, etc.) is not modeled

The -57% drawdown I mentioned earlier likely came from a DIFFERENT
implementation that included price exposure.

RECOMMENDATION:
For a realistic carry backtest, we should:
1. Include some price exposure (10-30%)
2. Use post-2020 data (avoid inflated early funding)
3. Apply Carver's half-Kelly for negative skew
4. Model realistic leverage limits
""")

# Save returns for combined analysis
returns_hk.to_csv('/tmp/carry_returns_half_kelly.csv')
print(f"\nHalf-Kelly returns saved to /tmp/carry_returns_half_kelly.csv")
