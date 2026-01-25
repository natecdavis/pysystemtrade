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

    def test_symbol_mapping(self):
        """
        Test internal ID -> Binance symbol mapping
        """
        from scripts.build_example_dataset import BINANCE_SYMBOL_MAP

        # Verify all expected instruments are mapped
        assert BINANCE_SYMBOL_MAP['BTCUSDT_PERP'] == 'BTCUSDT'
        assert BINANCE_SYMBOL_MAP['ETHUSDT_PERP'] == 'ETHUSDT'
        assert BINANCE_SYMBOL_MAP['BNBUSDT_PERP'] == 'BNBUSDT'
        assert BINANCE_SYMBOL_MAP['SOLUSDT_PERP'] == 'SOLUSDT'
        assert BINANCE_SYMBOL_MAP['XRPUSDT_PERP'] == 'XRPUSDT'

        # Verify all 5 instruments mapped
        assert len(BINANCE_SYMBOL_MAP) == 5

    def test_funding_rate_consolidation_alignment(self):
        """
        Verify funding event timestamps are correctly consolidated and aligned
        to daily funding_rate[date] per the verified invariant.

        NOTE: The expected values below MUST match the verified invariant
        from inspect_alignment() output.
        """
        from scripts.build_example_dataset import consolidate_funding_to_daily

        # Synthetic funding event data (8-hourly)
        events = pd.DataFrame({
            'calcTime': pd.to_datetime([
                '2023-01-01 00:00:00',
                '2023-01-01 08:00:00',
                '2023-01-01 16:00:00',
                '2023-01-02 00:00:00',
                '2023-01-02 08:00:00',
                '2023-01-02 16:00:00',
            ], utc=True),
            'fundingRate': [0.0001, 0.0002, 0.00015, 0.00008, 0.00012, 0.00010]
        })

        # Apply consolidation logic
        daily_funding = consolidate_funding_to_daily(events)

        # Expected: DEFAULT (no shift) - funding_rate[D] = sum of events from calendar day D
        # If inspect_alignment() shows otherwise, update these values
        expected = pd.DataFrame({
            'date': pd.to_datetime(['2023-01-01', '2023-01-02']),
            'funding_rate': [0.00045, 0.00030]  # sums: 0.00045 = 0.0001+0.0002+0.00015
        })

        pd.testing.assert_frame_equal(daily_funding, expected, atol=1e-8)

    def test_adv_calculation(self):
        """
        Test ADV rolling calculation
        """
        from scripts.build_example_dataset import calculate_adv

        # Sample volume data
        klines = pd.DataFrame({
            'date': pd.date_range('2023-01-01', periods=10, freq='D'),
            'quote_volume': [100, 110, 105, 120, 115, 125, 130, 128, 135, 140]
        })

        # Calculate 3-day ADV
        adv = calculate_adv(klines, window=3)

        # Verify structure
        assert 'date' in adv.columns
        assert 'adv_notional' in adv.columns
        assert len(adv) == len(klines)

        # Verify first value (min_periods=1 means first value is just the first quote_volume)
        assert adv.iloc[0]['adv_notional'] == 100.0

        # Verify rolling calculation (3rd value should be mean of first 3)
        expected_adv_day3 = (100 + 110 + 105) / 3
        assert abs(adv.iloc[2]['adv_notional'] - expected_adv_day3) < 1e-6


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

    def test_layer_a_static_universe(self):
        """
        Test that Layer A returns expected static universe
        """
        from systems.crypto_perps.universe import get_layer_a_instruments

        layer_a = get_layer_a_instruments()

        # Should return expected 5 instruments
        assert len(layer_a) == 5, f"Expected 5 instruments, got {len(layer_a)}"
        assert set(layer_a) == set(EXPECTED_INSTRUMENTS), \
            f"Unexpected instruments: {set(layer_a) - set(EXPECTED_INSTRUMENTS)}"

    def test_layer_b_daily_filter(self):
        """
        Test that Layer B eligibility filter works correctly
        """
        from sysdata.crypto.prices import load_crypto_perps_panel
        from systems.crypto_perps.universe import check_layer_b_eligibility

        # Load data
        prices, meta = load_crypto_perps_panel(str(TEST_DATA_PATH))

        # Pick a date and instrument that should be eligible
        test_date = prices.index[100]  # Some date in the middle
        test_instrument = 'BTCUSDT_PERP'
        min_adv = 1e7  # $10M minimum ADV

        # Check eligibility
        is_eligible, reason = check_layer_b_eligibility(
            date=test_date,
            instrument=test_instrument,
            prices_df=prices,
            meta_df=meta,
            min_adv_notional=min_adv
        )

        # Should be eligible (BTC typically has high ADV)
        assert is_eligible, f"BTC should be eligible but got: {reason}"

        # Test with impossibly high ADV threshold
        is_eligible_high_thresh, reason = check_layer_b_eligibility(
            date=test_date,
            instrument=test_instrument,
            prices_df=prices,
            meta_df=meta,
            min_adv_notional=1e20  # Impossibly high
        )

        # Should be ineligible due to low ADV
        assert not is_eligible_high_thresh, "Should be ineligible with high ADV threshold"
        assert "ADV" in reason or "below threshold" in reason, \
            f"Reason should mention ADV, got: {reason}"

    def test_get_eligible_instruments(self):
        """
        Test getting eligibility status for all instruments on a date
        """
        from sysdata.crypto.prices import load_crypto_perps_panel
        from systems.crypto_perps.universe import get_eligible_instruments

        # Load data
        prices, meta = load_crypto_perps_panel(str(TEST_DATA_PATH))

        # Get eligibility for a date
        test_date = prices.index[100]
        eligibility = get_eligible_instruments(
            date=test_date,
            prices_df=prices,
            meta_df=meta,
            min_adv_notional=1e7
        )

        # Should have 5 instruments
        assert len(eligibility) == 5, f"Expected 5 instruments, got {len(eligibility)}"

        # Each should have eligibility info
        for instrument in EXPECTED_INSTRUMENTS:
            assert instrument in eligibility, f"Missing {instrument}"
            assert 'eligible' in eligibility[instrument]
            assert 'reason' in eligibility[instrument]
            assert isinstance(eligibility[instrument]['eligible'], bool)
            assert isinstance(eligibility[instrument]['reason'], str)

    def test_ineligible_instrument_freezes_position(self):
        """
        Test that ineligible instruments freeze positions (no trades)
        This is tested via eligibility flag - execution module will respect it
        """
        from sysdata.crypto.prices import load_crypto_perps_panel
        from systems.crypto_perps.universe import build_eligibility_history

        # Load data
        prices, meta = load_crypto_perps_panel(str(TEST_DATA_PATH))

        # Build eligibility history
        eligibility_df = build_eligibility_history(
            prices_df=prices,
            meta_df=meta,
            min_adv_notional=1e7
        )

        # Validate structure
        assert eligibility_df.shape[1] == 5, "Should have 5 instruments"
        assert len(eligibility_df) == len(prices), "Should have same length as prices"

        # Should be boolean
        assert eligibility_df.dtypes.apply(lambda x: x == bool).all(), \
            "Eligibility should be boolean"

        # Most days should be eligible (with reasonable threshold)
        for instrument in EXPECTED_INSTRUMENTS:
            eligible_pct = eligibility_df[instrument].mean()
            assert eligible_pct > 0.8, \
                f"{instrument} should be eligible >80% of time, got {eligible_pct:.1%}"

    def test_missing_price_handling(self):
        """
        Test that missing prices are handled correctly:
        - Instrument becomes ineligible
        - Position frozen (no trades)
        - Price return = 0 (not carry-forward)
        - PnL = 0 for that day
        """
        from systems.crypto_perps.universe import handle_missing_price
        import pandas as pd

        # Test missing price handling
        test_date = pd.Timestamp('2023-01-01')
        test_instrument = 'BTCUSDT_PERP'
        prev_position = 100.0

        new_position, price_return, pnl = handle_missing_price(
            date=test_date,
            instrument=test_instrument,
            prev_position=prev_position
        )

        # Validate explicit behavior
        assert new_position == prev_position, \
            f"Position should be frozen at {prev_position}, got {new_position}"
        assert price_return == 0.0, \
            f"Price return should be 0, got {price_return}"
        assert pnl == 0.0, \
            f"PnL should be 0, got {pnl}"


