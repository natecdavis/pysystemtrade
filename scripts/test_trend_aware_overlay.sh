#!/bin/bash
#
# Test trend-aware OI overlay vs standard overlay
#
# Compares:
#   - Standard overlay (bidirectional, Phase 1)
#   - Trend-aware overlay (Phase 1.5 fix)
#
# Expected improvement from trend-aware mode:
#   - Better crisis performance (avoid whipsaw on bounces)
#   - Keep profitable trend-aligned positions
#   - Only reduce counter-trend positions
#
# Usage: ./scripts/test_trend_aware_overlay.sh
#

set -e  # Exit on error

# Configuration
DATA="data/dataset_538registry_6yr_jagged.parquet"
OUTDIR="out/oi_trend_aware"

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo "======================================================================"
echo "Trend-Aware OI Overlay Testing (Phase 1.5)"
echo "======================================================================"
echo ""
echo "Dataset: $DATA"
echo "Output:  $OUTDIR"
echo ""
echo "This will test the trend-aware overlay modification that addresses"
echo "the whipsaw problem identified in crash diagnosis."
echo ""
echo "Expected improvements vs standard overlay:"
echo "  - Better May 2021 crash performance (avoid whipsaw)"
echo "  - Better Nov 2022 FTX performance (keep profitable shorts)"
echo "  - Similar or better overall Sharpe"
echo ""

# Test 1: Baseline (for reference, may skip if already run)
if [ ! -d "$OUTDIR/baseline" ]; then
    echo -e "${BLUE}[1/3] Running BASELINE (no overlay)...${NC}"
    python scripts/run_dynamic_universe_backtest.py \
      --config config/crypto_perps_oi_baseline.yaml \
      --data "$DATA" \
      --outdir "$OUTDIR/baseline"
    echo -e "${GREEN}✓ Baseline complete${NC}"
    echo ""
else
    echo -e "${YELLOW}[1/3] BASELINE already exists, skipping${NC}"
    echo ""
fi

# Test 2: Standard overlay (bidirectional)
if [ ! -d "$OUTDIR/standard" ]; then
    echo -e "${BLUE}[2/3] Running STANDARD OVERLAY (bidirectional)...${NC}"
    python scripts/run_dynamic_universe_backtest.py \
      --config config/crypto_perps_oi_test.yaml \
      --data "$DATA" \
      --outdir "$OUTDIR/standard"
    echo -e "${GREEN}✓ Standard overlay complete${NC}"
    echo ""
else
    echo -e "${YELLOW}[2/3] STANDARD OVERLAY already exists, skipping${NC}"
    echo ""
fi

# Test 3: Trend-aware overlay (Phase 1.5 fix)
echo -e "${BLUE}[3/3] Running TREND-AWARE OVERLAY (Phase 1.5)...${NC}"
python scripts/run_dynamic_universe_backtest.py \
  --config config/crypto_perps_oi_trend_aware.yaml \
  --data "$DATA" \
  --outdir "$OUTDIR/trend_aware"
echo -e "${GREEN}✓ Trend-aware overlay complete${NC}"
echo ""

echo "======================================================================"
echo -e "${GREEN}All tests complete!${NC}"
echo "======================================================================"
echo ""
echo "Results summary:"
echo "  Baseline:      $OUTDIR/baseline/metrics.txt"
echo "  Standard:      $OUTDIR/standard/metrics.txt"
echo "  Trend-aware:   $OUTDIR/trend_aware/metrics.txt"
echo ""
echo "Key metrics to compare:"
echo "  1. Overall Sharpe (trend-aware should be ≥ standard)"
echo "  2. May 2021 crash returns (trend-aware should be better)"
echo "  3. Nov 2022 FTX returns (trend-aware should be better)"
echo "  4. Turnover (trend-aware might be lower)"
echo ""
echo "Success criteria:"
echo "  ✓ Trend-aware Sharpe ≥ Standard Sharpe"
echo "  ✓ May 2021: Trend-aware > Baseline (avoid -2.7% underperformance)"
echo "  ✓ Nov 2022: Trend-aware > Baseline (avoid -2.3% underperformance)"
echo ""
echo "Next steps:"
echo "  1. Compare metrics.json files across all 3 runs"
echo "  2. Extract crisis period performance (create analysis script)"
echo "  3. If successful, update production config with trend_aware: true"
echo ""
