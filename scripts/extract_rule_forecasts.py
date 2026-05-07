#!/usr/bin/env python3
"""
Extract capped forecast panels and daily return panel for walk-forward weight calibration.

Builds the trading system once and extracts per-rule capped forecasts (±20 scale) for
all instruments and dates. Capped forecasts are independent of forecast weights — they
are rule outputs multiplied by forecast scalars only — so there is no circular dependency
with the walk-forward calibration.

Writes to --outdir:
  forecasts.parquet  — DataFrame, MultiIndex columns (rule, instrument), date index
  returns.parquet    — DataFrame, instrument columns, date index, daily log-returns

Runtime: ~20-60 min for 319 instruments × 41 rules (depends on hardware and cache state).
Outputs are reusable; re-run only if the dataset or rule set changes.

Usage:
    python scripts/extract_rule_forecasts.py \\
        --config config/crypto_perps_full_rules.yaml \\
        --data data/dataset_538registry_6yr_jagged.parquet \\
        --outdir data/forecast_panels
"""

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

from sysdata.config.configdata import Config
from sysdata.crypto.parquet_perps_sim_data import parquetCryptoPerpsSimData
from systems.basesystem import System
from systems.forecasting import Rules
from systems.forecast_scale_cap import ForecastScaleCap
from systems.rawdata import RawData
from systems.positionsizing import PositionSizing
from systems.accounts.accounts_stage import Account
from systems.crypto_perps.forecast_combine_gated import ForecastCombineGated
from systems.provided.crypto_example.core.dynamic_portfolio import CryptoDynamicPortfolio

try:
    from syscore.constants import arg_not_supplied
except ImportError:
    arg_not_supplied = None

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")


# ---------------------------------------------------------------------------
# System builder
# ---------------------------------------------------------------------------

def build_system(config_path: str, data_path: str) -> System:
    with open(config_path) as f:
        cfg_dict = yaml.safe_load(f)
    config = Config(cfg_dict)

    repo_root = Path(__file__).resolve().parent.parent

    def _resolve(*candidates: Path) -> str:
        for p in candidates:
            if p.exists():
                return str(p)
        return arg_not_supplied

    data_dir = Path(data_path).parent

    macro_kwarg = _resolve(
        data_dir / "macro_factors.parquet",
        repo_root / "data" / "macro_factors.parquet",
    )
    sector_kwarg = _resolve(
        data_dir / "sector_map.json",
        repo_root / "data" / "sector_map.json",
    )
    volume_kwarg = _resolve(
        data_dir / "binance_volume_daily.parquet",
        repo_root / "data" / "binance_volume_daily.parquet",
    )
    oi_kwarg = _resolve(
        data_dir / "binance_oi_processed.parquet",
        repo_root / "data" / "binance_oi_processed.parquet",
    )
    fg_kwarg = _resolve(
        data_dir / "fg_index.parquet",
        repo_root / "data" / "fg_index.parquet",
    )
    mvrv_kwarg = _resolve(
        data_dir / "mvrv_index.parquet",
        repo_root / "data" / "mvrv_index.parquet",
    )
    aa_kwarg = _resolve(
        data_dir / "active_addresses.parquet",
        repo_root / "data" / "active_addresses.parquet",
    )
    mcap_kwarg = _resolve(
        data_dir / "market_cap.parquet",
        repo_root / "data" / "market_cap.parquet",
    )
    hl_kwarg = _resolve(
        data_dir / "hyperliquid_instruments.json",
        repo_root / "data" / "hyperliquid_instruments.json",
    )
    stablecoin_kwarg = _resolve(
        data_dir / "stablecoin_supply.parquet",
        repo_root / "data" / "stablecoin_supply.parquet",
    )
    etf_kwarg = _resolve(
        data_dir / "etf_flows.parquet",
        repo_root / "data" / "etf_flows.parquet",
    )
    basis_kwarg = _resolve(
        data_dir / "binance_premium_index_processed.parquet",
        repo_root / "envs" / "dev" / "data" / "binance_premium_index_processed.parquet",
        repo_root / "data" / "binance_premium_index_processed.parquet",
    )

    data = parquetCryptoPerpsSimData(
        data_path,
        macro_data_path=macro_kwarg,
        sector_map_path=sector_kwarg,
        volume_data_path=volume_kwarg,
        oi_data_path=oi_kwarg,
        fg_data_path=fg_kwarg,
        mvrv_data_path=mvrv_kwarg,
        active_addresses_data_path=aa_kwarg,
        market_cap_data_path=mcap_kwarg,
        hl_instruments_path=hl_kwarg,
        stablecoin_supply_path=stablecoin_kwarg,
        etf_flows_path=etf_kwarg,
        premium_index_path=basis_kwarg,
    )

    return System(
        [RawData(), Rules(), ForecastScaleCap(), ForecastCombineGated(),
         PositionSizing(), CryptoDynamicPortfolio(), Account()],
        data,
        config,
    )


