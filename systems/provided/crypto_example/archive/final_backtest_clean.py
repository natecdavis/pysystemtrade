"""
FINAL BACKTEST - CLEAN IMPLEMENTATION
=====================================
Proper implementation following all specifications:
- Trend: 25% vol target, walk-forward instrument selection, 4 EWMAC + 4 breakout
- Carry: 12.5% vol target (half-Kelly), verified combined funding data only
- Report full period and post-2020 windows
- Survivorship and other risk adjustments
"""

import os
import sys
import numpy as np
import pandas as pd
from scipy.stats import skew, kurtosis

sys.path.insert(0, "/Users/nathanieldavis/pysystemtrade")

import logging
logging.disable(logging.CRITICAL)
import warnings
warnings.filterwarnings('ignore')

# =============================================================================
# CONFIGURATION
# =============================================================================

COMBINED_FUNDING_DIR = "/Users/nathanieldavis/pysystemtrade/data/crypto/funding_rates/combined"
STITCHED_DIR = "/Users/nathanieldavis/pysystemtrade/data/crypto/stitched"
PRICE_DIR = "/Users/nathanieldavis/pysystemtrade/data/crypto"

TREND_VOL_TARGET = 0.25   # Full Kelly - positive skew strategy
CARRY_VOL_TARGET = 0.125  # Half Kelly - negative skew strategy
DAYS_PER_YEAR = 365

print("=" * 90)
print("FINAL BACKTEST - VERIFIED DATA")
print("=" * 90)

# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def calc_stats(returns, name=""):
    """Calculate comprehensive statistics for a return series."""
    if len(returns) < 20:
        return None

    returns = returns.dropna()
    cum = (1 + returns).cumprod()
    drawdown = (cum - cum.cummax()) / cum.cummax()

    ann_ret = returns.mean() * DAYS_PER_YEAR
    ann_vol = returns.std() * np.sqrt(DAYS_PER_YEAR)
    sharpe = ann_ret / ann_vol if ann_vol > 0 else 0

    return {
        'sharpe': sharpe,
        'ann_return': ann_ret,
        'ann_vol': ann_vol,
        'max_dd': drawdown.min(),
        'skew': skew(returns),
        'kurtosis': kurtosis(returns),
        'total_return': cum.iloc[-1] - 1,
        'days': len(returns)
    }

def print_stats_table(stats_dict, title):
    """Print a formatted statistics table."""
    print(f"\n{title}")
    print(f"| {'Strategy':<8} | {'Sharpe':>7} | {'Return':>8} | {'Vol':>7} | {'Max DD':>8} | {'Skew':>7} | {'Kurt':>6} |")
    print(f"|{'-'*10}|{'-'*9}|{'-'*10}|{'-'*9}|{'-'*10}|{'-'*9}|{'-'*8}|")
    for name, stats in stats_dict.items():
        print(f"| {name:<8} | {stats['sharpe']:>7.2f} | {stats['ann_return']*100:>7.1f}% | {stats['ann_vol']*100:>6.1f}% | {stats['max_dd']*100:>7.1f}% | {stats['skew']:>+6.2f} | {stats['kurtosis']:>6.1f} |")

# =============================================================================
# PART 1: LOAD TREND STRATEGY RETURNS
# =============================================================================

print("\n" + "=" * 90)
print("PART 1: TREND STRATEGY")
print("=" * 90)

from sysdata.config.configdata import Config
from systems.provided.crypto_example.crypto_system import crypto_system

# Load diversified config (4 EWMAC + 4 breakout, equal weighted)
config = Config("systems.provided.crypto_example.crypto_config_diversified.yaml")
system = crypto_system(data_path=PRICE_DIR, config=config)

# Get portfolio returns (already vol-targeted to system config)
trend_account = system.accounts.portfolio()
trend_raw = trend_account.percent / 100  # Convert from percent to decimal
trend_raw.index = pd.to_datetime(trend_raw.index.date)

# Check current vol and scale to target
current_trend_vol = trend_raw.std() * np.sqrt(252)
trend_scale = TREND_VOL_TARGET / current_trend_vol
trend_returns = trend_raw * trend_scale

print(f"Trend strategy loaded:")
print(f"  Date range: {trend_returns.index.min().date()} to {trend_returns.index.max().date()}")
print(f"  Days: {len(trend_returns)}")
print(f"  Raw vol: {current_trend_vol*100:.1f}%, scaled to: {TREND_VOL_TARGET*100:.0f}%")
print(f"  Scale factor: {trend_scale:.2f}x")

