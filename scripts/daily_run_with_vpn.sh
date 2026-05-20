#!/bin/bash
# Daily paper-trading run gated by a non-US Wireguard tunnel.
#
# Pre-flight: brings up Proton VPN (NL, JP fallback) and verifies the
# exit IP is non-US. Aborts the daily run if VPN cannot reach a verified
# non-US exit. Always tears the tunnel down on exit, even when the daily
# run crashes.
#
# Usage:
#     ./scripts/daily_run_with_vpn.sh [ENV]
#
# ENV is the environment name passed to daily_paper_run.py (default "dev").
# The aux-feed freshness check below resolves files under envs/$ENV/data/,
# so prod-clone invocations should pass "prod" as the first arg.
#
# REPO_ROOT is computed from the script's own location so the same file
# works in both the dev clone and the prod clone without per-clone edits
# (which would otherwise be reverted on every git pull).
#
# Exit codes:
#     0   daily run succeeded
#     2   VPN pre-flight failed; daily run was NOT attempted
#     >0  daily run failed (the script forwards daily_paper_run.py's code)

set -u

ENV="${1:-dev}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="/opt/homebrew/opt/python@3.10/bin/python3.10"
PREFLIGHT="$REPO_ROOT/scripts/vpn_preflight.py"
DAILY_RUN="$REPO_ROOT/scripts/daily_paper_run.py"
CONFIG="$REPO_ROOT/config/crypto_perps_1k.yaml"
LOG="$REPO_ROOT/live/daily_run_with_vpn.log"
MARKER="$REPO_ROOT/live/last_run_utc_date.txt"
ENV_DATA_DIR="$REPO_ROOT/envs/$ENV/data"

mkdir -p "$(dirname "$LOG")"

ts() { date -u +'%Y-%m-%dT%H:%M:%SZ'; }
log() { echo "[$(ts)] $*" | tee -a "$LOG"; }

# Idempotency guard. Two StartCalendarInterval entries (Hour=19 + Hour=20) in
# the plist ensure one fire lands at 00:05 UTC under both EDT and EST. The
# marker file stores the UTC date of the last successful run; if it matches
# today's UTC date, we've already run and the second fire is a no-op.
TODAY_UTC=$(date -u +%Y-%m-%d)
if [ -f "$MARKER" ] && [ "$(cat "$MARKER")" = "$TODAY_UTC" ]; then
    log "already ran for UTC date $TODAY_UTC (marker present); skipping"
    exit 0
fi

# Always disconnect on exit (success, failure, signal). Idempotent.
disconnect() {
    log "post-run: tearing down VPN"
    "$PYTHON" "$PREFLIGHT" --disconnect >> "$LOG" 2>&1 || true
}
trap disconnect EXIT INT TERM

log "=========================================================="
log "starting daily_run_with_vpn"

log "step 1/2: VPN pre-flight"
if ! "$PYTHON" "$PREFLIGHT" --connect >> "$LOG" 2>&1; then
    log "FATAL: VPN pre-flight failed — daily run NOT attempted"
    exit 2
fi
log "VPN pre-flight OK"

# Freshness guard: if all six aux files prestage produces are < 12h old,
# pass --skip-prestage to daily_paper_run so it doesn't re-fetch them.
# If ANY file is stale or missing, run without the flag and let the full
# run re-do the prestage — fail-safe behavior on aux-refresh failure days.
AUX_FILES=(
    "$ENV_DATA_DIR/macro_factors.parquet"
    "$ENV_DATA_DIR/active_addresses.parquet"
    "$ENV_DATA_DIR/market_cap.parquet"
    "$ENV_DATA_DIR/hyperliquid_instruments.json"
    "$ENV_DATA_DIR/binance_oi_processed.parquet"
    "$ENV_DATA_DIR/binance_premium_index_processed.parquet"
)
MAX_AGE_SEC=$((12 * 3600))
NOW_EPOCH=$(date +%s)
SKIP_PRESTAGE=""
STALE_FILE=""
for f in "${AUX_FILES[@]}"; do
    if [ ! -f "$f" ]; then
        STALE_FILE="$(basename "$f") (missing)"
        break
    fi
    mtime=$(stat -f %m "$f")
    age=$((NOW_EPOCH - mtime))
    if [ "$age" -gt "$MAX_AGE_SEC" ]; then
        STALE_FILE="$(basename "$f") ($((age / 3600))h old)"
        break
    fi
done
if [ -z "$STALE_FILE" ]; then
    SKIP_PRESTAGE="--skip-prestage"
    log "aux freshness OK: all 6 files < 12h old — passing --skip-prestage"
else
    log "aux freshness FAIL: $STALE_FILE — running full prestage in daily_paper_run.py"
fi

log "step 2/2: daily_paper_run.py --env $ENV $SKIP_PRESTAGE"
# Suppress per-instrument forecast-scaling DEBUG chatter (saves ~5-10% wall-
# clock from stdout I/O alone and keeps launchd_stdout.log from growing
# 100 MB/week). Honored by syslogging/logger.py::_configure_sim.
PYSYS_LOG_LEVEL=INFO "$PYTHON" "$DAILY_RUN" --config "$CONFIG" --env "$ENV" --notify $SKIP_PRESTAGE >> "$LOG" 2>&1
rc=$?
if [ $rc -eq 0 ]; then
    log "daily_paper_run.py succeeded"
    # Stamp the marker only on full success — so a VPN-up + daily-run-failed
    # day will NOT block the second fire from retrying.
    echo "$TODAY_UTC" > "$MARKER"
    log "wrote marker $MARKER (UTC date $TODAY_UTC)"
else
    log "daily_paper_run.py FAILED with exit code $rc"
fi
exit $rc
