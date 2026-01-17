"""
Unified Crypto Data Stitcher
=============================

Combines multiple data sources to create the most complete price and funding rate
history for crypto backtesting.

Sources (in priority order for price data):
1. Kraken CSVs (bulk download) - primary, highest quality
2. Kraken API - fills gap from CSV end date to today
3. Coinmetrics - extends history back (BTC to 2010, etc.)
4. CryptoDataDownload Binance - extends SOL/AVAX, fills gaps
5. CoinGecko - tokens not available elsewhere (365d limit)

Sources for funding rates:
1. Combined funding rates (Binance + Bybit) - existing data
2. Binance API - can extend if needed

Author: Generated for pysystemtrade crypto backtesting
"""

import os
import time
import json
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Tuple, Any
from dataclasses import dataclass
from enum import Enum

import numpy as np
import pandas as pd
import requests

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ============================================================================
# CONFIGURATION
# ============================================================================

@dataclass
class DataPaths:
    """Data directory paths."""
    KRAKEN_CSV: str = "/Users/nathanieldavis/pysystemtrade/data/crypto/Kraken_OHLCVT"
    COINMETRICS: str = "/Users/nathanieldavis/pysystemtrade/data/crypto/coinmetrics_community/csv"
    FUNDING_RATES: str = "/Users/nathanieldavis/pysystemtrade/data/crypto/funding_rates/combined"
    OUTPUT: str = "/Users/nathanieldavis/pysystemtrade/data/crypto/stitched"


class DataSource(Enum):
    """Data source identifiers."""
    KRAKEN_CSV = "kraken_csv"
    KRAKEN_API = "kraken_api"
    COINMETRICS = "coinmetrics"
    CRYPTODATADOWNLOAD = "cryptodatadownload"
    COINGECKO = "coingecko"
    FUNDING_COMBINED = "funding_combined"
    BINANCE_FUNDING_API = "binance_funding_api"


# Symbol mappings between sources
SYMBOL_MAPPINGS = {
    # Kraken symbol -> normalized symbol
    'XBT': 'BTC',
    'XXBT': 'BTC',
    'XETH': 'ETH',
    'XXRP': 'XRP',
    'XLTC': 'LTC',
    'XXLM': 'XLM',
}

# Kraken to CoinGecko ID mapping (includes tokens available on both)
KRAKEN_TO_COINGECKO = {
    'BTC': 'bitcoin',
    'ETH': 'ethereum',
    'XRP': 'ripple',
    'LTC': 'litecoin',
    'ADA': 'cardano',
    'SOL': 'solana',
    'AVAX': 'avalanche-2',
    'DOT': 'polkadot',
    'LINK': 'chainlink',
    'UNI': 'uniswap',
    'ATOM': 'cosmos',
    'MATIC': 'matic-network',
    'NEAR': 'near',
    'FTM': 'fantom',
    'ARB': 'arbitrum',
    'OP': 'optimism',
}

# Tokens ONLY available on CoinGecko (not on Kraken) - 365 day limit
# These are popular tokens that Kraken doesn't list
COINGECKO_ONLY_TOKENS = {
    # Layer 1s
    'BNB': 'binancecoin',
    'TON': 'the-open-network',
    'TRX': 'tron',
    'HBAR': 'hedera-hashgraph',
    'ICP': 'internet-computer',
    'APT': 'aptos',
    'SUI': 'sui',
    'SEI': 'sei-network',
    'INJ': 'injective-protocol',
    'TIA': 'celestia',
    'STX': 'blockstack',
    'EGLD': 'elrond-erd-2',
    'KAVA': 'kava',
    'CELO': 'celo',
    'ROSE': 'oasis-network',
    'KAS': 'kaspa',
    'CFX': 'conflux-token',
    # Layer 2s / Scaling
    'IMX': 'immutable-x',
    'STRK': 'starknet',
    'MANTA': 'manta-network',
    'METIS': 'metis-token',
    'ZK': 'zksync',
    # DeFi
    'MKR': 'maker',
    'AAVE': 'aave',
    'LDO': 'lido-dao',
    'SNX': 'havven',
    'CRV': 'curve-dao-token',
    'COMP': 'compound-governance-token',
    'SUSHI': 'sushi',
    'CAKE': 'pancakeswap-token',
    'DYDX': 'dydx',
    'GMX': 'gmx',
    'PENDLE': 'pendle',
    'JUP': 'jupiter-exchange-solana',
    'RAY': 'raydium',
    'ORCA': 'orca',
    # Memecoins
    'DOGE': 'dogecoin',
    'SHIB': 'shiba-inu',
    'PEPE': 'pepe',
    'WIF': 'dogwifcoin',
    'BONK': 'bonk',
    'FLOKI': 'floki',
    # AI / Data
    'FET': 'fetch-ai',
    'RNDR': 'render-token',
    'THETA': 'theta-token',
    'GRT': 'the-graph',
    'FIL': 'filecoin',
    'AR': 'arweave',
    'AKT': 'akash-network',
    'TAO': 'bittensor',
    # Gaming / Metaverse
    'AXS': 'axie-infinity',
    'SAND': 'the-sandbox',
    'MANA': 'decentraland',
    'GALA': 'gala',
    'ENJ': 'enjincoin',
    'MAGIC': 'magic',
    'PRIME': 'echelon-prime',
    # Infrastructure
    'QNT': 'quant-network',
    'VET': 'vechain',
    'IOTA': 'iota',
    'HNT': 'helium',
    'PYTH': 'pyth-network',
    'WLD': 'worldcoin-wld',
    'ENS': 'ethereum-name-service',
}

# Tokens where CryptoDataDownload Binance extends history beyond Kraken
CDD_EXTENSION_TOKENS = {
    'SOL': {'kraken_start': '2021-06-16', 'cdd_start': '2020-08-11', 'extra_months': 10},
    'AVAX': {'kraken_start': '2021-12-20', 'cdd_start': '2020-09-22', 'extra_months': 15},
}

