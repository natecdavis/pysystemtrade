"""
Unit tests for environment path resolver.

Tests all resolution paths and priority rules for LiveOpsEnvironment.
"""

import os
import pytest
from pathlib import Path
from sysdata.crypto.env_paths import LiveOpsEnvironment


class TestLiveOpsEnvironment:
    """Unit tests for LiveOpsEnvironment path resolver."""

    def test_default_behavior(self, tmp_path):
        """No --env flag = current behavior (backward compatible)."""
        env = LiveOpsEnvironment(project_root=tmp_path)

        assert env.resolve('live') == tmp_path / 'live'
        assert env.resolve_binance_raw_dir() == tmp_path / 'data' / 'raw' / 'binance'
        assert env.resolve_data_root() == tmp_path / 'data' / 'raw'
        assert not env.is_env_aware

    def test_env_flag_prod(self, tmp_path):
        """--env prod uses envs/prod/ structure."""
        env = LiveOpsEnvironment(env='prod', project_root=tmp_path)

        assert env.resolve_binance_raw_dir() == tmp_path / 'envs' / 'prod' / 'data' / 'raw' / 'binance'
        assert env.resolve_data_root() == tmp_path / 'envs' / 'prod' / 'data' / 'raw'
        assert env.resolve('live') == tmp_path / 'envs' / 'prod' / 'live'
        assert env.resolve('out') == tmp_path / 'envs' / 'prod' / 'out'
        assert env.resolve('config') == tmp_path / 'envs' / 'prod' / 'config'
        assert env.is_env_aware

    def test_env_flag_dev(self, tmp_path):
        """--env dev uses envs/dev/ structure."""
        env = LiveOpsEnvironment(env='dev', project_root=tmp_path)

        assert env.resolve('live') == tmp_path / 'envs' / 'dev' / 'live'
        assert env.resolve_binance_raw_dir() == tmp_path / 'envs' / 'dev' / 'data' / 'raw' / 'binance'
        assert env.resolve_data_root() == tmp_path / 'envs' / 'dev' / 'data' / 'raw'
        assert env.is_env_aware

    def test_env_flag_arbitrary(self, tmp_path):
        """--env accepts arbitrary environment names."""
        env1 = LiveOpsEnvironment(env='paper', project_root=tmp_path)
        assert env1.resolve('live') == tmp_path / 'envs' / 'paper' / 'live'
        assert env1.is_env_aware

        env2 = LiveOpsEnvironment(env='exp1', project_root=tmp_path)
        assert env2.resolve_binance_raw_dir() == tmp_path / 'envs' / 'exp1' / 'data' / 'raw' / 'binance'
        assert env2.is_env_aware

        env3 = LiveOpsEnvironment(env='test123', project_root=tmp_path)
        assert env3.resolve('out') == tmp_path / 'envs' / 'test123' / 'out'
        assert env3.is_env_aware

    def test_override_priority(self, tmp_path):
        """Explicit path overrides --env (highest priority)."""
        env = LiveOpsEnvironment(env='prod', project_root=tmp_path)

        override_binance = Path('/custom/binance/path')
        override_data_root = Path('/custom/data/root')
        override_live = Path('/custom/live')

        assert env.resolve_binance_raw_dir(override=override_binance) == override_binance.absolute()
        assert env.resolve_data_root(override=override_data_root) == override_data_root.absolute()
        assert env.resolve('live', override=override_live) == override_live.absolute()

    def test_env_root_flag(self, tmp_path):
        """--env-root flag takes precedence over --env."""
        custom_root = tmp_path / 'custom_env'
        env = LiveOpsEnvironment(env='prod', env_root=custom_root, project_root=tmp_path)

        assert env.resolve('live') == custom_root / 'live'
        assert env.resolve_binance_raw_dir() == custom_root / 'data' / 'raw' / 'binance'
        assert env.is_env_aware

    def test_env_var(self, tmp_path):
        """LIVE_OPS_ENV_ROOT env var works."""
        custom_root = tmp_path / 'env_var_root'
        os.environ['LIVE_OPS_ENV_ROOT'] = str(custom_root)

        try:
            env = LiveOpsEnvironment(project_root=tmp_path)
            assert env.resolve('live') == custom_root / 'live'
            assert env.resolve_binance_raw_dir() == custom_root / 'data' / 'raw' / 'binance'
            assert env.is_env_aware
        finally:
            del os.environ['LIVE_OPS_ENV_ROOT']

    def test_env_root_priority_over_env_var(self, tmp_path):
        """--env-root takes priority over LIVE_OPS_ENV_ROOT env var."""
        env_var_root = tmp_path / 'env_var_root'
        env_root_flag = tmp_path / 'env_root_flag'

        os.environ['LIVE_OPS_ENV_ROOT'] = str(env_var_root)

        try:
            env = LiveOpsEnvironment(env_root=env_root_flag, project_root=tmp_path)
            assert env.resolve('live') == env_root_flag / 'live'
        finally:
            del os.environ['LIVE_OPS_ENV_ROOT']

    def test_data_path_distinction(self, tmp_path):
        """Test distinction between data_root and binance_raw_dir."""
        env = LiveOpsEnvironment(env='prod', project_root=tmp_path)

        # data_root = data/raw (parent of binance/)
        assert env.resolve_data_root() == tmp_path / 'envs' / 'prod' / 'data' / 'raw'

        # binance_raw_dir = data/raw/binance
        assert env.resolve_binance_raw_dir() == tmp_path / 'envs' / 'prod' / 'data' / 'raw' / 'binance'

    def test_absolute_paths(self, tmp_path):
        """All resolved paths should be absolute."""
        env = LiveOpsEnvironment(env='dev', project_root=tmp_path)

        assert env.resolve('live').is_absolute()
        assert env.resolve('out').is_absolute()
        assert env.resolve('config').is_absolute()
        assert env.resolve_binance_raw_dir().is_absolute()
        assert env.resolve_data_root().is_absolute()

    def test_invalid_path_type(self, tmp_path):
        """Unknown path_type should raise ValueError."""
        env = LiveOpsEnvironment(env='dev', project_root=tmp_path)

        with pytest.raises(ValueError, match="Unknown path_type"):
            env.resolve('invalid_type')

    def test_repr(self, tmp_path):
        """Test string representation."""
        env = LiveOpsEnvironment(env='prod', project_root=tmp_path)
        repr_str = repr(env)

        assert 'LiveOpsEnvironment' in repr_str
        assert 'prod' in repr_str
        assert 'is_env_aware=True' in repr_str
