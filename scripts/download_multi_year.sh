#!/bin/bash
#
# Batch download script for multi-year Binance perpetual futures data
#
# Phase 1: Downloads 2020-2024 data for 4 instruments with full 2020 coverage
# Phase 2: Expands to 15 instruments for 2021-2024
#
# Usage:
#   ./scripts/download_multi_year.sh phase1  # Download Phase 1 (2020-2024, 4 instruments)
#   ./scripts/download_multi_year.sh phase2  # Download Phase 2 (2021-2024, 15 instruments)
#   ./scripts/download_multi_year.sh custom SYMBOL YEAR  # Download specific symbol/year
#

set -e  # Exit on error

# Phase 1: Only symbols with full 2020 coverage (excludes SOL - launches mid-2020)
PHASE1_SYMBOLS="BTCUSDT ETHUSDT BNBUSDT XRPUSDT"
PHASE1_YEARS="2020 2021 2022 2023 2024"

# Phase 2: Add 11 more symbols (15 total)
PHASE2_SYMBOLS="BTCUSDT ETHUSDT BNBUSDT XRPUSDT LTCUSDT EOSUSDT SOLUSDT DOTUSDT LINKUSDT ADAUSDT DOGEUSDT MATICUSDT AVAXUSDT UNIUSDT BCHUSDT"
PHASE2_YEARS="2021 2022 2023 2024"

# Default output directory
OUTPUT_DIR="${OUTPUT_DIR:-data/raw}"

# Get script directory for python script path
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DOWNLOAD_SCRIPT="$SCRIPT_DIR/download_binance_data.py"

if [[ ! -f "$DOWNLOAD_SCRIPT" ]]; then
    echo "ERROR: download_binance_data.py not found at $DOWNLOAD_SCRIPT"
    exit 1
fi

# Phase selection
PHASE="${1:-phase1}"

case "$PHASE" in
    phase1)
        echo "=== Phase 1: Time Horizon Expansion ==="
        echo "Downloading 2020-2024 data for 4 instruments (BTC, ETH, BNB, XRP)"
        echo "Expected: ~192 ZIP files (4 symbols × 5 years × 12 months × 2 types)"
        echo ""
        SYMBOLS="$PHASE1_SYMBOLS"
        YEARS="$PHASE1_YEARS"
        ;;
    phase2)
        echo "=== Phase 2: Cross-Section Expansion ==="
        echo "Downloading 2021-2024 data for 15 instruments"
        echo "Expected: ~720 ZIP files (15 symbols × 4 years × 12 months × 2 types)"
        echo ""
        SYMBOLS="$PHASE2_SYMBOLS"
        YEARS="$PHASE2_YEARS"
        ;;
    custom)
        if [[ -z "$2" ]] || [[ -z "$3" ]]; then
            echo "ERROR: custom mode requires SYMBOL and YEAR"
            echo "Usage: $0 custom BTCUSDT 2023"
            exit 1
        fi
        SYMBOLS="$2"
        YEARS="$3"
        ;;
    *)
        echo "ERROR: Unknown phase '$PHASE'"
        echo "Usage: $0 [phase1|phase2|custom SYMBOL YEAR]"
        exit 1
        ;;
esac

# Download loop
TOTAL_DOWNLOADS=0
FAILED_DOWNLOADS=0

for symbol in $SYMBOLS; do
    for year in $YEARS; do
        echo "=== Downloading $symbol for $year ==="

        if python3 "$DOWNLOAD_SCRIPT" \
            --symbols "$symbol" \
            --year "$year" \
            --data-dir "$OUTPUT_DIR"; then
            TOTAL_DOWNLOADS=$((TOTAL_DOWNLOADS + 1))
            echo "✓ $symbol $year completed"
        else
            FAILED_DOWNLOADS=$((FAILED_DOWNLOADS + 1))
            echo "✗ $symbol $year FAILED"
        fi

        echo ""
    done
done

# Summary
echo "=== Download Summary ==="
echo "Total successful: $TOTAL_DOWNLOADS"
echo "Total failed: $FAILED_DOWNLOADS"
echo "Output directory: $OUTPUT_DIR"

if [[ $FAILED_DOWNLOADS -gt 0 ]]; then
    echo ""
    echo "WARNING: Some downloads failed. Check logs above for details."
    exit 1
fi

echo ""
echo "✓ All downloads completed successfully!"
echo ""
echo "Next steps:"
echo "  1. Build 5-year dataset:"
echo "     python scripts/build_example_dataset.py --source real --start-date 2020-01-01 --end-date 2024-12-31 --output-path data/example_crypto_perps_5yr.parquet"
echo "  2. Validate dataset:"
echo "     python scripts/validate_real_data.py data/example_crypto_perps_5yr.parquet"
