# Phase F — Findings Ledger

Aggregates Phases A–E into a single prioritized punch-list. Each row: file:line evidence, one-line description, recommended fix, estimated effort, rollback plan, and the phase that surfaced it.

Severity classes:
- **P0** — correctness, blocking. Latent or active misbehavior that can lose money.
- **P1** — correctness, non-blocking. Wrong-result or test-coverage gap that doesn't bite today but will.
- **P2** — simplicity / drift. Cognitive load, no immediate correctness impact.
- **P3** — speed / paper-cuts. Wall-clock or trivial doc.

---

## P0 — Correctness, blocking (2 findings)

### F1. Staleness overlay silently disabled in live daily flow

| | |
|---|---|
| **Evidence** | `daily_paper_run.py:434` writes `--output-report .../envs/dev/out/raw_data_status_v1.json`; `run_live_advisory.py:957` reads `output_dir / "raw_data_status.json"`; `daily_paper_run.py:1091` passes `--skip-data-update` so advisory never writes the expected file. Confirmed by `paper_run_latest.log` line 341344: `WARNING ... raw_data_status.json. Staleness overlay skipped` and line 341367: `Data status path not provided - staleness overlay skipped (V0 mode)`. |
| **Impact** | Trade plan generates new exposure on stale data without the per-instrument staleness overlay protection. Latent — bites only on a VPN-down or partial-fetch day (`update_data_daily.py` exit 3 path: `daily_paper_run.py:438-442`). On a clean-fetch day it is a no-op (Phase B Probe 1B confirmed all 496 instruments at `staleness_days=0`). |
| **Fix** | Smallest: in `daily_paper_run.py`, after writing `raw_data_status_v1.json`, also `shutil.copy()` it to `output_dir / "raw_data_status.json"` so `run_live_advisory.py:957` finds it. Better: standardize on one filename in one canonical location and have all consumers read from there. |
| **Effort** | 30 min for the copy fix; 2 h for canonical refactor. |
| **Rollback** | `git revert`; if the copy is wrong, the only side-effect is that `--data-status` is or isn't passed — no live-state mutation. |
| **Phase** | B (Probe 1A) |

### F2. 7 active rules silently produce zero forecast for 20–97 days

| | |
|---|---|
| **Evidence** | Phase B Probe 7. `attn_exhaustion_fade`, `attn_panic_rebound`, `crowd_deleverage_trend`, `xs_oi_attention` last fired 2026-01-29/30 (≈97 days). `btc_dom_level_120`, `btc_dom_rotation_16`, `btc_dom_rotation_32` last fired 2026-04-16 (20 days). All seven are at non-zero weight (≈1/122) in `config/crypto_perps_1k.yaml.forecast_weights`. The combiner's NaN-renormalize (`forecast_combine_gated.py:107-114`) silently redistributes their nominal ~5.7% budget across the 115 firing rules. Freshness checker reports `binance_oi_lsr: status=ok lag=0/2 latest=2026-05-05` because the parquet is genuinely fresh. |
| **Impact** | Today's combined forecast is **not** the 1/122 weighting the config declares — it is a different weighting on the firing rules. Strategy drift of ~5.7% of forecast budget. The user's stated audit goal ("no data from any source is allowed to get stale") is violated even though the operator-facing freshness reports green. |
| **Fix** | Two parts. (a) **Visibility:** `required_data_status.json` should include per-rule emit-coverage cross-check (rule-fired-today=Y/N) so the freshness checker says "this rule's input is fresh, but the rule is silent" — escalate that to a warning. ~3 h. (b) **Root cause:** investigate why `attn_*`, `crowd_deleverage_trend`, `xs_oi_attention` produce no forecast despite OI parquet having `max_date=2026-05-05` for 496 of 507 instruments. Likely `parquet_perps_sim_data.get_open_interest()` instrument-symbol mapping or rule lookback warmup; needs a separate session to root-cause. |
| **Effort** | Part (a) ~3 h. Part (b) ~1 day investigation + however long the fix takes. |
| **Rollback** | Visibility check is purely additive (warning logging). Root-cause fix should be tested via Phase B Probe 7 reproduction. |
| **Phase** | B (Probe 7) |

---

## P1 — Correctness, non-blocking (12 findings)

