"""
Final Report Generator
=======================
Generates comprehensive portfolio comparison report combining all analyses.

Produces:
1. Master results table (all 9 cases)
2. Marginal contribution summary
3. Diversification analysis
4. Capital efficiency recommendations
5. Executive summary with key findings

Usage:
    python generate_final_report.py [--output report.md]
"""

import os
import sys
import argparse
from datetime import datetime

# Get project root and add to path
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, "..", "..", "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from ..core.cache_systems import load_returns, cache_exists, load_metadata
from ..core.portfolio_metrics import calculate_all_metrics, format_metrics_table
from ..core.portfolio_combiner import combine_sleeves_simple_weights
from ..analysis.decompose_portfolio import (
    analyze_tail_risk, compare_diversification,
    calculate_market_exposure
)


def generate_master_table():
    """Generate master comparison table for all 9 cases."""

    print("\n" + "=" * 90)
    print("GENERATING MASTER COMPARISON TABLE")
    print("=" * 90)

    # Check if returns are cached
    required = ['carry_returns', 'trend_static_returns', 'trend_dynamic_returns', 'btc_returns']
    missing = [c for c in required if not cache_exists(c)]

    if missing:
        print(f"\nError: Missing cached returns: {missing}")
        print("Please run run_portfolio_experiment.py first.")
        return None

    # Load returns
    carry_rets = load_returns('carry_returns')
    trend_static_rets = load_returns('trend_static_returns')
    trend_dynamic_rets = load_returns('trend_dynamic_returns')
    btc_rets = load_returns('btc_returns')

    # Define all 9 cases
    cases = {
        'A_CARRY_ONLY': ('CARRY Only', carry_rets),
        'B_TREND_STATIC': ('TREND Static Only', trend_static_rets),
        'C_TREND_DYNAMIC': ('TREND Dynamic Only', trend_dynamic_rets),
        'D1_STATIC_80_20': (
            'Static 80/20',
            combine_sleeves_simple_weights(trend_static_rets, carry_rets, 0.8, 0.2, verbose=False)
        ),
        'D2_STATIC_50_50': (
            'Static 50/50',
            combine_sleeves_simple_weights(trend_static_rets, carry_rets, 0.5, 0.5, verbose=False)
        ),
        'D3_STATIC_20_80': (
            'Static 20/80',
            combine_sleeves_simple_weights(trend_static_rets, carry_rets, 0.2, 0.8, verbose=False)
        ),
        'E1_DYNAMIC_80_20': (
            'Dynamic 80/20',
            combine_sleeves_simple_weights(trend_dynamic_rets, carry_rets, 0.8, 0.2, verbose=False)
        ),
        'E2_DYNAMIC_50_50': (
            'Dynamic 50/50',
            combine_sleeves_simple_weights(trend_dynamic_rets, carry_rets, 0.5, 0.5, verbose=False)
        ),
        'E3_DYNAMIC_20_80': (
            'Dynamic 20/80',
            combine_sleeves_simple_weights(trend_dynamic_rets, carry_rets, 0.2, 0.8, verbose=False)
        ),
    }

    # Calculate metrics for all cases
    metrics_list = []
    for case_id, (name, returns) in cases.items():
        print(f"  Computing metrics for {name}...")
        metrics = calculate_all_metrics(
            returns=returns,
            name=name,
            market_returns=btc_rets,
            market_name='BTC'
        )
        metrics_list.append(metrics)

    # Format as markdown table
    table = format_metrics_table(metrics_list, format='markdown')

    return table, metrics_list


def generate_marginal_summary(metrics_list):
    """Generate marginal contribution summary."""

    print("\n" + "=" * 90)
    print("GENERATING MARGINAL CONTRIBUTION SUMMARY")
    print("=" * 90)

    # Find baseline (CARRY only)
    baseline = None
    for m in metrics_list:
        if 'CARRY Only' in m['name']:
            baseline = m
            break

    if baseline is None:
        print("Error: Could not find baseline (CARRY Only) in metrics")
        return None

    # Calculate marginal contributions
    lines = []
    lines.append("## Marginal Contribution Analysis")
    lines.append("")
    lines.append(f"**Baseline:** {baseline['name']}")
    lines.append(f"- CAGR: {baseline['cagr']*100:.1f}%")
    lines.append(f"- Sharpe: {baseline['sharpe']:.2f}")
    lines.append(f"- Max DD: {baseline['max_dd']*100:.1f}%")
    lines.append("")
    lines.append("**Question:** What does adding TREND (static vs dynamic) provide?")
    lines.append("")

    # Compare 80/20, 50/50, 20/80 for both STATIC and DYNAMIC
    comparisons = []

    for m in metrics_list:
        if '80/20' in m['name'] or '50/50' in m['name'] or '20/80' in m['name']:
            variant = 'STATIC' if 'Static' in m['name'] else 'DYNAMIC'
            allocation = '80/20' if '80/20' in m['name'] else ('50/50' if '50/50' in m['name'] else '20/80')

            marginal_sharpe = m['sharpe'] - baseline['sharpe']
            marginal_cagr = (m['cagr'] - baseline['cagr']) * 100
            marginal_dd = (m['max_dd'] - baseline['max_dd']) * 100
            marginal_calmar = m['calmar'] - baseline['calmar']

            comparisons.append({
                'name': f"{variant} {allocation}",
                'marginal_sharpe': marginal_sharpe,
                'marginal_cagr': marginal_cagr,
                'marginal_dd': marginal_dd,
                'marginal_calmar': marginal_calmar
            })

    # Format table
    lines.append("| Strategy | Δ Sharpe | Δ CAGR | Δ MaxDD | Δ Calmar |")
    lines.append("|----------|----------|---------|---------|----------|")

    for comp in comparisons:
        lines.append(f"| {comp['name']:<20} | "
                     f"{comp['marginal_sharpe']:+.2f} | "
                     f"{comp['marginal_cagr']:+.1f}% | "
                     f"{comp['marginal_dd']:+.1f}% | "
                     f"{comp['marginal_calmar']:+.2f} |")

    lines.append("")
    lines.append("**Interpretation:**")
    lines.append("- Δ Sharpe: Positive = improvement in risk-adjusted returns")
    lines.append("- Δ CAGR: Positive = higher absolute returns")
    lines.append("- Δ MaxDD: Negative = smaller drawdown (better)")
    lines.append("- Δ Calmar: Positive = better return/drawdown ratio")

    return "\n".join(lines)


