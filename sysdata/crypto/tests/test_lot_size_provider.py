"""
Unit tests for LotSizeProvider.
"""

import unittest
import pandas as pd
import numpy as np

from sysdata.crypto.lot_size_provider import LotSizeProvider, LOT_SIZES, DEFAULT_LOT_SIZE


class TestLotSizeProvider(unittest.TestCase):
    def setUp(self):
        """Set up test fixtures."""
        self.provider = LotSizeProvider()

    def test_get_lot_size_known_instrument(self):
        """Test getting lot size for known instrument."""
        btc_lot = self.provider.get_lot_size('BTCUSDT_PERP')
        self.assertEqual(btc_lot, 0.001)

        eth_lot = self.provider.get_lot_size('ETHUSDT_PERP')
        self.assertEqual(eth_lot, 0.01)

        sol_lot = self.provider.get_lot_size('SOLUSDT_PERP')
        self.assertEqual(sol_lot, 0.1)

    def test_get_lot_size_unknown_instrument(self):
        """Test getting lot size for unknown instrument uses default."""
        unknown_lot = self.provider.get_lot_size('UNKNOWNUSDT_PERP')
        self.assertEqual(unknown_lot, DEFAULT_LOT_SIZE)

        # Verify it was added to used_default set
        self.assertIn('UNKNOWNUSDT_PERP', self.provider.instruments_using_default)

    def test_get_lot_value(self):
        """Test calculating lot value."""
        # BTC: 0.001 × 85000 = 85.0
        btc_lot_value = self.provider.get_lot_value('BTCUSDT_PERP', price=85000)
        self.assertAlmostEqual(btc_lot_value, 85.0, places=2)

        # ETH: 0.01 × 3000 = 30.0
        eth_lot_value = self.provider.get_lot_value('ETHUSDT_PERP', price=3000)
        self.assertAlmostEqual(eth_lot_value, 30.0, places=2)

        # SOL: 0.1 × 150 = 15.0
        sol_lot_value = self.provider.get_lot_value('SOLUSDT_PERP', price=150)
        self.assertAlmostEqual(sol_lot_value, 15.0, places=2)

    def test_convert_notional_to_lots(self):
        """Test converting notional position to lots."""
        # 2.374 BTC / 0.001 = 2374.0 lots
        lots = self.provider.convert_notional_to_lots(2.374, 0.001)
        self.assertAlmostEqual(lots, 2374.0, places=2)

        # 5.5 ETH / 0.01 = 550.0 lots
        lots = self.provider.convert_notional_to_lots(5.5, 0.01)
        self.assertAlmostEqual(lots, 550.0, places=2)

    def test_convert_lots_to_notional(self):
        """Test converting lots to notional position."""
        # 2374 lots × 0.001 = 2.374 BTC
        notional = self.provider.convert_lots_to_notional(2374, 0.001)
        self.assertAlmostEqual(notional, 2.374, places=3)

        # 550 lots × 0.01 = 5.5 ETH
        notional = self.provider.convert_lots_to_notional(550, 0.01)
        self.assertAlmostEqual(notional, 5.5, places=2)

    def test_round_trip_conversion(self):
        """Test that conversions round-trip correctly."""
        notional = 2.374
        lot_size = 0.001

        # notional → lots → notional
        lots = self.provider.convert_notional_to_lots(notional, lot_size)
        recovered_notional = self.provider.convert_lots_to_notional(lots, lot_size)

        self.assertAlmostEqual(recovered_notional, notional, places=6)

    def test_get_lot_values_for_instruments(self):
        """Test getting lot values for multiple instruments."""
        instruments = ['BTCUSDT_PERP', 'ETHUSDT_PERP', 'SOLUSDT_PERP']
        prices = pd.Series({
            'BTCUSDT_PERP': 85000,
            'ETHUSDT_PERP': 3000,
            'SOLUSDT_PERP': 150,
        })

        lot_values = self.provider.get_lot_values_for_instruments(instruments, prices)

        self.assertAlmostEqual(lot_values['BTCUSDT_PERP'], 85.0, places=2)
        self.assertAlmostEqual(lot_values['ETHUSDT_PERP'], 30.0, places=2)
        self.assertAlmostEqual(lot_values['SOLUSDT_PERP'], 15.0, places=2)

    def test_get_lot_values_missing_price(self):
        """Test getting lot values when price is missing."""
        instruments = ['BTCUSDT_PERP', 'ETHUSDT_PERP']
        prices = pd.Series({
            'BTCUSDT_PERP': 85000,
            # ETH price missing
        })

        lot_values = self.provider.get_lot_values_for_instruments(instruments, prices)

        self.assertAlmostEqual(lot_values['BTCUSDT_PERP'], 85.0, places=2)
        self.assertTrue(np.isnan(lot_values['ETHUSDT_PERP']))

    def test_convert_positions_to_lots(self):
        """Test converting positions to lots for multiple instruments."""
        positions = pd.Series({
            'BTCUSDT_PERP': 2.374,
            'ETHUSDT_PERP': 5.5,
            'SOLUSDT_PERP': 12.3,
        })

        lots = self.provider.convert_positions_to_lots(positions)

        self.assertAlmostEqual(lots['BTCUSDT_PERP'], 2374.0, places=2)
        self.assertAlmostEqual(lots['ETHUSDT_PERP'], 550.0, places=2)
        self.assertAlmostEqual(lots['SOLUSDT_PERP'], 123.0, places=2)

    def test_convert_lots_to_positions(self):
        """Test converting lots to positions for multiple instruments."""
        lots = pd.Series({
            'BTCUSDT_PERP': 2374.0,
            'ETHUSDT_PERP': 550.0,
            'SOLUSDT_PERP': 123.0,
        })

        positions = self.provider.convert_lots_to_positions(lots)

        self.assertAlmostEqual(positions['BTCUSDT_PERP'], 2.374, places=3)
        self.assertAlmostEqual(positions['ETHUSDT_PERP'], 5.5, places=2)
        self.assertAlmostEqual(positions['SOLUSDT_PERP'], 12.3, places=2)

    def test_custom_lot_sizes(self):
        """Test provider with custom lot sizes."""
        custom_lot_sizes = {
            'TESTUSDT_PERP': 0.1,
        }

        provider = LotSizeProvider(lot_sizes=custom_lot_sizes, default_lot_size=10.0)

        # Known instrument uses custom size
        self.assertEqual(provider.get_lot_size('TESTUSDT_PERP'), 0.1)

        # Unknown instrument uses custom default
        self.assertEqual(provider.get_lot_size('UNKNOWNUSDT_PERP'), 10.0)

    def test_instruments_with_mappings(self):
        """Test getting list of instruments with explicit mappings."""
        instruments = self.provider.instruments_with_mappings

        # Should include all instruments in LOT_SIZES
        self.assertIn('BTCUSDT_PERP', instruments)
        self.assertIn('ETHUSDT_PERP', instruments)
        self.assertIn('SOLUSDT_PERP', instruments)

        # Should be same length as LOT_SIZES
        self.assertEqual(len(instruments), len(LOT_SIZES))

    def test_zero_lot_size_raises(self):
        """Test that zero lot size raises ValueError."""
        with self.assertRaises(ValueError):
            self.provider.convert_notional_to_lots(100.0, lot_size=0.0)

        with self.assertRaises(ValueError):
            self.provider.convert_notional_to_lots(100.0, lot_size=-1.0)


if __name__ == '__main__':
    unittest.main()
