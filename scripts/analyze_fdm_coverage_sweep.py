#!/usr/bin/env python3
"""
Diagnose the coverage-aware FDM sweep at flat-68 SB-corrected, 1k config.

Reads each per-alpha backtest dir under out/fdm_cov_sweep/ and writes
DIAGNOSIS.md + diagnosis.json comparing alpha in {0, 0.5, 1.0}.

Six outputs:
  1. Headline metrics per alpha (Sharpe, Calmar, CAGR, MaxDD, RealVol)
  2. FDM distribution per alpha (mean, median, frac at 2.5 cap, frac < 1.5)
  3. Coverage panel — backed out from fdm ratios across alpha runs:
        coverage = (fdm[alpha=X] / fdm[alpha=0]) ** (1/X)   for X > 0
  4. Coverage-decile PnL stratification per alpha
  5. Example trajectories: 3 lowest-coverage instruments × 3 alphas
  6. Verdict: adopt / reject / mixed

Usage:
    python scripts/analyze_fdm_coverage_sweep.py [--sweep-dir out/fdm_cov_sweep]
"""
import argparse
import json
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

DEFAULT_SWEEP_DIR = "out/fdm_cov_sweep"
DEFAULT_DATASET   = "data/dataset_sb_corrected_6yr_jagged.parquet"
COVERAGE_BUCKETS  = [(0.0, 0.25), (0.25, 0.50), (0.50, 0.75), (0.75, 0.95), (0.95, 1.01)]


def discover_runs(sweep_dir: Path) -> dict[float, Path]:
    """Map alpha → backtest_aXpYY directory."""
    runs = {}
    for sub in sweep_dir.iterdir():
        if not sub.is_dir() or not sub.name.startswith("backtest_a"):
            continue
        m = re.match(r"backtest_a(\d+p\d+)$", sub.name)
        if not m:
            continue
        alpha = float(m.group(1).replace("p", "."))
        if (sub / "performance_summary.json").exists():
            runs[alpha] = sub
    return dict(sorted(runs.items()))


def load_diag(run_dir: Path) -> pd.DataFrame:
    df = pd.read_parquet(run_dir / "diagnostics.parquet")
    df["date"] = pd.to_datetime(df["date"])
    return df


def load_perf(run_dir: Path) -> dict:
    return json.load(open(run_dir / "performance_summary.json"))


def headline_table(runs: dict[float, Path]) -> list[dict]:
    rows = []
    for alpha, run_dir in runs.items():
        p = load_perf(run_dir)
        m = p["metrics"]; pf = p["portfolio"]; cm = p.get("cost_model", {})
        rows.append({
            "alpha": alpha,
            "sharpe": m["sharpe"],
            "calmar": m["calmar"],
            "cagr": m["cagr"],
            "max_dd": m["max_dd"],
            "ann_vol": m["ann_vol"],
            "annual_turnover": pf["annual_turnover"],
            "avg_active_positions": pf["avg_active_positions"],
            "transaction_cost_ann": cm.get("transaction_cost_ann", float("nan")),
            "funding_drag_ann": cm.get("funding_drag_ann", float("nan")),
        })
    return rows


def fdm_distribution(runs: dict[float, Path]) -> list[dict]:
    rows = []
    for alpha, run_dir in runs.items():
        diag = load_diag(run_dir)
        fdm = diag["fdm"].dropna()
        rows.append({
            "alpha": alpha,
            "n_obs": int(len(fdm)),
            "mean": float(fdm.mean()),
            "median": float(fdm.median()),
            "p10": float(fdm.quantile(0.10)),
            "p90": float(fdm.quantile(0.90)),
            "frac_at_cap_2_5": float((fdm > 2.49).mean()),
            "frac_below_1_5": float((fdm < 1.5).mean()),
            "frac_below_1_0": float((fdm < 1.0).mean()),
        })
    return rows


