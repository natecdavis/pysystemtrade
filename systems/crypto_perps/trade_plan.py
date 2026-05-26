"""
Trade plan generation for live advisory system.

Compares target positions from research_v1 backtest against actual current positions
to generate actionable trade recommendations with risk checks and audit trail.

V1 Extension: Staleness-based eligibility overlay for daily operations.
"""

import hashlib
import pandas as pd
import numpy as np
import subprocess
from pathlib import Path
from typing import Dict, List, Tuple, Optional
from datetime import datetime, date as Date
import json
import logging

from systems.crypto_perps.staleness_overlay import (
    apply_staleness_overlay,
    compute_staleness_summary,
    validate_staleness_inputs,
)

logger = logging.getLogger(__name__)


def normalize_status_instrument_code(instrument: str) -> str:
    """Convert raw exchange symbols in data-status files to internal perp codes."""
    instrument = str(instrument)
    if instrument.endswith("_PERP"):
        return instrument
    return f"{instrument}_PERP"


def load_actual_positions(
    positions_path: Path,
    prices: Optional[Dict[str, float]] = None,
) -> pd.DataFrame:
    """
    Load actual current positions from CSV file.

    Expected schema (minimum):
        instrument,contracts,timestamp[,notes]

    Optional columns (auto-derived from `prices` dict if absent):
        mark_price_usd, notional_usd

    Returns:
        DataFrame with 'instrument' as index and columns:
            contracts, mark_price_usd, notional_usd, timestamp[, notes, ...]
    """
    df = pd.read_csv(positions_path)

    # Validate always-required columns
    required_cols = ["instrument", "contracts", "timestamp"]
    missing = set(required_cols) - set(df.columns)
    if missing:
        raise ValueError(f"Missing required columns in actual positions: {missing}")

    # Derive mark_price_usd from prices dict if not in CSV. Leave NaN when missing
    # so the orphan check below can fail closed; only zero-contract rows get a safe 0.0 fill.
    if "mark_price_usd" not in df.columns:
        if prices is None:
            raise ValueError(
                "mark_price_usd column missing from positions CSV and no prices dict provided. "
                "Either include the column or pass last_prices.json from the backtest."
            )
        df["mark_price_usd"] = df["instrument"].map(prices)

    # Fail closed on non-zero contracts with no valid mark price. A delisted/migrated symbol
    # silently zeroed out here would generate a zero-delta hard exit and the orphan position
    # would never be flattened. Examples: HL's 1000PEPE → 1000000PEPE migration leaving a
    # 1000PEPE position in current_positions.csv that's missing from last_prices.json.
    nonzero_no_price = (df["contracts"] != 0) & (
        df["mark_price_usd"].isna() | (df["mark_price_usd"] == 0)
    )
    if nonzero_no_price.any():
        orphan_rows = df.loc[nonzero_no_price, ["instrument", "contracts"]]
        orphans = [
            f"{r['instrument']}={r['contracts']:+g} contracts"
            for _, r in orphan_rows.iterrows()
        ]
        raise ValueError(
            f"Cannot value {len(orphans)} actual position(s) with non-zero contracts and "
            f"no valid mark price (likely delisted/migrated orphans): "
            f"[{', '.join(orphans)}]. "
            f"Resolve by adding mark_price_usd to {positions_path}, updating last_prices.json, "
            f"or manually flattening these positions on Hyperliquid before regenerating "
            f"the trade plan."
        )

    # Zero-contract rows with no price are harmless; backfill so notional math stays clean.
    df["mark_price_usd"] = df["mark_price_usd"].fillna(0.0)

    # Recompute notional from contracts × mark_price (mark price drifts between fill and recording)
    df["notional_usd"] = df["contracts"] * df["mark_price_usd"]

    # Set index
    df = df.set_index("instrument")

    return df


def load_backtest_positions(backtest_dir: Path, as_of_date: str) -> pd.Series:
    """
    Load target positions from backtest for specific date.

    Args:
        backtest_dir: Path to backtest output directory
        as_of_date: Date string in YYYY-MM-DD format

    Returns:
        Series with instrument as index, target position in BASE-ASSET TOKENS (not USD).
        Caller must multiply by last_prices.json to convert to USD notional.
    """
    positions_path = backtest_dir / "positions.csv"
    if not positions_path.exists():
        raise FileNotFoundError(f"Backtest positions not found: {positions_path}")

    # Load positions
    df = pd.read_csv(positions_path, index_col=0, parse_dates=True)

    # Check if as_of_date exists
    as_of_dt = pd.to_datetime(as_of_date)
    if as_of_dt not in df.index:
        raise ValueError(
            f"Date {as_of_date} not found in backtest positions. "
            f"Available range: {df.index[0].date()} to {df.index[-1].date()}"
        )

    # Extract target positions for as_of_date
    targets = df.loc[as_of_dt]

    return targets


