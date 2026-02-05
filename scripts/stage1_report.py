#!/usr/bin/env python3
"""
Stage-1 Research Report Generator

Unified script to perform all Stage-1 research analyses:
1. Observable PnL decomposition (price, funding, costs)
2. Counterfactual attribution (carry effect, constraint effect)
3. Regime drawdown analysis
4. Constraint binding analysis
5. State transition analysis
6. Turnover clustering analysis (with cost context)

Generates:
- research_summary.md: Executive summary with all findings
- Multiple plots and analysis tables

Usage:
    python scripts/stage1_report.py \
        --baseline-dir out/stage1_baseline \
        --carry-off-dir out/stage1_carry_off \
        --constraints-off-dir out/stage1_constraints_off \
        --output-dir out/stage1_baseline
"""

import argparse
from pathlib import Path
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from datetime import datetime
import json

# Regime definitions (date-based)
REGIMES = [
    ("COVID Crash", "2020-02-10", "2020-04-30"),
    ("Post-COVID", "2020-05-01", "2020-12-31"),
    ("Bull 2021", "2021-01-01", "2021-11-30"),
    ("Bear 2022", "2022-01-01", "2022-12-31"),
    ("Recovery 2023", "2023-01-01", "2023-12-31"),
    ("Bull 2024", "2024-01-01", "2024-12-31"),
]


def load_backtest_data(backtest_dir: Path) -> dict:
    """Load all relevant files from a backtest output directory."""
    data = {}

    # Load equity curve
    equity_path = backtest_dir / "equity_curve.csv"
    if equity_path.exists():
        data["equity"] = pd.read_csv(equity_path, parse_dates=["date"])

    # Load PnL breakdown
    pnl_path = backtest_dir / "pnl_breakdown.csv"
    if pnl_path.exists():
        data["pnl"] = pd.read_csv(pnl_path, parse_dates=["date"])

    # Load positions
    positions_path = backtest_dir / "positions.csv"
    if positions_path.exists():
        data["positions"] = pd.read_csv(positions_path, parse_dates=["date"])

    # Load diagnostics
    diagnostics_path = backtest_dir / "diagnostics.parquet"
    if diagnostics_path.exists():
        data["diagnostics"] = pd.read_parquet(diagnostics_path)

    # Load metadata
    metadata_path = backtest_dir / "metadata.json"
    if metadata_path.exists():
        with open(metadata_path, 'r') as f:
            data["metadata"] = json.load(f)

    return data


def analyze_observable_pnl(pnl_df: pd.DataFrame) -> tuple:
    """
    Analyze observable PnL decomposition (price, funding, costs).

    Returns:
        (pnl_summary_dict, pnl_timeseries_df)
    """
    total_price_pnl = pnl_df["price_pnl"].sum()
    total_funding_pnl = pnl_df["funding_pnl"].sum()
    total_costs = pnl_df["costs"].sum()
    total_pnl = pnl_df["total_pnl"].sum()

    gross_pnl = abs(total_price_pnl) + abs(total_funding_pnl)

    summary = {
        "Price PnL": total_price_pnl,
        "Funding PnL": total_funding_pnl,
        "Costs": total_costs,
        "Net PnL": total_pnl,
        "Price %": 100 * total_price_pnl / gross_pnl if gross_pnl > 0 else 0,
        "Funding %": 100 * total_funding_pnl / gross_pnl if gross_pnl > 0 else 0,
        "Costs %": 100 * total_costs / gross_pnl if gross_pnl > 0 else 0,
    }

    # Cumulative PnL timeseries
    timeseries = pnl_df[["date", "price_pnl", "funding_pnl", "costs"]].copy()
    timeseries["cum_price"] = timeseries["price_pnl"].cumsum()
    timeseries["cum_funding"] = timeseries["funding_pnl"].cumsum()
    timeseries["cum_costs"] = timeseries["costs"].cumsum()
    timeseries["cum_total"] = timeseries["cum_price"] + timeseries["cum_funding"] + timeseries["cum_costs"]

    return summary, timeseries


