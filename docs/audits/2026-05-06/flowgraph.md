# Phase A — Definitive Flowgraph

Source-of-truth: code as of `develop` HEAD `cd9156cc` (2026-05-06). Every claim cites `file:line`. Where the active plan or `AUDIT_FINDINGS.md` (2026-04-17) disagrees with the code, the code wins.

---

## 1. Entry points

### 1.1 22:00 UTC cron — `daily_paper_run.py --non-binance-only`

- **Scheduler:** launchd plist `~/Library/LaunchAgents/com.nathanieldavis.paper-trading.plist:5-23`. Fires at system-local Hour=18 (`StartCalendarInterval`, plist:45-51), which is 22:00 UTC during DST and 23:00 UTC during EST (plist:36-43 acknowledges the 1h drift).
- **Command:** `python3.10 scripts/daily_paper_run.py --config config/crypto_perps_1k.yaml --non-binance-only --notify` (plist:15-23). `RunAtLoad=false`, `KeepAlive=false` (plist:60-65).
- **Env:** `TZ=UTC` for log strings only — does NOT affect when launchd schedules (plist:28-43).

### 1.2 00:00 UTC manual — `daily_paper_run.py` (no `--non-binance-only`)

- Same script, run by the user after VPN is up. The `--non-binance-only` flag gates which branches execute (`scripts/daily_paper_run.py:239-248`).

### 1.3 `scripts/prestage_daily.py` — **shadow / unused**

Same nine `[3b–3m]` parallel steps as the cron path, but **the launchd plist does not invoke it** (plist:18 invokes `daily_paper_run.py`). It also runs `[3l]` SB rebuild unconditionally (`scripts/prestage_daily.py:422`), where `daily_paper_run.py --non-binance-only` skips `[3l]` because cron has no fresh klines (`scripts/daily_paper_run.py:924-925`). This script is structurally redundant with the cron path. **D1 simplicity finding.**

---

## 2. Cron flow (22:00 UTC, `--non-binance-only`)

```
launchd  → daily_paper_run.py --non-binance-only
          ├ [0/10] HL pre-sync                          SKIP (--non-binance-only)
          ├ [1/10] read equity                          SKIP
          ├ [2/10] CB pre-check                         SKIP
          ├ [3/10] Binance klines + funding             SKIP
          ├ ── lock acquired @ live/.daily_run.lock (atomic_io:74-123)
          ├ requirements = required_auxiliary_files(config, env_root)   [3:455]
          │
          ├ ThreadPoolExecutor(max_workers=4) ── [3b–3m] parallel:
          │   ├ [3b]  macro_factors.parquet              yfinance         WARN-only
          │   ├ [3c]  active_addresses + market_cap      CoinMetrics      WARN-only
          │   ├ [3d]  binance_oi_processed.parquet       SKIP (non-binance-only)
          │   ├ [3e]  binance_volume_daily.parquet       SKIP
          │   ├ [3f]  sector_map.json                    CoinGecko (≥30d)  WARN-only
          │   ├ [3g]  hyperliquid_instruments.json       HL info API       WARN-only
          │   ├ [3j]  etf_flows.parquet                  yfinance          WARN-only
          │   ├ [3k]  stablecoin_supply.parquet          DefiLlama         WARN-only
          │   └ [3m]  binance_premium_index_processed    SKIP (non-binance-only)
          │
          ├ write_required_data_status() → required_data_status.json    [3:823-835]
          ├ [3i] patch dataset_as_of_date in raw_data_status_v1.json    [3:850-867]
          ├ [3k-base] base 538-registry rebuild          SKIP (non-binance-only) [3:882-883]
          ├ [3l] SB-corrected dataset auto-rebuild       SKIP (non-binance-only) [3:924-925]
          ├ [3n] C4 forecast feature panel               FULL rebuild ~60–90 min  [3:963-972]
          ├ [3o] C4 multiplier panel                     SKIP (non-binance-only — manual is authoritative) [3:1002-1007]
          ├ [4]  doctor preflight                        SKIP
          ├ [5]  advisory                                SKIP
          ├ [6]  append equity                           SKIP
          ├ [7]  CB re-evaluate                          SKIP
          ├ [7b] manifest chain verify                   SKIP (gated by !args.non_binance_only) [3:1140]
          ├ [8]  parse trade plan                        num_trades=0
          ├ [9]  notification (title="📊 Non-Binance Data Update OK"|"⚠️ … Warnings")  [3:1191-1199]
          └ [10] write paper_run_latest.log              [3:1225-1226]
```

