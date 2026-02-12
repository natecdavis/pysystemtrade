# Module Contracts - Crypto Perps System

Explicit documentation of all major interfaces in the system.

---

## 1. Data Ingestion Contract

**Module:** `sysdata/crypto/prices.py`

### Function: `load_crypto_perps_panel(path, validate_schema=True, allow_jagged=False, lifecycle_path=None)`

**Purpose:** Load crypto perpetual futures data from parquet file.

**Input Contract:**
- `path` (str): Absolute or relative path to parquet file
- Parquet schema must match:
  ```
  date: datetime64[ns] (UTC-naive)
  instrument: str (format: {SYMBOL}_PERP)
  close: float64 (>0)
  funding_rate: float64
  adv_notional: float64 (>=0)
  spread_frac: float64 (>=0, <1)
  taker_fee_frac: float64 (>=0, <1)
  ```
- If `allow_jagged=True`, must provide `lifecycle_path` pointing to valid lifecycle JSON
- All instruments must have monotonic unique dates
- Rectangular panel (default): all instruments must have identical date ranges
- Jagged panel (`allow_jagged=True`): instruments may have different date ranges (NaN for missing dates)

**Output Contract:**
- Returns: `Tuple[pd.DataFrame, pd.DataFrame, Optional[pd.DataFrame]]`
  1. `prices_df`: Wide DataFrame, dates × instruments, values = close prices
     - Index: DatetimeIndex (UTC-naive)
     - Columns: instrument names
     - Values: float64 prices
     - Rectangular: No NaN allowed
     - Jagged: NaN allowed only before launch or after delist

  2. `meta_df`: MultiIndex DataFrame (date, instrument)
     - Columns: funding_rate, adv_notional, spread_frac, taker_fee_frac
     - All values validated (funding in reasonable range, adv >= 0, etc.)

  3. `lifecycle_df`: Optional DataFrame (only if allow_jagged=True)
     - Index: instrument names
     - Columns: launch_date, status, delist_date
     - Used for validating NaN prices

**Invariants:**
- `prices_df.index` matches `meta_df.index.get_level_values(0).unique()`
- `prices_df.columns` matches `meta_df.index.get_level_values(1).unique()`
- No duplicate (date, instrument) pairs
- Dates are calendar days (no gaps for weekends - crypto trades 24/7)

**Errors:**
- `ValueError` if schema validation fails
- `ValueError` if NaN in prices (rectangular) or unjustified NaN (jagged)
- `FileNotFoundError` if parquet file doesn't exist

---

## 2. Signal/Forecast Contract

**Module:** `systems/crypto_perps/forecasts.py`

### Function: `process_all_forecasts(...)`

**Purpose:** Combine multiple forecast rules (EWMAC, carry, relative momentum) into single combined forecast per instrument.

**Input Contract:**
- Each forecast rule returns `Dict[instrument, pd.Series]`
  - Keys: All active instruments
  - Values: Daily forecasts (float, typically scaled to ±10)
  - Index: DatetimeIndex matching backtest date range
  - Must not have NaN for active instruments

**Output Contract:**
- Returns: `Dict[str, pd.Series]`
- Keys: All instruments present in any input
- Values: Combined forecast series
  - Weighted average of all enabled rules
  - Capped at forecast_cap (default 20.0)
  - Floored at -forecast_cap
  - NaN if all components are NaN

**Invariants:**
- Output index matches input index (same dates)
- All forecasts in range [-20, +20] (capped)
- No forecast > 20.0 or < -20.0
- If instrument has only one rule → combined = that rule
- If multiple rules → combined = weighted average

**Errors:**
- `ValueError` if no forecasts provided for any instrument

---

## 3. Sizing & Constraints Contract

**Module:** `systems/crypto_perps/constraints.py`

### Class: `PortfolioConstraintEngine`

**Purpose:** Apply IDM-based portfolio constraints to desired positions.

**Input Contract (apply_constraints method):**
- `desired_notionals`: Dict[instrument, float]
  - Keys: Active instruments for this date
  - Values: Desired notional positions ($) before constraints
  - Should respect vol-targeting from sizing stage

