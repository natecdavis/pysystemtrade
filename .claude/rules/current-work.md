# Current Work Context

## Current Baseline (2026-03-26, post-audit)

**Live config:** `config/crypto_perps_1k.yaml` (Hyperliquid testnet, $1K capital)
**Research config:** `config/crypto_perps_full_rules.yaml` ($10K reference)
**Dataset:** `data/dataset_538registry_6yr_jagged.parquet` (300 instruments, 2020–2026)
**Branch:** `develop`

**$1K / HL filter post-audit (2026-03-26):**
- Sharpe ~1.31, Calmar ~1.50, CAGR ~13.2%, MaxDD ~-8.8%, Vol ~9.9%

**$10K full_rules post-audit (2026-03-26):**
- Sharpe ~1.28, Calmar ~1.65, CAGR ~15.0%, MaxDD ~-9.1%, Vol ~11.4%

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
fee_bps: 3.5                  # A1: was 5 (Hyperliquid taker)
taker_fee_frac: 0.00035       # A1: was 0.0005
vol_days: 63                  # D4: was 35
```

**Forecast weights (as of 2026-03-26):**
- gated_carry_10/30: 0.07 | gated_carry_60: 0.10
- xs_carry/activity/val/inter_sector: 0.10 each
- skew_abs_90/180/365: 0.0167 each
- skew_rv_90/180/365: 0.03 each  (D1: was 0.0167)
- 19 trend rules: flat equal weights per family

---

## Recent History (condensed — full details in MEMORY.md)

| Date | Work | Result |
|------|------|--------|
| 2026-03-26 | Comprehensive backtesting audit (A1→E3) | COMPLETE: commit f05201cc. 5 adoptions, 6 rejections. See MEMORY.md decisions. |
| 2026-03-21 | Paper trading infrastructure | COMPLETE: circuit_breaker.py, daily_paper_run.py, setup_paper_trading.py, reset_circuit_breaker.py, launchd plist (TZ=UTC, 01:00 UTC). |
| 2026-03-22 | Hyperliquid exchange filter | ADOPT: exchange_filter: hyperliquid. 148/300 instruments on HL. K=30 confirmed. |
| 2026-03-21 | K sweep at $1K / Hyperliquid testnet | ADOPT K=30. min_notional_position fix: was $25 (Binance), set to $1 (Hyperliquid). |
| 2026-03-08 | skew_rv/abs rules + adv_window=252 | ADOPT (see decisions.md for details) |

---

## Next Steps (open research ideas)

- **Capital scaling / leverage:** Vol is ~10% vs 25% target; leverage is the main lever at $1K
- **Hyperliquid live positions:** Connect actual API for position tracking (no API keys yet)
- **skew_rv weight fine-tune:** 0.03 adopted; could probe 0.03–0.05 range (window narrows at w≥0.08)
- **Per-instrument SR estimates for Carver static:** Prerequisite to making it useful