`[3]` = `scripts/daily_paper_run.py`.

The cron's only real work is **[3b/c/f/g/j/k]** parallel fetches + **[3n]** the slow C4 forecast-panel full rebuild. Everything else is `SKIP (--non-binance-only)`. The launchd plist runs at 22:00 UTC; the [3n] full rebuild typically lands by ~23:30 UTC and is the gate for the manual run's `--since today` incremental append.

## 3. Manual flow (00:00 UTC, no `--non-binance-only`)

```
user → daily_paper_run.py --config ... --notify
       ├ [0/10] HL pre-sync                         scripts/sync_hl_positions.py    [3:329-347]
       │       ↳ writes envs/dev/live/current_positions.csv
       │       ↳ failure: WARN, continue with stale state                            [3:343-345]
       ├ [1/10] read equity                         envs/dev/live/current_equity.txt [3:349-366]
       │       ↳ AUTO-PATCHES config: notional_trading_capital := equity × leverage_multiple [3:368-391]
       │       ↳ failure: FAIL-CLOSED (return 1)                                     [3:357-366]
       ├ [2/10] CB pre-check                        circuit_breaker.check()          [3:396-419]
       │       ↳ if state=triggered: FAIL-CLOSED                                     [3:407-416]
       ├ [3/10] Binance klines + funding            scripts/update_data_daily.py     [3:424-453]
       │       ↳ --output-report envs/dev/out/raw_data_status_v1.json
       │       ↳ exit 3 (no VPN): WARN, continue                                     [3:438-442]
       │       ↳ other non-zero: FAIL-CLOSED                                         [3:443-451]
       │
       ├ [3b–3m] parallel ThreadPoolExecutor(max_workers=4)  ── same as cron:
       │   [3b] macro · [3c] CoinMetrics · [3d] OI/LSR · [3e] volume
       │   [3f] sector_map · [3g] HL · [3j] ETF · [3k] stablecoin · [3m] premium index
       │   ─ all WARN-only on failure
       │
       ├ [3g'] write_required_data_status → out/paper_<today>/required_data_status.json [3:823-835]
       ├ [3i] patch dataset_as_of_date in raw_data_status_v1.json                     [3:850-867]
       │
       ├ [3k-base] base 538-registry rebuild        scripts/build_example_dataset.py [3:892-914]
       │       ↳ output: data/dataset_538registry_6yr_jagged.parquet
       │       ↳ failure: WARN-only                                                   [3:910-912]
       ├ [3l] SB-corrected auto-rebuild             scripts/auto_rebuild_sb_dataset.py [3:923-937]
       │       ↳ no-op if base SHA256 + graveyard fingerprint unchanged
       │           (auto_rebuild_sb_dataset:80-107)
       │       ↳ writes manifest sidecar                                              [auto_rebuild:110-141]
       │       ↳ failure: WARN-only
       ├ [3n] C4 forecast feature panel             scripts/extract_rule_forecasts.py [3:954-990]
       │       ↳ Manual mode: --since <today_iso>, INCREMENTAL append, ~3–7 min      [3:973-984]
       │       ↳ atomic parquet write (extract_rule_forecasts:172-177)
       │       ↳ failure: WARN-only; also gates [3o] (skipped if c4_forecast_rc != 0) [3:1008-1009]
       ├ [3o] C4 multiplier panel                   scripts/build_c4_multiplier_panel.py --incremental [3:1010-1035]
       │       ↳ load_latest_fit (c4_xgboost_combiner:549-622) — schema-validated
       │       ↳ predict_today_only (c4_xgboost_combiner:625-649)
       │       ↳ atomic write (build_c4_multiplier_panel:320-323)
       │       ↳ month-boundary detect → inline retrain + save_fit (build_c4:259-296)
       │       ↳ failure: WARN-only                                                   [3:1024-1026]
       │
       ├ [4]  doctor preflight                      scripts/doctor_live_ops.py       [3:1040-1073]
       │       ↳ exit 2: FAIL-CLOSED   ↳ exit 1: WARN+continue                       [3:1060-1071]
       ├ [5]  ADVISORY                              scripts/run_live_advisory.py     [3:1078-1107]
       │       ↳ --skip-data-update --use-dynamic-universe --base-dataset data/dataset_538registry_6yr_jagged.parquet
       │       ↳ failure: FAIL-CLOSED (return 1)                                      [3:1097-1105]
       │       │
       │       │  Inside run_live_advisory.py:
       │       │  ├ generate run_id = new_run_id() (run_live_advisory:484)
       │       │  ├ STEP 1 data update SKIP (--skip-data-update)                      [run_live:620-621]
       │       │  ├ refresh aux + write_required_data_status → out/paper_*/required_data_status.json [run_live:702-713]
       │       │  ├ STEP 2 dataset rebuild — base+delta path                          [run_live:733-836]
       │       │  │  ↳ writes out/paper_*/dataset_latest.parquet
       │       │  ├ STAGE 1 manifest_chain.append_stage(stage="dataset_build", run_id) [run_live:843-851]
       │       │  ├ STEP 3 backtest                  scripts/run_dynamic_universe_backtest.py [run_live:856-871]
       │       │  │  ↳ --run-id propagated; backtest writes its own chain entry as STAGE 2
       │       │  └ STEP 4 trade plan                scripts/generate_trade_plan.py   [run_live:937-987]
       │       │     ↳ --run-id propagated; trade plan writes STAGE 3
       │       │     ↳ --data-status passed iff out/paper_*/raw_data_status.json exists [run_live:957-963]
       │       │     ↳ ⚠️ FILE NEVER WRITTEN in --skip-data-update path: see §4.1 below
       │       │     ↳ inside trade_plan.generate_trade_plan: C4 panel age check 30h FAIL-CLOSED [trade_plan:454-486]
       │       │     ↳ load_actual_positions FAIL-CLOSED on non-zero contracts w/o mark price [trade_plan:75-91]
       │
       ├ [6]  append equity to history              circuit_breaker.append_equity   [3:1116, circuit_breaker:100-119]
       │       ↳ idempotent on (date, equity); atomic_write_csv
       ├ [7]  CB re-evaluate                        circuit_breaker.check()          [3:1126-1131]
       │       ↳ triggered: WARN added but DOES NOT block notification               [3:1129]
       ├ [7b] manifest_chain.verify_chain           sysdata.crypto.manifest_chain    [3:1140-1173]
       │       ↳ chain file missing: FAIL-CLOSED                                     [3:1145-1153]
       │       ↳ chain integrity issues: FAIL-CLOSED                                 [3:1155-1163]
       │       ↳ chain check raises generic exc: WARN-only (BUG?)                    [3:1172-1173]
       ├ [8]  parse trade plan                      glob + read_csv                  [3:1183]
       ├ [9]  notification                          osascript display notification    [3:1189-1220]
       │       ↳ title flips on warnings/CB-triggered/dry-run                        [3:1203-1208]
       └ [10] write log                             live/paper_run_latest.log         [3:1226]
```