### F3. C4 multiplier consumer-side hook is fully silent on identity / missing / NaN

| | |
|---|---|
| **Evidence** | `systems/crypto_perps/forecast_combine_gated.py:130-160`. Three identity-fallback branches with no log, no metric, no audit-bundle flag: (a) panel path config key absent; (b) instrument not in panel columns; (c) NaN cell `fillna(1.0)`. Phase B Probe 6: today's live multiplier panel is full identity (`is_uninformative=True`). |
| **Impact** | Operator reviewing today's trade plan has no signal that C4 is or isn't modulating. Probe 6 confirmed last 6 days are pure identity — the +0.126 Sharpe ADOPT decision is silently absent today. |
| **Fix** | Add an INFO log per `get_combined_forecast` (or aggregated once per backtest): `c4_multiplier: instrument=BTC mode=panel|identity-uninformative|missing mean=… σ=…`. Add `c4_multiplier_state` to `audit_bundle["constraints_snapshot"]`. |
| **Effort** | 1 h. |
| **Rollback** | Purely additive logging; revert if it floods. |
| **Phase** | A finding 2, B Probe 6, D5, D17 |

### F4. No fail-closed for C4 panel age in the backtest stage

| | |
|---|---|
| **Evidence** | `systems/crypto_perps/trade_plan.py:454-486` is the *only* C4 panel-age check (30 h fail-closed). The combiner-side hook `forecast_combine_gated.py:130-160` has no age check. The dynamic-universe backtest (`scripts/run_dynamic_universe_backtest.py`) routes through the combiner; its forecasts can therefore be modulated by a stale panel without alarm, only catching it when trade-plan generation fires later. |
| **Impact** | A stale panel produces silent wrong forecasts in the backtest. The trade plan throws then, but the backtest already produced wrong-multiplier `positions.csv`. |
| **Fix** | Move the 30 h check into `_apply_walk_forward_multiplier` so backtest *and* trade-plan share one source of truth. |
| **Effort** | 30 min + a unit test (closes test gap C2). |
| **Rollback** | Revert; the check stays in `trade_plan.py` as backup. |
| **Phase** | A finding 3, D6 |

### F5. `verify_chain` exception swallow inconsistent with rest of chain semantics

| | |
|---|---|
| **Evidence** | `scripts/daily_paper_run.py:1172-1173`. Every other manifest-chain failure mode (file missing, integrity mismatch, no complete run) is fail-closed (`return 1`); this `except Exception` becomes WARN-only and the run continues. |
| **Impact** | An unexpected verifier exception (e.g. permission error, race) silently lets an incoherent trade plan ship with a successful notification. |
| **Fix** | Replace bare `except Exception` with explicit list (`FileNotFoundError, json.JSONDecodeError, ManifestChainError`) — fail-closed otherwise. Or just re-raise. |
| **Effort** | 5 min. |
| **Rollback** | Trivial. |
| **Phase** | A finding 5, B Probe 4 caveat, D13 |

### F6. `circuit_breaker.append_equity` runs before `verify_chain` and pollutes history on chain failure

| | |
|---|---|
| **Evidence** | `daily_paper_run.py:1116` (step `[6/10]`) runs `cb.append_equity(today_iso, equity)`. `daily_paper_run.py:1140-1163` (step `[7b/10]`) verifies the chain. If `[7b]` fails-closed, `equity_history.csv` already has today's row — and `CircuitBreaker.check()` (`circuit_breaker.py:45-98`) bases drawdown calculations on it. |
| **Impact** | A chain-incoherent run pollutes the live CB state. The next run computes drawdown against a possibly-wrong equity. |
| **Fix** | Move `[6/10]` after `[7b/10]`, or batch the equity append into the success path only. |
| **Effort** | 15 min. |
| **Rollback** | Equity history is human-readable; manual edit is trivial. |
| **Phase** | A finding 6 |

### F7. `extract_rule_forecasts.py:281-291` swallows per-(rule, instrument) exceptions silently

