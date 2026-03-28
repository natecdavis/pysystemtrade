#!/usr/bin/env python3
"""
Skew vs Downside-Beta Overlap Diagnostic

Quantifies how much a rolling-skew signal overlaps with the existing β_down
overlay, helping us decide whether a skew trading rule would add genuinely
new information or just re-express crash-risk exposure we already manage.

Method:
  1. Load price data and compute daily log-returns.
  2. Compute the β_down panel (same formula as parquet_perps_sim_data.py).
  3. Compute rolling skew at 90d / 180d / 365d windows.
  4. Cross-sectionally rank both signals per date (percentile, 0–1).
  5. Measure correlation between β_down rank and skew rank across three levels:
       a) Panel-level  : Spearman corr on stacked (date × instrument) pairs
       b) Instrument-level : per-instrument time-series correlation, then summarise
       c) Date-level   : per-date cross-sectional correlation, then summarise
  6. Breakdown: what fraction of instrument-days with negative skew also have
     high β_down (top tercile)?

Usage:
    python scripts/diagnose_skew_beta_overlap.py \\
        --data data/dataset_538registry_6yr_jagged.parquet \\
        --beta-window 90 \\
        --skew-windows 90 180 365

Runtime: ~30 seconds (pure pandas/numpy, no system build).
"""

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

# ──────────────────────────────────────────────────────────────────────────────

def compute_beta_down_panel(
    log_ret: pd.DataFrame, window: int = 90, min_periods: int = 20
) -> pd.DataFrame:
    """Replicates parquet_perps_sim_data._compute_downside_beta_panel."""
    market_ret = log_ret.median(axis=1)
    down_mask = (market_ret < 0).astype(float)
    mkt_masked = market_ret * down_mask

    instr_times_mkt = log_ret.multiply(mkt_masked, axis=0)
    cov_sum = instr_times_mkt.rolling(window, min_periods=min_periods).sum()

    mkt_sq_masked = (market_ret ** 2) * down_mask
    var_sum = mkt_sq_masked.rolling(window, min_periods=min_periods).sum()

    return cov_sum.divide(var_sum, axis=0)


def compute_skew_panel(
    log_ret: pd.DataFrame, window: int, min_periods: int = 30
) -> pd.DataFrame:
    """Rolling skew of log-returns over `window` days."""
    return log_ret.rolling(window, min_periods=min_periods).skew()


def cross_sectional_rank(panel: pd.DataFrame) -> pd.DataFrame:
    """Cross-sectional percentile rank (0–1) per date."""
    return panel.rank(axis=1, pct=True)


def panel_spearman(x: pd.Series, y: pd.Series) -> tuple[float, float]:
    """Spearman correlation on aligned, non-NaN (x, y) pairs."""
    both = pd.concat([x, y], axis=1).dropna()
    if len(both) < 50:
        return float("nan"), float("nan")
    r, p = stats.spearmanr(both.iloc[:, 0], both.iloc[:, 1])
    return float(r), float(p)


def summarise_per_instrument(corr_series: pd.Series, label: str) -> None:
    cs = corr_series.dropna()
    print(f"  {label}:")
    print(f"    n={len(cs)}  median={cs.median():.3f}  mean={cs.mean():.3f}"
          f"  p10={cs.quantile(0.10):.3f}  p90={cs.quantile(0.90):.3f}")


def summarise_per_date(corr_series: pd.Series, label: str) -> None:
    cs = corr_series.dropna()
    print(f"  {label}:")
    print(f"    n={len(cs)}  median={cs.median():.3f}  mean={cs.mean():.3f}"
          f"  p10={cs.quantile(0.10):.3f}  p90={cs.quantile(0.90):.3f}")