def derive_coverage(diag_a0: pd.DataFrame, diag_aX: pd.DataFrame, alpha: float) -> pd.DataFrame:
    """coverage = (fdm[α=X] / fdm[α=0]) ** (1/X), aligned on (date, instrument)."""
    if alpha <= 0:
        raise ValueError("Need alpha > 0 to back out coverage")
    a0 = diag_a0[["date", "instrument", "fdm", "position", "combined_forecast"]]
    aX = diag_aX[["date", "instrument", "fdm"]].rename(columns={"fdm": f"fdm_a{alpha}"})
    m = a0.merge(aX, on=["date", "instrument"], how="inner")
    valid = (m["fdm"] > 1e-9) & (m[f"fdm_a{alpha}"] > 1e-9)
    m["coverage"] = np.nan
    m.loc[valid, "coverage"] = (m.loc[valid, f"fdm_a{alpha}"] / m.loc[valid, "fdm"]).clip(0.0, 1.0).pow(1.0 / alpha)
    return m


def coverage_decile_pnl(
    runs: dict[float, Path], coverage_df: pd.DataFrame, prices: pd.DataFrame
) -> dict:
    """For each alpha: bucket (date, instrument) cells by coverage and sum signed-PnL."""
    fwd_ret = prices.pct_change().shift(-1)
    fwd_long = (
        fwd_ret.stack().rename("fwd_ret").reset_index()
        .rename(columns={"level_1": "instrument"})
    )
    cov_df = coverage_df[["date", "instrument", "coverage"]].copy()
    bucket_labels = [f"[{lo:.2f}-{hi:.2f})" for lo, hi in COVERAGE_BUCKETS]

    out = {"buckets": bucket_labels}
    for alpha, run_dir in runs.items():
        diag = load_diag(run_dir)
        diag = diag.merge(prices.stack().rename("price").reset_index().rename(
            columns={"level_1": "instrument"}), on=["date", "instrument"], how="left")
        diag = diag.merge(fwd_long, on=["date", "instrument"], how="left")
        diag = diag.merge(cov_df, on=["date", "instrument"], how="left")
        diag["pnl_usd"] = diag["position"] * diag["price"] * diag["fwd_ret"]
        bucket_pnl = []
        for lo, hi in COVERAGE_BUCKETS:
            mask = (diag["coverage"] >= lo) & (diag["coverage"] < hi)
            bucket_pnl.append(float(diag.loc[mask, "pnl_usd"].sum()))
        total = float(diag["pnl_usd"].sum())
        out[f"alpha_{alpha}"] = {
            "total_pnl_usd": total,
            "bucket_pnl_usd": dict(zip(bucket_labels, bucket_pnl)),
            "bucket_share": {
                lab: (v / total if abs(total) > 1e-9 else 0.0)
                for lab, v in zip(bucket_labels, bucket_pnl)
            },
        }
    return out


def example_trajectories(runs: dict[float, Path], coverage_df: pd.DataFrame, n: int = 3) -> list[dict]:
    """Pick the lowest-mean-coverage instruments and show their FDM/position across alpha."""
    inst_cov = coverage_df.groupby("instrument")["coverage"].mean().dropna().sort_values()
    chosen = inst_cov.head(n).index.tolist()
    rows = []
    diag_by_alpha = {alpha: load_diag(rd) for alpha, rd in runs.items()}
    for inst in chosen:
        rec = {"instrument": inst, "mean_coverage": float(inst_cov.loc[inst])}
        for alpha, diag in diag_by_alpha.items():
            sub = diag[diag["instrument"] == inst]
            rec[f"alpha_{alpha}_mean_fdm"] = float(sub["fdm"].mean())
            rec[f"alpha_{alpha}_mean_abs_position_usd"] = float(sub["position"].abs().mean())
        rows.append(rec)
    return rows


