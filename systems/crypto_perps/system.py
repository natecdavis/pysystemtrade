#!/usr/bin/env python3
"""
Crypto Perpetual Futures Trading System - Phase 1 MVP

Main orchestrator that runs the daily trading loop.

Usage:
    python -m systems.crypto_perps.system --config CONFIG --data DATA --outdir OUT

Example:
    python -m systems.crypto_perps.system \\
        --config config/crypto_perps.yaml \\
        --data data/example_crypto_perps.parquet \\
        --outdir out/crypto_perps
"""

import argparse
import sys
import yaml
import logging
from pathlib import Path
import pandas as pd
import numpy as np

# Data loading
from sysdata.crypto.prices import load_crypto_perps_panel

# System modules
from systems.crypto_perps.universe import (
    get_layer_a_instruments,
    build_eligibility_history,
    compute_daily_eligibility_df,
    build_instrument_states,
    InstrumentState,
    calculate_decay_target
)
from systems.crypto_perps.review_schedule import generate_review_dates, get_review_membership
from systems.crypto_perps.rules.ewmac import ewmac_forecasts
from systems.crypto_perps.rules.carry_funding import funding_carry_forecasts
from systems.crypto_perps.rules.relmom import relative_momentum_forecasts
from systems.crypto_perps.forecasts import process_all_forecasts
from systems.crypto_perps.sizing import calculate_target_weights, calculate_daily_volatility
from systems.crypto_perps.constraints import apply_portfolio_constraints
from systems.crypto_perps.execution import execute_trades, execute_trade_for_date
from systems.crypto_perps.accounting import calculate_cumulative_pnl
from systems.crypto_perps.diagnostics import DiagnosticsCollector
from systems.crypto_perps.metrics import calculate_metrics
from systems.crypto_perps.metadata import write_run_metadata
from systems.crypto_perps.config_validator import validate_config

# Version tracking
VERSION_FILE = Path(__file__).parent.parent.parent / 'VERSION'
SYSTEM_VERSION = VERSION_FILE.read_text().strip() if VERSION_FILE.exists() else 'unknown'

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def load_config(config_path: str) -> dict:
    """Load configuration from YAML file"""
    with open(config_path, 'r') as f:
        config = yaml.safe_load(f)
    return config


