#!/usr/bin/env python3
"""
Compare Stage 2 universe composition between ADV-based and forecast-based selection.

Analyzes which instruments get selected under each criterion and quantifies differences.

Usage:
    python scripts/compare_stage2_universes.py \\
        --adv out/stage2_comparison/adv_baseline/universe_snapshot.json \\
        --forecast out/stage2_comparison/forecast_magnitude/universe_snapshot.json \\
        --output out/stage2_comparison/universe_comparison.json
"""

import argparse
import json
import pandas as pd
from pathlib import Path
from typing import Dict, Set
from datetime import datetime


def load_universe_snapshot(path: Path) -> Dict[str, Set[str]]:
    """Load universe snapshot JSON and convert to date -> set mapping."""
    with open(path, 'r') as f:
        data = json.load(f)

    # Convert list to set for each date
    return {date: set(instruments) for date, instruments in data.items()}


def calculate_overlap_stats(
    adv_universe: Dict[str, Set[str]],
    forecast_universe: Dict[str, Set[str]]
) -> pd.DataFrame:
    """Calculate daily overlap statistics between two universes."""

    # Ensure same dates
    common_dates = sorted(set(adv_universe.keys()) & set(forecast_universe.keys()))

    overlap_stats = []

    for date in common_dates:
        adv_set = adv_universe[date]
        forecast_set = forecast_universe[date]

        overlap = adv_set & forecast_set
        adv_only = adv_set - forecast_set
        forecast_only = forecast_set - adv_set

        overlap_pct = len(overlap) / len(adv_set) * 100 if len(adv_set) > 0 else 0

        overlap_stats.append({
            'date': date,
            'adv_count': len(adv_set),
            'forecast_count': len(forecast_set),
            'overlap_count': len(overlap),
            'overlap_pct': overlap_pct,
            'adv_only_count': len(adv_only),
            'forecast_only_count': len(forecast_only),
        })

    return pd.DataFrame(overlap_stats)


def identify_divergent_selections(
    adv_universe: Dict[str, Set[str]],
    forecast_universe: Dict[str, Set[str]],
    min_appearances: int = 100
) -> Dict[str, Dict]:
    """
    Identify instruments that frequently appear in one universe but not the other.

    Args:
        adv_universe: ADV-based universe over time
        forecast_universe: Forecast-based universe over time
        min_appearances: Minimum days an instrument must appear to be included

    Returns:
        Dict with 'adv_preferred' and 'forecast_preferred' lists
    """
    from collections import Counter

    # Count appearances in each universe
    adv_appearances = Counter()
    forecast_appearances = Counter()

    for date in adv_universe:
        for instr in adv_universe[date]:
            adv_appearances[instr] += 1

    for date in forecast_universe:
        for instr in forecast_universe[date]:
            forecast_appearances[instr] += 1

    # Find instruments that appear much more in one vs the other
    adv_preferred = []
    forecast_preferred = []

    all_instruments = set(adv_appearances.keys()) | set(forecast_appearances.keys())

    for instr in all_instruments:
        adv_count = adv_appearances.get(instr, 0)
        forecast_count = forecast_appearances.get(instr, 0)

        # Skip if neither has enough appearances
        if max(adv_count, forecast_count) < min_appearances:
            continue

        # Calculate preference ratio
        if adv_count > 0 and forecast_count > 0:
            ratio = adv_count / forecast_count
            if ratio > 2.0:  # ADV strongly preferred
                adv_preferred.append({
                    'instrument': instr,
                    'adv_days': adv_count,
                    'forecast_days': forecast_count,
                    'ratio': ratio
                })
            elif ratio < 0.5:  # Forecast strongly preferred
                forecast_preferred.append({
                    'instrument': instr,
                    'adv_days': adv_count,
                    'forecast_days': forecast_count,
                    'ratio': ratio
                })
        elif adv_count >= min_appearances:
            # Only in ADV
            adv_preferred.append({
                'instrument': instr,
                'adv_days': adv_count,
                'forecast_days': 0,
                'ratio': float('inf')
            })
        elif forecast_count >= min_appearances:
            # Only in forecast
            forecast_preferred.append({
                'instrument': instr,
                'adv_days': 0,
                'forecast_days': forecast_count,
                'ratio': 0.0
            })

    return {
        'adv_preferred': sorted(adv_preferred, key=lambda x: x.get('ratio', 0), reverse=True),
        'forecast_preferred': sorted(forecast_preferred, key=lambda x: x.get('ratio', 1))
    }


