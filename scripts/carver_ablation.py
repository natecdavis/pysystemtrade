#!/usr/bin/env python3
"""
Carver ablation: progressively add rule complexity from pure EWMAC → full config.

Each level adds one group of rules on top of the previous.  The delta columns show
how much each group contributes vs the pure-EWMAC baseline (level 0), answering
"where is the Sharpe actually coming from?"

Levels:
  0  ewmac_only          — EWMAC 8/16/32 only (Carver's core, no carry)
  1  ewmac_plus_carry    — + gated_carry_30 (one carry rule — "Carver's system")
  2  full_trend          — + remaining 19 trend rules (breakout/normmom/accel/assettrend/relmom/resmom)
  3  trend_plus_carry    — + gated_carry_10 + gated_carry_60 (full carry sleeve)
  4  plus_funding_mr     — + funding_mr (drawdown hedge)
  5  plus_demeaned_carry — + demeaned_carry_10/30/60 (idiosyncratic carry)
  6  plus_xs             — + xs_carry/xs_activity/xs_val/inter_sector
  7  full_config         — + skew_abs + skew_rv (= current baseline; validation)

Level 7 should reproduce the known baseline:
  Sharpe ≈ 1.335, Calmar ≈ 1.539, MaxDD ≈ -6.42%

Usage:
    python scripts/carver_ablation.py
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
OUTDIR_ROOT = Path("out/carver_ablation")

KNOWN_BASELINE = {
    "sharpe": 1.3349,
    "calmar": 1.5390,
    "cagr": 0.0988,
    "max_dd": -0.0642,
}

# ---------------------------------------------------------------------------
# Rule groups
# ---------------------------------------------------------------------------
_EWMAC = ["ewmac_8", "ewmac_16", "ewmac_32"]

_TREND_EXTENDED = [
    "breakout_20", "breakout_40", "breakout_80", "breakout_160",
    "normmom_8", "normmom_16", "normmom_32",
    "accel_16", "accel_32", "accel_64",
    "assettrend_8", "assettrend_16", "assettrend_32", "assettrend_64",
    "relmomentum_20", "relmomentum_40",
    "residual_momentum_16", "residual_momentum_32", "residual_momentum_64",
]

_GATED_CARRY_CORE = ["gated_carry_30"]          # level 1: one carry rule
_GATED_CARRY_FULL = ["gated_carry_10", "gated_carry_60"]  # level 3: completes the sleeve

_FUNDING_MR = ["funding_mr"]

_DEMEANED_CARRY = ["demeaned_carry_10", "demeaned_carry_30", "demeaned_carry_60"]

_XS = ["xs_carry", "xs_activity", "xs_val", "inter_sector"]

_SKEW = [
    "skew_abs_90", "skew_abs_180", "skew_abs_365",
    "skew_rv_90", "skew_rv_180", "skew_rv_365",
]

# All rules that have non-zero weights in the config (the universe we'll manage)
_ALL_ACTIVE = (
    _EWMAC
    + _TREND_EXTENDED
    + _GATED_CARRY_CORE
    + _GATED_CARRY_FULL
    + _FUNDING_MR
    + _DEMEANED_CARRY
    + _XS
    + _SKEW
)

# ---------------------------------------------------------------------------
# Ablation level definitions
# Each level is cumulative: active_rules = union of all groups added so far
# ---------------------------------------------------------------------------
ABLATION_LEVELS = [
    {
        "level": 0,
        "name": "ewmac_only",
        "description": "EWMAC only (pure Carver baseline)",
        "active_rules": _EWMAC,
    },
    {
        "level": 1,
        "name": "ewmac_plus_carry",
        "description": "+ gated_carry_30  (Carver's system analogue)",
        "active_rules": _EWMAC + _GATED_CARRY_CORE,
    },
    {
        "level": 2,
        "name": "full_trend",
        "description": "+ all 19 remaining trend rules  (breakout/normmom/accel/assettrend/relmom/resmom)",
        "active_rules": _EWMAC + _GATED_CARRY_CORE + _TREND_EXTENDED,
    },
    {
        "level": 3,
        "name": "trend_plus_carry",
        "description": "+ gated_carry_10/60  (full carry sleeve 10/30/60)",
        "active_rules": _EWMAC + _GATED_CARRY_CORE + _TREND_EXTENDED + _GATED_CARRY_FULL,
    },
    {
        "level": 4,
        "name": "plus_funding_mr",
        "description": "+ funding_mr  (drawdown hedge, fires at extreme z-scores)",
        "active_rules": _EWMAC + _GATED_CARRY_CORE + _TREND_EXTENDED + _GATED_CARRY_FULL + _FUNDING_MR,
    },
    {
        "level": 5,
        "name": "plus_demeaned_carry",
        "description": "+ demeaned_carry_10/30/60  (idiosyncratic funding, ungated)",
        "active_rules": _EWMAC + _GATED_CARRY_CORE + _TREND_EXTENDED + _GATED_CARRY_FULL + _FUNDING_MR + _DEMEANED_CARRY,
    },
    {
        "level": 6,
        "name": "plus_xs",
        "description": "+ xs_carry/xs_activity/xs_val/inter_sector  (cross-sectional signals)",
        "active_rules": _EWMAC + _GATED_CARRY_CORE + _TREND_EXTENDED + _GATED_CARRY_FULL + _FUNDING_MR + _DEMEANED_CARRY + _XS,
    },
    {
        "level": 7,
        "name": "full_config",
        "description": "+ skew_abs/skew_rv  [= current baseline, validation]",
        "active_rules": _ALL_ACTIVE,
    },
]


def load_config(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def patch_config(cfg: dict, active_rules: list[str]) -> dict:
    """Zero all forecast_weights except those in active_rules (kept at config value)."""
    c = copy.deepcopy(cfg)
    fw = c.get("forecast_weights", {})
    for key in _ALL_ACTIVE:
        if key not in active_rules:
            fw[key] = 0.0
    c["forecast_weights"] = fw
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
    base_cfg = load_config(BASE_CONFIG)
    OUTDIR_ROOT.mkdir(parents=True, exist_ok=True)

    results = []

    for lvl in ABLATION_LEVELS:
        n = lvl["level"]
        name = lvl["name"]
        desc = lvl["description"]
        outdir = OUTDIR_ROOT / f"level_{n}_{name}"

        rule_count = len(lvl["active_rules"])
        print(f"  [{n}/7] {name}  ({rule_count} active rules)", flush=True)

        cfg = patch_config(base_cfg, lvl["active_rules"])
        tmp_cfg = write_temp_config(cfg)

        try:
            data = run_backtest(tmp_cfg, outdir)
        finally:
            tmp_cfg.unlink(missing_ok=True)

        if data is None:
            results.append({
                "level": n, "name": name, "description": desc,
                "sharpe": None, "calmar": None, "cagr": None, "max_dd": None, "ann_vol": None,
            })
        else:
            m = data["metrics"]
            results.append({
                "level": n, "name": name, "description": desc,
                "sharpe": m["sharpe"], "calmar": m["calmar"],
                "cagr": m["cagr"], "max_dd": m["max_dd"], "ann_vol": m["ann_vol"],
            })

    # -----------------------------------------------------------------------
    # Print results table
    # -----------------------------------------------------------------------
    baseline = next((r for r in results if r["level"] == 0 and r["sharpe"] is not None), None)

    print()
    print("=" * 106)
    print("CARVER ABLATION — progressive rule complexity")
    print(f"Base config: {BASE_CONFIG}  |  Dataset: {DATA}")
    print(f"Known full-config baseline: Sharpe={KNOWN_BASELINE['sharpe']:.4f}  "
          f"Calmar={KNOWN_BASELINE['calmar']:.4f}  MaxDD={KNOWN_BASELINE['max_dd']:.1%}")
    print("Delta columns relative to level 0 (pure EWMAC).")
    print("=" * 106)

    print(
        f"  {'Lvl':>3}  {'Rules':>5}  "
        f"{'Sharpe':>7}  {'ΔSharpe':>8}  "
        f"{'Calmar':>7}  {'ΔCalmar':>8}  "
        f"{'MaxDD':>7}  {'CAGR':>6}  "
        f"Description"
    )
    print("-" * 106)

    for r in results:
        n = r["level"]
        rule_count = len(ABLATION_LEVELS[n]["active_rules"])

        if r["sharpe"] is None:
            print(f"  {n:>3}  {rule_count:>5}  ERROR  —  {r['description']}")
            continue

        if baseline is not None:
            ds = (r["sharpe"] - baseline["sharpe"]) / baseline["sharpe"] * 100
            dc = (r["calmar"] - baseline["calmar"]) / baseline["calmar"] * 100
            ds_str = f"{ds:>+7.1f}%" if n > 0 else "       —"
            dc_str = f"{dc:>+7.1f}%" if n > 0 else "       —"
        else:
            ds_str = "       ?"
            dc_str = "       ?"

        val_note = ""
        if n == 7 and r["sharpe"] is not None:
            diff = abs(r["sharpe"] - KNOWN_BASELINE["sharpe"]) / KNOWN_BASELINE["sharpe"]
            val_note = " [OK]" if diff < 0.03 else f" [WARN: {diff:.1%} off baseline]"

        print(
            f"  {n:>3}  {rule_count:>5}  "
            f"{r['sharpe']:>7.4f}  {ds_str}  "
            f"{r['calmar']:>7.4f}  {dc_str}  "
            f"{r['max_dd']:>7.2%}  {r['cagr']:>6.2%}  "
            f"{r['description']}{val_note}"
        )

    print()

    # Summary: are the custom signals justified?
    if baseline and results[-1]["sharpe"] is not None:
        full = results[-1]
        total_gain = (full["sharpe"] - baseline["sharpe"]) / baseline["sharpe"] * 100
        # Find level 3 (trend + full carry) as the "crypto Carver" threshold
        lvl3 = next((r for r in results if r["level"] == 3 and r["sharpe"] is not None), None)
        if lvl3:
            carry_gain = (lvl3["sharpe"] - baseline["sharpe"]) / baseline["sharpe"] * 100
            custom_gain = total_gain - carry_gain
            print(f"  Carver core (trend only, lvl 0→2):          +{carry_gain - (carry_gain if lvl3 else 0):.1f}% Sharpe vs EWMAC baseline")
            print(f"  Crypto carry (gated_carry sleeve, lvl 2→3): +{(lvl3['sharpe'] - results[2]['sharpe']) / results[2]['sharpe'] * 100:.1f}% Sharpe")
            print(f"  Non-Carver signals (lvl 3→7):               +{(full['sharpe'] - lvl3['sharpe']) / lvl3['sharpe'] * 100:.1f}% Sharpe")
            print(f"  Total gain over pure EWMAC (lvl 0→7):       +{total_gain:.1f}% Sharpe")


if __name__ == "__main__":
    main()
