#!/bin/bash
#
# Environment Setup Helper for Dev/Prod Separation
#
# Creates environment directory structure with proper config handling:
# - Dev: symlinked config (fast iteration)
# - Prod: copied config snapshot (pinned, edit intentionally only)
# - Custom: accepts arbitrary environment names
#
# Usage:
#   ./scripts/setup_environments.sh              # Creates dev + prod
#   ./scripts/setup_environments.sh paper exp1   # Creates custom environments

set -e

PROJECT_ROOT=$(pwd)

# Default: create dev and prod
ENVIRONMENTS=("$@")
if [ ${#ENVIRONMENTS[@]} -eq 0 ]; then
    ENVIRONMENTS=("dev" "prod")
fi

echo "Setting up environments: ${ENVIRONMENTS[@]}"
echo ""

for env in "${ENVIRONMENTS[@]}"; do
    echo "Creating envs/$env..."
    mkdir -p "envs/$env/live"
    mkdir -p "envs/$env/data/raw/binance/klines"
    mkdir -p "envs/$env/data/raw/binance/fundingRate"
    mkdir -p "envs/$env/data/raw/binance/api_cache"
    mkdir -p "envs/$env/data/raw/metadata"
    mkdir -p "envs/$env/out"

    # Config handling: dev gets symlink (fast iteration), others get copy (pinned)
    if [ "$env" == "dev" ]; then
        # Symlink for dev (fast iteration)
        if [ -e "envs/$env/config" ]; then
            rm -rf "envs/$env/config"
        fi
        ln -sf "../../config" "envs/$env/config"
        echo "  ✓ Config: symlinked to ../../config (fast iteration)"
    else
        # Copy for prod and custom environments (pinned)
        if [ -d "config" ]; then
            cp -r "config" "envs/$env/config"
            echo "  ✓ Config: copied snapshot (pinned)"
            if [ "$env" == "prod" ]; then
                echo "  ⚠️  WARNING: Edit envs/prod/config only intentionally. This is your production config."
            fi
        else
            echo "  ⚠ WARNING: config/ directory not found, skipping config setup"
        fi
    fi

    # Initialize dev with test data
    if [ "$env" == "dev" ]; then
        if [ ! -f "envs/$env/live/current_positions.csv" ]; then
            echo "instrument,contracts,mark_price_usd,notional_usd,timestamp,notes" > "envs/$env/live/current_positions.csv"
        fi
        if [ ! -f "envs/$env/live/current_equity.txt" ]; then
            echo "5000.0" > "envs/$env/live/current_equity.txt"
        fi
        echo "  ✓ Initialized dev test data"
    fi

    echo ""
done

echo "✓ Environment structure created"
echo ""
echo "Directory structure:"
ls -la envs/
echo ""

echo "Next steps:"
echo ""
echo "1. Copy production state (if migrating from single environment):"
echo "   cp live/* envs/prod/live/"
echo "   cp data/raw/binance/* envs/prod/data/raw/binance/ -r"
echo ""
echo "2. Test dev environment:"
echo "   python scripts/doctor_live_ops.py --env dev \\"
echo "       --config config/crypto_perps_baseline_v1.yaml \\"
echo "       --actual-positions envs/dev/live/current_positions.csv \\"
echo "       --current-equity-file envs/dev/live/current_equity.txt \\"
echo "       --cadence daily"
echo ""
echo "3. Test prod environment:"
echo "   python scripts/doctor_live_ops.py --env prod \\"
echo "       --config config/crypto_perps_baseline_v1.yaml \\"
echo "       --actual-positions envs/prod/live/current_positions.csv \\"
echo "       --current-equity-file envs/prod/live/current_equity.txt \\"
echo "       --cadence daily"
echo ""
echo "4. Run in dev (safe, won't touch prod):"
echo "   python scripts/dry_run_v1.py --env dev \\"
echo "       --mode recent-tail \\"
echo "       --instruments BTCUSDT_PERP ETHUSDT_PERP \\"
echo "       --tail-days 30 \\"
echo "       --current-equity 5000.0 \\"
echo "       --output-dir envs/dev/out/dry_run_$(date +%Y%m%d)"
echo ""
echo "5. Run in prod (nightly):"
echo "   python scripts/run_live_advisory.py --env prod \\"
echo "       --config config/crypto_perps_baseline_v1.yaml \\"
echo "       --actual-positions envs/prod/live/current_positions.csv \\"
echo "       --current-equity \$(cat envs/prod/live/current_equity.txt) \\"
echo "       --output-dir envs/prod/out/live_$(date +%Y%m%d) \\"
echo "       --cadence daily"
echo ""
