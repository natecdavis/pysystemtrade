#!/usr/bin/env python3
"""
Sweep demeaned_carry combined weight and compare backtest performance.

Two-phase sweep:

  Phase 1 — Gated vs ungated comparison
    Test ungated bundle (demeaned_carry_10/30/60) at combined weight 0.15
    Test gated bundle (demeaned_carry_gated_10/30/60) at same weight
    Select better variant (or both if close)

  Phase 2 — Weight sweep for winner
    Grid: combined weight ∈ {0.0, 0.03, 0.06, 0.09, 0.12, 0.15, 0.18}
    Apply to winner from Phase 1
    Adopt if ΔSharpe ≥ +1% and not Calmar-peak at w=0

Signal: Idiosyncratic funding rate = per-instrument smoothed carry - universe-mean
  carry. Vol-normalized using price-dollar vol (same as gated_carry).
  Theoretically distinct from:
    gated_carry: uses raw funding vs own history (not vs contemporaneous peers)
    xs_carry:    uses cross-sectional rank (ordinal), not absolute deviation from mean

Adoption criteria:
  ΔSharpe vs w=0.0  > +1%    (must add meaningful Sharpe)
  Not Calmar-peak at w=0.0   (signal must add value, not zero being best)

Usage:
    # Phase 1 only (gated vs ungated)
    python scripts/sweep_demeaned_carry.py \\
        --phase 1 \\
        --base-config config/crypto_perps_full_rules.yaml \\
        --data data/dataset_538registry_6yr_jagged.parquet \\
        --outdir out/demeaned_carry_sweep

    # Phase 2 only (weight sweep for winning variant)
    python scripts/sweep_demeaned_carry.py \\
        --phase 2 \\
        --variant ungated \\
        --weights 0.0 0.03 0.06 0.09 0.12 0.15 0.18 \\
        ...

    # Both phases sequentially (default)
    python scripts/sweep_demeaned_carry.py
"""

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

UNGATED_RULES = ["demeaned_carry_10", "demeaned_carry_30", "demeaned_carry_60"]
GATED_RULES   = ["demeaned_carry_gated_10", "demeaned_carry_gated_30", "demeaned_carry_gated_60"]
ALL_DEMEANED  = UNGATED_RULES + GATED_RULES


