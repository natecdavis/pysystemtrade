# Current Work Context

## K-sweep on flat-68 SB-corrected, 1k config (2026-04-30)

**K=30 confirmed at flat-68; not overfit. Higher K underperforms because the $9745.58 capital base is too small to support more concurrent positions — min-notional clipping de-leverages the system.**

Sweep: `out/k_sweep_flat68_sb_1k/`. Config: `crypto_perps_1k.yaml` (HL filter, capital=$9745.58 = equity $3898.23 × 2.5). Buffers proportional to current: eb=K/15, ex=K/3.

| K | eb | ex | Sharpe | Calmar | CAGR | MaxDD | RealVol | AvgPos | Turn |
|---|----|----|--------|--------|------|-------|---------|--------|------|
| **30** | **2** | **10** | **1.4039** | 1.6540 | **11.99%** | -7.25% | **8.31%** | 25.1 | 14.4 |
| 60 | 4 | 20 | 1.2701 | 1.5268 | 8.95% | -5.86% | 6.94% | 37.3 | 10.8 |
| 100 | 7 | 33 | 1.3043 | 1.6265 | 8.32% | -5.12% | 6.28% | 44.3 | 7.8 |
| 150 | 10 | 50 | 1.2985 | 1.5512 | 7.97% | -5.14% | 6.04% | 46.7 | 6.8 |
| 200 | 13 | 67 | 1.3236 | 1.5404 | 8.41% | -5.46% | 6.25% | 47.0 | 6.9 |
| 229 | 15 | 76 | 1.3152 | 1.5851 | 8.38% | -5.29% | 6.27% | 47.1 | 7.0 |

**Mechanism (six-diagnostic decomposition, `out/k_sweep_flat68_sb_1k/DIAGNOSIS.md`):**
1. **Capital clipping fraction grows from 19% (K=30) to 36% (K≥150).** A third of in-universe forecasts at high K can't take a position because the vol-scaled target is below the $10 min-notional floor.
2. **Universe saturates around 47 actual positions.** AvgPos: 25→37→44→47→47→47 across K=30..229. Beyond K=100 we add eligible instruments without actually filling more slots.
3. **Effective bets plateau at ~24** (1/HHI weights). eff_bets/K drops from 44% (K=30) to 10% (K=229) — pure dilution; nominal K is meaningless beyond K=60.
4. **PnL is heavily concentrated in top-30 ADV at every K.** Top-30 contribute 52.6% of PnL at K=30, still 42-43% at K=100+. Rank 151+ adds 9-10% at high K but it's not enough to compensate for the gross-return loss.
5. **Realized vol is well below 25% target at all K**, but high K is worse: 8.3% (K=30) → 6.0% (K=150). Min-notional floor effectively de-leverages the strategy. **At $9745 capital we're already running at 33% of vol target at K=30.**
6. **Costs IMPROVE with K** (turnover drops from 14.4 → 7.0; tx cost halves; funding flips from -5bp to -27bp — net cost goes negative). Costs are NOT the reason high K underperforms.
7. **Total dollar PnL drops monotonically:** $7,512 (K=30) → $5,344 (K=229). The "diversification" from K>30 is a phantom — most positions are zero-clipped.

**Upper-K structural cap:** ~100. Beyond K=100 nothing changes meaningfully (AvgPos plateaus, eff_bets plateaus, frac_below_$10 plateaus at ~36%). At K=229, lot-size filter additionally excludes BTC, BSV, ILV, TAO, TRB (1 lot > $43 = capital/K).

**Implications for live trading:**
- K=30 stays. Empirically dominant on this data.
- At current $3898 equity, the system runs at 33% of 25% vol target — **the live realized return ceiling is bound by capital, not strategy parameters.** Bigger equity (or bigger leverage_multiple) is the lever, not bigger universe.
- "Diversification is the only free lunch" only when each position is large enough to express signal. With a $10 floor on $9745 notional, the floor binds at avg position fraction <0.1% — i.e., the moment you want >100 positions.

