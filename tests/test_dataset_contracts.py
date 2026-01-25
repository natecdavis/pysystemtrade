import pytest
import pandas as pd
import numpy as np
from pathlib import Path
import os
from sysdata.crypto.schema import validate_schema_compliance, REQUIRED_COLUMNS

# Use test fixture as primary dataset (small, committed to git)
FIXTURE_PATH = Path("data/test_fixtures/btc_eth_jan2023.parquet")

# Large dataset tests (optional - require env var)
LARGE_DATASET_PATH = Path("data/example_crypto_perps.parquet")
RUN_LARGE_TESTS = os.getenv('RUN_LARGE_DATASET_TESTS') == '1'


@pytest.fixture(scope="session")
def fixture_df():
    """Load test fixture once per session, fail if missing (not skip - fixture should be committed)"""
    if not FIXTURE_PATH.exists():
        pytest.fail(
            f"Test fixture not found: {FIXTURE_PATH}\n"
            f"Fixture should be committed to git. Generate it with:\n"
            f"  python scripts/build_example_dataset.py --source real \\\n"
            f"    --instruments BTCUSDT_PERP ETHUSDT_PERP \\\n"
            f"    --start-date 2023-01-01 --end-date 2023-01-31\n"
            f"  mkdir -p data/test_fixtures/\n"
            f"  mv data/example_crypto_perps.parquet data/test_fixtures/btc_eth_jan2023.parquet"
        )
    return pd.read_parquet(FIXTURE_PATH)


class TestDatasetSchema:
    """Validate dataset structure against canonical schema (using test fixture)"""

    def test_required_columns_exist(self, fixture_df):
        df = fixture_df
        required = set(REQUIRED_COLUMNS)
        actual = set(df.columns)
        assert required.issubset(actual), f"Missing columns: {required - actual}"

    def test_column_dtypes_correct(self, fixture_df):
        """Use pandas type helpers, NOT string matching"""
        df = fixture_df

        # Check date is datetime
        assert pd.api.types.is_datetime64_any_dtype(df['date']), \
            f"date: expected datetime, got {df['date'].dtype}"

        # Check numeric columns
        for col in ['close', 'funding_rate', 'adv_notional', 'spread_frac', 'taker_fee_frac']:
            assert pd.api.types.is_numeric_dtype(df[col]), \
                f"{col}: expected numeric, got {df[col].dtype}"

    def test_no_duplicate_date_instrument_pairs(self, fixture_df):
        dups = fixture_df.duplicated(subset=['date', 'instrument'])
        assert not dups.any(), f"Found {dups.sum()} duplicate (date, instrument) pairs"

    def test_dates_monotonic_per_instrument(self, fixture_df):
        for instrument in fixture_df['instrument'].unique():
            inst_df = fixture_df[fixture_df['instrument'] == instrument].sort_values('date')
            assert inst_df['date'].is_monotonic_increasing, \
                f"{instrument}: dates not monotonic"

    def test_schema_compliance(self, fixture_df):
        errors = validate_schema_compliance(fixture_df, require_rectangular=True)  # Fixture is rectangular
        assert not errors, f"Schema validation failed:\n" + "\n".join(errors)


