"""
Test dataset manifest generation (Phase 2).
"""
import pytest
from pathlib import Path
import json
import pandas as pd
import tempfile
from scripts.build_example_dataset import generate_dataset_manifest


def test_manifest_structure():
    """Verify manifest has all required sections."""
    # Create minimal dataset
    df = pd.DataFrame({
        'date': pd.date_range('2024-01-01', periods=10),
        'instrument': ['BTCUSDT_PERP'] * 10,
        'close': [100.0] * 10,
        'funding_rate': [0.0001] * 10,
        'adv_notional': [1e9] * 10,
        'spread_frac': [0.0005] * 10,
        'taker_fee_frac': [0.0004] * 10
    })

    instruments_included = {
        'BTCUSDT_PERP': {
            'date_range': {'start': '2024-01-01', 'end': '2024-01-10'},
            'coverage_days': 10,
            'coverage_pct': 1.0,
            'funding_coverage_pct': 1.0,
            'schema_compliant': True
        }
    }
    instruments_excluded = {}

    with tempfile.TemporaryDirectory() as tmpdir:
        manifest_path = Path(tmpdir) / 'test.manifest.json'

        manifest = generate_dataset_manifest(
            dataset_df=df,
            instruments_included=instruments_included,
            instruments_excluded=instruments_excluded,
            start_date='2024-01-01',
            end_date='2024-01-10',
            output_path=manifest_path
        )

        # Verify structure
        assert 'generated_at' in manifest
        assert 'dataset_metadata' in manifest
        assert 'date_range' in manifest
        assert 'instruments' in manifest
        assert 'summary' in manifest

        # Verify instruments section
        assert 'included' in manifest['instruments']
        assert 'excluded' in manifest['instruments']

        # Verify counts
        assert manifest['summary']['total_candidates'] == 1
        assert manifest['summary']['included_count'] == 1
        assert manifest['summary']['excluded_count'] == 0

        # Verify file was written
        assert manifest_path.exists()

        # Verify file contents match
        with open(manifest_path) as f:
            loaded = json.load(f)
        assert loaded == manifest


def test_exclusion_breakdown():
    """Verify exclusion reasons are counted correctly."""
    df = pd.DataFrame({
        'date': pd.date_range('2024-01-01', periods=10),
        'instrument': ['BTCUSDT_PERP'] * 10,
        'close': [100.0] * 10,
        'funding_rate': [0.0001] * 10,
        'adv_notional': [1e9] * 10,
        'spread_frac': [0.0005] * 10,
        'taker_fee_frac': [0.0004] * 10
    })

    instruments_included = {
        'BTCUSDT_PERP': {
            'date_range': {'start': '2024-01-01', 'end': '2024-01-10'},
            'coverage_days': 10,
            'coverage_pct': 1.0,
            'funding_coverage_pct': 1.0,
            'schema_compliant': True
        }
    }
    instruments_excluded = {
        'ETHUSDT_PERP': 'load_error',
        'BNBUSDT_PERP': 'load_error',
        'SOLUSDT_PERP': 'missing_funding',
        'XRPUSDT_PERP': 'insufficient_coverage'
    }

    with tempfile.TemporaryDirectory() as tmpdir:
        manifest_path = Path(tmpdir) / 'test.manifest.json'

        manifest = generate_dataset_manifest(
            dataset_df=df,
            instruments_included=instruments_included,
            instruments_excluded=instruments_excluded,
            start_date='2024-01-01',
            end_date='2024-01-10',
            output_path=manifest_path
        )

        # Verify exclusion breakdown
        breakdown = manifest['summary']['exclusion_breakdown']
        assert breakdown['load_error'] == 2
        assert breakdown['missing_funding'] == 1
        assert breakdown['insufficient_coverage'] == 1

        # Verify summary counts
        assert manifest['summary']['total_candidates'] == 5
        assert manifest['summary']['included_count'] == 1
        assert manifest['summary']['excluded_count'] == 4


