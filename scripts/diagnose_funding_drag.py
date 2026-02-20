#!/usr/bin/env python3
"""
Diagnose funding drag decomposition from a backtest positions file.

Decomposes total funding drag into long-leg and short-leg contributions,
checks whether the short-leg ~0% result is a structural feature (trend-follower
holds long in high-funding instruments, short in low-funding instruments) or a
calculation bug.

Usage:
    python scripts/diagnose_funding_drag.py \
        --positions out/resmom_weighted/positions.csv \
        --data data/dataset_538registry_6yr_jagged.parquet \
        --capital 10000
"""

import argparse
import sys
from pathlib import Path

import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))

from sysdata.crypto.prices import load_crypto_perps_panel


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _annualise(daily_series: pd.Series, capital: float) -> float:
    """Convert a daily-USD P&L series to annualised % of capital."""
    return float(daily_series.mean() * 365 / capital * 100)


def _pct_of_capital(daily_series: pd.Series, capital: float) -> float:
    """Mean daily value as annualised % of capital."""
    return _annualise(daily_series, capital)


# ---------------------------------------------------------------------------
# Main diagnostic
# ---------------------------------------------------------------------------

def run_diagnostic(positions_csv: str, data_path: str, capital: float) -> None:
    print("=" * 65)
    print("FUNDING DRAG DIAGNOSTIC")
    print("=" * 65)

    # ------------------------------------------------------------------
    # Load positions
    # ------------------------------------------------------------------
    pos = pd.read_csv(positions_csv, index_col=0, parse_dates=True).fillna(0)
    print(f"\nPositions: {pos.shape[0]} days × {pos.shape[1]} instruments")
    print(f"  Date range: {pos.index[0].date()} → {pos.index[-1].date()}")

    # ------------------------------------------------------------------
    # Load market data
    # ------------------------------------------------------------------
    print(f"\nLoading market data: {data_path}")
    prices_df, meta_df, _ = load_crypto_perps_panel(
        data_path, validate_schema=False, allow_jagged=True
    )

    # Restrict to instruments present in both positions and prices
    instruments = [c for c in pos.columns if c in prices_df.columns]
    print(f"  Instruments matched to positions: {len(instruments)} / {len(pos.columns)}")

    p  = pos[instruments]
    pr = prices_df[instruments].reindex(p.index, method='ffill')

    # Funding rates panel
    fr_long = meta_df['funding_rate'].unstack('instrument')
    fr = fr_long.reindex(p.index, method='ffill').reindex(columns=instruments)

    # ------------------------------------------------------------------
    # Core quantities — replicate _compute_funding_pnl_series exactly
    # funding_pnl[t,i] = -(pos[t,i] * price[t,i] * rate[t,i])
    # ------------------------------------------------------------------
    notional   = p * pr                   # signed notional USD per instrument per day
    funding_pnl = -(p * pr * fr)          # signed funding P&L per instrument per day

    # ------------------------------------------------------------------
    # SECTION 1 — Exposure & drag decomposition
    # ------------------------------------------------------------------
    print("\n" + "=" * 65)
    print("SECTION 1 — Exposure & Drag Decomposition")
    print("=" * 65)

    gross_long_daily  = notional.clip(lower=0).sum(axis=1)
    gross_short_daily = notional.clip(upper=0).abs().sum(axis=1)
    net_daily         = notional.sum(axis=1)

    avg_gross_long  = float(gross_long_daily.mean())
    avg_gross_short = float(gross_short_daily.mean())
    avg_net         = float(net_daily.mean())

    # Long / short leg masks (per instrument-day)
    long_mask  = (p > 0)
    short_mask = (p < 0)

    long_drag_daily  = funding_pnl.where(long_mask,  0.0).sum(axis=1)
    short_drag_daily = funding_pnl.where(short_mask, 0.0).sum(axis=1)
    total_drag_daily = funding_pnl.sum(axis=1)

    long_drag_ann  = _annualise(long_drag_daily,  capital)
    short_drag_ann = _annualise(short_drag_daily, capital)
    total_drag_ann = _annualise(total_drag_daily, capital)

    # Implied rates (drag / avg exposure)
    long_implied_rate  = (long_drag_ann  / (avg_gross_long  / capital * 100)) if avg_gross_long  > 0 else float('nan')
    short_implied_rate = (short_drag_ann / (avg_gross_short / capital * 100)) if avg_gross_short > 0 else float('nan')

    print(f"\n{'Exposure & Drag Summary':}")
    print(f"{'':2}{'Avg gross long  exposure:':35} {avg_gross_long  / capital * 100:6.1f}% of capital")
    print(f"{'':2}{'Avg gross short exposure:':35} {avg_gross_short / capital * 100:6.1f}% of capital")
    print(f"{'':2}{'Avg net exposure:':35} {avg_net         / capital * 100:6.1f}% of capital")
    print()
    print(f"{'':2}{'Funding drag (long leg):':35} {long_drag_ann:+7.2f}% p.a.")
    print(f"{'':2}{'Funding drag (short leg):':35} {short_drag_ann:+7.2f}% p.a.  (positive = receive)")
    print(f"{'':2}{'Funding drag (total):':35} {total_drag_ann:+7.2f}% p.a.")
    print()
    print(f"{'':2}{'Implied rate on long  positions:':35} {long_implied_rate:6.1f}% p.a.")
    print(f"{'':2}{'Implied rate on short positions:':35} {short_implied_rate:+6.1f}% p.a.  (positive = receive)")

    # Consistency check: does total match sum of legs?
    residual = total_drag_ann - long_drag_ann - short_drag_ann
    if abs(residual) > 0.001:
        print(f"\n  WARNING: long + short legs don't sum to total (residual = {residual:.4f}%)")
    else:
        print(f"\n  Check: long + short = {long_drag_ann + short_drag_ann:.2f}% ≈ total ({total_drag_ann:.2f}%)  OK")

    # ------------------------------------------------------------------
    # SECTION 2 — Rate distribution
    # ------------------------------------------------------------------
    print("\n" + "=" * 65)
    print("SECTION 2 — Funding Rate Distribution")
    print("=" * 65)

    mean_rate = fr.mean(axis=1)   # daily cross-sectional mean (unweighted)

    frac_positive = (fr > 0).sum().sum() / fr.notna().sum().sum()
    median_rate_ann = float(mean_rate.median() * 365 * 100)

    # Exposure-weighted average funding rate
    # Only count instrument-days where we have a non-zero position AND a rate
    weight_mask = (p != 0) & fr.notna()
    abs_notional = notional.abs()
    total_notional_weighted = (abs_notional.where(weight_mask, 0.0) * fr.abs().where(weight_mask, 0.0)).sum().sum()
    total_abs_notional      = abs_notional.where(weight_mask, 0.0).sum().sum()
    exposure_weighted_rate_ann = (
        total_notional_weighted / total_abs_notional * 365 * 100
        if total_abs_notional > 0 else float('nan')
    )

    print(f"\n{'':2}Cross-sectional mean funding rate (daily median, annualised): {median_rate_ann:+.2f}% p.a.")
    print(f"{'':2}Fraction of instrument-days with rate > 0:                   {frac_positive:.1%}")
    print(f"{'':2}|Funding rate| exposure-weighted average (annualised):        {exposure_weighted_rate_ann:.2f}% p.a.")

    # Rate on held positions only
    long_fr_only  = fr.where(long_mask  & fr.notna(), other=np.nan)
    short_fr_only = fr.where(short_mask & fr.notna(), other=np.nan)

    long_mean_rate_ann  = float(long_fr_only.stack().mean()  * 365 * 100) if long_fr_only.stack().notna().any() else float('nan')
    short_mean_rate_ann = float(short_fr_only.stack().mean() * 365 * 100) if short_fr_only.stack().notna().any() else float('nan')

    print(f"\n  Mean funding rate on LONG  instrument-days: {long_mean_rate_ann:+.2f}% p.a.")
    print(f"  Mean funding rate on SHORT instrument-days: {short_mean_rate_ann:+.2f}% p.a.")
    print(f"  (If short rate ≈ 0 or negative → explains low short-leg drag)")

    # ------------------------------------------------------------------
    # SECTION 3 — Rate vs position correlation
    # ------------------------------------------------------------------
    print("\n" + "=" * 65)
    print("SECTION 3 — Position vs Funding Rate Correlation")
    print("=" * 65)
    print("  Tests whether the system is structurally long high-funding /")
    print("  short low-funding instruments (expected for trend followers).")

    corrs = {}
    for inst in instruments:
        mask = p[inst].notna() & fr[inst].notna() & (p[inst] != 0)
        if mask.sum() > 20:
            corrs[inst] = p[inst][mask].corr(fr[inst][mask])

    if corrs:
        corr_series = pd.Series(corrs)
        print(f"\n  Instruments with ≥20 non-zero-position days: {len(corr_series)}")
        print(f"  Median pos-vs-funding-rate correlation:       {corr_series.median():.3f}")
        print(f"  Mean   pos-vs-funding-rate correlation:       {corr_series.mean():.3f}")
        print(f"  Pct instruments with positive corr:           {(corr_series > 0).mean():.1%}")

        print("\n  Interpretation:")
        if corr_series.median() > 0.05:
            print("    → POSITIVE median correlation: structural long-in-high-funding bias confirmed.")
            print("      Trend follower is long assets with positive funding, short assets with")
            print("      near-zero or negative funding. Both legs pay (or short leg receives nothing).")
        elif corr_series.median() < -0.05:
            print("    → NEGATIVE median correlation: system is short high-funding instruments.")
            print("      Unusual — investigate whether there is a data/sign issue.")
        else:
            print("    → Near-zero median correlation: no systematic funding bias by direction.")
            print("      If short-leg drag is still ~0, this may indicate a calculation bug.")

        print("\n  Top 10 instruments by positive correlation:")
        top_pos = corr_series.nlargest(10)
        for inst, c in top_pos.items():
            print(f"    {inst:20s}  {c:+.3f}")

        print("\n  Top 10 instruments by negative correlation:")
        top_neg = corr_series.nsmallest(10)
        for inst, c in top_neg.items():
            print(f"    {inst:20s}  {c:+.3f}")
    else:
        print("\n  WARNING: No instruments had enough non-zero-position days for correlation.")

    # ------------------------------------------------------------------
    # SECTION 4 — Per-year drag
    # ------------------------------------------------------------------
    print("\n" + "=" * 65)
    print("SECTION 4 — Per-Year Drag Breakdown")
    print("=" * 65)

    annual_long  = long_drag_daily.groupby(long_drag_daily.index.year).sum()   / capital * 100
    annual_short = short_drag_daily.groupby(short_drag_daily.index.year).sum() / capital * 100
    annual_total = total_drag_daily.groupby(total_drag_daily.index.year).sum() / capital * 100

    # Days per year (for context)
    days_per_year = total_drag_daily.groupby(total_drag_daily.index.year).count()

    print(f"\n  {'Year':>6}  {'Days':>5}  {'Long drag':>10}  {'Short drag':>11}  {'Total drag':>11}")
    print(f"  {'-'*6}  {'-'*5}  {'-'*10}  {'-'*11}  {'-'*11}")
    for yr in sorted(annual_total.index):
        print(
            f"  {yr:6d}  {days_per_year.get(yr, 0):5d}  "
            f"{annual_long.get(yr, 0):+10.2f}%  "
            f"{annual_short.get(yr, 0):+11.2f}%  "
            f"{annual_total.get(yr, 0):+11.2f}%"
        )

    # ------------------------------------------------------------------
    # SECTION 5 — Per-instrument breakdown
    # ------------------------------------------------------------------
    print("\n" + "=" * 65)
    print("SECTION 5 — Per-Instrument Funding P&L (% of capital, total)")
    print("=" * 65)

    per_inst_total = funding_pnl.sum(axis=0) / capital * 100
    per_inst_long  = funding_pnl.where(long_mask,  0.0).sum(axis=0) / capital * 100
    per_inst_short = funding_pnl.where(short_mask, 0.0).sum(axis=0) / capital * 100

    inst_summary = pd.DataFrame({
        'long':  per_inst_long,
        'short': per_inst_short,
        'total': per_inst_total,
    })

    print("\n  Top 10 payers (most negative total funding P&L):")
    top_payers = inst_summary['total'].nsmallest(10)
    print(f"  {'Instrument':20s}  {'Long':>9}  {'Short':>9}  {'Total':>9}")
    print(f"  {'-'*20}  {'-'*9}  {'-'*9}  {'-'*9}")
    for inst in top_payers.index:
        row = inst_summary.loc[inst]
        print(f"  {inst:20s}  {row['long']:+9.2f}%  {row['short']:+9.2f}%  {row['total']:+9.2f}%")

    print("\n  Top 10 receivers (most positive total funding P&L):")
    top_receivers = inst_summary['total'].nlargest(10)
    print(f"  {'Instrument':20s}  {'Long':>9}  {'Short':>9}  {'Total':>9}")
    print(f"  {'-'*20}  {'-'*9}  {'-'*9}  {'-'*9}")
    for inst in top_receivers.index:
        row = inst_summary.loc[inst]
        print(f"  {inst:20s}  {row['long']:+9.2f}%  {row['short']:+9.2f}%  {row['total']:+9.2f}%")

    # ------------------------------------------------------------------
    # Final verdict
    # ------------------------------------------------------------------
    print("\n" + "=" * 65)
    print("VERDICT")
    print("=" * 65)

    if abs(total_drag_ann - long_drag_ann - short_drag_ann) < 0.01:
        print(f"\n  Total drag ({total_drag_ann:.2f}% p.a.) = ")
        print(f"    Long leg  ({long_drag_ann:.2f}% p.a.) + Short leg ({short_drag_ann:.2f}% p.a.)  ✓")
    else:
        print(f"\n  WARNING: Leg decomposition does not sum to total — investigate NaN handling.")

    if abs(short_drag_ann) < 0.1:
        print(f"\n  Short-leg drag is ~0% p.a.")
        if corrs and corr_series.median() > 0.05:
            print(f"  Combined with positive pos-vs-rate correlation ({corr_series.median():.3f}),")
            print(f"  this is CONSISTENT with structural long-high-funding / short-low-funding bias.")
            print(f"  When the system goes short, those instruments tend to have near-zero or")
            print(f"  negative funding, so the short leg receives essentially nothing.")
            print(f"  This is expected behaviour for a momentum system — not a bug.")
        else:
            print(f"  Pos-vs-rate correlation is not clearly positive — investigate further.")
            print(f"  Check NaN alignment between positions and funding rate panels.")

    print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Diagnose funding drag decomposition from a backtest positions file.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        '--positions', type=Path, required=True,
        help='Path to positions.csv (dates × instruments, base-asset units)',
    )
    parser.add_argument(
        '--data', type=Path, required=True,
        help='Path to parquet dataset (same format used in backtest)',
    )
    parser.add_argument(
        '--capital', type=float, default=10_000.0,
        help='Notional trading capital in USD (default: 10000)',
    )

    args = parser.parse_args()

    if not args.positions.exists():
        print(f"ERROR: positions file not found: {args.positions}")
        sys.exit(1)
    if not args.data.exists():
        print(f"ERROR: data file not found: {args.data}")
        sys.exit(1)

    run_diagnostic(
        positions_csv=str(args.positions),
        data_path=str(args.data),
        capital=args.capital,
    )


if __name__ == '__main__':
    main()
