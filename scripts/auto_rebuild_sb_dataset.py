#!/usr/bin/env python3
"""
Idempotent wrapper around build_sb_corrected_dataset.py.

The SB-corrected dataset (`dataset_sb_corrected_6yr_jagged.parquet`) is the
research dataset that includes 150 graveyard instruments alongside the 319 active
ones. Without this script, it's rebuilt manually — research backtests can drift days
behind production whenever fresh klines or graveyard data lands.

This wrapper:
  1. Reads a sibling manifest sidecar listing the SHA256 of the base dataset and
     a fingerprint (file count + max mtime) of the graveyard directory.
  2. Skips the rebuild if both signatures still match — cheap O(1) read.
  3. Otherwise invokes build_sb_corrected_dataset.py and updates the manifest with
     the new fingerprints, build timestamp, row count, and the reason it rebuilt.

Wired into the 22:00 UTC pre-stage cron via prestage_daily.py.

Usage:
    python scripts/auto_rebuild_sb_dataset.py
    python scripts/auto_rebuild_sb_dataset.py --force
    python scripts/auto_rebuild_sb_dataset.py --check-only   # exits 2 if rebuild needed
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

from sysdata.crypto.atomic_io import atomic_write_json
from sysdata.crypto.manifest_chain import file_sha256

DEFAULT_BASE = REPO_ROOT / "data/dataset_538registry_6yr_jagged.parquet"
DEFAULT_GRAVEYARD = REPO_ROOT / "data/raw/graveyard"
DEFAULT_OUTPUT = REPO_ROOT / "data/dataset_sb_corrected_6yr_jagged.parquet"


def graveyard_fingerprint(graveyard_dir: Path) -> dict[str, int | float | str]:
    """
    Lightweight signature of the graveyard directory: file count + max mtime + total bytes.
    Avoids hashing every file (the dir can be many GB) while still detecting
    additions, deletions, and overwrites that would warrant a rebuild.
    """
    if not graveyard_dir.exists():
        return {"exists": False, "file_count": 0, "max_mtime": 0.0, "total_bytes": 0}
    file_count = 0
    max_mtime = 0.0
    total_bytes = 0
    for p in graveyard_dir.rglob("*"):
        if not p.is_file():
            continue
        file_count += 1
        try:
            stat = p.stat()
        except FileNotFoundError:
            continue
        if stat.st_mtime > max_mtime:
            max_mtime = stat.st_mtime
        total_bytes += stat.st_size
    return {
        "exists": True,
        "file_count": file_count,
        "max_mtime": max_mtime,
        "total_bytes": total_bytes,
    }


def manifest_path_for(output: Path) -> Path:
    return output.with_suffix(output.suffix + ".manifest.json")


def needs_rebuild(
    base: Path,
    graveyard: Path,
    output: Path,
    manifest: Path,
    force: bool,
) -> tuple[bool, str | None]:
    if force:
        return True, "forced"
    if not output.exists():
        return True, "output missing"
    if not manifest.exists():
        return True, "manifest missing"
    try:
        recorded = json.loads(manifest.read_text())
    except json.JSONDecodeError:
        return True, "manifest unreadable"

    if recorded.get("base_dataset_sha256") != file_sha256(base):
        return True, "base dataset changed"

    current_grave = graveyard_fingerprint(graveyard)
    recorded_grave = recorded.get("graveyard_fingerprint", {})
    for key in ("file_count", "max_mtime", "total_bytes"):
        if recorded_grave.get(key) != current_grave.get(key):
            return True, f"graveyard {key} changed"

    return False, None


def write_manifest(
    manifest: Path,
    base: Path,
    base_hash: str,
    graveyard: Path,
    grave_fp: dict,
    output: Path,
    reason: str,
) -> None:
    manifest_data = {
        "output": str(output),
        "output_sha256": file_sha256(output),
        "output_size_bytes": output.stat().st_size,
        "base_dataset": str(base),
        "base_dataset_sha256": base_hash,
        "graveyard_dir": str(graveyard),
        "graveyard_fingerprint": grave_fp,
        "rebuilt_at_utc": datetime.now(timezone.utc).isoformat(),
        "rebuild_reason": reason,
    }
    # Optional row/instrument counts — read once at end to keep the manifest informative.
    try:
        import pandas as pd
        df = pd.read_parquet(output)
        manifest_data["rows"] = int(len(df))
        for key in ("instrument", "ticker", "symbol"):
            if key in df.columns:
                manifest_data["instruments"] = int(df[key].nunique())
                break
    except Exception:
        pass
    atomic_write_json(manifest, manifest_data)


def main() -> int:
    parser = argparse.ArgumentParser(description="Auto-rebuild the SB-corrected dataset when inputs change.")
    parser.add_argument("--base-dataset", type=Path, default=DEFAULT_BASE)
    parser.add_argument("--graveyard-dir", type=Path, default=DEFAULT_GRAVEYARD)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--force", action="store_true", help="Rebuild even if signatures match.")
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Exit code 2 if rebuild is needed, 0 if up-to-date. Doesn't run the rebuild.",
    )
    args = parser.parse_args()

    base: Path = args.base_dataset
    graveyard: Path = args.graveyard_dir
    output: Path = args.output
    manifest = manifest_path_for(output)

    if not base.exists():
        print(f"Base dataset not found: {base}", file=sys.stderr)
        return 1

    rebuild, reason = needs_rebuild(base, graveyard, output, manifest, args.force)

    if not rebuild:
        print(f"SB-corrected dataset up to date — no rebuild needed.")
        print(f"  Output:   {output}")
        print(f"  Manifest: {manifest}")
        return 0

    if args.check_only:
        print(f"REBUILD NEEDED: {reason}")
        return 2

    print(f"Rebuilding SB-corrected dataset (reason: {reason})...")
    cmd = [
        sys.executable,
        str(REPO_ROOT / "scripts" / "build_sb_corrected_dataset.py"),
        "--base-dataset", str(base),
        "--graveyard-dir", str(graveyard),
        "--output", str(output),
    ]
    rc = subprocess.call(cmd, cwd=str(REPO_ROOT))
    if rc != 0:
        print(f"Rebuild failed (exit {rc})", file=sys.stderr)
        return rc

    if not output.exists():
        print(f"Rebuild reported success but output missing: {output}", file=sys.stderr)
        return 1

    base_hash = file_sha256(base)
    grave_fp = graveyard_fingerprint(graveyard)
    write_manifest(manifest, base, base_hash, graveyard, grave_fp, output, reason)
    print(f"Manifest written: {manifest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