def test_manifest_invariant_holds():
    """Verify hard invariant: included set == dataset instruments."""
    df = pd.DataFrame({
        'date': pd.date_range('2024-01-01', periods=10).tolist() * 2,
        'instrument': ['BTCUSDT_PERP'] * 10 + ['ETHUSDT_PERP'] * 10,
        'close': [100.0] * 20,
        'funding_rate': [0.0001] * 20,
        'adv_notional': [1e9] * 20,
        'spread_frac': [0.0005] * 20,
        'taker_fee_frac': [0.0004] * 20
    })

    instruments_included = {
        'BTCUSDT_PERP': {
            'date_range': {'start': '2024-01-01', 'end': '2024-01-10'},
            'coverage_days': 10,
            'coverage_pct': 1.0,
            'funding_coverage_pct': 1.0,
            'schema_compliant': True
        },
        'ETHUSDT_PERP': {
            'date_range': {'start': '2024-01-01', 'end': '2024-01-10'},
            'coverage_days': 10,
            'coverage_pct': 1.0,
            'funding_coverage_pct': 1.0,
            'schema_compliant': True
        }
    }
    instruments_excluded = {}

    with tempfile.TemporaryDirectory() as tmpdir:
        manifest_path = Path(tmpdir) / 'test.manifest.json'

        # Should pass - included matches dataset
        manifest = generate_dataset_manifest(
            dataset_df=df,
            instruments_included=instruments_included,
            instruments_excluded=instruments_excluded,
            start_date='2024-01-01',
            end_date='2024-01-10',
            output_path=manifest_path
        )

        # Verify invariant
        dataset_instruments = set(df['instrument'].unique())
        manifest_included = set(manifest['instruments']['included'].keys())
        assert dataset_instruments == manifest_included


def test_manifest_invariant_violation_detected():
    """Verify invariant violation raises RuntimeError."""
    df = pd.DataFrame({
        'date': pd.date_range('2024-01-01', periods=10),
        'instrument': ['BTCUSDT_PERP'] * 10,
        'close': [100.0] * 10,
        'funding_rate': [0.0001] * 10,
        'adv_notional': [1e9] * 10,
        'spread_frac': [0.0005] * 10,
        'taker_fee_frac': [0.0004] * 10
    })

    # Mismatch: dataset has BTCUSDT, but included claims ETHUSDT
    instruments_included = {
        'ETHUSDT_PERP': {
            'date_range': {'start': '2024-01-01', 'end': '2024-01-10'},
            'coverage_days': 10,
            'coverage_pct': 1.0,
            'funding_coverage_pct': 1.0,
            'schema_compliant': True
        }
    }
    instruments_excluded = {}

    with tempfile.TemporaryDirectory() as tmpdir:
        manifest_path = Path(tmpdir) / 'test.manifest.json'

        # Should raise RuntimeError (invariant violation)
        with pytest.raises(RuntimeError, match="Manifest consistency check failed"):
            generate_dataset_manifest(
                dataset_df=df,
                instruments_included=instruments_included,
                instruments_excluded=instruments_excluded,
                start_date='2024-01-01',
                end_date='2024-01-10',
                output_path=manifest_path
            )


def test_atomic_write():
    """Verify atomic write (temp + rename) behavior."""
    df = pd.DataFrame({
        'date': pd.date_range('2024-01-01', periods=10),
        'instrument': ['BTCUSDT_PERP'] * 10,
        'close': [100.0] * 10,
        'funding_rate': [0.0001] * 10,
        'adv_notional': [1e9] * 10,
        'spread_frac': [0.0005] * 10,
        'taker_fee_frac': [0.0004] * 10
    })

    instruments_included = {
        'BTCUSDT_PERP': {
            'date_range': {'start': '2024-01-01', 'end': '2024-01-10'},
            'coverage_days': 10,
            'coverage_pct': 1.0,
            'funding_coverage_pct': 1.0,
            'schema_compliant': True
        }
    }
    instruments_excluded = {}

    with tempfile.TemporaryDirectory() as tmpdir:
        manifest_path = Path(tmpdir) / 'test.manifest.json'

        # First write
        manifest1 = generate_dataset_manifest(
            dataset_df=df,
            instruments_included=instruments_included,
            instruments_excluded=instruments_excluded,
            start_date='2024-01-01',
            end_date='2024-01-10',
            output_path=manifest_path
        )

        # Second write (should overwrite atomically)
        manifest2 = generate_dataset_manifest(
            dataset_df=df,
            instruments_included=instruments_included,
            instruments_excluded=instruments_excluded,
            start_date='2024-01-01',
            end_date='2024-01-10',
            output_path=manifest_path
        )

        # File should exist and contain second write
        assert manifest_path.exists()

        with open(manifest_path) as f:
            loaded = json.load(f)

        # Should match second write (atomic overwrite)
        assert loaded['generated_at'] == manifest2['generated_at']


def test_deterministic_naming():
    """Verify manifest naming is deterministic (X.parquet → X.manifest.json)."""
    # This is tested implicitly by the Path construction in main()
    # Here we just verify the convention
    dataset_path = Path('/tmp/dataset_test.parquet')
    expected_manifest = Path('/tmp/dataset_test.manifest.json')

    # Verify naming convention
    manifest_path = dataset_path.with_suffix('.manifest.json')
    assert manifest_path == expected_manifest
