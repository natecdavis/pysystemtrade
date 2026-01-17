"""
Final Sharpe Estimates with Clean Methodology
==============================================
Rigorous calculation of expected Sharpe ratios for trend/carry portfolio.
"""

import os
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, "/Users/nathanieldavis/pysystemtrade")

COMBINED_DIR = "/Users/nathanieldavis/pysystemtrade/data/crypto/funding_rates/combined"
PRICE_DIR = "/Users/nathanieldavis/pysystemtrade/data/crypto"

# =============================================================================
# SECTION 1: TRANSACTION COST VALIDATION
# =============================================================================

print("=" * 70)
print("SECTION 1: TRANSACTION COST VALIDATION")
print("=" * 70)

# Load BTC price data
btc_prices = pd.read_csv(
    os.path.join(PRICE_DIR, "BTC.csv"),
    parse_dates=['date']
)
btc_prices = btc_prices.set_index('date')['close']

# Calculate daily returns
btc_returns = btc_prices.pct_change().dropna()

print(f"\nBTC Price Data: {btc_returns.index.min().strftime('%Y-%m-%d')} to {btc_returns.index.max().strftime('%Y-%m-%d')}")
print(f"Days: {len(btc_returns)}")

# Delta-neutral rebalancing analysis
# Assumption: We rebalance when delta drifts beyond threshold

def count_rebalances(returns: pd.Series, threshold: float = 0.10) -> dict:
    """
    Count how often we'd need to rebalance given a drift threshold.

    For delta-neutral: Long 1 BTC spot, Short 1 BTC perp
    Delta drift occurs when price moves significantly.
    Actually, for PERFECTLY delta-neutral, drift is minimal.

    But in practice:
    1. Funding payments change position value
    2. We might size based on USD notional
    3. Leverage requirements change

    More realistic: count 10%+ moves from any local high/low
    """
    cumulative = (1 + returns).cumprod()

    rebalance_count = 0
    last_rebalance_price = cumulative.iloc[0]
    rebalances = []

    for date, price in cumulative.items():
        pct_move = abs(price / last_rebalance_price - 1)
        if pct_move >= threshold:
            rebalance_count += 1
            rebalances.append({
                'date': date,
                'move': pct_move,
                'direction': 'up' if price > last_rebalance_price else 'down'
            })
            last_rebalance_price = price

    years = len(returns) / 365

    return {
        'total_rebalances': rebalance_count,
        'years': years,
        'rebalances_per_year': rebalance_count / years,
        'rebalances': rebalances
    }

print("\nRebalancing frequency by threshold:")
print(f"{'Threshold':<15} {'Rebalances':<15} {'Per Year':<15} {'Annual Cost':<15}")
print("-" * 60)

# Round-trip cost: ~0.15% (0.05% maker fee × 2 legs × 1.5 for slippage)
ROUND_TRIP_COST = 0.0015

for threshold in [0.05, 0.10, 0.15, 0.20]:
    stats = count_rebalances(btc_returns, threshold)
    annual_cost = stats['rebalances_per_year'] * ROUND_TRIP_COST
    print(f"{threshold*100:.0f}%{'':<12} {stats['total_rebalances']:<15} {stats['rebalances_per_year']:<15.1f} {annual_cost*100:.2f}%")

# For delta-neutral carry, the main rebalancing needs are:
# 1. Position sizing adjustments (less frequent than threshold suggests)
# 2. Rolling (perpetuals don't roll, so 0)
# 3. Margin adjustments (can often be done with cash, not trading)

print("""
ANALYSIS:
---------
For delta-neutral perpetual carry, rebalancing is actually LESS frequent
than price moves suggest because:

1. True delta-neutral: Spot + perp are perfectly offsetting
   - No rebalance needed for price moves
   - Only rebalance for:
     a) Funding accumulation changing position value (~quarterly)
     b) Switching instruments (rare)
     c) Portfolio rebalancing across coins (monthly)

2. Realistic estimate:
   - Quarterly funding rebalance: 4/year
   - Monthly portfolio rebalance: 12/year
   - Total: ~16 rebalances/year × 0.15% = 2.4% annual cost

   BUT most rebalancing is small (not full position), so:
   - Effective turnover: ~50% of position per rebalance
   - Adjusted cost: 16 × 0.15% × 0.5 = 1.2% annual

3. Conservative estimate for carry: 1.5% annual cost drag
""")

CARRY_ANNUAL_COST = 0.015  # 1.5% conservative

# For trend following (spot only)
print("\nTrend Following Transaction Costs:")
print("-" * 40)

# Trend following with typical turnover
# EWMAC has turnover of roughly 5-10x per year for the forecast
# But position changes are gradual, not binary

