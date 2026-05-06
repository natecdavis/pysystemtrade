"""
Manifest hash chain for the daily pipeline.

The pipeline goes:
    Stage 1: run_live_advisory.py builds dataset_latest.parquet
    Stage 2: run_dynamic_universe_backtest.py consumes dataset → positions.csv + diagnostics
    Stage 3: generate_trade_plan.py consumes positions.csv (+ dataset hash for traceability)
             → trade_plan_*.csv

Without a hash chain, a half-written dataset can be silently consumed by the next stage
and a corrupted backtest output can be silently fed to trade-plan generation. Each stage
appends an entry to manifest_chain.json (in the per-run output directory) recording:
    - the stage name + UTC timestamp
    - a run_id grouping the three stages of a single pipeline invocation
    - SHA256 of every input file consumed
    - SHA256 of every output file produced

Stages that consume an upstream artifact verify the hash matches what the upstream stage
recorded; mismatch raises ManifestChainError so the pipeline aborts loudly instead of
producing an incoherent trade plan.

Chain entries are append-only and grouped by `run_id`. Two pipeline runs in the same UTC
day land in the same chain file but get distinct run_ids; `verify_chain()` validates only
the latest fully-complete run so a re-run does not invalidate the previous run's
already-overwritten files.

To replay a run's coherence:

    python -c "from sysdata.crypto.manifest_chain import verify_chain; \
               verify_chain('out/paper_20260501/manifest_chain.json')"
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

CHAIN_FILENAME = "manifest_chain.json"
HASH_BUF = 1024 * 1024  # 1 MB chunks
REQUIRED_STAGES = ("dataset_build", "backtest", "trade_plan")


def new_run_id() -> str:
    """Return a fresh hex run_id for tagging a pipeline invocation's stages."""
    return uuid.uuid4().hex


class ManifestChainError(RuntimeError):
    """Raised when a stage's recorded input hash does not match the upstream output."""


def file_sha256(path: Path) -> str:
    """SHA256 of a file, streaming so large parquets don't blow memory."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(HASH_BUF):
            h.update(chunk)
    return h.hexdigest()


def _hash_files(paths: dict[str, Path]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for name, p in paths.items():
        p = Path(p)
        if not p.exists():
            out[name] = {"path": str(p), "sha256": None, "size_bytes": None, "missing": True}
            continue
        out[name] = {
            "path": str(p),
            "sha256": file_sha256(p),
            "size_bytes": p.stat().st_size,
        }
    return out


def load_chain(chain_path: Path) -> list[dict[str, Any]]:
    chain_path = Path(chain_path)
    if not chain_path.exists():
        return []
    return json.loads(chain_path.read_text())


def save_chain(chain_path: Path, entries: list[dict[str, Any]]) -> None:
    # Use atomic_write_json if available; otherwise plain json dump.
    chain_path = Path(chain_path)
    chain_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        from sysdata.crypto.atomic_io import atomic_write_json
        atomic_write_json(chain_path, entries, indent=2)
    except ImportError:
        chain_path.write_text(json.dumps(entries, indent=2, default=str) + "\n")


def append_stage(
    chain_path: Path,
    stage: str,
    inputs: Optional[dict[str, Path]] = None,
    outputs: Optional[dict[str, Path]] = None,
    extra: Optional[dict[str, Any]] = None,
    run_id: Optional[str] = None,
) -> dict[str, Any]:
    """Append a stage entry. Hashes input + output files synchronously.

    `run_id` groups stages from the same pipeline invocation; if omitted, a fresh
    one is generated (so direct/ad-hoc invocations still get tagged).
    """
    chain_path = Path(chain_path)
    entries = load_chain(chain_path)
    entry = {
        "stage": stage,
        "run_id": run_id or new_run_id(),
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "inputs": _hash_files(inputs or {}),
        "outputs": _hash_files(outputs or {}),
    }
    if extra:
        entry["extra"] = extra
    entries.append(entry)
    save_chain(chain_path, entries)
    logger.info(
        "manifest_chain: stage=%s run_id=%s recorded inputs=%s outputs=%s",
        stage,
        entry["run_id"],
        list(entry["inputs"].keys()),
        list(entry["outputs"].keys()),
    )
    return entry


def find_upstream_output(
    entries: list[dict[str, Any]],
    upstream_stage: str,
    output_name: str,
) -> Optional[dict[str, Any]]:
    """Walk the chain backwards looking for the most recent matching output."""
    for entry in reversed(entries):
        if entry["stage"] != upstream_stage:
            continue
        if output_name in entry.get("outputs", {}):
            return entry["outputs"][output_name]
    return None


def verify_input_against_upstream(
    chain_path: Path,
    upstream_stage: str,
    output_name: str,
    current_path: Path,
) -> None:
    """
    Confirm that the file at current_path matches what upstream_stage recorded
    as output_name. Raises ManifestChainError on mismatch or missing upstream.

    Use at the start of a stage that consumes an upstream artifact.
    """
    chain_path = Path(chain_path)
    if not chain_path.exists():
        raise ManifestChainError(
            f"manifest_chain not found at {chain_path}; run the upstream stage first."
        )
    entries = load_chain(chain_path)
    upstream = find_upstream_output(entries, upstream_stage, output_name)
    if upstream is None:
        raise ManifestChainError(
            f"Upstream stage '{upstream_stage}' did not record output '{output_name}' in {chain_path}."
        )
    current = Path(current_path)
    if not current.exists():
        raise ManifestChainError(f"Expected input {current} does not exist.")
    current_hash = file_sha256(current)
    if current_hash != upstream.get("sha256"):
        raise ManifestChainError(
            f"Hash mismatch for {output_name}:\n"
            f"  upstream ({upstream_stage}) recorded: {upstream.get('sha256')}\n"
            f"  current file ({current}):           {current_hash}\n"
            f"This means the file was modified or replaced between stages — aborting."
        )


def _verify_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Re-hash every input/output path in `entries` and return any mismatches."""
    issues: list[dict[str, Any]] = []
    for entry in entries:
        for kind in ("inputs", "outputs"):
            for name, info in entry.get(kind, {}).items():
                p = Path(info["path"])
                expected = info.get("sha256")
                if not p.exists():
                    if expected is not None:
                        issues.append(
                            {
                                "stage": entry["stage"],
                                "kind": kind,
                                "name": name,
                                "issue": "missing",
                                "path": str(p),
                            }
                        )
                    continue
                actual = file_sha256(p)
                if actual != expected:
                    issues.append(
                        {
                            "stage": entry["stage"],
                            "kind": kind,
                            "name": name,
                            "issue": "hash_mismatch",
                            "path": str(p),
                            "expected_sha256": expected,
                            "actual_sha256": actual,
                        }
                    )
    return issues


