#!/usr/bin/env python
"""
Factorial Test Analysis - OI Overlay × relcarry Attribution

Analyzes results from 2×2 factorial test to isolate:
1. OI overlay effect (B - A)
2. relcarry effect (C - A)
3. Interaction effect (D - max(B, C))
"""

import sys
from pathlib import Path
import json
import pandas as pd
import numpy as np

# Test definitions
TESTS = {
    'A': {
        'id': 'test_A_pure_baseline',
        'name': 'Pure Baseline',
        'overlay': False,
        'relcarry': False,
        'description': 'No overlay, no relcarry (vol_norm_carry only)',
    },
    'B': {
        'id': 'test_B_overlay_only',
        'name': 'Overlay Only',
        'overlay': True,
        'relcarry': False,
        'description': 'OI overlay enabled, no relcarry',
    },
    'C': {
        'id': 'test_C_relcarry_only',
        'name': 'relcarry Only',
        'overlay': False,
        'relcarry': True,
        'description': 'No overlay, relcarry 6%',
    },
    'D': {
        'id': 'test_D_combined',
        'name': 'Combined',
        'overlay': True,
        'relcarry': True,
        'description': 'OI overlay + relcarry 6%',
    },
}


def load_performance(test_dir):
    """Load performance summary from a test directory."""
    perf_file = Path(test_dir) / 'performance_summary.json'
    if not perf_file.exists():
        raise FileNotFoundError(f"Performance file not found: {perf_file}")

    with open(perf_file) as f:
        data = json.load(f)

    # Extract key metrics
    metrics = data.get('metrics', {})
    portfolio = data.get('portfolio', {})
    cost_model = data.get('cost_model', {})

    return {
        'sharpe': metrics.get('sharpe', np.nan),
        'cagr': metrics.get('cagr', np.nan),
        'ann_vol': metrics.get('ann_vol', np.nan),
        'max_dd': metrics.get('max_dd', np.nan),
        'calmar': metrics.get('calmar', np.nan),
        'crisis_return': metrics.get('crisis_return', np.nan),
        'crisis_sharpe': metrics.get('crisis_sharpe', np.nan),
        'avg_positions': portfolio.get('avg_active_positions', np.nan),
        'turnover': portfolio.get('annual_turnover', np.nan),
        'txn_cost': cost_model.get('transaction_cost_ann', np.nan),
        'funding_drag': cost_model.get('funding_drag_ann', np.nan),
    }


def calculate_acute_crash_returns(test_dir):
    """Calculate acute crash returns from positions and prices."""
    # For now, return None - will implement if needed
    # This would require loading positions and running the acute crash analysis
    return None


def format_pct(value, decimals=2):
    """Format as percentage."""
    if pd.isna(value):
        return "N/A"
    return f"{value * 100:.{decimals}f}%"


def format_delta(value, decimals=2, prefix=True):
    """Format delta with sign."""
    if pd.isna(value):
        return "N/A"
    sign = "+" if value >= 0 else ""
    if prefix:
        return f"{sign}{value * 100:.{decimals}f}%"
    else:
        return f"{value * 100:.{decimals}f}%"


def format_num(value, decimals=2):
    """Format number."""
    if pd.isna(value):
        return "N/A"
    return f"{value:.{decimals}f}"


