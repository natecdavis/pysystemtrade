#!/usr/bin/env python3
"""
Advisory Report Generator - Human-Readable Terminal Report

Generates formatted terminal report from live advisory outputs with prominent
monthly cadence warnings and all key diagnostics.

Usage:
    python reports/advisory_report.py \
        --advisory-dir out/live_advisory_20260128 \
        --output out/live_advisory_20260128/advisory_report.txt
"""

import argparse
import sys
from pathlib import Path
import json
import pandas as pd
from datetime import datetime
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def format_trade_table(trade_plan: pd.DataFrame, max_rows: int = 20) -> str:
    """Format trade plan as ASCII table."""
    lines = []

    # Header
    lines.append("")
    lines.append("Priority  Instrument        Current    Target     Delta     Cost    State    Warnings")
    lines.append("--------  ----------------  ---------  ---------  --------  ------  -------  ---------")

    # Rows (show top N by priority)
    for idx, row in trade_plan.head(max_rows).iterrows():
        priority = int(row['priority'])
        inst = idx[:16].ljust(16)  # Truncate instrument name
        current = f"${row['current_notional']:>8.2f}" if abs(row['current_notional']) < 10000 else f"${row['current_notional']:>7.0f}"
        target = f"${row['target_notional']:>8.2f}" if abs(row['target_notional']) < 10000 else f"${row['target_notional']:>7.0f}"
        delta = f"{row['delta_notional']:>+8.2f}" if abs(row['delta_notional']) < 10000 else f"{row['delta_notional']:>+7.0f}"
        cost = f"${row['estimated_cost']:>5.2f}"
        state = str(row['state'])[:7].ljust(7)
        warnings = str(row.get('warnings', ''))[:20]  # Truncate warnings

        lines.append(f"   {priority:>2}      {inst}  {current}  {target}  {delta}  {cost}  {state}  {warnings}")

    if len(trade_plan) > max_rows:
        lines.append(f"   ... {len(trade_plan) - max_rows} more trades (see trade_plan.csv for full list)")

    lines.append("")
    return "\n".join(lines)


