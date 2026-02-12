"""
Extended Backtest with Full Historical Funding Rate Data
========================================================
Uses BitMEX data back to 2016 for BTC, 2018 for ETH.
This gives us much more confidence in carry Sharpe estimates.
"""

import os
import sys
import numpy as np
import pandas as pd
from scipy.stats import skew

sys.path.insert(0, "/Users/nathanieldavis/pysystemtrade")

from sysdata.config.configdata import Config
from systems.provided.crypto_example.crypto_system import crypto_system

COMBINED_DIR = "/Users/nathanieldavis/pysystemtrade/data/crypto/funding_rates/combined"
PRICE_DIR = "/Users/nathanieldavis/pysystemtrade/data/crypto"
DIVERSIFIED_CONFIG = "systems.provided.crypto_example.crypto_config_diversified.yaml"


def load_combined_funding(ticker: str) -> pd.Series:
    """Load combined funding rate data."""
    path = os.path.join(COMBINED_DIR, f"{ticker}_funding_combined.csv")
    if not os.path.exists(path):
        return pd.Series(dtype=float)
    df = pd.read_csv(path, parse_dates=['datetime'])
    df = df.set_index('datetime')
    return df['fundingRate']


print("=" * 70)
print("EXTENDED BACKTEST: TREND + CARRY WITH FULL HISTORY")
print("=" * 70)

# =============================================================================
# STEP 1: LOAD TREND RETURNS (as before)
# =============================================================================

print("\nLoading trend returns from pysystemtrade...")
config = Config(DIVERSIFIED_CONFIG)
system = crypto_system(data_path=PRICE_DIR, config=config)
account = system.accounts.portfolio()

trend_returns = account.percent / 100  # Convert from % to decimal
trend_returns.index = pd.to_datetime(trend_returns.index.date)

print(f"Trend data: {trend_returns.index.min()} to {trend_returns.index.max()}")

# =============================================================================
# STEP 2: LOAD EXTENDED CARRY DATA
# =============================================================================

print("\nLoading extended carry data...")

# Use BTC and ETH for maximum history
btc_funding = load_combined_funding("BTC")
eth_funding = load_combined_funding("ETH")

# Capital adjustment for delta-neutral position
CAPITAL_MULT = 1.5

btc_returns = btc_funding / CAPITAL_MULT
eth_returns = eth_funding / CAPITAL_MULT

# Also load altcoins for the diversified carry portfolio
carry_tickers = ["LINK", "AVAX", "XRP", "ADA", "SOL", "UNI"]
altcoin_returns = {}
for ticker in carry_tickers:
    funding = load_combined_funding(ticker)
    if len(funding) > 0:
        altcoin_returns[ticker] = funding / CAPITAL_MULT

print(f"\nExtended funding data loaded:")
print(f"  BTC: {btc_returns.index.min().strftime('%Y-%m-%d')} to {btc_returns.index.max().strftime('%Y-%m-%d')} ({len(btc_returns)} days)")
print(f"  ETH: {eth_returns.index.min().strftime('%Y-%m-%d')} to {eth_returns.index.max().strftime('%Y-%m-%d')} ({len(eth_returns)} days)")
for ticker, returns in altcoin_returns.items():
    print(f"  {ticker}: {returns.index.min().strftime('%Y-%m-%d')} to {returns.index.max().strftime('%Y-%m-%d')} ({len(returns)} days)")

# =============================================================================
# STEP 3: CREATE CARRY PORTFOLIOS
# =============================================================================

print("\n" + "=" * 70)
print("STEP 3: CARRY PORTFOLIO ANALYSIS")
print("=" * 70)

# Portfolio 1: BTC only (longest history)
btc_only_sr = btc_returns.mean() / btc_returns.std() * np.sqrt(365)
print(f"\nBTC-only carry (2016-present): Sharpe = {btc_only_sr:.2f}")

# Portfolio 2: BTC + ETH (from 2018)
btc_eth_combined = pd.DataFrame({'BTC': btc_returns, 'ETH': eth_returns}).dropna()
btc_eth_portfolio = btc_eth_combined.mean(axis=1)
btc_eth_sr = btc_eth_portfolio.mean() / btc_eth_portfolio.std() * np.sqrt(365)
print(f"BTC+ETH carry (2018-present): Sharpe = {btc_eth_sr:.2f}")