---

## 4. Stage handoff (manifest chain)

| Stage | Producer | Output recorded | Consumer verify | Verifier |
|---|---|---|---|---|
| 1 `dataset_build` | `run_live_advisory.py:843-851` | `out/paper_*/dataset_latest.parquet` | implicit (via `run_id`) | `manifest_chain.append_stage` |
| 2 `backtest` | `run_dynamic_universe_backtest.py` (passed `--run-id`, run_live:867-868) | `positions.csv`, `diagnostics.parquet` | implicit | `append_stage` |
| 3 `trade_plan` | `scripts/generate_trade_plan.py` (passed `--run-id`, run_live:952-953) | `trade_plan_*.csv` | implicit | `append_stage` |
| **end-to-end** | n/a | n/a | `daily_paper_run.py:1140-1173` | `manifest_chain.verify_chain()` |

`verify_chain()` re-hashes every input/output recorded in the latest fully-complete `run_id`, returns `passed=False` on any mismatch (`manifest_chain:255-306`). Pre-`run_id` legacy entries are skipped (`manifest_chain:280-285`). Two runs in the same UTC day get distinct `run_id`s and only the latest fully-complete run is verified (`manifest_chain:223-252`).

`verify_input_against_upstream()` also exists (`manifest_chain:151-184`) for stages that want to *block* on a hash mismatch at start of stage. **No call sites grep up in the code.** This means downstream stages currently only get end-to-end verification at the *end* of the run, not at their own entry. Possible P1.

### 4.1 Staleness overlay wiring — likely BROKEN