def load_yaml(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def run_backtest(config_path: Path, data_path: Path, outdir: Path) -> int:
    """Run a single backtest. Returns subprocess return code."""
    cmd = [
        sys.executable,
        "scripts/run_dynamic_universe_backtest.py",
        "--config", str(config_path),
        "--data",   str(data_path),
        "--outdir", str(outdir),
    ]
    print(f"\n  CMD: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=False)
    return result.returncode


def load_results(outdir: Path) -> dict:
    """Load performance_summary.json from a backtest outdir."""
    summary_path = outdir / "performance_summary.json"
    if not summary_path.exists():
        return {}
    with open(summary_path) as f:
        return json.load(f)


def set_demeaned_weights(cfg: dict, rules_to_set: list, w_each: float) -> dict:
    """
    Update forecast_weights in cfg: set rules_to_set to w_each, zero all others.
    Returns a modified copy (shallow copy of cfg, deep copy of forecast_weights).
    """
    cfg = dict(cfg)
    fw = dict(cfg.get("forecast_weights", {}))
    # Zero all demeaned rules first
    for rule in ALL_DEMEANED:
        fw.pop(rule, None)
    # Set target rules
    if w_each > 1e-10:
        for rule in rules_to_set:
            fw[rule] = float(w_each)
    cfg["forecast_weights"] = fw
    return cfg


def print_phase1_table(results: list) -> None:
    """Print Phase 1 comparison table."""
    print()
    print("=" * 100)
    print("PHASE 1 — GATED vs UNGATED COMPARISON  (combined weight = 0.15, 0.05/rule)")
    print("Baseline: w_combined = 0.0 (no demeaned_carry)")
    print("=" * 100)

    hdr = (
        f'{"Variant":<20}  {"Sharpe":>8}  {"Calmar":>8}  {"CAGR":>8}  '
        f'{"MaxDD":>8}  {"ΔSharpe%":>9}  {"ΔCalmar":>8}  {"ΔMaxDD":>8}  {"Verdict"}'
    )
    print(hdr)
    print("─" * 100)

    baseline = next((r for r in results if r["variant"] == "baseline"), None)
    b_m = baseline.get("metrics", {}) if baseline else {}
    b_sharpe = b_m.get("sharpe", float("nan"))
    b_calmar = b_m.get("calmar", float("nan"))
    b_maxdd  = b_m.get("max_dd", float("nan"))

    best = None
    for r in results:
        m       = r.get("metrics", {})
        variant = r["variant"]
        sharpe  = m.get("sharpe",  float("nan"))
        calmar  = m.get("calmar",  float("nan"))
        cagr    = m.get("cagr",    float("nan"))
        maxdd   = m.get("max_dd",  float("nan"))

        if variant == "baseline":
            d_sharpe_pct = 0.0
            d_calmar     = 0.0
            d_maxdd_pp   = 0.0
            verdict      = "← baseline"
        else:
            d_sharpe_pct = (sharpe - b_sharpe) / abs(b_sharpe) * 100 if b_sharpe else float("nan")
            d_calmar     = calmar - b_calmar
            d_maxdd_pp   = (maxdd - b_maxdd) * 100
            passes       = d_sharpe_pct > 1.0
            verdict      = "✓ CANDIDATE" if passes else "✗ skip"
            if passes and (best is None or calmar > best.get("metrics", {}).get("calmar", float("-inf"))):
                best = r

        print(
            f"  {variant:<18}  "
            f"{sharpe:>8.4f}  "
            f"{calmar:>8.4f}  "
            f"{cagr*100:>7.2f}%  "
            f"{maxdd*100:>7.2f}%  "
            f"{d_sharpe_pct:>+8.1f}%  "
            f"{d_calmar:>+8.4f}  "
            f"{d_maxdd_pp:>+7.2f}pp"
            f"  {verdict}"
        )

    print("─" * 100)
    print()

    if best:
        print(f"  PHASE 1 WINNER: {best['variant']}")
        print(f"  → Proceed to Phase 2 weight sweep with variant '{best['variant']}'")
        winning_rules = GATED_RULES if best["variant"] == "gated" else UNGATED_RULES
        print(f"  → Rules: {winning_rules}")
    else:
        print("  No variant passes ΔSharpe ≥ +1% at w=0.15.")
        print("  Options: (a) test both variants in Phase 2 anyway (w=0.15 may not be optimal)")
        print("           (b) reject demeaned_carry signal entirely")
    print()

    return best


def print_phase2_table(results: list, variant: str) -> None:
    """Print Phase 2 weight sweep table."""
    print()
    print("=" * 110)
    print(f"PHASE 2 — WEIGHT SWEEP  (variant: {variant})")
    print("Δ columns: relative to w_combined=0.0 (no demeaned_carry baseline)")
    print("=" * 110)

    hdr = (
        f'{"w_comb":>8}  {"w_each":>7}  {"Sharpe":>8}  {"Calmar":>8}  {"CAGR":>8}  '
        f'{"MaxDD":>8}  {"ΔSharpe%":>9}  {"ΔCalmar":>8}  {"ΔMaxDD":>8}  {"Verdict"}'
    )
    print(hdr)
    print("─" * 110)

    baseline = next((r for r in results if abs(r["combined_weight"]) < 1e-6), None)
    b_m = baseline.get("metrics", {}) if baseline else {}
    b_sharpe = b_m.get("sharpe", float("nan"))
    b_calmar = b_m.get("calmar", float("nan"))
    b_maxdd  = b_m.get("max_dd", float("nan"))

    candidates = []

    for r in results:
        m          = r.get("metrics", {})
        w_combined = r["combined_weight"]
        w_each     = w_combined / 3.0
        sharpe     = m.get("sharpe",  float("nan"))
        calmar     = m.get("calmar",  float("nan"))
        cagr       = m.get("cagr",    float("nan"))
        maxdd      = m.get("max_dd",  float("nan"))

        if abs(w_combined - 0.0) < 1e-6:
            d_sharpe_pct = 0.0
            d_calmar     = 0.0
            d_maxdd_pp   = 0.0
            tag          = " ← zero-baseline"
            verdict      = ""
        else:
            d_sharpe_pct = (sharpe - b_sharpe) / abs(b_sharpe) * 100 if b_sharpe else float("nan")
            d_calmar     = calmar - b_calmar
            d_maxdd_pp   = (maxdd - b_maxdd) * 100
            c1           = d_sharpe_pct > 1.0
            verdict      = "✓ CANDIDATE" if c1 else "✗ skip"
            if c1:
                candidates.append(r)
            tag = ""

        print(
            f"{w_combined:>8.2f}  "
            f"{w_each:>7.4f}  "
            f"{sharpe:>8.4f}  "
            f"{calmar:>8.4f}  "
            f"{cagr*100:>7.2f}%  "
            f"{maxdd*100:>7.2f}%  "
            f"{d_sharpe_pct:>+8.1f}%  "
            f"{d_calmar:>+8.4f}  "
            f"{d_maxdd_pp:>+7.2f}pp"
            f"  {verdict}{tag}"
        )

    print("─" * 110)
    print()

    # Calmar-peak check
    all_calmar = [r.get("metrics", {}).get("calmar", float("-inf")) for r in results]
    zero_idx = next((i for i, r in enumerate(results) if abs(r["combined_weight"]) < 1e-6), None)
    calmar_peak_at_zero = (zero_idx is not None and all_calmar[zero_idx] == max(all_calmar))

    print("ADOPTION CRITERIA")
    print("  (1) ΔSharpe vs w=0.0  > +1%    — must add meaningful Sharpe")
    print("  (2) Not Calmar-peak at w=0.0   — signal must genuinely add value")
    if calmar_peak_at_zero:
        print("  ✗ CALMAR-PEAK AT w=0.0: Calmar is best when signal is absent → REJECT")
    else:
        print("  ✓ Calmar is NOT peak at w=0.0")
    print()

    if candidates and not calmar_peak_at_zero:
        best = max(candidates, key=lambda r: r.get("metrics", {}).get("calmar", float("-inf")))
        best_w    = best["combined_weight"]
        best_each = best_w / 3.0
        best_m    = best.get("metrics", {})
        d_s       = (best_m.get("sharpe", 0) - b_sharpe) / abs(b_sharpe) * 100 if b_sharpe else 0.0
        d_c       = best_m.get("calmar", 0) - b_calmar
        d_maxdd   = (best_m.get("max_dd", 0) - b_maxdd) * 100

        rules = GATED_RULES if variant == "gated" else UNGATED_RULES
        rule_names = "\n    ".join(f"{r}: {best_each:.4f}" for r in rules)

        print(f"  RECOMMENDATION: ADOPT variant={variant}, combined weight={best_w:.2f}")
        print(f"    Per rule: {best_each:.4f} each")
        print(f"    vs no-demeaned baseline:  ΔSharpe={d_s:+.1f}%,  ΔCalmar={d_c:+.4f},  ΔMaxDD={d_maxdd:+.2f}pp")
        print()
        print("  NEXT STEP: Update forecast_weights in both configs:")
        print(f"    {rule_names}")
        print()
        print("  Set non-winning variant rules to 0.0. Then verify on 1k config. Commit.")
    else:
        if calmar_peak_at_zero:
            print("  RECOMMENDATION: REJECT — Calmar-peak at w=0.0.")
        else:
            print("  RECOMMENDATION: REJECT — No weight passes ΔSharpe ≥ +1%.")
        print("  → Keep all demeaned_carry weights at 0.0 in both configs.")
    print()


def main():
    parser = argparse.ArgumentParser(
        description="Sweep demeaned_carry combined weight.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--base-config", type=Path,
        default=Path("config/crypto_perps_full_rules.yaml"),
    )
    parser.add_argument(
        "--data", type=Path,
        default=Path("data/dataset_538registry_6yr_jagged.parquet"),
    )
    parser.add_argument(
        "--outdir", type=Path,
        default=Path("out/demeaned_carry_sweep"),
    )
    parser.add_argument(
        "--phase", type=int, choices=[1, 2, 12], default=12,
        help="Phase to run: 1=gated vs ungated, 2=weight sweep, 12=both (default: 12)",
    )
    parser.add_argument(
        "--variant", type=str, choices=["ungated", "gated"], default=None,
        help="Phase 2: which variant to sweep (required if --phase 2)",
    )
    parser.add_argument(
        "--weights", type=float, nargs="+",
        default=[0.0, 0.03, 0.06, 0.09, 0.12, 0.15, 0.18],
        help="Phase 2 combined weight grid (default: 0.0 0.03 0.06 0.09 0.12 0.15 0.18)",
    )
    parser.add_argument(
        "--skip-existing", action="store_true",
        help="Skip runs where outdir/performance_summary.json already exists",
    )

    args = parser.parse_args()

    if args.phase == 2 and args.variant is None:
        parser.error("--variant is required when --phase 2")

    if not args.base_config.exists():
        print(f"ERROR: base config not found: {args.base_config}")
        sys.exit(1)
    if not args.data.exists():
        print(f"ERROR: data file not found: {args.data}")
        sys.exit(1)

    args.outdir.mkdir(parents=True, exist_ok=True)

    base_cfg = load_yaml(args.base_config)

    print(f"Base config:  {args.base_config}")
    print(f"Data:         {args.data}")
    print(f"Output dir:   {args.outdir}")
    print(f"Phase(s):     {args.phase}")
    print()

    # ── Phase 1: Gated vs Ungated ──────────────────────────────────────────
    phase1_winner = None

    if args.phase in (1, 12):
        print("=" * 60)
        print("PHASE 1: Gated vs Ungated (combined weight = 0.15)")
        print("=" * 60)
        print()

        p1_results = []

        # Run baseline (w=0.0)
        tag        = "baseline"
        run_outdir = args.outdir / "p1_baseline"
        print(f"Running baseline (w=0.0)  →  {run_outdir}")

        if args.skip_existing and (run_outdir / "performance_summary.json").exists():
            print("  Skipping (--skip-existing)")
        else:
            cfg = set_demeaned_weights(base_cfg, [], 0.0)
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".yaml", delete=False, dir=args.outdir
            ) as tmp:
                yaml.dump(cfg, tmp, default_flow_style=False, sort_keys=False)
                tmp_path = Path(tmp.name)
            try:
                run_backtest(tmp_path, args.data, run_outdir)
            finally:
                tmp_path.unlink(missing_ok=True)

        r = load_results(run_outdir)
        r["variant"] = "baseline"
        p1_results.append(r)
        m = r.get("metrics", {})
        print(f"  Sharpe={m.get('sharpe', float('nan')):.4f}  Calmar={m.get('calmar', float('nan')):.4f}")
        print()

        # Run ungated and gated at w=0.15
        for variant_name, rules in [("ungated", UNGATED_RULES), ("gated", GATED_RULES)]:
            w_combined = 0.15
            w_each     = w_combined / 3.0
            run_outdir = args.outdir / f"p1_{variant_name}"
            print(f"Running {variant_name} (w={w_combined:.2f}, {w_each:.4f}/rule)  →  {run_outdir}")

            if args.skip_existing and (run_outdir / "performance_summary.json").exists():
                print("  Skipping (--skip-existing)")
            else:
                cfg = set_demeaned_weights(base_cfg, rules, w_each)
                with tempfile.NamedTemporaryFile(
                    mode="w", suffix=".yaml", delete=False, dir=args.outdir
                ) as tmp:
                    yaml.dump(cfg, tmp, default_flow_style=False, sort_keys=False)
                    tmp_path = Path(tmp.name)
                try:
                    run_backtest(tmp_path, args.data, run_outdir)
                finally:
                    tmp_path.unlink(missing_ok=True)

            r = load_results(run_outdir)
            r["variant"] = variant_name
            p1_results.append(r)
            m = r.get("metrics", {})
            print(f"  Sharpe={m.get('sharpe', float('nan')):.4f}  Calmar={m.get('calmar', float('nan')):.4f}")
            print()

        phase1_winner = print_phase1_table(p1_results)

        # Save Phase 1 summary
        p1_summary = args.outdir / "phase1_summary.json"
        with open(p1_summary, "w") as f:
            json.dump(p1_results, f, indent=2, default=str)
        print(f"Phase 1 results saved: {p1_summary}")
        print()

    # ── Phase 2: Weight Sweep ──────────────────────────────────────────────
    if args.phase in (2, 12):
        # Determine which variant to sweep
        if args.phase == 12:
            if phase1_winner is None:
                print("Phase 1 found no winner. Running Phase 2 for both variants.")
                variants_to_sweep = ["ungated", "gated"]
            else:
                variants_to_sweep = [phase1_winner["variant"]]
        else:
            variants_to_sweep = [args.variant]

        for variant in variants_to_sweep:
            rules = GATED_RULES if variant == "gated" else UNGATED_RULES

            print("=" * 60)
            print(f"PHASE 2: Weight sweep — variant={variant}")
            print(f"Rules: {rules}")
            print(f"Grid:  {args.weights}")
            print("=" * 60)
            print()

            p2_results = []

            for w_combined in args.weights:
                w_each = w_combined / 3.0
                tag        = f"w{w_combined:.2f}".replace(".", "p")
                run_outdir = args.outdir / f"p2_{variant}_{tag}"

                print(f"Running variant={variant}, w_combined={w_combined:.2f}  ({w_each:.4f}/rule)  →  {run_outdir}")

                if args.skip_existing and (run_outdir / "performance_summary.json").exists():
                    print("  Skipping (--skip-existing)")
                else:
                    cfg = set_demeaned_weights(base_cfg, rules, w_each)
                    with tempfile.NamedTemporaryFile(
                        mode="w", suffix=".yaml", delete=False, dir=args.outdir
                    ) as tmp:
                        yaml.dump(cfg, tmp, default_flow_style=False, sort_keys=False)
                        tmp_path = Path(tmp.name)
                    try:
                        run_backtest(tmp_path, args.data, run_outdir)
                    finally:
                        tmp_path.unlink(missing_ok=True)

                r = load_results(run_outdir)
                r["combined_weight"] = w_combined
                r["variant"]         = variant
                p2_results.append(r)

                m = r.get("metrics", {})
                print(
                    f"  Sharpe={m.get('sharpe', float('nan')):.4f}  "
                    f"Calmar={m.get('calmar', float('nan')):.4f}  "
                    f"CAGR={m.get('cagr', 0)*100:.2f}%  "
                    f"MaxDD={m.get('max_dd', 0)*100:.2f}%"
                )
                print()

            print_phase2_table(p2_results, variant)

            p2_summary = args.outdir / f"phase2_{variant}_summary.json"
            with open(p2_summary, "w") as f:
                json.dump(p2_results, f, indent=2, default=str)
            print(f"Phase 2 ({variant}) results saved: {p2_summary}")
            print()


if __name__ == "__main__":
    main()
