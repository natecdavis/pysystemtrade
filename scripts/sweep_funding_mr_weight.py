#!/usr/bin/env python3
"""
Sweep funding_mr weight across [0.0, 0.03, 0.06, 0.09, 0.12, 0.15, 0.20]
on crypto_perps_1k.yaml.

funding_mr fires only when an instrument's funding z-score exceeds ±2.0
(roughly top/bottom 2.5% of funding history), betting on mean reversion.
It is already defined in trading_rules but has never been given a non-zero weight.

Usage:
    python scripts/sweep_funding_mr_weight.py
"""

import copy
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

BASE_CONFIG = Path("config/crypto_perps_1k.yaml")
DATA = Path("data/dataset_538registry_6yr_jagged.parquet")
OUTDIR_ROOT = Path("out/sweep_funding_mr")

RULE = "funding_mr"
BASELINE_W = 0.0
WEIGHTS = [0.20, 0.25, 0.30, 0.40, 0.50]

BASELINE = {
    "sharpe": 1.2424,
    "calmar": 1.2003,
    "cagr": 0.1160,
    "max_dd": -0.0967,
}


def load_config(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def patch_config(cfg: dict, weight: float) -> dict:
    c = copy.deepcopy(cfg)
    c.setdefault("forecast_weights", {})[RULE] = weight
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
        tag = f"w{int(w * 1000):03d}"
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
    print("=" * 76)
    print(f"SWEEP: funding_mr weight")
    print(f"Baseline (w=0.0, 2026-03-31): "
          f"Sharpe={BASELINE['sharpe']:.4f}  Calmar={BASELINE['calmar']:.4f}")
    print("=" * 76)
    print(f"{'w':>6}  {'Sharpe':>8}  {'ΔSharpe':>8}  {'Calmar':>8}  {'ΔCalmar':>8}  "
          f"{'CAGR':>7}  {'MaxDD':>7}  {'Note'}")
    print(f"{'------':>6}  {'--------':>8}  {'--------':>8}  {'--------':>8}  "
          f"{'--------':>8}  {'-------':>7}  {'-------':>7}")

    valid = [r for r in results if r["sharpe"] is not None]
    best_sharpe_w = max(valid, key=lambda r: r["sharpe"])["w"]
    best_calmar_w = max(valid, key=lambda r: r["calmar"])["w"]

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
    best = max(valid, key=lambda r: r["calmar"])
    ds_best = (best["sharpe"] - BASELINE["sharpe"]) / BASELINE["sharpe"] * 100
    dc_best = (best["calmar"] - BASELINE["calmar"]) / BASELINE["calmar"] * 100

    if abs(best["w"] - BASELINE_W) < 1e-9:
        print("  RECOMMENDATION: REJECT — Calmar peaks at w=0.0 (no benefit from funding_mr)")
    elif ds_best < 1.0:
        print(f"  RECOMMENDATION: REJECT — ΔSharpe={ds_best:+.1f}% at best weight, below +1% threshold")
    else:
        print(f"  RECOMMENDATION: CONSIDER adopting w={best['w']:.2f} "
              f"(ΔSharpe={ds_best:+.1f}%, ΔCalmar={dc_best:+.1f}%)")
        print(f"  Adopt if ΔSharpe > +1% AND ΔCalmar > 0. Update config/crypto_perps_1k.yaml.")


if __name__ == "__main__":
    main()
