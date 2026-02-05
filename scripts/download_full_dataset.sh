#!/bin/bash
set -euo pipefail

# Full dataset download script for 15 instruments (2019-2025)
# With disk space checks, politeness delays, and failure logging

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DATA_DIR="$PROJECT_ROOT/data/raw/binance"
DOWNLOAD_SCRIPT="$SCRIPT_DIR/download_binance_data.py"
FAILURE_LOG="$PROJECT_ROOT/download_failures.log"

# Download configuration
INTER_SYMBOL_SLEEP=2  # seconds between symbols (politeness)
EXPECTED_SIZE_GB=10   # Expected total download size

# Instruments organized by launch date
# Core 7 (2019-09 launch): BTCUSDT ETHUSDT BNBUSDT XRPUSDT LTCUSDT EOSUSDT BCHUSDT
# 2020 launches: LINKUSDT SOLUSDT DOTUSDT ADAUSDT
# 2021 launches: UNIUSDT MATICUSDT DOGEUSDT AVAXUSDT

CORE_7_SYMBOLS=(BTCUSDT ETHUSDT BNBUSDT XRPUSDT LTCUSDT EOSUSDT BCHUSDT)
CORE_7_YEARS=(2019 2020 2021 2022 2023 2024 2025)

LAUNCH_2020_SYMBOLS=(LINKUSDT SOLUSDT DOTUSDT ADAUSDT)
LAUNCH_2020_YEARS=(2020 2021 2022 2023 2024 2025)

LAUNCH_2021_SYMBOLS=(UNIUSDT MATICUSDT DOGEUSDT AVAXUSDT)
LAUNCH_2021_YEARS=(2021 2022 2023 2024 2025)

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo "╔════════════════════════════════════════════════════════════╗"
echo "║  Full Dataset Download - 15 Instruments (2019-2025)       ║"
echo "╚════════════════════════════════════════════════════════════╝"
echo ""

# Step 1: Check disk space
echo -e "${BLUE}[1/4] Checking disk space...${NC}"

# macOS compatible disk space check
if [[ "$OSTYPE" == "darwin"* ]]; then
    # macOS: df -h gives human readable (e.g., "68Gi")
    AVAILABLE=$(df -h "$DATA_DIR" 2>/dev/null | tail -1 | awk '{print $4}')
    # Extract numeric part (68 from "68Gi" or "68G")
    AVAILABLE_GB=$(echo "$AVAILABLE" | sed 's/Gi$//' | sed 's/G$//')
else
    # Linux: df -BG gives gigabytes
    AVAILABLE_GB=$(df -BG "$DATA_DIR" 2>/dev/null | tail -1 | awk '{print $4}' | sed 's/G//')
fi

# Convert to integer for comparison
AVAILABLE_GB_INT=$(printf "%.0f" "$AVAILABLE_GB" 2>/dev/null || echo "0")

if [ "$AVAILABLE_GB_INT" -lt "$EXPECTED_SIZE_GB" ]; then
    echo -e "${RED}✗ Insufficient disk space${NC}"
    echo "  Available: ${AVAILABLE}B (${AVAILABLE_GB_INT}GB)"
    echo "  Expected: ${EXPECTED_SIZE_GB}GB"
    echo ""
    echo "Please free up disk space and try again."
    exit 1
else
    echo -e "${GREEN}✓ Sufficient disk space${NC}"
    echo "  Available: ${AVAILABLE}B (${AVAILABLE_GB_INT}GB, need ~${EXPECTED_SIZE_GB}GB)"
fi
echo ""

# Step 2: Count existing files
echo -e "${BLUE}[2/4] Checking existing downloads...${NC}"
EXISTING_KLINES=$(find "$DATA_DIR/klines" -name "*.zip" 2>/dev/null | wc -l || echo "0")
EXISTING_FUNDING=$(find "$DATA_DIR/funding_rates" -name "*.zip" 2>/dev/null | wc -l || echo "0")
EXISTING_TOTAL=$((EXISTING_KLINES + EXISTING_FUNDING))

echo "  Klines: $EXISTING_KLINES files"
echo "  Funding: $EXISTING_FUNDING files"
echo "  Total: $EXISTING_TOTAL files"
echo ""

if [ "$EXISTING_TOTAL" -gt 0 ]; then
    echo -e "${GREEN}✓ Existing files will be skipped (use --force to redownload)${NC}"
else
    echo "  No existing files found - fresh download"
fi
echo ""

# Step 3: Calculate download plan
echo -e "${BLUE}[3/4] Calculating download plan...${NC}"

CORE_7_MONTHS=$((7 * 7 * 12))  # 7 symbols * 7 years * 12 months
LAUNCH_2020_MONTHS=$((4 * 6 * 12))  # 4 symbols * 6 years * 12 months
LAUNCH_2021_MONTHS=$((4 * 5 * 12))  # 4 symbols * 5 years * 12 months

TOTAL_MONTHS=$((CORE_7_MONTHS + LAUNCH_2020_MONTHS + LAUNCH_2021_MONTHS))
TOTAL_FILES=$((TOTAL_MONTHS * 2))  # klines + funding

echo "  Core 7 (2019-2025): ${CORE_7_MONTHS} months × 2 data types = $((CORE_7_MONTHS * 2)) files"
echo "  2020 launches (2020-2025): ${LAUNCH_2020_MONTHS} months × 2 = $((LAUNCH_2020_MONTHS * 2)) files"
echo "  2021 launches (2021-2025): ${LAUNCH_2021_MONTHS} months × 2 = $((LAUNCH_2021_MONTHS * 2)) files"
echo ""
echo -e "${GREEN}Total: $TOTAL_FILES files to download${NC}"

