"""
Unified Crypto Data Adapter for pysystemtrade
==============================================

Provides access to ALL available crypto data sources for backtesting:
1. Stitched data (pre-combined, highest quality)
2. Kraken CSV (412 USD pairs, 2013-2025)
3. Coinmetrics (139 tokens with price, 2010-present)
4. CoinGecko (60+ tokens, 365 days - growing over time)
5. Funding rates (8 tokens, 2016-present)

The adapter automatically selects the best available data source for each token.
"""

import os
from datetime import datetime
from typing import Optional, List, Dict, Tuple

import numpy as np
import pandas as pd

# Data directories
DATA_ROOT = "/Users/nathanieldavis/pysystemtrade/data/crypto"
STITCHED_DIR = os.path.join(DATA_ROOT, "stitched")
KRAKEN_DIR = os.path.join(DATA_ROOT, "Kraken_OHLCVT")
COINMETRICS_DIR = os.path.join(DATA_ROOT, "coinmetrics_community/csv")
FUNDING_DIR = os.path.join(DATA_ROOT, "funding_rates/combined")
COINGECKO_DIR = os.path.join(DATA_ROOT, "coingecko")  # For cached CoinGecko data

# Symbol normalization
KRAKEN_SYMBOL_MAP = {
    'BTC': ['XBTUSD', 'XXBTUSD'],
    'ETH': ['ETHUSD', 'XETHUSD'],
    'XRP': ['XRPUSD', 'XXRPUSD'],
    'LTC': ['LTCUSD', 'XLTCUSD'],
    'XLM': ['XLMUSD', 'XXLMUSD'],
}