def print_comparison_table(results):
    """Print formatted comparison table."""
    print("\n" + "=" * 100)
    print("FACTORIAL TEST RESULTS - FULL BACKTEST PERFORMANCE")
    print("=" * 100)
    print()

    # Extract baseline (Test A)
    baseline = results['A']

    # Print header
    print(f"{'Metric':<20} {'A: Baseline':<15} {'B: Overlay':<15} {'C: relcarry':<15} {'D: Combined':<15}")
    print(f"{'':20} {'(no/no)':<15} {'(yes/no)':<15} {'(no/yes)':<15} {'(yes/yes)':<15}")
    print("-" * 100)

    # Sharpe
    print(f"{'Sharpe':<20} {format_num(baseline['sharpe']):<15} " +
          f"{format_num(results['B']['sharpe']):<15} " +
          f"{format_num(results['C']['sharpe']):<15} " +
          f"{format_num(results['D']['sharpe']):<15}")

    # CAGR
    print(f"{'CAGR':<20} {format_pct(baseline['cagr']):<15} " +
          f"{format_pct(results['B']['cagr']):<15} " +
          f"{format_pct(results['C']['cagr']):<15} " +
          f"{format_pct(results['D']['cagr']):<15}")

    # Vol
    print(f"{'Annual Vol':<20} {format_pct(baseline['ann_vol']):<15} " +
          f"{format_pct(results['B']['ann_vol']):<15} " +
          f"{format_pct(results['C']['ann_vol']):<15} " +
          f"{format_pct(results['D']['ann_vol']):<15}")

    # Max DD
    print(f"{'Max DD':<20} {format_pct(baseline['max_dd']):<15} " +
          f"{format_pct(results['B']['max_dd']):<15} " +
          f"{format_pct(results['C']['max_dd']):<15} " +
          f"{format_pct(results['D']['max_dd']):<15}")

    # Calmar
    print(f"{'Calmar':<20} {format_num(baseline['calmar']):<15} " +
          f"{format_num(results['B']['calmar']):<15} " +
          f"{format_num(results['C']['calmar']):<15} " +
          f"{format_num(results['D']['calmar']):<15}")

    print("-" * 100)

    # Crisis performance
    print(f"{'Crisis Return':<20} {format_pct(baseline['crisis_return']):<15} " +
          f"{format_pct(results['B']['crisis_return']):<15} " +
          f"{format_pct(results['C']['crisis_return']):<15} " +
          f"{format_pct(results['D']['crisis_return']):<15}")

    print(f"{'Crisis Sharpe':<20} {format_num(baseline['crisis_sharpe']):<15} " +
          f"{format_num(results['B']['crisis_sharpe']):<15} " +
          f"{format_num(results['C']['crisis_sharpe']):<15} " +
          f"{format_num(results['D']['crisis_sharpe']):<15}")

    print("-" * 100)

    # Portfolio stats
    print(f"{'Avg Positions':<20} {format_num(baseline['avg_positions']):<15} " +
          f"{format_num(results['B']['avg_positions']):<15} " +
          f"{format_num(results['C']['avg_positions']):<15} " +
          f"{format_num(results['D']['avg_positions']):<15}")

    print(f"{'Turnover (x)':<20} {format_num(baseline['turnover']):<15} " +
          f"{format_num(results['B']['turnover']):<15} " +
          f"{format_num(results['C']['turnover']):<15} " +
          f"{format_num(results['D']['turnover']):<15}")

    print(f"{'Txn Cost (bps/yr)':<20} {format_num(baseline['txn_cost'] * 10000):<15} " +
          f"{format_num(results['B']['txn_cost'] * 10000):<15} " +
          f"{format_num(results['C']['txn_cost'] * 10000):<15} " +
          f"{format_num(results['D']['txn_cost'] * 10000):<15}")

    print()


