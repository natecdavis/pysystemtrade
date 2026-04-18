"""
Test whether same-calendar-month annual-lag seasonality exists in crypto perps.

Based on Wang (2024): instrument-level seasonality using annual lags only (t-2, t-3, ...),
skipping t-1 to avoid momentum contamination.

Tests run:
  1. Time-series IC: per-instrument Spearman corr(seasonality_score, next_month_return)
  2. Cross-sectional IC: per-month Spearman corr across instruments
  3. Placebo: same tests with 6-month-offset lags (should be ~0)
  4. Lookback robustness: 3yr vs 5yr
  5. Subgroup: majors vs alts, long-history vs short-history
  6. Simple L/S Sharpe: long top tercile, short bottom tercile, monthly rebalance

Usage:
    python scripts/test_seasonality_effect.py
"""

import os
import sys
import warnings

import numpy as np
import pandas as pd
from scipy import stats

warnings.filterwarnings("ignore")

DATASET_PATH = "data/dataset_538registry_6yr_jagged.parquet"

MAJORS = {
    "BTCUSDT_PERP", "ETHUSDT_PERP", "SOLUSDT_PERP", "BNBUSDT_PERP",
    "XRPUSDT_PERP", "DOGEUSDT_PERP", "ADAUSDT_PERP", "LTCUSDT_PERP",
}


# ============================================================================
# DATA LOADING
# ============================================================================


def load_prices() -> pd.DataFrame:
    """Load wide daily close price dataframe (dates × instruments)."""
    df = pd.read_parquet(DATASET_PATH)
    df["date"] = pd.to_datetime(df["date"])
    prices = df.pivot_table(index="date", columns="instrument", values="close")
    prices.index = pd.DatetimeIndex(prices.index)
    return prices


# ============================================================================
# SEASONALITY SCORE COMPUTATION
# ============================================================================


def compute_seasonality_scores(
    prices: pd.DataFrame,
    n_lags_min: int = 2,
    n_lags_max: int = 5,
    placebo_offset: int = 0,
    predict_next_month: bool = True,
) -> pd.DataFrame:
    """
    Compute same-calendar-month seasonality score for each instrument and month.

    Two modes:
    - predict_next_month=True (trading-relevant): score at month M's end uses
      month M+1's historical same-month returns. Predicts M+1's actual return.
      This is the correct trading framing: at end of March, ask "how has April
      historically performed?" and trade April accordingly.
    - predict_next_month=False (contemporaneous): score at month M's end uses
      month M's historical same-month returns. Tests whether historical same-month
      performance predicts the same-month outcome (score computable before month M
      begins since we skip t-1 and t-0).

    Args:
        prices:             Wide daily price dataframe (dates × instruments).
        n_lags_min:         First annual lag year (default 2, skipping t-1).
        n_lags_max:         Last annual lag year (inclusive).
        placebo_offset:     Shift lags by this many months (placebo test).
        predict_next_month: If True, use next month's historical data (see above).

    Returns:
        DataFrame of seasonality scores indexed by month-end dates.
        Pair with monthly_rets.shift(-1) if predict_next_month=True,
        or monthly_rets if predict_next_month=False.
    """
    monthly = prices.resample("ME").last()
    monthly_rets = monthly.pct_change()

    scores = {}
    for dt in monthly_rets.index:
        # If predict_next_month, score at month M targets M+1's historical pattern
        if predict_next_month:
            target_dt = dt + pd.DateOffset(months=1)
        else:
            target_dt = dt
        month_num = target_dt.month
        year_num = target_dt.year

        row = {}
        for instr in monthly_rets.columns:
            lags = []
            for k in range(n_lags_min, n_lags_max + 1):
                target_months_back = k * 12 + placebo_offset
                target_date_approx = target_dt - pd.DateOffset(months=target_months_back)
                target_month = target_date_approx.month
                target_year = target_date_approx.year
                lag_idx = monthly_rets.index[
                    (monthly_rets.index.month == target_month)
                    & (monthly_rets.index.year == target_year)
                ]
                if len(lag_idx) == 1:
                    val = monthly_rets.loc[lag_idx[0], instr]
                    if not np.isnan(val):
                        lags.append(val)
            row[instr] = float(np.mean(lags)) if lags else np.nan
        scores[dt] = row

    return pd.DataFrame(scores).T  # months × instruments


# ============================================================================
# IC COMPUTATION
# ============================================================================


