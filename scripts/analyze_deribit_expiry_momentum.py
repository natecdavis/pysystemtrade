#!/usr/bin/env python3
"""
Deribit Expiry Momentum Event Study

Tests whether momentum signal P&L concentrates around Deribit monthly
options expiry dates (last Friday of each month, 08:00 UTC).

Rationale: Deribit is the dominant crypto options exchange. Monthly expiry
forces market makers to unwind gamma hedges and leveraged longs to de-risk
before reporting periods. If losers are sold preferentially (margin cover /
risk reduction), this produces the same structure as the "Intramonth Momentum
Cycle" paper — concentrated momentum returns in the pre-expiry window — but
via different causal plumbing.

Output:
  - Console table: event day | mean daily return | t-stat | cumulative return
  - Phase 2 criteria assessment (proceed with calendar scaler or not)
  - CSV saved to --outdir

Usage:
    python scripts/analyze_deribit_expiry_momentum.py \\
        --config config/crypto_perps_1k.yaml \\
        --data data/dataset_538registry_6yr_jagged.parquet \\
        --outdir out/deribit_expiry_analysis

Runtime: ~8-15 minutes (system build + per-rule accounts computation).
"""

import argparse
import logging
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

from syscore.constants import arg_not_supplied
from sysdata.config.configdata import Config
from systems.basesystem import System
from systems.forecasting import Rules
from systems.forecast_scale_cap import ForecastScaleCap
from systems.forecast_combine import ForecastCombine
from systems.rawdata import RawData
from systems.positionsizing import PositionSizing
from systems.accounts.accounts_stage import Account
from sysdata.crypto.parquet_perps_sim_data import parquetCryptoPerpsSimData
from systems.crypto_perps.crypto_portfolio import CryptoPortfolios
from systems.crypto_perps.crypto_portfolio_oi_overlay import (
    CryptoDynamicPortfolioWithOIOverlay,
    CryptoDynamicPortfolio,
    CryptoPortfolioWithOIOverlay,
)
from systems.crypto_perps.forecast_combine_gated import ForecastCombineGated

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# Momentum rule families to include in the analysis.
# relmomentum and residual_momentum are excluded: cross-sectional and
# macro-residualized signals whose return drivers are different from
# the time-series momentum that Deribit expiry flows would affect.
MOMENTUM_PREFIXES = ('ewmac_', 'breakout_', 'normmom_', 'accel_', 'assettrend_')

# Event study window (trading days relative to expiry date, day 0 = expiry Friday)
WINDOW_BEFORE = 15
WINDOW_AFTER = 10


# ---------------------------------------------------------------------------
# Deribit expiry calendar
# ---------------------------------------------------------------------------

def deribit_expiry_dates(start: str, end: str) -> pd.DatetimeIndex:
    """
    Return the last Friday of each month between start and end (inclusive).
    Deribit monthly options expire on the last Friday at 08:00 UTC.
    """
    months = pd.date_range(start, end, freq='ME')
    fridays = []
    for month_end in months:
        d = month_end
        while d.weekday() != 4:  # 4 = Friday
            d -= pd.Timedelta(days=1)
        fridays.append(d)
    return pd.DatetimeIndex(fridays)


# ---------------------------------------------------------------------------
# System construction (mirrors run_dynamic_universe_backtest.py)
# ---------------------------------------------------------------------------