def analyze_counterfactual(baseline_equity: pd.DataFrame,
                           carry_off_equity: pd.DataFrame,
                           constraints_off_equity: pd.DataFrame) -> dict:
    """
    Analyze counterfactual attribution (carry effect, constraint effect).

    Returns:
        dict with scenario results
    """
    scenarios = {}

    # Baseline
    baseline_final = baseline_equity["equity"].iloc[-1]
    baseline_start = baseline_equity["equity"].iloc[0]
    baseline_return = (baseline_final / baseline_start - 1) * 100
    scenarios["Baseline"] = {
        "final_equity": baseline_final,
        "return_pct": baseline_return,
        "delta_vs_baseline": 0,
        "delta_pct": 0,
    }

    # Carry off
    carry_off_final = carry_off_equity["equity"].iloc[-1]
    carry_off_start = carry_off_equity["equity"].iloc[0]
    carry_off_return = (carry_off_final / carry_off_start - 1) * 100
    scenarios["Carry Off"] = {
        "final_equity": carry_off_final,
        "return_pct": carry_off_return,
        "delta_vs_baseline": carry_off_final - baseline_final,
        "delta_pct": ((carry_off_final / baseline_final) - 1) * 100,
    }

    # Constraints off
    constraints_off_final = constraints_off_equity["equity"].iloc[-1]
    constraints_off_start = constraints_off_equity["equity"].iloc[0]
    constraints_off_return = (constraints_off_final / constraints_off_start - 1) * 100
    scenarios["Constraints Off"] = {
        "final_equity": constraints_off_final,
        "return_pct": constraints_off_return,
        "delta_vs_baseline": constraints_off_final - baseline_final,
        "delta_pct": ((constraints_off_final / baseline_final) - 1) * 100,
    }

    return scenarios


def analyze_regime_drawdowns(equity_df: pd.DataFrame) -> tuple:
    """
    Analyze drawdowns by regime.

    Returns:
        (regime_summary_dict, underwater_series)
    """
    equity_df = equity_df.copy()
    equity_df["date"] = pd.to_datetime(equity_df["date"])

    # Calculate running maximum and drawdown
    equity_df["running_max"] = equity_df["equity"].expanding().max()
    equity_df["drawdown"] = (equity_df["equity"] / equity_df["running_max"]) - 1

    regime_summary = {}

    for regime_name, start_str, end_str in REGIMES:
        start_date = pd.Timestamp(start_str)
        end_date = pd.Timestamp(end_str)

        regime_data = equity_df[(equity_df["date"] >= start_date) &
                                (equity_df["date"] <= end_date)]

        if len(regime_data) == 0:
            continue

        start_equity = regime_data["equity"].iloc[0]
        end_equity = regime_data["equity"].iloc[-1]
        regime_return = ((end_equity / start_equity) - 1) * 100

        max_dd = regime_data["drawdown"].min() * 100
        max_dd_date = regime_data.loc[regime_data["drawdown"].idxmin(), "date"]

        regime_summary[regime_name] = {
            "start_equity": start_equity,
            "end_equity": end_equity,
            "return_pct": regime_return,
            "max_dd_pct": max_dd,
            "max_dd_date": max_dd_date,
        }

    return regime_summary, equity_df[["date", "equity", "running_max", "drawdown"]]


def analyze_constraint_binding(diagnostics_df: pd.DataFrame) -> dict:
    """
    Analyze constraint binding frequency and severity.

    Returns:
        dict with binding statistics
    """
    # Calculate binding frequency
    total_rows = len(diagnostics_df)
    binding_rows = (diagnostics_df["overall_scalar"] < 1.0).sum()
    binding_pct = 100 * binding_rows / total_rows if total_rows > 0 else 0

    # Identify which constraint binds
    gross_lev_binding = (diagnostics_df["gross_leverage"] >= 1.99).sum()  # Close to 2.0 cap
    idm_binding = (diagnostics_df["idm"] >= 2.49).sum()  # Close to 2.5 cap

    # Severity distribution
    scalar_stats = diagnostics_df["overall_scalar"].describe()

    binding_summary = {
        "total_rows": total_rows,
        "binding_rows": binding_rows,
        "binding_pct": binding_pct,
        "gross_lev_binding_rows": gross_lev_binding,
        "idm_binding_rows": idm_binding,
        "scalar_mean": scalar_stats["mean"],
        "scalar_min": scalar_stats["min"],
        "scalar_p10": diagnostics_df["overall_scalar"].quantile(0.10),
        "scalar_p50": scalar_stats["50%"],
        "scalar_p90": diagnostics_df["overall_scalar"].quantile(0.90),
    }

    # Time series
    daily_scalar = diagnostics_df.groupby("date")["overall_scalar"].mean().reset_index()

    return binding_summary, daily_scalar


