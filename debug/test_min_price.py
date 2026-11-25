import os
import unittest
from unittest.mock import patch
from repricing_preview import read_min_price_cents, proposed_price

class MinimumPriceTests(unittest.TestCase):
    def test_default(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(read_min_price_cents(), 75)
    def test_custom(self):
        with patch.dict(os.environ, {'MANAPOOL_MIN_PRICE_CENTS': '125'}):
            self.assertEqual(read_min_price_cents(), 125)
    def test_invalid(self):
        for value in ('', '0', '-1', '0.75', 'abc', ' 75'):
            with self.subTest(value=value), patch.dict(os.environ, {'MANAPOOL_MIN_PRICE_CENTS': value}):
                with self.assertRaises(ValueError):
                    read_min_price_cents()
    def test_pricing(self):
        with patch('repricing_preview.MIN_PRICE_CENTS', 125):
            self.assertEqual(proposed_price(75, 50)[0], 125)
            self.assertEqual(proposed_price(200, 50)[0], 125)
            self.assertEqual(proposed_price(200, 150)[0], 151)
