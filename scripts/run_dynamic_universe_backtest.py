#!/usr/bin/env python3
"""
Run backtest with dynamic universe using parquet-backed data adapter.

This script runs a pysystemtrade backtest with dynamic instrument universe,
outputting results compatible with the live advisory workflow.

Usage:
    python scripts/run_dynamic_universe_backtest.py \
        --config config/crypto_perps_dynamic_universe_v1.yaml \
        --data data/dataset_latest.parquet \
        --outdir out/backtest_latest

Outputs (compatible with generate_trade_plan.py):
    - positions.csv: Daily positions for all instruments
    - diagnostics.parquet: Full system diagnostics
    - metadata.json: Run metadata (config, dates, instruments)
"""

import argparse
import sys
import json
import logging
from pathlib import Path
from datetime import datetime

import pandas as pd
import numpy as np

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sysdata.config.configdata import Config
from sysdata.crypto.parquet_perps_sim_data import parquetCryptoPerpsSimData
from systems.provided.crypto_example.core.dynamic_portfolio import CryptoDynamicPortfolio
from systems.crypto_perps.crypto_portfolio import CryptoPortfolios
from systems.crypto_perps.crypto_portfolio_oi_overlay import (
    CryptoPortfolioWithOIOverlay,
    CryptoDynamicPortfolioWithOIOverlay,
)
from systems.basesystem import System
from systems.forecasting import Rules
from systems.forecast_combine import ForecastCombine
from systems.crypto_perps.forecast_combine_gated import ForecastCombineGated
from systems.forecast_scale_cap import ForecastScaleCap
from systems.rawdata import RawData
from systems.positionsizing import PositionSizing
from systems.accounts.accounts_stage import Account

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def build_dynamic_universe_config(raw_config: dict) -> dict:
    """Build the kwargs dict consumed by DynamicUniverseManager (and by the
    IVOL-eligibility step in parquetCryptoPerpsSimData._init_dynamic_universe).

    Centralised so tests can verify every config key the downstream consumer
    reads is actually plumbed. Prior to this helper, ``min_annual_vol`` and the
    IVOL filter keys were declared in the YAML but silently dropped on the way
    through — the live config's ``min_annual_vol: 0.2`` had been a no-op for
    every backtest run.
    """
    du = raw_config.get('dynamic_universe', {}) or {}
    return {
        'max_sr_cost_per_trade': du.get('max_sr_cost_per_trade', 0.01),
        'max_sr_cost_annual': du.get('max_sr_cost_annual', 0.13),
        'stack_turnover': du.get('stack_turnover', 15.0),
        'adv_window': du.get('adv_window', 30),
        'fee_bps': du.get('fee_bps', 5),
        'vol_window': du.get('vol_window', 35),
        'min_annual_vol': du.get('min_annual_vol', 0.0),
        'min_history_rule_requirement': du.get('min_history_rule_requirement', 'any_rule'),
        'ivol_cap_enabled': du.get('ivol_cap_enabled', False),
        'ivol_cap_percentile': du.get('ivol_cap_percentile', 75),
        'ivol_window': du.get('ivol_window', du.get('vol_window', 35)),
        'forecast_weights': raw_config.get('forecast_weights'),
    }


def apply_forecast_buffering(
    system,
    optimal_positions: pd.DataFrame,
) -> pd.DataFrame:
    """
    Apply Carver's forecast-method position buffer to optimal positions.

    Buffer bounds come from system.portfolio.get_actual_buffers_for_position(),
    which computes: buffer_width = vol_scalar × instrument_weight × IDM × buffer_size.
    This is dynamic (~±$0.3-2 USD per instrument on a $2.5K/30-instrument account) vs.
    the prior static method (~±$0.1-2, diluted by out-of-universe zeros).

    State machine (trade_to_edge=False — jump to optimal on breach):
      last_pos > top_pos  →  current_pos = optimal
      last_pos < bot_pos  →  current_pos = optimal
      else                →  hold last_pos

    Jumping to optimal (not just the edge) performs better in trending crypto:
    discrete lot sizes mean partial edge trades often fall below the minimum
    notional and get suppressed, while going straight to optimal ensures a clean
    full rebalance. Trade-to-edge Sharpe was -2.8% vs this method on 6yr backtest.

    Entry/exit with instrument_weight_ewma_span=1: weights jump 0↔1/N instantly.
    When an instrument exits, the buffer collapses to [0,0] and the state
    machine immediately exits the position. When it enters, the first-day
    position (0) falls outside the new buffer zone and jumps to optimal.
    Both are correct — entry/exit transitions are intentional, not noise.
    """
    buffered = {}
    min_notional = system.config.get_element_or_default("min_notional_position", 10.0)

    for instrument in optimal_positions.columns:
        opt = optimal_positions[instrument]

        try:
            pos_buffers = system.portfolio.get_actual_buffers_for_position(instrument)
        except Exception as e:
            logger.warning(
                f"Could not get buffer bounds for {instrument}, "
                f"passing through unmodified: {e}"
            )
            buffered[instrument] = opt
            continue

        top_pos = pos_buffers['top_pos'].reindex(opt.index).ffill()
        bot_pos = pos_buffers['bot_pos'].reindex(opt.index).ffill()

        try:
            prices = system.rawdata.get_daily_prices(instrument).reindex(opt.index, method='ffill')
            price_values = prices.values
        except Exception:
            price_values = np.full(len(opt), np.nan)

        values = opt.values
        top = top_pos.values
        bot = bot_pos.values

        current_pos = float(values[0]) if not np.isnan(values[0]) else 0.0
        result = [current_pos]

        for i in range(1, len(values)):
            optimal = float(values[i])
            t = float(top[i])
            b = float(bot[i])

            if np.isnan(t) or np.isnan(b) or np.isnan(optimal):
                result.append(current_pos)
                continue

            if current_pos > t or current_pos < b:
                if abs(optimal) > 1e-6:  # not a full close — check $10 minimum
                    price = float(price_values[i])
                    if not np.isnan(price) and price > 0:
                        delta_notional = abs(optimal - current_pos) * price
                        if delta_notional < min_notional:
                            result.append(current_pos)  # sub-$10 trade — hold
                            continue
                current_pos = optimal  # breach → jump to optimal
            result.append(current_pos)

        buffered[instrument] = pd.Series(result, index=opt.index, name=instrument)

    return pd.DataFrame(buffered)


