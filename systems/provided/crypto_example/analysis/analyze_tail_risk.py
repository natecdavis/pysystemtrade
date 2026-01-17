"""
Tail Risk Analysis for TREND + CARRY Portfolios
================================================

Generate focused tail risk comparison using Expected Shortfall (CVaR)
and drawdown duration metrics instead of noisy skew-based analysis.

This script loads cached backtest returns and computes robust tail risk
metrics to answer: Which CARRY allocation minimizes tail risk?

Usage:
    python analyze_tail_risk.py

Output:
    - Console report with tail risk metrics table
    - TAIL_RISK_ANALYSIS.md documentation file
"""

import pandas as pd
import numpy as np
from pathlib import Path
from ..core.portfolio_metrics import calculate_all_metrics, format_metrics_table


def load_cached_returns():
    """
    Load cached backtest returns from run_portfolio_experiment.py.

    Returns:
        dict: {portfolio_name: pd.Series of daily returns}
    """
    cache_path = Path(__file__).parent / "backtest_cache"

    if not cache_path.exists():
        raise FileNotFoundError(
            f"Cache directory not found: {cache_path}\n"
            "Please run run_portfolio_experiment.py first to generate cached returns."
        )

    returns_dict = {}

    # Expected portfolio names (9 portfolios)
    portfolio_names = [
        'A_CARRY_ONLY',
        'B_TREND_STATIC',
        'C_TREND_DYNAMIC',
        'D1_STATIC_80_20',
        'D2_STATIC_50_50',
        'D3_STATIC_20_80',
        'E1_DYNAMIC_80_20',
        'E2_DYNAMIC_50_50',
        'E3_DYNAMIC_20_80'
    ]

    for name in portfolio_names:
        returns_file = cache_path / f"{name}_returns.csv"

        if returns_file.exists():
            # Load returns (should have 'date' and 'return' columns)
            df = pd.read_csv(returns_file, parse_dates=['date'], index_col='date')
            returns_dict[name] = df['return']
        else:
            print(f"Warning: Missing cached returns for {name}")

    if len(returns_dict) == 0:
        raise FileNotFoundError(
            "No cached returns found. Please run run_portfolio_experiment.py first."
        )

    return returns_dict


def calculate_tail_risk_metrics(returns_dict: dict) -> list:
    """
    Calculate tail risk metrics for all portfolios.

    Args:
        returns_dict: {portfolio_name: pd.Series of daily returns}

    Returns:
        list of metrics dicts
    """
    metrics_list = []

    for name, returns in returns_dict.items():
        print(f"Calculating tail risk for {name}...")

        # Calculate all metrics including new tail risk measures
        metrics = calculate_all_metrics(returns, name=name)
        metrics_list.append(metrics)

    return metrics_list