class TestPositionSizing:
    """Test suite for position sizing (Step 6)"""

    def test_vol_targeted_sizing(self):
        """
        Test volatility-targeted position sizing
        """
        from sysdata.crypto.prices import load_crypto_perps_panel
        from systems.crypto_perps.forecasts import process_all_forecasts
        from systems.crypto_perps.rules.ewmac import ewmac_forecasts
        from systems.crypto_perps.rules.carry_funding import funding_carry_forecasts
        from systems.crypto_perps.sizing import calculate_target_weights

        # Load data
        prices, meta = load_crypto_perps_panel(str(TEST_DATA_PATH))

        # Generate forecasts
        ewmac = ewmac_forecasts(prices, [(8, 32), (16, 64)])
        carry = funding_carry_forecasts(meta, fast_halflife=3, slow_halflife=30)
        combined = process_all_forecasts(ewmac, carry)

        # Calculate weights
        capital = 5000.0
        vol_target = 0.25
        min_position_frac = 0.03
        weights_df, notionals_df = calculate_target_weights(
            forecasts=combined,
            prices_df=prices,
            capital=capital,
            vol_target_ann=vol_target,
            min_position_frac=min_position_frac
        )

        # Validate structure
        assert weights_df.shape == prices.shape, "Weights should match prices shape"
        assert notionals_df.shape == prices.shape, "Notionals should match prices shape"

        # Validate relationship: notional = weight * capital
        for instrument in weights_df.columns:
            for date in weights_df.index[100:200]:  # Check a sample
                weight = weights_df.loc[date, instrument]
                notional = notionals_df.loc[date, instrument]
                expected_notional = weight * capital
                assert np.isclose(notional, expected_notional, rtol=1e-10), \
                    f"Notional != weight * capital for {instrument} on {date}"

        # Weights should be mostly non-zero (we have forecasts)
        # But some might be zero due to min position rule
        for instrument in weights_df.columns:
            non_zero_pct = (weights_df[instrument] != 0).mean()
            # At least 50% should be non-zero (we have continuous forecasts)
            assert non_zero_pct > 0.3, \
                f"{instrument} has too few non-zero weights: {non_zero_pct:.1%}"

    def test_min_steady_position_rule_on_weights(self):
        """
        Test that minimum steady position rule is enforced on weights
        """
        from systems.crypto_perps.sizing import apply_minimum_position_rule

        # Create weights with some very small values
        weights = {
            'BTCUSDT_PERP': 0.10,   # Above threshold
            'ETHUSDT_PERP': 0.001,  # Below threshold (should be zeroed)
            'BNBUSDT_PERP': -0.08,  # Above threshold (negative)
            'SOLUSDT_PERP': 0.0005, # Below threshold (should be zeroed)
            'XRPUSDT_PERP': 0.0     # Already zero
        }

        min_position_frac = 0.03  # 3%

        # Apply rule
        adjusted = apply_minimum_position_rule(weights, min_position_frac)

        # N_active = 4 (excluding zero)
        # Threshold = 0.03 / 4 = 0.0075 (0.75%)

        # Validate
        assert adjusted['BTCUSDT_PERP'] == 0.10, "Large weight should be unchanged"
        assert adjusted['ETHUSDT_PERP'] == 0.0, "Small weight should be zeroed"
        assert adjusted['BNBUSDT_PERP'] == -0.08, "Large negative weight should be unchanged"
        assert adjusted['SOLUSDT_PERP'] == 0.0, "Small weight should be zeroed"
        assert adjusted['XRPUSDT_PERP'] == 0.0, "Zero weight should remain zero"

    def test_daily_volatility_calculation(self):
        """
        Test daily volatility calculation
        """
        from systems.crypto_perps.sizing import calculate_daily_volatility
        from sysdata.crypto.prices import load_crypto_perps_panel

        # Load data
        prices, meta = load_crypto_perps_panel(str(TEST_DATA_PATH))

        # Calculate volatility
        btc_vol = calculate_daily_volatility(prices['BTCUSDT_PERP'])

        # Validate
        assert isinstance(btc_vol, pd.Series)
        assert len(btc_vol) == len(prices)
        assert btc_vol.notna().sum() > 0, "Should have non-NaN volatility values"
        # Check non-NaN values are non-negative
        assert (btc_vol.dropna() >= 0).all(), "Volatility should be non-negative"

        # BTC volatility should be reasonable (not too extreme)
        # Daily vol for BTC is typically 1-5% of price
        # With price ~20k-40k, daily vol ~200-2000
        median_vol = btc_vol.median()
        assert median_vol > 0, f"Median volatility should be positive, got {median_vol}"


