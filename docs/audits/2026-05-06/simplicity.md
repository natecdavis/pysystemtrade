# Phase D — Simplicity Audit

The bar: "would a new operator reading this for the first time understand it without a guide." Findings below are **catalogued, not fixed.** Each item: where, why it's complex, proposed change, estimated risk, estimated effort.

Severity legend: **P1** = active footgun (operator confusion likely to cause an incident); **P2** = drag (slow learning curve, accidentally-stale state); **P3** = paper-cut.

---

## D1 — `scripts/prestage_daily.py` is shadow code (P2)

**Where.** `scripts/prestage_daily.py` (469 lines, 9 step closures, full ThreadPoolExecutor scaffolding).

**Why complex.** The launchd plist (`~/Library/LaunchAgents/com.nathanieldavis.paper-trading.plist:18-22`) invokes `daily_paper_run.py --non-binance-only`, **not** `prestage_daily.py`. No shell script, runbook, or scheduled job actually calls `prestage_daily.py`. It is structurally a parallel implementation of the same nine `[3b–3m]` steps. The two scripts disagree on `[3l]` (SB-corrected dataset rebuild): `daily_paper_run.py:924-925` skips it under `--non-binance-only` (correct — no fresh klines yet); `prestage_daily.py:422` runs it unconditionally.

**Two pieces of doc-drift caused by the shadow:**
- `daily_paper_run.py:233-236` says `--skip-prestage` "skip[s] steps already completed by prestage_daily.py" — implying a workflow where the user runs prestage first. No such workflow is exercised.
- `auto_rebuild_sb_dataset.py:17`: "Wired into the 22:00 UTC pre-stage cron via prestage_daily.py" — false. The cron is `daily_paper_run.py --non-binance-only`.

**Proposal.** Delete `scripts/prestage_daily.py`. Delete `--skip-prestage` flag and its 4 use sites in `daily_paper_run.py:229-237, 479-480, 503-504, 535-536, 589-590`. Fix `auto_rebuild_sb_dataset.py:17` doc.

**Risk.** Low. The functionality lives in `daily_paper_run.py --non-binance-only`. **Effort:** ~30 min.

---

## D2 — `daily_paper_run.py` CLI flag combinatorics (P2)

**Where.** `scripts/daily_paper_run.py:200-272`, 10 args:

```
--config           (required)
--dry-run          (boolean)
--skip-cb-check    (boolean)
--notify           (BooleanOptionalAction, default True)
--refresh-sector-map  (boolean)
--skip-prestage    (boolean — see D1, vestigial)
--non-binance-only (boolean)
--env              (default "dev")
--env-root         (Path)
--parallel-workers (int, default 4)
```

**Why complex.** Six boolean flags = 64 implicit mode combinations. Most are nonsensical or never used. The actual modes the user runs:
1. **Cron** — `--non-binance-only --notify` (launchd plist).
2. **Manual** — bare `--config <path> --notify`.
3. **Test** — `--dry-run` (rare).

The semantics of `--skip-prestage` overlap with `--non-binance-only`; `--non-binance-only` already implies skipping the same steps plus more.

**Proposal.** Replace boolean flags with `--mode {cron, manual, dryrun}`. Keep `--config`, `--env`/`--env-root`, `--notify/--no-notify`, `--parallel-workers` as orthogonal knobs. `--refresh-sector-map` and `--skip-cb-check` stay as escape hatches.

**Risk.** Low — touches the orchestrator only, no other scripts depend on it. **Effort:** ~1h plus updating launchd plist + runbook.

---

## D3 — Config-key access pattern is mixed in `trade_plan.py` (P1)

**Where.** `systems/crypto_perps/trade_plan.py`, `generate_trade_plan` body:

| Line | Pattern | Behavior |
|---|---|---|
| 461-465 | `config.get(...) if isinstance(config, dict) else config.get_element_or_default(...)` | dispatches at runtime |
| 656-658 | `config.get("costs", {}).get(...)` | dict-only |
| 682-684 | isinstance dispatch (same as 461) | both shapes |
| 721-723 | `config.get("notional_trading_capital", config.get("system", {}).get("capital", 5000.0))` | dict-only |
| 731 | `config.get("idm_cap", 2.5)` | dict-only |

