#!/usr/bin/env python
"""
Factorial Test Acute Crash Analysis

Analyzes performance of all 4 factorial test configurations during specific
3-7 day acute crash events to determine real crash protection.
"""

import sys
from pathlib import Path
import json
import pandas as pd
import numpy as np

# Crash events (same as original acute crash analysis)
CRASH_EVENTS = {
    'May 2021 China Ban': {
        'start': '2021-05-19',
        'end': '2021-05-21',
        'description': 'China mining ban + leverage unwind',
        'expected_btc_drop': -30,
        'duration_days': 3,
    },
    'June 2022 3AC/Celsius': {
        'start': '2022-06-13',
        'end': '2022-06-18',
        'description': '3AC, Celsius, Luna fallout',
        'expected_btc_drop': -40,
        'duration_days': 6,
    },
    'Nov 2022 FTX Collapse': {
        'start': '2022-11-08',
        'end': '2022-11-10',
        'description': 'FTX insolvency, systemic shock',
        'expected_btc_drop': -24,
        'duration_days': 3,
    },
}

# Factorial test configurations
TESTS = {
    'A': {
        'id': 'test_A_pure_baseline',
        'name': 'Pure Baseline',
        'overlay': False,
        'relcarry': False,
    },
    'B': {
        'id': 'test_B_overlay_only',
        'name': 'Overlay Only',
        'overlay': True,
        'relcarry': False,
    },
    'C': {
        'id': 'test_C_relcarry_only',
        'name': 'relcarry Only',
        'overlay': False,
        'relcarry': True,
    },
    'D': {
        'id': 'test_D_combined',
        'name': 'Combined',
        'overlay': True,
        'relcarry': True,
    },
}


def calculate_returns_from_positions(positions: pd.DataFrame, price_data_path: str, capital: float = 10000.0):
    """Calculate daily returns from positions and price data."""
    price_df = pd.read_parquet(price_data_path)

    # Extract prices
    if 'close' in price_df.columns:
        prices = price_df.pivot(index='date', columns='instrument', values='close')
    else:
        prices = price_df

    # Align positions and prices
    common_dates = positions.index.intersection(prices.index)
    common_instruments = positions.columns.intersection(prices.columns)

    pos = positions.loc[common_dates, common_instruments]
    price = prices.loc[common_dates, common_instruments]

    # Calculate daily P&L: PnL[t] = position[t-1] * (price[t] - price[t-1])
    price_changes = price.diff()
    daily_pnl = (pos.shift(1) * price_changes).sum(axis=1)

    # Convert to returns
    daily_returns = daily_pnl / capital

    return daily_returns


def load_test_results(test_dir, price_data_path):
    """Load positions and calculate returns for a test."""
    positions_path = Path(test_dir) / 'positions.csv'
    if not positions_path.exists():
        raise FileNotFoundError(f"Positions file not found: {positions_path}")

    positions = pd.read_csv(positions_path, index_col=0, parse_dates=True)
    returns = calculate_returns_from_positions(positions, price_data_path)

    return {
        'positions': positions,
        'returns': returns,
    }


def extract_event_returns(returns_series, start_date, end_date):
    """Extract cumulative returns during an event window."""
    event_returns = returns_series.loc[start_date:end_date]

    if len(event_returns) == 0:
        return {
            'cumulative_return': np.nan,
            'max_drawdown': np.nan,
            'worst_day': np.nan,
            'n_days': 0,
        }

    # Calculate metrics
    cumulative_return = (1 + event_returns).prod() - 1
    cumulative_curve = (1 + event_returns).cumprod()
    running_max = cumulative_curve.expanding().max()
    drawdown_curve = (cumulative_curve / running_max) - 1
    max_drawdown = drawdown_curve.min()

    return {
        'cumulative_return': cumulative_return,
        'max_drawdown': max_drawdown,
        'worst_day': event_returns.min(),
        'n_days': len(event_returns),
    }


def analyze_event(event_name, event_config, test_results):
    """Analyze a single crash event across all configurations."""
    start_date = event_config['start']
    end_date = event_config['end']

    print(f"\n{'=' * 100}")
    print(f"Event: {event_name}")
    print(f"{'=' * 100}")
    print(f"Date Range: {start_date} to {end_date} ({event_config['duration_days']} days)")
    print(f"Description: {event_config['description']}")
    print(f"Expected BTC Drop: {event_config['expected_btc_drop']}%")
    print()

    results = {}

    for test_id in ['A', 'B', 'C', 'D']:
        test_info = TESTS[test_id]
        data = test_results[test_id]

        # Extract returns
        returns_data = extract_event_returns(data['returns'], start_date, end_date)

        results[test_id] = returns_data

        print(f"{test_info['name']:<20} Return: {returns_data['cumulative_return']:>7.2%}  " +
              f"MaxDD: {returns_data['max_drawdown']:>7.2%}  " +
              f"Worst Day: {returns_data['worst_day']:>7.2%}")

    print()

    return results


