"""
Portfolio Experiment Runner
============================
Master script to run all 9 portfolio combinations and generate comprehensive comparison.

Experiment Matrix:
    Case A: CARRY only (100% CARRY)
    Case B: TREND STATIC only (100% TREND static universe)
    Case C: TREND DYNAMIC only (100% TREND dynamic universe)
    Case D1: CARRY + TREND STATIC (80% TREND / 20% CARRY)
    Case D2: CARRY + TREND STATIC (50% TREND / 50% CARRY)
    Case D3: CARRY + TREND STATIC (20% TREND / 80% CARRY)
    Case E1: CARRY + TREND DYNAMIC (80% TREND / 20% CARRY)
    Case E2: CARRY + TREND DYNAMIC (50% TREND / 50% CARRY)
    Case E3: CARRY + TREND DYNAMIC (20% TREND / 80% CARRY)

Usage:
    python run_portfolio_experiment.py [--force-recalc] [--start-date YYYY-MM-DD]
"""

import os
import sys
import argparse
import pandas as pd
import numpy as np

# Get project root and add to path
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, "..", "..", "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import logging
logging.disable(logging.CRITICAL)
import warnings
warnings.filterwarnings('ignore')

# Local imports
from .carry_returns import get_carry_returns
from ..core.cache_systems import (
    cache_exists, save_returns, load_returns,
    clear_cache, print_cache_summary
)
from ..core.portfolio_combiner import combine_sleeves_simple_weights
from ..core.portfolio_metrics import calculate_all_metrics, format_metrics_table


def load_btc_returns(start_date='2020-01-01'):
    """
    Load BTC returns for beta calculation.

    Args:
        start_date: Start date for returns

    Returns:
        pd.Series: Daily BTC percentage returns
    """
    print("\n" + "=" * 90)
    print("LOADING BTC RETURNS (for beta calculation)")
    print("=" * 90)

    # Try to load from cache first
    if cache_exists('btc_returns'):
        try:
            btc_rets = load_returns('btc_returns')
            if btc_rets.index.min() <= pd.Timestamp(start_date):
                return btc_rets[btc_rets.index >= start_date]
        except Exception as e:
            print(f"  Warning: Failed to load BTC from cache: {e}")
            print("  Will load from CSV instead")

    # Load BTC price from CSV
    btc_path = os.path.join(PROJECT_ROOT, "data", "crypto", "BTC.csv")

    if not os.path.exists(btc_path):
        print(f"  Warning: BTC price file not found at {btc_path}")
        print("  Beta calculation will be skipped")
        return None

    df = pd.read_csv(btc_path, parse_dates=['date'])
    df = df.set_index('date')
    df.index = pd.to_datetime(df.index.date)

    # Calculate returns
    btc_price = df['close']
    btc_returns = btc_price.pct_change().dropna()

    # Cache for future use
    save_returns(btc_returns, 'btc_returns', metadata={
        'source': 'data/crypto/BTC.csv',
        'start_date': str(btc_returns.index.min().date()),
        'end_date': str(btc_returns.index.max().date())
    })

    # Filter to start date
    btc_returns_filtered = btc_returns[btc_returns.index >= start_date]

    print(f"  Date range: {btc_returns_filtered.index.min().date()} to {btc_returns_filtered.index.max().date()}")
    print(f"  Days: {len(btc_returns_filtered)}")

    return btc_returns_filtered


def get_trend_static_returns(start_date='2020-01-01', force_recalc=False):
    """
    Load or calculate TREND STATIC returns.

    Args:
        start_date: Start date for returns
        force_recalc: Force recalculation even if cache exists

    Returns:
        pd.Series: Daily percentage returns
    """
    print("\n" + "=" * 90)
    print("LOADING TREND STATIC RETURNS")
    print("=" * 90)

    # Check cache
    if not force_recalc and cache_exists('trend_static_returns'):
        try:
            trend_rets = load_returns('trend_static_returns')
            if trend_rets.index.min() <= pd.Timestamp(start_date):
                return trend_rets[trend_rets.index >= start_date]
        except Exception as e:
            print(f"  Warning: Failed to load from cache: {e}")
            print("  Will recalculate instead")

    # Calculate from system
    print("  Running TREND STATIC backtest (this may take 20-30 minutes)...")

    from crypto_system import crypto_system

    system = crypto_system(data_path='data/crypto')
    account = system.accounts.portfolio()

    # Convert to daily percentage returns as decimals (0.01 = 1%)
    # accountCurve.percent gives percentage returns where 1.0 = 1%
    # We need to divide by 100 to get decimals
    trend_returns = pd.Series(account.percent) / 100.0

    # Cache for future use
    save_returns(trend_returns, 'trend_static_returns', metadata={
        'system': 'crypto_system (static universe)',
        'start_date': str(trend_returns.index.min().date()),
        'end_date': str(trend_returns.index.max().date()),
        'instruments': '12 static'
    })

    # Filter to start date
    trend_returns_filtered = trend_returns[trend_returns.index >= start_date]

    return trend_returns_filtered


