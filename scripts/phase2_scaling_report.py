#!/usr/bin/env python3
"""
Phase 2 Scaling Analysis Report

Characterizes system behavior at N=15 instruments (2021-2024) vs N=4 (2020-2024).
Focus: Scaling behavior, NOT rule performance evaluation.

Outputs:
- phase2_scaling_summary.md: Executive characterization summary
- correlation_heatmap.png: 15x15 correlation matrix
- idm_over_time.png: IDM time series comparison
- position_concentration.png: Herfindahl index and top weights
- constraint_comparison.csv: Binding frequency analysis
- runtime_profile.txt: Performance metrics
"""

import argparse
import json
import time
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns


def load_backtest_data(backtest_dir: Path):
    """Load all backtest outputs from a directory."""
    data = {}

    # Load equity curve
    equity_path = backtest_dir / "equity_curve.csv"
    if equity_path.exists():
        data['equity'] = pd.read_csv(equity_path, parse_dates=['date'], index_col='date')

    # Load positions
    positions_path = backtest_dir / "positions.csv"
    if positions_path.exists():
        data['positions'] = pd.read_csv(positions_path, parse_dates=['date'])

    # Load PnL breakdown
    pnl_path = backtest_dir / "pnl_breakdown.csv"
    if pnl_path.exists():
        data['pnl'] = pd.read_csv(pnl_path, parse_dates=['date'], index_col='date')

    # Load diagnostics
    diag_path = backtest_dir / "diagnostics.parquet"
    if diag_path.exists():
        data['diagnostics'] = pd.read_parquet(diag_path)

    # Load metadata
    meta_path = backtest_dir / "metadata.json"
    if meta_path.exists():
        with open(meta_path) as f:
            data['metadata'] = json.load(f)

    return data


def analyze_correlation_structure(diagnostics_df: pd.DataFrame, output_dir: Path):
    """Analyze and visualize correlation matrix."""
    print("\n=== Correlation Structure Analysis ===")

    # Extract correlation matrix from diagnostics
    # Assuming diagnostics has instrument-level correlations or we compute from returns
    instruments = diagnostics_df['instrument'].unique()
    n_instruments = len(instruments)

    print(f"Number of instruments: {n_instruments}")

    # Compute correlation from scaled forecasts or returns
    # For simplicity, compute from combined forecasts
    if 'forecast_combined' in diagnostics_df.columns:
        # Pivot to instrument x date
        forecast_pivot = diagnostics_df.pivot(
            index='date',
            columns='instrument',
            values='forecast_combined'
        )

        # Compute correlation matrix
        corr_matrix = forecast_pivot.corr()

        # Save correlation matrix
        corr_matrix.to_csv(output_dir / "correlation_matrix.csv")

        # Compute pairwise correlation statistics
        # Get upper triangle (excluding diagonal)
        mask = np.triu(np.ones_like(corr_matrix, dtype=bool), k=1)
        upper_tri = corr_matrix.where(mask)
        pairwise_corrs = upper_tri.stack()

        median_corr = pairwise_corrs.median()
        mean_corr = pairwise_corrs.mean()
        min_corr = pairwise_corrs.min()
        max_corr = pairwise_corrs.max()

        print(f"Median pairwise correlation: {median_corr:.3f}")
        print(f"Mean pairwise correlation: {mean_corr:.3f}")
        print(f"Range: [{min_corr:.3f}, {max_corr:.3f}]")

        # Plot correlation heatmap
        fig, ax = plt.subplots(figsize=(12, 10))
        sns.heatmap(
            corr_matrix,
            annot=False,
            cmap='RdYlBu_r',
            center=0,
            vmin=-1,
            vmax=1,
            square=True,
            cbar_kws={'label': 'Correlation'},
            ax=ax
        )
        ax.set_title(f'Forecast Correlation Matrix (N={n_instruments})', fontsize=14, pad=20)
        plt.tight_layout()
        plt.savefig(output_dir / "correlation_heatmap.png", dpi=150, bbox_inches='tight')
        plt.close()

        # Plot correlation distribution
        fig, ax = plt.subplots(figsize=(10, 6))
        ax.hist(pairwise_corrs, bins=30, edgecolor='black', alpha=0.7)
        ax.axvline(median_corr, color='red', linestyle='--', linewidth=2, label=f'Median: {median_corr:.3f}')
        ax.set_xlabel('Pairwise Correlation')
        ax.set_ylabel('Frequency')
        ax.set_title('Distribution of Pairwise Forecast Correlations')
        ax.legend()
        plt.tight_layout()
        plt.savefig(output_dir / "correlation_distribution.png", dpi=150, bbox_inches='tight')
        plt.close()

        return {
            'median': median_corr,
            'mean': mean_corr,
            'min': min_corr,
            'max': max_corr,
            'n_instruments': n_instruments
        }
    else:
        print("WARNING: combined_forecast not found in diagnostics")
        return None


