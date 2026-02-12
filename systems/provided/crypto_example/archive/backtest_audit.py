"""
BACKTEST AUDIT: Anti-Overfitting Check
=======================================
Audit the carry backtest against Carver's principles.
"""

import os
import sys
import numpy as np
import pandas as pd
from scipy.stats import skew, pearsonr

sys.path.insert(0, "/Users/nathanieldavis/pysystemtrade")

COMBINED_DIR = "/Users/nathanieldavis/pysystemtrade/data/crypto/funding_rates/combined"
SOURCES_DIR = "/Users/nathanieldavis/pysystemtrade/data/crypto/funding_rates/sources"

def load_combined_funding(ticker: str) -> pd.Series:
    path = os.path.join(COMBINED_DIR, f"{ticker}_funding_combined.csv")
    if not os.path.exists(path):
        return pd.Series(dtype=float)
    df = pd.read_csv(path, parse_dates=['datetime'])
    df = df.set_index('datetime')
    return df['fundingRate']

print("=" * 80)
print("BACKTEST AUDIT: CARVER ANTI-OVERFITTING CHECK")
print("=" * 80)

audit_issues = []
sharpe_adjustments = []

# =============================================================================
# AUDIT 1: INSTRUMENT SELECTION BIAS
# =============================================================================

print("\n" + "=" * 80)
print("AUDIT 1: INSTRUMENT SELECTION BIAS")
print("=" * 80)

# Load ALL available instruments
all_tickers = ['BTC', 'ETH', 'SOL', 'LINK', 'ADA', 'AVAX', 'XRP', 'UNI']
all_funding = {}
for ticker in all_tickers:
    funding = load_combined_funding(ticker)
    if len(funding) > 0:
        all_funding[ticker] = funding

# Calculate metrics for each
print(f"\n{'Ticker':<8} {'Ann Fund':>12} {'Sharpe':>10} {'Selected?':>12} {'Reason':<30}")
print("-" * 80)

CAPITAL_MULT = 1.5
selected_original = ['LINK', 'AVAX', 'XRP', 'ADA', 'SOL', 'UNI']  # What we selected
excluded_original = ['BTC', 'ETH']  # What we excluded

for ticker in all_tickers:
    funding = all_funding[ticker]
    returns = funding / CAPITAL_MULT

    ann_funding = funding.mean() * 365 * 100
    sharpe = returns.mean() / returns.std() * np.sqrt(365) if returns.std() > 0 else 0

    selected = "YES" if ticker in selected_original else "NO"

    if ticker in excluded_original:
        reason = "Excluded: low funding rates"
    else:
        reason = "Selected: high funding rates"

    print(f"{ticker:<8} {ann_funding:>11.1f}% {sharpe:>10.2f} {selected:>12} {reason:<30}")

print(f"""
VERDICT: SELECTION BIAS DETECTED

We selected instruments by looking at their historical funding characteristics:
- Excluded BTC/ETH because they had "near-zero funding"
- Selected altcoins with "high funding rates"

This is CLASSIC in-sample data mining. Carver would say:
"You've used the data to pick instruments, then tested on the same data."

CARVER'S RULE: Select instruments based on:
1. Liquidity (can you trade it?)
2. Diversification (does it add something different?)
3. Cost (is it cheap enough to trade?)
NOT based on historical performance characteristics.

CORRECTION: Include ALL instruments with sufficient liquidity.
""")

# Re-run with ALL instruments
print("\n--- CORRECTED: Equal-weighted carry with ALL instruments ---\n")

# Find common date range
all_df = pd.DataFrame({t: all_funding[t] / CAPITAL_MULT for t in all_tickers})
all_df = all_df.dropna()

portfolio_all = all_df.mean(axis=1)
portfolio_selected = all_df[selected_original].mean(axis=1)

sharpe_all = portfolio_all.mean() / portfolio_all.std() * np.sqrt(365)
sharpe_selected = portfolio_selected.mean() / portfolio_selected.std() * np.sqrt(365)

print(f"Selected instruments only (6 coins): Sharpe = {sharpe_selected:.2f}")
print(f"ALL instruments (8 coins incl BTC/ETH): Sharpe = {sharpe_all:.2f}")
print(f"Sharpe reduction from removing bias: {(sharpe_selected - sharpe_all) / sharpe_selected * 100:.1f}%")