TREND_TURNOVER = 6.0  # Annual turnover (conservative)
TREND_COST_PER_TURN = 0.001  # 0.1% per trade (spot only, lower than perp)
TREND_ANNUAL_COST = TREND_TURNOVER * TREND_COST_PER_TURN

print(f"Estimated turnover: {TREND_TURNOVER:.1f}x per year")
print(f"Cost per turn: {TREND_COST_PER_TURN*100:.2f}%")
print(f"Annual cost: {TREND_ANNUAL_COST*100:.1f}%")

# =============================================================================
# SECTION 2: SURVIVORSHIP BIAS ANALYSIS
# =============================================================================

print("\n" + "=" * 70)
print("SECTION 2: SURVIVORSHIP BIAS FOR DELTA-NEUTRAL")
print("=" * 70)

print("""
LUNA COLLAPSE CASE STUDY:
-------------------------
Timeline:
- May 7-12, 2022: UST depeg triggers LUNA death spiral
- LUNA went from ~$80 to ~$0.0001 in 5 days
- Perp funding rates during collapse:

Key question: What happens to delta-neutral position during collapse?
""")

# Simulate what would have happened
print("""
DELTA-NEUTRAL MECHANICS DURING COLLAPSE:
----------------------------------------

1. SPOT LEG: Long LUNA spot
   - Lost ~100% ($80 → $0)
   - P&L: -$80 per LUNA

2. PERP LEG: Short LUNA perp
   - Gained ~100% (short from $80 to $0)
   - P&L: +$80 per LUNA (approximately)

3. FUNDING DURING COLLAPSE:
   - Funding went EXTREMELY negative (longs paying shorts)
   - As a short, we RECEIVED massive funding
   - Estimated: 10-50% funding over the collapse week

4. NET P&L:
   - Spot loss: -100%
   - Perp gain: +~100% (but basis can diverge)
   - Funding received: +10-50%

   THEORETICAL NET: Slightly positive!

   BUT REAL RISKS:
   a) Exchange halts perp trading → can't close short
   b) Perp price diverges from spot (basis blowout)
   c) Counter-party risk (exchange insolvency)
   d) Liquidation cascade (despite being "hedged")

5. REALISTIC LOSS SCENARIOS:
   - Best case: +5% to +20% (funding profit, clean exit)
   - Base case: -5% to -20% (basis divergence, fees)
   - Worst case: -50% to -100% (exchange halt, can't exit perp)
""")

# Historical collapse probability
print("""
SURVIVORSHIP BIAS QUANTIFICATION:
---------------------------------

Historical collapse frequency (top 20 coins by market cap):
- LUNA (2022): Complete collapse
- FTT (2022): Complete collapse
- UST (2022): Complete collapse (stablecoin)
- Estimated: 3 major collapses in 5 years among top 20

Probability per coin per year: ~3%

For 8-coin diversified carry portfolio:
- P(at least one collapse in 1 year) = 1 - (0.97)^8 = 22%
- P(at least one collapse in 5 years) = 1 - (0.97)^40 = 71%

Expected annual loss from collapses:
- P(collapse) × E[loss | collapse] × (1/N coins)
""")

# Calculate expected survivorship drag
n_coins = 8
p_collapse_per_coin = 0.03  # 3% per year
p_at_least_one = 1 - (1 - p_collapse_per_coin) ** n_coins

# Loss scenarios with probabilities
loss_scenarios = [
    (0.3, 0.05),   # 30% chance: +5% (funding profit)
    (0.3, -0.15),  # 30% chance: -15% (basis divergence)
    (0.3, -0.40),  # 30% chance: -40% (significant issues)
    (0.1, -0.80),  # 10% chance: -80% (major failure)
]

expected_loss_given_collapse = sum(p * loss for p, loss in loss_scenarios)
weight_per_coin = 1 / n_coins

# Annual expected loss
annual_survivorship_drag = p_at_least_one * expected_loss_given_collapse * weight_per_coin

print(f"""
Expected loss given collapse: {expected_loss_given_collapse*100:.1f}%
Weight per coin: {weight_per_coin*100:.1f}%
P(at least one collapse/year): {p_at_least_one*100:.1f}%

ANNUAL SURVIVORSHIP DRAG: {annual_survivorship_drag*100:.2f}%

RANGE ESTIMATE:
- Optimistic (we exit before collapse): 0.0% drag
- Base case (calculated above): {annual_survivorship_drag*100:.2f}%
- Pessimistic (2 collapses, bad exits): {annual_survivorship_drag*100*3:.2f}%
""")

SURVIVORSHIP_DRAG_LOW = 0.0
SURVIVORSHIP_DRAG_MID = abs(annual_survivorship_drag)
SURVIVORSHIP_DRAG_HIGH = abs(annual_survivorship_drag) * 3

# =============================================================================
# SECTION 3: HONEST SHARPE CALCULATION
# =============================================================================