| | |
|---|---|
| **Evidence** | `for rule: for inst: try: ... except Exception: pass`. Bare exception swallow with no logging or counter. Upstream root cause for F2 — when an OI rule throws on instrument-symbol lookup, the failure disappears. |
| **Impact** | Real wiring breaks (data attachment, schema mismatch, missing instrument) are silently masked; the rule produces no forecast and the operator never knows. |
| **Fix** | Catch only the expected narrow classes (`KeyError`, `ValueError`, possibly `IndexError`); log the rest at WARN. Emit a per-rule "X/Y instruments with silent exceptions today" summary. |
| **Effort** | 3 h (need to enumerate the legitimately-thrown exceptions before tightening). |
| **Rollback** | Easy to revert — the catch site is one block. |
| **Phase** | A finding 4, D15 |

### F8. `trade_plan.py` mixed config-key access (`config.get` vs `get_element_or_default`)

| | |
|---|---|
| **Evidence** | Same function alternates between `config.get(...)` (dict-only) and `config.get_element_or_default(...)` (Carver Config). Two of seven config reads dispatch at runtime via `isinstance(config, dict)`; the other five would crash on a Carver Config. The dispatches are scaffolding for a code path that doesn't exist. |
| **File** | `systems/crypto_perps/trade_plan.py:461-465, 656-658, 682-684, 721-722, 731` |
| **Impact** | Cognitive footgun for refactors / new keys: "is this a dict or a Config? both? with fallback?" |
| **Fix** | Standardize on dict in `trade_plan.py`. Remove the `isinstance` dispatches. Document the API in the docstring. |
| **Effort** | 30 min. |
| **Rollback** | `git revert`; pytest covers all current call sites. |
| **Phase** | D3 |

### F9. No end-to-end test exercises `daily_paper_run.py`

| | |
|---|---|
| **Evidence** | `grep -rln "daily_paper_run\|--non-binance-only" tests/` returns empty. The orchestrator that runs every day on cron (1239 lines) has zero integration tests. F1's bug shipped because of this gap. |
| **Impact** | Any regression in the orchestrator's data-status wiring, manifest-chain integration, advisory invocation, or CB ordering ships silently. |
| **Fix** | Add `tests/test_daily_paper_run_orchestrator.py` with at least one cron-mode test (verifies that `raw_data_status.json` lands where `run_live_advisory.py` looks) and one manual-mode dry-run test (asserts manifest chain is verified before equity is appended). |
| **Effort** | 3-4 h for a useful first version. |
| **Rollback** | Adding tests has no rollback risk. |
| **Phase** | C1 |

### F10. No test for `trade_plan.py` 30 h panel-age fail-closed

| | |
|---|---|
| **Evidence** | `grep -n "30.0\|age_hours\|too old" tests/test_trade_plan.py tests/test_c4_xgboost_combiner.py tests/test_live_advisory_integration.py` — no matches for the age-check semantics. |
| **Impact** | A regression that drops the check, raises the threshold, or swallows the ValueError will not be caught. |
| **Fix** | Add `test_panel_older_than_30h_raises_valueerror` to `tests/test_trade_plan.py`. (Skeleton in `tests.md` C2.) |
| **Effort** | 30 min. |
| **Rollback** | n/a (test addition). |
| **Phase** | C2 |

### F11. No test for `_apply_walk_forward_multiplier` silent-fallback branches

| | |
|---|---|
| **Evidence** | `grep -rn "_apply_walk_forward_multiplier" tests/` returns empty. Three fallback branches in `forecast_combine_gated.py:130-160` all untested. |
| **Impact** | A regression that breaks `fillna(1.0)` to `fillna(0.0)` would zero-out forecasts silently. No test fires. |
| **Fix** | Add three tests to `tests/test_c4_xgboost_combiner.py` (or new module): `test_missing_panel_path_returns_input_unchanged`, `test_instrument_not_in_panel_returns_input_unchanged`, `test_nan_cells_become_identity_not_zero`. |
| **Effort** | 1 h. |
| **Rollback** | n/a (test addition). |
| **Phase** | C3 |

### F12. No "active rules must fire when feeds are fresh" liveness invariant

| | |
|---|---|
| **Evidence** | F2 shipped because no test asserts that every rule in active `forecast_weights` actually emits non-NaN today when its required feed is `status=ok`. |
| **Impact** | Probe 7's silent-rule pattern can recur. |
| **Fix** | Add a daily smoke test (or pytest run gated on real data) that, given `required_data_status.json` and the latest forecast panel, asserts every active rule has at least N non-NaN cells on today's row. |
| **Effort** | 2 h. |
| **Rollback** | n/a. |
| **Phase** | C4 |