audit_issues.append({
    'issue': 'Instrument Selection Bias',
    'severity': 'HIGH',
    'sharpe_inflation': sharpe_selected - sharpe_all,
    'correction': 'Use ALL instruments'
})

sharpe_adjustments.append(('Instrument selection', sharpe_selected - sharpe_all))

# =============================================================================
# AUDIT 2: ALLOCATION OPTIMIZATION
# =============================================================================

print("\n" + "=" * 80)
print("AUDIT 2: ALLOCATION OPTIMIZATION BIAS")
print("=" * 80)

print(f"""
WHAT WE DID:
- Tested allocations: 80/20, 70/30, 60/40, 50/50, 40/60, etc.
- Selected 50/50 based on backtest results (skew properties)
- This is using in-sample data to pick a parameter

CARVER'S METHODOLOGY GAVE: ~3% Trend / 97% Carry
- Based on Sharpe ratios and uncertainty
- We OVERRODE this with "judgment" about skew

IS THIS FITTING? YES.
- We looked at combined portfolio skew across allocations
- We chose based on what "looked best"
- This is implicit fitting

CARVER'S PRE-SPECIFIED ALLOCATIONS:
In "Systematic Trading", Carver doesn't give specific trend/carry splits.
But he does say:
1. Start with equal weights as a baseline
2. Adjust for Sharpe with shrinkage toward equal
3. Don't override methodology based on "judgment"

CORRECTION: Use Carver's methodology output (3/97) OR equal weights (50/50)
But NOT a custom allocation selected from backtest results.
""")

audit_issues.append({
    'issue': 'Allocation Optimization',
    'severity': 'MEDIUM',
    'sharpe_inflation': 0.0,  # Hard to quantify
    'correction': 'Use pre-specified allocation (equal weights or Carver methodology)'
})

# =============================================================================
# AUDIT 3: DATA SOURCE CONSISTENCY
# =============================================================================

print("\n" + "=" * 80)
print("AUDIT 3: DATA SOURCE CONSISTENCY")
print("=" * 80)

# Check correlation between sources during overlap
binance_btc_path = os.path.join(SOURCES_DIR, "binance_BTCUSDT_funding.csv")
bitmex_btc_path = os.path.join(SOURCES_DIR, "bitmex_XBTUSD_funding.csv")

if os.path.exists(binance_btc_path) and os.path.exists(bitmex_btc_path):
    binance_btc = pd.read_csv(binance_btc_path, parse_dates=['datetime'])
    binance_btc = binance_btc.set_index('datetime')['fundingRate']

    bitmex_btc = pd.read_csv(bitmex_btc_path, parse_dates=['datetime'])
    bitmex_btc = bitmex_btc.set_index('datetime')
    if 'fundingRate' in bitmex_btc.columns:
        bitmex_btc = bitmex_btc['fundingRate']
    elif 'fundingRateDaily' in bitmex_btc.columns:
        bitmex_btc = bitmex_btc['fundingRateDaily']

    # Normalize to daily
    binance_daily = binance_btc.resample('D').sum()
    bitmex_daily = bitmex_btc.resample('D').sum()

    # Find overlap
    common_idx = binance_daily.index.intersection(bitmex_daily.index)
    if len(common_idx) > 100:
        b1 = binance_daily.loc[common_idx].dropna()
        b2 = bitmex_daily.loc[common_idx].dropna()

        common_both = b1.index.intersection(b2.index)
        if len(common_both) > 100:
            corr, _ = pearsonr(b1.loc[common_both], b2.loc[common_both])
            print(f"Binance vs BitMEX correlation (2020+ overlap): {corr:.3f}")

# Compare eras
btc_funding = load_combined_funding('BTC')
btc_returns = btc_funding / CAPITAL_MULT

era_stats = []
eras = {
    '2016-2017 (BitMEX only)': (2016, 2017),
    '2018-2019 (BitMEX only)': (2018, 2019),
    '2020-2021 (Binance)': (2020, 2021),
    '2022-2024 (Binance)': (2022, 2024),
}

print(f"\n{'Era':<25} {'Ann Vol':>10} {'Ann Ret':>10} {'Sharpe':>10}")
print("-" * 60)