def get_trend_dynamic_returns(start_date='2020-01-01', force_recalc=False):
    """
    Load or calculate TREND DYNAMIC returns.

    Args:
        start_date: Start date for returns
        force_recalc: Force recalculation even if cache exists

    Returns:
        pd.Series: Daily percentage returns
    """
    print("\n" + "=" * 90)
    print("LOADING TREND DYNAMIC RETURNS")
    print("=" * 90)

    # Check cache
    if not force_recalc and cache_exists('trend_dynamic_returns'):
        try:
            trend_rets = load_returns('trend_dynamic_returns')
            if trend_rets.index.min() <= pd.Timestamp(start_date):
                return trend_rets[trend_rets.index >= start_date]
        except Exception as e:
            print(f"  Warning: Failed to load from cache: {e}")
            print("  Will recalculate instead")

    # Calculate from system
    print("  Running TREND DYNAMIC backtest (this may take 60-90 minutes)...")

    from crypto_system import crypto_system_with_dynamic_universe

    system = crypto_system_with_dynamic_universe(data_path='data/crypto')
    account = system.accounts.portfolio()

    # Convert to daily percentage returns as decimals (0.01 = 1%)
    # accountCurve.percent gives percentage returns where 1.0 = 1%
    # We need to divide by 100 to get decimals
    trend_returns = pd.Series(account.percent) / 100.0

    # Cache for future use
    save_returns(trend_returns, 'trend_dynamic_returns', metadata={
        'system': 'crypto_system_with_dynamic_universe',
        'start_date': str(trend_returns.index.min().date()),
        'end_date': str(trend_returns.index.max().date()),
        'instruments': '~185 dynamic'
    })

    # Filter to start date
    trend_returns_filtered = trend_returns[trend_returns.index >= start_date]

    return trend_returns_filtered


def get_carry_strategy_returns(start_date='2020-01-01', force_recalc=False):
    """
    Load or calculate CARRY returns.

    Args:
        start_date: Start date for returns
        force_recalc: Force recalculation even if cache exists

    Returns:
        pd.Series: Daily percentage returns
    """
    print("\n" + "=" * 90)
    print("LOADING CARRY RETURNS")
    print("=" * 90)

    # Check cache
    if not force_recalc and cache_exists('carry_returns'):
        try:
            carry_rets = load_returns('carry_returns')
            if carry_rets.index.min() <= pd.Timestamp(start_date):
                return carry_rets[carry_rets.index >= start_date]
        except Exception as e:
            print(f"  Warning: Failed to load from cache: {e}")
            print("  Will recalculate instead")

    # Calculate from carry_returns module
    carry_returns = get_carry_returns(start_date=start_date, verbose=True)

    # Cache for future use
    save_returns(carry_returns, 'carry_returns', metadata={
        'source': 'carry_returns.py',
        'start_date': str(carry_returns.index.min().date()),
        'end_date': str(carry_returns.index.max().date()),
        'vol_target': 0.125
    })

    return carry_returns


