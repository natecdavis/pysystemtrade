"""
CSV reader for spot crypto prices.

Reads OHLCV CSV files with the format:
    date,open,high,low,close,volume
    2020-01-01,7200.00,7250.00,7150.00,7220.00,1234567

The 'close' column is used as the primary price series.
"""

import os
import pandas as pd
from typing import List

from syscore.constants import arg_not_supplied
from syscore.fileutils import resolve_path_and_filename_for_package
from syslogging.logger import get_logger


# Default column names - case insensitive matching
DATE_COLUMNS = ["date", "datetime", "timestamp", "time"]
CLOSE_COLUMNS = ["close", "price", "adj_close", "adj close"]


class csvSpotPricesData:
    """
    Reads spot crypto prices from CSV files.

    Expects one CSV file per instrument, named {instrument_code}.csv
    Uses the 'close' column (or similar) as the price series.
    """

    def __init__(
        self,
        datapath: str = arg_not_supplied,
        log=get_logger("csvSpotPricesData"),
    ):
        self._log = log

        if datapath is arg_not_supplied:
            raise ValueError("datapath must be provided for csvSpotPricesData")

        self._datapath = datapath

    def __repr__(self):
        return f"csvSpotPricesData accessing {self._datapath}"

    @property
    def datapath(self) -> str:
        return self._datapath

    @property
    def log(self):
        return self._log

    def get_list_of_instruments(self) -> List[str]:
        """
        Returns list of instrument codes by scanning for CSV files.

        Handles both naming conventions:
        - {INSTRUMENT}.csv (legacy)
        - {INSTRUMENT}_price.csv (stitched data)

        Returns:
            List of instrument codes (filenames without .csv extension)
        """
        try:
            resolved_path = resolve_path_and_filename_for_package(self.datapath, "")
        except Exception:
            resolved_path = self.datapath

        if not os.path.exists(resolved_path):
            self.log.warning(f"Data path does not exist: {resolved_path}")
            return []

        files = os.listdir(resolved_path)
        instruments = set()
        for f in files:
            if f.startswith("."):
                continue
            if f.endswith("_price.csv"):
                # Stitched data format: BTC_price.csv -> BTC
                instruments.add(f[:-10])
            elif f.endswith(".csv") and not f.endswith("_funding.csv"):
                # Legacy format: BTC.csv -> BTC
                instruments.add(f[:-4])
        return sorted(instruments)

    def get_spot_prices(self, instrument_code: str) -> pd.Series:
        """
        Get spot prices for an instrument.

        Args:
            instrument_code: The instrument code (e.g., 'BTC', 'ETH')

        Returns:
            pd.Series with datetime index and close prices
        """
        filename = self._filename_for_instrument(instrument_code)

        try:
            df = self._read_csv_file(filename)
        except FileNotFoundError:
            self.log.warning(f"Price file not found: {filename}")
            return pd.Series(dtype=float)
        except Exception as e:
            self.log.warning(f"Error reading {filename}: {e}")
            return pd.Series(dtype=float)

        # Extract close price
        close_col = self._find_column(df, CLOSE_COLUMNS)
        if close_col is None:
            self.log.warning(
                f"No close/price column found in {filename}. "
                f"Available columns: {list(df.columns)}"
            )
            return pd.Series(dtype=float)

        prices = df[close_col].astype(float)
        prices.name = instrument_code

        # Remove duplicates, keeping last value for each timestamp
        prices = prices[~prices.index.duplicated(keep="last")]

        # Sort by index
        prices = prices.sort_index()

        return prices

    def _read_csv_file(self, filename: str) -> pd.DataFrame:
        """
        Read a CSV file and return DataFrame with datetime index.
        """
        # Try to resolve package path, fall back to direct path
        try:
            resolved = resolve_path_and_filename_for_package(
                self.datapath, os.path.basename(filename)
            )
        except Exception:
            resolved = filename

        # Read CSV
        df = pd.read_csv(resolved)

        # Find and set date index
        date_col = self._find_column(df, DATE_COLUMNS)
        if date_col is None:
            # Try using first column as date
            date_col = df.columns[0]

        df[date_col] = pd.to_datetime(df[date_col])
        df = df.set_index(date_col)
        df.index.name = "datetime"

        return df

    def _find_column(self, df: pd.DataFrame, candidates: List[str]) -> str:
        """
        Find a column name from a list of candidates (case-insensitive).
        """
        df_cols_lower = {col.lower(): col for col in df.columns}
        for candidate in candidates:
            if candidate.lower() in df_cols_lower:
                return df_cols_lower[candidate.lower()]
        return None

    def _filename_for_instrument(self, instrument_code: str) -> str:
        """
        Get the filename for an instrument.

        Tries both naming conventions:
        - {INSTRUMENT}_price.csv (stitched data, preferred)
        - {INSTRUMENT}.csv (legacy)
        """
        # Try stitched format first
        stitched_path = os.path.join(self.datapath, f"{instrument_code}_price.csv")
        if os.path.exists(stitched_path):
            return stitched_path

        # Try resolved stitched path
        try:
            resolved_stitched = resolve_path_and_filename_for_package(
                self.datapath, f"{instrument_code}_price.csv"
            )
            if os.path.exists(resolved_stitched):
                return resolved_stitched
        except Exception:
            pass

        # Fall back to legacy format
        return os.path.join(self.datapath, f"{instrument_code}.csv")

    def has_data_for_instrument(self, instrument_code: str) -> bool:
        """
        Check if data exists for an instrument.

        Checks both naming conventions.
        """
        # Check stitched format
        try:
            resolved = resolve_path_and_filename_for_package(
                self.datapath, f"{instrument_code}_price.csv"
            )
            if os.path.exists(resolved):
                return True
        except Exception:
            stitched_path = os.path.join(self.datapath, f"{instrument_code}_price.csv")
            if os.path.exists(stitched_path):
                return True

        # Check legacy format
        try:
            resolved = resolve_path_and_filename_for_package(
                self.datapath, f"{instrument_code}.csv"
            )
            return os.path.exists(resolved)
        except Exception:
            legacy_path = os.path.join(self.datapath, f"{instrument_code}.csv")
            return os.path.exists(legacy_path)

    def get_spot_volume(self, instrument_code: str) -> pd.Series:
        """
        Get volume data for an instrument.

        Args:
            instrument_code: The instrument code (e.g., 'BTC', 'ETH')

        Returns:
            pd.Series with datetime index and volume values
        """
        filename = self._filename_for_instrument(instrument_code)

        try:
            df = self._read_csv_file(filename)
        except FileNotFoundError:
            self.log.warning(f"Price file not found: {filename}")
            return pd.Series(dtype=float)
        except Exception as e:
            self.log.warning(f"Error reading {filename}: {e}")
            return pd.Series(dtype=float)

        # Find volume column
        volume_col = self._find_column(df, ["volume", "vol"])
        if volume_col is None:
            self.log.warning(
                f"No volume column found in {filename}. "
                f"Available columns: {list(df.columns)}"
            )
            return pd.Series(dtype=float)

        volume = df[volume_col].astype(float)
        volume.name = instrument_code

        # Remove duplicates, keeping last value for each timestamp
        volume = volume[~volume.index.duplicated(keep="last")]

        # Sort by index
        volume = volume.sort_index()

        return volume

    def get_ohlcv(self, instrument_code: str) -> pd.DataFrame:
        """
        Get full OHLCV data for an instrument.

        Args:
            instrument_code: The instrument code (e.g., 'BTC', 'ETH')

        Returns:
            pd.DataFrame with datetime index and open, high, low, close, volume columns
        """
        filename = self._filename_for_instrument(instrument_code)

        try:
            df = self._read_csv_file(filename)
        except FileNotFoundError:
            self.log.warning(f"Price file not found: {filename}")
            return pd.DataFrame()
        except Exception as e:
            self.log.warning(f"Error reading {filename}: {e}")
            return pd.DataFrame()

        # Remove duplicates, keeping last value for each timestamp
        df = df[~df.index.duplicated(keep="last")]

        # Sort by index
        df = df.sort_index()

        return df