# Rate limiting configuration
RATE_LIMITS = {
    'kraken': 1.0,      # 1 request per second
    'coingecko': 6.0,   # ~10 requests per minute for free tier
    'binance': 0.1,     # 10 requests per second
}


# ============================================================================
# DATA LOADERS
# ============================================================================

class KrakenCSVLoader:
    """Load data from Kraken bulk CSV downloads."""

    def __init__(self, data_dir: str = DataPaths.KRAKEN_CSV):
        self.data_dir = data_dir

    def get_available_pairs(self) -> List[str]:
        """Get list of available USD pairs."""
        pairs = []
        for f in os.listdir(self.data_dir):
            if f.endswith('_1440.csv') and 'USD' in f:
                pair = f.replace('_1440.csv', '')
                pairs.append(pair)
        return sorted(pairs)

    def load(self, symbol: str, timeframe: str = '1440') -> Optional[pd.DataFrame]:
        """Load OHLCV data for a symbol."""
        # Try different symbol formats
        candidates = [
            f"{symbol}USD_{timeframe}.csv",
            f"X{symbol}USD_{timeframe}.csv",
            f"{symbol.replace('BTC', 'XBT')}USD_{timeframe}.csv",
        ]

        for filename in candidates:
            path = os.path.join(self.data_dir, filename)
            if os.path.exists(path):
                df = pd.read_csv(path, header=None,
                               names=['timestamp', 'open', 'high', 'low', 'close', 'volume', 'trades'])
                df['date'] = pd.to_datetime(df['timestamp'], unit='s').dt.normalize()
                df = df.set_index('date')
                df = df[['open', 'high', 'low', 'close', 'volume', 'trades']]
                df['source'] = DataSource.KRAKEN_CSV.value
                return df

        return None


class KrakenAPILoader:
    """Load recent data from Kraken REST API."""

    BASE_URL = "https://api.kraken.com/0/public"

    def __init__(self, rate_limit: float = RATE_LIMITS['kraken']):
        self.rate_limit = rate_limit
        self.last_request = 0

    def _rate_limit_wait(self):
        """Wait to respect rate limits."""
        elapsed = time.time() - self.last_request
        if elapsed < self.rate_limit:
            time.sleep(self.rate_limit - elapsed)
        self.last_request = time.time()

    def load(self, symbol: str, since: Optional[datetime] = None) -> Optional[pd.DataFrame]:
        """Load OHLC data from Kraken API (max 720 candles)."""
        self._rate_limit_wait()

        # Map symbol to Kraken format
        kraken_symbol = symbol.replace('BTC', 'XBT')
        pair = f"{kraken_symbol}USD"

        params = {'pair': pair, 'interval': 1440}  # Daily
        if since:
            params['since'] = int(since.timestamp())

        try:
            response = requests.get(f"{self.BASE_URL}/OHLC", params=params, timeout=30)
            data = response.json()

            if data.get('error') and len(data['error']) > 0:
                logger.warning(f"Kraken API error for {symbol}: {data['error']}")
                return None

            # Find the result key (varies by pair)
            result_key = None
            for key in data.get('result', {}):
                if key != 'last':
                    result_key = key
                    break

            if not result_key:
                return None

            ohlc_data = data['result'][result_key]
            if not ohlc_data:
                return None

            df = pd.DataFrame(ohlc_data,
                            columns=['timestamp', 'open', 'high', 'low', 'close', 'vwap', 'volume', 'count'])
            df['date'] = pd.to_datetime(df['timestamp'].astype(int), unit='s').dt.normalize()
            df = df.set_index('date')

            # Convert string prices to float
            for col in ['open', 'high', 'low', 'close', 'volume']:
                df[col] = df[col].astype(float)

            df = df[['open', 'high', 'low', 'close', 'volume']]
            df['trades'] = df.get('count', 0)
            df['source'] = DataSource.KRAKEN_API.value

            return df

        except Exception as e:
            logger.error(f"Kraken API error for {symbol}: {e}")
            return None


class CoinmetricsLoader:
    """Load data from Coinmetrics Community CSV files."""

    def __init__(self, data_dir: str = DataPaths.COINMETRICS):
        self.data_dir = data_dir

    def get_available_tokens(self) -> List[str]:
        """Get list of available tokens with price data."""
        tokens = []
        for f in os.listdir(self.data_dir):
            if f.endswith('.csv'):
                tokens.append(f.replace('.csv', ''))
        return sorted(tokens)

    def load(self, symbol: str) -> Optional[pd.DataFrame]:
        """Load price data for a symbol."""
        path = os.path.join(self.data_dir, f"{symbol.lower()}.csv")
        if not os.path.exists(path):
            return None

        try:
            df = pd.read_csv(path)
            df['date'] = pd.to_datetime(df['time'])
            df = df.set_index('date')

            # Use PriceUSD if available, otherwise ReferenceRateUSD
            if 'PriceUSD' in df.columns and df['PriceUSD'].notna().sum() > 0:
                price_col = 'PriceUSD'
            elif 'ReferenceRateUSD' in df.columns:
                price_col = 'ReferenceRateUSD'
            else:
                return None

            result = pd.DataFrame({
                'open': df[price_col],
                'high': df[price_col],
                'low': df[price_col],
                'close': df[price_col],
                'volume': df.get('volume_reported_spot_usd_1d', np.nan),
            })
            result['source'] = DataSource.COINMETRICS.value
            result = result.dropna(subset=['close'])

            return result

        except Exception as e:
            logger.error(f"Coinmetrics load error for {symbol}: {e}")
            return None


