"""
Data manifest generation and verification for reproducibility.

Generates lightweight manifest for dataset builds with:
- Full SHA256 hashes for API cache (recent 2-7 days)
- Lightweight fingerprints for Vision ZIPs (filename + size + mtime)
- Dataset-level fingerprint for quick comparison
"""

import hashlib
import json
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import logging

logger = logging.getLogger(__name__)


def compute_file_sha256(file_path: Path) -> str:
    """
    Compute SHA256 hash of a file.

    Args:
        file_path: Path to file

    Returns:
        Hex digest of SHA256 hash
    """
    sha256_hash = hashlib.sha256()
    with open(file_path, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            sha256_hash.update(chunk)
    return sha256_hash.hexdigest()


def get_file_fingerprint(file_path: Path) -> Dict:
    """
    Get lightweight fingerprint for a file (no hashing).

    Args:
        file_path: Path to file

    Returns:
        Dict with filename, size, and mtime
    """
    stat = file_path.stat()
    return {
        'filename': file_path.name,
        'size': stat.st_size,
        'mtime': datetime.fromtimestamp(stat.st_mtime).isoformat()
    }


def extract_date_range_from_filename(filename: str) -> Optional[Tuple[str, str]]:
    """
    Extract date range from filename.

    Patterns:
    - Monthly: BTCUSDT-1d-2025-12.zip → ('2025-12-01', '2025-12-31')
    - Daily: BTCUSDT-1d-2026-01-15.zip → ('2026-01-15', '2026-01-15')
    - API cache: 2026-01-15_klines.parquet → ('2026-01-15', '2026-01-15')

    Args:
        filename: Filename to parse

    Returns:
        Tuple of (start_date, end_date) as ISO strings, or None if cannot parse
    """
    # Monthly ZIP: BTCUSDT-1d-2025-12.zip
    if '-' in filename and '.zip' in filename:
        parts = filename.replace('.zip', '').split('-')
        if len(parts) >= 3:
            # Check if last part is DD (daily)
            if len(parts[-1]) == 2 and parts[-1].isdigit():
                # Could be daily (YYYY-MM-DD) or monthly (YYYY-MM)
                if len(parts[-2]) == 2 and parts[-2].isdigit():
                    # Daily: YYYY-MM-DD
                    if len(parts[-3]) == 4 and parts[-3].isdigit():
                        try:
                            year = int(parts[-3])
                            month = int(parts[-2])
                            day = int(parts[-1])
                            date_str = f"{year:04d}-{month:02d}-{day:02d}"
                            return (date_str, date_str)
                        except ValueError:
                            pass

            # Monthly: YYYY-MM
            year, month = parts[-2], parts[-1]
            if year.isdigit() and month.isdigit() and len(year) == 4 and len(month) == 2:
                try:
                    y, m = int(year), int(month)
                    # Compute last day of month
                    if m == 12:
                        last_day = 31
                    else:
                        last_day = (date(y, m + 1, 1) - timedelta(days=1)).day
                    return (f"{y:04d}-{m:02d}-01", f"{y:04d}-{m:02d}-{last_day:02d}")
                except ValueError:
                    pass

    # API cache: 2026-01-15_klines.parquet
    if '_' in filename and '.parquet' in filename:
        parts = filename.split('_')
        for part in parts:
            if len(part) == 10 and part[4] == '-' and part[7] == '-':
                # YYYY-MM-DD pattern
                return (part, part)

    # API cache range: BTCUSDT_2026-01-15_2026-01-17_klines.parquet
    if '_' in filename:
        parts = filename.split('_')
        dates = [p for p in parts if len(p) == 10 and p[4] == '-' and p[7] == '-']
        if len(dates) >= 2:
            return (min(dates), max(dates))
        elif len(dates) == 1:
            return (dates[0], dates[0])

    return None


def generate_data_manifest(
    data_dir: Path,
    instruments: List[str],
    symbol_map: Dict[str, str],
    as_of_date: date,
    include_api_cache: bool = True,
    env_root: Optional[Path] = None
) -> Dict:
    """
    Generate manifest of all raw files used in dataset build.

    For API cache: Full SHA256 hashes (recent tail, small files)
    For Vision ZIPs: Lightweight fingerprints (large historical files)

    Args:
        data_dir: Root data directory (e.g., data/raw/binance)
        instruments: List of internal instrument IDs (e.g., BTCUSDT_PERP)
        symbol_map: Mapping from internal ID to Binance symbol
        as_of_date: Dataset as_of_date
        include_api_cache: If True, include API cache files
        env_root: Environment root for portable path resolution (default: data_dir.parent.parent)

    Returns:
        Manifest dict with sources for each instrument
    """
    # Determine env_root with backward compatible fallback
    if env_root is None:
        # Backward compatible: data/raw/binance -> project_root
        env_root = data_dir.parent.parent

    manifest = {
        'as_of_date': str(as_of_date),
        'generated_at': datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
        'include_api_cache': include_api_cache,
        'env_root_hint': str(env_root),  # NEW: for debugging path resolution
        'sources': {}
    }

    for instrument in instruments:
        binance_symbol = symbol_map[instrument]

        # Vision monthly ZIPs (lightweight fingerprints)
        vision_monthly = []
        klines_dir = data_dir / 'klines' / binance_symbol
        if klines_dir.exists():
            monthly_zips = list(klines_dir.glob(f'{binance_symbol}-*-????-??.zip'))
            for zip_file in sorted(monthly_zips):
                fingerprint = get_file_fingerprint(zip_file)
                date_range = extract_date_range_from_filename(zip_file.name)
                vision_monthly.append({
                    'file': str(zip_file.relative_to(env_root)),
                    'fingerprint': fingerprint,
                    'date_range': date_range
                })

        # Vision daily ZIPs (if any)
        vision_daily = []
        if klines_dir.exists():
            daily_zips = list(klines_dir.glob(f'{binance_symbol}-*-????-??-??.zip'))
            for zip_file in sorted(daily_zips):
                fingerprint = get_file_fingerprint(zip_file)
                date_range = extract_date_range_from_filename(zip_file.name)
                vision_daily.append({
                    'file': str(zip_file.relative_to(env_root)),
                    'fingerprint': fingerprint,
                    'date_range': date_range
                })

        # API cache (full SHA256 hashes)
        api_cache = []
        if include_api_cache:
            api_cache_dir = data_dir / 'api_cache' / binance_symbol
            if api_cache_dir.exists():
                cache_files = list(api_cache_dir.glob('*_klines.parquet'))
                for cache_file in sorted(cache_files):
                    sha256 = compute_file_sha256(cache_file)
                    date_range = extract_date_range_from_filename(cache_file.name)
                    file_stat = cache_file.stat()

                    api_cache.append({
                        'file': str(cache_file.relative_to(env_root)),
                        'sha256': sha256,
                        'fetch_timestamp': datetime.fromtimestamp(file_stat.st_mtime).strftime('%Y-%m-%dT%H:%M:%SZ'),
                        'date_range': date_range,
                        'size': file_stat.st_size
                    })

        manifest['sources'][instrument] = {
            'binance_symbol': binance_symbol,
            'vision_monthly': vision_monthly,
            'vision_daily': vision_daily,
            'api_cache': api_cache
        }

    # Compute dataset fingerprint (hash of sorted source fingerprints)
    # This allows quick comparison without re-hashing all files
    manifest_json = json.dumps(manifest['sources'], sort_keys=True)
    dataset_fingerprint = hashlib.sha256(manifest_json.encode()).hexdigest()
    manifest['dataset_fingerprint'] = dataset_fingerprint

    return manifest


def save_manifest(manifest: Dict, output_path: Path) -> None:
    """
    Save manifest to JSON file.

    Args:
        manifest: Manifest dict
        output_path: Path to save manifest
    """
    with open(output_path, 'w') as f:
        json.dump(manifest, f, indent=2)
    logger.info(f"Manifest saved: {output_path}")


def load_manifest(manifest_path: Path) -> Dict:
    """
    Load manifest from JSON file.

    Args:
        manifest_path: Path to manifest file

    Returns:
        Manifest dict
    """
    with open(manifest_path, 'r') as f:
        return json.load(f)


def verify_manifest(
    manifest: Dict,
    data_dir: Path,
    verify_sha256: bool = True,
    env_root: Optional[Path] = None
) -> Tuple[bool, List[str]]:
    """
    Verify that files in manifest match current state.

    Args:
        manifest: Manifest dict
        data_dir: Root data directory
        verify_sha256: If True, verify SHA256 hashes for API cache (default: True)
                      If False, only check file existence and size
        env_root: Environment root for path resolution (default: use env_root_hint from manifest or data_dir.parent.parent)

    Returns:
        Tuple of (all_valid, errors)
        - all_valid: True if all files match
        - errors: List of error messages
    """
    # Resolve env_root with better fallback logic
    if env_root is None:
        # Try to use env_root_hint from manifest if available
        if 'env_root_hint' in manifest:
            logger.info(f"Using env_root_hint from manifest: {manifest['env_root_hint']}")
            env_root = Path(manifest['env_root_hint'])
        else:
            # Fallback: data/raw/binance -> project_root
            env_root = data_dir.parent.parent
            logger.warning(f"No env_root_hint in manifest, using fallback: {env_root}")

    errors = []

    for instrument, sources in manifest['sources'].items():
        # Check Vision monthly ZIPs (fingerprint only)
        for source in sources['vision_monthly']:
            file_path = env_root / source['file']
            if not file_path.exists():
                errors.append(f"Missing Vision monthly file: {source['file']}")
                continue

            # Check fingerprint
            current_fp = get_file_fingerprint(file_path)
            expected_fp = source['fingerprint']

            if current_fp['size'] != expected_fp['size']:
                errors.append(
                    f"Size mismatch for {source['file']}: "
                    f"expected {expected_fp['size']}, got {current_fp['size']}"
                )

            # Note: mtime can change (e.g., file copied), so we don't enforce strict match
            # Size + filename is sufficient for Vision ZIPs

        # Check Vision daily ZIPs
        for source in sources['vision_daily']:
            file_path = env_root / source['file']
            if not file_path.exists():
                errors.append(f"Missing Vision daily file: {source['file']}")
                continue

            current_fp = get_file_fingerprint(file_path)
            expected_fp = source['fingerprint']

            if current_fp['size'] != expected_fp['size']:
                errors.append(
                    f"Size mismatch for {source['file']}: "
                    f"expected {expected_fp['size']}, got {current_fp['size']}"
                )

        # Check API cache (full SHA256 verification)
        for source in sources['api_cache']:
            file_path = env_root / source['file']
            if not file_path.exists():
                errors.append(f"Missing API cache file: {source['file']}")
                continue

            if verify_sha256:
                current_sha256 = compute_file_sha256(file_path)
                expected_sha256 = source['sha256']

                if current_sha256 != expected_sha256:
                    errors.append(
                        f"SHA256 mismatch for {source['file']}: "
                        f"expected {expected_sha256[:8]}..., got {current_sha256[:8]}..."
                    )
            else:
                # Quick check: size only
                current_size = file_path.stat().st_size
                expected_size = source['size']

                if current_size != expected_size:
                    errors.append(
                        f"Size mismatch for {source['file']}: "
                        f"expected {expected_size}, got {current_size}"
                    )

    all_valid = len(errors) == 0
    return all_valid, errors
