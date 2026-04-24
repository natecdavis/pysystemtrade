#!/usr/bin/env python3
"""
Leave-one-out exclusion ablations on the SB-corrected flat-69 stack.

For each candidate rule, remove it from the 69-rule stack, reweight the
remaining 68 rules to 1/68, and run on the SB-corrected dataset.

If BOTH ΔSharpe > 0 AND ΔCalmar > 0 on removal → rule was a net drag
(SB false-positive candidate). Present to user for removal decision.

Baseline: flat-69 SB-corrected (Sharpe=1.4471, Calmar=2.3667, MaxDD=-5.38%)
from out/cs_mr_flat69_sb_combined/performance_summary.json

Usage:
    python scripts/run_sb_exclusion_audit.py
    python scripts/run_sb_exclusion_audit.py \\
        --rules relmomentum_20 relmomentum_40
    python scripts/run_sb_exclusion_audit.py \\
        --data data/dataset_sb_corrected_6yr_jagged.parquet \\
        --outdir out/sb_exclusion_audit
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
logger = logging.getLogger("sb_exclusion_audit")

# Rules most likely to be SB false positives:
# long-biased momentum rules that would have been long LUNA/FTT before crashes,
# plus small-contribution rules whose original positive may have been marginal.
_CANDIDATE_RULES = [
    "relmomentum_20",
    "relmomentum_40",
    "assettrend_8",
    "assettrend_16",
    "assettrend_32",
    "assettrend_64",
    "accel_16",
    "accel_32",
    "accel_64",
    "breakout_80",
    "breakout_160",
    "volume_surge_momentum",
    "xs_low_vol_20",
    "volume_price_divergence",
    "crowd_deleverage_trend",
    "attn_exhaustion_fade",
    "attn_panic_rebound",
]

_FLAT69_SB_BASELINE = {
    "sharpe": 1.4471,
    "calmar": 2.3667,
    "maxdd": -0.0538,
    "source": "hardcoded flat-69 SB (out/cs_mr_flat69_sb_combined/)",
}

_FLAT69_SB_BASELINE_PATH = Path(
    "out/cs_mr_flat69_sb_combined/performance_summary.json"
)


def _load_baseline() -> dict:
    if _FLAT69_SB_BASELINE_PATH.exists():
        try:
            data = json.loads(_FLAT69_SB_BASELINE_PATH.read_text())
            m = data.get("metrics", {})
            baseline = {
                "sharpe": m.get("sharpe"),
                "calmar": m.get("calmar"),
                "maxdd": m.get("max_dd"),
                "source": str(_FLAT69_SB_BASELINE_PATH),
            }
            if baseline["sharpe"] is not None and baseline["calmar"] is not None:
                logger.info(
                    f"Loaded flat-69 SB baseline: "
                    f"Sharpe={baseline['sharpe']:.4f}, Calmar={baseline['calmar']:.4f}"
                )
                return baseline
        except Exception as e:
            logger.warning(f"Failed to read baseline file: {e}")

    logger.warning(
        "Baseline file not found at %s — using hardcoded flat-69 SB values.",
        _FLAT69_SB_BASELINE_PATH,
    )
    return _FLAT69_SB_BASELINE.copy()


def _make_exclusion_config(base_config: dict, rule_name: str) -> dict:
    cfg = copy.deepcopy(base_config)
    fw = cfg.get("forecast_weights", {})
    if rule_name not in fw:
        raise ValueError(
            f"Rule '{rule_name}' not found in forecast_weights. "
            f"Available: {sorted(fw.keys())}"
        )
    remaining = {k: v for k, v in fw.items() if k != rule_name}
    n = len(remaining)
    cfg["forecast_weights"] = {k: round(1.0 / n, 8) for k in remaining}
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


def run_exclusion_audit(
    config_path: str = "config/crypto_perps_full_rules.yaml",
    data_path: str = "data/dataset_sb_corrected_6yr_jagged.parquet",
    out_dir: str = "out/sb_exclusion_audit",
    rules: list[str] | None = None,
) -> dict:
    config_path = Path(config_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    data_path_obj = Path(data_path)
    if not data_path_obj.exists():
        logger.error(f"SB-corrected dataset not found: {data_path_obj}")
        sys.exit(1)

    baseline = _load_baseline()

    with open(config_path) as f:
        base_config = yaml.safe_load(f)

    n_base = len(base_config.get("forecast_weights", {}))
    logger.info(f"Base config has {n_base} rules in forecast_weights")

    rules_to_run = rules or _CANDIDATE_RULES
    results = {}

    for rule in rules_to_run:
        logger.info(f"\n{'='*60}")
        logger.info(f"Exclusion ablation: removing {rule}")
        logger.info(f"{'='*60}")

        try:
            exclusion_cfg = _make_exclusion_config(base_config, rule)
        except ValueError as e:
            logger.error(str(e))
            results[rule] = {"success": False, "error": str(e)}
            continue

        n_remaining = len(exclusion_cfg["forecast_weights"])
        w = list(exclusion_cfg["forecast_weights"].values())[0]
        logger.info(f"  {n_remaining} rules remaining, weight={w:.8f}")

        rule_out = out_dir / f"backtest_{rule}"
        rule_out.mkdir(exist_ok=True)

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", dir=out_dir, delete=False
        ) as tmpf:
            yaml.dump(exclusion_cfg, tmpf, default_flow_style=False, sort_keys=False)
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
    print("SB EXCLUSION AUDIT RESULTS (leave-one-out on flat-69 SB-corrected dataset)")
    print(
        f"Baseline ({baseline['source']}): "
        f"Sharpe={bl_sharpe:.4f}, Calmar={bl_calmar:.4f}"
        + (f", MaxDD={bl_maxdd:.2%}" if bl_maxdd is not None else "")
    )
    print(f"{'='*80}")
    print(
        f"{'Rule':<26} {'Sharpe':>8} {'ΔSharpe':>9} {'Calmar':>8} {'ΔCalmar':>9} "
        f"{'MaxDD':>8}  Verdict"
    )
    print(f"{'-'*26} {'-'*8} {'-'*9} {'-'*8} {'-'*9} {'-'*8}  {'-'*16}")

    remove_candidates = []

    for rule in rules_to_run:
        m = results.get(rule, {})
        if not m.get("success", False) or m.get("sharpe") is None:
            err = m.get("error", "FAILED")
            print(f"{rule:<26} {'FAILED':>8}  [{err[:40]}]")
            continue
        ds = m["sharpe"] - bl_sharpe
        dc = m["calmar"] - bl_calmar
        verdict = "REMOVE_CANDIDATE" if ds > 0 and dc > 0 else "KEEP"
        if verdict == "REMOVE_CANDIDATE":
            remove_candidates.append(rule)
        maxdd_str = f"{m['maxdd']:.2%}" if m.get("maxdd") is not None else "   N/A"
        print(
            f"{rule:<26} {m['sharpe']:>8.4f} {ds:>+9.4f} {m['calmar']:>8.4f} {dc:>+9.4f} "
            f"{maxdd_str:>8}  [{verdict}]"
        )

    print()
    if remove_candidates:
        print("REMOVE CANDIDATES (both ΔSharpe and ΔCalmar improved on removal):")
        for rule in remove_candidates:
            m = results[rule]
            ds = m["sharpe"] - bl_sharpe
            dc = m["calmar"] - bl_calmar
            print(
                f"  {rule}: ΔSharpe={ds:+.4f}, ΔCalmar={dc:+.4f} — "
                "present to user for removal decision"
            )
    else:
        print(
            "No remove candidates found. SB inflation is diffuse — "
            "no individual rule is a clear false positive."
        )
    print()

    out_path = out_dir / "sb_exclusion_results.json"
    with open(out_path, "w") as f:
        json.dump(
            {
                "baseline": baseline,
                "results": results,
                "verdicts": {
                    rule: (
                        "REMOVE_CANDIDATE"
                        if results.get(rule, {}).get("success")
                        and results[rule].get("sharpe") is not None
                        and (results[rule]["sharpe"] - bl_sharpe) > 0
                        and (results[rule]["calmar"] - bl_calmar) > 0
                        else "KEEP"
                    )
                    for rule in rules_to_run
                },
                "remove_candidates": remove_candidates,
            },
            f,
            indent=2,
        )
    logger.info(f"Results saved: {out_path}")

    return results


def main():
    parser = argparse.ArgumentParser(
        description="Leave-one-out exclusion ablations to find SB false-positive rules"
    )
    parser.add_argument("--config", default="config/crypto_perps_full_rules.yaml")
    parser.add_argument(
        "--data",
        default="data/dataset_sb_corrected_6yr_jagged.parquet",
    )
    parser.add_argument("--outdir", default="out/sb_exclusion_audit")
    parser.add_argument(
        "--rules",
        nargs="+",
        default=None,
        help="Subset of candidate rules to run (default: all 17)",
    )
    args = parser.parse_args()

    run_exclusion_audit(
        config_path=args.config,
        data_path=args.data,
        out_dir=args.outdir,
        rules=args.rules,
    )


if __name__ == "__main__":
    main()
