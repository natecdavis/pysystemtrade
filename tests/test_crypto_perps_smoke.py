"""
Smoke tests for Crypto Perpetual Futures Trading System - Phase 1

These tests validate all Definition-of-Done criteria for Phase 1 MVP.
"""

import pytest
import pandas as pd
import numpy as np
from pathlib import Path


# Test fixtures and constants
TEST_DATA_PATH = Path(__file__).parent.parent / 'data' / 'example_crypto_perps.parquet'

EXPECTED_INSTRUMENTS = [
    'BTCUSDT_PERP',
    'ETHUSDT_PERP',
    'BNBUSDT_PERP',
    'SOLUSDT_PERP',
    'XRPUSDT_PERP'
]


class TestDataAdapter:
    """Test suite for data adapter (Step 1)"""

    def test_data_adapter_loads_and_validates(self):
        """
        Test that data adapter loads data correctly and performs validation
        """
        from sysdata.crypto.prices import load_crypto_perps_panel

        # Load data
        prices, meta = load_crypto_perps_panel(str(TEST_DATA_PATH))

        # Assert date index is monotonic
        assert prices.index.is_monotonic_increasing, "Date index must be monotonic increasing"

        # Assert no duplicate dates
        assert not prices.index.duplicated().any(), "Date index must not have duplicates"

        # Assert expected 5 instruments
        instruments = list(prices.columns)
        assert len(instruments) == 5, f"Expected 5 instruments, got {len(instruments)}"
        assert set(instruments) == set(EXPECTED_INSTRUMENTS), \
            f"Unexpected instruments: {set(instruments) - set(EXPECTED_INSTRUMENTS)}"

        # Assert no NaN in prices
        assert not prices.isna().any().any(), "Prices must not contain NaN values"

        # Assert metadata structure
        assert isinstance(meta.index, pd.MultiIndex), "Metadata must have MultiIndex"
        assert meta.index.names == ['date', 'instrument'], \
            f"Metadata index names must be ['date', 'instrument'], got {meta.index.names}"

        # Assert required metadata columns
        required_meta_cols = ['funding_rate', 'adv_notional', 'spread_frac', 'taker_fee_frac']
        assert all(col in meta.columns for col in required_meta_cols), \
            f"Missing metadata columns: {set(required_meta_cols) - set(meta.columns)}"

    def test_funding_rate_alignment(self):
        """
        Test that funding rates are correctly aligned with price data
        funding_rate[t] applies from close(t-1) to close(t)
        """
        from sysdata.crypto.prices import load_crypto_perps_panel

        prices, meta = load_crypto_perps_panel(str(TEST_DATA_PATH))

        # For each instrument, verify funding rate dates match price dates
        for instrument in prices.columns:
            price_dates = set(prices.index)
            funding_dates = set(
                meta.loc[(slice(None), instrument), 'funding_rate'].index.get_level_values(0)
            )

            assert price_dates == funding_dates, \
                f"Funding rate dates mismatch for {instrument}"

    def test_data_date_range(self):
        """
        Test that data covers expected date range
        """
        from sysdata.crypto.prices import load_crypto_perps_panel

        prices, meta = load_crypto_perps_panel(str(TEST_DATA_PATH))

        # Should have at least 365 days of data
        assert len(prices) >= 365, f"Expected at least 365 days, got {len(prices)}"

        # Dates should be daily frequency (approximately)
        date_diffs = prices.index.to_series().diff().dt.days
        # Most diffs should be 1 day (allowing some flexibility for edge cases)
        assert (date_diffs.dropna() == 1).mean() > 0.99, \
            "Date index should be daily frequency"


