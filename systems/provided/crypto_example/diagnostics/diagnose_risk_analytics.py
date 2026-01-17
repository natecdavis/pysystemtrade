"""
Comprehensive Risk Analytics Diagnostic for Dynamic Universe Volatility Issue

Investigates why dynamic universe shows 2.08% realized vol vs 21.90% for static
(both targeting 25%) using proper portfolio risk framework (w'Σw).

Phases:
1. Portfolio-level volatility scaling verification
2. Predicted vs realized volatility comparison
3. Market factor exposure analysis
4. Correlation structure analysis
5. Root cause determination

Run from project root:
    python systems/provided/crypto_example/diagnose_risk_analytics.py
"""

import pandas as pd
import numpy as np
from scipy import stats
from systems.provided.crypto_example.crypto_system import (
    crypto_system,
    crypto_system_with_dynamic_universe,
)


def print_section(title):
    """Print formatted section header"""
    print(f"\n{'=' * 80}")
    print(f"{title:^80}")
    print(f"{'=' * 80}\n")


def phase1_portfolio_vol_scaling(static_system, dynamic_system):
    """
    Phase 1: Verify Portfolio-Level Volatility Scaling

    Checks if portfolio-level risk targeting is missing or misconfigured.
    """
    print_section("PHASE 1: Portfolio-Level Volatility Scaling Verification")

    results = {}

    # A) Check IDM values
    print("A) Instrument Diversification Multiplier (IDM)\n")
    print("   IDM = 1/sqrt(w'Σw) measures diversification benefit")
    print("   Higher IDM = more diversification = can leverage more\n")

    try:
        static_idm = static_system.portfolio.get_instrument_diversification_multiplier()
        dynamic_idm = dynamic_system.portfolio.get_instrument_diversification_multiplier()

        static_idm_latest = static_idm.iloc[-1] if len(static_idm) > 0 else np.nan
        dynamic_idm_latest = dynamic_idm.iloc[-1] if len(dynamic_idm) > 0 else np.nan

        print(f"   Static IDM (latest):  {static_idm_latest:.3f}")
        print(f"   Dynamic IDM (latest): {dynamic_idm_latest:.3f}")
        print(f"   Expected for 185 instruments: ~1.5-2.0 (if uncorrelated)")

        if abs(static_idm_latest - dynamic_idm_latest) < 0.1:
            print("\n   ⚠️  WARNING: IDMs are nearly identical despite 15x more instruments!")
            print("   → Suggests IDM may not be scaling with diversification")

        results['static_idm'] = static_idm_latest
        results['dynamic_idm'] = dynamic_idm_latest
        results['idm_series_static'] = static_idm.tail(252)
        results['idm_series_dynamic'] = dynamic_idm.tail(252)

    except Exception as e:
        print(f"   ⚠️  Could not compute IDM: {e}")
        results['static_idm'] = np.nan
        results['dynamic_idm'] = np.nan

    # B) Check predicted portfolio risk
    print("\n\nB) Predicted Portfolio Risk (from w'Σw calculation)\n")

    try:
        # Get predicted risk used in risk overlay
        static_pred_risk = static_system.portfolio.get_portfolio_risk_for_original_positions()
        dynamic_pred_risk = dynamic_system.portfolio.get_portfolio_risk_for_original_positions()

        static_pred_mean = static_pred_risk.iloc[-252:].mean()
        dynamic_pred_mean = dynamic_pred_risk.iloc[-252:].mean()

        print(f"   Static predicted vol (1yr):   {static_pred_mean:.2f}%")
        print(f"   Dynamic predicted vol (1yr):  {dynamic_pred_mean:.2f}%")
        print(f"   Target vol: 25%")

        results['static_pred_vol'] = static_pred_mean
        results['dynamic_pred_vol'] = dynamic_pred_mean
        results['pred_vol_series_static'] = static_pred_risk.tail(252)
        results['pred_vol_series_dynamic'] = dynamic_pred_risk.tail(252)

    except Exception as e:
        print(f"   ⚠️  Could not compute predicted risk: {e}")
        print(f"   → Risk overlay may not be enabled")
        results['static_pred_vol'] = np.nan
        results['dynamic_pred_vol'] = np.nan

    # C) Check risk scalar
    print("\n\nC) Risk Scalar (scales positions to hit target vol)\n")

    try:
        static_risk_scalar = static_system.portfolio.get_risk_scalar()
        dynamic_risk_scalar = dynamic_system.portfolio.get_risk_scalar()

        static_scalar_latest = static_risk_scalar.iloc[-1]
        dynamic_scalar_latest = dynamic_risk_scalar.iloc[-1]

        print(f"   Static risk scalar (latest):  {static_scalar_latest:.3f}")
        print(f"   Dynamic risk scalar (latest): {dynamic_scalar_latest:.3f}")
        print(f"   Value = 1.0 means no adjustment needed")
        print(f"   Value < 1.0 means scaling down (risk too high)")
        print(f"   Value > 1.0 means scaling up (risk too low)")

        results['static_risk_scalar'] = static_scalar_latest
        results['dynamic_risk_scalar'] = dynamic_scalar_latest
        results['risk_scalar_series_static'] = static_risk_scalar.tail(252)
        results['risk_scalar_series_dynamic'] = dynamic_risk_scalar.tail(252)

    except Exception as e:
        print(f"   ⚠️  Could not compute risk scalar: {e}")
        results['static_risk_scalar'] = np.nan
        results['dynamic_risk_scalar'] = np.nan

    # D) Verify risk overlay config
    print("\n\nD) Risk Overlay Configuration\n")

    config = dynamic_system.config
    print(f"   Risk overlay enabled: {hasattr(config, 'risk_overlay')}")

    if hasattr(config, 'max_risk_fraction_normal_risk'):
        print(f"   Max risk fraction: {config.max_risk_fraction_normal_risk}")
        if config.max_risk_fraction_normal_risk > 10:
            print(f"   → Effectively disabled (value >> 1.0)")

    if hasattr(config, 'percentage_vol_target'):
        print(f"   Target volatility: {config.percentage_vol_target}%")

    return results