def _write_universe_snapshot(system, output_path: Path, config_path: str) -> None:
    """
    Extract Stage 2 tradable set from portfolio stage and write universe_snapshot.json.

    The snapshot records the last date's tradable instruments, entry/exit transitions
    vs any previous snapshot, and the selector parameters used.

    Args:
        system: Backtest System object (portfolio stage must have _tradable_over_time)
        output_path: Directory where universe_snapshot.json will be written
        config_path: Path to config YAML (for reading dynamic_universe parameters)
    """
    import yaml

    portfolio_stage = system.portfolio
    tradable_over_time = getattr(portfolio_stage, '_tradable_over_time', None)

    if not tradable_over_time:
        logger.warning(
            "_tradable_over_time not found on portfolio stage — "
            "Stage 2 may not be wired in or top_k not configured. Skipping snapshot."
        )
        return

    last_date = max(tradable_over_time.keys())
    new_tradable = sorted(tradable_over_time[last_date])

    # Load dynamic_universe config parameters
    with open(config_path) as f:
        raw_config = yaml.safe_load(f)
    du_config = raw_config.get('dynamic_universe', {})
    K = du_config.get('top_k', 30)
    entry_buffer = du_config.get('entry_buffer', 5)
    exit_buffer = du_config.get('exit_buffer', 10)

    # Compute entrants/exits relative to any previous snapshot in the same output dir
    prev_snapshot_path = output_path / 'universe_snapshot.json'
    prev_tradable = None
    if prev_snapshot_path.exists():
        try:
            with open(prev_snapshot_path) as f:
                prev_snap = json.load(f)
            prev_tradable = set(prev_snap.get('tradable_instruments', []))
        except (json.JSONDecodeError, IOError):
            pass

    if prev_tradable is not None:
        entrants = sorted(set(new_tradable) - prev_tradable)
        exits = sorted(prev_tradable - set(new_tradable))
    else:
        entrants = []
        exits = []

    snapshot = {
        'as_of_date': last_date.strftime('%Y-%m-%d'),
        'tradable_instruments': new_tradable,
        'count': len(new_tradable),
        'K': K,
        'entry_threshold': K - entry_buffer,
        'exit_threshold': K + exit_buffer,
        'pinned_instruments': raw_config.get('pinned_instruments', []),
        'entrants': entrants,
        'exits': exits,
    }

    snapshot_path = output_path / 'universe_snapshot.json'
    with open(snapshot_path, 'w') as f:
        json.dump(snapshot, f, indent=2)

    logger.info(
        f"  ✓ {snapshot_path} "
        f"({len(new_tradable)} instruments, {len(entrants)} entrants, {len(exits)} exits)"
    )


def _compute_calendar_pnl(
    portfolio_positions: pd.DataFrame,
    prices_df: pd.DataFrame,
    meta_df: pd.DataFrame,
    capital: float,
) -> tuple:
    """
    Compute calendar-daily gross returns and transaction costs as fractions of capital.

    Uses the full calendar-day index (including weekends) so that crypto weekend
    moves are captured rather than bundled into the following Monday.

    Gross:  pos[t-1] × (price[t] - price[t-1]) / capital
    Cost:   |Δpos[t]| × price[t] × (spread_frac/2 + taker_fee_frac) / capital
            (half-spread on entry + taker fee; mirrored on exit when position reverses)

    Args:
        portfolio_positions: dates × instruments, position in base-asset units
        prices_df:           dates × instruments close prices (calendar daily)
        meta_df:             MultiIndex (date, instrument) with spread_frac, taker_fee_frac
        capital:             notional capital in USD

    Returns:
        (gross_returns, daily_cost) — both pd.Series indexed on portfolio_positions.index
    """
    instruments = [c for c in portfolio_positions.columns if c in prices_df.columns]
    pos = portfolio_positions[instruments]
    price = prices_df[instruments].reindex(pos.index, method='ffill')

    # Gross P&L
    gross_pnl = (pos.shift(1) * price.diff()).sum(axis=1)
    gross_returns = gross_pnl / capital

    # Transaction costs: incurred on position-change days
    pos_change = pos.diff().abs()
    try:
        spread_panel = (
            meta_df['spread_frac']
            .unstack('instrument')
            .reindex(pos.index, method='ffill')
            .reindex(columns=instruments)
        )
        fee_panel = (
            meta_df['taker_fee_frac']
            .unstack('instrument')
            .reindex(pos.index, method='ffill')
            .reindex(columns=instruments)
        )
        # Per-leg cost: half-spread (crossing) + taker fee
        cost_frac_panel = spread_panel / 2.0 + fee_panel
        notional_traded = (pos_change * price * cost_frac_panel).sum(axis=1)
    except Exception as e:
        logger.warning(f"  Per-instrument cost data unavailable ({e}), using flat 10bps")
        notional_traded = (pos_change * price).sum(axis=1) * 0.001

    daily_cost = notional_traded / capital
    return gross_returns, daily_cost


def _compute_funding_pnl_series(
    portfolio_positions: pd.DataFrame,
    prices_df: pd.DataFrame,
    funding_rates_df: pd.DataFrame,
    capital: float,
) -> pd.Series:
    """
    Compute daily portfolio funding P&L as a fraction of capital.

    Convention:
      - Long position (positive) + positive funding → you PAY → negative P&L
      - Short position (negative) + positive funding → you RECEIVE → positive P&L
      formula: funding_pnl = -signed_position_base × price × funding_rate

    Args:
        portfolio_positions: dates × instruments, base-asset units
        prices_df: dates × instruments, close prices
        funding_rates_df: dates × instruments, daily total funding rate
        capital: notional trading capital in USD

    Returns:
        pd.Series of daily funding P&L as fraction of capital
    """
    if funding_rates_df.empty or capital <= 0:
        return pd.Series(0.0, index=portfolio_positions.index)

    instruments = [
        c for c in portfolio_positions.columns
        if c in prices_df.columns and c in funding_rates_df.columns
    ]
    if not instruments:
        return pd.Series(0.0, index=portfolio_positions.index)

    pos = portfolio_positions[instruments].reindex(funding_rates_df.index, method='ffill')
    price = prices_df[instruments].reindex(funding_rates_df.index, method='ffill')
    rate = funding_rates_df[instruments]

    daily_funding_usd = -(pos * price * rate).sum(axis=1)
    return daily_funding_usd / capital