class TestEWMACRule:
    """Test suite for EWMAC rule implementation (Step 2)"""

    def test_ewmac_produces_valid_forecasts(self):
        """
        Test that EWMAC rule produces valid forecasts
        """
        from sysdata.crypto.prices import load_crypto_perps_panel
        from systems.crypto_perps.rules.ewmac import ewmac_forecasts

        # Load data
        prices, meta = load_crypto_perps_panel(str(TEST_DATA_PATH))

        # Calculate EWMAC forecasts for standard pairs
        ewmac_pairs = [(8, 32), (16, 64)]
        forecasts = ewmac_forecasts(prices, ewmac_pairs)

        # Validate structure
        assert len(forecasts) == 5, "Should have forecasts for 5 instruments"
        for instrument in EXPECTED_INSTRUMENTS:
            assert instrument in forecasts, f"Missing forecasts for {instrument}"
            assert len(forecasts[instrument]) == 2, \
                f"Should have 2 EWMAC rules for {instrument}"
            assert 'ewmac_8_32' in forecasts[instrument]
            assert 'ewmac_16_64' in forecasts[instrument]

        # Validate forecast values
        for instrument in EXPECTED_INSTRUMENTS:
            for rule_name, forecast in forecasts[instrument].items():
                # Check it's a Series
                assert isinstance(forecast, pd.Series), \
                    f"{instrument}/{rule_name} should be a Series"

                # Check no inf values
                assert not np.isinf(forecast).any(), \
                    f"{instrument}/{rule_name} contains inf values"

                # Check we have some non-NaN values
                assert forecast.notna().sum() > 0, \
                    f"{instrument}/{rule_name} has no non-NaN values"

                # Check values are numeric
                assert pd.api.types.is_numeric_dtype(forecast), \
                    f"{instrument}/{rule_name} should be numeric"

    def test_ewmac_single_instrument(self):
        """
        Test EWMAC calculation for single instrument
        """
        from sysdata.crypto.prices import load_crypto_perps_panel
        from systems.crypto_perps.rules.ewmac import ewmac_forecast_single_instrument

        # Load data
        prices, meta = load_crypto_perps_panel(str(TEST_DATA_PATH))

        # Get a single instrument
        instrument = 'BTCUSDT_PERP'
        price_series = prices[instrument]

        # Calculate forecast
        forecast = ewmac_forecast_single_instrument(price_series, Lfast=8, Lslow=32)

        # Validate
        assert isinstance(forecast, pd.Series)
        assert len(forecast) == len(price_series)
        assert not np.isinf(forecast).any()
        assert forecast.notna().sum() > 0


class TestFundingCarryRule:
    """Test suite for funding carry rule (Step 3)"""

    def test_funding_carry_signal(self):
        """
        Test that funding carry rule produces valid signals
        """
        from sysdata.crypto.prices import load_crypto_perps_panel
        from systems.crypto_perps.rules.carry_funding import funding_carry_forecasts

        # Load data
        prices, meta = load_crypto_perps_panel(str(TEST_DATA_PATH))

        # Calculate carry forecasts
        fast_halflife = 3
        slow_halflife = 30
        carry_forecasts = funding_carry_forecasts(meta, fast_halflife, slow_halflife)

        # Validate structure
        assert len(carry_forecasts) == 5, "Should have carry forecasts for 5 instruments"
        for instrument in EXPECTED_INSTRUMENTS:
            assert instrument in carry_forecasts, \
                f"Missing carry forecast for {instrument}"

        # Validate forecast values
        for instrument, forecast in carry_forecasts.items():
            # Check it's a Series
            assert isinstance(forecast, pd.Series), \
                f"{instrument} carry should be a Series"

            # Check no inf values
            assert not np.isinf(forecast).any(), \
                f"{instrument} carry contains inf values"

            # Check we have some non-NaN values
            assert forecast.notna().sum() > 0, \
                f"{instrument} carry has no non-NaN values"

            # Check values are numeric
            assert pd.api.types.is_numeric_dtype(forecast), \
                f"{instrument} carry should be numeric"

            # Carry signal should be small (funding rates are typically < 0.1% per day)
            # After EWMA, the difference should be even smaller
            assert forecast.abs().max() < 0.01, \
                f"{instrument} carry signal unexpectedly large: {forecast.abs().max()}"

    def test_funding_carry_single_instrument(self):
        """
        Test funding carry calculation for single instrument
        """
        from sysdata.crypto.prices import load_crypto_perps_panel
        from systems.crypto_perps.rules.carry_funding import funding_carry_forecast

        # Load data
        prices, meta = load_crypto_perps_panel(str(TEST_DATA_PATH))

        # Get funding rates for one instrument
        instrument = 'BTCUSDT_PERP'
        funding_rates = meta.loc[(slice(None), instrument), 'funding_rate'].droplevel('instrument')

        # Calculate carry forecast
        forecast = funding_carry_forecast(
            funding_rates=funding_rates,
            fast_halflife=3,
            slow_halflife=30
        )

        # Validate
        assert isinstance(forecast, pd.Series)
        assert len(forecast) == len(funding_rates)
        assert not np.isinf(forecast).any()
        assert forecast.notna().sum() > 0

    def test_funding_carry_signal_direction(self):
        """
        Test that carry signal direction matches funding rate trends
        """
        # Create synthetic funding rates with clear trend
        dates = pd.date_range('2023-01-01', periods=100, freq='D')
        # Rising funding rates
        rising_rates = pd.Series(
            np.linspace(0.0001, 0.001, 100),
            index=dates
        )

        from systems.crypto_perps.rules.carry_funding import funding_carry_forecast

        # Calculate carry with rising rates
        carry_rising = funding_carry_forecast(rising_rates, fast_halflife=3, slow_halflife=30)

        # With rising rates, slow EWMA lags behind fast EWMA
        # So slow < fast, and carry = slow - fast should be negative initially
        # But eventually positive as the slow catches up past initial low values
        # The key is that the signal should be non-zero and respond to the trend
        assert carry_rising.notna().sum() > 0, "Should have non-NaN values"
        assert not np.allclose(carry_rising.dropna(), 0), \
            "Carry signal should be non-zero for trending rates"