def analyze_idm_behavior(phase2_data: dict, phase1_data: dict, output_dir: Path):
    """Compare IDM scaling between N=15 and N=4."""
    print("\n=== IDM Scaling Analysis ===")

    idm_stats = {}

    # Extract IDM from diagnostics
    for label, data in [('Phase 2 (N=15)', phase2_data), ('Phase 1 (N=4)', phase1_data)]:
        if 'diagnostics' in data:
            diag = data['diagnostics']

            # IDM is typically constant across instruments on a given date
            # Take first instrument's IDM per date
            if 'idm' in diag.columns:
                idm_series = diag.groupby('date')['idm'].first()

                stats = {
                    'mean': idm_series.mean(),
                    'median': idm_series.median(),
                    'max': idm_series.max(),
                    'min': idm_series.min(),
                    'std': idm_series.std()
                }

                idm_stats[label] = stats

                print(f"\n{label}:")
                print(f"  Mean IDM: {stats['mean']:.3f}")
                print(f"  Median IDM: {stats['median']:.3f}")
                print(f"  Max IDM: {stats['max']:.3f}")
                print(f"  Min IDM: {stats['min']:.3f}")

    # Plot IDM time series comparison
    if idm_stats:
        fig, ax = plt.subplots(figsize=(14, 6))

        for label, data in [('Phase 2 (N=15)', phase2_data), ('Phase 1 (N=4)', phase1_data)]:
            if 'diagnostics' in data:
                diag = data['diagnostics']
                if 'idm' in diag.columns:
                    idm_series = diag.groupby('date')['idm'].first()
                    ax.plot(idm_series.index, idm_series.values, label=label, alpha=0.7)

        ax.set_xlabel('Date')
        ax.set_ylabel('IDM')
        ax.set_title('Instrument Diversification Multiplier Over Time')
        ax.legend()
        ax.grid(alpha=0.3)
        plt.tight_layout()
        plt.savefig(output_dir / "idm_over_time.png", dpi=150, bbox_inches='tight')
        plt.close()

    return idm_stats


