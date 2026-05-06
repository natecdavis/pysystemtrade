# Phase E — Wall-clock Profile

## Method

Read live timing data from existing logs rather than re-running the pipeline (no live-state mutation). Two sources:

1. **Manual flow:** `envs/dev/live/paper_run_latest.log` — last night's full manual run (2026-05-05 21:53:40 → 22:47:47 EDT, 54 minutes).
2. **Cron flow:** `live/launchd_stdout.log` — six most recent 22:00 UTC scheduled runs.

Per-step elapsed extracted by parsing local-time `YYYY-MM-DD HH:MM:SS` markers in step bodies and computing first-stamp-to-last-stamp deltas; idle gaps between steps captured separately.

---

## Manual flow (00:00 UTC)

Run: 2026-05-05 21:53:40 → 22:47:47 EDT. Total wall-clock: **~54 minutes**.

| Step | Elapsed | % of total | Notes |
|---|---:|---:|---|
| `[0/10]` HL pre-sync | 1s | 0.0% | `sync_hl_positions.py` |
| `[1/10]` read equity | <1s | 0.0% | |
| `[2/10]` CB pre-check | <1s | 0.0% | |
| `[3/10]` Binance kline+funding update | 6s | 0.2% | no-op (data already fresh) |
| `[3b–3m]` parallel ThreadPoolExecutor (9 feeds) | ~30s | 0.9% | I/O-bound across 4 workers |
| `[3i]` patch dataset_as_of_date | <1s | | |
| **`[3k-base]` base 538-registry rebuild** | **~7m** | **13%** | `build_example_dataset.py` — full rebuild from raw klines + API cache |
| `[3l]` SB-corrected dataset auto-rebuild | <1s | | manifest hash unchanged → skipped |
| **`[3n]` C4 forecast feature panel (`--since today` incremental)** | **14.8m** | **27%** | docstring says "~3–7 min" — actual is **2–4× slower** than spec |
| `[3o]` C4 multiplier panel (incremental) | 1.0m | 1.9% | docstring says "~5–15s" — actual is **4–12× slower** than spec |
| `[4/10]` doctor preflight | <1s | 0.0% | |
| **`[5/10]` advisory (backtest + trade plan)** | **30.0m** | **56%** | breakdown below |
| `[6/10]` append equity | <1s | 0.0% | |
| `[7/10]` CB re-evaluate | <1s | 0.0% | |
| `[7b/10]` verify_chain | <1s | 0.0% | |
| `[8/10]` parse trade plan | <1s | 0.0% | |
| `[9/10]` notification | <1s | 0.0% | |

### `[5/10]` advisory breakdown (30 min)

Sub-steps from `run_live_advisory.py` log lines:

| Sub-step | Elapsed | Notes |
|---|---:|---|
| Aux feed refresh (`refresh_active_rule_aux_data`) | 103s | re-runs the same monthly+daily Binance update inside the advisory |
| Dataset rebuild (base + delta) | 0s | base ends 2026-05-06, delta empty |
| Manifest stage 1 (`dataset_build`) | <1s | |
| **Backtest (`run_dynamic_universe_backtest.py`)** | **1689s ≈ 28.2m** | **dominant cost** |
| Manifest stage 2 (`backtest`) | <1s | |
| Trade plan (`generate_trade_plan.py`) | 6s | |
| Manifest stage 3 (`trade_plan`) | <1s | |

**The backtest itself is 52% of total manual wall-clock.** Profiling that subprocess is where the next material speed-up lives.

---

## Cron flow (22:00 UTC)

Six most recent runs from `live/launchd_stdout.log`:

| Date (UTC) | Wall-clock | Log lines | Likely behavior |
|---|---:|---:|---|
| 2026-05-01 04:00 (off-schedule) | 17.1m | 142,844 | full `[3n]` rebuild (likely manual cron test) |
| 2026-05-01 22:00 | 1.9m | 331 | `[3n]` skipped (config did not yet have `walk_forward_multiplier_panel_path`) |
| 2026-05-02 22:00 | 0.6m | 332 | same — pre-promotion |
| 2026-05-03 22:00 | 0.4m | 321 | same |
| 2026-05-04 22:00 | 14.3m | 874 | partial run; some steps ran |
| **2026-05-05 22:00** | **5.3m** | **116,035** | first cron after C4 promotion (commit `8b170e33`); `[3n]` FULL rebuild ran |

