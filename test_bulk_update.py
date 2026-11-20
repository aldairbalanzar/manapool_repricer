import unittest
from full_repricing import verify_bulk_response
def candidate(pid, price=75, qty=2):
    return ({'product_id': pid}, 15, price, qty)
class BulkTests(unittest.TestCase):
    def test_valid(self):
        x = verify_bulk_response([candidate('a')], {'inventory':[{'product_id':'a','product_type':'mtg_single','price_cents':75,'quantity':2}], 'skipped':[]})
        self.assertEqual(x['a'][0], 'VERIFIED_RESPONSE')
    def test_skipped(self):
        x = verify_bulk_response([candidate('a')], {'inventory':[], 'skipped':[{'product_id':'a','reason':'incomplete_new_inventory'}]})
        self.assertEqual(x['a'][0], 'API_SKIPPED')
    def test_missing(self):
        x = verify_bulk_response([candidate('a')], {'inventory':[], 'skipped':[]})
        self.assertEqual(x['a'][0], 'MISSING_RESPONSE')
    def test_wrong_quantity(self):
        x = verify_bulk_response([candidate('a')], {'inventory':[{'product_id':'a','product_type':'mtg_single','price_cents':75,'quantity':3}], 'skipped':[]})
        self.assertEqual(x['a'][0], 'VERIFY_FAILED')
    def test_duplicate(self):
        item = {'product_id':'a','product_type':'mtg_single','price_cents':75,'quantity':2}
        with self.assertRaises(ValueError):
            verify_bulk_response([candidate('a')], {'inventory':[item,item], 'skipped':[]})
if __name__ == '__main__':
    unittest.main()