def _compute_performance_metrics(
    system,
    portfolio_positions: pd.DataFrame,
    weights: pd.DataFrame,
    output_path: Path,
    data=None,
) -> None:
    """
    Compute and write performance_summary.json using existing accounting/metrics modules.
    Called after positions are computed and written.

    Uses calendar-daily P&L (all 365 days/year including weekends) so that crypto
    weekend moves are captured accurately. Pysystemtrade's Account stage is NOT used
    here because it runs on a business-day calendar and silently drops ~635 weekend
    days per 6-year backtest.
    """
    from systems.provided.crypto_example.core.portfolio_metrics import (
        calculate_all_metrics,
        format_metrics_table,
    )

    logger.info("\nComputing performance metrics...")

    capital = float(getattr(system.config, 'notional_trading_capital', 10000.0))

    # 1. Calendar-daily gross returns + transaction costs
    daily_returns_dec = None
    daily_cost_ann = 0.0
    funding_drag_ann = 0.0

    if data is not None:
        try:
            gross_returns, daily_cost = _compute_calendar_pnl(
                portfolio_positions,
                data._prices_df,
                data._meta_df,
                capital,
            )
            net_before_funding = gross_returns - daily_cost
            daily_cost_ann = float(daily_cost.mean() * 365)
            logger.info(
                f"  Calendar P&L: {len(gross_returns)} days  "
                f"cost drag = {daily_cost_ann * 100:.2f}% p.a."
            )
            daily_returns_dec = net_before_funding
        except Exception as e:
            logger.warning(f"  Calendar P&L computation failed ({e}) — P&L metrics will be omitted")

    # 1b. Add funding P&L
    if daily_returns_dec is not None and data is not None:
        try:
            funding_rates_df = data.get_funding_rates_df()
            if not funding_rates_df.empty:
                funding_series = _compute_funding_pnl_series(
                    portfolio_positions,
                    data._prices_df,
                    funding_rates_df,
                    capital,
                )
                funding_aligned = funding_series.reindex(
                    daily_returns_dec.index
                ).fillna(0.0)
                funding_drag_ann = float(funding_aligned.mean() * 365)
                daily_returns_dec = daily_returns_dec + funding_aligned
                logger.info(
                    f"  Funding P&L: annualised drag = {funding_drag_ann:.4f} "
                    f"({funding_drag_ann * 100:.2f}% p.a.)"
                )
        except Exception as e:
            logger.warning(f"  Funding P&L computation failed ({e}) — skipping")

    # 2. Portfolio-specific metrics from positions/weights
    # Count instruments with non-zero POSITIONS (after lot-size rounding and min-notional
    # filter) — not just non-zero weights. The two differ when many positions are zeroed
    # by the $25 min-notional floor.
    universe_size = (weights > 0).sum(axis=1)
    avg_active_weighted = float(universe_size.mean())
    positioned_size = (portfolio_positions.abs() > 0).sum(axis=1)
    avg_active = float(positioned_size.mean())

    # Annual turnover: total abs position changes / (2 × avg exposure) / n_years
    total_exposure = portfolio_positions.abs().sum(axis=1)
    avg_exposure = float(total_exposure.mean())
    daily_delta = portfolio_positions.diff().abs().sum(axis=1)
    n_years = len(portfolio_positions) / 365.0
    if avg_exposure > 0 and n_years > 0:
        annual_turnover = float(daily_delta.sum() / (2.0 * avg_exposure) / n_years)
    else:
        annual_turnover = float('nan')

    start_date = portfolio_positions.index[0].strftime('%Y-%m-%d')
    end_date = portfolio_positions.index[-1].strftime('%Y-%m-%d')
    n_instruments = len(portfolio_positions.columns)
    n_days = len(portfolio_positions)

    # 3. Compute and display metrics
    metrics = {}
    if daily_returns_dec is not None and len(daily_returns_dec) >= 20:
        metrics = calculate_all_metrics(daily_returns_dec, name="Dynamic Universe")
        table = format_metrics_table([metrics])
        print("\nPERFORMANCE SUMMARY")
        print("=" * 60)
        print(table)
    else:
        logger.warning("  No returns available — performance metrics not computed")

    # 4. Write performance_summary.json
    def _to_python(v):
        """Convert numpy scalars to Python native for JSON."""
        if isinstance(v, (np.integer,)):
            return int(v)
        if isinstance(v, (np.floating,)):
            return float(v)
        if isinstance(v, (np.bool_,)):
            return bool(v)
        return v

    summary = {
        "metrics": {k: _to_python(v) for k, v in metrics.items() if k != 'name'},
        "portfolio": {
            "avg_active_positions": avg_active,
            "avg_active_weighted": avg_active_weighted,
            "annual_turnover": annual_turnover,
            "start_date": start_date,
            "end_date": end_date,
            "n_instruments": n_instruments,
            "n_days": n_days,
        },
        "cost_model": {
            "transaction_cost_ann": daily_cost_ann,
            "funding_drag_ann": funding_drag_ann,
        },
    }

    perf_path = output_path / 'performance_summary.json'
    with open(perf_path, 'w') as f:
        json.dump(summary, f, indent=2, default=str)
    logger.info(f"  ✓ {perf_path}")

    if daily_returns_dec is not None:
        returns_path = output_path / 'daily_returns.csv'
        daily_returns_dec.to_csv(returns_path, header=['net_return'])
        logger.info(f"  ✓ {returns_path}")


