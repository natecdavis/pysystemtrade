#!/usr/bin/env python3
"""
Rebalancing cadence sensitivity sweep.

Evaluates the performance impact of less-frequent rebalancing by applying
cadence filters to pre-computed daily positions. Signal generation is
unchanged — the system still produces daily target positions, but positions
are only updated on rebalance dates; between updates the previous position
is held fixed.

Cadences:
    daily     — baseline (no filtering)
    weekly    — every Monday
    biweekly  — every other Monday
    monthly   — first business day of each month

Usage:
    python scripts/sweep_rebalance_cadence.py \\
        --positions out/resmom_weighted/positions.csv \\
        --data data/dataset_538registry_6yr_jagged.parquet \\
        --capital 10000 \\
        --cost-frac 0.001

Optional:
    --cadences daily weekly biweekly monthly   (subset to run)
    --outdir out/cadence_sweep                 (write results JSON here)
"""

import argparse
import json
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from sysdata.crypto.prices import load_crypto_perps_panel
from systems.provided.crypto_example.core.portfolio_metrics import calculate_all_metrics

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Cadence definitions
# ---------------------------------------------------------------------------

CADENCES = {
    'daily':    None,       # no filter — full daily baseline
    'weekly':   'W-MON',    # every Monday
    'biweekly': '2W-MON',   # every other Monday
    'monthly':  'BMS',      # first business day of month
}


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------

def apply_rebalance_cadence(
    daily_positions: pd.DataFrame,
    cadence_offset,
) -> pd.DataFrame:
    """
    Apply a rebalancing cadence to daily target positions.

    On rebalance dates, the daily target position is adopted.
    On non-rebalance dates, the previous position is held.

    Args:
        daily_positions: dates × instruments DataFrame of daily target positions
        cadence_offset:  pandas offset string (e.g. 'W-MON') or None for daily

    Returns:
        held_positions: same shape as daily_positions
    """
    if cadence_offset is None:
        return daily_positions.copy()

    idx = daily_positions.index
    start, end = idx.min(), idx.max()

    rebalance_dates = pd.date_range(start=start, end=end, freq=cadence_offset)
    rebalance_dates = rebalance_dates[rebalance_dates.isin(idx)]

    # NaN on non-rebalance days, then forward-fill (hold position)
    mask = daily_positions.index.isin(rebalance_dates)
    held = daily_positions.copy()
    held.loc[~mask] = np.nan
    held = held.ffill()
    # Back-fill leading NaN so we start invested from day 1
    held = held.bfill()

    return held


def compute_gross_returns(
    held_positions: pd.DataFrame,
    prices_df: pd.DataFrame,
    capital: float,
) -> pd.Series:
    """
    Daily gross P&L as a fraction of capital.

    P&L[t] = sum_i( held[t-1, i] × (price[t, i] − price[t-1, i]) ) / capital

    Args:
        held_positions: dates × instruments, position in base-asset units
        prices_df:      dates × instruments close prices
        capital:        notional capital in USD

    Returns:
        pd.Series of daily gross returns (decimals)
    """
    instruments = [c for c in held_positions.columns if c in prices_df.columns]
    pos = held_positions[instruments]
    price = prices_df[instruments].reindex(pos.index, method='ffill')

    daily_pnl = (pos.shift(1) * price.diff()).sum(axis=1)
    return daily_pnl / capital


def compute_cost_and_turnover(
    held_positions: pd.DataFrame,
    prices_df: pd.DataFrame,
    capital: float,
    cost_frac: float,
) -> tuple[float, pd.Series]:
    """
    Compute transaction costs and annualised turnover.

    Costs are only incurred when the held position changes (i.e. on rebalance
    dates). Cost[t] = |Δpos[t]| × price[t] × cost_frac / capital.

    Args:
        held_positions: dates × instruments held positions
        prices_df:      dates × instruments close prices
        capital:        notional capital in USD
        cost_frac:      round-trip cost as a fraction (e.g. 0.001 = 10 bps)

    Returns:
        (annual_turnover, daily_cost_series)
        annual_turnover: total notional traded / capital / n_years
        daily_cost_series: pd.Series of daily cost as fraction of capital
    """
    instruments = [c for c in held_positions.columns if c in prices_df.columns]
    pos = held_positions[instruments]
    price = prices_df[instruments].reindex(pos.index, method='ffill')

    pos_change = pos.diff().abs()
    notional_traded = (pos_change * price).sum(axis=1)

    daily_cost = notional_traded * cost_frac / capital

    n_years = len(held_positions) / 252.0
    annual_turnover = float(notional_traded.sum() / (capital * n_years))

    return annual_turnover, daily_cost


def _count_rebalances(held_positions: pd.DataFrame) -> int:
    """Count days on which any position changed."""
    return int(held_positions.diff().abs().sum(axis=1).gt(0).sum())


# ---------------------------------------------------------------------------
# Main sweep
# ---------------------------------------------------------------------------