def generate_report(advisory_dir: Path) -> str:
    """
    Generate human-readable advisory report.

    Args:
        advisory_dir: Path to advisory output directory

    Returns:
        Formatted report string
    """
    lines = []

    # Header
    lines.append("=" * 80)
    lines.append("MONTHLY TRADING ADVISORY")
    lines.append("=" * 80)

    # Load outputs
    try:
        # Find trade plan (may have date suffix)
        trade_plan_files = list(advisory_dir.glob('trade_plan_*.csv'))
        if not trade_plan_files:
            raise FileNotFoundError("No trade plan found")
        trade_plan_path = trade_plan_files[0]
        trade_plan = pd.read_csv(trade_plan_path, index_col=0)

        # Extract date from filename
        as_of_date = trade_plan_path.stem.replace('trade_plan_', '')

        # Load sanity checks
        sanity_checks_files = list(advisory_dir.glob('sanity_checks_*.json'))
        if not sanity_checks_files:
            raise FileNotFoundError("No sanity checks found")
        with open(sanity_checks_files[0]) as f:
            sanity_checks = json.load(f)

        # Load audit bundle
        audit_bundle_files = list(advisory_dir.glob('audit_bundle_*.json'))
        if not audit_bundle_files:
            raise FileNotFoundError("No audit bundle found")
        with open(audit_bundle_files[0]) as f:
            audit_bundle = json.load(f)

        # Load data status (optional)
        data_status_path = advisory_dir / 'raw_data_status.json'
        if data_status_path.exists():
            with open(data_status_path) as f:
                data_status = json.load(f)
        else:
            data_status = None

    except Exception as e:
        return f"Error loading advisory outputs: {e}"

    # Timestamp
    timestamp = datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')
    lines.append(f"Generated: {timestamp}")
    lines.append(f"As of date: {as_of_date}")
    lines.append("")

    # CRITICAL WARNINGS (prominent)
    lines.append("⚠ CRITICAL: MONTHLY ADVISORY SYSTEM (NOT DAILY)")
    lines.append("  - Binance Vision publication lag: ~2-4 weeks after month end")
    lines.append("  - Targets based on data through: " + audit_bundle.get('last_complete_bar_date', 'unknown'))
    lines.append("  - Data lag: " + str(audit_bundle.get('data_lag_days', '?')) + " days")
    lines.append("  - Do NOT use for intraday decisions - monthly cadence only")
    lines.append("")

    # System info
    lines.append("System: " + audit_bundle.get('system_version', 'unknown'))
    config_hash = audit_bundle.get('backtest_metadata', {}).get('config_hash', 'unknown')
    lines.append(f"Config: {config_hash}")
    git_commit = audit_bundle.get('backtest_metadata', {}).get('git_commit', 'unknown')
    lines.append(f"Git commit: {git_commit[:8]}")
    lines.append("")

    # Equity info
    equity_info = sanity_checks.get('equity_info', audit_bundle.get('equity_info', {}))
    current_equity = equity_info.get('current_equity_usd', sanity_checks.get('current_equity', 0))
    initial_capital = equity_info.get('initial_capital_usd', sanity_checks.get('initial_capital', 0))
    pnl_pct = equity_info.get('total_pnl_pct', sanity_checks.get('equity_pnl_pct', 0))

    lines.append(f"Current Equity: ${current_equity:,.2f} ({pnl_pct:+.2%} from initial ${initial_capital:,.2f})")
    lines.append("")

    # RECOMMENDED TRADES
    lines.append("--- RECOMMENDED TRADES ---")
    lines.append(format_trade_table(trade_plan))

    # Trade summary
    total_trades = len(trade_plan)
    trades_above_min = len(trade_plan[~trade_plan['warnings'].astype(str).str.contains('below_min_trade_size', na=False)])
    total_cost = trade_plan['estimated_cost'].sum()
    cost_pct = sanity_checks['checks'].get('cost_as_pct_of_equity', 0)

    lines.append(f"Total Trades: {total_trades}")
    lines.append(f"Trades Above Min Size: {trades_above_min}")
    lines.append(f"Total Estimated Cost: ${total_cost:.2f} ({cost_pct:.2%} of equity)")
    lines.append("⚠ Costs are ESTIMATED - verify live spreads before executing")
    lines.append("")

    # SANITY CHECKS
    lines.append("--- SANITY CHECKS ---")

    # Gross leverage
    gross_lev = sanity_checks['checks']['gross_leverage']
    status_icon = '✓' if gross_lev['status'] == 'pass' else '✗'
    lines.append(f"{status_icon} Gross Leverage: {gross_lev['after_trades']:.2f} / {gross_lev['cap']:.2f} cap "
                 f"(headroom: {gross_lev['headroom']:.2f}) [{gross_lev['note']}]")

    # IDM
    idm = sanity_checks['checks']['idm_target_portfolio']
    if idm['value'] is not None:
        status_icon = '✓' if idm['status'] == 'pass' else '✗'
        lines.append(f"{status_icon} IDM (target portfolio): {idm['value']:.2f} / {idm['cap']:.2f} cap "
                     f"(headroom: {idm['headroom']:.2f})")
    else:
        lines.append("⚠ IDM: Not available in diagnostics")

    # Min position sizes
    min_sizes = sanity_checks['checks']['min_position_sizes']
    status_icon = '✓' if min_sizes['status'] == 'pass' else '⚠'
    below_count = len(min_sizes['below_threshold'])
    lines.append(f"{status_icon} Min Position Sizes: {below_count} trade(s) below threshold "
                 f"(${min_sizes['threshold_usd']:.2f})")

    # Banned instruments
    banned = sanity_checks['checks']['banned_instruments']
    status_icon = '✓' if banned['status'] == 'pass' else '⚠'
    lines.append(f"{status_icon} Banned Instruments: {banned['count']}")

    # Instrument states
    states = sanity_checks['checks']['instrument_states']
    active = states.get('ACTIVE', 0)
    ineligible = states.get('INELIGIBLE_HOLD', 0)
    banned_count = states.get('BANNED_FLATTEN', 0)
    lines.append(f"✓ Instrument States: {active} ACTIVE, {ineligible} INELIGIBLE_HOLD, {banned_count} BANNED_FLATTEN")
    lines.append("")

    # KEY DIAGNOSTICS
    lines.append("--- KEY DIAGNOSTICS ---")

    # Universe size
    forecasts = audit_bundle.get('forecasts_snapshot', {})
    lines.append(f"Universe Size: {len(forecasts)} instruments")
    lines.append(f"Active Instruments: {states.get('ACTIVE', 0)}")
    lines.append("")

    # Top forecasts
    if forecasts:
        lines.append("Top Forecasts (as of " + audit_bundle.get('last_complete_bar_date', 'unknown') + "):")
        # Sort by combined forecast (if available)
        forecast_items = []
        for inst, inst_forecasts in forecasts.items():
            combined = inst_forecasts.get('combined_forecast', inst_forecasts.get('combined', 0))
            forecast_items.append((inst, combined, inst_forecasts))

        forecast_items.sort(key=lambda x: abs(x[1]), reverse=True)

        for i, (inst, combined, inst_forecasts) in enumerate(forecast_items[:5], 1):
            # Format sub-forecasts
            sub_forecasts = []
            for key, val in inst_forecasts.items():
                if key not in ['combined_forecast', 'combined']:
                    sub_forecasts.append(f"{key}: {val:+.1f}")
            sub_str = ", ".join(sub_forecasts[:3])  # Show first 3
            lines.append(f"  {i}. {inst}: {combined:+.1f} ({sub_str})")
    lines.append("")

    # DATA STATUS
    lines.append("--- DATA STATUS ---")

    if data_status:
        lines.append(f"Raw Data Updated: {data_status.get('as_of_date', 'unknown')}")
        lines.append(f"Expected Last Month: {data_status.get('expected_last_month', 'unknown')} "
                     f"(conservative, M-{data_status.get('lag_policy_months', 2)})")

        summary = data_status.get('summary', {})
        up_to_date = summary.get('up_to_date', 0)
        lagging = summary.get('lagging', 0)
        missing = summary.get('missing_data', 0)
        max_lag = summary.get('max_lag_days', 0)

        if missing > 0:
            lines.append(f"✗ Missing Data: {missing} instrument(s) - CRITICAL ERROR")
        elif lagging > 0:
            lines.append(f"⚠ Lagging: {lagging} instrument(s) (max lag: {max_lag} days)")
        else:
            lines.append(f"✓ All Instruments Up to Date: {up_to_date}/{up_to_date + lagging}")
    else:
        lines.append("⚠ Data status report not found")

    # Dataset info
    backtest_meta = audit_bundle.get('backtest_metadata', {})
    dataset_path = backtest_meta.get('dataset_path', 'unknown')
    dataset_range = backtest_meta.get('dataset_date_range', ['?', '?'])

    lines.append("")
    lines.append(f"Dataset: {dataset_path}")
    lines.append(f"Date Range: {dataset_range[0]} to {dataset_range[1]}")
    lines.append("")

    # WARNINGS
    if sanity_checks.get('warnings'):
        lines.append("--- WARNINGS ---")
        for warning in sanity_checks['warnings']:
            lines.append(f"⚠ {warning}")
        lines.append("")

    # Footer
    lines.append("=" * 80)
    lines.append("⚠ CRITICAL REMINDERS:")
    lines.append("  - This advisory uses data through " + audit_bundle.get('last_complete_bar_date', 'unknown'))
    lines.append("  - Binance Vision publication lag means data can be 30-60 days stale")
    lines.append("  - Do NOT use for intraday decisions - monthly cadence only")
    lines.append("  - Verify live prices before executing trades")
    lines.append("  - Update current_positions.csv and current_equity.txt after execution")
    lines.append("")
    lines.append("Action Items:")
    lines.append(f"  1. Review trade_plan_{as_of_date}.csv")
    lines.append("  2. Verify live spreads and prices on exchange")
    lines.append("  3. Execute trades manually on exchange")
    lines.append("  4. Update live/current_positions.csv with actual fills")
    lines.append("  5. Update live/current_equity.txt with actual P&L")
    lines.append("")
    lines.append("Next Advisory Run: After next month's Binance data is published")
    expected_next = data_status.get('expected_last_month', 'unknown') if data_status else 'unknown'
    lines.append(f"  (Expected: ~1st week of next month for {expected_next} data)")
    lines.append("=" * 80)

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description='Generate human-readable advisory report',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    parser.add_argument(
        '--advisory-dir',
        type=Path,
        required=True,
        help='Path to advisory output directory'
    )
    parser.add_argument(
        '--output',
        type=Path,
        help='Output file path (default: {advisory-dir}/advisory_report.txt)'
    )

    args = parser.parse_args()

    # Validate input
    if not args.advisory_dir.exists():
        logger.error(f"Advisory directory not found: {args.advisory_dir}")
        sys.exit(1)

    # Generate report
    try:
        report = generate_report(args.advisory_dir)

        # Determine output path
        if args.output:
            output_path = args.output
        else:
            output_path = args.advisory_dir / 'advisory_report.txt'

        # Write report
        with open(output_path, 'w') as f:
            f.write(report)

        # Also print to stdout
        print(report)

        logger.info(f"\n✓ Advisory report written to {output_path}")
        sys.exit(0)

    except Exception as e:
        logger.error(f"✗ Report generation failed: {e}", exc_info=True)
        sys.exit(1)


if __name__ == '__main__':
    main()
