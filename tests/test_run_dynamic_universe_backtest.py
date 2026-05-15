"""
Wiring tests for `scripts/run_dynamic_universe_backtest.build_dynamic_universe_config`.

Regression coverage for the bug where keys declared in the live YAML's
`dynamic_universe` block (notably `min_annual_vol`) were silently dropped on
their way to `DynamicUniverseManager`, causing the configured vol floor to be a
no-op in every backtest.
"""

import inspect
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "scripts"))

from run_dynamic_universe_backtest import build_dynamic_universe_config  # noqa: E402

from sysdata.crypto.dynamic_universe import DynamicUniverseManager  # noqa: E402


# Keys the downstream consumer (`parquetCryptoPerpsSimData._init_dynamic_universe`
# and `DynamicUniverseManager.__init__`) reads from the dict. If a new key is
# added there, this list must grow and the helper must plumb it.
_EXPECTED_CONSUMED_KEYS = {
    'max_sr_cost_per_trade',
    'max_sr_cost_annual',
    'stack_turnover',
    'adv_window',
    'fee_bps',
    'vol_window',
    'min_annual_vol',
    'min_history_rule_requirement',
    'ivol_cap_enabled',
    'ivol_cap_percentile',
    'ivol_window',
    'forecast_weights',
}


class TestBuildDynamicUniverseConfig:
    def test_min_annual_vol_plumbed_from_yaml(self):
        """The exact bug: live YAML's `min_annual_vol: 0.2` must reach the dict."""
        raw_config = {'dynamic_universe': {'min_annual_vol': 0.2}}
        out = build_dynamic_universe_config(raw_config)
        assert out['min_annual_vol'] == 0.2

    def test_min_annual_vol_default_zero_when_absent(self):
        out = build_dynamic_universe_config({'dynamic_universe': {}})
        assert out['min_annual_vol'] == 0.0

    def test_min_annual_vol_default_zero_when_block_missing(self):
        out = build_dynamic_universe_config({})
        assert out['min_annual_vol'] == 0.0

    def test_helper_emits_every_key_the_consumer_reads(self):
        """Guardrail against the same bug class for any newly-consumed key."""
        out = build_dynamic_universe_config({})
        missing = _EXPECTED_CONSUMED_KEYS - set(out.keys())
        assert not missing, (
            f"Helper output is missing keys the downstream consumer reads: {missing}. "
            "If you added a new config key, plumb it in build_dynamic_universe_config "
            "and extend _EXPECTED_CONSUMED_KEYS here."
        )

    def test_full_live_config_shape_plumbs_through(self):
        """Approximate the live `crypto_perps_1k.yaml` shape and verify every
        nested key lands in the helper output."""
        raw_config = {
            'forecast_weights': {'ewmac_8': 0.5, 'breakout_20': 0.5},
            'dynamic_universe': {
                'max_sr_cost_per_trade': 0.01,
                'max_sr_cost_annual': 0.13,
                'stack_turnover': 15.0,
                'adv_window': 252,
                'fee_bps': 4.5,
                'vol_window': 63,
                'min_annual_vol': 0.2,
                'min_history_rule_requirement': 'any_rule',
                'ivol_cap_enabled': False,
                'ivol_cap_percentile': 75,
                'ivol_window': 35,
            },
        }
        out = build_dynamic_universe_config(raw_config)
        for k, v in raw_config['dynamic_universe'].items():
            assert out[k] == v, f"YAML's {k}={v} not plumbed (got {out.get(k)!r})"
        assert out['forecast_weights'] == raw_config['forecast_weights']

    def test_ivol_window_falls_back_to_vol_window(self):
        """`ivol_window` is allowed to inherit `vol_window` when not set explicitly."""
        out = build_dynamic_universe_config({'dynamic_universe': {'vol_window': 63}})
        assert out['ivol_window'] == 63

    def test_forecast_weights_read_from_top_level_not_du_block(self):
        """`forecast_weights` lives at YAML top level, not nested under
        `dynamic_universe` — the helper must reach up to grab it."""
        raw_config = {
            'forecast_weights': {'ewmac_8': 1.0},
            'dynamic_universe': {},
        }
        out = build_dynamic_universe_config(raw_config)
        assert out['forecast_weights'] == {'ewmac_8': 1.0}


class TestEndToEndPlumbing:
    """Helper output → DynamicUniverseManager constructor must respect the floor."""

    def test_manager_receives_nonzero_vol_floor(self):
        raw_config = {'dynamic_universe': {'min_annual_vol': 0.2}}
        cfg = build_dynamic_universe_config(raw_config)

        # Mirror the `_init_dynamic_universe` consumer's read pattern. We can't
        # instantiate a full manager without a cost_estimator stub, but we CAN
        # introspect the constructor to verify the helper key matches the
        # parameter the manager expects.
        sig = inspect.signature(DynamicUniverseManager.__init__)
        assert 'min_annual_vol' in sig.parameters, (
            "DynamicUniverseManager no longer accepts 'min_annual_vol' — update "
            "build_dynamic_universe_config and _EXPECTED_CONSUMED_KEYS."
        )
        # The helper's key matches the manager's parameter name.
        manager_kwarg_value = cfg.get('min_annual_vol', 0.0)
        assert manager_kwarg_value == 0.2


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
