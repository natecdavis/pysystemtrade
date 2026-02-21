#!/usr/bin/env python
"""
Verify that min_history configuration parameters are correctly wired through the system.

This script tests that:
1. DynamicUniverseManager accepts min_history_mode parameter
2. Config parameters are read and passed correctly
3. ValueError is raised for invalid modes

Usage:
    python scripts/verify_min_history_config.py
"""

import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sysdata.crypto.dynamic_universe import DynamicUniverseManager, MIN_HISTORY_ANY_RULE, MIN_HISTORY_ALL_RULES
from sysdata.crypto.walk_forward_costs import WalkForwardCostEstimator


def test_min_history_constants():
    """Verify that constants are defined correctly."""
    print("Testing minimum history constants...")

    assert MIN_HISTORY_ANY_RULE == 15, f"MIN_HISTORY_ANY_RULE should be 15, got {MIN_HISTORY_ANY_RULE}"
    assert MIN_HISTORY_ALL_RULES == 270, f"MIN_HISTORY_ALL_RULES should be 270, got {MIN_HISTORY_ALL_RULES}"

    print(f"  ✅ MIN_HISTORY_ANY_RULE = {MIN_HISTORY_ANY_RULE}")
    print(f"  ✅ MIN_HISTORY_ALL_RULES = {MIN_HISTORY_ALL_RULES}")


def test_min_history_mode_parameter():
    """Verify that min_history_mode parameter works correctly."""
    print("\nTesting min_history_mode parameter...")

    # Create a dummy cost estimator
    class DummyPriceData:
        def get_spot_prices(self, instrument_code):
            import pandas as pd
            return pd.Series(dtype=float)

        def get_spot_volume(self, instrument_code):
            import pandas as pd
            return pd.Series(dtype=float)

        def get_adv_notional(self, instrument_code):
            import pandas as pd
            return pd.Series(dtype=float)

    dummy_data = DummyPriceData()
    cost_estimator = WalkForwardCostEstimator(prices_data=dummy_data)

    # Test 'any_rule' mode
    manager_any = DynamicUniverseManager(
        cost_estimator=cost_estimator,
        min_history_mode='any_rule'
    )
    assert manager_any._min_history_days == MIN_HISTORY_ANY_RULE, \
        f"'any_rule' mode should set threshold to {MIN_HISTORY_ANY_RULE}, got {manager_any._min_history_days}"
    print(f"  ✅ 'any_rule' mode → {manager_any._min_history_days} days")

    # Test 'all_rules' mode
    manager_all = DynamicUniverseManager(
        cost_estimator=cost_estimator,
        min_history_mode='all_rules'
    )
    assert manager_all._min_history_days == MIN_HISTORY_ALL_RULES, \
        f"'all_rules' mode should set threshold to {MIN_HISTORY_ALL_RULES}, got {manager_all._min_history_days}"
    print(f"  ✅ 'all_rules' mode → {manager_all._min_history_days} days")

    # Test invalid mode (should raise ValueError)
    try:
        manager_invalid = DynamicUniverseManager(
            cost_estimator=cost_estimator,
            min_history_mode='invalid_mode'
        )
        raise AssertionError("Should have raised ValueError for invalid mode")
    except ValueError as e:
        print(f"  ✅ Invalid mode raises ValueError: {str(e)}")


def test_config_parameters():
    """Test that config parameters match expected format."""
    print("\nTesting config YAML parameters...")

    import yaml

    # Test baseline config
    baseline_path = Path(__file__).parent.parent / "config" / "crypto_perps_test_365d_baseline.yaml"
    with open(baseline_path) as f:
        baseline_config = yaml.safe_load(f)

    du_config = baseline_config.get('dynamic_universe', {})
    assert 'min_history_days_topk' in du_config, "min_history_days_topk missing from config"
    assert 'min_history_rule_requirement' in du_config, "min_history_rule_requirement missing from config"
    assert du_config['min_history_days_topk'] == 365, "Baseline should have 365d threshold"
    assert du_config['min_history_rule_requirement'] == 'any_rule', "Baseline should use 'any_rule' mode"
    print(f"  ✅ Baseline config: topk={du_config['min_history_days_topk']}d, mode={du_config['min_history_rule_requirement']}")

    # Test alternative 1 config
    alt1_path = Path(__file__).parent.parent / "config" / "crypto_perps_test_15d_any_rule.yaml"
    with open(alt1_path) as f:
        alt1_config = yaml.safe_load(f)

    du_config = alt1_config.get('dynamic_universe', {})
    assert du_config['min_history_days_topk'] == 15, "Alt1 should have 15d threshold"
    assert du_config['min_history_rule_requirement'] == 'any_rule', "Alt1 should use 'any_rule' mode"
    print(f"  ✅ Alt1 config: topk={du_config['min_history_days_topk']}d, mode={du_config['min_history_rule_requirement']}")

    # Test alternative 2 config
    alt2_path = Path(__file__).parent.parent / "config" / "crypto_perps_test_270d_all_rules.yaml"
    with open(alt2_path) as f:
        alt2_config = yaml.safe_load(f)

    du_config = alt2_config.get('dynamic_universe', {})
    assert du_config['min_history_days_topk'] == 270, "Alt2 should have 270d threshold"
    assert du_config['min_history_rule_requirement'] == 'all_rules', "Alt2 should use 'all_rules' mode"
    print(f"  ✅ Alt2 config: topk={du_config['min_history_days_topk']}d, mode={du_config['min_history_rule_requirement']}")


def main():
    """Run all verification tests."""
    print("=" * 60)
    print("Minimum History Configuration Verification")
    print("=" * 60)

    try:
        test_min_history_constants()
        test_min_history_mode_parameter()
        test_config_parameters()

        print("\n" + "=" * 60)
        print("✅ ALL TESTS PASSED")
        print("=" * 60)
        print("\nImplementation is correctly wired. Ready for dataset build & testing.")
        return 0

    except AssertionError as e:
        print(f"\n❌ TEST FAILED: {str(e)}")
        return 1
    except Exception as e:
        print(f"\n❌ UNEXPECTED ERROR: {str(e)}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == '__main__':
    sys.exit(main())
