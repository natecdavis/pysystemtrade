#!/usr/bin/env python3
"""
maturity_b50_t365: token-maturity multiplier panel walk-forward ablation.

Hypothesis (Gu, Flirting With Models): "for small or new tokens, almost all
the risk is idiosyncratic." Vol-targeting on a 35–63d realised vol can't see
upcoming airdrop cliffs, vesting unlocks, or MM-fragility regimes. A graduated
dampener that scales positions down for tokens with launch_date within the
last 365 days should reduce blowups on young instruments without excluding
them entirely.

Mechanism: a per-(date, instrument) multiplier panel ∈ [0.5, 1.0] is plumbed
through `walk_forward.py:WalkForwardHarness(wf_multiplier_path=…)` and
consumed at `systems/crypto_perps/forecast_combine_gated.py:176-182` as
`forecast × multiplier, clipped ±20`. The full_rules.yaml baseline config
has no other multiplier wired, so this is the only modulation active during
the WF test (1k.yaml-style C4 composition is a downstream concern only if
this passes).

Single canonical parameterisation: β=0.5 (positions halved at age 0),
T=365d (linear ramp to identity by 1 year).

Pre-stated adoption rule:
    ΔSharpe ≥ +0.02 AND ΔCalmar ≥ 0 AND no quarter exceeds -0.30 drawdown.

Usage:
    python scripts/run_maturity_multiplier_experiment.py
    python scripts/run_maturity_multiplier_experiment.py --skip-baseline
    python scripts/run_maturity_multiplier_experiment.py --skip-panel-rebuild
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from systems.crypto_perps.walk_forward import (
    AdoptionRule,
    WalkForwardHarness,
    WalkForwardMultiplierCandidate,
)


CANDIDATE_LABEL = "maturity_b50_t365"
PANEL_PATH = REPO_ROOT / "data" / "research" / "maturity_multiplier_b50_t365.parquet"


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--config", default=str(REPO_ROOT / "config" / "crypto_perps_full_rules.yaml")
    )
    parser.add_argument(
        "--data", default=str(REPO_ROOT / "data" / "dataset_sb_corrected_6yr_jagged.parquet")
    )
    parser.add_argument("--out-root", type=Path, default=REPO_ROOT / "out")
    parser.add_argument("--panel", type=Path, default=PANEL_PATH)
    parser.add_argument(
        "--skip-panel-rebuild",
        action="store_true",
        help="Skip rebuilding the multiplier panel (only safe if mtime < 30h).",
    )
    parser.add_argument("--skip-baseline", action="store_true")
    parser.add_argument(
        "--baseline-cache",
        type=Path,
        default=None,
        help="Optional cached baseline backtest directory.",
    )
    parser.add_argument("--rescore-only", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    log = logging.getLogger(__name__)

    # Step 1: rebuild the maturity panel fresh (refreshes mtime so the
    # `assert_multiplier_panel_fresh` 30h TTL check at forecast_combine_gated.py:158
    # will pass throughout the ~70-min backtest).
    if not args.skip_panel_rebuild:
        log.info("Rebuilding maturity multiplier panel...")
        rc = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "scripts" / "build_maturity_multiplier_panel.py"),
                "--data", args.data,
                "--output", str(args.panel),
            ],
            check=False,
        ).returncode
        if rc != 0:
            log.error("Panel build failed")
            return rc

    if not args.panel.exists():
        log.error(f"Multiplier panel not found at {args.panel} — rebuild required")
        return 1

    adoption = AdoptionRule(
        name="maturity_penalty_default",
        min_delta_sharpe=0.02,
        min_delta_calmar=0.0,
        max_quarter_drawdown=-0.30,
    )

    out_dir = args.out_root / f"wf_{CANDIDATE_LABEL}"
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.skip_baseline:
        cached_baseline = (
            args.baseline_cache
            if args.baseline_cache is not None
            else args.out_root / "wf_funding_oi_conc_mr" / "backtest_flat_baseline"
        )
        if not (cached_baseline / "performance_summary.json").exists():
            print(
                f"--skip-baseline set but no cached baseline at {cached_baseline}",
                file=sys.stderr,
            )
            return 1
        target = out_dir / "backtest_flat_baseline"
        if not target.exists():
            target.symlink_to(cached_baseline.resolve())

    harness = WalkForwardHarness(
        config_path=args.config,
        data_path=args.data,
        out_dir=out_dir,
        adoption_rule=adoption,
    )

    if args.rescore_only:
        cand_dir = out_dir / f"backtest_{CANDIDATE_LABEL}"
        base_dir = out_dir / "backtest_flat_baseline"
        if not (cand_dir / "performance_summary.json").exists():
            print(f"--rescore-only requires existing backtest at {cand_dir}", file=sys.stderr)
            return 1
        result = harness.rescore_cached(cand_dir, base_dir, candidate_name=CANDIDATE_LABEL)
    else:
        candidate = WalkForwardMultiplierCandidate(
            name=CANDIDATE_LABEL, multiplier_panel_path=args.panel
        )
        result = harness.run(candidate)

    summary = {
        "candidate": CANDIDATE_LABEL,
        "decision": result.decision,
        "candidate_sharpe": result.candidate_metrics.get("sharpe"),
        "baseline_sharpe": result.baseline_metrics.get("sharpe"),
        "delta_sharpe": result.candidate_metrics.get("sharpe", 0)
        - result.baseline_metrics.get("sharpe", 0),
        "candidate_calmar": result.candidate_metrics.get("calmar"),
        "delta_calmar": result.candidate_metrics.get("calmar", 0)
        - result.baseline_metrics.get("calmar", 0),
        "max_dd": result.candidate_metrics.get("max_dd"),
        "reasons": result.reasons,
        "decision_path": str(result.artifacts_dir / "decision.md"),
    }
    summary_path = args.out_root / f"wf_{CANDIDATE_LABEL}_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2))

    print()
    print(f"=== {CANDIDATE_LABEL}: {result.decision} ===")
    print(f"  baseline Sharpe  = {summary['baseline_sharpe']:.4f}")
    print(
        f"  candidate Sharpe = {summary['candidate_sharpe']:.4f}  (Δ {summary['delta_sharpe']:+.4f})"
    )
    print(
        f"  candidate Calmar = {summary['candidate_calmar']:.4f}  (Δ {summary['delta_calmar']:+.4f})"
    )
    print(f"  artifacts: {result.artifacts_dir}/decision.md")
    if result.reasons:
        print("  reasons:")
        for r in result.reasons:
            print(f"    - {r}")
    return 0 if result.decision != "REJECT" else 1


if __name__ == "__main__":
    sys.exit(main())
