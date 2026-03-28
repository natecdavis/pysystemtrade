#!/usr/bin/env python
"""
Diagnose OI overlay behavior during crash events.

Investigates why the OI overlay makes crash performance worse by analyzing:
- Funding rates and z-scores during crashes
- Position changes (baseline vs OI overlay)
- Timing of overlay triggers
- Impact on P&L
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
import numpy as np
import json
from sysdata.crypto.parquet_perps_sim_data import parquetCryptoPerpsSimData


def analyze_crash_event(
    data: parquetCryptoPerpsSimData,
    baseline_positions: pd.DataFrame,
    combined_positions: pd.DataFrame,
    event_name: str,
    start_date: str,
    end_date: str,
    lookback: int = 90,
    threshold: float = 2.0,
):
    """
    Analyze what happened during a specific crash event.

    Args:
        data: Data object with funding rates
        baseline_positions: Positions without OI overlay
        combined_positions: Positions with OI overlay
        event_name: Name of crash event
        start_date: Start of crash window
        end_date: End of crash window
        lookback: OI overlay lookback parameter
        threshold: OI overlay threshold parameter
    """
    print("=" * 80)
    print(f"{event_name}: {start_date} to {end_date}")
    print("=" * 80)

    # Get top instruments by position size (to focus analysis)
    total_baseline = baseline_positions.abs().sum(axis=0).sort_values(ascending=False)
    top_instruments = total_baseline.head(10).index.tolist()

    print(f"\nAnalyzing top 10 instruments by position size:")
    print(f"  {', '.join(top_instruments)}")

    # Expand window to include pre-crash period for context
    window_start = pd.Timestamp(start_date) - pd.Timedelta(days=30)
    window_end = pd.Timestamp(end_date) + pd.Timedelta(days=7)

    # Analyze each instrument
    for instrument in top_instruments:
        print(f"\n{'-' * 80}")
        print(f"Instrument: {instrument}")
        print(f"{'-' * 80}")

        try:
            # Get funding rate and calculate z-score
            funding = data.get_funding_rate(instrument)
            funding_ann = funding * 3 * 365

            # Filter to window
            funding_window = funding_ann[window_start:window_end]

            if len(funding_window) < 10:
                print(f"  Insufficient funding data")
                continue

            # Calculate z-score (same logic as OI overlay)
            rolling_mean = funding_ann.rolling(lookback, min_periods=30).mean()
            rolling_std = funding_ann.rolling(lookback, min_periods=30).std()
            rolling_std = rolling_std.replace(0.0, 0.01)
            z_score = (funding_ann - rolling_mean) / rolling_std
            z_abs = z_score.abs()

            # Calculate multiplier
            sensitivity = 0.5 / threshold  # (1.0 - 0.5) / threshold
            multiplier = 1.0 - (z_abs - threshold) * sensitivity
            multiplier = multiplier.clip(lower=0.5, upper=1.0).fillna(1.0)

            # Filter to window
            z_window = z_score[window_start:window_end]
            mult_window = multiplier[window_start:window_end]

            # Get positions
            baseline_pos = baseline_positions[instrument][window_start:window_end]
            combined_pos = combined_positions[instrument][window_start:window_end]

            # Get prices for P&L calculation
            prices = data.daily_prices(instrument)[window_start:window_end]

            # Align all series
            df = pd.DataFrame({
                'funding_ann': funding_window,
                'z_score': z_window,
                'oi_mult': mult_window,
                'baseline_pos': baseline_pos,
                'combined_pos': combined_pos,
                'price': prices,
            })

            df = df.dropna(subset=['price'])

            if len(df) < 5:
                print(f"  Insufficient data after alignment")
                continue

            # Calculate daily returns
            df['price_ret'] = df['price'].pct_change()

            # Calculate position P&L (position[t-1] * return[t])
            df['baseline_pnl'] = df['baseline_pos'].shift(1) * df['price_ret'] * df['price']
            df['combined_pnl'] = df['combined_pos'].shift(1) * df['price_ret'] * df['price']

            # Focus on crash period
            crash_df = df[start_date:end_date]

            if len(crash_df) == 0:
                print(f"  No data in crash period")
                continue

            print(f"\nPre-Crash Context (7 days before):")
            pre_crash = df[:start_date].tail(7)
            if len(pre_crash) > 0:
                print(f"  Avg funding: {pre_crash['funding_ann'].mean():.2%} p.a.")
                print(f"  Avg z-score: {pre_crash['z_score'].mean():.2f}σ")
                print(f"  Avg OI mult: {pre_crash['oi_mult'].mean():.3f}")
                print(f"  Avg baseline pos: {pre_crash['baseline_pos'].mean():.2f}")
                print(f"  Avg combined pos: {pre_crash['combined_pos'].mean():.2f}")

            print(f"\nDuring Crash ({len(crash_df)} days):")
            print(f"  Funding range: {crash_df['funding_ann'].min():.2%} to {crash_df['funding_ann'].max():.2%} p.a.")
            print(f"  Z-score range: {crash_df['z_score'].min():.2f}σ to {crash_df['z_score'].max():.2f}σ")
            print(f"  OI mult range: {crash_df['oi_mult'].min():.3f} to {crash_df['oi_mult'].max():.3f}")
            print(f"  Avg OI mult: {crash_df['oi_mult'].mean():.3f}")

            print(f"\n  Position Changes:")
            print(f"    Baseline: {pre_crash['baseline_pos'].mean():.2f} → {crash_df['baseline_pos'].mean():.2f}")
            print(f"    Combined: {pre_crash['combined_pos'].mean():.2f} → {crash_df['combined_pos'].mean():.2f}")
            print(f"    Δ (overlay effect): {(crash_df['combined_pos'].mean() - crash_df['baseline_pos'].mean()):.2f}")

            print(f"\n  Price Movement:")
            price_start = crash_df['price'].iloc[0]
            price_end = crash_df['price'].iloc[-1]
            price_ret_total = (price_end / price_start) - 1
            print(f"    Start: ${price_start:.2f}")
            print(f"    End: ${price_end:.2f}")
            print(f"    Total return: {price_ret_total:.2%}")

            print(f"\n  P&L Impact:")
            baseline_pnl_total = crash_df['baseline_pnl'].sum()
            combined_pnl_total = crash_df['combined_pnl'].sum()
            pnl_diff = combined_pnl_total - baseline_pnl_total
            print(f"    Baseline P&L: ${baseline_pnl_total:.2f}")
            print(f"    Combined P&L: ${combined_pnl_total:.2f}")
            print(f"    Δ (overlay effect): ${pnl_diff:.2f}")

            # Daily breakdown
            print(f"\n  Day-by-Day Breakdown:")
            print(f"  {'Date':<12} {'Price':>8} {'Ret%':>7} {'Z-score':>8} {'Mult':>6} {'Δ Pos':>7} {'Δ P&L':>9}")
            print(f"  {'-' * 80}")

            for date, row in crash_df.iterrows():
                delta_pos = row['combined_pos'] - row['baseline_pos']
                delta_pnl = row['combined_pnl'] - row['baseline_pnl']
                print(f"  {date.strftime('%Y-%m-%d'):<12} ${row['price']:>7.2f} {row['price_ret']*100:>6.1f}% "
                      f"{row['z_score']:>7.2f}σ {row['oi_mult']:>5.3f} {delta_pos:>6.1f} ${delta_pnl:>8.2f}")

            # Check if overlay triggered (mult < 1.0) during crash
            triggered = (crash_df['oi_mult'] < 1.0).any()
            if triggered:
                trigger_days = (crash_df['oi_mult'] < 1.0).sum()
                print(f"\n  ⚠️ OI overlay TRIGGERED on {trigger_days}/{len(crash_df)} days")
                print(f"     (Funding z-score exceeded {threshold}σ threshold)")
            else:
                print(f"\n  ✓ OI overlay DID NOT trigger (z-score stayed below {threshold}σ)")

            # Determine if the overlay made things worse
            if pnl_diff < -1.0:  # More than $1 worse
                print(f"\n  ❌ OVERLAY MADE THINGS WORSE by ${-pnl_diff:.2f}")

                # Diagnose why
                if triggered:
                    print(f"     Reason: Overlay reduced positions when we should have kept them")
                    if price_ret_total < 0 and crash_df['baseline_pos'].mean() < 0:
                        print(f"     Detail: We were SHORT (good), price fell (profit opportunity)")
                        print(f"             But overlay reduced our short → missed profits")
                    elif price_ret_total > 0 and crash_df['baseline_pos'].mean() > 0:
                        print(f"     Detail: We were LONG (bad), price rose (would make money)")
                        print(f"             Overlay reduced our long → reduced profits")
                    elif price_ret_total < 0 and crash_df['baseline_pos'].mean() > 0:
                        print(f"     Detail: We were LONG (bad), price fell (losing money)")
                        print(f"             Overlay reduced our long → reduced losses (GOOD)")
                        print(f"             But something else went wrong...")
                else:
                    print(f"     Reason: Overlay didn't trigger, but relcarry or other effect worsened P&L")

            elif pnl_diff > 1.0:  # More than $1 better
                print(f"\n  ✅ OVERLAY HELPED by ${pnl_diff:.2f}")
            else:
                print(f"\n  → OVERLAY NEUTRAL (minimal impact)")

        except Exception as e:
            print(f"  Error analyzing {instrument}: {e}")
            import traceback
            traceback.print_exc()
            continue


def main():
    """Main analysis."""
    print("\n")
    print("╔" + "=" * 78 + "╗")
    print("║" + " " * 20 + "OI OVERLAY CRASH BEHAVIOR DIAGNOSIS" + " " * 23 + "║")
    print("╚" + "=" * 78 + "╝")
    print("\n")

    # Load data
    data_path = "data/dataset_538registry_6yr_jagged.parquet"
    print(f"Loading data: {data_path}")
    data = parquetCryptoPerpsSimData(data_path)

    # Load positions
    print("Loading positions...")
    baseline_positions = pd.read_csv('out/oi_mvp/baseline/positions.csv', index_col=0, parse_dates=True)
    combined_positions = pd.read_csv('out/oi_mvp/combined/positions.csv', index_col=0, parse_dates=True)

    # Define crash events
    crashes = [
        ('May 2021 Crash', '2021-05-19', '2021-05-23'),
        ('June 2022 Liquidations', '2022-06-12', '2022-06-18'),
        ('Nov 2022 FTX Collapse', '2022-11-08', '2022-11-11'),
    ]

    # Analyze each crash
    for event_name, start_date, end_date in crashes:
        analyze_crash_event(
            data=data,
            baseline_positions=baseline_positions,
            combined_positions=combined_positions,
            event_name=event_name,
            start_date=start_date,
            end_date=end_date,
            lookback=90,
            threshold=2.0,
        )
        print("\n\n")

    print("=" * 80)
    print("DIAGNOSIS COMPLETE")
    print("=" * 80)


if __name__ == "__main__":
    main()
