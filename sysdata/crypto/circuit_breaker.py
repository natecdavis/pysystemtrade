#!/usr/bin/env python3
"""
Circuit Breaker for paper trading.

Reads equity_history.csv to compute daily returns and drawdown.
Triggers a halt if daily loss or portfolio drawdown exceeds configured limits.

State persisted in live/circuit_breaker_state.json.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from sysdata.crypto.atomic_io import atomic_write_csv, atomic_write_json

DEFAULTS = dict(max_daily_loss_pct=0.09, max_drawdown_pct=0.15)
# Thresholds set 2026-04-25: notional_trading_capital=$10,000 (2.5× phantom leverage
# on $4K actual equity) with min_notional_position=$10 (HL constraint).
# Expected live MaxDD = 5.03% (backtest re: notional) × 2.5 = 12.6% of actual equity.
# Limits give ~2.5pp headroom:
#   max_daily_loss_pct: 0.09  (worst day 2.92% × 2.5 = 7.3% + 2pp)
#   max_drawdown_pct:   0.15  (12.6% expected + 2.5pp buffer)


class CircuitBreaker:
    def __init__(
        self,
        equity_history_path: Path,
        state_path: Path,
        max_daily_loss_pct: float = DEFAULTS["max_daily_loss_pct"],
        max_drawdown_pct: float = DEFAULTS["max_drawdown_pct"],
    ):
        self.equity_history_path = Path(equity_history_path)
        self.state_path = Path(state_path)
        self.max_daily_loss_pct = max_daily_loss_pct
        self.max_drawdown_pct = max_drawdown_pct

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check(self) -> tuple[bool, str]:
        """
        Return (is_triggered, reason).

        Reads equity_history.csv and evaluates daily return + drawdown.
        Also checks the persisted state: if already triggered, returns
        immediately without re-evaluating (manual reset required).
        """
        state = self._load_state()

        # Already triggered — require explicit reset
        if state.get("status") == "triggered":
            reason = state.get("reason", "previously triggered")
            return True, f"Already triggered: {reason}"

        if not self.equity_history_path.exists():
            return False, "equity_history.csv not found — skipping check"

        df = self._load_history()
        if df is None or len(df) < 2:
            # Not enough data to compute returns yet
            self._save_state("clear", None)
            return False, "insufficient history for check"

        equity = df["equity"].values
        last = equity[-1]
        prev = equity[-2]
        # Trailing all-time peak: drawdown is measured from the highest equity ever reached.
        # This is intentional — protects accumulated profits (a 15% drop from $10K requires
        # falling to $8,500, not just $850 from $1K start). If fixed-capital drawdown from
        # initial equity is ever needed, replace with equity[0] instead of equity.max().
        peak = equity.max()

        daily_return = (last / prev) - 1.0
        drawdown = (last / peak) - 1.0

        triggered = False
        reason = ""

        if daily_return < -self.max_daily_loss_pct:
            triggered = True
            reason = (
                f"Daily loss {daily_return:.2%} exceeds limit "
                f"-{self.max_daily_loss_pct:.2%}"
            )
        elif drawdown < -self.max_drawdown_pct:
            triggered = True
            reason = (
                f"Drawdown {drawdown:.2%} exceeds limit "
                f"-{self.max_drawdown_pct:.2%}"
            )

        self._save_state("triggered" if triggered else "clear", reason if triggered else None)
        return triggered, reason

    def append_equity(self, date: str, equity: float) -> None:
        """
        Append (date, equity) to equity_history.csv.

        Idempotent: if today's date already present, overwrites that row.
        Creates the file with header if it does not exist.
        """
        if self.equity_history_path.exists():
            df = self._load_history()
        else:
            df = pd.DataFrame(columns=["date", "equity"])

        # Remove existing row for this date (idempotent)
        df = df[df["date"] != date]

        new_row = pd.DataFrame([{"date": date, "equity": equity}])
        df = pd.concat([df, new_row], ignore_index=True)
        df = df.sort_values("date")

        atomic_write_csv(df, self.equity_history_path, index=False, float_format="%.2f")

    def reset(self) -> None:
        """Clear triggered state."""
        self._save_state("clear", None)

    def get_state(self) -> dict:
        """Return current state dict."""
        return self._load_state()

    def get_history_summary(self, n: int = 7) -> pd.DataFrame | None:
        """Return last n rows of equity history."""
        if not self.equity_history_path.exists():
            return None
        df = self._load_history()
        return df.tail(n) if df is not None else None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_history(self) -> pd.DataFrame | None:
        try:
            df = pd.read_csv(self.equity_history_path)
            df = df.sort_values("date").reset_index(drop=True)
            return df
        except Exception:
            return None

    def _load_state(self) -> dict:
        if not self.state_path.exists():
            return {"status": "clear", "triggered_at": None, "reason": None, "last_checked": None}
        try:
            with open(self.state_path) as f:
                return json.load(f)
        except Exception:
            return {"status": "clear", "triggered_at": None, "reason": None, "last_checked": None}

    def _save_state(self, status: str, reason: str | None) -> None:
        now = datetime.now(timezone.utc).isoformat()
        state = self._load_state()
        state["status"] = status
        state["last_checked"] = now
        if status == "triggered":
            state["triggered_at"] = state.get("triggered_at") or now
            state["reason"] = reason
        else:
            state["triggered_at"] = None
            state["reason"] = None
        atomic_write_json(self.state_path, state, indent=2)
