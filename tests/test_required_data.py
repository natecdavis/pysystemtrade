from datetime import date
from pathlib import Path

import pytest
import yaml

from sysdata.crypto import required_data as required_data_module
from sysdata.crypto.required_data import (
    build_required_data_status,
    get_active_rule_data_methods,
    get_active_rule_names,
    required_auxiliary_files,
)


def test_full_rules_active_rule_count_and_required_sources():
    config_path = "config/crypto_perps_full_rules.yaml"
    with open(config_path) as f:
        config = yaml.safe_load(f)

    active_rules = get_active_rule_names(config)
    methods = get_active_rule_data_methods(config)
    requirements = required_auxiliary_files(config_path, env_root=Path("."))

    assert len(active_rules) == 122
    assert "data.get_spx_price" in methods
    assert "data.get_xs_activity_forecast" in methods
    assert "data.get_xs_val_forecast" in methods
    assert "data.get_long_short_ratio" in methods
    assert "data.get_toptrader_long_short_ratio" in methods
    assert "data.get_inter_sector_forecast" in methods
    assert "data.get_hl_cross_sectional_median_funding" in methods

    assert "macro_factors" in requirements
    assert "active_addresses" in requirements
    assert "market_cap" in requirements
    assert "binance_oi_lsr" in requirements
    assert "sector_map" in requirements
    assert "hyperliquid_instruments" in requirements


def test_required_data_status_warns_without_failing(tmp_path, monkeypatch):
    # Isolate the resolver from the real repo data/ so this test asserts
    # missing-file behavior independently of what's checked in.
    isolated_repo = tmp_path / "isolated_repo_data"
    isolated_repo.mkdir()
    monkeypatch.setattr(required_data_module, "_REPO_DATA_DIR", isolated_repo)

    config = {
        "trading_rules": {
            "macro": {
                "function": "example.macro",
                "data": ["data.get_spx_price"],
            },
            "xs": {
                "function": "example.xs",
                "data": ["data.get_xs_activity_forecast"],
            },
        },
        "forecast_weights": {"macro": 0.5, "xs": 0.5},
        "use_fg_overlay": False,
        "use_mvrv_overlay": False,
    }
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(config))

    report = build_required_data_status(
        config_path,
        tmp_path,
        expected_as_of_date=date(2026, 4, 16),
    )

    assert report["summary"]["status"] == "warning"
    assert report["summary"]["warnings"] == 2
    assert report["sources"]["macro_factors"]["status"] == "warning"
    assert report["sources"]["active_addresses"]["status"] == "warning"


def test_resolve_path_falls_back_to_repo_data_dir(tmp_path, monkeypatch):
    # Verify env-first, repo-fallback resolution: a feed missing in env_root
    # should resolve to the repo copy when present.
    isolated_repo = tmp_path / "isolated_repo_data"
    isolated_repo.mkdir()
    fake_repo_file = isolated_repo / "macro_factors.parquet"
    fake_repo_file.write_bytes(b"")
    monkeypatch.setattr(required_data_module, "_REPO_DATA_DIR", isolated_repo)

    env_data_dir = tmp_path / "envdata"
    env_data_dir.mkdir()
    resolved = required_data_module._resolve_path(env_data_dir, "macro_factors.parquet")
    assert resolved == fake_repo_file
