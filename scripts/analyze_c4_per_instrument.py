#!/usr/bin/env python3
"""
Per-instrument PnL decomposition for the C4 ADOPT result.

The harness only computes portfolio-level Sharpe — but the C4 ADOPT verdict
could plausibly be carried by a few long-history instruments (BTC/ETH).
This script answers: is the +0.13 ΔSharpe distributed across the universe,
or concentrated?

Method:
  1. Load positions.csv from baseline + candidate backtest dirs.
  2. Load close prices from the SB-corrected dataset.
  3. Compute per-instrument daily PnL contribution:
       pnl[instr, t] = position[instr, t-1] × (close[instr, t] - close[instr, t-1]) / close[instr, t-1]
  4. Stitch each instrument's daily-pnl series, compute per-instrument Sharpe.
  5. Rank instruments by ΔSharpe and ΔPnL contribution.
  6. Compute concentration metrics: top-1, top-5 share of positive ΔPnL; Gini.

The position units are base-asset contracts (per `trade_plan.py:104-135`)
which is what the % return formula uses directly. (We're not converting to
USD for this analysis — we want the per-instrument contribution to the
PORTFOLIO daily return, not absolute USD PnL, so the % return formula is
the right primitive.)

Usage:
    python scripts/analyze_c4_per_instrument.py \\
        --candidate-dir out/wf_c4_xgboost_h20/backtest_c4_xgboost_h20 \\
        --baseline-dir out/wf_c4_xgboost_h20/backtest_flat_baseline \\
        --dataset data/dataset_sb_corrected_6yr_jagged.parquet \\
        --out-dir out/wf_c4_xgboost_h20/per_instrument
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_positions(backtest_dir: Path) -> pd.DataFrame:
    df = pd.read_csv(backtest_dir / "positions.csv", index_col=0, parse_dates=True)
    return df


def _load_close_panel(dataset_path: Path) -> pd.DataFrame:
    """Pivot the long-form dataset to (date × instrument) close-price wide form."""
    raw = pd.read_parquet(dataset_path)
    if not {"date", "instrument", "close"}.issubset(raw.columns):
        raise ValueError(
            f"Dataset {dataset_path} missing required columns. "
            f"Got: {list(raw.columns)}; need date/instrument/close."
        )
    raw["date"] = pd.to_datetime(raw["date"])
    panel = raw.pivot(index="date", columns="instrument", values="close")
    return panel.sort_index()


def _per_instrument_daily_pnl(positions: pd.DataFrame, close: pd.DataFrame) -> pd.DataFrame:
    """Returns a (date × instrument) DataFrame whose cells are the daily PnL
    contribution per instrument expressed as fractional return on that
    instrument's notional (i.e. position-weighted % return).

    pnl[t] = pos[t-1] * pct_change(close)[t]   (per instrument)

    Using position[t-1] (lagged) to avoid in-bar look-ahead — today's
    return is realized given yesterday's position.
    """
    common_instr = sorted(set(positions.columns) & set(close.columns))
    pos = positions[common_instr]
    px = close[common_instr]
    # Align on the union of dates; positions cover the full backtest window
    # but close may have extra warmup dates outside that window.
    common_dates = pos.index.intersection(px.index)
    pos = pos.loc[common_dates]
    px = px.loc[common_dates]

    pct = px.pct_change()
    pos_lag = pos.shift(1)
    pnl = pos_lag * pct
    return pnl.fillna(0.0)


def _sharpe(s: pd.Series, ann_factor: float = 365.0) -> float:
    s = s.dropna()
    if len(s) < 30 or s.std() == 0:
        return float("nan")
    return float(s.mean() / s.std() * np.sqrt(ann_factor))


def _gini(values: np.ndarray) -> float:
    """Gini coefficient for a non-negative series. 0 = perfectly equal,
    1 = total concentration. Uses the standard formula:
        G = sum_i sum_j |x_i - x_j| / (2 n^2 mean)
    """
    x = np.array([v for v in values if np.isfinite(v) and v > 0])
    n = len(x)
    if n == 0:
        return float("nan")
    x = np.sort(x)
    cum = np.cumsum(x)
    return float((n + 1 - 2 * np.sum(cum) / cum[-1]) / n) if cum[-1] > 0 else float("nan")


def main() -> int:
    p = argparse.ArgumentParser(description="C4 per-instrument decomposition")
    p.add_argument("--candidate-dir", type=Path, required=True)
    p.add_argument("--baseline-dir", type=Path, required=True)
    p.add_argument(
        "--dataset",
        type=Path,
        default=REPO_ROOT / "data/dataset_sb_corrected_6yr_jagged.parquet",
    )
    p.add_argument("--out-dir", type=Path, required=True)
    p.add_argument(
        "--min-trading-days",
        type=int,
        default=200,
        help="Instruments with fewer days of nonzero positions are dropped from the "
        "concentration check (too short to have a stable Sharpe estimate).",
    )
    args = p.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)

    print("=== C4 per-instrument decomposition ===")
    print(f"Candidate: {args.candidate_dir}")
    print(f"Baseline:  {args.baseline_dir}")

    print("Loading positions + close prices ...")
    pos_b = _load_positions(args.baseline_dir)
    pos_c = _load_positions(args.candidate_dir)
    close = _load_close_panel(args.dataset)
    print(f"  baseline positions:  {pos_b.shape}")
    print(f"  candidate positions: {pos_c.shape}")
    print(f"  close panel:         {close.shape}")

    print("Computing per-instrument daily PnL ...")
    pnl_b = _per_instrument_daily_pnl(pos_b, close)
    pnl_c = _per_instrument_daily_pnl(pos_c, close)
    common_instr = sorted(set(pnl_b.columns) & set(pnl_c.columns))
    pnl_b = pnl_b[common_instr]
    pnl_c = pnl_c[common_instr]

    # Persist the wide PnL panels for downstream audit.
    pnl_b.to_parquet(args.out_dir / "per_instrument_pnl_baseline.parquet")
    pnl_c.to_parquet(args.out_dir / "per_instrument_pnl_candidate.parquet")

    print("Aggregating per-instrument metrics ...")
    n_active = (pnl_c.fillna(0) != 0).sum(axis=0)  # nonzero PnL days per instr
    sharpe_b = pnl_b.apply(_sharpe, axis=0)
    sharpe_c = pnl_c.apply(_sharpe, axis=0)
    delta_sharpe = sharpe_c - sharpe_b
    total_pnl_b = pnl_b.sum(axis=0)
    total_pnl_c = pnl_c.sum(axis=0)
    delta_pnl = total_pnl_c - total_pnl_b

    summary = pd.DataFrame({
        "n_trading_days": n_active,
        "sharpe_baseline": sharpe_b,
        "sharpe_candidate": sharpe_c,
        "delta_sharpe": delta_sharpe,
        "total_pnl_baseline": total_pnl_b,
        "total_pnl_candidate": total_pnl_c,
        "delta_pnl": delta_pnl,
    })
    summary = summary.sort_values("delta_pnl", ascending=False)
    summary.to_parquet(args.out_dir / "per_instrument_summary.parquet")

    qualified = summary[summary["n_trading_days"] >= args.min_trading_days]
    print(f"  Total instruments: {len(summary)}")
    print(f"  Qualified (>= {args.min_trading_days} trading days): {len(qualified)}")

    # ---------- Concentration checks ----------
    pos = qualified[qualified["delta_pnl"] > 0]
    pos_sorted = pos.sort_values("delta_pnl", ascending=False)
    total_positive = pos_sorted["delta_pnl"].sum()
    top1_share = pos_sorted["delta_pnl"].iloc[0] / total_positive if len(pos_sorted) > 0 else float("nan")
    top5_share = pos_sorted["delta_pnl"].head(5).sum() / total_positive if len(pos_sorted) > 0 else float("nan")
    gini = _gini(qualified["delta_pnl"].values)
    pct_positive = (qualified["delta_sharpe"] > 0).mean()

    # Also: top-1 contribution to total positive PnL (not just delta_pnl)
    abs_top1 = qualified.loc[qualified["delta_pnl"].idxmax(), "delta_pnl"]
    abs_total = qualified["delta_pnl"].sum()

    print(f"\n=== Concentration metrics ===")
    print(f"Top-1 share of total positive ΔPnL: {top1_share:.1%}  (gate: <25%)")
    print(f"Top-5 share of total positive ΔPnL: {top5_share:.1%}  (gate: <50%)")
    print(f"Fraction of qualified instruments with ΔSharpe > 0: {pct_positive:.1%}  (gate: >=60%)")
    print(f"Gini coefficient on per-instrument ΔPnL (qualified, positive only): {gini:.3f}")

    # ---------- Markdown summary ----------
    def _md_table(df: pd.DataFrame) -> str:
        """Hand-roll a markdown table to avoid the `tabulate` dependency."""
        cols = ["instrument"] + list(df.columns)
        out = ["| " + " | ".join(cols) + " |"]
        out.append("|" + "|".join(["---"] * len(cols)) + "|")
        for instr, row in df.iterrows():
            cells = [str(instr)]
            for c in df.columns:
                v = row[c]
                if isinstance(v, (int, np.integer)):
                    cells.append(f"{int(v)}")
                elif isinstance(v, float) and not np.isnan(v):
                    cells.append(f"{v:.5f}")
                else:
                    cells.append(str(v))
            out.append("| " + " | ".join(cells) + " |")
        return "\n".join(out)

    lines = [
        "# C4 h=20 per-instrument decomposition",
        "",
        f"- Candidate: `{args.candidate_dir}`",
        f"- Baseline:  `{args.baseline_dir}`",
        f"- Total instruments evaluated: {len(summary)}",
        f"- Qualified (>= {args.min_trading_days} trading days): {len(qualified)}",
        "",
        "## Pre-stated pass criteria",
        "",
        "| metric | gate | result | pass |",
        "|---|---|---|---|",
        f"| Top-1 share of positive ΔPnL | < 25% | {top1_share:.1%} | {'✓' if top1_share < 0.25 else '✗'} |",
        f"| Top-5 share of positive ΔPnL | < 50% | {top5_share:.1%} | {'✓' if top5_share < 0.50 else '✗'} |",
        f"| % qualified with ΔSharpe > 0 | ≥ 60% | {pct_positive:.1%} | {'✓' if pct_positive >= 0.60 else '✗'} |",
        f"| Gini on positive ΔPnL (qualified) | (informational) | {gini:.3f} | — |",
        "",
        "## Top-20 positive ΔSharpe contributors (qualified)",
        "",
        _md_table(qualified.sort_values("delta_sharpe", ascending=False).head(20)),
        "",
        "## Top-20 positive ΔPnL contributors (qualified)",
        "",
        _md_table(qualified.sort_values("delta_pnl", ascending=False).head(20)),
        "",
        "## Bottom-20 ΔPnL contributors (where the candidate hurts)",
        "",
        _md_table(qualified.sort_values("delta_pnl", ascending=True).head(20)),
        "",
        "## Falsification trigger",
        "",
        "If Top-1 share of positive ΔPnL exceeds 50%, the lift is one-instrument-driven "
        "and the result is fragile — REJECT promotion regardless of other checks.",
    ]
    (args.out_dir / "decomposition.md").write_text("\n".join(lines))
    print(f"\nWrote {args.out_dir / 'decomposition.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