def load_backtest_diagnostics(backtest_dir: Path, as_of_date: str) -> pd.DataFrame:
    """
    Load diagnostics (forecasts, constraints, state) for a specific date.

    Returns:
        DataFrame indexed by instrument, with diagnostic columns.

    Handles two diagnostics layouts:
      - Current: long-form with `date` and `instrument` as regular columns
        plus a default RangeIndex (what run_dynamic_universe_backtest writes
        today, ~1.1M rows for 6+ years of history).
      - Legacy: MultiIndex of (date, instrument).

    Pre-fix this only handled the MultiIndex case; on the current layout it
    silently returned an empty frame, so the audit bundle's
    `forecasts_snapshot` was always empty (audit 2026-05-26).
    """
    diagnostics_path = backtest_dir / "diagnostics.parquet"
    if not diagnostics_path.exists():
        raise FileNotFoundError(f"Backtest diagnostics not found: {diagnostics_path}")

    df = pd.read_parquet(diagnostics_path)
    as_of_dt = pd.to_datetime(as_of_date)

    if isinstance(df.index, pd.MultiIndex):
        df_date = df[df.index.get_level_values(0) == as_of_dt]
        df_date = df_date.reset_index(level=0, drop=True)
        return df_date

    if "date" in df.columns and "instrument" in df.columns:
        # Long-form columns layout (current writer)
        df = df.copy()
        df["date"] = pd.to_datetime(df["date"])
        df_date = df[df["date"] == as_of_dt].copy()
        if df_date.empty:
            return df_date.set_index("instrument").drop(columns=["date"], errors="ignore")
        return df_date.set_index("instrument").drop(columns=["date"])

    raise ValueError(
        f"Unrecognized diagnostics layout: cols={list(df.columns)[:8]}, "
        f"index_type={type(df.index).__name__}. Expected MultiIndex "
        "(date, instrument) OR columns containing both 'date' and 'instrument'."
    )


def load_backtest_metadata(backtest_dir: Path) -> dict:
    """Load backtest metadata (config, dataset fingerprint, etc.)"""
    metadata_path = backtest_dir / "metadata.json"
    if not metadata_path.exists():
        raise FileNotFoundError(f"Backtest metadata not found: {metadata_path}")

    with open(metadata_path) as f:
        return json.load(f)


def _file_sha256(path: Path) -> str:
    """Compute sha256 of a file, returning 'missing' if absent or 'error:<exc>' on failure."""
    try:
        if not path.exists():
            return "missing"
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 16), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception as exc:  # pragma: no cover - defensive
        return f"error:{type(exc).__name__}"


def _git_commit_hash(repo_root: Optional[Path] = None) -> str:
    """Return current HEAD sha for the repo containing trade_plan.py (or 'unknown')."""
    try:
        cwd = str(repo_root) if repo_root else str(Path(__file__).resolve().parent)
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        )
        return out.stdout.strip()
    except Exception:
        return "unknown"


def populate_backtest_metadata(metadata: dict, backtest_dir: Path) -> dict:
    """Derive the audit-bundle `backtest_metadata` block from what's available.

    The backtest's own metadata.json records `config_path` and `data_path` but
    NOT the corresponding hashes / commits — so prior versions of this audit
    block reported every field as "unknown" (audit 2026-05-26). This helper
    fills in the missing pieces deterministically: hashes the actual files,
    grabs the git HEAD sha, and uses the start/end dates as the date-range
    pair.
    """
    config_path = metadata.get("config_path")
    data_path = metadata.get("data_path")
    return {
        "backtest_dir": str(backtest_dir),
        "config_hash": _file_sha256(Path(config_path)) if config_path else "unknown",
        "dataset_fingerprint": _file_sha256(Path(data_path)) if data_path else "unknown",
        "git_commit": _git_commit_hash(),
        "dataset_path": str(data_path) if data_path else "unknown",
        "dataset_date_range": [
            metadata.get("backtest_start_date"),
            metadata.get("backtest_end_date"),
        ],
    }


