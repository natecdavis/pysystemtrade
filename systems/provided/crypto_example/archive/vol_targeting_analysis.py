"""
VOLATILITY TARGETING ANALYSIS
=============================
Explains why realized vol is ~7% instead of 25% target.
"""

import numpy as np

print("=" * 80)
print("WHY IS REALIZED VOL 7% INSTEAD OF 25%?")
print("=" * 80)

# Configuration
CAPITAL = 10000
VOL_TARGET = 0.25
N_INSTRUMENTS = 15
AVG_CORRELATION = 0.6
AVG_INSTRUMENT_VOL = 1.0  # 100% annual vol for crypto

# IDM calculation
IDM = np.sqrt(N_INSTRUMENTS) / np.sqrt(1 + (N_INSTRUMENTS - 1) * AVG_CORRELATION)
print(f"\nIDM = sqrt({N_INSTRUMENTS}) / sqrt(1 + {N_INSTRUMENTS-1} × {AVG_CORRELATION})")
print(f"    = {np.sqrt(N_INSTRUMENTS):.3f} / {np.sqrt(1 + (N_INSTRUMENTS-1) * AVG_CORRELATION):.3f}")
print(f"    = {IDM:.3f}")

# Instrument weight
WEIGHT = 1.0 / N_INSTRUMENTS
print(f"\nInstrument weight = 1/{N_INSTRUMENTS} = {WEIGHT:.4f}")

# Position sizing formula analysis
print("\n" + "=" * 80)
print("CARVER'S POSITION SIZING FORMULA")
print("=" * 80)

print("""
position = (capital × vol_target) / (price × instrument_vol) × IDM × weight × (forecast/10)

At forecast = 10, for an instrument with price $100 and 100% vol:

Step 1: Subsystem position (targets 25% vol for this instrument alone)
  subsystem = (10000 × 0.25) / (100 × 1.0) = 2500 / 100 = 25 units
  value = 25 × $100 = $2,500
  vol contribution = $2,500 × 100% = $2,500 = 25% of capital ✓

Step 2: After IDM (increases position for diversification benefit)
  position = 25 × 1.26 = 31.5 units
  value = 31.5 × $100 = $3,150
  vol contribution = $3,150 × 100% = 31.5% of capital

Step 3: After instrument weight (decreases for capital allocation)
  position = 31.5 × 0.067 = 2.1 units
  value = 2.1 × $100 = $210
  vol contribution = $210 × 100% = 2.1% of capital

Step 4: After forecast scaling (forecast = 10, so factor = 1.0)
  position = 2.1 × 1.0 = 2.1 units
  value = $210
  vol contribution = 2.1% of capital
""")

# Calculate expected portfolio vol
print("\n" + "=" * 80)
print("EXPECTED PORTFOLIO VOLATILITY")
print("=" * 80)

per_instrument_vol = VOL_TARGET * IDM * WEIGHT
print(f"\nPer-instrument vol contribution = {VOL_TARGET*100}% × {IDM:.3f} × {WEIGHT:.4f}")
print(f"                                = {per_instrument_vol*100:.2f}% of capital")

# Portfolio vol formula for equal-weighted correlated assets
# var = n × w² × σ² × (1 + (n-1)ρ) / n = w² × σ² × (1 + (n-1)ρ)
# With per-instrument vol contribution σ_contrib:
# portfolio_var = n × σ_contrib² × (1/n + (n-1)/n × ρ) = σ_contrib² × (1 + (n-1)ρ)

portfolio_var = per_instrument_vol**2 * N_INSTRUMENTS * (1 + (N_INSTRUMENTS-1) * AVG_CORRELATION * (1/N_INSTRUMENTS))
# Actually the correct formula is:
# portfolio_var = Σᵢ Σⱼ σᵢ σⱼ ρᵢⱼ
# For equal contributions σ and correlations ρ:
# = n × σ² (for i=j) + n(n-1) × σ² × ρ (for i≠j)
# = n × σ² × (1 + (n-1)ρ)

portfolio_var = N_INSTRUMENTS * per_instrument_vol**2 * (1/N_INSTRUMENTS + (N_INSTRUMENTS-1)/N_INSTRUMENTS * AVG_CORRELATION)
portfolio_vol = np.sqrt(portfolio_var)

print(f"\nPortfolio variance = {N_INSTRUMENTS} × {per_instrument_vol*100:.2f}%² × (1/{N_INSTRUMENTS} + {N_INSTRUMENTS-1}/{N_INSTRUMENTS} × {AVG_CORRELATION})")
print(f"                   = {N_INSTRUMENTS} × {per_instrument_vol**2*10000:.4f}%² × {1/N_INSTRUMENTS + (N_INSTRUMENTS-1)/N_INSTRUMENTS * AVG_CORRELATION:.3f}")
print(f"                   = {portfolio_var*10000:.4f}%²")
print(f"\nPortfolio vol = sqrt({portfolio_var*10000:.4f}%²) = {portfolio_vol*100:.2f}%")

