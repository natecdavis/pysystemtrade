"""
Atomic file writes and process locks for live state.

Live state files (current_equity.txt, equity_history.csv, circuit_breaker_state.json)
are read by every cron run. A naive write that crashes mid-stream leaves a corrupt or
empty file and the next run either errors or computes wrong drawdowns. The atomic
helpers here write to a sibling temp file, fsync, then rename — POSIX rename is atomic
so the destination is always either entirely the old content or entirely the new.

The DailyRunLock context manager uses fcntl flock for actual mutual exclusion
(automatically released when the process dies, so no stale-PID cleanup needed) and
records owner PID + timestamp in the lock file for operator visibility.
"""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import pandas as pd


def _atomic_replace(tmp_path: Path, dest_path: Path) -> None:
    """fsync the temp file and atomically rename into place."""
    fd = os.open(str(tmp_path), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(str(tmp_path), str(dest_path))


def atomic_write_text(path: Path, content: str, encoding: str = "utf-8") -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding=encoding) as f:
            f.write(content)
        _atomic_replace(Path(tmp), path)
    except Exception:
        Path(tmp).unlink(missing_ok=True)
        raise


def atomic_write_json(path: Path, data: Any, indent: int = 2) -> None:
    atomic_write_text(path, json.dumps(data, indent=indent, default=str) + "\n")


def atomic_write_csv(df: pd.DataFrame, path: Path, **to_csv_kwargs: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    os.close(fd)
    try:
        df.to_csv(tmp, **to_csv_kwargs)
        _atomic_replace(Path(tmp), path)
    except Exception:
        Path(tmp).unlink(missing_ok=True)
        raise


class LockBusy(RuntimeError):
    """Raised when a DailyRunLock cannot acquire because another process holds it."""


@contextmanager
def daily_run_lock(lock_path: Path) -> Iterator[None]:
    """
    fcntl-based exclusive lock for live cron runs.

    The lock is automatically released when the process exits (POSIX semantics),
    so a crashed run cannot leave a permanent stale lock. The lock file itself
    records the owner PID + acquisition timestamp for operator visibility (e.g.,
    `cat live/.daily_run.lock` shows who's running).

    Raises LockBusy if another process already holds the lock.
    """
    lock_path = Path(lock_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    f = open(lock_path, "a+")
    try:
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError):
            f.seek(0)
            existing = f.read().strip() or "<unknown>"
            f.close()
            raise LockBusy(
                f"Another process holds {lock_path} ({existing}). "
                f"Wait for it to finish or kill it before retrying."
            )
        # We hold the lock — record ownership for visibility.
        f.seek(0)
        f.truncate()
        f.write(f"pid={os.getpid()} acquired_utc={datetime.now(timezone.utc).isoformat()}\n")
        f.flush()
        try:
            yield
        finally:
            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            finally:
                f.close()
                # Best-effort: remove the lock file. Concurrent runs that beat us to
                # the unlink are fine (their lock attempt would have already failed
                # while we held it, then they'll re-create on their own acquire).
                try:
                    lock_path.unlink()
                except FileNotFoundError:
                    pass
    except Exception:
        try:
            f.close()
        except Exception:
            pass
        raise
