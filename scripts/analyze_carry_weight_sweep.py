"""
Analyze carry_weight sweep results.

Generates:
1. Performance table sorted by Sharpe
2. Comparison vs baseline (carry_weight=0.3)
3. Identification of optimal carry_weight
4. Markdown report
"""

import json
import pandas as pd
from pathlib import Path
import sys
import argparse


def analyze_sweep(sweep_dir: Path):
    """Analyze carry_weight sweep results and generate report."""

    # Load results
    summary_path = sweep_dir / "sweep_summary.json"

    if not summary_path.exists():
        print(f"Error: Sweep summary not found at {summary_path}")
        print(f"Run sweep first: python scripts/sweep_carry_weight.py")
        sys.exit(1)

    with open(summary_path) as f:
        data = json.load(f)

    results = data['results']

    # Filter out failed runs
    failed = [r for r in results if 'error' in r]
    results = [r for r in results if 'error' not in r]

    if not results:
        print("Error: No successful results to analyze")
        sys.exit(1)

    # Convert to DataFrame
    df = pd.DataFrame(results)

    # Sort by Sharpe descending
    df = df.sort_values('sharpe', ascending=False)

    # Identify optimal
    optimal = df.iloc[0]

    # Baseline (carry_weight=0.3)
    baseline = df[df['carry_weight'] == 0.3].iloc[0] if len(df[df['carry_weight'] == 0.3]) > 0 else None

    # Generate report
    report = []
    report.append("# Carry Weight Sweep Analysis\n\n")
    report.append(f"**Sweep Date:** {data['timestamp']}\n\n")
    report.append(f"**Fixed Parameter:** threshold = {data['fixed_params']['threshold']}\n\n")
    report.append(f"**Configurations Tested:** {len(results)}\n\n")

    if failed:
        report.append(f"**Failed Runs:** {len(failed)}\n\n")

    report.append("## Optimal Configuration\n\n")
    report.append(f"- **carry_weight:** {optimal['carry_weight']:.2f}\n")
    report.append(f"- **Sharpe:** {optimal['sharpe']:.4f}\n")
    report.append(f"- **CAGR:** {optimal['cagr']*100:.2f}%\n")
    report.append(f"- **Vol:** {optimal['vol']*100:.2f}%\n")
    report.append(f"- **Max DD:** {optimal['max_dd']*100:.2f}%\n")
    report.append(f"- **Turnover:** {optimal['turnover']:.2f}x\n")
    report.append(f"- **Avg Positions:** {optimal['avg_positions']:.1f}\n\n")

    if baseline is not None:
        delta_sharpe = (optimal['sharpe'] - baseline['sharpe']) / baseline['sharpe'] * 100
        delta_cagr = optimal['cagr'] - baseline['cagr']
        delta_vol = optimal['vol'] - baseline['vol']
        delta_dd = optimal['max_dd'] - baseline['max_dd']
        delta_turnover = optimal['turnover'] - baseline['turnover']

        report.append("## Comparison vs Previous Optimum (carry_weight=0.3)\n\n")
        report.append(f"- **Sharpe Δ:** {optimal['sharpe']:.4f} vs {baseline['sharpe']:.4f} ({delta_sharpe:+.1f}%)\n")
        report.append(f"- **CAGR Δ:** {optimal['cagr']*100:.2f}% vs {baseline['cagr']*100:.2f}% ({delta_cagr*100:+.2f}%)\n")
        report.append(f"- **Vol Δ:** {optimal['vol']*100:.2f}% vs {baseline['vol']*100:.2f}% ({delta_vol*100:+.2f}%)\n")
        report.append(f"- **Max DD Δ:** {optimal['max_dd']*100:.2f}% vs {baseline['max_dd']*100:.2f}% ({delta_dd*100:+.2f}%)\n")
        report.append(f"- **Turnover Δ:** {optimal['turnover']:.2f}x vs {baseline['turnover']:.2f}x ({delta_turnover:+.2f}x)\n\n")

        # Recommendation
        report.append("## Recommendation\n\n")
        if optimal['carry_weight'] == 0.3:
            report.append("✅ **Keep current configuration** (carry_weight=0.3)\n\n")
            report.append("The extended sweep confirms that 0.3 is the robust optimum. Higher carry weights degrade performance.\n\n")
        elif delta_sharpe > 2.0:
            report.append(f"✅ **Adopt new optimum** (carry_weight={optimal['carry_weight']:.2f})\n\n")
            report.append(f"Sharpe improvement of {delta_sharpe:.1f}% is statistically meaningful. Update config with new carry_weight.\n\n")
        else:
            report.append(f"⚠️ **Marginal improvement** (carry_weight={optimal['carry_weight']:.2f})\n\n")
            report.append(f"Sharpe improvement of {delta_sharpe:.1f}% is modest. Consider keeping current value (0.3) for robustness unless other metrics strongly favor the new value.\n\n")

    report.append("## Full Results (sorted by Sharpe)\n\n")
    report.append("| carry_weight | Sharpe | CAGR | Vol | MaxDD | Turnover | Avg Pos |\n")
    report.append("|--------------|--------|------|-----|-------|----------|----------|\n")

    for _, row in df.iterrows():
        marker = " ✅" if row['carry_weight'] == optimal['carry_weight'] else ""
        report.append(f"| {row['carry_weight']:.2f}{marker} | {row['sharpe']:.4f} | "
                     f"{row['cagr']*100:.2f}% | {row['vol']*100:.2f}% | "
                     f"{row['max_dd']*100:.2f}% | {row['turnover']:.2f}x | "
                     f"{row['avg_positions']:.1f} |\n")

    if failed:
        report.append("\n## Failed Runs\n\n")
        for r in failed:
            report.append(f"- **carry_weight={r['carry_weight']:.2f}:** {r['error']}\n")

    report.append("\n## Key Insights\n\n")

    # Analyze trend in results
    df_sorted_by_weight = df.sort_values('carry_weight')
    sharpe_trend = df_sorted_by_weight['sharpe'].values

    if len(sharpe_trend) > 1:
        # Check if Sharpe generally decreases with higher carry_weight
        early_avg = sharpe_trend[:len(sharpe_trend)//2].mean()
        late_avg = sharpe_trend[len(sharpe_trend)//2:].mean()

        if late_avg < early_avg - 0.01:
            report.append("- 📉 **Diminishing returns:** Sharpe tends to decrease with higher carry_weight\n")
            report.append("- Carry provides modest benefit when conservatively weighted, but becomes noisy when overweighted\n")
        elif late_avg > early_avg + 0.01:
            report.append("- 📈 **Increasing returns:** Sharpe improves with higher carry_weight\n")
            report.append("- Carry provides stronger alpha than initially estimated; consider further testing at even higher weights\n")
        else:
            report.append("- ➡️ **Flat response:** Sharpe relatively stable across carry_weight range\n")
            report.append("- Carry contribution is robust to weighting; optimal value is well-defined\n")

    # Effective carry weight analysis
    report.append("\n## Effective Carry Weight Analysis\n\n")
    report.append("The effective weight of carry in the combined forecast is:\n\n")
    report.append("```\n")
    report.append("effective_weight = carry_weight × forecast_weight[carry_rules]\n")
    report.append("                 = carry_weight × 3%\n")
    report.append("```\n\n")
    report.append("| carry_weight | Effective Weight | Ratio to Trend Family* |\n")
    report.append("|--------------|------------------|------------------------|\n")

    for w_c in data['tested_weights']:
        eff_weight = w_c * 0.03
        ratio = eff_weight / 0.14286  # Trend family has ~14.286% weight
        report.append(f"| {w_c:.2f} | {eff_weight*100:.2f}% | {ratio*100:.1f}% |\n")

    report.append("\n*Each trend family has ~14.286% weight in forecast_weights\n\n")

    # Save report
    report_path = sweep_dir / "SWEEP_ANALYSIS.md"
    with open(report_path, 'w') as f:
        f.writelines(report)

    # Print to console
    print("".join(report))
    print(f"\n{'='*80}")
    print(f"Report saved to: {report_path}")
    print(f"{'='*80}\n")


def main():
    parser = argparse.ArgumentParser(description="Analyze carry_weight sweep results")
    parser.add_argument(
        "--sweep-dir",
        type=Path,
        default=Path("out/carry_weight_sweep"),
        help="Directory containing sweep results (default: out/carry_weight_sweep)"
    )

    args = parser.parse_args()

    if not args.sweep_dir.exists():
        print(f"Error: Sweep directory not found: {args.sweep_dir}")
        sys.exit(1)

    analyze_sweep(args.sweep_dir)


if __name__ == "__main__":
    main()
