"""
Integration tests for environment isolation.

Tests that prod and dev environments are properly isolated and backward compatibility is maintained.
"""

import pytest
from pathlib import Path
from sysdata.crypto.env_paths import LiveOpsEnvironment


class TestEnvironmentIsolation:
    """Integration tests for environment isolation."""

    def test_prod_dev_isolation(self, tmp_path):
        """Verify prod and dev never write to each other's directories."""
        # Setup both environments
        for env_name in ['prod', 'dev']:
            env_dir = tmp_path / 'envs' / env_name
            (env_dir / 'live').mkdir(parents=True)
            (env_dir / 'data/raw/binance').mkdir(parents=True)
            (env_dir / 'out').mkdir(parents=True)

        # Write different data to each
        (tmp_path / 'envs/prod/live/test.txt').write_text('prod data')
        (tmp_path / 'envs/dev/live/test.txt').write_text('dev data')

        # Verify isolation
        prod_env = LiveOpsEnvironment(env='prod', project_root=tmp_path)
        dev_env = LiveOpsEnvironment(env='dev', project_root=tmp_path)

        assert prod_env.resolve('live') != dev_env.resolve('live')
        assert (prod_env.resolve('live') / 'test.txt').read_text() == 'prod data'
        assert (dev_env.resolve('live') / 'test.txt').read_text() == 'dev data'

    def test_backward_compatibility(self, tmp_path):
        """No --env flag works exactly as before."""
        # Create traditional directory structure
        (tmp_path / 'live').mkdir()
        (tmp_path / 'data/raw/binance').mkdir(parents=True)
        (tmp_path / 'out').mkdir()

        env = LiveOpsEnvironment(project_root=tmp_path)

        # Should resolve to project_root directly (not envs/)
        assert env.resolve('live') == tmp_path / 'live'
        assert env.resolve_binance_raw_dir() == tmp_path / 'data' / 'raw' / 'binance'
        assert env.resolve_data_root() == tmp_path / 'data' / 'raw'
        assert env.resolve('out') == tmp_path / 'out'
        assert not env.is_env_aware

    def test_multiple_custom_environments(self, tmp_path):
        """Multiple custom environments are isolated."""
        envs = ['paper', 'exp1', 'exp2']

        # Create directory structure for each
        for env_name in envs:
            env_dir = tmp_path / 'envs' / env_name
            (env_dir / 'live').mkdir(parents=True)
            (env_dir / 'live/test.txt').write_text(f'{env_name} data')

        # Verify isolation
        for env_name in envs:
            env = LiveOpsEnvironment(env=env_name, project_root=tmp_path)
            test_file = env.resolve('live') / 'test.txt'
            assert test_file.read_text() == f'{env_name} data'

    def test_override_in_environment(self, tmp_path):
        """Explicit override works within environment context."""
        # Setup dev environment
        dev_dir = tmp_path / 'envs' / 'dev'
        (dev_dir / 'live').mkdir(parents=True)

        # Setup custom override location
        custom_dir = tmp_path / 'custom_data'
        custom_dir.mkdir()
        (custom_dir / 'test.txt').write_text('custom data')

        env = LiveOpsEnvironment(env='dev', project_root=tmp_path)

        # Default: uses dev environment
        assert env.resolve_binance_raw_dir() == dev_dir / 'data' / 'raw' / 'binance'

        # Override: uses custom path
        override_path = env.resolve_binance_raw_dir(override=custom_dir)
        assert override_path == custom_dir.absolute()
        assert (override_path / 'test.txt').read_text() == 'custom data'

    def test_data_path_distinction_across_environments(self, tmp_path):
        """Test data_root vs binance_raw_dir distinction across environments."""
        for env_name in ['prod', 'dev']:
            env = LiveOpsEnvironment(env=env_name, project_root=tmp_path)

            data_root = env.resolve_data_root()
            binance_raw_dir = env.resolve_binance_raw_dir()

            # binance_raw_dir should be child of data_root
            assert binance_raw_dir.parent == data_root
            assert binance_raw_dir.name == 'binance'
            assert data_root.name == 'raw'

    def test_env_aware_flag_consistency(self, tmp_path):
        """is_env_aware flag is consistent with resolution behavior."""
        # Not env-aware (default)
        env_default = LiveOpsEnvironment(project_root=tmp_path)
        assert not env_default.is_env_aware
        assert env_default.resolve('live') == tmp_path / 'live'

        # Env-aware (--env flag)
        env_prod = LiveOpsEnvironment(env='prod', project_root=tmp_path)
        assert env_prod.is_env_aware
        assert env_prod.resolve('live') == tmp_path / 'envs' / 'prod' / 'live'

        # Env-aware (--env-root flag)
        env_custom = LiveOpsEnvironment(env_root=tmp_path / 'custom', project_root=tmp_path)
        assert env_custom.is_env_aware
        assert env_custom.resolve('live') == tmp_path / 'custom' / 'live'

    def test_shared_data_different_state(self, tmp_path):
        """Test scenario where data is shared but state is isolated."""
        # Setup shared data directory
        shared_data = tmp_path / 'shared_data'
        (shared_data / 'binance').mkdir(parents=True)
        (shared_data / 'binance/test.csv').write_text('shared market data')

        # Setup isolated state directories
        for env_name in ['prod', 'dev']:
            env_dir = tmp_path / 'envs' / env_name
            (env_dir / 'live').mkdir(parents=True)
            (env_dir / 'live/positions.csv').write_text(f'{env_name} positions')

        # Prod: use shared data, isolated state
        prod_env = LiveOpsEnvironment(env='prod', project_root=tmp_path)
        prod_data = prod_env.resolve_binance_raw_dir(override=shared_data / 'binance')
        prod_live = prod_env.resolve('live')

        assert prod_data == (shared_data / 'binance').absolute()
        assert (prod_data / 'test.csv').read_text() == 'shared market data'
        assert (prod_live / 'positions.csv').read_text() == 'prod positions'

        # Dev: use shared data, isolated state
        dev_env = LiveOpsEnvironment(env='dev', project_root=tmp_path)
        dev_data = dev_env.resolve_binance_raw_dir(override=shared_data / 'binance')
        dev_live = dev_env.resolve('live')

        assert dev_data == (shared_data / 'binance').absolute()
        assert (dev_data / 'test.csv').read_text() == 'shared market data'
        assert (dev_live / 'positions.csv').read_text() == 'dev positions'

        # Verify state isolation
        assert prod_live != dev_live

    def test_config_resolution(self, tmp_path):
        """Test config path resolution across environments."""
        for env_name in ['prod', 'dev']:
            env = LiveOpsEnvironment(env=env_name, project_root=tmp_path)
            config_path = env.resolve('config')
            assert config_path == tmp_path / 'envs' / env_name / 'config'

        # Default (backward compatible)
        env_default = LiveOpsEnvironment(project_root=tmp_path)
        assert env_default.resolve('config') == tmp_path / 'config'