- `daily_paper_run.py:434` writes the data-status to `env.resolve("out") / "raw_data_status_v1.json"` (env-level out/).
- `run_live_advisory.py:957` looks for `output_dir / "raw_data_status.json"` (no `_v1` suffix, **paper_<today>** subdir).
- `run_live_advisory.py` is invoked with `--skip-data-update` (`daily_paper_run.py:1091`), so its own `update_data_daily.py` call that *would* write the right file is skipped (`run_live:620-621`).
- The `daily_paper_run.py` patch step `[3i]` (lines 850-867) only patches the `_v1` file — it does not copy or symlink to `output_dir / raw_data_status.json`.
- Net: `data_status_path.exists()` is **False** at `run_live:958`; line 960-963 logs "Staleness overlay skipped" and `--data-status` is not passed to `generate_trade_plan.py`.

`AUDIT_FINDINGS.md` P0-3 (2026-04-17) claimed "fixed" by adding `--output-report` to `update_data_daily.py`. It IS now passed (line 434), but the consumer expects a different filename in a different directory. **High-priority candidate finding for Phase B probe verification.**

---

## 5. Freshness matrix

| Feed | Producer step | Consumer | doc max_lag | required_data.py max_lag | Failure mode |
|---|---|---|---|---|---|
| binance_klines + funding | `[3]` `update_data_daily.py` | base dataset / extractor | 1 | 1 (`required_data:130-135`) | `[3]` raises non-3 → FAIL-CLOSED; warned by `[3g]` checker |
| macro_factors.parquet | `[3b]` | `dxy_momentum_16`, `us10y_momentum_16`, `oil_momentum_16` | 3 | 3 (`required_data:138-144`) | WARN-only |
| active_addresses.parquet | `[3c]` | `xs_activity` | 1–2 | 2 (`required_data:146-152`) | WARN-only |
| market_cap.parquet | `[3c]` | `xs_val` | 1–2 | 2 (`required_data:154-160`) | WARN-only |
| binance_oi_processed.parquet | `[3d]` | OI/LSR rules | 1–2 | 2 (`required_data:170-176`) | WARN-only |
| binance_volume_daily.parquet | `[3e]` | `volume_price_divergence` | 1 | 2 (`required_data:186-192`) | WARN-only |
| sector_map.json | `[3f]` (~30d) | `inter_sector`, `mrinasset` | static | None static (`required_data:162-168`) | "named sector count <2" warn (`required_data:285-291`) |
| hyperliquid_instruments.json | `[3g]` | exchange filter | 2 | 2 (`required_data:178-184`) | WARN-only |
| etf_flows.parquet | `[3j]` | `btc_etf_flow_trend_20` | 3–5 | **NOT IN CHECKER** | silent stale |
| stablecoin_supply.parquet | `[3k]` | `stablecoin_supply_trend_32` | 1 | **NOT IN CHECKER** | silent stale |
| binance_premium_index_processed | `[3m]` | `basis_mr_5` | 1 | **NOT IN CHECKER** | silent stale |
| forecast_panels_122/forecasts.parquet | `[3n]` | C4 multiplier build | n/a | n/a | WARN-only at `[3n]`; downstream age check on multiplier panel |
| c4_multiplier_panel_h20.parquet | `[3o]` | combine stage + trade plan | n/a | n/a | **trade_plan.py:476-486 FAIL-CLOSED at >30h**; combiner identity-fallback (silent) |
| dataset_538registry_6yr_jagged.parquet | `[3k-base]` | `[3l]` SB rebuild | n/a | n/a | WARN-only; downstream chain hashes catch a corrupt write |
| dataset_sb_corrected_6yr_jagged.parquet | `[3l]` | `[3n]` extractor | n/a | n/a | WARN-only |

**Three feeds with no freshness gate and no max_lag**: `etf_flows`, `stablecoin_supply`, `premium_index`. The doc says "consumed only by extractor" — true — but a stale extractor input silently produces stale rule forecasts that flow into the multiplier and into trade plans. P1 candidate.

The `forecast_combine_gated._apply_walk_forward_multiplier` consumer (`forecast_combine_gated:130-160`) returns identity (no-op) when:
- panel path config key is None,
- `instrument_code not in panel.columns`,
- ffill/fillna(1.0) fills NaN cells.

**No log line is emitted on any of these branches.** Backtest runs would silently ignore the multiplier with no signal in the run log. The 30h panel age check in `trade_plan.py` does fire — but only at trade-plan generation, not during the backtest stage. P1 candidate.

