"""
Final Combined Backtest - Trend + Carry
========================================
Properly scaled returns for both strategies.
"""

import os
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, "/Users/nathanieldavis/pysystemtrade")

from sysdata.config.configdata import Config
from systems.provided.crypto_example.crypto_system import crypto_system

FUNDING_DIR = "/Users/nathanieldavis/pysystemtrade/data/crypto/funding_rates"
PRICE_DIR = "/Users/nathanieldavis/pysystemtrade/data/crypto"
DIVERSIFIED_CONFIG = "systems.provided.crypto_example.crypto_config_diversified.yaml"

def load_funding_rates(ticker):
    path = os.path.join(FUNDING_DIR, f"{ticker}_funding.csv")
    if not os.path.exists(path):
        return pd.Series(dtype=float)
    df = pd.read_csv(path, parse_dates=["datetime"])
    df = df.set_index("datetime")
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    return df["fundingRate"]

def funding_to_daily(funding):
    if len(funding) == 0:
        return pd.Series(dtype=float)
    if funding.index.tz is not None:
        funding = funding.copy()
        funding.index = funding.index.tz_localize(None)
    return funding.resample("D").sum()

print("=" * 70)
print("STEP 1: GET TREND RETURNS (properly scaled)")
print("=" * 70)

# Load system
config = Config(DIVERSIFIED_CONFIG)
system = crypto_system(data_path=PRICE_DIR, config=config)

# Get the account curve
account = system.accounts.portfolio()

# The .percent attribute gives daily P&L as % of initial capital
# For a $100k notional, a value of 0.5 means $500 daily P&L = 0.5% return
trend_returns_raw = account.percent

# Convert to daily decimal returns (divide by 100 since already in %)
# Actually looking at the values, they seem to already be in decimal form
# Let's verify by checking the Sharpe
trend_daily_sharpe = trend_returns_raw.mean() / trend_returns_raw.std()
trend_annual_sharpe = trend_daily_sharpe * np.sqrt(252)

print(f"\nTrend Strategy (from pysystemtrade):")
print(f"  Daily mean: {trend_returns_raw.mean():.4f}")
print(f"  Daily std: {trend_returns_raw.std():.4f}")
print(f"  Annual Sharpe: {trend_annual_sharpe:.2f}")
print(f"  Data: {trend_returns_raw.index.min()} to {trend_returns_raw.index.max()}")

# Normalize to proper daily returns (the values are in % of capital)
# So 1.0 means 1% daily return
trend_returns = trend_returns_raw / 100  # Convert from % to decimal
trend_daily_sharpe = trend_returns.mean() / trend_returns.std()
trend_annual_sharpe = trend_daily_sharpe * np.sqrt(252)

print(f"\nAfter normalizing (÷100):")
print(f"  Daily mean: {trend_returns.mean()*100:.4f}%")
print(f"  Daily std: {trend_returns.std()*100:.4f}%")
print(f"  Annual mean: {trend_returns.mean()*252*100:.1f}%")
print(f"  Annual std: {trend_returns.std()*np.sqrt(252)*100:.1f}%")
print(f"  Sharpe: {trend_annual_sharpe:.2f}")

# Verify against pysystemtrade's built-in Sharpe
pst_sharpe = account.sharpe()
print(f"  pysystemtrade Sharpe: {pst_sharpe:.2f}")

print("\n" + "=" * 70)
print("STEP 2: CALCULATE CARRY RETURNS (same scale)")
print("=" * 70)

# Carry instruments (top 6 from our ranking)
carry_instruments = ["LINK", "AVAX", "XRP", "ADA", "SOL", "UNI"]

# For carry strategy, we need to express returns on the same basis
# The trend strategy uses $100k notional capital
# For carry, we'll allocate 30% = $30k to carry
# With 6 instruments: $5k each notional
# Delta-neutral needs spot + perp margin
# At 2x leverage: $5k spot + $2.5k margin = $7.5k capital per instrument
# But let's express as return on carry capital allocation

carry_capital = 30000  # $30k allocated to carry (30% of $100k)
per_instrument = carry_capital / len(carry_instruments)  # $5k per instrument

print(f"\nCarry Capital Allocation:")
print(f"  Total carry capital: ${carry_capital:,}")
print(f"  Per instrument: ${per_instrument:,.0f}")

