#!/usr/bin/env python3
"""
Validate config for 541-perp registry integration.

Checks:
- Registry consistency (auto_discover vs registry file existence)
- layer_a_instruments presence (production safety)
- Top-K parameter consistency
- Tradable instruments subset of registry
- Dynamic universe configuration

Usage:
    python scripts/validate_config.py \
        --config config/crypto_perps_dynamic_universe_top30.yaml \
        --env-root envs/dev

    python scripts/validate_config.py \
        --config config/crypto_perps_dynamic_universe_top30.yaml \
        --env dev
"""

import argparse
import sys
import json
import yaml
from pathlib import Path
from typing import List, Tuple

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))


def validate_registry_config(config_path: Path, env_root: Path) -> Tuple[List[str], List[str]]:
    """
    Validate config for registry mode.

    Args:
        config_path: Path to config YAML file
        env_root: Environment root directory

    Returns:
        (errors, warnings) - Lists of error/warning messages
    """
    with open(config_path) as f:
        config = yaml.safe_load(f)

    errors = []
    warnings = []

    # Check auto_discover consistency
    auto_discover = config.get('data_acquisition', {}).get('auto_discover', False)

    if auto_discover:
        # Verify registry exists
        registry_path = env_root / 'data/raw/metadata/discovered_candidate_instruments.json'
        if not registry_path.exists():
            errors.append(
                f"auto_discover=true but registry not found: {registry_path}\n"
                f"  Run: python scripts/refresh_binance_market_registry.py --env-root {env_root}"
            )
        else:
            # Verify registry is valid JSON
            try:
                with open(registry_path) as f:
                    registry_data = json.load(f)

                if 'candidate_instruments' not in registry_data:
                    errors.append(f"Registry missing 'candidate_instruments' key: {registry_path}")
                elif not registry_data['candidate_instruments']:
                    errors.append(f"Registry has empty candidate_instruments list: {registry_path}")
            except json.JSONDecodeError as e:
                errors.append(f"Registry is not valid JSON: {registry_path}\n  Error: {e}")

        # Verify layer_a_instruments exists (safety)
        layer_a = config.get('universe', {}).get('layer_a_instruments')
        if not layer_a:
            errors.append(
                "universe.layer_a_instruments is empty (production safety violation)\n"
                "  layer_a_instruments defines the MAX tradable set for production safety"
            )
        elif len(layer_a) < 5:
            warnings.append(
                f"layer_a_instruments only has {len(layer_a)} instruments (seems low)\n"
                f"  Typical production configs have 30 instruments in layer_a"
            )

    # Check that tradable list is subset of registry (if registry exists and auto_discover enabled)
    if auto_discover:
        registry_path = env_root / 'data/raw/metadata/discovered_candidate_instruments.json'
        if registry_path.exists():
            try:
                with open(registry_path) as f:
                    registry_data = json.load(f)

                registry_candidates = set(registry_data.get('candidate_instruments', []))
                layer_a = config.get('universe', {}).get('layer_a_instruments', [])
                tradable = set(layer_a)

                non_existent = tradable - registry_candidates
                if non_existent:
                    warnings.append(
                        f"{len(non_existent)} layer_a instruments not in registry (may be delisted):\n"
                        f"  {sorted(non_existent)}\n"
                        f"  Consider removing from layer_a or updating registry"
                    )
            except Exception:
                pass  # Already handled above

    # Validate top-K parameters (if dynamic universe enabled)
    dynamic_universe = config.get('dynamic_universe', {})
    top_k = dynamic_universe.get('top_k')

    if top_k:
        # Check that layer_a is large enough
        layer_a = config.get('universe', {}).get('layer_a_instruments', [])
        layer_a_count = len(layer_a)

        if top_k > layer_a_count:
            errors.append(
                f"top_k ({top_k}) > layer_a count ({layer_a_count})\n"
                f"  layer_a_instruments defines MAX tradable set\n"
                f"  Expand layer_a to at least {top_k} instruments"
            )

        # Check entry/exit buffers
        entry_buffer = dynamic_universe.get('entry_buffer', 5)
        exit_buffer = dynamic_universe.get('exit_buffer', 10)

        if entry_buffer < 0 or exit_buffer < 0:
            errors.append(
                f"Buffers must be non-negative: entry_buffer={entry_buffer}, exit_buffer={exit_buffer}"
            )

        if entry_buffer >= top_k:
            warnings.append(
                f"entry_buffer ({entry_buffer}) >= top_k ({top_k})\n"
                f"  Entry threshold would be <= 0, preventing any entries\n"
                f"  Recommended: entry_buffer < top_k / 2"
            )

        if exit_buffer > top_k:
            warnings.append(
                f"exit_buffer ({exit_buffer}) > top_k ({top_k})\n"
                f"  Exit threshold would be > 2*top_k, very permissive\n"
                f"  Consider reducing exit_buffer for tighter control"
            )

        # Check ADV window
        adv_window = dynamic_universe.get('adv_window', 30)
        if adv_window < 7:
            warnings.append(
                f"adv_window ({adv_window} days) is very short\n"
                f"  ADV metric may be unstable with <7 days window"
            )

        # Check min_history_days
        min_history_days = dynamic_universe.get('min_history_days', 365)
        if min_history_days < 90:
            warnings.append(
                f"min_history_days ({min_history_days}) is quite low\n"
                f"  Instruments with <90 days history may have unreliable ADV metrics"
            )

    # Check cost filter parameters
    max_sr_cost_per_trade = dynamic_universe.get('max_sr_cost_per_trade')
    max_sr_cost_annual = dynamic_universe.get('max_sr_cost_annual')

    if max_sr_cost_per_trade is not None:
        if max_sr_cost_per_trade <= 0 or max_sr_cost_per_trade > 0.05:
            warnings.append(
                f"max_sr_cost_per_trade ({max_sr_cost_per_trade}) outside typical range (0.005 - 0.015)\n"
                f"  Carver recommendation: <= 0.01 SR per trade"
            )

    if max_sr_cost_annual is not None:
        if max_sr_cost_annual <= 0 or max_sr_cost_annual > 0.30:
            warnings.append(
                f"max_sr_cost_annual ({max_sr_cost_annual}) outside typical range (0.10 - 0.20)\n"
                f"  Carver recommendation: <= 0.13 SR per year"
            )

    return errors, warnings