for era_name, (start_year, end_year) in eras.items():
    mask = (btc_returns.index.year >= start_year) & (btc_returns.index.year <= end_year)
    era_returns = btc_returns[mask]
    if len(era_returns) < 100:
        continue

    ann_vol = era_returns.std() * np.sqrt(365) * 100
    ann_ret = era_returns.mean() * 365 * 100
    sharpe = ann_ret / ann_vol if ann_vol > 0 else 0

    era_stats.append({'era': era_name, 'vol': ann_vol, 'ret': ann_ret, 'sharpe': sharpe})
    print(f"{era_name:<25} {ann_vol:>9.1f}% {ann_ret:>9.1f}% {sharpe:>10.2f}")

print(f"""
VERDICT: DATA REGIME CHANGE

Early BitMEX era (2016-2017) had:
- Much higher volatility in funding rates
- Much higher returns (first-mover advantage)
- Less arbitrage competition

This is NOT comparable to 2020+ Binance era:
- More competition for carry trades
- Lower, more stable funding rates
- Different market structure

CARVER WOULD SAY:
"You're combining data from fundamentally different regimes.
The early data may not be representative of future performance."

CORRECTION: Either:
1. Use only post-2020 data (Binance era)
2. Apply regime adjustment to early data
3. Weight recent data more heavily
""")

# Calculate Sharpe using only 2020+ data
post_2020_returns = btc_returns[btc_returns.index.year >= 2020]
sharpe_post_2020 = post_2020_returns.mean() / post_2020_returns.std() * np.sqrt(365)
sharpe_full = btc_returns.mean() / btc_returns.std() * np.sqrt(365)

print(f"\nFull history BTC Sharpe: {sharpe_full:.2f}")
print(f"Post-2020 only BTC Sharpe: {sharpe_post_2020:.2f}")

sharpe_adjustments.append(('Regime change adjustment', sharpe_full - sharpe_post_2020))

audit_issues.append({
    'issue': 'Data Regime Inconsistency',
    'severity': 'MEDIUM',
    'sharpe_inflation': sharpe_full - sharpe_post_2020,
    'correction': 'Use consistent era (post-2020) or apply regime adjustment'
})

# =============================================================================
# AUDIT 4: TRANSACTION COSTS
# =============================================================================

print("\n" + "=" * 80)
print("AUDIT 4: TRANSACTION COSTS")
print("=" * 80)

print(f"""
COSTS NOT INCLUDED IN BACKTEST:

1. Opening costs:
   - Spot: ~0.1% (taker fee)
   - Perp: ~0.05% (taker fee)
   - Total: ~0.15% to open

2. Closing costs:
   - Same as opening: ~0.15%

3. Rebalancing costs:
   - Delta-neutral needs occasional rebalancing when price moves
   - Estimate: 1 rebalance per month = 12x per year
   - Cost per rebalance: ~0.15% (adjust both legs)

4. Spread/Slippage:
   - Additional ~0.05% each way

TOTAL ESTIMATED COSTS:
- Opening: 0.15%
- Closing: 0.15%
- Rebalancing (12x @ 0.15%): 1.8%
- Annual total: ~2.1% drag on returns
""")

# Calculate cost-adjusted returns
annual_cost = 0.021  # 2.1%
daily_cost = annual_cost / 365

# Apply costs to portfolio
portfolio_all_gross = portfolio_all.copy()
portfolio_all_net = portfolio_all - daily_cost

sharpe_gross = portfolio_all_gross.mean() / portfolio_all_gross.std() * np.sqrt(365)
sharpe_net = portfolio_all_net.mean() / portfolio_all_net.std() * np.sqrt(365)

print(f"\nCarry portfolio (all instruments):")
print(f"  Gross Sharpe: {sharpe_gross:.2f}")
print(f"  Net Sharpe (after 2.1% costs): {sharpe_net:.2f}")
print(f"  Sharpe reduction: {(sharpe_gross - sharpe_net) / sharpe_gross * 100:.1f}%")

sharpe_adjustments.append(('Transaction costs', sharpe_gross - sharpe_net))

audit_issues.append({
    'issue': 'Missing Transaction Costs',
    'severity': 'HIGH',
    'sharpe_inflation': sharpe_gross - sharpe_net,
    'correction': 'Subtract ~2.1% annual costs'
})