# Estimate time (assume 1 file/sec including retries + sleep)
ESTIMATED_MINUTES=$((TOTAL_FILES / 60))
echo "  Estimated time: ~${ESTIMATED_MINUTES} minutes (varies with network speed)"
echo ""

# Step 4: Confirm before proceeding (skip if non-interactive)
if [ -t 0 ]; then
    # Interactive: ask for confirmation
    read -p "Proceed with download? (y/n) " -n 1 -r
    echo ""
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "Download cancelled."
        exit 0
    fi
    echo ""
else
    # Non-interactive: proceed automatically
    echo "Non-interactive mode: proceeding with download..."
    echo ""
fi

# Clear previous failure log
> "$FAILURE_LOG"

# Download function
download_symbol_years() {
    local symbol=$1
    shift
    local years=("$@")
    local num_years=${#years[@]}
    local first_year="${years[0]}"
    local last_year="${years[$((num_years-1))]}"

    echo ""
    echo -e "${BLUE}════════════════════════════════════════════════════════════${NC}"
    echo -e "${BLUE}Downloading $symbol ($first_year-$last_year)${NC}"
    echo -e "${BLUE}════════════════════════════════════════════════════════════${NC}"

    for year in "${years[@]}"; do
        echo ""
        echo -e "${YELLOW}→ $symbol $year${NC}"

        # Run download script (inherits error handling, retries, validation)
        if python3 "$DOWNLOAD_SCRIPT" \
            --symbols "$symbol" \
            --year "$year" \
            --data-dir "$DATA_DIR" \
            --skip-existing; then
            echo -e "${GREEN}✓ $symbol $year completed${NC}"
        else
            echo -e "${RED}✗ $symbol $year failed${NC}"
            echo "$symbol $year" >> "$FAILURE_LOG"
        fi

        # Politeness delay (between years)
        if [ "$year" != "$last_year" ]; then
            sleep 1
        fi
    done

    # Inter-symbol delay (longer pause between symbols)
    sleep $INTER_SYMBOL_SLEEP
}

# Start download timer
START_TIME=$(date +%s)

echo ""
echo -e "${BLUE}[4/4] Starting downloads...${NC}"
echo ""

# Download Core 7 symbols (2019-2025)
echo -e "${GREEN}▶ Core 7 symbols (2019-09 launch)${NC}"
for symbol in "${CORE_7_SYMBOLS[@]}"; do
    download_symbol_years "$symbol" "${CORE_7_YEARS[@]}"
done

# Download 2020 launches (2020-2025)
echo ""
echo -e "${GREEN}▶ 2020 launches${NC}"
for symbol in "${LAUNCH_2020_SYMBOLS[@]}"; do
    download_symbol_years "$symbol" "${LAUNCH_2020_YEARS[@]}"
done

# Download 2021 launches (2021-2025)
echo ""
echo -e "${GREEN}▶ 2021 launches${NC}"
for symbol in "${LAUNCH_2021_SYMBOLS[@]}"; do
    download_symbol_years "$symbol" "${LAUNCH_2021_YEARS[@]}"
done

# Calculate elapsed time
END_TIME=$(date +%s)
ELAPSED_SECONDS=$((END_TIME - START_TIME))
ELAPSED_MINUTES=$((ELAPSED_SECONDS / 60))
ELAPSED_SECONDS_REMAINDER=$((ELAPSED_SECONDS % 60))

# Final summary
echo ""
echo "╔════════════════════════════════════════════════════════════╗"
echo "║                    Download Complete                       ║"
echo "╚════════════════════════════════════════════════════════════╝"
echo ""

# Count final files
FINAL_KLINES=$(find "$DATA_DIR/klines" -name "*.zip" 2>/dev/null | wc -l || echo "0")
FINAL_FUNDING=$(find "$DATA_DIR/funding_rates" -name "*.zip" 2>/dev/null | wc -l || echo "0")
FINAL_TOTAL=$((FINAL_KLINES + FINAL_FUNDING))

echo "Files downloaded:"
echo "  Klines: $FINAL_KLINES files"
echo "  Funding: $FINAL_FUNDING files"
echo "  Total: $FINAL_TOTAL files"
echo ""

# Calculate total size
TOTAL_SIZE_BYTES=$(find "$DATA_DIR" -name "*.zip" -exec stat -f%z {} + 2>/dev/null | awk '{s+=$1} END {print s}' || echo "0")
TOTAL_SIZE_GB=$(echo "scale=2; $TOTAL_SIZE_BYTES / 1024 / 1024 / 1024" | bc)
echo "Total size: ${TOTAL_SIZE_GB}GB"
echo ""

echo "Time elapsed: ${ELAPSED_MINUTES}m ${ELAPSED_SECONDS_REMAINDER}s"
echo ""

# Check for failures
if [ -s "$FAILURE_LOG" ]; then
    FAILURE_COUNT=$(wc -l < "$FAILURE_LOG")
    echo -e "${RED}✗ $FAILURE_COUNT download(s) failed${NC}"
    echo ""
    echo "Failed downloads logged to: $FAILURE_LOG"
    echo ""
    echo "To retry failed downloads:"
    echo "  cat $FAILURE_LOG | while read symbol year; do"
    echo "    python $DOWNLOAD_SCRIPT --symbols \$symbol --year \$year"
    echo "  done"
    echo ""
    exit 1
else
    echo -e "${GREEN}✓ All downloads completed successfully!${NC}"
    echo ""
    echo "Next steps:"
    echo "  1. Build datasets:"
    echo "     bash scripts/build_all_datasets.sh"
    echo ""
    echo "  2. Run backtests:"
    echo "     bash scripts/run_all_backtests.sh"
    echo ""
    exit 0
fi
