#!/bin/bash
#
# 2×2 Factorial Test Runner
# Tests OI overlay and relcarry effects independently and combined
#
# Runtime: ~40 minutes (4 backtests × 10 min each)
#

set -e  # Exit on error

DATASET="data/dataset_538registry_6yr_jagged.parquet"
OUTDIR="out/factorial_tests"

echo ""
echo "╔════════════════════════════════════════════════════════════════════════════╗"
echo "║                     2×2 FACTORIAL TEST SUITE                               ║"
echo "║                   OI Overlay × relcarry Attribution                        ║"
echo "╚════════════════════════════════════════════════════════════════════════════╝"
echo ""

# Create output directory
mkdir -p "$OUTDIR"

# Helper function to run a single test
run_test() {
    local test_id="$1"
    local test_name="$2"
    local config="$3"
    local overlay_status="$4"
    local relcarry_status="$5"

    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "Test $test_id: $test_name"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  Config:        $config"
    echo "  OI Overlay:    $overlay_status"
    echo "  relcarry:      $relcarry_status"
    echo "  Output:        $OUTDIR/$test_id"
    echo ""

    python scripts/run_dynamic_universe_backtest.py \
        --config "$config" \
        --data "$DATASET" \
        --outdir "$OUTDIR/$test_id"

    echo ""
    echo "✓ Test $test_id complete"
    echo ""
}

# Test A: Pure Baseline (no overlay, no relcarry)
run_test \
    "test_A_pure_baseline" \
    "Pure Baseline" \
    "config/factorial_test_A_pure_baseline.yaml" \
    "OFF" \
    "OFF (0%)"

# Test B: Overlay Only (overlay enabled, no relcarry)
run_test \
    "test_B_overlay_only" \
    "Overlay Only" \
    "config/factorial_test_B_overlay_only.yaml" \
    "ON" \
    "OFF (0%)"

# Test C: relcarry Only (no overlay, relcarry enabled)
run_test \
    "test_C_relcarry_only" \
    "relcarry Only" \
    "config/factorial_test_C_relcarry_only.yaml" \
    "OFF" \
    "ON (6%)"

# Test D: Combined (overlay + relcarry)
run_test \
    "test_D_combined" \
    "Combined (Overlay + relcarry)" \
    "config/factorial_test_D_combined.yaml" \
    "ON" \
    "ON (6%)"

echo ""
echo "╔════════════════════════════════════════════════════════════════════════════╗"
echo "║                    ✓ ALL FACTORIAL TESTS COMPLETE                          ║"
echo "╚════════════════════════════════════════════════════════════════════════════╝"
echo ""
echo "Results saved to: $OUTDIR/"
echo ""
echo "Next step: Run analysis script to compare results"
echo "  python scripts/analyze_factorial_tests.py"
echo ""
