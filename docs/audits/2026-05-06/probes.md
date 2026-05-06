# Phase B — Behavior Probes

All probes run either against the live env's persisted artifacts (read-only) or in `/tmp/probe*_*` tmpdirs. **No probe writes to `live/*`, `data/c4_models/`, or any HL endpoint.** A scratch worktree exists at `/tmp/audit-worktree` (HEAD `cd9156cc`) but most probes here exercise unit-level imports of the main repo's code rather than re-running the full pipeline.

| Probe | Subject | Outcome | Severity |
|---|---|---|---|
| 1A | Staleness overlay path mismatch (filename + dir) | **CONFIRMED BROKEN** — `--data-status` never passed to trade-plan generator | **P0 (latent, gated on VPN-down day)** |
| 1B | Are stale instruments currently masking risk? | NO — all 496 instruments report `staleness_days=0` | informational |
| 4  | Manifest-chain teeth | All 6 sub-checks pass | clean |
| 5  | Atomic IO + lock + equity dedup | All 11 sub-checks pass | clean |
| 6  | C4 live multiplier panel coverage | Panel is up-to-date; last 6 days emit identity (1.0) for 477/477 instruments — *documented* `is_uninformative` branch with no operator visibility | **P1** |
| 7  | Per-rule liveness vs `forecast_weights` declaration | **7 active rules silently NaN for 20–97 days** while freshness checker reports `binance_oi_lsr: status=ok` | **P0** |

---

## Probe 1A — Staleness overlay is silently disabled

**Method.** Inspect the live env's `out/` tree for the two filenames the two consumers expect.

**Observation.**
- `daily_paper_run.py:434` writes `--output-report .../envs/dev/out/raw_data_status_v1.json` (env-out level).
- `doctor_live_ops.py:68` reads `env_root / 'out' / 'raw_data_status_v1.json'` ✓ — picked up.
- `run_live_advisory.py:957` reads `output_dir / 'raw_data_status.json'` (no `_v1`, in `paper_<today>/`) ✗ — does not exist.
- `run_live_advisory.py` is invoked with `--skip-data-update` (`daily_paper_run.py:1091`), so it never re-runs `update_data_daily.py` to write the file at the path it expects.

```
$ find /Users/nathanieldavis/pysystemtrade-crypto-perps/envs/dev/out/ -name "raw_data_status*"
.../envs/dev/out/raw_data_status_v1.json    # only this exists
# nothing in any paper_<date>/ subdir
```

