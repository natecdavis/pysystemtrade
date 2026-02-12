#!/bin/bash
set -euo pipefail

# Run all backtests after datasets are built
# Order:
#   1. Phase 1 on 15x2yr rectangular (baseline)
#   2. Phase 1 on 15x6yr jagged (test jagged panel support)
#   3. Optional: Phase 2 on 15x6yr jagged (test dynamic universe)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DATA_DIR="$PROJECT_ROOT/data"
CONFIG_DIR="$PROJECT_ROOT/config"
OUT_DIR="$PROJECT_ROOT/out"

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo "╔════════════════════════════════════════════════════════════╗"
echo "║              Run All Backtests (3 variants)                ║"
echo "╚════════════════════════════════════════════════════════════╝"
echo ""

# Check datasets exist
echo -e "${BLUE}Checking datasets...${NC}"

DATASET_15X2YR="$DATA_DIR/example_crypto_perps_15x2yr.parquet"
DATASET_15X6YR_JAGGED="$DATA_DIR/example_crypto_perps_15x6yr_jagged.parquet"

if [ ! -f "$DATASET_15X2YR" ]; then
    echo -e "${RED}✗ Dataset not found: $DATASET_15X2YR${NC}"
    echo "  Run: bash scripts/build_all_datasets.sh"
    exit 1
fi

if [ ! -f "$DATASET_15X6YR_JAGGED" ]; then
    echo -e "${RED}✗ Dataset not found: $DATASET_15X6YR_JAGGED${NC}"
    echo "  Run: bash scripts/build_all_datasets.sh"
    exit 1
fi

echo -e "${GREEN}✓ Datasets found${NC}"
echo ""

# Backtest 1: Phase 1 on 15x2yr rectangular
echo "╔════════════════════════════════════════════════════════════╗"
echo "║  [1/3] Phase 1 - 15x2yr Rectangular (2023-2025)           ║"
echo "╚════════════════════════════════════════════════════════════╝"
echo ""
echo "  Config: crypto_perps_baseline_v1.yaml"
echo "  Data: 15 instruments, 2023-2025 (rectangular)"
echo "  Universe: Static Layer-A (Phase 1)"
echo ""

python -m systems.crypto_perps.system \
  --config "$CONFIG_DIR/crypto_perps_baseline_v1.yaml" \
  --data "$DATASET_15X2YR" \
  --outdir "$OUT_DIR/phase1_15x2yr_rectangular"

echo ""
echo -e "${GREEN}✓ Backtest 1 complete${NC}"
echo "  Output: $OUT_DIR/phase1_15x2yr_rectangular/"
echo ""

# Backtest 2: Phase 1 on 15x6yr jagged
echo "╔════════════════════════════════════════════════════════════╗"
echo "║  [2/3] Phase 1 - 15x6yr Jagged (2019-2025)                ║"
echo "╚════════════════════════════════════════════════════════════╝"
echo ""
echo "  Config: crypto_perps_baseline_v1.yaml (with allow_jagged)"
echo "  Data: 15 instruments, 2019-2025 (jagged panel)"
echo "  Universe: Static Layer-A (Phase 1)"
echo "  Note: Tests lifecycle states, warmup periods, IDM eligibility"
echo ""

# Create temporary config with allow_jagged enabled
TEMP_CONFIG=$(mktemp)
cat "$CONFIG_DIR/crypto_perps_baseline_v1.yaml" > "$TEMP_CONFIG"
echo "" >> "$TEMP_CONFIG"
echo "# Jagged panel support (added by run_all_backtests.sh)" >> "$TEMP_CONFIG"
echo "system:" >> "$TEMP_CONFIG"
echo "  allow_jagged: true" >> "$TEMP_CONFIG"

python -m systems.crypto_perps.system \
  --config "$TEMP_CONFIG" \
  --data "$DATASET_15X6YR_JAGGED" \
  --outdir "$OUT_DIR/phase1_15x6yr_jagged"

rm "$TEMP_CONFIG"

