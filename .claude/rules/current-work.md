# Current Work Context

## Current Baseline (2026-03-08)

**Config:** `config/crypto_perps_full_rules.yaml` | **Branch:** `develop`
**Dataset:** `data/dataset_538registry_6yr_jagged.parquet` (300 instruments, 2020–2026)

**Performance (post adv_window=252 adoption, commit `447cd578`):**
- Sharpe ~1.17, Calmar ~1.39, CAGR ~12.4%, MaxDD ~-8.9%
- Vol ~10.6% (structural — correlated crypto, IDM≈1.1, $10K capital)

**Key config parameters:**
```yaml
top_k: 35
entry_buffer: 5
exit_buffer: 11
adv_window: 252
max_lot_notional: 'auto'
instrument_weight_ewma_span: 1
stage2_method: 'adv'
```

**Forecast weights (calibrated 2026-03-07):**
- gated_carry_10/30: 0.07 | gated_carry_60: 0.10
- xs_carry/activity/val/inter_sector: 0.10 each
- 19 trend rules: flat equal weights per family

---

## Recent History (condensed — full details in MEMORY.md)

| Date | Work | Result |
|------|------|--------|
| 2026-03-08 | adv_window sweep (30→90→252) | ADOPT 252 (ΔSharpe +4.7%) |
| 2026-03-08 | exit_buffer sweep | Keep 11 (plateau, ΔSharpe +0.6%) |
| 2026-03-08 | K=35 top-K + lot-size gate | ADOPT K=35 (ΔSharpe +1.8%, Calmar peak) |
| 2026-03-08 | EWMA span 125→1 | Fix ghost dilution in dynamic universe |
| 2026-03-08 | Carver static selection | REJECT (ΔSharpe -31.7%) |
| 2026-03-08 | gated_carry vol units | Keep price-dollar vol (intentional design) |
| 2026-03-07 | Instrument handcraft weights | REJECT (recency bias) |
| 2026-03-07 | Forecast weight diagnosis | gated_carry_60 should > _10/30 (confirmed) |
| 2026-03-07 | Forecast weight calibration | All weights tuned per Calmar-peak criterion |
| 2026-03-06 | 4 additive sleeves → standard rules | COMPLETE (commit b3c406fd) |
| 2026-03-06 | Carver ablation audit | xs_addr_growth disabled |
| 2026-03-06 | Equal family weights | REJECT (flat-0.05 wins) |

---

## Next Steps (open research ideas)

- **adv_window=252 plateau check:** Could test 365d, but likely diminishing returns
- **Capital scaling:** Vol is ~10% vs 25% target; scaling capital or using leverage is the only lever
- **Per-instrument SR estimates for Carver static:** Would unlock better instrument selection
- **Illiquidity premium:** Empirical weight estimator shows illiquidity_20/60 high gross SR — worth testing net of costs
