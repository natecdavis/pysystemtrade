#!/usr/bin/env python3
"""
Diagnostic: verify demeaned_carry rule outputs before running the full sweep.

Checks for a representative instrument (default ETHUSDT_PERP):
  1. Non-NaN fill rate ≥ 90% for all 6 rule variants
  2. Output range plausible (roughly ±20 before forecast cap)
  3. Gated variants: ~50% zeros (same as gated_carry)
  4. Correlations with existing signals: should be low vs gated_carry and xs_carry

Usage:
    python scripts/diagnose_demeaned_carry.py \\
        --data data/dataset_538registry_6yr_jagged.parquet \\
        --instrument ETHUSDT_PERP

Runtime: ~10 seconds
"""

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from sysdata.crypto.parquet_perps_sim_data import parquetCryptoPerpsSimData
from sysquant.estimators.vol import robust_vol_calc
from systems.crypto_perps.rules.rule_library import (
    demeaned_carry,
    gated_carry,
)


# ──────────────────────────────────────────────────────────────────────────────


def load_data(data_path: Path):
    """Load parquet sim data adapter."""
    data = parquetCryptoPerpsSimData(str(data_path))
    return data


def get_vol(data, instrument: str) -> pd.Series:
    """Compute price-dollar volatility (same as rawdata.daily_returns_volatility)."""
    prices = data.daily_prices(instrument)
    daily_returns = prices.diff()
    return robust_vol_calc(daily_returns)


def compute_rule(
    data,
    instrument: str,
    carry_span: int,
    gate: bool,
) -> pd.Series:
    """Compute demeaned_carry for a single instrument."""
    funding_rates  = data.get_funding_rate(instrument)
    market_funding = data.get_cross_sectional_median_funding(instrument)
    prices         = data.daily_prices(instrument)
    vol            = get_vol(data, instrument)

    return demeaned_carry(
        funding_rates=funding_rates,
        market_funding=market_funding,
        price=prices,
        vol=vol,
        carry_span=carry_span,
        gate=gate,
    )


def compute_gated_carry_baseline(data, instrument: str, carry_span: int) -> pd.Series:
    """Compute existing gated_carry for correlation comparison."""
    funding_rates = data.get_funding_rate(instrument)
    prices        = data.daily_prices(instrument)
    vol           = get_vol(data, instrument)

    return gated_carry(
        funding_rates=funding_rates,
        price=prices,
        vol=vol,
        carry_span=carry_span,
    )


def print_series_stats(name: str, s: pd.Series) -> None:
    """Print key statistics for a signal series."""
    non_nan = s.dropna()
    zeros = (non_nan == 0.0).sum()
    total = len(non_nan)
    fill_rate = total / len(s) if len(s) > 0 else 0.0
    zero_rate = zeros / total if total > 0 else 0.0
    p1  = non_nan.quantile(0.01)
    p99 = non_nan.quantile(0.99)

    flag_fill = "" if fill_rate >= 0.9 else "  ← WARN: fill rate < 90%"
    flag_range = "" if (abs(p1) < 100 and abs(p99) < 100) else "  ← WARN: outliers?"

    print(
        f"  {name:<30}  fill={fill_rate:.1%}  zero={zero_rate:.1%}  "
        f"p1={p1:+8.2f}  p99={p99:+8.2f}  "
        f"mean={non_nan.mean():+7.3f}{flag_fill}{flag_range}"
    )


def correlations(a: pd.Series, b: pd.Series, label_a: str, label_b: str) -> None:
    """Print Pearson and Spearman correlation between two aligned signals."""
    df = pd.concat([a.rename("a"), b.rename("b")], axis=1).dropna()
    if len(df) < 50:
        print(f"  {label_a} vs {label_b}: insufficient overlap ({len(df)} rows)")
        return
    pearson = df["a"].corr(df["b"])
    spearman = df["a"].rank().corr(df["b"].rank())
    print(f"  {label_a:<35} vs {label_b:<30}  Pearson={pearson:+.3f}  Spearman={spearman:+.3f}")