### 2026-05-05 cron breakdown

```
22:00:00  run start
22:00:30  [0]-[3l] sequential setup           ≈ 30s
22:00:30  [3n] FULL rebuild start
22:05:18  [3n] FULL rebuild end               ≈ 4m 48s
22:05:18  [3o] skipped (--non-binance-only — manual is authoritative)
22:05:20  run end
```

**The cron's full forecast-panel rebuild takes ~4–5 min, not the docstring's "60-90 min" claim.** The docstring at `daily_paper_run.py:965` is wildly stale.

---

## Top three slowest serial steps

| # | Step | Wall-clock | Cap | Notes |
|---|---|---:|---|---|
| 1 | `[5]` backtest (`run_dynamic_universe_backtest.py`) | **28.2 min** | hard | the binding cost on every manual run |
| 2 | `[3n]` C4 forecast panel `--since today` | **14.8 min** | maybe ~5 min floor | re-iterates 122 rules × 469 instruments to emit today's row only |
| 3 | `[3k-base]` base 538-registry dataset rebuild | **~7 min** | unclear | rebuilds from scratch every day; no manifest-based shortcut |

## Top three "embarrassingly parallel" candidates already serial

| Step | Currently | Could be parallelized? |
|---|---|---|
| `[3k-base]` base dataset rebuild | serial (full rebuild every day) | **No — needs reform, not parallelism**. The `[3l]` SB auto-rebuild is already manifest-gated and instant when nothing changed; `[3k-base]` lacks the same shortcut. Adding a base-dataset manifest sidecar (input klines hash + API cache fingerprint) to skip the rebuild when unchanged could save ~7 min/day on most runs. |
| `[3n]` incremental forecast panel | serial per (rule, instrument) inner loop | **Yes** — `extract_rule_forecasts.py:278-291` is `for rule: for inst: try: get_capped_forecast(...)`. The bulk is the System pipeline iteration; the inner extract is independent per (rule, inst). Wrapping in a `ThreadPoolExecutor` is plausible for the today-only path (small payload) but not safe for the full path without rework. |
| `[5]` backtest internals | serial single subprocess | **Probably yes, with rework.** The dynamic universe backtest is monolithic; profiling needed before any parallelism claim. |

---

## Floor estimates