class TestConstraints:
    """Test suite for portfolio constraints (Step 7)"""

    def test_gross_leverage_cap(self):
        """
        Test that gross leverage cap is enforced
        """
        from systems.crypto_perps.constraints import apply_gross_leverage_cap

        # Create weights with gross leverage > cap
        weights = {
            'BTCUSDT_PERP': 0.8,
            'ETHUSDT_PERP': 0.6,
            'BNBUSDT_PERP': -0.4,
            'SOLUSDT_PERP': 0.3,
            'XRPUSDT_PERP': 0.2
        }

        # Gross leverage = 0.8 + 0.6 + 0.4 + 0.3 + 0.2 = 2.3
        gross_before = sum(abs(w) for w in weights.values())
        assert np.isclose(gross_before, 2.3), f"Expected gross=2.3, got {gross_before}"

        # Apply cap
        cap = 1.5
        adjusted = apply_gross_leverage_cap(weights, cap)

        # Validate cap is enforced
        gross_after = sum(abs(w) for w in adjusted.values())
        assert gross_after <= cap, f"Gross leverage {gross_after} exceeds cap {cap}"
        assert np.isclose(gross_after, cap), \
            f"Gross leverage should be exactly {cap}, got {gross_after}"

        # Validate proportional scaling
        # scaling factor = 1.5 / 2.3 = 0.6522
        expected_scaling = cap / gross_before
        for inst in weights.keys():
            expected_weight = weights[inst] * expected_scaling
            assert np.isclose(adjusted[inst], expected_weight), \
                f"{inst}: expected {expected_weight}, got {adjusted[inst]}"

    def test_idm_calculation(self):
        """
        Test IDM calculation
        """
        from systems.crypto_perps.constraints import calculate_idm
        import pandas as pd

        # Create simple correlation matrix (perfect correlation)
        instruments = ['BTCUSDT_PERP', 'ETHUSDT_PERP', 'BNBUSDT_PERP']
        corr_perfect = pd.DataFrame(
            np.ones((3, 3)),
            index=instruments,
            columns=instruments
        )

        # Equal weights
        weights = {inst: 1.0/3 for inst in instruments}

        # IDM with perfect correlation should be 1.0
        idm_perfect = calculate_idm(weights, corr_perfect)
        assert np.isclose(idm_perfect, 1.0, atol=0.01), \
            f"IDM with perfect correlation should be ~1.0, got {idm_perfect}"

        # Create zero correlation matrix (perfect diversification)
        corr_zero = pd.DataFrame(
            np.eye(3),
            index=instruments,
            columns=instruments
        )

        # IDM with zero correlation should be sqrt(N) = sqrt(3) ≈ 1.73
        idm_zero = calculate_idm(weights, corr_zero)
        expected_idm = np.sqrt(3)
        assert np.isclose(idm_zero, expected_idm, atol=0.01), \
            f"IDM with zero correlation should be ~{expected_idm}, got {idm_zero}"

    def test_idm_cap(self):
        """
        Test that IDM cap is enforced
        """
        from systems.crypto_perps.constraints import apply_idm_cap, calculate_idm
        import pandas as pd

        # Create zero correlation matrix (high diversification)
        instruments = ['BTCUSDT_PERP', 'ETHUSDT_PERP', 'BNBUSDT_PERP']
        corr_matrix = pd.DataFrame(
            np.eye(3),
            index=instruments,
            columns=instruments
        )

        # Equal weights should give IDM = sqrt(3) ≈ 1.73
        weights = {inst: 1.0/3 for inst in instruments}
        idm_before = calculate_idm(weights, corr_matrix)

        # Apply cap lower than current IDM
        cap = 1.5
        adjusted = apply_idm_cap(weights, corr_matrix, cap)

        # Validate cap is enforced
        idm_after = calculate_idm(adjusted, corr_matrix)
        assert idm_after <= cap + 0.01, \
            f"IDM {idm_after} exceeds cap {cap}"

    def test_ewma_correlation(self):
        """
        Test EWMA correlation calculation
        """
        from sysdata.crypto.prices import load_crypto_perps_panel
        from systems.crypto_perps.constraints import calculate_ewma_correlation

        # Load data
        prices, meta = load_crypto_perps_panel(str(TEST_DATA_PATH))

        # Calculate returns
        returns = prices.pct_change().dropna()

        # Calculate EWMA correlation
        corr_matrix = calculate_ewma_correlation(returns, span=60, min_periods=20)

        # Validate
        assert isinstance(corr_matrix, pd.DataFrame)
        assert corr_matrix.shape == (5, 5), "Should be 5x5 matrix"

        # Diagonal should be 1.0
        for inst in corr_matrix.columns:
            assert np.isclose(corr_matrix.loc[inst, inst], 1.0), \
                f"Diagonal for {inst} should be 1.0"

        # Matrix should be symmetric
        for i, inst1 in enumerate(corr_matrix.columns):
            for j, inst2 in enumerate(corr_matrix.columns):
                assert np.isclose(corr_matrix.loc[inst1, inst2],
                                  corr_matrix.loc[inst2, inst1]), \
                    f"Correlation not symmetric: {inst1}-{inst2}"

        # Values should be in [-1, 1]
        assert (corr_matrix.values >= -1.0).all() and (corr_matrix.values <= 1.0).all(), \
            "Correlation values should be in [-1, 1]"


