"""
Sweep drawdown-contingent leverage overlay parameters.

Reads daily_returns.csv from a backtest output directory and sweeps a grid of
(base_leverage, dd_threshold, min_scale) to find Calmar-peak configuration.

Usage:
    python scripts/sweep_dd_leverage.py \\
        --backtest-dir out/dd_leverage_base \\
        [--base-leverages 1.0 1.5 2.0 2.26] \\
        [--dd-thresholds 0.05 0.10 0.15] \\
        [--min-scales 0.0 0.25 0.50]
"""

import argparse
import sys
from pathlib import Path

import pandas as pd

# Allow running from repo root
sys.path.insert(0, str(Path(__file__).parent.parent))

from systems.crypto_perps.leverage_overlay import compute_overlay_metrics


def sweep(
    base_returns: pd.Series,
    base_leverages: list[float],
    dd_thresholds: list[float],
    min_scales: list[float],
) -> list[dict]:
    rows = []

    for bl in base_leverages:
        # Baseline row: no overlay (pure leverage)
        m = compute_overlay_metrics(base_returns, bl, dd_threshold=0.0, min_scale=1.0)
        rows.append({
            "base_leverage": bl,
            "dd_threshold": 0.0,
            "min_scale": None,
            **m,
        })
        # Overlay rows
        for thr in dd_thresholds:
            for ms in min_scales:
                m = compute_overlay_metrics(base_returns, bl, thr, ms)
                rows.append({
                    "base_leverage": bl,
                    "dd_threshold": thr,
                    "min_scale": ms,
                    **m,
                })

    return rows


def format_table(rows: list[dict]) -> str:
    # Find global Calmar peak
    best_calmar = max(r["calmar"] for r in rows)

    header = (
        f"{'dd_threshold':>14} {'min_scale':>10} {'Sharpe':>8} "
        f"{'Calmar':>8} {'CAGR':>8} {'MaxDD':>8} {'Avg Lev':>9}"
    )
    sep = "-" * len(header)

    lines = []
    current_bl = None

    for r in rows:
        if r["base_leverage"] != current_bl:
            current_bl = r["base_leverage"]
            lines.append("")
            lines.append(f"=== base_leverage = {current_bl:.2f}x ===")
            lines.append(header)
            lines.append(sep)

        thr = r["dd_threshold"]
        ms = r["min_scale"]

        if thr == 0.0:
            thr_str = "0% (none)"
            ms_str = "—"
        else:
            thr_str = f"{thr*100:.0f}%"
            ms_str = f"{ms:.2f}"

        flag = " ✓" if abs(r["calmar"] - best_calmar) < 1e-9 else ""

        line = (
            f"{thr_str:>14} {ms_str:>10} {r['sharpe']:>8.3f} "
            f"{r['calmar']:>8.3f} {r['cagr']*100:>7.1f}% "
            f"{r['max_dd']*100:>7.1f}% {r['avg_leverage']:>8.2f}x"
            f"{flag}"
        )
        lines.append(line)

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Sweep DD-contingent leverage overlay")
    parser.add_argument("--backtest-dir", required=True, help="Directory with daily_returns.csv")
    parser.add_argument(
        "--base-leverages", nargs="+", type=float,
        default=[1.0, 1.5, 2.0, 2.26],
        help="List of base leverage multipliers"
    )
    parser.add_argument(
        "--dd-thresholds", nargs="+", type=float,
        default=[0.05, 0.10, 0.15],
        help="DD thresholds (fraction) at which leverage reaches min_scale"
    )
    parser.add_argument(
        "--min-scales", nargs="+", type=float,
        default=[0.0, 0.25, 0.50],
        help="Minimum leverage scalar (0=flat, 1=no reduction)"
    )
    args = parser.parse_args()

    returns_path = Path(args.backtest_dir) / "daily_returns.csv"
    if not returns_path.exists():
        print(f"ERROR: {returns_path} not found. Run the backtest first.", file=sys.stderr)
        sys.exit(1)

    df = pd.read_csv(returns_path, index_col=0, parse_dates=True)
    base_returns = df.iloc[:, 0].dropna()
    print(f"Loaded {len(base_returns)} daily returns from {returns_path}")
    print(f"Period: {base_returns.index[0].date()} → {base_returns.index[-1].date()}")

    rows = sweep(base_returns, args.base_leverages, args.dd_thresholds, args.min_scales)
    print(format_table(rows))
    print()

    # Summary: best Calmar per base_leverage
    print("=== Calmar-peak per base_leverage ===")
    seen = {}
    for r in rows:
        bl = r["base_leverage"]
        if bl not in seen or r["calmar"] > seen[bl]["calmar"]:
            seen[bl] = r
    for bl, r in sorted(seen.items()):
        thr = r["dd_threshold"]
        ms = r["min_scale"]
        thr_str = "none" if thr == 0.0 else f"dd_thr={thr*100:.0f}%"
        ms_str = "" if ms is None else f", min_scale={ms:.2f}"
        print(
            f"  {bl:.2f}x  ({thr_str}{ms_str})  "
            f"Sharpe={r['sharpe']:.3f}  Calmar={r['calmar']:.3f}  "
            f"MaxDD={r['max_dd']*100:.1f}%  CAGR={r['cagr']*100:.1f}%  "
            f"AvgLev={r['avg_leverage']:.2f}x"
        )


if __name__ == "__main__":
    main()