class CryptoDataAdapter:
    """
    Unified data adapter that provides access to all crypto data sources.

    Usage:
        adapter = CryptoDataAdapter()

        # Get all available tokens
        tokens = adapter.get_available_tokens()

        # Get price data for a token
        prices = adapter.get_price_data('BTC')

        # Get funding rate data
        funding = adapter.get_funding_data('ETH')
    """

    def __init__(self, cache_coingecko: bool = True):
        """
        Initialize the adapter.

        Args:
            cache_coingecko: If True, cache CoinGecko data locally for future use
        """
        self.cache_coingecko = cache_coingecko
        self._token_cache: Dict[str, pd.DataFrame] = {}
        self._available_tokens: Optional[Dict[str, List[str]]] = None

        # Ensure CoinGecko cache directory exists
        if cache_coingecko:
            os.makedirs(COINGECKO_DIR, exist_ok=True)

    def get_available_tokens(self, refresh: bool = False) -> Dict[str, List[str]]:
        """
        Get all available tokens organized by source.

        Returns:
            Dict with keys: 'stitched', 'kraken', 'coinmetrics', 'coingecko', 'funding'
        """
        if self._available_tokens is not None and not refresh:
            return self._available_tokens

        result = {
            'stitched': [],
            'kraken': [],
            'coinmetrics': [],
            'coingecko': [],
            'funding': [],
            'all': set(),  # Union of all tokens
        }

        # Stitched tokens
        if os.path.exists(STITCHED_DIR):
            for f in os.listdir(STITCHED_DIR):
                if f.endswith('_price.csv'):
                    token = f.replace('_price.csv', '')
                    result['stitched'].append(token)
                    result['all'].add(token)

        # Kraken tokens
        if os.path.exists(KRAKEN_DIR):
            for f in os.listdir(KRAKEN_DIR):
                if f.endswith('_1440.csv') and 'USD' in f:
                    pair = f.replace('_1440.csv', '')
                    # Normalize to standard symbol
                    token = pair.replace('USD', '')
                    if token.startswith('X') and len(token) == 4:
                        token = token[1:]  # Remove X prefix
                    if token == 'XBT':
                        token = 'BTC'
                    result['kraken'].append(token)
                    result['all'].add(token)

        # Coinmetrics tokens (only those with price data)
        if os.path.exists(COINMETRICS_DIR):
            for f in os.listdir(COINMETRICS_DIR):
                if f.endswith('.csv'):
                    token = f.replace('.csv', '').upper()
                    # Check if it has price data
                    try:
                        df = pd.read_csv(os.path.join(COINMETRICS_DIR, f), nrows=5)
                        if 'PriceUSD' in df.columns:
                            result['coinmetrics'].append(token)
                            result['all'].add(token)
                    except:
                        pass

        # CoinGecko cached tokens
        if os.path.exists(COINGECKO_DIR):
            for f in os.listdir(COINGECKO_DIR):
                if f.endswith('_price.csv'):
                    token = f.replace('_price.csv', '')
                    result['coingecko'].append(token)
                    result['all'].add(token)

        # Funding rate tokens
        if os.path.exists(FUNDING_DIR):
            for f in os.listdir(FUNDING_DIR):
                if f.endswith('_funding_combined.csv'):
                    token = f.replace('_funding_combined.csv', '')
                    result['funding'].append(token)

        # Sort lists
        for key in result:
            if isinstance(result[key], list):
                result[key] = sorted(result[key])
            elif isinstance(result[key], set):
                result[key] = sorted(result[key])

        self._available_tokens = result
        return result

    def get_token_sources(self, symbol: str) -> List[str]:
        """Get list of available sources for a token."""
        symbol = symbol.upper()
        tokens = self.get_available_tokens()

        sources = []
        if symbol in tokens['stitched']:
            sources.append('stitched')
        if symbol in tokens['kraken']:
            sources.append('kraken')
        if symbol in tokens['coinmetrics']:
            sources.append('coinmetrics')
        if symbol in tokens['coingecko']:
            sources.append('coingecko')

        return sources

    def _load_stitched(self, symbol: str) -> Optional[pd.DataFrame]:
        """Load stitched price data."""
        path = os.path.join(STITCHED_DIR, f"{symbol}_price.csv")
        if not os.path.exists(path):
            return None

        df = pd.read_csv(path, index_col='date', parse_dates=True)
        return df

    def _load_kraken(self, symbol: str) -> Optional[pd.DataFrame]:
        """Load Kraken CSV price data."""
        # Try different symbol formats
        candidates = [
            f"{symbol}USD_1440.csv",
            f"X{symbol}USD_1440.csv",
            f"{symbol.replace('BTC', 'XBT')}USD_1440.csv",
        ]

        for filename in candidates:
            path = os.path.join(KRAKEN_DIR, filename)
            if os.path.exists(path):
                df = pd.read_csv(path, header=None,
                               names=['timestamp', 'open', 'high', 'low', 'close', 'volume', 'trades'])
                df['date'] = pd.to_datetime(df['timestamp'], unit='s').dt.normalize()
                df = df.set_index('date')
                df = df[['open', 'high', 'low', 'close', 'volume']]
                df['source'] = 'kraken'
                return df

        return None

    def _load_coinmetrics(self, symbol: str) -> Optional[pd.DataFrame]:
        """Load Coinmetrics price data."""
        path = os.path.join(COINMETRICS_DIR, f"{symbol.lower()}.csv")
        if not os.path.exists(path):
            return None

        try:
            df = pd.read_csv(path)
            df['date'] = pd.to_datetime(df['time'])
            df = df.set_index('date')

            if 'PriceUSD' not in df.columns or df['PriceUSD'].isna().all():
                return None

            result = pd.DataFrame({
                'open': df['PriceUSD'],
                'high': df['PriceUSD'],
                'low': df['PriceUSD'],
                'close': df['PriceUSD'],
                'volume': df.get('volume_reported_spot_usd_1d', np.nan),
            })
            result['source'] = 'coinmetrics'
            result = result.dropna(subset=['close'])
            return result
        except Exception:
            return None

    def _load_coingecko_cache(self, symbol: str) -> Optional[pd.DataFrame]:
        """Load cached CoinGecko data."""
        path = os.path.join(COINGECKO_DIR, f"{symbol}_price.csv")
        if not os.path.exists(path):
            return None

        df = pd.read_csv(path, index_col='date', parse_dates=True)
        return df

    def _load_funding_combined(self, symbol: str) -> Optional[pd.DataFrame]:
        """Load combined funding rate data."""
        path = os.path.join(FUNDING_DIR, f"{symbol}_funding_combined.csv")
        if not os.path.exists(path):
            return None

        df = pd.read_csv(path, parse_dates=['datetime'])
        df['date'] = df['datetime'].dt.normalize()

        # Aggregate to daily
        daily = df.groupby('date').agg({
            'fundingRate': 'sum'
        }).reset_index()
        daily = daily.set_index('date')
        daily['source'] = 'funding_combined'

        return daily

    def _load_stitched_funding(self, symbol: str) -> Optional[pd.DataFrame]:
        """Load stitched funding rate data."""
        path = os.path.join(STITCHED_DIR, f"{symbol}_funding.csv")
        if not os.path.exists(path):
            return None

        df = pd.read_csv(path, index_col='date', parse_dates=True)
        return df

    def get_price_data(self, symbol: str,
                       prefer_stitched: bool = True) -> Optional[pd.DataFrame]:
        """
        Get price data for a token from the best available source.

        Priority:
        1. Stitched data (if prefer_stitched=True and available)
        2. Kraken CSV
        3. Coinmetrics
        4. CoinGecko cache

        Args:
            symbol: Token symbol (e.g., 'BTC', 'ETH')
            prefer_stitched: If True, use stitched data when available

        Returns:
            DataFrame with OHLCV data, or None if not found
        """
        symbol = symbol.upper()

        # Check cache
        cache_key = f"price_{symbol}"
        if cache_key in self._token_cache:
            return self._token_cache[cache_key]

        df = None

        # Try sources in priority order
        if prefer_stitched:
            df = self._load_stitched(symbol)
            if df is not None:
                self._token_cache[cache_key] = df
                return df

        df = self._load_kraken(symbol)
        if df is not None:
            self._token_cache[cache_key] = df
            return df

        df = self._load_coinmetrics(symbol)
        if df is not None:
            self._token_cache[cache_key] = df
            return df

        df = self._load_coingecko_cache(symbol)
        if df is not None:
            self._token_cache[cache_key] = df
            return df

        return None

    def get_funding_data(self, symbol: str) -> Optional[pd.DataFrame]:
        """
        Get funding rate data for a token.

        Priority:
        1. Stitched funding data
        2. Combined funding files

        Args:
            symbol: Token symbol (e.g., 'BTC', 'ETH')

        Returns:
            DataFrame with daily funding rates, or None if not found
        """
        symbol = symbol.upper()

        # Check cache
        cache_key = f"funding_{symbol}"
        if cache_key in self._token_cache:
            return self._token_cache[cache_key]

        # Try stitched first
        df = self._load_stitched_funding(symbol)
        if df is not None:
            self._token_cache[cache_key] = df
            return df

        # Fall back to combined
        df = self._load_funding_combined(symbol)
        if df is not None:
            self._token_cache[cache_key] = df
            return df

        return None

    def get_all_price_data(self,
                           min_history_days: int = 0,
                           require_funding: bool = False) -> Dict[str, pd.DataFrame]:
        """
        Get price data for all available tokens.

        Args:
            min_history_days: Minimum days of history required
            require_funding: If True, only return tokens with funding data

        Returns:
            Dict mapping symbol -> DataFrame
        """
        tokens = self.get_available_tokens()
        result = {}

        funding_tokens = set(tokens['funding']) if require_funding else None

        for symbol in tokens['all']:
            if require_funding and symbol not in funding_tokens:
                continue

            df = self.get_price_data(symbol)
            if df is not None and len(df) >= min_history_days:
                result[symbol] = df

        return result

    def get_data_summary(self) -> pd.DataFrame:
        """
        Get summary of all available data.

        Returns:
            DataFrame with token, sources, start_date, end_date, days, has_funding
        """
        tokens = self.get_available_tokens()

        rows = []
        for symbol in tokens['all']:
            sources = self.get_token_sources(symbol)
            df = self.get_price_data(symbol)
            funding = self.get_funding_data(symbol)

            if df is not None and len(df) > 0:
                rows.append({
                    'symbol': symbol,
                    'sources': ', '.join(sources),
                    'start_date': df.index.min().strftime('%Y-%m-%d'),
                    'end_date': df.index.max().strftime('%Y-%m-%d'),
                    'days': len(df),
                    'has_funding': funding is not None and len(funding) > 0,
                })

        return pd.DataFrame(rows).sort_values('days', ascending=False)


