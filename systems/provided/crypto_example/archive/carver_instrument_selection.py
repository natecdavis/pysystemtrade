"""
Carver Instrument Selection for $10k Crypto Account
=====================================================
Applying Carver's methodology from pysystemtrade:
1. Calculate cost per trade in SR units
2. Apply Carver's filters (cost < 0.01 SR, annual < 0.13 SR)
3. Run greedy algorithm to find optimal instrument set
4. Capital allocation check
"""

import os
import sys
import numpy as np
import pandas as pd
from datetime import datetime

sys.path.insert(0, "/Users/nathanieldavis/pysystemtrade")

KRAKEN_DIR = "/Users/nathanieldavis/pysystemtrade/data/crypto/Kraken_OHLCVT"
FUNDING_DIR = "/Users/nathanieldavis/pysystemtrade/data/crypto/funding_rates/combined"

# Account parameters
ACCOUNT_SIZE = 10000  # $10k
TARGET_VOL = 0.25     # 25% annual vol target

# Cost assumptions (conservative for retail)
TAKER_FEE = 0.001     # 0.1% taker fee (Binance/Kraken standard)
MAKER_FEE = 0.0005    # 0.05% maker fee
SPREAD_BPS = 5        # 5 bps typical spread for major tokens
SPREAD_BPS_ALT = 15   # 15 bps for smaller alts

# Carver's thresholds
MAX_COST_PER_TRADE_SR = 0.01   # From docs/instruments.md
MAX_ANNUAL_COST_SR = 0.13      # From docs/backtesting.md
MIN_HISTORY_YEARS = 3.0        # Carver suggests 20 years per parameter, we use 3 as minimum

print("=" * 80)
print("CARVER INSTRUMENT SELECTION FOR $10K CRYPTO ACCOUNT")
print("=" * 80)

# =============================================================================
# STEP 1: LOAD ALL KRAKEN USD DATA
# =============================================================================

print("\n" + "=" * 80)
print("STEP 1: LOADING FULL KRAKEN UNIVERSE")
print("=" * 80)

def load_kraken_data(pair: str) -> pd.DataFrame:
    """Load Kraken daily OHLCV data."""
    path = os.path.join(KRAKEN_DIR, f"{pair}_1440.csv")
    if not os.path.exists(path):
        return None
    try:
        df = pd.read_csv(path, header=None,
                        names=["timestamp", "open", "high", "low", "close", "volume", "trades"])
        df["date"] = pd.to_datetime(df["timestamp"], unit="s")
        df = df.set_index("date").sort_index()
        return df
    except:
        return None

# Find all USD pairs
usd_pairs = []
for f in os.listdir(KRAKEN_DIR):
    if f.endswith("USD_1440.csv"):
        pair = f.replace("_1440.csv", "")
        usd_pairs.append(pair)

print(f"Found {len(usd_pairs)} USD pairs")

# Load and analyze each
instruments = []
for pair in sorted(usd_pairs):
    df = load_kraken_data(pair)
    if df is None or len(df) < 100:
        continue

    # Calculate statistics
    ticker = pair.replace("USD", "")
    if ticker == "XBT":
        ticker = "BTC"

    # Returns
    df["returns"] = df["close"].pct_change()

    # Annualized volatility
    ann_vol = df["returns"].std() * np.sqrt(365)

    # History length
    years = len(df) / 365

    # Average daily volume in USD
    df["volume_usd"] = df["volume"] * df["close"]
    avg_volume_usd = df["volume_usd"].mean()

    # Current price
    current_price = df["close"].iloc[-1]

    instruments.append({
        "ticker": ticker,
        "pair": pair,
        "years": years,
        "ann_vol": ann_vol,
        "avg_volume_usd": avg_volume_usd,
        "current_price": current_price,
        "n_days": len(df),
        "start_date": df.index.min(),
        "end_date": df.index.max()
    })

instruments_df = pd.DataFrame(instruments)
print(f"Loaded {len(instruments_df)} instruments with 100+ days of data")

# =============================================================================
# STEP 2: CALCULATE COST PER TRADE IN SR UNITS
# =============================================================================

print("\n" + "=" * 80)
print("STEP 2: CALCULATING COST PER TRADE IN SR UNITS")
print("=" * 80)

