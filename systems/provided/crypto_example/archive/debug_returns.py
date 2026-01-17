"""
Debug the returns format from pysystemtrade
"""

import sys
import numpy as np
import pandas as pd

sys.path.insert(0, "/Users/nathanieldavis/pysystemtrade")

from sysdata.config.configdata import Config
from systems.provided.crypto_example.crypto_system import crypto_system

PRICE_DIR = "/Users/nathanieldavis/pysystemtrade/data/crypto"
DIVERSIFIED_CONFIG = "systems.provided.crypto_example.crypto_config_diversified.yaml"

# Load system
config = Config(DIVERSIFIED_CONFIG)
system = crypto_system(data_path=PRICE_DIR, config=config)

# Get account
account = system.accounts.portfolio()

# Inspect the account object
print("Account object type:", type(account))
print("\nAccount attributes:")
for attr in dir(account):
    if not attr.startswith('_'):
        print(f"  {attr}")

# Get different return series
print("\n" + "=" * 70)
print("RETURN SERIES COMPARISON")
print("=" * 70)

# Try different methods
try:
    percent = account.percent
    print(f"\naccount.percent:")
    print(f"  Type: {type(percent)}")
    print(f"  Shape: {len(percent)}")
    print(f"  Sample values:")
    print(percent.tail(10))
    print(f"  Mean: {percent.mean():.6f}")
    print(f"  Std: {percent.std():.6f}")
    print(f"  Min: {percent.min():.6f}")
    print(f"  Max: {percent.max():.6f}")
except Exception as e:
    print(f"Error with percent: {e}")

try:
    curve = account.curve()
    print(f"\naccount.curve():")
    print(f"  Type: {type(curve)}")
    print(f"  Sample values:")
    print(curve.tail(10))
except Exception as e:
    print(f"Error with curve: {e}")

# Check what stats() returns
print("\n" + "=" * 70)
print("STATS FROM ACCOUNT")
print("=" * 70)

try:
    stats = account.stats()
    print(f"\naccount.stats() type: {type(stats)}")
    print(f"stats contents: {stats}")
except Exception as e:
    print(f"Error: {e}")

# Get gross returns
try:
    gross = account.gross.percent
    print(f"\naccount.gross.percent:")
    print(f"  Mean: {gross.mean():.6f}")
    print(f"  Std: {gross.std():.6f}")
    daily_sr = gross.mean() / gross.std() if gross.std() > 0 else 0
    annual_sr = daily_sr * np.sqrt(252)
    print(f"  Daily Sharpe: {daily_sr:.4f}")
    print(f"  Annual Sharpe: {annual_sr:.2f}")
except Exception as e:
    print(f"Error: {e}")

# Try net returns
try:
    net = account.net.percent
    print(f"\naccount.net.percent:")
    print(f"  Mean: {net.mean():.6f}")
    print(f"  Std: {net.std():.6f}")
    daily_sr = net.mean() / net.std() if net.std() > 0 else 0
    annual_sr = daily_sr * np.sqrt(252)
    print(f"  Daily Sharpe: {daily_sr:.4f}")
    print(f"  Annual Sharpe: {annual_sr:.2f}")
except Exception as e:
    print(f"Error: {e}")

# Check cumulative
print("\n" + "=" * 70)
print("CUMULATIVE RETURNS")
print("=" * 70)

cumulative = percent.cumsum()
print(f"Cumulative return at end: {cumulative.iloc[-1]*100:.1f}%")
print(f"Total return (exp): {(np.exp(percent.sum()) - 1)*100:.1f}%")

# If these are log returns, annualize properly
log_returns = percent  # Assume log returns
ann_log_mean = log_returns.mean() * 252
ann_log_std = log_returns.std() * np.sqrt(252)
print(f"\nIf log returns:")
print(f"  Ann mean: {ann_log_mean*100:.2f}%")
print(f"  Ann std: {ann_log_std*100:.2f}%")
print(f"  Sharpe: {ann_log_mean/ann_log_std:.2f}")