class CryptoDataDownloadLoader:
    """Load data from CryptoDataDownload (Binance)."""

    BASE_URL = "https://www.cryptodatadownload.com/cdd"

    def __init__(self, rate_limit: float = 0.5):
        self.rate_limit = rate_limit
        self.last_request = 0
        self.cache: Dict[str, pd.DataFrame] = {}

    def _rate_limit_wait(self):
        elapsed = time.time() - self.last_request
        if elapsed < self.rate_limit:
            time.sleep(self.rate_limit - elapsed)
        self.last_request = time.time()

    def load(self, symbol: str, exchange: str = 'Binance') -> Optional[pd.DataFrame]:
        """Load daily OHLCV data from CryptoDataDownload."""
        cache_key = f"{exchange}_{symbol}"
        if cache_key in self.cache:
            return self.cache[cache_key]

        self._rate_limit_wait()

        # Binance uses USDT pairs
        url = f"{self.BASE_URL}/{exchange}_{symbol}USDT_d.csv"

        try:
            response = requests.get(url, timeout=30)
            if response.status_code != 200:
                return None

            # Skip first row (contains website URL)
            lines = response.text.strip().split('\n')
            if lines[0].startswith('http'):
                lines = lines[1:]

            from io import StringIO
            df = pd.read_csv(StringIO('\n'.join(lines)))

            # Standardize column names
            df.columns = df.columns.str.lower().str.strip()

            # Parse date
            if 'date' in df.columns:
                df['date'] = pd.to_datetime(df['date'])
            elif 'unix' in df.columns:
                df['date'] = pd.to_datetime(df['unix'], unit='ms')

            df = df.set_index('date')
            df = df.sort_index()

            # Standardize OHLCV columns
            result = pd.DataFrame({
                'open': df['open'].astype(float),
                'high': df['high'].astype(float),
                'low': df['low'].astype(float),
                'close': df['close'].astype(float),
                'volume': df.get(f'volume {symbol.lower()}', df.get('volume', np.nan)),
            })
            result['source'] = DataSource.CRYPTODATADOWNLOAD.value

            self.cache[cache_key] = result
            return result

        except Exception as e:
            logger.error(f"CryptoDataDownload error for {symbol}: {e}")
            return None


class CoinGeckoLoader:
    """Load data from CoinGecko API (365-day limit for free tier)."""

    BASE_URL = "https://api.coingecko.com/api/v3"

    def __init__(self, rate_limit: float = RATE_LIMITS['coingecko']):
        self.rate_limit = rate_limit
        self.last_request = 0

    def _rate_limit_wait(self):
        elapsed = time.time() - self.last_request
        if elapsed < self.rate_limit:
            time.sleep(self.rate_limit - elapsed)
        self.last_request = time.time()

    def get_coin_id(self, symbol: str) -> Optional[str]:
        """Get CoinGecko coin ID for a symbol."""
        symbol_upper = symbol.upper()
        # Check both mappings
        if symbol_upper in KRAKEN_TO_COINGECKO:
            return KRAKEN_TO_COINGECKO[symbol_upper]
        if symbol_upper in COINGECKO_ONLY_TOKENS:
            return COINGECKO_ONLY_TOKENS[symbol_upper]
        return None

    def is_coingecko_only(self, symbol: str) -> bool:
        """Check if a symbol is only available on CoinGecko (not Kraken)."""
        return symbol.upper() in COINGECKO_ONLY_TOKENS

    def load(self, symbol: str, days: int = 365) -> Optional[pd.DataFrame]:
        """Load market chart data (max 365 days for free tier)."""
        coin_id = self.get_coin_id(symbol)
        if not coin_id:
            logger.warning(f"No CoinGecko mapping for {symbol}")
            return None

        self._rate_limit_wait()

        try:
            url = f"{self.BASE_URL}/coins/{coin_id}/market_chart"
            params = {'vs_currency': 'usd', 'days': min(days, 365), 'interval': 'daily'}

            response = requests.get(url, params=params, timeout=30)
            data = response.json()

            if 'error' in data:
                logger.warning(f"CoinGecko error for {symbol}: {data['error']}")
                return None

            prices = data.get('prices', [])
            if not prices:
                return None

            df = pd.DataFrame(prices, columns=['timestamp', 'close'])
            df['date'] = pd.to_datetime(df['timestamp'], unit='ms').dt.normalize()
            df = df.set_index('date')

            # CoinGecko only provides close prices in market_chart
            df['open'] = df['close']
            df['high'] = df['close']
            df['low'] = df['close']
            df['volume'] = np.nan  # Would need separate call
            df['source'] = DataSource.COINGECKO.value

            return df[['open', 'high', 'low', 'close', 'volume', 'source']]

        except Exception as e:
            logger.error(f"CoinGecko error for {symbol}: {e}")
            return None


