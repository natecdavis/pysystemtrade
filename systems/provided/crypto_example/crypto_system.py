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
DEFAULT_CONFIG_PATH = "systems.provided.crypto_example.crypto_config_diversified.yaml"


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


def crypto_system_with_dynamic_universe(
    data_path: str,
    config: Optional[Config] = None,
    dynamic_universe_config: dict = arg_not_supplied,
    instrument_config: dict = arg_not_supplied,
    instrument_config_file: str = arg_not_supplied,
) -> System:
    """
    Create crypto system with walk-forward dynamic instrument universe.

    Instruments enter when cost filters pass (SR thresholds), exit when
    aggregate forecast hits zero. Uses equal weighting (1/N) among active
    instruments at each date.

    Entry Logic:
        - Cost filter passes (SR per trade ≤ 0.01, annual ≤ 0.13)
        - Has minimum history required for rules

    Exit Logic:
        - Aggregate forecast crosses zero (signal exhausted)
        - Does NOT force exit when cost filter fails

    Hold Logic:
        - Keep position even if cost filter subsequently fails
        - Exit only on signal (forecast ≈ 0)

    Args:
        data_path: Path to crypto CSV files
        config: Optional Config (defaults to crypto_config_diversified.yaml)
        dynamic_universe_config: Cost filter settings (defaults to Carver's thresholds)
        instrument_config: Optional instrument-specific settings
        instrument_config_file: Optional path to instrument config YAML

    Returns:
        System object with dynamic universe enabled

    Example:
        system = crypto_system_with_dynamic_universe(data_path='data/crypto')
        account = system.accounts.portfolio()

        # View universe size over time
        weights = system.portfolio.get_instrument_weights()
        universe_size = (weights > 0).sum(axis=1)
        print(universe_size.describe())

        # Compare to static universe
        system_static = crypto_system(data_path='data/crypto')
        print(f"Static universe: 12 instruments")
        print(f"Dynamic universe avg: {universe_size.mean():.0f} instruments")
    """
    # Load config (use diversified config as base)
    if config is None:
        config = Config(
            "systems.provided.crypto_example.crypto_config_diversified.yaml"
        )

    # Default cost filter settings (Carver's thresholds)
    if dynamic_universe_config is arg_not_supplied:
        dynamic_universe_config = {
            "max_sr_cost_per_trade": 0.01,  # 1% of annual SR per trade
            "max_sr_cost_annual": 0.13,  # 13% of annual SR
            "stack_turnover": 15.0,  # Expected round-trips/year
            "adv_window": 30,  # ADV calculation window (days)
            "fee_bps": 5,  # One-way fee in basis points
        }

    # Create data with dynamic universe enabled
    data = csvSpotSimData(
        data_path=data_path,
        use_dynamic_universe=True,
        dynamic_universe_config=dynamic_universe_config,
        instrument_config=instrument_config,
        instrument_config_file=instrument_config_file,
    )

    # Import dynamic portfolio stage
    from systems.provided.crypto_example.core.dynamic_portfolio import (
        CryptoDynamicPortfolio,
    )

    # Create system with dynamic portfolio
    system = System(
        stage_list=[
            Account(),
            CryptoDynamicPortfolio(),  # Dynamic weights instead of fixed
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