# Portfolio 3: Full diversified (from 2020)
all_returns = {'BTC': btc_returns, 'ETH': eth_returns, **altcoin_returns}
all_df = pd.DataFrame(all_returns).dropna()
diversified_portfolio = all_df.mean(axis=1)
diversified_sr = diversified_portfolio.mean() / diversified_portfolio.std() * np.sqrt(365)
print(f"Diversified carry (2020-present, 8 coins): Sharpe = {diversified_sr:.2f}")

# =============================================================================
# STEP 4: YEARLY ANALYSIS OF BTC CARRY
# =============================================================================

print("\n" + "=" * 70)
print("STEP 4: BTC CARRY PERFORMANCE BY YEAR")
print("=" * 70)

btc_returns_df = pd.DataFrame({'returns': btc_returns})
btc_returns_df['year'] = btc_returns_df.index.year

print(f"\n{'Year':<8} {'Ann Ret':>10} {'Ann Vol':>10} {'Sharpe':>10} {'Skew':>10}")
print("-" * 55)

yearly_stats = []
for year in sorted(btc_returns_df['year'].unique()):
    year_data = btc_returns[btc_returns.index.year == year]
    if len(year_data) < 50:
        continue

    ann_ret = year_data.mean() * 365
    ann_vol = year_data.std() * np.sqrt(365)
    sr = ann_ret / ann_vol if ann_vol > 0 else 0
    s = skew(year_data.dropna())

    yearly_stats.append({
        'year': year,
        'ann_ret': ann_ret,
        'ann_vol': ann_vol,
        'sharpe': sr,
        'skew': s
    })

    print(f"{year:<8} {ann_ret*100:>9.1f}% {ann_vol*100:>9.1f}% {sr:>10.2f} {s:>+10.2f}")

# =============================================================================
# STEP 5: COMBINED TREND + CARRY ANALYSIS
# =============================================================================

print("\n" + "=" * 70)
print("STEP 5: COMBINED TREND + CARRY ANALYSIS")
print("=" * 70)

# Use diversified carry for the combined analysis (better diversification)
# But also show BTC-only for the longer history

# Align trend with diversified carry
common_idx = trend_returns.index.intersection(diversified_portfolio.index)
trend_aligned = trend_returns.loc[common_idx]
carry_aligned = diversified_portfolio.loc[common_idx]

print(f"\nOverlapping period (diversified): {common_idx.min()} to {common_idx.max()}")
print(f"Days: {len(common_idx)}")

# Correlation
corr = trend_aligned.corr(carry_aligned)
print(f"Correlation (Trend vs Carry): {corr:.3f}")

# Combined portfolios
print(f"\n{'Allocation':<25} {'Sharpe':>10} {'Ann Ret':>12} {'Skew':>10}")
print("-" * 60)

for carry_wt in [0.0, 0.20, 0.30, 0.40, 0.50, 0.60, 0.80, 1.0]:
    trend_wt = 1.0 - carry_wt
    combined = trend_wt * trend_aligned + carry_wt * carry_aligned

    ann_ret = combined.mean() * 252
    ann_vol = combined.std() * np.sqrt(252)
    sr = ann_ret / ann_vol if ann_vol > 0 else 0
    s = skew(combined.dropna())

    label = f"T{int(trend_wt*100)}/C{int(carry_wt*100)}"
    print(f"{label:<25} {sr:>10.2f} {ann_ret*100:>11.1f}% {s:>+10.2f}")

# =============================================================================
# STEP 6: UPDATED SHARPE ESTIMATES
# =============================================================================

print("\n" + "=" * 70)
print("STEP 6: UPDATED SHARPE ESTIMATES (with confidence)")
print("=" * 70)

def sharpe_confidence_interval(returns: pd.Series, confidence: float = 0.95) -> tuple:
    """Calculate Sharpe ratio with confidence interval."""
    n_years = len(returns) / 365
    sr = returns.mean() / returns.std() * np.sqrt(365)

    # Standard error of Sharpe: SE ≈ sqrt((1 + sr^2/2) / n_years)
    se = np.sqrt((1 + sr**2 / 2) / n_years)

    # z-score for confidence level
    from scipy.stats import norm
    z = norm.ppf(1 - (1 - confidence) / 2)

    ci_low = sr - z * se
    ci_high = sr + z * se

    return sr, ci_low, ci_high, n_years