def load_backtest_prices(backtest_dir: Path) -> pd.Series:
    """Load last-day instrument prices saved by the backtest."""
    path = backtest_dir / "prices_last.csv"
    if not path.exists():
        return pd.Series(dtype=float)
    s = pd.read_csv(path, index_col=0).squeeze()
    s.name = "price"
    return s


def load_backtest_daily_vols(backtest_dir: Path) -> pd.Series:
    """Load last-day daily vols (in price units) saved by the backtest."""
    path = backtest_dir / "daily_vols_last.csv"
    if not path.exists():
        return pd.Series(dtype=float)
    s = pd.read_csv(path, index_col=0).squeeze()
    s.name = "daily_vol"
    return s


def load_staleness_data(
    data_status_path: Path,
) -> Tuple[Optional[pd.Series], Optional[Date], Optional[Date]]:
    """
    Load staleness data from data_status.json file.

    Args:
        data_status_path: Path to raw_data_status.json

    Returns:
        Tuple of (staleness_days, expected_as_of_date, dataset_as_of_date)
        Returns (None, None, None) if file doesn't exist or is missing required fields
    """
    if not data_status_path.exists():
        logger.warning(
            f"Data status file not found: {data_status_path}. Staleness overlay skipped."
        )
        return None, None, None

    with open(data_status_path) as f:
        status = json.load(f)

    # Check if this is a V1 report (has staleness tracking)
    if "instruments" not in status:
        logger.warning(
            "Data status file is missing 'instruments' field. Staleness overlay skipped."
        )
        return None, None, None

    # Extract staleness per instrument
    staleness_dict = {}
    for inst, data in status["instruments"].items():
        if "staleness_days" in data:
            staleness_dict[normalize_status_instrument_code(inst)] = data[
                "staleness_days"
            ]

    if not staleness_dict:
        logger.warning(
            "Data status file has no staleness_days. Staleness overlay skipped."
        )
        return None, None, None

    staleness_series = pd.Series(staleness_dict)

    # Extract dates
    expected_as_of_date = None
    dataset_as_of_date = None

    if "expected_as_of_date" in status:
        expected_as_of_date = datetime.strptime(
            status["expected_as_of_date"], "%Y-%m-%d"
        ).date()

    if "dataset_as_of_date" in status:
        dataset_as_of_date = datetime.strptime(
            status["dataset_as_of_date"], "%Y-%m-%d"
        ).date()

    return staleness_series, expected_as_of_date, dataset_as_of_date


def calculate_position_deltas(
    targets: pd.Series, actuals: pd.DataFrame, current_equity: float
) -> pd.DataFrame:
    """
    Calculate position deltas (target - actual) for all instruments.

    Args:
        targets: Series of target notional positions (from backtest)
        actuals: DataFrame of actual positions (from manual input)
        current_equity: Current account equity

    Returns:
        DataFrame with columns:
            target_notional, current_notional, delta_notional, delta_weight, current_contracts, mark_price_usd
    """
    # Build results dataframe
    results = []

    for inst in targets.index:
        target_notional = targets[inst]

        # Get actual position (default to 0 if not present)
        if inst in actuals.index:
            current_notional = actuals.loc[inst, "notional_usd"]
            current_contracts = actuals.loc[inst, "contracts"]
            mark_price = actuals.loc[inst, "mark_price_usd"]
        else:
            current_notional = 0.0
            current_contracts = 0.0
            mark_price = 0.0

        # Calculate delta
        delta_notional = target_notional - current_notional
        delta_weight = delta_notional / current_equity if current_equity > 0 else 0.0

        results.append(
            {
                "instrument": inst,
                "current_contracts": current_contracts,
                "mark_price_usd": mark_price,
                "current_notional": current_notional,
                "target_notional": target_notional,
                "delta_notional": delta_notional,
                "delta_weight": delta_weight,
            }
        )

    df = pd.DataFrame(results)
    df = df.set_index("instrument")

    return df


def estimate_trade_costs(
    deltas: pd.DataFrame, spread_frac: float = 0.00025, taker_fee_frac: float = 0.0004
) -> pd.Series:
    """
    Estimate round-trip costs for trades.

    RTC = |delta_notional| × (spread_frac/2 + taker_fee_frac)

    Default: 0.00065 (65 bps) = 0.00025/2 + 0.0004

    Returns:
        Series with instrument as index, estimated cost as values
    """
    rtc_frac = spread_frac / 2 + taker_fee_frac
    costs = deltas["delta_notional"].abs() * rtc_frac
    return costs


