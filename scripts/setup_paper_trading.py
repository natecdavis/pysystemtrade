#!/usr/bin/env python3
"""
One-Time Paper Trading Setup

Initialises all live/ state files and installs the macOS launchd job.
Safe to re-run (idempotent).

Steps:
    1. Validate live/current_positions.csv and live/current_equity.txt exist
    2. Initialise live/equity_history.csv (seed with --starting-equity if provided)
    3. Initialise live/circuit_breaker_state.json (clear state)
    4. Render live/paper_trading.plist.template → ~/Library/LaunchAgents/
    5. launchctl load the plist
    6. Print setup summary

Usage:
    python scripts/setup_paper_trading.py \\
        --config config/crypto_perps_full_rules.yaml \\
        [--starting-equity 10000] \\
        [--run-hour 1] \\
        [--dry-run]
"""

import argparse
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
LIVE_DIR = REPO_ROOT / "live"
PLIST_TEMPLATE = LIVE_DIR / "paper_trading.plist.template"
LAUNCH_AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"
PLIST_LABEL = "com.nathanieldavis.paper-trading"
PLIST_DEST = LAUNCH_AGENTS_DIR / f"{PLIST_LABEL}.plist"

EQUITY_HISTORY = LIVE_DIR / "equity_history.csv"
CB_STATE = LIVE_DIR / "circuit_breaker_state.json"

sys.path.insert(0, str(REPO_ROOT))
from sysdata.crypto.circuit_breaker import CircuitBreaker


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _print(msg: str, dry_run: bool = False) -> None:
    prefix = "[DRY RUN] " if dry_run else ""
    print(f"{prefix}{msg}")


def validate_prerequisites() -> list[str]:
    """Return list of error messages for missing prerequisites."""
    errors = []
    positions_file = LIVE_DIR / "current_positions.csv"
    equity_file = LIVE_DIR / "current_equity.txt"

    if not positions_file.exists():
        errors.append(
            f"Missing: {positions_file}\n"
            "  Create it with your current Binance positions (can be empty with headers).\n"
            "  Expected columns: instrument,quantity,entry_price"
        )
    if not equity_file.exists():
        errors.append(
            f"Missing: {equity_file}\n"
            "  Create it with your current portfolio value in USD, e.g.:\n"
            "    echo '10000.00' > live/current_equity.txt"
        )
    return errors


def init_equity_history(starting_equity: float | None, dry_run: bool) -> None:
    """Seed equity_history.csv with today's equity if not already present."""
    if EQUITY_HISTORY.exists():
        _print(f"  {EQUITY_HISTORY.name} already exists — skipping seed.", dry_run)
        return

    if starting_equity is None:
        # Try to read from current_equity.txt
        equity_file = LIVE_DIR / "current_equity.txt"
        if equity_file.exists():
            try:
                starting_equity = float(equity_file.read_text().strip())
            except ValueError:
                pass

    if starting_equity is None:
        _print(
            f"  WARNING: Cannot seed {EQUITY_HISTORY.name} — no --starting-equity provided "
            "and live/current_equity.txt is missing or unreadable.\n"
            "  The circuit breaker will be skipped until 2 equity rows exist.",
            dry_run,
        )
        return

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    _print(f"  Seeding {EQUITY_HISTORY.name} with {today}: ${starting_equity:,.2f}", dry_run)

    if not dry_run:
        cb = CircuitBreaker(equity_history_path=EQUITY_HISTORY, state_path=CB_STATE)
        cb.append_equity(today, starting_equity)


def init_circuit_breaker_state(dry_run: bool) -> None:
    """Write clean circuit breaker state."""
    _print(f"  Initialising {CB_STATE.name}...", dry_run)
    if not dry_run:
        cb = CircuitBreaker(equity_history_path=EQUITY_HISTORY, state_path=CB_STATE)
        cb.reset()