def sweep(
    positions_csv: str,
    data_path: str,
    capital: float,
    cost_frac: float,
    cadence_names: list[str],
) -> list[dict]:
    """
    Run the cadence sweep and return a list of result dicts.

    Args:
        positions_csv:  path to positions.csv from a prior backtest run
        data_path:      path to parquet dataset (for prices)
        capital:        notional capital in USD
        cost_frac:      round-trip cost fraction
        cadence_names:  subset of CADENCES keys to evaluate

    Returns:
        list of result dicts, one per cadence
    """
    logger.info(f"Loading positions from {positions_csv}")
    daily_positions = pd.read_csv(positions_csv, index_col=0, parse_dates=True)
    logger.info(
        f"  {len(daily_positions)} days × {daily_positions.shape[1]} instruments"
    )

    logger.info(f"Loading prices from {data_path}")
    prices_df, _meta, _lifecycle = load_crypto_perps_panel(
        data_path,
        validate_schema=False,
        allow_jagged=True,
    )
    logger.info(
        f"  {len(prices_df)} days × {prices_df.shape[1]} instruments"
    )

    results = []

    for name in cadence_names:
        if name not in CADENCES:
            logger.warning(f"Unknown cadence '{name}', skipping")
            continue

        offset = CADENCES[name]
        logger.info("=" * 60)
        logger.info(f"CADENCE: {name}  (offset={offset!r})")
        logger.info("=" * 60)

        held = apply_rebalance_cadence(daily_positions, offset)

        n_rebalances = _count_rebalances(held)
        n_years = len(held) / 252.0
        logger.info(f"  Rebalance events: {n_rebalances}  ({n_rebalances/n_years:.1f}/yr)")

        gross_returns = compute_gross_returns(held, prices_df, capital)
        annual_turnover, daily_cost = compute_cost_and_turnover(
            held, prices_df, capital, cost_frac
        )
        net_returns = gross_returns - daily_cost

        gross_metrics = calculate_all_metrics(
            gross_returns.dropna(), name=f'{name} (gross)'
        )
        net_metrics = calculate_all_metrics(
            net_returns.dropna(), name=f'{name} (net)'
        )

        logger.info(
            f"  Gross Sharpe={gross_metrics.get('sharpe', float('nan')):.3f}  "
            f"Net Sharpe={net_metrics.get('sharpe', float('nan')):.3f}  "
            f"Turnover={annual_turnover:.2f}x/yr"
        )

        results.append({
            'cadence':         name,
            'annual_turnover': annual_turnover,
            'n_rebalances':    n_rebalances,
            'gross':           gross_metrics,
            'net':             net_metrics,
        })

    return results


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------

def _fmt_pct(v, decimals=1):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return 'N/A'
    return f"{v * 100:.{decimals}f}%"


def _fmt_f(v, decimals=2):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return 'N/A'
    return f"{v:.{decimals}f}"


def print_table(results: list[dict]) -> None:
    """Print a markdown comparison table."""
    col_headers = [
        'Cadence', 'Sharpe (gross)', 'Sharpe (net)', 'CAGR (net)',
        'MaxDD', 'Vol', 'Turnover/yr', 'N rebalances',
    ]
    col_widths = [max(12, len(h)) for h in col_headers]

    def _row(cells):
        padded = [str(c).ljust(w) for c, w in zip(cells, col_widths)]
        return '| ' + ' | '.join(padded) + ' |'

    def _sep():
        return '|-' + '-|-'.join(['-' * w for w in col_widths]) + '-|'

    print()
    print('## Rebalancing Cadence Sweep')
    print()
    print(_row(col_headers))
    print(_sep())

    for r in results:
        g = r.get('gross', {})
        n = r.get('net', {})
        cells = [
            r['cadence'],
            _fmt_f(g.get('sharpe')),
            _fmt_f(n.get('sharpe')),
            _fmt_pct(n.get('cagr')),
            _fmt_pct(n.get('max_dd')),
            _fmt_pct(n.get('ann_vol')),
            _fmt_f(r.get('annual_turnover'), decimals=2) + 'x',
            str(r.get('n_rebalances', 'N/A')),
        ]
        print(_row(cells))

    print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description='Rebalancing cadence sensitivity sweep (daily/weekly/biweekly/monthly)'
    )
    parser.add_argument(
        '--positions',
        required=True,
        help='Path to positions.csv from a prior backtest run'
    )
    parser.add_argument(
        '--data',
        required=True,
        help='Path to parquet dataset (for prices)'
    )
    parser.add_argument(
        '--capital',
        type=float,
        default=10_000.0,
        help='Notional capital in USD (default: 10000)'
    )
    parser.add_argument(
        '--cost-frac',
        type=float,
        default=0.001,
        dest='cost_frac',
        help='Round-trip cost as fraction (default: 0.001 = 10 bps)'
    )
    parser.add_argument(
        '--cadences',
        nargs='+',
        default=list(CADENCES.keys()),
        choices=list(CADENCES.keys()),
        metavar='CADENCE',
        help='Cadences to evaluate (default: all)'
    )
    parser.add_argument(
        '--outdir',
        default=None,
        help='If provided, write cadence_sweep_results.json here'
    )

    args = parser.parse_args()

    positions_path = Path(args.positions)
    if not positions_path.exists():
        logger.error(f"Positions file not found: {args.positions}")
        sys.exit(1)

    data_path = Path(args.data)
    if not data_path.exists():
        logger.error(f"Data file not found: {args.data}")
        sys.exit(1)

    results = sweep(
        positions_csv=str(positions_path),
        data_path=str(data_path),
        capital=args.capital,
        cost_frac=args.cost_frac,
        cadence_names=args.cadences,
    )

    print_table(results)

    if args.outdir:
        outdir = Path(args.outdir)
        outdir.mkdir(parents=True, exist_ok=True)
        results_path = outdir / 'cadence_sweep_results.json'
        with open(results_path, 'w') as f:
            json.dump(results, f, indent=2, default=str)
        logger.info(f"Results written to {results_path}")


if __name__ == '__main__':
    main()
