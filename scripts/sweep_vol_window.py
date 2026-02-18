#!/usr/bin/env python3
"""
Vol lookback sensitivity sweep.

Runs the full backtest for each vol window in [10, 20, 35, 63, 126] days,
patching three config keys simultaneously so all vol lookbacks move together:

    dynamic_universe.vol_window   — universe SR-cost + vol-floor eligibility
    sizing.vol_days               — position sizing
    volatility_calculation.days   — RawData / forecast scaling

Prints a markdown comparison table when all runs are complete.

Usage:
    python scripts/sweep_vol_window.py \\
        --config config/crypto_perps_full_rules.yaml \\
        --data data/dataset_538registry_6yr_jagged.parquet \\
        --outdir out/vol_window_sweep

Optional:
    --windows 10 20 35        Override default sweep windows
    --static-universe         Disable dynamic universe (cost filtering)
"""

import argparse
import json
import logging
import sys
import tempfile
from pathlib import Path

import yaml

# Add project root to sys.path so we can import from scripts/
sys.path.insert(0, str(Path(__file__).parent.parent))

# Import run_backtest from the backtest runner
from scripts.run_dynamic_universe_backtest import run_backtest

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

DEFAULT_WINDOWS = [10, 20, 35, 63, 126]
BASELINE_WINDOW = 35


def _patch_config(config_dict: dict, vol_window: int) -> dict:
    """
    Return a copy of config_dict with all three vol lookback keys set to vol_window.
    """
    import copy
    patched = copy.deepcopy(config_dict)

    # 1. Universe eligibility vol window
    patched.setdefault('dynamic_universe', {})['vol_window'] = vol_window

    # 2. Position sizing vol window
    patched.setdefault('sizing', {})['vol_days'] = vol_window

    # 3. RawData / forecast scaling vol window
    # Preserve existing sub-keys (func, name_returns_attr_in_rawdata, etc.)
    patched.setdefault('volatility_calculation', {})['days'] = vol_window

    return patched


def _load_summary(run_dir: Path) -> dict | None:
    """Load performance_summary.json from a run output directory."""
    summary_path = run_dir / 'performance_summary.json'
    if not summary_path.exists():
        logger.warning(f"No performance_summary.json in {run_dir}")
        return None
    with open(summary_path) as f:
        return json.load(f)


def _fmt_pct(v, decimals=1):
    """Format a decimal return as a percentage string."""
    if v is None or (isinstance(v, float) and not (v == v)):  # nan check
        return 'N/A'
    return f"{v * 100:.{decimals}f}%"


def _fmt_float(v, decimals=2):
    if v is None or (isinstance(v, float) and not (v == v)):
        return 'N/A'
    return f"{v:.{decimals}f}"


def _print_table(results: list[dict]) -> None:
    """Print markdown comparison table."""
    header = (
        "| Vol Window | CAGR   | Vol    | Sharpe | Max DD  "
        "| Worst Mo | Crisis Ret | Avg Pos |"
    )
    sep = (
        "|------------|--------|--------|--------|---------|"
        "----------|------------|---------|"
    )
    print()
    print("## Vol Lookback Sensitivity Sweep")
    print()
    print(header)
    print(sep)

    for r in results:
        w = r['window']
        label = f"{w}d" + (" (curr)" if w == BASELINE_WINDOW else "")
        label = label.ljust(10)

        m = r.get('metrics', {})
        p = r.get('portfolio', {})

        cagr = _fmt_pct(m.get('cagr'))
        ann_vol = _fmt_pct(m.get('ann_vol'))
        sharpe = _fmt_float(m.get('sharpe'))
        max_dd = _fmt_pct(m.get('max_dd'))
        worst_mo = _fmt_pct(m.get('worst_month'))
        crisis = _fmt_pct(m.get('crisis_return'))
        avg_pos = _fmt_float(p.get('avg_active_positions'), decimals=1)

        print(
            f"| {label} | {cagr:>6} | {ann_vol:>6} | {sharpe:>6} | {max_dd:>7} "
            f"| {worst_mo:>8} | {crisis:>10} | {avg_pos:>7} |"
        )

    print()


