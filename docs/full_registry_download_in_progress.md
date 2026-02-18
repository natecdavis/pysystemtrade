# Full Registry Download: In Progress

**Started:** 2026-02-15 10:02 AM PST
**Status:** ✅ RUNNING
**PID:** 30845

## Progress

- **Completed:** 26/541 instruments (4.8%)
- **Remaining:** 515 instruments (95.2%)
- **Current:** 1000000BOBUSDT (getting 404s - recently launched)

## Estimated Completion

**Based on layer_a download performance:**
- Previous: 25 instruments in ~32 minutes (~1.3 min/instrument)
- Remaining: 515 instruments × 1.3 min = **670 minutes (~11 hours)**

**Expected completion:** ~9:00 PM PST today (2026-02-15)

**Note:** Actual time may vary:
- Recent launches have fewer months (faster, more 404s)
- Older instruments have more data (slower, more downloads)
- Network speed fluctuations

## Monitoring

### Real-time Monitor
```bash
# Watch progress every 10 seconds
watch -n 10 ./scripts/monitor_vision_download.sh

# View live log
tail -f /tmp/vision_full_registry.log

# Check progress file
cat envs/dev/data/raw/vision_download_progress.json | jq
```

### Quick Status Check
```bash
./scripts/monitor_vision_download.sh
```

## Background Process

**PID:** 30845
**Command:** `python scripts/download_vision_bulk.py --env dev`
**Log file:** `/tmp/vision_full_registry.log`

To stop (if needed):
```bash
kill 30845
```

To resume (idempotent):
```bash
python scripts/download_vision_bulk.py --env dev
```

## What Happens Next

The downloader will:
1. ✅ Skip already downloaded instruments (26 complete)
2. 🔄 Download remaining 515 instruments from registry
3. 📊 Save progress after each instrument
4. ✅ Handle 404s gracefully (pre-launch months)
5. 💾 Store ZIPs in `envs/dev/data/raw/binance/`

## Expected Output

**Total files when complete:**
- Klines: ~35,000-40,000 ZIPs
- Funding: ~35,000-40,000 ZIPs
- **Total: ~70,000-80,000 files**

**Storage estimate:** ~5-10 GB

## Progress Checkpoints

| Instruments | Estimated Time | Status |
|------------|----------------|--------|
| 26 (5%)    | 10:02 AM      | ✅ Complete |
| 100 (18%)  | ~11:30 AM     | 🔄 In progress |
| 200 (37%)  | ~2:00 PM      | ⏳ Pending |
| 300 (55%)  | ~4:30 PM      | ⏳ Pending |
| 400 (74%)  | ~7:00 PM      | ⏳ Pending |
| 500 (92%)  | ~8:30 PM      | ⏳ Pending |
| 541 (100%) | ~9:00 PM      | ⏳ Pending |

## Resumability

If interrupted, simply re-run:
```bash
python scripts/download_vision_bulk.py --env dev
```

Progress is saved after each instrument in:
```
envs/dev/data/raw/vision_download_progress.json
```

## What This Enables

Once complete, you'll have:
- ✅ Full 6-year history for 541 Binance perpetual futures
- ✅ Ready for large-scale backtesting
- ✅ Complete dynamic universe research
- ✅ Historical lifecycle analysis
- ✅ ADV-based liquidity rankings
- ✅ Top-K selection with full history

## Next Steps After Completion

1. **Build Full Dataset**
   ```bash
   python scripts/build_example_dataset.py \
       --source real \
       --data-dir envs/dev/data/raw/binance \
       --output-path envs/dev/data/datasets/full_541_instruments.parquet \
       --allow-jagged \
       --min-history-days 365
   ```

2. **Generate Lifecycle Manifest**
   - Derive launch/delist dates from actual Vision data
   - Track data coverage per instrument

3. **Run Top-K Analysis**
   - Compute ADV rankings over time
   - Test hysteresis settings
   - Validate top-30 selection

4. **Full Universe Backtest**
   - Test dynamic universe with 541 candidates
   - Verify cost filters
   - Analyze turnover

## Monitoring Commands

```bash
# One-time status
./scripts/monitor_vision_download.sh

# Continuous monitoring (updates every 10 seconds)
watch -n 10 ./scripts/monitor_vision_download.sh

# Live log tail
tail -f /tmp/vision_full_registry.log | grep -E "(Processing|Downloaded|INFO)"

# Progress count
cat envs/dev/data/raw/vision_download_progress.json | jq '.count'

# Check process
ps aux | grep download_vision_bulk | grep -v grep
```

## Troubleshooting

**If download stops:**
1. Check if process is running: `ps aux | grep download_vision_bulk`
2. Check last error in log: `tail -100 /tmp/vision_full_registry.log`
3. Resume: `python scripts/download_vision_bulk.py --env dev`

**If network errors:**
- Download automatically handles 404s (pre-launch data)
- Other network errors may cause failure for that instrument
- Re-running will retry failed instruments

**If disk space issues:**
- Check space: `df -h`
- Each instrument: ~5-20 MB average
- Total expected: 5-10 GB for 541 instruments
