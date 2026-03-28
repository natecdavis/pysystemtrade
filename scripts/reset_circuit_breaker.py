#!/usr/bin/env python3
"""
Circuit Breaker Inspector and Reset Tool

Usage:
    # Show current state + last 7 equity rows
    python scripts/reset_circuit_breaker.py --status

    # Clear triggered state (requires --confirm)
    python scripts/reset_circuit_breaker.py --reset --confirm
"""

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
LIVE_DIR = REPO_ROOT / "live"
EQUITY_HISTORY = LIVE_DIR / "equity_history.csv"
CB_STATE = LIVE_DIR / "circuit_breaker_state.json"

sys.path.insert(0, str(REPO_ROOT))
from sysdata.crypto.circuit_breaker import CircuitBreaker


def cmd_status(cb: CircuitBreaker) -> None:
    state = cb.get_state()
    print("=" * 50)
    print("Circuit Breaker Status")
    print("=" * 50)
    print(f"  Status:        {state.get('status', 'unknown')}")
    print(f"  Last checked:  {state.get('last_checked') or 'never'}")
    if state.get("status") == "triggered":
        print(f"  Triggered at:  {state.get('triggered_at')}")
        print(f"  Reason:        {state.get('reason')}")

    print()
    print("Equity History (last 7 rows):")
    history = cb.get_history_summary(n=7)
    if history is None or history.empty:
        print("  (no history yet)")
    else:
        # Compute daily returns for display
        try:
            import pandas as pd
            full = cb.get_history_summary(n=9999)
            equity_all = full["equity"].values if full is not None else []
            peak = max(equity_all) if len(equity_all) > 0 else 1.0
            last_eq = equity_all[-1] if len(equity_all) > 0 else 1.0
            current_dd = (last_eq / peak - 1.0) if peak > 0 else 0.0

            print(f"  {'Date':<12} {'Equity':>12} {'Daily Ret':>10}")
            print(f"  {'-'*12} {'-'*12} {'-'*10}")
            rows = history.to_dict("records")
            for i, row in enumerate(rows):
                eq = row["equity"]
                # Find prev equity for return calc
                full_rows = full.to_dict("records") if full is not None else []
                idx = next(
                    (j for j, r in enumerate(full_rows) if r["date"] == row["date"]),
                    None,
                )
                if idx is not None and idx > 0:
                    prev_eq = full_rows[idx - 1]["equity"]
                    daily_ret = f"{(eq / prev_eq - 1):+.2%}"
                else:
                    daily_ret = "  —"
                print(f"  {row['date']:<12} {eq:>12,.2f} {daily_ret:>10}")

            print()
            print(f"  Peak equity:     ${peak:,.2f}")
            print(f"  Current equity:  ${last_eq:,.2f}")
            print(f"  Drawdown:        {current_dd:.2%}")
        except Exception as e:
            print(f"  Error computing history: {e}")
            print(history.to_string(index=False))

    print()
    print(f"Limits: max_daily_loss={cb.max_daily_loss_pct:.1%}  max_drawdown={cb.max_drawdown_pct:.1%}")


def cmd_reset(cb: CircuitBreaker) -> None:
    state = cb.get_state()
    if state.get("status") != "triggered":
        print("Circuit breaker is not triggered — nothing to reset.")
        return

    print(f"Resetting circuit breaker (was: {state.get('reason')})")
    cb.reset()
    new_state = cb.get_state()
    print(f"  Status: {new_state['status']}")
    print("  Done. The next daily run will proceed normally.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Circuit breaker inspector and reset tool")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--status", action="store_true", help="Show current state + equity history")
    group.add_argument("--reset", action="store_true", help="Clear triggered state")
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Required with --reset to confirm the action",
    )
    args = parser.parse_args()

    cb = CircuitBreaker(equity_history_path=EQUITY_HISTORY, state_path=CB_STATE)

    if args.status:
        cmd_status(cb)
        return 0

    if args.reset:
        if not args.confirm:
            print("ERROR: --reset requires --confirm")
            print("  python scripts/reset_circuit_breaker.py --reset --confirm")
            return 1
        cmd_reset(cb)
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
