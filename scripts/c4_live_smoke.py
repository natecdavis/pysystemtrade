#!/usr/bin/env python3
"""
C4 live trade-plan smoke test.

Runs `scripts/generate_trade_plan.py` against the BASELINE and CANDIDATE
backtest output dirs (the same two dirs the harness used to score the C4
ADOPT verdict), then diffs the resulting trade plans to verify three
invariants:

  1. Where the multiplier panel's last-date value is within ±0.001 of 1.0,
     the per-instrument trade-plan delta should be zero (within float
     precision). The hook returns identity when multiplier == 1.0.

  2. Where the multiplier deviates: the SIGN of (candidate_target -
     baseline_target) should match the SIGN of (multiplier - 1) ×
     baseline_target in at least 95% of cases. (5% slack for buffer-
     induced rounding in the trade-plan layer.)

  3. Instruments in the live universe but missing from the multiplier
     panel produce zero deltas (identity-fallback).

This is the end-to-end plumbing check before promoting the multiplier to
live config — it confirms the multiplier panel actually flows through the
trade-plan pipeline producing per-instrument deltas in the expected
direction and magnitude.

The smoke test does NOT validate that the multiplier IMPROVES live
performance — that's the harness's job, already done.

Usage:
    python scripts/c4_live_smoke.py \\
        --baseline-dir out/wf_c4_xgboost_h20/backtest_flat_baseline \\
        --candidate-dir out/wf_c4_xgboost_h20/backtest_c4_xgboost_h20 \\
        --multiplier-panel out/wf_c4_xgboost_h20/multiplier_panel.parquet \\
        --out-dir out/wf_c4_xgboost_h20/live_smoke
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent

# Smoke-test "live" parameters — values are arbitrary; what matters is that
# both invocations use the same as_of_date and equity, so any per-instrument
# delta is attributable to the multiplier alone.
SMOKE_EQUITY_USD = 10_000.0
TOL_MULT_IS_IDENTITY = 1e-3   # |multiplier - 1| below this = identity
TOL_DELTA_IS_ZERO = 1e-9      # absolute target-position delta below this = zero


def _last_date_in_backtest(backtest_dir: Path) -> str:
    """Return the YYYY-MM-DD of the last row in positions.csv — must match
    --as-of-date for `generate_trade_plan.py`.
    """
    pos = pd.read_csv(backtest_dir / "positions.csv", index_col=0, parse_dates=True)
    return str(pos.index.max().date())


def _write_zero_positions(path: Path, instruments: list[str]) -> None:
    """Stub actual_positions CSV: zero contracts everywhere. The trade plan
    will compute target deltas against zero, so each row's delta == its
    target — the cleanest input for a smoke comparison.
    """
    df = pd.DataFrame({
        "instrument": instruments,
        "hl_symbol": [s.replace("USDT_PERP", "") for s in instruments],
        "contracts": 0.0,
        "timestamp": pd.Timestamp.utcnow().isoformat(),
    })
    df.to_csv(path, index=False)


def _run_trade_plan(
    backtest_dir: Path,
    actual_positions: Path,
    as_of_date: str,
    output_dir: Path,
) -> Path:
    """Invoke generate_trade_plan.py, return the trade-plan CSV path."""
    output_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        str(REPO_ROOT / "scripts" / "generate_trade_plan.py"),
        "--backtest-dir", str(backtest_dir),
        "--actual-positions", str(actual_positions),
        "--current-equity", str(SMOKE_EQUITY_USD),
        "--as-of-date", as_of_date,
        "--output-dir", str(output_dir),
    ]
    print(f"  Running: {' '.join(cmd)}")
    subprocess.run(cmd, check=True, cwd=str(REPO_ROOT))
    expected = output_dir / f"trade_plan_{as_of_date}.csv"
    if not expected.exists():
        raise FileNotFoundError(
            f"trade_plan CSV not produced at {expected} (subprocess succeeded but no output)"
        )
    return expected


def _last_panel_multipliers(panel_path: Path) -> pd.Series:
    """Multiplier per instrument as of the most recent date in the panel.
    NaN means the instrument is not in the panel (hook returns identity).
    """
    panel = pd.read_parquet(panel_path)
    last = panel.iloc[-1]
    return last


def _diff_trade_plans(
    baseline_csv: Path,
    candidate_csv: Path,
    multipliers: pd.Series,
) -> pd.DataFrame:
    b = pd.read_csv(baseline_csv)
    c = pd.read_csv(candidate_csv)
    # Trade plans are indexed by instrument; the column with the target
    # position varies across versions of the script. Pick the most likely
    # candidate columns and fail loudly if the schema changed.
    target_col = None
    for c_name in ("target_contracts", "target_position", "target", "contracts_target"):
        if c_name in b.columns:
            target_col = c_name
            break
    if target_col is None:
        raise ValueError(
            f"Could not find a target-position column in trade_plan.csv. "
            f"Got columns: {list(b.columns)}. "
            f"Update scripts/c4_live_smoke.py to match."
        )
    # Align on instrument
    if "instrument" not in b.columns:
        raise ValueError(f"trade_plan missing 'instrument' column. Got: {list(b.columns)}")
    bi = b.set_index("instrument")[target_col].rename("baseline_target")
    ci = c.set_index("instrument")[target_col].rename("candidate_target")
    diff = pd.concat([bi, ci], axis=1).fillna(0.0)
    diff["delta_target"] = diff["candidate_target"] - diff["baseline_target"]
    diff["multiplier"] = multipliers.reindex(diff.index)
    diff["multiplier_is_identity"] = (
        diff["multiplier"].isna() | (diff["multiplier"].sub(1.0).abs() < TOL_MULT_IS_IDENTITY)
    )
    diff["delta_is_zero"] = diff["delta_target"].abs() < TOL_DELTA_IS_ZERO
    diff["expected_sign"] = np.sign((diff["multiplier"] - 1.0).fillna(0.0)) * np.sign(diff["baseline_target"])
    diff["actual_sign"] = np.sign(diff["delta_target"])
    diff["sign_matches"] = diff["expected_sign"] == diff["actual_sign"]
    return diff


def _summarize(diff: pd.DataFrame, out_path: Path) -> dict:
    """Compute the three invariant checks and write a markdown summary."""
    n = len(diff)
    identity = diff[diff["multiplier_is_identity"]]
    non_identity = diff[~diff["multiplier_is_identity"]]

    # Invariant 1: identity rows have zero delta
    identity_with_nonzero_delta = identity[~identity["delta_is_zero"]]
    inv1_pass_pct = 1 - len(identity_with_nonzero_delta) / max(len(identity), 1)

    # Invariant 2: non-identity rows have sign-matching delta. Exclude rows
    # where baseline_target==0 (no position to scale).
    sign_test = non_identity[non_identity["baseline_target"].abs() > 0]
    sign_match_pct = sign_test["sign_matches"].mean() if len(sign_test) > 0 else float("nan")

    # Invariant 3: panel-missing instruments (multiplier==NaN) → identity behaviour
    missing = diff[diff["multiplier"].isna()]
    missing_with_nonzero_delta = missing[~missing["delta_is_zero"]]
    inv3_pass_pct = 1 - len(missing_with_nonzero_delta) / max(len(missing), 1)

    result = {
        "n_instruments": int(n),
        "n_identity": int(len(identity)),
        "n_non_identity": int(len(non_identity)),
        "n_panel_missing": int(len(missing)),
        "inv1_identity_implies_zero_delta_pct": float(inv1_pass_pct),
        "inv2_non_identity_sign_match_pct": float(sign_match_pct),
        "inv3_panel_missing_implies_zero_delta_pct": float(inv3_pass_pct),
    }

    inv1_pass = inv1_pass_pct >= 0.999
    inv2_pass = (not np.isnan(sign_match_pct)) and sign_match_pct >= 0.95
    inv3_pass = inv3_pass_pct >= 0.999

    lines = [
        "# C4 h=20 live trade-plan smoke test",
        "",
        f"- Total instruments: {n}",
        f"- Identity multipliers (|m-1|<{TOL_MULT_IS_IDENTITY}): {len(identity)}",
        f"- Non-identity multipliers: {len(non_identity)}",
        f"- Missing from panel (NaN mult.): {len(missing)}",
        "",
        "## Invariant checks",
        "",
        "| invariant | gate | result | pass |",
        "|---|---|---|---|",
        f"| 1. Identity multiplier ⇒ zero delta | ≥99.9% | {inv1_pass_pct:.2%} | {'✓' if inv1_pass else '✗'} |",
        f"| 2. Non-identity ⇒ sign matches (m-1)×baseline_target | ≥95% | "
        f"{sign_match_pct:.2%} | {'✓' if inv2_pass else '✗'} |",
        f"| 3. Missing from panel ⇒ zero delta | ≥99.9% | {inv3_pass_pct:.2%} | {'✓' if inv3_pass else '✗'} |",
        "",
        "## Top 20 |delta| trades",
        "",
        "| instrument | baseline | candidate | delta | mult | sign_match |",
        "|---|---|---|---|---|---|",
    ]
    top = diff.assign(absd=diff["delta_target"].abs()).sort_values("absd", ascending=False).head(20)
    for instr, row in top.iterrows():
        lines.append(
            f"| {instr} | {row['baseline_target']:.4f} | {row['candidate_target']:.4f} "
            f"| {row['delta_target']:+.4f} | {row['multiplier']:.4f} | "
            f"{'✓' if row['sign_matches'] else '✗'} |"
        )

    if len(identity_with_nonzero_delta) > 0:
        lines += ["", "## Invariant-1 violations (identity but non-zero delta)", ""]
        lines += [_format_violation_row(instr, r) for instr, r in identity_with_nonzero_delta.head(10).iterrows()]

    out_path.write_text("\n".join(lines))
    return result


def _format_violation_row(instr: str, r: pd.Series) -> str:
    return (f"- {instr}: baseline={r['baseline_target']:.6f}, "
            f"candidate={r['candidate_target']:.6f}, delta={r['delta_target']:+.6f}, "
            f"mult={r['multiplier']:.6f}")


def main() -> int:
    p = argparse.ArgumentParser(description="C4 live trade-plan smoke test")
    p.add_argument("--baseline-dir", type=Path, required=True)
    p.add_argument("--candidate-dir", type=Path, required=True)
    p.add_argument("--multiplier-panel", type=Path, required=True)
    p.add_argument("--out-dir", type=Path, required=True)
    args = p.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)

    print("=== C4 live trade-plan smoke test ===")

    # Both backtests should end on the same date
    base_last = _last_date_in_backtest(args.baseline_dir)
    cand_last = _last_date_in_backtest(args.candidate_dir)
    if base_last != cand_last:
        print(f"WARNING: baseline ends {base_last} but candidate ends {cand_last}")
    as_of = base_last
    print(f"as_of_date: {as_of}")

    # Build a zero-positions stub covering the union of instruments in both runs
    pos_b = pd.read_csv(args.baseline_dir / "positions.csv", index_col=0, parse_dates=True)
    pos_c = pd.read_csv(args.candidate_dir / "positions.csv", index_col=0, parse_dates=True)
    instruments = sorted(set(pos_b.columns) | set(pos_c.columns))
    stub_path = args.out_dir / "actual_positions_zero.csv"
    _write_zero_positions(stub_path, instruments)
    print(f"Wrote {stub_path} ({len(instruments)} instruments)")

    # Run trade-plan twice
    print("\nRunning trade plan against BASELINE backtest dir ...")
    baseline_csv = _run_trade_plan(args.baseline_dir, stub_path, as_of, args.out_dir / "baseline")

    print("\nRunning trade plan against CANDIDATE backtest dir ...")
    candidate_csv = _run_trade_plan(args.candidate_dir, stub_path, as_of, args.out_dir / "candidate")

    # Diff
    print("\nDiffing trade plans ...")
    multipliers = _last_panel_multipliers(args.multiplier_panel)
    diff = _diff_trade_plans(baseline_csv, candidate_csv, multipliers)
    diff.to_parquet(args.out_dir / "trade_plan_diff.parquet")

    summary_path = args.out_dir / "diff_summary.md"
    result = _summarize(diff, summary_path)
    print(f"\nWrote {summary_path}")
    print(f"\nInvariant checks:")
    print(f"  1. Identity ⇒ zero delta:  {result['inv1_identity_implies_zero_delta_pct']:.2%} (gate: ≥99.9%)")
    print(f"  2. Non-identity sign match: {result['inv2_non_identity_sign_match_pct']:.2%} (gate: ≥95%)")
    print(f"  3. Panel-missing ⇒ zero:   {result['inv3_panel_missing_implies_zero_delta_pct']:.2%} (gate: ≥99.9%)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
