"""
Run metadata logging for crypto perpetual futures trading system

Tracks provenance: git commit, dataset fingerprint, config, metrics.
Enables experiment reproducibility and result tracking.
"""

import json
import hashlib
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, Any


def get_git_commit() -> str:
    """
    Get current git commit hash

    Returns:
        40-character hex commit hash, or 'unknown' if not a git repo
    """
    try:
        result = subprocess.run(
            ['git', 'rev-parse', 'HEAD'],
            capture_output=True,
            text=True,
            check=True,
            timeout=5
        )
        return result.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return 'unknown'


def get_git_status() -> str:
    """
    Get git working tree status

    Returns:
        'clean' if no uncommitted changes, 'dirty' if changes present, 'unknown' if not a git repo
    """
    try:
        result = subprocess.run(
            ['git', 'status', '--porcelain'],
            capture_output=True,
            text=True,
            check=True,
            timeout=5
        )
        status = result.stdout.strip()
        return 'clean' if not status else 'dirty'
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return 'unknown'


def calculate_dataset_fingerprint(data_path: Path) -> str:
    """
    Calculate MD5 hash of dataset file

    Args:
        data_path: Path to dataset file

    Returns:
        MD5 hash as hex string
    """
    md5 = hashlib.md5()
    with open(data_path, 'rb') as f:
        # Read in chunks to handle large files
        for chunk in iter(lambda: f.read(8192), b''):
            md5.update(chunk)
    return md5.hexdigest()


def write_run_metadata(
    outdir: Path,
    config: Dict[str, Any],
    data_path: Path,
    metrics: Dict[str, float]
):
    """
    Write run metadata JSON for experiment tracking

    Args:
        outdir: Output directory
        config: Full config dict
        data_path: Path to input dataset
        metrics: Metrics dict from calculate_metrics()

    Output:
        metadata.json with:
        - Timestamp (UTC ISO format)
        - Python version
        - Git commit hash and status
        - Dataset path and MD5 fingerprint
        - Config snapshot
        - Headline metrics (sharpe, return, vol, drawdown, exposure)
    """
    metadata = {
        'timestamp': datetime.utcnow().isoformat() + 'Z',
        'python_version': f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        'git_commit': get_git_commit(),
        'git_status': get_git_status(),
        'dataset_path': str(data_path.resolve()),
        'dataset_fingerprint': calculate_dataset_fingerprint(data_path),
        'config_snapshot': config,
        'headline_metrics': {
            'sharpe': metrics.get('sharpe'),
            'ann_return': metrics.get('ann_return'),
            'ann_vol': metrics.get('ann_vol'),
            'max_drawdown': metrics.get('max_drawdown'),
            'gross_exposure': metrics.get('gross_exposure'),
            'turnover': metrics.get('turnover')
        }
    }

    # Write to JSON
    outdir.mkdir(parents=True, exist_ok=True)
    with open(outdir / 'metadata.json', 'w') as f:
        json.dump(metadata, f, indent=2, default=str)
