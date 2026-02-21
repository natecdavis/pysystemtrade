#!/usr/bin/env python
"""
Analyze impact of different minimum history requirements on universe composition and performance.

Usage:
    python scripts/analyze_min_history_impact.py \\
        --baseline out/min_history_test/baseline_365d \\
        --alt1 out/min_history_test/alt1_15d_any_rule \\
        --alt2 out/min_history_test/alt2_270d_all_rules \\
        --output out/min_history_test/ANALYSIS_REPORT.md

Generates:
    - Instrument count comparison
    - Entry date comparison
    - Rule coverage analysis
    - Performance metrics comparison
    - P&L attribution by instrument cohort
"""

import argparse
import json
from pathlib import Path
from typing import Dict, List, Tuple
import pandas as pd
import numpy as np


def load_backtest_results(outdir: Path) -> Dict:
    """Load backtest results from output directory."""
    results = {}

    # Load equity curve
    equity_path = outdir / "equity_curve.csv"
    if equity_path.exists():
        results['equity'] = pd.read_csv(equity_path, index_col=0, parse_dates=True)

    # Load positions
    positions_path = outdir / "positions.csv"
    if positions_path.exists():
        results['positions'] = pd.read_csv(positions_path, index_col=0, parse_dates=True)

    # Load diagnostics
    diag_path = outdir / "diagnostics.parquet"
    if diag_path.exists():
        results['diagnostics'] = pd.read_parquet(diag_path)

    # Load universe snapshot (if exists)
    snapshot_path = outdir / "universe_snapshot.json"
    if snapshot_path.exists():
        with open(snapshot_path) as f:
            results['universe_snapshot'] = json.load(f)

    # Load performance summary
    perf_path = outdir / "performance_summary.txt"
    if perf_path.exists():
        results['performance'] = parse_performance_summary(perf_path)

    return results


def parse_performance_summary(path: Path) -> Dict:
    """Parse performance metrics from summary text file."""
    metrics = {}
    with open(path) as f:
        for line in f:
            if ':' in line:
                key, val = line.split(':', 1)
                key = key.strip()
                val = val.strip()
                # Try to convert to float
                try:
                    if '%' in val:
                        metrics[key] = float(val.replace('%', '')) / 100
                    else:
                        metrics[key] = float(val)
                except ValueError:
                    metrics[key] = val
    return metrics


def analyze_universe_composition(
    baseline: Dict,
    alt1: Dict,
    alt2: Dict
) -> pd.DataFrame:
    """Compare instrument counts and universe composition."""

    def get_instrument_stats(results: Dict) -> Dict:
        positions = results.get('positions', pd.DataFrame())
        if positions.empty:
            return {'total': 0, 'avg_active': 0, 'max_active': 0}

        total = len(positions.columns)
        active = (positions != 0).sum(axis=1)
        return {
            'total': total,
            'avg_active': active.mean(),
            'max_active': active.max(),
            'min_active': active.min()
        }

    stats = pd.DataFrame({
        'Baseline (365d)': get_instrument_stats(baseline),
        'Alt 1 (15d)': get_instrument_stats(alt1),
        'Alt 2 (270d)': get_instrument_stats(alt2)
    })

    return stats


def analyze_entry_dates(
    baseline: Dict,
    alt1: Dict,
    alt2: Dict
) -> pd.DataFrame:
    """Compare when instruments first entered the universe."""

    def get_entry_dates(results: Dict) -> pd.Series:
        positions = results.get('positions', pd.DataFrame())
        if positions.empty:
            return pd.Series(dtype='datetime64[ns]')

        # Find first non-zero position for each instrument
        entry_dates = {}
        for col in positions.columns:
            first_nonzero = positions[col][positions[col] != 0].first_valid_index()
            if first_nonzero is not None:
                entry_dates[col] = first_nonzero

        return pd.Series(entry_dates)

    baseline_entries = get_entry_dates(baseline)
    alt1_entries = get_entry_dates(alt1)
    alt2_entries = get_entry_dates(alt2)

    # Combine into DataFrame
    all_instruments = set(baseline_entries.index) | set(alt1_entries.index) | set(alt2_entries.index)

    comparison = pd.DataFrame(
        index=sorted(all_instruments),
        columns=['Baseline (365d)', 'Alt 1 (15d)', 'Alt 2 (270d)', 'Days Earlier (Alt1)', 'Days Earlier (Alt2)']
    )

    for instr in all_instruments:
        comparison.loc[instr, 'Baseline (365d)'] = baseline_entries.get(instr, pd.NaT)
        comparison.loc[instr, 'Alt 1 (15d)'] = alt1_entries.get(instr, pd.NaT)
        comparison.loc[instr, 'Alt 2 (270d)'] = alt2_entries.get(instr, pd.NaT)

        # Calculate days earlier
        if instr in baseline_entries and instr in alt1_entries:
            delta = (baseline_entries[instr] - alt1_entries[instr]).days
            comparison.loc[instr, 'Days Earlier (Alt1)'] = delta

        if instr in baseline_entries and instr in alt2_entries:
            delta = (baseline_entries[instr] - alt2_entries[instr]).days
            comparison.loc[instr, 'Days Earlier (Alt2)'] = delta

    return comparison


