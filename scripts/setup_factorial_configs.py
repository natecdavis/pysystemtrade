#!/usr/bin/env python
"""
Setup script for 2×2 factorial test configs.

Creates 4 test configurations:
- A: Pure baseline (no overlay, no relcarry)
- B: Overlay only (overlay enabled, no relcarry)
- C: relcarry only (no overlay, relcarry enabled)
- D: Combined (overlay + relcarry)
"""

import yaml
from pathlib import Path

# Base config to copy from
BASE_CONFIG = "config/crypto_perps_full_rules.yaml"

# relcarry rule definitions to insert
RELCARRY_RULES = {
    'relcarry_30': {
        'function': 'systems.crypto_perps.rules.rule_library.relcarry',
        'data': [
            'data.get_funding_rate',
            'data.get_cross_sectional_median_funding'
        ],
        'other_args': {'smooth_days': 30}
    },
    'relcarry_60': {
        'function': 'systems.crypto_perps.rules.rule_library.relcarry',
        'data': [
            'data.get_funding_rate',
            'data.get_cross_sectional_median_funding'
        ],
        'other_args': {'smooth_days': 60}
    },
    'relcarry_125': {
        'function': 'systems.crypto_perps.rules.rule_library.relcarry',
        'data': [
            'data.get_funding_rate',
            'data.get_cross_sectional_median_funding'
        ],
        'other_args': {'smooth_days': 125}
    },
}

# relcarry weights (6% total: 2% each × 3 rules)
RELCARRY_WEIGHTS = {
    'relcarry_30': 0.02,
    'relcarry_60': 0.02,
    'relcarry_125': 0.02,
}


def load_config(path):
    """Load YAML config."""
    with open(path) as f:
        return yaml.safe_load(f)


def save_config(config, path):
    """Save YAML config."""
    with open(path, 'w') as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False, width=120)


def create_test_A_pure_baseline():
    """A: No overlay, no relcarry (current production state minus overlay)."""
    config = load_config(BASE_CONFIG)

    # Disable OI overlay
    config['use_oi_overlay'] = False

    # Ensure relcarry not in forecast_weights (already should be absent)
    for rule in RELCARRY_WEIGHTS:
        if rule in config.get('forecast_weights', {}):
            del config['forecast_weights'][rule]

    # Save
    save_config(config, 'config/factorial_test_A_pure_baseline.yaml')
    print("✓ Created Test A: Pure Baseline (no overlay, no relcarry)")


def create_test_B_overlay_only():
    """B: Overlay enabled, no relcarry."""
    config = load_config(BASE_CONFIG)

    # Enable OI overlay
    config['use_oi_overlay'] = True

    # Ensure relcarry not in forecast_weights
    for rule in RELCARRY_WEIGHTS:
        if rule in config.get('forecast_weights', {}):
            del config['forecast_weights'][rule]

    # Save
    save_config(config, 'config/factorial_test_B_overlay_only.yaml')
    print("✓ Created Test B: Overlay Only (overlay enabled, no relcarry)")


def create_test_C_relcarry_only():
    """C: No overlay, relcarry enabled."""
    config = load_config(BASE_CONFIG)

    # Disable OI overlay
    config['use_oi_overlay'] = False

    # Add relcarry rule definitions
    if 'trading_rules' not in config:
        config['trading_rules'] = {}

    for rule_name, rule_def in RELCARRY_RULES.items():
        config['trading_rules'][rule_name] = rule_def

    # Add relcarry weights
    if 'forecast_weights' not in config:
        config['forecast_weights'] = {}

    for rule_name, weight in RELCARRY_WEIGHTS.items():
        config['forecast_weights'][rule_name] = weight

    # Save
    save_config(config, 'config/factorial_test_C_relcarry_only.yaml')
    print("✓ Created Test C: relcarry Only (no overlay, relcarry 6%)")


def create_test_D_combined():
    """D: Overlay + relcarry."""
    config = load_config(BASE_CONFIG)

    # Enable OI overlay
    config['use_oi_overlay'] = True

    # Add relcarry rule definitions
    if 'trading_rules' not in config:
        config['trading_rules'] = {}

    for rule_name, rule_def in RELCARRY_RULES.items():
        config['trading_rules'][rule_name] = rule_def

    # Add relcarry weights
    if 'forecast_weights' not in config:
        config['forecast_weights'] = {}

    for rule_name, weight in RELCARRY_WEIGHTS.items():
        config['forecast_weights'][rule_name] = weight

    # Save
    save_config(config, 'config/factorial_test_D_combined.yaml')
    print("✓ Created Test D: Combined (overlay + relcarry 6%)")


def main():
    """Create all 4 factorial test configs."""
    print("\n2×2 Factorial Test Configuration Setup")
    print("=" * 60)
    print()

    create_test_A_pure_baseline()
    create_test_B_overlay_only()
    create_test_C_relcarry_only()
    create_test_D_combined()

    print()
    print("=" * 60)
    print("✓ All 4 configs created successfully")
    print()
    print("Test Matrix:")
    print("  A: Pure Baseline    | OI Overlay: OFF | relcarry: OFF (0%)")
    print("  B: Overlay Only     | OI Overlay: ON  | relcarry: OFF (0%)")
    print("  C: relcarry Only    | OI Overlay: OFF | relcarry: ON  (6%)")
    print("  D: Combined         | OI Overlay: ON  | relcarry: ON  (6%)")
    print()


if __name__ == "__main__":
    main()
