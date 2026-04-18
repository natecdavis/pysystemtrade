# Current Work Context

## Current Baseline (2026-04-18, flat-61 + eb=2/ex=10)

**Live config:** `config/crypto_perps_1k.yaml` (Hyperliquid testnet, $1K actual equity)
**Research config:** `config/crypto_perps_full_rules.yaml` ($10K reference)
**Dataset:** `data/dataset_538registry_6yr_jagged.parquet` (319 instruments, 2020–2026)
**Branch:** `develop`

**$10K full_rules (2026-04-18, flat-61, eb=2/ex=10):**
- 61 rules at 0.016393 each (adding volume_surge_momentum + volume_price_divergence)
- Ablation individual results vs flat-59 baseline (Sharpe=1.3889, Calmar=1.8737, MaxDD=-7.87%):
  - volume_surge_momentum:  Sharpe=1.3905 (+0.0016), Calmar=1.9115 (+0.0378), MaxDD=-7.76% [ADOPT]
  - volume_price_divergence: Sharpe=1.3989 (+0.0100), Calmar=1.9181 (+0.0444), MaxDD=-7.62% [ADOPT]
  - xs_volume_attention: Sharpe=1.3784 (-0.0105), Calmar=1.8389 (-0.0348), MaxDD=-7.93% [REJECT]
- Combined flat-61 result: Sharpe=1.3871, Calmar=1.8617, CAGR=14.49%, MaxDD=-7.78% (`out/volume_flat61_combined/`)
  - Note: combined is marginally below flat-59 on Sharpe/Calmar (within noise); MaxDD improved -9bps
- Individual ablation results from `out/volume_ablation/`
- Prior flat-59 baseline: Sharpe=1.3889, Calmar=1.8737, CAGR=14.75%, MaxDD=-7.87%

**$1K / HL filter (stale — needs fresh run with new buffers):**
- Prior (pre-flat-56, sweep-optimized weights): Sharpe ~1.335, Calmar ~1.539, CAGR ~9.88%, MaxDD ~-6.42% (re: $3.5K notional)
- **Live (re: $1K actual equity, estimate):** CAGR ~34.6%, MaxDD ~-22.5%, realized vol ~25.3%
- Notional choice: $3.5K (3.5× phantom leverage) to target ~25% live vol

**Key config parameters (1k config, post-audit):**
```yaml
notional_trading_capital: 2500.0  # 2.5× phantom leverage on $1K actual equity: targets ~24% live vol
min_notional_position: 10.0   # HL minimum order size; reduce-only exempt (adopted 2026-03-30)
lot_size_notional_override: 1.0  # USD-denominated lots for Hyperliquid
top_k: 30
entry_buffer: 2               # buffer sweep 2026-04-17: was 3
exit_buffer: 10               # buffer sweep 2026-04-17: was 15
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

**Forecast weights (as of 2026-04-18, flat-61):**
- All 61 rules: 0.016393 each (1/61)
- Walk-forward benchmark (flat-56, authoritative): Sharpe=1.2693, Calmar=1.5059
- All prior sweep-optimized weights superseded — see MEMORY.md for details

---

## Recent History (condensed — full details in MEMORY.md)

| Date | Work | Result |
|------|------|--------|
| 2026-04-18 | Volume signals (flat-61) | ADOPT volume_surge_momentum (ΔSharpe+0.0016, ΔCalmar+0.0378) + volume_price_divergence (ΔSharpe+0.0100, ΔCalmar+0.0444). REJECT xs_volume_attention (both negative). Coverage: 30 instruments with Vision ZIPs. Results: `out/volume_ablation/`. |
| 2026-04-18 | OI-based attention proxy signals (flat-59) | ADOPT all 3: xs_oi_attention (+2.0%/+8.3%), attn_exhaustion_fade (+1.8%/+13.5%), attn_panic_rebound (+1.8%/+6.8%). Combined (59 rules): Sharpe=1.3889, Calmar=1.8737, MaxDD=-7.87%. seasonality_3yr/5yr tested same day — REJECT (Calmar worse). |
| 2026-04-17 | K sweep + buffer sweep (in-sample) | K=30 confirmed optimal (Sharpe peaks, monotonically worse above). ADOPT eb=2, ex=10: Sharpe=1.3651, Calmar=1.7148 vs eb=3/ex=15 fresh run ~1.345. ex saturates at ≥10 — exit buffer value irrelevant above minimum. |
| 2026-04-15 | flat-56 adoption | ADOPT flat 1/N across all 56 rules (w=0.017857). Walk-forward Sharpe=1.2693 (+36% vs prior flat-42 baseline of 0.931). All adaptive weighting schemes rejected (-16% to -49%). All prior sweep-optimized weights overfit. |
| 2026-04-02 | gated_carry re-sweep post-funding_mr | ADOPT 0.15/0.15/0.15 (was 0.07/0.07/0.10). Sweep range 0.0→0.30: Sharpe/Calmar/MaxDD improve to w=0.20 peak, cliff at w=0.30 (trend diluted to 48%). Conservative w=0.15: ΔSharpe +7.5%, MaxDD -6.42%. New baseline: Sharpe 1.335, Calmar 1.539, MaxDD -6.42%. |
| 2026-03-31 | funding_mr adopted at w=0.25 | ADOPT: ΔSharpe -0.1%, ΔCalmar +35.3%, MaxDD -9.67%→-6.47%. Acts as drawdown hedge (fires only at extreme funding z-scores). Live MaxDD ~-16% vs prior ~-24%. New baseline: Sharpe 1.242, Calmar 1.624, MaxDD -6.47%. |
| 2026-03-31 | $10 min trade size in backtest + buffer/live fixes | Backtest now models HL $10 min order (full-close exempt). Carver forecast buffer implemented live + backtest. Baseline pre-funding_mr: Sharpe 1.242, Calmar 1.200, CAGR 11.60%, MaxDD -9.67%. |
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
