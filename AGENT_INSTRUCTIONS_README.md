# Agent Instructions — Crypto Perps System (pysystemtrade fork)

This README is the **single entrypoint** for a coding agent working in a fresh fork of Robert Carver’s `pysystemtrade` repo to implement the Crypto Perpetual Futures Trading System described in the design spec.

## 0) What to read first

1. **Design Spec (Agent-Ready Revision)** — normative source of truth for logic, data contracts, and acceptance tests.
2. This README — defines scope, order of work, and repo conventions.

## 1) Source-of-truth and precedence

If anything conflicts:

> **Design Spec > This README > CLAUDE.md**

Do not invent behavior not explicitly specified.

---

## 2) Integration preference (IMPORTANT)

### Preferred: Compose using existing `pysystemtrade` stages/utilities

- Reuse PST components for volatility estimation, forecasting helpers, and portfolio constraint utilities *when they exist and match the spec*.
- Add only the missing pieces (crypto data adapter, funding carry rule, universe/state logic) under `systems/crypto_perps/` and `sysdata/crypto/`.

### Fallback (allowed for Phase 1): Minimal runner with PST-compatible interfaces

If PST stage wiring becomes a blocker, implement a small `systems/crypto_perps/system.py` runner that:

- Loads config from `config/crypto_perps.yaml`
- Uses the data adapter contract in §5
- Produces outputs in a stable format

**But** keep data types and naming compatible with PST expectations so it can be migrated into full stage composition later.

---

## 3) Non-negotiables

- **Do not refactor PST core abstractions** unless explicitly required.
- **Daily-frequency only**. No intraday bars, no async execution logic.
- **Deterministic**: same inputs + config ⇒ stable outputs (within floating tolerance).
- **One canonical cost model** and **one canonical state machine**.

---

## 4) Target file layout (must follow)

Create (or update) only these system-specific modules first:

- `systems/crypto_perps/system.py` — orchestrates the daily loop / runner
- `systems/crypto_perps/universe.py` — Layer A/B + state machine + exits
- `systems/crypto_perps/rules/ewmac.py`
- `systems/crypto_perps/rules/carry_funding.py`
- `systems/crypto_perps/execution.py` — buffers, delta-to-trade, costs
- `systems/crypto_perps/accounting.py` — price/funding/cost PnL attribution
- `sysdata/crypto/prices.py` — data adapter producing PST-compatible series
- `config/crypto_perps.yaml`
- `tests/test_crypto_perps_smoke.py`
- `data/example_crypto_perps.parquet` — small golden dataset

Avoid touching anything outside this list unless blocked.

---

## 5) Data adapter contract (STRICT)

Implement a single adapter entrypoint in `sysdata/crypto/prices.py`.

### 5.1 Function signature

The adapter must provide **one** of the following (pick one; document which):

**Option A (recommended for Phase 1 runner):**

- `load_crypto_perps_panel(path: str) -> tuple[pd.DataFrame, pd.DataFrame]`
  - Returns:
    - `prices_df`: columns = instruments, index = UTC dates, values = close
    - `meta_df`: multi-index (date, instrument) or aligned panel with fields: funding_rate, adv_notional, spread_frac, taker_fee_frac

**Option B (more PST-native):**

- `get_prices_for_instrument(instrument: str) -> pd.Series` (UTC date index)
- `get_metadata_for_instrument(instrument: str) -> pd.DataFrame` (UTC date index)
- `list_instruments() -> list[str]`

### 5.2 Invariants (must be asserted)

- Date index is UTC daily, **monotonic increasing**, no duplicates.
- Instruments are stable strings (e.g., `BTCUSDT_PERP`).
- `funding_rate[t]` aligns to position held from close(t−1) → close(t).
- Missing `close` ⇒ instrument ineligible that day.
- Missing `funding_rate` ⇒ funding PnL = 0 (explicit).

Add a unit test in `test_crypto_perps_smoke.py` that validates these invariants on `data/example_crypto_perps.parquet`.

---

## 6) How to run (CANONICAL COMMAND)

Phase 1 must be runnable via **one** blessed command.

Choose one of these patterns and implement it:

### Pattern A: module entrypoint

```
python -m systems.crypto_perps.system --config config/crypto_perps.yaml --data data/example_crypto_perps.parquet --outdir out/crypto_perps
```

### Pattern B: script entrypoint

```
python systems/crypto_perps/system.py --config config/crypto_perps.yaml --data data/example_crypto_perps.parquet --outdir out/crypto_perps
```

Expected outputs (Phase 1):

- `out/crypto_perps/equity_curve.csv`
- `out/crypto_perps/positions.csv`
- Optional: `out/crypto_perps/diagnostics.json`

Also ensure tests run with:

```
pytest -q
```

---

## 7) Implementation order (do this sequentially)

### Phase 1 (MVP — stop here until tests pass)

1. **Data adapter**: load `data/example_crypto_perps.parquet` and expose daily `close` + metadata per instrument.
2. **Universe**: implement Layer A membership (static for Phase 1) and daily eligibility filter.
3. **Rules**: implement EWMAC + funding carry rules with caps.
4. **Forecast scaling**: ensure `mean(abs(forecast)) ≈ 10` and cap at ±20.
5. **Sizing**: vol targeting + min steady position rule.
6. **Constraints**: gross leverage cap and IDM cap.
7. **Execution**: buffer logic, generate trades, apply cost model.
8. **Accounting**: compute daily price PnL + funding PnL − costs.
9. **Outputs**: equity curve + positions CSV.
10. **Tests**: make all Definition-of-Done assertions pass.

### Phase 2 (only after Phase 1 is green)

- Monthly Layer A review schedule + frozen membership between reviews.
- Full state machine behavior: `INELIGIBLE_HOLD` decay and `BANNED_FLATTEN` immediate flatten.
- Optional relative momentum rule module.

---

## 8) Reuse PST where possible (CHECKLIST)

Before implementing custom code, search the repo for existing utilities that match the spec:

- Volatility estimation / scalar (daily→annual)
- EWMAC / trend forecast helpers
- Forecast scaling / normalisation utilities
- Portfolio constraint utilities (gross leverage, diversification multiplier/IDM)
- Accounting helpers for PnL aggregation

If you reuse a PST utility, add a short comment pointing to the module used (no long refactors).

---

## 9) Testing expectations

At minimum, implement tests for:

- Forecast scaling and caps
- Leverage and IDM caps
- Accounting identity: `total_pnl = price + funding − costs`

(Phase 2 adds state-machine exit behavior tests.)

---

## 10) PR discipline (how to chunk work)

Make changes in small, reviewable steps:

1. Data adapter + small dataset + smoke test scaffold
2. EWMAC rule + forecast scaling tests
3. Carry rule + forecast scaling tests
4. Sizing + constraint enforcement
5. Execution + cost model
6. Accounting + equity curve output

Each step should leave the repo in a runnable state.

---

## 11) Out of scope (do not implement)

- Live trading / exchange connectivity
- Intraday execution optimization
- Funding prediction models
- Alternative cost models
- Parameter search / optimization

---

**If you are an agent:** implement Phase 1 fully and stop. Do not continue to Phase 2 without explicit instruction.

