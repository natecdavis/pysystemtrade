#!/usr/bin/env python3
"""
stablecoin_dominance_trend_32 walk-forward ablation.

Hypothesis: when total USD-pegged stablecoin supply grows faster than crypto
market cap (i.e., stablecoin DOMINANCE rises), capital is parking on the
sidelines rather than being deployed into spot — bearish for next-period prices.
Falling dominance = stables being spent into crypto = bullish.

This is the alternate interpretation explicitly flagged in the docstring of the
live `stablecoin_supply_trend` rule but never tested. Spearman of the candidate
panel vs the absolute-supply rule = -0.05 — orthogonal (captures the relative-
share dimension the absolute supply rule misses by construction).

Sign committed a priori: rising dominance → SHORT.

Adoption rule (project standard for capital-flow candidates):
    ΔSharpe ≥ +0.02 AND ΔCalmar ≥ 0 AND no-quarter ≤ -0.30.

Usage:
    python scripts/run_stablecoin_dominance_experiment.py
    python scripts/run_stablecoin_dominance_experiment.py --skip-baseline
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from systems.crypto_perps.walk_forward import (
    AdoptionRule,
    ConfigOverrideCandidate,
    WalkForwardHarness,
)


NEW_RULE_NAME = "stablecoin_dominance_trend_32"


def _build_overrides(base_config_path: Path) -> dict:
    """
    Add stablecoin_dominance_trend_32 to trading_rules and re-normalize
    forecast_weights from 1/N → 1/(N+1).
    """
    with open(base_config_path) as f:
        base = yaml.safe_load(f)

    base_weights = base.get("forecast_weights", {})
    n_existing = len(base_weights)
    new_n = n_existing + 1
    new_weight = 1.0 / new_n

    new_weights = {rule: new_weight for rule in base_weights.keys()}
    new_weights[NEW_RULE_NAME] = new_weight

    new_rule_def = {
        NEW_RULE_NAME: {
            "function": "systems.crypto_perps.rules.rule_library.stablecoin_dominance_trend",
            "data": [
                "data.daily_prices",
                "data.get_stablecoin_dominance",
            ],
            "other_args": {"Lfast": 32},
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
        "--skip-baseline",
        action="store_true",
        help="Reuse a cached flat baseline run instead of re-running it.",
    )
    parser.add_argument(
        "--baseline-cache",
        type=Path,
        default=None,
        help="Path to a cached baseline outdir to symlink (with --skip-baseline).",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    adoption = AdoptionRule(
        name="capital_flow_default",
        min_delta_sharpe=0.02,
        min_delta_calmar=0.0,
        max_quarter_drawdown=-0.30,
    )

    candidate_label = NEW_RULE_NAME
    out_dir = args.out_root / f"wf_{candidate_label}"
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.skip_baseline:
        if args.baseline_cache is None:
            # Default: try the c2b baseline since it was built on the same flat config family.
            args.baseline_cache = args.out_root / "wf_c2b_stablecoin_supply_trend_32" / "backtest_flat_baseline"
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
        "decision": result.decision,
        "candidate_sharpe": result.candidate_metrics.get("sharpe"),
        "baseline_sharpe": result.baseline_metrics.get("sharpe"),
        "delta_sharpe": result.candidate_metrics.get("sharpe", 0)
        - result.baseline_metrics.get("sharpe", 0),
        "candidate_calmar": result.candidate_metrics.get("calmar"),
        "baseline_calmar": result.baseline_metrics.get("calmar"),
        "delta_calmar": result.candidate_metrics.get("calmar", 0)
        - result.baseline_metrics.get("calmar", 0),
        "max_dd": result.candidate_metrics.get("max_dd"),
        "reasons": result.reasons,
        "decision_path": str(result.artifacts_dir / "decision.md"),
    }
    summary_path = args.out_root / f"wf_{candidate_label}_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2))

    print()
    print(f"=== {candidate_label}: {result.decision} ===")
    print(f"  baseline Sharpe = {summary['baseline_sharpe']:.4f}")
    print(
        f"  candidate Sharpe = {summary['candidate_sharpe']:.4f}  "
        f"(Δ {summary['delta_sharpe']:+.4f})"
    )
    print(
        f"  candidate Calmar = {summary['candidate_calmar']:.4f}  "
        f"(Δ {summary['delta_calmar']:+.4f})"
    )
    print(f"  artifacts: {result.artifacts_dir}/decision.md")
    if result.reasons:
        print("  reasons:")
        for r in result.reasons:
            print(f"    - {r}")
    return 0 if result.decision != "REJECT" else 1


if __name__ == "__main__":
    sys.exit(main())