print("""
Carver's formula: SR_cost = cost_currency / annualized_stdev_currency

For a round-trip trade:
- Trading cost = 2 × (spread/2 + fee) = spread + 2×fee
- SR_cost = trading_cost_pct / annual_volatility
""")

def calculate_sr_cost(ann_vol: float, avg_volume_usd: float, is_major: bool = False) -> float:
    """
    Calculate SR cost per round-trip trade.

    Major tokens (BTC, ETH, etc): tighter spreads
    Alt tokens: wider spreads
    """
    # Spread in percentage terms
    if is_major or avg_volume_usd > 10_000_000:  # >$10M daily volume
        spread_pct = SPREAD_BPS / 10000
    elif avg_volume_usd > 1_000_000:  # >$1M daily volume
        spread_pct = SPREAD_BPS_ALT / 10000
    else:  # Low volume
        spread_pct = SPREAD_BPS_ALT * 2 / 10000  # 30 bps

    # Round-trip cost = spread + 2 × fee
    round_trip_cost_pct = spread_pct + 2 * TAKER_FEE

    # SR cost = trading cost / annual vol
    sr_cost = round_trip_cost_pct / ann_vol if ann_vol > 0 else 999

    return sr_cost, round_trip_cost_pct, spread_pct

# Major tokens list
MAJOR_TOKENS = ["BTC", "ETH", "XRP", "LTC", "BCH", "ADA", "DOT", "LINK", "SOL", "AVAX", "MATIC", "ATOM"]

# Calculate for each instrument
sr_costs = []
for _, row in instruments_df.iterrows():
    is_major = row["ticker"] in MAJOR_TOKENS
    sr_cost, rt_cost, spread = calculate_sr_cost(row["ann_vol"], row["avg_volume_usd"], is_major)
    sr_costs.append({
        "ticker": row["ticker"],
        "sr_cost_per_trade": sr_cost,
        "round_trip_pct": rt_cost,
        "spread_pct": spread
    })

costs_df = pd.DataFrame(sr_costs)
instruments_df = instruments_df.merge(costs_df, on="ticker")

# Apply Carver's filter
instruments_df["passes_cost_filter"] = instruments_df["sr_cost_per_trade"] <= MAX_COST_PER_TRADE_SR

print(f"\nCarver's cost filter: SR cost per trade <= {MAX_COST_PER_TRADE_SR}")
print(f"  Pass: {instruments_df['passes_cost_filter'].sum()}")
print(f"  Fail: {(~instruments_df['passes_cost_filter']).sum()}")

# Show top instruments by SR cost (lowest = best)
print("\nTop 30 instruments by SR cost (lowest = best):")
print("-" * 90)
print(f"{'Ticker':<8} {'Years':>6} {'Vol':>7} {'Vol$M':>8} {'SR Cost':>8} {'RT Cost':>8} {'Pass':>6}")
print("-" * 90)
for _, row in instruments_df.nsmallest(30, "sr_cost_per_trade").iterrows():
    print(f"{row['ticker']:<8} {row['years']:>6.1f} {row['ann_vol']*100:>6.1f}% "
          f"{row['avg_volume_usd']/1e6:>7.2f}M {row['sr_cost_per_trade']:>8.4f} "
          f"{row['round_trip_pct']*100:>7.3f}% {'YES' if row['passes_cost_filter'] else 'NO':>6}")

# =============================================================================
# STEP 3: CALCULATE ANNUAL COST DRAG BY STRATEGY
# =============================================================================

print("\n" + "=" * 80)
print("STEP 3: ANNUAL COST DRAG BY STRATEGY")
print("=" * 80)

# Turnover estimates
TREND_TURNOVER = 12  # ~12 round-trips per year for EWMAC (from Carver's book)
CARRY_TURNOVER = 4   # ~4 round-trips per year (quarterly rebalancing + entry/exit)

print(f"""
Turnover assumptions:
  TREND (EWMAC): ~{TREND_TURNOVER} round-trips/year
  CARRY: ~{CARRY_TURNOVER} round-trips/year (rebalancing + entry/exit)

Annual cost = SR_cost_per_trade × turnover
Carver's ceiling: {MAX_ANNUAL_COST_SR} SR units annual
""")