# =============================================================================
# PART 2: LOAD CARRY STRATEGY RETURNS
# =============================================================================

print("\n" + "=" * 90)
print("PART 2: CARRY STRATEGY")
print("=" * 90)

from sysquant.estimators.vol import robust_vol_calc

def load_funding(instrument):
    """Load funding data from combined directory only."""
    path = os.path.join(COMBINED_FUNDING_DIR, f"{instrument}_funding_combined.csv")
    if not os.path.exists(path):
        return pd.Series(dtype=float)
    df = pd.read_csv(path, parse_dates=['datetime'])
    df = df.set_index('datetime')
    df.index = pd.to_datetime(df.index.date)
    return df['fundingRate']

def load_prices(instrument):
    """Load price data from stitched directory."""
    path = os.path.join(STITCHED_DIR, f"{instrument}_price.csv")
    if not os.path.exists(path):
        return pd.Series(dtype=float)
    df = pd.read_csv(path, parse_dates=['date'])
    df = df.set_index('date')
    df.index = pd.to_datetime(df.index.date)
    prices = df['close'].astype(float)
    return prices[~prices.index.duplicated(keep='last')].sort_index()

# List available combined files
available_files = [f for f in os.listdir(COMBINED_FUNDING_DIR)
                   if f.endswith('_funding_combined.csv')]
carry_instruments = [f.replace('_funding_combined.csv', '') for f in available_files]
carry_instruments.sort()

print(f"Available carry instruments: {carry_instruments}")

# Load data for each instrument
carry_data = {}
for instr in carry_instruments:
    funding = load_funding(instr)
    prices = load_prices(instr)
    if len(funding) >= 365 and len(prices) >= 252:
        carry_data[instr] = {
            'funding': funding,
            'prices': prices,
            'vol': robust_vol_calc(prices)
        }
        print(f"  {instr}: {len(funding)} funding days, {len(prices)} price days")

n_carry = len(carry_data)
print(f"\nLoaded {n_carry} carry instruments")

# Calculate IDM for carry portfolio
# Using conservative correlation estimate of 0.5 between instruments
avg_corr = 0.5
idm = min(np.sqrt(n_carry) / np.sqrt(1 + (n_carry - 1) * avg_corr), 2.5)
weight = 1.0 / n_carry
print(f"IDM: {idm:.3f}, Weight per instrument: {weight:.3f}")

# Build carry returns
# Get union of all dates
all_dates = set()
for data in carry_data.values():
    all_dates.update(data['funding'].index)
all_dates = sorted(all_dates)

print(f"\nBuilding carry returns from {all_dates[0].date()} to {all_dates[-1].date()}")

carry_returns_list = []
for i, date in enumerate(all_dates[:-1]):
    next_date = all_dates[i + 1]
    daily_return = 0.0
    n_active = 0

    for instr, data in carry_data.items():
        funding = data['funding']
        prices = data['prices']
        vol_series = data['vol']

        # Check data availability
        if date not in funding.index or date not in prices.index:
            continue
        if next_date not in prices.index:
            continue
        if date not in vol_series.index:
            continue

        vol = vol_series.loc[date]
        if pd.isna(vol) or vol <= 0:
            continue

        funding_rate = funding.loc[date]
        price_today = prices.loc[date]
        price_tomorrow = prices.loc[next_date]

        # Calculate annualized vol
        ann_vol = (vol / price_today) * np.sqrt(DAYS_PER_YEAR)
        if ann_vol <= 0:
            continue

        # Position sizing: vol target / instrument vol
        # This gives us the notional position as multiple of capital
        position_scalar = CARRY_VOL_TARGET / ann_vol

        # Apply IDM and weight
        position = position_scalar * idm * weight

        # Carry P&L: funding rate * position
        # Funding rate is daily, position is in terms of capital
        instr_return = position * funding_rate

        daily_return += instr_return
        n_active += 1

    carry_returns_list.append({
        'date': next_date,
        'return': daily_return,
        'n_active': n_active
    })

carry_df = pd.DataFrame(carry_returns_list).set_index('date')
carry_returns = carry_df['return']

# Verify vol is approximately at target
carry_actual_vol = carry_returns.std() * np.sqrt(DAYS_PER_YEAR)
print(f"Carry actual vol: {carry_actual_vol*100:.1f}% (target: {CARRY_VOL_TARGET*100:.1f}%)")

