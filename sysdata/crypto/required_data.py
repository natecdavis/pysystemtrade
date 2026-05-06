"""
Required data discovery and freshness checks for active crypto trading rules.

The live pipeline uses these helpers to make silent data dropouts visible without
turning auxiliary-provider outages into hard failures.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

# Repo-root data/ is the canonical location for static/backfill files (sector_map,
# binance_volume_daily). env_root/data/ takes precedence when it exists there, but
# we fall back to repo data/ so the pipeline works without manual copying.
_REPO_DATA_DIR = Path(__file__).parent.parent.parent / "data"


def _resolve_path(env_data_dir: Path, filename: str) -> Path:
    """Return env_data_dir/filename if present, else fall back to repo data/filename."""
    env_path = env_data_dir / filename
    if env_path.exists():
        return env_path
    repo_path = _REPO_DATA_DIR / filename
    if repo_path.exists():
        return repo_path
    return env_path  # canonical for error reporting


BASE_DATA_METHODS = {
    "data.daily_prices",
    "rawdata.daily_returns_volatility",
    "data.get_normalised_price_this_instrument",
    "data.get_normalised_price_for_asset_class",
    "data.get_asset_class_index_price",
    "data.get_btc_price",
    "data.get_adv_notional",
    "data.get_funding_rate",
    "data.get_cross_sectional_median_funding",
    "data.get_xs_carry_forecast",
    "data.get_skew_abs_90_forecast",
    "data.get_skew_abs_180_forecast",
    "data.get_skew_abs_365_forecast",
    "data.get_skew_rv_90_forecast",
    "data.get_skew_rv_180_forecast",
    "data.get_skew_rv_365_forecast",
    "data.get_xs_vol_zscore",
}

MACRO_METHODS = {
    "data.get_spx_price",
    "data.get_dxy_price",
    "data.get_us10y_yield",
    "data.get_gold_price",
    "data.get_vix_level",
    "data.get_oil_price",
}

VOLUME_METHODS = {
    "data.get_daily_volume",
}

ACTIVE_ADDRESS_METHODS = {
    "data.get_xs_activity_forecast",
    "data.get_xs_val_forecast",
}

MARKET_CAP_METHODS = {
    "data.get_xs_val_forecast",
}

SECTOR_METHODS = {
    "data.get_sector_index_price",
    "data.get_inter_sector_forecast",
}

OI_METHODS = {
    "data.get_open_interest",
    "data.get_long_short_ratio",
    "data.get_toptrader_long_short_ratio",
    "data.get_xs_oi_change_zscore",
}

HL_METHODS = {
    "data.get_hl_cross_sectional_median_funding",
}


def load_config_dict(config_path: Path) -> dict[str, Any]:
    with open(config_path) as f:
        return yaml.safe_load(f) or {}


def get_active_rule_names(config: dict[str, Any]) -> list[str]:
    rules = config.get("trading_rules", {}) or {}
    weights = config.get("forecast_weights", {}) or {}
    active = [
        name
        for name, weight in weights.items()
        if name in rules and float(weight or 0.0) != 0.0
    ]
    return sorted(active)


def get_active_rule_data_methods(config: dict[str, Any]) -> set[str]:
    rules = config.get("trading_rules", {}) or {}
    methods: set[str] = set()
    for rule_name in get_active_rule_names(config):
        for method in rules.get(rule_name, {}).get("data", []) or []:
            methods.add(str(method))
        if not rules.get(rule_name, {}).get("data"):
            methods.add("data.daily_prices")
    return methods


def required_auxiliary_files(
    config_path: Path,
    env_root: Path,
) -> dict[str, dict[str, Any]]:
    config = load_config_dict(config_path)
    methods = get_active_rule_data_methods(config)
    data_dir = env_root / "data"

    requirements: dict[str, dict[str, Any]] = {
        "binance_price_funding": {
            "path": None,
            "required_by": sorted(methods & BASE_DATA_METHODS),
            "max_lag_days": 1,
            "kind": "raw_binance",
        }
    }

    if methods & MACRO_METHODS:
        requirements["macro_factors"] = {
            "path": _resolve_path(data_dir, "macro_factors.parquet"),
            "required_by": sorted(methods & MACRO_METHODS),
            "max_lag_days": 3,
            "kind": "parquet_index",
        }

    if methods & ACTIVE_ADDRESS_METHODS:
        requirements["active_addresses"] = {
            "path": _resolve_path(data_dir, "active_addresses.parquet"),
            "required_by": sorted(methods & ACTIVE_ADDRESS_METHODS),
            "max_lag_days": 2,
            "kind": "parquet_index",
        }

    if methods & MARKET_CAP_METHODS:
        requirements["market_cap"] = {
            "path": _resolve_path(data_dir, "market_cap.parquet"),
            "required_by": sorted(methods & MARKET_CAP_METHODS),
            "max_lag_days": 2,
            "kind": "parquet_index",
        }

    if methods & SECTOR_METHODS:
        requirements["sector_map"] = {
            "path": _resolve_path(data_dir, "sector_map.json"),
            "required_by": sorted(methods & SECTOR_METHODS),
            "max_lag_days": None,
            "kind": "json_static",
        }

    if methods & OI_METHODS:
        requirements["binance_oi_lsr"] = {
            "path": _resolve_path(data_dir, "binance_oi_processed.parquet"),
            "required_by": sorted(methods & OI_METHODS),
            "max_lag_days": 2,
            "kind": "parquet_date_column",
        }

    if methods & HL_METHODS:
        requirements["hyperliquid_instruments"] = {
            "path": _resolve_path(data_dir, "hyperliquid_instruments.json"),
            "required_by": sorted(methods & HL_METHODS),
            "max_lag_days": 2,
            "kind": "json_fetched_at",
        }

    if methods & VOLUME_METHODS:
        requirements["binance_volume"] = {
            "path": _resolve_path(data_dir, "binance_volume_daily.parquet"),
            "required_by": sorted(methods & VOLUME_METHODS),
            "max_lag_days": 2,
            "kind": "parquet_date_column",
        }

    return requirements


def _normalise_date(value: Any) -> date | None:
    if value is None or pd.isna(value):
        return None
    try:
        ts = pd.Timestamp(value)
    except Exception:
        return None
    if ts.tzinfo is not None:
        ts = ts.tz_convert(None)
    return ts.date()


def _latest_parquet_date(path: Path, kind: str) -> date | None:
    df = pd.read_parquet(path)
    if df.empty:
        return None
    if kind == "parquet_date_column" and "date" in df.columns:
        return _normalise_date(pd.to_datetime(df["date"]).max())
    return _normalise_date(df.index.max())


def _latest_json_date(path: Path, kind: str) -> date | None:
    with open(path) as f:
        payload = json.load(f)
    if kind == "json_fetched_at":
        return _normalise_date(payload.get("fetched_at"))
    return None


def build_required_data_status(
    config_path: Path,
    env_root: Path,
    expected_as_of_date: date,
) -> dict[str, Any]:
    requirements = required_auxiliary_files(config_path, env_root)
    warnings: list[str] = []
    sources: dict[str, Any] = {}

    for name, req in requirements.items():
        path = req["path"]
        status = {
            "required_by": req["required_by"],
            "path": str(path) if path is not None else None,
            "kind": req["kind"],
            "max_lag_days": req["max_lag_days"],
            "exists": True,
            "latest_date": None,
            "lag_days": None,
            "status": "ok",
            "warning": None,
        }

        if path is None:
            status["status"] = "covered_elsewhere"
            status["warning"] = (
                "Checked by raw_data_status.json / dataset build, not by aux status."
            )
            sources[name] = status
            continue

        if not path.exists():
            status["exists"] = False
            status["status"] = "warning"
            status["warning"] = f"Required data file is missing: {path}"
            warnings.append(status["warning"])
            sources[name] = status
            continue

        try:
            if req["kind"].startswith("parquet"):
                latest = _latest_parquet_date(path, req["kind"])
            elif req["kind"].startswith("json"):
                latest = _latest_json_date(path, req["kind"])
            else:
                latest = None
        except Exception as exc:
            status["status"] = "warning"
            status["warning"] = f"Could not inspect {path}: {exc}"
            warnings.append(status["warning"])
            sources[name] = status
            continue

        if req["kind"] == "json_static":
            try:
                with open(path) as f:
                    payload = json.load(f)
                named = {value for value in payload.values() if value != "Other"}
                status["named_sector_count"] = len(named)
                if name == "sector_map" and len(named) < 2:
                    status["status"] = "warning"
                    status["warning"] = (
                        f"sector_map has only {len(named)} named sectors; "
                        "inter-sector forecasts may be all NaN."
                    )
                    warnings.append(status["warning"])
            except Exception as exc:
                status["status"] = "warning"
                status["warning"] = f"Could not inspect {path}: {exc}"
                warnings.append(status["warning"])
            sources[name] = status
            continue

        if latest is not None:
            lag_days = (expected_as_of_date - latest).days
            status["latest_date"] = str(latest)
            status["lag_days"] = lag_days
            max_lag = req["max_lag_days"]
            if max_lag is not None and lag_days > max_lag:
                status["status"] = "warning"
                status["warning"] = (
                    f"{name} latest date {latest} is {lag_days} days behind "
                    f"expected {expected_as_of_date} (allowed {max_lag})."
                )
                warnings.append(status["warning"])

        sources[name] = status

    config = load_config_dict(config_path)
    return {
        "generated_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "config_path": str(config_path),
        "env_root": str(env_root),
        "expected_as_of_date": str(expected_as_of_date),
        "active_rule_count": len(get_active_rule_names(config)),
        "active_data_methods": sorted(get_active_rule_data_methods(config)),
        "sources": sources,
        "summary": {
            "warnings": len(warnings),
            "status": "warning" if warnings else "ok",
        },
        "warnings": warnings,
    }


def write_required_data_status(
    config_path: Path,
    env_root: Path,
    expected_as_of_date: date,
    output_path: Path,
) -> dict[str, Any]:
    report = build_required_data_status(config_path, env_root, expected_as_of_date)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(report, f, indent=2)
    return report