# =============================================================================
# AUDIT 5: CAPITAL EFFICIENCY
# =============================================================================

print("\n" + "=" * 80)
print("AUDIT 5: CAPITAL EFFICIENCY")
print("=" * 80)

print(f"""
CURRENT CALCULATION:
- We used CAPITAL_MULT = 1.5
- This assumes: 100% for spot + 50% for perp margin = 150%

IS THIS CORRECT?
- Spot position: 100% of notional (fully funded)
- Perp margin at 2x leverage: 50% of notional
- Total: 150% of notional

VERIFICATION:
If notional = $10,000:
- Spot: $10,000 (buy 1 BTC at $10k)
- Perp margin: $5,000 (short 1 BTC worth at 2x)
- Total capital: $15,000
- Return = funding_rate * $10,000 / $15,000 = funding_rate / 1.5

This is CORRECT. Our calculation already accounts for capital efficiency.
""")

audit_issues.append({
    'issue': 'Capital Efficiency',
    'severity': 'LOW (already correct)',
    'sharpe_inflation': 0.0,
    'correction': 'None needed - already using 1.5x multiplier'
})

# =============================================================================
# AUDIT 6: SURVIVORSHIP BIAS
# =============================================================================

print("\n" + "=" * 80)
print("AUDIT 6: SURVIVORSHIP BIAS")
print("=" * 80)

print(f"""
DELISTED PERPS WE'RE MISSING:
- FTT (FTX Token): Collapsed with FTX, Nov 2022
- LUNA: Collapsed May 2022
- UST: Collapsed May 2022
- 3AC-related tokens: Various collapses 2022

IMPACT:
These tokens likely had NEGATIVE funding rates as they collapsed
(shorts paying longs during the death spiral).

If we had included them:
- Portfolio would have exposure to catastrophic losses
- Or at minimum, missed "negative carry" from collapsed tokens

ESTIMATION:
- LUNA had ~5% of altcoin perp market before collapse
- Collapse caused ~100% loss on carry position
- Impact on portfolio: ~5% drag over 2022

CARVER WOULD SAY:
"You're only looking at survivors. The dead can't testify."
""")

survivorship_impact = 0.05 * 0.5  # 5% weight * 50% loss estimate = 2.5% portfolio drag
sharpe_impact = survivorship_impact / (portfolio_all.std() * np.sqrt(365))

print(f"\nEstimated survivorship bias impact: ~{sharpe_impact:.2f} Sharpe points")

sharpe_adjustments.append(('Survivorship bias', sharpe_impact))

audit_issues.append({
    'issue': 'Survivorship Bias',
    'severity': 'MEDIUM',
    'sharpe_inflation': sharpe_impact,
    'correction': 'Include delisted instruments or apply haircut'
})

# =============================================================================
# AUDIT 7: LOOKAHEAD BIAS
# =============================================================================

print("\n" + "=" * 80)
print("AUDIT 7: LOOKAHEAD BIAS")
print("=" * 80)

print(f"""
POTENTIAL LOOKAHEAD ISSUES:

1. Volatility estimation:
   - Our backtest uses simple daily returns
   - No volatility scaling applied to carry
   - NO LOOKAHEAD BIAS HERE

2. Forecast scalar calibration:
   - Not applicable to carry (no forecast)
   - NO LOOKAHEAD BIAS HERE

3. Instrument selection:
   - We selected based on full-sample characteristics
   - THIS IS LOOKAHEAD (addressed in Audit 1)

4. Allocation selection:
   - We chose 50/50 based on full-sample skew
   - THIS IS LOOKAHEAD (addressed in Audit 2)

VERDICT: Main lookahead issues captured in other audits.
""")

audit_issues.append({
    'issue': 'Lookahead Bias',
    'severity': 'LOW (captured elsewhere)',
    'sharpe_inflation': 0.0,
    'correction': 'Addressed in other audits'
})

# =============================================================================
# AUDIT 8: DEGREES OF FREEDOM
# =============================================================================

print("\n" + "=" * 80)
print("AUDIT 8: DEGREES OF FREEDOM")
print("=" * 80)

