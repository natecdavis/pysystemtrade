#!/usr/bin/env python
"""
Convert downloaded Binance OI metrics data (CSV/ZIP) to unified parquet format.

This script:
1. Reads all downloaded ZIP files from data/binance_oi_raw/{SYMBOL}/
2. Extracts and combines CSV data for each symbol
3. Aggregates 5-minute data to daily (using 23:55 UTC snapshot)
4. Saves as unified parquet file: data/binance_oi_processed.parquet

Data schema:
- date: trading date (YYYY-MM-DD)
- instrument: symbol (e.g., BTCUSDT)
- open_interest: OI in USD notional
- long_short_ratio: all trader long/short ratio (optional)
- toptrader_long_short_ratio: top trader long/short ratio (optional)

Usage:
    # Convert all downloaded data (full rebuild)
    python scripts/convert_oi_to_parquet.py \\
        --input-dir data/binance_oi_raw \\
        --output data/binance_oi_processed.parquet

    # Incremental: only re-read ZIPs whose filename-date is newer than
    # (per-symbol max date in existing parquet) - safety_days. Falls back to
    # full rebuild if the output parquet does not already exist.
    python scripts/convert_oi_to_parquet.py \\
        --input-dir data/binance_oi_raw \\
        --output data/binance_oi_processed.parquet \\
        --incremental

    # Dry run to check coverage
    python scripts/convert_oi_to_parquet.py \\
        --input-dir data/binance_oi_raw \\
        --output data/binance_oi_processed.parquet \\
        --dry-run

Author: Phase 2 OI Data Implementation
Date: 2026-02-21
"""

import argparse
import logging
import re
import sys
import zipfile
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
from tqdm import tqdm

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# Binance Vision daily metrics ZIPs are named {SYMBOL}-metrics-{YYYY-MM-DD}.zip
_ZIP_DATE_RE = re.compile(r"-metrics-(\d{4}-\d{2}-\d{2})\.zip$")


