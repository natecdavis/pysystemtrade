#!/usr/bin/env python
"""
Acute Crash Event Analysis - Phase 1/1.5 OI Overlay Evaluation

Analyzes performance during specific 3-7 day crash events to determine:
1. Did standard overlay protect or hurt during acute crashes?
2. Did trend-aware overlay improve upon standard?
3. Should we adopt any overlay, or stay with baseline?

Crash Events:
- May 19-21, 2021: China mining ban (-30% crash)
- June 13-18, 2022: 3AC/Celsius liquidations (-40% crash)
- Nov 8-10, 2022: FTX collapse (-24% crash)
"""

import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
import numpy as np
import json
from datetime import datetime, timedelta


# Define crash events (date ranges)
CRASH_EVENTS = {
    'May 2021 China Ban': {
        'start': '2021-05-19',
        'end': '2021-05-21',
        'description': 'China mining ban + leverage unwind',
        'expected_btc_drop': -30,  # %
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


def load_backtest_results(outdir: str, price_data_path: str = None):
    """Load positions and diagnostics from a backtest run."""
    base_path = Path(outdir)

    # Load positions CSV
    positions_path = base_path / 'positions.csv'
    if not positions_path.exists():
        raise FileNotFoundError(f"Positions file not found: {positions_path}")

    positions = pd.read_csv(positions_path, index_col=0, parse_dates=True)

    # Load diagnostics parquet
    diag_path = base_path / 'diagnostics.parquet'
    if not diag_path.exists():
        raise FileNotFoundError(f"Diagnostics file not found: {diag_path}")

    diagnostics = pd.read_parquet(diag_path)

    # Load metadata JSON
    meta_path = base_path / 'metadata.json'
    if meta_path.exists():
        with open(meta_path) as f:
            metadata = json.load(f)
    else:
        metadata = {}

    # Calculate daily returns from positions and prices
    if price_data_path:
        returns = calculate_returns_from_positions(positions, price_data_path)
    else:
        # Try to use default path
        default_price_path = "data/dataset_538registry_6yr_jagged.parquet"
        if Path(default_price_path).exists():
            returns = calculate_returns_from_positions(positions, default_price_path)
        else:
            returns = pd.Series(dtype=float)  # Empty series

    return {
        'positions': positions,
        'diagnostics': diagnostics,
        'metadata': metadata,
        'returns': returns,
    }


def calculate_returns_from_positions(positions: pd.DataFrame, price_data_path: str, capital: float = 10000.0):
    """Calculate daily returns from positions and price data."""
    # Load price data
    price_df = pd.read_parquet(price_data_path)

    # Extract prices (assuming 'close' column, or use the price columns in the dataset)
    if 'close' in price_df.columns:
        prices = price_df.pivot(index='date', columns='instrument', values='close')
    else:
        # Dataset might be already pivoted
        prices = price_df

    # Align positions and prices
    common_dates = positions.index.intersection(prices.index)
    common_instruments = positions.columns.intersection(prices.columns)

    pos = positions.loc[common_dates, common_instruments]
    price = prices.loc[common_dates, common_instruments]

    # Calculate daily P&L
    # PnL[t] = position[t-1] * (price[t] - price[t-1])
    price_changes = price.diff()
    daily_pnl = (pos.shift(1) * price_changes).sum(axis=1)

    # Convert to returns (fraction of capital)
    daily_returns = daily_pnl / capital

    return daily_returns


def extract_event_returns(returns_series, start_date, end_date):
    """Extract cumulative returns during an event window."""
    # Filter to event window
    event_returns = returns_series.loc[start_date:end_date]

    if len(event_returns) == 0:
        return {
            'cumulative_return': np.nan,
            'daily_returns': [],
            'max_drawdown': np.nan,
            'volatility': np.nan,
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
        'daily_returns': event_returns.tolist(),
        'max_drawdown': max_drawdown,
        'volatility': event_returns.std() * np.sqrt(252),  # Annualized
        'worst_day': event_returns.min(),
        'n_days': len(event_returns),
    }


def extract_position_changes(positions, start_date, end_date):
    """Extract position changes during an event window."""
    # Get positions before, during, and after event
    start_dt = pd.to_datetime(start_date)
    end_dt = pd.to_datetime(end_date)

    # Get position 1 day before event
    pre_date = start_dt - timedelta(days=1)
    pre_positions = positions.loc[positions.index <= pre_date].iloc[-1] if len(positions.loc[positions.index <= pre_date]) > 0 else None

    # Get position during event (last day)
    event_positions = positions.loc[start_date:end_date]
    during_position = event_positions.iloc[-1] if len(event_positions) > 0 else None

    # Get position 1 day after event
    post_date = end_dt + timedelta(days=1)
    post_positions = positions.loc[positions.index >= post_date]
    post_position = post_positions.iloc[0] if len(post_positions) > 0 else None

    if pre_positions is None or during_position is None:
        return {
            'avg_position_before': np.nan,
            'avg_position_during': np.nan,
            'avg_position_after': np.nan,
            'position_change_pct': np.nan,
            'n_positions_reduced': 0,
        }

    # Calculate aggregate metrics
    pre_total = pre_positions.abs().sum()
    during_total = during_position.abs().sum()
    post_total = post_position.abs().sum() if post_position is not None else np.nan

    # Count how many positions were reduced
    position_changes = during_position - pre_positions
    n_reduced = (position_changes.abs() < -0.01).sum()  # Reduced by more than 0.01 units

    position_change_pct = (during_total - pre_total) / pre_total if pre_total > 0 else np.nan

    return {
        'avg_position_before': pre_total,
        'avg_position_during': during_total,
        'avg_position_after': post_total,
        'position_change_pct': position_change_pct,
        'n_positions_reduced': int(n_reduced),
    }


def analyze_event(event_name, event_config, baseline, standard, trend_aware):
    """Analyze a single crash event across all three modes."""
    start_date = event_config['start']
    end_date = event_config['end']

    print(f"\n{'=' * 80}")
    print(f"Event: {event_name}")
    print(f"{'=' * 80}")
    print(f"Date Range: {start_date} to {end_date} ({event_config['duration_days']} days)")
    print(f"Description: {event_config['description']}")
    print(f"Expected BTC Drop: {event_config['expected_btc_drop']}%")
    print()

    results = {}

    for mode_name, data in [
        ('Baseline', baseline),
        ('Standard', standard),
        ('Trend-Aware', trend_aware),
    ]:
        print(f"Analyzing {mode_name}...")

        # Extract returns
        returns_data = extract_event_returns(data['returns'], start_date, end_date)

        # Extract position changes
        position_data = extract_position_changes(data['positions'], start_date, end_date)

        results[mode_name] = {
            **returns_data,
            **position_data,
        }

        print(f"  Cumulative Return: {returns_data['cumulative_return']:.2%}")
        print(f"  Max Drawdown: {returns_data['max_drawdown']:.2%}")
        print(f"  Worst Day: {returns_data['worst_day']:.2%}")
        print(f"  Position Change: {position_data['position_change_pct']:.1%}")
        print()

    return results


def compare_results(event_name, results):
    """Compare results across the three modes and determine winner."""
    baseline = results['Baseline']
    standard = results['Standard']
    trend_aware = results['Trend-Aware']

    print(f"\n{'=' * 80}")
    print(f"Comparison: {event_name}")
    print(f"{'=' * 80}")
    print()

    # Cumulative returns comparison
    print("Cumulative Returns:")
    baseline_ret = baseline['cumulative_return']
    standard_ret = standard['cumulative_return']
    trend_ret = trend_aware['cumulative_return']

    print(f"  Baseline:      {baseline_ret:>7.2%}")
    print(f"  Standard:      {standard_ret:>7.2%}  (Δ: {(standard_ret - baseline_ret):>+6.2%})")
    print(f"  Trend-Aware:   {trend_ret:>7.2%}  (Δ: {(trend_ret - baseline_ret):>+6.2%})")

    # Determine winner
    best_ret = max(baseline_ret, standard_ret, trend_ret)
    if best_ret == baseline_ret:
        winner_ret = "Baseline"
    elif best_ret == standard_ret:
        winner_ret = "Standard"
    else:
        winner_ret = "Trend-Aware"
    print(f"  Winner: {winner_ret} ✅")
    print()

    # Max drawdown comparison
    print("Max Drawdown:")
    baseline_dd = baseline['max_drawdown']
    standard_dd = standard['max_drawdown']
    trend_dd = trend_aware['max_drawdown']

    print(f"  Baseline:      {baseline_dd:>7.2%}")
    print(f"  Standard:      {standard_dd:>7.2%}  (Δ: {(standard_dd - baseline_dd):>+6.2%})")
    print(f"  Trend-Aware:   {trend_dd:>7.2%}  (Δ: {(trend_dd - baseline_dd):>+6.2%})")

    # Shallower is better (less negative)
    best_dd = max(baseline_dd, standard_dd, trend_dd)
    if best_dd == baseline_dd:
        winner_dd = "Baseline"
    elif best_dd == standard_dd:
        winner_dd = "Standard"
    else:
        winner_dd = "Trend-Aware"
    print(f"  Winner: {winner_dd} ✅")
    print()

    # Position changes
    print("Position Changes During Event:")
    baseline_pos = baseline['position_change_pct']
    standard_pos = standard['position_change_pct']
    trend_pos = trend_aware['position_change_pct']

    print(f"  Baseline:      {baseline_pos:>7.1%}  (N reduced: {baseline['n_positions_reduced']})")
    print(f"  Standard:      {standard_pos:>7.1%}  (N reduced: {standard['n_positions_reduced']})")
    print(f"  Trend-Aware:   {trend_pos:>7.1%}  (N reduced: {trend_aware['n_positions_reduced']})")
    print()

    # Overall verdict
    print("Verdict:")
    if winner_ret == winner_dd == 'Baseline':
        verdict = "❌ OVERLAYS HURT - Both overlays underperformed baseline"
    elif winner_ret == 'Standard' and winner_dd == 'Standard':
        verdict = "✅ STANDARD HELPED - Standard overlay outperformed both"
    elif winner_ret == 'Trend-Aware' and winner_dd == 'Trend-Aware':
        verdict = "✅ TREND-AWARE HELPED - Trend-aware overlay outperformed both"
    elif winner_ret == 'Baseline' or winner_dd == 'Baseline':
        verdict = "⚠️ MIXED RESULTS - At least one overlay underperformed baseline"
    else:
        verdict = "⚠️ MIXED RESULTS - Different winners for return vs drawdown"

    print(f"  {verdict}")
    print()

    return {
        'winner_return': winner_ret,
        'winner_drawdown': winner_dd,
        'verdict': verdict,
        'baseline_return': baseline_ret,
        'standard_return': standard_ret,
        'trend_aware_return': trend_ret,
        'baseline_dd': baseline_dd,
        'standard_dd': standard_dd,
        'trend_aware_dd': trend_dd,
    }


def generate_summary(all_results):
    """Generate overall summary across all events."""
    print("\n" + "=" * 80)
    print("OVERALL SUMMARY")
    print("=" * 80)
    print()

    # Count wins
    baseline_wins = 0
    standard_wins = 0
    trend_wins = 0

    for event_name, comparison in all_results.items():
        if comparison['winner_return'] == 'Baseline':
            baseline_wins += 1
        elif comparison['winner_return'] == 'Standard':
            standard_wins += 1
        else:
            trend_wins += 1

    print(f"Win Count (by cumulative return):")
    print(f"  Baseline:      {baseline_wins} / {len(all_results)}")
    print(f"  Standard:      {standard_wins} / {len(all_results)}")
    print(f"  Trend-Aware:   {trend_wins} / {len(all_results)}")
    print()

    # Average performance deltas
    avg_standard_delta = np.mean([
        r['standard_return'] - r['baseline_return']
        for r in all_results.values()
    ])
    avg_trend_delta = np.mean([
        r['trend_aware_return'] - r['baseline_return']
        for r in all_results.values()
    ])

    print(f"Average Return Delta vs Baseline:")
    print(f"  Standard:      {avg_standard_delta:+.2%}")
    print(f"  Trend-Aware:   {avg_trend_delta:+.2%}")
    print()

    # Final recommendation
    print("=" * 80)
    print("FINAL RECOMMENDATION")
    print("=" * 80)
    print()

    if standard_wins == len(all_results):
        recommendation = "✅ ADOPT STANDARD OVERLAY"
        reasoning = "Standard overlay won in ALL acute crash events"
    elif baseline_wins == len(all_results):
        recommendation = "❌ ABANDON ALL OVERLAYS"
        reasoning = "Baseline won in ALL acute crash events - overlays consistently hurt"
    elif trend_wins == len(all_results):
        recommendation = "✅ ADOPT TREND-AWARE OVERLAY"
        reasoning = "Trend-aware overlay won in ALL acute crash events"
    elif standard_wins > baseline_wins and standard_wins > trend_wins:
        recommendation = "⚠️ CONSIDER STANDARD OVERLAY"
        reasoning = f"Standard won {standard_wins}/{len(all_results)} events, but not unanimous"
    elif avg_standard_delta > 0 and avg_standard_delta > avg_trend_delta:
        recommendation = "⚠️ LEAN TOWARD STANDARD OVERLAY"
        reasoning = f"Standard has positive average delta (+{avg_standard_delta:.2%})"
    elif avg_standard_delta < 0:
        recommendation = "❌ ABANDON OVERLAYS"
        reasoning = f"Standard has negative average delta ({avg_standard_delta:.2%}) - hurts more than helps"
    else:
        recommendation = "⚠️ UNCLEAR - NEED MORE ANALYSIS"
        reasoning = "Results are mixed, no clear winner"

    print(f"Recommendation: {recommendation}")
    print(f"Reasoning: {reasoning}")
    print()

    # Context from full backtest
    print("Context (Full 6-Year Backtest):")
    print("  Baseline Sharpe:      0.9879")
    print("  Standard Sharpe:      0.9933  (+0.55%)")
    print("  Trend-Aware Sharpe:   0.9850  (-0.29%)")
    print()
    print("Interpretation:")
    if avg_standard_delta > 0:
        print("  ✅ Standard overlay helped in acute crashes AND full backtest")
        print("     The +0.55% Sharpe is legitimate (includes crash protection)")
    else:
        print("  ⚠️ Standard overlay hurt in acute crashes BUT helped in full backtest")
        print("     The +0.55% Sharpe came from volatility management, NOT crash protection")
        print("     Trade-off: Accept crash underperformance for overall Sharpe gain?")
    print()

    return {
        'recommendation': recommendation,
        'reasoning': reasoning,
        'baseline_wins': baseline_wins,
        'standard_wins': standard_wins,
        'trend_aware_wins': trend_wins,
        'avg_standard_delta': avg_standard_delta,
        'avg_trend_delta': avg_trend_delta,
    }


def main():
    """Run acute crash analysis."""
    print("\n")
    print("╔" + "=" * 78 + "╗")
    print("║" + " " * 22 + "ACUTE CRASH EVENT ANALYSIS" + " " * 30 + "║")
    print("╚" + "=" * 78 + "╝")
    print("\n")

    # Load backtest results
    print("Loading backtest results...")

    base_dir = Path("out/oi_trend_aware")

    try:
        baseline = load_backtest_results(base_dir / "baseline")
        print("  ✓ Baseline loaded")
    except Exception as e:
        print(f"  ✗ Failed to load baseline: {e}")
        return 1

    try:
        standard = load_backtest_results(base_dir / "standard")
        print("  ✓ Standard overlay loaded")
    except Exception as e:
        print(f"  ✗ Failed to load standard: {e}")
        return 1

    try:
        trend_aware = load_backtest_results(base_dir / "trend_aware")
        print("  ✓ Trend-aware overlay loaded")
    except Exception as e:
        print(f"  ✗ Failed to load trend-aware: {e}")
        return 1

    print()

    # Analyze each crash event
    all_results = {}
    all_comparisons = {}

    for event_name, event_config in CRASH_EVENTS.items():
        try:
            results = analyze_event(
                event_name,
                event_config,
                baseline,
                standard,
                trend_aware
            )
            all_results[event_name] = results

            comparison = compare_results(event_name, results)
            all_comparisons[event_name] = comparison

        except Exception as e:
            print(f"✗ Failed to analyze {event_name}: {e}")
            import traceback
            traceback.print_exc()
            continue

    # Generate overall summary
    if all_comparisons:
        summary = generate_summary(all_comparisons)

        # Save results to JSON
        output_file = base_dir / "acute_crash_analysis.json"
        output_data = {
            'events': all_comparisons,
            'summary': summary,
            'timestamp': datetime.now().isoformat(),
        }

        with open(output_file, 'w') as f:
            json.dump(output_data, f, indent=2)

        print(f"✓ Results saved to: {output_file}")
        print()

        return 0
    else:
        print("✗ No events were successfully analyzed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
