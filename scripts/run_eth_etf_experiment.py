#!/usr/bin/env python3
"""
eth_etf_flow_trend_20 walk-forward ablation.

Direct mirror of the live `btc_etf_flow_trend_20` rule applied to ETHA signed
dollar volume. Hypothesis: net institutional dollars into the spot ETH ETF
over the past 20 trading days predicts crypto price action 1-3 weeks ahead.

Same forecast broadcast to every instrument. Pre-launch (before 2024-07-23)
returns NaN — the WF stitched OOS series ignores those windows automatically.

Two pre-stated adoption bars (mirroring the C2a btc_etf protocol):

  default (full-period scoring):
    ΔSharpe ≥ 0.02, ΔCalmar ≥ 0, max_quarter_dd ≥ -0.30
    Dilutes the post-2024-07 signal across 6 years of NaN backfill.

  --windowed (active-window scoring, B7 protocol):
    ΔSharpe ≥ 0.05 (above 0.04 floor), ΔCalmar ≥ 0, max_quarter_dd ≥ -0.30
    Scores only [2024-07-23, end] — fair sample for a rule with this history.
    Higher Sharpe bar because a true full-period +0.02 contribution implies
    ~+0.05 over the actually-emitting window (~1/2.5 dilution factor).

Both modes run on the same candidate backtest; --windowed re-scores via
harness.rescore_cached() without paying the compute cost twice.

Usage:
    python scripts/run_eth_etf_experiment.py
    python scripts/run_eth_etf_experiment.py --windowed
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import pandas as pd
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from systems.crypto_perps.walk_forward import (
    AdoptionRule,
    ConfigOverrideCandidate,
    WalkForwardHarness,
)


NEW_RULE_NAME = "eth_etf_flow_trend_20"
ETHA_LAUNCH = "2024-07-23"


def _build_overrides(base_config_path: Path) -> dict:
    """Add eth_etf_flow_trend_20 to trading_rules; renormalize weights 1/N → 1/(N+1)."""
    with open(base_config_path) as f:
        base = yaml.safe_load(f)

    base_weights = base.get("forecast_weights", {})
    new_n = len(base_weights) + 1
    new_weight = 1.0 / new_n
    new_weights = {rule: new_weight for rule in base_weights.keys()}
    new_weights[NEW_RULE_NAME] = new_weight

    new_rule_def = {
        NEW_RULE_NAME: {
            "function": "systems.crypto_perps.rules.rule_library.eth_etf_flow_trend",
            "data": [
                "data.daily_prices",
                "data.get_eth_etf_signed_volume",
            ],
            "other_args": {"Lfast": 20},
        }
    }

    return {
        "trading_rules": new_rule_def,
        "forecast_weights": new_weights,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--config",
        default=str(REPO_ROOT / "config" / "crypto_perps_full_rules.yaml"),
    )
    parser.add_argument(
        "--data",
        default=str(REPO_ROOT / "data" / "dataset_sb_corrected_6yr_jagged.parquet"),
    )
    parser.add_argument("--out-root", type=Path, default=REPO_ROOT / "out")
    parser.add_argument(
        "--windowed",
        action="store_true",
        help="Score over [ETHA launch, end] instead of full period (B7 protocol).",
    )
    parser.add_argument(
        "--skip-baseline",
        action="store_true",
        help="Reuse a cached baseline outdir (default: the freshly-built one from "
             "the stablecoin_dominance run, which is the same 123-rule live config).",
    )
    parser.add_argument(
        "--baseline-cache",
        type=Path,
        default=None,
        help="Path to a cached baseline outdir to symlink (with --skip-baseline).",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if args.windowed:
        adoption = AdoptionRule(
            name="eth_etf_windowed",
            min_delta_sharpe=0.05,
            min_delta_calmar=0.0,
            max_quarter_drawdown=-0.30,
            data_available_after=ETHA_LAUNCH,
        )
        mode_tag = "windowed"
    else:
        adoption = AdoptionRule(
            name="eth_etf_default",
            min_delta_sharpe=0.02,
            min_delta_calmar=0.0,
            max_quarter_drawdown=-0.30,
        )
        mode_tag = "default"

    candidate_label = NEW_RULE_NAME
    out_dir = args.out_root / f"wf_{candidate_label}"
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.skip_baseline:
        if args.baseline_cache is None:
            args.baseline_cache = (
                args.out_root / "wf_stablecoin_dominance_trend_32" / "backtest_flat_baseline"
            )
        if not (args.baseline_cache / "performance_summary.json").exists():
            print(
                f"--skip-baseline set but no cached baseline at {args.baseline_cache}",
                file=sys.stderr,
            )
            return 1
        target = out_dir / "backtest_flat_baseline"
        if not target.exists():
            target.symlink_to(args.baseline_cache.resolve())

    overrides = _build_overrides(Path(args.config))

    harness = WalkForwardHarness(
        config_path=args.config,
        data_path=args.data,
        out_dir=out_dir,
        adoption_rule=adoption,
    )
    candidate = ConfigOverrideCandidate(name=candidate_label, overrides=overrides)
    result = harness.run(candidate)

    summary = {
        "candidate": candidate_label,
        "mode": mode_tag,
        "decision": result.decision,
        "adoption_rule": {
            "min_delta_sharpe": adoption.min_delta_sharpe,
            "min_delta_calmar": adoption.min_delta_calmar,
            "max_quarter_drawdown": adoption.max_quarter_drawdown,
            "data_available_after": str(adoption.data_available_after) if adoption.data_available_after else None,
        },
        "candidate_sharpe": result.candidate_metrics.get("sharpe"),
        "baseline_sharpe": result.baseline_metrics.get("sharpe"),
        "delta_sharpe": result.candidate_metrics.get("sharpe", 0)
        - result.baseline_metrics.get("sharpe", 0),
        "candidate_calmar": result.candidate_metrics.get("calmar"),
        "baseline_calmar": result.baseline_metrics.get("calmar"),
        "delta_calmar": result.candidate_metrics.get("calmar", 0)
        - result.baseline_metrics.get("calmar", 0),
        "max_dd": result.candidate_metrics.get("max_dd"),
        "scoring_window": result.scoring_window,
        "reasons": result.reasons,
        "decision_path": str(result.artifacts_dir / "decision.md"),
    }
    summary_path = args.out_root / f"wf_{candidate_label}_summary_{mode_tag}.json"
    summary_path.write_text(json.dumps(summary, indent=2))

    print()
    print(f"=== {candidate_label} [{mode_tag}]: {result.decision} ===")
    print(f"  baseline Sharpe = {summary['baseline_sharpe']:.4f}")
    print(
        f"  candidate Sharpe = {summary['candidate_sharpe']:.4f}  "
        f"(Δ {summary['delta_sharpe']:+.4f})"
    )
    print(
        f"  candidate Calmar = {summary['candidate_calmar']:.4f}  "
        f"(Δ {summary['delta_calmar']:+.4f})"
    )
    if result.scoring_window:
        print(f"  scoring window: {result.scoring_window['scored_start']} → "
              f"{result.scoring_window['scored_end']} "
              f"({result.scoring_window['n_days_scored']} days)")
    print(f"  artifacts: {result.artifacts_dir}/decision.md")
    if result.reasons:
        print("  reasons:")
        for r in result.reasons:
            print(f"    - {r}")
    return 0 if result.decision != "REJECT" else 1


if __name__ == "__main__":
    sys.exit(main())