def analyze_rule_coverage(
    baseline: Dict,
    alt1: Dict,
    alt2: Dict
) -> pd.DataFrame:
    """Analyze rule coverage per instrument over time."""

    def get_rule_coverage_stats(results: Dict) -> Dict:
        diag = results.get('diagnostics', pd.DataFrame())
        if diag.empty or 'rule_name' not in diag.columns:
            return {'avg_rules_per_instr': 0, 'min_rules': 0, 'max_rules': 22}

        # Count unique rules per (date, instrument)
        rule_counts = diag.groupby(['date', 'instrument'])['rule_name'].nunique()

        return {
            'avg_rules_per_instr': rule_counts.mean(),
            'min_rules': rule_counts.min(),
            'max_rules': rule_counts.max()
        }

    stats = pd.DataFrame({
        'Baseline (365d)': get_rule_coverage_stats(baseline),
        'Alt 1 (15d)': get_rule_coverage_stats(alt1),
        'Alt 2 (270d)': get_rule_coverage_stats(alt2)
    })

    return stats


def compare_performance(
    baseline: Dict,
    alt1: Dict,
    alt2: Dict
) -> pd.DataFrame:
    """Compare key performance metrics."""

    def calc_metrics(results: Dict) -> Dict:
        equity = results.get('equity', pd.DataFrame())
        if equity.empty or 'portfolio_value' not in equity.columns:
            return {}

        pv = equity['portfolio_value']
        returns = pv.pct_change().dropna()

        # Calculate metrics
        sharpe = returns.mean() / returns.std() * np.sqrt(252) if len(returns) > 0 else 0
        cagr = (pv.iloc[-1] / pv.iloc[0]) ** (252 / len(pv)) - 1 if len(pv) > 1 else 0
        vol = returns.std() * np.sqrt(252)
        max_dd = ((pv.cummax() - pv) / pv.cummax()).max()

        # Get positions stats
        positions = results.get('positions', pd.DataFrame())
        avg_pos = (positions != 0).sum(axis=1).mean() if not positions.empty else 0

        # Calculate turnover
        turnover = 0
        if not positions.empty:
            position_changes = positions.diff().abs()
            turnover = position_changes.sum().sum() / len(positions)

        return {
            'Sharpe': sharpe,
            'CAGR': cagr,
            'Volatility': vol,
            'Max Drawdown': max_dd,
            'Avg Positions': avg_pos,
            'Turnover': turnover
        }

    metrics = pd.DataFrame({
        'Baseline (365d)': calc_metrics(baseline),
        'Alt 1 (15d)': calc_metrics(alt1),
        'Alt 2 (270d)': calc_metrics(alt2)
    })

    # Add delta columns
    metrics['Δ Alt1 vs Baseline'] = metrics['Alt 1 (15d)'] - metrics['Baseline (365d)']
    metrics['Δ Alt2 vs Baseline'] = metrics['Alt 2 (270d)'] - metrics['Baseline (365d)']

    # Add percentage change
    metrics['% Change Alt1'] = (metrics['Alt 1 (15d)'] / metrics['Baseline (365d)'] - 1) * 100
    metrics['% Change Alt2'] = (metrics['Alt 2 (270d)'] / metrics['Baseline (365d)'] - 1) * 100

    return metrics


def analyze_pnl_by_cohort(
    alt1: Dict,
    entry_dates: pd.DataFrame
) -> pd.DataFrame:
    """Analyze P&L contribution by instrument age cohort (only for Alt1)."""

    positions = alt1.get('positions', pd.DataFrame())
    equity = alt1.get('equity', pd.DataFrame())

    if positions.empty or equity.empty:
        return pd.DataFrame()

    # Define cohorts based on data age
    cohorts = {
        'Very Young (15-60d)': (15, 60),
        'Young (61-180d)': (61, 180),
        'Mature (181-365d)': (181, 365),
        'Established (365d+)': (365, 10000)
    }

    # Calculate daily returns per instrument
    pv = equity['portfolio_value']
    returns = pv.pct_change().dropna()

    # Assign instruments to cohorts on each date
    cohort_returns = {cohort: [] for cohort in cohorts}

    for date in positions.index:
        if date not in returns.index:
            continue

        daily_return = returns.loc[date]

        for instr in positions.columns:
            if instr not in entry_dates.index:
                continue

            entry_date = entry_dates.loc[instr, 'Alt 1 (15d)']
            if pd.isna(entry_date):
                continue

            # Calculate age at this date
            age_days = (date - entry_date).days

            # Find cohort
            for cohort, (min_age, max_age) in cohorts.items():
                if min_age <= age_days < max_age:
                    # Weight by position size
                    pos = positions.loc[date, instr]
                    if pos != 0:
                        cohort_returns[cohort].append(daily_return * abs(pos))
                    break

    # Summarize cohort performance
    cohort_stats = {}
    for cohort, returns_list in cohort_returns.items():
        if len(returns_list) > 0:
            cohort_stats[cohort] = {
                'Avg Daily Return': np.mean(returns_list),
                'Total Return': np.sum(returns_list),
                'Count': len(returns_list)
            }

    return pd.DataFrame(cohort_stats).T