def print_attribution_analysis(results):
    """Print attribution analysis (main effects and interaction)."""
    baseline = results['A']
    overlay_only = results['B']
    relcarry_only = results['C']
    combined = results['D']

    print("\n" + "=" * 100)
    print("ATTRIBUTION ANALYSIS - ISOLATING EFFECTS")
    print("=" * 100)
    print()

    # Main effect: Overlay (B - A)
    overlay_effect_sharpe = overlay_only['sharpe'] - baseline['sharpe']
    overlay_effect_cagr = overlay_only['cagr'] - baseline['cagr']
    overlay_effect_vol = overlay_only['ann_vol'] - baseline['ann_vol']

    print("1. OI OVERLAY MAIN EFFECT (B - A)")
    print("-" * 100)
    print(f"   Sharpe:      {format_delta(overlay_effect_sharpe)} " +
          f"({format_num(baseline['sharpe'])} → {format_num(overlay_only['sharpe'])})")
    print(f"   CAGR:        {format_delta(overlay_effect_cagr)} " +
          f"({format_pct(baseline['cagr'])} → {format_pct(overlay_only['cagr'])})")
    print(f"   Vol:         {format_delta(overlay_effect_vol)} " +
          f"({format_pct(baseline['ann_vol'])} → {format_pct(overlay_only['ann_vol'])})")
    print(f"   Crisis Ret:  {format_delta(overlay_only['crisis_return'] - baseline['crisis_return'])} " +
          f"({format_pct(baseline['crisis_return'])} → {format_pct(overlay_only['crisis_return'])})")
    print()

    if overlay_effect_sharpe > 0:
        verdict = "✅ OVERLAY HELPS (positive Sharpe delta)"
    elif overlay_effect_sharpe < -0.001:
        verdict = "❌ OVERLAY HURTS (negative Sharpe delta)"
    else:
        verdict = "⚠️ OVERLAY NEUTRAL (negligible Sharpe delta)"

    print(f"   Verdict: {verdict}")
    print()

    # Main effect: relcarry (C - A)
    relcarry_effect_sharpe = relcarry_only['sharpe'] - baseline['sharpe']
    relcarry_effect_cagr = relcarry_only['cagr'] - baseline['cagr']
    relcarry_effect_vol = relcarry_only['ann_vol'] - baseline['ann_vol']

    print("2. relcarry MAIN EFFECT (C - A)")
    print("-" * 100)
    print(f"   Sharpe:      {format_delta(relcarry_effect_sharpe)} " +
          f"({format_num(baseline['sharpe'])} → {format_num(relcarry_only['sharpe'])})")
    print(f"   CAGR:        {format_delta(relcarry_effect_cagr)} " +
          f"({format_pct(baseline['cagr'])} → {format_pct(relcarry_only['cagr'])})")
    print(f"   Vol:         {format_delta(relcarry_effect_vol)} " +
          f"({format_pct(baseline['ann_vol'])} → {format_pct(relcarry_only['ann_vol'])})")
    print(f"   Crisis Ret:  {format_delta(relcarry_only['crisis_return'] - baseline['crisis_return'])} " +
          f"({format_pct(baseline['crisis_return'])} → {format_pct(relcarry_only['crisis_return'])})")
    print()

    if relcarry_effect_sharpe > 0:
        verdict = "✅ relcarry HELPS (positive Sharpe delta)"
    elif relcarry_effect_sharpe < -0.001:
        verdict = "❌ relcarry HURTS (negative Sharpe delta)"
    else:
        verdict = "⚠️ relcarry NEUTRAL (negligible Sharpe delta)"

    print(f"   Verdict: {verdict}")
    print()

    # Interaction effect
    # If effects are purely additive: D should equal A + (B-A) + (C-A)
    # Actual synergy: D - [A + (B-A) + (C-A)] = D - B - C + A
    expected_combined_sharpe = baseline['sharpe'] + overlay_effect_sharpe + relcarry_effect_sharpe
    actual_combined_sharpe = combined['sharpe']
    interaction_effect_sharpe = actual_combined_sharpe - expected_combined_sharpe

    print("3. INTERACTION EFFECT (Synergy/Antagonism)")
    print("-" * 100)
    print(f"   Expected (additive):  {format_num(expected_combined_sharpe)}")
    print(f"   Actual (combined):    {format_num(actual_combined_sharpe)}")
    print(f"   Interaction:          {format_delta(interaction_effect_sharpe)}")
    print()

    if interaction_effect_sharpe > 0.005:
        verdict = "✅ POSITIVE SYNERGY (combined better than sum of parts)"
    elif interaction_effect_sharpe < -0.005:
        verdict = "❌ NEGATIVE SYNERGY (combined worse than sum of parts)"
    else:
        verdict = "⚠️ NO INTERACTION (effects are additive)"

    print(f"   Verdict: {verdict}")
    print()


