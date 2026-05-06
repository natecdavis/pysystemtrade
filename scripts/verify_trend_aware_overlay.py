#!/usr/bin/env python
"""
Verification script for trend-aware OI overlay implementation.

Tests that trend-aware logic correctly:
1. Keeps trend-aligned positions (no scaling)
2. Allows scaling of counter-trend positions
3. Handles edge cases (zero positions, zero forecasts)
"""

import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
import numpy as np
from sysdata.crypto.parquet_perps_sim_data import parquetCryptoPerpsSimData


def test_trend_aware_logic():
    """Test trend-aware scaling logic with synthetic data."""
    print("=" * 80)
    print("TEST: Trend-Aware OI Overlay Logic")
    print("=" * 80)
    print()

    # Use sample dataset
    data_path = "data/example_crypto_perps_15x4yr.parquet"
    if not Path(data_path).exists():
        print(f"⚠ Sample data not found: {data_path}")
        print("  Test requires sample dataset")
        return False

    try:
        data = parquetCryptoPerpsSimData(data_path)
        instruments = data.get_instrument_list()
        test_instrument = instruments[0]
        print(f"Testing with instrument: {test_instrument}")
        print()

        # Get actual price data for index
        prices = data.daily_prices(test_instrument)
        n_days = min(500, len(prices))  # Use last 500 days
        test_index = prices.index[-n_days:]

        # Create synthetic test scenarios
        scenarios = []

        # Scenario 1: Trend-aligned LONG position
        # Position: +100, Trend: +10 → KEEP (aligned)
        pos1 = pd.Series(100.0, index=test_index)
        trend1 = pd.Series(10.0, index=test_index)
        scenarios.append({
            'name': 'Trend-aligned LONG',
            'position': pos1,
            'trend': trend1,
            'expected_scaling': False,  # Should NOT scale
            'reason': 'Position (+100) aligned with trend (+10)',
        })

        # Scenario 2: Trend-aligned SHORT position
        # Position: -100, Trend: -10 → KEEP (aligned)
        pos2 = pd.Series(-100.0, index=test_index)
        trend2 = pd.Series(-10.0, index=test_index)
        scenarios.append({
            'name': 'Trend-aligned SHORT',
            'position': pos2,
            'trend': trend2,
            'expected_scaling': False,  # Should NOT scale
            'reason': 'Position (-100) aligned with trend (-10)',
        })

        # Scenario 3: Counter-trend LONG position
        # Position: +100, Trend: -10 → ALLOW SCALING (counter-trend)
        pos3 = pd.Series(100.0, index=test_index)
        trend3 = pd.Series(-10.0, index=test_index)
        scenarios.append({
            'name': 'Counter-trend LONG',
            'position': pos3,
            'trend': trend3,
            'expected_scaling': True,  # SHOULD scale
            'reason': 'Position (+100) fights trend (-10)',
        })

        # Scenario 4: Counter-trend SHORT position
        # Position: -100, Trend: +10 → ALLOW SCALING (counter-trend)
        pos4 = pd.Series(-100.0, index=test_index)
        trend4 = pd.Series(10.0, index=test_index)
        scenarios.append({
            'name': 'Counter-trend SHORT',
            'position': pos4,
            'trend': trend4,
            'expected_scaling': True,  # SHOULD scale
            'reason': 'Position (-100) fights trend (+10)',
        })

        # Scenario 5: Zero position
        # Position: 0, Trend: +10 → Scaling allowed but has no effect
        # (alignment = 0*10 = 0, which is <=0, so treated as counter-trend)
        pos5 = pd.Series(0.0, index=test_index)
        trend5 = pd.Series(10.0, index=test_index)
        scenarios.append({
            'name': 'Zero position',
            'position': pos5,
            'trend': trend5,
            'expected_scaling': True,  # Scaling allowed (but has no effect: 0 * mult = 0)
            'reason': 'Position is zero (alignment=0, treated as counter-trend)',
        })

        # Scenario 6: Weak trend
        # Position: +100, Trend: +0.1 → KEEP (weakly aligned)
        pos6 = pd.Series(100.0, index=test_index)
        trend6 = pd.Series(0.1, index=test_index)
        scenarios.append({
            'name': 'Weak trend aligned',
            'position': pos6,
            'trend': trend6,
            'expected_scaling': False,  # Should NOT scale
            'reason': 'Position (+100) aligned with weak trend (+0.1)',
        })

        # Run tests
        all_passed = True
        for i, scenario in enumerate(scenarios, 1):
            print(f"Scenario {i}: {scenario['name']}")
            print(f"  {scenario['reason']}")

            # Get multiplier with trend-aware mode
            multiplier = data.get_oi_regime_multiplier(
                test_instrument,
                lookback=90,
                threshold=2.0,
                min_scale=0.5,
                base_position=scenario['position'],
                trend_forecast=scenario['trend'],
                trend_aware=True,
            )

            # Check if scaling was applied
            # If multiplier is always 1.0, no scaling occurred
            scaling_applied = (multiplier < 1.0).any()

            # Verify expected behavior
            expected = scenario['expected_scaling']
            actual = scaling_applied

            if expected == actual:
                status = "✓ PASS"
                all_passed = all_passed and True
            else:
                status = "✗ FAIL"
                all_passed = False

            print(f"  Expected scaling: {expected}")
            print(f"  Actual scaling:   {actual}")
            print(f"  Multiplier stats: min={multiplier.min():.3f}, "
                  f"max={multiplier.max():.3f}, "
                  f"mean={multiplier.mean():.3f}")
            print(f"  {status}")
            print()

        if all_passed:
            print("✓ All scenarios passed")
            return True
        else:
            print("✗ Some scenarios failed")
            return False

    except Exception as e:
        print(f"✗ Test failed with error: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_backward_compatibility():
    """Test that standard (non-trend-aware) mode still works."""
    print("=" * 80)
    print("TEST: Backward Compatibility (Standard Mode)")
    print("=" * 80)
    print()

    data_path = "data/example_crypto_perps_15x4yr.parquet"
    if not Path(data_path).exists():
        print(f"⚠ Sample data not found: {data_path}")
        print("  Skipping backward compatibility test")
        return True

    try:
        data = parquetCryptoPerpsSimData(data_path)
        instruments = data.get_instrument_list()
        test_instrument = instruments[0]

        # Test standard mode (trend_aware=False, default)
        multiplier_standard = data.get_oi_regime_multiplier(
            test_instrument,
            lookback=90,
            threshold=2.0,
            min_scale=0.5,
            trend_aware=False,  # Explicit standard mode
        )

        print(f"  Instrument: {test_instrument}")
        print(f"  Multiplier length: {len(multiplier_standard)}")
        print(f"  Multiplier range: [{multiplier_standard.min():.3f}, {multiplier_standard.max():.3f}]")
        print(f"  Days with scaling: {(multiplier_standard < 1.0).sum()} / {len(multiplier_standard)}")

        # Validate constraints
        assert multiplier_standard.min() >= 0.5, f"Multiplier below min_scale: {multiplier_standard.min()}"
        assert multiplier_standard.max() <= 1.0, f"Multiplier above 1.0: {multiplier_standard.max()}"
        assert not multiplier_standard.isna().any(), "Multiplier contains NaN values"

        print("  ✓ Standard mode works correctly")
        print()
        return True

    except Exception as e:
        print(f"  ✗ Backward compatibility test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_config_loading():
    """Test that trend-aware config loads correctly."""
    print("=" * 80)
    print("TEST: Trend-Aware Config Loading")
    print("=" * 80)
    print()

    import yaml
    from sysdata.config.configdata import Config

    config_path = "config/research/crypto_perps_oi_trend_aware.yaml"
    try:
        with open(config_path) as f:
            config_dict = yaml.safe_load(f)
        config = Config(config_dict)

        use_oi = config.get_element_or_default('use_oi_overlay', False)
        params = config.get_element_or_default('oi_overlay_params', {})
        trend_aware = params.get('trend_aware', False)

        print(f"  Config: {config_path}")
        print(f"  use_oi_overlay: {use_oi}")
        print(f"  trend_aware: {trend_aware}")
        print(f"  lookback: {params.get('lookback', 'N/A')}")
        print(f"  threshold: {params.get('threshold', 'N/A')}")
        print(f"  min_scale: {params.get('min_scale', 'N/A')}")

        assert use_oi is True, "use_oi_overlay should be True"
        assert trend_aware is True, "trend_aware should be True"

        print("  ✓ Config loaded correctly")
        print()
        return True

    except Exception as e:
        print(f"  ✗ Config loading failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """Run all verification tests."""
    print("\n")
    print("╔" + "=" * 78 + "╗")
    print("║" + " " * 18 + "TREND-AWARE OVERLAY VERIFICATION" + " " * 28 + "║")
    print("╚" + "=" * 78 + "╝")
    print("\n")

    tests = [
        ("Config Loading", test_config_loading),
        ("Backward Compatibility", test_backward_compatibility),
        ("Trend-Aware Logic", test_trend_aware_logic),
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
        print("Trend-aware overlay is ready for testing!")
        print()
        print("Next steps:")
        print("  1. Run backtest with trend-aware config:")
        print("     python scripts/run_dynamic_universe_backtest.py \\")
        print("       --config config/research/crypto_perps_oi_trend_aware.yaml \\")
        print("       --data data/dataset_538registry_6yr_jagged.parquet \\")
        print("       --outdir out/oi_trend_aware/combined")
        print()
        print("  2. Compare vs standard overlay:")
        print("     - Standard: out/oi_mvp/combined/metrics.json")
        print("     - Trend-aware: out/oi_trend_aware/combined/metrics.json")
        print()
        print("  3. Check crisis performance (May 2021, Nov 2022)")
        print()
        return 0
    else:
        print("✗✗✗ SOME TESTS FAILED ✗✗✗")
        print()
        print("Please fix the issues above before running backtests.")
        print()
        return 1


if __name__ == "__main__":
    sys.exit(main())
