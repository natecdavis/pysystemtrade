#!/usr/bin/env python3
"""
Long/Short Asymmetry Analysis for crypto perpetuals backtest.

Quantifies whether long or short signals are more predictive, and whether a
constant forecast offset (tilt) would improve risk-adjusted returns.

Uses existing backtest outputs — no system rebuild required.

Usage:
    python scripts/analyze_long_short_asymmetry.py \\
        --positions out/sector_test/reverted_baseline/positions.csv \\
        --diagnostics out/sector_test/reverted_baseline/diagnostics.parquet \\
        --data data/dataset_538registry_6yr_jagged.parquet \\
        --outdir out/asymmetry_analysis \\
        --capital 10000

Decision rule:
    If long/short Sharpe ratio outside [0.8, 1.2] → proceed with tilt.
    If within [0.8, 1.2] → symmetric, no tilt needed.
"""

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from sysdata.crypto.prices import load_crypto_perps_panel


# ---------------------------------------------------------------------------
# Stats helpers
# ---------------------------------------------------------------------------

def _sharpe(daily_pct: pd.Series) -> float:
    """Annualised Sharpe from daily % returns."""
    if daily_pct.std() == 0 or daily_pct.dropna().empty:
        return float('nan')
    return float(daily_pct.mean() / daily_pct.std() * np.sqrt(365))


def _ann_return(daily_pct: pd.Series) -> float:
    """Annualised mean return (%)."""
    return float(daily_pct.mean() * 365 * 100)


def _ann_vol(daily_pct: pd.Series) -> float:
    """Annualised volatility (%)."""
    return float(daily_pct.std() * np.sqrt(365) * 100)


def _hit_rate(daily_pct: pd.Series) -> float:
    """Fraction of days with positive return."""
    valid = daily_pct.dropna()
    if valid.empty:
        return float('nan')
    return float((valid > 0).mean())


def _compute_pnl_legs(
    positions: pd.DataFrame,
    prices_df: pd.DataFrame,
    capital: float,
) -> dict:
    """
    Compute long-leg and short-leg daily P&L series (as % of capital).

    Returns dict with:
        long_pnl_daily, short_pnl_daily, total_pnl_daily  (pd.Series)
    """
    instruments = [c for c in positions.columns if c in prices_df.columns]
    pos = positions[instruments].fillna(0.0)
    pr = prices_df[instruments].reindex(pos.index, method='ffill')

    price_changes = pr.diff()                          # ΔP in quote (USD)
    gross_pnl = pos.shift(1) * price_changes          # USD P&L per instrument-day

    long_mask = pos.shift(1) > 0
    short_mask = pos.shift(1) < 0

    long_pnl_daily  = gross_pnl.where(long_mask,  0.0).sum(axis=1) / capital
    short_pnl_daily = gross_pnl.where(short_mask, 0.0).sum(axis=1) / capital
    total_pnl_daily = (long_pnl_daily + short_pnl_daily)

    return {
        'long_pnl_daily':  long_pnl_daily,
        'short_pnl_daily': short_pnl_daily,
        'total_pnl_daily': total_pnl_daily,
        'instruments':     instruments,
        'long_mask':       long_mask,
        'short_mask':      short_mask,
    }


def _leg_stats(daily_pct: pd.Series, total_return_pct: float) -> dict:
    """Compute summary stats for one leg."""
    ann_ret = _ann_return(daily_pct)
    return {
        'ann_return_pct': ann_ret,
        'ann_vol_pct':    _ann_vol(daily_pct),
        'sharpe':         _sharpe(daily_pct),
        'hit_rate':       _hit_rate(daily_pct),
        'pct_of_total_return': (ann_ret / total_return_pct * 100) if total_return_pct else float('nan'),
    }


# ---------------------------------------------------------------------------
# Analysis 1: P&L decomposition
# ---------------------------------------------------------------------------

def analysis1_pnl_decomposition(
    positions: pd.DataFrame,
    prices_df: pd.DataFrame,
    capital: float,
) -> dict:
    """Decompose P&L into long/short legs and compute per-leg stats."""
    legs = _compute_pnl_legs(positions, prices_df, capital)
    total_ann = _ann_return(legs['total_pnl_daily'])

    return {
        'long':  _leg_stats(legs['long_pnl_daily'],  total_ann),
        'short': _leg_stats(legs['short_pnl_daily'], total_ann),
        'total': _leg_stats(legs['total_pnl_daily'], total_ann),
    }