def main():
    parser = argparse.ArgumentParser(description='Compare Stage 2 universe compositions')
    parser.add_argument('--adv', required=True, help='Path to ADV-based universe_snapshot.json')
    parser.add_argument('--forecast', required=True, help='Path to forecast-based universe_snapshot.json')
    parser.add_argument('--output', required=True, help='Output path for comparison JSON')
    parser.add_argument('--min-appearances', type=int, default=100,
                        help='Minimum days for divergent instrument analysis (default: 100)')

    args = parser.parse_args()

    print(f"Loading ADV universe from {args.adv}")
    adv_universe = load_universe_snapshot(Path(args.adv))

    print(f"Loading forecast universe from {args.forecast}")
    forecast_universe = load_universe_snapshot(Path(args.forecast))

    print("\nCalculating overlap statistics...")
    overlap_df = calculate_overlap_stats(adv_universe, forecast_universe)

    print("\nIdentifying divergent selections...")
    divergent = identify_divergent_selections(
        adv_universe, forecast_universe,
        min_appearances=args.min_appearances
    )

    # Summary statistics
    summary = {
        'comparison_date': datetime.now().isoformat(),
        'total_days': len(overlap_df),
        'avg_overlap_pct': float(overlap_df['overlap_pct'].mean()),
        'min_overlap_pct': float(overlap_df['overlap_pct'].min()),
        'max_overlap_pct': float(overlap_df['overlap_pct'].max()),
        'avg_adv_count': float(overlap_df['adv_count'].mean()),
        'avg_forecast_count': float(overlap_df['forecast_count'].mean()),
        'avg_adv_only': float(overlap_df['adv_only_count'].mean()),
        'avg_forecast_only': float(overlap_df['forecast_only_count'].mean()),
        'divergent_instruments': {
            'adv_preferred_count': len(divergent['adv_preferred']),
            'forecast_preferred_count': len(divergent['forecast_preferred']),
            'adv_preferred': divergent['adv_preferred'][:10],  # Top 10
            'forecast_preferred': divergent['forecast_preferred'][:10],  # Top 10
        }
    }

    # Save results
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, 'w') as f:
        json.dump(summary, f, indent=2)

    print(f"\n✓ Comparison saved to {output_path}")

    # Print summary
    print("\n" + "="*80)
    print("UNIVERSE COMPARISON SUMMARY")
    print("="*80)
    print(f"Total days analyzed: {summary['total_days']}")
    print(f"Average overlap: {summary['avg_overlap_pct']:.1f}%")
    print(f"Overlap range: {summary['min_overlap_pct']:.1f}% - {summary['max_overlap_pct']:.1f}%")
    print(f"\nAverage universe size:")
    print(f"  ADV-based:      {summary['avg_adv_count']:.1f} instruments")
    print(f"  Forecast-based: {summary['avg_forecast_count']:.1f} instruments")
    print(f"\nAverage divergence:")
    print(f"  ADV-only:      {summary['avg_adv_only']:.1f} instruments/day")
    print(f"  Forecast-only: {summary['avg_forecast_only']:.1f} instruments/day")

    print(f"\nInstruments with strong preference:")
    print(f"  ADV-preferred:      {summary['divergent_instruments']['adv_preferred_count']} instruments")
    print(f"  Forecast-preferred: {summary['divergent_instruments']['forecast_preferred_count']} instruments")

    if summary['divergent_instruments']['adv_preferred']:
        print("\nTop ADV-preferred instruments:")
        for item in summary['divergent_instruments']['adv_preferred'][:5]:
            ratio_str = f"{item['ratio']:.1f}x" if item['ratio'] != float('inf') else 'ADV-only'
            print(f"  {item['instrument']:10s} — ADV: {item['adv_days']:4d} days, "
                  f"Forecast: {item['forecast_days']:4d} days ({ratio_str})")

    if summary['divergent_instruments']['forecast_preferred']:
        print("\nTop Forecast-preferred instruments:")
        for item in summary['divergent_instruments']['forecast_preferred'][:5]:
            ratio_str = f"{item['ratio']:.1f}x" if item['ratio'] != 0.0 else 'Forecast-only'
            print(f"  {item['instrument']:10s} — ADV: {item['adv_days']:4d} days, "
                  f"Forecast: {item['forecast_days']:4d} days ({ratio_str})")

    print("="*80)


if __name__ == '__main__':
    main()