def phase2_predicted_vs_realized(static_system, dynamic_system, phase1_results):
    """
    Phase 2: Predicted vs Realized Volatility Comparison

    Determines if low realized vol is expected (diversification) or a bug (missing scaling).
    """
    print_section("PHASE 2: Predicted vs Realized Volatility Comparison")

    results = {}

    # Get account curves and compute realized vol
    print("A) Realized Portfolio Volatility (from actual returns)\n")

    account_static = static_system.accounts.portfolio()
    account_dynamic = dynamic_system.accounts.portfolio()

    # Filter to 2018+ to remove historical artifact
    start_date = '2018-01-01'
    account_static_pct = account_static.percent.loc[start_date:]
    account_dynamic_pct = account_dynamic.percent.loc[start_date:]

    realized_vol_static = account_static_pct.std() * np.sqrt(256)
    realized_vol_dynamic = account_dynamic_pct.std() * np.sqrt(256)

    print(f"   Static realized vol (2018+):   {realized_vol_static:.2f}%")
    print(f"   Dynamic realized vol (2018+):  {realized_vol_dynamic:.2f}%")
    print(f"   Target vol: 25%")

    results['realized_vol_static'] = realized_vol_static
    results['realized_vol_dynamic'] = realized_vol_dynamic

    # B) Compare predicted vs realized
    print("\n\nB) Predicted vs Realized Comparison\n")

    comparison = pd.DataFrame({
        'System': ['Static', 'Dynamic'],
        'Predicted Vol': [
            phase1_results.get('static_pred_vol', np.nan),
            phase1_results.get('dynamic_pred_vol', np.nan)
        ],
        'Realized Vol': [realized_vol_static, realized_vol_dynamic],
        'Target Vol': [25.0, 25.0],
        'Pred/Real Ratio': [
            phase1_results.get('static_pred_vol', np.nan) / realized_vol_static
            if realized_vol_static > 0 else np.nan,
            phase1_results.get('dynamic_pred_vol', np.nan) / realized_vol_dynamic
            if realized_vol_dynamic > 0 else np.nan
        ]
    })

    print(comparison.to_string(index=False))

    print("\n\nInterpretation:")
    pred_dynamic = phase1_results.get('dynamic_pred_vol', np.nan)

    if not np.isnan(pred_dynamic):
        if pred_dynamic > 20 and realized_vol_dynamic < 5:
            print("   🔴 SCALING BUG: Predicted ~25% but realized ~2%")
            print("   → Positions not scaled to match prediction")
            print("   → Likely missing portfolio-level vol scaling")
        elif pred_dynamic < 5 and realized_vol_dynamic < 5:
            print("   🟡 EXPECTED BEHAVIOR: Predicted ~2% and realized ~2%")
            print("   → Low-risk diversified/hedged portfolio by design")
            print("   → Check market beta (Phase 3) to confirm")
        elif 20 <= pred_dynamic <= 30 and 20 <= realized_vol_dynamic <= 30:
            print("   🟢 CORRECT: Vol targeting working as designed")
    else:
        print("   ⚠️  Could not compute predicted vol - risk overlay may be disabled")

    # C) Decompose volatility sources
    print("\n\nC) Volatility Decomposition (Dynamic Universe)\n")

    try:
        weights_dynamic = dynamic_system.portfolio.get_instrument_weights().loc[start_date:]
        instruments = [col for col in weights_dynamic.columns if weights_dynamic[col].abs().sum() > 0]

        print(f"   Analyzing {len(instruments)} instruments with non-zero weights...\n")

        instrument_vols = []
        for inst in instruments[:50]:  # Limit to first 50 for speed
            try:
                returns = dynamic_system.rawdata.get_daily_percentage_returns(inst).loc[start_date:]
                if len(returns) > 30:
                    vol = returns.std() * np.sqrt(256)
                    avg_weight = weights_dynamic[inst].abs().mean()
                    instrument_vols.append({
                        'instrument': inst,
                        'volatility': vol,
                        'avg_abs_weight': avg_weight,
                        'marginal_contribution': vol * avg_weight
                    })
            except:
                pass

        if len(instrument_vols) > 0:
            vol_df = pd.DataFrame(instrument_vols).sort_values('marginal_contribution', ascending=False)

            sum_marginal = vol_df['marginal_contribution'].sum()
            diversification_benefit = 1 - (realized_vol_dynamic / sum_marginal) if sum_marginal > 0 else 0

            print(f"   Sum of marginal contributions (no correlation): {sum_marginal:.2f}%")
            print(f"   Actual portfolio vol: {realized_vol_dynamic:.2f}%")
            print(f"   Diversification benefit: {diversification_benefit:.1%}")

            print(f"\n   Top 10 contributors:")
            print(vol_df.head(10)[['instrument', 'volatility', 'avg_abs_weight', 'marginal_contribution']].to_string(index=False))

            results['vol_decomposition'] = vol_df

    except Exception as e:
        print(f"   ⚠️  Could not decompose volatility: {e}")

    return results


