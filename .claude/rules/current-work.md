# Current Work Context

## Current Baseline (2026-03-28, post-skew_rv-re-sweep)

**Live config:** `config/crypto_perps_1k.yaml` (Hyperliquid testnet, $1K capital)
**Research config:** `config/crypto_perps_full_rules.yaml` ($10K reference)
**Dataset:** `data/dataset_538registry_6yr_jagged.parquet` (300 instruments, 2020–2026)
**Branch:** `develop`

**$1K / HL filter (2026-03-28, skew_rv w=0.08):**
- Sharpe ~1.40, Calmar ~1.50, CAGR ~12.79%, MaxDD ~-8.50%

**$10K full_rules (2026-03-28, skew_rv w=0.08):**
- Sharpe ~1.37, Calmar ~1.95, CAGR ~14.84%, MaxDD ~-7.63%

**Key config parameters (1k config, post-audit):**
```yaml
notional_trading_capital: 1000.0
min_notional_position: 1.0    # Hyperliquid min ~$1 (vs Binance $25)
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

- **Capital scaling / leverage:** Vol is ~10% vs 25% target; leverage is the main lever at $1K
- **Hyperliquid live positions:** Connect actual API for position tracking (no API keys yet)
- ~~**skew_rv weight fine-tune:**~~ DONE — 0.08 adopted (2026-03-28)
- **Per-instrument SR estimates for Carver static:** Prerequisite to making it useful
