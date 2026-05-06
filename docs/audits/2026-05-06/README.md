# Audit 2026-05-06 — pysystemtrade-crypto-perps

## What this is

A correctness/simplicity/speed audit of the live daily trading pipeline, conducted 2026-05-06 against `develop` HEAD `cd9156cc`. Six phases:

| Phase | Artifact | One-line summary |
|---|---|---|
| A | [`flowgraph.md`](flowgraph.md) | Definitive end-to-end flow with file:line citations + `AUDIT_FINDINGS.md` (2026-04-17) reconciliation |
| B | [`probes.md`](probes.md) | 7 behavior probes; 4 clean, 2 P0 + 1 P1 surfaced |
| C | [`tests.md`](tests.md) | 795 passed / 0 failed / 34 skipped; 7 coverage gaps |
| D | [`simplicity.md`](simplicity.md) | 17 simplicity findings; 4 P1, 12 P2, 1 P3 |
| E | [`timing.md`](timing.md) | Manual flow ~54 min, cron ~5 min; top 3 cost centers |
| F | [`findings.md`](findings.md) | Aggregated prioritized punch-list (2 P0, 12 P1, 12 P2, 5 P3) |

Reproducible probe scripts live in this directory: [`probe4_manifest_chain.py`](probe4_manifest_chain.py), [`probe5_atomic_io.py`](probe5_atomic_io.py).

## Top-line headlines

**Two P0 latent-correctness issues** that the operator should be aware of immediately:

- **F1 — Staleness overlay silently disabled.** `daily_paper_run.py:434` writes `raw_data_status_v1.json` to env-out level; `run_live_advisory.py:957` looks for `raw_data_status.json` in `paper_<today>/`. They never match. The trade-plan generator runs in V0 mode (no overlay) on every live run. Latent: bites only on a VPN-down or partial-Binance day. Fix: 30 min.
- **F2 — 7 active rules silently emit zero forecast for 20–97 days.** 4 OI rules + 3 BTC-dominance rules at nominal 1/122 weight each contribute nothing to today's combined forecast. The combiner re-normalizes silently. Freshness checker reports green. ~5.7% of declared forecast budget is silently absent. Visibility fix: 3 h. Root-cause investigation: separate session.

**Other items the operator probably wants to know:**

- **C4 multiplier today is full identity** for all 477 instruments. Documented behavior (`is_uninformative=True` when `best_iter=0` on May-1 refit), consistent with WF history (~30 of 66 monthly refits historically land at `best_iter=0`). But there is no operator-facing signal that today's run does or doesn't get the +0.126 Sharpe lift that drove the ADOPT decision.
- **AUDIT_FINDINGS.md (2026-04-17) is mostly closed**: P0-1 (env separation), P0-2 (advisory failure now fail-closed), P0-5 (orphan mark-price now raises) all FIXED. P0-4 (gross leverage) DEFERRED-BY-PLAN. P0-3 (staleness overlay) is the F1 above (still OPEN with a different mechanism than originally documented).
- **Test suite is clean** (795 passed, 0 failed) but **the orchestrator script that runs every day has zero integration tests**. Both P0s shipped because of this gap.
- **`prestage_daily.py` is shadow code** — launchd doesn't invoke it; structurally redundant with `daily_paper_run.py --non-binance-only`. ~470 lines of dead surface area. Deletion is 30 min.
- **Manual flow takes ~54 min.** 52% of that is the dynamic-universe backtest; another 27% is the C4 forecast panel that runs `--since today` slower than the docstring claims (actual 14.8 min vs documented 3-7 min). The cron's "FULL rebuild" docstring claims 60-90 min but actually runs in 5 min — the incremental path may be obsolete dead weight.

## Reading order

If you have 5 minutes, read [`findings.md`](findings.md) (especially the §"Recommended fix order" at the bottom).

If you have 30 minutes, read `findings.md` plus [`flowgraph.md`](flowgraph.md) §6 (AUDIT_FINDINGS.md reconciliation) and [`probes.md`](probes.md) §"Phase B summary."

If you have an afternoon, read all six artifacts in order A → F.

## Reproducing

The two probe scripts can be re-run any time without side effects on live state:

```
PYTHONPATH=. python3.10 docs/audits/2026-05-06/probe4_manifest_chain.py
PYTHONPATH=. python3.10 docs/audits/2026-05-06/probe5_atomic_io.py
```

Phase A's claims are verifiable from the source tree at HEAD `cd9156cc`. Phase B Probes 1A/1B/6/7 are verifiable from `envs/dev/live/paper_run_latest.log` and `envs/dev/out/raw_data_status_v1.json`. Phase E timing is verifiable from `envs/dev/live/paper_run_latest.log` and `live/launchd_stdout.log`.

## Out of scope (this audit)

- Code edits, config changes, commits.
- Lane C research adoption / re-evaluation (C2/C3/C4).
- Items the active plan explicitly defers (gross-leverage cap A1, Slack/email A4, per-symbol CB A5).
- Heavy data-mutating probes (C4 reproducibility full rebuild, replay determinism). Documented as deferred in `probes.md`.

## What's next

A follow-on session uses [`findings.md`](findings.md) §"Recommended fix order" to drive a fix sweep, ordered P0 → P3.
