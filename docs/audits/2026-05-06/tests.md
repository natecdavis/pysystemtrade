# Phase C — Test Status & Coverage Gaps

## 1. Headline numbers

```
$ PYTHONPATH=. pytest -q tests/ --ignore=tests/test_examples.py
829 collected (1 module excluded for upstream IB import)
   795 passed
    34 skipped (gated on --runslow / --runlive / RUN_LARGE_DATASET_TESTS / network / file-on-disk)
     0 failed
50.59s
```

**Net assessment.** The current test suite is healthy. AUDIT_FINDINGS.md (2026-04-17) P1-4 ("tests not protecting live trade-plan surface") is **closed** — `test_trade_plan.py` collects fine, `test_live_advisory_integration.py` runs all 7 cases green. The legacy `check_gross_leverage` import error is gone. A re-read of the test pass tally is the strongest evidence that the bulk of the AUDIT_FINDINGS.md P0/P1 items have been remediated.

But the suite is *passing for the wrong reasons* on three live-path issues already surfaced in Phase A/B. Those are documented as coverage gaps below — they're the tests that *should* exist, not tests that fail.

---

## 2. Skipped tests — all by design except two

| Skipped | Reason | Action |
|---|---|---|
| `tests/crypto/test_data_invariants.py` (12 tests) | `--runslow` flag | OK |
| `tests/crypto/test_sniff.py` (12) | `--runlive` flag | OK |
| `tests/test_dataset_contracts.py:131,136,142` (3) | `RUN_LARGE_DATASET_TESTS=1` | OK |
| `tests/test_integration_diagnostics.py:14,85` (2) | `--runslow` flag | OK |
| `tests/test_jagged_panel_validation.py:27,37` (2) | needs Binance ZIPs locally | OK |
| `tests/test_phase2_opportunistic_refresh.py:114` (1) | manual network test | OK |
| **`tests/test_macro_signal_rules.py:52`** | **looks for `data/stablecoin_supply.parquet` (repo root) — file moved to `envs/dev/data/`** | **silent skip — fix needed** |
| **`tests/test_macro_signal_rules.py:93`** | **same, for `data/etf_flows.parquet`** | **silent skip — fix needed** |

The two macro-signal-rules tests are *real-data smoke tests* that should validate the rule end-to-end against the actual DefiLlama / yfinance output. Production migrated those files to `envs/dev/data/` (per `docs/AUXILIARY_DATA_FRESHNESS.md`); the tests were not updated, so they silently `pytest.skip`. **D2 finding.** Test-path drift is exactly the kind of slow-bleed coverage loss audits exist to catch.

Fix: change `tests/test_macro_signal_rules.py:52, 93` from `Path(__file__).parent.parent / "data" / X` to use the same env-first/repo-fallback resolver pattern that production uses (`required_data._resolve_path` or `extract_rule_forecasts._resolve`).

---

## 3. Deliberately-excluded modules (pre-existing reasons, all OK)

| Module | Reason | Outcome |
|---|---|---|
| `tests/test_examples.py` | upstream Carver test imports `ib_insync` which is not installed | leave as-is — irrelevant to crypto-perps |
| `tests/test_real_data_smoke.py` | needs `data/test_fixtures/btc_eth_jan2023.parquet` | **fixture exists** — runs green; should not be excluded from default pytest |
| `tests/test_baseline_equivalence.py` | uses real fixture | runs green |
| `tests/test_daily_cadence_e2e.py` | mocked Binance API E2E | runs green |

Three of these run fine; only `test_examples.py` is genuinely excluded. Phase D candidate to clean up the upstream legacy or tag with a `--upstream` skip marker.

---

## 4. Gaps that let known bugs ship

The most important Phase C output: tests that should have caught the Phase B P0 findings but don't exist.

### 4.1 No end-to-end test exercises `daily_paper_run.py`

```
$ grep -rln "daily_paper_run\|--non-binance-only" tests/
(empty)
```

The script that runs every day on cron and after VPN — `scripts/daily_paper_run.py`, 1239 lines — has zero integration tests. `test_daily_cadence_e2e.py` calls `apply_staleness_overlay` directly (line 380, 420), constructing its own `raw_data_status.json` in a tmp dir; it does not subprocess-out to `daily_paper_run.py` and so cannot detect the path-resolution glue mismatch between `daily_paper_run.py:434` and `run_live_advisory.py:957`.

