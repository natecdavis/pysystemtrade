"""
E3: Top-K Entry/Exit Buffer Tuning.

Tests entry_buffer ∈ {3, 5, 7} × exit_buffer ∈ {7, 10, 15} = 9 combinations.
Current: entry_buffer=5, exit_buffer=10.
With HL's 148-instrument pool (vs 300), buffers may need recalibration.
Decision: Adopt jointly if ΔSharpe ≥ +1%.
"""
import sys
import json
import tempfile
import shutil
from pathlib import Path
from itertools import product

sys.path.insert(0, str(Path(__file__).parent.parent))

ENTRY_BUFFERS = [3, 5, 7]
EXIT_BUFFERS = [7, 10, 15]
CONFIG_TEMPLATE = "config/crypto_perps_full_rules.yaml"
DATA = "data/dataset_538registry_6yr_jagged.parquet"


def run_with_buffers(entry_buf: int, exit_buf: int) -> dict:
    import yaml
    import logging
    logging.basicConfig(level=logging.WARNING)

    # Load config as dict, override buffer values
    with open(CONFIG_TEMPLATE) as f:
        cfg = yaml.safe_load(f)

    cfg.setdefault('dynamic_universe', {})
    cfg['dynamic_universe']['entry_buffer'] = entry_buf
    cfg['dynamic_universe']['exit_buffer'] = exit_buf

    # Write modified config to temp file
    tmp_cfg = tempfile.NamedTemporaryFile(suffix='.yaml', mode='w', delete=False)
    yaml.dump(cfg, tmp_cfg)
    tmp_cfg.flush()
    tmp_cfg_path = tmp_cfg.name
    tmp_cfg.close()

    outdir = tempfile.mkdtemp(prefix=f"buf_{entry_buf}_{exit_buf}_")
    try:
        from scripts.run_dynamic_universe_backtest import run_backtest
        ok = run_backtest(
            config_path=tmp_cfg_path,
            data_path=DATA,
            output_dir=outdir,
        )
        if ok:
            perf_path = Path(outdir) / "performance_summary.json"
            if perf_path.exists():
                perf = json.loads(perf_path.read_text())
                m = perf.get("metrics", perf)
                return {
                    "sharpe": m.get("sharpe"),
                    "calmar": m.get("calmar"),
                    "cagr": m.get("cagr"),
                    "maxdd": m.get("max_dd"),
                }
        return {}
    finally:
        shutil.rmtree(outdir, ignore_errors=True)
        Path(tmp_cfg_path).unlink(missing_ok=True)


def main():
    results = {}
    for entry_buf, exit_buf in product(ENTRY_BUFFERS, EXIT_BUFFERS):
        key = (entry_buf, exit_buf)
        marker = " ←current" if key == (5, 10) else ""
        print(f"\n--- entry={entry_buf}, exit={exit_buf}{marker} ---", flush=True)
        r = run_with_buffers(entry_buf, exit_buf)
        results[key] = r
        if r:
            print(f"  Sharpe={r['sharpe']:.4f}  Calmar={r['calmar']:.4f}  MaxDD={r['maxdd']:.1%}")
        else:
            print("  FAILED")

    # Summary table
    print("\n\n=== E3 BUFFER SWEEP RESULTS ===")
    baseline = results.get((5, 10), {})
    base_sharpe = baseline.get("sharpe", 0)
    print(f"{'entry':>7} {'exit':>6}  {'Sharpe':>8}  {'ΔSharpe':>8}  {'Calmar':>8}  {'MaxDD':>8}")
    print("-" * 60)
    for entry_buf in ENTRY_BUFFERS:
        for exit_buf in EXIT_BUFFERS:
            r = results.get((entry_buf, exit_buf), {})
            sharpe = r.get("sharpe", float("nan"))
            calmar = r.get("calmar", float("nan"))
            maxdd = r.get("maxdd", float("nan"))
            delta = sharpe - base_sharpe if base_sharpe else float("nan")
            marker = " ←current" if (entry_buf, exit_buf) == (5, 10) else ""
            print(f"{entry_buf:>7} {exit_buf:>6}  {sharpe:>8.4f}  {delta:>+8.4f}  {calmar:>8.4f}  {maxdd:>8.1%}{marker}")

    # Best combo
    valid = {k: v for k, v in results.items() if v.get("sharpe")}
    if valid:
        best_key = max(valid, key=lambda k: valid[k]["sharpe"])
        best = valid[best_key]
        delta_best = best["sharpe"] - base_sharpe
        print(f"\nBest: entry={best_key[0]}, exit={best_key[1]}  Sharpe={best['sharpe']:.4f} ({delta_best:+.4f} vs current)")
        if delta_best / base_sharpe >= 0.01:
            print("→ ADOPT: ΔSharpe ≥ +1% threshold met")
        else:
            print("→ REJECT: ΔSharpe < +1% threshold")


if __name__ == "__main__":
    main()