def phase3_market_factor_exposure(static_system, dynamic_system):
    """
    Phase 3: Market Factor Exposure Analysis

    Determines if dynamic universe is market-neutral (low beta → low vol).
    """
    print_section("PHASE 3: Market Factor Exposure Analysis")

    results = {}
    start_date = '2018-01-01'

    # A) Estimate Beta to BTC (crypto market proxy)
    print("A) Beta to BTC (Crypto Market Proxy)\n")

    try:
        # Get BTC returns as market proxy
        btc_returns = dynamic_system.rawdata.get_daily_percentage_returns('BTC').loc[start_date:]

        # Get strategy returns
        account_static = static_system.accounts.portfolio().percent.loc[start_date:]
        account_dynamic = dynamic_system.accounts.portfolio().percent.loc[start_date:]

        # Align dates
        common_dates = btc_returns.index.intersection(account_static.index).intersection(account_dynamic.index)
        btc_returns = btc_returns.loc[common_dates]
        account_static = account_static.loc[common_dates]
        account_dynamic = account_dynamic.loc[common_dates]

        # Compute beta using OLS regression
        slope_static, intercept_static, r_static, p_static, se_static = stats.linregress(
            btc_returns, account_static
        )

        slope_dynamic, intercept_dynamic, r_dynamic, p_dynamic, se_dynamic = stats.linregress(
            btc_returns, account_dynamic
        )

        print(f"   Static Universe:")
        print(f"      Beta to BTC: {slope_static:.3f} (R² = {r_static**2:.3f}, p = {p_static:.4f})")
        print(f"      Alpha: {intercept_static:.3f}% per day")

        print(f"\n   Dynamic Universe:")
        print(f"      Beta to BTC: {slope_dynamic:.3f} (R² = {r_dynamic**2:.3f}, p = {p_dynamic:.4f})")
        print(f"      Alpha: {intercept_dynamic:.3f}% per day")

        print(f"\n   Interpretation:")
        if abs(slope_dynamic) < 0.2:
            print(f"      🟡 Dynamic is MARKET-NEUTRAL (beta ≈ 0)")
            print(f"      → Low vol expected from lack of directional exposure")
        elif abs(slope_dynamic) < abs(slope_static) * 0.5:
            print(f"      🟡 Dynamic has REDUCED market exposure (beta 50%< static)")
            print(f"      → Partially explains vol reduction")
        else:
            print(f"      🔴 Both have similar market exposure")
            print(f"      → Beta doesn't explain vol difference")

        results['beta_static'] = slope_static
        results['beta_dynamic'] = slope_dynamic
        results['r2_static'] = r_static**2
        results['r2_dynamic'] = r_dynamic**2

    except Exception as e:
        print(f"   ⚠️  Could not compute beta: {e}")

    # B) Long/Short Exposure Analysis
    print("\n\nB) Long/Short Position Analysis\n")

    try:
        weights_dynamic = dynamic_system.portfolio.get_instrument_weights().loc[start_date:]

        # Compute long/short exposure over time
        long_exposure = weights_dynamic.clip(lower=0).sum(axis=1)
        short_exposure = weights_dynamic.clip(upper=0).abs().sum(axis=1)
        net_exposure = long_exposure - short_exposure
        gross_exposure = long_exposure + short_exposure

        print(f"   Average Gross Exposure: {gross_exposure.mean():.2f}")
        print(f"   Average Long Exposure:  {long_exposure.mean():.2f}")
        print(f"   Average Short Exposure: {short_exposure.mean():.2f}")
        print(f"   Average Net Exposure:   {net_exposure.mean():.2f}")
        print(f"   Net/Gross Ratio:        {(net_exposure / gross_exposure).mean():.2f}")

        print(f"\n   Recent values (last date):")
        print(f"   Gross: {gross_exposure.iloc[-1]:.2f}")
        print(f"   Long:  {long_exposure.iloc[-1]:.2f}")
        print(f"   Short: {short_exposure.iloc[-1]:.2f}")
        print(f"   Net:   {net_exposure.iloc[-1]:.2f}")

        if abs(net_exposure.mean()) < 0.2:
            print(f"\n   🟡 Portfolio is approximately MARKET-NEUTRAL")
            print(f"   → Long and short positions roughly balanced")

        results['gross_exposure'] = gross_exposure.mean()
        results['net_exposure'] = net_exposure.mean()
        results['net_gross_ratio'] = (net_exposure / gross_exposure).mean()

    except Exception as e:
        print(f"   ⚠️  Could not analyze exposure: {e}")

    return results


