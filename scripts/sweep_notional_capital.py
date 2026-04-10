#!/usr/bin/env python3
"""
Sweep notional_trading_capital to find the value that targets 25% realized vol.

Rationale (Carver's preferred approach):
    Increasing notional_trading_capital while keeping actual equity fixed is
    equivalent to using leverage.  It is Carver's preferred framing because it
    cleanly separates "how much capital the sizing formula sees" from "how much
    margin is actually posted."

The backtest metrics (Sharpe, Calmar, CAGR, MaxDD, ann_vol) are always reported
as a fraction of notional_trading_capital.  For a live account with actual equity
E_actual and notional_capital C:

    actual_vol    = ann_vol_backtest × (C / E_actual)
    actual_CAGR   ≈ cagr_backtest   × (C / E_actual)   [ignoring variance drag]
    actual_max_dd = max_dd_backtest × (C / E_actual)
    leverage      = C / E_actual

We sweep C until actual_vol ≈ 25%.

Usage:
    python scripts/sweep_notional_capital.py [--actual-equity 1000]
"""

import argparse
import copy
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import yaml

BASE_CONFIG = Path("config/crypto_perps_1k.yaml")
DATA = Path("data/dataset_538registry_6yr_jagged.parquet")
OUTDIR_ROOT = Path("out/sweep_notional_capital")

BASELINE_CAPITAL = 1000.0

# Capitals to sweep — bracket the expected target (~$2,800)
CAPITALS = [1000, 1500, 2000, 2500, 3000, 3500, 4000, 5000, 6000]

BASELINE_METRICS = {
    "sharpe": 1.3964,
    "calmar": 1.5044,
    "cagr": 0.1279,
    "max_dd": -0.0850,
    "ann_vol": 0.0891,
}


def load_config(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def patch_config(cfg: dict, capital: float) -> dict:
    c = copy.deepcopy(cfg)
    c["notional_trading_capital"] = float(capital)
    return c


def write_temp_config(cfg: dict) -> Path:
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", delete=False, dir="/tmp"
    ) as f:
        yaml.dump(cfg, f, default_flow_style=False, allow_unicode=True)
        return Path(f.name)


def run_backtest(config_path: Path, outdir: Path) -> dict | None:
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
    parser = argparse.ArgumentParser(description="Sweep notional_trading_capital")
    parser.add_argument(
        "--actual-equity", type=float, default=1000.0,
        help="Actual account equity in USD (default: 1000). Used to compute leverage and live metrics."
    )
    args = parser.parse_args()
    actual_equity = args.actual_equity

    base_cfg = load_config(BASE_CONFIG)
    OUTDIR_ROOT.mkdir(parents=True, exist_ok=True)

    results = []

    for capital in CAPITALS:
        tag = f"c{int(capital):05d}"
        outdir = OUTDIR_ROOT / tag
        marker = "← baseline" if capital == BASELINE_CAPITAL else ""
        print(f"  capital=${capital:,}  {marker}", flush=True)

        cfg = patch_config(base_cfg, capital)
        tmp_cfg = write_temp_config(cfg)

        try:
            data = run_backtest(tmp_cfg, outdir)
        finally:
            tmp_cfg.unlink(missing_ok=True)

        if data is None:
            results.append({
                "capital": capital, "sharpe": None, "calmar": None,
                "cagr": None, "max_dd": None, "ann_vol": None,
            })
            continue

        m = data["metrics"]
        results.append({
            "capital": capital,
            "sharpe": m["sharpe"],
            "calmar": m["calmar"],
            "cagr": m["cagr"],
            "max_dd": m["max_dd"],
            "ann_vol": m["ann_vol"],
        })

    # ------------------------------------------------------------------ #
    # Print sweep table
    # ------------------------------------------------------------------ #
    print()
    print("=" * 100)
    print("SWEEP: notional_trading_capital  (Option B phantom leverage)")
    print(f"Actual equity: ${actual_equity:,.0f}  |  Base config: {BASE_CONFIG}")
    print(f"Baseline (C=$1,000): "
          f"Sharpe={BASELINE_METRICS['sharpe']:.4f}  "
          f"Calmar={BASELINE_METRICS['calmar']:.4f}  "
          f"ann_vol={BASELINE_METRICS['ann_vol']:.1%}")
    print("=" * 100)

    # Header: backtest metrics (relative to C) + live metrics (relative to actual equity)
    print(
        f"{'Capital':>8}  {'Leverage':>8}  "
        f"{'Sharpe':>8}  {'ΔSharpe':>8}  "
        f"{'Calmar':>8}  {'ΔCalmar':>8}  "
        f"{'ann_vol':>8}  "                  # relative to C
        f"{'live_vol':>9}  "                  # relative to actual equity
        f"{'live_CAGR':>10}  "
        f"{'live_MaxDD':>10}  "
        f"{'Note'}"
    )
    print("-" * 100)

    TARGET_VOL = 0.25
    best_capital_for_target = None

    for r in results:
        capital = r["capital"]
        leverage = capital / actual_equity

        if r["sharpe"] is None:
            print(f"  ${capital:>6,}  ERROR")
            continue

        ds = (r["sharpe"] - BASELINE_METRICS["sharpe"]) / BASELINE_METRICS["sharpe"] * 100
        dc = (r["calmar"] - BASELINE_METRICS["calmar"]) / BASELINE_METRICS["calmar"] * 100

        # Live metrics = backtest metrics × leverage
        live_vol = r["ann_vol"] * leverage
        live_cagr = r["cagr"] * leverage
        live_maxdd = r["max_dd"] * leverage  # negative number

        note = ""
        if capital == BASELINE_CAPITAL:
            note = "← baseline"
        elif best_capital_for_target is None and live_vol >= TARGET_VOL:
            note = f"← first hits {TARGET_VOL:.0%} live vol"
            best_capital_for_target = capital

        print(
            f"  ${capital:>6,}  {leverage:>7.2f}×  "
            f"{r['sharpe']:>8.4f}  {ds:>+7.1f}%  "
            f"{r['calmar']:>8.4f}  {dc:>+7.1f}%  "
            f"{r['ann_vol']:>8.1%}  "
            f"{live_vol:>9.1%}  "
            f"{live_cagr:>10.1%}  "
            f"{live_maxdd:>10.1%}  "
            f"{note}"
        )

    print()

    # Summary recommendation
    target_hits = [r for r in results if r["ann_vol"] is not None and r["ann_vol"] * (r["capital"] / actual_equity) >= TARGET_VOL]
    if target_hits:
        rec = target_hits[0]
        lev = rec["capital"] / actual_equity
        print(f"  To hit {TARGET_VOL:.0%} live vol:  notional_trading_capital = ${rec['capital']:,}  ({lev:.2f}× leverage)")
        print(f"    Sharpe={rec['sharpe']:.4f}  Calmar={rec['calmar']:.4f}  "
              f"live_CAGR≈{rec['cagr']*lev:.1%}  live_MaxDD≈{rec['max_dd']*lev:.1%}")
    else:
        print("  25% live vol target not reached in this sweep range — increase CAPITALS list.")

    print()
    print("NOTE: 'live_*' metrics assume all backtest P&L is delivered relative to actual_equity.")
    print("      Sharpe and Calmar are scale-invariant (same at all leverage levels).")
    print("      live_MaxDD shows the drawdown as % of actual equity — watch for values > 30%.")


if __name__ == "__main__":
    main()
