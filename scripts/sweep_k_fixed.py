#!/usr/bin/env python3
"""
Sweep fixed K values with proportional hysteresis buffers on flat-68 SB-corrected.

Live config (crypto_perps_1k.yaml) with HL filter, capital=$9745.58.
Proportional rule preserves current K=30/eb=2/ex=10: eb = round(K/15), ex = round(K/3)

Usage:
    python scripts/sweep_k_fixed.py [--force] [--k 30 60 100 150 200 229]
"""
import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

DEFAULT_CONFIG   = "config/crypto_perps_1k.yaml"
DEFAULT_DATA     = "data/dataset_sb_corrected_6yr_jagged.parquet"
DEFAULT_OUTDIR   = "out/k_sweep_flat68_sb_1k"
DEFAULT_K_VALUES = [30, 60, 100, 150, 200, 229]


def proportional_buffers(K: int) -> tuple[int, int]:
    eb = max(1, round(K / 15))
    ex = max(3, round(K / 3))
    return eb, ex


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
        dir=outdir.parent, prefix="tmp_ksweep_",
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
    m = s.get("metrics", {}); pf = s.get("portfolio", {})
    return {
        "sharpe":   m.get("sharpe",       float("nan")),
        "calmar":   m.get("calmar",        float("nan")),
        "cagr":     m.get("cagr",          float("nan")),
        "vol":      m.get("ann_vol",       float("nan")),
        "max_dd":   m.get("max_dd",        float("nan")),
        "turnover": pf.get("annual_turnover",       float("nan")),
        "avg_pos":  pf.get("avg_active_positions",  float("nan")),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config",  default=DEFAULT_CONFIG)
    parser.add_argument("--data",    default=DEFAULT_DATA)
    parser.add_argument("--outdir",  default=DEFAULT_OUTDIR)
    parser.add_argument("--k", nargs="+", type=int, default=DEFAULT_K_VALUES)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    out_dir = Path(args.outdir)
    out_dir.mkdir(parents=True, exist_ok=True)

    results = {}

    # ── Sweep ──────────────────────────────────────────────────────────────
    for K in sorted(args.k):
        eb, ex = proportional_buffers(K)
        label = f"k{K:03d}"
        sweep_dir = out_dir / f"backtest_{label}"
        summary_path = sweep_dir / "performance_summary.json"

        print(f"{'='*60}")
        print(f"K={K}, eb={eb}, ex={ex}")
        print(f"{'='*60}")

        if not args.force and summary_path.exists():
            print(f"  Results exist — skipping")
        else:
            run_backtest(
                config_path=args.config,
                data_path=args.data,
                outdir=sweep_dir,
                extra_config={
                    # top_k / entry_buffer / exit_buffer are nested under
                    # dynamic_universe in the config YAML.
                    "dynamic_universe": {
                        "top_k":        K,
                        "entry_buffer": eb,
                        "exit_buffer":  ex,
                    },
                },
            )

        results[K] = load_metrics(sweep_dir)
        if results[K]:
            r = results[K]
            print(f"  Sharpe={r['sharpe']:.4f}, Calmar={r['calmar']:.4f}, "
                  f"CAGR={r['cagr']*100:.2f}%, MaxDD={r['max_dd']*100:.2f}%, "
                  f"AvgPos={r['avg_pos']:.1f}, Turnover={r['turnover']:.1f}x\n")

    # ── Summary table ──────────────────────────────────────────────────────
    print(f"\n{'='*85}")
    print("K SWEEP SUMMARY (proportional buffers: eb=K/15, ex=K/3)")
    print(f"{'='*85}")
    print(f"{'K':>5}  {'eb':>4}  {'ex':>4}  {'Sharpe':>7}  {'Calmar':>7}  "
          f"{'CAGR%':>7}  {'MaxDD%':>7}  {'Turn':>6}  {'AvgPos':>7}")
    print("-" * 85)

    for K in sorted(args.k):
        if K not in results or not results[K]:
            continue
        r = results[K]
        eb, ex = proportional_buffers(K)
        print(f"{K:>5}  {eb:>4}  {ex:>4}  {r['sharpe']:>7.4f}  {r['calmar']:>7.4f}  "
              f"{r['cagr']*100:>7.2f}%  {r['max_dd']*100:>7.2f}%  "
              f"{r['turnover']:>6.1f}  {r['avg_pos']:>7.1f}")
    print(f"{'='*85}")

    # Save
    out_path = out_dir / "sweep_results.json"
    with open(out_path, "w") as f:
        json.dump({str(k): v for k, v in results.items()}, f, indent=2)
    print(f"\nResults saved to {out_path}")


if __name__ == "__main__":
    main()