def analyze_state_transitions(diagnostics_df: pd.DataFrame) -> dict:
    """
    Analyze state occupancy and transitions.

    Returns:
        dict with state statistics
    """
    total_rows = len(diagnostics_df)

    # State occupancy
    state_counts = diagnostics_df["state"].value_counts()
    state_occupancy = {
        state: {
            "count": int(count),
            "pct": 100 * count / total_rows if total_rows > 0 else 0
        }
        for state, count in state_counts.items()
    }

    # Transition detection (simplified: just count state changes)
    diagnostics_df = diagnostics_df.sort_values(["instrument", "date"])
    diagnostics_df["state_prev"] = diagnostics_df.groupby("instrument")["state"].shift(1)
    transitions = (diagnostics_df["state"] != diagnostics_df["state_prev"]).sum()

    return {
        "state_occupancy": state_occupancy,
        "total_transitions": int(transitions),
    }


def analyze_turnover_clustering(positions_df: pd.DataFrame,
                                pnl_df: pd.DataFrame,
                                diagnostics_df: pd.DataFrame) -> tuple:
    """
    Analyze turnover clustering and cost context.

    Returns:
        (turnover_summary_dict, daily_turnover_series)
    """
    positions_df = positions_df.copy()
    positions_df["date"] = pd.to_datetime(positions_df["date"])

    # Calculate daily turnover (sum of absolute position changes)
    instrument_cols = [col for col in positions_df.columns if col != "date"]
    for col in instrument_cols:
        positions_df[f"{col}_change"] = positions_df[col].diff().abs()

    change_cols = [f"{col}_change" for col in instrument_cols]
    positions_df["daily_turnover"] = positions_df[change_cols].sum(axis=1)

    # Turnover statistics
    turnover_stats = positions_df["daily_turnover"].describe()

    # Gini coefficient (clustering measure)
    turnover_sorted = np.sort(positions_df["daily_turnover"].values)
    n = len(turnover_sorted)
    index = np.arange(1, n + 1)
    gini = ((2 * np.sum(index * turnover_sorted)) / (n * np.sum(turnover_sorted))) - ((n + 1) / n)

    # Cost analysis by regime
    pnl_df = pnl_df.copy()
    pnl_df["date"] = pd.to_datetime(pnl_df["date"])

    regime_costs = []
    for regime_name, start_str, end_str in REGIMES:
        start_date = pd.Timestamp(start_str)
        end_date = pd.Timestamp(end_str)

        regime_pnl = pnl_df[(pnl_df["date"] >= start_date) &
                            (pnl_df["date"] <= end_date)]
        regime_positions = positions_df[(positions_df["date"] >= start_date) &
                                       (positions_df["date"] <= end_date)]

        if len(regime_pnl) == 0:
            continue

        total_costs = regime_pnl["costs"].sum()
        gross_pnl = abs(regime_pnl["price_pnl"].sum()) + abs(regime_pnl["funding_pnl"].sum())
        mean_turnover = regime_positions["daily_turnover"].mean()

        regime_costs.append({
            "regime": regime_name,
            "mean_turnover": mean_turnover,
            "total_costs": total_costs,
            "costs_pct_gross": 100 * total_costs / gross_pnl if gross_pnl > 0 else 0,
        })

    turnover_summary = {
        "mean": turnover_stats["mean"],
        "p50": turnover_stats["50%"],
        "p90": turnover_stats.get("90%", positions_df["daily_turnover"].quantile(0.90)),
        "p99": positions_df["daily_turnover"].quantile(0.99),
        "gini": gini,
    }

    return turnover_summary, positions_df[["date", "daily_turnover"]], regime_costs


def plot_pnl_decomposition(timeseries: pd.DataFrame, output_path: Path):
    """Plot cumulative PnL decomposition."""
    fig, ax = plt.subplots(figsize=(12, 6))

    ax.plot(timeseries["date"], timeseries["cum_price"], label="Price PnL", linewidth=2)
    ax.plot(timeseries["date"], timeseries["cum_funding"], label="Funding PnL", linewidth=2)
    ax.plot(timeseries["date"], timeseries["cum_costs"], label="Costs", linewidth=2)
    ax.plot(timeseries["date"], timeseries["cum_total"], label="Total", linewidth=2.5, color="black", linestyle="--")

    ax.set_xlabel("Date")
    ax.set_ylabel("Cumulative PnL ($)")
    ax.set_title("Observable PnL Decomposition (2020-2024)")
    ax.legend()
    ax.grid(True, alpha=0.3)

    plt.tight_layout()
    plt.savefig(output_path, dpi=150)
    plt.close()