def compute_ts_ic(
    scores: pd.DataFrame,
    monthly_rets: pd.DataFrame,
    forward_shift: int = -1,
) -> tuple[float, float, float, int, int]:
    """
    Time-series IC: per-instrument Spearman corr(score, target_return).

    forward_shift=-1: score at M predicts M+1 (trading-relevant for predict_next_month=True)
    forward_shift=0:  score at M predicts M (same-month, for predict_next_month=False)

    Returns: (mean_IC, std_IC, t_stat, n_instruments, total_pairs)
    """
    ics = []
    target_rets = monthly_rets.shift(forward_shift) if forward_shift != 0 else monthly_rets

    for instr in scores.columns:
        if instr not in target_rets.columns:
            continue
        s = scores[instr].dropna()
        r = target_rets[instr].reindex(s.index).dropna()
        common = s.index.intersection(r.index)
        if len(common) < 6:
            continue
        ic, _ = stats.spearmanr(s.loc[common], r.loc[common])
        if not np.isnan(ic):
            ics.append(ic)

    if not ics:
        return 0.0, 0.0, 0.0, 0, 0

    ics = np.array(ics)
    mean_ic = float(np.mean(ics))
    std_ic = float(np.std(ics, ddof=1))
    t_stat = mean_ic / (std_ic / np.sqrt(len(ics))) if std_ic > 0 else 0.0
    n_pairs = int(sum(
        len(scores[c].dropna()) for c in scores.columns if c in monthly_rets.columns
    ))
    return mean_ic, std_ic, t_stat, len(ics), n_pairs


def compute_xs_ic(
    scores: pd.DataFrame,
    monthly_rets: pd.DataFrame,
    forward_shift: int = -1,
) -> tuple[float, float, float, int]:
    """
    Cross-sectional IC: per-month Spearman corr(score_rank, target_return).

    Returns: (mean_IC, std_IC, t_stat, n_months)
    """
    target_rets = monthly_rets.shift(forward_shift) if forward_shift != 0 else monthly_rets
    monthly_ics = []

    for dt in scores.index:
        if dt not in target_rets.index:
            continue
        s_row = scores.loc[dt].dropna()
        r_row = target_rets.loc[dt].reindex(s_row.index).dropna()
        common = s_row.index.intersection(r_row.index)
        if len(common) < 5:
            continue
        ic, _ = stats.spearmanr(s_row.loc[common], r_row.loc[common])
        if not np.isnan(ic):
            monthly_ics.append(ic)

    if not monthly_ics:
        return 0.0, 0.0, 0.0, 0

    monthly_ics = np.array(monthly_ics)
    mean_ic = float(np.mean(monthly_ics))
    std_ic = float(np.std(monthly_ics, ddof=1))
    t_stat = mean_ic / std_ic * np.sqrt(len(monthly_ics)) if std_ic > 0 else 0.0
    return mean_ic, std_ic, t_stat, len(monthly_ics)


# ============================================================================
# LONG/SHORT SHARPE COMPUTATION
# ============================================================================


def compute_ls_sharpe(
    scores: pd.DataFrame,
    monthly_rets: pd.DataFrame,
    forward_shift: int = -1,
) -> float:
    """
    Simple long-top-tercile / short-bottom-tercile monthly strategy.
    Equal weight within each group, no transaction costs.
    Returns annualised Sharpe ratio.
    """
    target_rets = monthly_rets.shift(forward_shift) if forward_shift != 0 else monthly_rets
    monthly_pnl = []

    for dt in scores.index:
        if dt not in target_rets.index:
            continue
        s_row = scores.loc[dt].dropna()
        r_row = target_rets.loc[dt].reindex(s_row.index).dropna()
        common = s_row.index.intersection(r_row.index)
        if len(common) < 6:
            continue

        s_common = s_row.loc[common]
        r_common = r_row.loc[common]

        tercile_33 = s_common.quantile(1 / 3)
        tercile_67 = s_common.quantile(2 / 3)

        longs = r_common[s_common >= tercile_67]
        shorts = r_common[s_common <= tercile_33]

        if len(longs) == 0 or len(shorts) == 0:
            continue

        pnl = longs.mean() - shorts.mean()
        monthly_pnl.append(pnl)

    if len(monthly_pnl) < 6:
        return np.nan

    pnl_series = np.array(monthly_pnl)
    sharpe = (np.mean(pnl_series) / np.std(pnl_series, ddof=1)) * np.sqrt(12)
    return float(sharpe)


# ============================================================================
# MAIN TEST RUNNER
# ============================================================================