def run_sweep(
    config_path: str,
    data_path: str,
    outdir: str,
    windows: list[int],
    use_dynamic_universe: bool = True,
) -> None:
    config_file = Path(config_path)
    if not config_file.exists():
        logger.error(f"Config not found: {config_path}")
        sys.exit(1)

    data_file = Path(data_path)
    if not data_file.exists():
        logger.error(f"Data not found: {data_path}")
        sys.exit(1)

    outdir_path = Path(outdir)
    configs_dir = outdir_path / 'configs'
    configs_dir.mkdir(parents=True, exist_ok=True)

    # Load base config once
    with open(config_file) as f:
        base_config = yaml.safe_load(f)

    results = []

    for N in windows:
        logger.info("=" * 70)
        logger.info(f"VOL WINDOW = {N}d  ({windows.index(N) + 1}/{len(windows)})")
        logger.info("=" * 70)

        # Write patched config to a temp YAML
        patched = _patch_config(base_config, N)
        patched_config_path = configs_dir / f'vol_window_{N}.yaml'
        with open(patched_config_path, 'w') as f:
            yaml.dump(patched, f, default_flow_style=False, sort_keys=False)
        logger.info(f"  Patched config → {patched_config_path}")

        run_dir = outdir_path / f'run_{N}'
        run_dir.mkdir(parents=True, exist_ok=True)

        try:
            run_backtest(
                config_path=str(patched_config_path),
                data_path=str(data_file),
                output_dir=str(run_dir),
                use_dynamic_universe=use_dynamic_universe,
            )
        except Exception as e:
            logger.error(f"  Run failed for vol_window={N}: {e}", exc_info=True)
            results.append({'window': N, 'error': str(e)})
            continue

        summary = _load_summary(run_dir)
        if summary is None:
            results.append({'window': N, 'error': 'no summary'})
        else:
            results.append({
                'window': N,
                'metrics': summary.get('metrics', {}),
                'portfolio': summary.get('portfolio', {}),
            })
            m = summary.get('metrics', {})
            logger.info(
                f"  → Sharpe={m.get('sharpe', 'N/A'):.3f}  "
                f"CAGR={m.get('cagr', 0)*100:.1f}%  "
                f"MaxDD={m.get('max_dd', 0)*100:.1f}%"
            )

    # Print summary table
    _print_table(results)

    # Also write results to JSON
    results_path = outdir_path / 'sweep_results.json'
    with open(results_path, 'w') as f:
        json.dump(results, f, indent=2, default=str)
    logger.info(f"Full results written to {results_path}")


def main():
    parser = argparse.ArgumentParser(
        description='Vol lookback sensitivity sweep across [10, 20, 35, 63, 126] days'
    )
    parser.add_argument(
        '--config',
        required=True,
        help='Base YAML config (e.g. config/crypto_perps_full_rules.yaml)'
    )
    parser.add_argument(
        '--data',
        required=True,
        help='Path to parquet dataset'
    )
    parser.add_argument(
        '--outdir',
        required=True,
        help='Output directory (will be created)'
    )
    parser.add_argument(
        '--windows',
        type=int,
        nargs='+',
        default=DEFAULT_WINDOWS,
        metavar='N',
        help=f'Vol window values to sweep (default: {DEFAULT_WINDOWS})'
    )
    parser.add_argument(
        '--static-universe',
        action='store_true',
        help='Disable dynamic universe cost filtering'
    )

    args = parser.parse_args()

    run_sweep(
        config_path=args.config,
        data_path=args.data,
        outdir=args.outdir,
        windows=args.windows,
        use_dynamic_universe=not args.static_universe,
    )


if __name__ == '__main__':
    main()