- `date`: pd.Timestamp (current date)
- `prices_df`: Wide price DataFrame (full history up to date)
- Constraint parameters: All explicit (no defaults)

**Output Contract:**
- Returns: `Tuple[Dict[str, float], Optional[dict]]`
  1. `constrained_notionals`: Dict[instrument, float]
     - Keys: Same instruments as input
     - Values: Constrained notional positions ($)
     - Respects `gross_leverage_cap` (gross exposure ≤ cap × capital)
     - Applies IDM (diversification multiplier)
     - IDM capped at `idm_cap` (max 2.5x)
     - Zero positions if abs(position) < `min_position_frac × capital`

  2. `diagnostics`: Optional dict with keys:
     - `idm_raw`: Raw IDM calculation (≥ 1.0)
     - `idm_applied`: IDM after capping (1.0 ≤ idm ≤ idm_cap)
     - `gross_lev_before`: Gross leverage before constraints
     - `gross_lev_after`: Gross leverage after constraints
     - `overall_scalar`: Position scaling factor (<1.0 means constrained)

**Invariants:**
- `sum(abs(constrained_notionals.values())) / capital <= gross_leverage_cap`
- `1.0 <= IDM <= idm_cap` (IDM = 1 if N=1, increases with N and low correlation)
- Constrained positions are proportionally scaled from desired (direction preserved)
- Zero positions explicitly represented (not dropped)

**Side Effects:**
- Updates internal EWMA correlation matrix state (incremental)
- No file I/O

**Errors:**
- `ValueError` if IDM < 1.0 (violates Carver-style normalization)
- `ValueError` if correlation matrix has invalid properties (not symmetric, diagonal != 1, etc.)

---

## 4. Execution Intent Contract

**Module:** `systems/crypto_perps/execution.py`

### Function: `execute_trade_for_date(...)`

**Purpose:** Convert desired positions to actual positions, applying execution buffer and costs.

**Input Contract:**
- `current_notionals`: Dict[instrument, float] (notional $, can be empty on first day)
- `desired_notionals`: Dict[instrument, float] (from constraints stage)
- `prices_df`: Wide price DataFrame
- `date`: Current date
- `buffer_frac`: Float in [0, 1] (e.g., 0.1 = 10% buffer)
- `volatilities`: Dict[instrument, float] (daily volatility for buffer calculation)
- `spread_frac`: Dict[instrument, float] (e.g., 0.0005 = 5 bps)
- `taker_fee_frac`: Dict[instrument, float] (e.g., 0.0004 = 4 bps)

**Output Contract:**
- Returns: `Tuple[Dict[str, float], Dict[str, float], Dict[str, str]]`
  1. `actual_notionals`: Dict[instrument, float]
     - Same keys as `desired_notionals`
     - Values: Actual positions after buffer logic
     - If `|desired - current| > buffer × volatility × desired`: trade to desired
     - Else: hold current

  2. `costs`: Dict[instrument, float] ($)
     - Sum of (spread cost + taker fee) for all trades
     - `cost_per_trade = abs(trade_notional) × (spread_frac + taker_fee_frac)`

  3. `trade_reasons`: Dict[instrument, str]
     - 'buffer_trade': Exceeded buffer, traded
     - 'buffer_no_trade': Within buffer, held
     - 'flatten_banned': Forced exit (banned instrument)
     - 'decay_ineligible': Gradual exit (ineligible)

**Invariants:**
- Actual positions exactly match desired positions IF no buffer
- With buffer: fewer trades, positions between current and desired
- Trading costs >= 0 always
- Costs proportional to trade size (notional turnover)

**Side Effects:**
- None (pure function)

**Errors:**
- `ValueError` if buffer_frac not in [0, 1]
- `ValueError` if spread or fees negative

---

## 5. Universe Selection Contract (Phase 2)

**Module:** `systems/crypto_perps/universe.py`

### Function: `get_layer_a_instruments(...)`

**Purpose:** Determine which instruments are eligible for trading on review date.