instruments_df["trend_annual_sr_cost"] = instruments_df["sr_cost_per_trade"] * TREND_TURNOVER
instruments_df["carry_annual_sr_cost"] = instruments_df["sr_cost_per_trade"] * CARRY_TURNOVER

instruments_df["passes_trend_annual"] = instruments_df["trend_annual_sr_cost"] <= MAX_ANNUAL_COST_SR
instruments_df["passes_carry_annual"] = instruments_df["carry_annual_sr_cost"] <= MAX_ANNUAL_COST_SR

print(f"Pass trend annual cost filter: {instruments_df['passes_trend_annual'].sum()}")
print(f"Pass carry annual cost filter: {instruments_df['passes_carry_annual'].sum()}")

# =============================================================================
# STEP 4: APPLY HISTORY FILTER
# =============================================================================

print("\n" + "=" * 80)
print("STEP 4: MINIMUM HISTORY FILTER")
print("=" * 80)

print(f"""
Carver's guidance: 20 years per fitted parameter
For crypto with limited history, we use {MIN_HISTORY_YEARS} years minimum.
""")

instruments_df["passes_history"] = instruments_df["years"] >= MIN_HISTORY_YEARS

print(f"Pass history filter ({MIN_HISTORY_YEARS}+ years): {instruments_df['passes_history'].sum()}")

# Combined filters
instruments_df["eligible_for_trend"] = (
    instruments_df["passes_cost_filter"] &
    instruments_df["passes_trend_annual"] &
    instruments_df["passes_history"]
)

print(f"\nEligible for TREND (all filters): {instruments_df['eligible_for_trend'].sum()}")

# Show eligible instruments
print("\nTREND-eligible instruments (sorted by years of history):")
print("-" * 100)
print(f"{'Ticker':<8} {'Years':>6} {'Vol':>7} {'Vol$M':>8} {'SR/Trade':>9} {'SR/Year':>9} {'Price':>10}")
print("-" * 100)

trend_eligible = instruments_df[instruments_df["eligible_for_trend"]].sort_values("years", ascending=False)
for _, row in trend_eligible.iterrows():
    print(f"{row['ticker']:<8} {row['years']:>6.1f} {row['ann_vol']*100:>6.1f}% "
          f"{row['avg_volume_usd']/1e6:>7.2f}M {row['sr_cost_per_trade']:>9.4f} "
          f"{row['trend_annual_sr_cost']:>9.4f} ${row['current_price']:>9.2f}")

# =============================================================================
# STEP 5: CHECK CARRY AVAILABILITY
# =============================================================================

print("\n" + "=" * 80)
print("STEP 5: CARRY TOKEN AVAILABILITY")
print("=" * 80)

# Check which tokens have funding rate data
carry_tokens = []
for f in os.listdir(FUNDING_DIR):
    if f.endswith("_funding_combined.csv"):
        ticker = f.replace("_funding_combined.csv", "")
        carry_tokens.append(ticker)

print(f"Tokens with funding rate data: {carry_tokens}")

# Mark carry eligibility
instruments_df["has_funding_data"] = instruments_df["ticker"].isin(carry_tokens)
instruments_df["eligible_for_carry"] = (
    instruments_df["has_funding_data"] &
    instruments_df["passes_cost_filter"] &
    instruments_df["passes_carry_annual"] &
    instruments_df["passes_history"]
)

print(f"\nEligible for CARRY (all filters + funding data): {instruments_df['eligible_for_carry'].sum()}")

carry_eligible = instruments_df[instruments_df["eligible_for_carry"]].sort_values("years", ascending=False)
if len(carry_eligible) > 0:
    print("\nCARRY-eligible instruments:")
    for _, row in carry_eligible.iterrows():
        print(f"  {row['ticker']}: {row['years']:.1f} years, SR cost {row['sr_cost_per_trade']:.4f}")

# =============================================================================
# STEP 6: CARVER'S GREEDY ALGORITHM
# =============================================================================

print("\n" + "=" * 80)
print("STEP 6: CARVER'S GREEDY ALGORITHM")
print("=" * 80)