def find_latest_complete_run(
    entries: list[dict[str, Any]],
    required_stages: tuple[str, ...] = REQUIRED_STAGES,
) -> tuple[Optional[str], list[dict[str, Any]]]:
    """Return (run_id, entries_in_run) for the latest run that contains every required stage.

    Entries lacking a `run_id` (legacy from before run_id grouping was introduced) are
    excluded. If no tagged run is fully complete, returns (None, []).
    """
    runs: dict[str, list[dict[str, Any]]] = {}
    for entry in entries:
        run_id = entry.get("run_id")
        if not run_id:
            continue
        runs.setdefault(run_id, []).append(entry)

    candidates: list[tuple[str, str, list[dict[str, Any]]]] = []
    required = set(required_stages)
    for rid, group in runs.items():
        stages_in_run = {e["stage"] for e in group}
        if not required.issubset(stages_in_run):
            continue
        latest_ts = max(e.get("timestamp_utc", "") for e in group)
        candidates.append((latest_ts, rid, group))

    if not candidates:
        return None, []
    candidates.sort(key=lambda x: x[0])
    _, rid, group = candidates[-1]
    return rid, group


def verify_chain(
    chain_path: Path,
    required_stages: tuple[str, ...] = REQUIRED_STAGES,
) -> dict[str, Any]:
    """
    Verify the latest fully-complete run in the chain (run_id + all required_stages).

    Re-hashes every input/output path recorded for that run to detect drift since the
    run completed. Earlier runs' entries are kept in the file for audit but not
    re-verified — a re-run within the same output directory legitimately overwrites
    the prior run's artifacts and would otherwise look like a hash mismatch.

    Legacy entries lacking a `run_id` (from before run_id grouping existed) are
    skipped and counted in `legacy_skipped` for visibility.

    Returns a dict:
        - stages: number of entries in the latest verified run
        - issues: list of hash mismatches / missing files in that run
        - passed: True iff issues is empty AND a complete run was found
        - run_id: the verified run_id, or None if no complete run exists
        - legacy_skipped: count of pre-run_id entries skipped
        - stages_in_latest_run: stage names in the verified run
    """
    chain_path = Path(chain_path)
    entries = load_chain(chain_path)
    legacy = [e for e in entries if not e.get("run_id")]
    if legacy:
        logger.info(
            "manifest_chain: skipping %d legacy entries without run_id (kept for audit)",
            len(legacy),
        )

    run_id, group = find_latest_complete_run(entries, required_stages=required_stages)
    if run_id is None:
        return {
            "stages": 0,
            "issues": [{"issue": "no_complete_run", "required_stages": list(required_stages)}],
            "passed": False,
            "run_id": None,
            "legacy_skipped": len(legacy),
            "stages_in_latest_run": [],
        }

    issues = _verify_entries(group)
    return {
        "stages": len(group),
        "issues": issues,
        "passed": not issues,
        "run_id": run_id,
        "legacy_skipped": len(legacy),
        "stages_in_latest_run": sorted({e["stage"] for e in group}),
    }
