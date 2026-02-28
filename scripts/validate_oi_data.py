#!/usr/bin/env python
"""
Validate Binance OI data quality for Phase 2 implementation.

Checks:
1. Coverage: % of backtest instruments with OI data (target >= 80%)
2. Gaps: maximum consecutive missing days per instrument (target <= 3)
3. Signal quality: OI z-score during known crash events (June 2022, Nov 2022)
4. Sanity checks: no negative OI, no sudden implausible jumps

Usage:
    python scripts/validate_oi_data.py \
        --oi-data data/binance_oi_processed.parquet \
        --backtest-data data/dataset_538registry_6yr_jagged.parquet \
        --output data/oi_data_quality_report.json
"""

import argparse
import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Known crash events (from Phase 1 analysis)
CRASH_EVENTS = {
    'june_2022_3ac': {
        'label': 'June 2022 (3AC/Celsius)',
        'pre_crash_start': '2022-05-01',
        'crash_start': '2022-06-13',
        'crash_end': '2022-06-18',
    },
    'nov_2022_ftx': {
        'label': 'Nov 2022 (FTX Collapse)',
        'pre_crash_start': '2022-10-01',
        'crash_start': '2022-11-08',
        'crash_end': '2022-11-10',
    },
}


def load_data(oi_path: str, backtest_path: str):
    logger.info(f"Loading OI data from {oi_path}")
    oi = pd.read_parquet(oi_path)
    oi['date'] = pd.to_datetime(oi['date']).dt.normalize()

    logger.info(f"Loading backtest data from {backtest_path}")
    bt = pd.read_parquet(backtest_path)
    bt['date'] = pd.to_datetime(bt['date']).dt.normalize()

    return oi, bt


def check_coverage(oi: pd.DataFrame, bt: pd.DataFrame) -> dict:
    """Check what % of backtest instruments have OI data."""
    logger.info("--- CHECK 1: Coverage ---")

    # Normalise names: backtest uses BTCUSDT_PERP, OI data uses BTCUSDT
    def strip_suffix(name):
        return name.replace('_PERP', '')

    bt_instruments_raw = set(bt['instrument'].unique())
    bt_instruments = {strip_suffix(i) for i in bt_instruments_raw}
    oi_instruments = set(oi['instrument'].unique())

    covered = bt_instruments & oi_instruments
    missing = bt_instruments - oi_instruments

    pct = len(covered) / len(bt_instruments) * 100

    logger.info(f"Backtest instruments: {len(bt_instruments)}")
    logger.info(f"OI instruments:       {len(oi_instruments)}")
    logger.info(f"Covered:              {len(covered)} ({pct:.1f}%)")
    logger.info(f"Missing OI:           {len(missing)}")
    if missing:
        logger.info(f"  Missing: {sorted(missing)}")

    # Per-instrument date coverage
    bt_date_range = (bt['date'].max() - bt['date'].min()).days + 1
    oi_counts = oi[oi['instrument'].isin(bt_instruments)].groupby('instrument')['date'].count()
    avg_days = oi_counts.mean()
    median_days = oi_counts.median()

    logger.info(f"Avg OI days per instrument: {avg_days:.0f} / {bt_date_range} ({avg_days/bt_date_range*100:.1f}%)")
    logger.info(f"Median OI days:             {median_days:.0f}")

    # Instruments with very low coverage (<30%)
    low_coverage = oi_counts[oi_counts < bt_date_range * 0.3]
    logger.info(f"Instruments with <30% coverage: {len(low_coverage)}")

    passed = pct >= 80.0
    logger.info(f"RESULT: {'PASS' if passed else 'FAIL'} (target >= 80%, got {pct:.1f}%)")

    return {
        'bt_instruments': len(bt_instruments),
        'oi_instruments': len(oi_instruments),
        'covered': len(covered),
        'missing': sorted(missing),
        'coverage_pct': round(pct, 2),
        'avg_days_per_instrument': round(float(avg_days), 1),
        'median_days_per_instrument': round(float(median_days), 1),
        'low_coverage_count': len(low_coverage),
        'passed': passed,
    }