## Current Baseline (2026-04-23, flat-68 + eb=2/ex=10)

**$10K full_rules (2026-04-23, flat-68, eb=2/ex=10):**
- 68 rules at 0.01470588 each (removed volume_surge_momentum from flat-69)
- Combined flat-68 result (SB-corrected data): Sharpe=1.4806, Calmar=2.5736, CAGR=12.9%, MaxDD=-5.03% (`out/vsm_removed_flat68_sb_combined/`)
- vs flat-69 SB baseline (Sharpe=1.4471, Calmar=2.3667, MaxDD=-5.38%): ΔSharpe=+0.0335, ΔCalmar=+0.2069, MaxDD improved -5.38%→-5.03%
- Removal exactly confirmed exclusion ablation prediction (predicted ΔSharpe+0.0336/ΔCalmar+0.2068)
- `volume_surge_momentum` removed: weakest original adoption signal (+0.0016 at adoption, noise-level), SB-inflated by LUNA's pre-crash volume surges

## SB Exclusion Audit (2026-04-23) — leave-one-out on flat-69 SB-corrected

**7 REMOVE_CANDIDATES from 17 tested** — rules that improved BOTH Sharpe and Calmar when removed from the 69-rule stack on the SB-corrected dataset. Baseline: flat-69 SB (Sharpe=1.4471, Calmar=2.3667, MaxDD=-5.38%).

| Rule | Sharpe | ΔSharpe | Calmar | ΔCalmar | MaxDD | Verdict |
|------|--------|---------|--------|---------|-------|---------|
| relmomentum_20 | 1.4334 | -0.0136 | 2.5418 | +0.1751 | -4.91% | KEEP |
| relmomentum_40 | 1.4320 | -0.0150 | 2.4576 | +0.0908 | -5.06% | KEEP |
| assettrend_8 | 1.4288 | -0.0183 | 2.2534 | -0.1133 | -5.47% | KEEP |
| assettrend_16 | 1.4417 | -0.0053 | 2.2774 | -0.0893 | -5.61% | KEEP |
| **assettrend_32** | **1.4768** | **+0.0297** | **2.4934** | **+0.1266** | -5.29% | **REMOVE_CANDIDATE** |
| **assettrend_64** | **1.4871** | **+0.0400** | **2.5001** | **+0.1333** | -5.31% | **REMOVE_CANDIDATE** |
| **accel_16** | **1.4494** | **+0.0024** | **2.4626** | **+0.0959** | -5.04% | **REMOVE_CANDIDATE** |
| **accel_32** | **1.4527** | **+0.0057** | **2.5191** | **+0.1524** | -4.96% | **REMOVE_CANDIDATE** |
| accel_64 | 1.4412 | -0.0058 | 2.4591 | +0.0924 | -5.13% | KEEP |
| **breakout_80** | **1.4590** | **+0.0119** | **2.4792** | **+0.1125** | -5.07% | **REMOVE_CANDIDATE** |
| breakout_160 | 1.4255 | -0.0215 | 2.4067 | +0.0400 | -5.21% | KEEP |
| **volume_surge_momentum** | **1.4806** | **+0.0336** | **2.5736** | **+0.2068** | -5.03% | **REMOVE_CANDIDATE** |
| xs_low_vol_20 | 1.4247 | -0.0224 | 2.2376 | -0.1291 | -5.72% | KEEP |
| volume_price_divergence | 1.4259 | -0.0211 | 2.4740 | +0.1072 | -5.30% | KEEP |
| **crowd_deleverage_trend** | **1.4623** | **+0.0153** | **2.5302** | **+0.1635** | -4.93% | **REMOVE_CANDIDATE** |
| attn_exhaustion_fade | 1.4467 | -0.0004 | 2.3473 | -0.0195 | -5.49% | KEEP |
| attn_panic_rebound | 1.4463 | -0.0008 | 2.3598 | -0.0070 | -5.39% | KEEP |