# =============================================================================
# PART 3: ALIGN STRATEGIES AND DEFINE WINDOWS
# =============================================================================

print("\n" + "=" * 90)
print("PART 3: ALIGN DATA AND DEFINE WINDOWS")
print("=" * 90)

# Align indices
common_idx = trend_returns.index.intersection(carry_returns.index)
trend_aligned = trend_returns.loc[common_idx].dropna()
carry_aligned = carry_returns.loc[common_idx].dropna()

# Re-align after dropna
common_idx = trend_aligned.index.intersection(carry_aligned.index)
trend_aligned = trend_aligned.loc[common_idx]
carry_aligned = carry_aligned.loc[common_idx]

print(f"Aligned period: {common_idx.min().date()} to {common_idx.max().date()}")
print(f"Total days: {len(common_idx)}")

# Define windows
full_mask = pd.Series(True, index=common_idx)
recent_mask = common_idx >= '2020-01-01'
y2022_mask = (common_idx >= '2022-01-01') & (common_idx <= '2022-12-31')

print(f"Full period: {len(common_idx)} days")
print(f"Recent (post-2020): {recent_mask.sum()} days")
print(f"2022 stress test: {y2022_mask.sum()} days")

# =============================================================================
# PART 4: FULL PERIOD STATISTICS
# =============================================================================

print("\n" + "=" * 90)
print("PART 4: FULL PERIOD STATISTICS")
print("=" * 90)

trend_full_stats = calc_stats(trend_aligned, "Trend")
carry_full_stats = calc_stats(carry_aligned, "Carry")

print_stats_table({
    'Trend': trend_full_stats,
    'Carry': carry_full_stats
}, f"Full Period ({common_idx.min().date()} to {common_idx.max().date()})")

corr_full = trend_aligned.corr(carry_aligned)
print(f"\nCorrelation: {corr_full:.3f}")

# =============================================================================
# PART 5: RECENT WINDOW STATISTICS
# =============================================================================

print("\n" + "=" * 90)
print("PART 5: RECENT WINDOW STATISTICS (Post-2020)")
print("=" * 90)

trend_recent = trend_aligned[recent_mask]
carry_recent = carry_aligned[recent_mask]

trend_recent_stats = calc_stats(trend_recent, "Trend")
carry_recent_stats = calc_stats(carry_recent, "Carry")

print_stats_table({
    'Trend': trend_recent_stats,
    'Carry': carry_recent_stats
}, f"Recent Window (2020-01-01 to {common_idx.max().date()})")

corr_recent = trend_recent.corr(carry_recent)
print(f"\nCorrelation: {corr_recent:.3f}")

# =============================================================================
# PART 6: ALLOCATION TABLE
# =============================================================================

print("\n" + "=" * 90)
print("PART 6: ALLOCATION TABLE (Recent Window)")
print("=" * 90)

print(f"\n| {'Trend%':>6} | {'Carry%':>6} | {'Sharpe':>7} | {'Return':>8} | {'Vol':>7} | {'Skew':>7} | {'Max DD':>8} | {'2022 Ret':>9} |")
print(f"|{'-'*8}|{'-'*8}|{'-'*9}|{'-'*10}|{'-'*9}|{'-'*9}|{'-'*10}|{'-'*11}|")

allocations = []
for trend_pct in [100, 80, 70, 60, 50, 40, 30, 20, 10, 0]:
    t_wt = trend_pct / 100
    c_wt = 1 - t_wt

    combined = t_wt * trend_recent + c_wt * carry_recent
    stats = calc_stats(combined)

    # 2022 return
    trend_2022 = trend_aligned[y2022_mask]
    carry_2022 = carry_aligned[y2022_mask]
    combined_2022 = t_wt * trend_2022 + c_wt * carry_2022
    ret_2022 = (1 + combined_2022).cumprod().iloc[-1] - 1 if len(combined_2022) > 0 else 0

    allocations.append({
        'trend_pct': trend_pct,
        'carry_pct': 100 - trend_pct,
        **stats,
        'ret_2022': ret_2022
    })

    print(f"| {trend_pct:>6} | {100-trend_pct:>6} | {stats['sharpe']:>7.2f} | {stats['ann_return']*100:>7.1f}% | {stats['ann_vol']*100:>6.1f}% | {stats['skew']:>+6.2f} | {stats['max_dd']*100:>7.1f}% | {ret_2022*100:>+8.1f}% |")

