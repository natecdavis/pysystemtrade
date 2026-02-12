# Automatic Binance Perpetual Discovery

## Overview

This system automatically discovers Binance USDT-margined perpetual futures using the CoinGecko API, enabling data acquisition for 500+ instruments without manual config updates.

**Key Design Principles:**
- ✅ Data acquisition only (never auto-expands tradable universe)
- ✅ Deterministic + auditable artifacts
- ✅ Backward compatible (fallback to existing behavior)
- ✅ Not geo-blocked (uses CoinGecko, not Binance API)

## Quick Start

### 1. Refresh the Registry

```bash
# Dry run (preview only)
python scripts/refresh_binance_market_registry.py --env dev --dry-run

# Actual run (creates artifacts)
python scripts/refresh_binance_market_registry.py --env dev
```

**What This Does:**
- Fetches derivatives from CoinGecko API (19,880 total contracts)
- Filters to Binance USDT-margined perpetuals (~541 instruments)
- Writes three artifacts to `envs/{env}/data/raw/metadata/`:
  1. `coingecko_derivatives_snapshot.json` - Full API response (8.8 MB)
  2. `binance_perp_registry.json` - Normalized registry (147 KB)
  3. `discovered_candidate_instruments.json` - Instrument IDs with _PERP suffix (11 KB)

### 2. Enable Auto-Discovery in Config

```yaml
# config/my_research_config.yaml
data_acquisition:
  auto_discover: true  # Enable registry-based discovery

universe:
  layer_a_instruments:
    # Tradable universe (unchanged)
    - BTCUSDT_PERP
    - ETHUSDT_PERP
    - BNBUSDT_PERP
```

### 3. Run Data Update

```bash
python scripts/update_data_monthly.py \
  --config config/my_research_config.yaml \
  --env dev \
  --dry-run
```

**Result:**
- Downloads data for 541 discovered instruments
- Trading universe remains 3 instruments (BTCUSDT, ETHUSDT, BNBUSDT)

## Architecture

### Data Flow

```
CoinGecko API (not geo-blocked)
    ↓
refresh_binance_market_registry.py
    ↓
discovered_candidate_instruments.json (541 instruments)
    ↓
update_data_monthly.py (if auto_discover=true)
    ↓
Download historical data from Binance Vision
```

### Precedence Logic

The system uses a 3-tier precedence for candidate instruments:

```python
1. config.data_acquisition.candidate_instruments (explicit config)
   ↓
2. discovered_candidate_instruments.json (if auto_discover=true)
   ↓
3. universe.layer_a_instruments (fallback)
```

**Important:** Trading universe is ALWAYS `universe.layer_a_instruments` (never auto-expanded).

### Artifact Files

#### 1. `coingecko_derivatives_snapshot.json`
**Purpose:** Complete API snapshot for audit/debugging

**Structure:**
```json
{
  "fetched_at": "2026-02-11T18:24:49Z",
  "source": "https://api.coingecko.com/api/v3/derivatives",
  "total_derivatives": 19880,
  "binance_perpetuals": 541,
  "raw_derivatives": [
    {
      "market": "Binance (Futures)",
      "symbol": "BTCUSDT",
      "contract_type": "perpetual",
      "price": "43250.5",
      "volume_24h": 9876543210,
      "open_interest": 1234567890,
      "funding_rate": 0.0001,
      "last_traded_at": 1739293847,
      "expired_at": null
    }
  ]
}
```

#### 2. `binance_perp_registry.json`
**Purpose:** Normalized, filterable registry for programmatic use

**Structure:**
```json
{
  "generated_at": "2026-02-11T18:24:49Z",
  "source": "coingecko_derivatives_snapshot.json",
  "version": "1.0",
  "filter_criteria": {
    "market": "Binance (Futures)",
    "contract_type": "perpetual",
    "symbol_contains": "USDT",
    "expired_at": "null"
  },
  "instruments": {
    "BTCUSDT": {
      "symbol": "BTCUSDT",
      "status": "ACTIVE",
      "base_asset": "BTC",
      "quote_asset": "USDT",
      "volume_24h": 9876543210,
      "open_interest": 1234567890,
      "funding_rate": 0.0001,
      "last_traded_at": 1739293847
    }
  },
  "summary": {
    "total_instruments": 541
  }
}
```

#### 3. `discovered_candidate_instruments.json`
**Purpose:** Direct input for `update_data_monthly.py`

**Structure:**
```json
{
  "generated_at": "2026-02-11T18:24:49Z",
  "source": "binance_perp_registry.json",
  "version": "1.0",
  "candidate_instruments": [
    "BTCUSDT_PERP",
    "ETHUSDT_PERP",
    ...
  ],
  "count": 541
}
```

## CoinGecko vs Binance API

### Why CoinGecko?