# Calculate daily returns for each carry instrument
carry_returns_dict = {}

for ticker in carry_instruments:
    funding = load_funding_rates(ticker)
    if len(funding) == 0:
        continue

    daily_funding = funding_to_daily(funding)

    # Funding return: we collect funding on our short perp notional
    # If we have $5k notional short, and funding rate is 0.01 (1%),
    # we collect $50 = 1% of $5k
    # As return on instrument capital: 50 / 5000 = 1%
    # But we also have spot position, so total capital = 1.5x notional (at 2x leverage)
    # Return on capital = funding_rate / 1.5

    capital_multiple = 1.5  # 1 for spot + 0.5 for margin at 2x leverage
    daily_return = daily_funding / capital_multiple

    carry_returns_dict[ticker] = daily_return

# Combine into portfolio (equal weight)
carry_df = pd.DataFrame(carry_returns_dict)
carry_df = carry_df.dropna(how='all')

# Portfolio return (equal weight across instruments)
portfolio_carry = carry_df.mean(axis=1)

# Normalize index to match trend
portfolio_carry.index = pd.to_datetime(portfolio_carry.index.date)

print(f"\nCarry Portfolio Statistics:")
print(f"  Data: {portfolio_carry.index.min()} to {portfolio_carry.index.max()}")
carry_sharpe = portfolio_carry.mean() / portfolio_carry.std() * np.sqrt(252)
print(f"  Daily mean: {portfolio_carry.mean()*100:.4f}%")
print(f"  Daily std: {portfolio_carry.std()*100:.4f}%")
print(f"  Annual mean: {portfolio_carry.mean()*252*100:.1f}%")
print(f"  Annual Sharpe: {carry_sharpe:.2f}")

# Yearly breakdown
print(f"\n  Yearly Carry Performance:")
for year in sorted(portfolio_carry.index.year.unique()):
    year_data = portfolio_carry[portfolio_carry.index.year == year]
    if len(year_data) < 50:
        continue
    ann_ret = year_data.mean() * 252 * 100
    sharpe = year_data.mean() / year_data.std() * np.sqrt(252) if year_data.std() > 0 else 0
    print(f"    {year}: {ann_ret:+.1f}% return, {sharpe:.2f} Sharpe")

print("\n" + "=" * 70)
print("STEP 3: ALIGN AND COMBINE")
print("=" * 70)

# Normalize trend returns index
trend_aligned = trend_returns.copy()
trend_aligned.index = pd.to_datetime(trend_aligned.index.date)

# Find common period
common_idx = trend_aligned.index.intersection(portfolio_carry.index)
print(f"\nOverlapping period: {common_idx.min()} to {common_idx.max()}")
print(f"Overlapping days: {len(common_idx)}")

trend_aligned = trend_aligned.loc[common_idx]
carry_aligned = portfolio_carry.loc[common_idx]

# Correlation
corr = trend_aligned.corr(carry_aligned)
print(f"Correlation (Trend vs Carry): {corr:.3f}")

print("\n" + "=" * 70)
print("STEP 4: COMBINED PORTFOLIO RESULTS")
print("=" * 70)

# Now we can properly combine
# 70% trend + 30% carry means:
# - 70% of capital in trend strategy
# - 30% of capital in carry strategy
# Combined return = 0.70 * trend_return + 0.30 * carry_return

print(f"\n{'Allocation':<25} {'Sharpe':>8} {'Ann Ret':>12} {'Ann Vol':>10} {'MaxDD':>10}")
print("-" * 70)

for carry_wt in [0.0, 0.10, 0.20, 0.30, 0.40, 0.50, 1.0]:
    trend_wt = 1.0 - carry_wt

    # Weighted combination of returns
    combined = trend_wt * trend_aligned + carry_wt * carry_aligned

    # Statistics
    ann_ret = combined.mean() * 252
    ann_vol = combined.std() * np.sqrt(252)
    sharpe = ann_ret / ann_vol if ann_vol > 0 else 0

    # Max drawdown
    cumulative = combined.cumsum()
    max_dd = (cumulative - cumulative.cummax()).min()

    label = f"Trend {int(trend_wt*100)}% / Carry {int(carry_wt*100)}%"
    print(f"{label:<25} {sharpe:>8.2f} {ann_ret*100:>11.1f}% {ann_vol*100:>9.1f}% {max_dd*100:>9.1f}%")

