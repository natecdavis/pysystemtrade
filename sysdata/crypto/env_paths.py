"""
Environment-aware path resolver for live ops.

This module provides environment separation (dev/prod/etc.) without requiring
infrastructure changes - just file-based isolation.

Resolution priority (highest to lowest):
1. Explicit CLI args (--data-dir, --output-dir) - ALWAYS wins
2. --env-root flag (custom path)
3. --env <name> flag (uses envs/<name>/ structure)
4. LIVE_OPS_ENV_ROOT env var
5. Default paths (backward compatible - current behavior)
"""

import os
from pathlib import Path
from typing import Optional
import logging

logger = logging.getLogger(__name__)


class LiveOpsEnvironment:
    """
    Environment-aware path resolver for live ops.

    Examples:
        # Default behavior (backward compatible)
        >>> env = LiveOpsEnvironment()
        >>> env.resolve('live')
        PosixPath('/project/live')

        # Dev environment
        >>> env = LiveOpsEnvironment(env='dev')
        >>> env.resolve('live')
        PosixPath('/project/envs/dev/live')

        # Prod environment
        >>> env = LiveOpsEnvironment(env='prod')
        >>> env.resolve_binance_raw_dir()
        PosixPath('/project/envs/prod/data/raw/binance')

        # Custom environment
        >>> env = LiveOpsEnvironment(env='paper')
        >>> env.resolve('out')
        PosixPath('/project/envs/paper/out')

        # Explicit override (highest priority)
        >>> env = LiveOpsEnvironment(env='prod')
        >>> env.resolve('live', override=Path('/custom/live'))
        PosixPath('/custom/live')
    """

    def __init__(
        self,
        env: Optional[str] = None,
        env_root: Optional[Path] = None,
        project_root: Optional[Path] = None
    ):
        """
        Initialize environment-aware path resolver.

        Args:
            env: Environment name (e.g., 'prod', 'dev', 'paper', 'exp1').
                 Uses envs/<env>/ directory structure.
            env_root: Custom environment root path (overrides env).
            project_root: Project root directory. Defaults to current working directory.
        """
        if project_root is None:
            project_root = Path.cwd()

        # Determine environment root based on resolution priority
        if env_root:
            # Priority 2: --env-root flag
            self.env_root = env_root
            logger.debug(f"Using env_root from --env-root: {self.env_root}")
        elif env:
            # Priority 3: --env flag
            self.env_root = project_root / "envs" / env
            logger.debug(f"Using env_root from --env {env}: {self.env_root}")
        elif os.getenv('LIVE_OPS_ENV_ROOT'):
            # Priority 4: LIVE_OPS_ENV_ROOT env var
            self.env_root = Path(os.getenv('LIVE_OPS_ENV_ROOT'))
            logger.debug(f"Using env_root from LIVE_OPS_ENV_ROOT: {self.env_root}")
        else:
            # Priority 5: Default (backward compatible)
            self.env_root = project_root
            logger.debug(f"Using default env_root (backward compatible): {self.env_root}")

        # Track whether we're in environment-aware mode
        self.is_env_aware = (
            env is not None or
            env_root is not None or
            os.getenv('LIVE_OPS_ENV_ROOT') is not None
        )

        self.env_name = env
        self.project_root = project_root

    def resolve(self, path_type: str, override: Optional[Path] = None) -> Path:
        """
        Resolve environment-aware path for standard directories.

        Args:
            path_type: Type of path to resolve. One of: 'live', 'out', 'config'
            override: Explicit path override (takes precedence over all)

        Returns:
            Absolute path

        Raises:
            ValueError: If path_type is not recognized
        """
        # Priority 1: Explicit override always wins
        if override:
            result = override.absolute()
            logger.debug(f"Resolved {path_type} to override: {result}")
            return result

        # Map path types to subdirectories
        defaults = {
            'live': self.env_root / 'live',
            'out': self.env_root / 'out',
            'config': self.env_root / 'config'
        }

        if path_type not in defaults:
            raise ValueError(
                f"Unknown path_type '{path_type}'. "
                f"Must be one of: {', '.join(defaults.keys())}"
            )

        result = defaults[path_type].absolute()
        logger.debug(f"Resolved {path_type} to: {result}")
        return result

    def resolve_data_root(self, override: Optional[Path] = None) -> Path:
        """
        Resolve data root directory (data/raw).

        This is the parent directory of binance/, metadata/, etc.
        Some scripts expect this level of the hierarchy.

        Args:
            override: Explicit path override (takes precedence)

        Returns:
            Absolute path to data/raw
        """
        if override:
            result = override.absolute()
            logger.debug(f"Resolved data_root to override: {result}")
            return result

        result = (self.env_root / 'data' / 'raw').absolute()
        logger.debug(f"Resolved data_root to: {result}")
        return result

    def resolve_binance_raw_dir(self, override: Optional[Path] = None) -> Path:
        """
        Resolve Binance raw data directory (data/raw/binance).

        This is the standard location for Binance klines and other raw data.
        Most scripts expect this level of the hierarchy.

        Args:
            override: Explicit path override (takes precedence)

        Returns:
            Absolute path to data/raw/binance
        """
        if override:
            result = override.absolute()
            logger.debug(f"Resolved binance_raw_dir to override: {result}")
            return result

        result = (self.env_root / 'data' / 'raw' / 'binance').absolute()
        logger.debug(f"Resolved binance_raw_dir to: {result}")
        return result

    def __repr__(self) -> str:
        """String representation for debugging."""
        return (
            f"LiveOpsEnvironment("
            f"env={self.env_name!r}, "
            f"env_root={self.env_root}, "
            f"is_env_aware={self.is_env_aware})"
        )