print("\n" + "=" * 80)
print("THE ROOT CAUSE")
print("=" * 80)

print(f"""
The formula gives portfolio vol of {portfolio_vol*100:.1f}%, not 25%.

This is because:

1. Subsystem position targets 25% vol for ONE instrument using FULL capital
2. IDM increases this by 1.26x (for diversification benefit)
3. BUT instrument weight divides by 15 (for capital allocation)

Net scaling factor = IDM × weight = {IDM:.3f} × {WEIGHT:.4f} = {IDM*WEIGHT:.4f}

This reduces each position's vol contribution from 25% to {25*IDM*WEIGHT:.2f}%.

With 15 positions at {25*IDM*WEIGHT:.2f}% vol each and 60% correlation:
Expected portfolio vol ≈ {portfolio_vol*100:.1f}%
""")

print("\n" + "=" * 80)
print("CARVER'S ACTUAL INTENT")
print("=" * 80)

print("""
In Carver's framework (for leveraged futures trading):

1. Each instrument can hold a FULL-SIZED position (using margin)
2. IDM accounts for diversification benefit (hold more than you would unleveraged)
3. Instrument weight is for FORECAST WEIGHTING, not position sizing

For SPOT trading (like crypto without leverage):

The problem is we're applying weight to POSITION SIZING, which limits
total notional to capital. This caps achievable vol.

OPTION A: Remove weight from position sizing (allows leverage)
  - position = subsystem × IDM × (forecast/10)
  - Total notional can exceed capital
  - Achieves 25% target vol
  - Requires leverage or margin

OPTION B: Accept lower target vol (no leverage)
  - Maximum achievable vol with 15 instruments at 60% corr:
    - If each gets capital/15: max vol ≈ 20%
  - Set vol_target = 20% instead of 25%

OPTION C: Use fewer instruments (concentrates capital)
  - With 5 instruments: max vol ≈ 35%
  - With 3 instruments: max vol ≈ 50%
  - Trade-off: less diversification

OPTION D: Use higher IDM (accepts more concentration risk)
  - Current IDM: 1.26
  - To achieve 25% vol: need IDM × weight × √(n × (1 + (n-1)ρ)) = 1
  - Required IDM ≈ 3.0 (much higher than diversification benefit)
""")

# Calculate maximum achievable vol with spot trading
print("\n" + "=" * 80)
print("MAXIMUM ACHIEVABLE VOL (SPOT TRADING)")
print("=" * 80)

# If each instrument gets capital/n, and has 100% vol:
max_per_instrument = CAPITAL / N_INSTRUMENTS
max_per_instrument_vol = max_per_instrument * AVG_INSTRUMENT_VOL / CAPITAL
max_portfolio_vol = np.sqrt(N_INSTRUMENTS * max_per_instrument_vol**2 * (1/N_INSTRUMENTS + (N_INSTRUMENTS-1)/N_INSTRUMENTS * AVG_CORRELATION))

print(f"\nWith ${CAPITAL:,} capital split equally across {N_INSTRUMENTS} instruments:")
print(f"  Capital per instrument: ${max_per_instrument:.2f}")
print(f"  Vol contribution per instrument: {max_per_instrument_vol*100:.2f}%")
print(f"  Maximum portfolio vol: {max_portfolio_vol*100:.1f}%")

# To achieve 25% vol
required_leverage = VOL_TARGET / max_portfolio_vol
print(f"\nTo achieve 25% vol, need {required_leverage:.2f}x leverage")

print("\n" + "=" * 80)
print("RECOMMENDATIONS")
print("=" * 80)

print(f"""
For $10,000 capital with 15 crypto instruments:

1. ACCEPT LOWER VOL TARGET
   - Set vol_target = {max_portfolio_vol*100:.0f}% (achievable without leverage)
   - Current formula works correctly
   - Expected Sharpe unchanged (return and vol both scale)

2. USE FEWER INSTRUMENTS
   - 8 instruments would allow ~25% vol
   - Less diversification but achieves target

3. USE LEVERAGE (if available)
   - {required_leverage:.1f}x leverage achieves 25% vol target
   - Only if your exchange/broker allows margin

4. MODIFY FORMULA (remove weight from position sizing)
   - position = subsystem × IDM × (forecast/10)
   - Allows notional > capital
   - Effectively builds in leverage
   - WARNING: Positions can exceed capital!

CURRENT BACKTEST:
- Realized vol: 7%
- This is CORRECT given the formula
- The formula caps positions to avoid leverage
- To increase vol, modify the approach above
""")
