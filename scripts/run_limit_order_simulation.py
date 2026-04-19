"""
Limit order simulation: sweep over maker_frac × fill_delay_days scenarios.

Models the tradeoff between:
  - Fee savings: taker 4.5 bps → maker 1.5 bps (HL 0.0150%)
  - Spread capture: maker receives half-spread instead of paying it
  - Fill delay: limit orders take 0–2 days to fill (adverse selection / slow execution)

Scenarios:
  taker_baseline     maker_frac=0.00, delay=0  Current system (must match Sharpe=1.4253)
  maker_instant      maker_frac=1.00, delay=0  Upper bound: instant limit fill
  maker_1day         maker_frac=1.00, delay=1  Realistic: fills next close
  maker_2day         maker_frac=1.00, delay=2  Pessimistic: slow fills
  hybrid_50pct       maker_frac=0.50, delay=0  50% limit (instant), 50% market
  hybrid_50pct_1day  maker_frac=0.50, delay=1  50% limit (1-day delay), 50% market

Usage:
  python scripts/run_limit_order_simulation.py
"""
from __future__ import annotations

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
logger = logging.getLogger("limit_order_sim")

_TAKER_FEE_BPS = 4.5
_MAKER_FEE_BPS = 1.5

_SCENARIOS = [
    ("taker_baseline",    0.00, 0),
    ("maker_instant",     1.00, 0),
    ("maker_1day",        1.00, 1),
    ("maker_2day",        1.00, 2),
    ("hybrid_50pct",      0.50, 0),
    ("hybrid_50pct_1day", 0.50, 1),
]

_BASELINE = {"sharpe": 1.4253, "calmar": 2.4762, "maxdd": -0.0560}


def _make_scenario_config(base_config: dict, maker_frac: float, delay_days: int) -> dict:
    cfg = copy.deepcopy(base_config)
    cfg["maker_frac"] = maker_frac
    cfg["fill_delay_days"] = delay_days
    return cfg


def _extract_metrics(out_dir: Path) -> dict:
    summary_path = out_dir / "performance_summary.json"
    if not summary_path.exists():
        return {}
    try:
        data = json.loads(summary_path.read_text())
        m = data.get("metrics", {})
        return {
            "sharpe": m.get("sharpe"),
            "calmar": m.get("calmar"),
            "maxdd": m.get("max_dd"),
            "cagr": m.get("cagr"),
        }
    except Exception:
        return {}


def _breakeven_fill_rate(spread_bps: float = 7.0) -> float:
    """
    What fill rate is needed for a limit-only strategy to break even with taker?

    If fill_rate fraction fill as maker and (1 - fill_rate) revert to taker:
      effective_cost = fill_rate * maker_cost + (1 - fill_rate) * taker_cost
    Break-even when effective_cost = taker_cost:
      fill_rate * maker_cost + (1 - fill_rate) * taker_cost = taker_cost
      fill_rate * (maker_cost - taker_cost) = 0
    That's always satisfied — any mix of maker/taker vs pure taker is better unless
    maker_cost > taker_cost (never true for HL with spread capture).

    More useful: fill rate needed for maker_1day to beat taker_baseline, accounting
    for the 1-day signal lag cost. Signal degradation per day ≈ Sharpe / sqrt(252).
    At Sharpe=1.4253, vol=9.4%, daily alpha ≈ 1.4253 * 9.4% / sqrt(252) ≈ 0.084%.
    Per-trade cost saving (maker vs taker): (spread_bps + taker_fee - maker_fee) bps.
    """
    daily_alpha_frac = 1.4253 * 0.094 / (252 ** 0.5)  # ~0.084%/day
    per_trade_saving_frac = (spread_bps + _TAKER_FEE_BPS - _MAKER_FEE_BPS) / 10000
    # Break-even: saving * fill_rate > daily_alpha * delay_days (signal lag cost)
    # => fill_rate > (daily_alpha * delay_days) / per_trade_saving
    # For 1-day delay:
    be_1day = daily_alpha_frac / per_trade_saving_frac
    return be_1day