# =============================================================================
# pysystemtrade Integration
# =============================================================================

class CryptoSimData:
    """
    pysystemtrade-compatible data source for crypto backtesting.

    This class provides the interface expected by pysystemtrade's System class.
    """

    def __init__(self, adapter: Optional[CryptoDataAdapter] = None):
        """
        Initialize with optional adapter.

        Args:
            adapter: CryptoDataAdapter instance. If None, creates new one.
        """
        self.adapter = adapter or CryptoDataAdapter()
        self._instrument_list: Optional[List[str]] = None

    def get_instrument_list(self) -> List[str]:
        """Get list of available instruments."""
        if self._instrument_list is None:
            tokens = self.adapter.get_available_tokens()
            self._instrument_list = sorted(tokens['all'])
        return self._instrument_list

    def get_raw_price(self, instrument_code: str) -> pd.Series:
        """Get raw price series for an instrument."""
        df = self.adapter.get_price_data(instrument_code)
        if df is None:
            return pd.Series(dtype=float)
        return df['close']

    def daily_prices(self, instrument_code: str) -> pd.DataFrame:
        """Get daily OHLC prices for an instrument."""
        df = self.adapter.get_price_data(instrument_code)
        if df is None:
            return pd.DataFrame()
        return df[['open', 'high', 'low', 'close']]

    def get_instrument_raw_carry_data(self, instrument_code: str) -> pd.DataFrame:
        """Get carry (funding rate) data for an instrument."""
        funding = self.adapter.get_funding_data(instrument_code)
        if funding is None:
            return pd.DataFrame()

        # Format for pysystemtrade carry calculations
        # Expects columns like 'PRICE', 'CARRY', 'CARRY_CONTRACT'
        price_df = self.adapter.get_price_data(instrument_code)
        if price_df is None:
            return pd.DataFrame()

        result = pd.DataFrame(index=funding.index)
        result['PRICE'] = price_df['close'].reindex(funding.index)
        result['CARRY'] = funding['fundingRate']
        result['CARRY_CONTRACT'] = funding['fundingRate']  # For perps, same as CARRY

        return result.dropna()