Key observations:
- **assettrend_32/64**: slow-horizon market-trend rules dragging on SB-corrected data. assettrend_8/16 are safe (short-horizon exits crashes faster).
- **accel_16/32**: mid-horizon acceleration rules flagged; accel_64 counterintuitively safe.
- **breakout_80**: stays long too long; breakout_160 counterintuitively safe.
- **volume_surge_momentum**: strongest Sharpe candidate (+0.0336) — LUNA's explosive pre-crash volume generated false momentum signal.
- **crowd_deleverage_trend**: strong Calmar candidate (+0.1635) — may have been riding deleverage noise.
- Results: `out/sb_exclusion_audit/sb_exclusion_results.json`
- **PENDING USER DECISION**: Remove all 7, subset, or keep and accept SB inflation as real signal.

## Prior Baseline (2026-04-23, flat-69 + eb=2/ex=10) — superseded

**$10K full_rules (2026-04-23, flat-69, eb=2/ex=10):**
- 69 rules at 0.01449275 each (adding cs_mr_125 + cs_mr_250)
- Combined flat-69 result (SB-corrected data): Sharpe=1.4471, Calmar=2.3667, CAGR=12.7%, MaxDD=-5.38% (`out/cs_mr_flat69_sb_combined/`)
- Combined flat-69 result (original data): Sharpe=1.4357, Calmar=2.3325, CAGR=13.0%, MaxDD=-5.57% (`out/cs_mr_flat69_combined/`)
- Ablation individual results vs SB-corrected flat-67 baseline (Sharpe=1.4044, Calmar=2.1694, MaxDD=-5.95%):
  - cs_mr_125: Sharpe=1.4546 (+0.0502), Calmar=2.2977 (+0.1283), MaxDD=-5.63% [ADOPT]
  - cs_mr_250: Sharpe=1.4078 (+0.0034), Calmar=2.3623 (+0.1929), MaxDD=-5.39% [ADOPT]
  - vol_trend_16:   ΔSharpe+0.0007, ΔCalmar-0.0166 [REJECT]
  - return_skew_20: ΔSharpe-0.0194, ΔCalmar-0.0512 [REJECT]
  - return_skew_60: ΔSharpe+0.0004, ΔCalmar-0.0689 [REJECT]
  - illiquidity_20: ΔSharpe-0.0679, ΔCalmar-0.2986 [REJECT]
  - illiquidity_60: ΔSharpe-0.0115, ΔCalmar-0.0644 [REJECT]
- Individual ablation results from `out/sb_corrected_ablations/`
- Survivorship bias haircut (flat-67): ΔSharpe=-0.046, ΔCalmar=-0.361, ΔMaxDD=-0.55pp vs original
- SB-corrected flat-67 baseline: Sharpe=1.4044, Calmar=2.1694, MaxDD=-5.95% (`out/sb_corrected_baseline/`)
- **NOTE**: cs_mr was REJECTED on original data (gross_SR=-0.53/-0.41) but ADOPTED on SB-corrected data.
  On original data, flat-69 shows ΔSharpe=-0.014, ΔCalmar=-0.198 vs flat-67. Use SB-corrected as the authoritative baseline.
- Prior flat-67 baseline: Sharpe=1.45, Calmar=2.53, CAGR=13.8%, MaxDD=-5.4%

## Prior Baseline (2026-04-19, flat-67 + eb=2/ex=10) — superseded

