"""
Quick test script to verify dynamic universe integration.

This script tests the basic functionality of the dynamic universe system
without running a full backtest.
"""

import sys
import pandas as pd

# Add parent directory to path
sys.path.insert(0, '/Users/nathanieldavis/pysystemtrade')

from systems.provided.crypto_example.crypto_system import (
    crypto_system_with_dynamic_universe
)


def test_dynamic_universe():
    """Test dynamic universe integration."""

    print("="*70)
    print("DYNAMIC UNIVERSE MVP TEST")
    print("="*70)

    # Create system with dynamic universe
    print("\n1. Creating system with dynamic universe...")
    try:
        system = crypto_system_with_dynamic_universe(data_path='data/crypto')
        print("   ✓ System created successfully")
    except Exception as e:
        print(f"   ✗ Error creating system: {e}")
        return False

    # Check data layer
    print("\n2. Checking data layer...")
    try:
        instrument_list = system.data.get_instrument_list()
        print(f"   ✓ Found {len(instrument_list)} instruments")
        print(f"   First 10: {instrument_list[:10]}")
    except Exception as e:
        print(f"   ✗ Error getting instrument list: {e}")
        return False

    # Check eligibility at a recent date
    print("\n3. Checking eligibility at a recent date...")
    try:
        recent_date = pd.Timestamp('2024-01-01')
        eligible = system.data.get_eligible_instruments_at_date(recent_date)
        print(f"   ✓ Eligible instruments on {recent_date}: {len(eligible)}")
        print(f"   Sample: {eligible[:10]}")
    except Exception as e:
        print(f"   ✗ Error getting eligible instruments: {e}")
        return False

    # Get weights (this triggers the full calculation)
    print("\n4. Calculating dynamic weights...")
    try:
        weights = system.portfolio.get_instrument_weights()
        print(f"   ✓ Weights calculated")
        print(f"   Date range: {weights.index[0]} to {weights.index[-1]}")
        print(f"   Shape: {weights.shape}")
    except Exception as e:
        print(f"   ✗ Error calculating weights: {e}")
        import traceback
        traceback.print_exc()
        return False

    # Analyze universe size over time
    print("\n5. Analyzing universe size...")
    try:
        universe_size = (weights > 0).sum(axis=1)
        print(f"   ✓ Universe size stats:")
        print(f"      Mean: {universe_size.mean():.1f} instruments")
        print(f"      Min: {universe_size.min():.0f} instruments")
        print(f"      Max: {universe_size.max():.0f} instruments")
        print(f"      Latest: {universe_size.iloc[-1]:.0f} instruments")
    except Exception as e:
        print(f"   ✗ Error analyzing universe: {e}")
        return False

    # Sample weights at a few dates
    print("\n6. Sample weights at key dates...")
    try:
        sample_dates = weights.index[::len(weights)//5][:5]  # 5 evenly spaced dates
        for date in sample_dates:
            active_weights = weights.loc[date][weights.loc[date] > 0]
            print(f"   {date.strftime('%Y-%m-%d')}: {len(active_weights):3.0f} instruments, "
                  f"avg weight: {active_weights.mean()*100:.3f}%")
    except Exception as e:
        print(f"   ✗ Error sampling weights: {e}")
        return False

    # Test a position calculation
    print("\n7. Testing position calculation...")
    try:
        btc_position = system.portfolio.get_notional_position('BTC')
        print(f"   ✓ BTC position calculated: {len(btc_position)} days")
        print(f"   Latest position: {btc_position.iloc[-1]:.4f}")
    except Exception as e:
        print(f"   ✗ Error calculating position: {e}")
        import traceback
        traceback.print_exc()
        return False

    print("\n" + "="*70)
    print("✓ ALL TESTS PASSED")
    print("="*70)

    return True


if __name__ == "__main__":
    success = test_dynamic_universe()
    sys.exit(0 if success else 1)