def run_backtest(
    config_path: str,
    data_path: str,
    output_dir: str,
    use_dynamic_universe: bool = True,
    macro_data_path: str = None,
    oi_data_path: str = None,
    sector_map_path: str = None,
    fg_data_path: str = None,
    mvrv_data_path: str = None,
    active_addresses_data_path: str = None,
    market_cap_data_path: str = None,
    hl_instruments_path: str = None,
    volume_data_path: str = None,
    capital_override: float = None,
    spread_multiplier: float = 1.0,
    use_vol_attenuation: bool = False,
):
    """
    Run pysystemtrade backtest with parquet-backed data adapter.

    Args:
        config_path: Path to YAML config file
        data_path: Path to parquet dataset
        output_dir: Output directory for results
        use_dynamic_universe: If True, use dynamic universe with cost filtering
    """
    logger.info("="*80)
    logger.info("DYNAMIC UNIVERSE BACKTEST")
    logger.info("="*80)

    # Create output directory
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    # Load config
    logger.info(f"Loading config: {config_path}")
    # Config class expects either a package path or a dict, not a file path string
    # Load YAML and pass as dict
    import yaml
    with open(config_path) as f:
        config_dict = yaml.safe_load(f)

    # Override notional_trading_capital with live equity if provided.
    # This ensures position sizing reflects actual account balance rather than
    # the static value in the config file.
    if capital_override is not None:
        config_dict['notional_trading_capital'] = capital_override
        logger.info(f"Capital override: notional_trading_capital set to ${capital_override:,.2f}")

    # Vol attenuation: inject use_attenuation list if not already in config.
    # Attenuate momentum/trend rules only — NOT carry, mean-reversion, or macro signals.
    if use_vol_attenuation and 'use_attenuation' not in config_dict:
        _ATTEN_RULES = [
            'ewmac_8', 'ewmac_16', 'ewmac_32',
            'breakout_20', 'breakout_40', 'breakout_80', 'breakout_160',
            'normmom_8', 'normmom_16', 'normmom_32',
            'accel_16', 'accel_32', 'accel_64',
            'assettrend_8', 'assettrend_16', 'assettrend_32', 'assettrend_64',
            'relmomentum_20', 'relmomentum_40',
            'residual_momentum_16', 'residual_momentum_32', 'residual_momentum_64',
            'round_number_break_10', 'round_number_break_20', 'round_number_break_40',
        ]
        # Only attenuate rules that are actually in the config's forecast_weights
        active_rules = set(config_dict.get('forecast_weights', {}).keys())
        config_dict['use_attenuation'] = [r for r in _ATTEN_RULES if r in active_rules]
        logger.info(f"  Vol attenuation enabled for {len(config_dict['use_attenuation'])} rules")

    config = Config(config_dict)

    # Extract dynamic universe config if enabled
    # Note: config.dynamic_universe is a plain dict (nested keys are not flattened
    # as attributes), so we use .get() rather than get_element_or_default().
    dynamic_universe_config = None
    if use_dynamic_universe:
        dynamic_universe_config = build_dynamic_universe_config(config_dict)
        logger.info(f"Dynamic universe enabled:")
        logger.info(f"  Max SR cost per trade: {dynamic_universe_config['max_sr_cost_per_trade']}")
        logger.info(f"  Max SR cost annual: {dynamic_universe_config['max_sr_cost_annual']}")
        logger.info(f"  Stack turnover: {dynamic_universe_config['stack_turnover']}")
        logger.info(f"  Vol window: {dynamic_universe_config['vol_window']}d")
        logger.info(f"  Min annual vol floor: {dynamic_universe_config['min_annual_vol']}")
        logger.info(f"  IVOL cap enabled: {dynamic_universe_config['ivol_cap_enabled']}")

    # Load data
    logger.info(f"Loading dataset: {data_path}")

    # Pass config_path and env_root for registry-aware candidate extraction
    # Determine env_root (use environment variable or current directory)
    import os
    env_root_str = os.environ.get('LIVE_OPS_ENV_ROOT')
    env_root = Path(env_root_str) if env_root_str else Path.cwd()

    # Auto-discover macro data if not explicitly provided
    if macro_data_path is None:
        candidate = Path(data_path).parent / 'macro_factors.parquet'
        if candidate.exists():
            macro_data_path = str(candidate)
            logger.info(f"Auto-discovered macro data: {macro_data_path}")
        else:
            logger.warning("macro_factors.parquet not found — residual_momentum rules will produce NaN forecasts")

    from syscore.constants import arg_not_supplied as _arg_not_supplied
    macro_kwarg = (
        macro_data_path if macro_data_path is not None else _arg_not_supplied
    )

    # Auto-discover volume data if not explicitly provided
    if volume_data_path is None:
        vol_candidate = Path(data_path).parent / 'binance_volume_daily.parquet'
        if vol_candidate.exists():
            volume_data_path = str(vol_candidate)
            logger.info(f"Auto-discovered volume data: {volume_data_path}")
        else:
            logger.info("binance_volume_daily.parquet not found — volume rules will produce empty forecasts")
    volume_kwarg = volume_data_path if volume_data_path is not None else _arg_not_supplied

    # Auto-discover OI data if not explicitly provided
    if oi_data_path is None:
        oi_candidate = Path(data_path).parent / 'binance_oi_processed.parquet'
        if oi_candidate.exists():
            oi_data_path = str(oi_candidate)
            logger.info(f"Auto-discovered OI data: {oi_data_path}")
        else:
            logger.info("binance_oi_processed.parquet not found — OI/LSR rules will produce empty forecasts")
    oi_kwarg = oi_data_path if oi_data_path is not None else _arg_not_supplied

    # Auto-discover sector map if not explicitly provided
    if sector_map_path is None:
        sector_candidate = Path(data_path).parent / 'sector_map.json'
        if sector_candidate.exists():
            sector_map_path = str(sector_candidate)
            logger.info(f"Auto-discovered sector map: {sector_candidate}")
        else:
            logger.info("sector_map.json not found — sector/inter-sector rules will produce NaN forecasts")
    sector_kwarg = sector_map_path if sector_map_path is not None else _arg_not_supplied

    # Auto-discover Fear & Greed index if not explicitly provided
    if fg_data_path is None:
        fg_candidate = Path(data_path).parent / 'fg_index.parquet'
        if fg_candidate.exists():
            fg_data_path = str(fg_candidate)
            logger.info(f"Auto-discovered F&G index: {fg_candidate}")
        else:
            logger.info("fg_index.parquet not found — F&G overlay disabled (run scripts/download_fg_index.py)")
    fg_kwarg = fg_data_path if fg_data_path is not None else _arg_not_supplied

    # Auto-discover MVRV index if not explicitly provided
    if mvrv_data_path is None:
        mvrv_candidate = Path(data_path).parent / 'mvrv_index.parquet'
        if mvrv_candidate.exists():
            mvrv_data_path = str(mvrv_candidate)
            logger.info(f"Auto-discovered MVRV index: {mvrv_candidate}")
        else:
            logger.info("mvrv_index.parquet not found — MVRV overlay disabled (run scripts/download_mvrv_index.py)")
    mvrv_kwarg = mvrv_data_path if mvrv_data_path is not None else _arg_not_supplied

    # Auto-discover active addresses data if not explicitly provided
    if active_addresses_data_path is None:
        aa_candidate = Path(data_path).parent / 'active_addresses.parquet'
        if aa_candidate.exists():
            active_addresses_data_path = str(aa_candidate)
            logger.info(f"Auto-discovered active addresses: {aa_candidate}")
        else:
            logger.info("active_addresses.parquet not found — XS activity rule will produce NaN forecasts")
    aa_kwarg = (
        active_addresses_data_path
        if active_addresses_data_path is not None
        else _arg_not_supplied
    )

    # Auto-discover market cap data if not explicitly provided
    if market_cap_data_path is None:
        mcap_candidate = Path(data_path).parent / 'market_cap.parquet'
        if mcap_candidate.exists():
            market_cap_data_path = str(mcap_candidate)
            logger.info(f"Auto-discovered market cap data: {mcap_candidate}")
        else:
            logger.info("market_cap.parquet not found — XS VAL rule will produce NaN forecasts")
    mcap_kwarg = (
        market_cap_data_path
        if market_cap_data_path is not None
        else _arg_not_supplied
    )

    if hl_instruments_path is None:
        hl_candidate = Path(data_path).parent / 'hyperliquid_instruments.json'
        if hl_candidate.exists():
            hl_instruments_path = str(hl_candidate)
            logger.info(f"Auto-discovered Hyperliquid instruments: {hl_candidate}")
    hl_kwarg = (
        hl_instruments_path
        if hl_instruments_path is not None
        else _arg_not_supplied
    )

    # Auto-discover stablecoin supply (DefiLlama, used by C2b stablecoin_supply_trend rule).
    # Lives in repo data/ rather than next to the dataset since it's a single global series.
    # Env-first lookup (mirrors the basis_index resolver above — same F2 fix class as
    # 833e7a37/f4cb4dcf): production daily_paper_run.py writes to envs/dev/data/.
    repo_root = Path(__file__).resolve().parent.parent
    stablecoin_candidate = Path(data_path).parent / 'stablecoin_supply.parquet'
    if not stablecoin_candidate.exists():
        stablecoin_candidate = repo_root / 'envs' / 'dev' / 'data' / 'stablecoin_supply.parquet'
    if not stablecoin_candidate.exists():
        stablecoin_candidate = repo_root / 'data' / 'stablecoin_supply.parquet'
    stablecoin_kwarg = (
        str(stablecoin_candidate) if stablecoin_candidate.exists() else _arg_not_supplied
    )
    if stablecoin_kwarg is not _arg_not_supplied:
        logger.info(f"Auto-discovered stablecoin supply: {stablecoin_candidate}")
    else:
        logger.info("stablecoin_supply.parquet not found — stablecoin_supply_trend rule will produce empty forecasts")

    # Auto-discover ETF flows (used by C2a btc_etf_flow_trend rule). Same env-first pattern.
    etf_candidate = Path(data_path).parent / 'etf_flows.parquet'
    if not etf_candidate.exists():
        etf_candidate = repo_root / 'envs' / 'dev' / 'data' / 'etf_flows.parquet'
    if not etf_candidate.exists():
        etf_candidate = repo_root / 'data' / 'etf_flows.parquet'
    etf_kwarg = str(etf_candidate) if etf_candidate.exists() else _arg_not_supplied
    if etf_kwarg is not _arg_not_supplied:
        logger.info(f"Auto-discovered ETF flows: {etf_candidate}")
    else:
        logger.info("etf_flows.parquet not found — etf_flow_trend rule will produce empty forecasts")

    # Auto-discover premium-index basis (used by C2c basis_mr rule).
    # Live env writes to envs/<env>/data; research uses repo data/.
    basis_candidate = Path(data_path).parent / 'binance_premium_index_processed.parquet'
    if not basis_candidate.exists():
        basis_candidate = repo_root / 'envs' / 'dev' / 'data' / 'binance_premium_index_processed.parquet'
    if not basis_candidate.exists():
        basis_candidate = repo_root / 'data' / 'binance_premium_index_processed.parquet'
    basis_kwarg = str(basis_candidate) if basis_candidate.exists() else _arg_not_supplied
    if basis_kwarg is not _arg_not_supplied:
        logger.info(f"Auto-discovered premium-index basis: {basis_candidate}")
    else:
        logger.info("binance_premium_index_processed.parquet not found — basis_mr rule will produce empty forecasts")

    data = parquetCryptoPerpsSimData(
        dataset_path=data_path,
        config_path=config_path,
        env_root=env_root,
        use_dynamic_universe=use_dynamic_universe,
        dynamic_universe_config=dynamic_universe_config,
        macro_data_path=macro_kwarg,
        oi_data_path=oi_kwarg,
        sector_map_path=sector_kwarg,
        fg_data_path=fg_kwarg,
        mvrv_data_path=mvrv_kwarg,
        active_addresses_data_path=aa_kwarg,
        market_cap_data_path=mcap_kwarg,
        hl_instruments_path=hl_kwarg,
        volume_data_path=volume_kwarg,
        stablecoin_supply_path=stablecoin_kwarg,
        etf_flows_path=etf_kwarg,
        premium_index_path=basis_kwarg,
    )

    data._spread_multiplier = spread_multiplier
    data._maker_frac = float(config_dict.get('maker_frac', 0.0))

    instruments = data.get_instrument_list()
    logger.info(f"  Loaded {len(instruments)} instruments")
    logger.info(f"  Sample: {instruments[:5]}")

    # Create system with dynamic portfolio
    logger.info("Creating system...")
    use_oi_overlay = config.get_element_or_default('use_oi_overlay', False)
    use_fg_overlay = config.get_element_or_default('use_fg_overlay', False)
    use_mvrv_overlay = config.get_element_or_default('use_mvrv_overlay', False)
    use_any_overlay = use_oi_overlay or use_fg_overlay or use_mvrv_overlay
    use_greedy_portfolio = config_dict.get('use_greedy_portfolio', False)

    if use_greedy_portfolio:
        from systems.crypto_perps.greedy_portfolio import MrGreedyPortfolio
        portfolio_stage = MrGreedyPortfolio()
        logger.info("  Using Mr Greedy portfolio (integer lot optimiser)")
    elif use_dynamic_universe:
        if use_any_overlay:
            portfolio_stage = CryptoDynamicPortfolioWithOIOverlay()
            overlay_desc = " + ".join(filter(None, [
                "OI" if use_oi_overlay else "",
                "F&G" if use_fg_overlay else "",
                "MVRV" if use_mvrv_overlay else "",
            ]))
            logger.info(f"  Using dynamic portfolio with overlay(s): {overlay_desc}")
        else:
            portfolio_stage = CryptoDynamicPortfolio()
            logger.info("  Using dynamic portfolio")
    else:
        if use_any_overlay:
            portfolio_stage = CryptoPortfolioWithOIOverlay()
            overlay_desc = " + ".join(filter(None, [
                "OI" if use_oi_overlay else "",
                "F&G" if use_fg_overlay else "",
                "MVRV" if use_mvrv_overlay else "",
            ]))
            logger.info(f"  Using static portfolio with overlay(s): {overlay_desc}")
        else:
            portfolio_stage = CryptoPortfolios()
            logger.info("  Using static portfolio")

    # Choose forecast combiner based on config
    use_gated_carry = config.get_element_or_default('use_gated_carry', False)
    use_coverage_aware_fdm = config.get_element_or_default('use_coverage_aware_fdm', False)
    if use_coverage_aware_fdm:
        from systems.crypto_perps.forecast_combine_coverage_aware import (
            ForecastCombineCoverageAware,
        )
        combiner = ForecastCombineCoverageAware()
        alpha = config.get_element_or_default('fdm_coverage_alpha', 0.0)
        logger.info(f"  Using coverage-aware FDM combination (alpha={alpha})")
    elif use_gated_carry:
        combiner = ForecastCombineGated()
        logger.info("  Using trend-gated carry combination")
    else:
        combiner = ForecastCombine()
        logger.info("  Using standard forecast combination")

    if use_vol_attenuation:
        from systems.crypto_perps.vol_atten_forecast_scale_cap import volAttenForecastScaleCap
        scale_cap_stage = volAttenForecastScaleCap()
        logger.info("  Using vol-attenuation ForecastScaleCap")
    else:
        scale_cap_stage = ForecastScaleCap()

    system = System(
        stage_list=[
            Account(),
            portfolio_stage,
            PositionSizing(),
            combiner,
            scale_cap_stage,
            Rules(),
            RawData(),
        ],
        data=data,
        config=config,
    )

    logger.info("✓ System created")

    # Run backtest by getting portfolio positions for all instruments
    logger.info("\nRunning backtest...")
    logger.info("  Getting optimal positions...")

    # Get optimal positions for all instruments (with capital multiplier applied)
    position_dict = {}
    for instrument in instruments:
        try:
            # get_actual_position() = get_notional_position() × capital_multiplier
            # (Note: "actual" is misleading - it doesn't apply buffering, just capital scaling)
            position = system.portfolio.get_actual_position(instrument)
            position_dict[instrument] = position
        except Exception as e:
            logger.warning(f"Could not get position for {instrument}: {e}")
            continue

    optimal_positions = pd.DataFrame(position_dict)

    if use_greedy_portfolio:
        # Greedy positions are in integer lots (lot_size_notional_override USD each).
        # Convert to base-asset units so _compute_calendar_pnl works correctly:
        #   base_asset = lots × lot_value / price
        # Forecast buffering is skipped — the greedy shadow cost already provides inertia.
        lot_value = float(config_dict.get('lot_size_notional_override', 1.0))
        logger.info(f"  Greedy: converting integer lots → base-asset units (lot_value=${lot_value})")
        prices_wide = data._prices_df.reindex(columns=optimal_positions.columns)
        prices_aligned = prices_wide.reindex(optimal_positions.index, method='ffill')
        # Avoid division by zero / NaN prices
        prices_safe = prices_aligned.replace(0, np.nan).ffill()
        portfolio_positions = optimal_positions * lot_value / prices_safe
        portfolio_positions = portfolio_positions.fillna(0.0)
        logger.info(f"  Greedy positions converted. Avg non-zero per day: "
                    f"{(portfolio_positions.abs() > 0).sum(axis=1).mean():.1f}")
    else:
        # Apply Carver forecast-method buffering.
        # buffer_width = vol_scalar × instrument_weight × IDM × buffer_size
        # buffer_size is read from config by pysystemtrade (default 0.10).
        logger.info("  Applying forecast-method buffering...")
        portfolio_positions = apply_forecast_buffering(
            system=system,
            optimal_positions=optimal_positions,
        )

    logger.info(f"  Backtest completed:")
    logger.info(f"    Date range: {portfolio_positions.index[0].date()} to {portfolio_positions.index[-1].date()}")
    logger.info(f"    N days: {len(portfolio_positions)}")
    logger.info(f"    Instruments: {len(portfolio_positions.columns)}")

    # Get instrument weights for diagnostic (greedy: derive from positions)
    if use_greedy_portfolio:
        weights = (portfolio_positions.abs() > 0).astype(float)
        weights = weights.div(weights.sum(axis=1).replace(0, np.nan), axis=0).fillna(0.0)
    else:
        weights = system.portfolio.get_instrument_weights()
    universe_size = (weights > 0).sum(axis=1)
    logger.info(f"    Universe size: min={universe_size.min():.0f}, max={universe_size.max():.0f}, avg={universe_size.mean():.1f}")

    # Write universe snapshot from Stage 2 selector output
    if use_dynamic_universe and not use_greedy_portfolio:
        _write_universe_snapshot(
            system=system,
            output_path=output_path,
            config_path=config_path,
        )

    # Write outputs
    logger.info("\nWriting outputs...")

    # 1. positions.csv (compatible with generate_trade_plan.py)
    positions_path = output_path / 'positions.csv'
    portfolio_positions.to_csv(positions_path)
    logger.info(f"  ✓ {positions_path}")

    # 1b. buffer_bounds_last.csv — last-day top_pos / bot_pos per instrument.
    # Used by trade_plan.py for the live Carver buffer check (trade to edge, not optimal).
    # Units: base-asset tokens (same as positions.csv). Trade plan multiplies by
    # last_prices.json to convert to USD, consistent with target position conversion.
    # @output() caching makes these re-calls essentially free.
    last_date = optimal_positions.index[-1]
    bounds_records = []
    for instrument in optimal_positions.columns:
        try:
            pos_buffers = system.portfolio.get_actual_buffers_for_position(instrument)
            if last_date in pos_buffers.index:
                bounds_records.append({
                    'instrument': instrument,
                    'top_pos': pos_buffers.loc[last_date, 'top_pos'],
                    'bot_pos': pos_buffers.loc[last_date, 'bot_pos'],
                })
        except Exception as e:
            logger.debug(f"No buffer bounds for {instrument}: {e}")
    if bounds_records:
        bounds_path = output_path / 'buffer_bounds_last.csv'
        pd.DataFrame(bounds_records).set_index('instrument').to_csv(bounds_path)
        logger.info(f"  ✓ {bounds_path} ({len(bounds_records)} instruments)")

    # 2. diagnostics.parquet (full system state)
    diagnostics_path = output_path / 'diagnostics.parquet'
    try:
        # Collect key diagnostic series
        diagnostics_data = []

        # IDM is portfolio-level (same for all instruments on a given date)
        try:
            _idm_raw = system.portfolio.get_instrument_diversification_multiplier()
            _idm_arr = _idm_raw.reindex(portfolio_positions.index, method='ffill').values
        except Exception:
            _idm_arr = np.full(len(portfolio_positions.index), np.nan)

        for instrument in portfolio_positions.columns:
            # Positions
            position = portfolio_positions[instrument]

            # Get forecasts if available
            try:
                combined_forecast = system.combForecast.get_combined_forecast(instrument)
            except:
                combined_forecast = pd.Series(np.nan, index=portfolio_positions.index)

            # Get instrument weight
            try:
                instrument_weight = weights[instrument]
            except:
                instrument_weight = pd.Series(np.nan, index=portfolio_positions.index)

            # Get FDM if available
            try:
                fdm_series = system.combForecast.get_forecast_diversification_multiplier(instrument)
                fdm_values = fdm_series.reindex(portfolio_positions.index, method='ffill').values
            except Exception:
                fdm_values = np.full(len(portfolio_positions.index), np.nan)

            # Build diagnostic row
            diag = pd.DataFrame({
                'date': portfolio_positions.index,
                'instrument': instrument,
                'position': position.values,
                'combined_forecast': combined_forecast.reindex(portfolio_positions.index).values,
                'instrument_weight': instrument_weight.reindex(portfolio_positions.index, method='ffill').values,
                'fdm': fdm_values,
                'idm': _idm_arr,
            })

            diagnostics_data.append(diag)

        diagnostics_df = pd.concat(diagnostics_data, ignore_index=True)
        diagnostics_df.to_parquet(diagnostics_path)
        logger.info(f"  ✓ {diagnostics_path}")

    except Exception as e:
        logger.warning(f"  Could not write diagnostics: {e}")

    # 3. metadata.json (run provenance)
    metadata = {
        'run_timestamp': datetime.utcnow().isoformat(),
        'system_type': 'dynamic_universe' if use_dynamic_universe else 'static_universe',
        'config_path': str(config_path),
        'data_path': str(data_path),
        'backtest_start_date': portfolio_positions.index[0].strftime('%Y-%m-%d'),
        'backtest_end_date': portfolio_positions.index[-1].strftime('%Y-%m-%d'),
        'n_days': len(portfolio_positions),
        'instruments': list(portfolio_positions.columns),
        'n_instruments': len(portfolio_positions.columns),
        'dynamic_universe_stats': {
            'min_active': int(universe_size.min()),
            'max_active': int(universe_size.max()),
            'avg_active': float(universe_size.mean()),
            'median_active': float(universe_size.median()),
        } if use_dynamic_universe else None,
        'dynamic_universe_config': dynamic_universe_config,
    }

    metadata_path = output_path / 'metadata.json'
    with open(metadata_path, 'w') as f:
        json.dump(metadata, f, indent=2)
    logger.info(f"  ✓ {metadata_path}")

    # 4. last_prices.json (most recent close price per instrument, for position valuation)
    last_prices = {}
    for instrument in portfolio_positions.columns:
        try:
            prices = system.rawdata.get_daily_prices(instrument).dropna()
            if len(prices) > 0:
                last_prices[instrument] = float(prices.iloc[-1])
        except Exception:
            pass
    last_prices_path = output_path / 'last_prices.json'
    with open(last_prices_path, 'w') as f:
        json.dump(last_prices, f, indent=2)
    logger.info(f"  ✓ {last_prices_path} ({len(last_prices)} instruments)")

    # 5. Performance metrics
    # fill_delay_days: shift positions forward N days to simulate limit-order fill delay.
    # Fractional values (e.g. 0.5) are rounded to nearest integer for the shift.
    fill_delay_days = int(round(float(config_dict.get('fill_delay_days', 0))))
    positions_for_metrics = portfolio_positions
    if fill_delay_days > 0:
        logger.info(f"  Fill delay: shifting positions by {fill_delay_days} day(s) (limit order simulation)")
        positions_for_metrics = portfolio_positions.shift(fill_delay_days)
    _compute_performance_metrics(
        system=system,
        portfolio_positions=positions_for_metrics,
        weights=weights,
        output_path=output_path,
        data=data,
    )

    logger.info("\n" + "="*80)
    logger.info("✓ BACKTEST COMPLETED SUCCESSFULLY")
    logger.info("="*80)

    return True