def run_all_experiments(start_date='2020-01-01', force_recalc=False):
    """
    Run all 9 portfolio experiment cases.

    Args:
        start_date: Start date for analysis
        force_recalc: Force recalculation of base sleeves

    Returns:
        dict: {case_name: {returns: pd.Series, metrics: dict}}
    """
    print("\n" + "=" * 90)
    print("PORTFOLIO EXPERIMENT RUNNER")
    print("=" * 90)
    print(f"  Start date: {start_date}")
    print(f"  Force recalc: {force_recalc}")

    # Step 1: Load base sleeves
    carry_rets = get_carry_strategy_returns(start_date, force_recalc)
    trend_static_rets = get_trend_static_returns(start_date, force_recalc)
    trend_dynamic_rets = get_trend_dynamic_returns(start_date, force_recalc)
    btc_rets = load_btc_returns(start_date)

    # Step 2: Define experiment cases
    cases = {
        'A_CARRY_ONLY': {
            'returns': carry_rets,
            'description': 'CARRY only (100%)'
        },
        'B_TREND_STATIC': {
            'returns': trend_static_rets,
            'description': 'TREND STATIC only (100%)'
        },
        'C_TREND_DYNAMIC': {
            'returns': trend_dynamic_rets,
            'description': 'TREND DYNAMIC only (100%)'
        },
        'D1_STATIC_80_20': {
            'returns': combine_sleeves_simple_weights(
                trend_static_rets, carry_rets, 0.8, 0.2, verbose=True
            ),
            'description': 'CARRY + TREND STATIC (80/20)'
        },
        'D2_STATIC_50_50': {
            'returns': combine_sleeves_simple_weights(
                trend_static_rets, carry_rets, 0.5, 0.5, verbose=True
            ),
            'description': 'CARRY + TREND STATIC (50/50)'
        },
        'D3_STATIC_20_80': {
            'returns': combine_sleeves_simple_weights(
                trend_static_rets, carry_rets, 0.2, 0.8, verbose=True
            ),
            'description': 'CARRY + TREND STATIC (20/80)'
        },
        'E1_DYNAMIC_80_20': {
            'returns': combine_sleeves_simple_weights(
                trend_dynamic_rets, carry_rets, 0.8, 0.2, verbose=True
            ),
            'description': 'CARRY + TREND DYNAMIC (80/20)'
        },
        'E2_DYNAMIC_50_50': {
            'returns': combine_sleeves_simple_weights(
                trend_dynamic_rets, carry_rets, 0.5, 0.5, verbose=True
            ),
            'description': 'CARRY + TREND DYNAMIC (50/50)'
        },
        'E3_DYNAMIC_20_80': {
            'returns': combine_sleeves_simple_weights(
                trend_dynamic_rets, carry_rets, 0.2, 0.8, verbose=True
            ),
            'description': 'CARRY + TREND DYNAMIC (20/80)'
        },
    }

    # Step 3: Calculate metrics for all cases
    print("\n" + "=" * 90)
    print("CALCULATING METRICS FOR ALL CASES")
    print("=" * 90)

    results = {}
    for case_name, case_info in cases.items():
        print(f"\n{case_name}: {case_info['description']}")

        metrics = calculate_all_metrics(
            returns=case_info['returns'],
            name=case_name,
            market_returns=btc_rets,
            market_name='BTC'
        )

        results[case_name] = {
            'returns': case_info['returns'],
            'metrics': metrics,
            'description': case_info['description']
        }

    return results


def save_results(results: dict, output_dir: str = None):
    """
    Save experiment results to CSV.

    Args:
        results: Results dict from run_all_experiments()
        output_dir: Directory to save results (default: same as script)
    """
    if output_dir is None:
        output_dir = SCRIPT_DIR

    # Save metrics table
    metrics_list = [r['metrics'] for r in results.values()]
    table_md = format_metrics_table(metrics_list, format='markdown')
    table_csv = format_metrics_table(metrics_list, format='csv')

    md_path = os.path.join(output_dir, 'portfolio_comparison.md')
    csv_path = os.path.join(output_dir, 'portfolio_comparison.csv')

    with open(md_path, 'w') as f:
        f.write("# Portfolio Comparison\n\n")
        f.write(f"Analysis period: {metrics_list[0]['start_date'].date()} to {metrics_list[0]['end_date'].date()}\n\n")
        f.write(table_md)

    with open(csv_path, 'w') as f:
        f.write(table_csv)

    print(f"\n✓ Saved results:")
    print(f"  Markdown: {md_path}")
    print(f"  CSV: {csv_path}")

    # Save individual portfolio returns to backtest_cache for tail risk analysis
    cache_dir = os.path.join(output_dir, 'backtest_cache')
    os.makedirs(cache_dir, exist_ok=True)

    for case_name, case_data in results.items():
        returns_df = pd.DataFrame({
            'date': case_data['returns'].index,
            'return': case_data['returns'].values
        })
        returns_path = os.path.join(cache_dir, f"{case_name}_returns.csv")
        returns_df.to_csv(returns_path, index=False)

    print(f"  Individual returns: {cache_dir}/ ({len(results)} portfolios)")


# =============================================================================
# MAIN
# =============================================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Run portfolio experiment matrix')
    parser.add_argument('--force-recalc', action='store_true',
                        help='Force recalculation of base sleeves (ignores cache)')
    parser.add_argument('--start-date', type=str, default='2020-01-01',
                        help='Start date for analysis (default: 2020-01-01)')
    parser.add_argument('--show-cache', action='store_true',
                        help='Show cache summary and exit')
    parser.add_argument('--clear-cache', type=str, default=None,
                        help='Clear cache for specific item (or "all")')

    args = parser.parse_args()

    # Handle cache commands
    if args.show_cache:
        print_cache_summary()
        sys.exit(0)

    if args.clear_cache:
        if args.clear_cache.lower() == 'all':
            clear_cache()
        else:
            clear_cache(args.clear_cache)
        sys.exit(0)

    # Run experiments
    results = run_all_experiments(
        start_date=args.start_date,
        force_recalc=args.force_recalc
    )

    # Display results
    print("\n" + "=" * 90)
    print("EXPERIMENT RESULTS")
    print("=" * 90)

    metrics_list = [r['metrics'] for r in results.values()]
    table = format_metrics_table(metrics_list, format='markdown')
    print("\n" + table)

    # Save results
    save_results(results)

    print("\n" + "=" * 90)
    print("✓ Portfolio experiment complete")
    print("=" * 90)