### F13. No test for `[6] append_equity` ordering after `[7b] verify_chain`

| | |
|---|---|
| **Evidence** | F6 plus `grep` confirms no test asserts the ordering invariant. |
| **Impact** | A regression that re-orders these silently corrupts CB state. |
| **Fix** | Add ordering-invariant test alongside F9's orchestrator test. |
| **Effort** | 30 min (combine with F9). |
| **Rollback** | n/a. |
| **Phase** | C6 |

### F14. `test_macro_signal_rules.py` real-data smoke tests silently skip due to path drift

| | |
|---|---|
| **Evidence** | `tests/test_macro_signal_rules.py:52, 93` reads `Path(__file__).parent.parent / "data" / X` — these files migrated to `envs/dev/data/` per `docs/AUXILIARY_DATA_FRESHNESS.md`. The tests `pytest.skip("data/X.parquet not present")`. |
| **Impact** | Real-data smoke tests for `stablecoin_supply` and `etf_flows` are silently disabled. Future stale-or-corrupted feed regression won't be caught. |
| **Fix** | Use the same env-first/repo-fallback resolver pattern as production (`required_data._resolve_path`). |
| **Effort** | 15 min. |
| **Rollback** | n/a. |
| **Phase** | C5 |

---

## P2 — Simplicity / drift (12 findings)

### F15. `prestage_daily.py` is shadow code (469 lines)

| | |
|---|---|
| **Evidence** | launchd plist invokes `daily_paper_run.py --non-binance-only`, not `prestage_daily.py`. Disagrees with the cron path on `[3l]` handling. `auto_rebuild_sb_dataset.py:17` docstring claim about prestage_daily is false. |
| **Fix** | Delete `scripts/prestage_daily.py`; delete `--skip-prestage` flag in `daily_paper_run.py:229-237` and its 4 call sites; fix `auto_rebuild_sb_dataset.py:17` doc. |
| **Effort** | 30 min. |
| **Rollback** | `git revert`. |
| **Phase** | A finding 9, D1, D14 |

### F16. CLI flag combinatorics in `daily_paper_run.py`

| | |
|---|---|
| **Evidence** | 6 boolean flags = 64 implicit modes; 3 real modes (cron / manual / dryrun). |
| **Fix** | Replace with `--mode {cron, manual, dryrun}`. Keep `--config`, `--env`/`--env-root`, `--notify`, `--parallel-workers` orthogonal. |
| **Effort** | 1 h + plist + runbook. |
| **Rollback** | `git revert`; update plist. |
| **Phase** | D2 |

### F17. Path resolver duplicated across two sites

| | |
|---|---|
| **Evidence** | `sysdata/crypto/required_data.py:24-32` (`_resolve_path`); `scripts/extract_rule_forecasts.py:67-71` (`_resolve`). Different signatures, same env-first/repo-fallback logic. |
| **Fix** | One utility in `sysdata/crypto/env_paths.py`. Both sites become wrappers. |
| **Effort** | 1 h. |
| **Rollback** | `git revert`. |
| **Phase** | A finding 10, D4 |

### F18. `daily_paper_run.py:368-391` mutates the version-controlled YAML on every run

| | |
|---|---|
| **Evidence** | Step `[1/10]` rewrites `notional_trading_capital` and `system.capital` in place via two regex `re.sub` calls. `git status` always shows `config/crypto_perps_1k.yaml` modified. |
| **Fix** | Compute notional in memory; pass `--notional-trading-capital <value>` (or YAML override) to `run_live_advisory.py` subprocess. |
| **Effort** | 1.5 h. |
| **Rollback** | `git revert`; restore the regex. |
| **Phase** | D7 |

### F19. `out/` directory lifecycle (262 subdirs, 9.1 GB)

| | |
|---|---|
| **Evidence** | `find out -maxdepth 1 -type d \| wc -l` = 262; `du -sh out` = 9.1 GB. Lane B6's `scripts/clean_output_dir.py` exists, has not been run. |
| **Fix** | Add `clean_output_dir.py --apply` to a weekly schedule. Move research subdirs to `out/_research/`. |
| **Effort** | 30 min. |
| **Rollback** | Cleanup is dry-run by default. |
| **Phase** | D8 |