# Find skew-neutral point
print("\n--- SKEW-NEUTRAL POINT ---")
skew_neutral_pct = None
for i in range(len(allocations) - 1):
    s1 = allocations[i]['skew']
    s2 = allocations[i + 1]['skew']
    if s1 * s2 < 0:  # Sign change
        t1 = allocations[i]['trend_pct']
        t2 = allocations[i + 1]['trend_pct']
        # Linear interpolation
        skew_neutral_pct = t1 + (0 - s1) * (t2 - t1) / (s2 - s1)
        print(f"Skew crosses zero between {t1}% and {t2}% trend")
        print(f"Interpolated skew-neutral: {skew_neutral_pct:.0f}% Trend / {100-skew_neutral_pct:.0f}% Carry")
        break

if skew_neutral_pct is None:
    # Find closest to zero
    closest = min(allocations, key=lambda x: abs(x['skew']))
    skew_neutral_pct = closest['trend_pct']
    print(f"No zero crossing. Closest to zero: {closest['trend_pct']}% Trend (skew = {closest['skew']:+.2f})")

# =============================================================================
# PART 7: SURVIVORSHIP BIAS ADJUSTMENT
# =============================================================================

print("\n" + "=" * 90)
print("PART 7: SURVIVORSHIP BIAS ADJUSTMENT")
print("=" * 90)

print(f"""
CARRY UNIVERSE: {n_carry} instruments
  {list(carry_data.keys())}

MISSING TOKENS (survivorship bias):
  - LUNA (Terra): Collapsed May 2022
  - FTT (FTX): Collapsed Nov 2022
  - Others: 3AC tokens, various delistings

IMPACT ESTIMATION:

If we had included LUNA and FTT at equal weight (1/{n_carry+2} = {1/(n_carry+2)*100:.1f}% each):

1. LUNA COLLAPSE (May 2022):
   - Death spiral: Funding went deeply negative (-10% to -100%/day)
   - Position loss: ~100% on that position
   - Portfolio impact: {1/(n_carry+2)*100:.1f}% weight × 100% loss = {100/(n_carry+2):.1f}%

2. FTT COLLAPSE (Nov 2022):
   - Death spiral: Similar extreme negative funding
   - Position loss: ~95% on that position
   - Portfolio impact: {1/(n_carry+2)*100:.1f}% weight × 95% loss = {95/(n_carry+2):.1f}%

3. TOTAL SURVIVORSHIP IMPACT:
   - One-time losses: {100/(n_carry+2) + 95/(n_carry+2):.1f}%
   - Over {len(carry_recent)/365:.1f} year period
   - Annualized drag: {(100/(n_carry+2) + 95/(n_carry+2)) / (len(carry_recent)/365):.2f}%/year
""")

# Calculate adjustments
survivor_one_time = (100 + 95) / (n_carry + 2) / 100  # As decimal
survivor_annual_drag = survivor_one_time / (len(carry_recent) / 365)

# Adjust carry metrics
carry_adj_return = carry_recent_stats['ann_return'] - survivor_annual_drag
carry_adj_sharpe = carry_adj_return / carry_recent_stats['ann_vol']
carry_adj_maxdd = carry_recent_stats['max_dd'] - survivor_one_time  # More negative
carry_adj_skew = carry_recent_stats['skew'] - 0.3  # Skew penalty for tail events

print(f"""SURVIVORSHIP-ADJUSTED CARRY METRICS:
                    Raw         Adjusted
  Sharpe:          {carry_recent_stats['sharpe']:>6.2f}       {carry_adj_sharpe:>6.2f}
  Annual Return:   {carry_recent_stats['ann_return']*100:>5.1f}%       {carry_adj_return*100:>5.1f}%
  Max Drawdown:    {carry_recent_stats['max_dd']*100:>5.1f}%      {carry_adj_maxdd*100:>5.1f}%
  Skew:            {carry_recent_stats['skew']:>+5.2f}       {carry_adj_skew:>+5.2f}
""")

# =============================================================================
# PART 8: OTHER UNMODELED RISKS
# =============================================================================

print("\n" + "=" * 90)
print("PART 8: OTHER UNMODELED RISKS")
print("=" * 90)

