#!/usr/bin/env python3
"""
Quantify the survivorship bias haircut by running the flat-67 backtest on
both the original and survivorship-corrected datasets and comparing metrics.

This script must be run AFTER:
  1. scripts/download_graveyard_data.py
  2. scripts/build_sb_corrected_dataset.py

Usage:
    python scripts/run_sb_baseline_comparison.py
    python scripts/run_sb_baseline_comparison.py \\
        --config config/crypto_perps_full_rules.yaml \\
        --original-data data/dataset_538registry_6yr_jagged.parquet \\
        --corrected-data data/dataset_sb_corrected_6yr_jagged.parquet \\
        --outdir out/sb_corrected_baseline
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.run_dynamic_universe_backtest import run_backtest

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("sb_baseline_comparison")

# flat-67 in-sample result (from out/oil_mom_flat67_combined/)
_ORIGINAL_BASELINE = {
    "sharpe": 1.45,
    "calmar": 2.53,
    "maxdd": -0.054,
    "source": "out/oil_mom_flat67_combined/ (flat-67, 2026-04-19)",
}


def _load_metrics(out_dir: Path) -> dict:
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
    except Exception as e:
        logger.warning(f"Failed to read metrics from {summary_path}: {e}")
        return {}


def run_comparison(
    config_path: str = "config/crypto_perps_full_rules.yaml",
    original_data: str = "data/dataset_538registry_6yr_jagged.parquet",
    corrected_data: str = "data/dataset_sb_corrected_6yr_jagged.parquet",
    outdir: str = "out/sb_corrected_baseline",
    skip_original_run: bool = False,
) -> dict:
    config_path = Path(config_path)
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    results = {}

    # --- Run on SB-corrected dataset ---
    logger.info("\n" + "=" * 60)
    logger.info("Running flat-67 on SURVIVORSHIP-CORRECTED dataset")
    logger.info("=" * 60)

    corrected_out = outdir / "sb_corrected"
    corrected_out.mkdir(exist_ok=True)

    with open(config_path) as f:
        cfg = yaml.safe_load(f)

    corrected_data_path = Path(corrected_data)
    if not corrected_data_path.exists():
        logger.error(f"SB-corrected dataset not found: {corrected_data_path}")
        logger.error("Run build_sb_corrected_dataset.py first.")
        sys.exit(1)

    try:
        run_backtest(
            config_path=str(config_path),
            data_path=str(corrected_data),
            output_dir=str(corrected_out),
        )
        results["sb_corrected"] = _load_metrics(corrected_out)
    except Exception as e:
        logger.error(f"SB-corrected backtest failed: {e}", exc_info=True)
        results["sb_corrected"] = {}

    # --- Optionally run on original dataset too ---
    if not skip_original_run:
        existing_original = Path("out/oil_mom_flat67_combined")
        if existing_original.exists():
            logger.info("Using existing flat-67 original results from out/oil_mom_flat67_combined/")
            results["original"] = _load_metrics(existing_original)
        else:
            logger.info("\n" + "=" * 60)
            logger.info("Running flat-67 on ORIGINAL dataset (for fresh comparison)")
            logger.info("=" * 60)
            original_out = outdir / "original"
            original_out.mkdir(exist_ok=True)
            try:
                run_backtest(
                    config_path=str(config_path),
                    data_path=str(original_data),
                    output_dir=str(original_out),
                )
                results["original"] = _load_metrics(original_out)
            except Exception as e:
                logger.error(f"Original backtest failed: {e}", exc_info=True)
                results["original"] = {}
    else:
        results["original"] = _ORIGINAL_BASELINE.copy()

    # --- Print comparison table ---
    orig = results.get("original", {})
    corr = results.get("sb_corrected", {})

    orig_sharpe = orig.get("sharpe") or _ORIGINAL_BASELINE["sharpe"]
    orig_calmar = orig.get("calmar") or _ORIGINAL_BASELINE["calmar"]
    orig_maxdd = orig.get("maxdd") or _ORIGINAL_BASELINE["maxdd"]

    corr_sharpe = corr.get("sharpe")
    corr_calmar = corr.get("calmar")
    corr_maxdd = corr.get("maxdd")

    print(f"\n{'='*70}")
    print(f"SURVIVORSHIP BIAS HAIRCUT  (flat-67)")
    print(f"{'='*70}")
    print(f"{'':30s} {'Original':>10} {'SB-Corrected':>13} {'Δ (haircut)':>12}")
    print(f"{'-'*30} {'-'*10} {'-'*13} {'-'*12}")

    def _fmt(v, fmt=".4f"):
        return f"{v:{fmt}}" if v is not None else "N/A"

    def _delta(orig, corr):
        if orig is None or corr is None:
            return "N/A"
        return f"{corr - orig:+.4f}"

    print(f"{'Sharpe':30s} {_fmt(orig_sharpe):>10} {_fmt(corr_sharpe):>13} {_delta(orig_sharpe, corr_sharpe):>12}")
    print(f"{'Calmar':30s} {_fmt(orig_calmar):>10} {_fmt(corr_calmar):>13} {_delta(orig_calmar, corr_calmar):>12}")
    print(f"{'MaxDD':30s} {_fmt(orig_maxdd, '.2%'):>10} {_fmt(corr_maxdd, '.2%') if corr_maxdd else 'N/A':>13} {_delta(orig_maxdd, corr_maxdd):>12}")

    print(f"\nInterpretation:")
    if corr_sharpe is not None and orig_sharpe is not None:
        haircut = corr_sharpe - orig_sharpe
        if haircut < -0.01:
            print(f"  ΔSharpe={haircut:+.4f}: graveyard tokens reduce backtest Sharpe "
                  f"(confirms survivorship bias was inflating results by ~{abs(haircut):.2f} SR points)")
        elif abs(haircut) <= 0.01:
            print(f"  ΔSharpe≈0: graveyard tokens did not enter K=30 or had minimal impact")
        else:
            print(f"  ΔSharpe={haircut:+.4f}: unexpected improvement with graveyard data — investigate")

    print()

    # Save results
    out_path = outdir / "sb_comparison_results.json"
    with open(out_path, "w") as f:
        json.dump(
            {
                "original_baseline": _ORIGINAL_BASELINE,
                "original_run": orig,
                "sb_corrected_run": corr,
                "haircut": {
                    "delta_sharpe": (
                        round(corr_sharpe - orig_sharpe, 6)
                        if corr_sharpe and orig_sharpe
                        else None
                    ),
                    "delta_calmar": (
                        round(corr_calmar - orig_calmar, 6)
                        if corr_calmar and orig_calmar
                        else None
                    ),
                    "delta_maxdd": (
                        round(corr_maxdd - orig_maxdd, 6)
                        if corr_maxdd and orig_maxdd
                        else None
                    ),
                },
            },
            f,
            indent=2,
        )
    logger.info(f"Results saved: {out_path}")

    return results


def main():
    parser = argparse.ArgumentParser(
        description="Quantify survivorship bias haircut via flat-67 on corrected dataset"
    )
    parser.add_argument("--config", default="config/crypto_perps_full_rules.yaml")
    parser.add_argument(
        "--original-data",
        default="data/dataset_538registry_6yr_jagged.parquet",
    )
    parser.add_argument(
        "--corrected-data",
        default="data/dataset_sb_corrected_6yr_jagged.parquet",
    )
    parser.add_argument("--outdir", default="out/sb_corrected_baseline")
    parser.add_argument(
        "--skip-original-run",
        action="store_true",
        help="Use existing flat-67 results instead of re-running",
    )
    args = parser.parse_args()

    run_comparison(
        config_path=args.config,
        original_data=args.original_data,
        corrected_data=args.corrected_data,
        outdir=args.outdir,
        skip_original_run=args.skip_original_run,
    )


if __name__ == "__main__":
    main()