def main():
    parser = argparse.ArgumentParser(
        description='Run backtest with dynamic universe',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument(
        '--config',
        type=Path,
        required=True,
        help='Path to config YAML file'
    )
    parser.add_argument(
        '--data',
        type=Path,
        required=True,
        help='Path to parquet dataset'
    )
    parser.add_argument(
        '--outdir',
        type=Path,
        required=True,
        help='Output directory for results'
    )
    parser.add_argument(
        '--static-universe',
        action='store_true',
        help='Use static universe (disable dynamic cost filtering)'
    )
    parser.add_argument(
        '--macro-data',
        type=Path,
        default=None,
        help='Path to macro factors parquet (spx, dxy, us10y columns); required for residual_momentum rules'
    )
    parser.add_argument(
        '--oi-data',
        type=Path,
        default=None,
        help='Path to Binance OI/LSR processed parquet'
    )
    parser.add_argument(
        '--sector-map',
        type=Path,
        default=None,
        help='Path to sector_map.json'
    )
    parser.add_argument(
        '--fg-data',
        type=Path,
        default=None,
        help='Path to Fear & Greed parquet'
    )
    parser.add_argument(
        '--mvrv-data',
        type=Path,
        default=None,
        help='Path to MVRV parquet'
    )
    parser.add_argument(
        '--active-addresses-data',
        type=Path,
        default=None,
        help='Path to active addresses parquet'
    )
    parser.add_argument(
        '--market-cap-data',
        type=Path,
        default=None,
        help='Path to market cap parquet'
    )
    parser.add_argument(
        '--hl-instruments',
        type=Path,
        default=None,
        help='Path to hyperliquid_instruments.json'
    )
    parser.add_argument(
        '--volume-data',
        type=Path,
        default=None,
        help='Path to binance_volume_daily.parquet (auto-discovered if omitted)'
    )
    parser.add_argument(
        '--capital',
        type=float,
        default=None,
        help='Override notional_trading_capital with current account equity (USD). '
             'Use this in live advisory to ensure position sizing reflects actual balance.'
    )
    parser.add_argument(
        '--vol-atten',
        action='store_true',
        help='Enable volatility attenuation stage (volAttenForecastScaleCap). '
             'Multiplies momentum/trend rule forecasts by a vol-regime multiplier '
             '(0.5 to 2.0) based on where current vol sits in its 10-year history. '
             'Requires use_attenuation list in config or uses default momentum rules.'
    )
    parser.add_argument(
        '--run-id',
        type=str,
        default=None,
        help=(
            'Optional run_id (hex) to tag this stage in the manifest_chain. When '
            'invoked from run_live_advisory.py the orchestrator passes a shared '
            'run_id so all three stages group together. If omitted, a fresh run_id '
            'is generated for ad-hoc invocations.'
        ),
    )

    args = parser.parse_args()

    # Validate inputs
    if not args.config.exists():
        logger.error(f"Config file not found: {args.config}")
        sys.exit(1)

    if not args.data.exists():
        logger.error(f"Data file not found: {args.data}")
        sys.exit(1)

    # Verify upstream dataset hash matches the manifest_chain entry written by
    # run_live_advisory.py's dataset_build stage. Skip silently when the chain is
    # missing — this script is also invoked directly for ad-hoc research backtests
    # where there's no advisory chain to verify against.
    try:
        from sysdata.crypto.manifest_chain import (
            CHAIN_FILENAME,
            ManifestChainError,
            verify_input_against_upstream,
        )
        chain_path_check = args.data.parent / CHAIN_FILENAME
        if chain_path_check.exists():
            verify_input_against_upstream(
                chain_path_check,
                upstream_stage="dataset_build",
                output_name="dataset",
                current_path=args.data,
            )
            logger.info(f"manifest_chain: dataset hash verified against {chain_path_check}")
    except ManifestChainError as exc:
        logger.error(f"manifest_chain verification failed: {exc}")
        sys.exit(2)

    try:
        use_dynamic = not args.static_universe
        success = run_backtest(
            config_path=str(args.config),
            data_path=str(args.data),
            output_dir=str(args.outdir),
            use_dynamic_universe=use_dynamic,
            macro_data_path=str(args.macro_data) if args.macro_data else None,
            oi_data_path=str(args.oi_data) if args.oi_data else None,
            sector_map_path=str(args.sector_map) if args.sector_map else None,
            fg_data_path=str(args.fg_data) if args.fg_data else None,
            mvrv_data_path=str(args.mvrv_data) if args.mvrv_data else None,
            active_addresses_data_path=(
                str(args.active_addresses_data)
                if args.active_addresses_data
                else None
            ),
            market_cap_data_path=(
                str(args.market_cap_data)
                if args.market_cap_data
                else None
            ),
            hl_instruments_path=(
                str(args.hl_instruments) if args.hl_instruments else None
            ),
            volume_data_path=str(args.volume_data) if args.volume_data else None,
            capital_override=args.capital,
            use_vol_attenuation=args.vol_atten,
        )

        # Append the backtest stage to the manifest chain (when one exists upstream).
        if success:
            try:
                from sysdata.crypto.manifest_chain import CHAIN_FILENAME, append_stage
                chain_path_post = args.data.parent / CHAIN_FILENAME
                if chain_path_post.exists():
                    backtest_outputs: dict[str, Path] = {
                        "positions": args.outdir / "positions.csv",
                        "diagnostics": args.outdir / "diagnostics.parquet",
                        "metadata": args.outdir / "metadata.json",
                    }
                    append_stage(
                        chain_path_post,
                        stage="backtest",
                        inputs={"dataset": args.data},
                        outputs={
                            name: p for name, p in backtest_outputs.items() if p.exists()
                        },
                        extra={"config": str(args.config)},
                        run_id=args.run_id,
                    )
            except Exception as exc:  # never block the run on chain bookkeeping
                logger.warning(f"manifest_chain: post-backtest record failed: {exc}")

        sys.exit(0 if success else 1)

    except Exception as e:
        logger.error(f"Backtest failed: {e}", exc_info=True)
        sys.exit(1)


if __name__ == '__main__':
    main()
