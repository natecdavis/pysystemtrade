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
    # Convert all downloaded data
    python scripts/convert_oi_to_parquet.py \\
        --input-dir data/binance_oi_raw \\
        --output data/binance_oi_processed.parquet

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
import sys
import zipfile
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
from tqdm import tqdm

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class OIDataConverter:
    """Converts Binance OI metrics CSV/ZIP data to unified parquet format."""

    def __init__(self, input_dir: str, output_path: str):
        """
        Initialize converter.

        Args:
            input_dir: Directory containing downloaded ZIP files (symbol subdirectories)
            output_path: Output parquet file path
        """
        self.input_dir = Path(input_dir)
        self.output_path = Path(output_path)

        # Statistics
        self.stats = {
            'symbols_processed': 0,
            'symbols_failed': 0,
            'total_files': 0,
            'total_rows_raw': 0,
            'total_rows_daily': 0,
            'date_range_start': None,
            'date_range_end': None
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

    def process_symbol_data(self, symbol_dir: Path) -> Optional[pd.DataFrame]:
        """
        Process all ZIP files for a single symbol.

        Args:
            symbol_dir: Path to symbol directory

        Returns:
            Daily aggregated DataFrame, or None if failed
        """
        symbol = symbol_dir.name
        logger.debug(f"Processing {symbol}")

        # Find all ZIP files
        zip_files = sorted(symbol_dir.glob("*-metrics-*.zip"))

        if len(zip_files) == 0:
            logger.warning(f"No ZIP files found for {symbol}")
            return None

        # Read all CSVs and combine
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
        combined['create_time'] = pd.to_datetime(combined['create_time'], format='mixed')

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

    def convert_all_symbols(self, symbol_dirs: List[Path]) -> pd.DataFrame:
        """
        Convert all symbol data to unified format.

        Args:
            symbol_dirs: List of symbol directory paths

        Returns:
            Combined DataFrame with all symbols
        """
        all_data = []

        for symbol_dir in tqdm(symbol_dirs, desc="Converting symbols"):
            try:
                symbol_data = self.process_symbol_data(symbol_dir)

                if symbol_data is not None:
                    all_data.append(symbol_data)
                    self.stats['symbols_processed'] += 1
                else:
                    self.stats['symbols_failed'] += 1

            except Exception as e:
                logger.error(f"Error processing {symbol_dir.name}: {e}")
                self.stats['symbols_failed'] += 1

        if len(all_data) == 0:
            raise ValueError("No valid data found in any symbol directory")

        # Combine all symbols
        logger.info("Combining all symbol data...")
        combined = pd.concat(all_data, ignore_index=True)

        # Sort by date and instrument
        combined = combined.sort_values(['date', 'instrument']).reset_index(drop=True)

        # Update date range stats
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
        logger.info(f"Symbols processed: {self.stats['symbols_processed']}")
        logger.info(f"Symbols failed: {self.stats['symbols_failed']}")
        logger.info(f"Total ZIP files: {self.stats['total_files']:,}")
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

        # Convert all data
        combined = self.convert_all_symbols(symbol_dirs)

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
    converter = OIDataConverter(args.input_dir, args.output)
    df = converter.run(dry_run=args.dry_run)

    # Analyze coverage if requested
    if df is not None and args.analyze:
        analyze_coverage(df)

    logger.info("Conversion complete!")


if __name__ == '__main__':
    main()