def main():
    parser = argparse.ArgumentParser(
        description="Diagnose demeaned_carry rule outputs.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--data", type=Path,
        default=Path("data/dataset_538registry_6yr_jagged.parquet"),
    )
    parser.add_argument(
        "--instrument", type=str,
        default="ETHUSDT_PERP",
        help="Instrument to diagnose (default: ETHUSDT_PERP)",
    )
    args = parser.parse_args()

    if not args.data.exists():
        print(f"ERROR: data file not found: {args.data}")
        sys.exit(1)

    print(f"Data:       {args.data}")
    print(f"Instrument: {args.instrument}")
    print()

    print("Loading data...")
    data = load_data(args.data)
    print("  Done.")
    print()

    # ── Check that required data series are non-empty ──────────────────────
    print("=== DATA AVAILABILITY ===")
    funding_rates    = data.get_funding_rate(args.instrument)
    market_funding   = data.get_cross_sectional_median_funding(args.instrument)
    prices           = data.daily_prices(args.instrument)
    vol              = get_vol(data, args.instrument)

    for name, s in [
        ("funding_rate",            funding_rates),
        ("market_funding (median)", market_funding),
        ("daily_prices",            prices),
        ("price-dollar vol",        vol),
    ]:
        non_nan = s.dropna()
        print(f"  {name:<35}  n={len(s):>6}  non-NaN={len(non_nan):>6}  "
              f"range=[{s.index.min().date()} → {s.index.max().date()}]")
    print()

    # ── Compute all 6 rule variants ────────────────────────────────────────
    print("=== SIGNAL STATISTICS ===")
    print("  (fill = non-NaN rate; zero = fraction == 0.0; p1/p99 = 1st/99th percentile)")
    print()

    rules = {}
    for carry_span in [10, 30, 60]:
        for gate in [False, True]:
            variant = "gated" if gate else "ungated"
            name = f"demeaned_{variant}_{carry_span}"
            s = compute_rule(data, args.instrument, carry_span, gate)
            rules[name] = s
            print_series_stats(name, s)

    print()

    # ── Gated zero-rate check ──────────────────────────────────────────────
    print("=== GATED ZERO-RATE CHECK (expect ~50% zeros, same as gated_carry) ===")
    for carry_span in [10, 30, 60]:
        gated_name    = f"demeaned_gated_{carry_span}"
        ungated_name  = f"demeaned_ungated_{carry_span}"
        gc = compute_gated_carry_baseline(data, args.instrument, carry_span)
        gated_s    = rules[gated_name].dropna()
        ungated_s  = rules[ungated_name].dropna()
        gc_nz      = gc.dropna()

        gated_zero   = (gated_s == 0).sum() / len(gated_s) if len(gated_s) else 0
        gc_zero      = (gc_nz == 0).sum() / len(gc_nz) if len(gc_nz) else 0

        status = "OK" if 0.3 <= gated_zero <= 0.7 else "WARN"
        print(f"  carry_span={carry_span:>3d}: demeaned_gated zero={gated_zero:.1%}  "
              f"gated_carry zero={gc_zero:.1%}  [{status}]")
    print()

    # ── Correlation analysis ────────────────────────────────────────────────
    print("=== CORRELATION vs EXISTING SIGNALS ===")
    print("  (low correlation = genuine diversification; high = redundant)")
    print()

    # Load xs_carry forecast if available (passthrough signal, pre-computed)
    try:
        xs_carry = data.get_xs_carry_forecast(args.instrument)
        has_xs_carry = len(xs_carry.dropna()) > 0
    except Exception:
        xs_carry = pd.Series(dtype=float)
        has_xs_carry = False

    for carry_span in [10, 30, 60]:
        gc = compute_gated_carry_baseline(data, args.instrument, carry_span)
        ungated = rules[f"demeaned_ungated_{carry_span}"]
        gated   = rules[f"demeaned_gated_{carry_span}"]

        correlations(ungated, gc, f"demeaned_ungated_{carry_span}", f"gated_carry_{carry_span}")
        correlations(gated,   gc, f"demeaned_gated_{carry_span}",   f"gated_carry_{carry_span}")

        if has_xs_carry:
            correlations(ungated, xs_carry, f"demeaned_ungated_{carry_span}", "xs_carry")
            correlations(gated,   xs_carry, f"demeaned_gated_{carry_span}",   "xs_carry")
        else:
            print(f"  xs_carry not available for {args.instrument} (skip xs_carry correlations)")

        # Cross-span correlations (10 vs 30 vs 60)
        if carry_span < 60:
            other_span = 30 if carry_span == 10 else 60
            other_ungated = rules[f"demeaned_ungated_{other_span}"]
            correlations(ungated, other_ungated,
                         f"demeaned_ungated_{carry_span}", f"demeaned_ungated_{other_span}")
        print()

    # ── Summary ───────────────────────────────────────────────────────────
    print("=== SUMMARY ===")
    pass_count = 0
    fail_count = 0

    for name, s in rules.items():
        non_nan = s.dropna()
        fill_rate = len(non_nan) / len(s) if len(s) > 0 else 0.0
        if fill_rate >= 0.9:
            print(f"  ✓  {name}: fill={fill_rate:.1%}")
            pass_count += 1
        else:
            print(f"  ✗  {name}: fill={fill_rate:.1%}  ← FAIL (below 90% threshold)")
            fail_count += 1

    print()
    if fail_count == 0:
        print(f"PASS: All {pass_count} variants have ≥90% fill rate. Ready for sweep_demeaned_carry.py")
    else:
        print(f"FAIL: {fail_count} variant(s) below 90% fill rate. Investigate before sweeping.")
        sys.exit(1)


if __name__ == "__main__":
    main()
