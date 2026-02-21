#!/bin/bash
# Check progress of Binance OI data download

echo "==================================================================="
echo "BINANCE OI DOWNLOAD PROGRESS"
echo "==================================================================="
echo ""

# Check if download is still running
if pgrep -f "download_binance_oi_data.py" > /dev/null; then
    echo "✅ Download is RUNNING"
    echo ""
else
    echo "❌ Download is NOT running (may have completed or failed)"
    echo ""
fi

# Show recent log entries
echo "Recent log entries:"
echo "-------------------------------------------------------------------"
tail -20 data/binance_oi_download.log
echo ""

# Count downloaded files
total_files=$(find data/binance_oi_raw -name "*.zip" 2>/dev/null | wc -l | tr -d ' ')
total_symbols=$(find data/binance_oi_raw -maxdepth 1 -type d 2>/dev/null | wc -l | tr -d ' ')
total_symbols=$((total_symbols - 1))  # Subtract parent directory

echo "==================================================================="
echo "STATISTICS"
echo "==================================================================="
echo "Symbols processed: $total_symbols / 300"
echo "Files downloaded: $total_files"

if [ $total_files -gt 0 ]; then
    # Calculate average size
    total_mb=$(du -sm data/binance_oi_raw 2>/dev/null | cut -f1)
    echo "Total data: ${total_mb} MB"

    # Estimate completion (rough)
    expected_files=$((300 * 2191))  # 300 symbols × ~2191 days
    progress=$((total_files * 100 / expected_files))
    echo "Estimated progress: ${progress}%"
fi

echo "==================================================================="
echo ""
echo "Commands:"
echo "  Monitor live: tail -f data/binance_oi_download.log"
echo "  Stop download: pkill -f download_binance_oi_data.py"
echo "  Restart: see PHASE2_OI_DATA_PLAN.md for full command"
echo ""