def check_min_position_sizes(
    deltas: pd.DataFrame,
    min_order_notional: float,
) -> dict:
    """
    Flag orders below HL's minimum order notional. Applies to all orders including reductions,
    except full closes (target_notional ≈ 0) which HL allows regardless of size.

    Returns:
        dict with keys: threshold_usd, below_threshold (list), status
    """
    is_full_close = deltas["target_notional"].abs() < 1e-6
    is_nonzero_order = deltas["delta_notional"].abs() > 1e-6
    below = deltas[
        is_nonzero_order
        & (deltas["delta_notional"].abs() < min_order_notional)
        & ~is_full_close
    ]
    return {
        "threshold_usd": round(min_order_notional, 2),
        "below_threshold": below.index.tolist(),
        "status": "pass" if below.empty else "warn",
    }


def classify_trade_reason(row: pd.Series) -> str:
    """
    Classify trade reason based on target and current positions.

    Returns:
        One of: target_increase | target_decrease | flatten_to_zero | new_position | rebalance
    """
    current = row["current_notional"]
    target = row["target_notional"]
    delta = row["delta_notional"]

    # Thresholds for classification
    ZERO_THRESHOLD = 1e-6

    # New position (current is zero or near-zero)
    if abs(current) < ZERO_THRESHOLD and abs(target) > ZERO_THRESHOLD:
        return "new_position"

    # Flatten to zero (target is zero or near-zero)
    if abs(target) < ZERO_THRESHOLD and abs(current) > ZERO_THRESHOLD:
        return "flatten_to_zero"

    # Same sign, increasing magnitude
    if np.sign(current) == np.sign(target) and abs(target) > abs(current):
        return "target_increase"

    # Same sign, decreasing magnitude
    if np.sign(current) == np.sign(target) and abs(target) < abs(current):
        return "target_decrease"

    # Different signs (flip from long to short or vice versa)
    if np.sign(current) != np.sign(target):
        return "rebalance"

    # Default
    return "rebalance"


def rank_trades_by_priority(deltas: pd.DataFrame) -> pd.DataFrame:
    """
    Rank trades by priority for manual execution.

    Priority = by absolute notional size (largest first)

    Returns:
        DataFrame with 'priority' column added (1 = highest priority)
    """
    # Sort by absolute delta (largest first)
    deltas_sorted = deltas.copy()
    deltas_sorted["abs_delta"] = deltas_sorted["delta_notional"].abs()
    deltas_sorted = deltas_sorted.sort_values("abs_delta", ascending=False)

    # Assign priority (1-indexed)
    deltas_sorted["priority"] = range(1, len(deltas_sorted) + 1)

    # Drop temporary column
    deltas_sorted = deltas_sorted.drop(columns=["abs_delta"])

    # Sort by priority
    deltas_sorted = deltas_sorted.sort_values("priority")

    return deltas_sorted


