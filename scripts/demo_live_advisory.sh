#!/bin/bash
# Demo script for Live Advisory V0 system
# This demonstrates the workflow using existing test data

set -e  # Exit on error

# Set PYTHONPATH to project root
export PYTHONPATH="$(cd "$(dirname "$0")/.." && pwd):$PYTHONPATH"

echo "================================================================================"
echo "LIVE ADVISORY V0 DEMO"
echo "================================================================================"
echo ""
echo "This demo shows the live advisory workflow using existing test data."
echo "In production, you would:"
echo "  1. Have actual raw data from Binance"
echo "  2. Update your actual positions after executing trades"
echo "  3. Run this monthly (not daily) due to Binance Vision lag"
echo ""
echo "================================================================================"
echo ""

# Configuration
CONFIG="config/crypto_perps_baseline_v1.yaml"
ACTUAL_POSITIONS="live/current_positions.csv"
CURRENT_EQUITY=5000.00
OUTPUT_DIR="out/live_advisory_demo_$(date +%Y%m%d_%H%M%S)"

echo "Step 1: Verify prerequisites"
echo "------------------------------------------------------------"

# Check config exists
if [ ! -f "$CONFIG" ]; then
    echo "ERROR: Config not found: $CONFIG"
    exit 1
fi
echo "✓ Config found: $CONFIG"

# Check actual positions exists
if [ ! -f "$ACTUAL_POSITIONS" ]; then
    echo "ERROR: Actual positions not found: $ACTUAL_POSITIONS"
    echo "  This file contains your current portfolio state."
    echo "  See live/README.md for setup instructions."
    exit 1
fi
echo "✓ Actual positions found: $ACTUAL_POSITIONS"

# Check dataset exists (for demo - skip data update)
DATASET="data/example_crypto_perps_5yr.parquet"
if [ ! -f "$DATASET" ]; then
    echo "ERROR: Example dataset not found: $DATASET"
    echo "  Run 'python scripts/build_example_dataset.py' first"
    exit 1
fi
echo "✓ Example dataset found: $DATASET"

echo ""
echo "Step 2: Run backtest on existing dataset"
echo "------------------------------------------------------------"
echo "NOTE: In production, run_live_advisory.py would:"
echo "  1. Download latest Binance data (monthly batch)"
echo "  2. Rebuild dataset with fresh data"
echo "  3. Run backtest for fresh targets"
echo "For demo, we'll use existing dataset and run backtest only."
echo ""

# Create output directory
mkdir -p "$OUTPUT_DIR/backtest_latest"

# Run backtest on existing dataset
echo "Running backtest..."
python3 systems/crypto_perps/system.py \
    --config "$CONFIG" \
    --data "$DATASET" \
    --outdir "$OUTPUT_DIR/backtest_latest"

if [ $? -ne 0 ]; then
    echo "ERROR: Backtest failed"
    exit 1
fi
echo "✓ Backtest completed"

# Extract as_of_date from positions.csv
AS_OF_DATE=$(python3 -c "
import pandas as pd
df = pd.read_csv('$OUTPUT_DIR/backtest_latest/positions.csv', index_col=0, parse_dates=True)
print(df.index[-1].strftime('%Y-%m-%d'))
")

echo "  As-of date: $AS_OF_DATE"

echo ""
echo "Step 3: Generate trade plan"
echo "------------------------------------------------------------"

python3 scripts/generate_trade_plan.py \
    --backtest-dir "$OUTPUT_DIR/backtest_latest" \
    --actual-positions "$ACTUAL_POSITIONS" \
    --current-equity "$CURRENT_EQUITY" \
    --as-of-date "$AS_OF_DATE" \
    --output-dir "$OUTPUT_DIR" \
    --config "$CONFIG"

if [ $? -ne 0 ]; then
    echo "ERROR: Trade plan generation failed"
    exit 1
fi
echo "✓ Trade plan generated"

echo ""
echo "Step 4: Generate advisory report"
echo "------------------------------------------------------------"

python3 reports/advisory_report.py \
    --advisory-dir "$OUTPUT_DIR" \
    --output "$OUTPUT_DIR/advisory_report.txt"

if [ $? -ne 0 ]; then
    echo "WARNING: Report generation failed (optional)"
else
    echo "✓ Advisory report generated"
fi

echo ""
echo "================================================================================"
echo "DEMO COMPLETE"
echo "================================================================================"
echo ""
echo "Output directory: $OUTPUT_DIR"
echo ""
echo "Generated files:"
ls -lh "$OUTPUT_DIR"/*.csv "$OUTPUT_DIR"/*.json "$OUTPUT_DIR"/*.txt 2>/dev/null | awk '{print "  - " $9 " (" $5 ")"}'
echo ""
echo "View trade plan:"
echo "  cat $OUTPUT_DIR/trade_plan_$AS_OF_DATE.csv"
echo ""
echo "View advisory report:"
echo "  cat $OUTPUT_DIR/advisory_report.txt"
echo ""
echo "View sanity checks:"
echo "  cat $OUTPUT_DIR/sanity_checks_$AS_OF_DATE.json | jq ."
echo ""
echo "================================================================================"
echo "NEXT STEPS (for production use):"
echo "================================================================================"
echo ""
echo "1. Review the trade plan carefully"
echo "2. Verify live prices on exchange before executing"
echo "3. Execute trades manually on exchange"
echo "4. Update live/current_positions.csv with actual fills"
echo "5. Update live/current_equity.txt with actual equity from exchange"
echo "6. Commit changes to git for audit trail"
echo ""
echo "See live/README.md and LIVE_OPS_V0_IMPLEMENTATION.md for full documentation."
echo ""