print("=" * 70)
print("SECTION 3: HONEST SHARPE CALCULATION")
print("=" * 70)

# Load actual returns data
def load_combined_funding(ticker: str) -> pd.Series:
    path = os.path.join(COMBINED_DIR, f"{ticker}_funding_combined.csv")
    if not os.path.exists(path):
        return pd.Series(dtype=float)
    df = pd.read_csv(path, parse_dates=['datetime'])
    df = df.set_index('datetime')
    return df['fundingRate']

btc_funding = load_combined_funding("BTC")
CAPITAL_MULT = 1.5
carry_returns = btc_funding / CAPITAL_MULT

# Load trend returns
from sysdata.config.configdata import Config
from systems.provided.crypto_example.crypto_system import crypto_system

config = Config("systems.provided.crypto_example.crypto_config_diversified.yaml")
system = crypto_system(data_path=PRICE_DIR, config=config)
account = system.accounts.portfolio()
trend_returns = account.percent / 100
trend_returns.index = pd.to_datetime(trend_returns.index.date)

# Align periods for correlation
common_idx = trend_returns.index.intersection(carry_returns.index)
trend_aligned = trend_returns.loc[common_idx]
carry_aligned = carry_returns.loc[common_idx]

print("\n--- TREND SLEEVE ---")
print("-" * 40)

trend_raw_sr = trend_aligned.mean() / trend_aligned.std() * np.sqrt(252)
trend_vol = trend_aligned.std() * np.sqrt(252)
trend_cost_sr_impact = TREND_ANNUAL_COST / trend_vol

print(f"Raw Sharpe (backtest): {trend_raw_sr:.2f}")
print(f"Annual volatility: {trend_vol*100:.1f}%")
print(f"Annual cost drag: {TREND_ANNUAL_COST*100:.1f}%")
print(f"Cost impact in SR terms: {trend_cost_sr_impact:.2f}")

trend_honest_sr = trend_raw_sr - trend_cost_sr_impact
print(f"TREND HONEST SHARPE: {trend_honest_sr:.2f}")

print("\n--- CARRY SLEEVE ---")
print("-" * 40)

carry_raw_sr = carry_returns.mean() / carry_returns.std() * np.sqrt(365)
carry_vol = carry_returns.std() * np.sqrt(365)
carry_cost_sr_impact = CARRY_ANNUAL_COST / carry_vol

print(f"Raw Sharpe (full history): {carry_raw_sr:.2f}")
print(f"Annual volatility: {carry_vol*100:.1f}%")
print(f"Annual cost drag: {CARRY_ANNUAL_COST*100:.1f}%")
print(f"Cost impact in SR terms: {carry_cost_sr_impact:.2f}")

# Survivorship in SR terms
survivorship_sr_impact_low = SURVIVORSHIP_DRAG_LOW / carry_vol
survivorship_sr_impact_mid = SURVIVORSHIP_DRAG_MID / carry_vol
survivorship_sr_impact_high = SURVIVORSHIP_DRAG_HIGH / carry_vol

carry_after_costs = carry_raw_sr - carry_cost_sr_impact

print(f"\nAfter costs: {carry_after_costs:.2f}")
print(f"Survivorship drag range: {survivorship_sr_impact_low:.2f} to {survivorship_sr_impact_high:.2f} SR")

carry_honest_sr_low = carry_after_costs - survivorship_sr_impact_high
carry_honest_sr_mid = carry_after_costs - survivorship_sr_impact_mid
carry_honest_sr_high = carry_after_costs - survivorship_sr_impact_low

print(f"CARRY HONEST SHARPE RANGE: {carry_honest_sr_low:.2f} to {carry_honest_sr_high:.2f}")
print(f"CARRY HONEST SHARPE (midpoint): {carry_honest_sr_mid:.2f}")

# =============================================================================
# SECTION 4: COMBINED PORTFOLIO
# =============================================================================

print("\n" + "=" * 70)
print("SECTION 4: COMBINED PORTFOLIO (50/50)")
print("=" * 70)

# Portfolio combination with diversification
correlation = trend_aligned.corr(carry_aligned)
print(f"\nTrend-Carry Correlation: {correlation:.2f}")

# Allocation (pre-specified, not optimized)
w_trend = 0.5
w_carry = 0.5

# Combined Sharpe with diversification benefit
# SR_portfolio = (w1*SR1 + w2*SR2) / sqrt(w1^2 + w2^2 + 2*w1*w2*corr)
# For equal vol targeting, this simplifies

def combined_sharpe(sr1, sr2, w1, w2, corr):
    """Calculate combined Sharpe with diversification."""
    # Assuming equal vol contribution (vol-targeted)
    numerator = w1 * sr1 + w2 * sr2
    denominator = np.sqrt(w1**2 + w2**2 + 2*w1*w2*corr)
    return numerator / denominator

