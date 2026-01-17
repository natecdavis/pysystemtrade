"""
Quick verification script for IDM fix

Tests that enabling use_instrument_div_mult_estimates allows IDM to scale
properly with the dynamic universe.

Run from project root:
    python systems/provided/crypto_example/verify_idm_fix.py
"""

import pandas as pd
import numpy as np
from systems.provided.crypto_example.crypto_system import (
    crypto_system,
    crypto_system_with_dynamic_universe,
)


def main():
    print("=" * 80)
    print("IDM FIX VERIFICATION")
    print("=" * 80)
    print("\nLoading systems (may take a few minutes)...")
    print("  - Static universe: 12 instruments")
    print("  - Dynamic universe: ~185 instruments (avg 2018+)\n")

    # Load both systems
    static_system = crypto_system(data_path='data/crypto')
    dynamic_system = crypto_system_with_dynamic_universe(data_path='data/crypto')

    print("✓ Systems loaded\n")

    # Check IDM values
    print("=" * 80)
    print("INSTRUMENT DIVERSIFICATION MULTIPLIER (IDM) CHECK")
    print("=" * 80)
    print()

    static_idm = static_system.portfolio.get_instrument_diversification_multiplier()
    dynamic_idm = dynamic_system.portfolio.get_instrument_diversification_multiplier()

    # Get statistics
    static_idm_latest = static_idm.iloc[-1] if len(static_idm) > 0 else np.nan
    dynamic_idm_latest = dynamic_idm.iloc[-1] if len(dynamic_idm) > 0 else np.nan

    # Get 2018+ statistics
    start_date = '2018-01-01'
    static_idm_recent = static_idm.loc[start_date:]
    dynamic_idm_recent = dynamic_idm.loc[start_date:]

    print(f"Static Universe (12 instruments):")
    print(f"  Latest IDM:       {static_idm_latest:.3f}")
    print(f"  Mean IDM (2018+): {static_idm_recent.mean():.3f}")
    print(f"  Expected:         ~1.22 (fixed)")

    print(f"\nDynamic Universe (~185 instruments):")
    print(f"  Latest IDM:       {dynamic_idm_latest:.3f}")
    print(f"  Mean IDM (2018+): {dynamic_idm_recent.mean():.3f}")
    print(f"  Min IDM (2018+):  {dynamic_idm_recent.min():.3f}")
    print(f"  Max IDM (2018+):  {dynamic_idm_recent.max():.3f}")
    print(f"  Expected:         ~1.5-2.0 (estimated from correlations)")

    # Diagnosis
    print(f"\n{'─' * 80}")
    print("DIAGNOSIS:")
    print(f"{'─' * 80}\n")

    if abs(dynamic_idm_latest - static_idm_latest) < 0.1:
        print("❌ FAIL: IDMs are nearly identical!")
        print("   → Dynamic universe NOT using estimated IDM")
        print("   → Check that use_instrument_div_mult_estimates: True")
        print(f"   → Current dynamic IDM: {dynamic_idm_latest:.3f}")
        print(f"   → Should be: ~1.5-2.0 for 185 instruments")
        success = False
    elif dynamic_idm_latest > 1.4:
        print("✅ PASS: IDM scaling working!")
        print(f"   → Dynamic IDM ({dynamic_idm_latest:.3f}) > Static IDM ({static_idm_latest:.3f})")
        print(f"   → Increase: {(dynamic_idm_latest / static_idm_latest - 1) * 100:.1f}%")
        print("   → More instruments = higher diversification benefit")
        success = True
    else:
        print("⚠️  WARNING: IDM increased but may be capped")
        print(f"   → Dynamic IDM: {dynamic_idm_latest:.3f}")
        print("   → Check dm_max in config (should be 2.5)")
        success = False

    # Check realized volatility
    print(f"\n{'=' * 80}")
    print("REALIZED VOLATILITY CHECK")
    print(f"{'=' * 80}\n")

    account_static = static_system.accounts.portfolio().percent.loc[start_date:]
    account_dynamic = dynamic_system.accounts.portfolio().percent.loc[start_date:]

    realized_vol_static = account_static.std() * np.sqrt(256)
    realized_vol_dynamic = account_dynamic.std() * np.sqrt(256)

    print(f"Static Universe:")
    print(f"  Realized vol (2018+): {realized_vol_static:.2f}%")
    print(f"  Target:               25%")
    print(f"  Difference:           {realized_vol_static - 25:.2f}pp")

    print(f"\nDynamic Universe:")
    print(f"  Realized vol (2018+): {realized_vol_dynamic:.2f}%")
    print(f"  Target:               25%")
    print(f"  Difference:           {realized_vol_dynamic - 25:.2f}pp")

    print(f"\nVol Ratio (Dynamic/Static): {realized_vol_dynamic / realized_vol_static:.2f}x")

    # Diagnosis
    print(f"\n{'─' * 80}")
    print("DIAGNOSIS:")
    print(f"{'─' * 80}\n")

    if realized_vol_dynamic < 5:
        print("❌ FAIL: Dynamic vol still too low (<5%)")
        print(f"   → Current: {realized_vol_dynamic:.2f}%")
        print("   → IDM fix may not have applied correctly")
        print("   → Or additional issues beyond IDM (market-neutral positioning)")
    elif 15 <= realized_vol_dynamic <= 30:
        print("✅ PASS: Dynamic vol in acceptable range!")
        print(f"   → Current: {realized_vol_dynamic:.2f}%")
        print(f"   → Target: 25%")
        print("   → Volatility targeting working correctly")
    elif 10 <= realized_vol_dynamic < 15:
        print("⚠️  PARTIAL: Dynamic vol improved but still below target")
        print(f"   → Current: {realized_vol_dynamic:.2f}%")
        print("   → Market-neutral positioning may still reduce vol")
        print("   → Consider reducing cross-sectional rule weights")
    else:
        print("⚠️  WARNING: Dynamic vol very high (>30%)")
        print(f"   → Current: {realized_vol_dynamic:.2f}%")
        print("   → Check risk overlay settings")

    # Overall result
    print(f"\n{'=' * 80}")
    print("OVERALL RESULT")
    print(f"{'=' * 80}\n")

    if success and 15 <= realized_vol_dynamic <= 30:
        print("🎉 SUCCESS: IDM fix working!")
        print("\n   Dynamic universe now:")
        print(f"   • Uses estimated IDM: {dynamic_idm_latest:.3f} (was 1.22)")
        print(f"   • Achieves {realized_vol_dynamic:.2f}% vol (was 2.08%)")
        print(f"   • Improvement: {(realized_vol_dynamic / 2.08):.1f}x volatility increase")
        print("\n   Next steps:")
        print("   • Run full backtest comparison")
        print("   • Verify Sharpe ratio maintained (~0.67)")
        print("   • Check absolute returns increased proportionally")
    elif dynamic_idm_latest > 1.4 and realized_vol_dynamic < 15:
        print("⚠️  PARTIAL SUCCESS: IDM fix applied but vol still low")
        print("\n   Dynamic universe now:")
        print(f"   • Uses estimated IDM: {dynamic_idm_latest:.3f} ✓")
        print(f"   • But vol only {realized_vol_dynamic:.2f}% (target 25%)")
        print("\n   Likely cause: Market-neutral positioning from cross-sectional rules")
        print("\n   Options:")
        print("   1. Accept lower vol (strategy is market-neutral by design)")
        print("   2. Reduce weight of relmomentum rules")
        print("   3. Increase notional capital to compensate")
    else:
        print("❌ VERIFICATION FAILED")
        print("\n   Issues:")
        print(f"   • IDM not scaling: {dynamic_idm_latest:.3f} (should be 1.5-2.0)")
        print(f"   • Vol still low: {realized_vol_dynamic:.2f}% (should be 15-25%)")
        print("\n   Troubleshooting:")
        print("   1. Verify use_instrument_div_mult_estimates: True in config")
        print("   2. Check instrument_div_mult_estimate section exists")
        print("   3. Review correlation matrix calculations")

    print()


if __name__ == '__main__':
    main()