print("""
From pysystemtrade/systems/provided/static_small_system_optimise/optimise_small_system.py:

1. Start with best instrument (highest net SR after costs)
2. Add next-best instrument
3. Recalculate portfolio SR (including IDM benefit)
4. If new_SR < previous_SR × 0.9, STOP
5. Otherwise continue

For simplicity, we assume:
- Base trend Sharpe: 0.4 (conservative estimate for crypto trend)
- Correlation between crypto: 0.6 (high correlation)
""")

BASE_TREND_SR = 0.4
CRYPTO_CORRELATION = 0.6

def calculate_idm(n_instruments: int, avg_correlation: float) -> float:
    """
    IDM = 1 / sqrt(avg weighted portfolio variance)
    For equal weights: variance = (1/n) + (1-1/n) * correlation
    IDM = sqrt(n / (1 + (n-1) * correlation))
    Capped at 2.5 per Carver's default
    """
    if n_instruments <= 0:
        return 1.0
    variance = (1/n_instruments) + (1 - 1/n_instruments) * avg_correlation
    idm = 1 / np.sqrt(variance)
    return min(idm, 2.5)

def portfolio_sr(n_instruments: int, base_sr: float, avg_cost_drag: float,
                 avg_correlation: float) -> float:
    """Calculate portfolio Sharpe after IDM benefit and costs."""
    idm = calculate_idm(n_instruments, avg_correlation)
    # Net SR = (Base SR - cost drag) × IDM_benefit
    # IDM benefit is sqrt because it affects both return and vol
    net_sr = (base_sr - avg_cost_drag) * np.sqrt(idm)
    return net_sr, idm

# Run greedy algorithm for TREND
eligible = trend_eligible.copy()
if len(eligible) == 0:
    print("No eligible instruments!")
else:
    # Sort by net SR (base - annual cost)
    eligible["net_sr"] = BASE_TREND_SR - eligible["trend_annual_sr_cost"]
    eligible = eligible.sort_values("net_sr", ascending=False)

    print("\nRunning greedy algorithm for TREND:")
    print("-" * 80)
    print(f"{'N':>3} {'Added':<8} {'IDM':>5} {'Avg Cost':>9} {'Port SR':>8} {'Change':>8}")
    print("-" * 80)

    selected = []
    prev_sr = 0

    for i, (_, row) in enumerate(eligible.iterrows()):
        # Add this instrument
        test_selected = selected + [row["ticker"]]
        n = len(test_selected)

        # Calculate average cost drag
        avg_cost = eligible[eligible["ticker"].isin(test_selected)]["trend_annual_sr_cost"].mean()

        # Calculate portfolio SR
        port_sr, idm = portfolio_sr(n, BASE_TREND_SR, avg_cost, CRYPTO_CORRELATION)

        # Check stopping condition
        if i > 0 and port_sr < prev_sr * 0.9:
            print(f"\n*** STOPPING: SR dropped from {prev_sr:.4f} to {port_sr:.4f} ({(port_sr/prev_sr-1)*100:.1f}%)")
            break

        change = ((port_sr / prev_sr) - 1) * 100 if prev_sr > 0 else 0
        print(f"{n:>3} {row['ticker']:<8} {idm:>5.2f} {avg_cost:>9.4f} {port_sr:>8.4f} {change:>+7.1f}%")

        selected.append(row["ticker"])
        prev_sr = port_sr

    print(f"\nOptimal TREND portfolio: {len(selected)} instruments")
    print(f"  Instruments: {selected}")
    print(f"  Final IDM: {calculate_idm(len(selected), CRYPTO_CORRELATION):.2f}")
    print(f"  Final Portfolio SR: {prev_sr:.4f}")

# =============================================================================
# STEP 7: CAPITAL ALLOCATION CHECK
# =============================================================================

print("\n" + "=" * 80)
print("STEP 7: CAPITAL ALLOCATION CHECK FOR $10K")
print("=" * 80)

print(f"""
Carver's minimum position rule (from optimise_small_system.py):
- If maximum_position < 0.5 contracts, apply massive penalty (9999)
- In crypto, we can trade fractional, so this is less binding

But we still need to check:
- Can we take meaningful positions in all instruments?
- What's the minimum position value per instrument?
""")

