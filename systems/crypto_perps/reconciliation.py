"""
Post-execution reconciliation.

After trades are submitted to Hyperliquid and positions are re-synced, compare the
fresh actual position state against the trade plan's targets. Surface any symbol
whose post-trade state diverges from intent so a partial fill, slippage rejection,
or skipped symbol cannot silently distort the next day's plan.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class ReconciliationResult:
    rows: list[dict[str, Any]]
    mismatches: list[dict[str, Any]]
    tolerance_usd: float
    timestamp_utc: str
    trade_plan_path: str
    positions_after_path: str
    execution_summary: dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return not self.mismatches

    def to_dict(self) -> dict[str, Any]:
        return {
            "trade_plan_path": self.trade_plan_path,
            "positions_after_path": self.positions_after_path,
            "tolerance_usd": self.tolerance_usd,
            "timestamp_utc": self.timestamp_utc,
            "total_symbols": len(self.rows),
            "mismatches_count": len(self.mismatches),
            "passed": self.passed,
            "execution_summary": self.execution_summary,
            "rows": self.rows,
        }


def reconcile_post_execution(
    trade_plan_path: Path,
    positions_after_path: Path,
    tolerance_usd: float = 10.0,
    execution_summary: Optional[dict[str, Any]] = None,
) -> ReconciliationResult:
    """
    Diff actual post-trade positions against the trade plan's targets.

    Comparison is done in USD notional using the mark price recorded in the trade
    plan (so post-trade volatility doesn't create spurious mismatches). A symbol is
    flagged when |actual_notional - target_notional| exceeds tolerance_usd, which
    should track the live min-notional (HL: $10).

    Symbols on HL but absent from the plan are also flagged (orphan positions
    that should have been hard-exited).

    Args:
        trade_plan_path: Path to trade_plan_*.csv (output of generate_trade_plan).
        positions_after_path: Path to current_positions.csv after sync_hl_positions
            re-pulls the live state.
        tolerance_usd: Per-symbol notional mismatch threshold.
        execution_summary: Optional summary of execute_trades results
            (submitted/skipped/errors counts).

    Returns:
        ReconciliationResult with per-symbol rows and the subset that exceeded tolerance.
    """
    plan = pd.read_csv(trade_plan_path)
    actuals_after = pd.read_csv(positions_after_path)

    plan_indexed = plan.set_index("instrument")
    actuals_indexed = actuals_after.set_index("instrument")

    rows: list[dict[str, Any]] = []
    seen_actuals: set[str] = set()

    for inst in plan_indexed.index:
        plan_row = plan_indexed.loc[inst]
        target_notional = float(plan_row.get("target_notional", 0.0) or 0.0)
        mark = float(plan_row.get("mark_price_usd", 0.0) or 0.0)

        if inst in actuals_indexed.index:
            actual_contracts = float(actuals_indexed.loc[inst, "contracts"])
            actuals_mark = float(actuals_indexed.loc[inst].get("mark_price_usd", 0.0) or 0.0)
            seen_actuals.add(inst)
        else:
            actual_contracts = 0.0
            actuals_mark = 0.0

        # The plan records mark_price_usd=0 for new positions (current_contracts=0
        # at plan-generation time, so there's no current notional to compute).
        # In that case, fall back to the post-execution sync's mark_price_usd to
        # value the actuals — otherwise every successfully-opened new position
        # would false-flag as MISMATCH.
        valuation_mark = mark if mark > 0 else actuals_mark
        if valuation_mark > 0:
            actual_notional = actual_contracts * valuation_mark
            delta_usd = actual_notional - target_notional
            note = "" if mark > 0 else "valued_with_actuals_mark"
        else:
            # Neither plan nor actuals have a usable mark — can't value at all.
            actual_notional = 0.0
            delta_usd = -target_notional
            note = "no_mark_in_plan" if abs(target_notional) > tolerance_usd else "no_mark_zero_target"

        exceeds = abs(delta_usd) > tolerance_usd
        rows.append(
            {
                "instrument": inst,
                "target_notional": round(target_notional, 2),
                "actual_contracts": round(actual_contracts, 6),
                "actual_notional": round(actual_notional, 2),
                "delta_notional_usd": round(delta_usd, 2),
                "mark_price_usd": round(mark, 6),
                "exceeds_tolerance": bool(exceeds),
                "note": note,
            }
        )

    # Symbols on HL with non-zero contracts that the plan didn't address.
    for inst in actuals_indexed.index:
        if inst in seen_actuals:
            continue
        actual_contracts = float(actuals_indexed.loc[inst, "contracts"])
        if actual_contracts == 0:
            continue
        # Use mark from positions CSV if available; otherwise we can't value.
        actual_mark = float(actuals_indexed.loc[inst].get("mark_price_usd", 0.0) or 0.0)
        actual_notional = actual_contracts * actual_mark
        rows.append(
            {
                "instrument": inst,
                "target_notional": 0.0,
                "actual_contracts": round(actual_contracts, 6),
                "actual_notional": round(actual_notional, 2),
                "delta_notional_usd": round(actual_notional, 2),
                "mark_price_usd": round(actual_mark, 6),
                "exceeds_tolerance": abs(actual_notional) > tolerance_usd or actual_mark == 0,
                "note": "not_in_plan",
            }
        )

    mismatches = [r for r in rows if r["exceeds_tolerance"]]

    return ReconciliationResult(
        rows=rows,
        mismatches=mismatches,
        tolerance_usd=tolerance_usd,
        timestamp_utc=datetime.now(timezone.utc).isoformat(),
        trade_plan_path=str(trade_plan_path),
        positions_after_path=str(positions_after_path),
        execution_summary=execution_summary or {},
    )


def write_reconciliation_report(result: ReconciliationResult, output_path: Path) -> None:
    output_path.write_text(json.dumps(result.to_dict(), indent=2, default=str))
    logger.info(
        "Reconciliation report written to %s — %d symbol(s), %d mismatch(es)",
        output_path,
        len(result.rows),
        len(result.mismatches),
    )


def format_reconciliation_summary(result: ReconciliationResult) -> str:
    """Pretty-print a reconciliation summary for the operator."""
    lines = []
    status = "OK" if result.passed else f"MISMATCH ({len(result.mismatches)})"
    lines.append(f"Reconciliation: {status}  tolerance=${result.tolerance_usd:.2f}")
    if result.mismatches:
        lines.append(f"  {'Symbol':<22} {'Target':>10} {'Actual':>10} {'Δ USD':>10}  Note")
        for row in result.mismatches:
            lines.append(
                f"  {row['instrument']:<22} {row['target_notional']:>10.2f} "
                f"{row['actual_notional']:>10.2f} {row['delta_notional_usd']:>+10.2f}  "
                f"{row.get('note', '')}"
            )
    return "\n".join(lines)