class OIDataConverter:
    """Converts Binance OI metrics CSV/ZIP data to unified parquet format."""

    def __init__(
        self,
        input_dir: str,
        output_path: str,
        incremental: bool = False,
        safety_days: int = 7,
    ):
        """
        Initialize converter.

        Args:
            input_dir: Directory containing downloaded ZIP files (symbol subdirectories)
            output_path: Output parquet file path
            incremental: If True and output_path exists, only read ZIPs whose
                filename-date is newer than (per-symbol max date - safety_days),
                then merge with the existing parquet. Falls back to full rebuild
                if output_path is missing.
            safety_days: Re-read this many days behind each symbol's max date to
                cover late-arriving / corrected ZIPs. 7d is well clear of the
                ~2d trailing-edge window the daily downloader produces.
        """
        self.input_dir = Path(input_dir)
        self.output_path = Path(output_path)
        self.incremental = incremental
        self.safety_days = safety_days

        # Statistics
        self.stats = {
            'symbols_processed': 0,
            'symbols_failed': 0,
            'total_files': 0,
            'total_rows_raw': 0,
            'total_rows_daily': 0,
            'date_range_start': None,
            'date_range_end': None,
            'mode': 'full',
            'zips_skipped_incremental': 0,
            'rows_existing': 0,
            'rows_new': 0,
        }

    def find_symbol_directories(self) -> List[Path]:
        """
        Find all symbol subdirectories in input directory.

        Returns:
            List of symbol directory paths
        """
        if not self.input_dir.exists():
            raise ValueError(f"Input directory does not exist: {self.input_dir}")

        symbol_dirs = [d for d in self.input_dir.iterdir() if d.is_dir()]
        logger.info(f"Found {len(symbol_dirs)} symbol directories")

        return sorted(symbol_dirs)

    @staticmethod
    def _zip_date(zip_path: Path) -> Optional[date]:
        """Parse the YYYY-MM-DD date out of a Binance Vision metrics ZIP filename."""
        match = _ZIP_DATE_RE.search(zip_path.name)
        if match is None:
            return None
        try:
            return date.fromisoformat(match.group(1))
        except ValueError:
            return None

    def _load_existing_max_dates(self) -> Dict[str, pd.Timestamp]:
        """Return {instrument: max_date} from the existing output parquet, or {}."""
        if not self.output_path.exists():
            return {}
        existing = pd.read_parquet(self.output_path, columns=['date', 'instrument'])
        existing['date'] = pd.to_datetime(existing['date']).dt.normalize()
        max_dates = existing.groupby('instrument')['date'].max()
        return max_dates.to_dict()

    def read_csv_from_zip(self, zip_path: Path) -> Optional[pd.DataFrame]:
        """
        Read CSV from a ZIP file.

        Args:
            zip_path: Path to ZIP file

        Returns:
            DataFrame with CSV contents, or None if failed
        """
        try:
            with zipfile.ZipFile(zip_path, 'r') as zf:
                # Get CSV filename (should be only one file in the ZIP)
                csv_files = [f for f in zf.namelist() if f.endswith('.csv')]

                if len(csv_files) == 0:
                    logger.warning(f"No CSV found in {zip_path.name}")
                    return None

                if len(csv_files) > 1:
                    logger.warning(f"Multiple CSVs in {zip_path.name}, using first: {csv_files[0]}")

                # Read CSV
                with zf.open(csv_files[0]) as f:
                    df = pd.read_csv(f)

                return df

        except Exception as e:
            logger.warning(f"Failed to read {zip_path.name}: {e}")
            return None

    def _process_zips(self, symbol: str, zip_files: List[Path]) -> Optional[pd.DataFrame]:
        """
        Aggregate a list of ZIPs for a single symbol into daily rows.

        Shared by both the full-rebuild path (all ZIPs) and the incremental
        path (filtered ZIPs).
        """
        if len(zip_files) == 0:
            return None

        all_dfs = []
        for zip_path in zip_files:
            df = self.read_csv_from_zip(zip_path)
            if df is not None:
                all_dfs.append(df)
                self.stats['total_files'] += 1

        if len(all_dfs) == 0:
            logger.warning(f"No valid data for {symbol}")
            return None

        # Combine all CSVs
        combined = pd.concat(all_dfs, ignore_index=True)
        self.stats['total_rows_raw'] += len(combined)

        logger.debug(f"{symbol}: {len(combined):,} 5-min rows from {len(zip_files)} files")

        # Parse timestamp (some symbols have mixed formats, e.g. ICPUSDT, TLMUSDT)
        combined['create_time'] = pd.to_datetime(combined['create_time'], infer_datetime_format=True)

        # Extract date
        combined['date'] = combined['create_time'].dt.date

        # Select end-of-day snapshot (23:55 UTC)
        # This gives us a consistent daily value close to market close
        combined['time'] = combined['create_time'].dt.time

        # For each date, take the last available timestamp (closest to 23:55)
        daily = (
            combined
            .sort_values('create_time')
            .groupby('date')
            .last()
            .reset_index()
        )

        # Select and rename columns
        result = pd.DataFrame({
            'date': pd.to_datetime(daily['date']),
            'instrument': symbol,
            'open_interest': daily['sum_open_interest_value'],  # OI in USD
            'long_short_ratio': daily['sum_taker_long_short_vol_ratio'],  # Taker LS ratio
            'toptrader_long_short_ratio': daily['sum_toptrader_long_short_ratio']  # Top trader LS ratio
        })

        self.stats['total_rows_daily'] += len(result)

        logger.debug(f"{symbol}: {len(result)} daily rows")

        return result

    def process_symbol_data(
        self,
        symbol_dir: Path,
        since: Optional[date] = None,
    ) -> Optional[pd.DataFrame]:
        """
        Process ZIP files for a single symbol.

        Args:
            symbol_dir: Path to symbol directory
            since: If provided, only ZIPs whose filename-date is strictly greater
                than `since` are read (incremental mode). When None, every ZIP is
                read (full rebuild).

        Returns:
            Daily aggregated DataFrame, or None if no eligible ZIPs / no valid data.
        """
        symbol = symbol_dir.name
        logger.debug(f"Processing {symbol}")

        all_zips = sorted(symbol_dir.glob("*-metrics-*.zip"))

        if len(all_zips) == 0:
            logger.warning(f"No ZIP files found for {symbol}")
            return None

        if since is None:
            zip_files = all_zips
        else:
            zip_files = []
            for zip_path in all_zips:
                zdate = self._zip_date(zip_path)
                if zdate is None:
                    # Unparseable filename — be safe and read it.
                    zip_files.append(zip_path)
                    continue
                if zdate > since:
                    zip_files.append(zip_path)
                else:
                    self.stats['zips_skipped_incremental'] += 1
            if len(zip_files) == 0:
                logger.debug(f"{symbol}: no new ZIPs since {since}; skipping")
                return None

        return self._process_zips(symbol, zip_files)

    def convert_all_symbols(
        self,
        symbol_dirs: List[Path],
        max_dates: Optional[Dict[str, pd.Timestamp]] = None,
    ) -> pd.DataFrame:
        """
        Convert all symbol data to unified format.

        Args:
            symbol_dirs: List of symbol directory paths
            max_dates: Per-symbol max date in the existing parquet. When provided,
                each symbol is processed incrementally (read only ZIPs whose
                filename-date > max_date - safety_days). When None, every ZIP is
                read for every symbol.

        Returns:
            Combined DataFrame with the rows for which new ZIPs were processed.
            May be empty (zero rows) in incremental mode when nothing is new —
            callers must handle that case.
        """
        all_data = []

        for symbol_dir in tqdm(symbol_dirs, desc="Converting symbols"):
            symbol = symbol_dir.name
            try:
                if max_dates is None:
                    since = None
                else:
                    sym_max = max_dates.get(symbol)
                    if sym_max is None:
                        since = None  # new symbol — read everything
                    else:
                        since = (
                            pd.Timestamp(sym_max).normalize().date()
                            - timedelta(days=self.safety_days)
                        )

                symbol_data = self.process_symbol_data(symbol_dir, since=since)

                if symbol_data is not None:
                    all_data.append(symbol_data)
                    self.stats['symbols_processed'] += 1
                else:
                    self.stats['symbols_failed'] += 1

            except Exception as e:
                logger.error(f"Error processing {symbol_dir.name}: {e}")
                self.stats['symbols_failed'] += 1

        if len(all_data) == 0:
            if max_dates is None:
                raise ValueError("No valid data found in any symbol directory")
            # Incremental mode with no new ZIPs anywhere is a normal no-op.
            return pd.DataFrame(
                columns=['date', 'instrument', 'open_interest',
                         'long_short_ratio', 'toptrader_long_short_ratio']
            )

        # Combine all symbols
        logger.info("Combining all symbol data...")
        combined = pd.concat(all_data, ignore_index=True)

        # Sort by date and instrument
        combined = combined.sort_values(['date', 'instrument']).reset_index(drop=True)

        # Update date range stats
        self.stats['date_range_start'] = combined['date'].min()
        self.stats['date_range_end'] = combined['date'].max()

        return combined

    def _merge_with_existing(self, new_df: pd.DataFrame) -> pd.DataFrame:
        """Concat new rows with the existing parquet and dedup on (date, instrument).

        Newer rows win on overlap (`keep='last'`), so a re-read ZIP that produces
        a corrected value supersedes whatever was in the previous parquet. Mirrors
        the pattern in `scripts/backfill_volume.py`.
        """
        existing = pd.read_parquet(self.output_path)
        existing['date'] = pd.to_datetime(existing['date']).dt.normalize()
        self.stats['rows_existing'] = len(existing)
        self.stats['rows_new'] = len(new_df)

        if new_df.empty:
            # Normalize sort order so callers see a consistent shape.
            return (
                existing
                .sort_values(['date', 'instrument'])
                .reset_index(drop=True)
            )

        new_df = new_df.copy()
        new_df['date'] = pd.to_datetime(new_df['date']).dt.normalize()

        combined = pd.concat([existing, new_df], ignore_index=True)
        combined = (
            combined
            .drop_duplicates(subset=['date', 'instrument'], keep='last')
            .sort_values(['date', 'instrument'])
            .reset_index(drop=True)
        )

        self.stats['date_range_start'] = combined['date'].min()
        self.stats['date_range_end'] = combined['date'].max()
        return combined

    def save_parquet(self, df: pd.DataFrame) -> None:
        """
        Save DataFrame to parquet format.

        Args:
            df: DataFrame to save
        """
        self.output_path.parent.mkdir(parents=True, exist_ok=True)

        logger.info(f"Saving to {self.output_path}...")
        df.to_parquet(
            self.output_path,
            engine='pyarrow',
            compression='snappy',
            index=False
        )

        file_size_mb = self.output_path.stat().st_size / (1024 ** 2)
        logger.info(f"Saved {len(df):,} rows ({file_size_mb:.2f} MB)")

    def print_summary(self) -> None:
        """Print conversion statistics."""
        logger.info("=" * 60)
        logger.info("CONVERSION SUMMARY")
        logger.info("=" * 60)
        logger.info(f"Mode: {self.stats['mode']}")
        logger.info(f"Symbols processed: {self.stats['symbols_processed']}")
        logger.info(f"Symbols failed: {self.stats['symbols_failed']}")
        logger.info(f"Total ZIP files: {self.stats['total_files']:,}")
        if self.stats['mode'] == 'incremental':
            logger.info(f"ZIPs skipped (already covered): {self.stats['zips_skipped_incremental']:,}")
            logger.info(f"Existing rows: {self.stats['rows_existing']:,}")
            logger.info(f"New / re-read rows: {self.stats['rows_new']:,}")
        logger.info(f"Total 5-min rows: {self.stats['total_rows_raw']:,}")
        logger.info(f"Total daily rows: {self.stats['total_rows_daily']:,}")
        logger.info(f"Date range: {self.stats['date_range_start']} to {self.stats['date_range_end']}")
        logger.info(f"Output: {self.output_path}")
        logger.info("=" * 60)

    def run(self, dry_run: bool = False) -> Optional[pd.DataFrame]:
        """
        Run the full conversion process.

        Args:
            dry_run: If True, only analyze coverage without saving

        Returns:
            Combined DataFrame if successful
        """
        # Find symbol directories
        symbol_dirs = self.find_symbol_directories()

        if len(symbol_dirs) == 0:
            logger.error("No symbol directories found")
            return None

        if self.incremental and not self.output_path.exists():
            logger.warning(
                f"--incremental requested but {self.output_path} does not exist — "
                "falling back to full rebuild"
            )

        do_incremental = self.incremental and self.output_path.exists()

        if do_incremental:
            self.stats['mode'] = 'incremental'
            max_dates = self._load_existing_max_dates()
            logger.info(
                f"Incremental mode: existing parquet has {len(max_dates)} symbols; "
                f"safety_days={self.safety_days}"
            )
            new_df = self.convert_all_symbols(symbol_dirs, max_dates=max_dates)
            combined = self._merge_with_existing(new_df)
        else:
            self.stats['mode'] = 'full'
            combined = self.convert_all_symbols(symbol_dirs, max_dates=None)

        # Save (unless dry run)
        if not dry_run:
            self.save_parquet(combined)
        else:
            logger.info("DRY RUN - not saving output file")

        # Print summary
        self.print_summary()

        return combined


