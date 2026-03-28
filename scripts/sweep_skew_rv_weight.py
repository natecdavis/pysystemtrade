#!/usr/bin/env python3
"""
Sweep skew_rv weight across [0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 0.07, 0.08]
on crypto_perps_full_rules.yaml.

All three rules (skew_rv_90/180/365) share the same weight at each step.

Usage:
    python scripts/sweep_skew_rv_weight.py
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

BASE_CONFIG = Path("config/crypto_perps_full_rules.yaml")
DATA = Path("data/dataset_538registry_6yr_jagged.parquet")
OUTDIR_ROOT = Path("out/sweep_skew_rv")

BASELINE_W = 0.03
WEIGHTS = [0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 0.07, 0.08]

BASELINE = {
    "sharpe": 1.3239,
    "calmar": 1.8321,
    "cagr": 0.1496,
    "max_dd": -0.0817,
}


def load_config(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def patch_config(cfg: dict, weight: float) -> dict:
    import copy
    c = copy.deepcopy(cfg)
    fw = c.get("forecast_weights", {})
    fw["skew_rv_90"] = weight
    fw["skew_rv_180"] = weight
    fw["skew_rv_365"] = weight
    c["forecast_weights"] = fw
    return c


def write_temp_config(cfg: dict) -> Path:
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", delete=False, dir="/tmp"
    ) as f:
        yaml.dump(cfg, f, default_flow_style=False, allow_unicode=True)
        return Path(f.name)


def run_backtest(config_path: Path, outdir: Path) -> dict:
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

    summary_path = outdir / "performance_summary.json"
    if not summary_path.exists():
        return None
    with open(summary_path) as f:
        return json.load(f)


def main():
    base_cfg = load_config(BASE_CONFIG)
    OUTDIR_ROOT.mkdir(parents=True, exist_ok=True)

    results = []

    for w in WEIGHTS:
        tag = f"w{int(w*1000):03d}"
        outdir = OUTDIR_ROOT / tag
        marker = "← baseline" if abs(w - BASELINE_W) < 1e-9 else ""
        print(f"  w={w:.2f}  {marker}", flush=True)

        cfg = patch_config(base_cfg, w)
        tmp_cfg = write_temp_config(cfg)

        try:
            data = run_backtest(tmp_cfg, outdir)
        finally:
            tmp_cfg.unlink(missing_ok=True)

        if data is None:
            results.append({"w": w, "sharpe": None, "calmar": None, "cagr": None, "max_dd": None})
            continue

        m = data["metrics"]
        results.append({
            "w": w,
            "sharpe": m["sharpe"],
            "calmar": m["calmar"],
            "cagr": m["cagr"],
            "max_dd": m["max_dd"],
        })

    # Print sweep table
    print()
    print("=" * 72)
    print("SWEEP: skew_rv weight (skew_rv_90 = skew_rv_180 = skew_rv_365 = w)")
    print(f"Baseline (w=0.03, fee-corrected 2026-03-28): "
          f"Sharpe={BASELINE['sharpe']:.4f}  Calmar={BASELINE['calmar']:.4f}")
    print("=" * 72)
    print(f"{'w':>6}  {'Sharpe':>8}  {'ΔSharpe':>8}  {'Calmar':>8}  {'ΔCalmar':>8}  "
          f"{'CAGR':>7}  {'MaxDD':>7}  {'Note'}")
    print(f"{'------':>6}  {'--------':>8}  {'--------':>8}  {'--------':>8}  "
          f"{'--------':>8}  {'-------':>7}  {'-------':>7}")

    best_sharpe_w = max((r for r in results if r["sharpe"] is not None), key=lambda r: r["sharpe"])["w"]
    best_calmar_w = max((r for r in results if r["calmar"] is not None), key=lambda r: r["calmar"])["w"]

    for r in results:
        w = r["w"]
        if r["sharpe"] is None:
            print(f"  {w:.2f}  ERROR")
            continue
        ds = (r["sharpe"] - BASELINE["sharpe"]) / BASELINE["sharpe"] * 100
        dc = (r["calmar"] - BASELINE["calmar"]) / BASELINE["calmar"] * 100
        note = ""
        if abs(w - BASELINE_W) < 1e-9:
            note = "← baseline"
        elif abs(w - best_sharpe_w) < 1e-9 and abs(w - best_calmar_w) < 1e-9:
            note = "← peak Sharpe+Calmar"
        elif abs(w - best_sharpe_w) < 1e-9:
            note = "← peak Sharpe"
        elif abs(w - best_calmar_w) < 1e-9:
            note = "← peak Calmar"
        print(f"  {w:.2f}  {r['sharpe']:>8.4f}  {ds:>+7.1f}%  {r['calmar']:>8.4f}  {dc:>+7.1f}%  "
              f"  {r['cagr']:>5.1%}  {r['max_dd']:>6.2%}  {note}")

    print()
    print(f"  Best Sharpe:  w={best_sharpe_w:.2f}")
    print(f"  Best Calmar:  w={best_calmar_w:.2f}")

    # Adoption recommendation
    print()
    best = max((r for r in results if r["sharpe"] is not None), key=lambda r: r["calmar"])
    ds_best = (best["sharpe"] - BASELINE["sharpe"]) / BASELINE["sharpe"] * 100
    dc_best = (best["calmar"] - BASELINE["calmar"]) / BASELINE["calmar"] * 100
    if best["w"] == BASELINE_W:
        print("  RECOMMENDATION: Keep w=0.03 (current is already optimal by Calmar)")
    elif abs(ds_best) < 0.5 and abs(dc_best) < 2.0:
        print(f"  RECOMMENDATION: No meaningful improvement — keep w={BASELINE_W:.2f}")
    else:
        print(f"  RECOMMENDATION: Consider w={best['w']:.2f} "
              f"(ΔSharpe={ds_best:+.1f}%, ΔCalmar={dc_best:+.1f}%)")


if __name__ == "__main__":
    main()