class FundingRateLoader:
    """Load funding rate data from combined files and exchange APIs."""

    BINANCE_API = "https://fapi.binance.com/fapi/v1/fundingRate"
    BYBIT_API = "https://api.bybit.com/v5/market/funding/history"

    # Bybit symbol mapping
    BYBIT_SYMBOLS = {
        'BTC': 'BTCUSDT',
        'ETH': 'ETHUSDT',
        'SOL': 'SOLUSDT',
        'XRP': 'XRPUSDT',
        'ADA': 'ADAUSDT',
        'AVAX': 'AVAXUSDT',
        'LINK': 'LINKUSDT',
        'UNI': 'UNIUSDT',
        'DOT': 'DOTUSDT',
        'MATIC': 'MATICUSDT',
    }

    def __init__(self, data_dir: str = DataPaths.FUNDING_RATES):
        self.data_dir = data_dir
        self.last_request = 0

    def load_combined(self, symbol: str) -> Optional[pd.DataFrame]:
        """Load from combined funding rate files."""
        path = os.path.join(self.data_dir, f"{symbol.upper()}_funding_combined.csv")
        if not os.path.exists(path):
            return None

        try:
            df = pd.read_csv(path, parse_dates=['datetime'])
            df['date'] = df['datetime'].dt.normalize()

            # Aggregate to daily
            daily = df.groupby('date').agg({
                'fundingRate': 'sum'
            }).reset_index()
            daily = daily.set_index('date')
            daily['source'] = DataSource.FUNDING_COMBINED.value

            return daily

        except Exception as e:
            logger.error(f"Funding rate load error for {symbol}: {e}")
            return None

    def load_bybit_api(self, symbol: str, start_time: Optional[datetime] = None,
                       limit: int = 200) -> Optional[pd.DataFrame]:
        """Load funding rates from Bybit API (no geo-restrictions)."""
        elapsed = time.time() - self.last_request
        if elapsed < RATE_LIMITS['binance']:
            time.sleep(RATE_LIMITS['binance'] - elapsed)
        self.last_request = time.time()

        bybit_symbol = self.BYBIT_SYMBOLS.get(symbol.upper())
        if not bybit_symbol:
            return None

        try:
            params = {
                'category': 'linear',
                'symbol': bybit_symbol,
                'limit': limit,
            }
            if start_time:
                params['startTime'] = int(start_time.timestamp() * 1000)

            response = requests.get(self.BYBIT_API, params=params, timeout=30)
            data = response.json()

            if data.get('retCode') != 0:
                logger.warning(f"Bybit API error for {symbol}: {data.get('retMsg')}")
                return None

            records = data.get('result', {}).get('list', [])
            if not records:
                return None

            df = pd.DataFrame(records)
            df['date'] = pd.to_datetime(df['fundingRateTimestamp'].astype(int), unit='ms').dt.normalize()
            df['fundingRate'] = df['fundingRate'].astype(float)

            # Aggregate to daily
            daily = df.groupby('date').agg({
                'fundingRate': 'sum'
            }).reset_index()
            daily = daily.set_index('date')
            daily['source'] = 'bybit_api'

            return daily

        except Exception as e:
            logger.error(f"Bybit funding API error for {symbol}: {e}")
            return None

    def load_binance_api(self, symbol: str, start_time: Optional[datetime] = None,
                         limit: int = 1000) -> Optional[pd.DataFrame]:
        """Load funding rates from Binance API (may be geo-restricted)."""
        elapsed = time.time() - self.last_request
        if elapsed < RATE_LIMITS['binance']:
            time.sleep(RATE_LIMITS['binance'] - elapsed)
        self.last_request = time.time()

        try:
            params = {'symbol': f"{symbol.upper()}USDT", 'limit': limit}
            if start_time:
                params['startTime'] = int(start_time.timestamp() * 1000)

            response = requests.get(self.BINANCE_API, params=params, timeout=30)
            data = response.json()

            if isinstance(data, dict) and 'code' in data:
                logger.warning(f"Binance API error for {symbol}: {data}")
                return None

            if not data:
                return None

            df = pd.DataFrame(data)
            df['date'] = pd.to_datetime(df['fundingTime'], unit='ms').dt.normalize()
            df['fundingRate'] = df['fundingRate'].astype(float)

            # Aggregate to daily
            daily = df.groupby('date').agg({
                'fundingRate': 'sum'
            }).reset_index()
            daily = daily.set_index('date')
            daily['source'] = DataSource.BINANCE_FUNDING_API.value

            return daily

        except Exception as e:
            logger.error(f"Binance funding API error for {symbol}: {e}")
            return None


# ============================================================================
# STITCHING ENGINE
# ============================================================================

