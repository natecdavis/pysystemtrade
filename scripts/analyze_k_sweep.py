#!/usr/bin/env python3
"""
Diagnose why higher K underperforms in the flat-68 SB-corrected K-sweep.

Reads each per-K backtest output dir under out/k_sweep_flat68_sb_1k/, joins
position/forecast diagnostics with prices+ADV from the dataset, and writes
DIAGNOSIS.md + diagnosis.json with six decompositions:

  1. Capital-clipping fraction (forecast≠0 but position=0)
  2. Effective number of bets (1 / sum w_i^2) vs nominal K
  3. Per-rank-bucket PnL contribution (top-30 ADV vs lower buckets)
  4. Cost decomposition (transaction cost vs funding drag, per K)
  5. Realized vol vs target (25%)
  6. Upper-K empirical limit (median active position $; fraction < $10 floor)

Usage:
    python scripts/analyze_k_sweep.py [--sweep-dir out/k_sweep_flat68_sb_1k]
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

DEFAULT_SWEEP_DIR = "out/k_sweep_flat68_sb_1k"
DEFAULT_DATASET   = "data/dataset_sb_corrected_6yr_jagged.parquet"
DEFAULT_CAPITAL   = 9745.58           # capital used in 1k config (= equity × 2.5)
MIN_NOTIONAL      = 10.0              # min_notional_position in config
RANK_BUCKETS      = [(1, 30), (31, 60), (61, 100), (101, 150), (151, 1000)]


def discover_runs(sweep_dir: Path) -> dict[int, Path]:
    """Map K → backtest_kKKK directory."""
    runs = {}
    for sub in sweep_dir.iterdir():
        if not sub.is_dir() or not sub.name.startswith("backtest_k"):
            continue
        try:
            K = int(sub.name.replace("backtest_k", ""))
        except ValueError:
            continue
        if (sub / "performance_summary.json").exists():
            runs[K] = sub
    return dict(sorted(runs.items()))


def load_dataset(path: str):
    """Load long-format dataset and reshape to wide price + ADV frames."""
    raw = pd.read_parquet(path)
    raw["date"] = pd.to_datetime(raw["date"])
    prices = raw.pivot(index="date", columns="instrument", values="close")
    adv    = raw.pivot(index="date", columns="instrument", values="adv_notional")
    return prices, adv


def diagnose_one_K(
    K: int,
    run_dir: Path,
    prices: pd.DataFrame,
    adv: pd.DataFrame,
    capital: float,
) -> dict:
    """Compute all six diagnostics for a single K."""
    diag = pd.read_parquet(run_dir / "diagnostics.parquet")
    diag["date"] = pd.to_datetime(diag["date"])
    perf = json.load(open(run_dir / "performance_summary.json"))

    # Filter to dates that exist in price frame (intersection)
    diag = diag[diag["date"].isin(prices.index)]

    # ----- Notional value of each position cell (long-format) -----
    diag = diag.merge(
        prices.stack().rename("price").reset_index().rename(
            columns={"level_1": "instrument"}
        ),
        on=["date", "instrument"],
        how="left",
    )
    diag["notional"] = diag["position"].abs() * diag["price"]

    # ----- 1. Capital-clipping fraction -----
    # Only count cells where the instrument was IN the active universe
    # (instrument_weight > 0) AND had a non-zero forecast — those are the
    # cells the strategy intended to take a position in. A zero realized
    # position there means the min-notional / min-fraction floor clipped it.
    in_universe       = diag["instrument_weight"].abs() > 1e-12
    has_forecast      = diag["combined_forecast"].abs() > 1e-9
    eligible_cells    = (in_universe & has_forecast)
    eligible_n        = int(eligible_cells.sum())
    clipped_cells_n   = int(
        (eligible_cells & (diag["position"].abs() < 1e-12)).sum()
    )
    clip_frac = clipped_cells_n / eligible_n if eligible_n > 0 else 0.0

    # ----- 6a. Median active notional & fraction below $10 floor -----
    active = diag[diag["position"].abs() > 1e-12].copy()
    median_notional   = active["notional"].median() if len(active) else 0.0
    p25_notional      = active["notional"].quantile(0.25) if len(active) else 0.0
    p10_notional      = active["notional"].quantile(0.10) if len(active) else 0.0
    frac_below_min    = (active["notional"] < MIN_NOTIONAL).mean() if len(active) else 0.0

    # ----- 2. Effective number of bets per date, average -----
    # Effective bets = 1 / sum(w_i^2) where w_i = notional_i / sum(|notional|)
    pivot_notional = diag.pivot_table(
        index="date", columns="instrument", values="notional", aggfunc="sum"
    ).fillna(0.0)
    row_sum = pivot_notional.sum(axis=1)
    weights = pivot_notional.div(row_sum.replace(0, np.nan), axis=0).fillna(0.0)
    eff_bets_series = 1.0 / (weights ** 2).sum(axis=1).replace(0, np.nan)
    avg_eff_bets    = eff_bets_series.mean(skipna=True)

    # ----- 3. Per-rank-bucket PnL contribution -----
    # Forward 1-day return × notional position − transaction cost approx.
    # PnL for a position held in lots: lots × ΔP. In USD terms = notional × (P_{t+1}/P_t − 1).
    fwd_ret = prices.pct_change().shift(-1)
    fwd_ret_long = (
        fwd_ret.stack().rename("fwd_ret").reset_index()
        .rename(columns={"level_1": "instrument"})
    )
    diag = diag.merge(fwd_ret_long, on=["date", "instrument"], how="left")
    # ADV rank per day (among instruments with non-null ADV)
    adv_rank = adv.rank(axis=1, ascending=False, method="min")
    adv_rank_long = (
        adv_rank.stack().rename("adv_rank").reset_index()
        .rename(columns={"level_1": "instrument"})
    )
    diag = diag.merge(adv_rank_long, on=["date", "instrument"], how="left")

    # PnL contribution per row = position * price * fwd_ret = signed notional * fwd_ret
    diag["signed_notional"] = diag["position"] * diag["price"]
    diag["pnl_usd"] = diag["signed_notional"] * diag["fwd_ret"]

    bucket_pnl = {}
    for lo, hi in RANK_BUCKETS:
        mask = (diag["adv_rank"] >= lo) & (diag["adv_rank"] <= hi)
        pnl = diag.loc[mask, "pnl_usd"].sum()
        bucket_pnl[f"rank_{lo:03d}_{hi:03d}"] = float(pnl)

    total_pnl_usd = float(diag["pnl_usd"].sum())
    bucket_pnl_share = {
        k: (v / total_pnl_usd if abs(total_pnl_usd) > 1e-9 else 0.0)
        for k, v in bucket_pnl.items()
    }

    # ----- 4. Cost decomposition (from perf summary) -----
    cost_model = perf.get("cost_model", {})
    transaction_cost_ann = cost_model.get("transaction_cost_ann", float("nan"))
    funding_drag_ann     = cost_model.get("funding_drag_ann",     float("nan"))

    # ----- 5. Realized vol vs target -----
    metrics = perf.get("metrics", {})
    realized_vol = metrics.get("ann_vol", float("nan"))
    cagr         = metrics.get("cagr",    float("nan"))
    sharpe       = metrics.get("sharpe",  float("nan"))
    calmar       = metrics.get("calmar",  float("nan"))
    max_dd       = metrics.get("max_dd",  float("nan"))
    portfolio    = perf.get("portfolio", {})

    return {
        "K": K,
        "sharpe": sharpe,
        "calmar": calmar,
        "cagr": cagr,
        "max_dd": max_dd,
        "realized_vol": realized_vol,
        "vol_target": 0.25,
        "vol_realized_over_target": realized_vol / 0.25,
        "transaction_cost_ann": transaction_cost_ann,
        "funding_drag_ann": funding_drag_ann,
        "annual_turnover": portfolio.get("annual_turnover", float("nan")),
        "avg_active_positions": portfolio.get("avg_active_positions", float("nan")),
        "n_instruments_in_dataset": portfolio.get("n_instruments", 0),
        # Diagnostic 1
        "clip_fraction": float(clip_frac),
        "eligible_cells": eligible_n,
        "clipped_cells": clipped_cells_n,
        # Diagnostic 2
        "avg_effective_bets": float(avg_eff_bets) if pd.notna(avg_eff_bets) else 0.0,
        "eff_bets_over_K": float(avg_eff_bets) / K if pd.notna(avg_eff_bets) else 0.0,
        # Diagnostic 3
        "bucket_pnl_usd": bucket_pnl,
        "bucket_pnl_share": bucket_pnl_share,
        "total_pnl_usd": total_pnl_usd,
        # Diagnostic 6
        "median_active_notional": float(median_notional),
        "p25_active_notional": float(p25_notional),
        "p10_active_notional": float(p10_notional),
        "frac_below_min_notional": float(frac_below_min),
    }


def write_markdown(report: dict, path: Path) -> None:
    """Write DIAGNOSIS.md."""
    rows = report["per_K"]
    lines = []
    a = lines.append

    a("# K-sweep diagnosis — flat-68 SB-corrected, 1k config (HL filter)")
    a("")
    a(f"Capital: ${report['capital']:.2f}  (equity ${report['capital']/2.5:.2f} × leverage 2.5)")
    a(f"Dataset: `{report['dataset']}` (469 SB-corrected instruments)")
    a(f"Min-notional floor: ${MIN_NOTIONAL}")
    a("")

    # Headline table
    a("## Headline metrics")
    a("")
    a("| K | eb | ex | Sharpe | Calmar | CAGR | MaxDD | RealVol | Turn | AvgPos |")
    a("|---|----|----|--------|--------|------|-------|---------|------|--------|")
    for r in rows:
        eb, ex = max(1, round(r["K"] / 15)), max(3, round(r["K"] / 3))
        a(
            f"| {r['K']} | {eb} | {ex} | "
            f"{r['sharpe']:.4f} | {r['calmar']:.4f} | {r['cagr']*100:.2f}% | "
            f"{r['max_dd']*100:.2f}% | {r['realized_vol']*100:.2f}% | "
            f"{r['annual_turnover']:.1f} | {r['avg_active_positions']:.1f} |"
        )
    a("")

    # Diagnostic 1: clipping
    a("## 1. Capital-clipping fraction")
    a("")
    a("Fraction of (date × instrument) cells where the instrument was in the active universe (`instrument_weight > 0`) and had a non-zero forecast, but the realized position rounded to zero — instruments the strategy *wanted* to take but couldn't due to the $10 min-notional / 3% min-fraction floor.")
    a("")
    a("| K | clip_fraction | eligible_cells | clipped_cells |")
    a("|---|---------------|----------------|---------------|")
    for r in rows:
        a(f"| {r['K']} | {r['clip_fraction']*100:.2f}% | {r['eligible_cells']:,} | {r['clipped_cells']:,} |")
    a("")

    # Diagnostic 2: effective bets
    a("## 2. Effective number of bets")
    a("")
    a("Inverse Herfindahl on |notional| weights (1 / Σ wᵢ²). Compares to nominal K — divergence reveals concentration in a few instruments.")
    a("")
    a("| K | avg_eff_bets | eff_bets / K |")
    a("|---|--------------|--------------|")
    for r in rows:
        a(f"| {r['K']} | {r['avg_effective_bets']:.2f} | {r['eff_bets_over_K']*100:.1f}% |")
    a("")

    # Diagnostic 3: rank-bucket PnL
    a("## 3. Per-rank-bucket PnL share (by ADV rank)")
    a("")
    a("Where in the ADV ranking does PnL come from? Top-30 ADV vs lower buckets. If lower buckets contribute negative or trivially positive net of costs, expanding K beyond 30 isn't 'free' diversification.")
    a("")
    bucket_keys = list(rows[0]["bucket_pnl_share"].keys())
    header = "| K | total_PnL_USD | " + " | ".join(bucket_keys) + " |"
    sep = "|---|" + "---|" * (len(bucket_keys) + 1)
    a(header)
    a(sep)
    for r in rows:
        cells = " | ".join(
            f"{r['bucket_pnl_share'][k]*100:.1f}%" for k in bucket_keys
        )
        a(f"| {r['K']} | ${r['total_pnl_usd']:,.0f} | {cells} |")
    a("")

    # Diagnostic 4: cost
    a("## 4. Cost decomposition")
    a("")
    a("| K | tx_cost_ann | funding_drag_ann | total_cost_ann |")
    a("|---|-------------|------------------|----------------|")
    for r in rows:
        total = r["transaction_cost_ann"] + r["funding_drag_ann"]
        a(
            f"| {r['K']} | {r['transaction_cost_ann']*100:.3f}% | "
            f"{r['funding_drag_ann']*100:.3f}% | {total*100:.3f}% |"
        )
    a("")

    # Diagnostic 5: realized vol
    a("## 5. Realized vol vs 25% target")
    a("")
    a("| K | realized_vol | realized / target |")
    a("|---|--------------|-------------------|")
    for r in rows:
        a(f"| {r['K']} | {r['realized_vol']*100:.2f}% | {r['vol_realized_over_target']*100:.1f}% |")
    a("")

    # Diagnostic 6: upper-K limit
    a("## 6. Upper-K empirical limit")
    a("")
    a("Median active-position notional and fraction below the $10 min-notional floor. The 'structural cap' is the K at which the strategy can't take its intended positions for a meaningful share of the time.")
    a("")
    a("| K | median_active_$ | p25_$ | p10_$ | frac_below_$10 |")
    a("|---|-----------------|-------|-------|----------------|")
    for r in rows:
        a(
            f"| {r['K']} | ${r['median_active_notional']:.2f} | "
            f"${r['p25_active_notional']:.2f} | ${r['p10_active_notional']:.2f} | "
            f"{r['frac_below_min_notional']*100:.2f}% |"
        )
    a("")

    a("---")
    a("")
    a("Generated by `scripts/analyze_k_sweep.py`.")

    path.write_text("\n".join(lines))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sweep-dir", default=DEFAULT_SWEEP_DIR)
    parser.add_argument("--dataset",   default=DEFAULT_DATASET)
    parser.add_argument("--capital",   type=float, default=DEFAULT_CAPITAL)
    args = parser.parse_args()

    sweep_dir = Path(args.sweep_dir)
    runs = discover_runs(sweep_dir)
    if not runs:
        print(f"No backtest_k* runs found in {sweep_dir}")
        return 1

    print(f"Discovered {len(runs)} runs: K = {sorted(runs.keys())}")
    print(f"Loading dataset {args.dataset}...")
    prices, adv = load_dataset(args.dataset)
    print(f"  prices shape: {prices.shape}, dates: {prices.index.min().date()}{prices.index.max().date()}")

    per_K = []
    for K, run_dir in runs.items():
        print(f"\nDiagnosing K={K} from {run_dir.name}...")
        rec = diagnose_one_K(K, run_dir, prices, adv, args.capital)
        per_K.append(rec)
        print(
            f"  Sharpe={rec['sharpe']:.4f} | clip={rec['clip_fraction']*100:.2f}% | "
            f"eff_bets={rec['avg_effective_bets']:.1f} | "
            f"med_active=${rec['median_active_notional']:.2f}"
        )

    report = {
        "capital": args.capital,
        "dataset": args.dataset,
        "min_notional": MIN_NOTIONAL,
        "per_K": per_K,
    }

    json_path = sweep_dir / "diagnosis.json"
    md_path   = sweep_dir / "DIAGNOSIS.md"
    json_path.write_text(json.dumps(report, indent=2, default=float))
    write_markdown(report, md_path)

    print(f"\nWrote {json_path}")
    print(f"Wrote {md_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