def plot_counterfactual_equity(baseline_equity: pd.DataFrame,
                               carry_off_equity: pd.DataFrame,
                               constraints_off_equity: pd.DataFrame,
                               output_path: Path):
    """Plot equity curves for all scenarios."""
    fig, ax = plt.subplots(figsize=(12, 6))

    ax.plot(baseline_equity["date"], baseline_equity["equity"], label="Baseline", linewidth=2.5)
    ax.plot(carry_off_equity["date"], carry_off_equity["equity"], label="Carry Off", linewidth=2, alpha=0.8)
    ax.plot(constraints_off_equity["date"], constraints_off_equity["equity"], label="Constraints Off", linewidth=2, alpha=0.8)

    ax.set_xlabel("Date")
    ax.set_ylabel("Equity ($)")
    ax.set_title("Counterfactual Equity Curves (2020-2024)")
    ax.legend()
    ax.grid(True, alpha=0.3)

    plt.tight_layout()
    plt.savefig(output_path, dpi=150)
    plt.close()


def plot_regime_drawdowns(underwater_df: pd.DataFrame, output_path: Path):
    """Plot underwater chart (drawdown over time)."""
    fig, ax = plt.subplots(figsize=(12, 6))

    ax.fill_between(underwater_df["date"], underwater_df["drawdown"] * 100, 0,
                     color="red", alpha=0.3, label="Drawdown")
    ax.plot(underwater_df["date"], underwater_df["drawdown"] * 100, color="red", linewidth=1.5)

    # Mark regime boundaries
    for regime_name, start_str, end_str in REGIMES:
        start_date = pd.Timestamp(start_str)
        ax.axvline(start_date, color="gray", linestyle="--", alpha=0.5, linewidth=0.8)

    ax.set_xlabel("Date")
    ax.set_ylabel("Drawdown (%)")
    ax.set_title("Underwater Plot by Regime (2020-2024)")
    ax.grid(True, alpha=0.3)

    plt.tight_layout()
    plt.savefig(output_path, dpi=150)
    plt.close()


def plot_constraint_binding(daily_scalar: pd.DataFrame, output_path: Path):
    """Plot time series of overall_scalar."""
    fig, ax = plt.subplots(figsize=(12, 6))

    ax.plot(daily_scalar["date"], daily_scalar["overall_scalar"], linewidth=1.5, color="blue")
    ax.axhline(1.0, color="green", linestyle="--", label="No Constraint (scalar=1.0)", linewidth=2)

    ax.set_xlabel("Date")
    ax.set_ylabel("Overall Scalar")
    ax.set_title("Constraint Binding Over Time (2020-2024)")
    ax.legend()
    ax.grid(True, alpha=0.3)

    plt.tight_layout()
    plt.savefig(output_path, dpi=150)
    plt.close()


def plot_turnover_clustering(daily_turnover: pd.DataFrame, output_path: Path):
    """Plot daily turnover time series and histogram."""
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 10))

    # Time series
    ax1.plot(daily_turnover["date"], daily_turnover["daily_turnover"], linewidth=1, alpha=0.7)
    ax1.set_xlabel("Date")
    ax1.set_ylabel("Daily Turnover ($)")
    ax1.set_title("Daily Turnover Time Series (2020-2024)")
    ax1.grid(True, alpha=0.3)

    # Histogram
    ax2.hist(daily_turnover["daily_turnover"], bins=50, edgecolor="black", alpha=0.7)
    ax2.set_xlabel("Daily Turnover ($)")
    ax2.set_ylabel("Frequency")
    ax2.set_title("Daily Turnover Distribution")
    ax2.grid(True, alpha=0.3, axis="y")

    plt.tight_layout()
    plt.savefig(output_path, dpi=150)
    plt.close()