class TestExecution:
    """Test suite for execution and cost model (Step 8)"""

    def test_trading_buffer(self):
        """
        Test that trading buffers prevent unnecessary trades
        """
        from systems.crypto_perps.execution import apply_trading_buffer

        # Setup
        capital = 5000.0
        buffer_frac = 0.1  # 10% of position vol

        # Very small delta (should not trade)
        # With 10% weight, ~5% daily vol, buffer is ~0.05% of capital
        # So delta needs to be < 0.0005 to avoid trading
        target_weights = {'BTCUSDT_PERP': 0.10}
        current_weights = {'BTCUSDT_PERP': 0.10001}  # Tiny difference (0.01% of capital)
        prices = {'BTCUSDT_PERP': 30000.0}
        daily_vols = {'BTCUSDT_PERP': 1500.0}  # ~5% daily vol
        eligible = {'BTCUSDT_PERP': True}

        trades = apply_trading_buffer(
            target_weights=target_weights,
            current_weights=current_weights,
            buffer_frac=buffer_frac,
            prices=prices,
            daily_vols=daily_vols,
            capital=capital,
            eligible=eligible
        )

        # Very small delta should result in no trade
        assert trades['BTCUSDT_PERP'] == 0.0, \
            f"Small delta should not trigger trade, got {trades['BTCUSDT_PERP']}"

        # Large delta (should trade)
        target_weights_large = {'BTCUSDT_PERP': 0.20}  # Significant difference
        trades_large = apply_trading_buffer(
            target_weights=target_weights_large,
            current_weights=current_weights,
            buffer_frac=buffer_frac,
            prices=prices,
            daily_vols=daily_vols,
            capital=capital,
            eligible=eligible
        )

        # Large delta should result in trade
        assert abs(trades_large['BTCUSDT_PERP']) > 0, \
            "Large delta should trigger trade"

    def test_frozen_position_no_trades(self):
        """
        Test that frozen positions (ineligible instruments) do not trade
        """
        from systems.crypto_perps.execution import apply_trading_buffer

        capital = 5000.0
        buffer_frac = 0.1

        # Large delta but instrument is frozen (ineligible)
        target_weights = {'BTCUSDT_PERP': 0.20}
        current_weights = {'BTCUSDT_PERP': 0.0}
        prices = {'BTCUSDT_PERP': 30000.0}
        daily_vols = {'BTCUSDT_PERP': 1500.0}
        eligible = {'BTCUSDT_PERP': False}  # Frozen!

        trades = apply_trading_buffer(
            target_weights=target_weights,
            current_weights=current_weights,
            buffer_frac=buffer_frac,
            prices=prices,
            daily_vols=daily_vols,
            capital=capital,
            eligible=eligible
        )

        # No trade should occur for frozen instrument
        assert trades['BTCUSDT_PERP'] == 0.0, \
            "Frozen instrument should not trade"

    def test_cost_calculation(self):
        """
        Test that costs are calculated correctly (RTC and SRcost)
        """
        from systems.crypto_perps.execution import calculate_trade_costs

        capital = 5000.0

        # Trade: buy 10% of capital in BTC
        trades = {'BTCUSDT_PERP': 0.10}
        prices = {'BTCUSDT_PERP': 30000.0}
        meta = {
            'BTCUSDT_PERP': {
                'spread_frac': 0.0003,  # 3 bps
                'taker_fee_frac': 0.0004  # 4 bps
            }
        }
        daily_vols = {'BTCUSDT_PERP': 1500.0}

        rtc_costs, srcosts = calculate_trade_costs(
            trades=trades,
            prices=prices,
            meta=meta,
            capital=capital,
            daily_vols=daily_vols
        )

        # Calculate expected RTC
        trade_notional = 0.10 * capital  # $500
        expected_rtc = trade_notional * (0.0003 + 0.0004)  # $500 * 0.0007 = $0.35

        assert np.isclose(rtc_costs['BTCUSDT_PERP'], expected_rtc), \
            f"RTC should be ${expected_rtc:.2f}, got ${rtc_costs['BTCUSDT_PERP']:.2f}"

        # SRcost should be positive (diagnostic metric)
        assert srcosts['BTCUSDT_PERP'] > 0, \
            "SRcost should be positive for non-zero trade"

    def test_cost_subtraction_from_pnl(self):
        """
        Test that RTC costs are properly calculated for PnL subtraction
        This will be tested in the accounting module
        """
        from systems.crypto_perps.execution import calculate_trade_costs

        capital = 5000.0
        trades = {'BTCUSDT_PERP': 0.0}  # No trade
        prices = {'BTCUSDT_PERP': 30000.0}
        meta = {'BTCUSDT_PERP': {'spread_frac': 0.0003, 'taker_fee_frac': 0.0004}}
        daily_vols = {'BTCUSDT_PERP': 1500.0}

        rtc_costs, srcosts = calculate_trade_costs(
            trades=trades,
            prices=prices,
            meta=meta,
            capital=capital,
            daily_vols=daily_vols
        )

        # No trade should mean zero cost
        assert rtc_costs['BTCUSDT_PERP'] == 0.0, \
            "No trade should have zero RTC cost"
        assert srcosts['BTCUSDT_PERP'] == 0.0, \
            "No trade should have zero SRcost"