---

## 6. Reconciliation of `AUDIT_FINDINGS.md` (2026-04-17) vs. current code

| AUDIT entry | 2026-04-17 line refs | Current state | Verdict | Evidence |
|---|---|---|---|---|
| P0-1: orchestrator mixes root live with `envs/dev` | daily_paper_run.py:42-47, 257-262 | `LiveOpsEnvironment` resolved at top of main; `--env`/`--env-root`/`env_args` propagated to every subprocess | **FIXED** | `daily_paper_run.py:99-105, 249-261, 274-281, 341, 436, 1056, 1095` |
| P0-2: advisory failure does not fail the run | daily_paper_run.py:396-400, 458-462 | Non-zero advisory exit → log + notification + `return 1` | **FIXED** | `daily_paper_run.py:1097-1105` |
| P0-3: staleness overlay skipped in daily path | daily_paper_run.py:257-262, run_live:614-619 | `--output-report` IS now passed (`daily_paper_run:434`), but it writes `raw_data_status_v1.json` in env-level `out/`; advisory expects `raw_data_status.json` in `out/paper_<today>/`; advisory uses `--skip-data-update` so never writes its own | **OPEN — likely broken (filename + dir mismatch)** | `daily_paper_run.py:434, 1091`; `run_live_advisory.py:957-963`; see §4.1 |
| P0-4: gross leverage not enforced | trade_plan.py:629-651 | `sanity_checks["checks"]` still has no `gross_leverage` entry; `validate_positions_file` still doesn't call `validate_gross_leverage` | **DEFERRED-BY-PLAN** (groovy-napping-wilkinson.md A1: out-of-scope at $25K notional, IDM cap + vol-targeting adequate) | `trade_plan.py:737-771`; plan §"Out of scope" |
| P0-5: missing mark price for orphans hides exposure | trade_plan.py:52-62 | Non-zero contracts with no/zero mark price → ValueError listing the orphan instrument(s) | **FIXED** (matches plan A2) | `trade_plan.py:75-91` |
| P1-1: dynamic diagnostics shape incompatible with trade-plan loader | run_dynamic_universe_backtest.py:809-821, trade_plan.py:114-123, 570-574 | **NOT VERIFIED** in this read; needs check of current `run_dynamic_universe_backtest.py` diagnostics shape against `load_backtest_diagnostics` (trade_plan.py:138-159) — current loader still does `df.index.get_level_values(0) == as_of_dt` | **OPEN — TO VERIFY** | `trade_plan.py:138-159`; verify producer in Phase B |
| P1-2: aux factor files written where dynamic backtest won't auto-discover | data/ vs envs/dev/data/ | `extract_rule_forecasts.py:67-123` resolves env-first/repo-fallback for every aux feed; `daily_paper_run.py:485-727` writes everything to `env_data_dir = env.env_root / "data"`; the dynamic backtest also runs through `parquetCryptoPerpsSimData` which may have its own resolver | **MOSTLY FIXED** but resolver duplication is a complexity smell | `extract_rule_forecasts.py:67-123`; `required_data.py:24-32` |
| P1-3: direct `run_live_advisory.py` passes unresolved `None` paths | run_live_advisory.py:450-470 | Need to re-check current `run_live_advisory.py:580-700` data-update branch; daily_paper_run path always uses `--skip-data-update` so the bug doesn't bite via that path | **TO VERIFY** | `run_live_advisory.py:619-683` |
| P1-4: tests not protecting live trade-plan surface | tests/test_trade_plan.py, tests/test_live_advisory_integration.py | DEFERRED to Phase C | **TO VERIFY** | run pytest in Phase C |
| P2: docs say monthly only | run_live_advisory.py:5-13, 169-184 | TO READ | **TO VERIFY** | run_live_advisory.py:1-50 |

**Key insight:** P0-1, P0-2, P0-5 are clearly fixed. P0-3 (staleness overlay) appears half-fixed and likely silently broken — Phase B should probe this. P0-4 (gross leverage) is explicitly deferred. P1 items mostly need verification probes.

---

## 7. Cross-cutting correctness observations (Phase A)

