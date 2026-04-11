#!/usr/bin/env python3
"""
Sweep crowd_deleverage_trend forecast weight.

Single rule: w_each = w_combined.
Grid: combined_weight ∈ {0.0, 0.03, 0.05, 0.08, 0.10, 0.12, 0.15, 0.20}

Baseline: empirical w=0.0 row (first grid point), NOT a hardcoded constant.

Adoption criteria (consistent with project conventions):
    Calmar does NOT peak at w=0.0
    ΔSharpe vs w=0.0 > +1%

Usage:
    python scripts/sweep_crowd_deleverage_weight.py
    python scripts/sweep_crowd_deleverage_weight.py --skip-existing
"""

import argparse
import copy
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

BASE_CONFIG = Path("config/crypto_perps_full_rules.yaml")
DATA        = Path("data/dataset_538registry_6yr_jagged.parquet")
OUTDIR_ROOT = Path("out/sweep_crowd_deleverage")

RULES   = ["crowd_deleverage_trend"]
WEIGHTS = [0.0, 0.03, 0.05, 0.08, 0.10, 0.12, 0.15, 0.20]


def load_config(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def patch_config(cfg: dict, w_combined: float) -> dict:
    c = copy.deepcopy(cfg)
    fw = c.get("forecast_weights", {})
    w_each = w_combined / len(RULES)
    for rule in RULES:
        fw[rule] = w_each
    c["forecast_weights"] = fw
    return c


def write_temp_config(cfg: dict) -> Path:
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", delete=False, dir="/tmp"
    ) as f:
        yaml.dump(cfg, f, default_flow_style=False, allow_unicode=True)
        return Path(f.name)


def run_backtest(config_path: Path, outdir: Path, skip_existing: bool = False) -> dict | None:
    summary_path = outdir / "performance_summary.json"
    if skip_existing and summary_path.exists():
        with open(summary_path) as f:
            return json.load(f)

    outdir.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            sys.executable,
            "scripts/run_dynamic_universe_backtest.py",
            "--config", str(config_path),
            "--data", str(DATA),
            "--outdir", str(outdir),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"  ERROR: {result.stderr[-500:]}", file=sys.stderr)
        return None

    if not summary_path.exists():
        return None
    with open(summary_path) as f:
        return json.load(f)


def print_sweep_table(results: list) -> None:
    """Print sweep table and adoption recommendation."""
    baseline = next((r for r in results if r["w"] == 0.0 and r["sharpe"] is not None), None)

    valid = [r for r in results if r["sharpe"] is not None]
    if not valid:
        print("  No valid results.")
        return

    best_sharpe_w = max(valid, key=lambda r: r["sharpe"])["w"]
    best_calmar_w = max(valid, key=lambda r: r["calmar"])["w"]

    print()
    print("=" * 85)
    print("SWEEP: crowd_deleverage_trend (single rule)")
    if baseline:
        print(f"Baseline (w=0.0 empirical): "
              f"Sharpe={baseline['sharpe']:.4f}  Calmar={baseline['calmar']:.4f}  "
              f"MaxDD={baseline['max_dd']:.2%}")
    print("=" * 85)

    hdr = (
        f"{'w_comb':>7}  "
        f"{'Sharpe':>8}  {'ΔSharpe':>8}  "
        f"{'Calmar':>8}  {'ΔCalmar':>8}  "
        f"{'CAGR':>7}  {'MaxDD':>7}  {'Note'}"
    )
    print(hdr)
    print("-" * 85)

    for r in results:
        w = r["w"]
        if r["sharpe"] is None:
            print(f"  {w:.2f}  ERROR")
            continue

        if baseline:
            ds = (r["sharpe"] - baseline["sharpe"]) / baseline["sharpe"] * 100
            dc = (r["calmar"] - baseline["calmar"]) / baseline["calmar"] * 100
        else:
            ds = dc = 0.0

        note = ""
        if w == 0.0:
            note = "← baseline"
        else:
            is_sharpe_peak = abs(w - best_sharpe_w) < 1e-9 and best_sharpe_w > 0.0
            is_calmar_peak = abs(w - best_calmar_w) < 1e-9 and best_calmar_w > 0.0
            if is_sharpe_peak and is_calmar_peak:
                note = "← peak Sharpe+Calmar"
            elif is_sharpe_peak:
                note = "← peak Sharpe"
            elif is_calmar_peak:
                note = "← peak Calmar"

        print(
            f"  {w:>5.2f}  "
            f"{r['sharpe']:>8.4f}  {ds:>+7.1f}%  "
            f"{r['calmar']:>8.4f}  {dc:>+7.1f}%  "
            f"{r['cagr']:>6.1%}  {r['max_dd']:>6.2%}  {note}"
        )

    print()
    if baseline:
        calmar_peaks_at_zero = best_calmar_w == 0.0
        best = max(valid, key=lambda r: r["calmar"])
        ds_best = (best["sharpe"] - baseline["sharpe"]) / baseline["sharpe"] * 100
        if calmar_peaks_at_zero:
            print("  RESULT: Calmar peaks at w=0.0 → signal adds no value. REJECT.")
        elif ds_best < 1.0:
            print(f"  RESULT: Calmar-peak w={best_calmar_w:.2f} but ΔSharpe={ds_best:+.1f}% "
                  f"< +1% threshold → marginal; consider REJECT.")
        else:
            best_calmar = max(valid, key=lambda r: r["calmar"])
            dc_best = (best_calmar["calmar"] - baseline["calmar"]) / baseline["calmar"] * 100
            print(f"  RESULT: Calmar-peak w={best_calmar_w:.2f}  "
                  f"ΔSharpe={ds_best:+.1f}%  ΔCalmar={dc_best:+.1f}%  "
                  f"MaxDD={best_calmar['max_dd']:.2%} → candidate for ADOPTION.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Sweep crowd_deleverage_trend weight")
    parser.add_argument(
        "--skip-existing", action="store_true",
        help="Re-use existing performance_summary.json if present (skip re-running backtest)"
    )
    args = parser.parse_args()

    base_cfg = load_config(BASE_CONFIG)
    OUTDIR_ROOT.mkdir(parents=True, exist_ok=True)

    results = []
    for w in WEIGHTS:
        tag = f"cdt_w{int(w * 1000):04d}"
        outdir = OUTDIR_ROOT / tag
        print(f"  w={w:.2f}  ({tag})", flush=True)

        cfg = patch_config(base_cfg, w)
        tmp_cfg = write_temp_config(cfg)

        try:
            data = run_backtest(tmp_cfg, outdir, skip_existing=args.skip_existing)
        finally:
            tmp_cfg.unlink(missing_ok=True)

        if data is None:
            results.append({"w": w, "sharpe": None, "calmar": None, "cagr": None, "max_dd": None})
        else:
            m = data["metrics"]
            results.append({
                "w": w,
                "sharpe": m["sharpe"],
                "calmar": m["calmar"],
                "cagr": m["cagr"],
                "max_dd": m["max_dd"],
            })

    print_sweep_table(results)

    print()
    print("Done. If signal passes adoption criteria, update forecast_weights in:")
    print("  config/crypto_perps_1k.yaml")
    print("  config/crypto_perps_full_rules.yaml")


if __name__ == "__main__":
    main()
