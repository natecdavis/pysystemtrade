"""
Probe 4: Manifest-chain teeth.

Verify that sysdata.crypto.manifest_chain.verify_chain() correctly:
  4a) returns passed=False when an output file is hash-tampered between record + verify
  4b) returns passed=False when a recorded file is deleted
  4c) returns passed=False when no fully-complete run_id exists
  4d) returns passed=True for a clean newly-recorded chain

Runs entirely in /tmp/<probe-tmpdir>/. Does not touch envs/dev/live/* or any HL endpoint.
"""

from __future__ import annotations
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "/Users/nathanieldavis/pysystemtrade-crypto-perps")

from sysdata.crypto.manifest_chain import (
    CHAIN_FILENAME,
    REQUIRED_STAGES,
    append_stage,
    new_run_id,
    verify_chain,
)


def write(p: Path, content: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)


def expect(name: str, cond: bool, detail: str = "") -> None:
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {name}{(': ' + detail) if detail else ''}")
    if not cond:
        sys.exit(1)


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="probe4_chain_"))
    print(f"Probe 4 tmpdir: {tmp}")

    chain = tmp / CHAIN_FILENAME

    # ─── 4a/d: clean chain ─────────────────────────────────────────────────
    print("\n4a/d: clean chain → verify passes")
    rid = new_run_id()
    ds = tmp / "dataset_latest.parquet"
    write(ds, "synthetic dataset content v1")
    pos = tmp / "positions.csv"
    write(pos, "instrument,position\nBTC,0.5\n")
    tp = tmp / "trade_plan_2026-05-06.csv"
    write(tp, "instrument,delta_notional\nBTC,100\n")

    append_stage(chain, "dataset_build", outputs={"dataset": ds}, run_id=rid)
    append_stage(chain, "backtest", inputs={"dataset": ds},
                 outputs={"positions": pos}, run_id=rid)
    append_stage(chain, "trade_plan", inputs={"positions": pos},
                 outputs={"trade_plan": tp}, run_id=rid)
    res = verify_chain(chain)
    expect("clean chain passed=True", res["passed"], f"run_id={res['run_id']}")
    expect("clean chain stages=3", res["stages"] == 3)
    expect("legacy_skipped=0", res["legacy_skipped"] == 0)

    # ─── 4b: tamper the dataset on disk ────────────────────────────────────
    print("\n4b: tamper dataset content → verify fails")
    ds.write_text("synthetic dataset content TAMPERED")
    res = verify_chain(chain)
    expect("tampered chain passed=False", not res["passed"])
    issues = res["issues"]
    has_hash_mismatch = any(
        i.get("issue") == "hash_mismatch" and "dataset" in i.get("name", "")
        for i in issues
    )
    expect("hash_mismatch issue surfaced for dataset", has_hash_mismatch,
           f"{len(issues)} issue(s)")

    # restore + write fresh chain for next test
    write(ds, "synthetic dataset content v2")
    chain.unlink()

    # ─── 4c: incomplete run (missing trade_plan stage) ────────────────────
    print("\n4c: only 2 of 3 required stages → verify fails (no_complete_run)")
    rid2 = new_run_id()
    append_stage(chain, "dataset_build", outputs={"dataset": ds}, run_id=rid2)
    append_stage(chain, "backtest", inputs={"dataset": ds},
                 outputs={"positions": pos}, run_id=rid2)
    res = verify_chain(chain)
    expect("incomplete chain passed=False", not res["passed"])
    expect("issue is no_complete_run",
           any(i.get("issue") == "no_complete_run" for i in res["issues"]))

    # ─── 4e: file deleted between record + verify ──────────────────────────
    print("\n4e: recorded output file deleted → verify fails (missing)")
    chain.unlink()
    rid3 = new_run_id()
    append_stage(chain, "dataset_build", outputs={"dataset": ds}, run_id=rid3)
    append_stage(chain, "backtest", inputs={"dataset": ds},
                 outputs={"positions": pos}, run_id=rid3)
    append_stage(chain, "trade_plan", inputs={"positions": pos},
                 outputs={"trade_plan": tp}, run_id=rid3)
    pos.unlink()
    res = verify_chain(chain)
    expect("missing-file chain passed=False", not res["passed"])
    has_missing = any(i.get("issue") == "missing" for i in res["issues"])
    expect("issue=missing surfaced", has_missing, f"{len(res['issues'])} issue(s)")

    # ─── 4f: legacy run (no run_id) is correctly skipped ───────────────────
    print("\n4f: legacy entries (no run_id) skipped, latest tagged run wins")
    chain.unlink()
    write(pos, "instrument,position\nBTC,0.5\n")  # restore
    # Forge legacy entries by writing JSON directly
    import json, hashlib
    legacy_hash = hashlib.sha256(b"old content").hexdigest()
    legacy_entries = [
        {"stage": "dataset_build", "timestamp_utc": "2026-05-01T00:00:00",
         "inputs": {}, "outputs": {"dataset": {"path": str(ds), "sha256": legacy_hash}}},
    ]
    chain.write_text(json.dumps(legacy_entries))
    rid4 = new_run_id()
    append_stage(chain, "dataset_build", outputs={"dataset": ds}, run_id=rid4)
    append_stage(chain, "backtest", inputs={"dataset": ds},
                 outputs={"positions": pos}, run_id=rid4)
    append_stage(chain, "trade_plan", inputs={"positions": pos},
                 outputs={"trade_plan": tp}, run_id=rid4)
    res = verify_chain(chain)
    expect("with-legacy chain passed=True", res["passed"], f"run_id={res['run_id']}")
    expect("legacy_skipped=1", res["legacy_skipped"] == 1,
           f"got {res['legacy_skipped']}")

    print("\nAll Probe 4 sub-checks passed.")
    shutil.rmtree(tmp)
    return 0


if __name__ == "__main__":
    sys.exit(main())
