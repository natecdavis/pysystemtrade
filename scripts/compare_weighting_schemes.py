#!/usr/bin/env python3
"""
Compare walk-forward forecast weighting schemes against the static baseline.

Implements the full pipeline:
  1. Extract capped forecast panels (or reuse existing)
  2. Compute walk-forward weight schedule for each scheme
  3. Run full backtest for each scheme + static baseline
  4. Print comparison table (Sharpe, Calmar, MaxDD, Turnover, ΔSharpe%, ΔCalmar%)

Schemes compared:
  flat        — equal weight, 1/N (null model)
  ic_weighted — rolling pooled IC@5d weighting
  gross_sr    — rolling gross SR, shrunk toward equal
  risk_parity — family budgets ∝ 1/√var(family-avg-forecast)
  baseline    — current static YAML weights (no walk-forward override)

Usage:
    # Full run (all schemes)
    python scripts/compare_weighting_schemes.py \\
        --config config/crypto_perps_full_rules.yaml \\
        --data data/dataset_538registry_6yr_jagged.parquet \\
        --outdir out/wf_comparison

    # Resume / partial run (skip completed steps)
    python scripts/compare_weighting_schemes.py \\
        --config config/crypto_perps_full_rules.yaml \\
        --data data/dataset_538registry_6yr_jagged.parquet \\
        --outdir out/wf_comparison \\
        --schemes ic_weighted gross_sr        # run subset
        --force-calibrate                     # recompute schedules
        --force-backtest                      # rerun backtests
"""

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

from systems.crypto_perps.walk_forward_weight_calibrator import WalkForwardWeightCalibrator

ALL_SCHEMES = [
    # Weighting schemes (proportional allocation among all active rules)
    "flat",
    "equal_family",
    "risk_parity",
    "ic_weighted",
    "ic_weighted_expanding",
    "gross_sr",
    "gross_sr_expanding",
    # Inclusion gate schemes (binary select → flat 1/N among included)
    "sr_gate",
    "sr_gate_expanding",
    "ic_gate_expanding",
    "ic_tstat_gate_expanding",
    # Bayesian HRP (shrinkage + correlation-aware allocation, all 56 rules)
    "bayes_hrp",
    "bayes_hrp_expanding",
]

DEFAULT_CONFIG = "config/crypto_perps_full_rules.yaml"
DEFAULT_DATA = "data/dataset_538registry_6yr_jagged.parquet"
DEFAULT_OUTDIR = "out/wf_comparison"


# ---------------------------------------------------------------------------
# Active rules helper
# ---------------------------------------------------------------------------

def get_active_rules(config_path: str, all_rules: bool = False) -> list:
    with open(config_path) as f:
        cfg = yaml.safe_load(f)
    fw = cfg.get("forecast_weights", {})
    if all_rules:
        return sorted(r for r, w in fw.items() if isinstance(w, (int, float)))
    return sorted(r for r, w in fw.items() if isinstance(w, (int, float)) and w > 0)


# ---------------------------------------------------------------------------
# Step 1: Forecast extraction
# ---------------------------------------------------------------------------

def run_extraction(config_path: str, data_path: str, panels_dir: Path,
                   all_rules: bool = False) -> None:
    print(f"\n{'='*70}")
    print("STEP 1: Extract forecast panels")
    print(f"{'='*70}")
    cmd = [
        sys.executable,
        "scripts/extract_rule_forecasts.py",
        "--config", config_path,
        "--data", data_path,
        "--outdir", str(panels_dir),
    ]
    if all_rules:
        cmd.append("--all-rules")
    subprocess.run(cmd, check=True)


# ---------------------------------------------------------------------------
# Step 2: Weight calibration
# ---------------------------------------------------------------------------