def build_system(config_path: str, data_path: str) -> System:
    """Build the full trading system from config + data."""
    logger.info(f"Loading config: {config_path}")
    with open(config_path) as f:
        config_dict = yaml.safe_load(f)
    config = Config(config_dict)

    du = getattr(config, 'dynamic_universe', {}) or {}
    dynamic_universe_config = {
        'max_sr_cost_per_trade': du.get('max_sr_cost_per_trade', 0.01),
        'max_sr_cost_annual':    du.get('max_sr_cost_annual', 0.13),
        'stack_turnover':        du.get('stack_turnover', 15.0),
        'adv_window':            du.get('adv_window', 30),
        'fee_bps':               du.get('fee_bps', 5),
        'vol_window':            du.get('vol_window', 35),
    }

    data_dir = Path(data_path).parent
    env_root = Path(os.environ.get('LIVE_OPS_ENV_ROOT', str(Path.cwd())))

    def _discover(name):
        p = data_dir / name
        if p.exists():
            logger.info(f"  Found {name}")
            return str(p)
        logger.info(f"  {name} not found — related signals may be NaN")
        return arg_not_supplied

    logger.info(f"Loading dataset: {data_path}")
    data = parquetCryptoPerpsSimData(
        dataset_path=data_path,
        config_path=config_path,
        env_root=env_root,
        use_dynamic_universe=True,
        dynamic_universe_config=dynamic_universe_config,
        macro_data_path=_discover('macro_factors.parquet'),
        oi_data_path=_discover('binance_oi_processed.parquet'),
        sector_map_path=_discover('sector_map.json'),
        fg_data_path=_discover('fg_index.parquet'),
        mvrv_data_path=_discover('mvrv_index.parquet'),
        active_addresses_data_path=_discover('active_addresses.parquet'),
        market_cap_data_path=_discover('market_cap.parquet'),
    )

    logger.info(f"  Loaded {len(data.get_instrument_list())} instruments")

    use_oi    = config.get_element_or_default('use_oi_overlay', False)
    use_fg    = config.get_element_or_default('use_fg_overlay', False)
    use_mvrv  = config.get_element_or_default('use_mvrv_overlay', False)
    use_any   = use_oi or use_fg or use_mvrv

    portfolio_stage = (
        CryptoDynamicPortfolioWithOIOverlay() if use_any else CryptoDynamicPortfolio()
    )

    combiner = (
        ForecastCombineGated()
        if config.get_element_or_default('use_gated_carry', False)
        else ForecastCombine()
    )

    system = System(
        stage_list=[
            Account(),
            portfolio_stage,
            PositionSizing(),
            combiner,
            ForecastScaleCap(),
            Rules(),
            RawData(),
        ],
        data=data,
        config=config,
    )
    logger.info("✓ System created")
    return system


# ---------------------------------------------------------------------------
# Per-rule P&L extraction
# ---------------------------------------------------------------------------

def get_momentum_returns(system: System) -> tuple[pd.Series, pd.Series]:
    """
    Extract weighted daily P&L for each momentum rule, sum to get the
    momentum-family return series. Also return total portfolio returns.

    Returns:
        momentum_returns: daily decimal returns for the combined momentum sleeve
        portfolio_returns: daily decimal returns for the full portfolio
    """
    all_rules = list(system.config.trading_rules.keys())
    momentum_rules = [r for r in all_rules
                      if any(r.startswith(p) for p in MOMENTUM_PREFIXES)]

    logger.info(f"Momentum rules ({len(momentum_rules)}): {momentum_rules}")

    rule_series = {}
    for i, rule in enumerate(momentum_rules, 1):
        logger.info(f"  [{i}/{len(momentum_rules)}] Computing P&L for {rule}...")
        try:
            curve = system.accounts.pandl_for_trading_rule_weighted(rule)
            # accountCurveGroup: .net gives the combined curve across instruments
            ts = curve.net.as_ts
            rule_series[rule] = ts
        except Exception as e:
            logger.warning(f"    Skipping {rule}: {e}")

    if not rule_series:
        raise RuntimeError("No momentum rule P&L could be computed.")

    momentum_returns = pd.DataFrame(rule_series).sum(axis=1, min_count=1)
    momentum_returns.name = 'momentum'

    logger.info("Computing portfolio returns...")
    try:
        portfolio_returns = system.accounts.portfolio().net.as_ts
        portfolio_returns.name = 'portfolio'
    except Exception as e:
        logger.warning(f"Portfolio returns failed: {e}")
        portfolio_returns = pd.Series(dtype=float, name='portfolio')

    return momentum_returns, portfolio_returns


# ---------------------------------------------------------------------------
# Event study
# ---------------------------------------------------------------------------