def main():
    parser = argparse.ArgumentParser(
        description='Validate config for 541-perp registry integration',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Validate production config
  %(prog)s --config config/crypto_perps_dynamic_universe_top30.yaml --env prod

  # Validate dev config with custom env-root
  %(prog)s --config config/test_auto_discover.yaml --env-root envs/dev

Exit codes:
  0 - Validation passed (may have warnings)
  1 - Validation failed (errors found)
        """
    )

    parser.add_argument(
        '--config',
        type=Path,
        required=True,
        help='Path to config YAML file'
    )
    parser.add_argument(
        '--env',
        help='Environment name (uses envs/<env>/ structure). Examples: prod, dev, paper'
    )
    parser.add_argument(
        '--env-root',
        type=Path,
        help='Custom environment root (overrides --env)'
    )

    args = parser.parse_args()

    # Resolve environment root
    if args.env_root:
        env_root = args.env_root
    elif args.env:
        env_root = Path(f'envs/{args.env}')
    else:
        print("ERROR: Must specify either --env or --env-root", file=sys.stderr)
        sys.exit(1)

    # Validate config exists
    if not args.config.exists():
        print(f"ERROR: Config file not found: {args.config}", file=sys.stderr)
        sys.exit(1)

    # Validate environment root exists
    if not env_root.exists():
        print(f"ERROR: Environment root not found: {env_root}", file=sys.stderr)
        print(f"  Create with: mkdir -p {env_root}/data/raw/metadata", file=sys.stderr)
        sys.exit(1)

    print("=" * 70)
    print(f"VALIDATING CONFIG: {args.config}")
    print(f"Environment root: {env_root}")
    print("=" * 70)
    print()

    # Run validation
    errors, warnings = validate_registry_config(args.config, env_root)

    # Print results
    if errors:
        print("❌ ERRORS FOUND:")
        print()
        for i, error in enumerate(errors, 1):
            print(f"{i}. {error}")
            print()

    if warnings:
        print("⚠️  WARNINGS:")
        print()
        for i, warning in enumerate(warnings, 1):
            print(f"{i}. {warning}")
            print()

    # Summary
    print("=" * 70)
    if errors:
        print(f"❌ VALIDATION FAILED: {len(errors)} error(s), {len(warnings)} warning(s)")
        print("=" * 70)
        sys.exit(1)
    elif warnings:
        print(f"⚠️  VALIDATION PASSED WITH WARNINGS: {len(warnings)} warning(s)")
        print("=" * 70)
        print()
        print("Review warnings above before using in production")
        sys.exit(0)
    else:
        print("✅ VALIDATION PASSED: No errors or warnings")
        print("=" * 70)
        sys.exit(0)


if __name__ == '__main__':
    main()