def compute_schedule(
    scheme: str,
    panels_dir: Path,
    out_dir: Path,
    config_path: str,
    lookback_days: int,
    ic_horizon: int,
    shrinkage: float,
    tstat_threshold: float = 1.0,
    all_rules: bool = False,
) -> Path:
    """Compute and save walk-forward weight schedule. Returns path to saved parquet.

    Scheme names ending in '_expanding' use an expanding window (all data from
    dataset start up to each rebalance date) instead of a fixed rolling lookback.
    """
    schedule_path = out_dir / f"wf_weights_{scheme}.parquet"

    print(f"  Computing {scheme} schedule...", end=" ", flush=True)
    forecast_df = pd.read_parquet(panels_dir / "forecasts.parquet")
    return_df = pd.read_parquet(panels_dir / "returns.parquet")

    active_rules = get_active_rules(config_path, all_rules=all_rules)

    # Parse _expanding suffix
    if scheme.endswith("_expanding"):
        base_scheme = scheme[: -len("_expanding")]
        expanding = True
    else:
        base_scheme = scheme
        expanding = False

    calibrator = WalkForwardWeightCalibrator(
        lookback_days=lookback_days,
        rebalance_freq="QS",
        ic_horizon=ic_horizon,
        shrinkage=shrinkage,
        expanding=expanding,
        tstat_threshold=tstat_threshold,
    )
    schedule = calibrator.compute_schedule(forecast_df, return_df, base_scheme, active_rules)
    schedule.to_parquet(schedule_path)

    n_dates = len(schedule)
    date_range = f"{schedule.index.min().date()} → {schedule.index.max().date()}"
    print(f"done  ({n_dates} quarterly dates, {date_range})")

    return schedule_path


# ---------------------------------------------------------------------------
# Step 3: Backtest
# ---------------------------------------------------------------------------

def run_backtest(
    config_path: str,
    data_path: str,
    outdir: Path,
    wf_weights_path: Path = None,
) -> None:
    """Run backtest, optionally injecting walk_forward_weights_path into config."""
    outdir.mkdir(parents=True, exist_ok=True)

    # Build config — inject wf_weights_path if provided
    with open(config_path) as f:
        cfg = yaml.safe_load(f)

    if wf_weights_path is not None:
        cfg["walk_forward_weights_path"] = str(wf_weights_path.resolve())

    # Write temp config adjacent to outdir
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", delete=False,
        dir=outdir.parent, prefix="wf_tmp_config_"
    ) as tmp:
        yaml.dump(cfg, tmp, default_flow_style=False, sort_keys=False)
        tmp_config = Path(tmp.name)

    macro_path = Path("data/macro_factors.parquet")

    cmd = [
        sys.executable,
        "scripts/run_dynamic_universe_backtest.py",
        "--config", str(tmp_config),
        "--data", data_path,
        "--outdir", str(outdir),
    ]
    if macro_path.exists():
        cmd += ["--macro-data", str(macro_path)]

    try:
        subprocess.run(cmd, check=True)
    finally:
        tmp_config.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Step 4: Parse results
# ---------------------------------------------------------------------------

def load_metrics(outdir: Path) -> dict:
    summary_path = outdir / "performance_summary.json"
    if not summary_path.exists():
        return {}
    with open(summary_path) as f:
        s = json.load(f)
    m = s.get("metrics", {})
    p = s.get("portfolio", {})
    return {
        "sharpe":    m.get("sharpe", float("nan")),
        "calmar":    m.get("calmar", float("nan")),
        "cagr":      m.get("cagr", float("nan")),
        "vol":       m.get("ann_vol", float("nan")),
        "max_dd":    m.get("max_dd", float("nan")),
        "turnover":  p.get("annual_turnover", float("nan")),
        "avg_pos":   p.get("avg_active_positions", float("nan")),
    }


# ---------------------------------------------------------------------------
# Step 5: Print comparison table
# ---------------------------------------------------------------------------