`/Users/nathanieldavis/pysystemtrade-crypto-perps/envs/dev/live/paper_run_latest.log` lines 341344–341367 (last night's run, 2026-05-05):
```
WARNING __main__ Data status file not found: .../envs/dev/out/paper_20260506/raw_data_status.json. Staleness overlay skipped.
INFO Data status path not provided - staleness overlay skipped (V0 mode)
```

So `--data-status` was never passed to `generate_trade_plan.py` and `trade_plan.generate_trade_plan` ran in V0 mode (no overlay). The `advisory_report.txt` for the same run also says `⚠ Data status report not found`.

**Severity.** P0 latent. On a normal VPN-up day all 496 instruments have `staleness_days=0` (Probe 1B confirms), so the overlay would be a no-op. But on any day Binance is unreachable (`update_data_daily.py` exit 3, `daily_paper_run.py:438-442` continues with a warning), the trade plan would generate new exposure on data that has not been refreshed — exactly the failure mode the overlay was designed to prevent.

**Reproduction (no execution required).**
```
grep -n "raw_data_status" scripts/daily_paper_run.py scripts/run_live_advisory.py scripts/doctor_live_ops.py
```

**Recommended fix (out of scope for this audit).** Either: (a) make `daily_paper_run.py` write to `output_dir / "raw_data_status.json"` *as well*, or (b) make `run_live_advisory.py:957-963` look at the env-out `_v1` filename when in `--skip-data-update + --cadence daily` mode.

---

## Probe 1B — Are stale instruments currently masking risk?

**Method.** Read `envs/dev/out/raw_data_status_v1.json` and bucket all 496 instruments by `staleness_days`.

**Observation.**
```
expected_as_of_date: 2026-05-05
n_instruments: 496
staleness_days histogram: [(0, 496)]
instruments with staleness_days >= 2: 0
```

Today the broken overlay is dormant. Combined with Probe 1A, this means the failure is *latent*, not active. Promote to active P0 the next time `daily_paper_run.py` logs `WARNING: Binance unreachable (exit 3) — data not updated (VPN?). Continuing.`

---

## Probe 4 — Manifest-chain teeth (full)

**Method.** [`probe4_manifest_chain.py`](probe4_manifest_chain.py) — synthesizes a 3-stage chain in a tmpdir and exercises each failure mode.

**All sub-checks pass:**
- 4a/d: clean chain → `passed=True`, `stages=3`, `legacy_skipped=0`.
- 4b: tampered output → `passed=False` with `hash_mismatch` issue surfaced for the tampered file name.
- 4c: incomplete run (only 2 of 3 required stages) → `passed=False` with `no_complete_run` issue.
- 4e: recorded output deleted between record + verify → `passed=False` with `missing` issue.
- 4f: a legacy entry without `run_id` is correctly skipped, while a fresh complete tagged run still verifies green; `legacy_skipped=1`.

**Caveat (carry over from Phase A).** `daily_paper_run.py:1172-1173` swallows generic exceptions from `verify_chain` as a WARN-only. Every other manifest-chain failure mode is fail-closed, so this exception-handler creates an inconsistent envelope. Severity P1. Verifier itself is sound.

---

## Probe 5 — Atomic IO + lock + equity dedup (full)

**Method.** [`probe5_atomic_io.py`](probe5_atomic_io.py) — each sub-check on synthetic state in a tmpdir.

**All sub-checks pass:**
- 5a: `atomic_write_text/json/csv` leave no `.tmp` artifacts; content correct on roundtrip.
- 5b: `daily_run_lock` raises `LockBusy` from a contending **multiprocessing** child (cross-process verification, not just same-process re-acquire).
- 5c: lock is auto-released after the `with`-block exits; re-acquire works.
- 5d/e: `circuit_breaker.append_equity` is idempotent on `(date, equity)` and stays coherent under 100+ rapid rewrites; history stays sorted; no `.equity_history.csv.*` artifacts left behind.

---

## Probe 6 — C4 live multiplier panel: identity for 6 consecutive days

**Method.** Inspect `data/c4_multiplier_panel_h20.parquet` and `data/c4_models/h20/latest.meta.json`.

**Observation.**
- Panel shape `(1983, 477)` — 6 years × 477 instruments.
- Last 40 days dispersion (mean / std / frac == 1.0):
  - 2026-03-28: mean=0.815, std=0.039, frac=1.0 = **0.0%** (per-instrument modulation)
  - 2026-03-30 → 2026-04-30: std collapses to 0.0 (single value broadcast to every instrument — model splits driven only by portfolio-state features)
  - **2026-05-01 → 2026-05-06**: mean=1.0, std=0.0, frac=1.0 = **100.0%** (pure identity)
- Persisted fit metadata at `data/c4_models/h20/latest.meta.json`:
  - `refit_date: 2026-05-01T00:00:00`
  - `is_uninformative: True`
  - `best_iteration: 0`
  - `train_pred_iqr: 0.500000` (== `TANH_SIGMA_MIN` floor)

**Interpretation.** The May-1 monthly refit's XGBoost early-stopped at iteration 0 (no improvement over a bias-only baseline on the validation slice). Per `c4_xgboost_combiner.py:641-643`, the documented response is to emit identity. Cross-checked against `out/wf_c4_xgboost_h20_live/training_report.md` per-refit table: roughly **30 of 66** monthly refits historically produced `best_iter=0`. The current state is consistent with the WF distribution that was used to make the +0.126 Sharpe ADOPT decision — it's an averaged improvement, not a per-month guaranteed lift.

**The actual finding.** This is **not** a regression. But:
1. The combiner's `_apply_walk_forward_multiplier` (`forecast_combine_gated.py:130-160`) and `predict_today_only` (`c4_xgboost_combiner.py:641-643`) emit identity multipliers with **no log line, no operator-facing signal, no notification flag**. The 30h fail-closed in `trade_plan.py:476-486` checks file *age*, not informativeness — a fresh identity panel passes the gate.
2. The user, reviewing today's trade plan, has no way to tell whether C4 is modulating or no-op-ing.

**Severity P1.** Add a log line + a flag in the audit bundle so the operator sees "C4 multiplier today: identity (model is bias-only)" or "C4 multiplier today: mean=0.92 σ=0.21".

---

## Probe 7 — Silent stale rules vs `forecast_weights` declaration

**Method.** Read `data/forecast_panels_122/forecasts.parquet`, group by rule, find each rule's most recent date with at least one non-NaN cell. Cross-reference against `config/crypto_perps_1k.yaml`'s `forecast_weights`.

**Observation.** Today's row (2026-05-06) has only **98 of 122** rules emitting any non-NaN forecast. Of the 24 silent rules:

| Rule | Last fired | Days stale | In active `forecast_weights` (w=1/122)? |
|---|---|---|---|
| `attn_exhaustion_fade` | 2026-01-30 | 96 | **YES** |
| `attn_panic_rebound` | 2026-01-29 | 97 | **YES** |
| `crowd_deleverage_trend` | 2026-01-30 | 96 | **YES** |
| `xs_oi_attention` | 2026-02-01 | 94 | **YES** |
| `btc_dom_level_120` | 2026-04-16 | 20 | **YES** |
| `btc_dom_rotation_16` | 2026-04-16 | 20 | **YES** |
| `btc_dom_rotation_32` | 2026-04-16 | 20 | **YES** |
| `basis_mr_5` (premium index, 1d lag) | 2026-05-04 | 2 | YES (within doc lag) |
| `btc_etf_flow_trend_20` (ETF, weekend) | 2026-05-01 | 5 | YES (weekend gap, expected) |
| `stablecoin_supply_trend_32` | 2026-05-01 | 5 | YES (weekend gap, expected) |
| (other 14 rules with shorter expected gaps) | … | … | various |

**Cross-check vs freshness checker.** `envs/dev/out/paper_20260506/required_data_status.json`:
```
binance_oi_lsr: status=ok lag=0/2 latest=2026-05-05
```
**The OI parquet IS fresh** (496 of 507 instruments at max date 2026-05-05). The freshness checker is reporting truthfully. But the *rules* that consume OI data have not emitted a forecast since 2026-01-30. So the failure is at a different layer — either:
- the rule's data attachment in `parquetCryptoPerpsSimData` (e.g. `get_open_interest()`) is misrouted or silently empty,
- or the rule's lookback warmup is not satisfied even with the available OI history (data starts 2026-01-16; ~110 days of history, enough for a 60d lookback),
- or rule-side instrument-symbol mapping (e.g. `BTCUSDT_PERP` ↔ `BTCUSDT`) is broken.

The audit-level finding is independent of root cause: **7 rules in active `forecast_weights` at 1/122 weight contribute 0 to the combined forecast — for 20 to 97 days — while operator-facing freshness reports green.**

**Effect on live trades.** The combiner's NaN-renormalization (`forecast_combine_gated.py:107-114`) silently redistributes the 7 rules' nominal `7 × 1/122 ≈ 5.7%` weight budget across the 115 firing rules. Today's combined forecast is **not** the 1/122 weighting the config declares — it is a different (heavier) weighting on the firing rules. This is a real strategy drift.

**Severity P0.** The user explicitly asked "no data from any source is allowed to get stale." The freshness checker tells the operator everything is fresh. The trade plan is generated. But ~5.7% of the declared forecast budget is silently absent. Root cause needs investigation in a follow-on session — the audit deliverable is the finding plus the file/line evidence.

**Reproduction (read-only).**
```
python -c "
import pandas as pd
fc = pd.read_parquet('data/forecast_panels_122/forecasts.parquet')
today = fc.index.max()
for rule in fc.columns.get_level_values('rule').unique():
    sub = fc[rule]
    nz = sub.notna().sum(axis=1)
    last = nz[nz > 0].index.max() if (nz > 0).any() else None
    if last is not None and (today - last).days >= 7:
        print(rule, last.date(), (today - last).days)
"
```

---

## Probes deferred (data-heavy, would require running pipelines)

- **Probe 2 — replay determinism.** Cheap version: `_merge_incremental` in `extract_rule_forecasts.py:180-200` is testable on synthetic data; full version requires re-running the extractor on yesterday's snapshot, which mutates `data/forecast_panels_122/`.
- **Probe 3 — C4 reproducibility (seed=42).** Requires `build_c4_multiplier_panel.py` non-incremental rebuild. Wall-clock ~5 min; would write to `out/wf_c4_xgboost_h20_*`. Defer.
- **Synthetic per-feed failure injection (rename + run).** Each rename + run cycle is 5-15 minutes. Would need ~10 cycles. Defer.

These do not block any of the P0/P1 findings above.

---

## Files written by Phase B

- [`probe4_manifest_chain.py`](probe4_manifest_chain.py) — reproducible Probe 4 harness.
- [`probe5_atomic_io.py`](probe5_atomic_io.py) — reproducible Probe 5 harness.
- `probes.md` — this file.

The two probe scripts can be re-run any time without side effects on live state.

---

## Phase B summary — promoted findings

1. **P0 — Staleness overlay silently disabled** (Probe 1A). `daily_paper_run.py` writes `raw_data_status_v1.json` to env-out level; `run_live_advisory.py:957` looks for `raw_data_status.json` in `paper_<today>/`. Never matches. `--data-status` never passed to `generate_trade_plan`. Latent — bites on any VPN-down or partial-Binance day.
2. **P0 — Silent stale rules in active `forecast_weights`** (Probe 7). 7 rules at 1/122 nominal weight have not fired for 20–97 days. Combiner re-normalizes silently. Today's combined forecast is misweighted by ~5.7% relative to config declaration. Freshness checker reports green.
3. **P1 — C4 identity-multiplier days have no operator visibility** (Probe 6). Today's panel is pure identity (`is_uninformative=True`). Documented behavior, but `forecast_combine_gated._apply_walk_forward_multiplier` and `predict_today_only` emit identity with no log, no flag in the audit bundle, no notification cue.
4. **P1 — `verify_chain` exception-swallow inconsistency** (Probe 4 caveat). `daily_paper_run.py:1172-1173` catches generic exceptions as WARN-only, while every other manifest-chain failure mode is fail-closed.
5. **CLEAN** — atomic IO, daily-run lock, equity dedup, manifest-chain happy path, manifest-chain detection of tampering / missing files / incomplete runs / legacy entries.
