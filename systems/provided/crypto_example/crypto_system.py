"""
Crypto backtesting system for pysystemtrade.

This module provides factory functions to create complete backtesting systems
for crypto trading strategies.

Example usage:
    from systems.provided.crypto_example import crypto_system

    # Create a system with default config
    system = crypto_system(data_path='/path/to/crypto/csvs')

    # Run the backtest
    account = system.accounts.portfolio()
    print(account.stats())

    # Get positions for a specific instrument
    positions = system.portfolio.get_notional_position('BTC')
"""

import os
from typing import Optional

from syscore.constants import arg_not_supplied
from syscore.fileutils import resolve_path_and_filename_for_package

from sysdata.config.configdata import Config
from sysdata.crypto import csvSpotSimData

from systems.basesystem import System
from systems.forecasting import Rules
from systems.forecast_combine import ForecastCombine
from systems.forecast_scale_cap import ForecastScaleCap
from systems.rawdata import RawData
from systems.positionsizing import PositionSizing
from systems.portfolio import Portfolios
from systems.accounts.accounts_stage import Account


# Default config path
DEFAULT_CONFIG_PATH = "systems.provided.crypto_example.crypto_config.yaml"


def crypto_system(
    data_path: str,
    config: Optional[Config] = None,
    instrument_config: dict = arg_not_supplied,
    instrument_config_file: str = arg_not_supplied,
) -> System:
    """
    Create a crypto backtesting system with fixed weights.

    This creates a complete backtesting system using:
    - csvSpotSimData for loading crypto price data from CSVs
    - EWMAC trading rules (trend following)
    - Fixed forecast and instrument weights
    - Standard volatility targeting and position sizing

    Args:
        data_path: Path to directory containing crypto CSV files
            (e.g., '/data/crypto/' containing 'BTC.csv', 'ETH.csv')
        config: Optional Config object. If not provided, uses default config.
        instrument_config: Optional dict of instrument-specific settings
        instrument_config_file: Optional path to instrument config YAML file

    Returns:
        System object ready for backtesting

    Example:
        system = crypto_system(data_path='/data/crypto')

        # View available instruments
        print(system.data.get_instrument_list())

        # Get portfolio returns
        account = system.accounts.portfolio()
        print(account.percent.stats())

        # Get positions
        btc_position = system.portfolio.get_notional_position('BTC')
    """
    # Load default config if not provided
    if config is None:
        config = Config(DEFAULT_CONFIG_PATH)

    # Create data object
    data = csvSpotSimData(
        data_path=data_path,
        instrument_config=instrument_config,
        instrument_config_file=instrument_config_file,
    )

    # Create system with all stages
    system = System(
        stage_list=[
            Account(),
            Portfolios(),
            PositionSizing(),
            ForecastCombine(),
            ForecastScaleCap(),
            Rules(),
            RawData(),
        ],
        data=data,
        config=config,
    )

    return system


def crypto_system_with_estimate(
    data_path: str,
    config: Optional[Config] = None,
    instrument_config: dict = arg_not_supplied,
    instrument_config_file: str = arg_not_supplied,
) -> System:
    """
    Create a crypto backtesting system with estimated weights.

    Similar to crypto_system() but uses estimated forecast weights
    and scalars based on historical performance.

    This is useful for:
    - Discovering optimal rule combinations
    - Validating fixed weight choices
    - Research and experimentation

    Note: Estimated weights can lead to overfitting. Use with caution
    and consider out-of-sample testing.

    Args:
        data_path: Path to directory containing crypto CSV files
        config: Optional Config object with estimation settings
        instrument_config: Optional dict of instrument-specific settings
        instrument_config_file: Optional path to instrument config YAML file

    Returns:
        System object with estimation enabled

    Example:
        system = crypto_system_with_estimate(data_path='/data/crypto')
        weights = system.combForecast.get_forecast_weights('BTC')
    """
    # Load default config if not provided
    if config is None:
        config = Config(DEFAULT_CONFIG_PATH)

    # Enable estimation
    config.use_forecast_scale_estimates = True
    config.use_forecast_weight_estimates = True

    # Create data object
    data = csvSpotSimData(
        data_path=data_path,
        instrument_config=instrument_config,
        instrument_config_file=instrument_config_file,
    )

    # Create system with all stages
    system = System(
        stage_list=[
            Account(),
            Portfolios(),
            PositionSizing(),
            ForecastCombine(),
            ForecastScaleCap(),
            Rules(),
            RawData(),
        ],
        data=data,
        config=config,
    )

    return system


if __name__ == "__main__":
    # Example usage - update the path to your crypto data
    import sys

    if len(sys.argv) < 2:
        print("Usage: python crypto_system.py /path/to/crypto/csvs")
        print("")
        print("Expected CSV format:")
        print("  date,open,high,low,close,volume")
        print("  2020-01-01,7200.00,7250.00,7150.00,7220.00,1234567")
        print("")
        print("Files should be named: BTC.csv, ETH.csv, etc.")
        sys.exit(1)

    data_path = sys.argv[1]

    if not os.path.exists(data_path):
        print(f"Error: Data path does not exist: {data_path}")
        sys.exit(1)

    print(f"Loading crypto data from: {data_path}")
    system = crypto_system(data_path=data_path)

    print(f"Instruments: {system.data.get_instrument_list()}")

    # Run backtest
    print("\nRunning backtest...")
    account = system.accounts.portfolio()

    print("\n=== Portfolio Statistics ===")
    print(account.percent.stats())