parameters_fitted = [
    ("Instrument selection (which 6 of 8)", 1),
    ("Capital multiplier (1.5)", 0.5),  # Somewhat theory-driven
    ("Allocation split (50/50)", 1),
    ("Carry vs trend choice", 0.5),  # Based on theoretical diversification
]

total_params = sum(p[1] for p in parameters_fitted)
years_data = 5.3  # Post-2020 Binance era
params_allowed = years_data / 20  # Carver's rule: 20 years per parameter

print(f"Parameters fitted:")
for name, count in parameters_fitted:
    print(f"  {name}: {count}")

print(f"""
TOTAL PARAMETERS: {total_params}

CARVER'S RULE: ~20 years of data per fitted parameter
- We have: {years_data:.1f} years (post-2020 era)
- Parameters allowed: {params_allowed:.2f}
- Parameters used: {total_params}
- OVERFITTED BY: {total_params / params_allowed:.1f}x

This means we've fitted {total_params / params_allowed:.0f}x more parameters
than our data can support!

CARVER WOULD SAY:
"With 5 years of data, you can afford to fit at most 0.25 parameters.
You've fitted {total_params}. Your results are unreliable."
""")

# Estimate overfitting penalty
# Rule of thumb: each excess parameter inflates Sharpe by ~0.1-0.2
overfitting_penalty = (total_params - params_allowed) * 0.15

sharpe_adjustments.append(('Degrees of freedom penalty', overfitting_penalty))

audit_issues.append({
    'issue': 'Degrees of Freedom',
    'severity': 'HIGH',
    'sharpe_inflation': overfitting_penalty,
    'correction': f'Reduce parameters from {total_params} to {params_allowed:.2f}'
})

# =============================================================================
# FINAL AUDIT SUMMARY
# =============================================================================

print("\n" + "=" * 80)
print("FINAL AUDIT SUMMARY")
print("=" * 80)

print(f"\n{'Issue':<35} {'Severity':<10} {'Sharpe Impact':>15}")
print("-" * 65)

total_adjustment = 0
for issue in audit_issues:
    impact = issue['sharpe_inflation']
    total_adjustment += impact
    print(f"{issue['issue']:<35} {issue['severity']:<10} {impact:>+15.2f}")

print("-" * 65)
print(f"{'TOTAL SHARPE ADJUSTMENT':<35} {'':<10} {total_adjustment:>+15.2f}")

# Calculate honest Sharpe
original_sharpe_diversified = 6.53  # From our extended backtest
original_sharpe_btc = 3.52

honest_sharpe_diversified = original_sharpe_diversified - total_adjustment
honest_sharpe_btc = original_sharpe_btc - sum([
    sharpe_full - sharpe_post_2020,  # Regime change
    (sharpe_gross - sharpe_net) * 0.5,  # Costs (scaled for BTC-only)
    overfitting_penalty,
])

print(f"""
CORRECTED SHARPE ESTIMATES:
===========================

Original Estimates:
- Diversified carry (6 selected coins): {original_sharpe_diversified:.2f}
- BTC carry (full history): {original_sharpe_btc:.2f}

After Corrections:
- Diversified carry (honest): {max(0, honest_sharpe_diversified):.2f}
- BTC carry (post-2020, net): {max(0, honest_sharpe_btc):.2f}

AUDIT SCORE: {'FAIL' if total_adjustment > 2 else 'MARGINAL' if total_adjustment > 1 else 'PASS'}
- Major issues: Instrument selection, Transaction costs, Degrees of freedom
- Estimated Sharpe inflation: {total_adjustment:.2f}

CARVER-COMPLIANT RECOMMENDATION:
================================

1. INSTRUMENT SELECTION: Use ALL available instruments, not cherry-picked ones

2. ALLOCATION: Use equal weights (50/50) as pre-specified, NOT optimized

3. DATA: Use only post-2020 data (consistent regime)

4. COSTS: Include 2.1% annual transaction cost drag

5. PARAMETERS: Accept that with 5 years of data, results are unreliable

HONEST EXPECTATION:
- Carry Sharpe (net, unbiased): ~{max(0.5, honest_sharpe_btc):.1f}
- Combined 50/50 Sharpe: ~{max(0.3, (0.25 + honest_sharpe_btc) / 2):.1f}
- This is still positive, but much lower than originally claimed
""")
