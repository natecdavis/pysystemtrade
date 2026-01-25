# Crypto Perpetual Futures Trading System — Agent‑Ready Design & Implementation Spec

> **Purpose**: This document is written to be handed directly to a coding agent operating inside a fresh fork of Robert Carver’s `pysystemtrade` repository. It is explicit about *where code lives*, *what data looks like*, *what invariants must hold*, and *what “done” means*.

---

## 0. Guiding Principles (Locked)

- Follow Robert Carver’s *systematic trading* philosophy wherever applicable.
- Deterministic, rules‑based, no discretionary overrides at runtime.
- Daily‑frequency system (no intraday logic).
- Capital‑scaled, volatility‑targeted, cost‑aware.
- Explicit constraints > implicit assumptions.

---

## 1. Repo Integration Plan (NEW — REQUIRED FOR AGENT)

### 1.1 Target Repo Structure

All new logic lives under a dedicated system namespace to avoid touching core PST internals unless strictly necessary.

```
pysystemtrade/
├── systems/
│   └── crypto_perps/
│       ├── system.py              # Orchestrates daily system loop
│       ├── universe.py            # Layer A / B universe + state machine
│       ├── execution.py           # Buffers, costs, delta→trade
│       ├── accounting.py          # Price PnL, funding PnL, cost attribution
│       ├── rules/
│       │   ├── ewmac.py            # Trend rules (Carver‑style)
│       │   ├── carry_funding.py    # Funding‑rate carry signal
│       │   └── relmom.py           # Cross‑sectional relative momentum
│       └── __init__.py
├── config/
│   └── crypto_perps.yaml           # Canonical system configuration
├── sysdata/
│   └── crypto/
│       └── prices.py               # Adapter → PST price series objects
├── tests/
│   └── test_crypto_perps_smoke.py  # Acceptance + invariants tests
└── data/
    └── example_crypto_perps.parquet
```

**Constraint**: The agent must not modify PST core abstractions unless explicitly noted.

---

## 2. Data Contracts (NEW — CRITICAL)

### 2.1 Instrument Identifier

- `instrument_code`: string
- Example: `BTCUSDT_PERP`, `ETHUSDT_PERP`

### 2.2 Price & Metadata Table (Daily)

| Column | Type | Units | Notes |
|------|------|------|------|
| date | datetime (UTC) | — | Daily close timestamp |
| instrument | str | — | Instrument code |
| close | float | USD | Settlement price |
| funding_rate | float | decimal | Daily funding; + = receive |
| adv_notional | float | USD/day | Avg daily notional volume |
| spread_frac | float | fraction | Half‑spread as fraction |
| taker_fee_frac | float | fraction | Per‑trade fee |

**Alignment rules**:
- `funding_rate[t]` applies to position held from close(t‑1) → close(t).
- Missing `close` ⇒ instrument is **ineligible** that day.
- Missing `funding_rate` ⇒ funding PnL = 0 (explicit, not inferred).

### 2.3 Example Rows

```
2025‑01‑03, BTCUSDT_PERP, 42750.0, 0.00012, 1.8e9, 0.00025, 0.0004
2025‑01‑04, BTCUSDT_PERP, 43210.0,‑0.00005, 1.7e9, 0.00026, 0.0004
```

---

## 3. Universe Definition & State Machine (Refined)

### 3.1 Layer A — Core Membership (Monthly Review)

Eligibility criteria (checked at review time):

- Exchange supported
- ADV ≥ `min_adv_notional`
- Data coverage ≥ `min_history_days`

Layer A membership is **frozen between reviews**.

### 3.2 Layer B — Daily Eligibility Filter

Daily checks:

- Data present for `close`
- ADV ≥ `daily_min_adv_notional`
- Instrument not banned / delisted

### 3.3 Instrument States

| State | Description | Trading Allowed |
|------|------------|----------------|
| ACTIVE | In Layer A and eligible today | Increase / decrease |
| INELIGIBLE_HOLD | Temporarily fails Layer B | Reduce only |
| BANNED_FLATTEN | Removed / untradeable | Immediate flatten |