def print_comparison(results: dict, schedules: dict) -> None:
    baseline = results.get("baseline", {})
    flat = results.get("flat", {})
    # Use flat as the inclusion-gate comparison baseline (both use 1/N weights)
    gate_ref = flat if flat else baseline

    print(f"\n{'='*100}")
    print("WALK-FORWARD WEIGHT / INCLUSION SCHEME COMPARISON")
    print(f"{'='*100}")
    print(
        f"{'Scheme':<22} {'Sharpe':>7} {'ΔSharpe':>8} {'Calmar':>7} {'ΔCalmar':>8} "
        f"{'CAGR%':>7} {'MaxDD%':>7} {'Turnover':>9} {'AvgPos':>7} {'AvgRules':>9}"
    )
    print("-" * 100)

    def _avg_rules(scheme):
        sched = schedules.get(scheme)
        if sched is None or sched.empty:
            return float("nan")
        return float((sched > 0).sum(axis=1).mean())

    # Print baseline first
    if baseline:
        b = baseline
        print(
            f"{'baseline (1k sweep)':<22} {b['sharpe']:>7.4f} {'—':>8} "
            f"{b['calmar']:>7.4f} {'—':>8} "
            f"{b['cagr']*100:>7.2f} {b['max_dd']*100:>7.2f} "
            f"{b['turnover']:>9.1f} {b['avg_pos']:>7.1f} {'42':>9}"
        )
        print("-" * 100)

    weighting_schemes = ["flat", "equal_family", "risk_parity",
                         "ic_weighted", "ic_weighted_expanding",
                         "gross_sr", "gross_sr_expanding"]
    gate_schemes = ["sr_gate", "sr_gate_expanding", "ic_gate_expanding", "ic_tstat_gate_expanding"]
    bayes_hrp_schemes = ["bayes_hrp", "bayes_hrp_expanding"]

    def _print_row(scheme, ref):
        if scheme not in results:
            return
        r = results[scheme]
        if not r:
            print(f"  {scheme:<20} — no results (backtest may have failed)")
            return
        b_sharpe = ref.get("sharpe", float("nan"))
        b_calmar = ref.get("calmar", float("nan"))
        d_sharpe = (r["sharpe"] - b_sharpe) / abs(b_sharpe) * 100 if ref else float("nan")
        d_calmar = (r["calmar"] - b_calmar) / abs(b_calmar) * 100 if ref else float("nan")
        arrow = "↑" if d_sharpe > 1 else ("↓" if d_sharpe < -1 else "~")
        avg_r = _avg_rules(scheme)
        avg_r_str = f"{avg_r:.1f}" if not np.isnan(avg_r) else "—"
        print(
            f"  {scheme:<20} {r['sharpe']:>7.4f} {d_sharpe:>+7.1f}% {arrow} "
            f"{r['calmar']:>7.4f} {d_calmar:>+7.1f}%  "
            f"{r['cagr']*100:>7.2f} {r['max_dd']*100:>7.2f} "
            f"{r['turnover']:>9.1f} {r['avg_pos']:>7.1f} {avg_r_str:>9}"
        )

    print("  [weighting schemes — ΔSharpe vs baseline]")
    for scheme in weighting_schemes:
        _print_row(scheme, baseline)

    print("-" * 100)
    print("  [inclusion gate schemes — ΔSharpe vs flat 1/N]")
    for scheme in gate_schemes:
        _print_row(scheme, gate_ref)

    print("-" * 100)
    print("  [bayesian HRP schemes — ΔSharpe vs flat 1/N across all 56 rules]")
    for scheme in bayes_hrp_schemes:
        _print_row(scheme, gate_ref)

    print("=" * 100)
    print("\nInterpretation:")
    print("  Weighting schemes: ΔSharpe vs baseline (static sweep weights)")
    print("  Inclusion gate schemes: ΔSharpe vs flat (blanket 1/N across all 42 rules)")
    print("  Bayesian HRP schemes: ΔSharpe vs flat (blanket 1/N across all 56 rules)")
    print("  AvgRules: avg number of rules included per rebalance (42 = no exclusions)")
    print("  Gate schemes beat flat → some active rules are genuine drag; gating adds value")
    print("  Gate schemes ≈ flat   → 42-rule selection was already principled; no benefit to gating")
    print()

    _print_schedule_summary(results, schedules)


