"""
Crypto perpetual futures data adapters.

Crypto data adapters for pysystemtrade backtesting.

This module provides data adapters for backtesting crypto trading strategies
using spot price data from CSV files.

Example usage:
    from sysdata.crypto import csvSpotSimData

    data = csvSpotSimData(data_path='/path/to/crypto/csvs')
    prices = data.daily_prices('BTC')
"""

from sysdata.crypto.spot_sim_data import csvSpotSimData
from sysdata.crypto.csv_spot_data import csvSpotPricesData
from sysdata.crypto.spot_instrument_data import csvSpotInstrumentData

__all__ = [
    "csvSpotSimData",
    "csvSpotPricesData",
    "csvSpotInstrumentData",
]
