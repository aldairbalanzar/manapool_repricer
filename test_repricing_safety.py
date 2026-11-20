"""Run locally with: python -m unittest test_repricing_safety.py"""
import unittest
from unittest.mock import patch
from repricing_preview import proposed_price

class PricingSafetyTests(unittest.TestCase):
    def test_floor(self):
        self.assertEqual(proposed_price(15, 15), (75, 'floor_increase', 'floor_increase'))
    def test_competitive(self):
        self.assertEqual(proposed_price(200, 150), (151, 'competitive_decrease', 'one_cent_above_market_low'))
    def test_no_self_chase(self):
        self.assertEqual(proposed_price(100, 100)[0], 100)
        self.assertEqual(proposed_price(100, 150)[0], 100)
    def test_floor_when_no_market(self):
        self.assertEqual(proposed_price(10, None)[0], 75)
    def test_no_write_without_confirmation(self):
        import full_repricing
        with patch.dict('os.environ', {'FULL_REPRICING_CONFIRM': ''}):
            with self.assertRaisesRegex(RuntimeError, 'not authorized'):
                full_repricing.main()

if __name__ == '__main__':
    unittest.main()