| Step | Current | Estimated floor | Cost to close gap | Why |
|---|---:|---:|---|---|
| `[5]` backtest | 28.2m | unknown — first profile, then estimate | ~1 day to profile + ~3 days to address top-3 hotspots | `run_dynamic_universe_backtest.py` runs the full Carver-style pipeline over 469 instruments × 6 yr history; suspect candidates are forecast-rule iteration in pandas, position-sizing inner loops, or repeated parquet reads. |
| `[3n]` `--since today` | 14.8m | ~3–5 min | ~half day | The bulk is system construction (one-time) + per-(rule, instrument) iteration. The today-only path could skip rules whose data feeds said "ok, lag=0" via the freshness checker, instead of re-iterating all 122 × 469. Or wrap inner loop in a `ThreadPoolExecutor`. |
| `[3k-base]` base dataset rebuild | ~7m | ~30s when unchanged | ~half day | Add a manifest sidecar (hash of registry + API cache + last raw kline mtime). Skip rebuild when unchanged. Same pattern as `auto_rebuild_sb_dataset.py:80-107`. |
| `[3o]` multiplier incremental | 1m | ~5–15s | ~quarter day | Incremental path reads existing live panel (1.4 MB), runs `predict_today_only` for 477 instruments, atomic-rewrites. The 1-min cost is suspicious; profile to find the bottleneck (likely `build_feature_panel` for today even though the docstring claims it's fast). |
| `[3] → [3n]` gap | ~8.2m | already covered | none | Mostly `[3k-base]`; addressing that addresses this. |
| `[5] → [6]` post-advisory | <1s | already fine | none | |

---

## Cross-cutting observations

1. **Docstring drift is misleading.** Two stale claims found:
   - `daily_paper_run.py:965` says "Mode: FULL rebuild (cron, ~60-90 min)" — actual cron full rebuild is **4–5 min**.
   - `daily_paper_run.py:976` says "Mode: INCREMENTAL append `--since today` (~3-7 min)" — actual manual incremental is **14.8 min**.
   - The two are inconsistent: the *full* path is faster than the *incremental* path. Either the incremental path has regressed, or the full path's "60-90 min" was always pessimistic. **Worth investigating.** If the full path really does run in 5 min, just always do full rebuilds — incremental path is dead weight.

2. **The largest single win is profiling the backtest.** `run_dynamic_universe_backtest.py` consumes 28 of 54 minutes. No other change comes close.

3. **`[3k-base]` daily-from-scratch rebuild is wasteful on no-change days.** A manifest sidecar (same pattern as `auto_rebuild_sb_dataset.py`) would skip ~7 min on most days. The base dataset only changes when new instruments are added to the registry or the Binance raw data is regenerated.

4. **The pipeline's narrow path is sequential.** `[3]` → `[3k-base]` → `[3l]` → `[3n]` → `[3o]` → `[4]` → `[5]` → `[6]` → `[7]` → `[7b]` are all strict sequential dependencies. The only parallelism is the `[3b–3m]` ThreadPoolExecutor (already there). Speed-ups have to come from per-step efficiency.

5. **The `[5]` advisory's first sub-step (`Refreshing registry from CoinGecko` 103s) duplicates `[3]`/`[3b–3m]` work.** `daily_paper_run.py:1091` already passed `--skip-data-update` to advisory; but `refresh_active_rule_aux_data` (called inside `run_live_advisory.py:692-699`) re-runs aux refreshes that the orchestrator already did 30 minutes earlier. Plausible **103-second cheap win.**

6. **Cron variability is dramatic.** Pre-C4-live the cron took 0.4–1.9 min; post-promotion it takes 5.3 min. Any future feature that adds another minute to the cron compounds, eventually colliding with the next-day `00:00 UTC` manual-run window. Worth establishing a soft budget (e.g., "cron must finish by 23:00 UTC = 1 hour after launch").

---

## Phase E summary — recommended speed work, ranked

| Rank | Item | Wins | Effort | Why this order |
|---|---|---|---|---|
| **E1** | Profile `[5]` backtest (`run_dynamic_universe_backtest.py`) | up to 28 min | 1 day for profile, 2-3 days for fix | Largest absolute cost; everything else is incremental once this is profiled. |
| **E2** | Add manifest-sidecar shortcut to `[3k-base]` | ~7 min on no-change days (most days) | 4 hours | Pattern exists in `auto_rebuild_sb_dataset.py`; trivial to copy. |
| **E3** | Skip `refresh_active_rule_aux_data` in advisory when called from `daily_paper_run.py` | ~103s | 30 min | The orchestrator already did this work; redundant. |
| **E4** | Reconcile `[3n]` docstring vs reality + decide incremental-or-full | ~10 min if full path always wins | 2 hours | If full rebuild is 5 min, deleting incremental simplifies code AND removes 14-15 min from manual. |
| **E5** | Address `D5` (silent C4 fallback log) and `D17` (rule-liveness visibility) | no wall-clock; cognitive | (already in Phase D) | These don't speed the pipeline; they speed *operator decision-making*. |
| **E6** | Tune `--parallel-workers` from 4 → 8 for the `[3b–3m]` block | <30s on a normal day | 5 min | Diminishing returns; only helps on heavy-fetch days. |

**Total available wins:** about 35–45 minutes from the manual flow if E1–E4 land — taking it from ~54 min down to ~10–15 min. The cron flow is already in good shape.