def phase4_correlation_structure(dynamic_system):
    """
    Phase 4: Correlation Structure Analysis

    Understands if 400 instruments are truly uncorrelated.
    """
    print_section("PHASE 4: Correlation Structure Analysis")

    results = {}

    # A) Average pairwise correlation
    print("A) Average Pairwise Correlation\n")

    try:
        correlation_list = dynamic_system.portfolio.get_list_of_instrument_returns_correlations()

        # Get most recent correlation matrix
        latest_date = pd.Timestamp('2025-01-01')
        latest_corr = correlation_list.most_recent_correlation_before_date(latest_date)

        if latest_corr is not None:
            corr_matrix = latest_corr.as_pd()
            n = len(corr_matrix)

            # Compute average off-diagonal correlation
            avg_corr = (corr_matrix.sum().sum() - n) / (n * (n - 1))

            # Implied diversification ratio
            div_ratio = 1 / np.sqrt(1 + (n - 1) * avg_corr) if avg_corr > -1/(n-1) else np.nan

            print(f"   Number of instruments: {n}")
            print(f"   Average pairwise correlation: {avg_corr:.3f}")
            print(f"   Implied diversification ratio: {div_ratio:.3f}")
            print(f"   Theoretical max IDM (1/div_ratio): {1/div_ratio:.3f}")

            print(f"\n   Interpretation:")
            if avg_corr > 0.5:
                print(f"      🔴 HIGH correlation - common factor dominates")
            elif avg_corr > 0.2:
                print(f"      🟡 MEDIUM correlation - some diversification benefit")
            else:
                print(f"      🟢 LOW correlation - strong diversification benefit")

            # Distribution of correlations
            off_diag = corr_matrix.values[np.triu_indices_from(corr_matrix.values, k=1)]
            print(f"\n   Correlation distribution:")
            print(f"      Min: {off_diag.min():.3f}")
            print(f"      25th percentile: {np.percentile(off_diag, 25):.3f}")
            print(f"      Median: {np.median(off_diag):.3f}")
            print(f"      75th percentile: {np.percentile(off_diag, 75):.3f}")
            print(f"      Max: {off_diag.max():.3f}")

            results['avg_correlation'] = avg_corr
            results['diversification_ratio'] = div_ratio
            results['num_instruments'] = n
        else:
            print(f"   ⚠️  Could not retrieve correlation matrix")

    except Exception as e:
        print(f"   ⚠️  Could not compute correlations: {e}")

    # B) Check if cross-sectional rules create offsetting
    print("\n\nB) Cross-Sectional Rule Impact\n")

    print("   Current rule stack includes:")
    config = dynamic_system.config
    if hasattr(config, 'trading_rules'):
        for rule_name in config.trading_rules.keys():
            print(f"      - {rule_name}")
            if 'relmomentum' in rule_name.lower():
                print(f"        ⚠️  Cross-sectional momentum (creates long/short offsetting)")

    print(f"\n   Cross-sectional rules (relmomentum20, relmomentum40):")
    print(f"      - Explicitly create long/short offsetting positions")
    print(f"      - Reduce market beta (market-neutral component)")
    print(f"      - Can reduce portfolio vol if market is dominant factor")

    return results