def run_test(
    label: str,
    scores: pd.DataFrame,
    monthly_rets: pd.DataFrame,
    n_lags_min: int,
    n_lags_max: int,
    forward_shift: int = -1,
) -> dict:
    """Run all ICs and Sharpe for a given scores panel."""
    ts_ic, ts_std, ts_tstat, n_instr, n_pairs = compute_ts_ic(
        scores, monthly_rets, forward_shift=forward_shift
    )
    xs_ic, xs_std, xs_tstat, n_months = compute_xs_ic(
        scores, monthly_rets, forward_shift=forward_shift
    )
    ls_sharpe = compute_ls_sharpe(scores, monthly_rets, forward_shift=forward_shift)

    return {
        "label": label,
        "n_lags_min": n_lags_min,
        "n_lags_max": n_lags_max,
        "n_instr": n_instr,
        "n_pairs": n_pairs,
        "n_months": n_months,
        "ts_ic": ts_ic,
        "ts_tstat": ts_tstat,
        "xs_ic": xs_ic,
        "xs_tstat": xs_tstat,
        "ls_sharpe": ls_sharpe,
    }


def print_results(results: list[dict]) -> None:
    header = (
        f"{'Label':<22} | {'N_instr':>7} | {'N_pairs':>7} | {'N_months':>8} | "
        f"{'TS_IC':>6} | {'TS_t':>5} | {'XS_IC':>6} | {'XS_t':>5} | {'L/S Sharpe':>10}"
    )
    sep = "-" * len(header)
    print()
    print("=== SEASONALITY TEST RESULTS ===")
    print(sep)
    print(header)
    print(sep)
    for r in results:
        ls = f"{r['ls_sharpe']:.2f}" if not np.isnan(r["ls_sharpe"]) else "  N/A"
        print(
            f"{r['label']:<22} | {r['n_instr']:>7} | {r['n_pairs']:>7} | {r['n_months']:>8} | "
            f"{r['ts_ic']:>+6.3f} | {r['ts_tstat']:>+5.2f} | "
            f"{r['xs_ic']:>+6.3f} | {r['xs_tstat']:>+5.2f} | {ls:>10}"
        )
    print(sep)
    print()