class DataStitcher:
    """Main stitching engine that combines all data sources."""

    def __init__(self):
        self.kraken_csv = KrakenCSVLoader()
        self.kraken_api = KrakenAPILoader()
        self.coinmetrics = CoinmetricsLoader()
        self.cryptodatadownload = CryptoDataDownloadLoader()
        self.coingecko = CoinGeckoLoader()
        self.funding = FundingRateLoader()

    def calculate_ratio_adjustment(self, primary: pd.DataFrame, extension: pd.DataFrame,
                                   overlap_days: int = 5) -> float:
        """Calculate ratio to adjust extension prices to match primary at join point."""
        overlap_dates = primary.index.intersection(extension.index)
        if len(overlap_dates) < overlap_days:
            return 1.0

        overlap_dates = sorted(overlap_dates)[:overlap_days]

        ratios = []
        for date in overlap_dates:
            primary_price = primary.loc[date, 'close']
            extension_price = extension.loc[date, 'close']
            if pd.notna(primary_price) and pd.notna(extension_price) and extension_price > 0:
                ratios.append(primary_price / extension_price)

        return np.median(ratios) if ratios else 1.0

    def stitch_dataframes(self, frames: List[Tuple[pd.DataFrame, int]],
                          adjust_prices: bool = True) -> pd.DataFrame:
        """
        Stitch multiple dataframes together by priority.

        Args:
            frames: List of (dataframe, priority) tuples. Lower priority = higher precedence.
            adjust_prices: If True, apply ratio adjustment at join points.

        Returns:
            Combined dataframe with best data for each date.
        """
        if not frames:
            return pd.DataFrame()

        # Sort by priority (lower = better)
        frames = sorted([(df, p) for df, p in frames if df is not None and len(df) > 0],
                       key=lambda x: x[1])

        if not frames:
            return pd.DataFrame()

        # Start with highest priority data
        result = frames[0][0].copy()

        for df, priority in frames[1:]:
            if df is None or len(df) == 0:
                continue

            # Find dates not in result
            new_dates = df.index.difference(result.index)

            if len(new_dates) == 0:
                continue

            extension = df.loc[new_dates].copy()

            # Apply ratio adjustment if needed
            if adjust_prices and 'close' in extension.columns:
                ratio = self.calculate_ratio_adjustment(result, df)
                if ratio != 1.0:
                    for col in ['open', 'high', 'low', 'close']:
                        if col in extension.columns:
                            extension[col] = extension[col] * ratio
                    logger.info(f"Applied ratio adjustment: {ratio:.4f}")

            result = pd.concat([result, extension])

        result = result.sort_index()
        result = result[~result.index.duplicated(keep='first')]

        return result

    def stitch_price_data(self, symbol: str,
                          use_kraken_api: bool = True,
                          use_coinmetrics: bool = True,
                          use_cdd: bool = True,
                          use_coingecko: bool = True) -> Tuple[pd.DataFrame, Dict[str, Any]]:
        """
        Stitch price data from all available sources for a symbol.

        Strategy:
        - Use highest priority source for each date
        - Fill gaps in higher priority sources with lower priority data
        - Apply ratio adjustment to ensure price continuity

        Priority (1 = highest):
        1. Kraken CSV (official exchange, highest quality)
        2. Kraken API (recent data after CSV ends)
        3. CryptoDataDownload Binance (SOL/AVAX extension + gap fill)
        4. Coinmetrics (historical extension + gap fill)
        5. CoinGecko (last resort, 365d limit)
        """
        metadata = {
            'symbol': symbol,
            'sources_used': [],
            'date_range': None,
            'total_days': 0,
            'gaps_filled': 0,
        }

        frames = []

        # Priority 1: Kraken CSV
        kraken_df = self.kraken_csv.load(symbol)
        if kraken_df is not None and len(kraken_df) > 0:
            frames.append((kraken_df, 1))
            metadata['sources_used'].append('kraken_csv')
            logger.info(f"{symbol}: Kraken CSV {kraken_df.index.min().date()} to {kraken_df.index.max().date()}")

        # Priority 2: Kraken API (fill recent gap)
        if use_kraken_api and kraken_df is not None:
            last_csv_date = kraken_df.index.max()
            if (datetime.now() - last_csv_date).days > 1:
                api_df = self.kraken_api.load(symbol, since=last_csv_date)
                if api_df is not None and len(api_df) > 0:
                    frames.append((api_df, 2))
                    metadata['sources_used'].append('kraken_api')
                    logger.info(f"{symbol}: Kraken API {api_df.index.min().date()} to {api_df.index.max().date()}")

        # Priority 3: CryptoDataDownload (SOL/AVAX extension + gap fill)
        if use_cdd and symbol.upper() in CDD_EXTENSION_TOKENS:
            cdd_df = self.cryptodatadownload.load(symbol)
            if cdd_df is not None and len(cdd_df) > 0:
                frames.append((cdd_df, 3))
                metadata['sources_used'].append('cryptodatadownload')
                logger.info(f"{symbol}: CDD Binance {cdd_df.index.min().date()} to {cdd_df.index.max().date()}")

        # Priority 4: Coinmetrics (historical extension + gap fill)
        if use_coinmetrics:
            cm_df = self.coinmetrics.load(symbol)
            if cm_df is not None and len(cm_df) > 0:
                frames.append((cm_df, 4))
                metadata['sources_used'].append('coinmetrics')
                logger.info(f"{symbol}: Coinmetrics {cm_df.index.min().date()} to {cm_df.index.max().date()}")

        # Priority 5: CoinGecko (fallback for tokens not in other sources, or CoinGecko-only tokens)
        if use_coingecko:
            # Use CoinGecko if: no other data, OR this is a CoinGecko-only token
            is_cg_only = self.coingecko.is_coingecko_only(symbol)
            if len(frames) == 0 or is_cg_only:
                cg_df = self.coingecko.load(symbol, days=365)
                if cg_df is not None and len(cg_df) > 0:
                    frames.append((cg_df, 5))
                    if 'coingecko' not in metadata['sources_used']:
                        metadata['sources_used'].append('coingecko')
                    if is_cg_only:
                        logger.info(f"{symbol}: CoinGecko (365d only) {cg_df.index.min().date()} to {cg_df.index.max().date()}")
                    else:
                        logger.info(f"{symbol}: CoinGecko {cg_df.index.min().date()} to {cg_df.index.max().date()}")

        # Stitch all frames together (fills gaps with lower priority sources)
        result = self.stitch_dataframes(frames, adjust_prices=True)

        if len(result) > 0:
            metadata['date_range'] = f"{result.index.min().date()} to {result.index.max().date()}"
            metadata['total_days'] = len(result)

            # Count gap fills (source transitions after first transition)
            if 'source' in result.columns:
                transitions = (result['source'] != result['source'].shift(1)).sum() - 1
                metadata['gaps_filled'] = max(0, transitions - len(metadata['sources_used']) + 1)

        return result, metadata

    def stitch_funding_data(self, symbol: str,
                            use_api: bool = True) -> Tuple[pd.DataFrame, Dict[str, Any]]:
        """
        Stitch funding rate data from all available sources.

        Priority:
        1. Combined funding files (Binance + Bybit)
        2. Bybit API (recent data, no geo-restrictions)
        3. Binance API (fallback, may be geo-restricted)
        """
        metadata = {
            'symbol': symbol,
            'sources_used': [],
            'date_range': None,
            'total_days': 0,
        }

        frames = []

        # Priority 1: Combined funding files
        combined_df = self.funding.load_combined(symbol)
        if combined_df is not None and len(combined_df) > 0:
            frames.append((combined_df, 1))
            metadata['sources_used'].append('funding_combined')
            logger.info(f"{symbol} funding: Combined {combined_df.index.min().date()} to {combined_df.index.max().date()}")

        # Priority 2: Bybit API (fill recent gap, no geo-restrictions)
        if use_api:
            last_date = None
            if combined_df is not None and len(combined_df) > 0:
                last_date = combined_df.index.max()

            if last_date is None or (datetime.now() - last_date).days > 1:
                # Try Bybit first (no geo-restrictions)
                api_df = self.funding.load_bybit_api(symbol, start_time=last_date)
                if api_df is not None and len(api_df) > 0:
                    frames.append((api_df, 2))
                    metadata['sources_used'].append('bybit_api')
                    logger.info(f"{symbol} funding: Bybit API {api_df.index.min().date()} to {api_df.index.max().date()}")
                else:
                    # Fallback to Binance (may be geo-restricted)
                    api_df = self.funding.load_binance_api(symbol, start_time=last_date)
                    if api_df is not None and len(api_df) > 0:
                        frames.append((api_df, 2))
                        metadata['sources_used'].append('binance_funding_api')
                        logger.info(f"{symbol} funding: Binance API {api_df.index.min().date()} to {api_df.index.max().date()}")

        # Combine frames (no price adjustment for funding rates)
        result = self.stitch_dataframes(frames, adjust_prices=False)

        if len(result) > 0:
            metadata['date_range'] = f"{result.index.min().date()} to {result.index.max().date()}"
            metadata['total_days'] = len(result)

        return result, metadata