def get_active_rules(config_path: str, include_zero_weight: bool = False) -> list:
    """Return rule names from forecast_weights in the config.

    Parameters
    ----------
    include_zero_weight : bool
        If True, include rules with weight == 0 (the rejected / held-out candidates).
        Use this to extract forecast panels for the full candidate pool.
    """
    with open(config_path) as f:
        cfg_dict = yaml.safe_load(f)
    fw = cfg_dict.get("forecast_weights", {})
    if include_zero_weight:
        rules = [r for r, w in fw.items() if isinstance(w, (int, float))]
    else:
        rules = [r for r, w in fw.items() if isinstance(w, (int, float)) and w > 0]
    return sorted(rules)


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

def _atomic_write_parquet(df: pd.DataFrame, path: Path) -> None:
    """Write parquet via tmp + os.replace so a mid-write crash doesn't leave
    a half-written file behind. Same pattern as live-state writes."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    df.to_parquet(tmp)
    os.replace(tmp, path)


def _merge_incremental(
    old: pd.DataFrame,
    new: pd.DataFrame,
    since: pd.Timestamp,
) -> pd.DataFrame:
    """Splice `new` (dates >= since) onto `old` (full history). Drop any
    existing rows in `old` at dates >= since first (idempotent — re-running
    for the same date overwrites that day's row, doesn't double-append).

    Returns the merged DataFrame, sorted by date with no duplicate dates.

    Equality invariant (enforced by tests): if `new` is the strict tail of a
    full-history compute, then this merge function applied to `old` (the
    truncated head) must produce a DataFrame identical to the original full
    history within the date range covered by `new`.
    """
    old_head = old.loc[old.index < since]
    merged = pd.concat([old_head, new]).sort_index()
    # Defensive against duplicate dates from upstream — keep the latest row.
    merged = merged[~merged.index.duplicated(keep="last")]
    return merged


def extract_panels(
    config_path: str, data_path: str, out_dir: Path,
    include_zero_weight: bool = False,
    since: Optional[pd.Timestamp] = None,
) -> None:
    """Extract per-rule capped forecasts and per-instrument log-returns into
    parquet panels.

    Modes:
      - Full rebuild (default, `since=None`): build system, compute every
        (rule, instrument) forecast, write panels from scratch.
      - Incremental append (`since=<date>`): load existing panels, build
        system on the new dataset, compute forecasts, slice each (rule, instr)
        series to dates >= `since`, drop any existing rows >= `since` from the
        loaded panels (idempotent), concatenate the new tail, write atomically.

    The bulk of the runtime is the system construction + forecast iteration —
    which we do anyway. The incremental mode just emits less to disk.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    fc_path = out_dir / "forecasts.parquet"
    ret_path = out_dir / "returns.parquet"

    existing_forecasts: Optional[pd.DataFrame] = None
    existing_returns: Optional[pd.DataFrame] = None
    if since is not None:
        if not fc_path.exists() or not ret_path.exists():
            print(
                f"\nERROR: incremental mode (--since {since.date()}) requires "
                f"existing panels at {fc_path} and {ret_path}. "
                f"Run a full extract first (omit --since)."
            )
            sys.exit(1)
        print(f"Loading existing panels for incremental append (since={since.date()})...")
        existing_forecasts = pd.read_parquet(fc_path)
        existing_returns = pd.read_parquet(ret_path)
        print(f"  existing forecasts: {existing_forecasts.shape}, "
              f"last date: {existing_forecasts.index.max().date() if len(existing_forecasts) else 'empty'}")
        print(f"  existing returns:   {existing_returns.shape}")

    print(f"Building system from {config_path} / {data_path}...")
    system = build_system(config_path, data_path)

    active_rules = get_active_rules(config_path, include_zero_weight=include_zero_weight)
    instruments = system.data.get_instrument_list()

    print(
        f"Extracting forecasts: {len(active_rules)} rules × {len(instruments)} instruments"
    )
    if since is None:
        print("(Progress shown per rule. Expect 20-60 min total.)\n")
    else:
        print(f"(Incremental: emitting only rows >= {since.date()}.)\n")

    forecast_dict: dict = {}
    return_dict: dict = {}

    # Silent-failure tally: keep the per-(rule, instrument) loop's bare
    # `except Exception: pass` semantics (one failed rule shouldn't crash
    # the whole extract), but record everything so the operator gets
    # actionable visibility instead of "0 instruments" with no reason
    # (audit F7, 2026-05-06; bridges F2 — silent OI/BTC-dom rule failures).
    from collections import Counter
    silent_counts: Counter = Counter()
    silent_examples: dict = {}

    def _record_silent(scope: str, exc: BaseException, instrument: str) -> None:
        key = (scope, type(exc).__name__)
        silent_counts[key] += 1
        if key not in silent_examples:
            silent_examples[key] = (instrument, str(exc))

    # --- Returns (fast — just price series) ---
    print("Extracting returns...", end=" ", flush=True)
    for inst in instruments:
        try:
            price = system.data.daily_prices(inst)
            if price is not None and len(price.dropna()) > 10:
                ret = np.log(price / price.shift(1))
                if since is not None:
                    ret = ret.loc[ret.index >= since]
                if not ret.empty:
                    return_dict[inst] = ret
        except Exception as exc:
            _record_silent("__returns__", exc, inst)
    print(f"done ({len(return_dict)} instruments with valid prices)")

    # --- Forecasts per rule ---
    for i, rule in enumerate(active_rules, 1):
        count = 0
        for inst in instruments:
            try:
                fc = system.forecastScaleCap.get_capped_forecast(inst, rule)
                if fc is not None and not fc.dropna().empty:
                    if since is not None:
                        fc = fc.loc[fc.index >= since]
                        if fc.empty:
                            continue
                    forecast_dict[(rule, inst)] = fc
                    count += 1
            except Exception as exc:
                _record_silent(rule, exc, inst)
        print(f"  [{i:2d}/{len(active_rules)}] {rule:<35} {count} instruments")

    # --- Silent-failure summary (audit F7) ---
    # Groups failures by (rule|"__returns__", exception_type) so a recurring
    # failure like "all 477 attn_exhaustion_fade calls KeyError on
    # 'BTCUSDT_PERP'" surfaces as one line, with the message text on the
    # first occurrence preserved as a hint. NOT a fail-closed gate — keeps
    # backwards-compatible swallow behavior; this is purely for visibility.
    if silent_counts:
        total = sum(silent_counts.values())
        n_groups = len(silent_counts)
        print(f"\n=== Silent extraction failures: {total} across {n_groups} (scope, exception) groups ===")
        # Sort: largest group first so the loudest signal lands first.
        for (scope, exc_type), n in sorted(silent_counts.items(), key=lambda x: -x[1]):
            ex_inst, ex_msg = silent_examples[(scope, exc_type)]
            short_msg = ex_msg.replace("\n", " ")
            if len(short_msg) > 100:
                short_msg = short_msg[:97] + "..."
            print(
                f"  {scope:<35} {exc_type:<22} ×{n:>4}  e.g. {ex_inst}: {short_msg}"
            )
        print(
            "  (Each line is a silently-skipped (rule, instrument) pair group. "
            "If the count == n_instruments for an active forecast_weights rule, "
            "that rule is contributing zero to the combined forecast today.)"
        )

    if not forecast_dict and since is None:
        print("\nERROR: No forecasts extracted. Check config and data paths.")
        sys.exit(1)
    if not forecast_dict and since is not None:
        print(f"\nWARNING: No forecasts emitted for since={since.date()} — "
              f"nothing to append. Existing panels left unchanged.")
        return

    # --- Build MultiIndex DataFrame for the new tail ---
    print("\nBuilding forecast panel...", end=" ", flush=True)
    new_forecasts = pd.DataFrame(forecast_dict)
    new_forecasts.columns = pd.MultiIndex.from_tuples(
        new_forecasts.columns.tolist(), names=["rule", "instrument"]
    )
    print(f"done  shape={new_forecasts.shape}")

    new_returns = pd.DataFrame(return_dict)
    print(f"Return panel shape={new_returns.shape}")

    # --- Merge with existing panels (incremental) or write fresh (full) ---
    if since is None:
        forecast_out = new_forecasts
        return_out = new_returns
    else:
        forecast_out = _merge_incremental(existing_forecasts, new_forecasts, since)
        return_out = _merge_incremental(existing_returns, new_returns, since)

    # --- Atomic write ---
    _atomic_write_parquet(forecast_out, fc_path)
    _atomic_write_parquet(return_out, ret_path)

    print(f"\nSaved:")
    print(f"  {fc_path}  ({fc_path.stat().st_size / 1e6:.1f} MB)  shape={forecast_out.shape}")
    print(f"  {ret_path}  ({ret_path.stat().st_size / 1e6:.1f} MB)  shape={return_out.shape}")
    if since is not None:
        print(f"  Appended {len(new_forecasts)} new dates from {since.date()} onwards.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract capped forecast panels for walk-forward weight calibration",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--config", required=True, help="Path to config YAML")
    parser.add_argument("--data", required=True, help="Path to parquet dataset")
    parser.add_argument(
        "--outdir",
        default="data/forecast_panels",
        help="Output directory (default: data/forecast_panels)",
    )
    parser.add_argument(
        "--all-rules", action="store_true",
        help="Include zero-weight (rejected/held-out) rules in extraction",
    )
    parser.add_argument(
        "--since",
        type=str,
        default=None,
        help="Incremental-append mode: only emit rows with date >= YYYY-MM-DD. "
        "Loads existing panels in --outdir, drops any existing rows >= since-date, "
        "appends the freshly-computed tail. Atomic write (tmp + os.replace). "
        "If existing panels are missing, fails with a clear message.",
    )
    args = parser.parse_args()

    since_ts = pd.Timestamp(args.since) if args.since else None

    extract_panels(args.config, args.data, Path(args.outdir),
                   include_zero_weight=args.all_rules,
                   since=since_ts)


if __name__ == "__main__":
    main()