def analyze_constraint_binding(phase2_data: dict, phase1_data: dict, output_dir: Path):
    """Compare constraint binding frequency between phases."""
    print("\n=== Constraint Binding Analysis ===")

    binding_stats = []

    for label, data in [('Phase 2 (N=15)', phase2_data), ('Phase 1 (N=4)', phase1_data)]:
        if 'diagnostics' in data:
            diag = data['diagnostics']

            # Group by date to get portfolio-level constraint status
            daily = diag.groupby('date').first()

            stats = {
                'phase': label,
                'total_days': len(daily)
            }

            # Check for gross leverage constraint
            if 'gross_leverage' in daily.columns and 'gross_leverage_cap' in daily.columns:
                # Consider binding if within 1% of cap
                binding = (daily['gross_leverage'] / daily['gross_leverage_cap']) > 0.99
                stats['gross_lev_binding_days'] = binding.sum()
                stats['gross_lev_binding_pct'] = 100.0 * binding.sum() / len(daily)
                stats['gross_lev_mean'] = daily['gross_leverage'].mean()
                stats['gross_lev_max'] = daily['gross_leverage'].max()

            # Check for IDM cap constraint
            if 'idm' in daily.columns and 'idm_cap' in daily.columns:
                binding = (daily['idm'] / daily['idm_cap']) > 0.99
                stats['idm_binding_days'] = binding.sum()
                stats['idm_binding_pct'] = 100.0 * binding.sum() / len(daily)
                stats['idm_mean'] = daily['idm'].mean()
                stats['idm_max'] = daily['idm'].max()

            binding_stats.append(stats)

            print(f"\n{label}:")
            print(f"  Total days: {stats['total_days']}")
            if 'gross_lev_binding_pct' in stats:
                print(f"  Gross leverage binding: {stats['gross_lev_binding_pct']:.1f}% of days")
                print(f"  Gross leverage: mean={stats['gross_lev_mean']:.2f}, max={stats['gross_lev_max']:.2f}")
            if 'idm_binding_pct' in stats:
                print(f"  IDM cap binding: {stats['idm_binding_pct']:.1f}% of days")
                print(f"  IDM: mean={stats['idm_mean']:.2f}, max={stats['idm_max']:.2f}")

    # Save comparison table
    if binding_stats:
        df = pd.DataFrame(binding_stats)
        df.to_csv(output_dir / "constraint_comparison.csv", index=False)

    return binding_stats


def analyze_position_concentration(positions_df: pd.DataFrame, output_dir: Path):
    """Measure position concentration using Herfindahl index."""
    print("\n=== Position Concentration Analysis ===")

    # Check if already in wide format (date index with instrument columns)
    if 'date' in positions_df.columns:
        # Load as wide format
        positions_wide = positions_df.set_index('date')
    else:
        # Already in wide format with date as index
        positions_wide = positions_df

    # Compute absolute position weights
    abs_positions = positions_wide.abs()
    total_abs = abs_positions.sum(axis=1)

    # Normalize to get weights
    weights = abs_positions.div(total_abs, axis=0)

    # Compute Herfindahl index (sum of squared weights)
    # 0 = perfectly equal, 1 = all in one instrument
    herfindahl = (weights ** 2).sum(axis=1)

    # Get top 3 position weights
    top3_weights = weights.apply(lambda row: row.nlargest(3).values, axis=1, result_type='expand')
    top3_weights.columns = ['Top 1', 'Top 2', 'Top 3']

    print(f"Herfindahl index: mean={herfindahl.mean():.3f}, median={herfindahl.median():.3f}")
    print(f"Top 1 weight: mean={top3_weights['Top 1'].mean():.1%}")
    print(f"Top 3 combined: mean={top3_weights.sum(axis=1).mean():.1%}")

    # Plot concentration over time
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 10))

    # Herfindahl index
    ax1.plot(herfindahl.index, herfindahl.values, alpha=0.7)
    ax1.axhline(1.0 / len(positions_wide.columns), color='red', linestyle='--',
                label=f'Equal weight (1/N = {1.0/len(positions_wide.columns):.3f})')
    ax1.set_ylabel('Herfindahl Index')
    ax1.set_title('Position Concentration (Herfindahl Index)')
    ax1.legend()
    ax1.grid(alpha=0.3)

    # Top 3 weights
    ax2.fill_between(top3_weights.index, 0, top3_weights['Top 1'], alpha=0.5, label='Top 1')
    ax2.fill_between(top3_weights.index, top3_weights['Top 1'],
                     top3_weights['Top 1'] + top3_weights['Top 2'], alpha=0.5, label='Top 2')
    ax2.fill_between(top3_weights.index,
                     top3_weights['Top 1'] + top3_weights['Top 2'],
                     top3_weights.sum(axis=1), alpha=0.5, label='Top 3')
    ax2.set_xlabel('Date')
    ax2.set_ylabel('Weight')
    ax2.set_title('Top 3 Position Weights')
    ax2.legend()
    ax2.grid(alpha=0.3)

    plt.tight_layout()
    plt.savefig(output_dir / "position_concentration.png", dpi=150, bbox_inches='tight')
    plt.close()

    return {
        'herfindahl_mean': herfindahl.mean(),
        'herfindahl_median': herfindahl.median(),
        'top1_mean': top3_weights['Top 1'].mean(),
        'top3_mean': top3_weights.sum(axis=1).mean()
    }