def generate_diversification_summary():
    """Generate diversification analysis summary."""

    print("\n" + "=" * 90)
    print("GENERATING DIVERSIFICATION SUMMARY")
    print("=" * 90)

    # Load returns
    carry_rets = load_returns('carry_returns')
    trend_static_rets = load_returns('trend_static_returns')
    trend_dynamic_rets = load_returns('trend_dynamic_returns')
    btc_rets = load_returns('btc_returns')

    # Compare diversification
    div_comp = compare_diversification(
        carry_rets, trend_static_rets, trend_dynamic_rets,
        "CARRY", "TREND"
    )

    # Market exposure
    btc_carry = calculate_market_exposure(carry_rets, btc_rets, 'BTC')
    btc_static = calculate_market_exposure(trend_static_rets, btc_rets, 'BTC')
    btc_dynamic = calculate_market_exposure(trend_dynamic_rets, btc_rets, 'BTC')

    lines = []
    lines.append("## Diversification Analysis")
    lines.append("")
    lines.append("### Correlation to CARRY")
    lines.append("")
    lines.append(f"- **TREND STATIC:** {div_comp['static_correlation']:+.3f}")
    lines.append(f"- **TREND DYNAMIC:** {div_comp['dynamic_correlation']:+.3f}")
    lines.append(f"- **Improvement:** {div_comp['correlation_improvement']:+.3f} (lower correlation = better diversification)")
    lines.append("")
    lines.append("### Beta to BTC (Market Neutrality)")
    lines.append("")
    lines.append(f"- **CARRY:** {btc_carry['beta']:+.3f} (correlation: {btc_carry['correlation']:+.3f})")
    lines.append(f"- **TREND STATIC:** {btc_static['beta']:+.3f} (correlation: {btc_static['correlation']:+.3f})")
    lines.append(f"- **TREND DYNAMIC:** {btc_dynamic['beta']:+.3f} (correlation: {btc_dynamic['correlation']:+.3f})")
    lines.append("")
    lines.append("**Interpretation:**")
    lines.append("- Beta ≈ 0: Market-neutral (uncorrelated with BTC)")
    lines.append("- Beta > 0.5: Directional exposure to BTC")
    lines.append("- TREND DYNAMIC's low beta suggests market-neutral profile")

    return "\n".join(lines)