def recommend_configuration(results):
    """Recommend best configuration based on results."""
    print("\n" + "=" * 100)
    print("RECOMMENDATION")
    print("=" * 100)
    print()

    # Compare Sharpe ratios
    sharpes = {k: v['sharpe'] for k, v in results.items()}
    best_config = max(sharpes, key=sharpes.get)
    best_sharpe = sharpes[best_config]

    print("Sharpe Ratio Rankings:")
    sorted_configs = sorted(sharpes.items(), key=lambda x: x[1], reverse=True)
    for i, (config_id, sharpe) in enumerate(sorted_configs, 1):
        config_info = TESTS[config_id]
        delta_vs_baseline = sharpe - sharpes['A']
        print(f"  {i}. {config_info['name']:<20} Sharpe: {format_num(sharpe):<8} " +
              f"(Δ: {format_delta(delta_vs_baseline, prefix=True)})")

    print()

    # Recommendation logic
    baseline_sharpe = sharpes['A']

    if best_config == 'A':
        print("✅ RECOMMENDATION: Keep Current Production Config (Pure Baseline)")
        print("   Reason: Baseline outperforms all alternatives")
        print("   Action: Do NOT enable OI overlay or relcarry")

    elif best_config == 'B':
        print("✅ RECOMMENDATION: Adopt OI Overlay Only")
        print("   Reason: Overlay provides benefit, relcarry does not")
        print(f"   Sharpe Improvement: {format_delta(sharpes['B'] - baseline_sharpe)}")
        print("   Action: Enable use_oi_overlay: true, keep relcarry disabled")

    elif best_config == 'C':
        print("✅ RECOMMENDATION: Adopt relcarry Only")
        print("   Reason: relcarry provides benefit, overlay does not")
        print(f"   Sharpe Improvement: {format_delta(sharpes['C'] - baseline_sharpe)}")
        print("   Action: Keep use_oi_overlay: false, enable relcarry weights (6%)")

    elif best_config == 'D':
        # Check if combined is significantly better than next best
        second_best = sorted_configs[1][0]
        delta_vs_second = sharpes['D'] - sharpes[second_best]

        if delta_vs_second > 0.01:  # >1% improvement
            print("✅ RECOMMENDATION: Adopt Combined Config (Overlay + relcarry)")
            print("   Reason: Combined provides strong synergistic benefit")
            print(f"   Sharpe Improvement: {format_delta(sharpes['D'] - baseline_sharpe)}")
            print("   Action: Enable both use_oi_overlay: true AND relcarry (6%)")
        else:
            print(f"⚠️ RECOMMENDATION: Consider {TESTS[second_best]['name']}")
            print("   Reason: Combined only marginally better than simpler alternative")
            print(f"   Sharpe: {format_num(sharpes['D'])} vs {format_num(sharpes[second_best])} " +
                  f"(Δ: {format_delta(delta_vs_second)})")
            print(f"   Suggest: Test {TESTS[second_best]['name']} first for simplicity")

    print()


def main():
    """Run factorial test analysis."""
    base_dir = Path("out/factorial_tests")

    if not base_dir.exists():
        print(f"Error: Test directory not found: {base_dir}")
        print("Run the factorial tests first: bash scripts/run_factorial_tests.sh")
        return 1

    print("\n")
    print("╔" + "=" * 98 + "╗")
    print("║" + " " * 35 + "FACTORIAL TEST ANALYSIS" + " " * 40 + "║")
    print("╚" + "=" * 98 + "╝")
    print("\n")

    # Load all test results
    print("Loading test results...")
    results = {}
    for test_id, test_info in TESTS.items():
        test_dir = base_dir / test_info['id']
        try:
            results[test_id] = load_performance(test_dir)
            print(f"  ✓ {test_info['name']:<20} loaded")
        except Exception as e:
            print(f"  ✗ {test_info['name']:<20} failed: {e}")
            return 1

    print()

    # Print comparison table
    print_comparison_table(results)

    # Print attribution analysis
    print_attribution_analysis(results)

    # Recommend configuration
    recommend_configuration(results)

    print()
    print("=" * 100)
    print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