def run_event_study(
    returns: pd.Series,
    expiry_dates: pd.DatetimeIndex,
    window_before: int = WINDOW_BEFORE,
    window_after: int = WINDOW_AFTER,
) -> pd.DataFrame:
    """
    For each expiry date, extract returns in [-window_before, +window_after]
    trading days (day 0 = expiry). Returns a DataFrame of shape
    (n_events, window_before + window_after + 1) indexed by event date
    with columns = relative trading days.
    """
    idx = returns.index
    records = []
    event_dates_used = []

    for expiry in expiry_dates:
        # Find the closest trading day on or after expiry
        pos = idx.searchsorted(expiry)
        if pos >= len(idx):
            continue
        lo = max(0, pos - window_before)
        hi = min(len(idx), pos + window_after + 1)

        window_vals = returns.iloc[lo:hi].values
        rel_days = list(range(-(pos - lo), len(window_vals) - (pos - lo)))

        record = pd.Series(window_vals, index=rel_days)
        records.append(record)
        event_dates_used.append(expiry)

    event_df = pd.DataFrame(records, index=pd.DatetimeIndex(event_dates_used))
    # Ensure columns are sorted as integers
    event_df = event_df.reindex(columns=sorted(event_df.columns))
    return event_df


# ---------------------------------------------------------------------------
# Results display + Phase 2 criteria
# ---------------------------------------------------------------------------

def print_event_table(event_df: pd.DataFrame, label: str) -> None:
    mean_ret = event_df.mean()
    std_err  = event_df.std() / np.sqrt(event_df.notna().sum())
    t_stat   = mean_ret / std_err.replace(0, np.nan)
    cum_ret  = (1 + mean_ret).cumprod() - 1
    pct_pos  = (event_df > 0).sum() / event_df.notna().sum()

    print(f"\n{'='*72}")
    print(f"EVENT STUDY: {label.upper()} RETURNS AROUND DERIBIT EXPIRY")
    print(f"N events: {len(event_df)}  |  Window: T-{WINDOW_BEFORE} to T+{WINDOW_AFTER}")
    print(f"{'='*72}")
    print(f"{'Day':>5} {'MeanRet%':>10} {'T-stat':>8} {'CumRet%':>10} {'%Pos':>8}")
    print(f"{'-'*5} {'-'*10} {'-'*8} {'-'*10} {'-'*8}")
    for day in sorted(event_df.columns):
        mr  = mean_ret.get(day, np.nan) * 100
        ts  = t_stat.get(day, np.nan)
        cr  = cum_ret.get(day, np.nan) * 100
        pp  = pct_pos.get(day, np.nan) * 100
        flag = " *" if abs(ts) > 1.5 else ("  " if not np.isnan(ts) else "  ")
        print(f"{day:>5} {mr:>9.3f}% {ts:>8.2f}{flag} {cr:>9.2f}% {pp:>7.1f}%")
    print(f"{'='*72}")


