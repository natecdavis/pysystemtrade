#!/bin/bash
#
# Run all 4 Phase 1 MVP test scenarios for OI overlay + crowding signals
#
# Usage: ./scripts/run_oi_mvp_tests.sh
#
# Tests:
#   1. Baseline       - No OI overlay, no relcarry (current system, Sharpe 0.99)
#   2. Overlay only   - OI overlay enabled, no relcarry (test defensive overlay)
#   3. Crowding only  - No OI overlay, relcarry enabled (test contrarian alpha)
#   4. Combined       - Both OI overlay and relcarry (test full Phase 1)
#
# Expected runtime: ~20 minutes (4 runs × 5 min each)
#

set -e  # Exit on error

# Configuration
DATA="data/dataset_538registry_6yr_jagged.parquet"
OUTDIR="out/oi_mvp"

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo "======================================================================"
echo "OI Regime Overlay + Crowding Signal - Phase 1 MVP Testing"
echo "======================================================================"
echo ""
echo "Dataset: $DATA"
echo "Output:  $OUTDIR"
echo ""

# Test 1: Baseline (no overlay, no crowding)
echo -e "${BLUE}[1/4] Running BASELINE (no overlay, no crowding)...${NC}"
python scripts/run_dynamic_universe_backtest.py \
  --config config/crypto_perps_oi_baseline.yaml \
  --data "$DATA" \
  --outdir "$OUTDIR/baseline"
echo -e "${GREEN}✓ Baseline complete${NC}"
echo ""

# Test 2: Overlay only
echo -e "${BLUE}[2/4] Running OVERLAY ONLY (OI regime scaling)...${NC}"
python scripts/run_dynamic_universe_backtest.py \
  --config config/crypto_perps_oi_overlay_only.yaml \
  --data "$DATA" \
  --outdir "$OUTDIR/overlay_only"
echo -e "${GREEN}✓ Overlay only complete${NC}"
echo ""

# Test 3: Crowding only
echo -e "${BLUE}[3/4] Running CROWDING ONLY (relcarry signals)...${NC}"
python scripts/run_dynamic_universe_backtest.py \
  --config config/crypto_perps_oi_crowding_only.yaml \
  --data "$DATA" \
  --outdir "$OUTDIR/crowding_only"
echo -e "${GREEN}✓ Crowding only complete${NC}"
echo ""

# Test 4: Combined
echo -e "${BLUE}[4/4] Running COMBINED (overlay + crowding)...${NC}"
python scripts/run_dynamic_universe_backtest.py \
  --config config/crypto_perps_oi_test.yaml \
  --data "$DATA" \
  --outdir "$OUTDIR/combined"
echo -e "${GREEN}✓ Combined complete${NC}"
echo ""

echo "======================================================================"
echo -e "${GREEN}All tests complete!${NC}"
echo "======================================================================"
echo ""
echo "Results summary:"
echo "  Baseline:      $OUTDIR/baseline/metrics.txt"
echo "  Overlay only:  $OUTDIR/overlay_only/metrics.txt"
echo "  Crowding only: $OUTDIR/crowding_only/metrics.txt"
echo "  Combined:      $OUTDIR/combined/metrics.txt"
echo ""
echo "Success criteria (from plan):"
echo "  ✓ OI overlay reduces MaxDD by ≥1%"
echo "  ✓ Crowding IC < -0.05 (contrarian working)"
echo "  ✓ Combined Sharpe ≥ 1.00 (+1% minimum)"
echo "  ✓ Correlation(crowding, trend) < 0.3 (orthogonal alpha)"
echo ""
echo "Next steps:"
echo "  1. Compare metrics across all 4 runs"
echo "  2. Check crisis performance (May 2021, Jun 2022, Nov 2022)"
echo "  3. Compute IC for relcarry signals"
echo "  4. If successful, proceed to Phase 2 (true OI data)"
echo ""