def run_backtest(config: dict, data_path: str, output_dir: str):
    """
    Run complete backtest

    Args:
        config: Configuration dict from YAML
        data_path: Path to data parquet file
        output_dir: Output directory for results
    """
    logger.info("=" * 80)
    logger.info("Crypto Perpetual Futures Trading System - Phase 1")
    logger.info("=" * 80)

    # Create output directory
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    # Extract config parameters
    capital = config['system']['capital']
    vol_target = config['system']['vol_target_ann']
    gross_lev_cap = config['system']['gross_leverage_cap']
    idm_cap = config['system']['idm_cap']
    min_pos_frac = config['system']['min_position_frac']
    min_adv = config['universe']['daily_min_adv_notional']
    ewmac_pairs = config['rules']['ewmac_pairs']
    carry_fast_hl = config['rules']['carry_fast_halflife']
    carry_slow_hl = config['rules']['carry_slow_halflife']
    buffer_frac = config['execution']['buffer_frac']
    corr_span = config['constraints']['correlation_span']
    corr_min_periods = config['constraints']['correlation_min_periods']

    # Phase 2 configuration (gated by review_freq)
    review_freq = config.get('universe', {}).get('review_freq')  # None = Phase 1
    phase2_enabled = bool(review_freq)  # Master Phase 2 flag

    if phase2_enabled:
        # Only extract Phase 2 params if enabled
        forced_exit_days = config.get('universe', {}).get('forced_exit_days', 5)
        min_adv_notional_review = config.get('universe', {}).get('min_adv_notional', 50000000.0)
        min_history_days = config.get('universe', {}).get('min_history_days', 365)
        data_gap_days = config.get('universe', {}).get('data_gap_days', 2)
        banned_instruments = config.get('universe', {}).get('banned_instruments', [])
    else:
        # Phase 1: defaults (won't be used, but define for safety)
        forced_exit_days = None
        min_adv_notional_review = None
        min_history_days = None
        data_gap_days = None
        banned_instruments = []

    # Relative momentum (independent of review schedule)
    use_relmom = config.get('forecasts', {}).get('use_relative_momentum', False)
    relmom_horizon = config.get('forecasts', {}).get('relmom', {}).get('horizon', 20)
    relmom_ewma_span = config.get('forecasts', {}).get('relmom', {}).get('ewma_span', 60)

    logger.info(f"Starting capital: ${capital:,.2f}")
    logger.info(f"Vol target: {vol_target:.1%}")
    logger.info(f"Gross leverage cap: {gross_lev_cap}")
    logger.info(f"IDM cap: {idm_cap}")

    # Initialize diagnostics collector (optional)
    diagnostics_enabled = config.get('diagnostics', {}).get('enabled', False)
    collector = DiagnosticsCollector() if diagnostics_enabled else None
    if diagnostics_enabled:
        logger.info("Diagnostics collection: ENABLED")

    # Step 1: Load data
    logger.info("\nStep 1: Loading data...")
    # Check if config specifies jagged panel support
    allow_jagged = config.get('system', {}).get('allow_jagged', False)
    prices_df, meta_df, lifecycle_df = load_crypto_perps_panel(data_path, allow_jagged=allow_jagged)
    logger.info(f"  Loaded data: {len(prices_df)} days, {len(prices_df.columns)} instruments")
    logger.info(f"  Date range: {prices_df.index[0].date()} to {prices_df.index[-1].date()}")
    if lifecycle_df is not None:
        logger.info(f"  Lifecycle metadata loaded for {len(lifecycle_df)} instruments")
    else:
        logger.info("  No lifecycle metadata (rectangular panel mode)")

    # Step 2: Building universe and eligibility
    logger.info("\nStep 2: Building universe and eligibility...")

    # Phase 2: Monthly reviews with frozen membership
    if phase2_enabled:
        logger.info(f"  Phase 2: Monthly reviews (freq={review_freq})")

        # Generate review dates
        review_dates = generate_review_dates(
            start_date=prices_df.index[0],
            end_date=prices_df.index[-1],
            freq=review_freq
        )

        # Build membership_by_date (frozen between reviews)
        membership_by_date = {}
        for date in prices_df.index:
            frozen_layer_a, last_review = get_review_membership(
                date=date,
                review_dates=review_dates,
                prices_df=prices_df,
                meta_df=meta_df,
                min_adv_notional=min_adv_notional_review,
                min_history_days=min_history_days
            )
            membership_by_date[pd.Timestamp(date)] = frozen_layer_a

        # For logging: layer_a on first date
        layer_a = membership_by_date[pd.Timestamp(prices_df.index[0])]
    else:
        # Phase 1: Static Layer A
        logger.info("  Phase 1: Static Layer A")
        # Read layer_a_instruments from config, fallback to default
        layer_a = config.get('universe', {}).get('layer_a_instruments', get_layer_a_instruments())
        membership_by_date = {pd.Timestamp(date): layer_a for date in prices_df.index}

    logger.info(f"  Layer A instruments (start): {layer_a}")

    # Daily eligibility filter
    # For Phase 2, compute eligibility for ALL instruments in universe pool (not just initial Layer-A)
    universe_pool = config.get('universe', {}).get('layer_a_instruments', get_layer_a_instruments())
    eligibility_df = build_eligibility_history(prices_df, meta_df, min_adv, layer_a_instruments=universe_pool)
    eligible_pct = eligibility_df.mean() * 100
    for inst in layer_a:
        logger.info(f"  {inst}: {eligible_pct[inst]:.1f}% eligible days")

    # Step 3: Calculate forecasts
    logger.info("\nStep 3: Calculating forecasts...")
    logger.info(f"  EWMAC pairs: {ewmac_pairs}")
    ewmac = ewmac_forecasts(prices_df, ewmac_pairs)

    logger.info(f"  Carry: fast_hl={carry_fast_hl}, slow_hl={carry_slow_hl}")
    carry = funding_carry_forecasts(meta_df, carry_fast_hl, carry_slow_hl)

    # Relative momentum (if enabled, independent of review schedule)
    if use_relmom:
        logger.info(f"  Relative momentum: horizon={relmom_horizon}, ewma_span={relmom_ewma_span}")
        relmom = relative_momentum_forecasts(
            prices_df=prices_df,
            membership_by_date=membership_by_date,
            horizon=relmom_horizon,
            ewma_span=relmom_ewma_span
        )
    else:
        relmom = None

    logger.info("  Scaling and combining forecasts...")
    rule_weights = config.get('forecasts', {}).get('rule_weights')
    combined_forecasts = process_all_forecasts(ewmac, carry, relmom_forecasts=relmom, rule_weights=rule_weights)

    # Validate forecast caps
    for inst, forecast in combined_forecasts.items():
        max_abs = forecast.abs().max()
        logger.info(f"  {inst}: max |forecast| = {max_abs:.2f}")

    # Hook: Record forecasts (if diagnostics enabled) - ONCE before daily loop
    if collector:
        for date in prices_df.index:
            for inst in combined_forecasts.keys():
                # Check if instrument has data on this date (for jagged panels)
                if date not in combined_forecasts[inst].index:
                    continue

                # Build per-rule forecasts dict dynamically
                per_rule = {}
                for rule_name in ewmac.get(inst, {}).keys():
                    if inst in ewmac and rule_name in ewmac[inst] and date in ewmac[inst][rule_name].index:
                        per_rule[rule_name] = ewmac[inst][rule_name].loc[date]
                if inst in carry and date in carry[inst].index:
                    per_rule['carry_funding'] = carry[inst].loc[date]
                # Phase 2: Add relmom forecast
                if use_relmom and inst in relmom and date in relmom[inst].index:
                    per_rule['relative_momentum'] = relmom[inst].loc[date]

                collector.record_forecasts(
                    date=date,
                    instrument=inst,
                    forecast_combined=combined_forecasts[inst].loc[date],
                    **per_rule
                )

    # Step 4: Build instrument states (Phase 2 pre-computation)
    logger.info("\nStep 4: Building instrument states...")

    if phase2_enabled:
        # Compute daily eligibility for state machine
        daily_eligibility = compute_daily_eligibility_df(
            prices_df=prices_df,
            meta_df=meta_df,
            instruments=list(prices_df.columns),
            daily_min_adv_notional=min_adv,
            data_gap_days=data_gap_days
        )

        # Build base state machine (ACTIVE ↔ INELIGIBLE_HOLD based on eligibility)
        state_df, days_in_state_df = build_instrument_states(
            dates=prices_df.index,
            instruments=list(prices_df.columns),
            eligibility_df=daily_eligibility,
            banned_instruments=banned_instruments,
            lifecycle_df=lifecycle_df,
            prices_df=prices_df,
            meta_df=meta_df,
            min_adv_notional=min_adv
        )

        # Enforce membership precedence (CRITICAL: order matters)
        logger.info("  Enforcing membership precedence:")
        for date in state_df.index:
            layer_a_today = membership_by_date[pd.Timestamp(date)]
            for inst in state_df.columns:
                current_state = state_df.loc[date, inst]

                # Precedence 1: Explicit ban dominates everything
                if inst in banned_instruments:
                    state_df.loc[date, inst] = InstrumentState.BANNED_FLATTEN.value
                    continue  # Skip other checks

                # Precedence 2: Non-membership forces INELIGIBLE_HOLD
                if inst not in layer_a_today:
                    # Only override if not already BANNED_FLATTEN
                    if current_state != InstrumentState.BANNED_FLATTEN.value:
                        state_df.loc[date, inst] = InstrumentState.INELIGIBLE_HOLD.value
                    continue  # Skip eligibility check

                # Precedence 3: Eligibility controls ACTIVE ↔ INELIGIBLE_HOLD
                # (Already handled by build_instrument_states, no override needed)

        # Log state distribution
        logger.info("  Instrument states built:")
        for inst in state_df.columns:
            state_counts = state_df[inst].value_counts().to_dict()
            if len(state_counts) > 1 or InstrumentState.ACTIVE.value not in state_counts:
                logger.info(f"    {inst}: {state_counts}")
    else:
        # Phase 1: All ACTIVE
        state_df = pd.DataFrame(
            InstrumentState.ACTIVE.value,  # Use enum.value for consistency
            index=prices_df.index,
            columns=prices_df.columns
        )
        days_in_state_df = pd.DataFrame(
            0,
            index=prices_df.index,
            columns=prices_df.columns
        )
        logger.info("  Phase 1: All instruments ACTIVE")

    # Step 5: Size positions (vectorized across all dates)
    logger.info("\nStep 5: Sizing positions...")
    weights_df, notionals_df = calculate_target_weights(
        forecasts=combined_forecasts,
        prices_df=prices_df,
        capital=capital,
        vol_target_ann=vol_target,
        min_position_frac=min_pos_frac
    )

    # Calculate volatilities (needed for execution buffers)
    daily_vols_df = pd.DataFrame(
        {inst: calculate_daily_volatility(prices_df[inst])
         for inst in prices_df.columns}
    )

    # Step 6: Daily loop (exit rules → constraints → execution)
    logger.info("\nStep 6: Running daily loop (exit rules, constraints, execution)...")

    # Initialize output containers
    dates = prices_df.index
    instruments = list(prices_df.columns)

    # Step 5.5: Initialize incremental constraints engine (after instruments defined)
    logger.info("\nStep 5.5: Initializing incremental constraints engine...")

    from systems.crypto_perps.constraints import (
        IncrementalConstraintsEngine,
        get_constraints_config,
        compute_returns
    )

    # Get constraints config and extract use_recursive flag
    cfg = get_constraints_config()
    use_recursive = cfg.pop("use_recursive")  # Not passed to engine

    # Pre-compute returns ONCE (not in loop - O(T·N) total, not O(T²·N))
    returns_df = compute_returns(prices_df, method=cfg["returns"])

    constraints_engine = IncrementalConstraintsEngine(
        instruments=instruments,
        span=corr_span,
        min_periods=corr_min_periods,
        idm_cap=idm_cap,
        gross_leverage_cap=gross_lev_cap,
        **cfg  # adjust, demean, idm_pre_cap, returns
    )

    logger.info(f"  Engine initialized: span={corr_span}, min_periods={corr_min_periods}")
    logger.info(f"  EWMA parameters: adjust={cfg['adjust']}, demean={cfg['demean']}, returns={cfg['returns']}")

    # Track current holdings (built sequentially)
    current_weights = {inst: 0.0 for inst in instruments}  # Start at zero

    # Output DataFrames (populate per-date)
    weights_after_exits_df = pd.DataFrame(index=dates, columns=instruments, dtype=float)
    constrained_weights_df = pd.DataFrame(index=dates, columns=instruments, dtype=float)
    trades_df = pd.DataFrame(index=dates, columns=instruments, dtype=float)
    costs_df = pd.DataFrame(index=dates, columns=instruments, dtype=float)
    gross_lev_series = pd.Series(index=dates, dtype=float)
    idm_series = pd.Series(index=dates, dtype=float)
    overall_scalars = pd.Series(index=dates, dtype=float)

    # Entry weights log (for diagnostics)
    entry_weights_log = {inst: {} for inst in instruments}

    # Exit rule state tracking (persistent across loop)
    exit_entry_weights = {inst: None for inst in instruments}

    # Daily loop
    for i, date in enumerate(dates):
        # 6a. Apply exit rules (per-date)
        if phase2_enabled:
            # Extract data for this date
            unconstrained_weights_today = weights_df.loc[date].to_dict()
            state_today = state_df.loc[date].to_dict()
            days_in_state_today = days_in_state_df.loc[date].to_dict()

            weights_after_exits_today = {}

            for inst in instruments:
                state = state_today[inst]
                days = days_in_state_today[inst]
                unconstrained = unconstrained_weights_today[inst]

                if state == InstrumentState.BANNED_FLATTEN.value:
                    # Immediate flatten
                    weights_after_exits_today[inst] = 0.0
                    exit_entry_weights[inst] = None  # Clear entry weight

                elif state == InstrumentState.INELIGIBLE_HOLD.value:
                    # Record entry weight on first day
                    if days == 0:
                        exit_entry_weights[inst] = current_weights[inst]

                    # Compute decay target
                    entry_weight = exit_entry_weights[inst] if exit_entry_weights[inst] is not None else 0.0
                    decay_target = calculate_decay_target(
                        entry_weight=entry_weight,
                        days_in_state=days,
                        total_days=forced_exit_days
                    )
                    weights_after_exits_today[inst] = decay_target

                    # Log for diagnostics
                    entry_weights_log[inst][date] = entry_weight

                else:  # ACTIVE
                    # No exit modification
                    weights_after_exits_today[inst] = unconstrained
                    exit_entry_weights[inst] = None  # Clear entry weight

            # Store in DataFrame
            weights_after_exits_df.loc[date] = pd.Series(weights_after_exits_today)

        else:
            # Phase 1: No exit rules
            weights_after_exits_df.loc[date] = weights_df.loc[date]

        # 6b. Apply constraints with Carver-style IDM multiplier (INCREMENTAL, O(N²) per day)
        # Get returns for this date (pre-computed outside loop)
        returns_today = returns_df.loc[date].to_dict()

        # Get weights after exit rules (these are "base_weights" semantically)
        weights_after_exits_today = weights_after_exits_df.loc[date].to_dict()

        # Apply constraints incrementally (IDM multiplier + gross lev cap)
        # Input: base_weights (from forecasts + vol targeting, before diversification benefit)
        # Output: constrained_weights (after IDM multiplier + gross lev cap)
        constrained_weights_today, gross_lev_val, idm_val, constraint_diag = constraints_engine.step(
            date=date,
            returns=returns_today,
            weights=weights_after_exits_today,
            return_diagnostics=True  # Get detailed constraint diagnostics
        )

        # Store results
        constrained_weights_df.loc[date] = pd.Series(constrained_weights_today)
        gross_lev_series.loc[date] = gross_lev_val
        idm_series.loc[date] = idm_val

        # Invariant checks (with small epsilon for numerical precision)
        eps = 0.01

        # Invariant 1: IDM ≥ 1.0 (Carver-style)
        if not (idm_val >= 1.0 - eps):
            raise ValueError(
                f"Date {date}: IDM={idm_val:.3f} should be >= 1.0 (Carver-style normalization). "
                f"This indicates a bug in IDM calculation."
            )

        # Invariant 2: Gross leverage respects cap
        if not (gross_lev_val <= gross_lev_cap + eps):
            raise ValueError(
                f"Date {date}: gross_leverage={gross_lev_val:.3f} exceeds cap {gross_lev_cap}. "
                f"This indicates a bug in position constraints."
            )

        # Invariant 3: idm_applied ≤ cap (if available in diagnostics)
        if constraint_diag and 'idm_applied' in constraint_diag:
            if not (constraint_diag['idm_applied'] <= constraint_diag['idm_cap'] + eps):
                raise ValueError(
                    f"Date {date}: idm_applied={constraint_diag['idm_applied']:.3f} exceeds cap {constraint_diag['idm_cap']}. "
                    f"This indicates a bug in IDM capping logic."
                )

        # Invariant 4: idm_applied ≥ 1.0 (multiplier should increase or maintain leverage)
        if constraint_diag and 'idm_applied' in constraint_diag:
            if not (constraint_diag['idm_applied'] >= 1.0 - eps):
                raise ValueError(
                    f"Date {date}: idm_applied={constraint_diag['idm_applied']:.3f} should be >= 1.0. "
                    f"This indicates a bug in IDM calculation."
                )

        # Compute overall scalar (for diagnostics/logging)
        scalar = constraint_diag.get('overall_scalar_from_base', 1.0) if constraint_diag else 1.0
        overall_scalars.loc[date] = scalar

        # 6c. Execute trades (per-date) using execution.py helper
        # Build metadata dict for cost calculation
        meta_today = {}
        for inst in instruments:
            try:
                inst_meta = meta_df.loc[(date, inst)]
                meta_today[inst] = {
                    'spread_frac': inst_meta['spread_frac'],
                    'taker_fee_frac': inst_meta['taker_fee_frac']
                }
            except KeyError:
                meta_today[inst] = {
                    'spread_frac': 0.0003,
                    'taker_fee_frac': 0.0004
                }

        # Call execute_trade_for_date() helper (ensures baseline equivalence)
        trades_today, costs_today = execute_trade_for_date(
            target_weights=constrained_weights_today,
            current_weights=current_weights.copy(),  # Pass as dict
            prices=prices_df.loc[date].to_dict(),
            meta=meta_today,
            eligible=eligibility_df.loc[date].to_dict(),
            daily_vols=daily_vols_df.loc[date].to_dict(),
            capital=capital,
            buffer_frac=buffer_frac,
            state=state_df.loc[date].to_dict() if phase2_enabled else None
        )

        # Store trades and costs
        trades_df.loc[date] = pd.Series(trades_today)
        costs_df.loc[date] = pd.Series(costs_today)

        # 6d. Update current_weights for next iteration
        for inst in instruments:
            current_weights[inst] += trades_today[inst]

        # 6e. Record diagnostics (ONLY for current date, avoid O(T×N))
        if collector:
            layer_a_today = membership_by_date.get(pd.Timestamp(date), layer_a)

            for inst in instruments:
                # State
                ban_source = None
                state_val = state_df.loc[date, inst]
                if state_val == InstrumentState.BANNED_FLATTEN.value:
                    if inst in banned_instruments:
                        ban_source = 'explicit'
                    elif inst not in layer_a_today:
                        ban_source = 'membership'

                entry_weight = entry_weights_log[inst].get(date, np.nan)

                collector.record_state(
                    date=date,
                    instrument=inst,
                    state=state_val,
                    in_layer_a=(inst in layer_a_today),
                    eligible=eligibility_df.loc[date, inst],
                    days_in_state=int(days_in_state_df.loc[date, inst]),
                    entry_weight=entry_weight,
                    ban_source=ban_source
                )

                # Weights
                collector.record_weights(
                    date=date,
                    instrument=inst,
                    unconstrained=weights_df.loc[date, inst],
                    after_exits=weights_after_exits_df.loc[date, inst],
                    constrained=constrained_weights_df.loc[date, inst],
                    current=current_weights[inst]
                )

                # Constraints
                collector.record_constraints(
                    date=date,
                    instrument=inst,
                    gross_lev=gross_lev_series.loc[date],
                    idm=idm_series.loc[date],
                    overall_scalar=overall_scalars.loc[date]
                )

                # Trades
                trade_weight = trades_df.loc[date, inst]
                if abs(trade_weight) > 1e-10:
                    reason = 'buffer_trade'
                else:
                    reason = 'buffer_no_trade'

                collector.record_trade(
                    date=date,
                    instrument=inst,
                    trade=trade_weight,
                    reason=reason,
                    buffer_threshold=np.nan
                )

    logger.info(f"  Daily loop complete: {len(dates)} dates processed")
    if phase2_enabled:
        exit_modifications = (weights_after_exits_df != weights_df).sum().sum()
        logger.info(f"  Exit rules modified {exit_modifications} weight cells")

    logger.info(f"  Gross leverage: mean={gross_lev_series.mean():.2f}, max={gross_lev_series.max():.2f}")
    logger.info(f"  IDM: mean={idm_series.mean():.2f}, max={idm_series.max():.2f}")

    # Build current_positions DataFrame from current_weights path
    current_positions = pd.DataFrame(index=dates, columns=instruments, dtype=float)
    for i, date in enumerate(dates):
        if i == 0:
            # Day 0: trades from zero
            current_positions.loc[date] = trades_df.loc[date] * capital
        else:
            # Day t: previous position + trades
            current_positions.loc[date] = current_positions.iloc[i-1] + trades_df.loc[date] * capital

    total_costs = costs_df.sum().sum()
    logger.info(f"  Total trading costs: ${total_costs:.2f}")

    # Step 7: Calculate PnL and equity curve
    logger.info("\nStep 7: Calculating PnL and equity curve...")
    price_pnl_df, funding_pnl_df, total_pnl_df, equity_curve = calculate_cumulative_pnl(
        positions_df=current_positions,
        prices_df=prices_df,
        meta_df=meta_df,
        costs_df=costs_df,
        initial_capital=capital
    )

    final_equity = equity_curve.iloc[-1]
    total_return = (final_equity - capital) / capital
    total_pnl = final_equity - capital

    logger.info(f"  Starting equity: ${capital:,.2f}")
    logger.info(f"  Final equity: ${final_equity:,.2f}")
    logger.info(f"  Total return: {total_return:+.2%}")
    logger.info(f"  Total PnL: ${total_pnl:+,.2f}")

    # Hook: Record PnL (if diagnostics enabled)
    if collector:
        for date in price_pnl_df.index:
            for inst in price_pnl_df.columns:
                collector.record_pnl(
                    date=date,
                    instrument=inst,
                    pnl_price=price_pnl_df.loc[date, inst],
                    pnl_funding=funding_pnl_df.loc[date, inst],
                    pnl_costs=costs_df.loc[date, inst]
                )

    # Step 8: Write outputs
    logger.info("\nStep 8: Writing outputs...")

    # Equity curve
    equity_file = output_path / config['output']['equity_curve_file']
    equity_curve.to_csv(equity_file, header=['equity'])
    logger.info(f"  Saved equity curve: {equity_file}")

    # Positions
    positions_file = output_path / config['output']['positions_file']
    current_positions.to_csv(positions_file)
    logger.info(f"  Saved positions: {positions_file}")

    # PnL breakdown (optional detailed output)
    pnl_breakdown_file = output_path / config['output']['pnl_breakdown_file']
    pnl_breakdown = pd.DataFrame({
        'total_pnl': total_pnl_df.sum(axis=1),
        'price_pnl': price_pnl_df.sum(axis=1),
        'funding_pnl': funding_pnl_df.sum(axis=1),
        'costs': costs_df.sum(axis=1),
        'equity': equity_curve
    })
    pnl_breakdown.to_csv(pnl_breakdown_file)
    logger.info(f"  Saved PnL breakdown: {pnl_breakdown_file}")

    # Write diagnostics (if enabled)
    if collector:
        diagnostics_file = output_path / 'diagnostics.parquet'
        collector.write_parquet(diagnostics_file)
        logger.info(f"  Saved diagnostics: {diagnostics_file}")

        # Extract diagnostics DataFrame for additional outputs
        diagnostics_df = collector.get_dataframe()

        # Write Layer-A membership history (Phase 2 only)
        if review_freq is not None:
            logger.info("  Writing Layer-A membership history...")

            # Build membership DataFrame
            membership_records = []
            for date in dates:
                # Get Layer-A members for this date from diagnostics
                day_diag = diagnostics_df[diagnostics_df['date'] == date]

                if len(day_diag) > 0:
                    # Get unique list of instruments that were in Layer-A that day
                    layer_a_members = day_diag[
                        day_diag['in_layer_a'] == True
                    ]['instrument'].unique().tolist()

                    membership_records.append({
                        'date': date,
                        'layer_a_size': len(layer_a_members),
                        'layer_a_members': ','.join(sorted(layer_a_members))
                    })

            membership_df = pd.DataFrame(membership_records)
            membership_path = output_path / 'layer_a_membership.csv'
            membership_df.to_csv(membership_path, index=False)
            logger.info(f"    Saved Layer-A membership: {membership_path}")

        # Write IDM history
        logger.info("  Writing IDM time series...")

        # Extract IDM from diagnostics
        idm_records = []
        for date in dates:
            day_diag = diagnostics_df[diagnostics_df['date'] == date]

            if len(day_diag) > 0:
                # IDM is same for all instruments on a given date
                idm_val = day_diag['idm'].iloc[0]

                # Get number of active instruments
                n_active = len(day_diag[day_diag['state'] == 'ACTIVE'])

                idm_records.append({
                    'date': date,
                    'idm': idm_val,
                    'n_active_instruments': n_active
                })

        idm_df = pd.DataFrame(idm_records)
        idm_path = output_path / 'idm_history.csv'
        idm_df.to_csv(idm_path, index=False)
        logger.info(f"    Saved IDM history: {idm_path}")

    # Calculate final metrics and write metadata
    logger.info("\nCalculating metrics and writing metadata...")

    # Note: overall_scalars already calculated in daily loop

    final_metrics = calculate_metrics(
        equity_curve=equity_curve,
        weights_df=constrained_weights_df,
        trades_df=trades_df,
        capital=capital,
        state_df=state_df,  # Phase 2: actual state machine (not None)
        constraint_scalars=overall_scalars
    )

    write_run_metadata(
        outdir=output_path,
        config=config,
        data_path=Path(data_path),
        metrics=final_metrics
    )
    logger.info(f"  Saved metadata: {output_path / 'metadata.json'}")

    logger.info("\n" + "=" * 80)
    logger.info("Backtest complete!")
    logger.info("=" * 80)

    # Return dict of computed objects (for ablation runner and metrics calculation)
    return {
        'equity_curve': equity_curve,
        'weights_df': constrained_weights_df,
        'trades_df': trades_df,
        'state_df': state_df,  # Phase 2: actual state machine (not None)
        'pnl_price_df': price_pnl_df,
        'pnl_funding_df': funding_pnl_df,
        'costs_df': costs_df,
        'gross_leverage_series': gross_lev_series,
        'idm_series': idm_series
    }


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description='Crypto Perpetual Futures Trading System - Phase 1'
    )
    parser.add_argument(
        '--config',
        required=True,
        help='Path to config YAML file (e.g., config/crypto_perps.yaml)'
    )
    parser.add_argument(
        '--data',
        required=True,
        help='Path to data parquet file (e.g., data/example_crypto_perps.parquet)'
    )
    parser.add_argument(
        '--outdir',
        required=True,
        help='Output directory for results (e.g., out/crypto_perps)'
    )

    args = parser.parse_args()

    # Load config
    config = load_config(args.config)

    # Validate config
    config_errors = validate_config(config)
    if config_errors:
        logger.error("Config validation failed:")
        for error in config_errors:
            logger.error(f"  - {error}")
        sys.exit(1)

    logger.info(f"Config validated successfully")

    # Run backtest
    run_backtest(config, args.data, args.outdir)


if __name__ == '__main__':
    main()