# ---------------------------------------------------------------------------
# Analysis 2: Forecast IC by direction
# ---------------------------------------------------------------------------

def analysis2_forecast_ic(
    diagnostics: pd.DataFrame,
    prices_df: pd.DataFrame,
    horizons: list = None,
) -> dict:
    """
    Compute IC of combined_forecast vs forward returns, split by signal direction.

    For each horizon (1d, 5d, 21d):
      - IC overall
      - IC when forecast > 0 (long signals)
      - IC when forecast < 0 (short signals, sign-flipped so positive IC = predictive)
    """
    if horizons is None:
        horizons = [1, 5, 21]

    # Pivot diagnostics to wide format
    fc_panel = diagnostics.pivot(
        index='date', columns='instrument', values='combined_forecast'
    )

    # Align instruments
    common_instr = [c for c in fc_panel.columns if c in prices_df.columns]
    fc = fc_panel[common_instr]
    pr = prices_df[common_instr].reindex(fc.index, method='ffill')

    results = {}
    for h in horizons:
        # Forward price return (h-day % change, shifted back so aligned with today's forecast)
        fwd_ret = pr.pct_change(h, fill_method=None).shift(-h)

        # Compute IC for each instrument, then average (Pearson correlation)
        ic_all_list   = []
        ic_long_list  = []
        ic_short_list = []

        for instr in common_instr:
            fc_s  = fc[instr].dropna()
            ret_s = fwd_ret[instr].dropna()
            idx   = fc_s.index.intersection(ret_s.index)

            if len(idx) < 30:
                continue

            fc_aligned  = fc_s[idx]
            ret_aligned = ret_s[idx]

            # Overall IC
            ic_all_list.append(fc_aligned.corr(ret_aligned))

            # Long signal IC: forecast > 0
            long_idx = idx[fc_aligned > 0]
            if len(long_idx) >= 20:
                ic_long_list.append(fc_aligned[long_idx].corr(ret_aligned[long_idx]))

            # Short signal IC: forecast < 0 (flip both signs — predictive if -fc correlates with -ret)
            short_idx = idx[fc_aligned < 0]
            if len(short_idx) >= 20:
                ic_short_list.append(
                    (-fc_aligned[short_idx]).corr(-ret_aligned[short_idx])
                )

        results[f'{h}d'] = {
            'ic_all':   float(np.nanmean(ic_all_list))   if ic_all_list   else float('nan'),
            'ic_long':  float(np.nanmean(ic_long_list))  if ic_long_list  else float('nan'),
            'ic_short': float(np.nanmean(ic_short_list)) if ic_short_list else float('nan'),
            'n_instruments': len(ic_all_list),
        }

    return results


# ---------------------------------------------------------------------------
# Analysis 3: Regime decomposition (BTC 200d MA)
# ---------------------------------------------------------------------------