**Precedence**: `BANNED_FLATTEN` > `INELIGIBLE_HOLD` > `ACTIVE`

### 3.4 Exit Rules

- `BANNED_FLATTEN`: target weight = 0 immediately
- `INELIGIBLE_HOLD`: linear decay to 0 over `forced_exit_days`

---

## 4. Forecast Rules (Locked)

### 4.1 Trend — EWMAC (Carver‑style)

- Pairs: configurable list, e.g. `(8,32), (16,64)`
- Vol‑normalized
- Scaled so `mean(abs(forecast)) ≈ 10`
- Cap: ±20

### 4.2 Carry — Funding Rate

- Signal = EWMA(funding_rate)
- Two half‑lives: fast / slow
- Net carry = slow − fast
- Normalized cross‑sectionally
- Cap: ±20

### 4.3 Relative Momentum (Optional Phase)

- Cross‑sectional rank over **Layer A universe as of last review**
- Ranks recomputed daily; membership fixed
- Exclude instruments with missing data for that day

### 4.4 Forecast Combination

- Equal‑weighted average of active rules
- Combined forecast capped at ±20

---

## 5. Position Sizing & Risk (Clarified)

### 5.1 Volatility Targeting

- Target annual vol: `vol_target_ann`
- Position sizing follows PST volatility scalar

### 5.2 Minimum Steady Position (RESOLVED)

- Minimum *per‑instrument* absolute weight:

```
min_weight_i = min_position_frac / N_active
```

- If `|w_i| < min_weight_i`, target is set to 0

### 5.3 Portfolio Constraints

- Gross leverage ≤ `gross_leverage_cap`
- Instrument Diversification Multiplier (IDM) ≤ `idm_cap`

---

## 6. Execution, Buffers & Costs (Canonical)

### 6.1 Trading Buffer

- No trade if `|target − current| < buffer_frac × position_vol`

### 6.2 Cost Model

For each trade:

```
RTC = notional × (spread_frac + taker_fee_frac)
SRcost = RTC / (annual_vol × capital)
```

Costs applied at trade time only.

---

## 7. Daily System Loop (Explicit)

1. Load prices & metadata
2. Update Layer B eligibility
3. Update instrument states
4. Compute forecasts
5. Combine & cap forecasts
6. Compute target positions
7. Apply exits / forced decays
8. Enforce leverage & IDM caps
9. Apply buffers → trades
10. Compute PnL (price + funding − costs)

---

## 8. Configuration Schema (YAML — REQUIRED)

```yaml
system:
  vol_target_ann: 0.25
  gross_leverage_cap: 1.5
  idm_cap: 2.5
  min_position_frac: 0.03

universe:
  review_freq: M
  min_adv_notional: 5e7
  daily_min_adv_notional: 1e7
  min_history_days: 365
  forced_exit_days: 5

rules:
  ewmac_pairs:
    - [8, 32]
    - [16, 64]
  carry_fast_halflife: 3
  carry_slow_halflife: 30

execution:
  buffer_frac: 0.1
```

---

## 9. Definition of Done (NEW — TESTABLE)

### 9.1 Forecast Tests

- Mean absolute forecast per rule ∈ [8, 12]
- Forecast never exceeds cap

### 9.2 Risk Tests

- Gross leverage ≤ cap at all times
- IDM ≤ cap at all times

### 9.3 State Machine Tests

- `BANNED_FLATTEN` ⇒ position = 0 same day
- `INELIGIBLE_HOLD` ⇒ monotonic decay to 0

### 9.4 Accounting Tests

- `Total PnL = price + funding − costs` (tolerance 1e‑6)

---

## 10. MVP Implementation Slice (Agent Guidance)

**Phase 1 only**:

- Instruments: top 5 by ADV
- Rules: EWMAC + Carry only
- Static Layer A universe
- Costs enabled
- Output: equity curve + CSV of positions

Once Phase 1 passes all tests, proceed to Layer B dynamics and rel‑mom.

---

## 11. Non‑Goals (Explicit)

- Intraday trading
- Funding prediction models
- Dynamic leverage beyond caps
- Discretionary overrides

---

**End of agent‑ready specification.**

