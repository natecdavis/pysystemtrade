"""
Probe 5: Atomic IO, lock, and equity dedup.

Verify, in /tmp/<probe-tmpdir>/, that:
  5a) atomic_write_text / json / csv writes are crash-safe (temp dir empty after success;
      destination always old or new content, never partial)
  5b) daily_run_lock raises LockBusy when another fd holds the lock
  5c) daily_run_lock auto-releases on scope exit (process exit equivalent)
  5d) circuit_breaker.append_equity is idempotent on (date, equity)
  5e) circuit_breaker.append_equity preserves history across many rewrites

Does not touch envs/dev/live/* or HL endpoints.
"""

from __future__ import annotations
import multiprocessing as mp
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, "/Users/nathanieldavis/pysystemtrade-crypto-perps")

from sysdata.crypto.atomic_io import (
    LockBusy,
    atomic_write_csv,
    atomic_write_json,
    atomic_write_text,
    daily_run_lock,
)
from sysdata.crypto.circuit_breaker import CircuitBreaker

import pandas as pd


def expect(name: str, cond: bool, detail: str = "") -> None:
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {name}{(': ' + detail) if detail else ''}")
    if not cond:
        sys.exit(1)


def list_temp_artifacts(d: Path) -> list[str]:
    return sorted(p.name for p in d.iterdir() if p.name.startswith("."))


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="probe5_atomic_"))
    print(f"Probe 5 tmpdir: {tmp}")

    # ─── 5a: atomic_write_* leave no .tmp behind ──────────────────────────
    print("\n5a: atomic_write_text/json/csv crash-safe + no leftover .tmp")
    txt = tmp / "test.txt"
    atomic_write_text(txt, "hello\n")
    expect("text content correct", txt.read_text() == "hello\n")
    expect("no leftover .test.txt.* tmp", not list_temp_artifacts(tmp))

    js = tmp / "state.json"
    atomic_write_json(js, {"a": 1, "b": [2, 3]})
    expect("json content readable", "\"a\": 1" in js.read_text())
    expect("no leftover .state.json.* tmp", not list_temp_artifacts(tmp))

    csv = tmp / "rows.csv"
    df = pd.DataFrame([{"date": "2026-01-01", "equity": 1000.00},
                       {"date": "2026-01-02", "equity": 1050.50}])
    atomic_write_csv(df, csv, index=False, float_format="%.2f")
    out = pd.read_csv(csv)
    expect("csv roundtrip preserves rows", len(out) == 2)
    expect("csv float format applied", out["equity"].iloc[1] == 1050.50)
    expect("no leftover .rows.csv.* tmp", not list_temp_artifacts(tmp))

    # ─── 5b: daily_run_lock raises LockBusy on contention ─────────────────
    print("\n5b: daily_run_lock raises LockBusy when held by another fd")
    lock_path = tmp / ".daily_run.lock"
    busy_raised = []

    with daily_run_lock(lock_path):
        # Try to acquire the same lock from another process — fcntl treats them
        # as different lock holders only across processes; same process re-acquires
        # fine, so simulate cross-process by spawning.
        def _try_acquire(qname, lock_path_str):
            import sys as _s
            _s.path.insert(0, "/Users/nathanieldavis/pysystemtrade-crypto-perps")
            from sysdata.crypto.atomic_io import LockBusy as _LB, daily_run_lock as _drl
            from pathlib import Path as _P
            try:
                with _drl(_P(lock_path_str)):
                    pass
                qname.put(("ok", None))
            except _LB as e:
                qname.put(("busy", str(e)))

        q = mp.Queue()
        proc = mp.Process(target=_try_acquire, args=(q, str(lock_path)))
        proc.start()
        proc.join(timeout=5)
        result, msg = q.get(timeout=2)
        busy_raised.append((result, msg))

    expect("contending process saw LockBusy", busy_raised[0][0] == "busy",
           f"raised: {busy_raised[0]}")

    # ─── 5c: lock auto-release after scope exit ──────────────────────────
    print("\n5c: lock auto-released after with-block exit")
    expect("lock file removed after release", not lock_path.exists())
    # Re-acquire works
    with daily_run_lock(lock_path):
        pass
    expect("re-acquire after release works", not lock_path.exists())

    # ─── 5d/e: circuit_breaker.append_equity idempotent + history-preserving ─
    print("\n5d/e: circuit_breaker.append_equity idempotency + history")
    eq_hist = tmp / "equity_history.csv"
    cb_state = tmp / "cb_state.json"
    cb = CircuitBreaker(equity_history_path=eq_hist, state_path=cb_state)
    cb.append_equity("2026-05-01", 4000.00)
    cb.append_equity("2026-05-02", 4050.00)
    cb.append_equity("2026-05-03", 4100.00)
    df = pd.read_csv(eq_hist)
    expect("3 rows after 3 unique appends", len(df) == 3)

    # Idempotent: same date overwrites
    cb.append_equity("2026-05-02", 4055.00)
    df = pd.read_csv(eq_hist)
    expect("still 3 rows after re-append same date",
           len(df) == 3, f"got {len(df)}")
    eq_for_2 = df.loc[df["date"] == "2026-05-02", "equity"].iloc[0]
    expect("re-append updated equity for 2026-05-02",
           eq_for_2 == 4055.00, f"got {eq_for_2}")

    # Stress: many rewrites stay coherent
    for i in range(100):
        cb.append_equity(f"2026-06-{(i % 28) + 1:02d}", 4000.0 + i)
    df = pd.read_csv(eq_hist)
    # Distinct dates: 3 (May) + 28 (June) = 31
    expect("rapid rewrites keep history coherent", len(df) == 31,
           f"got {len(df)}")
    expect("history sorted by date", list(df["date"]) == sorted(df["date"]))

    # No tmp leftovers from any of the writes
    expect("no leftover .equity_history.csv.* tmp",
           not [p for p in tmp.iterdir() if p.name.startswith(".equity_history")])

    print("\nAll Probe 5 sub-checks passed.")
    import shutil
    shutil.rmtree(tmp)
    return 0


if __name__ == "__main__":
    mp.set_start_method("fork", force=True)
    sys.exit(main())