def generate_trade_plan(
    backtest_dir: Path,
    actual_positions_path: Path,
    current_equity: float,
    as_of_date: str,
    config: dict,
    data_status_path: Optional[Path] = None,
    equity_history_path: Optional[Path] = None,
) -> Tuple[pd.DataFrame, dict, dict]:
    """
    Generate trade plan by comparing backtest targets to actual positions.

    Args:
        backtest_dir: Path to backtest output directory
        actual_positions_path: Path to actual positions CSV
        current_equity: Current account equity in USD
        as_of_date: Evaluation date (must match last date in backtest)
        config: System config dict (for spread_frac, fees, caps, etc.)
        data_status_path: Optional path to raw_data_status.json (for V1 staleness overlay)

    Returns:
        Tuple of (trade_plan_df, sanity_checks_dict, audit_bundle_dict)

    Raises:
        ValueError: If as_of_date doesn't match backtest end or other validation failures
    """
    logger.info(f"Generating trade plan for {as_of_date}")

    # 1. Validate as_of_date matches backtest end
    logger.info("Loading backtest positions...")
    positions_csv = pd.read_csv(
        backtest_dir / "positions.csv", index_col=0, parse_dates=True
    )
    backtest_end_date = positions_csv.index[-1].strftime("%Y-%m-%d")

    if as_of_date != backtest_end_date:
        raise ValueError(
            f"Date mismatch: backtest ends at {backtest_end_date}, requested {as_of_date}. "
            f"Targets must be FRESH (not stale). Re-run backtest with latest data."
        )

    # C4 multiplier-panel staleness check (fail-closed). Defense-in-depth: the
    # backtest combiner's first panel load also calls assert_multiplier_panel_fresh
    # (forecast_combine_gated.py:_apply_walk_forward_multiplier), so by the time
    # we reach trade-plan generation the panel was fresh at backtest start.
    # Re-checking here catches the rare case where the panel ages past 30h
    # between backtest and trade-plan generation in the same run. Also
    # captures today's modulation state for the audit bundle (audit F3).
    mult_path_str = config.get("walk_forward_multiplier_panel_path")
    if mult_path_str:
        from systems.crypto_perps.c4_xgboost_combiner import (
            assert_multiplier_panel_fresh,
            summarize_multiplier_row,
        )
        resolved_panel_path = assert_multiplier_panel_fresh(mult_path_str)
        c4_state = summarize_multiplier_row(
            pd.read_parquet(resolved_panel_path),
            row_date=pd.Timestamp(as_of_date),
        )
    else:
        c4_state = {"mode": "disabled", "as_of_date": as_of_date}

    # 2. Load backtest outputs
    logger.info("Loading backtest outputs...")
    targets = load_backtest_positions(backtest_dir, as_of_date)
    diagnostics = load_backtest_diagnostics(backtest_dir, as_of_date)
    metadata = load_backtest_metadata(backtest_dir)

    # 2a. Convert target positions from base-asset tokens → USD notional.
    #
    # pysystemtrade's get_notional_position() returns contracts in base-asset units
    # (e.g., PENGU tokens, ADA tokens), NOT USD. The block_value formula uses
    # value_of_block_price_move=1.0 (hard-coded for crypto perps), so the sizing
    # formula produces: vol_scalar [contracts] = daily_cash_vol_target [USD] / ivm [USD/contract/day].
    # positions.csv stores these token counts directly. We must multiply by last_prices.json
    # to get true USD notionals before any comparison with actual USD positions.
    last_prices_path = backtest_dir / "last_prices.json"
    if not last_prices_path.exists():
        raise FileNotFoundError(
            f"last_prices.json not found in {backtest_dir}. "
            "Re-run backtest to generate it (required to convert token positions to USD)."
        )
    with open(last_prices_path) as f:
        last_prices: Optional[Dict[str, float]] = json.load(f)
    last_prices_series = pd.Series(last_prices).reindex(targets.index)
    targets = (targets * last_prices_series).fillna(0.0)
    logger.info(
        f"Converted {(last_prices_series.notna()).sum()}/{len(targets)} target positions "
        f"from tokens to USD using last_prices.json"
    )

    # 3. Load actual positions (prices from last_prices.json if mark_price_usd not in CSV)
    logger.info("Loading actual positions...")
    actuals = load_actual_positions(actual_positions_path, prices=last_prices)

    # Validate instruments
    target_instruments = set(targets.index)
    actual_instruments = set(actuals.index)

    # Check for instruments in actuals but not in universe
    extra_instruments = actual_instruments - target_instruments
    if extra_instruments:
        logger.warning(
            f"Instruments in actual positions but NOT in backtest universe: {extra_instruments}. "
            f"These will be added to the trade plan as hard exits (target_notional=0)."
        )
        # Build hard-exit targets for out-of-universe instruments
        extra_targets = pd.Series(
            {inst: 0.0 for inst in extra_instruments},
            name=targets.name,
            dtype=float,
        )
        targets = pd.concat([targets, extra_targets])
        target_instruments = set(targets.index)

    # Missing instruments in actuals are OK (default to 0.0)
    missing_instruments = target_instruments - actual_instruments
    if missing_instruments:
        logger.info(
            f"Instruments in universe but not in actual positions (defaulting to 0.0): {missing_instruments}"
        )

    # 3.5. Apply staleness overlay (V1 daily operations)
    staleness_overlay_applied = False
    staleness_audit = None
    staleness_summary = None

    if data_status_path:
        logger.info("Loading staleness data...")
        staleness_days, expected_date, dataset_date = load_staleness_data(
            data_status_path
        )

        if staleness_days is not None:
            logger.info("Applying staleness-based eligibility overlay...")

            # Extract actual notionals as Series
            actual_notionals = pd.Series(
                {
                    inst: actuals.loc[inst, "notional_usd"]
                    if inst in actuals.index
                    else 0.0
                    for inst in targets.index
                }
            )

            # Validate inputs
            validate_staleness_inputs(targets, actual_notionals, staleness_days)

            # Apply overlay
            original_targets = targets.copy()
            targets, staleness_audit = apply_staleness_overlay(
                targets,
                actual_notionals,
                staleness_days,
                dataset_date
                if dataset_date
                else datetime.strptime(as_of_date, "%Y-%m-%d").date(),
            )

            # Compute summary
            staleness_summary = compute_staleness_summary(staleness_days)
            staleness_overlay_applied = True

            logger.info(
                f"Staleness overlay applied: {len(staleness_audit)} instruments overridden, "
                f"summary: {staleness_summary}"
            )
        else:
            logger.info("Staleness data not available - overlay skipped (V0 mode)")
    else:
        logger.info(
            "Data status path not provided - staleness overlay skipped (V0 mode)"
        )

    # 4. Calculate position deltas
    logger.info("Calculating position deltas...")
    deltas = calculate_position_deltas(targets, actuals, current_equity)

    # 4.5. Apply Carver forecast-method live buffer check.
    #
    # Load today's buffer bounds (top_pos / bot_pos in token units) saved by the
    # backtest, convert to USD via last_prices.json, then check each instrument:
    #
    #   current in [bot, top]  → in buffer zone: suppress trade (set delta = 0)
    #   outside zone           → trade to optimal (positions.csv target, unchanged)
    #
    # Backtest uses trade_to_edge=False (jump to optimal on breach), so positions.csv
    # already holds the optimal target. Out-of-zone live positions just use that target
    # as-is. Suppressed instruments get 'buffer_suppressed' in warnings so
    # parse_trade_plan can exclude them from the notification count.
    buffer_suppressed_instruments: set = set()
    buffer_bounds_path = backtest_dir / "buffer_bounds_last.csv"
    if buffer_bounds_path.exists():
        logger.info("Applying Carver forecast-method live buffer check...")
        bb = pd.read_csv(buffer_bounds_path, index_col=0)
        prices_s = pd.Series(last_prices)
        bb["top_usd"] = bb["top_pos"] * prices_s.reindex(bb.index)
        bb["bot_usd"] = bb["bot_pos"] * prices_s.reindex(bb.index)

        for inst in deltas.index:
            if inst not in bb.index:
                continue
            if abs(deltas.loc[inst, "delta_notional"]) < 1e-6:
                continue  # Already no trade needed; buffer check irrelevant
            current = deltas.loc[inst, "current_notional"]
            top = bb.loc[inst, "top_usd"]
            bot = bb.loc[inst, "bot_usd"]
            if pd.isna(top) or pd.isna(bot):
                continue

            if bot <= current <= top:
                # In buffer zone — no trade needed
                deltas.loc[inst, "target_notional"] = current
                deltas.loc[inst, "delta_notional"] = 0.0
                buffer_suppressed_instruments.add(inst)
            # Outside zone: positions.csv target (optimal) stands unchanged

        logger.info(
            f"  Buffer suppressed {len(buffer_suppressed_instruments)} trades "
            f"(in-zone, no action needed): {sorted(buffer_suppressed_instruments)}"
        )
    else:
        logger.warning(
            "buffer_bounds_last.csv not found — live buffer check skipped. "
            "Re-run backtest to generate it."
        )

    # 5. Estimate costs
    logger.info("Estimating trade costs...")
    costs_config = config.get("costs", {})
    spread_frac = costs_config.get("spread_estimate", 0.0005)
    taker_fee_frac = costs_config.get("taker_fee_frac", 0.0004)

    costs = estimate_trade_costs(deltas, spread_frac, taker_fee_frac)
    deltas["estimated_cost"] = costs

    # 6. Classify trade reasons
    logger.info("Classifying trades...")
    deltas["reason"] = deltas.apply(classify_trade_reason, axis=1)

    # 7. Add instrument state from diagnostics
    if "state" in diagnostics.columns:
        deltas["state"] = diagnostics["state"]
    else:
        deltas["state"] = "ACTIVE"  # Default if no state column

    # 8. Rank trades by priority
    logger.info("Ranking trades...")
    deltas = rank_trades_by_priority(deltas)

    # 9. Apply risk checks
    logger.info("Running sanity checks...")

    # Min position size check
    min_order_notional = config.get("min_notional_position", 10.0)
    min_size_check = check_min_position_sizes(
        deltas, min_order_notional=min_order_notional
    )

    # Banned instruments
    banned_instruments = deltas[deltas["state"] == "BANNED_FLATTEN"].index.tolist()

    # Instrument states summary
    state_counts = deltas["state"].value_counts().to_dict()

    # Total estimated cost
    total_cost = deltas["estimated_cost"].sum()
    cost_pct = total_cost / current_equity if current_equity > 0 else 0.0

    # Add warnings to deltas
    warnings = []
    for inst in deltas.index:
        inst_warnings = []

        # Buffer suppressed (delta too small relative to position volatility)
        if inst in buffer_suppressed_instruments:
            inst_warnings.append("buffer_suppressed")

        # Below min size
        if inst in min_size_check["below_threshold"]:
            inst_warnings.append("below_min_trade_size")

        # Stale target (if backtest is old)
        # This is already checked at function entry, so we're OK here

        warnings.append(",".join(inst_warnings) if inst_warnings else "")

    deltas["warnings"] = warnings

    # 10. Build sanity checks dict
    initial_capital = config.get(
        "notional_trading_capital", config.get("system", {}).get("capital", 5000.0)
    )

    # PnL pct since inception: read first row of equity_history.csv when provided.
    # When the path is not supplied or the file is empty, leave the field null —
    # don't fall back to dividing equity by notional capital (that gave a
    # nonsensical −66.67% on a leveraged account; pre-2026-05-21 bug).
    starting_equity: Optional[float] = None
    inception_date: Optional[str] = None
    if equity_history_path is not None and equity_history_path.exists():
        try:
            history = pd.read_csv(equity_history_path)
            if len(history) > 0 and "equity" in history.columns:
                starting_equity = float(history["equity"].iloc[0])
                if "date" in history.columns:
                    inception_date = str(history["date"].iloc[0])
        except Exception as e:
            logger.warning(
                f"Failed to read equity_history at {equity_history_path}: {e}. "
                f"equity_pnl_pct will be null."
            )

    if starting_equity is not None and starting_equity > 0:
        equity_pnl_pct: Optional[float] = (current_equity - starting_equity) / starting_equity
    else:
        equity_pnl_pct = None

    # IDM from diagnostics (target portfolio only, cannot compute from actual)
    idm_cap = config.get("idm_cap", 2.5)
    if "idm" in diagnostics.columns:
        idm_target = diagnostics["idm"].iloc[0] if len(diagnostics) > 0 else None
    else:
        idm_target = None

    sanity_checks = {
        "as_of_date": as_of_date,
        "current_equity": round(current_equity, 2),
        "initial_capital": round(initial_capital, 2),
        "starting_equity": round(starting_equity, 2) if starting_equity is not None else None,
        "inception_date": inception_date,
        "equity_pnl_pct": round(equity_pnl_pct, 4) if equity_pnl_pct is not None else None,
        "checks": {
            "idm_target_portfolio": {
                "value": round(idm_target, 2) if idm_target is not None else None,
                "cap": idm_cap,
                "headroom": round(idm_cap - idm_target, 2)
                if idm_target is not None
                else None,
                "status": "pass"
                if idm_target is None or idm_target <= idm_cap
                else "fail",
                "note": "IDM from target portfolio only (cannot compute from actual positions)",
            },
            "min_position_sizes": min_size_check,
            "banned_instruments": {
                "count": len(banned_instruments),
                "instruments": banned_instruments,
                "status": "pass" if len(banned_instruments) == 0 else "warn",
            },
            "instrument_states": state_counts,
            "total_estimated_cost": round(total_cost, 2),
            "cost_as_pct_of_equity": round(cost_pct, 4),
        },
        "overall_status": "pass",
        "warnings": [
            f"{len(min_size_check['below_threshold'])} trade(s) below min_position_size threshold"
            if min_size_check["status"] == "warn"
            else None,
            "Using estimated spreads - check live order book before executing",
        ],
    }

    # Remove None warnings
    sanity_checks["warnings"] = [w for w in sanity_checks["warnings"] if w is not None]

    # Update overall status
    if any(
        check.get("status") == "fail"
        for check in sanity_checks["checks"].values()
        if isinstance(check, dict) and "status" in check
    ):
        sanity_checks["overall_status"] = "fail"
    elif any(
        check.get("status") == "warn"
        for check in sanity_checks["checks"].values()
        if isinstance(check, dict) and "status" in check
    ):
        sanity_checks["overall_status"] = "pass_with_warnings"
    else:
        sanity_checks["overall_status"] = "pass"

    # 11. Build audit bundle
    # Extract prices snapshot from actuals
    prices_snapshot = {}
    for inst in actuals.index:
        prices_snapshot[inst] = {
            "mark_price": round(actuals.loc[inst, "mark_price_usd"], 2),
            "contracts": round(actuals.loc[inst, "contracts"], 6),
            "notional": round(actuals.loc[inst, "notional_usd"], 2),
        }

    # Extract forecasts snapshot from diagnostics
    forecasts_snapshot = {}
    forecast_cols = [
        c
        for c in diagnostics.columns
        if "forecast" in c.lower() or c in ["combined_forecast"]
    ]
    if forecast_cols:
        for inst in diagnostics.index:
            inst_forecasts = {}
            for col in forecast_cols:
                if col in diagnostics.columns:
                    val = diagnostics.loc[inst, col]
                    if pd.notna(val):
                        inst_forecasts[col] = round(val, 2)
            if inst_forecasts:
                forecasts_snapshot[inst] = inst_forecasts

    # Extract constraints snapshot
    constraints_snapshot = {}
    if "idm" in diagnostics.columns and len(diagnostics) > 0:
        constraints_snapshot["idm_target"] = round(diagnostics["idm"].iloc[0], 2)
    if "gross_leverage" in diagnostics.columns and len(diagnostics) > 0:
        constraints_snapshot["gross_leverage_target"] = round(
            diagnostics["gross_leverage"].iloc[0], 2
        )
    if "overall_scalar" in diagnostics.columns and len(diagnostics) > 0:
        constraints_snapshot["overall_scalar"] = round(
            diagnostics["overall_scalar"].iloc[0], 2
        )
    constraints_snapshot[
        "note"
    ] = "IDM from target portfolio model, not actual positions"

    # Build target portfolio summary
    target_weights = {}
    target_notionals = {}
    for inst in targets.index:
        target_notionals[inst] = round(targets[inst], 2)
        target_weights[inst] = (
            round(targets[inst] / current_equity, 4) if current_equity > 0 else 0.0
        )

    audit_bundle = {
        "timestamp_utc": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "system_version": "research_v1",
        "as_of_date": as_of_date,
        "last_complete_bar_date": backtest_end_date,
        "data_lag_days": (
            datetime.utcnow().date() - pd.to_datetime(backtest_end_date).date()
        ).days,
        "advisory_cadence": "daily" if staleness_overlay_applied else "monthly",
        "backtest_metadata": populate_backtest_metadata(metadata, backtest_dir),
        "actual_positions": {
            "source_file": str(actual_positions_path),
            "timestamp": actuals["timestamp"].iloc[0]
            if len(actuals) > 0 and "timestamp" in actuals.columns
            else "unknown",
            "prices_snapshot": prices_snapshot,
        },
        "equity_info": {
            "current_equity_usd": round(current_equity, 2),
            "initial_capital_usd": round(initial_capital, 2),
            "starting_equity_usd": round(starting_equity, 2) if starting_equity is not None else None,
            "inception_date": inception_date,
            "total_pnl_pct": round(equity_pnl_pct, 4) if equity_pnl_pct is not None else None,
            "source": "manual_input",
        },
        "forecasts_snapshot": forecasts_snapshot,
        "constraints_snapshot": constraints_snapshot,
        "target_portfolio": {
            "target_weights": target_weights,
            "target_notionals": target_notionals,
        },
        "warnings": [
            f"Advisory based on data through {backtest_end_date} ({(datetime.utcnow().date() - pd.to_datetime(backtest_end_date).date()).days} day lag)",
            "Estimated costs - check live spreads",
            "Daily cadence advisory - staleness overlay applied"
            if staleness_overlay_applied
            else "Monthly cadence advisory - not for intraday decisions",
        ],
    }

    # Add staleness overlay section (V1)
    if staleness_overlay_applied:
        audit_bundle["staleness_overlay"] = {
            "applied": True,
            "as_of_date": str(dataset_date) if dataset_date else as_of_date,
            "expected_as_of_date": str(expected_date) if expected_date else None,
            "summary": staleness_summary,
            "overrides": staleness_audit if staleness_audit else {},
        }
    else:
        audit_bundle["staleness_overlay"] = {
            "applied": False,
            "reason": "no_data_status_file" if not data_status_path else "v0_mode",
        }

    # C4 multiplier modulation state for the operator-facing audit trail
    # (audit F3, 2026-05-06). c4_state was populated upstream alongside the
    # 30h panel-age fail-closed check.
    audit_bundle["c4_multiplier_state"] = c4_state

    logger.info("Trade plan generation complete")

    return deltas, sanity_checks, audit_bundle