def compare_event_results(event_name, results):
    """Compare results and determine winners."""
    print(f"{'=' * 100}")
    print(f"Attribution Analysis: {event_name}")
    print(f"{'=' * 100}")
    print()

    # Extract values
    baseline_ret = results['A']['cumulative_return']
    overlay_ret = results['B']['cumulative_return']
    relcarry_ret = results['C']['cumulative_return']
    combined_ret = results['D']['cumulative_return']

    # Calculate effects
    overlay_effect = overlay_ret - baseline_ret
    relcarry_effect = relcarry_ret - baseline_ret

    print("Main Effects:")
    print(f"  Overlay effect (B - A):   {overlay_effect:>+7.2%}")
    print(f"  relcarry effect (C - A):  {relcarry_effect:>+7.2%}")
    print(f"  Combined (D):             {combined_ret - baseline_ret:>+7.2%} vs baseline")
    print()

    # Determine best
    all_returns = {
        'A: Baseline': baseline_ret,
        'B: Overlay': overlay_ret,
        'C: relcarry': relcarry_ret,
        'D: Combined': combined_ret,
    }

    best = max(all_returns, key=all_returns.get)
    print(f"Best Configuration: {best} ({all_returns[best]:.2%})")
    print()

    return {
        'overlay_effect': overlay_effect,
        'relcarry_effect': relcarry_effect,
        'best_config': best,
    }


def print_summary(all_event_results):
    """Print overall summary across all events."""
    print("\n" + "=" * 100)
    print("SUMMARY: ACUTE CRASH PERFORMANCE")
    print("=" * 100)
    print()

    # Average effects
    avg_overlay = np.mean([r['overlay_effect'] for r in all_event_results.values()])
    avg_relcarry = np.mean([r['relcarry_effect'] for r in all_event_results.values()])

    print(f"Average Overlay Effect (B - A):   {avg_overlay:>+7.2%}")
    print(f"Average relcarry Effect (C - A):  {avg_relcarry:>+7.2%}")
    print()

    # Count wins
    overlay_wins = sum(1 for r in all_event_results.values() if 'B:' in r['best_config'])
    relcarry_wins = sum(1 for r in all_event_results.values() if 'C:' in r['best_config'])
    combined_wins = sum(1 for r in all_event_results.values() if 'D:' in r['best_config'])
    baseline_wins = sum(1 for r in all_event_results.values() if 'A:' in r['best_config'])

    print(f"Win Count (best return in each event):")
    print(f"  Baseline (A):    {baseline_wins} / 3")
    print(f"  Overlay (B):     {overlay_wins} / 3")
    print(f"  relcarry (C):    {relcarry_wins} / 3")
    print(f"  Combined (D):    {combined_wins} / 3")
    print()

    # Verdict
    print("=" * 100)
    print("VERDICT")
    print("=" * 100)
    print()

    if avg_overlay > 0:
        print(f"✅ OVERLAY HELPS in acute crashes: {avg_overlay:+.2%} average improvement")
    else:
        print(f"❌ OVERLAY HURTS in acute crashes: {avg_overlay:+.2%} average decline")

    if avg_relcarry > 0:
        print(f"✅ relcarry HELPS in acute crashes: {avg_relcarry:+.2%} average improvement")
    else:
        print(f"❌ relcarry HURTS in acute crashes: {avg_relcarry:+.2%} average decline")

    print()

    # Recommendation
    if overlay_wins >= 2 and avg_overlay > 0:
        print("✅ RECOMMENDATION: Overlay provides real crash protection")
    elif overlay_wins < 2 or avg_overlay < 0:
        print("❌ WARNING: Overlay does NOT provide consistent crash protection")

    print()

    return {
        'avg_overlay_effect': avg_overlay,
        'avg_relcarry_effect': avg_relcarry,
        'overlay_wins': overlay_wins,
        'relcarry_wins': relcarry_wins,
    }


def main():
    """Run factorial acute crash analysis."""
    print("\n")
    print("╔" + "=" * 98 + "╗")
    print("║" + " " * 30 + "FACTORIAL ACUTE CRASH ANALYSIS" + " " * 38 + "║")
    print("╚" + "=" * 98 + "╝")
    print("\n")

    base_dir = Path("out/factorial_tests")
    price_data = "data/dataset_538registry_6yr_jagged.parquet"

    # Load all test results
    print("Loading test results...")
    test_results = {}
    for test_id, test_info in TESTS.items():
        test_dir = base_dir / test_info['id']
        try:
            test_results[test_id] = load_test_results(test_dir, price_data)
            print(f"  ✓ {test_info['name']:<20} loaded")
        except Exception as e:
            print(f"  ✗ {test_info['name']:<20} failed: {e}")
            return 1

    print()

    # Analyze each crash event
    all_event_results = {}
    all_comparisons = {}

    for event_name, event_config in CRASH_EVENTS.items():
        try:
            results = analyze_event(event_name, event_config, test_results)
            all_event_results[event_name] = results

            comparison = compare_event_results(event_name, results)
            all_comparisons[event_name] = comparison

        except Exception as e:
            print(f"✗ Failed to analyze {event_name}: {e}")
            import traceback
            traceback.print_exc()
            continue

    # Print summary
    summary = print_summary(all_comparisons)

    # Save results
    output_file = base_dir / "factorial_acute_crash_analysis.json"
    output_data = {
        'events': all_event_results,
        'comparisons': all_comparisons,
        'summary': summary,
    }

    # Convert numpy types to native Python for JSON serialization
    def convert_numpy(obj):
        if isinstance(obj, np.integer):
            return int(obj)
        elif isinstance(obj, np.floating):
            return float(obj)
        elif isinstance(obj, np.ndarray):
            return obj.tolist()
        elif isinstance(obj, dict):
            return {k: convert_numpy(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [convert_numpy(item) for item in obj]
        return obj

    output_data = convert_numpy(output_data)

    with open(output_file, 'w') as f:
        json.dump(output_data, f, indent=2)

    print(f"✓ Results saved to: {output_file}")
    print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
