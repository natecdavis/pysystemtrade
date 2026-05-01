#!/usr/bin/env python3
"""
Sweep coverage-aware FDM scaling on flat-68 SB-corrected, 1k config.

Tests FDM_eff = FDM_base × (n_active_rules / n_total_rules) ** alpha
for alpha in {0, 0.5, 1.0}. alpha=0 reproduces baseline (smoke test).

Usage:
    python scripts/sweep_fdm_coverage.py [--force] [--alpha 0 0.5 1.0]
"""
import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

DEFAULT_CONFIG     = "config/crypto_perps_1k.yaml"
DEFAULT_DATA       = "data/dataset_sb_corrected_6yr_jagged.parquet"
DEFAULT_OUTDIR     = "out/fdm_cov_sweep"
DEFAULT_ALPHAS     = [0.0, 0.5, 1.0]


def label_for(alpha: float) -> str:
    return f"a{alpha:.2f}".replace(".", "p")


def run_backtest(config_path: str, data_path: str, outdir: Path,
                 extra_config: dict) -> None:
    outdir.mkdir(parents=True, exist_ok=True)
    with open(config_path) as f:
        cfg = yaml.safe_load(f)
    for k, v in extra_config.items():
        if isinstance(v, dict) and isinstance(cfg.get(k), dict):
            merged = dict(cfg[k]); merged.update(v); cfg[k] = merged
        else:
            cfg[k] = v
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", delete=False,
        dir=outdir.parent, prefix="tmp_fdmcov_",
    ) as tmp:
        yaml.dump(cfg, tmp, default_flow_style=False, sort_keys=False)
        tmp_config = Path(tmp.name)
    macro_path = Path("data/macro_factors.parquet")
    cmd = [
        sys.executable, "scripts/run_dynamic_universe_backtest.py",
        "--config", str(tmp_config),
        "--data",   data_path,
        "--outdir", str(outdir),
    ]
    if macro_path.exists():
        cmd += ["--macro-data", str(macro_path)]
    try:
        subprocess.run(cmd, check=True)
    finally:
        tmp_config.unlink(missing_ok=True)


def load_metrics(outdir: Path) -> dict:
    p = outdir / "performance_summary.json"
    if not p.exists():
        return {}
    with open(p) as f:
        s = json.load(f)
    m = s.get("metrics", {}); pf = s.get("portfolio", {}); cm = s.get("cost_model", {})
    return {
        "sharpe":   m.get("sharpe",       float("nan")),
        "calmar":   m.get("calmar",        float("nan")),
        "cagr":     m.get("cagr",          float("nan")),
        "vol":      m.get("ann_vol",       float("nan")),
        "max_dd":   m.get("max_dd",        float("nan")),
        "turnover": pf.get("annual_turnover",       float("nan")),
        "avg_pos":  pf.get("avg_active_positions",  float("nan")),
        "tx_cost":  cm.get("transaction_cost_ann",  float("nan")),
        "funding":  cm.get("funding_drag_ann",      float("nan")),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config",  default=DEFAULT_CONFIG)
    parser.add_argument("--data",    default=DEFAULT_DATA)
    parser.add_argument("--outdir",  default=DEFAULT_OUTDIR)
    parser.add_argument("--alpha", nargs="+", type=float, default=DEFAULT_ALPHAS)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    out_dir = Path(args.outdir)
    out_dir.mkdir(parents=True, exist_ok=True)

    results = {}

    for alpha in sorted(args.alpha):
        label = label_for(alpha)
        sweep_dir = out_dir / f"backtest_{label}"
        summary_path = sweep_dir / "performance_summary.json"

        print(f"{'='*60}")
        print(f"alpha={alpha}")
        print(f"{'='*60}")

        if not args.force and summary_path.exists():
            print(f"  Results exist — skipping")
        else:
            run_backtest(
                config_path=args.config,
                data_path=args.data,
                outdir=sweep_dir,
                extra_config={
                    "use_coverage_aware_fdm": True,
                    "fdm_coverage_alpha":     alpha,
                },
            )

        results[alpha] = load_metrics(sweep_dir)
        if results[alpha]:
            r = results[alpha]
            print(
                f"  Sharpe={r['sharpe']:.4f}, Calmar={r['calmar']:.4f}, "
                f"CAGR={r['cagr']*100:.2f}%, MaxDD={r['max_dd']*100:.2f}%, "
                f"AvgPos={r['avg_pos']:.1f}, Turn={r['turnover']:.1f}x\n"
            )

    # Summary table
    print(f"\n{'='*90}")
    print("FDM COVERAGE SWEEP SUMMARY (FDM_eff = FDM_base × coverage^alpha)")
    print(f"{'='*90}")
    print(
        f"{'alpha':>5}  {'Sharpe':>7}  {'Calmar':>7}  "
        f"{'CAGR%':>7}  {'MaxDD%':>7}  {'Vol%':>6}  {'Turn':>6}  {'AvgPos':>7}"
    )
    print("-" * 90)

    for alpha in sorted(args.alpha):
        if alpha not in results or not results[alpha]:
            continue
        r = results[alpha]
        print(
            f"{alpha:>5.2f}  {r['sharpe']:>7.4f}  {r['calmar']:>7.4f}  "
            f"{r['cagr']*100:>7.2f}%  {r['max_dd']*100:>7.2f}%  "
            f"{r['vol']*100:>6.2f}  {r['turnover']:>6.1f}  {r['avg_pos']:>7.1f}"
        )
    print(f"{'='*90}")

    out_path = out_dir / "sweep_results.json"
    with open(out_path, "w") as f:
        json.dump({str(a): v for a, v in results.items()}, f, indent=2)
    print(f"\nResults saved to {out_path}")


if __name__ == "__main__":
    main()