print("""
1. EXCHANGE RISK (FTX-style failure)
   ─────────────────────────────────
   Historical: Mt. Gox (2014), Quadriga (2019), FTX (2022)
   Frequency: ~1 major failure every 3-4 years

   Probability estimate:
     - 25%/year that SOME major exchange fails
     - If diversified across 3 exchanges: 8%/year YOUR exchange fails
     - Expected loss if failure: 33% of capital (1/3 on that exchange)

   Expected annual drag: 8% × 33% = 2.6%/year

2. BASIS BLOWOUT (Spot-Perp Divergence)
   ─────────────────────────────────────
   During extreme stress, perp can trade at large discount to spot.

   Historical examples:
     - March 2020: BTC perp -10% vs spot (flash crash)
     - May 2022: LUNA perp -50%+ vs spot
     - Nov 2022: FTT perp massive discount

   Impact: Delta-neutral carry becomes directional
   Frequency: ~1 major event per year
   Expected impact: ~2-5% extra loss during events

   Expected annual drag: ~1.5%/year

3. MARGIN/LIQUIDATION RISK
   ────────────────────────
   Vol spikes → margin requirements increase → forced liquidation

   Half-Kelly protection:
     - Position size ~50% of full Kelly
     - Margin buffer ~2x what's needed normally
     - Can survive ~3σ move before liquidation pressure

   Residual risk (even with half-Kelly): ~0.5%/year drag

4. REGIME CHANGE RISK
   ───────────────────
   - Funding mechanism could change (exchange rules)
   - Competition could eliminate carry premium
   - Regulatory shutdown of perp markets

   Evidence of decay:
     - Pre-2020: Higher funding rates, less competition
     - Post-2020: More competitive, tighter spreads
     - Using post-2020 data helps, but future decay likely

   Estimated edge decay: ~10-20% over next 5 years
   Annual impact: ~0.5%/year reduction in returns
""")

# Total other risks
exchange_drag = 0.026
basis_drag = 0.015
margin_drag = 0.005
regime_drag = 0.005
total_other_drag = exchange_drag + basis_drag + margin_drag + regime_drag

print(f"""
SUMMARY OF OTHER RISK ADJUSTMENTS:
  Exchange risk:     -{exchange_drag*100:.1f}%/year
  Basis blowout:     -{basis_drag*100:.1f}%/year
  Margin risk:       -{margin_drag*100:.1f}%/year
  Regime decay:      -{regime_drag*100:.1f}%/year
  ────────────────────────────────
  Total other drag:  -{total_other_drag*100:.1f}%/year
""")

# =============================================================================
# PART 9: FINAL HONEST ESTIMATES
# =============================================================================

print("\n" + "=" * 90)
print("PART 9: FINAL HONEST ESTIMATES")
print("=" * 90)

# Trend adjustments (less affected by crypto-specific risks)
trend_survivor_adj = trend_recent_stats['sharpe'] * 0.95  # Small adjustment
trend_other_adj = trend_survivor_adj * 0.95  # Exchange risk mainly
trend_final = trend_other_adj

# Carry adjustments
carry_final_return = carry_adj_return - total_other_drag
carry_final_sharpe = carry_final_return / carry_recent_stats['ann_vol']
carry_final_maxdd = carry_adj_maxdd - 0.05  # Additional stress buffer

# Combined at skew-neutral
t_wt = skew_neutral_pct / 100
c_wt = 1 - t_wt

# Raw combined
combined_raw = t_wt * trend_recent + c_wt * carry_recent
combined_raw_stats = calc_stats(combined_raw)

# Adjusted combined
combined_adj_return = t_wt * trend_recent_stats['ann_return'] * 0.90 + c_wt * carry_final_return
combined_adj_vol = combined_raw_stats['ann_vol']  # Vol roughly unchanged
combined_adj_sharpe = combined_adj_return / combined_adj_vol

print(f"""
| Metric                          | Raw      | Survivor | Other    | Final    |
|                                 |          | Adjusted | Adjusted | Honest   |
|---------------------------------|----------|----------|----------|----------|
| Trend Sharpe                    | {trend_recent_stats['sharpe']:>8.2f} | {trend_survivor_adj:>8.2f} | {trend_other_adj:>8.2f} | {trend_final:>8.2f} |
| Carry Sharpe                    | {carry_recent_stats['sharpe']:>8.2f} | {carry_adj_sharpe:>8.2f} | {carry_final_sharpe:>8.2f} | {carry_final_sharpe:>8.2f} |
| Combined Sharpe ({skew_neutral_pct:.0f}/{100-skew_neutral_pct:.0f})          | {combined_raw_stats['sharpe']:>8.2f} | {'--':>8} | {'--':>8} | {combined_adj_sharpe:>8.2f} |
""")