def render_plist(config: str, run_hour: int, dry_run: bool) -> str:
    """Render the plist template and return rendered content."""
    if not PLIST_TEMPLATE.exists():
        raise FileNotFoundError(f"Plist template not found: {PLIST_TEMPLATE}")

    python_path = sys.executable
    content = PLIST_TEMPLATE.read_text()
    content = content.replace("PYTHON_PATH", python_path)
    content = content.replace("REPO_ROOT", str(REPO_ROOT))
    content = content.replace("RUN_HOUR", str(run_hour))

    # Override config path in plist
    content = content.replace(
        "config/crypto_perps_full_rules.yaml",
        config,
    )

    return content


def install_launchd(config: str, run_hour: int, dry_run: bool) -> None:
    """Render plist, write to LaunchAgents, and load it."""
    _print(f"  Rendering plist (run_hour={run_hour})...", dry_run)
    rendered = render_plist(config, run_hour, dry_run)

    _print(f"  Writing → {PLIST_DEST}", dry_run)
    if not dry_run:
        LAUNCH_AGENTS_DIR.mkdir(parents=True, exist_ok=True)

        # Unload existing job first (ignore errors — job may not exist)
        subprocess.run(
            ["launchctl", "unload", str(PLIST_DEST)],
            capture_output=True,
        )
        PLIST_DEST.write_text(rendered)

    _print(f"  Loading plist with launchctl...", dry_run)
    if not dry_run:
        result = subprocess.run(
            ["launchctl", "load", str(PLIST_DEST)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print(f"  WARNING: launchctl load failed: {result.stderr.strip()}")
        else:
            _print(f"  launchd job loaded: {PLIST_LABEL}", dry_run)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="One-time paper trading setup")
    parser.add_argument("--config", required=True, help="Backtest config YAML path")
    parser.add_argument(
        "--starting-equity",
        type=float,
        default=None,
        help="Starting equity in USD to seed equity_history.csv (default: read from current_equity.txt)",
    )
    parser.add_argument(
        "--run-hour",
        type=int,
        default=1,
        help="Local hour for launchd to fire (default: 1 = 01:00 local time)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview without making changes")
    args = parser.parse_args()

    print("=" * 60)
    print("Paper Trading Setup")
    print("=" * 60)
    if args.dry_run:
        print("DRY RUN — no changes will be made\n")

    # Step 1: Validate prerequisites
    print("[1/4] Validating prerequisites...")
    errors = validate_prerequisites()
    if errors:
        print("\nERROR: Missing required files:\n")
        for e in errors:
            print(f"  {e}\n")
        return 1
    print("  OK — current_positions.csv and current_equity.txt found.")

    # Step 2: Equity history
    print("\n[2/4] Initialising equity history...")
    init_equity_history(args.starting_equity, args.dry_run)

    # Step 3: Circuit breaker state
    print("\n[3/4] Initialising circuit breaker state...")
    init_circuit_breaker_state(args.dry_run)

    # Step 4: launchd
    print("\n[4/4] Installing launchd job...")
    try:
        install_launchd(args.config, args.run_hour, args.dry_run)
    except Exception as e:
        print(f"  ERROR: {e}")
        return 1

    # Summary
    print("\n" + "=" * 60)
    print("Setup complete!")
    print("=" * 60)
    print(f"  Config:          {args.config}")
    print(f"  Scheduled:       Daily at {args.run_hour:02d}:00 local time")
    print(f"  Equity history:  {EQUITY_HISTORY}")
    print(f"  CB state:        {CB_STATE}")
    print(f"  launchd plist:   {PLIST_DEST}")
    print(f"  Logs:            {LIVE_DIR}/launchd_stdout.log")
    print()
    print("Daily user workflow:")
    print("  1. Update live/current_equity.txt with today's Binance balance")
    print("  2. Wait for 01:00 notification (or run manually with daily_paper_run.py)")
    print("  3. Execute trades on Binance")
    print("  4. Update live/current_positions.csv with fills")
    print()
    print("To run manually:")
    print(f"  python scripts/daily_paper_run.py --config {args.config}")
    print()
    print("To check circuit breaker:")
    print("  python scripts/reset_circuit_breaker.py --status")
    return 0


if __name__ == "__main__":
    sys.exit(main())
