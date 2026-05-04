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
import sys
from pathlib import Path

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

def extract_panels(
    config_path: str, data_path: str, out_dir: Path,
    include_zero_weight: bool = False,
) -> None:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Building system from {config_path} / {data_path}...")
    system = build_system(config_path, data_path)

    active_rules = get_active_rules(config_path, include_zero_weight=include_zero_weight)
    instruments = system.data.get_instrument_list()

    print(
        f"Extracting forecasts: {len(active_rules)} rules × {len(instruments)} instruments"
    )
    print("(Progress shown per rule. Expect 20-60 min total.)\n")

    forecast_dict: dict = {}
    return_dict: dict = {}

    # --- Returns (fast — just price series) ---
    print("Extracting returns...", end=" ", flush=True)
    for inst in instruments:
        try:
            price = system.data.daily_prices(inst)
            if price is not None and len(price.dropna()) > 10:
                return_dict[inst] = np.log(price / price.shift(1))
        except Exception:
            pass
    print(f"done ({len(return_dict)} instruments with valid prices)")

    # --- Forecasts per rule ---
    for i, rule in enumerate(active_rules, 1):
        count = 0
        for inst in instruments:
            if inst not in return_dict:
                continue
            try:
                fc = system.forecastScaleCap.get_capped_forecast(inst, rule)
                if fc is not None and not fc.dropna().empty:
                    forecast_dict[(rule, inst)] = fc
                    count += 1
            except Exception:
                pass
        print(f"  [{i:2d}/{len(active_rules)}] {rule:<35} {count} instruments")

    if not forecast_dict:
        print("\nERROR: No forecasts extracted. Check config and data paths.")
        sys.exit(1)

    # --- Build MultiIndex DataFrame ---
    print("\nBuilding forecast panel...", end=" ", flush=True)
    forecast_df = pd.DataFrame(forecast_dict)
    forecast_df.columns = pd.MultiIndex.from_tuples(
        forecast_df.columns.tolist(), names=["rule", "instrument"]
    )
    print(f"done  shape={forecast_df.shape}")

    return_df = pd.DataFrame(return_dict)
    print(f"Return panel shape={return_df.shape}")

    # --- Save ---
    fc_path = out_dir / "forecasts.parquet"
    ret_path = out_dir / "returns.parquet"
    forecast_df.to_parquet(fc_path)
    return_df.to_parquet(ret_path)

    print(f"\nSaved:")
    print(f"  {fc_path}  ({fc_path.stat().st_size / 1e6:.1f} MB)")
    print(f"  {ret_path}  ({ret_path.stat().st_size / 1e6:.1f} MB)")


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
    args = parser.parse_args()

    extract_panels(args.config, args.data, Path(args.outdir),
                   include_zero_weight=args.all_rules)


if __name__ == "__main__":
    main()
