#!/usr/bin/env python3
"""
Check download completeness before building parquet datasets

Performs:
1. Count files per symbol/year (flag suspicious gaps)
2. Random integrity spot-check on ~5 files
"""

import sys
from pathlib import Path
from collections import defaultdict
import random

# Project root
PROJECT_ROOT = Path(__file__).parent.parent
DATA_DIR = PROJECT_ROOT / "data" / "raw" / "binance"

# Expected symbols and their launch years
SYMBOL_YEARS = {
    # Core 7 (2019-09 launch)
    'BTCUSDT': list(range(2019, 2026)),
    'ETHUSDT': list(range(2019, 2026)),
    'BNBUSDT': list(range(2019, 2026)),
    'XRPUSDT': list(range(2019, 2026)),
    'LTCUSDT': list(range(2019, 2026)),
    'EOSUSDT': list(range(2019, 2026)),
    'BCHUSDT': list(range(2019, 2026)),

    # 2020 launches
    'LINKUSDT': list(range(2020, 2026)),
    'SOLUSDT': list(range(2020, 2026)),
    'DOTUSDT': list(range(2020, 2026)),
    'ADAUSDT': list(range(2020, 2026)),

    # 2021 launches
    'UNIUSDT': list(range(2021, 2026)),
    'MATICUSDT': list(range(2021, 2026)),
    'DOGEUSDT': list(range(2021, 2026)),
    'AVAXUSDT': list(range(2021, 2026)),
}

# Partial years (2019 started Sep, 2025 partial)
PARTIAL_YEARS = {
    2019: 4,  # Sep-Dec = 4 months
    2025: 1,  # Jan only (current month as of 2025-01-26)
}


def count_files_per_symbol_year():
    """Count kline and funding files per symbol/year"""
    print("=" * 80)
    print("DOWNLOAD COMPLETENESS CHECK")
    print("=" * 80)
    print()

    klines_dir = DATA_DIR / "klines"
    funding_dir = DATA_DIR / "funding_rates"

    results = defaultdict(lambda: defaultdict(lambda: {'klines': 0, 'funding': 0}))
    issues = []

    # Count klines
    for symbol_dir in klines_dir.iterdir():
        if not symbol_dir.is_dir():
            continue

        symbol = symbol_dir.name
        for zip_file in symbol_dir.glob("*.zip"):
            # Extract year from filename: BTCUSDT-1d-2020-01.zip
            parts = zip_file.stem.split('-')
            if len(parts) >= 4:
                year = int(parts[2])
                results[symbol][year]['klines'] += 1

    # Count funding rates
    for symbol_dir in funding_dir.iterdir():
        if not symbol_dir.is_dir():
            continue

        symbol = symbol_dir.name
        for zip_file in symbol_dir.glob("*.zip"):
            # Extract year from filename: BTCUSDT-fundingRate-2020-01.zip
            parts = zip_file.stem.split('-')
            if len(parts) >= 3:
                year = int(parts[2])
                results[symbol][year]['funding'] += 1

    # Check completeness
    print("File counts per symbol/year:")
    print()
    print(f"{'Symbol':<12} {'Year':<6} {'Klines':<8} {'Funding':<8} {'Status':<20}")
    print("-" * 80)

    for symbol in sorted(SYMBOL_YEARS.keys()):
        expected_years = SYMBOL_YEARS[symbol]

        for year in expected_years:
            klines_count = results[symbol][year]['klines']
            funding_count = results[symbol][year]['funding']

            # Expected count (12 for full years, partial for 2019/2025)
            expected_count = PARTIAL_YEARS.get(year, 12)

            # Check if counts are suspicious
            status = "✓ OK"
            if klines_count < expected_count or funding_count < expected_count:
                status = "⚠ INCOMPLETE"
                issues.append({
                    'symbol': symbol,
                    'year': year,
                    'klines': klines_count,
                    'funding': funding_count,
                    'expected': expected_count
                })

            print(f"{symbol:<12} {year:<6} {klines_count:<8} {funding_count:<8} {status:<20}")

        print()  # Blank line between symbols

    # Summary
    print("=" * 80)
    print(f"SUMMARY: {len(issues)} issue(s) found")
    print("=" * 80)
    print()

    if issues:
        print("Issues requiring attention:")
        print()
        for issue in issues:
            print(f"  {issue['symbol']} {issue['year']}: "
                  f"klines={issue['klines']}/{issue['expected']}, "
                  f"funding={issue['funding']}/{issue['expected']}")
        print()
        print("Suggested fix:")
        print("  # Rerun downloads for missing months:")
        for issue in issues:
            print(f"  python3 scripts/download_binance_data.py --symbols {issue['symbol']} --year {issue['year']}")
        print()
        return False
    else:
        print("✓ All symbols/years have expected file counts!")
        print()
        return True


def spot_check_integrity():
    """Randomly sample and validate ~5 ZIP files"""
    print("=" * 80)
    print("INTEGRITY SPOT-CHECK")
    print("=" * 80)
    print()

    # Collect all ZIP files
    klines_dir = DATA_DIR / "klines"
    all_zips = list(klines_dir.rglob("*.zip"))

    if len(all_zips) == 0:
        print("⚠ No ZIP files found!")
        return False

    # Sample 5 random files (or all if <5)
    sample_size = min(5, len(all_zips))
    sample_files = random.sample(all_zips, sample_size)

    print(f"Checking {sample_size} random ZIP files:")
    print()

    import zipfile

    all_valid = True
    for i, zip_path in enumerate(sample_files, 1):
        print(f"[{i}/{sample_size}] {zip_path.name}")

        try:
            # Check file size
            size_bytes = zip_path.stat().st_size
            if size_bytes == 0:
                print(f"  ✗ Empty file (0 bytes)")
                all_valid = False
                continue

            # Validate ZIP structure
            with zipfile.ZipFile(zip_path) as z:
                # Check ZIP integrity
                bad_file = z.testzip()
                if bad_file is not None:
                    print(f"  ✗ Corrupt ZIP: {bad_file}")
                    all_valid = False
                    continue

                # Check ZIP contains CSV
                namelist = z.namelist()
                csv_files = [f for f in namelist if f.endswith('.csv')]
                if not csv_files:
                    print(f"  ✗ No CSV file found")
                    all_valid = False
                    continue

                print(f"  ✓ Valid ZIP ({size_bytes:,} bytes, {len(csv_files)} CSV file{'s' if len(csv_files) > 1 else ''})")

        except zipfile.BadZipFile:
            print(f"  ✗ Invalid ZIP file format")
            all_valid = False
        except Exception as e:
            print(f"  ✗ Error: {e}")
            all_valid = False

    print()
    print("=" * 80)
    if all_valid:
        print("✓ All spot-checked files are valid!")
        print()
        return True
    else:
        print("✗ Some files failed validation - consider redownloading")
        print()
        return False


def main():
    print()

    # Step 1: Completeness check
    completeness_ok = count_files_per_symbol_year()

    # Step 2: Integrity spot-check
    integrity_ok = spot_check_integrity()

    # Final verdict
    if completeness_ok and integrity_ok:
        print("=" * 80)
        print("✅ READY TO BUILD PARQUET DATASETS")
        print("=" * 80)
        print()
        print("Next steps:")
        print("  bash scripts/build_all_datasets.sh")
        print()
        sys.exit(0)
    else:
        print("=" * 80)
        print("❌ ISSUES FOUND - Fix before building datasets")
        print("=" * 80)
        print()
        sys.exit(1)


if __name__ == '__main__':
    main()