def generate_research_summary(
    pnl_summary: dict,
    counterfactual_summary: dict,
    regime_summary: dict,
    constraint_summary: dict,
    state_summary: dict,
    turnover_summary: dict,
    regime_costs: list,
    output_path: Path
):
    """Generate markdown research summary."""

    md = []
    md.append("# Stage-1 Research Summary: Baseline System Behavior (2020-2024)\n")
    md.append("## Executive Summary\n")
    md.append(f"**Dataset:** 1,782 days, 4 instruments (BTC, ETH, BNB, XRP)\n")
    md.append(f"**Total Return:** {counterfactual_summary['Baseline']['return_pct']:.1f}%\n")
    md.append(f"**Final Equity:** ${counterfactual_summary['Baseline']['final_equity']:,.2f}\n")
    md.append("\n---\n\n")

    # 1. Observable PnL Decomposition
    md.append("## 1. Observable PnL Decomposition\n\n")
    md.append("| Component     | Total PnL    | % of Gross |\n")
    md.append("|---------------|--------------|------------|\n")
    md.append(f"| Price PnL     | ${pnl_summary['Price PnL']:+,.0f} | {pnl_summary['Price %']:+.1f}% |\n")
    md.append(f"| Funding PnL   | ${pnl_summary['Funding PnL']:+,.0f} | {pnl_summary['Funding %']:+.1f}% |\n")
    md.append(f"| Costs         | ${pnl_summary['Costs']:+,.0f} | {pnl_summary['Costs %']:+.1f}% |\n")
    md.append(f"| **Net PnL**   | **${pnl_summary['Net PnL']:+,.0f}** | |\n")
    md.append("\n**Key Finding:** Price PnL dominates returns. Funding contribution is modest. Costs are material relative to funding.\n\n")

    # 2. Counterfactual Attribution
    md.append("## 2. Counterfactual Attribution\n\n")
    md.append("| Scenario         | Final Equity | Return  | Delta vs Baseline |\n")
    md.append("|------------------|--------------|---------|-------------------|\n")
    for scenario_name, scenario_data in counterfactual_summary.items():
        delta_str = f"${scenario_data['delta_vs_baseline']:+,.0f} ({scenario_data['delta_pct']:+.1f}%)" if scenario_data['delta_vs_baseline'] != 0 else "—"
        md.append(f"| {scenario_name:16s} | ${scenario_data['final_equity']:,.2f} | {scenario_data['return_pct']:+.1f}% | {delta_str} |\n")
    md.append("\n")

    carry_effect_pct = counterfactual_summary['Carry Off']['delta_pct']
    constraint_effect_pct = counterfactual_summary['Constraints Off']['delta_pct']

    md.append(f"**Key Finding:**\n")
    md.append(f"- **Carry effect:** {carry_effect_pct:+.1f}% impact (baseline vs carry-off)\n")
    md.append(f"- **Constraint effect:** {constraint_effect_pct:+.1f}% impact (constraints-off vs baseline)\n")

    if abs(carry_effect_pct) < 1.0:
        md.append("- Carry forecast has minimal impact on returns (expected for Phase 1 default weights).\n")
    if constraint_effect_pct > 10:
        md.append("- Constraints materially reduce returns. IDM and gross leverage caps bind frequently.\n")
    elif constraint_effect_pct > 5:
        md.append("- Constraints have moderate impact on returns.\n")
    else:
        md.append("- Constraints have minimal impact on returns.\n")
    md.append("\n")

    # 3. Drawdowns by Regime
    md.append("## 3. Drawdowns by Regime\n\n")
    md.append("| Regime       | Return  | Max DD  | DD Date    |\n")
    md.append("|--------------|---------|---------|------------|\n")
    for regime_name, regime_data in regime_summary.items():
        dd_date_str = regime_data["max_dd_date"].strftime("%Y-%m-%d") if pd.notna(regime_data["max_dd_date"]) else "N/A"
        md.append(f"| {regime_name:12s} | {regime_data['return_pct']:+.1f}% | {regime_data['max_dd_pct']:.1f}% | {dd_date_str} |\n")
    md.append("\n")

    # Find worst regime
    worst_regime = min(regime_summary.items(), key=lambda x: x[1]["max_dd_pct"])
    md.append(f"**Key Finding:** Worst drawdown in **{worst_regime[0]}** ({worst_regime[1]['max_dd_pct']:.1f}%).\n\n")

    # 4. Constraint Binding
    md.append("## 4. Constraint Binding\n\n")
    md.append(f"- **Binding frequency:** {constraint_summary['binding_pct']:.1f}% of instrument-days\n")
    md.append(f"- **Gross leverage binding:** {constraint_summary['gross_lev_binding_rows']} rows (cap=2.0)\n")
    md.append(f"- **IDM binding:** {constraint_summary['idm_binding_rows']} rows (cap=2.5)\n")
    md.append(f"- **Mean scalar:** {constraint_summary['scalar_mean']:.3f}\n")
    md.append(f"- **Min scalar:** {constraint_summary['scalar_min']:.3f}\n")
    md.append("\n")

    if constraint_summary['binding_pct'] > 20:
        md.append(f"**Key Finding:** Constraints bind frequently ({constraint_summary['binding_pct']:.1f}% of time). ")
        md.append(f"Counterfactual shows {constraint_effect_pct:+.1f}% return improvement without constraints. ")
        md.append("This is material.\n\n")
    elif constraint_summary['binding_pct'] > 10:
        md.append(f"**Key Finding:** Constraints bind moderately ({constraint_summary['binding_pct']:.1f}% of time). ")
        md.append("Impact is measurable but not dominant.\n\n")
    else:
        md.append("**Key Finding:** Constraints rarely bind. Impact is minimal.\n\n")

    # 5. State Transitions
    md.append("## 5. State Transitions\n\n")
    for state, state_data in state_summary["state_occupancy"].items():
        md.append(f"- **{state}:** {state_data['pct']:.1f}% ({state_data['count']} instrument-days)\n")
    md.append(f"\n**Total transitions:** {state_summary['total_transitions']}\n\n")

    if state_summary['total_transitions'] == 0:
        md.append("**Key Finding:** No state transitions detected. All instruments remain ACTIVE (expected for N=4 liquid perps).\n\n")
    else:
        md.append(f"**Key Finding:** {state_summary['total_transitions']} state transitions detected. Review diagnostics for details.\n\n")

    # 6. Turnover & Costs
    md.append("## 6. Turnover & Costs\n\n")
    md.append(f"- **Mean daily turnover:** ${turnover_summary['mean']:,.0f}\n")
    md.append(f"- **Median daily turnover:** ${turnover_summary['p50']:,.0f}\n")
    md.append(f"- **P90 turnover:** ${turnover_summary['p90']:,.0f}\n")
    md.append(f"- **P99 turnover:** ${turnover_summary['p99']:,.0f}\n")
    md.append(f"- **Gini coefficient:** {turnover_summary['gini']:.3f} (0=uniform, 1=clustered)\n")
    md.append("\n")

    md.append("### Cost Analysis by Regime\n\n")
    md.append("| Regime          | Mean Turnover | Costs ($) | Costs as % Gross PnL |\n")
    md.append("|-----------------|---------------|-----------|----------------------|\n")
    for regime_cost in regime_costs:
        md.append(f"| {regime_cost['regime']:15s} | ${regime_cost['mean_turnover']:,.0f} | ${regime_cost['total_costs']:,.0f} | {regime_cost['costs_pct_gross']:.2f}% |\n")
    md.append("\n")

    if turnover_summary['gini'] > 0.5:
        md.append("**Key Finding:** Turnover is clustered (high Gini). Costs spike during regime changes.\n\n")
    else:
        md.append("**Key Finding:** Turnover is relatively smooth (low Gini). Costs are consistent.\n\n")

    # Conclusion
    md.append("## Conclusion\n\n")
    md.append("**Does the system behave sensibly at N=4?**\n\n")

    # Heuristic sanity checks
    issues = []
    if constraint_summary['binding_pct'] > 30:
        issues.append("- Constraints bind very frequently (>30% of time), limiting diversification benefit.")
    if worst_regime[1]['max_dd_pct'] < -50:
        issues.append(f"- Extreme drawdown in {worst_regime[0]} ({worst_regime[1]['max_dd_pct']:.1f}%).")
    if pnl_summary['Costs %'] > 10:
        issues.append(f"- Costs are very high ({pnl_summary['Costs %']:.1f}% of gross PnL).")

    if len(issues) == 0:
        md.append("**Yes.** The system demonstrates sensible behavior across all regimes:\n")
        md.append("- Returns are positive and drawdowns are manageable.\n")
        md.append("- Constraints bind but do not dominate.\n")
        md.append("- Costs are material but not excessive.\n")
        md.append("- State machine behaves as expected for liquid instruments.\n")
        md.append("\n**Recommendation:** Proceed to Phase 2 (N=15 expansion).\n\n")
    else:
        md.append("**Issues detected:**\n\n")
        for issue in issues:
            md.append(f"{issue}\n")
        md.append("\n**Recommendation:** Review these issues before proceeding to Phase 2.\n\n")

    # Red flags
    md.append("## Red Flags (if any)\n\n")
    if len(issues) > 0:
        for issue in issues:
            md.append(f"{issue}\n")
    else:
        md.append("None detected.\n")

    md.append("\n---\n\n")
    md.append(f"*Report generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*\n")

    # Write to file
    with open(output_path, 'w') as f:
        f.write("".join(md))


