#!/usr/bin/env python3
"""
Re-ablate 7 previously-rejected rules against the survivorship-bias-corrected
flat-67 baseline.

Rules that were rejected on the original dataset may flip positive once
graveyard tokens (LUNA, FTT, etc.) are included — particularly short-biased
rules (cs_mr) and volatility/skew rules.

This script must be run AFTER:
  1. scripts/download_graveyard_data.py
  2. scripts/build_sb_corrected_dataset.py
  3. scripts/run_sb_baseline_comparison.py

The SB-corrected flat-67 baseline is loaded from:
  out/sb_corrected_baseline/sb_corrected/performance_summary.json

If that file doesn't exist, falls back to the original flat-67 baseline
(Sharpe=1.45, Calmar=2.53) with a warning.

Usage:
    python scripts/run_sb_corrected_ablations.py
    python scripts/run_sb_corrected_ablations.py \\
        --data data/dataset_sb_corrected_6yr_jagged.parquet \\
        --outdir out/sb_corrected_ablations
"""
from __future__ import annotations

import argparse
import copy
import json
import logging
import sys
import tempfile
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.run_dynamic_universe_backtest import run_backtest

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("sb_corrected_ablations")

_RULES = [
    "cs_mr_125",
    "cs_mr_250",
    "vol_trend_16",
    "return_skew_20",
    "return_skew_60",
    "illiquidity_20",
    "illiquidity_60",
]

# Original flat-67 baseline (fallback if SB-corrected baseline not yet computed)
_FLAT67_ORIGINAL_BASELINE = {
    "sharpe": 1.45,
    "calmar": 2.53,
    "maxdd": -0.054,
    "source": "original flat-67 (out/oil_mom_flat67_combined/)",
}

_SB_CORRECTED_BASELINE_PATH = Path(
    "out/sb_corrected_baseline/sb_corrected/performance_summary.json"
)


def _load_sb_corrected_baseline() -> dict:
    """Load SB-corrected flat-67 baseline; fall back to original if unavailable."""
    if _SB_CORRECTED_BASELINE_PATH.exists():
        try:
            data = json.loads(_SB_CORRECTED_BASELINE_PATH.read_text())
            m = data.get("metrics", {})
            baseline = {
                "sharpe": m.get("sharpe"),
                "calmar": m.get("calmar"),
                "maxdd": m.get("max_dd"),
                "source": str(_SB_CORRECTED_BASELINE_PATH),
            }
            if baseline["sharpe"] is not None and baseline["calmar"] is not None:
                logger.info(
                    f"Loaded SB-corrected baseline: "
                    f"Sharpe={baseline['sharpe']:.4f}, Calmar={baseline['calmar']:.4f}"
                )
                return baseline
        except Exception as e:
            logger.warning(f"Failed to read SB-corrected baseline: {e}")

    logger.warning(
        "SB-corrected baseline not found at %s — falling back to original flat-67 baseline. "
        "Run run_sb_baseline_comparison.py first for accurate comparisons.",
        _SB_CORRECTED_BASELINE_PATH,
    )
    return _FLAT67_ORIGINAL_BASELINE.copy()


def _make_ablation_config(base_config: dict, rule_name: str) -> dict:
    cfg = copy.deepcopy(base_config)
    fw = cfg.get("forecast_weights", {})
    n = len(fw) + 1
    new_w = round(1.0 / n, 8)
    fw_new = {k: new_w for k in fw}
    fw_new[rule_name] = new_w
    cfg["forecast_weights"] = fw_new
    return cfg


def _extract_metrics(out_dir: Path) -> dict:
    summary_path = out_dir / "performance_summary.json"
    if not summary_path.exists():
        return {}
    try:
        data = json.loads(summary_path.read_text())
        m = data.get("metrics", {})
        return {
            "sharpe": m.get("sharpe"),
            "calmar": m.get("calmar"),
            "maxdd": m.get("max_dd"),
        }
    except Exception:
        return {}