1. **Staleness overlay path mismatch (P0 candidate).** §4.1 above. Phase B probe: synthesize a `raw_data_status_v1.json` with `staleness_days=2` for one instrument, run end-to-end, assert the overlay was applied (or wasn't). If wasn't, this is a P0.
2. **Combine-stage silent fallback to identity multiplier.** `forecast_combine_gated._apply_walk_forward_multiplier` (forecast_combine_gated:130-160) has 3 silent-fallback branches with no log. If C4 model degrades to `is_uninformative` (best_iter==0) the multiplier becomes identity for *every* instrument (`c4_xgboost_combiner:641-643`), still silently. P1.
3. **No fail-closed at backtest stage for stale C4 panel.** Only `trade_plan.py:454-486` enforces 30h. The backtest itself can run with a panel from a week ago, modulating forecasts wrongly, and only the trade-plan stage flags the staleness. P1.
4. **`extract_rule_forecasts.py:281-291` swallows per-rule per-instrument exceptions.** A specific rule failing for a specific instrument disappears with no log. With 122 rules × ~470 instruments, this is a lot of silent NaN territory. P1.
5. **`daily_paper_run.py:1172-1173` chain check exception is swallowed as WARN.** Every other manifest-chain failure mode is fail-closed; an unanticipated exception in `verify_chain()` becomes a warning. Inconsistent. P1.
6. **`circuit_breaker.append_equity` runs at step [6/10] BEFORE `verify_chain` at [7b/10].** If verify_chain fails-closed at [7b], step [6] has already polluted `equity_history.csv` with a row for a run whose chain was incoherent. P1.
7. **Trade plan ↔ HL position-sync ordering at start of run.** `[0/10]` syncs HL positions BEFORE `[3]` Binance update. If HL is reachable but Binance isn't (exit 3), the trade plan generation downstream uses HL-fresh positions but stale data — masked behind the staleness overlay (which we may have broken). P1.
8. **Manifest chain does not cover C4 panels.** `dataset_build → backtest → trade_plan` is recorded; the C4 forecast panel and multiplier panel are not. They are pre-conditions of the backtest but pass through the chain unhashed. A corrupt multiplier panel can flow through end-to-end without the chain catching it. P1.
9. **`prestage_daily.py` is shadow.** Not invoked by launchd; structurally redundant with `daily_paper_run.py --non-binance-only`; differs in [3l] handling. P2 (delete or mark `# DEPRECATED`).
10. **Auxiliary path resolver duplicated.** `required_data.py:_resolve_path` (24-32) and `extract_rule_forecasts.py:_resolve` (67-71) are independent implementations of the same env-first/repo-fallback pattern. Not subtly different — fully duplicated. P2 simplicity.

---

## 8. Files read in this phase (with byte coverage)

| File | Lines | Read |
|---|---|---|
| `~/Library/LaunchAgents/com.nathanieldavis.paper-trading.plist` | 67 | full |
| `scripts/daily_paper_run.py` | 1239 | full |
| `scripts/prestage_daily.py` | 469 | full (key blocks) |
| `scripts/run_live_advisory.py` | 1120 | targeted (manifest, staleness, advisory blocks) |
| `scripts/extract_rule_forecasts.py` | 372 | full (system build + extraction + atomic write) |
| `scripts/build_c4_multiplier_panel.py` | 621 | targeted (incremental path) |
| `scripts/auto_rebuild_sb_dataset.py` | 203 | full |
| `scripts/execute_trades.py` | 292 | targeted (reconciliation block) |
| `systems/crypto_perps/trade_plan.py` | 907 | targeted (load_actual_positions, generate_trade_plan, sanity_checks) |
| `systems/crypto_perps/forecast_combine_gated.py` | 276 | full |
| `systems/crypto_perps/c4_xgboost_combiner.py` | 905 | targeted (XGB_PARAMS, save_fit/load_latest_fit, predict_today_only, _train_one_fit) |
| `systems/crypto_perps/reconciliation.py` | 191 | full |
| `sysdata/crypto/required_data.py` | 341 | full |
| `sysdata/crypto/manifest_chain.py` | 307 | full |
| `sysdata/crypto/atomic_io.py` | 124 | full |
| `sysdata/crypto/circuit_breaker.py` | 168 | full |
| `AUDIT_FINDINGS.md` | 298 | full |
| `docs/AUXILIARY_DATA_FRESHNESS.md` | (skim only) | partial |

Not yet read but listed for later phases: `scripts/run_dynamic_universe_backtest.py`, `scripts/sync_hl_positions.py`, `scripts/generate_trade_plan.py`, `systems/crypto_perps/staleness_overlay.py`, the `tests/` modules.