| Feature | CoinGecko | Binance API |
|---------|-----------|-------------|
| **Geo-blocking** | ❌ No (works in MA) | ✅ Yes (451 in MA) |
| **Auth required** | ❌ No | ❌ No |
| **Rate limits** | 10-50 calls/min | 2400 req/min |
| **Coverage** | 541 Binance perps | ~280 perps |
| **Data freshness** | Real-time snapshots | Real-time |
| **Historical data** | ❌ No | ✅ Yes (via Binance Vision) |

**Key Insight:** CoinGecko is ONLY for symbol discovery. Historical data still comes from Binance Vision (monthly ZIPs, not geo-blocked).

### What CoinGecko Provides

✅ **Symbol discovery** - Which perpetuals exist right now
✅ **Current snapshot data** - Latest price, funding rate, volume, open interest
✅ **NOT geo-blocked** - Accessible from Massachusetts

❌ **NOT for trading decisions** - Use for discovery only, not for:
- Historical funding rate time series (need for Carver's carry rule)
- Historical OHLCV klines (need for EWMAC)
- Launch dates / lifecycle tracking

### Data Pipeline for Trading

```python
# Symbol Discovery (NEW - NOT geo-blocked)
scripts/refresh_binance_market_registry.py
    └── Source: CoinGecko API (works in MA)

# Historical Data (UNCHANGED - NOT geo-blocked)
scripts/update_data_monthly.py
    ├── klines: BTCUSDT-1d-2023-01.zip
    └── funding_rates: BTCUSDT-fundingRate-2023-01.zip
    └── Source: data.binance.vision (works in MA)

# Daily Updates (NEEDS VPN/PROXY - geo-blocked)
scripts/update_data_daily.py
    ├── Endpoint: /fapi/v1/klines (GEO-BLOCKED in MA)
    ├── Endpoint: /fapi/v1/fundingRate (GEO-BLOCKED in MA)
    └── Solution: Use VPN/proxy for Binance REST API access
```

## VPN/Proxy Setup (for Daily Updates)

### Why Needed?

The Binance REST API (`fapi.binance.com`) is geo-blocked in Massachusetts. You need:
- TODAY's closing price (00:00 UTC / 7pm ET)
- LATEST funding rate (8-hourly updates)

CoinGecko cannot replace this because:
- Provides current snapshot (not aligned to candle close)
- No historical time series (need for EWMA calculations)

### Recommended Setup: HTTP/SOCKS Proxy

**Advantages:**
- Per-application (only affects Python scripts)
- No system-wide changes
- Easy to integrate with `urllib`

**Implementation:**

1. **Set environment variable:**
```bash
export BINANCE_PROXY=socks5://proxy-server:1080
```

2. **Run scripts:**
```bash
python scripts/update_data_daily.py --config config/crypto_perps_baseline_v1.yaml
# Automatically uses proxy for Binance API
```

**Proxy Options:**
- Commercial SOCKS5 proxy (~$5-10/month): Bright Data, Smartproxy, IPRoyal
- Self-hosted VPS (~$5/month): DigitalOcean/Linode + `tinyproxy`/`dante`
- SSH tunnel (quick test): `ssh -D 1080 user@vps-ip`

### Alternative: System-Wide VPN

If you prefer VPN:
- Mullvad (privacy-focused, $5/month)
- ProtonVPN (free tier available)
- WireGuard + VPS (~$5/month total)

## Known Limitations

### Symbol Length Validation

**Issue:** Current validation rejects symbols > 12 characters (23 of 541 symbols).

**Affected Symbols:**
```
1000000BOBUSDT (14 chars)
1000000MOGUSDT (14 chars)
1000CHEEMSUSDT (14 chars)
BROCCOLI714USDT (15 chars)
BROCCOLIF3BUSDT (15 chars)
...
```

**Workaround:** The validation is in `scripts/download_binance_data.py:normalize_and_validate_symbol()`. These are legitimate Binance symbols, but the validator needs updating to accept up to 18 characters.

**Status:** 518 of 541 symbols (95.7%) work with current validation.

### Lifecycle Tracking

**Issue:** CoinGecko does not provide launch dates or `onboardDate`.

**Options:**
1. Manual maintenance of `binance_symbol_lifecycle.json` (recommended)
2. Heuristic approximation (first appearance in Binance Vision ZIPs)
3. Snapshot comparison to detect new listings

**Current Implementation:** Auto-discovery without launch dates (sufficient for data acquisition).

## Usage Patterns

### Pattern 1: Explicit Config (No Registry)

```yaml
data_acquisition:
  candidate_instruments:
    - BTCUSDT_PERP
    - ETHUSDT_PERP
    - BNBUSDT_PERP

universe:
  layer_a_instruments:
    - BTCUSDT_PERP
    - ETHUSDT_PERP
```

**Result:** Downloads 3 instruments (explicit list).

### Pattern 2: Auto-Discovery (Registry)

```yaml
data_acquisition:
  auto_discover: true

universe:
  layer_a_instruments:
    - BTCUSDT_PERP
    - ETHUSDT_PERP
```

**Result:** Downloads 541 instruments (from registry), trades 2 instruments.

### Pattern 3: Backward Compatible (No data_acquisition)

```yaml
universe:
  layer_a_instruments:
    - BTCUSDT_PERP
    - ETHUSDT_PERP
```

**Result:** Downloads 2 instruments (same as tradable universe).

## Maintenance

### Refresh Frequency

**Recommended:** Weekly or monthly

Binance rarely lists new perpetuals (1-2 per week at most). CoinGecko free tier allows 10-50 calls/minute, so daily refreshes are fine but unnecessary.

**Command:**
```bash
# Run weekly via cron/systemd
python scripts/refresh_binance_market_registry.py --env dev
```

### Monitoring New Listings

**Detection:**
```python
import json
from pathlib import Path

# Compare with previous snapshot
current = json.loads(Path('envs/dev/data/raw/metadata/binance_perp_registry.json').read_text())
previous = json.loads(Path('envs/dev/data/raw/metadata/binance_perp_registry.json.backup').read_text())

current_symbols = set(current['instruments'].keys())
previous_symbols = set(previous['instruments'].keys())

new_symbols = current_symbols - previous_symbols
if new_symbols:
    print(f"New listings detected: {new_symbols}")
```

## Testing

### Unit Tests

```bash
# Run discovery tests
pytest tests/test_perp_discovery.py -v

# Run existing tests (backward compatibility)
pytest tests/test_candidate_expansion_phase1.py -v
```

### Integration Test

```bash
# 1. Refresh registry
python scripts/refresh_binance_market_registry.py --env dev --dry-run

# 2. Test with auto_discover
python scripts/update_data_monthly.py \
  --config config/test_auto_discover.yaml \
  --env dev \
  --dry-run \
  --lag-months 2

# 3. Verify source
# Should see: "Using candidate instruments from: discovered_candidate_instruments.json"
```

## Files Modified

### New Files
- `scripts/refresh_binance_market_registry.py` (233 lines)
- `tests/test_perp_discovery.py` (213 lines)
- `config/test_auto_discover.yaml` (test config)

### Modified Files
- `sysdata/crypto/config_helpers.py` (+60 lines)
  - Added `extract_candidate_instruments_with_registry()`
  - Enhanced precedence logic
- `scripts/update_data_monthly.py` (~15 lines)
  - Updated `extract_universe_symbols()` to accept `env_root`
  - Updated `update_raw_data()` to pass `env_root`
  - Added `Optional` import

### Artifacts (Environment-Specific)
- `envs/{env}/data/raw/metadata/coingecko_derivatives_snapshot.json`
- `envs/{env}/data/raw/metadata/binance_perp_registry.json`
- `envs/{env}/data/raw/metadata/discovered_candidate_instruments.json`

## Future Enhancements

### Phase 1: Infrastructure (✅ Complete)
- Automatic discovery via CoinGecko
- Registry generation with atomic writes
- Integration with `update_data_monthly.py`
- Comprehensive tests

### Phase 2: Lifecycle Tracking (Future)
- Snapshot comparison for new listings
- Heuristic launch date approximation
- Alert system for delistings

### Phase 3: Market-Cap Filtering (Future)
- Cross-reference with CoinMarketCap for market caps
- Filter by minimum market cap threshold
- Exclude micro-cap / meme coins

### Phase 4: Auto-Reconciliation (Future)
- Compare registry with manual config
- Alert on mismatches
- Suggest additions/removals

## FAQ

**Q: Does auto_discover change the tradable universe?**
A: No. It only affects data acquisition. Tradable universe is ALWAYS `universe.layer_a_instruments`.

**Q: Can I disable auto_discover?**
A: Yes. Set `auto_discover: false` or remove the section. Falls back to existing behavior.

**Q: What if the registry is missing?**
A: Falls back to `universe.layer_a_instruments` with a warning in logs.

**Q: How often should I refresh the registry?**
A: Weekly or monthly is sufficient. Binance lists new perpetuals slowly.

**Q: Why not use Binance exchangeInfo API?**
A: Geo-blocked in Massachusetts (HTTP 451). CoinGecko works without VPN.

**Q: Can I use CoinGecko for daily trading data?**
A: No. CoinGecko provides snapshots only. You need Binance API (via VPN/proxy) for:
- Precise 00:00 UTC candle closes
- Historical funding rate time series
- 8-hourly funding rate updates

**Q: What about symbols with >12 characters?**
A: Known limitation (23 of 541 symbols). Validation needs updating. 95.7% of symbols work.

**Q: How do I track launch dates?**
A: Manually maintain `binance_symbol_lifecycle.json`. CoinGecko doesn't provide launch dates.

## Support

For issues or questions:
- Check logs: `envs/{env}/logs/`
- Run with `--verbose` flag
- Review `coingecko_derivatives_snapshot.json` for raw data
- Compare registry counts with CoinGecko website