def write_markdown(report: dict, path: Path) -> None:
    a = []
    p = a.append
    p("# FDM coverage-aware sweep — flat-68 SB-corrected, 1k config (HL filter)")
    p("")
    p("Tested `FDM_eff = FDM_base × (n_active_rules / n_total_rules) ** alpha` for alpha ∈ {0, 0.5, 1.0}.")
    p("alpha=0 reproduces baseline (no override). alpha=1 fully linear coverage-dampening.")
    p("")
    # Headline
    p("## 1. Headline metrics")
    p("")
    p("| alpha | Sharpe | Calmar | CAGR | MaxDD | RealVol | Turn | AvgPos |")
    p("|-------|--------|--------|------|-------|---------|------|--------|")
    for r in report["headline"]:
        p(
            f"| {r['alpha']:.2f} | {r['sharpe']:.4f} | {r['calmar']:.4f} | "
            f"{r['cagr']*100:.2f}% | {r['max_dd']*100:.2f}% | "
            f"{r['ann_vol']*100:.2f}% | {r['annual_turnover']:.1f} | "
            f"{r['avg_active_positions']:.1f} |"
        )
    p("")
    # Deltas vs alpha=0
    if any(r["alpha"] == 0.0 for r in report["headline"]):
        base = next(r for r in report["headline"] if r["alpha"] == 0.0)
        p("## 2. Δ vs alpha=0 (baseline)")
        p("")
        p("| alpha | ΔSharpe | ΔCalmar | ΔCAGR | ΔMaxDD (pp) |")
        p("|-------|---------|---------|-------|-------------|")
        for r in report["headline"]:
            if r["alpha"] == 0.0:
                continue
            p(
                f"| {r['alpha']:.2f} | {r['sharpe']-base['sharpe']:+.4f} | "
                f"{r['calmar']-base['calmar']:+.4f} | "
                f"{(r['cagr']-base['cagr'])*100:+.2f}% | "
                f"{(r['max_dd']-base['max_dd'])*100:+.2f} |"
            )
        p("")
    # FDM distribution
    p("## 3. FDM distribution per alpha")
    p("")
    p("Confirms the dampening actually moved FDM. With alpha>0, mean/median should fall and frac_at_cap should drop.")
    p("")
    p("| alpha | n | mean | median | p10 | p90 | frac@2.5 | frac<1.5 | frac<1.0 |")
    p("|-------|---|------|--------|-----|-----|----------|----------|----------|")
    for r in report["fdm_distribution"]:
        p(
            f"| {r['alpha']:.2f} | {r['n_obs']:,} | {r['mean']:.4f} | "
            f"{r['median']:.4f} | {r['p10']:.4f} | {r['p90']:.4f} | "
            f"{r['frac_at_cap_2_5']*100:.2f}% | {r['frac_below_1_5']*100:.2f}% | "
            f"{r['frac_below_1_0']*100:.2f}% |"
        )
    p("")
    # Coverage-decile PnL
    p("## 4. PnL share by coverage bucket")
    p("")
    p("Coverage = fraction of rules firing for that (date, instrument) cell. Backed out from FDM ratios. Each row sums to 100% per alpha.")
    p("")
    cd = report["coverage_decile_pnl"]
    buckets = cd["buckets"]
    header = "| alpha | total_PnL_$ | " + " | ".join(buckets) + " |"
    sep = "|---|" + "---|" * (len(buckets) + 1)
    p(header)
    p(sep)
    for alpha in sorted(set(report["headline"][i]["alpha"] for i in range(len(report["headline"])))):
        key = f"alpha_{alpha}"
        if key not in cd:
            continue
        row = cd[key]
        cells = " | ".join(f"{row['bucket_share'][b]*100:.1f}%" for b in buckets)
        p(f"| {alpha:.2f} | ${row['total_pnl_usd']:,.0f} | {cells} |")
    p("")
    # Example trajectories
    p("## 5. Lowest-coverage instrument examples")
    p("")
    p("Mean position size and FDM for the 3 instruments with lowest mean coverage. Shows the dampening at the edge.")
    p("")
    if report["examples"]:
        first = report["examples"][0]
        alpha_keys = [k for k in first if k.startswith("alpha_") and "fdm" in k]
        alphas_in = sorted(set(float(k.split("_")[1]) for k in alpha_keys))
        head = "| instrument | mean_cov | " + " | ".join(
            f"FDM (a={a})" for a in alphas_in
        ) + " | " + " | ".join(f"|pos|$ (a={a})" for a in alphas_in) + " |"
        sep = "|---|---|" + "---|" * (2 * len(alphas_in))
        p(head)
        p(sep)
        for ex in report["examples"]:
            fdms = " | ".join(
                f"{ex.get(f'alpha_{a}_mean_fdm', float('nan')):.3f}" for a in alphas_in
            )
            poss = " | ".join(
                f"${ex.get(f'alpha_{a}_mean_abs_position_usd', float('nan')):.2f}" for a in alphas_in
            )
            p(f"| {ex['instrument']} | {ex['mean_coverage']*100:.1f}% | {fdms} | {poss} |")
    p("")
    # Verdict
    p("## 6. Verdict")
    p("")
    p(report.get("verdict_text", "_(no verdict computed)_"))
    p("")
    p("---")
    p("Generated by `scripts/analyze_fdm_coverage_sweep.py`.")
    path.write_text("\n".join(a))


