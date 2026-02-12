"""
Compare static vs dynamic universe backtest performance.

This script runs both static (12 fixed instruments) and dynamic (cost-filtered)
universe backtests and compares their performance metrics.

Usage:
    python systems/provided/crypto_example/compare_static_vs_dynamic.py
"""

import sys
import pandas as pd
import numpy as np

# Add parent directory to path
sys.path.insert(0, '/Users/nathanieldavis/pysystemtrade')

from systems.provided.crypto_example.crypto_system import (
    crypto_system,
    crypto_system_with_dynamic_universe
)


def run_comparison(data_path='data/crypto', start_date=None):
    """
    Run static vs dynamic universe comparison.

    Args:
        data_path: Path to crypto data directory
        start_date: Optional start date (str or pd.Timestamp) to filter results.
                    If provided, metrics are only computed from this date onwards.
                    Example: '2018-01-01' to focus on stable universe period
    """

    print("="*70)
    print("CRYPTO BACKTEST COMPARISON: Static vs Dynamic Universe")
    if start_date:
        print(f"Filtering results from: {start_date}")
    print("="*70)

    # Static universe backtest
    print("\n" + "="*70)
    print("RUNNING STATIC UNIVERSE BACKTEST (12 fixed instruments)")
    print("="*70)

    try:
        system_static = crypto_system(data_path=data_path)
        print("System created successfully")

        # Get weights to see universe
        weights_static = system_static.portfolio.get_instrument_weights()
        universe_size_static = (weights_static > 0).sum(axis=1)

        print(f"Date range: {weights_static.index[0]} to {weights_static.index[-1]}")
        print(f"Universe size: {universe_size_static.iloc[-1]:.0f} instruments (constant)")

        # Run backtest
        print("Running backtest...")
        account_static = system_static.accounts.portfolio()

        print("✓ Static backtest complete")

    except Exception as e:
        print(f"✗ Error in static backtest: {e}")
        import traceback
        traceback.print_exc()
        return None

    # Dynamic universe backtest
    print("\n" + "="*70)
    print("RUNNING DYNAMIC UNIVERSE BACKTEST (cost-filtered)")
    print("="*70)

    try:
        system_dynamic = crypto_system_with_dynamic_universe(data_path=data_path)
        print("System created successfully")

        # Get weights to see universe
        weights_dynamic = system_dynamic.portfolio.get_instrument_weights()
        universe_size_dynamic = (weights_dynamic > 0).sum(axis=1)

        print(f"Date range: {weights_dynamic.index[0]} to {weights_dynamic.index[-1]}")
        print(f"Universe size range: {universe_size_dynamic.min():.0f}-{universe_size_dynamic.max():.0f} instruments")
        print(f"Average universe size: {universe_size_dynamic.mean():.1f} instruments")

        # Run backtest
        print("Running backtest...")
        account_dynamic = system_dynamic.accounts.portfolio()

        print("✓ Dynamic backtest complete")

    except Exception as e:
        print(f"✗ Error in dynamic backtest: {e}")
        import traceback
        traceback.print_exc()
        return None

    # IMPORTANT NOTE ON FILTERING ACCOUNT CURVES:
    # Using .loc[] on accountCurve objects returns plain pd.Series, which loses:
    # - The .sharpe(), .ann_mean(), .ann_std() methods
    # - The underlying pandl_calculator_with_costs object
    # - The frequency and curve_type metadata
    #
    # To filter correctly:
    # 1. Convert to .percent first (interpretable daily % returns)
    # 2. Filter the percentage series with .loc[]
    # 3. Manually calculate metrics by SUMMING daily returns (not differencing endpoints)
    #
    # See: systems/accounts/curves/account_curve.py for proper metric implementation

    # Filter by start_date if provided
    if start_date:
        start_date = pd.Timestamp(start_date)
        print(f"\nFiltering accounts to dates >= {start_date.date()}")

        # Convert to percentage returns BEFORE filtering
        # This preserves interpretability when we lose accountCurve methods
        account_static_pct = account_static.percent  # Returns accountCurve with is_percentage=True
        account_dynamic_pct = account_dynamic.percent

        # Now filter (still returns plain Series, but values are % returns)
        account_static_filtered = account_static_pct.loc[account_static_pct.index >= start_date]
        account_dynamic_filtered = account_dynamic_pct.loc[account_dynamic_pct.index >= start_date]

        # Filter weights
        weights_static_filtered = weights_static[weights_static.index >= start_date]
        weights_dynamic_filtered = weights_dynamic[weights_dynamic.index >= start_date]
        universe_size_dynamic_filtered = universe_size_dynamic[universe_size_dynamic.index >= start_date]

        print(f"Filtered date range: {account_static_filtered.index[0].date()} to {account_static_filtered.index[-1].date()}")
        print(f"Number of days: {len(account_static_filtered)}")
    else:
        # Use full account curves (convert to percent for consistency)
        account_static_filtered = account_static.percent
        account_dynamic_filtered = account_dynamic.percent
        weights_static_filtered = weights_static
        weights_dynamic_filtered = weights_dynamic
        universe_size_dynamic_filtered = universe_size_dynamic

    # Calculate metrics from filtered series
    print("\n" + "="*70)
    print("CALCULATING PERFORMANCE METRICS")
    print("="*70)

    # Manual metric calculation (filtering loses accountCurve methods)
    from scipy.stats import skew as scipy_skew
    from syscore.pandas.strategy_functions import drawdown

    def calc_metrics(pct_returns_series):
        """
        Calculate metrics from percentage return series (daily % returns).

        IMPORTANT: This function expects daily percentage returns from accountCurve.percent.
        It correctly sums these returns (not differences endpoints like the old broken version).

        Args:
            pct_returns_series: pd.Series of daily percentage returns
                               (from accountCurve.percent)

        Returns:
            dict with Sharpe, Ann Return %, Ann Vol %, Calmar, Skew
        """
        # For daily data (business days)
        RETURNS_PER_YEAR = 256
        VOL_SCALAR = np.sqrt(RETURNS_PER_YEAR)

        # pct_returns_series contains daily % returns
        # Sum to get total % return over period (CORRECT method)
        total_pct_return = pct_returns_series.sum()
        years = len(pct_returns_series) / RETURNS_PER_YEAR

        # Annualized mean
        ann_mean = total_pct_return / years

        # Annualized volatility
        period_std = pct_returns_series.std()
        ann_std = period_std * VOL_SCALAR

        # Sharpe
        sharpe_ratio = ann_mean / ann_std if ann_std > 0 else np.nan

        # For Calmar, need cumulative returns for drawdown calculation
        # Convert daily % returns to cumulative return series
        cumulative = (1 + pct_returns_series / 100).cumprod() - 1
        cumulative = cumulative * 100  # Back to percentage

        # Calculate drawdown
        dd = drawdown(cumulative)
        worst_dd = dd.min()
        calmar_ratio = ann_mean / -worst_dd if worst_dd < 0 else np.nan

        # Skew of daily returns
        skew_val = scipy_skew(pct_returns_series.dropna())

        return {
            'Sharpe Ratio': sharpe_ratio,
            'Ann Return (%)': ann_mean,
            'Ann Vol (%)': ann_std,
            'Calmar Ratio': calmar_ratio,
            'Skew': skew_val,
        }

    results = pd.DataFrame({
        'Static': calc_metrics(account_static_filtered),
        'Dynamic': calc_metrics(account_dynamic_filtered),
    })

    # Universe statistics (use filtered data)
    universe_stats = pd.DataFrame({
        'Static': (weights_static_filtered > 0).sum(axis=1).describe(),
        'Dynamic': universe_size_dynamic_filtered.describe()
    })

    # Print results
    print("\n" + "="*70)
    print("PERFORMANCE COMPARISON")
    print("="*70)
    print(results.round(2))

    print("\n" + "="*70)
    print("UNIVERSE SIZE STATISTICS")
    print("="*70)
    print(universe_stats.round(1))

    # Calculate improvements
    print("\n" + "="*70)
    print("IMPROVEMENT (Dynamic vs Static)")
    print("="*70)

    improvements = pd.Series({
        'Sharpe Ratio': results.loc['Sharpe Ratio', 'Dynamic'] - results.loc['Sharpe Ratio', 'Static'],
        'Ann Return (%)': results.loc['Ann Return (%)', 'Dynamic'] - results.loc['Ann Return (%)', 'Static'],
        'Ann Vol (%)': results.loc['Ann Vol (%)', 'Dynamic'] - results.loc['Ann Vol (%)', 'Static'],
        'Calmar Ratio': results.loc['Calmar Ratio', 'Dynamic'] - results.loc['Calmar Ratio', 'Static'],
    })
    print(improvements.round(2))

    # Show universe evolution over time
    print("\n" + "="*70)
    print("UNIVERSE SIZE OVER TIME (Dynamic)")
    print("="*70)

    # Sample universe size at key dates (every 6 months) - use filtered data
    sample_dates = pd.date_range(
        start=weights_dynamic_filtered.index[0],
        end=weights_dynamic_filtered.index[-1],
        freq='6MS'  # Every 6 months
    )

    for date in sample_dates:
        closest_date = universe_size_dynamic_filtered.index[
            universe_size_dynamic_filtered.index.get_indexer([date], method='nearest')[0]
        ]
        size = universe_size_dynamic_filtered.loc[closest_date]
        print(f"{closest_date.strftime('%Y-%m-%d')}: {size:3.0f} instruments")

    # Calculate position concentration (use filtered data)
    print("\n" + "="*70)
    print("POSITION CONCENTRATION")
    print("="*70)

    max_weight_static = weights_static_filtered.max(axis=1).mean()
    max_weight_dynamic = weights_dynamic_filtered.max(axis=1).mean()

    print(f"Static universe:")
    print(f"  Average max weight per instrument: {max_weight_static*100:.2f}%")
    print(f"  Expected: {100.0/12:.2f}% (equal 1/12)")

    print(f"\nDynamic universe:")
    print(f"  Average max weight per instrument: {max_weight_dynamic*100:.2f}%")
    print(f"  (varies by universe size)")

    # Summary
    print("\n" + "="*70)
    print("SUMMARY")
    print("="*70)

    if results.loc['Sharpe Ratio', 'Dynamic'] > results.loc['Sharpe Ratio', 'Static']:
        print(f"✓ Dynamic universe OUTPERFORMS static by {improvements['Sharpe Ratio']:.2f} Sharpe")
    else:
        print(f"✗ Dynamic universe UNDERPERFORMS static by {abs(improvements['Sharpe Ratio']):.2f} Sharpe")

    print(f"\nDiversification benefit:")
    print(f"  Static: 12 instruments")
    print(f"  Dynamic: {universe_size_dynamic_filtered.mean():.0f} instruments (avg), "
          f"{universe_size_dynamic_filtered.max():.0f} (max)")

    print(f"\nKey trade-offs:")
    print(f"  + Better diversification ({universe_size_dynamic_filtered.mean():.0f} vs 12 instruments)")
    print(f"  + Cost-aware selection (only trade liquid instruments)")
    print(f"  + Dynamic risk management (universe adapts to market conditions)")
    if improvements['Calmar Ratio'] > 0:
        print(f"  + Better risk-adjusted returns (Calmar: {improvements['Calmar Ratio']:.2f} improvement)")
    print(f"  - Higher complexity (walk-forward universe changes)")

    print("\n" + "="*70)

    return {
        'results': results,
        'universe_stats': universe_stats,
        'improvements': improvements,
        'system_static': system_static,
        'system_dynamic': system_dynamic,
        'weights_static': weights_static_filtered,
        'weights_dynamic': weights_dynamic_filtered,
        'start_date': start_date,
        'account_static': account_static_filtered,
        'account_dynamic': account_dynamic_filtered,
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description='Compare static vs dynamic universe backtests')
    parser.add_argument('--start-date', type=str, default=None,
                        help='Start date for metrics calculation (e.g., 2018-01-01). '
                             'If provided, metrics are only computed from this date onwards.')
    args = parser.parse_args()

    print("Starting backtest comparison...")
    if args.start_date:
        print(f"Will filter metrics from {args.start_date} onwards (to focus on stable universe period)")
    print("This may take several minutes as it processes all instruments...\n")

    comparison = run_comparison(start_date=args.start_date)

    if comparison is not None:
        print("\n✓ Comparison complete!")
        if args.start_date:
            print(f"\n✓ Metrics computed from {args.start_date} onwards")
        print("\nTo access results in Python:")
        print("  from systems.provided.crypto_example.compare_static_vs_dynamic import run_comparison")
        if args.start_date:
            print(f"  comparison = run_comparison(start_date='{args.start_date}')")
        else:
            print("  comparison = run_comparison()")
        print("  results = comparison['results']")
        print("  system_dynamic = comparison['system_dynamic']")
    else:
        print("\n✗ Comparison failed")
        sys.exit(1)