**$10K full_rules (2026-04-19, flat-67, eb=2/ex=10):**
- 67 rules at 0.01492537 each (adding oil_momentum_16)
- Combined flat-67 result: Sharpe=1.45, Calmar=2.53, CAGR=13.8%, MaxDD=-5.4% (`out/oil_mom_flat67_combined/`)
- Ablation individual results vs flat-66 baseline (Sharpe=1.4253, Calmar=2.4762, MaxDD=-5.60%):
  - gold_momentum_16: Sharpe=1.4588 (+0.0335), Calmar=2.3684 (-0.1078), MaxDD=-5.87% [REJECT]
  - vix_momentum_16:  Sharpe=1.3724 (-0.0529), Calmar=2.1374 (-0.3388), MaxDD=-6.25% [REJECT]
  - oil_momentum_16:  Sharpe=1.4452 (+0.0199), Calmar=2.5345 (+0.0583), MaxDD=-5.43% [ADOPT]
- Individual ablation results from `out/macro_ext2_ablation/`
- Prior flat-66 baseline: Sharpe=1.4253, Calmar=2.4762, CAGR=13.87%, MaxDD=-5.60%

## Prior Baseline (2026-04-19, flat-66 + eb=2/ex=10) — superseded

**$10K full_rules (2026-04-19, flat-66, eb=2/ex=10):**
- 66 rules at 0.01515152 each (adding vol_zscore_ts — user override: Calmar/MaxDD improvement accepted despite ΔSharpe<0)
- Combined flat-66 result: Sharpe=1.4253, Calmar=2.4762, CAGR=13.87%, MaxDD=-5.60% (`out/vol_zscore_flat66_combined/`)
- Prior flat-65 baseline: Sharpe=1.4431, Calmar=2.3929, CAGR=14.27%, MaxDD=-5.97%

## Prior Baseline (2026-04-19, flat-65 + eb=2/ex=10) — superseded

**$10K full_rules (2026-04-19, flat-65, eb=2/ex=10):**
- 65 rules at 0.01538462 each (adding us10y_momentum_16)
- Combined flat-65 result: Sharpe=1.4431, Calmar=2.3929, CAGR=14.27%, MaxDD=-5.97% (`out/macro_ext_flat65_combined/`)
- Ablation individual results vs flat-64 baseline (Sharpe=1.4325, Calmar=2.2198, MaxDD=-6.46%):
  - spx_momentum_16:   Sharpe=1.3865 (-0.0460), Calmar=2.1151 (-0.1047) [REJECT]
  - spx_momentum_32:   Sharpe=1.3905 (-0.0420), Calmar=2.1516 (-0.0682) [REJECT]
  - us10y_momentum_16: Sharpe=1.4431 (+0.0106), Calmar=2.3929 (+0.1731), MaxDD=-5.97% [ADOPT]
- Individual ablation results from `out/macro_ext_ablation/`
- Prior flat-64 baseline: Sharpe=1.4325, Calmar=2.2198, CAGR=14.33%, MaxDD=-6.46%

## Prior Baseline (2026-04-18, flat-64 + eb=2/ex=10) — superseded

**Live config:** `config/crypto_perps_1k.yaml` (Hyperliquid testnet, $1K actual equity)
**Research config:** `config/crypto_perps_full_rules.yaml` ($10K reference)
**Dataset:** `data/dataset_538registry_6yr_jagged.parquet` (319 instruments, 2020–2026)
**Branch:** `develop`

**BTC dominance ablation (2026-04-18, vs flat-64 baseline):**
- btc_dom_rotation_16: Sharpe=1.4002 (-0.0323), Calmar=2.1562 (-0.0636), MaxDD=-6.47% [REJECT]
- btc_dom_rotation_32: Sharpe=1.4135 (-0.0190), Calmar=2.1657 (-0.0541), MaxDD=-6.48% [REJECT]
- btc_dom_level_120:   Sharpe=1.4067 (-0.0258), Calmar=2.1285 (-0.0913), MaxDD=-6.60% [REJECT]
- All three rejected (both ΔSharpe and ΔCalmar negative). Rules added to trading_rules only.
- Results: `out/btc_dom_ablation/`
- Baseline unchanged: flat-64, Sharpe=1.4325, Calmar=2.2198, MaxDD=-6.46%