def phase5_root_cause_determination(phase1, phase2, phase3, phase4):
    """
    Phase 5: Root Cause Determination

    Synthesizes findings to answer: Is low vol due to (a) missing scaling,
    (b) market-neutral design, or (c) a bug?
    """
    print_section("PHASE 5: Root Cause Determination")

    print("Decision Tree Analysis:\n")

    # Check 1: Predicted vs Realized
    pred_vol = phase1.get('dynamic_pred_vol', np.nan)
    realized_vol = phase2.get('realized_vol_dynamic', np.nan)

    print(f"1. Predicted vs Realized Volatility:")
    print(f"   Predicted: {pred_vol:.2f}%")
    print(f"   Realized:  {realized_vol:.2f}%")

    if np.isnan(pred_vol):
        print(f"\n   ⚠️  ISSUE: Cannot compute predicted vol")
        print(f"   → Risk overlay may be disabled or misconfigured")
        print(f"   → Recommended: Check risk_overlay config")
        diagnosis = "MISSING_PREDICTION"

    elif pred_vol > 20 and realized_vol < 5:
        print(f"\n   🔴 ISSUE: Predicted ~25% but realized ~2%")
        print(f"   → Missing portfolio-level vol scaling")
        print(f"   → Positions not scaled to match prediction")
        diagnosis = "MISSING_VOL_SCALING"

    elif pred_vol < 5 and realized_vol < 5:
        print(f"\n   🟡 EXPECTED: Predicted matches realized (~2%)")

        # Check beta
        beta = phase3.get('beta_dynamic', np.nan)
        net_gross = phase3.get('net_gross_ratio', np.nan)

        print(f"\n2. Market Factor Exposure:")
        print(f"   Beta to BTC: {beta:.3f}")
        print(f"   Net/Gross:   {net_gross:.3f}")

        if abs(beta) < 0.2 or abs(net_gross) < 0.3:
            print(f"\n   🟡 DESIGN: Market-neutral strategy")
            print(f"   → Low vol is by design (offsetting long/short positions)")
            print(f"   → Cross-sectional rules create market-neutral exposure")
            diagnosis = "MARKET_NEUTRAL_DESIGN"
        else:
            # Check IDM
            idm_static = phase1.get('static_idm', np.nan)
            idm_dynamic = phase1.get('dynamic_idm', np.nan)

            print(f"\n3. Diversification Multiplier:")
            print(f"   Static IDM:  {idm_static:.3f}")
            print(f"   Dynamic IDM: {idm_dynamic:.3f}")

            if abs(idm_static - idm_dynamic) < 0.1:
                print(f"\n   🔴 ISSUE: IDM not scaling with 15x more instruments")
                print(f"   → IDM should be ~1.5-2.0 for 185 uncorrelated instruments")
                diagnosis = "IDM_NOT_SCALING"
            else:
                print(f"\n   🟢 IDM scaling correctly")
                diagnosis = "UNKNOWN"

    else:
        print(f"\n   🟢 Working correctly (both near target)")
        diagnosis = "WORKING_CORRECTLY"

    # Print summary and recommendations
    print_section("SUMMARY AND RECOMMENDATIONS")

    print(f"Root Cause Diagnosis: {diagnosis}\n")

    if diagnosis == "MISSING_VOL_SCALING":
        print("RECOMMENDATION: Implement portfolio-level volatility scaling")
        print("\nOption 1: Add vol scalar in dynamic_portfolio.py")
        print("  - Scale all positions based on lagged realized portfolio vol")
        print("  - Use 30-60 day window to avoid circularity")
        print("  - Cap at 3x to prevent over-leverage")
        print("  - See VOLATILITY_TARGETING_DIAGNOSIS.md Option 1")

        print("\nOption 2: Enable risk overlay scaling up")
        print("  - Check if risk overlay only scales down (not up)")
        print("  - May need config change or code modification")

    elif diagnosis == "MARKET_NEUTRAL_DESIGN":
        print("FINDING: Low volatility is by design (market-neutral strategy)")
        print("\nThe dynamic universe with cross-sectional momentum rules creates")
        print("a market-neutral portfolio with offsetting long/short positions.")
        print("This is working as intended but requires different expectations.")

        print("\nOptions:")
        print("  A) Accept design - increase notional capital 10x for equivalent returns")
        print("  B) Remove relmomentum rules - increase directional exposure")
        print("  C) Add leverage multiplier - scale all positions uniformly")
        print("  D) Hybrid - reduce weight of cross-sectional rules (not remove)")

        print("\nRecommended: Option D (reduce relmomentum weight)")
        print("  - Rebalance forecast weights to give less weight to relmomentum")
        print("  - Maintains diversification benefit without full market-neutrality")
        print("  - Target net/gross ratio of ~0.6-0.7 (vs current ~0.1-0.3)")

    elif diagnosis == "IDM_NOT_SCALING":
        print("RECOMMENDATION: Enable estimated IDM")
        print("\nSet in crypto_config_diversified.yaml:")
        print("  use_instrument_div_mult_estimates: True")
        print("  instrument_div_mult_estimate:")
        print("    ewma_span: 125")
        print("    dm_max: 2.5  # Allow higher IDM for many instruments")

    elif diagnosis == "MISSING_PREDICTION":
        print("RECOMMENDATION: Enable risk overlay")
        print("\nCheck config has:")
        print("  risk_overlay: True")
        print("  percentage_vol_target: 25")
        print("  max_risk_fraction_normal_risk: 1.0")

    elif diagnosis == "WORKING_CORRECTLY":
        print("FINDING: System working as designed")
        print("\nNo action needed - volatility targeting functioning correctly.")

    else:
        print("FINDING: Root cause unclear from diagnostics")
        print("\nRecommended: Manual investigation of:")
        print("  - Position sizing calculations")
        print("  - Buffering and rounding")
        print("  - Capital allocation")

    return diagnosis