This is the gap that let **Probe 1A** (staleness overlay silently disabled) ship.

**Recommended skeleton:**
```python
# tests/test_daily_paper_run_orchestrator.py
def test_non_binance_only_writes_data_status_where_advisory_can_find_it(tmp_env):
    """Cron-mode invariant: daily_paper_run --non-binance-only must produce
    raw_data_status.json at the path run_live_advisory.py expects.
    """
    # set up tmp env with synthetic Binance/aux feeds
    rc = subprocess.run([sys.executable, "scripts/daily_paper_run.py",
                         "--config", str(tmp_cfg), "--env-root", str(tmp_env),
                         "--non-binance-only", "--no-notify"]).returncode
    assert rc == 0
    # The file the staleness consumer expects:
    expected = tmp_env / "out" / f"paper_{today}" / "raw_data_status.json"
    assert expected.exists(), (
        "run_live_advisory.py:957 reads exactly this filename in this directory; "
        "missing means the staleness overlay is silently skipped on every live run."
    )
```

### 4.2 No test exercises `trade_plan.py:476-482` (>30h C4 panel age fail-closed)

```
$ grep -n "30.0\|age_hours\|too old" tests/test_trade_plan.py tests/test_c4_xgboost_combiner.py tests/test_live_advisory_integration.py
(no matches for the age-check semantics; only matches are unrelated min_order_notional=30.0)
```

The 30-hour fail-closed in `systems/crypto_perps/trade_plan.py:476-482` is a P0 safety check (refuse to trade on stale C4 multipliers). It has no test. A regression that drops the check, increases the threshold to 30 days, or swallows the ValueError will not be caught.

**Recommended skeleton:**
```python
def test_panel_older_than_30h_raises_valueerror(tmp_path, mock_backtest_dir, ...):
    panel = tmp_path / "c4_multiplier_panel_h20.parquet"
    pd.DataFrame({"BTCUSDT_PERP": [1.0]}).to_parquet(panel)
    os.utime(panel, (time.time() - 31*3600, time.time() - 31*3600))
    cfg = {..., "walk_forward_multiplier_panel_path": str(panel)}
    with pytest.raises(ValueError, match="multiplier panel.*old"):
        generate_trade_plan(mock_backtest_dir, ..., config=cfg)
```

### 4.3 No test for `forecast_combine_gated._apply_walk_forward_multiplier` consumer-side fallbacks

```
$ grep -rn "_apply_walk_forward_multiplier" tests/
(empty)
```

Three silent-fallback branches in `systems/crypto_perps/forecast_combine_gated.py:130-160`:
- panel path config key absent → return forecast unchanged
- panel file exists but instrument missing from columns → return forecast unchanged
- panel cell NaN → fillna(1.0) (identity)

All untested. A regression that breaks the `fillna(1.0)` to `fillna(0.0)` would zero-out forecasts silently — no log, no test fires. Combined with Probe 6's finding (today's panel is full-identity), the consumer-side hook's silent behavior is what makes operator visibility nonexistent.

**Recommended skeletons:**
```python
def test_missing_panel_path_returns_input_unchanged(): ...
def test_instrument_not_in_panel_returns_input_unchanged(): ...
def test_nan_cells_become_identity_not_zero(): ...
def test_non_nan_cells_clip_to_pm20_after_multiplication(): ...
```

### 4.4 No "if checker says OK then rule must fire" invariant test

This is the gap that let **Probe 7** ship. 7 active rules in `forecast_weights` produce zero forecasts for 20–97 days while `required_data_status.json` reports all feeds `status=ok`.

A useful invariant test:
```python
def test_active_rules_emit_today_when_their_inputs_are_fresh():
    """For every (rule, instrument) where the rule's required feeds are not
    in 'warning' state, the live forecast panel must have a non-NaN value
    for today's date (or yesterday if today's row hasn't been appended yet)."""
    status = json.load(open("envs/dev/out/paper_<today>/required_data_status.json"))
    panel = pd.read_parquet("data/forecast_panels_122/forecasts.parquet")
    # build per-rule expected liveness; assert reality matches
```

The right home for this is probably a daily smoke test, not unit suite — but it's exactly the assertion that would have surfaced "OI feed fresh, OI rules silent for 96 days" the day it broke.