### F20. `config/` clutter (47 YAMLs, 2 active)

| | |
|---|---|
| **Evidence** | `ls config/` lists 47 files; the active configs are `crypto_perps_1k.yaml` + `crypto_perps_full_rules.yaml`. The remaining 43 are research one-shots, abandoned tests, factorial sweeps, etc. `config/research/` already exists but is unused. |
| **Fix** | `git mv` test_*.yaml, factorial_test_*.yaml, phase2_test_*.yaml, etc., to `config/research/`. |
| **Effort** | 30 min. |
| **Rollback** | `git revert`. |
| **Phase** | D9 |

### F21. 11 stale top-level docs (2-4 months old)

| | |
|---|---|
| **Evidence** | `IMPLEMENTATION_*.md`, `PHASE1/2_*.md`, `TESTING_GUIDE_*.md`, `OPERATIONALIZATION.md`, `OI_OVERLAY_IMPLEMENTATION.md`, `TREND_AWARE_*.md`, `NEXT_STEPS_MIN_HISTORY.md` — all dated Feb–Mar 2026 and refer to superseded designs. |
| **Fix** | `mkdir docs/_archive/2026-Q1/` and `git mv` the 11 files. |
| **Effort** | 15 min. |
| **Rollback** | `git revert`. |
| **Phase** | D10 |

### F22. `.bak` files in tracked dirs

| | |
|---|---|
| **Evidence** | 5 `.bak_legacy_*` parquets in `data/`, 1 `.bak_20260505` parquet, 2 `.yaml.bak` configs. No retention policy. |
| **Fix** | `.gitignore` `.bak_*`; document a 30-day retention policy in `docs/OUTPUT_DIRS.md` or new `docs/BACKUP_POLICY.md`. Delete the legacy `.bak_legacy_*` since the env-first resolver is in production. |
| **Effort** | 15 min. |
| **Rollback** | Files recoverable from git history (yaml) or rebuild from raw sources (parquet). |
| **Phase** | D11 |

### F23. `run_live_advisory.py` docstring says "MONTHLY only" but it runs daily

| | |
|---|---|
| **Evidence** | `scripts/run_live_advisory.py:1-13`: `**CRITICAL:** This is a MONTHLY advisory system (not daily)`. Invoked daily at `daily_paper_run.py:1085-1107`. AUDIT_FINDINGS.md P2 from 2026-04-17 still open. |
| **Fix** | Rewrite docstring around two modes: monthly V0 / daily V1. The daily-V1 is the production-of-record. |
| **Effort** | 10 min. |
| **Rollback** | n/a (doc-only). |
| **Phase** | D12 |

### F24. `extract_rule_forecasts.py` docstring claims wrong runtimes

| | |
|---|---|
| **Evidence** | Phase E. `daily_paper_run.py:965` says "FULL rebuild (cron, ~60-90 min)" — actual is 4-5 min. `daily_paper_run.py:976` says "INCREMENTAL append --since today (~3-7 min)" — actual is 14.8 min. The two are inconsistent: full path is faster than incremental path. |
| **Fix** | Either delete the incremental path entirely (E4 — saves 10-15 min and removes complexity) and always run the 5-min full rebuild, or fix the incremental path's inefficiency. Update docstrings either way. |
| **Effort** | 2 h to investigate + decide; 1 h to delete the incremental code if that's the call. |
| **Rollback** | `git revert`. |
| **Phase** | E observation 1, E4 |

### F25. Three feeds excluded from the freshness checker

| | |
|---|---|
| **Evidence** | `sysdata/crypto/required_data.py` covers 7 feeds (binance prices, macro, active addresses, market cap, sector_map, OI/LSR, HL instruments, volume). Docs say `etf_flows`, `stablecoin_supply`, `binance_premium_index_processed` are "consumed only by extractor" → not in the checker. |
| **Impact** | A stale (e.g. 30-day-old) `etf_flows.parquet` → `btc_etf_flow_trend_20` rule contributes silently-stale forecast. No alarm. |
| **Fix** | Add the three feeds to `required_auxiliary_files()` with appropriate `max_lag_days` (5 for ETF, 1 for stablecoin, 1 for premium). |
| **Effort** | 30 min. |
| **Rollback** | `git revert`. |
| **Phase** | A finding (Phase A §5) |