**Input Contract:**
- `date`: Review date (monthly on Phase 2)
- `candidate_instruments`: List[str] (candidate pool from config)
- `meta_df`: Metadata (ADV, funding, spreads)
- `lifecycle_df`: Instrument lifecycle (launch/delist dates)
- `min_adv_notional`: Float (ADV threshold for Layer-A membership)
- `min_history_days`: Int (minimum data coverage)
- `banned_instruments`: List[str] (manually banned)

**Output Contract:**
- Returns: `List[str]`
- Instruments passing all checks:
  1. Not banned
  2. Launched (date >= launch_date)
  3. Not delisted (date < delist_date or delist_date is None)
  4. Sufficient history (>= min_history_days of valid prices)
  5. ADV > min_adv_notional (trailing 30-day average)
  6. No data gaps (< data_gap_days consecutive NaN)

**Invariants:**
- Returned list ⊆ input candidate list
- Order preserved from input
- Empty list is valid (no instruments eligible)

**Side Effects:**
- None (stateless per review)

**Errors:**
- `ValueError` if lifecycle data missing for instrument
- `ValueError` if ADV data insufficient (<30 days)

---

## 6. State Machine Contract (Phase 2)

**Module:** `systems/crypto_perps/universe.py`

### Enum: `InstrumentState`

**States:**
- `ACTIVE`: Normal trading, full position
- `INELIGIBLE_HOLD`: Failed daily check, hold position, decay over N days
- `BANNED_FLATTEN`: Banned instrument, flatten immediately

**Transitions:**
- `ACTIVE → INELIGIBLE_HOLD`: Daily eligibility check fails (ADV drop, data gap)
- `INELIGIBLE_HOLD → ACTIVE`: Passes daily check again within decay period
- `INELIGIBLE_HOLD → BANNED_FLATTEN`: Decay period expires (N days)
- `ACTIVE → BANNED_FLATTEN`: Manual ban or Layer-A exit at review
- `BANNED_FLATTEN → (exit)`: Position fully flattened, instrument removed

### Function: `build_instrument_states(...)`

**Purpose:** Manage instrument lifecycle and state transitions across backtest.

**Input Contract:**
- `review_schedule`: Dict[pd.Timestamp, List[str]] (Layer-A membership history)
- `eligibility_df`: DataFrame (dates × instruments, bool) (daily checks)
- `dates`: List[pd.Timestamp] (backtest date range)
- `instruments`: List[str] (all candidate instruments)
- `forced_exit_days`: Int (decay period for INELIGIBLE_HOLD)

**Output Contract:**
- Returns: `Tuple[pd.DataFrame, pd.DataFrame, Dict[str, Dict[pd.Timestamp, float]]]`
  1. `state_df`: DataFrame (dates × instruments, InstrumentState)
     - Index: dates
     - Columns: instruments
     - Values: Current state

  2. `days_in_state_df`: DataFrame (dates × instruments, int)
     - Tracks duration in current state

  3. `entry_weights_log`: Dict[instrument, Dict[date, float]]
     - Records weight at entry to INELIGIBLE_HOLD (for gradual exit)

**Invariants:**
- State transitions follow rules above
- Days in state increments daily while in same state, resets on transition
- Entry weight is frozen at ACTIVE → INELIGIBLE_HOLD transition

**Side Effects:**
- None (pure function, computes full state history)

**Errors:**
- `ValueError` if review schedule has gaps or inconsistencies

---

## 7. Accounting Contract

**Module:** `systems/crypto_perps/accounting.py`

### Function: `calculate_cumulative_pnl(...)`

**Purpose:** Calculate daily PnL decomposition and equity curve.

**Input Contract:**
- `positions_df`: DataFrame (dates × instruments, notional $)
- `prices_df`: DataFrame (dates × instruments, close prices)
- `meta_df`: MultiIndex DataFrame (date, instrument) with funding_rate
- `costs_df`: DataFrame (dates × instruments, trading costs $)
- `initial_capital`: Float (starting equity)