def check_gaps(oi: pd.DataFrame, bt_instruments: set) -> dict:
    """Check for consecutive missing days per instrument."""
    logger.info("--- CHECK 2: Gap Analysis ---")

    oi_bt = oi[oi['instrument'].isin(bt_instruments)].copy()

    max_gaps = {}
    instruments_with_large_gaps = []

    for instrument, grp in oi_bt.groupby('instrument'):
        dates = pd.to_datetime(grp['date']).sort_values().reset_index(drop=True)
        if len(dates) < 2:
            continue
        diffs = dates.diff().dt.days.dropna()
        max_gap = int(diffs.max()) - 1  # gap = days between dates - 1
        max_gaps[instrument] = max_gap
        if max_gap > 3:
            instruments_with_large_gaps.append((instrument, max_gap))

    instruments_with_large_gaps.sort(key=lambda x: -x[1])

    all_max = max(max_gaps.values()) if max_gaps else 0
    avg_max = np.mean(list(max_gaps.values())) if max_gaps else 0
    pct_ok = sum(1 for g in max_gaps.values() if g <= 3) / len(max_gaps) * 100 if max_gaps else 0

    logger.info(f"Largest single gap: {all_max} days")
    logger.info(f"Avg max gap per instrument: {avg_max:.1f} days")
    logger.info(f"Instruments with gap <= 3 days: {pct_ok:.1f}%")
    if instruments_with_large_gaps[:5]:
        logger.info(f"Worst gaps: {instruments_with_large_gaps[:5]}")

    passed = pct_ok >= 90.0
    logger.info(f"RESULT: {'PASS' if passed else 'FAIL'} (target >= 90% instruments with gap <= 3d)")

    return {
        'largest_gap_days': all_max,
        'avg_max_gap_days': round(float(avg_max), 1),
        'pct_instruments_gap_ok': round(pct_ok, 1),
        'instruments_with_large_gaps': instruments_with_large_gaps[:20],
        'passed': passed,
    }


def check_signal_quality(oi: pd.DataFrame, bt_instruments: set) -> dict:
    """Check OI z-score behaviour around known crash events."""
    logger.info("--- CHECK 3: Signal Quality (Crash Events) ---")

    # Focus on major liquid instruments available for full period
    core_instruments = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'ADAUSDT']
    core_available = [i for i in core_instruments if i in set(oi['instrument'].unique())]

    results = {}

    for event_key, event in CRASH_EVENTS.items():
        logger.info(f"\n  Event: {event['label']}")

        pre_start = pd.Timestamp(event['pre_crash_start'])
        crash_start = pd.Timestamp(event['crash_start'])
        crash_end = pd.Timestamp(event['crash_end'])

        event_results = {}

        for instrument in core_available:
            inst_oi = oi[oi['instrument'] == instrument].set_index('date')['open_interest'].sort_index()

            # Need data covering the pre-crash window
            if inst_oi.index.min() > pre_start:
                continue

            # Compute 90-day rolling z-score
            window = 90
            roll_mean = inst_oi.rolling(window, min_periods=30).mean()
            roll_std = inst_oi.rolling(window, min_periods=30).std()
            z = ((inst_oi - roll_mean) / roll_std.clip(lower=1e-8))

            # Pre-crash peak z-score (30 days before crash)
            pre_window = z.loc[pre_start:crash_start]
            crash_window = z.loc[crash_start:crash_end + pd.Timedelta(days=7)]

            if pre_window.empty or crash_window.empty:
                continue

            pre_peak_z = float(pre_window.max())
            pre_peak_date = str(pre_window.idxmax().date())
            crash_peak_z = float(crash_window.max())

            # Did OI z-score exceed 1.5 before the crash? (elevated leverage signal)
            elevated_before = pre_peak_z > 1.5
            leads_crash = pre_peak_z > 0.5  # Some elevation before crash

            logger.info(f"    {instrument}: pre-crash peak z={pre_peak_z:.2f} on {pre_peak_date}, "
                        f"crash peak z={crash_peak_z:.2f}, elevated={elevated_before}")

            event_results[instrument] = {
                'pre_crash_peak_z': round(pre_peak_z, 3),
                'pre_crash_peak_date': pre_peak_date,
                'crash_peak_z': round(crash_peak_z, 3),
                'elevated_before_crash': bool(elevated_before),
                'leads_crash': bool(leads_crash),
            }

        results[event_key] = {
            'label': event['label'],
            'instruments_analyzed': len(event_results),
            'elevated_count': sum(1 for r in event_results.values() if r['elevated_before_crash']),
            'per_instrument': event_results,
        }

        if event_results:
            elev = results[event_key]['elevated_count']
            total = results[event_key]['instruments_analyzed']
            logger.info(f"  -> {elev}/{total} instruments showed elevated OI (z>1.5) before crash")

    # Overall signal quality pass: at least 1 out of 2 events had elevated OI
    events_with_signal = sum(
        1 for e in results.values()
        if e['instruments_analyzed'] > 0 and e['elevated_count'] >= 1
    )
    passed = events_with_signal >= 1
    logger.info(f"\nRESULT: {'PASS' if passed else 'FAIL'} "
                f"({events_with_signal}/2 crash events showed elevated OI signal)")

    return {
        'events_with_signal': events_with_signal,
        'events': results,
        'passed': passed,
    }