**Why complex.** Same function accepts both a plain dict (when called from `generate_trade_plan.py` CLI) and a Carver `Config` object (when called from a System pipeline). Two of seven config reads dispatch at runtime; the other five assume dict and would crash on a Carver `Config`. So in practice the function only works with a dict — the isinstance dispatches are defensive scaffolding for a code path that doesn't exist.

**P1 because** it makes refactoring or adding a new config key a quiet correctness footgun: "is this read from a dict or a Carver Config? both, with a fallback?"

`forecast_combine_gated.py` is consistent (10/10 use `config.get_element_or_default` because it always receives a Carver Config). `run_live_advisory.py` is consistent (always dict). The mix is concentrated in `trade_plan.py`.

**Proposal.** Standardize on dict in `trade_plan.py` (it's a CLI-callable function, dict is more natural). Remove the isinstance dispatches at lines 461-465 and 682-684.

**Risk.** Low if pytest passes (and it does — `tests/test_trade_plan.py` and `test_live_advisory_integration.py` always pass dict). Tag the API in the docstring. **Effort:** ~30 min.

---

## D4 — Path resolver duplicated across two sites (P2)

**Where.**
- `sysdata/crypto/required_data.py:24-32` — `_resolve_path(env_data_dir, filename) → Path` (env-first, repo-fallback)
- `scripts/extract_rule_forecasts.py:67-71` — `_resolve(*candidates) → str | arg_not_supplied` (returns Carver sentinel)

**Why complex.** Same intent, two implementations, different signatures, different return types (`Path` vs `str | sentinel`). When the env-first/repo-fallback policy needs to change (e.g. removing the repo fallback per Lane B5/B6 cleanup), two places need updating.

**Bonus:** `extract_rule_forecasts.py:_resolve` returns the Carver sentinel `arg_not_supplied`, which then trickles into the System constructor and silently disables aux feeds when missing. This is one of the silent-fallback paths flagged in Phase A finding 4 — the resolver isn't structurally bad, but the sentinel return masks "feed missing" as "feed disabled."

**Proposal.** One utility in `sysdata/crypto/env_paths.py` (the same module that owns `LiveOpsEnvironment`):
```python
def resolve_aux(env_data_dir: Path, filename: str, *, required: bool = False) -> Path | None:
    """env-first, repo-fallback. Returns None when missing (or raises if required=True)."""
```
Call sites: `required_data._resolve_path` becomes a thin wrapper; `extract_rule_forecasts._resolve` is replaced with the same shared helper.

**Risk.** Low. Structural mechanical refactor. **Effort:** ~1h.

---

## D5 — Silent-fallback pattern in `_apply_walk_forward_multiplier` (P1)

**Where.** `systems/crypto_perps/forecast_combine_gated.py:130-160`. Three fallback branches:

```python
if mult_path is None:                 return forecast        # 147-148
if instrument_code not in panel.cols: return forecast        # 152-153
mult.fillna(1.0)                                              # 157
```

No log lines. No counter. No flag in the audit bundle.

**Why complex.** The operator running today's trade plan has no way to tell which of the four states the system is in:
- C4 multiplier is producing per-instrument modulation,
- C4 multiplier is portfolio-level (model splits driven only by macro features),
- C4 multiplier is identity because the panel says so (Probe 6: 6 of last 6 days),
- C4 multiplier is identity because the panel doesn't cover this instrument or the panel file is missing.

Probe 6 already showed the live state is in branch 3. None of those four states surface in the operator's daily notification or `advisory_report.txt`. So the operator carries hidden uncertainty about whether the live system is running with C4 or without.

**Proposal.**
1. Log one INFO line per `get_combined_forecast` call: `c4_multiplier: instrument=BTC mode=identity|panel|missing mean=… σ=…`. (Or aggregated once per backtest.)
2. Add `c4_multiplier_state` to `audit_bundle["constraints_snapshot"]` with the per-instrument multiplier mean / coverage.
3. Optionally surface "C4 identity-only today" in the macOS notification body when 100% of cells are 1.0.

**Risk.** Low — purely additive logging. **Effort:** ~1h.

---

## D6 — Duplicate fail-closed checks for C4 panel age (P2)

**Where.**
- `trade_plan.py:454-486` raises `ValueError` if panel >30h old (fail-closed at trade-plan generation).
- `forecast_combine_gated._apply_walk_forward_multiplier` has **no** age check (silent identity fallback).

**Why complex.** Two consumers of the same panel with different staleness contracts. Backtest reads through the combiner (no check); trade-plan reads via `generate_trade_plan` (30h check). A stale panel produces silent wrong-multiplier in the backtest *and* a fail-closed error at trade-plan. Operator sees the error but the backtest already completed with bad data.

**Proposal.** Move the age check to the combiner's `_apply_walk_forward_multiplier` (or a single helper called by both). One source of truth for "panel is fresh enough to use."

**Risk.** Low — same semantics, single site. **Effort:** ~30 min, plus the test from Phase C C2.

---

## D7 — `daily_paper_run.py:368-391` mutates the version-controlled config YAML (P2)

**Where.** Step `[1/10]` reads `current_equity.txt` and then **rewrites the YAML config file** in place to set `notional_trading_capital = equity × leverage_multiple` and the matching `system.capital` field. Two regex substitutions, then `_f.write(_cfg_text)`.

**Why complex.**
- Every daily run mutates `config/crypto_perps_1k.yaml` — `git status` permanently shows this file modified, masking real config changes.
- The mutation happens after `--non-binance-only` lock acquisition but before the lock is released, so a crashed cron leaves the file in some state that may or may not match the most recent equity.
- Two scripts (run_live_advisory.py, run_dynamic_universe_backtest.py) read the config at subprocess start. They get the freshly-mutated value — but the in-memory `_cfg` dict in `daily_paper_run.py` is not updated either, so any further reads in this script use the stale value.

**Proposal.** Compute notional in memory; pass `--notional-trading-capital <value>` (or equivalent override) to the advisory subprocess. Stop mutating the YAML.

**Risk.** Low-medium — need a small CLI extension on `run_live_advisory.py` and friends. **Effort:** ~1.5h.

---

## D8 — `out/` directory lifecycle (P2)

**Status.**
- 262 subdirectories, **9.1 GB**.
- `scripts/clean_output_dir.py` was shipped (Lane B6 done) and `docs/OUTPUT_DIRS.md` exists. Apparently not run.

**Why complex.** Researchers (and Claude) constantly create new subdirs for each adoption test, sweep, ablation. After many months the dir is a giant stratigraphy. Operators looking for "what's the latest paper run?" have to glob+sort by mtime. Disk is the cheap part; cognitive overhead is the real cost.

**Proposal.**
- Add `scripts/clean_output_dir.py --apply` to a weekly cron (or scheduled by `schedule` skill).
- Move research subdirs (`out/wf_*`, `out/k_sweep*`, `out/sb_corrected_*`, etc.) to `out/_research/` so the operationally-relevant `paper_<date>/` subdirs are visible at a glance.
- Document "what should be at the top of `out/` and what is research-only" in `docs/OUTPUT_DIRS.md`.

**Risk.** Low — `clean_output_dir.py` is dry-run by default. **Effort:** ~30 min.

---

## D9 — `config/` directory clutter (P2)

**Status.** 47 YAML files. The active two are `crypto_perps_1k.yaml` and `crypto_perps_full_rules.yaml`. There are also 2 `.bak` files. The remaining ~43 are research one-shots and abandoned tests:

```
crypto_perps_15x_baseline.yaml
crypto_perps_15x_phase2.yaml
crypto_perps_30x_phase2.yaml
crypto_perps_addr_growth_fix_test.yaml
crypto_perps_baseline_v1*.yaml (3)
crypto_perps_carry_fix_test.yaml
crypto_perps_carver_static_test.yaml
crypto_perps_corr_shock_test.yaml
crypto_perps_dynamic_universe_*.yaml (2)
crypto_perps_full_rules_forecast_*.yaml (2)
crypto_perps_gated_carry_test.yaml
crypto_perps_greedy*.yaml (2)
crypto_perps_oi_*.yaml (5)
crypto_perps_phase2_v1.yaml
crypto_perps_research_superset.yaml
crypto_perps_simplified_test.yaml
crypto_perps_skew_test.yaml
crypto_perps_test_*.yaml (3)
factorial_test_*.yaml (4)
phase2_test_*.yaml (2)
test_*.yaml (5)
```

**Why complex.** The clutter blocks "which config does the daily run use?" as a one-second answer. `config/research/` already exists as a subfolder — it's just not used.

**Proposal.** Move all *_test.yaml, factorial_test_*, phase2_*, research-only configs to `config/research/`. Keep `config/` containing only: `crypto_perps_1k.yaml`, `crypto_perps_full_rules.yaml`, `CONFIG_SCHEMA.md`, `hl_account.json`, the two `.bak` files (or move them to `config/_archive/`).

**Risk.** Low — git history preserved by `git mv`. Some hard-coded paths in research notebooks will break and need a one-line fix. **Effort:** ~30 min.

---

## D10 — Top-level docs are 2–4 months stale (P2)

**Status.** 11 historical implementation/phase/testing docs at repo root, all dated Feb–March 2026:

| File | Last touched | Topic |
|---|---|---|
| `OPERATIONALIZATION.md` | 2026-02-04 | early ops plan |
| `IMPLEMENTATION_MIN_HISTORY_TEST.md` | 2026-02-20 | adoption notes |
| `IMPLEMENTATION_SUMMARY_GATED_CARRY.md` | 2026-02-20 | adoption notes |
| `NEXT_STEPS_MIN_HISTORY.md` | 2026-02-20 | follow-on items |
| `TESTING_GUIDE_FORECAST_SELECTION.md` | 2026-02-20 | testing guide |
| `TESTING_GUIDE_GATED_CARRY.md` | 2026-02-20 | testing guide |
| `OI_OVERLAY_IMPLEMENTATION.md` | 2026-02-21 | implementation log |
| `TREND_AWARE_OVERLAY_IMPLEMENTATION.md` | 2026-02-21 | implementation log |
| `PHASE2_OI_DATA_PLAN.md` | 2026-02-27 | superseded plan |
| `PHASE1_READY.md` | 2026-03-28 | adoption note |
| `TREND_AWARE_READY.md` | 2026-03-28 | adoption note |

**Why complex.** New contributors / Claude sessions read repo-root MDs for context; these documents reflect superseded designs (e.g. flat-68 → flat-122 + Carver filter migration on 2026-05-03 invalidated most of them). Mixing them with `README.md`, `CHANGELOG.md`, `CLAUDE.md`, `AGENTS.md` (current) makes navigation hard.

**Proposal.** `mkdir docs/_archive/2026-Q1/`, `git mv` the 11 stale docs there. Leave at root: `README.md`, `CHANGELOG.md`, `CLAUDE.md`, `AGENTS.md`, `CONTRIBUTING.md`, `ENVIRONMENT_SETUP.md`, `LICENSE`, `CRYPTO_MARKET_FACTORS_LIT_REVIEW.md` (research; could also move), `AUDIT_FINDINGS.md` (will become this audit's predecessor).

**Risk.** Low. Pure file moves. **Effort:** ~15 min.

---

## D11 — Stale `.bak` files in `data/` and `config/` (P2)

**Status.**
```
data/macro_factors.parquet.bak_legacy_20260506
data/etf_flows.parquet.bak_legacy_20260506
data/binance_oi_processed.parquet.bak_legacy_20260506
data/dataset_538registry_6yr_jagged.parquet.bak_20260505
data/stablecoin_supply.parquet.bak_legacy_20260506
config/crypto_perps_full_rules_flat68.yaml.bak
config/crypto_perps_1k_flat68.yaml.bak
```

**Why complex.** Backup files left in tracked directories are anti-patterns of git. Existence at all only makes sense as a rollback safety net for very-recent changes; no policy is documented for when to delete them.

**Proposal.** Add a `.bak_*` glob to `.gitignore`. Document a "delete after 30 days" policy in `docs/OUTPUT_DIRS.md` or a new `docs/BACKUP_POLICY.md`. The `.bak_legacy_*` files exist because `_resolve_path`'s repo-fallback makes them safe to delete (production reads from `envs/dev/data/`); confirmed by code-review of `required_data.py:24-32`.

**Risk.** Low — files are recoverable from git history (the .yaml.bak ones) or rebuildable from raw sources (the .parquet.bak ones). **Effort:** ~15 min.

---

## D12 — `run_live_advisory.py` docstring claims monthly-only (P2 / AUDIT P2)

**Where.** `scripts/run_live_advisory.py:1-13`:
```
"""
Live Operations Advisory System - Main Orchestrator

Single entry point for full monthly advisory workflow:
    ...
**CRITICAL:** This is a MONTHLY advisory system (not daily) due to Binance Vision
publication lag (~2-4 weeks after month end).
"""
```

**Why complex.** It's invoked daily by `daily_paper_run.py:1085-1107`. The "monthly only" claim is loud (`**CRITICAL:**`) and false. AUDIT_FINDINGS.md P2 already flagged this on 2026-04-17; still open.

**Proposal.** Rewrite docstring around two modes: monthly V0 (`--cadence monthly`) and daily V1 (`--cadence daily --use-dynamic-universe`). The latter is the production-of-record.

**Risk.** Zero — doc-only. **Effort:** ~10 min.

---

## D13 — `daily_paper_run.py:1172-1173` swallows `verify_chain` exceptions (P1)

(Already noted as a Phase A finding, restated as a complexity issue here.)

**Where.**
```python
# scripts/daily_paper_run.py:1140-1173
try:
    ...
    verify_result = verify_chain(chain_path)
    if not verify_result["passed"]:
        return 1
    ...
except Exception as exc:
    log_lines.append(f"  WARNING — chain check raised: {exc}")
```

**Why complex.** Every other manifest-chain failure mode (file missing, integrity issues) fail-closes (`return 1`). A bare `except Exception` here turns any unanticipated failure of `verify_chain` into a WARN-only and the run continues. So:
- Operator sees the trade plan and the success notification.
- Manifest-chain integrity may have actually broken silently.
- Inconsistent envelope: it's "fail-closed if we expected this failure mode, fail-open otherwise."

**Proposal.** Either:
(a) replace with `except (FileNotFoundError, json.JSONDecodeError, ManifestChainError):` — explicit list, fail-closed otherwise; or
(b) re-raise, treating any verifier exception as fail-closed (consistent with the rest of the chain semantics).

**Risk.** Tiny — change is one line. **Effort:** ~5 min.

---

## D14 — `--non-binance-only` and `--skip-prestage` overlap (P2)

(Already covered in D1 / D2; restated as a single dimension.)

`--non-binance-only` (effectively the cron mode) skips: HL pre-sync, equity read, CB pre-check, Binance update, base dataset rebuild, SB rebuild, C4 multiplier rebuild, doctor, advisory, equity append, CB re-eval, manifest verify, trade-plan parse — and also forwards `--skip-prestage`-equivalent skips into the parallel data fetch closures (lines 533, 536, 590, 644, etc).

`--skip-prestage` (per docstring intended for a hypothetical "prestage_daily.py was already run") only skips the same parallel data fetches. It is a strict subset of `--non-binance-only`'s parallel-fetch behavior.

**Proposal.** Delete `--skip-prestage`. Bundle it into D2's `--mode {cron,manual,dryrun}` if any pseudo-prestage workflow ever returns.

---

## D15 — `extract_rule_forecasts.py` exception-swallowing per (rule, instrument) (P2)

**Where.** `scripts/extract_rule_forecasts.py:281-291`:
```python
for i, rule in enumerate(active_rules, 1):
    count = 0
    for inst in instruments:
        try:
            fc = system.forecastScaleCap.get_capped_forecast(inst, rule)
            if fc is not None and not fc.dropna().empty:
                ...
        except Exception:
            pass
    print(f"  [{i:2d}/{len(active_rules)}] {rule:<35} {count} instruments")
```

**Why complex.** The bare `except Exception: pass` swallows every per-instrument rule failure, including ones that should be loud — bad rule wiring, missing data, schema mismatch. This is one of the upstream causes of Probe 7's 7-rules-silently-NaN finding.

**Proposal.** Catch only the expected exception classes (`KeyError`, `ValueError` from missing data column / instrument-not-in-aux-feed). Log the rest. Increment a counter and emit "rule X had 50/300 silent exceptions today" — that's the operator-facing signal needed.

**Risk.** Medium — extracting rules involves many narrow exception classes; some currently-thrown ones may be legitimate "skip this instrument." Need a careful enumeration before tightening. **Effort:** ~3h to do safely.

---

## D16 — `--refresh-sector-map` warns "~10 minutes" but actually says "~90 min" inside (P3)

**Where.**
- `daily_paper_run.py:223-224` flag help: "Takes ~10 minutes."
- `daily_paper_run.py:639` runtime log: "Refreshing sector map from CoinGecko (~90 min — 12s rate-limit/call × ~470 base assets …)".

**Why complex.** Operator running with `--refresh-sector-map` reads "10 min" in `--help`, sees "90 min" once it starts, doesn't know which is true. Trivial doc update.

**Proposal.** Update the flag help to ~90 min.

**Effort:** ~1 min.

---

## D17 — `forecast_weights` declares 122 active rules; only ~98 fire on a typical day (P1)

(Phase B Probe 7 finding, restated as a complexity / mental-model issue.)

The user's mental model of the live system: "all 122 rules at 1/122 weight." Reality: 24 are silently NaN today; 7 of those have been silent for 20–97 days. The combiner re-normalizes the firing set, so `forecast_weights` is a *declaration*, not a *guarantee*.

**Why complex.** The disconnect between "what the config declares" and "what actually contributes" is the kind of thing that bites you twice: once when adopting (the WF backtest used the declared weights and reflected their realized firing rates), and again when interpreting today's trade plan ("does dxy_momentum_16 contribute today? you'd have to check").

**Proposal.**
1. Add per-rule liveness to the daily notification: "Active rules today: 98/122. Silent: …".
2. The fix-the-OI-rules part is a separate engineering task; the operator-visibility piece can ship independently.

**Effort (visibility piece):** ~2h.

---

## Summary table

| ID | Severity | Topic | Effort |
|---|---|---|---|
| D1 | P2 | `prestage_daily.py` shadow code | 30m |
| D2 | P2 | CLI flag combinatorics → `--mode` | 1h + plist |
| D3 | P1 | `trade_plan.py` mixed config-key access | 30m |
| D4 | P2 | Path resolver duplicated | 1h |
| D5 | P1 | `_apply_walk_forward_multiplier` silent fallback | 1h |
| D6 | P2 | C4 panel-age check duplication | 30m |
| D7 | P2 | YAML auto-mutation by orchestrator | 1.5h |
| D8 | P2 | `out/` 9.1 GB lifecycle | 30m |
| D9 | P2 | `config/` 47-file clutter | 30m |
| D10 | P2 | 11 stale top-level docs | 15m |
| D11 | P2 | `.bak` files in tracked dirs | 15m |
| D12 | P2 | `run_live_advisory.py` "monthly only" docstring | 10m |
| D13 | P1 | `verify_chain` exception swallow | 5m |
| D14 | P2 | `--non-binance-only` vs `--skip-prestage` overlap | (with D1/D2) |
| D15 | P2 | `extract_rule_forecasts.py` exception swallowing | 3h |
| D16 | P3 | `--refresh-sector-map` help text mismatch | 1m |
| D17 | P1 | 122-rules-declared / ~98-fire visibility gap | 2h |

**Total effort if all done:** ~12 hours, well within a single dedicated session.

The four **P1** simplicity items (D3, D5, D13, D17) are the "operator confusion likely to cause incident" set. They're cheap individually but collectively they explain why the live system feels harder to reason about than its component parts.