def _print_schedule_summary(results: dict, schedules: dict) -> None:
    """Print most recent quarterly weight/inclusion summary for each scheme."""
    gate_schemes = {"sr_gate", "sr_gate_expanding", "ic_gate_expanding", "ic_tstat_gate_expanding"}

    print("Most recent quarterly schedule (latest rebalance date):")
    print("-" * 70)
    for scheme in ["flat", "ic_weighted", "gross_sr", "risk_parity",
                   "sr_gate", "sr_gate_expanding", "ic_gate_expanding", "ic_tstat_gate_expanding",
                   "bayes_hrp", "bayes_hrp_expanding"]:
        schedule = schedules.get(scheme)
        if schedule is None or schedule.empty:
            continue
        latest = schedule.iloc[-1]
        latest_date = schedule.index[-1].date()

        if scheme in gate_schemes:
            included = sorted(latest[latest > 0].index.tolist())
            excluded = sorted(latest[latest == 0].index.tolist())
            n_in = len(included)
            n_ex = len(excluded)
            print(f"\n  {scheme} (as of {latest_date}): {n_in} included, {n_ex} excluded")
            if excluded:
                print(f"    Excluded: {', '.join(excluded)}")
        else:
            top5 = latest.nlargest(5)
            print(f"\n  {scheme} (as of {latest_date}):")
            for rule, w in top5.items():
                print(f"    {rule:<35} {w:.4f}")
            if len(latest) > 5:
                print(f"    ... ({len(latest)-5} more rules)")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare walk-forward forecast weighting schemes",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--config",  default=DEFAULT_CONFIG, help="Path to config YAML")
    parser.add_argument("--data",    default=DEFAULT_DATA,   help="Path to parquet dataset")
    parser.add_argument("--outdir",  default=DEFAULT_OUTDIR, help="Output base directory")
    parser.add_argument(
        "--schemes", nargs="+", choices=ALL_SCHEMES + ["all"], default=["all"],
        help="Schemes to run (default: all)",
    )
    parser.add_argument(
        "--lookback-days", type=int, default=504,
        help="Lookback window in calendar days for calibration (default: 504 ≈ 2yr)",
    )
    parser.add_argument(
        "--ic-horizon", type=int, default=5,
        help="Forward return horizon in days for IC (default: 5)",
    )
    parser.add_argument(
        "--shrinkage", type=float, default=0.5,
        help="Shrinkage toward equal weight for gross_sr scheme (default: 0.5)",
    )
    parser.add_argument(
        "--force-extract", action="store_true",
        help="Re-run forecast extraction even if panels already exist",
    )
    parser.add_argument(
        "--force-calibrate", action="store_true",
        help="Recompute weight schedules even if parquets already exist",
    )
    parser.add_argument(
        "--force-backtest", action="store_true",
        help="Rerun backtests even if results already exist",
    )
    parser.add_argument(
        "--skip-baseline", action="store_true",
        help="Skip the static baseline backtest",
    )
    parser.add_argument(
        "--all-rules", action="store_true",
        help="Use all forecast_weights entries (incl. zero-weight) as gate candidates",
    )
    parser.add_argument(
        "--tstat-threshold", type=float, default=1.0,
        help="IC t-stat threshold for ic_tstat_gate scheme (default: 1.0 ≈ one-sided 84%% CI)",
    )
    args = parser.parse_args()

    schemes = ALL_SCHEMES if "all" in args.schemes else args.schemes
    out_dir = Path(args.outdir)
    out_dir.mkdir(parents=True, exist_ok=True)
    panels_dir = out_dir / "forecast_panels"

    print(f"\nWalk-forward weight comparison")
    print(f"  Config:       {args.config}")
    print(f"  Data:         {args.data}")
    print(f"  Outdir:       {out_dir}")
    print(f"  Schemes:      {schemes}")
    print(f"  Lookback:     {args.lookback_days} days")
    print(f"  IC horizon:   {args.ic_horizon} days")
    print(f"  Shrinkage:    {args.shrinkage}")
    print(f"  T-stat threshold: {args.tstat_threshold}")
    print(f"  All rules (incl. zero-weight): {args.all_rules}")

    # ---------- Step 1: Extract panels ----------
    fc_path = panels_dir / "forecasts.parquet"
    ret_path = panels_dir / "returns.parquet"
    if args.force_extract or not (fc_path.exists() and ret_path.exists()):
        run_extraction(args.config, args.data, panels_dir, all_rules=args.all_rules)
    else:
        print(f"\nStep 1: Skipping extraction (panels exist at {panels_dir})")

    # ---------- Step 2: Calibrate ----------
    print(f"\n{'='*70}")
    print("STEP 2: Compute walk-forward weight schedules")
    print(f"{'='*70}")

    schedule_paths: dict[str, Path] = {}
    schedules: dict[str, pd.DataFrame] = {}

    for scheme in schemes:
        sched_path = out_dir / f"wf_weights_{scheme}.parquet"
        if args.force_calibrate or not sched_path.exists():
            sched_path = compute_schedule(
                scheme=scheme,
                panels_dir=panels_dir,
                out_dir=out_dir,
                config_path=args.config,
                lookback_days=args.lookback_days,
                ic_horizon=args.ic_horizon,
                shrinkage=args.shrinkage,
                tstat_threshold=args.tstat_threshold,
                all_rules=args.all_rules,
            )
        else:
            print(f"  Skipping {scheme} calibration (schedule exists)")

        schedule_paths[scheme] = sched_path
        try:
            schedules[scheme] = pd.read_parquet(sched_path)
        except Exception:
            pass

    # ---------- Step 3: Run backtests ----------
    print(f"\n{'='*70}")
    print("STEP 3: Run backtests")
    print(f"{'='*70}")

    results: dict = {}
    for scheme, f in schedules.items():
        results[f"schedule_{scheme}"] = f  # for weight summary display

    # Scheme backtests
    for scheme in schemes:
        scheme_outdir = out_dir / f"backtest_{scheme}"
        summary_path = scheme_outdir / "performance_summary.json"

        if args.force_backtest or not summary_path.exists():
            print(f"\n  [{scheme}] Running backtest...")
            try:
                run_backtest(
                    config_path=args.config,
                    data_path=args.data,
                    outdir=scheme_outdir,
                    wf_weights_path=schedule_paths.get(scheme),
                )
                print(f"  [{scheme}] Backtest complete.")
            except subprocess.CalledProcessError as e:
                print(f"  [{scheme}] ERROR: backtest failed — {e}")
                continue
        else:
            print(f"  [{scheme}] Skipping backtest (results exist)")

        results[scheme] = load_metrics(scheme_outdir)

    # Static baseline backtest
    if not args.skip_baseline:
        baseline_outdir = out_dir / "backtest_baseline"
        summary_path = baseline_outdir / "performance_summary.json"
        if args.force_backtest or not summary_path.exists():
            print(f"\n  [baseline] Running static-weight backtest...")
            try:
                run_backtest(
                    config_path=args.config,
                    data_path=args.data,
                    outdir=baseline_outdir,
                    wf_weights_path=None,  # no override — uses YAML weights
                )
                print(f"  [baseline] Backtest complete.")
            except subprocess.CalledProcessError as e:
                print(f"  [baseline] ERROR: baseline backtest failed — {e}")
        else:
            print(f"  [baseline] Skipping backtest (results exist)")
        results["baseline"] = load_metrics(baseline_outdir)

    # ---------- Step 4: Print results ----------
    print_comparison(results, schedules)

    # Save results summary
    results_path = out_dir / "comparison_results.json"
    saveable = {k: v for k, v in results.items() if isinstance(v, dict)}
    with open(results_path, "w") as f:
        json.dump(saveable, f, indent=2)
    print(f"Results saved to {results_path}")


if __name__ == "__main__":
    main()