print("\nCombined Sharpe Calculation:")
print("-" * 40)

# Range of combined Sharpes
combined_sr_low = combined_sharpe(trend_honest_sr, carry_honest_sr_low, w_trend, w_carry, correlation)
combined_sr_mid = combined_sharpe(trend_honest_sr, carry_honest_sr_mid, w_trend, w_carry, correlation)
combined_sr_high = combined_sharpe(trend_honest_sr, carry_honest_sr_high, w_trend, w_carry, correlation)

print(f"Trend Sharpe: {trend_honest_sr:.2f}")
print(f"Carry Sharpe: {carry_honest_sr_low:.2f} to {carry_honest_sr_high:.2f}")
print(f"Correlation: {correlation:.2f}")
print(f"Allocation: {w_trend*100:.0f}% Trend / {w_carry*100:.0f}% Carry")

print(f"\nDiversification multiplier: {1/np.sqrt(w_trend**2 + w_carry**2 + 2*w_trend*w_carry*correlation):.2f}")

print(f"\nCOMBINED SHARPE RANGE: {combined_sr_low:.2f} to {combined_sr_high:.2f}")
print(f"COMBINED SHARPE (best estimate): {combined_sr_mid:.2f}")

# After pessimism haircut
print("\n--- After 50% Pessimism Haircut ---")
conservative_sr_low = combined_sr_low * 0.5
conservative_sr_mid = combined_sr_mid * 0.5
conservative_sr_high = combined_sr_high * 0.5

print(f"CONSERVATIVE SHARPE RANGE: {conservative_sr_low:.2f} to {conservative_sr_high:.2f}")
print(f"CONSERVATIVE SHARPE (for expectations): {conservative_sr_mid:.2f}")

# =============================================================================
# SECTION 5: FINAL OUTPUT
# =============================================================================

print("\n" + "=" * 70)
print("FINAL OUTPUT: DEFENSIBLE SHARPE ESTIMATES")
print("=" * 70)

print(f"""
┌─────────────────────────────────────────────────────────────────────┐
│                    FINAL SHARPE ESTIMATES                           │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  COMPONENT SHARPES (for understanding):                             │
│  ──────────────────────────────────────                             │
│  Trend:  {trend_honest_sr:.2f}  (after {TREND_ANNUAL_COST*100:.1f}% cost drag)                          │
│  Carry:  {carry_honest_sr_mid:.2f}  (after {CARRY_ANNUAL_COST*100:.1f}% costs + {SURVIVORSHIP_DRAG_MID*100:.1f}% survivorship)      │
│                                                                     │
│  COMBINED 50/50 PORTFOLIO:                                          │
│  ─────────────────────────                                          │
│  Best Estimate:         {combined_sr_mid:.2f}                                       │
│  Plausible Range:       {combined_sr_low:.2f} to {combined_sr_high:.2f}                               │
│                                                                     │
│  CONSERVATIVE (after 50% haircut):                                  │
│  ─────────────────────────────────                                  │
│  For Return Expectations: {conservative_sr_mid:.2f}                                 │
│  Plausible Range:         {conservative_sr_low:.2f} to {conservative_sr_high:.2f}                           │
│                                                                     │
│  WHAT THIS MEANS AT 25% TARGET VOL:                                 │
│  ──────────────────────────────────                                 │
│  Expected Return (best):        {combined_sr_mid * 0.25 * 100:>5.1f}%                          │
│  Expected Return (conservative): {conservative_sr_mid * 0.25 * 100:>5.1f}%                          │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘

KEY ASSUMPTIONS:
================
1. Transaction costs:
   - Trend: {TREND_ANNUAL_COST*100:.1f}% (6x turnover × 0.1% per trade)
   - Carry: {CARRY_ANNUAL_COST*100:.1f}% (16 rebalances × 0.15% × 50% position)

2. Survivorship bias:
   - 3% collapse probability per coin per year
   - 22% chance of at least one collapse across 8-coin portfolio
   - Expected loss given collapse: ~{expected_loss_given_collapse*100:.0f}% of that position
   - Annual drag: {SURVIVORSHIP_DRAG_MID*100:.1f}% (range: 0% to {SURVIVORSHIP_DRAG_HIGH*100:.1f}%)

3. Allocation: 50/50 (pre-specified for skewness, not optimized)

4. Pessimism factor: 50% for return expectations only
   (Position sizing uses vol-targeting, not Sharpe)

RECOMMENDATION:
===============
Use {conservative_sr_mid:.2f} Sharpe for setting return expectations.
At 25% vol target, expect ~{conservative_sr_mid * 0.25 * 100:.0f}% annual returns.
Actual results will vary: could be -{0.25 * 100:.0f}% to +{(combined_sr_high + 1) * 0.25 * 100:.0f}% in any given year.
""")