**$10K full_rules (2026-04-18, flat-64, eb=2/ex=10):**
- 64 rules at 0.015625 each (adding dxy_momentum_16)
- Combined flat-64 result: Sharpe=1.4325, Calmar=2.2198, CAGR=14.33%, MaxDD=-6.46% (`out/macro_flat64_combined/`)
- Ablation individual results vs flat-63 baseline (Sharpe=1.3983, Calmar=1.9356, MaxDD=-7.25%):
  - macro_momentum_16: Sharpe=1.3794 (-0.0189), Calmar=1.8903 (-0.0453), MaxDD=-7.34% [REJECT]
  - macro_momentum_32: Sharpe=1.3810 (-0.0173), Calmar=1.9144 (-0.0212), MaxDD=-7.20% [REJECT]
  - dxy_momentum_16:   Sharpe=1.4325 (+0.0342), Calmar=2.2198 (+0.2842), MaxDD=-6.46% [ADOPT]
- Individual ablation results from `out/macro_ablation/`
- Prior flat-63 baseline: Sharpe=1.3983, Calmar=1.9356, CAGR=14.03%, MaxDD=-7.25%

**$10K full_rules (2026-04-18, flat-63, eb=2/ex=10) — superseded:**
- 63 rules at 0.01587302 each (adding xs_low_vol_20 + xs_low_vol_60)
- Combined flat-63 result: Sharpe=1.3983, Calmar=1.9356, CAGR=14.03%, MaxDD=-7.25% (`out/vol_regime_flat63_combined/`)

**$10K full_rules (2026-04-18, flat-61, eb=2/ex=10) — superseded:**
- 61 rules at 0.016393 each (adding volume_surge_momentum + volume_price_divergence)
- Ablation individual results vs flat-59 baseline (Sharpe=1.3889, Calmar=1.8737, MaxDD=-7.87%):
  - volume_surge_momentum:  Sharpe=1.3905 (+0.0016), Calmar=1.9115 (+0.0378), MaxDD=-7.76% [ADOPT]
  - volume_price_divergence: Sharpe=1.3989 (+0.0100), Calmar=1.9181 (+0.0444), MaxDD=-7.62% [ADOPT]
  - xs_volume_attention: Sharpe=1.3784 (-0.0105), Calmar=1.8389 (-0.0348), MaxDD=-7.93% [REJECT]
- Combined: Sharpe=1.3871, Calmar=1.8617, CAGR=14.49%, MaxDD=-7.78% (`out/volume_flat61_combined/`)

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

**Forecast weights (as of 2026-04-18, flat-64):**
- All 64 rules: 0.015625 each (1/64)
- Walk-forward benchmark (flat-56, authoritative): Sharpe=1.2693, Calmar=1.5059
- All prior sweep-optimized weights superseded — see MEMORY.md for details

---

## Recent History (condensed — full details in MEMORY.md)

