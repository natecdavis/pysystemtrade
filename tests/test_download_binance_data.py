"""
Unit tests for Binance data downloader script

These tests verify that:
1. BASE_URL structure matches the filename format (prevents regressions)
2. URL construction functions produce correct, well-formed URLs
3. URLs contain /monthly/ since we use YYYY-MM filename format
"""

import sys
from pathlib import Path

# Add scripts directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / 'scripts'))

from download_binance_data import BASE_URL, build_kline_url, build_funding_url


def test_base_url_structure_matches_filename_format():
    """
    Verify BASE_URL contains /monthly since we use YYYY-MM filenames

    Rationale: This script uses --year and --months CLI args, producing
    YYYY-MM formatted filenames. The BASE_URL must point to the /monthly/
    endpoint to match. If it points to /daily/, we'll get 404s.
    """
    assert '/monthly' in BASE_URL, (
        f"BASE_URL must contain '/monthly' for YYYY-MM filename format. "
        f"Got: {BASE_URL}"
    )


def test_kline_url_construction():
    """Verify kline URLs are correctly formatted"""
    url = build_kline_url("BTCUSDT", 2023, 1)

    # Check full URL structure
    assert url == "https://data.binance.vision/data/futures/um/monthly/klines/BTCUSDT/1d/BTCUSDT-1d-2023-01.zip", (
        f"Unexpected kline URL format: {url}"
    )

    # Check critical components
    assert '/monthly' in url, "Kline URL must use /monthly endpoint"
    assert '2023-01' in url, "Kline URL must use YYYY-MM format"
    assert url.endswith('.zip'), "Kline URL must end with .zip"


def test_kline_url_month_padding():
    """Verify month numbers are zero-padded"""
    url = build_kline_url("ETHUSDT", 2023, 6)
    assert '2023-06' in url, f"Month should be zero-padded: {url}"

    url_jan = build_kline_url("ETHUSDT", 2023, 1)
    assert '2023-01' in url_jan, f"Single-digit month should be zero-padded: {url_jan}"


def test_funding_url_construction():
    """Verify funding URLs are correctly formatted"""
    url = build_funding_url("ETHUSDT", 2023, 6)

    # Check full URL structure
    assert url == "https://data.binance.vision/data/futures/um/monthly/fundingRate/ETHUSDT/ETHUSDT-fundingRate-2023-06.zip", (
        f"Unexpected funding URL format: {url}"
    )

    # Check critical components
    assert '/monthly' in url, "Funding URL must use /monthly endpoint"
    assert '2023-06' in url, "Funding URL must use YYYY-MM format"
    assert url.endswith('.zip'), "Funding URL must end with .zip"


def test_funding_url_month_padding():
    """Verify month numbers are zero-padded"""
    url = build_funding_url("BTCUSDT", 2023, 3)
    assert '2023-03' in url, f"Month should be zero-padded: {url}"


def test_urls_use_same_base():
    """Verify both URL types use the same BASE_URL"""
    kline = build_kline_url("BTCUSDT", 2023, 1)
    funding = build_funding_url("BTCUSDT", 2023, 1)

    assert kline.startswith(BASE_URL), f"Kline URL should start with BASE_URL: {kline}"
    assert funding.startswith(BASE_URL), f"Funding URL should start with BASE_URL: {funding}"