### 4.5 `test_macro_signal_rules.py` real-data smoke tests silently disabled

Per §2 above. Move from `Path(__file__).parent.parent / "data" / X` to env-aware resolution.

### 4.6 No test ties `daily_paper_run.py` step ordering to safety-critical invariants

Phase A finding: `[6/10] append_equity` runs *before* `[7b/10] verify_chain`, so a chain-incoherent run still pollutes `equity_history.csv`. No test captures the ordering invariant. A regression that re-orders these would silently corrupt CB state.

```python
def test_equity_appended_only_after_chain_verified():
    """If verify_chain fails, equity_history.csv must NOT have a new row for today."""
```

---

## 5. Live-path coverage already in place (good)

These code paths are well-tested; they should not regress easily:

- `sysdata/crypto/manifest_chain.py` — `tests/test_manifest_chain.py` covers every branch (legacy entries, run_id grouping, hash-mismatch detection, missing-file detection).
- `sysdata/crypto/atomic_io.py` — `tests/test_atomic_io.py` covers atomic writes + lock acquire / release / cross-process contention.
- `sysdata/crypto/circuit_breaker.py` — `tests/crypto/test_circuit_breaker.py` covers `append_equity` dedup, daily-loss/drawdown triggers, state persistence.
- `systems/crypto_perps/trade_plan.py:75-91` (orphan mark-price fail-closed, A2) — `tests/test_trade_plan.py::TestLoadActualPositions::test_orphan_position_no_price_dict`, `test_orphan_position_zero_price_in_csv`, `test_zero_contracts_orphan_is_safe` — covered.
- `systems/crypto_perps/c4_xgboost_combiner.py` `is_uninformative` identity branch — `tests/test_c4_xgboost_combiner.py:109` (`test_uninformative_fit_emits_identity_multiplier`), `:387` (`test_predict_today_uninformative_returns_identity`) — both producer-side. (Consumer-side hook still untested per §4.3.)
- `sysdata/crypto/required_data.py` — `tests/test_required_data.py` covers per-feed lag computation.
- `systems/crypto_perps/walk_forward.py` — `tests/test_walk_forward_harness.py` covers AdoptionRule, per-window decomp, deep-merge, windowed scoring (B7).
- `systems/crypto_perps/staleness_overlay.py` — `tests/test_staleness_overlay.py` 20 tests cover the function in isolation. (The wiring to it from the orchestrator is the gap; see §4.1.)
- Reconciliation — `tests/test_reconciliation.py` covers tolerance boundary, plan-vs-actual diff, orphan detection.

---

## 6. Pytest run hygiene

- 570 deprecation warnings, mostly pandas+numpy compatibility (`np.find_common_type`, `cumproduct`, `product`). All come from upstream pandas internals. Should fade as the project tracks pandas 2.x; not an audit P-finding.
- `tests/test_examples.py` errors collection due to missing `ib_insync`. Either add a `pytestmark = pytest.mark.skipif(not HAS_IB)`-style guard at the top, or move it under `tests/upstream/` with an `--upstream` flag in `conftest.py`. Tiny D-tier item.

---

## 7. Phase C summary — promoted findings

| ID | Severity | Description |
|---|---|---|
| C1 | **P1** | No end-to-end test for `daily_paper_run.py`. Phase B Probe 1A's bug shipped due to this gap. |
| C2 | **P1** | No test for `trade_plan.py:476-482` >30h panel-age fail-closed. |
| C3 | **P1** | No test for `forecast_combine_gated._apply_walk_forward_multiplier` consumer-side fallbacks (3 silent identity branches). |
| C4 | **P1** | No "active rules must fire when feeds are fresh" liveness invariant. Phase B Probe 7's bug shipped due to this gap. |
| C5 | **P2** | `tests/test_macro_signal_rules.py:52, 93` real-data smoke tests silently skip due to test-path drift after migration to `envs/dev/data/`. |
| C6 | **P2** | No test for the `daily_paper_run.py` step-ordering invariant (`[6] append_equity` must follow `[7b] verify_chain`). |
| C7 | **P3** | `tests/test_examples.py` errors at collection — gate it under an `--upstream` flag or guard the import. |

**Test status** is **clean** for the in-scope code: 795 passed, 0 failed. The findings here are coverage gaps, not failing tests.
