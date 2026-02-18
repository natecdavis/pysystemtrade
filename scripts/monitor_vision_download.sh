#!/bin/bash
# Monitor Vision bulk download progress
# Usage: ./scripts/monitor_vision_download.sh

ENV_ROOT="envs/dev"
PROGRESS_FILE="$ENV_ROOT/data/raw/vision_download_progress.json"
LOG_FILE="/tmp/vision_full_registry.log"

echo "===================================================================="
echo "Vision Download Monitor"
echo "===================================================================="
echo ""

# Check if download is running
if ps aux | grep -v grep | grep download_vision_bulk > /dev/null; then
    PID=$(ps aux | grep -v grep | grep download_vision_bulk | awk '{print $2}')
    echo "✓ Download RUNNING (PID: $PID)"
else
    echo "✗ Download NOT running"
fi

echo ""

# Progress
if [ -f "$PROGRESS_FILE" ]; then
    COMPLETED=$(python3 -c "import json; print(json.load(open('$PROGRESS_FILE'))['count'])")
    LAST_UPDATED=$(python3 -c "import json; print(json.load(open('$PROGRESS_FILE'))['last_updated'])")
    echo "Progress: $COMPLETED/541 instruments completed"
    echo "Last updated: $LAST_UPDATED"

    REMAINING=$((541 - COMPLETED))
    echo "Remaining: $REMAINING instruments"
    echo ""

    # Show last 5 completed
    echo "Last 5 completed:"
    python3 -c "
import json
data = json.load(open('$PROGRESS_FILE'))
completed = data['completed']
for instr in completed[-5:]:
    print(f'  - {instr}')
"
else
    echo "✗ Progress file not found"
fi

echo ""

# Recent activity from log
if [ -f "$LOG_FILE" ]; then
    echo "Recent activity (last 10 lines):"
    tail -10 "$LOG_FILE" | grep -E "(Processing|Downloaded|INFO)" || tail -10 "$LOG_FILE"
else
    echo "✗ Log file not found"
fi

echo ""
echo "===================================================================="
echo "Commands:"
echo "  Watch progress: watch -n 10 ./scripts/monitor_vision_download.sh"
echo "  View log:       tail -f $LOG_FILE"
echo "  Stop download:  kill $PID"
echo "===================================================================="
