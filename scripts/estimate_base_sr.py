"""
estimate_base_sr.py — Empirical base_sr estimation from backtest diagnostics.

Post-processes existing backtest outputs to estimate SR per unit absolute forecast
(base_sr) without re-running the system.

Algorithm (per instrument i):
    daily_pnl_i   = pos_i[t-1] × (price_i[t] - price_i[t-1]) / capital
    SR_i          = mean(daily_pnl_i) / std(daily_pnl_i) × sqrt(365)
    mean_abs_f_i  = abs(combined_forecast_i).mean()
    base_sr_i     = SR_i × 10 / mean_abs_f_i    (if mean_abs_f_i > 0.5 else skip)

base_sr_estimate = median(base_sr_i over instruments with ≥180 days valid data)

Usage:
    python scripts/estimate_base_sr.py \\
      --diagnostics out/net_sr_full/diagnostics.parquet \\
      --data data/dataset_538registry_6yr_jagged.parquet \\
      --capital 10000
"""

import argparse
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def estimate_base_sr(
    diagnostics_path: str,
    data_path: str,
    capital: float = 10_000.0,
    min_days: int = 180,
    min_mean_abs_forecast: float = 0.5,
) -> float:
    """
    Estimate base_sr from diagnostics + price panel.

    Args:
        diagnostics_path: Path to diagnostics.parquet written by the backtest runner.
        data_path:        Path to the price panel parquet used in the backtest.
        capital:          Notional capital used in the backtest (for PnL scaling).
        min_days:         Minimum number of valid PnL days to include an instrument.
        min_mean_abs_forecast: Minimum mean |forecast| to avoid division instability.

    Returns:
        Recommended base_sr (median across qualifying instruments).
    """
    logger.info(f"Loading diagnostics: {diagnostics_path}")
    diag = pd.read_parquet(diagnostics_path)

    logger.info(f"Loading price panel: {data_path}")
    prices_panel = pd.read_parquet(data_path)

    # Diagnostics may be long-form (instrument as a column) or wide multi-index.
    # Detect format and extract position + combined_forecast per instrument.
    logger.info(f"Diagnostics shape: {diag.shape}")
    logger.info(f"Diagnostics columns (sample): {list(diag.columns)[:10]}")

    # Handle multi-index columns: (instrument, field) → wide form
    if isinstance(diag.columns, pd.MultiIndex):
        instruments = diag.columns.get_level_values(0).unique().tolist()
        positions_wide = diag.xs('position', axis=1, level=1, drop_level=True)
        forecasts_wide = diag.xs('combined_forecast', axis=1, level=1, drop_level=True)
    elif 'instrument' in diag.columns:
        # Long form: one row per (date, instrument)
        # Ensure 'date' is the index before pivoting
        if 'date' in diag.columns:
            diag = diag.set_index('date')
        elif diag.index.name != 'date':
            diag = diag.reset_index().set_index('date')
        instruments = diag['instrument'].unique().tolist()
        positions_wide = diag.pivot(columns='instrument', values='position')
        forecasts_wide = diag.pivot(columns='instrument', values='combined_forecast')
    else:
        # Assume wide form with instrument as column prefix: e.g. BTC_position
        # Try to split on '_' — fragile but common convention
        pos_cols = [c for c in diag.columns if c.endswith('_position')]
        fct_cols = [c for c in diag.columns if c.endswith('_combined_forecast')]
        instruments = [c.replace('_position', '') for c in pos_cols]
        positions_wide = diag[pos_cols].rename(
            columns={c: c.replace('_position', '') for c in pos_cols}
        )
        forecasts_wide = diag[fct_cols].rename(
            columns={c: c.replace('_combined_forecast', '') for c in fct_cols}
        )

    logger.info(f"Found {len(instruments)} instruments in diagnostics")

    # Align prices_panel index to positions_wide (daily)
    if isinstance(prices_panel.columns, pd.MultiIndex):
        # Panel parquet with (instrument, field) columns — take 'close' or first field
        try:
            prices_wide = prices_panel.xs('close', axis=1, level=1)
        except KeyError:
            prices_wide = prices_panel.xs(
                prices_panel.columns.get_level_values(1)[0], axis=1, level=1
            )
    elif 'instrument' in prices_panel.columns and 'close' in prices_panel.columns:
        # Long-form: pivot to wide using 'close' column
        price_date_col = 'date' if 'date' in prices_panel.columns else prices_panel.index.name
        if price_date_col == 'date' and 'date' in prices_panel.columns:
            prices_wide = prices_panel.pivot(index='date', columns='instrument', values='close')
        else:
            prices_wide = prices_panel.pivot(columns='instrument', values='close')
    else:
        prices_wide = prices_panel

    # Reindex prices to match diagnostics index
    prices_wide = prices_wide.reindex(positions_wide.index)

    results = []

    for inst in instruments:
        if inst not in positions_wide.columns:
            continue
        if inst not in prices_wide.columns:
            logger.debug(f"{inst}: not in price panel — skipping")
            continue

        pos = positions_wide[inst].dropna()
        fct = forecasts_wide[inst] if inst in forecasts_wide.columns else pd.Series(dtype=float)
        px = prices_wide[inst]

        # Daily PnL: position[t-1] × price_change[t] / capital
        px_chg = px.diff()
        pos_lag = pos.shift(1)
        common_idx = pos_lag.dropna().index.intersection(px_chg.dropna().index)

        if len(common_idx) < min_days:
            logger.debug(f"{inst}: only {len(common_idx)} valid PnL days — skipping")
            continue

        daily_pnl = (pos_lag.loc[common_idx] * px_chg.loc[common_idx]) / capital
        sr_i = daily_pnl.mean() / daily_pnl.std() * np.sqrt(365) if daily_pnl.std() > 0 else np.nan

        # Mean absolute forecast
        fct_common = fct.reindex(common_idx).dropna()
        mean_abs_f = fct_common.abs().mean() if len(fct_common) > 0 else np.nan

        if pd.isna(mean_abs_f) or mean_abs_f < min_mean_abs_forecast:
            logger.debug(
                f"{inst}: mean_abs_forecast={mean_abs_f:.3f} below threshold — skipping"
            )
            continue

        if pd.isna(sr_i):
            continue

        base_sr_i = sr_i * 10.0 / mean_abs_f
        results.append(
            {
                'instrument': inst,
                'n_days': len(common_idx),
                'SR_i': sr_i,
                'mean_abs_forecast': mean_abs_f,
                'base_sr_i': base_sr_i,
            }
        )

    if not results:
        raise ValueError("No instruments qualified — check diagnostics format and column names")

    df = pd.DataFrame(results).sort_values('base_sr_i')

    # Print per-instrument table
    print("\n" + "=" * 72)
    print("Per-instrument base_sr estimates")
    print("=" * 72)
    pd.set_option('display.float_format', '{:.4f}'.format)
    pd.set_option('display.max_rows', 200)
    pd.set_option('display.width', 100)
    print(df.to_string(index=False))

    # Cross-instrument distribution
    vals = df['base_sr_i'].dropna()
    p25  = vals.quantile(0.25)
    med  = vals.median()
    p75  = vals.quantile(0.75)
    mean = vals.mean()

    print("\n" + "=" * 72)
    print("Cross-instrument base_sr distribution")
    print("=" * 72)
    print(f"  n instruments : {len(vals)}")
    print(f"  p25           : {p25:.4f}")
    print(f"  median        : {med:.4f}")
    print(f"  mean          : {mean:.4f}")
    print(f"  p75           : {p75:.4f}")
    print()
    recommended = round(med, 2)
    print(f"  Recommended config value (median, rounded to 2dp): base_sr: {recommended}")
    print("=" * 72)

    return recommended


def main():
    parser = argparse.ArgumentParser(
        description="Estimate empirical base_sr from backtest diagnostics"
    )
    parser.add_argument(
        '--diagnostics',
        required=True,
        help='Path to diagnostics.parquet from a completed backtest run',
    )
    parser.add_argument(
        '--data',
        required=True,
        help='Path to the price panel parquet used in the backtest',
    )
    parser.add_argument(
        '--capital',
        type=float,
        default=10_000.0,
        help='Notional capital used in the backtest (default: 10000)',
    )
    parser.add_argument(
        '--min-days',
        type=int,
        default=180,
        help='Minimum valid PnL days for an instrument to qualify (default: 180)',
    )
    args = parser.parse_args()

    if not Path(args.diagnostics).exists():
        logger.error(f"Diagnostics file not found: {args.diagnostics}")
        sys.exit(1)
    if not Path(args.data).exists():
        logger.error(f"Price panel not found: {args.data}")
        sys.exit(1)

    estimate_base_sr(
        diagnostics_path=args.diagnostics,
        data_path=args.data,
        capital=args.capital,
        min_days=args.min_days,
    )


if __name__ == '__main__':
    main()