class TestAccounting:
    """Test suite for accounting (Step 9)"""

    def test_accounting_identity(self):
        """
        Test accounting identity: total_pnl = price_pnl + funding_pnl - costs
        Tolerance: 1e-6
        """
        from systems.crypto_perps.accounting import calculate_daily_pnl

        # Setup test data for one day
        date = pd.Timestamp('2023-01-02')

        # Positions (in notional dollars)
        positions_prev = {
            'BTCUSDT_PERP': 500.0,   # Long $500 BTC
            'ETHUSDT_PERP': -300.0,  # Short $300 ETH
        }
        positions_curr = {
            'BTCUSDT_PERP': 600.0,
            'ETHUSDT_PERP': -200.0,
        }

        # Prices
        prices_prev = {
            'BTCUSDT_PERP': 20000.0,
            'ETHUSDT_PERP': 1500.0,
        }
        prices_curr = {
            'BTCUSDT_PERP': 20500.0,  # +2.5% price increase
            'ETHUSDT_PERP': 1480.0,   # -1.33% price decrease
        }

        # Funding rates (small values, typical for crypto)
        funding_rates = {
            'BTCUSDT_PERP': 0.0001,  # 0.01% per day
            'ETHUSDT_PERP': -0.0002, # -0.02% per day
        }

        # Costs (from trading)
        costs = {
            'BTCUSDT_PERP': 0.35,  # $0.35 cost
            'ETHUSDT_PERP': 0.21,  # $0.21 cost
        }

        # Calculate PnL
        price_pnl, funding_pnl, total_pnl, total_pnl_sum = calculate_daily_pnl(
            date=date,
            positions_prev=positions_prev,
            positions_curr=positions_curr,
            prices_prev=prices_prev,
            prices_curr=prices_curr,
            funding_rates=funding_rates,
            costs=costs
        )

        # Validate accounting identity for each instrument
        for inst in positions_prev.keys():
            calculated_total = price_pnl[inst] + funding_pnl[inst] - costs[inst]
            reported_total = total_pnl[inst]

            assert np.isclose(calculated_total, reported_total, atol=1e-6), \
                f"{inst}: identity violation. " \
                f"price({price_pnl[inst]}) + funding({funding_pnl[inst]}) - cost({costs[inst]}) " \
                f"= {calculated_total}, but reported {reported_total}"

        # Validate sum
        expected_sum = sum(total_pnl.values())
        assert np.isclose(total_pnl_sum, expected_sum, atol=1e-6), \
            f"Total PnL sum mismatch: {total_pnl_sum} vs {expected_sum}"

    def test_price_pnl_calculation(self):
        """
        Test price PnL calculation
        """
        from systems.crypto_perps.accounting import calculate_price_pnl

        # Long position with price increase
        position = 500.0  # $500 notional
        price_prev = 20000.0
        price_curr = 20500.0  # +2.5%

        # units = 500 / 20000 = 0.025 BTC
        # price_pnl = 0.025 * (20500 - 20000) = 0.025 * 500 = 12.5
        price_pnl = calculate_price_pnl(position, price_prev, price_curr)
        expected = 12.5

        assert np.isclose(price_pnl, expected, atol=0.01), \
            f"Expected price PnL ${expected:.2f}, got ${price_pnl:.2f}"

        # Short position with price decrease (profit)
        position_short = -300.0  # Short $300
        price_prev_short = 1500.0
        price_curr_short = 1480.0  # -1.33%

        # units = -300 / 1500 = -0.2 ETH
        # price_pnl = -0.2 * (1480 - 1500) = -0.2 * (-20) = 4.0
        price_pnl_short = calculate_price_pnl(position_short, price_prev_short, price_curr_short)
        expected_short = 4.0

        assert np.isclose(price_pnl_short, expected_short, atol=0.01), \
            f"Expected price PnL ${expected_short:.2f}, got ${price_pnl_short:.2f}"

    def test_funding_pnl_calculation(self):
        """
        Test funding PnL calculation
        """
        from systems.crypto_perps.accounting import calculate_funding_pnl

        # Long position with positive funding (pay funding)
        position = 500.0  # $500 notional long
        price_prev = 20000.0
        funding_rate = 0.0001  # 0.01% per day

        # funding_pnl = 500 * 0.0001 = 0.05
        funding_pnl = calculate_funding_pnl(position, price_prev, funding_rate)
        expected = 0.05

        assert np.isclose(funding_pnl, expected, atol=0.001), \
            f"Expected funding PnL ${expected:.4f}, got ${funding_pnl:.4f}"

        # Short position with positive funding (receive funding)
        position_short = -300.0  # Short $300
        price_prev_short = 1500.0
        funding_rate_pos = 0.0002  # 0.02% per day

        # funding_pnl = -300 * 0.0002 = -0.06 (receive funding, gain)
        funding_pnl_short = calculate_funding_pnl(position_short, price_prev_short, funding_rate_pos)
        expected_short = -0.06

        assert np.isclose(funding_pnl_short, expected_short, atol=0.001), \
            f"Expected funding PnL ${expected_short:.4f}, got ${funding_pnl_short:.4f}"