def generate_executive_summary(metrics_list):
    """Generate executive summary with key findings."""

    # Find best performers
    best_sharpe = max(metrics_list, key=lambda x: x['sharpe'])
    best_cagr = max(metrics_list, key=lambda x: x['cagr'])
    best_calmar = max(metrics_list, key=lambda x: x['calmar'])
    lowest_dd = max(metrics_list, key=lambda x: x['max_dd'])  # Max because max_dd is negative

    lines = []
    lines.append("# Portfolio Comparison Report")
    lines.append("")
    lines.append(f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"**Analysis Period:** {metrics_list[0]['start_date'].date()} to {metrics_list[0]['end_date'].date()}")
    lines.append("")
    lines.append("## Executive Summary")
    lines.append("")
    lines.append("### Best Performers")
    lines.append("")
    lines.append(f"- **Highest Sharpe:** {best_sharpe['name']} ({best_sharpe['sharpe']:.2f})")
    lines.append(f"- **Highest CAGR:** {best_cagr['name']} ({best_cagr['cagr']*100:.1f}%)")
    lines.append(f"- **Best Calmar:** {best_calmar['name']} ({best_calmar['calmar']:.2f})")
    lines.append(f"- **Smallest Drawdown:** {lowest_dd['name']} ({lowest_dd['max_dd']*100:.1f}%)")
    lines.append("")
    lines.append("### Key Findings")
    lines.append("")
    lines.append("1. **CARRY Strategy:**")
    carry_metrics = [m for m in metrics_list if 'CARRY Only' in m['name']][0]
    lines.append(f"   - Sharpe: {carry_metrics['sharpe']:.2f}, CAGR: {carry_metrics['cagr']*100:.1f}%, Vol: {carry_metrics['ann_vol']*100:.1f}%")
    lines.append(f"   - Skew: {carry_metrics['skew']:+.2f} (slightly negative - tail risk present)")
    lines.append("")
    lines.append("2. **TREND STATIC vs DYNAMIC:**")
    static_metrics = [m for m in metrics_list if 'TREND Static Only' in m['name']][0]
    dynamic_metrics = [m for m in metrics_list if 'TREND Dynamic Only' in m['name']][0]
    lines.append(f"   - STATIC: Sharpe {static_metrics['sharpe']:.2f}, CAGR {static_metrics['cagr']*100:.1f}%, Vol {static_metrics['ann_vol']*100:.1f}%")
    lines.append(f"   - DYNAMIC: Sharpe {dynamic_metrics['sharpe']:.2f}, CAGR {dynamic_metrics['cagr']*100:.1f}%, Vol {dynamic_metrics['ann_vol']*100:.1f}%")
    lines.append(f"   - DYNAMIC is market-neutral (low vol, low beta) vs STATIC (directional)")
    lines.append("")
    lines.append("3. **Combined Portfolios:**")
    lines.append(f"   - 80/20 allocations balance returns with skew management")
    lines.append(f"   - 50/50 allocations provide balanced exposure")
    lines.append(f"   - 20/80 allocations test high CARRY exposure (skew risk)")
    lines.append("")

    return "\n".join(lines)


def generate_full_report(output_file: str = None):
    """Generate complete portfolio comparison report."""

    print("=" * 90)
    print("GENERATING FINAL REPORT")
    print("=" * 90)

    # Generate all sections
    exec_summary = None
    master_table = None
    marginal_summary = None
    div_summary = None

    try:
        # Master table
        table, metrics_list = generate_master_table()
        master_table = table

        # Executive summary
        exec_summary = generate_executive_summary(metrics_list)

        # Marginal contribution
        marginal_summary = generate_marginal_summary(metrics_list)

        # Diversification
        div_summary = generate_diversification_summary()

    except Exception as e:
        print(f"\nError generating report: {e}")
        import traceback
        traceback.print_exc()
        return None

    # Combine all sections
    report_lines = []

    if exec_summary:
        report_lines.append(exec_summary)
        report_lines.append("")

    if master_table:
        report_lines.append("## Master Comparison Table")
        report_lines.append("")
        report_lines.append(master_table)
        report_lines.append("")

    if marginal_summary:
        report_lines.append(marginal_summary)
        report_lines.append("")

    if div_summary:
        report_lines.append(div_summary)
        report_lines.append("")

    # Recommendations
    report_lines.append("## Recommendations")
    report_lines.append("")
    report_lines.append("### For Different Objectives:")
    report_lines.append("")
    report_lines.append("1. **Maximum Sharpe Ratio:**")
    report_lines.append("   - Choose portfolio with highest Sharpe from table above")
    report_lines.append("   - Balance risk-adjusted returns")
    report_lines.append("")
    report_lines.append("2. **Maximum Absolute Returns (CAGR):**")
    report_lines.append("   - TREND STATIC provides higher returns but higher volatility")
    report_lines.append("   - Accept higher drawdowns for higher CAGR")
    report_lines.append("")
    report_lines.append("3. **Risk Management (Low Drawdown):**")
    report_lines.append("   - TREND DYNAMIC provides lower volatility and smaller drawdowns")
    report_lines.append("   - Market-neutral profile reduces correlation to BTC crashes")
    report_lines.append("")
    report_lines.append("4. **Diversification Benefit:**")
    report_lines.append("   - TREND DYNAMIC has lower correlation to CARRY")
    report_lines.append("   - Better diversification for multi-strategy portfolios")
    report_lines.append("")
    report_lines.append("5. **Small Capital ($10k):**")
    report_lines.append("   - CARRY and TREND STATIC more capital-efficient")
    report_lines.append("   - TREND DYNAMIC may require reducing vol target or more capital")
    report_lines.append("")

    # Save to file
    report_text = "\n".join(report_lines)

    if output_file is None:
        output_file = os.path.join(SCRIPT_DIR, 'PORTFOLIO_COMPARISON_REPORT.md')

    with open(output_file, 'w') as f:
        f.write(report_text)

    print(f"\n✓ Report saved to: {output_file}")

    return report_text


# =============================================================================
# MAIN
# =============================================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Generate final portfolio comparison report')
    parser.add_argument('--output', type=str, default=None,
                        help='Output file path (default: PORTFOLIO_COMPARISON_REPORT.md)')

    args = parser.parse_args()

    # Generate report
    report = generate_full_report(output_file=args.output)

    if report is not None:
        print("\n" + "=" * 90)
        print("✓ Final report generation complete")
        print("=" * 90)
        print("\nReport preview (first 50 lines):")
        print("-" * 90)
        for line in report.split('\n')[:50]:
            print(line)