# ──────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", default="data/dataset_538registry_6yr_jagged.parquet")
    parser.add_argument("--beta-window", type=int, default=90)
    parser.add_argument("--skew-windows", type=int, nargs="+", default=[90, 180, 365])
    args = parser.parse_args()

    data_path = Path(args.data)
    if not data_path.exists():
        print(f"ERROR: data file not found: {data_path}", file=sys.stderr)
        sys.exit(1)

    # ── Load prices ──────────────────────────────────────────────────────────
    print(f"Loading prices from {data_path} ...")
    raw = pd.read_parquet(data_path)
    # Long-format: columns are date, instrument, close, ...
    prices = raw.pivot(index="date", columns="instrument", values="close")
    prices.index = pd.DatetimeIndex(prices.index)
    prices = prices.sort_index()
    print(f"  {prices.shape[0]} dates × {prices.shape[1]} instruments")
    print(f"  Date range: {prices.index[0].date()} → {prices.index[-1].date()}")

    log_ret = np.log(prices / prices.shift(1))

    # ── Compute β_down ───────────────────────────────────────────────────────
    print(f"\nComputing β_down panel (window={args.beta_window}d) ...")
    beta_panel = compute_beta_down_panel(log_ret, window=args.beta_window)
    beta_rank = cross_sectional_rank(beta_panel)
    valid_beta = beta_panel.stack().dropna()
    print(f"  Valid (date, instrument) pairs: {len(valid_beta):,}")
    print(f"  β_down  median={valid_beta.median():.3f}  "
          f"p10={valid_beta.quantile(0.10):.3f}  p90={valid_beta.quantile(0.90):.3f}")

    # ── For each skew window ─────────────────────────────────────────────────
    for sw in args.skew_windows:
        print(f"\n{'='*60}")
        print(f"Skew window = {sw}d")
        print('='*60)

        skew_panel = compute_skew_panel(log_ret, window=sw)
        skew_rank = cross_sectional_rank(skew_panel)

        # Align both panels
        both_beta = beta_rank.stack().rename("beta_rank")
        both_skew = skew_rank.stack().rename("skew_rank")
        combined = pd.concat([both_beta, both_skew], axis=1).dropna()
        n_pairs = len(combined)
        print(f"  Aligned pairs (date × instrument, no NaN): {n_pairs:,}")

        # ── 1. Panel-level Spearman ──────────────────────────────────────────
        r_panel, p_panel = panel_spearman(combined["beta_rank"], combined["skew_rank"])
        print(f"\n1. Panel-level Spearman corr(β_down_rank, skew_rank):")
        print(f"   r = {r_panel:+.4f}   p = {p_panel:.2e}")
        if abs(r_panel) < 0.10:
            verdict = "negligible overlap"
        elif abs(r_panel) < 0.25:
            verdict = "modest overlap"
        elif abs(r_panel) < 0.50:
            verdict = "meaningful overlap"
        else:
            verdict = "STRONG overlap"
        print(f"   → {verdict}")

        # ── 2. Per-instrument time-series correlation ────────────────────────
        print(f"\n2. Per-instrument time-series Spearman corr:")
        instr_corrs = {}
        beta_rank_t = beta_rank.reindex(skew_rank.index)
        for col in skew_rank.columns:
            if col not in beta_rank_t.columns:
                continue
            pair = pd.concat([beta_rank_t[col], skew_rank[col]], axis=1).dropna()
            if len(pair) < 50:
                continue
            r, _ = stats.spearmanr(pair.iloc[:, 0], pair.iloc[:, 1])
            instr_corrs[col] = r
        instr_corr_series = pd.Series(instr_corrs)
        summarise_per_instrument(instr_corr_series, f"skew_{sw}d vs β_down")

        # ── 3. Per-date cross-sectional correlation ──────────────────────────
        print(f"\n3. Per-date cross-sectional Spearman corr:")
        date_corrs = {}
        shared_dates = beta_panel.index.intersection(skew_panel.index)
        for dt in shared_dates:
            b_row = beta_panel.loc[dt].dropna()
            s_row = skew_panel.loc[dt].dropna()
            shared_cols = b_row.index.intersection(s_row.index)
            if len(shared_cols) < 10:
                continue
            r, _ = stats.spearmanr(b_row[shared_cols], s_row[shared_cols])
            date_corrs[dt] = r
        date_corr_series = pd.Series(date_corrs)
        summarise_per_date(date_corr_series, f"skew_{sw}d vs β_down (XS, per date)")

        # ── 4. Overlap fraction: negative skew ∩ high β_down ────────────────
        print(f"\n4. Overlap: negative skew ∩ high β_down (top tercile)")
        neg_skew_mask = skew_panel.stack() < 0
        high_beta_mask = beta_rank.stack() > (2/3)

        aligned_idx = neg_skew_mask.index.intersection(high_beta_mask.index)
        neg_skew_aligned = neg_skew_mask.loc[aligned_idx]
        high_beta_aligned = high_beta_mask.loc[aligned_idx]

        neg_skew_n = neg_skew_aligned.sum()
        both_n = (neg_skew_aligned & high_beta_aligned).sum()
        pct = 100.0 * both_n / neg_skew_n if neg_skew_n > 0 else 0.0
        print(f"   Negative-skew instrument-days: {neg_skew_n:,}")
        print(f"   Of those, also high-β_down:    {both_n:,} ({pct:.1f}%)")
        print(f"   (Expected if independent: 33.3%)")
        if pct > 40:
            print(f"   → β_down OVER-represented among negative-skew → moderate overlap")
        elif pct < 27:
            print(f"   → β_down UNDER-represented among negative-skew → signals diverge")
        else:
            print(f"   → roughly independent")

        # ── 5. Quintile breakdown ────────────────────────────────────────────
        print(f"\n5. Mean β_down rank by skew quintile (skew_{sw}d):")
        combined_raw = pd.concat([
            beta_rank.stack().rename("beta_rank"),
            skew_panel.stack().rename("skew_raw"),
        ], axis=1).dropna()
        combined_raw["skew_quintile"] = pd.qcut(
            combined_raw["skew_raw"], q=5,
            labels=["Q1 (most-neg)", "Q2", "Q3", "Q4", "Q5 (most-pos)"]
        )
        print(combined_raw.groupby("skew_quintile", observed=True)["beta_rank"]
              .agg(["mean", "median", "count"])
              .rename(columns={"mean": "mean_β_rank", "median": "med_β_rank"})
              .to_string())

    print("\nDone.")


if __name__ == "__main__":
    main()
