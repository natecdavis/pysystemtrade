#!/usr/bin/env python
"""
Verification script for OI overlay implementation.

Checks that all components are properly integrated before running full backtests.
"""

import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import yaml
import pandas as pd
from sysdata.config.configdata import Config
from sysdata.crypto.parquet_perps_sim_data import parquetCryptoPerpsSimData
from systems.crypto_perps.crypto_portfolio_oi_overlay import (
    CryptoPortfolioWithOIOverlay,
    CryptoDynamicPortfolioWithOIOverlay,
    apply_oi_overlay,
)


def test_config_loading():
    """Test that all config files load correctly."""
    print("=" * 80)
    print("TEST 1: Configuration File Loading")
    print("=" * 80)

    configs = [
        "config/crypto_perps_oi_baseline.yaml",
        "config/crypto_perps_oi_overlay_only.yaml",
        "config/crypto_perps_oi_crowding_only.yaml",
        "config/crypto_perps_oi_test.yaml",
    ]

    for config_path in configs:
        try:
            # Load YAML and create Config
            with open(config_path) as f:
                config_dict = yaml.safe_load(f)
            config = Config(config_dict)
            use_oi = config.get_element_or_default('use_oi_overlay', False)
            params = config.get_element_or_default('oi_overlay_params', {})

            # Check for relcarry weights
            weights = config.get_element_or_default('forecast_weights', {})
            relcarry_weights = {k: v for k, v in weights.items() if 'relcarry' in k}
            has_relcarry = any(v > 0 for v in relcarry_weights.values())

            print(f"✓ {config_path}")
            print(f"    use_oi_overlay: {use_oi}")
            print(f"    oi_params: lookback={params.get('lookback', 'N/A')}, "
                  f"threshold={params.get('threshold', 'N/A')}, "
                  f"min_scale={params.get('min_scale', 'N/A')}")
            print(f"    relcarry enabled: {has_relcarry}")
            if has_relcarry:
                print(f"    relcarry weights: {relcarry_weights}")
            print()
        except Exception as e:
            print(f"✗ {config_path}: {e}")
            return False

    print("✓ All config files loaded successfully\n")
    return True