def analysis3_regime(
    positions: pd.DataFrame,
    prices_df: pd.DataFrame,
    diagnostics: pd.DataFrame,
    capital: float,
    horizons: list = None,
) -> dict:
    """
    Repeat Analysis 1 & 2 split by BTC bull/bear regime (BTC price vs 200d MA).
    """
    if horizons is None:
        horizons = [5]

    # Compute bull/bear regime mask
    btc_col = 'BTCUSDT_PERP'
    if btc_col not in prices_df.columns:
        # Try to find BTC column
        btc_candidates = [c for c in prices_df.columns if 'BTC' in c and 'PERP' in c]
        btc_col = btc_candidates[0] if btc_candidates else None

    if btc_col is None:
        return {'error': 'BTC price column not found'}

    btc = prices_df[btc_col].dropna()
    bull_regime = (btc > btc.rolling(200).mean()).reindex(positions.index).ffill().fillna(False)

    bull_mask = bull_regime.astype(bool)
    bear_mask = ~bull_mask

    results = {}
    for regime_name, mask in [('bull', bull_mask), ('bear', bear_mask)]:
        pos_regime = positions[mask]
        pr_regime  = prices_df.reindex(mask[mask].index, method='ffill')

        if pos_regime.empty:
            results[regime_name] = {'error': 'no data'}
            continue

        # P&L decomposition for this regime
        legs = _compute_pnl_legs(pos_regime, pr_regime, capital)
        total_ann = _ann_return(legs['total_pnl_daily'])

        pnl_stats = {
            'long':  _leg_stats(legs['long_pnl_daily'],  total_ann),
            'short': _leg_stats(legs['short_pnl_daily'], total_ann),
            'total': _leg_stats(legs['total_pnl_daily'], total_ann),
            'n_days': int(mask.sum()),
        }

        # IC decomposition for this regime (5d horizon)
        fc_panel = diagnostics.pivot(
            index='date', columns='instrument', values='combined_forecast'
        )
        regime_dates = mask[mask].index.intersection(fc_panel.index)
        fc_regime = fc_panel.loc[regime_dates]
        common_instr = [c for c in fc_regime.columns if c in prices_df.columns]

        if not common_instr or fc_regime.empty:
            ic_stats = {'5d': {'ic_long': float('nan'), 'ic_short': float('nan')}}
        else:
            pr_ic = prices_df[common_instr].reindex(fc_regime.index, method='ffill')
            fwd_ret = pr_ic.pct_change(5, fill_method=None).shift(-5)

            ic_long_list, ic_short_list = [], []
            for instr in common_instr:
                fc_s  = fc_regime[instr].dropna()
                ret_s = fwd_ret[instr].dropna()
                idx   = fc_s.index.intersection(ret_s.index)
                if len(idx) < 20:
                    continue
                fc_a  = fc_s[idx]
                ret_a = ret_s[idx]

                long_idx  = idx[fc_a > 0]
                short_idx = idx[fc_a < 0]
                if len(long_idx) >= 10:
                    ic_long_list.append(fc_a[long_idx].corr(ret_a[long_idx]))
                if len(short_idx) >= 10:
                    ic_short_list.append((-fc_a[short_idx]).corr(-ret_a[short_idx]))

            ic_stats = {'5d': {
                'ic_long':  float(np.nanmean(ic_long_list))  if ic_long_list  else float('nan'),
                'ic_short': float(np.nanmean(ic_short_list)) if ic_short_list else float('nan'),
            }}

        results[regime_name] = {
            'pnl': pnl_stats,
            'ic':  ic_stats,
        }

    regime_days = bull_mask.sum()
    total_days  = len(bull_mask)
    results['regime_split'] = {
        'bull_days': int(regime_days),
        'bear_days': int(total_days - regime_days),
        'bull_pct':  float(regime_days / total_days * 100),
    }

    return results


# ---------------------------------------------------------------------------
# Decision rule
# ---------------------------------------------------------------------------

def evaluate_tilt(sharpe_long: float, sharpe_short: float) -> dict:
    """
    Evaluate whether a forecast tilt is warranted.

    Returns dict with:
        ratio: long/short Sharpe ratio
        asymmetric: bool — is tilt warranted?
        direction: 'long' | 'short' | 'symmetric'
        suggested_offsets: list of offsets to test
    """
    if sharpe_short == 0 or np.isnan(sharpe_short) or np.isnan(sharpe_long):
        return {
            'ratio': float('nan'),
            'asymmetric': False,
            'direction': 'unknown',
            'suggested_offsets': [],
            'reason': 'Cannot compute ratio (zero or NaN Sharpe)',
        }

    ratio = sharpe_long / abs(sharpe_short)
    asymmetric = ratio < 0.8 or ratio > 1.2

    if ratio > 1.2:
        direction = 'long'
        suggested_offsets = [1.0, 2.0, 3.0, 5.0]
    elif ratio < 0.8:
        direction = 'short'
        suggested_offsets = [-1.0, -2.0, -3.0, -5.0]
    else:
        direction = 'symmetric'
        suggested_offsets = []

    return {
        'ratio': float(ratio),
        'asymmetric': bool(asymmetric),
        'direction': direction,
        'suggested_offsets': suggested_offsets,
        'reason': (
            f'Long/short Sharpe ratio = {ratio:.3f} '
            f'(outside [0.8, 1.2] → tilt warranted)'
            if asymmetric else
            f'Long/short Sharpe ratio = {ratio:.3f} '
            f'(within [0.8, 1.2] → symmetric, no tilt needed)'
        ),
    }


