"""
Test suite for Binance symbol length validation

This module tests the normalize_and_validate_symbol() function in download_binance_data.py,
focusing on the relaxed 18-character length ceiling introduced to support newly discovered
perpetuals with longer names (e.g., BROCCOLI714USDT, 1000000BOBUSDT).

Test Coverage:
- Valid symbols within 6-18 character range
- Invalid symbols exceeding 18 characters
- Invalid symbols under 6 characters
- Preserved validation checks (USDT suffix, _PERP rejection)
- Normalization (whitespace stripping, uppercasing)
"""

import pytest
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from scripts.download_binance_data import normalize_and_validate_symbol


class TestValidSymbolsWithin18Chars:
    """Test that symbols ≤18 characters are accepted."""

    def test_standard_symbols_6_to_12_chars(self):
        """Standard Binance symbols (6-12 characters) should be accepted."""
        # Short symbols
        assert normalize_and_validate_symbol('BTCUSDT') == 'BTCUSDT'  # 7 chars
        assert normalize_and_validate_symbol('ETHUSDT') == 'ETHUSDT'  # 7 chars

        # Medium symbols
        assert normalize_and_validate_symbol('1INCHUSDT') == '1INCHUSDT'  # 9 chars
        assert normalize_and_validate_symbol('SOLUSDT') == 'SOLUSDT'  # 7 chars

        # 12-character symbols (previous ceiling)
        assert normalize_and_validate_symbol('AAVEUSDT') == 'AAVEUSDT'

    def test_previously_rejected_symbols_13_to_15_chars(self):
        """Symbols with 13-15 characters should now be accepted (previously rejected)."""
        # 13 characters
        assert normalize_and_validate_symbol('1000FLOKIUSDT') == '1000FLOKIUSDT'
        assert normalize_and_validate_symbol('BANANAS31USDT') == 'BANANAS31USDT'
        assert normalize_and_validate_symbol('VELODROMEUSDT') == 'VELODROMEUSDT'

        # 14 characters
        assert normalize_and_validate_symbol('1000000BOBUSDT') == '1000000BOBUSDT'
        assert normalize_and_validate_symbol('1000000MOGUSDT') == '1000000MOGUSDT'
        assert normalize_and_validate_symbol('1000CHEEMSUSDT') == '1000CHEEMSUSDT'
        assert normalize_and_validate_symbol('1MBABYDOGEUSDT') == '1MBABYDOGEUSDT'
        assert normalize_and_validate_symbol('JELLYJELLYUSDT') == 'JELLYJELLYUSDT'

        # 15 characters (longest known Binance symbols)
        assert normalize_and_validate_symbol('BROCCOLI714USDT') == 'BROCCOLI714USDT'
        assert normalize_and_validate_symbol('BROCCOLIF3BUSDT') == 'BROCCOLIF3BUSDT'

    def test_edge_case_symbols_16_to_18_chars(self):
        """Test symbols at the edge of the 18-character ceiling."""
        # 16 characters (headroom for future listings)
        assert normalize_and_validate_symbol('VERYLONGTKNUSDT') == 'VERYLONGTKNUSDT'

        # 17 characters
        assert normalize_and_validate_symbol('VERYLONGTOKENUSDT') == 'VERYLONGTOKENUSDT'

        # 18 characters (exactly at ceiling)
        assert normalize_and_validate_symbol('VERYLONGTOKENNUSDT') == 'VERYLONGTOKENNUSDT'


class TestRejectSymbolsOver18Chars:
    """Test that symbols >18 characters are rejected."""

    def test_reject_19_chars(self):
        """Symbols with 19 characters should be rejected."""
        with pytest.raises(ValueError, match="length must be 6-18 characters"):
            normalize_and_validate_symbol('VERYLONGTOKENNAUSDT')  # 19 chars

    def test_reject_20_plus_chars(self):
        """Symbols with 20+ characters should be rejected."""
        with pytest.raises(ValueError, match="length must be 6-18 characters"):
            normalize_and_validate_symbol('SUPERLONGTOKENNAMEUSDT')  # 22 chars

        with pytest.raises(ValueError, match="length must be 6-18 characters"):
            normalize_and_validate_symbol('EXTREMELYLONGTOKENUSDT')  # 22 chars