class TestDatasetInvariants:
    """Validate dataset invariants (prices positive, no NaN, etc.)"""

    def test_prices_strictly_positive(self, fixture_df):
        assert (fixture_df['close'] > 0).all(), "Found non-positive prices"
        assert (fixture_df['close'] < 1e6).all(), "Found unrealistic prices > 1e6"

    def test_adv_non_negative(self, fixture_df):
        assert (fixture_df['adv_notional'] >= 0).all(), "Found negative ADV"

    def test_funding_rates_finite(self, fixture_df):
        # Convert to NumPy array before np.isfinite to avoid dtype issues
        funding_np = fixture_df['funding_rate'].to_numpy(dtype=float, na_value=np.nan)
        assert np.isfinite(funding_np).all(), "Found inf/nan in funding_rate"

    def test_funding_rates_in_typical_range(self, fixture_df):
        # Warn if > 10x typical range (not strict error)
        extreme_low = fixture_df['funding_rate'] < -0.1  # -10% daily
        extreme_high = fixture_df['funding_rate'] > 0.3   # +30% daily
        if extreme_low.any() or extreme_high.any():
            print(f"Warning: Found extreme funding rates outside typical range")

    def test_rectangular_panel_fixture(self, fixture_df):
        """Test fixture is rectangular (fixed universe)"""
        date_counts = fixture_df.groupby('instrument')['date'].count()
        assert date_counts.nunique() == 1, \
            f"Fixture should be rectangular: date counts {dict(date_counts)}"

    def test_no_missing_prices(self, fixture_df):
        assert not fixture_df['close'].isna().any(), "Found NaN in close prices"

    def test_spread_and_fees_in_fixture(self, fixture_df):
        """Fixture-specific test: spread/fee should be < 1% (stricter than universal [0, 1))"""
        assert (fixture_df['spread_frac'] >= 0).all() and (fixture_df['spread_frac'] < 0.01).all(), \
            "Fixture spread outside [0, 1%] - universal schema allows [0, 1) but fixture is stricter"
        assert (fixture_df['taker_fee_frac'] >= 0).all() and (fixture_df['taker_fee_frac'] < 0.01).all(), \
            "Fixture taker_fee outside [0, 1%] - universal schema allows [0, 1) but fixture is stricter"


class TestFixtureCoverage:
    """Validate test fixture has expected properties"""

    def test_fixture_date_range(self, fixture_df):
        """Fixture should be Jan 2023 (31 days)"""
        assert fixture_df['date'].min().date().isoformat() == '2023-01-01'
        assert fixture_df['date'].max().date().isoformat() == '2023-01-31'

    def test_fixture_instruments(self, fixture_df):
        """Fixture should have BTC and ETH"""
        expected = {'BTCUSDT_PERP', 'ETHUSDT_PERP'}
        actual = set(fixture_df['instrument'].unique())
        assert expected == actual, f"Fixture instruments mismatch: {actual}"

    def test_fixture_row_count(self, fixture_df):
        """Fixture should have 62 rows (31 days × 2 instruments)"""
        assert len(fixture_df) == 62, f"Expected 62 rows, got {len(fixture_df)}"


@pytest.mark.skipif(not RUN_LARGE_TESTS, reason="Large dataset tests disabled (set RUN_LARGE_DATASET_TESTS=1)")
class TestLargeDatasetCoverage:
    """Optional tests for full example dataset (requires RUN_LARGE_DATASET_TESTS=1)"""

    def test_minimum_date_range(self):
        df = pd.read_parquet(LARGE_DATASET_PATH)
        date_range = (df['date'].max() - df['date'].min()).days
        assert date_range >= 365, f"Dataset has only {date_range} days, need >= 365"

    def test_expected_instruments_present(self):
        df = pd.read_parquet(LARGE_DATASET_PATH)
        expected = {'BTCUSDT_PERP', 'ETHUSDT_PERP', 'BNBUSDT_PERP', 'SOLUSDT_PERP', 'XRPUSDT_PERP'}
        actual = set(df['instrument'].unique())
        assert expected.issubset(actual), f"Missing instruments: {expected - actual}"

    def test_daily_frequency(self):
        df = pd.read_parquet(LARGE_DATASET_PATH)
        for instrument in df['instrument'].unique():
            inst_df = df[df['instrument'] == instrument].sort_values('date')
            date_diffs = inst_df['date'].diff().dt.days.dropna()
            # Most gaps should be 1 day (allow some missing dates)
            one_day_pct = (date_diffs == 1).mean()
            assert one_day_pct >= 0.95, \
                f"{instrument}: only {one_day_pct*100:.1f}% of dates are 1 day apart"