def test_data_method():
    """Test that get_oi_regime_multiplier works on sample data."""
    print("=" * 80)
    print("TEST 2: Data Layer Method (get_oi_regime_multiplier)")
    print("=" * 80)

    # Use a small sample dataset
    data_path = "data/example_crypto_perps_15x4yr.parquet"
    if not Path(data_path).exists():
        print(f"⚠ Sample data not found: {data_path}")
        print("  Skipping data method test (will work in full backtest)")
        print()
        return True

    try:
        data = parquetCryptoPerpsSimData(data_path)
        instruments = data.get_instrument_list()

        if not instruments:
            print("✗ No instruments found in sample data")
            return False

        test_instrument = instruments[0]
        print(f"  Testing with instrument: {test_instrument}")

        # Test with default parameters
        multiplier = data.get_oi_regime_multiplier(
            test_instrument,
            lookback=90,
            threshold=2.0,
            min_scale=0.5,
        )

        print(f"    Multiplier series length: {len(multiplier)}")
        print(f"    Multiplier range: [{multiplier.min():.3f}, {multiplier.max():.3f}]")
        print(f"    Multiplier mean: {multiplier.mean():.3f}")
        print(f"    Days with scaling (<1.0): {(multiplier < 1.0).sum()} / {len(multiplier)}")

        # Validate constraints
        assert multiplier.min() >= 0.5, f"Multiplier below min_scale: {multiplier.min()}"
        assert multiplier.max() <= 1.0, f"Multiplier above 1.0: {multiplier.max()}"
        assert not multiplier.isna().any(), "Multiplier contains NaN values"

        print("✓ get_oi_regime_multiplier works correctly\n")
        return True

    except Exception as e:
        print(f"✗ Data method test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_portfolio_classes():
    """Test that portfolio overlay classes can be instantiated."""
    print("=" * 80)
    print("TEST 3: Portfolio Class Instantiation")
    print("=" * 80)

    try:
        # Test static portfolio with OI overlay
        static_portfolio = CryptoPortfolioWithOIOverlay()
        print(f"✓ CryptoPortfolioWithOIOverlay instantiated")
        print(f"    Class: {static_portfolio.__class__.__name__}")
        print()

        # Test dynamic portfolio with OI overlay
        dynamic_portfolio = CryptoDynamicPortfolioWithOIOverlay()
        print(f"✓ CryptoDynamicPortfolioWithOIOverlay instantiated")
        print(f"    Class: {dynamic_portfolio.__class__.__name__}")
        print()

        return True

    except Exception as e:
        print(f"✗ Portfolio class test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_helper_function():
    """Test that apply_oi_overlay helper function exists."""
    print("=" * 80)
    print("TEST 4: Helper Function (apply_oi_overlay)")
    print("=" * 80)

    try:
        # Check function signature
        import inspect
        sig = inspect.signature(apply_oi_overlay)
        params = list(sig.parameters.keys())

        print(f"✓ apply_oi_overlay function exists")
        print(f"    Parameters: {params}")
        print(f"    Expected: ['portfolio_instance', 'instrument_code', 'base_position']")

        assert params == ['portfolio_instance', 'instrument_code', 'base_position'], \
            f"Unexpected parameters: {params}"

        print("✓ Helper function signature correct\n")
        return True

    except Exception as e:
        print(f"✗ Helper function test failed: {e}")
        return False


def test_config_variations():
    """Test that the 4 test configs have correct parameter combinations."""
    print("=" * 80)
    print("TEST 5: Config Variation Correctness")
    print("=" * 80)

    expected = {
        "config/crypto_perps_oi_baseline.yaml": {
            'use_oi_overlay': False,
            'has_relcarry': False,
        },
        "config/crypto_perps_oi_overlay_only.yaml": {
            'use_oi_overlay': True,
            'has_relcarry': False,
        },
        "config/crypto_perps_oi_crowding_only.yaml": {
            'use_oi_overlay': False,
            'has_relcarry': True,
        },
        "config/crypto_perps_oi_test.yaml": {
            'use_oi_overlay': True,
            'has_relcarry': True,
        },
    }

    all_correct = True

    for config_path, expected_vals in expected.items():
        # Load YAML and create Config
        with open(config_path) as f:
            config_dict = yaml.safe_load(f)
        config = Config(config_dict)
        use_oi = config.get_element_or_default('use_oi_overlay', False)
        weights = config.get_element_or_default('forecast_weights', {})
        relcarry_weights = {k: v for k, v in weights.items() if 'relcarry' in k}
        has_relcarry = any(v > 0 for v in relcarry_weights.values())

        use_oi_correct = use_oi == expected_vals['use_oi_overlay']
        relcarry_correct = has_relcarry == expected_vals['has_relcarry']

        status = "✓" if (use_oi_correct and relcarry_correct) else "✗"

        print(f"{status} {Path(config_path).name}")
        print(f"    use_oi_overlay: {use_oi} (expected {expected_vals['use_oi_overlay']}) "
              f"{'✓' if use_oi_correct else '✗'}")
        print(f"    has_relcarry: {has_relcarry} (expected {expected_vals['has_relcarry']}) "
              f"{'✓' if relcarry_correct else '✗'}")

        if not (use_oi_correct and relcarry_correct):
            all_correct = False
            print("    ⚠ Configuration mismatch!")
        print()

    if all_correct:
        print("✓ All config variations correct\n")
    else:
        print("✗ Some config variations incorrect\n")

    return all_correct


def main():
    """Run all verification tests."""
    print("\n")
    print("╔" + "=" * 78 + "╗")
    print("║" + " " * 20 + "OI OVERLAY VERIFICATION SUITE" + " " * 28 + "║")
    print("╚" + "=" * 78 + "╝")
    print("\n")

    tests = [
        ("Config Loading", test_config_loading),
        ("Data Method", test_data_method),
        ("Portfolio Classes", test_portfolio_classes),
        ("Helper Function", test_helper_function),
        ("Config Variations", test_config_variations),
    ]

    results = {}

    for test_name, test_func in tests:
        try:
            results[test_name] = test_func()
        except Exception as e:
            print(f"✗ {test_name} test crashed: {e}")
            import traceback
            traceback.print_exc()
            results[test_name] = False

    # Summary
    print("=" * 80)
    print("VERIFICATION SUMMARY")
    print("=" * 80)

    for test_name, passed in results.items():
        status = "✓ PASS" if passed else "✗ FAIL"
        print(f"{status}: {test_name}")

    print()

    if all(results.values()):
        print("✓✓✓ ALL TESTS PASSED ✓✓✓")
        print()
        print("Implementation is ready for Phase 1 testing!")
        print("Next step: Run ./scripts/run_oi_mvp_tests.sh")
        print()
        return 0
    else:
        print("✗✗✗ SOME TESTS FAILED ✗✗✗")
        print()
        print("Please fix the issues above before running full backtests.")
        print()
        return 1


if __name__ == "__main__":
    sys.exit(main())
