#!/usr/bin/env python3
"""
Generate trade plan by comparing backtest targets to actual positions.

This script is the core of the live advisory system, generating actionable
trade recommendations with risk checks and audit trails.

Usage:
    python scripts/generate_trade_plan.py \
        --backtest-dir out/live_advisory_20260128/backtest_latest \
        --actual-positions live/current_positions.csv \
        --current-equity 5125.50 \
        --as-of-date 2026-01-28 \
        --output-dir out/live_advisory_20260128
"""

import argparse
import sys
from pathlib import Path
import pandas as pd
import yaml
import json
import logging

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from systems.crypto_perps.trade_plan import generate_trade_plan

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def load_universe_snapshot(snapshot_path):
    """
    Load a universe_snapshot.json file.

    Returns:
        dict with snapshot data, or None if path is None/missing/empty.
    """
    if not snapshot_path:
        return None
    p = Path(snapshot_path)
    if not p.exists():
        return None
    with open(p) as f:
        return json.load(f)


def load_data_status_instruments(data_status_path):
    """
    Load per-instrument data from raw_data_status.json.

    Returns:
        dict of {instrument: {api_staleness_days: N, ...}}, or {} if unavailable.
    """
    if not data_status_path:
        return {}
    p = Path(data_status_path)
    if not p.exists():
        return {}
    with open(p) as f:
        status = json.load(f)
    return status.get('instruments', {})


def load_delisted_from_registry_changelog(changelog_path):
    """
    Extract delisted instruments from a registry changelog JSON file.

    Returns:
        list of delisted instrument symbols, or [] if unavailable.
    """
    if not changelog_path:
        return []
    p = Path(changelog_path)
    if not p.exists():
        return []
    with open(p) as f:
        changelog = json.load(f)
    return changelog.get('delisted_instruments', [])


def compute_shadow_targets(reduce_only_instruments, backtest_dir, prev_backtest_dir=None, log=None):
    """
    For reduce_only instruments where the current backtest target is 0 (config-driven),
    compute a 'shadow target' = what the position would be if the instrument were sized
    like a normal active instrument at the current forecast.

    Method: infer the current sizing factor from active instruments on the last backtest date:
        sizing_factor = mean(|position| / |forecast|) across instruments with weight > 0, |fc| > 1
        shadow_target = sizing_factor × current_forecast

    This is equivalent to applying the full position sizing formula (capital × IDM × weight ×
    target_vol / inst_vol × forecast/10) with current parameters, without needing to recompute
    each term individually.

    Returns:
        dict {instrument: shadow_target_notional}
    """
    import numpy as np

    shadow_targets = {}
    if not reduce_only_instruments:
        return shadow_targets

    diag_path = Path(backtest_dir) / 'diagnostics.parquet'
    if not diag_path.exists():
        if log:
            log.warning(f"No diagnostics.parquet in {backtest_dir} — shadow targets unavailable")
        return shadow_targets

    try:
        diag = pd.read_parquet(diag_path)
    except Exception as e:
        if log:
            log.warning(f"Could not load diagnostics for shadow targets: {e}")
        return shadow_targets

    # Last date in the backtest
    last_date = diag['date'].max()
    today_rows = diag[diag['date'] == last_date]

    # Sizing factor: median |position| / |forecast| across well-sized active instruments today.
    # instrument_weight is NaN on non-rebalance dates; use non-zero position as proxy for active.
    # Filter out lot-size-capped positions (|pos| < $5) and clear outliers (ratio > 10x median)
    # to get a clean estimate of the current $/forecast-unit for a normal 1/K-weight instrument.
    active = today_rows[
        (today_rows['position'].abs() > 5.0) &   # exclude lot-size-capped tiny positions
        (today_rows['combined_forecast'].abs() > 1)
    ].copy()

    if active.empty:
        if log:
            log.warning("Shadow targets: no active instruments on last backtest date — unavailable")
        return shadow_targets

    sizing_factors = active['position'].abs() / active['combined_forecast'].abs()
    # Remove outliers > 3× median (legacy over-sized zombie positions)
    raw_median = float(sizing_factors.median())
    sizing_factors = sizing_factors[sizing_factors <= raw_median * 3]
    sizing_factor = float(sizing_factors.median())

    if log:
        log.info(
            f"Shadow target sizing factor: median |pos|/|fc| = {sizing_factor:.2f} "
            f"across {len(active)} active instruments on {last_date}"
        )

    for inst in reduce_only_instruments:
        inst_rows = diag[diag['instrument'] == inst].sort_values('date')

        # Current forecast = last row's combined_forecast (instrument may have weight=0)
        if inst_rows.empty:
            if log:
                log.debug(f"Shadow target: {inst} not in diagnostics")
            continue

        cf_val = inst_rows.iloc[-1].get('combined_forecast')
        current_forecast = float(cf_val) if cf_val is not None and not pd.isna(cf_val) else None

        if current_forecast is None or abs(current_forecast) < 0.1:
            if log:
                log.debug(f"Shadow target: no current forecast for {inst}")
            continue

        shadow = sizing_factor * current_forecast
        shadow_targets[inst] = shadow
        if log:
            log.info(
                f"Shadow target for {inst}: {sizing_factor:.2f} × {current_forecast:.2f} = {shadow:.0f}"
            )

    return shadow_targets


