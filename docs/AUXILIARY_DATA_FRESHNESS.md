# Auxiliary Data Freshness

This document explains the lag profile of every non-Binance-prices auxiliary feed
the live trade-plan pipeline depends on, so an operator reading the daily log can
tell at a glance which "today's value is NaN" lines are normal-mode behavior vs.
an actual outage.

The canonical location for every feed is `envs/dev/data/<file>` (env-aware,
selected by `LiveOpsEnvironment`). The freshness checker
(`sysdata/crypto/required_data.py`) and the forecast extractor
(`scripts/extract_rule_forecasts.py`) both use an env-first, repo-fallback
resolver — if a file is present at `envs/dev/data/<file>` it wins; otherwise
they fall back to `data/<file>`. Legacy repo-root copies have been moved to
`*.bak_legacy_*` and are no longer indexed (see `MEMORY.md` 2026-05-06 entry).

## How "today is NaN" is handled

Auxiliary feeds publish on natural lags ranging from minutes to days. Rules that
depend on a specific feed have NaN values for any date where that feed has not
yet published. At forecast-combine time, `ForecastCombineGated` re-normalizes
weights across the rules that *do* fire, so a NaN row contributes 0 and the
remaining rules absorb the weight. The daily run produces a trade plan even
when some rules are NaN. **This is by design, not a bug.**

The two failure modes that *do* warrant a page:

1. The freshness checker (`[3g] Active-rule data status`) marks a feed as a
   warning when its `lag_days` exceeds the per-feed `max_lag_days` threshold.
2. A whole feed is missing on disk (file not found at either env or repo path).

Anything else — including 30+ rules being NaN on today's row — is normal.

## Per-feed reference

| Feed | Update step | Source | Typical lag | `max_lag_days` | Dependent rules (representative) | NaN-on-today behavior |
|---|---|---|---|---|---|---|
| `binance_klines` + `binance_funding` | `[3]` | Binance Vision (klines) + REST API (funding) | 1 day (Vision publishes daily at end-of-day UTC) | 1 | All EWMAC/breakout/normmom/skew/carry | Should never be NaN on today; if so, hard fail. |
| `macro_factors.parquet` | `[3b]` | yfinance (`SPX`, `DXY`, `^TNX`, `GC=F`, `^VIX`, `CL=F`) | 1–3 days (weekend gaps + delayed close prints) | 3 | `dxy_momentum_16`, `us10y_momentum_16`, `oil_momentum_16` | Today's macro rules contribute 0; combiner re-normalizes. |
| `active_addresses.parquet` | `[3c]` | CoinMetrics community endpoint | 1–2 days | 2 | `xs_activity` | XS rule fires only on instruments with current data; non-coverage rows skip. |
| `market_cap.parquet` | `[3c]` | CoinMetrics community endpoint | 1–2 days | 2 | `xs_val` | Same as above. |
| `binance_oi_processed.parquet` | `[3d]` | Binance Vision OI/LSR archives | 1–2 days | 2 | `xs_oi_attention`, `attn_exhaustion_fade`, `attn_panic_rebound`, `lsr_*`, `crowd_deleverage_trend` | OI rules contribute 0 on the as-of date; signal lights up once OI is published. |
| `binance_volume_daily.parquet` | `[3e]` | Binance Vision daily volume ZIPs | 1 day | 2 | `volume_price_divergence` | Today's row 0; backfilled tomorrow. |
| `sector_map.json` | `[3f]` (~30-day cadence) | CoinGecko categories | static (refreshed every 30 days) | n/a (kind=`json_static`) | `inter_sector`, `mrinasset`, sector index pulls | Out-of-date sector tagging routes a new instrument as `Other` — degrades gracefully. |
| `hyperliquid_instruments.json` | `[3g]` | Hyperliquid info API | minutes | 2 | `data.get_hl_cross_sectional_median_funding`, exchange-filter universe | Stale list locks universe to last-known-listed set. Hard exit if file missing. |
| `etf_flows.parquet` | `[3j]` | yfinance ETF AUM/volume → flow proxy | 3–5 days (US market days only; spot-ETF reporting catches up over the weekend) | n/a (consumed only by extractor) | `btc_etf_flow_trend_20` | Flow rule contributes 0 on weekends and ETF holidays — normal. |
| `stablecoin_supply.parquet` | `[3k]` | DefiLlama stablecoins endpoint | 1 day | n/a (consumed only by extractor) | `stablecoin_supply_trend_32` | Today's row 0 until DefiLlama publishes. |
| `binance_premium_index_processed.parquet` | `[3m]` | Binance Vision premium-index archives | 1 day | n/a (consumed only by extractor) | `basis_mr_5` | Today's row 0; backfilled tomorrow. |
| Base 538-registry dataset (`data/dataset_538registry_6yr_jagged.parquet`) | `[3k-base]` | Local rebuild from API cache | rebuilt every run | n/a | All trend/breakout/normmom/etc. (price-derived) | Should never be NaN; rebuilt as the first step before downstream consumers. |
| C4 forecast feature panel | `[3n]` | Local rebuild from feature inputs | rebuilt every run | n/a | C4 multiplier panel feeds combine stage | Day-of consumption. |
| C4 multiplier panel | `[3o]` | Local rebuild | rebuilt every run | n/a | Combine stage forecast multiplier | Day-of consumption. |

## Diagnosing a "stale data" alarm

1. Check `envs/dev/<run-dir>/raw_data_status.json` and the daily log's
   `[3g] Active-rule data status` block. Each feed shows `latest_date`,
   `lag_days`, and the configured `max_lag_days`.
2. If a feed is `warning`, look at the corresponding `[3x]` step in the same
   log — the upstream downloader logs its own pass/fail.
3. **Do not** look at `data/<file>`-prefixed paths in the repo root for
   freshness. Those are either missing or `*.bak_legacy_*`. The freshness
   checker reads from `envs/dev/data/<file>` (with repo-fallback for the few
   genuinely-static feeds like `sector_map.json`).
4. If the env file is genuinely behind: re-run the corresponding `[3x]`
   updater manually. The downloader scripts are idempotent — running them
   twice on the same day costs only the API/Vision request.

## What is *not* in this list

* **Binance prices themselves** — covered by the dataset rebuild step
  (`[3k-base]`) and its own manifest hash chain (Stage 1: `dataset_build`).
* **Live exchange position state** — synced from the exchange in `[0]` /
  `[3h]`; freshness measured separately by `live/positions.csv` mtime.

The single source of truth is this file. If a feed is added to the live
config, add a row here in the same PR — that keeps the freshness audit
script and the on-call runbook aligned.
