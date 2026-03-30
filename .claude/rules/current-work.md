# Current Work Context

## Current Baseline (2026-03-30, min_notional=$10 + $2.5K notional)

**Live config:** `config/crypto_perps_1k.yaml` (Hyperliquid testnet, $1K actual equity)
**Research config:** `config/crypto_perps_full_rules.yaml` ($10K reference)
**Dataset:** `data/dataset_538registry_6yr_jagged.parquet` (300 instruments, 2020–2026)
**Branch:** `develop`

**$1K / HL filter (2026-03-30, 2.5× phantom leverage, notional=$2.5K, min_notional=$10):**
- Sharpe ~1.29, Calmar ~1.20, CAGR ~12.65% (re: $2.5K notional), MaxDD ~-10.5% (re: $2.5K notional)
- **Live (re: $1K actual equity):** CAGR ~31.6%, MaxDD ~-26.4%, realized vol ~23.9%
- Notional choice: $2.5K selected to target ~24% live vol (Sharpe scale-invariant, vol is the right criterion)

**$10K full_rules (2026-03-28, skew_rv w=0.08, no phantom leverage):**
- Sharpe ~1.37, Calmar ~1.95, CAGR ~14.84%, MaxDD ~-7.63%

**Key config parameters (1k config, post-audit):**
```yaml
notional_trading_capital: 2500.0  # 2.5× phantom leverage on $1K actual equity: targets ~24% live vol
min_notional_position: 10.0   # HL minimum order size; reduce-only exempt (adopted 2026-03-30)
lot_size_notional_override: 1.0  # USD-denominated lots for Hyperliquid
top_k: 30
entry_buffer: 3               # E3: was 5
exit_buffer: 15               # E3: was 10
adv_window: 252
max_lot_notional: 'auto'
instrument_weight_ewma_span: 1
stage2_method: 'adv'
use_gated_carry: true         # MUST be true (ForecastCombineGated)
fee_bps: 4.5                  # corrected 2026-03-28: HL taker=0.045% (was 3.5)
taker_fee_frac: 0.00045       # corrected 2026-03-28: HL taker=0.045% (was 0.00035)
vol_days: 63                  # D4: was 35
```

**Circuit breaker (updated 2026-03-30 for 2.5× leverage + $10 min):**
- `max_daily_loss_pct`: 12%
- `max_drawdown_pct`: 28% (~2pp above expected MaxDD of 26%)

**Forecast weights (as of 2026-03-26):**
- gated_carry_10/30: 0.07 | gated_carry_60: 0.10
- xs_carry/activity/val/inter_sector: 0.10 each
- skew_abs_90/180/365: 0.0167 each
- skew_rv_90/180/365: 0.08 each  (D1 re-sweep 2026-03-28: was 0.03)
- demeaned_carry_10/30/60: 0.05 each  (ADOPTED 2026-03-27)
- 19 trend rules: flat equal weights per family

---

## Recent History (condensed — full details in MEMORY.md)

| Date | Work | Result |
|------|------|--------|
| 2026-03-30 | HL $10 min order + re-sweep | ADOPT min_notional=10 (reduce-only exempt). Re-sweep run; $2.5K retained (Sharpe scale-invariant — live vol 24% is the right criterion). CB: daily 12%, MaxDD 28%. |
| 2026-03-29 | Units bug fix: positions.csv stores tokens not USD | CRITICAL BUG FIXED. trade_plan.py now multiplies backtest targets by last_prices.json. Live positions are ~10–157× too large; trade plan generated to reduce. |
| 2026-03-29 | Phantom leverage sweep (notional_capital $1K→$6K) | ADOPT $2.5K (2.5×). Targets 25% realized vol. Live vol ~24%, live CAGR ~34%, live MaxDD ~25%. Sharpe ~1.36 (scale-invariant). CB updated: daily 12%, MaxDD 28%. Superseded 2026-03-30. |
| 2026-03-28 | skew_rv re-sweep 0.03→0.08 | ADOPT: full_rules ΔSharpe +3.7%, ΔCalmar +6.2%. 1k ΔSharpe +4.9%, ΔCalmar +9.2%. Calmar peaks at w=0.08, narrows after. |
| 2026-03-28 | Fee correction: HL taker 3.5→4.5bps, dataset patched | full_rules: Sharpe 1.3239, Calmar 1.8321. 1k: Sharpe 1.3315, Calmar 1.3779. Small but real cost increase. |
| 2026-03-27 | demeaned_carry (idiosyncratic funding, ungated) | ADOPT: w=0.05/rule. full_rules ΔSharpe +3.4%, ΔCalmar +0.18. 1k ΔSharpe +2.6% (Calmar slight divergence). |
| 2026-03-26 | Comprehensive backtesting audit (A1→E3) | COMPLETE: commit f05201cc. 5 adoptions, 6 rejections. See MEMORY.md decisions. |
| 2026-03-21 | Paper trading infrastructure | COMPLETE: circuit_breaker.py, daily_paper_run.py, setup_paper_trading.py, reset_circuit_breaker.py, launchd plist (TZ=UTC, 01:00 UTC). |
| 2026-03-22 | Hyperliquid exchange filter | ADOPT: exchange_filter: hyperliquid. 148/300 instruments on HL. K=30 confirmed. |
| 2026-03-21 | K sweep at $1K / Hyperliquid testnet | ADOPT K=30. min_notional_position fix: was $25 (Binance), set to $1 (Hyperliquid). |
| 2026-03-08 | skew_rv/abs rules + adv_window=252 | ADOPT (see decisions.md for details) |

---

## Next Steps (open research ideas)

- ~~**Capital scaling / leverage:**~~ DONE — 2× phantom leverage adopted 2026-03-29 (notional $2K, actual equity $1K)
- **Reduce oversized live positions:** Trade plan generated (2026-03-29); PENGU/VINE/TST/HBAR/DOGE all need large reductions due to units bug
- **Hyperliquid live positions:** Connect actual API for position tracking (no API keys yet)
- ~~**skew_rv weight fine-tune:**~~ DONE — 0.08 adopted (2026-03-28)
- **Per-instrument SR estimates for Carver static:** Prerequisite to making it useful
