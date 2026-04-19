"""
Ablation backtest for SPX and 10Y momentum rules against the flat-64 baseline.

Tests each rule individually as the 65th rule (all 65 at equal weight 1/65).
Baseline: flat-64 at Sharpe=1.4325, Calmar=2.2198, MaxDD=-6.46%

Rules tested:
  spx_momentum_16:  SPX EWMAC(16,64) — rising SPX → long crypto (risk-on)
  spx_momentum_32:  SPX EWMAC(32,128) — slower equity sentiment signal
  us10y_momentum_16: 10Y yield EWMAC(16,64) inverted — rising yields → short crypto

Usage:
  python scripts/run_macro_ext_ablation.py
"""
from __future__ import annotations

import argparse
import copy
import json
import logging
import sys
import tempfile
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.run_dynamic_universe_backtest import run_backtest

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("macro_ext_ablation")

_RULES = ["spx_momentum_16", "spx_momentum_32", "us10y_momentum_16"]
_BASELINE = {"sharpe": 1.4325, "calmar": 2.2198, "maxdd": -0.0646}


def _make_ablation_config(base_config: dict, rule_name: str) -> dict:
    cfg = copy.deepcopy(base_config)
    fw = cfg.get("forecast_weights", {})
    n = len(fw) + 1
    new_w = round(1.0 / n, 8)
    fw_new = {k: new_w for k in fw}
    fw_new[rule_name] = new_w
    cfg["forecast_weights"] = fw_new
    return cfg


def _extract_metrics(out_dir: Path) -> dict:
    summary_path = out_dir / "performance_summary.json"
    if not summary_path.exists():
        return {}
    try:
        data = json.loads(summary_path.read_text())
        m = data.get("metrics", {})
        return {"sharpe": m.get("sharpe"), "calmar": m.get("calmar"), "maxdd": m.get("max_dd")}
    except Exception:
        return {}


def run_ablation(
    config_path: str = "config/crypto_perps_full_rules.yaml",
    data_path: str = "data/dataset_538registry_6yr_jagged.parquet",
    out_dir: str = "out/macro_ext_ablation",
):
    config_path = Path(config_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    with open(config_path) as f:
        base_config = yaml.safe_load(f)

    results = {}

    for rule in _RULES:
        logger.info(f"\n{'='*60}\nRunning ablation: {rule}\n{'='*60}")
        ablation_cfg = _make_ablation_config(base_config, rule)
        rule_out = out_dir / f"backtest_{rule}"
        rule_out.mkdir(exist_ok=True)

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", dir=out_dir, delete=False
        ) as tmpf:
            yaml.dump(ablation_cfg, tmpf, default_flow_style=False, sort_keys=False)
            tmp_config = tmpf.name

        try:
            success = run_backtest(config_path=tmp_config, data_path=data_path, output_dir=str(rule_out))
            metrics = _extract_metrics(rule_out)
            results[rule] = {"success": success, **metrics}
        except Exception as exc:
            logger.error(f"{rule}: failed — {exc}", exc_info=True)
            results[rule] = {"success": False}
        finally:
            Path(tmp_config).unlink(missing_ok=True)

    print(f"\n{'='*70}")
    print(f"MACRO EXT ABLATION RESULTS  (flat-64 baseline: "
          f"Sharpe={_BASELINE['sharpe']:.4f}, Calmar={_BASELINE['calmar']:.4f}, "
          f"MaxDD={_BASELINE['maxdd']:.2%})")
    print(f"{'='*70}")
    print(f"{'Rule':<22} {'Sharpe':>8} {'ΔSharpe':>9} {'Calmar':>8} {'ΔCalmar':>9} {'MaxDD':>8}")
    print(f"{'-'*22} {'-'*8} {'-'*9} {'-'*8} {'-'*9} {'-'*8}")

    for rule in _RULES:
        m = results.get(rule, {})
        if not m.get("success", False) or m.get("sharpe") is None:
            print(f"{rule:<22} {'FAILED':>8}")
            continue
        ds = m["sharpe"] - _BASELINE["sharpe"]
        dc = m["calmar"] - _BASELINE["calmar"]
        verdict = "ADOPT" if ds > 0 and dc > 0 else "REJECT"
        print(f"{rule:<22} {m['sharpe']:>8.4f} {ds:>+9.4f} {m['calmar']:>8.4f} {dc:>+9.4f} "
              f"{m.get('maxdd', float('nan')):>8.2%}  [{verdict}]")
    print()
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config/crypto_perps_full_rules.yaml")
    parser.add_argument("--data", default="data/dataset_538registry_6yr_jagged.parquet")
    parser.add_argument("--outdir", default="out/macro_ext_ablation")
    args = parser.parse_args()
    run_ablation(config_path=args.config, data_path=args.data, out_dir=args.outdir)


if __name__ == "__main__":
    main()
