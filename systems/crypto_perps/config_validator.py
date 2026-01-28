"""Config validation for crypto perps system"""


def validate_config(config: dict) -> list:
    """
    Validate config has all required parameters

    Returns:
        List of validation errors (empty if valid)
    """
    errors = []

    # Required sections
    required_sections = [
        'system', 'universe', 'rules', 'forecasts',
        'sizing', 'execution', 'constraints', 'output'
    ]

    for section in required_sections:
        if section not in config:
            errors.append(f"Missing required section: {section}")
            continue

        # Validate section content
        section_errors = _validate_section(section, config[section])
        errors.extend(section_errors)

    return errors


def _validate_section(section: str, params: dict) -> list:
    """Validate individual section"""
    errors = []

    required_params = {
        'system': ['capital', 'vol_target_ann', 'gross_leverage_cap', 'idm_cap', 'min_position_frac'],
        'universe': ['layer_a_instruments'],  # review_freq optional (None = Phase 1)
        'rules': ['ewmac_pairs', 'ewmac_vol_days', 'carry_fast_halflife', 'carry_slow_halflife'],
        'forecasts': ['target_abs', 'cap'],
        'sizing': ['vol_days'],
        'execution': ['buffer_frac'],
        'constraints': ['correlation_span', 'correlation_min_periods'],
        'output': ['equity_curve_file', 'positions_file']
    }

    for param in required_params.get(section, []):
        if param not in params:
            errors.append(f"{section}.{param} is required but missing")

    # Type validations
    if section == 'system':
        if not isinstance(params.get('capital'), (int, float)) or params.get('capital', 0) <= 0:
            errors.append("system.capital must be positive number")
        if not isinstance(params.get('vol_target_ann'), (int, float)) or not 0 < params.get('vol_target_ann', 0) < 1:
            errors.append("system.vol_target_ann must be between 0 and 1")

    if section == 'universe':
        if not isinstance(params.get('layer_a_instruments'), list) or len(params.get('layer_a_instruments', [])) == 0:
            errors.append("universe.layer_a_instruments must be non-empty list")

    return errors


def get_config_defaults() -> dict:
    """
    Return dict of all implicit defaults in system

    Use this for documentation only - configs should be explicit
    """
    return {
        'universe': {
            'review_freq': None,  # None = Phase 1 (static), 'BMS' = Phase 2 (dynamic)
            'forced_exit_days': 5,
            'min_adv_notional': 50000000.0,  # $50M
            'min_history_days': 365,
            'data_gap_days': 2,
            'banned_instruments': []
        },
        'forecasts': {
            'use_relative_momentum': False,
            'relmom': {
                'horizon': 20,
                'ewma_span': 60
            }
        },
        'system': {
            'allow_jagged': False
        },
        'diagnostics': {
            'enabled': False
        }
    }
