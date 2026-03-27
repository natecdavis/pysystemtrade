"""
E2: Spread Model Sensitivity Analysis.

Tests spread multipliers 0.7x, 0.8x, 1.0x, 1.2x to understand Sharpe sensitivity
to spread assumptions. Informational only — no adoption decision.

Current tiers: top-20 → 2 bps, rank 21-70 → 5 bps, rest → 12 bps.
Purpose: if 0.7x shows +3%+ Sharpe, suggests HL spreads are tighter than modeled.
"""
import sys
import json
import tempfile
import shutil
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

MULTIPLIERS = [0.7, 0.8, 1.0, 1.2]
CONFIG = "config/crypto_perps_full_rules.yaml"
DATA = "data/dataset_538registry_6yr_jagged.parquet"


def main():
    import logging
    logging.basicConfig(level=logging.WARNING)

    sys.path.insert(0, '.')
    from scripts.run_dynamic_universe_backtest import run_backtest

    results = {}
    for mult in MULTIPLIERS:
        print(f"\n--- spread_multiplier={mult:.1f}x ---", flush=True)
        outdir = tempfile.mkdtemp(prefix=f"spread_{mult}_")
        try:
            ok = run_backtest(
                config_path=CONFIG,
                data_path=DATA,
                output_dir=outdir,
                spread_multiplier=mult,
            )
            if ok:
                perf_path = Path(outdir) / "performance_summary.json"
                if perf_path.exists():
                    perf = json.loads(perf_path.read_text())
                    m = perf.get("metrics", perf)
                    results[mult] = {
                        "sharpe": m.get("sharpe"),
                        "calmar": m.get("calmar"),
                        "cagr": m.get("cagr"),
                        "maxdd": m.get("max_dd"),
                    }
                    print(f"  Sharpe={results[mult]['sharpe']:.4f}  Calmar={results[mult]['calmar']:.4f}  MaxDD={results[mult]['maxdd']:.1%}")
            else:
                print(f"  FAILED")
        finally:
            shutil.rmtree(outdir, ignore_errors=True)

    # Summary table
    print("\n\n=== E2 SPREAD SENSITIVITY RESULTS ===")
    baseline = results.get(1.0, {})
    base_sharpe = baseline.get("sharpe", 0)
    print(f"{'Mult':>6}  {'Sharpe':>8}  {'ΔSharpe':>8}  {'Calmar':>8}  {'MaxDD':>8}")
    print("-" * 50)
    for mult in MULTIPLIERS:
        r = results.get(mult, {})
        sharpe = r.get("sharpe", float("nan"))
        calmar = r.get("calmar", float("nan"))
        maxdd = r.get("maxdd", float("nan"))
        delta = sharpe - base_sharpe if base_sharpe else float("nan")
        marker = " ←baseline" if mult == 1.0 else ""
        print(f"{mult:>6.1f}x  {sharpe:>8.4f}  {delta:>+8.4f}  {calmar:>8.4f}  {maxdd:>8.1%}{marker}")

    # Implication check
    if 0.7 in results and 1.0 in results:
        delta_07 = results[0.7]["sharpe"] - results[1.0]["sharpe"]
        if delta_07 / results[1.0]["sharpe"] >= 0.03:
            print("\n→ 0.7x shows +3%+ Sharpe: HL spread model may be over-estimated. Consider updating tiers with real HL data.")
        else:
            print("\n→ Spread sensitivity < 3%: current spread model is adequate.")


if __name__ == "__main__":
    main()