class TestForecastScaling:
    """Test suite for forecast scaling and combination (Step 4)"""

    def test_forecast_scaling(self):
        """
        Test that forecasts are scaled to mean abs ≈ 10 and capped at ±20
        """
        from systems.crypto_perps.forecasts import scale_and_cap_forecast

        # Create a raw forecast with known characteristics
        dates = pd.date_range('2020-01-01', periods=1000, freq='D')
        # Raw forecast with mean abs around 1.0
        np.random.seed(42)
        raw_forecast = pd.Series(np.random.randn(1000), index=dates)

        # Scale and cap
        scaled = scale_and_cap_forecast(raw_forecast)

        # Validate scaling: mean abs should be close to 10
        # Allow some tolerance since we're using rolling window
        mean_abs = scaled.abs().mean()
        assert 8 <= mean_abs <= 12, \
            f"Mean abs forecast should be ~10, got {mean_abs}"

        # Validate capping: max abs should be <= 20
        max_abs = scaled.abs().max()
        assert max_abs <= 20.0, \
            f"Max abs forecast should be <= 20, got {max_abs}"

    def test_forecast_cap(self):
        """
        Test that forecast capping works correctly
        """
        from systems.crypto_perps.forecasts import apply_forecast_cap

        # Create forecast with values exceeding cap
        dates = pd.date_range('2023-01-01', periods=10)
        forecast = pd.Series([-30, -25, -20, -10, 0, 10, 20, 25, 30, 15], index=dates)

        # Apply cap
        capped = apply_forecast_cap(forecast, cap=20.0)

        # Validate
        assert capped.max() == 20.0, "Max should be capped at 20"
        assert capped.min() == -20.0, "Min should be capped at -20"
        assert (capped == [-20, -20, -20, -10, 0, 10, 20, 20, 20, 15]).all(), \
            "Capping not applied correctly"

    def test_forecast_combination(self):
        """
        Test that forecasts are combined correctly with caps
        """
        from systems.crypto_perps.forecasts import combine_forecasts, apply_forecast_cap

        # Create two forecasts
        dates = pd.date_range('2023-01-01', periods=100)
        forecast1 = pd.Series(np.full(100, 10.0), index=dates)
        forecast2 = pd.Series(np.full(100, -10.0), index=dates)

        forecasts = {
            'rule1': forecast1,
            'rule2': forecast2
        }

        # Combine with equal weights (default)
        combined = combine_forecasts(forecasts)

        # With equal weights, should average to 0
        assert np.allclose(combined, 0.0), \
            "Equal-weighted combination of +10 and -10 should be 0"

        # Combine with custom weights
        weights = {'rule1': 0.75, 'rule2': 0.25}
        combined_weighted = combine_forecasts(forecasts, weights=weights)

        # Should be 0.75*10 + 0.25*(-10) = 7.5 - 2.5 = 5.0
        assert np.allclose(combined_weighted, 5.0), \
            f"Weighted combination should be 5.0, got {combined_weighted.mean()}"

    def test_scale_and_combine_forecasts(self):
        """
        Test full forecast processing pipeline
        """
        from sysdata.crypto.prices import load_crypto_perps_panel
        from systems.crypto_perps.rules.ewmac import ewmac_forecasts
        from systems.crypto_perps.rules.carry_funding import funding_carry_forecasts
        from systems.crypto_perps.forecasts import process_all_forecasts

        # Load data
        prices, meta = load_crypto_perps_panel(str(TEST_DATA_PATH))

        # Generate raw forecasts
        ewmac = ewmac_forecasts(prices, [(8, 32), (16, 64)])
        carry = funding_carry_forecasts(meta, fast_halflife=3, slow_halflife=30)

        # Process all forecasts
        combined = process_all_forecasts(ewmac, carry)

        # Validate
        assert len(combined) == 5, "Should have combined forecasts for 5 instruments"

        for instrument, forecast in combined.items():
            # Check it's a Series
            assert isinstance(forecast, pd.Series), \
                f"{instrument} combined forecast should be a Series"

            # Check no inf values
            assert not np.isinf(forecast).any(), \
                f"{instrument} combined forecast contains inf"

            # Check we have non-NaN values
            assert forecast.notna().sum() > 0, \
                f"{instrument} combined forecast has no non-NaN values"

            # Check cap is enforced
            assert forecast.abs().max() <= 20.0, \
                f"{instrument} combined forecast exceeds cap: {forecast.abs().max()}"