def analyze_coverage(df: pd.DataFrame) -> None:
    """
    Analyze data coverage and quality.

    Args:
        df: Combined DataFrame
    """
    logger.info("=" * 60)
    logger.info("COVERAGE ANALYSIS")
    logger.info("=" * 60)

    # Overall stats
    unique_symbols = df['instrument'].nunique()
    date_range = (df['date'].max() - df['date'].min()).days + 1
    total_possible_rows = unique_symbols * date_range
    actual_rows = len(df)
    coverage_pct = (actual_rows / total_possible_rows) * 100

    logger.info(f"Unique instruments: {unique_symbols}")
    logger.info(f"Date range: {df['date'].min()} to {df['date'].max()} ({date_range} days)")
    logger.info(f"Coverage: {actual_rows:,} / {total_possible_rows:,} ({coverage_pct:.1f}%)")

    # Per-symbol stats
    symbol_counts = df.groupby('instrument')['date'].count().sort_values(ascending=False)
    logger.info(f"\nTop 10 symbols by coverage:")
    for symbol, count in symbol_counts.head(10).items():
        pct = (count / date_range) * 100
        logger.info(f"  {symbol}: {count} days ({pct:.1f}%)")

    logger.info(f"\nBottom 10 symbols by coverage:")
    for symbol, count in symbol_counts.tail(10).items():
        pct = (count / date_range) * 100
        logger.info(f"  {symbol}: {count} days ({pct:.1f}%)")

    # Data quality checks
    logger.info("\nData quality:")
    null_oi = df['open_interest'].isna().sum()
    zero_oi = (df['open_interest'] == 0).sum()
    logger.info(f"  Null OI values: {null_oi} ({null_oi/len(df)*100:.2f}%)")
    logger.info(f"  Zero OI values: {zero_oi} ({zero_oi/len(df)*100:.2f}%)")

    logger.info("=" * 60)


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description='Convert Binance OI metrics data to parquet format',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Convert all downloaded data
  python scripts/convert_oi_to_parquet.py \\
    --input-dir data/binance_oi_raw \\
    --output data/binance_oi_processed.parquet

  # Incremental update (reuses existing parquet)
  python scripts/convert_oi_to_parquet.py \\
    --input-dir data/binance_oi_raw \\
    --output data/binance_oi_processed.parquet \\
    --incremental

  # Dry run with coverage analysis
  python scripts/convert_oi_to_parquet.py \\
    --input-dir data/binance_oi_raw \\
    --output data/binance_oi_processed.parquet \\
    --dry-run \\
    --analyze

  # Verbose output
  python scripts/convert_oi_to_parquet.py \\
    --input-dir data/binance_oi_raw \\
    --output data/binance_oi_processed.parquet \\
    --verbose
        """
    )

    parser.add_argument(
        '--input-dir',
        type=str,
        required=True,
        help='Input directory with downloaded ZIP files (symbol subdirectories)'
    )

    parser.add_argument(
        '--output',
        type=str,
        required=True,
        help='Output parquet file path'
    )

    parser.add_argument(
        '--incremental',
        action='store_true',
        help=(
            'Update tail only: read just ZIPs newer than '
            '(per-symbol max date in existing parquet) - safety-days, then '
            'merge into the existing parquet. Falls back to full rebuild if '
            'the output parquet does not yet exist.'
        ),
    )

    parser.add_argument(
        '--safety-days',
        type=int,
        default=7,
        help=(
            'Incremental mode: re-read this many days behind each symbol\'s '
            'max date to cover late-arriving / corrected ZIPs (default: 7).'
        ),
    )

    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Analyze coverage without saving output file'
    )

    parser.add_argument(
        '--analyze',
        action='store_true',
        help='Run detailed coverage analysis'
    )

    parser.add_argument(
        '--verbose',
        action='store_true',
        help='Enable verbose logging'
    )

    args = parser.parse_args()

    # Set logging level
    if args.verbose:
        logger.setLevel(logging.DEBUG)

    # Run conversion
    converter = OIDataConverter(
        args.input_dir,
        args.output,
        incremental=args.incremental,
        safety_days=args.safety_days,
    )
    df = converter.run(dry_run=args.dry_run)

    # Analyze coverage if requested
    if df is not None and args.analyze:
        analyze_coverage(df)

    logger.info("Conversion complete!")


if __name__ == '__main__':
    main()