def apply_hard_exits_and_reduce_only(
    trade_plan,
    new_snapshot,
    prev_snapshot,
    data_status_instruments,
    delisted_instruments,
    banned_instruments,
    log,
    reduce_only_instruments=None,
    shadow_targets=None,
):
    """
    Apply hard exits and reduce-only constraints to trade plan.

    Modifies trade_plan in place:
    - Hard exits (target=0): delisted, API-stale, BANNED_FLATTEN
    - Reduce-only (no exposure increase): instruments exiting universe
    - Reduce-only (zombie guard): instruments with non-zero target not in current snapshot
    - Reduce-only (notes): instruments explicitly marked 'reduce_only' in current_positions.csv.
      For these, if the backtest target is 0 (config-driven) and a shadow_target is provided,
      uses shadow_target as the effective target (natural position decay via forecast ratio).
      Without a shadow_target, the position is frozen at current until the note is removed.

    Also recomputes delta_notional and delta_weight for modified rows.

    Args:
        trade_plan: DataFrame indexed by instrument with target_notional, current_notional, reason
        new_snapshot: dict from current universe_snapshot.json, or None
        prev_snapshot: dict from previous run's universe_snapshot.json, or None
        data_status_instruments: dict {instrument: {api_staleness_days: N, ...}}
        delisted_instruments: list of delisted symbols
        banned_instruments: set of banned instrument codes
        log: Logger
        reduce_only_instruments: set of instrument codes marked 'reduce_only' in positions notes
        shadow_targets: dict {instrument: shadow_target_notional} from compute_shadow_targets()

    Returns:
        Number of instruments modified
    """
    import numpy as np

    modified = 0

    # --- Hard exits ---

    # Hard exit 1: Delisted (not in current registry)
    for inst in delisted_instruments:
        if inst in trade_plan.index:
            trade_plan.loc[inst, 'target_notional'] = 0.0
            trade_plan.loc[inst, 'reason'] = 'hard_exit_delisted'
            modified += 1
            log.warning(f"Hard exit (delisted): {inst}")

    # Hard exit 2: Binance API daily tail-patch staleness > 2 days
    # Uses api_staleness_days field (NOT Vision archive lag — that's expected)
    for inst, s in data_status_instruments.items():
        if s.get('api_staleness_days', 0) > 2:
            if inst in trade_plan.index:
                current_reason = trade_plan.loc[inst, 'reason']
                if not str(current_reason).startswith('hard_exit'):
                    trade_plan.loc[inst, 'target_notional'] = 0.0
                    trade_plan.loc[inst, 'reason'] = 'hard_exit_stale_api_data'
                    modified += 1
                    log.warning(
                        f"Hard exit (stale API data: {s['api_staleness_days']}d): {inst}"
                    )

    # Hard exit 3: BANNED_FLATTEN
    for inst in banned_instruments:
        if inst in trade_plan.index:
            current_reason = trade_plan.loc[inst, 'reason']
            if not str(current_reason).startswith('hard_exit'):
                trade_plan.loc[inst, 'target_notional'] = 0.0
                trade_plan.loc[inst, 'reason'] = 'hard_exit_banned'
                modified += 1
                log.warning(f"Hard exit (banned): {inst}")

    # --- Reduce-only for instruments exiting universe ---

    if prev_snapshot is not None and new_snapshot is not None:
        prev_tradable = set(prev_snapshot.get('tradable_instruments', []))
        new_tradable = set(new_snapshot.get('tradable_instruments', []))
        exits = prev_tradable - new_tradable

        for inst in exits:
            if inst not in trade_plan.index:
                continue
            # Skip if already handled by a hard exit
            if str(trade_plan.loc[inst, 'reason']).startswith('hard_exit'):
                continue

            target = float(trade_plan.loc[inst, 'target_notional'])
            current = float(trade_plan.loc[inst, 'current_notional'])

            # Invariant: cannot increase absolute exposure for exiting instruments
            if current == 0.0:
                new_target = 0.0
            elif current > 0.0:
                # Long: can only reduce toward zero, cannot flip to short
                new_target = max(min(target, current), 0.0)
            else:
                # Short: can only reduce toward zero, cannot flip to long
                new_target = min(max(target, current), 0.0)

            if abs(new_target - target) > 1e-6:
                trade_plan.loc[inst, 'target_notional'] = new_target
                trade_plan.loc[inst, 'reason'] = 'reduce_only_exit'
                modified += 1
                log.info(
                    f"Reduce-only (universe exit): {inst} "
                    f"target {target:.0f} → {new_target:.0f}"
                )

    # --- Reduce-only for zombie instruments (non-zero target but never in snapshot) ---
    # Catches instruments that the backtest carries as legacy positions from a prior
    # high-ADV period, but have never appeared in any paper-trading universe snapshot.
    # The exit-transition guard above misses these because they were never in prev_tradable.

    if new_snapshot is not None:
        new_tradable = set(new_snapshot.get('tradable_instruments', []))

        for inst in trade_plan.index:
            # Skip if already handled
            current_reason = str(trade_plan.loc[inst, 'reason'])
            if current_reason.startswith('hard_exit') or current_reason == 'reduce_only_exit':
                continue

            target = float(trade_plan.loc[inst, 'target_notional'])
            if abs(target) < 0.01:
                continue  # Nothing to restrict

            if inst not in new_tradable:
                current = float(trade_plan.loc[inst, 'current_notional'])
                # Reduce-only: can close or hold, but cannot increase absolute exposure
                if current == 0.0:
                    new_target = 0.0
                elif current > 0.0:
                    new_target = max(min(target, current), 0.0)
                else:
                    new_target = min(max(target, current), 0.0)

                if abs(new_target - target) > 1e-6:
                    trade_plan.loc[inst, 'target_notional'] = new_target
                    trade_plan.loc[inst, 'reason'] = 'reduce_only_not_in_universe'
                    modified += 1
                    log.warning(
                        f"Reduce-only (zombie — not in universe): {inst} "
                        f"target {target:.0f} → {new_target:.0f}"
                    )

    # --- Notes-based reduce-only (explicit user override) ---
    # Instruments marked 'reduce_only' in current_positions.csv notes:
    # - No increases beyond current position
    # - No abrupt flatten (even if backtest says target=0 due to config change)
    # - If shadow_targets provides a shadow for this instrument, use it as the effective
    #   target when backtest says 0 — this allows natural decay as forecast weakens.
    #   Shadow=0 (forecast sign flip) is treated as a principled exit signal.
    # - Without shadow, position is frozen until the note is removed.
    # Hard exits (delisted, banned, stale) still override this.

    if reduce_only_instruments:
        _shadow = shadow_targets or {}
        for inst in reduce_only_instruments:
            if inst not in trade_plan.index:
                continue
            current_reason = str(trade_plan.loc[inst, 'reason'])
            if current_reason.startswith('hard_exit'):
                continue  # Hard exits take precedence

            current = float(trade_plan.loc[inst, 'current_notional'])
            target = float(trade_plan.loc[inst, 'target_notional'])

            if abs(current) < 0.01:
                continue  # Position already closed, nothing to protect

            # Determine effective target:
            # - If backtest says 0 (config-driven) and shadow is available, use shadow
            # - Otherwise use backtest target as-is
            shadow = _shadow.get(inst)
            if shadow is not None and abs(target) < 0.01:
                effective_target = shadow
            else:
                effective_target = target

            # Cap in the direction of the existing position (no increases, no flips)
            if current > 0.0:
                new_target = max(min(effective_target, current), 0.0)
            else:
                new_target = min(max(effective_target, current), 0.0)

            # Suppress abrupt flatten only when there's no principled basis:
            # shadow=None → no forecast info, hold at current
            # shadow≠0 but capped to 0 → shouldn't happen (shadow non-zero lands non-zero)
            # shadow=0 → forecast sign flipped, allow exit (don't suppress)
            if abs(new_target) < 0.01 and abs(current) > 0.01:
                if shadow is None or abs(shadow) > 0.01:
                    # No principled exit signal — hold at current
                    new_target = current
                # else: shadow=0 (sign flip) — allow exit, don't suppress

            if abs(new_target - target) > 1e-6:
                trade_plan.loc[inst, 'target_notional'] = new_target
                trade_plan.loc[inst, 'reason'] = 'reduce_only_notes'
                modified += 1
                shadow_note = f" [shadow={shadow:.0f}]" if shadow is not None else ""
                log.info(
                    f"Notes reduce-only: {inst} target {target:.0f} → {new_target:.0f}"
                    f"{shadow_note} (remove 'reduce_only' note to allow full close)"
                )

    # Tag all reduce_only rows with a warning so they're visually distinct
    # in the trade plan CSV and filterable (e.g. held_reduce_only means
    # "backtest wants to move this but reduce_only logic capped it").
    reduce_only_mask = (
        (trade_plan['reason'] == 'reduce_only_exit')
        | (trade_plan['reason'] == 'reduce_only_not_in_universe')
        | (trade_plan['reason'] == 'reduce_only_notes')
    )
    if reduce_only_mask.any() and 'warnings' in trade_plan.columns:
        for inst in trade_plan.index[reduce_only_mask]:
            existing = str(trade_plan.loc[inst, 'warnings'])
            if existing in ('', 'nan', 'None'):
                trade_plan.loc[inst, 'warnings'] = 'held_reduce_only'
            elif 'held_reduce_only' not in existing:
                trade_plan.loc[inst, 'warnings'] = existing + ',held_reduce_only'

    # Recompute delta_notional and delta_weight for all modified rows
    hard_exit_mask = trade_plan['reason'].astype(str).str.startswith('hard_exit')
    changed_mask = hard_exit_mask | reduce_only_mask

    if changed_mask.any():
        equity = trade_plan.attrs.get('current_equity', 1.0)
        trade_plan.loc[changed_mask, 'delta_notional'] = (
            trade_plan.loc[changed_mask, 'target_notional']
            - trade_plan.loc[changed_mask, 'current_notional']
        )
        if equity > 0:
            trade_plan.loc[changed_mask, 'delta_weight'] = (
                trade_plan.loc[changed_mask, 'delta_notional'] / equity
            )

    return modified