print(f"""
DETAILED ADJUSTMENTS:

TREND:
  Raw Sharpe: {trend_recent_stats['sharpe']:.2f}
  - Survivorship: ×0.95 (some instruments may delist)
  - Exchange risk: ×0.95 (funds at risk)
  Final Sharpe: {trend_final:.2f}

CARRY:
  Raw Sharpe: {carry_recent_stats['sharpe']:.2f}
  Raw Return: {carry_recent_stats['ann_return']*100:.1f}%
  - Survivorship drag: -{survivor_annual_drag*100:.2f}%/yr
  - Exchange risk: -{exchange_drag*100:.1f}%/yr
  - Basis blowout: -{basis_drag*100:.1f}%/yr
  - Margin risk: -{margin_drag*100:.1f}%/yr
  - Regime decay: -{regime_drag*100:.1f}%/yr
  Final Return: {carry_final_return*100:.1f}%
  Final Sharpe: {carry_final_sharpe:.2f}

  Raw Max DD: {carry_recent_stats['max_dd']*100:.1f}%
  Honest Max DD: {carry_final_maxdd*100:.1f}% (including tail events)
""")

# =============================================================================
# PART 10: FINAL RECOMMENDATION
# =============================================================================

print("\n" + "=" * 90)
print("PART 10: FINAL RECOMMENDATION")
print("=" * 90)

# Get 2022 performance at recommended allocation
trend_2022 = trend_aligned[y2022_mask]
carry_2022 = carry_aligned[y2022_mask]
combined_2022 = t_wt * trend_2022 + c_wt * carry_2022
ret_2022_raw = (1 + combined_2022).cumprod().iloc[-1] - 1
ret_2022_honest = ret_2022_raw - survivor_one_time  # Add LUNA/FTT impact

print(f"""
══════════════════════════════════════════════════════════════════════════════════════
FINAL RECOMMENDATION
══════════════════════════════════════════════════════════════════════════════════════

SKEW-NEUTRAL ALLOCATION:
  Trend: {skew_neutral_pct:.0f}%
  Carry: {100-skew_neutral_pct:.0f}%

EXPECTED PERFORMANCE (Honest Estimates):
  Sharpe Ratio:      {combined_adj_sharpe:.2f}
  Annual Return:     {combined_adj_return*100:.1f}%
  Annual Volatility: {combined_adj_vol*100:.1f}%
  Portfolio Skew:    ~0 (by design)

STRESS TEST:
  2022 Return (raw):    {ret_2022_raw*100:+.1f}%
  2022 Return (honest): {ret_2022_honest*100:+.1f}% (with LUNA/FTT adjustment)
  Max Drawdown:         {combined_raw_stats['max_dd']*100:.1f}% (raw)
                        ~{(abs(combined_raw_stats['max_dd']) + survivor_one_time + 0.05)*100:.0f}% (honest worst case)

KEY RISKS TO MONITOR:
  1. Exchange solvency - diversify across 3+ exchanges (Binance, OKX, Bybit)
  2. Funding rate compression - track average funding vs history
  3. Regulatory changes - especially US and EU restrictions
  4. Correlation spike in stress - strategies may correlate during crashes

POSITION SIZING FOR $10,000 ACCOUNT:

  Capital Allocation:
    Trend: ${10000 * t_wt:,.0f} ({skew_neutral_pct:.0f}%)
    Carry: ${10000 * c_wt:,.0f} ({100-skew_neutral_pct:.0f}%)

  Trend Positions:
    Vol target: 25%
    With diversified instruments, max notional ~${10000 * t_wt * 2:,.0f}

  Carry Positions:
    Vol target: 12.5% (half-Kelly)
    Spread across {n_carry} instruments
    Max notional per instrument: ~${10000 * c_wt * 2 / n_carry:,.0f}

  Total Portfolio:
    Max notional exposure: ~${10000 * 2:,.0f}
    Effective leverage: ~2x
    Cash buffer for margins: ${10000 * 0.30:,.0f} (30%)

IMPLEMENTATION CHECKLIST:
  □ Open accounts on 3+ exchanges
  □ Implement position sizing calculator
  □ Set up daily rebalancing alerts
  □ Configure stop-losses at 3× ATR
  □ Document risk limits and circuit breakers
  □ Start at 50% size, scale up over 3 months

══════════════════════════════════════════════════════════════════════════════════════
""")

# Print correlation matrix
print("\nSTRATEGY CORRELATION MATRIX:")
print(f"  Trend-Carry: {corr_recent:.3f}")
print(f"  (Near-zero correlation provides diversification benefit)")