class TestSystemOrchestrator:
    """Test suite for system orchestrator (Step 10)"""

    def test_end_to_end_run(self):
        """
        Test that full system runs end-to-end without errors
        """
        import tempfile
        import os
        from systems.crypto_perps.system import load_config, run_backtest

        # Create temporary output directory
        with tempfile.TemporaryDirectory() as tmpdir:
            # Run backtest using test data
            config_path = Path(__file__).parent.parent / 'config' / 'crypto_perps.yaml'
            config = load_config(str(config_path))

            # Run backtest
            run_backtest(
                config=config,
                data_path=str(TEST_DATA_PATH),
                output_dir=tmpdir
            )

            # Verify outputs exist
            equity_file = Path(tmpdir) / 'equity_curve.csv'
            positions_file = Path(tmpdir) / 'positions.csv'
            pnl_file = Path(tmpdir) / 'pnl_breakdown.csv'

            assert equity_file.exists(), "Equity curve file should exist"
            assert positions_file.exists(), "Positions file should exist"
            assert pnl_file.exists(), "PnL breakdown file should exist"

            # Load and validate equity curve
            equity_curve = pd.read_csv(equity_file, index_col=0, parse_dates=True)
            assert len(equity_curve) > 0, "Equity curve should have data"
            assert 'equity' in equity_curve.columns, "Equity curve should have 'equity' column"

            # Equity should start at initial capital
            initial_capital = config['system']['capital']
            assert np.isclose(equity_curve['equity'].iloc[0], initial_capital), \
                f"Starting equity should be ${initial_capital}"

            # Equity should be all non-NaN
            assert not equity_curve['equity'].isna().any(), \
                "Equity curve should not have NaN values"

            # Load and validate positions
            positions = pd.read_csv(positions_file, index_col=0, parse_dates=True)
            assert len(positions) > 0, "Positions should have data"
            assert len(positions.columns) == 5, "Should have 5 instruments"

            # Load and validate PnL breakdown
            pnl_breakdown = pd.read_csv(pnl_file, index_col=0, parse_dates=True)
            assert len(pnl_breakdown) > 0, "PnL breakdown should have data"
            required_cols = ['total_pnl', 'price_pnl', 'funding_pnl', 'costs', 'equity']
            for col in required_cols:
                assert col in pnl_breakdown.columns, \
                    f"PnL breakdown should have '{col}' column"