def compute_verdict(headline: list[dict]) -> tuple[str, str | None]:
    """Apply the decision rule from the plan."""
    base = next((r for r in headline if r["alpha"] == 0.0), None)
    if base is None:
        return ("inconclusive — no alpha=0 baseline", None)
    candidates = [r for r in headline if r["alpha"] > 0]
    winners = []
    for r in candidates:
        d_sharpe = r["sharpe"] - base["sharpe"]
        d_calmar = r["calmar"] - base["calmar"]
        d_max_dd_pp = (r["max_dd"] - base["max_dd"]) * 100  # negative = worse
        if d_sharpe >= 0.01 and d_calmar >= 0 and d_max_dd_pp >= -0.5:
            winners.append((r["alpha"], d_sharpe, d_calmar, d_max_dd_pp))
    if not winners:
        text = (
            "**REJECT.** No alpha satisfies the decision rule "
            "(ΔSharpe ≥ +0.01, ΔCalmar ≥ 0, ΔMaxDD regression ≤ 0.5pp). "
            "Coverage-aware FDM dampening does not help on this data — the "
            "implicit coverage handling in the existing correlation-based "
            "FDM is sufficient."
        )
        return text, None
    winners.sort(key=lambda x: x[1], reverse=True)
    best_alpha, dS, dC, dMDD = winners[0]
    text = (
        f"**ADOPT alpha={best_alpha}.** ΔSharpe={dS:+.4f}, ΔCalmar={dC:+.4f}, "
        f"ΔMaxDD={dMDD:+.2f}pp. Update config/crypto_perps_1k.yaml with "
        f"`use_coverage_aware_fdm: true` and `fdm_coverage_alpha: {best_alpha}`."
    )
    return text, best_alpha


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sweep-dir", default=DEFAULT_SWEEP_DIR)
    parser.add_argument("--dataset",   default=DEFAULT_DATASET)
    args = parser.parse_args()

    sweep_dir = Path(args.sweep_dir)
    runs = discover_runs(sweep_dir)
    if not runs:
        print(f"No backtest_a* runs found in {sweep_dir}")
        return 1
    print(f"Discovered {len(runs)} runs: alphas = {sorted(runs.keys())}")

    # Headline + FDM distribution
    headline = headline_table(runs)
    fdm_dist = fdm_distribution(runs)

    # Coverage backed out from highest-alpha run vs alpha=0
    coverage_df = pd.DataFrame()
    if 0.0 in runs:
        diag_a0 = load_diag(runs[0.0])
        # Pick the largest alpha for the back-out (most signal)
        max_alpha = max(a for a in runs if a > 0)
        diag_aX = load_diag(runs[max_alpha])
        coverage_df = derive_coverage(diag_a0, diag_aX, max_alpha)
    else:
        print("WARN: no alpha=0 run; cannot back out coverage")

    # Coverage-decile PnL
    print("Loading prices for PnL stratification...")
    raw = pd.read_parquet(args.dataset)
    raw["date"] = pd.to_datetime(raw["date"])
    prices = raw.pivot(index="date", columns="instrument", values="close")
    decile_pnl = (
        coverage_decile_pnl(runs, coverage_df, prices)
        if not coverage_df.empty else {"buckets": []}
    )

    # Examples
    examples = (
        example_trajectories(runs, coverage_df, n=3)
        if not coverage_df.empty else []
    )

    # Verdict
    verdict_text, winner = compute_verdict(headline)

    report = {
        "headline": headline,
        "fdm_distribution": fdm_dist,
        "coverage_decile_pnl": decile_pnl,
        "examples": examples,
        "verdict_text": verdict_text,
        "verdict_winner_alpha": winner,
    }

    json_path = sweep_dir / "diagnosis.json"
    md_path   = sweep_dir / "DIAGNOSIS.md"
    json_path.write_text(json.dumps(report, indent=2, default=float))
    write_markdown(report, md_path)

    print(f"\nWrote {json_path}")
    print(f"Wrote {md_path}")
    print(f"\n{verdict_text}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