| Date | Work | Result |
|------|------|--------|
| 2026-04-19 | Macro ext2 signals (flat-67) | ADOPT oil_momentum_16 (ΔSharpe+0.0199, ΔCalmar+0.0583, MaxDD -5.60%→-5.43%). REJECT gold_momentum_16 (ΔCalmar-0.1078) + vix_momentum_16 (both strongly negative). Combined flat-67: Sharpe=1.45, Calmar=2.53, MaxDD=-5.4%. Results: `out/macro_ext2_ablation/`, `out/oil_mom_flat67_combined/`. |
| 2026-04-19 | Limit order simulation | REJECT limit orders: fee savings trivial (0.35% p.a. max), signal lag cost enormous (0.84%/day). maker_instant: ΔSharpe-0.0001 (negligible). maker_1day: ΔSharpe-0.1568 (catastrophic). Root cause: buffers reduce effective turnover to ~2.5 rt/yr, so fees are not the binding cost. Breakeven fill rate = 844% (impossible). Stick with taker. Results: `out/limit_order_simulation/`. |
| 2026-04-19 | vol_zscore_ts adoption (flat-66) | ADOPT (user override): ΔSharpe-0.0178, ΔCalmar+0.0833, MaxDD -5.97%→-5.60%. Drawdown hedge value accepted despite ΔSharpe<0. Combined flat-66: Sharpe=1.4253, Calmar=2.4762, MaxDD=-5.60%. xs_oi_trend and vol_trend_16 remain rejected (both metrics negative). |
| 2026-04-19 | OI trend + vol TS ablation (flat-65) | REJECT all 3 by dual criterion: xs_oi_trend (ΔSharpe-0.0263, ΔCalmar-0.0764), vol_trend_16 (ΔSharpe-0.0259, ΔCalmar-0.0651), vol_zscore_ts (ΔSharpe-0.0178, ΔCalmar+0.0833). Results: `out/oi_vol_ablation/`. |
| 2026-04-19 | Funding momentum ablation (flat-65) | REJECT both: funding_momentum_16 (ΔSharpe-0.0122, ΔCalmar-0.0533), funding_momentum_32 (ΔSharpe-0.0121, ΔCalmar-0.0625). Root cause: gated_carry/demeaned_carry already capture rate-trend information via level. Rules in trading_rules only. Results: `out/funding_momentum_ablation/`. |
| 2026-04-19 | Macro ext signals (flat-65) | ADOPT us10y_momentum_16 (ΔSharpe+0.0106, ΔCalmar+0.1731, MaxDD -6.46%→-5.97%). REJECT spx_momentum_16/32 (both negative — SPX redundant with crypto trend rules). Combined flat-65: Sharpe=1.4431, Calmar=2.3929. Results: `out/macro_ext_ablation/`. |
| 2026-04-19 | Return skew ablation (flat-64) | REJECT both: return_skew_20 (ΔSharpe-0.0268, ΔCalmar-0.2134), return_skew_60 (ΔSharpe-0.0405, ΔCalmar-0.1043). Pass 1 "bad reversion" verdict confirmed at flat-64. Results: `out/return_skew_ablation/`. |
| 2026-04-19 | K re-sweep at flat-64 | K=30 confirmed optimal (Sharpe=1.4296, Calmar=2.5659 with proportional buffers). Monotonically worse above and below. Buffer sweep (eb × ex) aborted early — eb=1 results below baseline, no structural reason for optimal (eb=2, ex=10) to shift with new rules. K=30/eb=2/ex=10 retained. |
| 2026-04-18 | BTC dominance signals | REJECT all 3: btc_dom_rotation_16 (ΔSharpe-0.0323, ΔCalmar-0.0636), btc_dom_rotation_32 (ΔSharpe-0.0190, ΔCalmar-0.0541), btc_dom_level_120 (ΔSharpe-0.0258, ΔCalmar-0.0913). BTC dominance cycle already captured by relmomentum/inter_sector. Results: `out/btc_dom_ablation/`. |
| 2026-04-18 | Macro direction signals (flat-64) | ADOPT dxy_momentum_16 (ΔSharpe+0.0342, ΔCalmar+0.2842, MaxDD -7.25%→-6.46%). REJECT macro_momentum_16/32 (OLS fitted values, both negative). Combined flat-64: Sharpe=1.4325, Calmar=2.2198, MaxDD=-6.46%. Results: `out/macro_ablation/`. |
| 2026-04-18 | Vol regime signals (flat-63) | ADOPT xs_low_vol_20 (ΔSharpe+0.0033, ΔCalmar+0.0416) + xs_low_vol_60 (ΔSharpe+0.0207, ΔCalmar+0.0873). REJECT vol_regime_trend (both negative). Combined flat-63: Sharpe=1.3983, Calmar=1.9356, MaxDD=-7.25%. Results: `out/vol_regime_ablation/`. |
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
