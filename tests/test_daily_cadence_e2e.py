"""
End-to-end smoke test for daily cadence (V1) workflow.

Single comprehensive test that runs the full pipeline with mocked API
and verifies all critical invariants:

1. Two-date concept (expected vs dataset as_of_date)
2. Staleness computed relative to expected date
3. Rectangular panel invariant (no NaNs)
4. Staleness overlay triggers correctly
5. Audit bundle contains all required V1 metadata
"""

import tempfile
from pathlib import Path
from datetime import date, datetime, timedelta
from unittest.mock import patch, Mock
import json
import zipfile
import io

import pandas as pd
import pytest

from sysdata.crypto.binance_api import BinanceAPIClient
from sysdata.crypto.data_status import (
    compute_dates_and_staleness,
    generate_data_status_report_v1
)
from scripts.build_example_dataset import build_real_crypto_dataset, BINANCE_SYMBOL_MAP
from systems.crypto_perps.staleness_overlay import apply_staleness_overlay
from systems.crypto_perps.trade_plan import load_staleness_data


class TestDailyCadenceE2E:
    """
    End-to-end smoke test for daily cadence workflow.

    Scenario:
    - BTCUSDT: up-to-date (has data through expected_date)
    - ETHUSDT: lagging by 1 day (missing latest day)
    - Expected: 2026-01-27 (yesterday)
    - Dataset: 2026-01-26 (min across instruments)
    """

    @pytest.fixture
    def temp_env(self):
        """Create temporary environment with synthetic data."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Real structure: data/raw/{klines,funding_rates,metadata,binance/api_cache}
            data_raw_dir = Path(tmpdir) / 'data' / 'raw'
            data_raw_dir.mkdir(parents=True)

            # Create metadata file (required by dataset builder)
            metadata_dir = data_raw_dir / 'metadata'
            metadata_dir.mkdir(parents=True)
            metadata = {
                'BTCUSDT': {
                    'spread_frac': 0.00025,
                    'taker_fee_frac': 0.0004
                },
                'ETHUSDT': {
                    'spread_frac': 0.00025,
                    'taker_fee_frac': 0.0004
                }
            }
            with open(metadata_dir / 'binance_market_info.json', 'w') as f:
                json.dump(metadata, f, indent=2)

            # Create Vision monthly ZIP data (base history)
            # klines and funding_rates are direct subdirs of data/raw
            self._create_vision_monthly_data(data_raw_dir)

            # Expected and dataset dates
            expected_as_of_date = date(2026, 1, 27)  # Yesterday
            dataset_as_of_date = date(2026, 1, 26)   # Min (ETHUSDT lags)

            yield {
                'data_dir': data_raw_dir,  # Now points to data/raw
                'expected_date': expected_as_of_date,
                'dataset_date': dataset_as_of_date,
                'tmpdir': Path(tmpdir)
            }

    def _create_vision_monthly_data(self, data_raw_dir: Path):
        """
        Create minimal Vision monthly ZIPs for base history (Dec 2025).

        Structure: data/raw/{klines,funding_rates}/{SYMBOL}/*.zip
        """
        # BTCUSDT: Create Dec 2025 monthly ZIP (base history)
        btc_dir = data_raw_dir / 'klines' / 'BTCUSDT'
        btc_dir.mkdir(parents=True)

        # Generate Dec 2025 klines (Dec 1-31, 2025)
        klines_data = []
        for day in range(1, 32):  # Dec 1-31, 2025
            dt = datetime(2025, 12, day, 23, 59, 59)
            open_time = int((dt - timedelta(days=1)).timestamp() * 1000)
            close_time = int(dt.timestamp() * 1000)

            klines_data.append([
                open_time,
                '39000.0',  # open
                '39500.0',  # high
                '38500.0',  # low
                '39200.0',  # close
                '98.5',     # volume
                close_time,
                '3900000.0',  # quote_volume
                950, '48.0', '1950000.0', '0'
            ])

        # Write to CSV in ZIP
        csv_content = '\n'.join([','.join(map(str, row)) for row in klines_data])
        with zipfile.ZipFile(btc_dir / 'BTCUSDT-1d-2025-12.zip', 'w') as zf:
            zf.writestr('BTCUSDT-1d-2025-12.csv', csv_content)

        # ETHUSDT: Same Dec 2025 data
        eth_dir = data_raw_dir / 'klines' / 'ETHUSDT'
        eth_dir.mkdir(parents=True)

        klines_data_eth = []
        for day in range(1, 32):  # Dec 1-31, 2025
            dt = datetime(2025, 12, day, 23, 59, 59)
            open_time = int((dt - timedelta(days=1)).timestamp() * 1000)
            close_time = int(dt.timestamp() * 1000)

            klines_data_eth.append([
                open_time,
                '2450.0', '2500.0', '2400.0', '2475.0',
                '195.5', close_time, '485000.0',
                1950, '97.0', '242500.0', '0'
            ])

        csv_content_eth = '\n'.join([','.join(map(str, row)) for row in klines_data_eth])
        with zipfile.ZipFile(eth_dir / 'ETHUSDT-1d-2025-12.zip', 'w') as zf:
            zf.writestr('ETHUSDT-1d-2025-12.csv', csv_content_eth)

        # Create funding rates for both instruments
        # BTCUSDT funding
        btc_funding_dir = data_raw_dir / 'funding_rates' / 'BTCUSDT'
        btc_funding_dir.mkdir(parents=True)

        funding_data_btc = []
        for day in range(1, 32):  # Dec 1-31, 2025
            # 3 funding events per day (8-hourly)
            for hour in [0, 8, 16]:
                dt = datetime(2025, 12, day, hour, 0, 0)
                funding_time = int(dt.timestamp() * 1000)
                funding_data_btc.append([
                    funding_time,
                    '8',  # funding_interval_hours
                    '0.0001'  # last_funding_rate
                ])

        # Add CSV header
        funding_csv_btc = 'calc_time,funding_interval_hours,last_funding_rate\n'
        funding_csv_btc += '\n'.join([','.join(map(str, row)) for row in funding_data_btc])
        with zipfile.ZipFile(btc_funding_dir / 'BTCUSDT-fundingRate-2025-12.zip', 'w') as zf:
            zf.writestr('BTCUSDT-fundingRate-2025-12.csv', funding_csv_btc)

        # Create January 2026 funding for BTCUSDT (to match klines Jan 1-27)
        funding_data_btc_jan = []
        for day in range(1, 28):  # Jan 1-27, 2026
            for hour in [0, 8, 16]:
                dt = datetime(2026, 1, day, hour, 0, 0)
                funding_time = int(dt.timestamp() * 1000)
                funding_data_btc_jan.append([
                    funding_time,
                    '8',
                    '0.0001'
                ])
        funding_csv_btc_jan = 'calc_time,funding_interval_hours,last_funding_rate\n'
        funding_csv_btc_jan += '\n'.join([','.join(map(str, row)) for row in funding_data_btc_jan])
        with zipfile.ZipFile(btc_funding_dir / 'BTCUSDT-fundingRate-2026-01.zip', 'w') as zf:
            zf.writestr('BTCUSDT-fundingRate-2026-01.csv', funding_csv_btc_jan)

        # ETHUSDT funding
        eth_funding_dir = data_raw_dir / 'funding_rates' / 'ETHUSDT'
        eth_funding_dir.mkdir(parents=True)

        funding_data_eth = []
        for day in range(1, 32):  # Dec 1-31, 2025
            # 3 funding events per day (8-hourly)
            for hour in [0, 8, 16]:
                dt = datetime(2025, 12, day, hour, 0, 0)
                funding_time = int(dt.timestamp() * 1000)
                funding_data_eth.append([
                    funding_time,
                    '8',  # funding_interval_hours
                    '0.00015'  # last_funding_rate
                ])

        # Add CSV header
        funding_csv_eth = 'calc_time,funding_interval_hours,last_funding_rate\n'
        funding_csv_eth += '\n'.join([','.join(map(str, row)) for row in funding_data_eth])
        with zipfile.ZipFile(eth_funding_dir / 'ETHUSDT-fundingRate-2025-12.zip', 'w') as zf:
            zf.writestr('ETHUSDT-fundingRate-2025-12.csv', funding_csv_eth)

        # Create January 2026 funding for ETHUSDT (Jan 1-26 only, lags by 1 day)
        funding_data_eth_jan = []
        for day in range(1, 27):  # Jan 1-26 only
            for hour in [0, 8, 16]:
                dt = datetime(2026, 1, day, hour, 0, 0)
                funding_time = int(dt.timestamp() * 1000)
                funding_data_eth_jan.append([
                    funding_time,
                    '8',
                    '0.00015'
                ])
        funding_csv_eth_jan = 'calc_time,funding_interval_hours,last_funding_rate\n'
        funding_csv_eth_jan += '\n'.join([','.join(map(str, row)) for row in funding_data_eth_jan])
        with zipfile.ZipFile(eth_funding_dir / 'ETHUSDT-fundingRate-2026-01.zip', 'w') as zf:
            zf.writestr('ETHUSDT-fundingRate-2026-01.csv', funding_csv_eth_jan)

    def _create_mocked_api_responses(self, data_raw_dir: Path, expected_date: date):
        """
        Create mocked API cache for daily tail.

        Scenario:
        - Both instruments: API cache for Jan 1-26 (filled by previous daily updates)
        - BTCUSDT only: API cache for Jan 27 (up-to-date, staleness=0)
        - ETHUSDT: NO Jan 27 (lagging by 1 day, staleness=1)

        Structure: data/raw/api_cache/{SYMBOL}/*.parquet
        """
        api_cache_dir = data_raw_dir / 'api_cache'

        # BTCUSDT: Has data through expected_date (Jan 27, staleness=0)
        btc_cache_dir = api_cache_dir / 'BTCUSDT'
        btc_cache_dir.mkdir(parents=True)

        # Create API cache for Jan 1-27 (simulating successful daily updates)
        for day in range(1, 28):  # Jan 1-27
            dt = date(2026, 1, day)

            # Klines
            klines = pd.DataFrame({
                'date': [dt],
                'open': [40000.0 + day * 10],
                'high': [40500.0 + day * 10],
                'low': [39500.0 + day * 10],
                'close': [40200.0 + day * 10],
                'volume': [100.0 + day],
                'quote_volume': [4020000.0 + day * 1000]
            })
            klines.to_parquet(btc_cache_dir / f'{dt}_klines.parquet', index=False)

            # Funding (aggregated to daily)
            funding = pd.DataFrame({
                'date': [dt],
                'symbol': ['BTCUSDT'],
                'funding_rate': [0.0003]  # Daily aggregate of 3x 0.0001
            })
            funding.to_parquet(btc_cache_dir / f'{dt}_funding.parquet', index=False)

        # ETHUSDT: Has data through Jan 26 only (lagging, staleness=1)
        eth_cache_dir = api_cache_dir / 'ETHUSDT'
        eth_cache_dir.mkdir(parents=True)

        # Create API cache for Jan 1-26 only (NO Jan 27 - simulating failed/delayed update)
        for day in range(1, 27):  # Jan 1-26 only
            dt = date(2026, 1, day)

            # Klines
            klines = pd.DataFrame({
                'date': [dt],
                'open': [2500.0 + day * 2],
                'high': [2550.0 + day * 2],
                'low': [2450.0 + day * 2],
                'close': [2520.0 + day * 2],
                'volume': [200.0 + day],
                'quote_volume': [504000.0 + day * 1000]
            })
            klines.to_parquet(eth_cache_dir / f'{dt}_klines.parquet', index=False)

            # Funding (aggregated to daily)
            funding = pd.DataFrame({
                'date': [dt],
                'symbol': ['ETHUSDT'],
                'funding_rate': [0.00045]  # Daily aggregate of 3x 0.00015
            })
            funding.to_parquet(eth_cache_dir / f'{dt}_funding.parquet', index=False)

    def test_daily_pipeline_end_to_end(self, temp_env):
        """
        CRITICAL E2E TEST: Full daily pipeline with all invariants.

        Tests:
        1. Two-date concept: expected vs dataset as_of_date
        2. Staleness relative to expected (not dataset)
        3. Rectangular panel (no NaNs)
        4. Staleness overlay triggers
        5. Audit trail completeness
        """
        data_dir = temp_env['data_dir']
        expected_date = temp_env['expected_date']
        dataset_date = temp_env['dataset_date']

        # STEP 1: Simulate update_data_daily (mock API)
        self._create_mocked_api_responses(data_dir, expected_date)

        # STEP 2: Compute dates and staleness
        instruments = ['BTCUSDT', 'ETHUSDT']

        # compute_dates_and_staleness looks for api_cache at data_dir/api_cache
        expected, dataset, staleness_report = compute_dates_and_staleness(
            data_dir,
            instruments,
            expected_as_of_date=expected_date
        )

        # CRITICAL ASSERTION 1: Two-date concept
        assert expected == expected_date, (
            f"Expected as_of_date should be {expected_date}, got {expected}"
        )
        assert dataset == dataset_date, (
            f"Dataset as_of_date should be {dataset_date} (min), got {dataset}. "
            f"ETHUSDT lags by 1 day, so min should be 2026-01-26"
        )
        assert expected != dataset, (
            f"CRITICAL: expected_as_of_date ({expected}) must differ from "
            f"dataset_as_of_date ({dataset}) in lagging scenario!"
        )

        # CRITICAL ASSERTION 2: Staleness relative to EXPECTED (not dataset)
        assert staleness_report['BTCUSDT']['staleness_days'] == 0, (
            f"BTCUSDT should have staleness=0 (up-to-date), "
            f"got {staleness_report['BTCUSDT']['staleness_days']}"
        )
        assert staleness_report['ETHUSDT']['staleness_days'] == 1, (
            f"ETHUSDT should have staleness=1 (1 day behind expected), "
            f"got {staleness_report['ETHUSDT']['staleness_days']}. "
            f"Expected: {expected}, Last: {staleness_report['ETHUSDT']['last_available_date']}"
        )

        # STEP 3: Build dataset with hybrid sources
        # Map internal IDs to Binance symbols
        internal_instruments = ['BTCUSDT_PERP', 'ETHUSDT_PERP']

        try:
            df = build_real_crypto_dataset(
                data_dir=data_dir,  # data/raw
                start_date='2025-12-15',  # Include warmup period for ADV (30-day window)
                end_date=str(dataset_date),  # Use dataset_date (2026-01-26)
                instruments=internal_instruments,
                fail_on_missing_close=True,
                min_coverage=0.50,
                allow_jagged=False,  # Rectangular panel required
                include_api_cache=True  # V1 mode
            )
        except Exception as e:
            pytest.fail(f"Dataset build failed: {e}")
            return

        # CRITICAL ASSERTION 3: Rectangular panel (no NaNs)
        prices_pivot = df.pivot(index='date', columns='instrument', values='close')
        assert not prices_pivot.isna().any().any(), (
            f"RECTANGULAR PANEL VIOLATION: Found NaN in prices! "
            f"NaN counts per instrument:\n{prices_pivot.isna().sum()}\n"
            f"Dataset should have no NaNs when allow_jagged=False"
        )

        # Verify dataset ends at dataset_as_of_date (min)
        last_date = pd.to_datetime(df['date']).max().date()
        assert last_date == dataset_date, (
            f"Dataset should end at {dataset_date} (min across instruments), "
            f"got {last_date}"
        )

        # STEP 4: Test staleness overlay
        # Create targets and actual positions
        targets = pd.Series({
            'BTCUSDT_PERP': 1000.0,   # Want to open position
            'ETHUSDT_PERP': 1500.0    # Want to open position
        })

        actual_positions = pd.Series({
            'BTCUSDT_PERP': 0.0,   # No position
            'ETHUSDT_PERP': 0.0    # No position
        })

        staleness_days = pd.Series({
            'BTCUSDT_PERP': staleness_report['BTCUSDT']['staleness_days'],
            'ETHUSDT_PERP': staleness_report['ETHUSDT']['staleness_days']
        })

        # Apply overlay
        overridden_targets, audit = apply_staleness_overlay(
            targets,
            actual_positions,
            staleness_days,
            dataset_date
        )

        # CRITICAL ASSERTION 4: Overlay triggers correctly
        # BTCUSDT: staleness=0, no position → allow opening
        assert overridden_targets['BTCUSDT_PERP'] == 1000.0, (
            f"BTCUSDT_PERP should allow opening (staleness=0), "
            f"target should be 1000.0, got {overridden_targets['BTCUSDT_PERP']}"
        )
        assert 'BTCUSDT_PERP' not in audit, (
            "BTCUSDT_PERP should not have overlay applied (staleness=0)"
        )

        # ETHUSDT: staleness=1, no position → block opening
        assert overridden_targets['ETHUSDT_PERP'] == 0.0, (
            f"ETHUSDT_PERP should block opening (staleness=1, no position), "
            f"target should be 0.0, got {overridden_targets['ETHUSDT_PERP']}"
        )
        assert 'ETHUSDT_PERP' in audit, (
            "ETHUSDT_PERP should have overlay applied (staleness=1)"
        )
        assert audit['ETHUSDT_PERP']['reason'] == 'no_new_positions_on_stale_data', (
            f"ETHUSDT_PERP override reason should be 'no_new_positions_on_stale_data', "
            f"got {audit['ETHUSDT_PERP']['reason']}"
        )

        # Test cap rule (staleness=1 with existing position)
        targets_with_pos = pd.Series({
            'BTCUSDT_PERP': 2000.0,  # Want to add
            'ETHUSDT_PERP': 2000.0   # Want to add
        })
        actual_with_pos = pd.Series({
            'BTCUSDT_PERP': 1000.0,  # Existing position
            'ETHUSDT_PERP': 1000.0   # Existing position
        })

        overridden_cap, audit_cap = apply_staleness_overlay(
            targets_with_pos,
            actual_with_pos,
            staleness_days,
            dataset_date
        )

        # BTCUSDT: staleness=0 → allow add
        assert overridden_cap['BTCUSDT_PERP'] == 2000.0, (
            "BTCUSDT_PERP should allow adding (staleness=0)"
        )

        # ETHUSDT: staleness=1 → cap to current
        assert overridden_cap['ETHUSDT_PERP'] == 1000.0, (
            f"ETHUSDT_PERP should cap to current (staleness=1, no adds), "
            f"got {overridden_cap['ETHUSDT_PERP']}"
        )
        assert audit_cap['ETHUSDT_PERP']['reason'] == 'no_adds_on_day1_staleness', (
            "ETHUSDT_PERP cap reason should be 'no_adds_on_day1_staleness'"
        )

        # STEP 5: Verify data status report structure
        status_report = generate_data_status_report_v1(
            data_dir,
            instruments,
            expected_as_of_date=expected_date
        )

        # CRITICAL ASSERTION 5: Report contains all required V1 fields
        assert 'expected_as_of_date' in status_report, (
            "Status report missing 'expected_as_of_date'"
        )
        assert 'dataset_as_of_date' in status_report, (
            "Status report missing 'dataset_as_of_date'"
        )
        assert status_report['expected_as_of_date'] == str(expected_date)
        assert status_report['dataset_as_of_date'] == str(dataset_date)

        # Verify instrument staleness
        assert status_report['instruments']['BTCUSDT']['staleness_days'] == 0
        assert status_report['instruments']['ETHUSDT']['staleness_days'] == 1
        assert status_report['instruments']['BTCUSDT']['status'] == 'up_to_date'
        assert status_report['instruments']['ETHUSDT']['status'] == 'lagging'

        # Verify summary
        assert status_report['summary']['up_to_date'] == 1
        assert status_report['summary']['lagging'] == 1
        assert status_report['summary']['max_staleness_days'] == 1
        assert status_report['summary']['as_of_date_alignment'] == 'strict_fail', (
            "Alignment should fail when any instrument is lagging"
        )

        # SUCCESS: All critical invariants verified!
        print("\n✓ E2E SMOKE TEST PASSED")
        print(f"  - Two-date concept verified: expected={expected}, dataset={dataset}")
        print(f"  - Staleness computed relative to expected (not dataset)")
        print(f"  - Rectangular panel invariant: no NaNs")
        print(f"  - Overlay triggered: ETHUSDT blocked/capped, BTCUSDT allowed")
        print(f"  - Data status report complete with V1 fields")


    def test_staleness_data_roundtrip(self, temp_env):
        """
        Test that staleness data can be saved and loaded correctly.

        Verifies the data flow: data_status.json -> load_staleness_data -> overlay
        """
        data_dir = temp_env['data_dir']
        expected_date = temp_env['expected_date']
        output_dir = temp_env['tmpdir'] / 'output'
        output_dir.mkdir()

        # Create mocked API responses
        self._create_mocked_api_responses(data_dir, expected_date)

        # Generate and save data status report
        instruments = ['BTCUSDT', 'ETHUSDT']
        status_report = generate_data_status_report_v1(
            data_dir,
            instruments,
            expected_as_of_date=expected_date
        )

        status_path = output_dir / 'raw_data_status.json'
        with open(status_path, 'w') as f:
            json.dump(status_report, f, indent=2)

        # Load staleness data (as trade_plan would do)
        staleness_days, expected_loaded, dataset_loaded = load_staleness_data(status_path)

        # Verify loaded data
        assert staleness_days is not None, "Staleness data should be loaded"
        assert expected_loaded == expected_date, "Expected date should be loaded"
        assert dataset_loaded == temp_env['dataset_date'], "Dataset date should be loaded"

        # Verify staleness values
        assert staleness_days['BTCUSDT'] == 0, "BTCUSDT staleness should be 0"
        assert staleness_days['ETHUSDT'] == 1, "ETHUSDT staleness should be 1"

        print("\n✓ STALENESS DATA ROUNDTRIP VERIFIED")
        print(f"  - Status file saved and loaded successfully")
        print(f"  - Staleness values preserved: BTCUSDT=0, ETHUSDT=1")
        print(f"  - Dates preserved: expected={expected_loaded}, dataset={dataset_loaded}")
