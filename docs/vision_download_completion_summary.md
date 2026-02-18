# Vision Download Completion Summary

**Date:** 2026-02-15
**Status:** ✅ COMPLETE

## Download Results

**Total Downloaded:** 26 instruments (25 layer_a + 1 from initial test)
**Success Rate:** 100% (25/25 requested)
**Failed:** 0
**Total Files:** 5,348 ZIPs (2,682 klines + 2,666 funding)

## Downloaded Instruments

All 25 layer_a instruments from `config/crypto_perps_dynamic_universe_top30.yaml`:

1. AAVEUSDT_PERP
2. ADAUSDT_PERP
3. ATOMUSDT_PERP
4. AVAXUSDT_PERP
5. AXSUSDT_PERP
6. BCHUSDT_PERP
7. BNBUSDT_PERP
8. BTCUSDT_PERP
9. DOGEUSDT_PERP
10. DOTUSDT_PERP
11. ETCUSDT_PERP
12. ETHUSDT_PERP
13. FILUSDT_PERP
14. ICPUSDT_PERP
15. LINKUSDT_PERP
16. LTCUSDT_PERP
17. MANAUSDT_PERP
18. SANDUSDT_PERP
19. SOLUSDT_PERP
20. THETAUSDT_PERP
21. TRXUSDT_PERP
22. UNIUSDT_PERP
23. VETUSDT_PERP
24. XLMUSDT_PERP
25. XRPUSDT_PERP

**Plus:** 0GUSDT_PERP (from initial test)

## Data Coverage

**Historical Range:** 2019-01-01 to 2026-01-31 (6+ years)
**Data Types:**
- Daily klines (OHLCV)
- Funding rates (8-hour intervals)

**Storage Location:** `envs/dev/data/raw/binance/`
```
envs/dev/data/raw/binance/
├── klines/
│   ├── BTCUSDT/
│   │   ├── BTCUSDT-1d-2019-09.zip
│   │   ├── BTCUSDT-1d-2019-10.zip
│   │   └── ... (2682 total)
│   └── ...
└── funding_rates/
    ├── BTCUSDT/
    │   ├── BTCUSDT-fundingRate-2019-09.zip
    │   └── ... (2666 total)
    └── ...
```

## Download Performance

**Start Time:** 2026-02-15 00:30:20 UTC
**End Time:** 2026-02-15 01:02:46 UTC
**Duration:** ~32 minutes
**Average:** ~1.3 minutes per instrument
**Network:** NO VPN required (Binance Vision is public)

## Verification

**Progress Tracker:** `envs/dev/data/raw/vision_download_progress.json`
```json
{
  "completed": [26 instruments],
  "last_updated": "2026-02-15T06:02:46.144392",
  "count": 26
}
```

**File Counts:**
- Klines ZIPs: 2,682
- Funding ZIPs: 2,666
- Total: 5,348 files

## Next Steps

1. ✅ **Phase A-C Complete** - Doctor semantics, positions sync, Vision downloader
2. 🔄 **Phase D: Turnover Diagnostics** - Add turnover metrics to advisory output
3. 📊 **Build Dataset** - Generate parquet dataset from downloaded ZIPs
4. 🧪 **Run Backtest** - Test dynamic universe with full historical data

## Notes

- **Idempotent:** Re-running download will skip existing files
- **Resumable:** Progress tracked in `vision_download_progress.json`
- **Expandable:** Run with `--instruments-limit N` to download more instruments
- **Full Registry:** 541 instruments available in registry (515 remaining)

## Example: Download More Instruments

```bash
# Download next 25 instruments (resumes automatically)
python scripts/download_vision_bulk.py \
    --env dev \
    --config config/crypto_perps_dynamic_universe_top30.yaml \
    --instruments-limit 50

# Download all 541 instruments
python scripts/download_vision_bulk.py --env dev
```

## Data Quality

**Expected Behavior:**
- ✅ 404 errors for pre-launch months (normal)
- ✅ 404 for February 2026 (current/incomplete month)
- ✅ Different launch dates per instrument (e.g., SOLUSDT launched 2020-09)

**Example Launch Dates:**
- BTCUSDT_PERP: 2019-09 (oldest)
- ETHUSDT_PERP: 2020-02
- SOLUSDT_PERP: 2020-09
- 0GUSDT_PERP: 2024+ (newest, mostly 404s expected)