def main():
    print("Loading dataset...")
    prices = load_prices()
    print(f"  {prices.shape[1]} instruments, {len(prices)} daily rows, "
          f"{prices.index[0].date()} to {prices.index[-1].date()}")

    monthly = prices.resample("ME").last()
    monthly_rets = monthly.pct_change()

    # ===========================================================================
    # MODE A: predict_next_month=True
    # Score at month M end uses M+1's historical same-month returns.
    # Predicts M+1's return. This is the correct trading framing.
    # Pair with forward_shift=-1 (next month's actual return).
    # ===========================================================================
    print("\n--- MODE A: Predict NEXT month (trading-relevant) ---")
    print("Score at end of March uses April's historical returns → predicts April return")
    results_a = []
    for label, lmin, lmax in [("3yr (t-2, t-3)", 2, 3), ("5yr (t-2..t-5)", 2, 5)]:
        print(f"Computing scores: {label}...")
        scores = compute_seasonality_scores(
            prices, n_lags_min=lmin, n_lags_max=lmax, predict_next_month=True
        )
        results_a.append(run_test(label, scores, monthly_rets, lmin, lmax, forward_shift=-1))

    scores_placebo_a = compute_seasonality_scores(
        prices, n_lags_min=2, n_lags_max=5, placebo_offset=6, predict_next_month=True
    )
    results_a.append(
        run_test("Placebo (+6mo offset)", scores_placebo_a, monthly_rets, 2, 5, forward_shift=-1)
    )
    print_results(results_a)

    # ===========================================================================
    # MODE B: predict_next_month=False
    # Score at month M end uses M's historical same-month returns.
    # Tests same-month pattern: "April has historically been strong → April is strong"
    # Pair with forward_shift=0 (same month's actual return — no look-ahead since
    # we skip t-1 and t-0 is the current month's return, already in the past by end-of-month).
    # ===========================================================================
    print("--- MODE B: Predict SAME month (contemporaneous, signal computable before month begins) ---")
    print("Score for April computed from prior Aprils (skip t-1) → predicts April return")
    results_b = []
    for label, lmin, lmax in [("3yr (t-2, t-3)", 2, 3), ("5yr (t-2..t-5)", 2, 5)]:
        print(f"Computing scores: {label}...")
        scores = compute_seasonality_scores(
            prices, n_lags_min=lmin, n_lags_max=lmax, predict_next_month=False
        )
        results_b.append(run_test(label, scores, monthly_rets, lmin, lmax, forward_shift=0))

    scores_placebo_b = compute_seasonality_scores(
        prices, n_lags_min=2, n_lags_max=5, placebo_offset=6, predict_next_month=False
    )
    results_b.append(
        run_test("Placebo (+6mo offset)", scores_placebo_b, monthly_rets, 2, 5, forward_shift=0)
    )
    print_results(results_b)

    # ===========================================================================
    # SUBGROUP ANALYSIS (best mode based on primary results)
    # ===========================================================================
    # Use mode with stronger signal (compare TS IC)
    use_mode = "a" if abs(results_a[0]["ts_ic"]) >= abs(results_b[0]["ts_ic"]) else "b"
    best_results = results_a if use_mode == "a" else results_b
    best_predict_next = use_mode == "a"
    best_shift = -1 if use_mode == "a" else 0

    print(f"=== SUBGROUP ANALYSIS (5yr window, mode {'A: predict next' if use_mode=='a' else 'B: same-month'}) ===")
    scores_5yr = compute_seasonality_scores(
        prices, n_lags_min=2, n_lags_max=5, predict_next_month=best_predict_next
    )
    subgroups = [
        ("Majors", [c for c in scores_5yr.columns if c in MAJORS]),
        ("Alts", [c for c in scores_5yr.columns if c not in MAJORS]),
        (
            "History ≥4yr",
            [
                c for c in scores_5yr.columns
                if prices[c].dropna().shape[0] > 100
                and prices[c].dropna().index[0] <= pd.Timestamp("2022-04-01")
            ],
        ),
        (
            "History ≥3yr",
            [
                c for c in scores_5yr.columns
                if prices[c].dropna().shape[0] > 100
                and prices[c].dropna().index[0] <= pd.Timestamp("2023-04-01")
            ],
        ),
    ]

    sub_results = []
    for label, instrs in subgroups:
        if not instrs:
            continue
        s_sub = scores_5yr[[c for c in instrs if c in scores_5yr.columns]]
        r_sub = monthly_rets[[c for c in instrs if c in monthly_rets.columns]]
        sub_results.append(run_test(label, s_sub, r_sub, 2, 5, forward_shift=best_shift))
    print_results(sub_results)

    # ===========================================================================
    # INTERPRETATION
    # ===========================================================================
    # Use the best-performing mode for the verdict
    primary_a = results_a[0]
    primary_b = results_b[0]
    placebo_a = results_a[2]
    placebo_b = results_b[2]

    best_ts_ic = max(primary_a["ts_ic"], primary_b["ts_ic"], key=abs)
    best_ts_tstat = primary_a["ts_tstat"] if abs(primary_a["ts_ic"]) >= abs(primary_b["ts_ic"]) else primary_b["ts_tstat"]
    best_xs_ic = max(primary_a["xs_ic"], primary_b["xs_ic"], key=abs)
    best_xs_tstat = primary_a["xs_tstat"] if abs(primary_a["xs_ic"]) >= abs(primary_b["xs_ic"]) else primary_b["xs_tstat"]
    placebo_clean = (
        abs(placebo_a["ts_ic"]) < 0.02 and abs(placebo_a["xs_ic"]) < 0.02
        and abs(placebo_b["ts_ic"]) < 0.02 and abs(placebo_b["xs_ic"]) < 0.02
    )

    ts_pass = best_ts_ic >= 0.02 and best_ts_tstat >= 1.5
    xs_pass = best_xs_ic >= 0.02 and best_xs_tstat >= 1.5

    print("=== INTERPRETATION ===")
    print(f"  Best TS IC (mode {'A' if use_mode=='a' else 'B'}): {best_ts_ic:+.3f}, t={best_ts_tstat:+.2f}")
    print(f"  Best XS IC: {best_xs_ic:+.3f}, t={best_xs_tstat:+.2f}")
    print(f"  TS IC threshold (≥0.02, t≥1.5): {'PASS' if ts_pass else 'FAIL'}")
    print(f"  XS IC threshold (≥0.02, t≥1.5): {'PASS' if xs_pass else 'FAIL'}")
    print(f"  Placebo near zero:               {'PASS' if placebo_clean else 'FAIL'}")

    if (ts_pass or xs_pass) and placebo_clean:
        print()
        print("  VERDICT: Evidence for seasonality effect — proceed with rule implementation.")
        if best_ts_ic < 0:
            print("  NOTE: TS IC is negative — rule should NEGATE the score (contrarian seasonality).")
    elif ts_pass or xs_pass:
        print()
        print("  VERDICT: Marginal evidence but placebo not clean — interpret with caution.")
    else:
        print()
        print("  VERDICT: Insufficient evidence — do not add rule to ensemble.")


if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    main()