class TestRejectSymbolsUnder6Chars:
    """Test that symbols <6 characters are rejected."""

    def test_reject_5_chars(self):
        """Symbols with 5 characters should be rejected."""
        with pytest.raises(ValueError, match="length must be 6-18 characters"):
            normalize_and_validate_symbol('USDT')  # 4 chars (invalid base)

    def test_reject_3_chars(self):
        """Symbols with 3 characters should be rejected."""
        with pytest.raises(ValueError, match="length must be 6-18 characters"):
            normalize_and_validate_symbol('BTC')  # 3 chars

    def test_reject_empty_string(self):
        """Empty strings should be rejected."""
        with pytest.raises(ValueError, match="length must be 6-18 characters"):
            normalize_and_validate_symbol('')


class TestOtherValidationChecks:
    """Test that other validation checks are still enforced."""

    def test_reject_non_usdt_symbols(self):
        """Symbols not ending with USDT should be rejected."""
        with pytest.raises(ValueError, match="must end with 'USDT'"):
            normalize_and_validate_symbol('BTCUSD')

        with pytest.raises(ValueError, match="must end with 'USDT'"):
            normalize_and_validate_symbol('ETHBUSD')

        with pytest.raises(ValueError, match="must end with 'USDT'"):
            normalize_and_validate_symbol('SOLUSDC')

    def test_reject_perp_suffix(self):
        """Symbols with _PERP suffix should be rejected (common mistake)."""
        with pytest.raises(ValueError, match="Use Binance symbol"):
            normalize_and_validate_symbol('BTCUSDT_PERP')

        with pytest.raises(ValueError, match="Use Binance symbol"):
            normalize_and_validate_symbol('1000FLOKIUSDT_PERP')

        # Verify error message suggests correct format
        with pytest.raises(ValueError, match="BTCUSDT"):
            normalize_and_validate_symbol('BTCUSDT_PERP')


class TestNormalization:
    """Test that whitespace stripping and uppercasing work correctly."""

    def test_strip_whitespace(self):
        """Whitespace should be stripped from symbols."""
        assert normalize_and_validate_symbol('  BTCUSDT  ') == 'BTCUSDT'
        assert normalize_and_validate_symbol(' ETHUSDT ') == 'ETHUSDT'
        assert normalize_and_validate_symbol('BTCUSDT ') == 'BTCUSDT'
        assert normalize_and_validate_symbol(' BTCUSDT') == 'BTCUSDT'

    def test_uppercase_conversion(self):
        """Lowercase and mixed-case symbols should be converted to uppercase."""
        assert normalize_and_validate_symbol('btcusdt') == 'BTCUSDT'
        assert normalize_and_validate_symbol('ethusdt') == 'ETHUSDT'
        assert normalize_and_validate_symbol('BtCuSdT') == 'BTCUSDT'
        assert normalize_and_validate_symbol('EtHuSdT') == 'ETHUSDT'

    def test_combined_normalization(self):
        """Test combined whitespace stripping and uppercasing."""
        assert normalize_and_validate_symbol('  btcusdt  ') == 'BTCUSDT'
        assert normalize_and_validate_symbol(' EtHuSdT ') == 'ETHUSDT'

        # Test with long symbols
        assert normalize_and_validate_symbol('  broccoli714usdt  ') == 'BROCCOLI714USDT'
        assert normalize_and_validate_symbol(' 1000000bobusdt ') == '1000000BOBUSDT'


class TestRegressionCases:
    """Test edge cases and regression scenarios."""

    def test_symbols_with_numbers(self):
        """Symbols with numbers should be accepted."""
        assert normalize_and_validate_symbol('1INCHUSDT') == '1INCHUSDT'
        assert normalize_and_validate_symbol('1000FLOKIUSDT') == '1000FLOKIUSDT'
        assert normalize_and_validate_symbol('1000000BOBUSDT') == '1000000BOBUSDT'
        assert normalize_and_validate_symbol('BANANAS31USDT') == 'BANANAS31USDT'
        assert normalize_and_validate_symbol('BROCCOLI714USDT') == 'BROCCOLI714USDT'

    def test_symbols_with_mixed_alphanumeric(self):
        """Symbols with mixed letters and numbers should be accepted."""
        assert normalize_and_validate_symbol('BROCCOLIF3BUSDT') == 'BROCCOLIF3BUSDT'
        assert normalize_and_validate_symbol('1MBABYDOGEUSDT') == '1MBABYDOGEUSDT'

    def test_backward_compatibility(self):
        """Ensure existing 6-12 character symbols still work (backward compatibility)."""
        # Common symbols from Phase 1
        common_symbols = [
            'BTCUSDT',
            'ETHUSDT',
            'BNBUSDT',
            'ADAUSDT',
            'DOGEUSDT',
            'SOLUSDT',
            'DOTUSDT',
            'MATICUSDT',
        ]

        for symbol in common_symbols:
            assert normalize_and_validate_symbol(symbol) == symbol


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