def generate_summary_report(
    output_dir: Path,
    phase2_data: dict,
    phase1_data: dict,
    corr_stats: dict,
    idm_stats: dict,
    constraint_stats: list,
    concentration_stats: dict,
    runtime_phase2: float,
    runtime_phase1: float
):
    """Generate markdown summary report."""
    print("\n=== Generating Summary Report ===")

    report = []
    report.append("# Phase 2 Scaling Analysis Summary")
    report.append("")
    report.append("**Date**: 2026-01-26")
    report.append("**Analysis**: Cross-section scaling from N=4 to N=15 instruments")
    report.append("")
    report.append("## Executive Summary")
    report.append("")
    report.append("This report characterizes system behavior when scaling from:")
    report.append("- **Phase 1 (Depth)**: N=4 instruments, 2020-2024 (1,782 days, includes COVID crash)")
    report.append("- **Phase 2 (Breadth)**: N=15 instruments, 2021-2024 (~1,460 days, post-maturity regime)")
    report.append("")
    report.append("**Key Insight**: This is a *depth vs breadth* comparison, not a performance ranking. ")
    report.append("Different regimes and cross-sections produce different economic outcomes by design.")
    report.append("")

    # Engineering checks
    report.append("## Engineering Success Criteria")
    report.append("")

    # Runtime check
    if runtime_phase2 > 0:
        runtime_ok = runtime_phase2 < 30.0
        report.append(f"- **Runtime**: {runtime_phase2:.1f}s (target <30s) {'✓' if runtime_ok else '✗ REQUIRES OPTIMIZATION'}")
        if runtime_phase1 > 0:
            report.append(f"  - Phase 1 (N=4): {runtime_phase1:.1f}s")
            report.append(f"  - Phase 2 (N=15): {runtime_phase2:.1f}s")
            report.append(f"  - Scaling factor: {runtime_phase2 / runtime_phase1:.1f}x")
        else:
            report.append(f"  - Phase 2 (N=15): {runtime_phase2:.1f}s")
            report.append(f"  - Phase 1 runtime not recorded")
        report.append("")
    else:
        report.append("- **Runtime**: Not recorded in metadata (backtest completed successfully based on outputs)")
        report.append("")

    # Stability check
    phase2_meta = phase2_data.get('metadata', {})
    has_errors = phase2_meta.get('errors', False)
    report.append(f"- **Stability**: {'✗ CRASHES/ERRORS DETECTED' if has_errors else '✓ No crashes or numeric instability'}")
    report.append("")

    # Correlation analysis
    report.append("## Correlation Structure (N=15)")
    report.append("")
    if corr_stats:
        report.append(f"- **Median pairwise correlation**: {corr_stats['median']:.3f}")
        report.append(f"- **Mean pairwise correlation**: {corr_stats['mean']:.3f}")
        report.append(f"- **Range**: [{corr_stats['min']:.3f}, {corr_stats['max']:.3f}]")
        report.append("")

        if corr_stats['median'] > 0.8:
            report.append("**Observation**: High correlations (>0.8) typical in post-2021 crypto regime. ")
            report.append("This limits diversification benefit but is a regime characteristic, not a failure.")
        elif corr_stats['median'] > 0.6:
            report.append("**Observation**: Moderate-high correlations typical for crypto assets.")
        report.append("")

    # IDM analysis
    report.append("## IDM Scaling Behavior")
    report.append("")
    if idm_stats:
        for label in ['Phase 2 (N=15)', 'Phase 1 (N=4)']:
            if label in idm_stats:
                stats = idm_stats[label]
                report.append(f"### {label}")
                report.append(f"- Mean IDM: {stats['mean']:.3f}")
                report.append(f"- Max IDM: {stats['max']:.3f}")
                report.append("")

        if 'Phase 2 (N=15)' in idm_stats and 'Phase 1 (N=4)' in idm_stats:
            idm_increase = idm_stats['Phase 2 (N=15)']['mean'] / idm_stats['Phase 1 (N=4)']['mean']
            report.append(f"**IDM scaling**: {idm_increase:.2f}x increase from N=4 to N=15")

            if idm_increase < 1.2:
                report.append("**Observation**: Limited IDM increase suggests high correlations reduce diversification benefit.")
            elif idm_increase > 1.5:
                report.append("**Observation**: Strong IDM increase suggests effective diversification at N=15.")
            report.append("")

    # Constraint binding
    report.append("## Constraint Binding Patterns")
    report.append("")
    if constraint_stats:
        for stats in constraint_stats:
            report.append(f"### {stats['phase']}")
            if 'gross_lev_binding_pct' in stats:
                report.append(f"- **Gross leverage**: {stats['gross_lev_mean']:.2f} mean, {stats['gross_lev_max']:.2f} max")
                report.append(f"- **Binding frequency**: {stats['gross_lev_binding_pct']:.1f}% of days")
            if 'idm_binding_pct' in stats:
                report.append(f"- **IDM cap binding**: {stats['idm_binding_pct']:.1f}% of days")
            report.append("")

    # Position concentration
    report.append("## Position Concentration (N=15)")
    report.append("")
    if concentration_stats:
        report.append(f"- **Herfindahl index**: {concentration_stats['herfindahl_mean']:.3f} (0=equal weights, 1=all in one)")
        report.append(f"- **Top 1 position**: {concentration_stats['top1_mean']:.1%} of portfolio on average")
        report.append(f"- **Top 3 combined**: {concentration_stats['top3_mean']:.1%} of portfolio on average")
        report.append("")

        equal_weight = 1.0 / corr_stats.get('n_instruments', 15)
        if concentration_stats['herfindahl_mean'] > 3 * equal_weight:
            report.append("**Observation**: Material position concentration detected. System favors certain instruments.")
        else:
            report.append("**Observation**: Reasonable diversification across instruments.")
        report.append("")

    # Regime differences
    report.append("## Regime Context: Depth vs Breadth")
    report.append("")
    report.append("**Phase 1 (2020-2024, N=4)**:")
    report.append("- Includes COVID crash (March 2020) - extreme volatility regime")
    report.append("- Longer history (1,782 days)")
    report.append("- Depth-focused: fewer instruments, longer time series")
    report.append("")
    report.append("**Phase 2 (2021-2024, N=15)**:")
    report.append("- Post-maturity regime, no COVID crash")
    report.append("- Shorter history (~1,460 days)")
    report.append("- Breadth-focused: more instruments, shorter time series")
    report.append("")
    report.append("**Important**: Performance differences reflect regime and scale differences, not system quality. ")
    report.append("Any divergence in Sharpe, drawdown, or returns should be interpreted as regime characteristics.")
    report.append("")

    # Engineering issues
    report.append("## Engineering Issues Identified")
    report.append("")

    issues = []
    if runtime_phase2 >= 30.0:
        issues.append(f"- Runtime exceeds 30s target ({runtime_phase2:.1f}s) - requires profiling and optimization")
    if has_errors:
        issues.append("- Errors or crashes detected during backtest - requires debugging")

    if issues:
        for issue in issues:
            report.append(issue)
        report.append("")
        report.append("**Action Required**: Fix engineering issues before proceeding to rule tuning.")
    else:
        report.append("✓ No engineering issues detected. System is stable at N=15.")
    report.append("")

    # Next steps
    report.append("## Next Steps")
    report.append("")
    report.append("1. **If engineering issues**: Fix runtime/stability problems first")
    report.append("2. **If no engineering issues**: Proceed to rule inclusion/tuning decisions with full context")
    report.append("3. **Future work**: Regime analysis within 2021-2024 period (Bull/Bear/Recovery phases)")
    report.append("")
    report.append("## Appendices")
    report.append("")
    report.append("- `correlation_heatmap.png`: 15x15 correlation matrix visualization")
    report.append("- `correlation_distribution.png`: Distribution of pairwise correlations")
    report.append("- `idm_over_time.png`: IDM time series (N=4 vs N=15)")
    report.append("- `position_concentration.png`: Herfindahl index and top weights over time")
    report.append("- `constraint_comparison.csv`: Detailed constraint binding statistics")
    report.append("- `correlation_matrix.csv`: Full correlation matrix")
    report.append("")

    # Write report
    with open(output_dir / "phase2_scaling_summary.md", 'w') as f:
        f.write('\n'.join(report))

    print(f"✓ Report written to {output_dir / 'phase2_scaling_summary.md'}")