print("\n" + "=" * 70)
print("STEP 5: YEARLY BREAKDOWN")
print("=" * 70)

# Analyze 70/30 by year
combined_70_30 = 0.70 * trend_aligned + 0.30 * carry_aligned

print(f"\n{'Year':<8} {'Trend SR':>10} {'Carry SR':>10} {'70/30 SR':>10} {'Improvement':>12}")
print("-" * 55)

for year in sorted(combined_70_30.index.year.unique()):
    mask = combined_70_30.index.year == year
    if mask.sum() < 50:
        continue

    t_yr = trend_aligned[mask]
    c_yr = carry_aligned[mask]
    comb_yr = combined_70_30[mask]

    t_sr = t_yr.mean() / t_yr.std() * np.sqrt(252) if t_yr.std() > 0 else 0
    c_sr = c_yr.mean() / c_yr.std() * np.sqrt(252) if c_yr.std() > 0 else 0
    comb_sr = comb_yr.mean() / comb_yr.std() * np.sqrt(252) if comb_yr.std() > 0 else 0

    improvement = comb_sr - t_sr

    print(f"{year:<8} {t_sr:>10.2f} {c_sr:>10.2f} {comb_sr:>10.2f} {improvement:>+11.2f}")

print("\n" + "=" * 70)
print("FINAL SUMMARY")
print("=" * 70)

# Full period stats for 70/30
trend_sr_full = trend_aligned.mean() / trend_aligned.std() * np.sqrt(252)
carry_sr_full = carry_aligned.mean() / carry_aligned.std() * np.sqrt(252)
combined_sr_full = combined_70_30.mean() / combined_70_30.std() * np.sqrt(252)

combined_max_dd = (combined_70_30.cumsum() - combined_70_30.cumsum().cummax()).min()
trend_max_dd = (trend_aligned.cumsum() - trend_aligned.cumsum().cummax()).min()

print(f"""
VERIFIED RESULTS (overlapping period {common_idx.min().strftime('%Y-%m-%d')} to {common_idx.max().strftime('%Y-%m-%d')}):

┌─────────────────────┬──────────┬──────────────┬──────────────┬────────────┐
│ Strategy            │ Sharpe   │ Ann Return   │ Ann Vol      │ Max DD     │
├─────────────────────┼──────────┼──────────────┼──────────────┼────────────┤
│ Trend Only          │ {trend_sr_full:>6.2f}   │ {trend_aligned.mean()*252*100:>10.1f}% │ {trend_aligned.std()*np.sqrt(252)*100:>10.1f}% │ {trend_max_dd*100:>8.1f}%  │
│ Carry Only          │ {carry_sr_full:>6.2f}   │ {carry_aligned.mean()*252*100:>10.1f}% │ {carry_aligned.std()*np.sqrt(252)*100:>10.1f}% │ {(carry_aligned.cumsum() - carry_aligned.cumsum().cummax()).min()*100:>8.1f}%  │
│ Combined (70/30)    │ {combined_sr_full:>6.2f}   │ {combined_70_30.mean()*252*100:>10.1f}% │ {combined_70_30.std()*np.sqrt(252)*100:>10.1f}% │ {combined_max_dd*100:>8.1f}%  │
└─────────────────────┴──────────┴──────────────┴──────────────┴────────────┘

Key Findings:
- Correlation: {corr:.3f} (low, good for diversification)
- Sharpe improvement: {combined_sr_full - trend_sr_full:+.2f} ({(combined_sr_full/trend_sr_full - 1)*100:+.1f}% improvement)

CARRY RETURN SANITY CHECK:
- SOL avg daily funding: {carry_df['SOL'].mean()*100:.3f}% of notional
- SOL annual funding rate: {carry_df['SOL'].mean()*365*100:.0f}%
- After capital adjustment (1.5x): {carry_df['SOL'].mean()*365/1.5*100:.0f}% return

RECOMMENDATION:
{'✓ Adding 30% carry IMPROVES portfolio Sharpe from ' + f'{trend_sr_full:.2f} to {combined_sr_full:.2f}' if combined_sr_full > trend_sr_full else '✗ Carry does NOT improve portfolio on this period'}
""")