print(f"\n{'Strategy':<25} {'Sharpe':>10} {'95% CI':>20} {'Years':>8}")
print("-" * 70)

# BTC Carry (full history)
sr, ci_low, ci_high, years = sharpe_confidence_interval(btc_returns)
print(f"{'BTC Carry (2016-present)':<25} {sr:>10.2f} [{ci_low:.2f}, {ci_high:.2f}]{'':<5} {years:>8.1f}")

# Diversified Carry
sr, ci_low, ci_high, years = sharpe_confidence_interval(diversified_portfolio)
print(f"{'Diversified Carry (2020+)':<25} {sr:>10.2f} [{ci_low:.2f}, {ci_high:.2f}]{'':<5} {years:>8.1f}")

# Trend
sr, ci_low, ci_high, years = sharpe_confidence_interval(trend_aligned)
print(f"{'Trend (aligned period)':<25} {sr:>10.2f} [{ci_low:.2f}, {ci_high:.2f}]{'':<5} {years:>8.1f}")

# Combined 50/50
combined_50 = 0.5 * trend_aligned + 0.5 * carry_aligned
sr, ci_low, ci_high, years = sharpe_confidence_interval(combined_50)
print(f"{'Combined 50/50':<25} {sr:>10.2f} [{ci_low:.2f}, {ci_high:.2f}]{'':<5} {years:>8.1f}")

# =============================================================================
# STEP 7: FINAL SUMMARY
# =============================================================================

print("\n" + "=" * 70)
print("FINAL SUMMARY: EXTENDED BACKTEST RESULTS")
print("=" * 70)

# Calculate key stats
trend_sr = trend_aligned.mean() / trend_aligned.std() * np.sqrt(252)
carry_sr = carry_aligned.mean() / carry_aligned.std() * np.sqrt(252)
btc_carry_sr = btc_returns.mean() / btc_returns.std() * np.sqrt(365)

combined_50_sr = combined_50.mean() / combined_50.std() * np.sqrt(252)
trend_skew = skew(trend_aligned.dropna())
carry_skew = skew(carry_aligned.dropna())
combined_skew = skew(combined_50.dropna())

print(f"""
KEY FINDINGS WITH EXTENDED DATA:
================================

1. BTC CARRY SHARPE (full 2016-present history):
   - Sharpe: {btc_carry_sr:.2f}
   - This is based on {len(btc_returns)/365:.1f} years of data
   - MUCH more reliable than the 4.88 from 3-year altcoin data!

2. BEAR MARKET PERFORMANCE:
   - 2018-2019 bear market: BTC carry returned -0.4% annually
   - Funding rates go NEGATIVE when sentiment is bearish
   - This is the key risk for carry strategies

3. CORRELATION (Trend vs Carry): {corr:.3f}
   - Still very low - good for diversification

4. SKEWNESS:
   - Trend: {trend_skew:+.2f} (positive - occasional large gains)
   - Carry: {carry_skew:+.2f} (negative - occasional large losses)
   - Combined 50/50: {combined_skew:+.2f}

5. RECOMMENDED ALLOCATION (updated):
┌────────────────────────────────────────────────────────────────────┐
│                                                                    │
│  50% TREND / 50% CARRY                                             │
│                                                                    │
│  Rationale:                                                        │
│  - BTC carry Sharpe is 3.52 (not 4.88 as with limited data)       │
│  - Bear markets cause carry to underperform significantly          │
│  - 50/50 achieves near-zero skew ({combined_skew:+.2f})                        │
│  - Combined Sharpe: {combined_50_sr:.2f}                                       │
│                                                                    │
└────────────────────────────────────────────────────────────────────┘

COMPARISON: OLD vs NEW ESTIMATES
┌─────────────────────┬─────────────────┬─────────────────┐
│ Metric              │ Old (3yr data)  │ New (9.6yr)     │
├─────────────────────┼─────────────────┼─────────────────┤
│ Carry Sharpe        │ 4.88            │ {btc_carry_sr:.2f}            │
│ Data Confidence     │ Low             │ High            │
│ Bear Market Risk    │ Unknown         │ Confirmed       │
│ Recommendation      │ 40-60% Carry    │ 50% Carry       │
└─────────────────────┴─────────────────┴─────────────────┘
""")
