#!/bin/bash
set -euo pipefail

# Build all recommended datasets based on actual Binance Data Vision availability
# See DOWNLOAD_EXTENSION_REPORT.md and DOWNLOAD_REALITY.md for rationale

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DATA_DIR="$PROJECT_ROOT/data"
BUILD_SCRIPT="$SCRIPT_DIR/build_example_dataset.py"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo "╔════════════════════════════════════════════════════════════╗"
echo "║       Build All Datasets (2020-2026 Coverage)             ║"
echo "╚════════════════════════════════════════════════════════════╝"
echo ""

# Dataset 1: 7-instrument rectangular (2020-2024)
echo -e "${BLUE}[1/4] Building 7x5yr rectangular dataset (2020-2024)...${NC}"
echo "  Date range: 2020-01-01 to 2024-12-31"
echo "  Instruments: BTC, ETH, BNB, XRP, LTC, EOS, BCH (core 7)"
echo "  Type: Rectangular (all instruments have full coverage)"
echo ""

python3 "$BUILD_SCRIPT" \
  --source real \
  --start-date 2020-01-01 \
  --end-date 2024-12-31 \
  --instruments BTCUSDT_PERP ETHUSDT_PERP BNBUSDT_PERP XRPUSDT_PERP \
               LTCUSDT_PERP EOSUSDT_PERP BCHUSDT_PERP \
  --output-path "$DATA_DIR/example_crypto_perps_7x5yr.parquet" \
  --min-coverage 0.75

if [ $? -eq 0 ]; then
    echo -e "${GREEN}✓ 7x5yr rectangular dataset created${NC}"
    ls -lh "$DATA_DIR/example_crypto_perps_7x5yr.parquet"
else
    echo -e "${RED}✗ Failed to build 7x5yr dataset${NC}"
    exit 1
fi
echo ""

# Dataset 2: 15-instrument rectangular (2023-2024)
echo -e "${BLUE}[2/4] Building 15x2yr rectangular dataset (2023-2024)...${NC}"
echo "  Date range: 2023-01-01 to 2024-09-30"
echo "  Instruments: All 15 (core 7 + 2020 launches + 2021 launches)"
echo "  Type: Rectangular (all instruments have full coverage)"
echo "  Note: Ends Sep 2024 to avoid MATICUSDT gap"
echo ""

python3 "$BUILD_SCRIPT" \
  --source real \
  --start-date 2023-01-01 \
  --end-date 2024-09-30 \
  --instruments BTCUSDT_PERP ETHUSDT_PERP BNBUSDT_PERP XRPUSDT_PERP \
               LTCUSDT_PERP EOSUSDT_PERP BCHUSDT_PERP LINKUSDT_PERP \
               SOLUSDT_PERP DOTUSDT_PERP ADAUSDT_PERP UNIUSDT_PERP \
               MATICUSDT_PERP DOGEUSDT_PERP AVAXUSDT_PERP \
  --output-path "$DATA_DIR/example_crypto_perps_15x2yr.parquet" \
  --min-coverage 0.90

if [ $? -eq 0 ]; then
    echo -e "${GREEN}✓ 15x2yr rectangular dataset created${NC}"
    ls -lh "$DATA_DIR/example_crypto_perps_15x2yr.parquet"
else
    echo -e "${RED}✗ Failed to build 15x2yr dataset${NC}"
    exit 1
fi
echo ""

# Dataset 3: 15-instrument jagged (2020-2024)
echo -e "${BLUE}[3/4] Building 15x5yr jagged dataset (2020-2024)...${NC}"
echo "  Date range: 2020-01-01 to 2024-09-30 (jagged)"
echo "  Instruments: All 15 with natural launch dates"
echo "  Type: Jagged (instruments have different date ranges)"
echo "  Note: Allows testing lifecycle states, IDM eligibility"
echo "  Status: Already built (see JAGGED_PANEL_FIX.md)"
echo ""

if [ -f "$DATA_DIR/example_crypto_perps_15x5yr_jagged.parquet" ]; then
    echo -e "${GREEN}✓ 15x5yr jagged dataset already exists${NC}"
    ls -lh "$DATA_DIR/example_crypto_perps_15x5yr_jagged.parquet"
else
    echo -e "${YELLOW}Building 15x5yr jagged dataset...${NC}"
    python3 "$BUILD_SCRIPT" \
      --source real \
      --start-date 2020-01-01 \
      --end-date 2024-09-30 \
      --instruments BTCUSDT_PERP ETHUSDT_PERP BNBUSDT_PERP XRPUSDT_PERP \
                   LTCUSDT_PERP EOSUSDT_PERP BCHUSDT_PERP LINKUSDT_PERP \
                   SOLUSDT_PERP DOTUSDT_PERP ADAUSDT_PERP UNIUSDT_PERP \
                   MATICUSDT_PERP DOGEUSDT_PERP AVAXUSDT_PERP \
      --output-path "$DATA_DIR/example_crypto_perps_15x5yr_jagged.parquet" \
      --min-coverage 0.60 \
      --allow-jagged

    if [ $? -eq 0 ]; then
        echo -e "${GREEN}✓ 15x5yr jagged dataset created${NC}"
        ls -lh "$DATA_DIR/example_crypto_perps_15x5yr_jagged.parquet"
    else
        echo -e "${RED}✗ Failed to build 15x5yr jagged dataset${NC}"
        exit 1
    fi
fi
echo ""

# Dataset 4: 13-instrument extended (2025 + Jan 2026)
echo -e "${BLUE}[4/4] Building 13x13mo extended dataset (2025-2026)...${NC}"
echo "  Date range: 2025-01-01 to 2026-01-25"
echo "  Instruments: 13 (all except EOSUSDT and MATICUSDT - delisted)"
echo "  Type: Rectangular (recent data for production testing)"
echo "  Note: Uses monthly + daily data from extended download"
echo ""

python3 "$BUILD_SCRIPT" \
  --source real \
  --start-date 2025-01-01 \
  --end-date 2026-01-25 \
  --instruments BTCUSDT_PERP ETHUSDT_PERP BNBUSDT_PERP XRPUSDT_PERP \
               LTCUSDT_PERP BCHUSDT_PERP LINKUSDT_PERP \
               SOLUSDT_PERP DOTUSDT_PERP ADAUSDT_PERP UNIUSDT_PERP \
               DOGEUSDT_PERP AVAXUSDT_PERP \
  --output-path "$DATA_DIR/example_crypto_perps_13x13mo.parquet" \
  --min-coverage 0.90

if [ $? -eq 0 ]; then
    echo -e "${GREEN}✓ 13x13mo extended dataset created${NC}"
    ls -lh "$DATA_DIR/example_crypto_perps_13x13mo.parquet"
else
    echo -e "${RED}✗ Failed to build 13x13mo dataset${NC}"
    exit 1
fi
echo ""

# Summary
echo "╔════════════════════════════════════════════════════════════╗"
echo "║                 All Datasets Created                       ║"
echo "╚════════════════════════════════════════════════════════════╝"
echo ""
echo "Created datasets:"
echo "  1. example_crypto_perps_7x5yr.parquet (2020-2024, 7 instruments)"
echo "  2. example_crypto_perps_15x2yr.parquet (2023-2024, 15 instruments)"
echo "  3. example_crypto_perps_15x5yr_jagged.parquet (2020-2024, 15 instruments, jagged)"
echo "  4. example_crypto_perps_13x13mo.parquet (2025-2026, 13 instruments)"
echo ""
echo "Next steps:"
echo "  # Run Phase 1 backtests"
echo "  bash scripts/run_all_backtests.sh"
echo ""