# =============================================================================
# Demonstration / CLI
# =============================================================================

if __name__ == "__main__":
    adapter = CryptoDataAdapter()

    print("=" * 70)
    print("CRYPTO DATA ADAPTER - AVAILABLE DATA")
    print("=" * 70)

    tokens = adapter.get_available_tokens()

    print(f"\nTokens by source:")
    print(f"  Stitched:    {len(tokens['stitched'])} tokens")
    print(f"  Kraken:      {len(tokens['kraken'])} tokens")
    print(f"  Coinmetrics: {len(tokens['coinmetrics'])} tokens")
    print(f"  CoinGecko:   {len(tokens['coingecko'])} tokens (cached)")
    print(f"  Funding:     {len(tokens['funding'])} tokens")
    print(f"  TOTAL:       {len(tokens['all'])} unique tokens")

    print("\n" + "=" * 70)
    print("DATA SUMMARY (Top 20 by history length)")
    print("=" * 70)

    summary = adapter.get_data_summary()
    print(f"\n{summary.head(20).to_string()}")

    print("\n" + "=" * 70)
    print("TOKENS WITH FUNDING DATA (for carry strategy)")
    print("=" * 70)

    funding_summary = summary[summary['has_funding']]
    print(f"\n{funding_summary.to_string()}")