echo ""
echo -e "${GREEN}✓ Backtest 2 complete${NC}"
echo "  Output: $OUT_DIR/phase1_15x6yr_jagged/"
echo ""

# Backtest 3: Optional Phase 2 on 15x6yr jagged
echo "╔════════════════════════════════════════════════════════════╗"
echo "║  [3/3] Phase 2 - 15x6yr Jagged (Optional)                 ║"
echo "╚════════════════════════════════════════════════════════════╝"
echo ""
echo "  Config: crypto_perps_phase2_v1.yaml (with review_freq='BMS')"
echo "  Data: 15 instruments, 2019-2025 (jagged panel)"
echo "  Universe: Dynamic Layer-A with monthly reviews (Phase 2)"
echo "  Note: Tests membership freezing, review logic at scale"
echo ""

read -p "Run optional Phase 2 backtest? (y/n) " -n 1 -r
echo ""
if [[ $REPLY =~ ^[Yy]$ ]]; then
    # Check if Phase 2 config exists
    if [ ! -f "$CONFIG_DIR/crypto_perps_phase2_v1.yaml" ]; then
        echo -e "${YELLOW}⚠ Phase 2 config not found, creating from baseline...${NC}"

        # Create Phase 2 config from baseline
        cat "$CONFIG_DIR/crypto_perps_baseline_v1.yaml" > "$CONFIG_DIR/crypto_perps_phase2_v1.yaml"
        cat >> "$CONFIG_DIR/crypto_perps_phase2_v1.yaml" << 'EOF'

# Phase 2: Dynamic universe with monthly reviews
universe:
  review_freq: 'BMS'  # Business Month Start (first business day of month)
  min_adv_notional: 50000000.0  # Layer-A ADV threshold (50M)
  min_history_days: 365  # Layer-A minimum data coverage
  forced_exit_days: 5  # Decay period for INELIGIBLE_HOLD state
  data_gap_days: 2  # Max consecutive missing days

# Enable jagged panel support
system:
  allow_jagged: true
EOF
        echo -e "${GREEN}✓ Created $CONFIG_DIR/crypto_perps_phase2_v1.yaml${NC}"
        echo ""
    fi

    python -m systems.crypto_perps.system \
      --config "$CONFIG_DIR/crypto_perps_phase2_v1.yaml" \
      --data "$DATASET_15X6YR_JAGGED" \
      --outdir "$OUT_DIR/phase2_15x6yr_jagged"

    echo ""
    echo -e "${GREEN}✓ Backtest 3 complete${NC}"
    echo "  Output: $OUT_DIR/phase2_15x6yr_jagged/"
    echo ""
else
    echo "  Skipped Phase 2 backtest"
    echo ""
fi

# Summary
echo "╔════════════════════════════════════════════════════════════╗"
echo "║                   Backtests Complete                       ║"
echo "╚════════════════════════════════════════════════════════════╝"
echo ""

echo "Results:"
echo "  1. Phase 1 (15x2yr rectangular): $OUT_DIR/phase1_15x2yr_rectangular/"
echo "  2. Phase 1 (15x6yr jagged): $OUT_DIR/phase1_15x6yr_jagged/"
if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo "  3. Phase 2 (15x6yr jagged): $OUT_DIR/phase2_15x6yr_jagged/"
fi
echo ""

echo "Compare results:"
echo "  # View equity curves"
echo "  cat out/phase1_15x2yr_rectangular/equity_curve.csv"
echo "  cat out/phase1_15x6yr_jagged/equity_curve.csv"
echo ""

echo "  # View metrics"
echo "  cat out/phase1_15x2yr_rectangular/metrics.json"
echo "  cat out/phase1_15x6yr_jagged/metrics.json"
echo ""

echo "  # View diagnostics (if enabled)"
echo "  python3 << 'EOF'"
echo "import pandas as pd"
echo "diag = pd.read_parquet('out/phase1_15x6yr_jagged/diagnostics.parquet')"
echo "print(diag.groupby('instrument')['state'].value_counts())"
echo "EOF"
echo ""
