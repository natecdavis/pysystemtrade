#!/usr/bin/env python3
"""
2-D sweep of (entry_buffer, exit_buffer) at fixed K=30.
Reference (eb=3, ex=15) already exists in out/wf_comparison_56rules/backtest_flat.

Skips pairs where ex <= eb (degenerate).

Usage:
    python scripts/sweep_buffers_2d.py [--force]
    python scripts/sweep_buffers_2d.py --eb 1 2 3 5 8 12 --ex 5 10 15 20 30
"""
import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

DEFAULT_CONFIG    = "config/crypto_perps_full_rules.yaml"
DEFAULT_DATA      = "data/dataset_538registry_6yr_jagged.parquet"
DEFAULT_OUTDIR    = "out/buffer_sweep"
DEFAULT_K         = 30
DEFAULT_EB_VALUES = [1, 2, 3, 5, 8, 12]
DEFAULT_EX_VALUES = [5, 10, 15, 20, 30]
REFERENCE_DIR     = "out/wf_comparison_56rules/backtest_flat"   # K=30, eb=3, ex=15


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
        dir=outdir.parent, prefix="tmp_bufsweep_",
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
        "sharpe":   m.get("sharpe",              float("nan")),
        "calmar":   m.get("calmar",               float("nan")),
        "cagr":     m.get("cagr",                 float("nan")),
        "vol":      m.get("ann_vol",              float("nan")),
        "max_dd":   m.get("max_dd",               float("nan")),
        "turnover": pf.get("annual_turnover",      float("nan")),
        "avg_pos":  pf.get("avg_active_positions", float("nan")),
    }


def label(eb: int, ex: int) -> str:
    return f"eb{eb:02d}_ex{ex:03d}"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config",  default=DEFAULT_CONFIG)
    parser.add_argument("--data",    default=DEFAULT_DATA)
    parser.add_argument("--outdir",  default=DEFAULT_OUTDIR)
    parser.add_argument("--k",       type=int, default=DEFAULT_K)
    parser.add_argument("--eb", nargs="+", type=int, default=DEFAULT_EB_VALUES)
    parser.add_argument("--ex", nargs="+", type=int, default=DEFAULT_EX_VALUES)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    out_dir = Path(args.outdir)
    out_dir.mkdir(parents=True, exist_ok=True)

    results = {}

    # ── Reference (K=30, eb=3, ex=15) ─────────────────────────────────────
    ref_dir = Path(REFERENCE_DIR)
    ref_key = (3, 15)
    if ref_dir.exists():
        results[ref_key] = load_metrics(ref_dir)
        r = results[ref_key]
        print(f"Reference K={args.k} eb=3 ex=15: "
              f"Sharpe={r['sharpe']:.4f}, Calmar={r['calmar']:.4f}, "
              f"Turnover={r['turnover']:.1f}x, AvgPos={r['avg_pos']:.1f}")
    print()

    # ── Grid sweep ─────────────────────────────────────────────────────────
    pairs = [(eb, ex) for eb in sorted(args.eb)
                      for ex in sorted(args.ex)
                      if ex > eb and (eb, ex) != ref_key]

    total = len(pairs)
    print(f"Grid: {len(args.eb)} eb × {len(args.ex)} ex → {total} new pairs "
          f"(+1 reference, skipping ex<=eb and reference)\n")

    for i, (eb, ex) in enumerate(pairs, 1):
        lbl       = label(eb, ex)
        sweep_dir = out_dir / f"backtest_{lbl}"
        summary_p = sweep_dir / "performance_summary.json"

        print(f"[{i}/{total}] {'='*55}")
        print(f"K={args.k}, eb={eb}, ex={ex}")
        print(f"{'='*60}")

        if not args.force and summary_p.exists():
            print(f"  Results exist — skipping")
        else:
            run_backtest(
                config_path=args.config,
                data_path=args.data,
                outdir=sweep_dir,
                extra_config={
                    "dynamic_universe": {
                        "top_k":        args.k,
                        "entry_buffer": eb,
                        "exit_buffer":  ex,
                    },
                },
            )

        results[(eb, ex)] = load_metrics(sweep_dir)
        if results[(eb, ex)]:
            r = results[(eb, ex)]
            print(f"  Sharpe={r['sharpe']:.4f}, Calmar={r['calmar']:.4f}, "
                  f"CAGR={r['cagr']*100:.2f}%, MaxDD={r['max_dd']*100:.2f}%, "
                  f"AvgPos={r['avg_pos']:.1f}, Turnover={r['turnover']:.1f}x\n")

    # ── Summary table ──────────────────────────────────────────────────────
    all_keys = sorted([ref_key] + pairs)
    print(f"\n{'='*95}")
    print(f"BUFFER SWEEP SUMMARY (K={args.k})")
    print(f"{'='*95}")
    print(f"{'eb':>4}  {'ex':>4}  {'Sharpe':>7}  {'Calmar':>7}  "
          f"{'CAGR%':>7}  {'MaxDD%':>7}  {'Turn':>6}  {'AvgPos':>7}  {'Note':}")
    print("-" * 95)

    for (eb, ex) in all_keys:
        if (eb, ex) not in results or not results[(eb, ex)]:
            continue
        r = results[(eb, ex)]
        note = " *ref*" if (eb, ex) == ref_key else ""
        print(f"{eb:>4}  {ex:>4}  {r['sharpe']:>7.4f}  {r['calmar']:>7.4f}  "
              f"{r['cagr']*100:>7.2f}%  {r['max_dd']*100:>7.2f}%  "
              f"{r['turnover']:>6.1f}  {r['avg_pos']:>7.1f}{note}")
    print(f"{'='*95}")

    # Save
    out_path = out_dir / "sweep_results.json"
    with open(out_path, "w") as f:
        json.dump({f"{k[0]},{k[1]}": v for k, v in results.items()}, f, indent=2)
    print(f"\nResults saved to {out_path}")


if __name__ == "__main__":
    main()
