# Config Schema Reference

All configuration parameters with their types, defaults, and meanings.

## system

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `capital` | float | Yes | N/A | Starting capital ($) |
| `vol_target_ann` | float | Yes | N/A | Target annualized volatility (0.25 = 25%) |
| `gross_leverage_cap` | float | Yes | N/A | Maximum gross leverage (2.0 = 2x) |
| `idm_cap` | float | Yes | N/A | Maximum IDM multiplier (2.5 = 2.5x) |
| `min_position_frac` | float | Yes | N/A | Minimum position as fraction of capital (0.03 = 3%) |
| `allow_jagged` | bool | No | false | Allow instruments with different date ranges |

## universe

**IMPORTANT:** `layer_a_instruments` is the **candidate pool**, not the active trading universe. In Phase 2, the system dynamically selects from this pool based on ADV and other criteria.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `layer_a_instruments` | list[str] | Yes | N/A | **Candidate instrument pool** (NOT active membership). Phase 2 selects subset dynamically. Phase 1 uses all instruments. |
| `review_freq` | str\|null | No | null | Review frequency ('BMS' = monthly Phase 2, null = Phase 1 static) |
| `daily_min_adv_notional` | float | No | 10000000.0 | Daily eligibility threshold ($10M) |
| `min_adv_notional` | float | No | 50000000.0 | Layer-A review threshold ($50M) - instruments must exceed this to be selected |
| `min_history_days` | int | No | 365 | Min days of history for Layer-A entry |
| `data_gap_days` | int | No | 2 | Max consecutive missing days before exit |
| `forced_exit_days` | int | No | 5 | Days to flatten after ineligibility |
| `banned_instruments` | list[str] | No | [] | Manually banned instruments |

### Review Frequency Options

- `null`: Phase 1 static universe (all instruments in candidate pool)
- `'BMS'`: Business Month Start (first business day of month, recommended)
- `'MS'`: Month Start (first calendar day)
- `'M'`: Month End (last day of month)
- Any pandas offset alias supported

## rules

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `ewmac_pairs` | list[list[int]] | Yes | N/A | EWMAC fast/slow pairs, e.g., [[8, 32], [16, 64]] |
| `ewmac_vol_days` | int | Yes | N/A | Volatility lookback for EWMAC (days) |
| `carry_fast_halflife` | int | Yes | N/A | Fast EWMA half-life for carry (days) |
| `carry_slow_halflife` | int | Yes | N/A | Slow EWMA half-life for carry (days) |

## forecasts

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `target_abs` | float | Yes | N/A | Target mean absolute forecast (10.0 standard) |
| `cap` | float | Yes | N/A | Forecast cap (±20.0 standard) |
| `use_relative_momentum` | bool | No | false | Enable relative momentum rule (Phase 2) |
| `relmom.horizon` | int | No | 20 | Momentum lookback period (days) |
| `relmom.ewma_span` | int | No | 60 | Smoothing span for momentum (days) |

### Rule Weights

Equal weights used by default. To specify custom weights:

```yaml
forecasts:
  rule_weights:
    ewmac_8_32: 0.25
    ewmac_16_64: 0.25
    carry_funding: 0.5
```

## sizing

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `vol_days` | int | Yes | N/A | Volatility lookback for position sizing (days) |

## constraints

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `correlation_span` | int | Yes | N/A | EWMA correlation span (60 days recommended for crypto) |
| `correlation_min_periods` | int | Yes | N/A | Minimum periods for correlation (20 recommended) |

## execution

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `buffer_frac` | float | Yes | N/A | Trading buffer (0.1 = 10% of position volatility) |

## costs

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `spread_estimate` | float | Yes | N/A | Bid-ask spread estimate (0.0005 = 5 bps) |
| `taker_fee_frac` | float | Yes | N/A | Taker fee (0.0004 = 4 bps) |

**Note:** These are applied to trade notional: `cost = abs(trade) × (spread + fee)`

## output

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `equity_curve_file` | str | Yes | N/A | Output filename for equity curve CSV |
| `positions_file` | str | Yes | N/A | Output filename for positions CSV |
| `pnl_breakdown_file` | str | No | N/A | Output filename for PnL breakdown (optional) |
| `write_diagnostics` | bool | No | false | Write detailed diagnostics parquet |
| `diagnostics_file` | str | No | 'diagnostics.parquet' | Diagnostics output filename |

## diagnostics

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `enabled` | bool | No | false | Enable diagnostics collection (writes detailed daily data) |

**Note:** If enabled, produces:
- `diagnostics.parquet`: Full diagnostics
- `idm_history.csv`: IDM time series
- `layer_a_membership.csv`: Layer-A membership history (Phase 2 only)

---

## Complete Example

```yaml
system:
  capital: 5000.0
  vol_target_ann: 0.25
  gross_leverage_cap: 2.0
  idm_cap: 2.5
  min_position_frac: 0.03
  allow_jagged: false

universe:
  # NOTE: This is the CANDIDATE POOL, not active membership
  # Phase 2 dynamically selects subset based on ADV
  layer_a_instruments:
    - BTCUSDT_PERP
    - ETHUSDT_PERP
    - BNBUSDT_PERP

  review_freq: BMS  # Phase 2: monthly reviews (null = Phase 1 static)
  daily_min_adv_notional: 10000000.0
  min_adv_notional: 50000000.0
  min_history_days: 365
  data_gap_days: 2
  forced_exit_days: 5
  banned_instruments: []

rules:
  ewmac_pairs:
    - [8, 32]
    - [16, 64]
  ewmac_vol_days: 35
  carry_fast_halflife: 3
  carry_slow_halflife: 30

forecasts:
  target_abs: 10.0
  cap: 20.0
  use_relative_momentum: false

sizing:
  vol_days: 35

constraints:
  correlation_span: 60
  correlation_min_periods: 20

execution:
  buffer_frac: 0.1

costs:
  spread_estimate: 0.0005
  taker_fee_frac: 0.0004

output:
  equity_curve_file: "equity_curve.csv"
  positions_file: "positions.csv"
  pnl_breakdown_file: "pnl_breakdown.csv"
  write_diagnostics: true
  diagnostics_file: "diagnostics.parquet"

diagnostics:
  enabled: true
```

---

## Validation

Configs are validated at startup by `systems/crypto_perps/config_validator.py`.

Invalid configs will error with specific messages:
- Missing required parameters
- Invalid types or value ranges
- Inconsistent settings

Use `get_config_defaults()` to see implicit defaults (for documentation only - all production configs should be explicit).