def check_sanity(oi: pd.DataFrame) -> dict:
    """Basic sanity checks: no negatives, no extreme jumps."""
    logger.info("--- CHECK 4: Sanity Checks ---")

    negative_oi = (oi['open_interest'] < 0).sum()
    zero_oi = (oi['open_interest'] == 0).sum()

    # Check for extreme day-over-day jumps (>10x in a single day)
    # Treat zeros as NaN to avoid spurious transitions from missing data
    extreme_jumps = 0
    for instrument, grp in oi.groupby('instrument'):
        oi_series = grp.set_index('date')['open_interest'].sort_index()
        oi_series = oi_series.replace(0, np.nan)
        pct_change = oi_series.pct_change().abs()
        extreme_jumps += (pct_change > 10).sum()

    logger.info(f"Negative OI values:   {negative_oi}")
    logger.info(f"Zero OI values:       {zero_oi} ({zero_oi/len(oi)*100:.2f}%)")
    logger.info(f"Extreme jumps (>10x): {extreme_jumps}")

    passed = negative_oi == 0 and extreme_jumps < 50
    logger.info(f"RESULT: {'PASS' if passed else 'FAIL'}")

    return {
        'negative_oi_count': int(negative_oi),
        'zero_oi_count': int(zero_oi),
        'zero_oi_pct': round(float(zero_oi / len(oi) * 100), 2),
        'extreme_jumps': int(extreme_jumps),
        'passed': passed,
    }


def main():
    parser = argparse.ArgumentParser(description='Validate OI data quality')
    parser.add_argument('--oi-data', default='data/binance_oi_processed.parquet')
    parser.add_argument('--backtest-data', default='data/dataset_538registry_6yr_jagged.parquet')
    parser.add_argument('--output', default='data/oi_data_quality_report.json')
    args = parser.parse_args()

    oi, bt = load_data(args.oi_data, args.backtest_data)
    # Normalise: backtest uses BTCUSDT_PERP suffix, OI data uses BTCUSDT
    bt_instruments = {i.replace('_PERP', '') for i in bt['instrument'].unique()}

    logger.info("=" * 60)
    logger.info("OI DATA QUALITY VALIDATION")
    logger.info("=" * 60)
    logger.info(f"OI rows: {len(oi):,} | Instruments: {oi['instrument'].nunique()}")
    logger.info(f"OI date range: {oi['date'].min().date()} to {oi['date'].max().date()}")
    logger.info(f"Backtest instruments: {len(bt_instruments)}")

    coverage = check_coverage(oi, bt)
    gaps = check_gaps(oi, bt_instruments)
    signal = check_signal_quality(oi, bt_instruments)
    sanity = check_sanity(oi)

    # Overall verdict
    all_passed = all([coverage['passed'], gaps['passed'], signal['passed'], sanity['passed']])

    logger.info("\n" + "=" * 60)
    logger.info("VALIDATION SUMMARY")
    logger.info("=" * 60)
    logger.info(f"  Coverage:       {'PASS' if coverage['passed'] else 'FAIL'} "
                f"({coverage['coverage_pct']}% of backtest instruments)")
    logger.info(f"  Gaps:           {'PASS' if gaps['passed'] else 'FAIL'} "
                f"({gaps['pct_instruments_gap_ok']}% instruments with gap <= 3d)")
    logger.info(f"  Signal quality: {'PASS' if signal['passed'] else 'FAIL'} "
                f"({signal['events_with_signal']}/2 crash events with elevated OI)")
    logger.info(f"  Sanity:         {'PASS' if sanity['passed'] else 'FAIL'}")
    logger.info(f"\nOVERALL: {'PASS - Ready for Phase 2 implementation' if all_passed else 'FAIL - Review issues above'}")

    report = {
        'overall_passed': all_passed,
        'coverage': coverage,
        'gaps': gaps,
        'signal_quality': signal,
        'sanity': sanity,
    }

    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, 'w') as f:
        json.dump(report, f, indent=2, default=str)
    logger.info(f"\nReport saved to {args.output}")


if __name__ == '__main__':
    main()