**Output Contract:**
- Returns: `Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.Series]`
  1. `price_pnl_df`: DataFrame (dates × instruments)
     - Daily PnL from price changes
     - Formula: `position[t-1] × (price[t] - price[t-1]) / price[t-1]`

  2. `funding_pnl_df`: DataFrame (dates × instruments)
     - Daily PnL from funding rate accrual
     - Formula: `position[t] × funding_rate[t]` (negative means paid)

  3. `total_pnl_df`: DataFrame (dates × instruments)
     - Total daily PnL: `price_pnl + funding_pnl - costs`

  4. `equity_curve`: Series (dates)
     - Cumulative equity: `initial_capital + total_pnl.sum(axis=1).cumsum()`

**Invariants:**
- `total_pnl = price_pnl + funding_pnl - costs` (accounting identity)
- `equity_curve[t] = initial_capital + sum(total_pnl[0:t])`
- All PnL values finite (no NaN or inf)

**Side Effects:**
- None (pure function)

**Errors:**
- `ValueError` if positions and prices have mismatched dates/instruments
- `ValueError` if initial_capital <= 0

---

## 8. Metrics Contract

**Module:** `systems/crypto_perps/metrics.py`

### Function: `calculate_metrics(...)`

**Purpose:** Calculate headline performance metrics for backtest.

**Input Contract:**
- `equity_curve`: Series (daily equity values)
- `weights_df`: DataFrame (dates × instruments, position weights)
- `trades_df`: DataFrame (dates × instruments, trade amounts)
- `capital`: Float (initial capital)
- `state_df`: Optional DataFrame (Phase 2 state machine history)
- `constraint_scalars`: Optional Series (IDM constraint history)

**Output Contract:**
- Returns: `Dict[str, float]`
  - `sharpe`: Sharpe ratio (annualized, risk-free rate = 0)
  - `ann_return`: Annualized return
  - `ann_vol`: Annualized volatility
  - `max_drawdown`: Maximum drawdown (negative, e.g., -0.45 = 45% DD)
  - `gross_exposure`: Average gross leverage (sum of abs weights)
  - `turnover`: Annualized turnover (sum of abs trades / avg equity)

**Invariants:**
- All metrics finite (no NaN)
- Sharpe = ann_return / ann_vol (if vol > 0, else 0)
- Turnover >= 0
- Max drawdown <= 0

**Side Effects:**
- None (pure function)

**Errors:**
- `ValueError` if equity_curve is empty or has NaN

---

## Usage Example

```python
# 1. Load data
prices_df, meta_df, lifecycle_df = load_crypto_perps_panel(
    path='data/dataset.parquet',
    allow_jagged=True,
    lifecycle_path='data/raw/binance/metadata/binance_symbol_lifecycle.json'
)

# 2. Generate forecasts
forecasts = process_all_forecasts(
    ewmac_forecasts={...},
    carry_forecasts={...},
    relmom_forecasts={...}
)

# 3. Size positions
weights_df, notionals_df = calculate_target_weights(
    forecasts=forecasts,
    prices_df=prices_df,
    capital=5000,
    vol_target_ann=0.25,
    min_position_frac=0.03
)

# 4. Apply constraints
constraint_engine = PortfolioConstraintEngine(...)
constrained_notionals, diag = constraint_engine.apply_constraints(
    desired_notionals=notionals_df.loc[date].to_dict(),
    date=date,
    prices_df=prices_df
)

# 5. Execute trades
actual_notionals, costs, reasons = execute_trade_for_date(
    current_notionals={...},
    desired_notionals=constrained_notionals,
    prices_df=prices_df,
    date=date,
    buffer_frac=0.1,
    ...
)

# 6. Calculate PnL
price_pnl, funding_pnl, total_pnl, equity = calculate_cumulative_pnl(
    positions_df=positions_df,
    prices_df=prices_df,
    meta_df=meta_df,
    costs_df=costs_df,
    initial_capital=5000
)

# 7. Calculate metrics
metrics = calculate_metrics(
    equity_curve=equity,
    weights_df=weights_df,
    trades_df=trades_df,
    capital=5000
)
```

---

## Contract Verification

All contracts are enforced through:
1. Type hints (static analysis with mypy)
2. Runtime validation (ValueError on contract violation)
3. Unit tests (tests/ directory)
4. Integration tests (end-to-end backtests)

See `tests/test_*` for contract verification tests.