def run_ablation(
    config_path: str = "config/crypto_perps_full_rules.yaml",
    data_path: str = "data/dataset_sb_corrected_6yr_jagged.parquet",
    out_dir: str = "out/sb_corrected_ablations",
    rules: list[str] | None = None,
) -> dict:
    config_path = Path(config_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    data_path_obj = Path(data_path)
    if not data_path_obj.exists():
        logger.error(f"SB-corrected dataset not found: {data_path_obj}")
        logger.error("Run build_sb_corrected_dataset.py first.")
        sys.exit(1)

    baseline = _load_sb_corrected_baseline()

    with open(config_path) as f:
        base_config = yaml.safe_load(f)

    rules_to_run = rules or _RULES
    results = {}

    for rule in rules_to_run:
        logger.info(f"\n{'='*60}")
        logger.info(f"Running SB-corrected ablation: {rule}")
        logger.info(f"{'='*60}")

        ablation_cfg = _make_ablation_config(base_config, rule)
        rule_out = out_dir / f"backtest_{rule}"
        rule_out.mkdir(exist_ok=True)

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", dir=out_dir, delete=False
        ) as tmpf:
            yaml.dump(ablation_cfg, tmpf, default_flow_style=False, sort_keys=False)
            tmp_config = tmpf.name

        try:
            success = run_backtest(
                config_path=tmp_config,
                data_path=data_path,
                output_dir=str(rule_out),
            )
            metrics = _extract_metrics(rule_out)
            results[rule] = {"success": success, **metrics}
        except Exception as exc:
            logger.error(f"{rule}: failed — {exc}", exc_info=True)
            results[rule] = {"success": False}
        finally:
            Path(tmp_config).unlink(missing_ok=True)

    bl_sharpe = baseline["sharpe"]
    bl_calmar = baseline["calmar"]
    bl_maxdd = baseline.get("maxdd")

    print(f"\n{'='*80}")
    print(f"SB-CORRECTED ABLATION RESULTS")
    print(
        f"Baseline ({baseline['source']}): "
        f"Sharpe={bl_sharpe:.4f}, Calmar={bl_calmar:.4f}"
        + (f", MaxDD={bl_maxdd:.2%}" if bl_maxdd is not None else "")
    )
    print(f"{'='*80}")
    print(
        f"{'Rule':<22} {'Sharpe':>8} {'ΔSharpe':>9} {'Calmar':>8} {'ΔCalmar':>9} "
        f"{'MaxDD':>8}  Verdict"
    )
    print(f"{'-'*22} {'-'*8} {'-'*9} {'-'*8} {'-'*9} {'-'*8}  {'-'*7}")

    for rule in rules_to_run:
        m = results.get(rule, {})
        if not m.get("success", False) or m.get("sharpe") is None:
            print(f"{rule:<22} {'FAILED':>8}")
            continue
        ds = m["sharpe"] - bl_sharpe
        dc = m["calmar"] - bl_calmar
        verdict = "ADOPT" if ds > 0 and dc > 0 else "REJECT"
        maxdd_str = f"{m['maxdd']:.2%}" if m.get("maxdd") is not None else "  N/A"
        print(
            f"{rule:<22} {m['sharpe']:>8.4f} {ds:>+9.4f} {m['calmar']:>8.4f} {dc:>+9.4f} "
            f"{maxdd_str:>8}  [{verdict}]"
        )

    print()

    # Save results JSON
    out_path = out_dir / "sb_ablation_results.json"
    with open(out_path, "w") as f:
        json.dump(
            {
                "baseline": baseline,
                "results": results,
                "verdicts": {
                    rule: (
                        "ADOPT"
                        if results.get(rule, {}).get("success")
                        and results[rule].get("sharpe") is not None
                        and (results[rule]["sharpe"] - bl_sharpe) > 0
                        and (results[rule]["calmar"] - bl_calmar) > 0
                        else "REJECT"
                    )
                    for rule in rules_to_run
                },
            },
            f,
            indent=2,
        )
    logger.info(f"Results saved: {out_path}")

    return results


def main():
    parser = argparse.ArgumentParser(
        description="Re-ablate previously-rejected rules on SB-corrected dataset"
    )
    parser.add_argument("--config", default="config/crypto_perps_full_rules.yaml")
    parser.add_argument(
        "--data",
        default="data/dataset_sb_corrected_6yr_jagged.parquet",
        help="Path to SB-corrected dataset (output of build_sb_corrected_dataset.py)",
    )
    parser.add_argument("--outdir", default="out/sb_corrected_ablations")
    parser.add_argument(
        "--rules",
        nargs="+",
        default=None,
        help="Subset of rules to run (default: all 7)",
    )
    args = parser.parse_args()

    run_ablation(
        config_path=args.config,
        data_path=args.data,
        out_dir=args.outdir,
        rules=args.rules,
    )


if __name__ == "__main__":
    main()
