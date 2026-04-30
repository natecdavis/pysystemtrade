from datetime import date
from pathlib import Path

import yaml

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

    assert len(active_rules) == 68
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


def test_required_data_status_warns_without_failing(tmp_path):
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