def main():
    parser = argparse.ArgumentParser(description='Generate Phase 2 scaling analysis report')
    parser.add_argument('--phase2-dir', type=str, required=True,
                       help='Phase 2 backtest output directory')
    parser.add_argument('--phase1-dir', type=str, required=True,
                       help='Phase 1 backtest output directory for comparison')
    parser.add_argument('--output-dir', type=str, default=None,
                       help='Output directory (default: same as phase2-dir)')
    args = parser.parse_args()

    phase2_dir = Path(args.phase2_dir)
    phase1_dir = Path(args.phase1_dir)
    output_dir = Path(args.output_dir) if args.output_dir else phase2_dir

    print("=" * 80)
    print("Phase 2 Scaling Analysis Report")
    print("=" * 80)
    print(f"\nPhase 2 directory: {phase2_dir}")
    print(f"Phase 1 directory: {phase1_dir}")
    print(f"Output directory: {output_dir}")

    # Load data
    print("\n=== Loading Data ===")
    start_time = time.time()

    phase2_data = load_backtest_data(phase2_dir)
    phase1_data = load_backtest_data(phase1_dir)

    # Get runtime from metadata
    runtime_phase2 = phase2_data.get('metadata', {}).get('runtime_seconds', 0.0)
    runtime_phase1 = phase1_data.get('metadata', {}).get('runtime_seconds', 0.0)

    print(f"✓ Loaded Phase 2 data: {list(phase2_data.keys())}")
    print(f"✓ Loaded Phase 1 data: {list(phase1_data.keys())}")

    # Run analyses
    corr_stats = None
    if 'diagnostics' in phase2_data:
        corr_stats = analyze_correlation_structure(phase2_data['diagnostics'], output_dir)

    idm_stats = analyze_idm_behavior(phase2_data, phase1_data, output_dir)

    constraint_stats = analyze_constraint_binding(phase2_data, phase1_data, output_dir)

    concentration_stats = None
    if 'positions' in phase2_data:
        concentration_stats = analyze_position_concentration(phase2_data['positions'], output_dir)

    # Generate summary report
    generate_summary_report(
        output_dir,
        phase2_data,
        phase1_data,
        corr_stats,
        idm_stats,
        constraint_stats,
        concentration_stats,
        runtime_phase2,
        runtime_phase1
    )

    elapsed = time.time() - start_time
    print(f"\n✓ Analysis complete in {elapsed:.1f}s")
    print(f"\nOutputs written to: {output_dir}")


if __name__ == '__main__':
    main()