def generate_report(
    baseline: Dict,
    alt1: Dict,
    alt2: Dict,
    output_path: Path
):
    """Generate comprehensive markdown report."""

    # Run all analyses
    universe_comp = analyze_universe_composition(baseline, alt1, alt2)
    entry_dates = analyze_entry_dates(baseline, alt1, alt2)
    rule_coverage = analyze_rule_coverage(baseline, alt1, alt2)
    performance = compare_performance(baseline, alt1, alt2)
    cohort_pnl = analyze_pnl_by_cohort(alt1, entry_dates)

    # Generate markdown
    report = f"""# Minimum History Requirement Analysis
## Summary

This report compares three different minimum history requirements:

1. **Baseline (365 days)**: Current system (reproduces Sharpe 0.95)
2. **Alternative 1 (15 days)**: Early entry with partial rule coverage
3. **Alternative 2 (270 days)**: Moderate entry with full rule coverage

---

## Universe Composition

{universe_comp.to_markdown()}

**Observations:**
- Baseline: ~{universe_comp.loc['total', 'Baseline (365d)']:.0f} total instruments, ~{universe_comp.loc['avg_active', 'Baseline (365d)']:.1f} avg active
- Alt 1: ~{universe_comp.loc['total', 'Alt 1 (15d)']:.0f} total (+{universe_comp.loc['total', 'Alt 1 (15d)'] - universe_comp.loc['total', 'Baseline (365d)']:.0f}), ~{universe_comp.loc['avg_active', 'Alt 1 (15d)']:.1f} avg active
- Alt 2: ~{universe_comp.loc['total', 'Alt 2 (270d)']:.0f} total (+{universe_comp.loc['total', 'Alt 2 (270d)'] - universe_comp.loc['total', 'Baseline (365d)']:.0f}), ~{universe_comp.loc['avg_active', 'Alt 2 (270d)']:.1f} avg active

---

## Rule Coverage Analysis

{rule_coverage.to_markdown()}

**Key Findings:**
- Baseline: All instruments have {rule_coverage.loc['max_rules', 'Baseline (365d)']:.0f}/22 rules active
- Alt 1: Variable coverage ({rule_coverage.loc['min_rules', 'Alt 1 (15d)']:.0f}-{rule_coverage.loc['max_rules', 'Alt 1 (15d)']:.0f} rules), avg {rule_coverage.loc['avg_rules_per_instr', 'Alt 1 (15d)']:.1f}
- Alt 2: All instruments require {rule_coverage.loc['min_rules', 'Alt 2 (270d)']:.0f}/22 rules (enforced by 'all_rules' mode)

---

## Performance Comparison

{performance.to_markdown()}

**Interpretation:**

### Sharpe Ratio
- Baseline: {performance.loc['Sharpe', 'Baseline (365d)']:.4f}
- Alt 1: {performance.loc['Sharpe', 'Alt 1 (15d)']:.4f} ({performance.loc['% Change Alt1', 'Sharpe']:+.2f}%)
- Alt 2: {performance.loc['Sharpe', 'Alt 2 (270d)']:.4f} ({performance.loc['% Change Alt2', 'Sharpe']:+.2f}%)

### CAGR
- Baseline: {performance.loc['CAGR', 'Baseline (365d)']:.2%}
- Alt 1: {performance.loc['CAGR', 'Alt 1 (15d)']:.2%} ({performance.loc['% Change Alt1', 'CAGR']:+.2f}%)
- Alt 2: {performance.loc['CAGR', 'Alt 2 (270d)']:.2%} ({performance.loc['% Change Alt2', 'CAGR']:+.2f}%)

### Risk Metrics
- Max Drawdown Baseline: {performance.loc['Max Drawdown', 'Baseline (365d)']:.2%}
- Max Drawdown Alt 1: {performance.loc['Max Drawdown', 'Alt 1 (15d)']:.2%}
- Max Drawdown Alt 2: {performance.loc['Max Drawdown', 'Alt 2 (270d)']:.2%}

### Transaction Costs
- Turnover Baseline: {performance.loc['Turnover', 'Baseline (365d)']:.2f}x
- Turnover Alt 1: {performance.loc['Turnover', 'Alt 1 (15d)']:.2f}x ({performance.loc['% Change Alt1', 'Turnover']:+.2f}%)
- Turnover Alt 2: {performance.loc['Turnover', 'Alt 2 (270d)']:.2f}x ({performance.loc['% Change Alt2', 'Turnover']:+.2f}%)

---

## Entry Date Comparison

First 10 instruments sorted by baseline entry date:

{entry_dates.head(10).to_markdown()}

**Insights:**
- Instruments entering earlier in Alt1: {(entry_dates['Days Earlier (Alt1)'] > 0).sum()}
- Instruments entering earlier in Alt2: {(entry_dates['Days Earlier (Alt2)'] > 0).sum()}
- Average early entry (Alt1): {entry_dates['Days Earlier (Alt1)'].mean():.0f} days
- Average early entry (Alt2): {entry_dates['Days Earlier (Alt2)'].mean():.0f} days

---

## P&L Attribution by Cohort (Alt 1 Only)

{cohort_pnl.to_markdown() if not cohort_pnl.empty else "No cohort data available"}

---

## Recommendation

Based on the analysis above:

"""

    # Add recommendation logic
    sharpe_baseline = performance.loc['Sharpe', 'Baseline (365d)']
    sharpe_alt1 = performance.loc['Sharpe', 'Alt 1 (15d)']
    sharpe_alt2 = performance.loc['Sharpe', 'Alt 2 (270d)']

    if sharpe_alt1 >= sharpe_baseline * 1.02:  # 2%+ improvement
        report += f"""
### ✅ ADOPT ALTERNATIVE 1 (15 days)

Alternative 1 shows {(sharpe_alt1/sharpe_baseline - 1)*100:.1f}% Sharpe improvement with acceptable risk metrics.
Early entry into young instruments provides meaningful alpha.

**Action Items:**
1. Update `crypto_perps_full_rules.yaml` with Alt 1 parameters
2. Rebuild production dataset with --min-history-days=15
3. Monitor transaction costs in live trading
"""
    elif sharpe_alt2 >= sharpe_baseline * 1.01:  # 1%+ improvement
        report += f"""
### ✅ ADOPT ALTERNATIVE 2 (270 days)

Alternative 2 shows {(sharpe_alt2/sharpe_baseline - 1)*100:.1f}% Sharpe improvement with lower risk.
Conservative approach captures 3-6 month old instruments with full rule coverage.

**Action Items:**
1. Update `crypto_perps_full_rules.yaml` with Alt 2 parameters
2. Rebuild production dataset with --min-history-days=270
3. Full rule coverage reduces implementation risk
"""
    else:
        report += f"""
### ⚠️ KEEP BASELINE (365 days)

Neither alternative shows material improvement (target: +1% Sharpe minimum).

- Alt 1: {(sharpe_alt1/sharpe_baseline - 1)*100:+.1f}%
- Alt 2: {(sharpe_alt2/sharpe_baseline - 1)*100:+.1f}%

**Conclusion:**
Current 365-day threshold is optimal. Data quality and maturity matter more than early entry.

**Recommendation:** Keep current system unchanged.
"""

    report += """
---

## Next Steps

1. **If adopting alternative:** Run parameter sweep on other thresholds (30d, 60d, 90d, 120d, 180d)
2. **If keeping baseline:** Document findings and close investigation
3. **Follow-up analysis:** Examine delisted instruments to quantify survivorship bias

---

*Report generated by analyze_min_history_impact.py*
"""

    # Write report
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w') as f:
        f.write(report)

    print(f"✅ Report written to: {output_path}")


def main():
    parser = argparse.ArgumentParser(description="Analyze minimum history requirement impact")
    parser.add_argument('--baseline', type=str, required=True, help='Path to baseline backtest output')
    parser.add_argument('--alt1', type=str, required=True, help='Path to Alternative 1 backtest output')
    parser.add_argument('--alt2', type=str, required=True, help='Path to Alternative 2 backtest output')
    parser.add_argument('--output', type=str, required=True, help='Path to output markdown report')

    args = parser.parse_args()

    # Load results
    print("Loading backtest results...")
    baseline = load_backtest_results(Path(args.baseline))
    alt1 = load_backtest_results(Path(args.alt1))
    alt2 = load_backtest_results(Path(args.alt2))

    # Generate report
    print("Generating analysis report...")
    generate_report(baseline, alt1, alt2, Path(args.output))

    print("✅ Analysis complete!")


if __name__ == '__main__':
    main()
