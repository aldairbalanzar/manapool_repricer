import tempfile
import unittest
from pathlib import Path
from repricing_exceptions import load_exceptions

HEADER = 'scryfall_id,card_name,set_code,is_foil,reason\n'
SID = '12345678-1234-4234-8234-123456789abc'

class DirectoryTests(unittest.TestCase):
    def test_multiple(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            (p/'a.csv').write_text(HEADER + f'{SID},Test,ABC,false,hold\n')
            (p/'b.CSV').write_text(HEADER + f'{SID},test,abc,false,hold\n{SID},Test,ABC,true,foil\n')
            (p/'notes.txt').write_text('ignored')
            result = load_exceptions(p)
            self.assertEqual(len(result), 2)
            self.assertEqual(result[(SID,'test','abc',False)], 'hold')
    def test_empty_header(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d)/'empty.csv').write_text(HEADER)
            self.assertEqual(load_exceptions(d), {})
    def test_missing(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError): load_exceptions(Path(d)/'missing')
            with self.assertRaises(ValueError): load_exceptions(d)
    def test_malformed(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d)/'a.csv').write_text(HEADER)
            (Path(d)/'b.csv').write_text('bad_header\n')
            with self.assertRaisesRegex(ValueError, 'b.csv'): load_exceptions(d)
    def test_distinct_reasons(self):
        with tempfile.TemporaryDirectory() as d:
            for name, reason in [('a','hold'),('b','manual'),('c','hold')]:
                (Path(d)/f'{name}.csv').write_text(HEADER+f'{SID},Test,ABC,false,{reason}\n')
            self.assertEqual(list(load_exceptions(d).values()), ['hold; manual'])