if len(selected) > 0:
    n_instruments = len(selected)
    idm = calculate_idm(n_instruments, CRYPTO_CORRELATION)
    weight_per_instrument = 1 / n_instruments

    # Capital per instrument at 25% target vol
    # Position = (Capital × Weight × IDM × Target_Vol) / Instrument_Vol

    print(f"\nWith {n_instruments} instruments, IDM = {idm:.2f}:")
    print(f"  Weight per instrument: {weight_per_instrument*100:.1f}%")
    print(f"  Capital per instrument: ${ACCOUNT_SIZE * weight_per_instrument:,.0f}")

    print("\nPosition sizing at full forecast:")
    print("-" * 80)
    print(f"{'Ticker':<8} {'Vol':>7} {'Price':>10} {'Notional':>10} {'Contracts':>10}")
    print("-" * 80)

    selected_df = eligible[eligible["ticker"].isin(selected)]
    total_notional = 0

    for _, row in selected_df.iterrows():
        # Notional position = (Capital × Weight × IDM × Target_Vol) / Instrument_Vol
        notional = (ACCOUNT_SIZE * weight_per_instrument * idm * TARGET_VOL) / row["ann_vol"]
        contracts = notional / row["current_price"]
        total_notional += notional

        print(f"{row['ticker']:<8} {row['ann_vol']*100:>6.1f}% ${row['current_price']:>9.2f} "
              f"${notional:>9.0f} {contracts:>10.4f}")

    print("-" * 80)
    print(f"{'TOTAL':<8} {'':>7} {'':>10} ${total_notional:>9.0f}")

    # Check leverage
    leverage = total_notional / ACCOUNT_SIZE
    print(f"\nImplied leverage: {leverage:.2f}x")

    if leverage > 3:
        print("WARNING: Leverage exceeds 3x - consider reducing position size or instrument count")

# =============================================================================
# STEP 8: FINAL RECOMMENDATIONS
# =============================================================================

print("\n" + "=" * 80)
print("STEP 8: FINAL RECOMMENDATIONS FOR $10K ACCOUNT")
print("=" * 80)

# Summarize by history buckets
print("\n--- TREND UNIVERSE BY HISTORY ---")
print(f"{'History':<15} {'Count':>6} {'Top Instruments':<50}")
print("-" * 75)

for min_yrs, max_yrs, label in [(7, 20, "7+ years"), (5, 7, "5-7 years"), (3, 5, "3-5 years")]:
    bucket = trend_eligible[(trend_eligible["years"] >= min_yrs) & (trend_eligible["years"] < max_yrs)]
    if len(bucket) > 0:
        top_tickers = ", ".join(bucket.nlargest(10, "years")["ticker"].tolist())
        print(f"{label:<15} {len(bucket):>6} {top_tickers:<50}")

print("\n--- CARRY UNIVERSE ---")
if len(carry_eligible) > 0:
    print(f"Eligible tokens: {', '.join(carry_eligible['ticker'].tolist())}")
else:
    print("No carry-eligible tokens with sufficient history")

print(f"""
--- RECOMMENDATIONS ---

1. TREND STRATEGY at $10k:
   - Optimal instruments: {len(selected) if len(selected) > 0 else 'N/A'}
   - Recommended: {', '.join(selected[:5]) if len(selected) > 0 else 'N/A'}
   - Expected IDM: {calculate_idm(min(len(selected), 5), CRYPTO_CORRELATION):.2f}
   - This is viable at $10k with fractional trading

2. CARRY STRATEGY at $10k:
   - Available tokens: {len(carry_eligible)}
   - Challenge: Delta-neutral requires 1.5x capital (spot + margin)
   - At $10k: Only ~$6.6k effective for carry
   - Recommend: Start trend-only, add carry when capital > $25k

3. COMBINED at $10k:
   - NOT recommended at this capital level
   - Focus on trend with 3-5 instruments first
   - Build capital, then diversify

4. MINIMUM CAPITAL FOR FULL STRATEGY:
   - Trend only: $10k viable
   - Trend + Carry: $25k minimum
   - Full diversification: $50k+ recommended
""")