def main():
    """Run all diagnostic phases"""

    print("=" * 80)
    print("DYNAMIC UNIVERSE VOLATILITY DIAGNOSTIC")
    print("=" * 80)
    print("\nLoading systems (this may take several minutes)...")
    print("  - Static universe: 12 instruments")
    print("  - Dynamic universe: ~185 instruments (avg 2018+)")

    # Load both systems
    static_system = crypto_system(data_path='data/crypto')
    dynamic_system = crypto_system_with_dynamic_universe(data_path='data/crypto')

    print("\n✓ Systems loaded successfully")

    # Run diagnostic phases
    phase1 = phase1_portfolio_vol_scaling(static_system, dynamic_system)
    phase2 = phase2_predicted_vs_realized(static_system, dynamic_system, phase1)
    phase3 = phase3_market_factor_exposure(static_system, dynamic_system)
    phase4 = phase4_correlation_structure(dynamic_system)
    diagnosis = phase5_root_cause_determination(phase1, phase2, phase3, phase4)

    # Save results to CSV
    print_section("SAVING RESULTS")

    summary = pd.DataFrame({
        'Metric': [
            'Static IDM',
            'Dynamic IDM',
            'Static Predicted Vol (%)',
            'Dynamic Predicted Vol (%)',
            'Static Realized Vol (%)',
            'Dynamic Realized Vol (%)',
            'Static Beta to BTC',
            'Dynamic Beta to BTC',
            'Dynamic Net/Gross Ratio',
            'Avg Pairwise Correlation',
            'Diagnosis'
        ],
        'Value': [
            phase1.get('static_idm', np.nan),
            phase1.get('dynamic_idm', np.nan),
            phase1.get('static_pred_vol', np.nan),
            phase1.get('dynamic_pred_vol', np.nan),
            phase2.get('realized_vol_static', np.nan),
            phase2.get('realized_vol_dynamic', np.nan),
            phase3.get('beta_static', np.nan),
            phase3.get('beta_dynamic', np.nan),
            phase3.get('net_gross_ratio', np.nan),
            phase4.get('avg_correlation', np.nan),
            diagnosis
        ]
    })

    summary.to_csv('systems/provided/crypto_example/risk_diagnostic_summary.csv', index=False)
    print("✓ Saved summary to risk_diagnostic_summary.csv")

    print("\nDiagnostic complete!")

    return {
        'phase1': phase1,
        'phase2': phase2,
        'phase3': phase3,
        'phase4': phase4,
        'diagnosis': diagnosis
    }


if __name__ == '__main__':
    results = main()