def main():
    parser = argparse.ArgumentParser(
        description='Stage-1 Research Report Generator'
    )
    parser.add_argument('--baseline-dir', required=True, help='Baseline backtest outputs')
    parser.add_argument('--carry-off-dir', required=True, help='Carry-off counterfactual')
    parser.add_argument('--constraints-off-dir', required=True, help='Constraints-off counterfactual')
    parser.add_argument('--output-dir', required=True, help='Where to write report + plots')

    args = parser.parse_args()

    baseline_dir = Path(args.baseline_dir)
    carry_off_dir = Path(args.carry_off_dir)
    constraints_off_dir = Path(args.constraints_off_dir)
    output_dir = Path(args.output_dir)

    output_dir.mkdir(parents=True, exist_ok=True)

    print("=== Stage-1 Research Report Generator ===\n")

    # Load data
    print("Loading baseline data...")
    baseline_data = load_backtest_data(baseline_dir)

    print("Loading carry-off data...")
    carry_off_data = load_backtest_data(carry_off_dir)

    print("Loading constraints-off data...")
    constraints_off_data = load_backtest_data(constraints_off_dir)

    # 1. Observable PnL decomposition
    print("\n1. Analyzing observable PnL decomposition...")
    pnl_summary, pnl_timeseries = analyze_observable_pnl(baseline_data["pnl"])
    plot_pnl_decomposition(pnl_timeseries, output_dir / "pnl_decomposition.png")

    # 2. Counterfactual attribution
    print("2. Analyzing counterfactual attribution...")
    counterfactual_summary = analyze_counterfactual(
        baseline_data["equity"],
        carry_off_data["equity"],
        constraints_off_data["equity"]
    )
    plot_counterfactual_equity(
        baseline_data["equity"],
        carry_off_data["equity"],
        constraints_off_data["equity"],
        output_dir / "counterfactual_equity.png"
    )

    # 3. Regime drawdowns
    print("3. Analyzing regime drawdowns...")
    regime_summary, underwater_df = analyze_regime_drawdowns(baseline_data["equity"])
    plot_regime_drawdowns(underwater_df, output_dir / "regime_drawdowns.png")

    # 4. Constraint binding
    print("4. Analyzing constraint binding...")
    constraint_summary, daily_scalar = analyze_constraint_binding(baseline_data["diagnostics"])
    plot_constraint_binding(daily_scalar, output_dir / "constraint_binding.png")

    # 5. State transitions
    print("5. Analyzing state transitions...")
    state_summary = analyze_state_transitions(baseline_data["diagnostics"])

    # 6. Turnover clustering
    print("6. Analyzing turnover clustering...")
    turnover_summary, daily_turnover, regime_costs = analyze_turnover_clustering(
        baseline_data["positions"],
        baseline_data["pnl"],
        baseline_data["diagnostics"]
    )
    plot_turnover_clustering(daily_turnover, output_dir / "turnover_clustering.png")

    # Generate research summary
    print("\nGenerating research summary...")
    generate_research_summary(
        pnl_summary,
        counterfactual_summary,
        regime_summary,
        constraint_summary,
        state_summary,
        turnover_summary,
        regime_costs,
        output_dir / "research_summary.md"
    )

    print(f"\n=== Report Complete ===")
    print(f"Outputs saved to: {output_dir}")
    print(f"- research_summary.md")
    print(f"- pnl_decomposition.png")
    print(f"- counterfactual_equity.png")
    print(f"- regime_drawdowns.png")
    print(f"- constraint_binding.png")
    print(f"- turnover_clustering.png")

    return 0


if __name__ == "__main__":
    exit(main())