### F26. Manifest chain doesn't cover C4 forecast / multiplier panels

| | |
|---|---|
| **Evidence** | `manifest_chain.py:47` — `REQUIRED_STAGES = ("dataset_build", "backtest", "trade_plan")`. The C4 forecast panel and multiplier panel are pre-conditions of backtest but not tracked. |
| **Impact** | A corrupt or out-of-band-replaced multiplier panel can pass end-to-end without the chain catching it. The trade-plan 30 h check covers staleness but not integrity. |
| **Fix** | Add a `c4_panel_build` stage written by `build_c4_multiplier_panel.py` and referenced by the backtest stage. |
| **Effort** | 2 h. |
| **Rollback** | `git revert`. |
| **Phase** | A finding 8 |

---

## P3 — Speed / paper-cuts (5 findings)

### F27. `--refresh-sector-map` `--help` says "~10 minutes" but actual is "~90 min"

| | |
|---|---|
| **Evidence** | `daily_paper_run.py:223-224` (help) vs `:639` (runtime log). |
| **Fix** | Update the help text to ~90 min. |
| **Effort** | 1 min. |
| **Phase** | D16 |

### F28. `tests/test_examples.py` errors at collection (missing `ib_insync`)

| | |
|---|---|
| **Evidence** | `pytest --collect-only` errors. Upstream Carver test, irrelevant to crypto-perps. |
| **Fix** | Guard the import (`pytest.importorskip("ib_insync")`) or move to `tests/upstream/` with a custom flag in `conftest.py`. |
| **Effort** | 5 min. |
| **Phase** | C7 |

### F29. `[5]` advisory's `refresh_active_rule_aux_data` duplicates orchestrator work (~103s)

| | |
|---|---|
| **Evidence** | Phase E. `run_live_advisory.py:692-699` is invoked unconditionally even though `daily_paper_run.py:457-820` already ran the same parallel feed updates 30 minutes earlier. Adds 1.7 minutes per manual run. |
| **Fix** | When advisory is invoked from the orchestrator (likely via a new `--skip-aux-refresh` flag), skip the inner refresh. |
| **Effort** | 30 min. |
| **Phase** | E3 |

### F30. `[3k-base]` base dataset rebuilt daily from scratch (~7 min) on no-change days

| | |
|---|---|
| **Evidence** | `daily_paper_run.py:892-914` runs `build_example_dataset.py --include-api-cache` every manual run regardless of whether anything changed. `auto_rebuild_sb_dataset.py:80-107` already has the manifest-sidecar pattern. |
| **Fix** | Add a manifest sidecar to `[3k-base]` (hash of registry + max-mtime of `envs/dev/data/raw/binance/` + API cache fingerprint). Skip rebuild if unchanged. |
| **Effort** | 4 h. |
| **Phase** | E2 |

### F31. `[5]` advisory backtest takes 28 min (52% of manual flow)

| | |
|---|---|
| **Evidence** | Phase E. `run_dynamic_universe_backtest.py` is the dominant cost. No profile yet. |
| **Fix** | Profile (with `cProfile` or `py-spy`) and address top-3 hotspots. Likely candidates: forecast-rule inner iteration in pandas, position-sizing inner loops, repeated parquet reads. |
| **Effort** | 1 day to profile, 2-3 days to fix top-3 hotspots. |
| **Phase** | E1 |

---

## Summary table