# ---------------------------------------------------------------------------
# Sanity check
# ---------------------------------------------------------------------------

def sanity_check(pnl_decomp: dict, tolerance: float = 0.05) -> bool:
    """
    Verify long + short P&L ≈ total P&L.

    Returns True if passes, False otherwise.
    """
    long_ret  = pnl_decomp['long']['ann_return_pct']
    short_ret = pnl_decomp['short']['ann_return_pct']
    total_ret = pnl_decomp['total']['ann_return_pct']
    residual  = abs((long_ret + short_ret) - total_ret)
    ok = residual < tolerance * max(abs(total_ret), 1.0)
    return bool(ok), float(residual)


# ---------------------------------------------------------------------------
# Report printing
# ---------------------------------------------------------------------------

def _fmt(val, fmt='6.2f', suffix='%'):
    if np.isnan(val):
        return ' ' * 7 + 'NaN'
    return f'{val:{fmt}}{suffix}'


def print_report(
    pnl_decomp: dict,
    ic_results: dict,
    regime_results: dict,
    tilt_decision: dict,
) -> None:
    print()
    print('=' * 60)
    print('LONG/SHORT ASYMMETRY REPORT')
    print('=' * 60)

    # --- Overall stats ---
    long  = pnl_decomp['long']
    short = pnl_decomp['short']
    total = pnl_decomp['total']

    def _ratio(a, b):
        if b == 0 or np.isnan(a) or np.isnan(b):
            return float('nan')
        return a / abs(b)

    print(f'\n{"":30s} {"Long":>8}  {"Short":>8}  {"Ratio":>7}  {"Total":>8}')
    print(f'{"─"*30}  {"─"*8}  {"─"*8}  {"─"*7}  {"─"*8}')

    for label, key, suffix, fmt in [
        ('Ann Return',   'ann_return_pct', '%',  '7.2f'),
        ('Ann Vol',      'ann_vol_pct',    '%',  '7.2f'),
        ('Sharpe',       'sharpe',         '',   '7.3f'),
        ('Hit Rate (1d)','hit_rate',       '%',  '7.1%'),
        ('% Total Ret',  'pct_of_total_return', '%', '7.1f'),
    ]:
        lv = long[key]
        sv = short[key]
        tv = total.get(key, float('nan'))

        if key == 'hit_rate':
            lv_str = f'{lv:.1%}' if not np.isnan(lv) else '    NaN'
            sv_str = f'{sv:.1%}' if not np.isnan(sv) else '    NaN'
            tv_str = f'{tv:.1%}' if not np.isnan(tv) else '    NaN'
            ratio_str = f'{_ratio(lv, sv):.3f}'  if not np.isnan(_ratio(lv, sv)) else '    NaN'
        else:
            lv_str = f'{lv:{fmt}}{suffix}' if not np.isnan(lv) else '     NaN'
            sv_str = f'{sv:{fmt}}{suffix}' if not np.isnan(sv) else '     NaN'
            tv_str = f'{tv:{fmt}}{suffix}' if not np.isnan(tv) else '     NaN'
            ratio_str = f'{_ratio(lv, sv):.3f}'  if not np.isnan(_ratio(lv, sv)) else '    NaN'

        print(f'{label:30s}  {lv_str:>8}  {sv_str:>8}  {ratio_str:>7}  {tv_str:>8}')

    # --- Forecast IC ---
    print(f'\n{"─"*60}')
    print('Forecast IC by Direction')
    print(f'{"─"*60}')
    print(f'{"Horizon":>10}  {"IC (all)":>10}  {"IC (long)":>10}  {"IC (short)":>10}  {"L/S Ratio":>10}')
    print(f'{"─"*10}  {"─"*10}  {"─"*10}  {"─"*10}  {"─"*10}')
    for h_key, h_data in sorted(ic_results.items()):
        ic_all  = h_data['ic_all']
        ic_long = h_data['ic_long']
        ic_sh   = h_data['ic_short']
        ls_r    = _ratio(ic_long, ic_sh)
        print(
            f'{h_key:>10}  '
            f'{ic_all:10.4f}  '
            f'{ic_long:10.4f}  '
            f'{ic_sh:10.4f}  '
            f'{ls_r:10.3f}'
        )

    # --- Regime breakdown ---
    if 'error' not in regime_results:
        print(f'\n{"─"*60}')
        split = regime_results.get('regime_split', {})
        print(f'Regime Breakdown  (bull {split.get("bull_pct", 0):.0f}% / bear {100 - split.get("bull_pct", 0):.0f}% of days)')
        print(f'{"─"*60}')
        print(f'{"":30s} {"Bull Long":>9}  {"Bull Short":>10}  {"Bear Long":>9}  {"Bear Short":>10}')
        print(f'{"─"*30}  {"─"*9}  {"─"*10}  {"─"*9}  {"─"*10}')

        for label, key, suffix, fmt in [
            ('Sharpe',    'sharpe',         '', '8.3f'),
            ('Ann Return','ann_return_pct', '%', '8.2f'),
        ]:
            bull_long  = regime_results.get('bull', {}).get('pnl', {}).get('long', {}).get(key, float('nan'))
            bull_short = regime_results.get('bull', {}).get('pnl', {}).get('short', {}).get(key, float('nan'))
            bear_long  = regime_results.get('bear', {}).get('pnl', {}).get('long', {}).get(key, float('nan'))
            bear_short = regime_results.get('bear', {}).get('pnl', {}).get('short', {}).get(key, float('nan'))

            def _s(v):
                if np.isnan(v): return '      NaN'
                return f'{v:{fmt}}{suffix}'

            print(f'{label:30s}  {_s(bull_long):>9}  {_s(bull_short):>10}  {_s(bear_long):>9}  {_s(bear_short):>10}')

        print(f'\n{"":30s} {"Bull":>7}  {"Bear":>7}')
        print(f'{"─"*30}  {"─"*7}  {"─"*7}')
        ic_bull_l = regime_results.get('bull', {}).get('ic', {}).get('5d', {}).get('ic_long', float('nan'))
        ic_bull_s = regime_results.get('bull', {}).get('ic', {}).get('5d', {}).get('ic_short', float('nan'))
        ic_bear_l = regime_results.get('bear', {}).get('ic', {}).get('5d', {}).get('ic_long', float('nan'))
        ic_bear_s = regime_results.get('bear', {}).get('ic', {}).get('5d', {}).get('ic_short', float('nan'))

        print(f'{"IC@5d (long signals)":30s}  {ic_bull_l:7.4f}  {ic_bear_l:7.4f}')
        print(f'{"IC@5d (short signals)":30s}  {ic_bull_s:7.4f}  {ic_bear_s:7.4f}')

    # --- Tilt decision ---
    print(f'\n{"═"*60}')
    print('TILT DECISION')
    print(f'{"═"*60}')
    print(f'  Long/Short Sharpe Ratio: {tilt_decision["ratio"]:.3f}')
    print(f'  Asymmetric:              {tilt_decision["asymmetric"]}')
    print(f'  {tilt_decision["reason"]}')
    if tilt_decision['asymmetric']:
        print(f'  Suggested tilt direction: {tilt_decision["direction"].upper()}')
        print(f'  Offsets to test: {tilt_decision["suggested_offsets"]}')
    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run_analysis(
    positions_csv: str,
    diagnostics_path: str,
    data_path: str,
    outdir: str,
    capital: float,
) -> dict:
    """Run the full asymmetry analysis and return results dict."""
    outdir_path = Path(outdir)
    outdir_path.mkdir(parents=True, exist_ok=True)

    print('=' * 60)
    print('LOADING DATA')
    print('=' * 60)

    # Load positions
    print(f'\nPositions: {positions_csv}')
    positions = pd.read_csv(positions_csv, index_col=0, parse_dates=True).fillna(0.0)
    print(f'  Shape: {positions.shape[0]} days × {positions.shape[1]} instruments')
    print(f'  Date range: {positions.index[0].date()} → {positions.index[-1].date()}')

    # Load diagnostics
    print(f'\nDiagnostics: {diagnostics_path}')
    diagnostics = pd.read_parquet(diagnostics_path)
    print(f'  Shape: {diagnostics.shape[0]} rows × {diagnostics.shape[1]} cols')
    print(f'  Columns: {list(diagnostics.columns)}')

    # Load prices
    print(f'\nMarket data: {data_path}')
    prices_df, _, _ = load_crypto_perps_panel(
        data_path, validate_schema=False, allow_jagged=True
    )
    print(f'  Price panel: {prices_df.shape[0]} days × {prices_df.shape[1]} instruments')

    # --- Analysis 1: P&L decomposition ---
    print('\n' + '=' * 60)
    print('ANALYSIS 1: P&L Decomposition by Direction')
    print('=' * 60)
    pnl_decomp = analysis1_pnl_decomposition(positions, prices_df, capital)

    # Sanity check
    ok, residual = sanity_check(pnl_decomp)
    status = '✓ OK' if ok else f'⚠ FAIL (residual={residual:.3f}%)'
    print(f'\n  Sanity check (long + short ≈ total): {status}')

    # --- Analysis 2: Forecast IC ---
    print('\n' + '=' * 60)
    print('ANALYSIS 2: Forecast IC by Direction')
    print('=' * 60)
    ic_results = analysis2_forecast_ic(diagnostics, prices_df, horizons=[1, 5, 21])

    # --- Analysis 3: Regime decomposition ---
    print('\n' + '=' * 60)
    print('ANALYSIS 3: Regime Decomposition (BTC 200d MA)')
    print('=' * 60)
    regime_results = analysis3_regime(positions, prices_df, diagnostics, capital)

    # --- Tilt decision ---
    sharpe_long  = pnl_decomp['long']['sharpe']
    sharpe_short = pnl_decomp['short']['sharpe']
    tilt_decision = evaluate_tilt(sharpe_long, sharpe_short)

    # --- Print report ---
    print_report(pnl_decomp, ic_results, regime_results, tilt_decision)

    # --- Assemble full results ---
    results = {
        'pnl_decomposition':  pnl_decomp,
        'forecast_ic':        ic_results,
        'regime_analysis':    regime_results,
        'tilt_decision':      tilt_decision,
        'metadata': {
            'positions_csv':    positions_csv,
            'diagnostics_path': diagnostics_path,
            'data_path':        data_path,
            'capital':          capital,
            'n_days':           len(positions),
            'n_instruments':    len(positions.columns),
            'date_start':       str(positions.index[0].date()),
            'date_end':         str(positions.index[-1].date()),
        },
    }

    # --- Save report ---
    out_json = outdir_path / 'asymmetry_report.json'
    with open(out_json, 'w') as f:
        json.dump(results, f, indent=2, default=str)
    print(f'Report saved: {out_json}')

    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description='Long/short asymmetry analysis for crypto backtest.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        '--positions', type=Path,
        default=Path('out/sector_test/reverted_baseline/positions.csv'),
        help='Path to positions.csv',
    )
    parser.add_argument(
        '--diagnostics', type=Path,
        default=Path('out/sector_test/reverted_baseline/diagnostics.parquet'),
        help='Path to diagnostics.parquet',
    )
    parser.add_argument(
        '--data', type=Path,
        default=Path('data/dataset_538registry_6yr_jagged.parquet'),
        help='Path to parquet dataset',
    )
    parser.add_argument(
        '--outdir', type=Path,
        default=Path('out/asymmetry_analysis'),
        help='Output directory for report',
    )
    parser.add_argument(
        '--capital', type=float, default=10_000.0,
        help='Notional capital in USD (default: 10000)',
    )

    args = parser.parse_args()

    # Validate paths
    missing = []
    for name, path in [('positions', args.positions), ('diagnostics', args.diagnostics), ('data', args.data)]:
        if not path.exists():
            missing.append(f'  {name}: {path}')
    if missing:
        print('ERROR: Missing required files:')
        for m in missing:
            print(m)
        sys.exit(1)

    run_analysis(
        positions_csv=str(args.positions),
        diagnostics_path=str(args.diagnostics),
        data_path=str(args.data),
        outdir=str(args.outdir),
        capital=args.capital,
    )


if __name__ == '__main__':
    main()