# ============================================================================
# MASTER ORCHESTRATOR
# ============================================================================

class CryptoDataOrchestrator:
    """Master orchestrator for crypto data stitching."""

    def __init__(self, output_dir: str = DataPaths.OUTPUT):
        self.stitcher = DataStitcher()
        self.output_dir = output_dir
        os.makedirs(output_dir, exist_ok=True)

    def get_all_available_symbols(self) -> Dict[str, List[str]]:
        """Get all available symbols from each source."""
        return {
            'kraken_csv': self.stitcher.kraken_csv.get_available_pairs(),
            'coinmetrics': self.stitcher.coinmetrics.get_available_tokens(),
            'coingecko_only': list(COINGECKO_ONLY_TOKENS.keys()),
        }

    def get_coingecko_only_symbols(self) -> List[str]:
        """Get list of symbols only available on CoinGecko (365d limit)."""
        return list(COINGECKO_ONLY_TOKENS.keys())

    def get_unified_symbols(self, include_coingecko_only: bool = True,
                            min_coinmetrics_days: int = 365) -> Dict[str, Any]:
        """
        Discover ALL available tokens across all data sources.

        Returns a unified, deduplicated list of symbols with source attribution.

        Args:
            include_coingecko_only: Include tokens only available on CoinGecko (365d limit)
            min_coinmetrics_days: Minimum days of data required for Coinmetrics-only tokens

        Returns:
            Dict with:
                - 'symbols': List of all unique symbols
                - 'by_source': Dict mapping source -> list of symbols
                - 'source_priority': Dict mapping symbol -> primary source
                - 'stats': Summary statistics
        """
        # Normalize Kraken symbol from pair name (e.g., "BTCUSD" -> "BTC", "XBTUSD" -> "BTC")
        def normalize_kraken_symbol(pair: str) -> str:
            # Remove USD suffix
            symbol = pair.replace('USD', '')
            # Handle Kraken's X prefix for some assets
            if symbol.startswith('XX'):
                symbol = symbol[2:]
            elif symbol.startswith('X') and len(symbol) > 3:
                symbol = symbol[1:]
            # Map Kraken-specific symbols
            symbol_map = {'XBT': 'BTC', 'XDG': 'DOGE'}
            return symbol_map.get(symbol, symbol)

        symbols_by_source = {
            'kraken': set(),
            'coinmetrics': set(),
            'coingecko_only': set(),
        }
        source_priority = {}  # symbol -> primary source

        # 1. Get all Kraken USD pairs (highest priority for most tokens)
        kraken_pairs = self.stitcher.kraken_csv.get_available_pairs()
        for pair in kraken_pairs:
            if 'USD' in pair:
                symbol = normalize_kraken_symbol(pair)
                if symbol and len(symbol) >= 2:
                    symbols_by_source['kraken'].add(symbol)
                    source_priority[symbol] = 'kraken'

        logger.info(f"Found {len(symbols_by_source['kraken'])} Kraken USD symbols")

        # 2. Get Coinmetrics tokens with price data
        coinmetrics_tokens = self.stitcher.coinmetrics.get_available_tokens()
        coinmetrics_with_price = []

        for token in coinmetrics_tokens:
            # Check if token has price data and sufficient history
            df = self.stitcher.coinmetrics.load(token.upper())
            if df is not None and len(df) >= min_coinmetrics_days:
                symbol = token.upper()
                symbols_by_source['coinmetrics'].add(symbol)
                coinmetrics_with_price.append(symbol)
                # Only set as primary source if not already on Kraken
                if symbol not in source_priority:
                    source_priority[symbol] = 'coinmetrics'

        logger.info(f"Found {len(coinmetrics_with_price)} Coinmetrics tokens with >= {min_coinmetrics_days} days of price data")

        # 3. Add CoinGecko-only tokens (lowest priority, 365d limit)
        if include_coingecko_only:
            for symbol in COINGECKO_ONLY_TOKENS.keys():
                symbols_by_source['coingecko_only'].add(symbol)
                if symbol not in source_priority:
                    source_priority[symbol] = 'coingecko_only'

            logger.info(f"Added {len(COINGECKO_ONLY_TOKENS)} CoinGecko-only tokens")

        # Compile unified list
        all_symbols_set = set()
        all_symbols_set.update(symbols_by_source['kraken'])
        all_symbols_set.update(symbols_by_source['coinmetrics'])
        all_symbols_set.update(symbols_by_source['coingecko_only'])

        # Calculate statistics (using sets)
        kraken_only = symbols_by_source['kraken'] - symbols_by_source['coinmetrics'] - symbols_by_source['coingecko_only']
        coinmetrics_only = symbols_by_source['coinmetrics'] - symbols_by_source['kraken'] - symbols_by_source['coingecko_only']
        coingecko_exclusive = symbols_by_source['coingecko_only'] - symbols_by_source['kraken'] - symbols_by_source['coinmetrics']
        multi_source = all_symbols_set - kraken_only - coinmetrics_only - coingecko_exclusive

        # Sort alphabetically for output
        all_symbols = sorted(all_symbols_set)

        stats = {
            'total_unique': len(all_symbols),
            'kraken_total': len(symbols_by_source['kraken']),
            'coinmetrics_total': len(symbols_by_source['coinmetrics']),
            'coingecko_only_total': len(symbols_by_source['coingecko_only']),
            'kraken_exclusive': len(kraken_only),
            'coinmetrics_exclusive': len(coinmetrics_only),
            'coingecko_exclusive': len(coingecko_exclusive),
            'multi_source': len(multi_source),
        }

        logger.info(f"Unified token discovery: {stats['total_unique']} total unique symbols")
        logger.info(f"  - Kraken exclusive: {stats['kraken_exclusive']}")
        logger.info(f"  - Coinmetrics exclusive: {stats['coinmetrics_exclusive']}")
        logger.info(f"  - CoinGecko exclusive: {stats['coingecko_exclusive']}")
        logger.info(f"  - Multi-source: {stats['multi_source']}")

        return {
            'symbols': all_symbols,
            'by_source': {k: sorted(v) for k, v in symbols_by_source.items()},
            'source_priority': source_priority,
            'stats': stats,
        }

    def process_symbol(self, symbol: str,
                       save: bool = True,
                       include_funding: bool = True) -> Dict[str, Any]:
        """
        Process a single symbol: stitch price data and optionally funding data.
        """
        results = {'symbol': symbol}

        # Stitch price data
        logger.info(f"Processing price data for {symbol}...")
        price_df, price_meta = self.stitcher.stitch_price_data(symbol)
        results['price'] = {
            'metadata': price_meta,
            'data': price_df,
        }

        if save and len(price_df) > 0:
            price_path = os.path.join(self.output_dir, f"{symbol}_price.csv")
            price_df.to_csv(price_path)
            logger.info(f"Saved price data to {price_path}")

        # Stitch funding data if available
        if include_funding:
            logger.info(f"Processing funding data for {symbol}...")
            funding_df, funding_meta = self.stitcher.stitch_funding_data(symbol)
            results['funding'] = {
                'metadata': funding_meta,
                'data': funding_df,
            }

            if save and len(funding_df) > 0:
                funding_path = os.path.join(self.output_dir, f"{symbol}_funding.csv")
                funding_df.to_csv(funding_path)
                logger.info(f"Saved funding data to {funding_path}")

        return results

    def process_all(self, symbols: Optional[List[str]] = None,
                    include_funding: bool = True,
                    save: bool = True,
                    unified: bool = False,
                    include_coingecko_only: bool = True,
                    skip_existing: bool = False) -> Dict[str, Dict]:
        """
        Process multiple symbols.

        Args:
            symbols: List of symbols to process. If None, uses default behavior.
            include_funding: Whether to process funding rate data.
            save: Whether to save results to CSV files.
            unified: If True and symbols is None, use unified token discovery
                     to process ALL tokens from ALL sources.
            include_coingecko_only: When unified=True, include CoinGecko-only tokens.
            skip_existing: Skip symbols that already have output files.

        If symbols is None and unified is False, process all Kraken symbols.
        If symbols is None and unified is True, process all unified symbols.
        """
        if symbols is None:
            if unified:
                # Use unified token discovery
                discovery = self.get_unified_symbols(
                    include_coingecko_only=include_coingecko_only
                )
                symbols = discovery['symbols']
                logger.info(f"Unified discovery found {len(symbols)} total symbols")
            else:
                # Get unique symbols from Kraken USD pairs only
                pairs = self.stitcher.kraken_csv.get_available_pairs()
                symbols = list(set(
                    p.replace('USD', '').replace('XBT', 'BTC').replace('XXBT', 'BTC')
                    for p in pairs if 'USD' in p
                ))

        # Optionally skip existing symbols
        if skip_existing:
            existing = set()
            for f in os.listdir(self.output_dir):
                if f.endswith('_price.csv'):
                    existing.add(f.replace('_price.csv', ''))
            original_count = len(symbols)
            symbols = [s for s in symbols if s not in existing]
            logger.info(f"Skipping {original_count - len(symbols)} existing symbols, {len(symbols)} remaining")

        results = {}
        total = len(symbols)
        for i, symbol in enumerate(symbols, 1):
            try:
                logger.info(f"[{i}/{total}] Processing {symbol}...")
                results[symbol] = self.process_symbol(
                    symbol, save=save, include_funding=include_funding
                )
            except Exception as e:
                logger.error(f"Error processing {symbol}: {e}")
                results[symbol] = {'error': str(e)}

        return results

    def process_unified(self, include_coingecko_only: bool = True,
                        include_funding: bool = True,
                        save: bool = True,
                        skip_existing: bool = True) -> Dict[str, Dict]:
        """
        Convenience method to process ALL tokens from ALL sources.

        This is the recommended way to build a comprehensive dataset.

        Args:
            include_coingecko_only: Include CoinGecko-only tokens (365d limit).
            include_funding: Process funding rate data where available.
            save: Save results to CSV files.
            skip_existing: Skip symbols that already have output files.

        Returns:
            Dict of processing results by symbol.
        """
        logger.info("=" * 60)
        logger.info("UNIFIED DATA PROCESSING - ALL SOURCES")
        logger.info("=" * 60)

        return self.process_all(
            symbols=None,
            include_funding=include_funding,
            save=save,
            unified=True,
            include_coingecko_only=include_coingecko_only,
            skip_existing=skip_existing
        )

    def generate_report(self, results: Dict[str, Dict]) -> str:
        """Generate a summary report of stitching results."""
        lines = [
            "=" * 80,
            "CRYPTO DATA STITCHING REPORT",
            "=" * 80,
            "",
            f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"Output directory: {self.output_dir}",
            "",
            "-" * 80,
            "PRICE DATA SUMMARY",
            "-" * 80,
            "",
            f"{'Symbol':<10} {'Sources':<40} {'Date Range':<25} {'Days':>8}",
            "-" * 80,
        ]

        for symbol, data in sorted(results.items()):
            if 'error' in data:
                lines.append(f"{symbol:<10} ERROR: {data['error']}")
                continue

            if 'price' in data:
                meta = data['price']['metadata']
                sources = ', '.join(meta.get('sources_used', []))
                date_range = meta.get('date_range') or 'N/A'
                days = meta.get('total_days', 0)
                if days > 0:  # Only include tokens with actual data
                    lines.append(f"{symbol:<10} {sources:<40} {date_range:<25} {days:>8}")

        lines.extend([
            "",
            "-" * 80,
            "FUNDING DATA SUMMARY",
            "-" * 80,
            "",
            f"{'Symbol':<10} {'Sources':<40} {'Date Range':<25} {'Days':>8}",
            "-" * 80,
        ])

        for symbol, data in sorted(results.items()):
            if 'funding' in data and data['funding']['metadata'].get('total_days', 0) > 0:
                meta = data['funding']['metadata']
                sources = ', '.join(meta.get('sources_used', []))
                date_range = meta.get('date_range', 'N/A')
                days = meta.get('total_days', 0)
                lines.append(f"{symbol:<10} {sources:<40} {date_range:<25} {days:>8}")

        lines.append("")
        lines.append("=" * 80)

        return '\n'.join(lines)