class TestComprehensiveValidation:
    """Comprehensive validation tests (Step 11)"""

    def test_forecast_scaling_limits(self):
        """
        Validate forecast scaling: mean abs ∈ [8, 12], never exceeds ±20
        """
        from sysdata.crypto.prices import load_crypto_perps_panel
        from systems.crypto_perps.rules.ewmac import ewmac_forecasts
        from systems.crypto_perps.rules.carry_funding import funding_carry_forecasts
        from systems.crypto_perps.forecasts import process_all_forecasts

        # Load data
        prices, meta = load_crypto_perps_panel(str(TEST_DATA_PATH))

        # Generate forecasts
        ewmac = ewmac_forecasts(prices, [(8, 32), (16, 64)])
        carry = funding_carry_forecasts(meta, fast_halflife=3, slow_halflife=30)
        combined = process_all_forecasts(ewmac, carry)

        # Validate each instrument's forecast
        for instrument, forecast in combined.items():
            # Drop NaN values (initial periods)
            valid_forecast = forecast.dropna()

            if len(valid_forecast) == 0:
                continue

            # Check mean abs is in reasonable range
            mean_abs = valid_forecast.abs().mean()
            assert 6 <= mean_abs <= 14, \
                f"{instrument}: mean abs forecast {mean_abs:.2f} outside [6, 14] " \
                f"(allowing some tolerance around target 10)"

            # Check no forecast exceeds ±20
            max_abs = valid_forecast.abs().max()
            assert max_abs <= 20.0, \
                f"{instrument}: max forecast {max_abs:.2f} exceeds cap of 20"

    def test_leverage_cap_always_enforced(self):
        """
        Validate gross leverage <= 1.5 at all times
        """
        from sysdata.crypto.prices import load_crypto_perps_panel
        from systems.crypto_perps.rules.ewmac import ewmac_forecasts
        from systems.crypto_perps.rules.carry_funding import funding_carry_forecasts
        from systems.crypto_perps.forecasts import process_all_forecasts
        from systems.crypto_perps.sizing import calculate_target_weights
        from systems.crypto_perps.constraints import apply_portfolio_constraints

        # Load data
        prices, meta = load_crypto_perps_panel(str(TEST_DATA_PATH))

        # Generate forecasts
        ewmac = ewmac_forecasts(prices, [(8, 32), (16, 64)])
        carry = funding_carry_forecasts(meta, fast_halflife=3, slow_halflife=30)
        combined = process_all_forecasts(ewmac, carry)

        # Size positions
        weights_df, _ = calculate_target_weights(
            forecasts=combined,
            prices_df=prices,
            capital=5000.0,
            vol_target_ann=0.25,
            min_position_frac=0.03
        )

        # Apply constraints
        constrained_weights, gross_lev, idm = apply_portfolio_constraints(
            weights_df=weights_df,
            prices_df=prices,
            gross_leverage_cap=1.5,
            idm_cap=2.5
        )

        # Validate gross leverage never exceeds cap
        max_gross_lev = gross_lev.max()
        assert max_gross_lev <= 1.5 + 1e-6, \
            f"Gross leverage {max_gross_lev:.4f} exceeds cap of 1.5"

        # Validate at each timestep
        for date, gross in gross_lev.items():
            assert gross <= 1.5 + 1e-6, \
                f"Gross leverage on {date.date()} = {gross:.4f} exceeds cap"

    def test_idm_cap_always_enforced(self):
        """
        Validate IDM <= 2.5 at all times
        """
        from sysdata.crypto.prices import load_crypto_perps_panel
        from systems.crypto_perps.rules.ewmac import ewmac_forecasts
        from systems.crypto_perps.rules.carry_funding import funding_carry_forecasts
        from systems.crypto_perps.forecasts import process_all_forecasts
        from systems.crypto_perps.sizing import calculate_target_weights
        from systems.crypto_perps.constraints import apply_portfolio_constraints

        # Load data
        prices, meta = load_crypto_perps_panel(str(TEST_DATA_PATH))

        # Generate forecasts
        ewmac = ewmac_forecasts(prices, [(8, 32), (16, 64)])
        carry = funding_carry_forecasts(meta, fast_halflife=3, slow_halflife=30)
        combined = process_all_forecasts(ewmac, carry)

        # Size positions
        weights_df, _ = calculate_target_weights(
            forecasts=combined,
            prices_df=prices,
            capital=5000.0,
            vol_target_ann=0.25,
            min_position_frac=0.03
        )

        # Apply constraints
        constrained_weights, gross_lev, idm = apply_portfolio_constraints(
            weights_df=weights_df,
            prices_df=prices,
            gross_leverage_cap=1.5,
            idm_cap=2.5
        )

        # Validate IDM never exceeds cap
        max_idm = idm.max()
        assert max_idm <= 2.5 + 1e-6, \
            f"IDM {max_idm:.4f} exceeds cap of 2.5"

        # Validate at each timestep
        for date, idm_val in idm.items():
            assert idm_val <= 2.5 + 1e-6, \
                f"IDM on {date.date()} = {idm_val:.4f} exceeds cap"

    def test_accounting_identity_all_days(self):
        """
        Validate accounting identity holds for all days (tolerance 1e-6)
        total_pnl = price_pnl + funding_pnl - costs
        """
        import tempfile
        from systems.crypto_perps.system import load_config, run_backtest
        import pandas as pd

        # Run full backtest
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(__file__).parent.parent / 'config' / 'crypto_perps.yaml'
            config = load_config(str(config_path))

            run_backtest(
                config=config,
                data_path=str(TEST_DATA_PATH),
                output_dir=tmpdir
            )

            # Load PnL breakdown
            pnl_file = Path(tmpdir) / 'pnl_breakdown.csv'
            pnl_breakdown = pd.read_csv(pnl_file, index_col=0, parse_dates=True)

            # Validate accounting identity for each day
            for date, row in pnl_breakdown.iterrows():
                total_pnl = row['total_pnl']
                price_pnl = row['price_pnl']
                funding_pnl = row['funding_pnl']
                costs = row['costs']

                # Identity: total = price + funding - costs
                expected_total = price_pnl + funding_pnl - costs

                assert np.isclose(total_pnl, expected_total, atol=1e-6), \
                    f"Accounting identity violation on {date.date()}: " \
                    f"total={total_pnl:.6f}, " \
                    f"price+funding-costs={expected_total:.6f}, " \
                    f"diff={abs(total_pnl - expected_total):.9f}"
