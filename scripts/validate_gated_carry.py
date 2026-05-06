#!/usr/bin/env python3
"""
Quick validation script for trend-gated carry implementation.

Tests:
1. Vol-normalized carry rule can be imported and called
2. ForecastCombineGated can be instantiated
3. Config parameters are correctly defined
4. Gating logic is activated by config flag

Usage:
    python scripts/validate_gated_carry.py
"""

import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))


def test_imports():
    """Test that all new modules can be imported."""
    print("Testing imports...")

    try:
        from systems.crypto_perps.rules.rule_library import vol_normalized_carry
        print("✓ vol_normalized_carry imported successfully")
    except ImportError as e:
        print(f"✗ Failed to import vol_normalized_carry: {e}")
        return False

    try:
        from systems.crypto_perps.forecast_combine_gated import ForecastCombineGated
        print("✓ ForecastCombineGated imported successfully")
    except ImportError as e:
        print(f"✗ Failed to import ForecastCombineGated: {e}")
        return False

    return True


def test_config():
    """Test that config files are correctly defined."""
    print("\nTesting config files...")

    import yaml

    # Test baseline config
    baseline_path = Path("config/crypto_perps_full_rules.yaml")
    if not baseline_path.exists():
        print(f"✗ Baseline config not found: {baseline_path}")
        return False

    with open(baseline_path) as f:
        baseline = yaml.safe_load(f)

    # Check carry rules exist
    if 'vol_norm_carry_10' not in baseline.get('trading_rules', {}):
        print("✗ vol_norm_carry_10 not found in baseline trading_rules")
        return False
    print("✓ Carry rules defined in baseline config")

    # Check carry weights are 0.0
    weights = baseline.get('forecast_weights', {})
    if weights.get('vol_norm_carry_10', -1) != 0.0:
        print("✗ Baseline carry weights should be 0.0 (disabled)")
        return False
    print("✓ Baseline carry weights are 0.0 (disabled)")

    # Check gating parameters exist
    if 'use_gated_carry' not in baseline:
        print("✗ use_gated_carry not found in baseline config")
        return False
    if baseline['use_gated_carry'] != False:
        print("✗ use_gated_carry should be false in baseline")
        return False
    print("✓ Gating parameters defined correctly in baseline")

    # Test test config
    test_path = Path("config/crypto_perps_gated_carry_test.yaml")
    if not test_path.exists():
        print(f"✗ Test config not found: {test_path}")
        return False

    with open(test_path) as f:
        test_config = yaml.safe_load(f)

    # Check carry is enabled
    if test_config.get('use_gated_carry') != True:
        print("✗ use_gated_carry should be true in test config")
        return False

    # Check carry weights are non-zero
    test_weights = test_config.get('forecast_weights', {})
    if test_weights.get('vol_norm_carry_10', 0) == 0.0:
        print("✗ Test config carry weights should be > 0.0")
        return False
    print("✓ Test config has carry enabled with non-zero weights")

    return True


def test_rule_function():
    """Test that vol_normalized_carry can be called."""
    print("\nTesting vol_normalized_carry function...")

    import pandas as pd
    import numpy as np
    from systems.crypto_perps.rules.rule_library import vol_normalized_carry

    # Create dummy data
    dates = pd.date_range('2020-01-01', periods=100, freq='D')
    funding_rates = pd.Series(
        np.random.randn(100) * 0.0001,  # Typical funding rate scale
        index=dates,
        name='funding'
    )
    vol = pd.Series(
        np.abs(np.random.randn(100) * 0.02) + 0.01,  # Positive volatility
        index=dates,
        name='vol'
    )

    try:
        result = vol_normalized_carry(funding_rates, vol, smooth_days=10)

        if not isinstance(result, pd.Series):
            print(f"✗ Result should be pd.Series, got {type(result)}")
            return False

        if len(result) != 100:
            print(f"✗ Result should have 100 values, got {len(result)}")
            return False

        print(f"✓ vol_normalized_carry executed successfully")
        print(f"  Result range: [{result.min():.2f}, {result.max():.2f}]")

        return True

    except Exception as e:
        print(f"✗ Error calling vol_normalized_carry: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_forecast_combine_gated():
    """Test that ForecastCombineGated can be instantiated."""
    print("\nTesting ForecastCombineGated class...")

    from systems.crypto_perps.forecast_combine_gated import ForecastCombineGated

    try:
        # Just test instantiation (can't test full functionality without a system)
        combiner = ForecastCombineGated()
        print("✓ ForecastCombineGated instantiated successfully")

        # Check that diagnostic methods exist
        methods = ['get_trend_strength', 'get_raw_carry', 'get_ranked_carry', 'get_gated_carry']
        for method in methods:
            if not hasattr(combiner, method):
                print(f"✗ Missing method: {method}")
                return False
        print(f"✓ All diagnostic methods present")

        return True

    except Exception as e:
        print(f"✗ Error instantiating ForecastCombineGated: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """Run all validation tests."""
    print("="*80)
    print("Validating Trend-Gated Carry Implementation")
    print("="*80)

    tests = [
        ("Imports", test_imports),
        ("Config Files", test_config),
        ("Rule Function", test_rule_function),
        ("ForecastCombineGated", test_forecast_combine_gated),
    ]

    results = []
    for name, test_func in tests:
        try:
            result = test_func()
            results.append((name, result))
        except Exception as e:
            print(f"\n✗ {name} test crashed: {e}")
            import traceback
            traceback.print_exc()
            results.append((name, False))

    print("\n" + "="*80)
    print("Validation Summary")
    print("="*80)

    for name, result in results:
        status = "✓ PASS" if result else "✗ FAIL"
        print(f"{status:8} {name}")

    all_passed = all(r for _, r in results)

    if all_passed:
        print("\n✓ All validation tests passed!")
        print("\nNext steps:")
        print("1. Run baseline backtest to verify Sharpe 0.84")
        print("2. Run gated carry test backtest")
        print("3. Compare results")
        print("\nSee docs/_archive/2026-Q1/TESTING_GUIDE_GATED_CARRY.md for detailed instructions.")
        return 0
    else:
        print("\n✗ Some validation tests failed. Fix errors before running backtests.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