def main():
    parser = argparse.ArgumentParser(
        description='Generate trade plan by comparing targets to actual positions',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Generate trade plan (called by run_live_advisory.py)
  %(prog)s \
      --backtest-dir out/live_advisory_20260128/backtest_latest \
      --actual-positions live/current_positions.csv \
      --current-equity 5125.50 \
      --as-of-date 2026-01-28 \
      --output-dir out/live_advisory_20260128

  # Historical replay (for testing)
  %(prog)s \
      --backtest-dir out/live_advisory_20260120/backtest_latest \
      --actual-positions live/positions_2026-01-20.csv \
      --current-equity 5050.25 \
      --as-of-date 2026-01-20 \
      --output-dir out/trade_plans/historical

Notes:
  - as_of_date MUST match last date in backtest (fresh targets only)
  - current_equity should reflect actual P&L, not initial capital
  - Actual positions must have: instrument, contracts, timestamp (mark_price_usd auto-derived from backtest)
  - Trade plan uses current_equity for all calculations (not initial capital)
        """
    )

    parser.add_argument(
        '--backtest-dir',
        type=Path,
        required=True,
        help='Path to FRESH backtest output directory (must contain positions.csv, diagnostics.parquet, metadata.json)'
    )
    parser.add_argument(
        '--actual-positions',
        type=Path,
        required=True,
        help='Path to actual positions CSV (columns: instrument, hl_symbol, contracts, timestamp[, notes])'
    )
    parser.add_argument(
        '--current-equity',
        type=float,
        required=True,
        help='Current account equity in USD (should reflect actual P&L, not initial capital)'
    )
    parser.add_argument(
        '--as-of-date',
        type=str,
        required=True,
        help='Evaluation date in YYYY-MM-DD format (MUST match last date in backtest)'
    )
    parser.add_argument(
        '--output-dir',
        type=Path,
        required=True,
        help='Output directory for trade plan and audit files'
    )
    parser.add_argument(
        '--config',
        type=Path,
        help='Optional: path to system config (if not provided, will try to load from backtest metadata)'
    )
    parser.add_argument(
        '--data-status',
        type=Path,
        help='Optional: path to raw_data_status.json (for V1 staleness overlay and API staleness hard exits). If not provided, staleness overlay skipped.'
    )
    parser.add_argument(
        '--universe-snapshot',
        type=Path,
        help='Optional: path to universe_snapshot.json from this run\'s backtest (for reduce-only validation). If not provided, universe validation skipped.'
    )
    parser.add_argument(
        '--prev-universe-snapshot',
        type=Path,
        help='Optional: path to universe_snapshot.json from previous run (for reduce-only exit computation). If not provided, reduce-only skipped.'
    )
    parser.add_argument(
        '--registry-changelog',
        type=Path,
        help='Optional: path to registry_changelog.json (for delisting hard exits). If not provided, delisting check skipped.'
    )

    # Environment isolation
    env_group = parser.add_argument_group('Environment settings')
    env_group.add_argument(
        '--env',
        help='Environment name (uses envs/<env>/ structure). Examples: prod, dev, paper, exp1. Default: current directory'
    )
    env_group.add_argument(
        '--env-root',
        type=Path,
        help='Custom environment root (overrides --env). Can also use LIVE_OPS_ENV_ROOT env var'
    )

    args = parser.parse_args()

    # Initialize environment resolver (mainly for logging/context)
    from sysdata.crypto.env_paths import LiveOpsEnvironment
    env = LiveOpsEnvironment(
        env=args.env if hasattr(args, 'env') else None,
        env_root=args.env_root if hasattr(args, 'env_root') else None
    )

    logger.info(f"Environment: {env}")

    # Validate inputs
    if not args.backtest_dir.exists():
        logger.error(f"Backtest directory not found: {args.backtest_dir}")
        sys.exit(1)

    if not args.actual_positions.exists():
        logger.error(f"Actual positions file not found: {args.actual_positions}")
        logger.error("This file must be manually maintained after trade execution.")
        sys.exit(1)

    # Create output directory
    args.output_dir.mkdir(parents=True, exist_ok=True)

    # Load config
    if args.config:
        config_path = args.config
    else:
        # Try to load from backtest metadata
        metadata_path = args.backtest_dir / 'metadata.json'
        if metadata_path.exists():
            with open(metadata_path) as f:
                metadata = json.load(f)
            config_path = Path(metadata.get('config_path', 'config/crypto_perps_baseline_v1.yaml'))
        else:
            logger.error("No config provided and cannot load from backtest metadata")
            sys.exit(1)

    if not config_path.exists():
        logger.error(f"Config file not found: {config_path}")
        sys.exit(1)

    logger.info(f"Loading config from {config_path}")
    with open(config_path) as f:
        config = yaml.safe_load(f)

    # Generate trade plan
    try:
        logger.info("=" * 60)
        logger.info("GENERATING TRADE PLAN")
        logger.info("=" * 60)

        trade_plan, sanity_checks, audit_bundle = generate_trade_plan(
            args.backtest_dir,
            args.actual_positions,
            args.current_equity,
            args.as_of_date,
            config,
            data_status_path=args.data_status if hasattr(args, 'data_status') else None
        )

        # Write outputs
        trade_plan_path = args.output_dir / f'trade_plan_{args.as_of_date}.csv'
        sanity_checks_path = args.output_dir / f'sanity_checks_{args.as_of_date}.json'
        audit_bundle_path = args.output_dir / f'audit_bundle_{args.as_of_date}.json'

        # Attach current_equity to trade_plan.attrs for delta recomputation in post-processing
        trade_plan.attrs['current_equity'] = args.current_equity

        # Post-processing: hard exits and reduce-only constraints
        new_snapshot = load_universe_snapshot(
            args.universe_snapshot if hasattr(args, 'universe_snapshot') else None
        )
        prev_snapshot = load_universe_snapshot(
            args.prev_universe_snapshot if hasattr(args, 'prev_universe_snapshot') else None
        )
        data_status_instruments = load_data_status_instruments(
            args.data_status if hasattr(args, 'data_status') else None
        )
        delisted_instruments = load_delisted_from_registry_changelog(
            args.registry_changelog if hasattr(args, 'registry_changelog') else None
        )
        banned_instruments = set(config.get('banned_instruments', []))

        # Load notes-based reduce-only instruments from actual positions CSV
        reduce_only_instruments = set()
        try:
            positions_df = pd.read_csv(args.actual_positions)
            if 'notes' in positions_df.columns and 'instrument' in positions_df.columns:
                reduce_only_instruments = set(
                    positions_df.loc[
                        positions_df['notes'].fillna('').str.strip() == 'reduce_only',
                        'instrument'
                    ]
                )
                if reduce_only_instruments:
                    logger.info(
                        f"Notes reduce-only instruments: {sorted(reduce_only_instruments)}"
                    )
        except Exception as e:
            logger.warning(f"Could not load notes from positions file: {e}")

        # Compute shadow targets for reduce_only instruments whose backtest target is 0
        # (driven by config changes rather than forecast decay). Shadow target = what the
        # position would be if sized like a normal active instrument at the current forecast.
        shadow_targets = compute_shadow_targets(
            reduce_only_instruments=reduce_only_instruments,
            backtest_dir=args.backtest_dir,
            log=logger,
        )

        n_modified = apply_hard_exits_and_reduce_only(
            trade_plan=trade_plan,
            new_snapshot=new_snapshot,
            prev_snapshot=prev_snapshot,
            data_status_instruments=data_status_instruments,
            delisted_instruments=delisted_instruments,
            banned_instruments=banned_instruments,
            log=logger,
            reduce_only_instruments=reduce_only_instruments,
            shadow_targets=shadow_targets,
        )
        if n_modified > 0:
            logger.info(
                f"Post-processing applied {n_modified} hard exit / reduce-only overrides"
            )

        # Validate trade plan is within new dynamic universe (soft check — warn only)
        if new_snapshot:
            new_tradable = set(new_snapshot.get('tradable_instruments', []))
            non_universe = set(trade_plan.index[trade_plan['target_notional'].abs() > 0.01]) - new_tradable
            if non_universe:
                logger.warning(
                    f"Trade plan includes {len(non_universe)} instrument(s) with non-zero target "
                    f"outside current universe: {sorted(non_universe)}"
                )
            else:
                logger.info(
                    f"✓ Trade plan validated: non-zero targets ⊆ universe "
                    f"({new_snapshot.get('count', '?')} instruments)"
                )
        else:
            logger.warning(
                "No universe snapshot provided — skipping universe membership validation"
            )

        # Add hl_symbol column for Hyperliquid execution convenience
        from sysdata.crypto.config_helpers import instrument_id_to_hl_symbol
        trade_plan.insert(0, 'hl_symbol', [
            instrument_id_to_hl_symbol(inst) for inst in trade_plan.index
        ])

        logger.info(f"Writing trade plan to {trade_plan_path}")
        trade_plan.to_csv(trade_plan_path)

        logger.info(f"Writing sanity checks to {sanity_checks_path}")
        with open(sanity_checks_path, 'w') as f:
            json.dump(sanity_checks, f, indent=2)

        logger.info(f"Writing audit bundle to {audit_bundle_path}")
        with open(audit_bundle_path, 'w') as f:
            json.dump(audit_bundle, f, indent=2)

        # Print summary
        logger.info("\n" + "=" * 60)
        logger.info("TRADE PLAN SUMMARY")
        logger.info("=" * 60)

        total_trades = len(trade_plan)
        actionable = trade_plan[
            ~trade_plan['warnings'].str.contains('below_min_trade_size') &
            ~trade_plan['warnings'].str.contains('buffer_suppressed')
        ]
        trades_above_min = len(actionable)
        total_cost = actionable['estimated_cost'].sum()

        logger.info(f"Total trades: {total_trades}")
        logger.info(f"Actionable trades (above min size, clear buffer): {trades_above_min}")
        logger.info(f"Total estimated cost: ${total_cost:.2f}")
        logger.info(f"Overall status: {sanity_checks['overall_status']}")

        if sanity_checks['warnings']:
            logger.warning("\nWARNINGS:")
            for warning in sanity_checks['warnings']:
                logger.warning(f"  - {warning}")

        logger.info("\n" + "=" * 60)
        logger.info("✓ Trade plan generation complete")
        logger.info("=" * 60)
        logger.info(f"\nOutputs:")
        logger.info(f"  - Trade plan: {trade_plan_path}")
        logger.info(f"  - Sanity checks: {sanity_checks_path}")
        logger.info(f"  - Audit bundle: {audit_bundle_path}")
        logger.info("")

        # Exit with appropriate status
        if sanity_checks['overall_status'] == 'fail':
            logger.error("Trade plan failed sanity checks - review before executing")
            sys.exit(2)  # Exit code 2 = warnings/failures
        elif sanity_checks['overall_status'] == 'pass_with_warnings':
            logger.warning("Trade plan has warnings - review carefully")
            sys.exit(0)  # Still success, but with warnings
        else:
            sys.exit(0)

    except Exception as e:
        logger.error(f"✗ Trade plan generation failed: {e}", exc_info=True)
        sys.exit(1)


if __name__ == '__main__':
    main()