# ============================================================================
# MAIN EXECUTION
# ============================================================================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description='Crypto Data Stitcher - Unified multi-source data pipeline',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Process all tokens from ALL sources (recommended)
  python data_stitcher.py --unified

  # Process only new tokens (skip existing)
  python data_stitcher.py --unified --skip-existing

  # Process specific symbols
  python data_stitcher.py --symbols BTC ETH SOL

  # Process all Kraken symbols only
  python data_stitcher.py --all

  # Discover available tokens without processing
  python data_stitcher.py --discover
        """
    )
    parser.add_argument('--symbols', nargs='+', help='Symbols to process (default: key tokens)')
    parser.add_argument('--all', action='store_true', help='Process all available Kraken symbols')
    parser.add_argument('--unified', action='store_true',
                        help='Process ALL tokens from ALL sources (Kraken + Coinmetrics + CoinGecko)')
    parser.add_argument('--coingecko-only', action='store_true',
                        help='Process CoinGecko-only tokens (365d limit)')
    parser.add_argument('--no-coingecko', action='store_true',
                        help='When using --unified, exclude CoinGecko-only tokens')
    parser.add_argument('--skip-existing', action='store_true',
                        help='Skip symbols that already have output files')
    parser.add_argument('--discover', action='store_true',
                        help='Only discover and report available tokens, do not process')
    parser.add_argument('--no-save', action='store_true', help='Do not save to files')
    parser.add_argument('--no-funding', action='store_true', help='Skip funding rate data')
    parser.add_argument('--output', default=DataPaths.OUTPUT, help='Output directory')

    args = parser.parse_args()

    orchestrator = CryptoDataOrchestrator(output_dir=args.output)

    # Discovery mode - just report available tokens
    if args.discover:
        print("=" * 70)
        print("UNIFIED TOKEN DISCOVERY")
        print("=" * 70)
        print()
        discovery = orchestrator.get_unified_symbols(include_coingecko_only=True)
        stats = discovery['stats']

        print(f"Total unique symbols: {stats['total_unique']}")
        print()
        print(f"By source:")
        print(f"  Kraken:         {stats['kraken_total']:>4} symbols ({stats['kraken_exclusive']} exclusive)")
        print(f"  Coinmetrics:    {stats['coinmetrics_total']:>4} symbols ({stats['coinmetrics_exclusive']} exclusive)")
        print(f"  CoinGecko-only: {stats['coingecko_only_total']:>4} symbols ({stats['coingecko_exclusive']} exclusive)")
        print(f"  Multi-source:   {stats['multi_source']:>4} symbols")
        print()
        print("All symbols:")
        print("-" * 70)
        symbols = discovery['symbols']
        # Print in columns
        cols = 8
        for i in range(0, len(symbols), cols):
            row = symbols[i:i+cols]
            print("  " + "  ".join(f"{s:<8}" for s in row))
        print()
        exit(0)

    # Determine which symbols to process
    use_unified = False
    if args.symbols:
        symbols = args.symbols
    elif args.unified:
        symbols = None  # Will use unified discovery
        use_unified = True
        include_cg = not args.no_coingecko
        print(f"UNIFIED MODE: Processing ALL tokens from ALL sources")
        if not include_cg:
            print("  (excluding CoinGecko-only tokens)")
    elif args.coingecko_only:
        symbols = orchestrator.get_coingecko_only_symbols()
        print(f"Processing {len(symbols)} CoinGecko-only tokens (365-day limit)")
    elif args.all:
        symbols = None  # Will process all Kraken symbols
    else:
        # Key tokens for backtesting
        symbols = ['BTC', 'ETH', 'SOL', 'AVAX', 'XRP', 'ADA', 'LINK', 'UNI',
                   'DOT', 'ATOM', 'LTC', 'MATIC', 'NEAR', 'FTM', 'ARB', 'OP']

    if not use_unified:
        print(f"Processing symbols: {symbols if symbols else 'ALL Kraken'}")
    print(f"Output directory: {args.output}")
    if args.skip_existing:
        print("Skipping existing symbols: enabled")
    print()

    results = orchestrator.process_all(
        symbols=symbols,
        include_funding=not args.no_funding,
        save=not args.no_save,
        unified=use_unified,
        include_coingecko_only=not args.no_coingecko if use_unified else True,
        skip_existing=args.skip_existing
    )

    report = orchestrator.generate_report(results)
    print(report)

    # Save report
    if not args.no_save:
        report_path = os.path.join(args.output, 'stitching_report.txt')
        with open(report_path, 'w') as f:
            f.write(report)
        print(f"\nReport saved to: {report_path}")
