"""
Crypto backtesting example for pysystemtrade.

This module provides a pre-configured system for backtesting crypto
trading strategies using spot prices from CSV files.

Example usage:
    from systems.provided.crypto_example import crypto_system

    # Create system with your data
    system = crypto_system(data_path='/path/to/crypto/csvs')

    # Get backtest results
    account = system.accounts.portfolio()
    print(account.stats())
"""

from systems.provided.crypto_example.crypto_system import (
    crypto_system,
    crypto_system_with_estimate,
)

__all__ = ["crypto_system", "crypto_system_with_estimate"]