def assess_phase2_criteria(event_df: pd.DataFrame) -> bool:
    """
    Apply the four Phase 2 gate criteria. Print results. Return True if all pass.

    Criteria:
    1. A contiguous window of ≤8 days where mean return is consistently positive
    2. At least half those days have |t-stat| > 1.0
    3. The window is pre-expiry (days < 0), consistent with the flow mechanism
    4. The effect is visible in ≥60% of individual events (not outlier-driven)
    """
    mean_ret = event_df.mean()
    std_err  = event_df.std() / np.sqrt(event_df.notna().sum())
    t_stat   = (mean_ret / std_err.replace(0, np.nan)).fillna(0)

    # Find best contiguous positive window (max sum of mean returns, length ≤ 8)
    days = sorted(event_df.columns)
    best_window = None
    best_sum = -np.inf
    for start_i in range(len(days)):
        for length in range(1, 9):
            end_i = start_i + length
            if end_i > len(days):
                break
            window_days = days[start_i:end_i]
            window_sum = mean_ret.reindex(window_days).sum()
            if mean_ret.reindex(window_days).min() > 0 and window_sum > best_sum:
                best_sum = window_sum
                best_window = window_days

    print(f"\n{'='*72}")
    print("PHASE 2 CRITERIA ASSESSMENT")
    print(f"{'='*72}")

    if best_window is None:
        print("✗ No contiguous positive window found.")
        print("→ PHASE 2: DO NOT PROCEED")
        return False

    window_days = list(best_window)
    w_tstat = t_stat.reindex(window_days)
    frac_above_1 = (w_tstat.abs() > 1.0).mean()
    is_pre_expiry = all(d < 0 for d in window_days)
    # Criterion 4: fraction of events positive in ≥ half the window days
    pct_pos_by_day = (event_df[window_days] > 0).sum() / event_df[window_days].notna().sum()
    frac_events_60pct = (pct_pos_by_day >= 0.60).mean()

    c1 = True  # already guaranteed — we found a contiguous positive window
    c2 = frac_above_1 >= 0.5
    c3 = is_pre_expiry
    c4 = frac_events_60pct >= 0.5  # at least half the days have ≥60% positive events

    total_return_pct = best_sum * 100
    print(f"Best contiguous positive window: T{min(window_days):+d} to T{max(window_days):+d} "
          f"({len(window_days)} days, total mean return: {total_return_pct:.2f}%)")
    print()
    print(f"  C1: Contiguous positive window ≤8 days:  {'✓ PASS' if c1 else '✗ FAIL'}")
    print(f"  C2: ≥50% days with |t-stat| > 1.0:       {'✓ PASS' if c2 else '✗ FAIL'} "
          f"({frac_above_1*100:.0f}% of window days)")
    print(f"  C3: Window is pre-expiry (all days < 0):  {'✓ PASS' if c3 else '✗ FAIL'}")
    print(f"  C4: Effect in ≥60% of events (≥50% days): {'✓ PASS' if c4 else '✗ FAIL'} "
          f"({frac_events_60pct*100:.0f}% of window days qualify)")
    print()

    all_pass = c1 and c2 and c3 and c4
    if all_pass:
        print("→ PHASE 2: ALL CRITERIA MET — proceed with calendar scaler")
        print(f"   Suggested config: window_start={min(window_days)}, "
              f"window_end={max(window_days)}, scale_up=1.5")
    else:
        print("→ PHASE 2: CRITERIA NOT MET — do not implement calendar scaler")
    print(f"{'='*72}")

    return all_pass


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Deribit expiry momentum event study"
    )
    parser.add_argument('--config', required=True, help="Config YAML path")
    parser.add_argument('--data', required=True, help="Parquet dataset path")
    parser.add_argument('--outdir', default='out/deribit_expiry_analysis',
                        help="Output directory")
    args = parser.parse_args()

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    # Build system
    system = build_system(args.config, args.data)

    # Extract returns
    logger.info("Extracting momentum rule returns (this takes several minutes)...")
    momentum_rets, portfolio_rets = get_momentum_returns(system)

    logger.info(f"Momentum returns: {len(momentum_rets)} days, "
                f"{momentum_rets.index[0].date()} → {momentum_rets.index[-1].date()}")

    # Generate expiry calendar
    start = str(momentum_rets.index[0].date())
    end   = str(momentum_rets.index[-1].date())
    expiry_dates = deribit_expiry_dates(start, end)
    logger.info(f"Deribit expiry dates: {len(expiry_dates)} events "
                f"({expiry_dates[0].date()} → {expiry_dates[-1].date()})")

    # Run event studies
    logger.info("Running event studies...")
    mom_event_df  = run_event_study(momentum_rets, expiry_dates)
    port_event_df = run_event_study(portfolio_rets, expiry_dates)

    # Print results
    print_event_table(mom_event_df, "Momentum component")
    print_event_table(port_event_df, "Full portfolio")

    # Assess Phase 2 criteria (momentum only)
    phase2_go = assess_phase2_criteria(mom_event_df)

    # Save outputs
    mom_event_df.to_csv(outdir / 'event_study_momentum.csv')
    port_event_df.to_csv(outdir / 'event_study_portfolio.csv')

    summary = {
        'n_events': len(mom_event_df),
        'date_range': f"{start} to {end}",
        'expiry_dates': [str(d.date()) for d in expiry_dates],
        'momentum_rules': [r for r in system.config.trading_rules
                           if any(r.startswith(p) for p in MOMENTUM_PREFIXES)],
        'phase2_criteria_met': phase2_go,
    }
    import json
    (outdir / 'summary.json').write_text(json.dumps(summary, indent=2))

    logger.info(f"\n✓ Results saved to {outdir}/")
    return 0 if phase2_go else 1


if __name__ == '__main__':
    sys.exit(main())