def generate_tail_risk_report(metrics_list: list) -> str:
    """
    Generate a focused tail risk analysis report.

    Args:
        metrics_list: List of metrics dicts

    Returns:
        str: Markdown report
    """
    report = []

    report.append("# TAIL RISK ANALYSIS: TREND + CARRY PORTFOLIOS")
    report.append("=" * 80)
    report.append("")
    report.append("## Methodology")
    report.append("")
    report.append("This analysis uses robust tail risk metrics instead of skew:")
    report.append("")
    report.append("- **Expected Shortfall (ES95)**: Mean of worst 5% of daily returns")
    report.append("- **Expected Shortfall (ES99)**: Mean of worst 1% of daily returns")
    report.append("- **Max DD Duration**: Longest period (days) from peak to recovery")
    report.append("- **Worst Month**: Worst 30-day compounded return (NOT sum)")
    report.append("")
    report.append("**Why ES instead of skew?**")
    report.append("- Skew is noisy in crypto (high kurtosis)")
    report.append("- Skew doesn't combine linearly across portfolios")
    report.append("- ES directly measures tail loss severity")
    report.append("")

    report.append("## Summary Table")
    report.append("")

    # Create focused tail risk table
    tail_columns = [
        ('name', 'Case'),
        ('cagr', 'CAGR'),
        ('sharpe', 'Sharpe'),
        ('max_dd', 'MaxDD'),
        ('max_dd_duration', 'DD Days'),
        ('es95', 'ES95'),
        ('es99', 'ES99'),
        ('worst_month', 'Worst Mo'),
    ]

    # Build markdown table manually with focused columns
    report.append("| " + " | ".join([col[1] for col in tail_columns]) + " |")
    report.append("| " + " | ".join(['---'] * len(tail_columns)) + " |")

    for m in metrics_list:
        row_vals = []
        for key, _ in tail_columns:
            val = m.get(key, '')
            if isinstance(val, float):
                if key == 'sharpe':
                    row_vals.append(f"{val:.2f}")
                elif key in ['es95', 'es99', 'worst_month', 'max_dd', 'cagr']:
                    row_vals.append(f"{val*100:.1f}%")
                else:
                    row_vals.append(f"{val:.2f}")
            elif key == 'max_dd_duration':
                row_vals.append(f"{int(val)}")
            else:
                row_vals.append(str(val))
        report.append("| " + " | ".join(row_vals) + " |")

    report.append("")

    # Key findings section
    report.append("## Key Findings")
    report.append("")

    # Find best/worst portfolios by different tail metrics
    carry_only = next(m for m in metrics_list if m['name'] == 'A_CARRY_ONLY')
    trend_static = next(m for m in metrics_list if m['name'] == 'B_TREND_STATIC')
    trend_dynamic = next(m for m in metrics_list if m['name'] == 'C_TREND_DYNAMIC')

    # Static portfolios
    static_80_20 = next(m for m in metrics_list if m['name'] == 'D1_STATIC_80_20')
    static_50_50 = next(m for m in metrics_list if m['name'] == 'D2_STATIC_50_50')
    static_20_80 = next(m for m in metrics_list if m['name'] == 'D3_STATIC_20_80')

    # Dynamic portfolios
    dynamic_80_20 = next(m for m in metrics_list if m['name'] == 'E1_DYNAMIC_80_20')
    dynamic_50_50 = next(m for m in metrics_list if m['name'] == 'E2_DYNAMIC_50_50')
    dynamic_20_80 = next(m for m in metrics_list if m['name'] == 'E3_DYNAMIC_20_80')

    report.append("### 1. Expected Shortfall Analysis (ES95)")
    report.append("")
    report.append(f"- **CARRY Only**: {carry_only['es95']*100:.2f}% (baseline tail risk)")
    report.append(f"- **TREND Static**: {trend_static['es95']*100:.2f}%")
    report.append(f"- **TREND Dynamic**: {trend_dynamic['es95']*100:.2f}%")
    report.append("")
    report.append("**Static Combinations (TREND/CARRY):**")
    report.append(f"- 80/20: {static_80_20['es95']*100:.2f}%")
    report.append(f"- 50/50: {static_50_50['es95']*100:.2f}%")
    report.append(f"- 20/80: {static_20_80['es95']*100:.2f}%")
    report.append("")
    report.append("**Dynamic Combinations (TREND/CARRY):**")
    report.append(f"- 80/20: {dynamic_80_20['es95']*100:.2f}%")
    report.append(f"- 50/50: {dynamic_50_50['es95']*100:.2f}%")
    report.append(f"- 20/80: {dynamic_20_80['es95']*100:.2f}%")
    report.append("")

    # Compare ES95 improvements
    carry_es95 = carry_only['es95']

    # Best tail protection
    best_es95 = min(metrics_list, key=lambda m: m['es95'])
    worst_es95 = max(metrics_list, key=lambda m: m['es95'])

    report.append(f"**Best tail protection**: {best_es95['name']} with ES95 = {best_es95['es95']*100:.2f}%")
    report.append(f"**Worst tail protection**: {worst_es95['name']} with ES95 = {worst_es95['es95']*100:.2f}%")
    report.append("")

    report.append("### 2. Drawdown Duration Analysis")
    report.append("")
    report.append(f"- **CARRY Only**: {carry_only['max_dd_duration']} days")
    report.append(f"- **TREND Static**: {trend_static['max_dd_duration']} days")
    report.append(f"- **TREND Dynamic**: {trend_dynamic['max_dd_duration']} days")
    report.append("")
    report.append("**Static Combinations:**")
    report.append(f"- 80/20: {static_80_20['max_dd_duration']} days")
    report.append(f"- 50/50: {static_50_50['max_dd_duration']} days")
    report.append(f"- 20/80: {static_20_80['max_dd_duration']} days")
    report.append("")
    report.append("**Dynamic Combinations:**")
    report.append(f"- 80/20: {dynamic_80_20['max_dd_duration']} days")
    report.append(f"- 50/50: {dynamic_50_50['max_dd_duration']} days")
    report.append(f"- 20/80: {dynamic_20_80['max_dd_duration']} days")
    report.append("")

    # Best/worst DD duration
    best_dd_duration = min(metrics_list, key=lambda m: m['max_dd_duration'])
    worst_dd_duration = max(metrics_list, key=lambda m: m['max_dd_duration'])

    report.append(f"**Fastest recovery**: {best_dd_duration['name']} with {best_dd_duration['max_dd_duration']} days")
    report.append(f"**Slowest recovery**: {worst_dd_duration['name']} with {worst_dd_duration['max_dd_duration']} days")
    report.append("")

    report.append("### 3. Extreme Tail Events (ES99)")
    report.append("")
    report.append(f"- **CARRY Only**: {carry_only['es99']*100:.2f}% (worst 1% of days)")
    report.append("")
    report.append("**Static vs Dynamic comparison at 50/50 allocation:**")
    report.append(f"- Static 50/50: {static_50_50['es99']*100:.2f}%")
    report.append(f"- Dynamic 50/50: {dynamic_50_50['es99']*100:.2f}%")

    if dynamic_50_50['es99'] > static_50_50['es99']:
        improvement = (dynamic_50_50['es99'] / static_50_50['es99'] - 1) * 100
        report.append(f"- Dynamic is {improvement:.1f}% better in extreme tail")
    else:
        deterioration = (static_50_50['es99'] / dynamic_50_50['es99'] - 1) * 100
        report.append(f"- Static is {deterioration:.1f}% better in extreme tail")

    report.append("")

    # Recommendations
    report.append("## Recommendations Based on Tail Risk")
    report.append("")

    # Find best Sharpe with acceptable tail risk
    sorted_by_sharpe = sorted(metrics_list, key=lambda m: m['sharpe'], reverse=True)

    report.append("### Portfolio Selection by Risk Tolerance")
    report.append("")
    report.append("**Conservative (minimize tail losses):**")
    report.append(f"- Choose: {best_es95['name']}")
    report.append(f"- ES95: {best_es95['es95']*100:.2f}%, Sharpe: {best_es95['sharpe']:.2f}, MaxDD Duration: {best_es95['max_dd_duration']} days")
    report.append("")

    report.append("**Balanced (optimize Sharpe with moderate tail risk):**")
    # Find portfolio with best Sharpe among those with ES95 better than median
    median_es95 = np.median([m['es95'] for m in metrics_list])
    balanced = sorted([m for m in metrics_list if m['es95'] <= median_es95],
                     key=lambda m: m['sharpe'], reverse=True)[0]
    report.append(f"- Choose: {balanced['name']}")
    report.append(f"- ES95: {balanced['es95']*100:.2f}%, Sharpe: {balanced['sharpe']:.2f}, MaxDD Duration: {balanced['max_dd_duration']} days")
    report.append("")

    report.append("**Aggressive (maximize Sharpe, accept tail risk):**")
    best_sharpe = sorted_by_sharpe[0]
    report.append(f"- Choose: {best_sharpe['name']}")
    report.append(f"- ES95: {best_sharpe['es95']*100:.2f}%, Sharpe: {best_sharpe['sharpe']:.2f}, MaxDD Duration: {best_sharpe['max_dd_duration']} days")
    report.append("")

    # Static vs Dynamic comparison
    report.append("### Static vs Dynamic Universe")
    report.append("")

    # Calculate average metrics for static and dynamic portfolios
    static_portfolios = [static_80_20, static_50_50, static_20_80]
    dynamic_portfolios = [dynamic_80_20, dynamic_50_50, dynamic_20_80]

    avg_static_es95 = np.mean([p['es95'] for p in static_portfolios])
    avg_dynamic_es95 = np.mean([p['es95'] for p in dynamic_portfolios])

    avg_static_dd_duration = np.mean([p['max_dd_duration'] for p in static_portfolios])
    avg_dynamic_dd_duration = np.mean([p['max_dd_duration'] for p in dynamic_portfolios])

    report.append(f"**Average ES95 (across 80/20, 50/50, 20/80 allocations):**")
    report.append(f"- Static: {avg_static_es95*100:.2f}%")
    report.append(f"- Dynamic: {avg_dynamic_es95*100:.2f}%")

    if avg_dynamic_es95 > avg_static_es95:
        report.append(f"- **Dynamic provides {(avg_dynamic_es95/avg_static_es95 - 1)*100:.1f}% better tail protection**")
    else:
        report.append(f"- **Static provides {(avg_static_es95/avg_dynamic_es95 - 1)*100:.1f}% better tail protection**")

    report.append("")
    report.append(f"**Average Max DD Duration:**")
    report.append(f"- Static: {avg_static_dd_duration:.0f} days")
    report.append(f"- Dynamic: {avg_dynamic_dd_duration:.0f} days")

    if avg_dynamic_dd_duration < avg_static_dd_duration:
        report.append(f"- **Dynamic recovers {(1 - avg_dynamic_dd_duration/avg_static_dd_duration)*100:.1f}% faster**")
    else:
        report.append(f"- **Static recovers {(1 - avg_static_dd_duration/avg_dynamic_dd_duration)*100:.1f}% faster**")

    report.append("")

    # Final summary
    report.append("## Summary")
    report.append("")
    report.append("**Key Takeaways:**")
    report.append("")
    report.append("1. **Expected Shortfall (ES95/ES99) is a more robust tail risk measure than skew**")
    report.append("   - Directly measures average severity of tail losses")
    report.append("   - Less noisy than skew in high-kurtosis crypto returns")
    report.append("")
    report.append("2. **CARRY allocation creates tail risk trade-offs:**")

    # Determine trend based on analysis
    if static_20_80['es95'] < static_80_20['es95']:
        report.append("   - Higher CARRY allocation (80%) → worse tail protection")
        report.append("   - Lower CARRY allocation (20%) → better tail protection")
    else:
        report.append("   - Higher CARRY allocation (80%) → better tail protection")
        report.append("   - Lower CARRY allocation (20%) → worse tail protection")

    report.append("")
    report.append("3. **Dynamic universe vs Static:**")

    if avg_dynamic_es95 > avg_static_es95:
        report.append("   - Dynamic provides better tail protection (lower ES95/ES99)")
    else:
        report.append("   - Static provides better tail protection (lower ES95/ES99)")

    if avg_dynamic_dd_duration < avg_static_dd_duration:
        report.append("   - Dynamic recovers from drawdowns faster")
    else:
        report.append("   - Static recovers from drawdowns faster")

    report.append("")
    report.append("---")
    report.append(f"*Analysis generated using {len(metrics_list)} portfolios*")

    return '\n'.join(report)


def main():
    """Main execution function."""
    print("=" * 80)
    print("TAIL RISK ANALYSIS: TREND + CARRY PORTFOLIOS")
    print("=" * 80)
    print()

    # Load cached returns
    print("Loading cached backtest returns...")
    returns_dict = load_cached_returns()
    print(f"✓ Loaded {len(returns_dict)} portfolios")
    print()

    # Calculate tail risk metrics
    print("Calculating tail risk metrics...")
    metrics_list = calculate_tail_risk_metrics(returns_dict)
    print(f"✓ Calculated metrics for {len(metrics_list)} portfolios")
    print()

    # Generate report
    print("Generating tail risk analysis report...")
    report = generate_tail_risk_report(metrics_list)

    # Save report to file
    output_file = Path(__file__).parent / "TAIL_RISK_ANALYSIS.md"
    with open(output_file, 'w') as f:
        f.write(report)

    print(f"✓ Report saved to: {output_file}")
    print()

    # Display report
    print("=" * 80)
    print(report)
    print("=" * 80)
    print()
    print("✓ Tail risk analysis complete")


if __name__ == "__main__":
    main()
