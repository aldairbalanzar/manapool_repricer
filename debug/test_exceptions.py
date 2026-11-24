import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import repricing_preview as p
from repricing_exceptions import load_exceptions

SID = '12345678-1234-4234-8234-123456789abc'
PID = '12345678-1234-4234-8234-123456789abd'

class ExceptionTests(unittest.TestCase):
    def test_report(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/'exceptions.csv'
            path.write_text(f'scryfall_id,card_name,set_code,is_foil,reason\n{SID}, test ,ABC,false,hold\n')
            exceptions = load_exceptions(Path(d))
            report = Path(d)/'report.csv'
            for finish, condition, expected in [('NF','NM','exception'),('NF','LP','exception'),('FO','NM','floor_increase'),('EF','NM','floor_increase')]:
                with patch.object(p,'REPORT_FILE',report):
                    p.generate_report({PID:{'price_cents':25,'quantity':1}}, {PID:{'card':{'scryfall_id':SID,'name':'Test','set_code':'abc'},'variant':{'finish_id':finish,'condition_id':condition,'low_price':100,'available_quantity':1}}}, exceptions)
                with report.open() as f:
                    row = next(csv.DictReader(f))
                self.assertEqual(row['action'],expected)
                self.assertEqual(row['target_price'],'0.25' if expected=='exception' else '0.75')
            with patch.object(p,'REPORT_FILE',report):
                p.generate_report({PID:{'price_cents':25,'quantity':1}}, {}, exceptions)
            with report.open() as f:
                self.assertEqual(next(csv.DictReader(f))['action'],'review')
            path.write_text('scryfall_id,card_name,set_code,is_foil\ninvalid,test,abc,false\n')
            with self.assertRaises(ValueError): load_exceptions(Path(d))