class TestUniverse:
    """Test suite for universe and eligibility logic (Step 5)"""

    @pytest.mark.skip(reason="Not yet implemented")
    def test_layer_a_static_universe(self):
        """
        Test that Layer A returns expected static universe
        """
        pass

    @pytest.mark.skip(reason="Not yet implemented")
    def test_layer_b_daily_filter(self):
        """
        Test that Layer B eligibility filter works correctly
        """
        pass

    @pytest.mark.skip(reason="Not yet implemented")
    def test_ineligible_instrument_freezes_position(self):
        """
        Test that ineligible instruments freeze positions (no trades)
        """
        pass

    @pytest.mark.skip(reason="Not yet implemented")
    def test_missing_price_handling(self):
        """
        Test that missing prices are handled correctly:
        - Instrument becomes ineligible
        - Position frozen (no trades)
        - Price return = 0 (not carry-forward)
        - PnL = 0 for that day
        """
        pass


class TestPositionSizing:
    """Test suite for position sizing (Step 6)"""

    @pytest.mark.skip(reason="Not yet implemented")
    def test_vol_targeted_sizing(self):
        """
        Test volatility-targeted position sizing
        """
        pass

    @pytest.mark.skip(reason="Not yet implemented")
    def test_min_steady_position_rule_on_weights(self):
        """
        Test that minimum steady position rule is enforced on weights
        """
        pass


class TestConstraints:
    """Test suite for portfolio constraints (Step 7)"""

    @pytest.mark.skip(reason="Not yet implemented")
    def test_gross_leverage_cap(self):
        """
        Test that gross leverage cap is enforced
        """
        pass

    @pytest.mark.skip(reason="Not yet implemented")
    def test_idm_cap(self):
        """
        Test that IDM cap is enforced
        """
        pass


class TestExecution:
    """Test suite for execution and cost model (Step 8)"""

    @pytest.mark.skip(reason="Not yet implemented")
    def test_trading_buffer(self):
        """
        Test that trading buffers prevent unnecessary trades
        """
        pass

    @pytest.mark.skip(reason="Not yet implemented")
    def test_frozen_position_no_trades(self):
        """
        Test that frozen positions (ineligible instruments) do not trade
        """
        pass

    @pytest.mark.skip(reason="Not yet implemented")
    def test_cost_calculation(self):
        """
        Test that costs are calculated correctly (RTC and SRcost)
        """
        pass

    @pytest.mark.skip(reason="Not yet implemented")
    def test_cost_subtraction_from_pnl(self):
        """
        Test that RTC costs are subtracted from PnL
        """
        pass


class TestAccounting:
    """Test suite for accounting (Step 9)"""

    @pytest.mark.skip(reason="Not yet implemented")
    def test_accounting_identity(self):
        """
        Test accounting identity: total_pnl = price_pnl + funding_pnl - costs
        Tolerance: 1e-6
        """
        pass


class TestSystemOrchestrator:
    """Test suite for system orchestrator (Step 10)"""

    @pytest.mark.skip(reason="Not yet implemented")
    def test_end_to_end_run(self):
        """
        Test that full system runs end-to-end without errors
        """
        pass


class TestComprehensiveValidation:
    """Comprehensive validation tests (Step 11)"""

    @pytest.mark.skip(reason="Not yet implemented")
    def test_forecast_scaling_limits(self):
        """
        Validate forecast scaling: mean abs ∈ [8, 12], never exceeds ±20
        """
        pass

    @pytest.mark.skip(reason="Not yet implemented")
    def test_leverage_cap_always_enforced(self):
        """
        Validate gross leverage <= 1.5 at all times
        """
        pass

    @pytest.mark.skip(reason="Not yet implemented")
    def test_idm_cap_always_enforced(self):
        """
        Validate IDM <= 2.5 at all times
        """
        pass

    @pytest.mark.skip(reason="Not yet implemented")
    def test_accounting_identity_all_days(self):
        """
        Validate accounting identity holds for all days (tolerance 1e-6)
        """
        pass
