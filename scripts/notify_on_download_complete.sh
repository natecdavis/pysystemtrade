#!/bin/bash
# Monitor OI download and notify when complete
#
# Usage:
#   # Run in background
#   ./scripts/notify_on_download_complete.sh &
#
#   # Or run with nohup
#   nohup ./scripts/notify_on_download_complete.sh > /tmp/download_monitor.log 2>&1 &

echo "Starting download monitor..."
echo "Will check every 5 minutes until download completes"

while true; do
    # Check if download process is still running
    if ! pgrep -f "download_binance_oi_data.py" > /dev/null; then
        echo "Download process has stopped!"

        # Check the log file for completion
        if tail -5 data/binance_oi_download.log | grep -q "Download complete"; then
            echo "✅ Download completed successfully!"

            # Get final stats
            total_files=$(find data/binance_oi_raw -name "*.zip" 2>/dev/null | wc -l | tr -d ' ')
            total_mb=$(du -sm data/binance_oi_raw 2>/dev/null | cut -f1)

            message="🎉 Binance OI Download Complete!\n\nFiles: $total_files\nSize: ${total_mb} MB\n\nNext step: Run conversion script"

            # macOS notification
            osascript -e "display notification \"$message\" with title \"Phase 2 Download Complete\" sound name \"Glass\""

            # Also log to file
            echo "========================================" >> data/download_complete.txt
            echo "Download completed: $(date)" >> data/download_complete.txt
            echo "Files: $total_files" >> data/download_complete.txt
            echo "Size: ${total_mb} MB" >> data/download_complete.txt
            echo "========================================" >> data/download_complete.txt

            echo "✅ Notification sent!"

        else
            echo "⚠️  Download process stopped but may have failed"
            echo "Check data/binance_oi_download.log for details"

            # Notification for failure
            osascript -e 'display notification "Download process stopped. Check logs." with title "Phase 2 Download Alert" sound name "Basso"'
        fi

        # Exit after notification
        exit 0
    fi

    # Still running - sleep for 5 minutes
    sleep 300
done
