"""
Quick CAGR calculation for both strategies (2018+ period)
"""
import numpy as np
import logging
from systems.provided.crypto_example.crypto_system import (
    crypto_system,
    crypto_system_with_dynamic_universe,
)

# Silence verbose logging
logging.getLogger().setLevel(logging.ERROR)

print("Loading systems (this will take a few minutes)...")
print()

static_system = crypto_system(data_path='data/crypto')
dynamic_system = crypto_system_with_dynamic_universe(data_path='data/crypto')

print("Calculating CAGRs (2018-01-01 onwards)...")
print()

# Get account curves (daily % returns)
account_static = static_system.accounts.portfolio().percent.loc['2018-01-01':]
account_dynamic = dynamic_system.accounts.portfolio().percent.loc['2018-01-01':]

# Calculate CAGR using geometric mean
# CAGR = [(1 + r1) * (1 + r2) * ... * (1 + rn)]^(1/years) - 1
years = len(account_static) / 256  # 256 trading days per year

# Convert daily percentage returns to cumulative growth
static_cum_return = (1 + account_static / 100).prod()
dynamic_cum_return = (1 + account_dynamic / 100).prod()

# Annualize to get CAGR
static_cagr = (static_cum_return ** (1/years) - 1) * 100
dynamic_cagr = (dynamic_cum_return ** (1/years) - 1) * 100

# Calculate volatility
static_vol = account_static.std() * np.sqrt(256)
dynamic_vol = account_dynamic.std() * np.sqrt(256)

# Calculate Sharpe (using CAGR / vol for simplicity)
static_sharpe = static_cagr / static_vol
dynamic_sharpe = dynamic_cagr / dynamic_vol

# Print results
print("=" * 70)
print("COMPOUND ANNUAL GROWTH RATES (2018-01-01 onwards)")
print("=" * 70)
print()
print(f"STATIC UNIVERSE (12 instruments):")
print(f"  CAGR:   {static_cagr:>8.2f}%")
print(f"  Vol:    {static_vol:>8.2f}%")
print(f"  Sharpe: {static_sharpe:>8.3f}")
print()
print(f"DYNAMIC UNIVERSE (~185 instruments):")
print(f"  CAGR:   {dynamic_cagr:>8.2f}%")
print(f"  Vol:    {dynamic_vol:>8.2f}%")
print(f"  Sharpe: {dynamic_sharpe:>8.3f}")
print()
print("=" * 70)
print("RATIOS:")
print(f"  CAGR (Dynamic/Static):  {dynamic_cagr/static_cagr:>6.2f}x")
print(f"  Vol (Dynamic/Static):   {dynamic_vol/static_vol:>6.2f}x")
print(f"  Sharpe Difference:      {dynamic_sharpe - static_sharpe:>6.3f}")
print("=" * 70)
print()
print(f"Days analyzed: {len(account_static)}")
print(f"Years: {years:.2f}")
print(f"Cumulative return - Static: {(static_cum_return - 1) * 100:.2f}%")
print(f"Cumulative return - Dynamic: {(dynamic_cum_return - 1) * 100:.2f}%")