def run_simulation(
    config_path: str = "config/crypto_perps_full_rules.yaml",
    data_path: str = "data/dataset_538registry_6yr_jagged.parquet",
    out_dir: str = "out/limit_order_simulation",
):
    config_path = Path(config_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    with open(config_path) as f:
        base_config = yaml.safe_load(f)

    results = {}

    for name, maker_frac, delay_days in _SCENARIOS:
        logger.info(f"\n{'='*60}\nScenario: {name}  "
                    f"(maker_frac={maker_frac:.2f}, delay={delay_days}d)\n{'='*60}")
        scenario_cfg = _make_scenario_config(base_config, maker_frac, delay_days)
        scenario_out = out_dir / f"backtest_{name}"
        scenario_out.mkdir(exist_ok=True)

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", dir=out_dir, delete=False
        ) as tmpf:
            yaml.dump(scenario_cfg, tmpf, default_flow_style=False, sort_keys=False)
            tmp_config = tmpf.name

        try:
            success = run_backtest(
                config_path=tmp_config,
                data_path=data_path,
                output_dir=str(scenario_out),
            )
            metrics = _extract_metrics(scenario_out)
            results[name] = {"success": success, "maker_frac": maker_frac,
                             "delay_days": delay_days, **metrics}
        except Exception as exc:
            logger.error(f"{name}: failed — {exc}", exc_info=True)
            results[name] = {"success": False, "maker_frac": maker_frac,
                             "delay_days": delay_days}
        finally:
            Path(tmp_config).unlink(missing_ok=True)

    # Print results table
    sep = "=" * 90
    print(f"\n{sep}")
    print(f"LIMIT ORDER SIMULATION RESULTS")
    print(f"  Taker fee: {_TAKER_FEE_BPS} bps/side  |  Maker fee: {_MAKER_FEE_BPS} bps/side (HL 0.0150%)")
    print(f"  Baseline (taker, no delay): Sharpe={_BASELINE['sharpe']:.4f}, "
          f"Calmar={_BASELINE['calmar']:.4f}, MaxDD={_BASELINE['maxdd']:.2%}")
    print(f"{sep}")
    print(f"{'Scenario':<22} {'MkrFrc':>6} {'Delay':>5} {'Sharpe':>8} {'ΔSharpe':>9} "
          f"{'Calmar':>8} {'ΔCalmar':>9} {'MaxDD':>8} {'CAGR':>7}")
    print(f"{'-'*22} {'-'*6} {'-'*5} {'-'*8} {'-'*9} {'-'*8} {'-'*9} {'-'*8} {'-'*7}")

    for name, maker_frac, delay_days in _SCENARIOS:
        m = results.get(name, {})
        if not m.get("success", False) or m.get("sharpe") is None:
            print(f"{name:<22} {maker_frac:>6.2f} {delay_days:>5} {'FAILED':>8}")
            continue
        ds = m["sharpe"] - _BASELINE["sharpe"]
        dc = m["calmar"] - _BASELINE["calmar"]
        cagr = m.get("cagr", float("nan"))
        cagr_str = f"{cagr:.2%}" if cagr == cagr else "N/A"
        print(f"{name:<22} {maker_frac:>6.2f} {delay_days:>5} {m['sharpe']:>8.4f} "
              f"{ds:>+9.4f} {m['calmar']:>8.4f} {dc:>+9.4f} "
              f"{m.get('maxdd', float('nan')):>8.2%} {cagr_str:>7}")

    # Breakeven analysis
    avg_spread_bps = 7.0  # rough midpoint across ADV tiers
    be = _breakeven_fill_rate(avg_spread_bps)
    per_trade_saving_bps = avg_spread_bps + _TAKER_FEE_BPS - _MAKER_FEE_BPS
    print(f"\n--- Breakeven Analysis (avg spread ≈ {avg_spread_bps:.0f} bps) ---")
    print(f"  Per-trade saving (maker vs taker, one-way): {per_trade_saving_bps:.1f} bps")
    print(f"  Breakeven fill rate for maker_1day to beat taker_baseline: {be:.1%}")
    print(f"  (At fill rate ≥ {be:.1%}, the fee/spread saving covers the 1-day signal lag cost)")
    print()

    return results


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config/crypto_perps_full_rules.yaml")
    parser.add_argument("--data", default="data/dataset_538registry_6yr_jagged.parquet")
    parser.add_argument("--outdir", default="out/limit_order_simulation")
    args = parser.parse_args()
    run_simulation(config_path=args.config, data_path=args.data, out_dir=args.outdir)


if __name__ == "__main__":
    main()