| ID | Severity | One-liner | Effort |
|---|---|---|---|
| F1 | **P0** | Staleness overlay silently disabled (filename + dir mismatch) | 30 min |
| F2 | **P0** | 7 active rules silently NaN for 20-97 days while checker says OK | 3h+1d |
| F3 | P1 | C4 multiplier consumer-side hook silent on identity / missing / NaN | 1 h |
| F4 | P1 | No fail-closed for stale C4 panel at backtest stage | 30 min |
| F5 | P1 | `verify_chain` exception swallow inconsistent with chain semantics | 5 min |
| F6 | P1 | `append_equity` runs before `verify_chain` — pollutes CB on chain failure | 15 min |
| F7 | P1 | `extract_rule_forecasts.py` swallows per-(rule, instrument) exceptions | 3 h |
| F8 | P1 | `trade_plan.py` mixed config-key access | 30 min |
| F9 | P1 | No end-to-end test for `daily_paper_run.py` | 3-4 h |
| F10 | P1 | No test for >30 h panel-age fail-closed | 30 min |
| F11 | P1 | No test for `_apply_walk_forward_multiplier` silent-fallback branches | 1 h |
| F12 | P1 | No "active rules must fire when feeds are fresh" liveness test | 2 h |
| F13 | P1 | No test for `[6] append_equity` after `[7b] verify_chain` ordering | (with F9) |
| F14 | P1 | `test_macro_signal_rules.py` smoke tests silently skip due to path drift | 15 min |
| F15 | P2 | `prestage_daily.py` shadow code (469 lines) | 30 min |
| F16 | P2 | CLI flag combinatorics → `--mode {cron, manual, dryrun}` | 1 h |
| F17 | P2 | Path resolver duplicated across two sites | 1 h |
| F18 | P2 | YAML auto-mutation by orchestrator | 1.5 h |
| F19 | P2 | `out/` 9.1 GB, 262 subdirs lifecycle | 30 min |
| F20 | P2 | `config/` clutter (47 YAMLs, 2 active) | 30 min |
| F21 | P2 | 11 stale top-level docs | 15 min |
| F22 | P2 | `.bak` files in tracked dirs | 15 min |
| F23 | P2 | `run_live_advisory.py` "monthly only" docstring | 10 min |
| F24 | P2 | `extract_rule_forecasts.py` runtime docstrings wrong + incremental slower than full | 2-3 h |
| F25 | P2 | Three feeds excluded from freshness checker (etf_flows, stablecoin, basis) | 30 min |
| F26 | P2 | Manifest chain doesn't cover C4 panels | 2 h |
| F27 | P3 | `--refresh-sector-map` help mismatch | 1 min |
| F28 | P3 | `test_examples.py` collection error | 5 min |
| F29 | P3 | Advisory aux refresh duplicates orchestrator work (~103s) | 30 min |
| F30 | P3 | `[3k-base]` base dataset rebuild manifest shortcut (~7 min/day) | 4 h |
| F31 | P3 | `[5]` advisory backtest 28 min — needs profiling | ~3-4 days |

---

## Recommended fix order

A single follow-on session (~1 day) can knock down most of P0 + the cheap P1s:

1. **F1** (staleness overlay path mismatch) — 30 min — closes the only P0 with a quick cure.
2. **F5** (verify_chain exception swallow) — 5 min — trivial.
3. **F6** (equity ordering) — 15 min — trivial.
4. **F8** (trade_plan config-key mix) — 30 min — clean refactor.
5. **F23** (`run_live_advisory.py` docstring) — 10 min.
6. **F27** (`--refresh-sector-map` help) — 1 min.
7. **F22** (.bak files) — 15 min.
8. **F21** (stale top-level docs) — 15 min.
9. **F19** (`clean_output_dir.py --apply`) — 30 min.
10. **F25** (three feeds into freshness checker) — 30 min.
11. **F3** (C4 multiplier visibility) — 1 h.
12. **F4** (consolidate panel-age check) — 30 min.
13. **F10, F11, F14, F28** (test gaps) — 2 h total.

That's about 6 hours covering 13 findings.

A **second session** (~1 day) for the deeper items:

14. **F2 part (a) — visibility** — 3 h.
15. **F9 + F13** (orchestrator E2E test) — 4 h.
16. **F15** (delete `prestage_daily.py`) — 30 min.
17. **F17** (consolidate path resolvers) — 1 h.

Then the **investment-grade items** that need their own sessions:

- **F2 part (b)** — root-cause OI / BTC-dominance silent rules — 1 day.
- **F31** — profile and address advisory backtest — 3-4 days.
- **F24** + **F30** — runtime reform — 2-3 days.

**End state if all P0/P1 items fixed:** the live system has zero silent-stale data paths, every fail-closed branch has a test, the operator sees C4 / rule-liveness state in every run's notification, and the manual flow drops from ~54 min to ~10–15 min.
