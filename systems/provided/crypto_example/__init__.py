"""
Crypto backtesting system for pysystemtrade.

Quick start:
    from systems.provided.crypto_example import crypto_system, run_backtest
    system = crypto_system(data_path='data/crypto')
    results = run_backtest()

Available functions:
    - crypto_system: Create basic crypto trading system
    - crypto_system_with_dynamic_universe: Create system with dynamic instrument selection
    - calculate_all_metrics: Calculate comprehensive performance metrics
    - calculate_tail_metrics: Calculate tail risk metrics
    - calculate_expected_shortfall: Calculate ES95/ES99 (CVaR)
    - calculate_drawdown_duration: Calculate drawdown duration metrics
"""

from .crypto_system import (
    crypto_system,
    crypto_system_with_estimate,
    crypto_system_with_dynamic_universe,
)

from .core.portfolio_metrics import (
    calculate_all_metrics,
    calculate_tail_metrics,
    calculate_expected_shortfall,
    calculate_drawdown_duration,
)

__all__ = [
    'crypto_system',
    'crypto_system_with_estimate',
    'crypto_system_with_dynamic_universe',
    'calculate_all_metrics',
    'calculate_tail_metrics',
    'calculate_expected_shortfall',
    'calculate_drawdown_duration',
]
