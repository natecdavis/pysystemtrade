"""
Integration tests for research quality tooling

These tests are marked as @pytest.mark.integration and @pytest.mark.slow.
They are skipped by default. Run with: pytest -m integration
"""

import pytest
import pandas as pd
import tempfile
from pathlib import Path


@pytest.mark.integration
@pytest.mark.slow
def test_full_backtest_with_diagnostics():
    """
    Full end-to-end test with diagnostics enabled

    Tests:
    - Backtest runs successfully with diagnostics enabled
    - diagnostics.parquet is written with correct structure
    - All required fields are present and non-null
    - No duplicate (date, instrument) rows
    - PnL accounting identity holds
    """
    from systems.crypto_perps.system import run_backtest, load_config

    # Load config
    config_path = Path(__file__).parent.parent / 'config' / 'crypto_perps.yaml'
    config = load_config(str(config_path))

    # Enable diagnostics
    config['diagnostics'] = {'enabled': True}

    # Run backtest on short date range for speed
    data_path = Path(__file__).parent.parent / 'data' / 'example_crypto_perps.parquet'

    with tempfile.TemporaryDirectory() as tmpdir:
        output_dir = Path(tmpdir)

        # Run backtest
        result = run_backtest(config, str(data_path), str(output_dir))

        # Verify result dict
        assert isinstance(result, dict)
        assert 'equity_curve' in result
        assert 'weights_df' in result
        assert 'trades_df' in result

        # Verify diagnostics file exists
        diagnostics_file = output_dir / 'diagnostics.parquet'
        assert diagnostics_file.exists(), "Diagnostics file must exist"

        # Read and verify diagnostics
        df = pd.read_parquet(diagnostics_file)

        # Check required fields exist
        required_fields = ['date', 'instrument', 'state', 'forecast_combined',
                          'target_weight_constrained', 'pnl_total']
        for field in required_fields:
            assert field in df.columns, f"Missing required field: {field}"

        # Check no duplicate rows
        duplicates = df.duplicated(subset=['date', 'instrument'])
        assert not duplicates.any(), "No duplicate (date, instrument) rows allowed"

        # Check required fields non-null
        assert not df['date'].isna().any()
        assert not df['instrument'].isna().any()
        assert not df['state'].isna().any()

        # Verify PnL accounting identity
        pnl_check = df['pnl_price'] + df['pnl_funding'] - df['pnl_costs']
        pnl_diff = (df['pnl_total'] - pnl_check).abs()
        assert (pnl_diff < 1e-6).all(), "PnL accounting identity must hold"

        print(f"\n✓ Full backtest with diagnostics passed")
        print(f"  Rows: {len(df)}")
        print(f"  Columns: {len(df.columns)}")
        print(f"  Date range: {df['date'].min()} to {df['date'].max()}")
        print(f"  Instruments: {sorted(df['instrument'].unique())}")


@pytest.mark.integration
@pytest.mark.slow
def test_ablation_runner_integration():
    """
    Test ablation_runner.py end-to-end on short date range

    Tests:
    - Ablation runner runs 4 experiments without errors
    - ablation_results.csv is written with 4 rows
    - Baseline has 0 exit activity (Phase 1)
    - Each experiment produces diagnostics.parquet and config.yaml
    """
    import sys
    from pathlib import Path

    # Add project root to path
    sys.path.insert(0, str(Path(__file__).parent.parent))

    from scripts.ablation_runner import run_ablation_study

    # Paths
    base_config = Path(__file__).parent.parent / 'config' / 'crypto_perps.yaml'
    data_path = Path(__file__).parent.parent / 'data' / 'example_crypto_perps.parquet'

    with tempfile.TemporaryDirectory() as tmpdir:
        outdir = Path(tmpdir)

        # Run ablation study on short date range (3 months for speed)
        print("\nRunning ablation study (this may take a minute)...")
        run_ablation_study(
            base_config_path=str(base_config),
            data_path=str(data_path),
            outdir=str(outdir),
            start_date='2023-01-01',
            end_date='2023-03-31',
            tag='integration_test'
        )

        # Verify ablation_results.csv exists and has 4 rows
        results_file = outdir / 'ablation_results.csv'
        assert results_file.exists(), "ablation_results.csv must exist"

        results_df = pd.read_parquet(results_file)
        assert len(results_df) == 4, f"Expected 4 experiments, got {len(results_df)}"

        # Verify experiment names
        expected_experiments = {'baseline', 'reviews', 'state_machine', 'relmom'}
        actual_experiments = set(results_df['experiment'])
        assert actual_experiments == expected_experiments, \
            f"Expected experiments {expected_experiments}, got {actual_experiments}"

        # Verify baseline has 0 exit activity (Phase 1)
        baseline = results_df[results_df['experiment'] == 'baseline'].iloc[0]
        assert baseline['exit_flattens'] == 0, "Baseline (Phase 1) should have 0 exit flattens"
        assert baseline['exit_decays'] == 0, "Baseline (Phase 1) should have 0 exit decays"

        # Verify each experiment has diagnostics and config
        for exp_name in expected_experiments:
            exp_dir = outdir / exp_name
            assert exp_dir.exists(), f"Experiment directory {exp_name} must exist"

            diagnostics_file = exp_dir / 'diagnostics.parquet'
            assert diagnostics_file.exists(), \
                f"Diagnostics file for {exp_name} must exist"

            config_file = exp_dir / 'config.yaml'
            assert config_file.exists(), \
                f"Config snapshot for {exp_name} must exist"

        print(f"\n✓ Ablation runner integration test passed")
        print(f"  Experiments: {len(results_df)}")
        print(f"  Results columns: {list(results_df.columns)}")
        print(f"\nSummary:")
        print(results_df[['experiment', 'sharpe', 'ann_return', 'max_drawdown']].to_string(index=False))
