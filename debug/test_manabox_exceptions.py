import tempfile
import unittest
from pathlib import Path
from repricing_exceptions import load_exceptions

class ManaBoxTests(unittest.TestCase):
    def test_mixed_formats(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            sid = '4e8f4f6c-dc34-4573-8596-885f61d4dda2'
            (p/'a.csv').write_text(f'Scryfall ID,Name,Set code,Foil,Condition\n{sid},Hornet Queen,SLZ,normal,near_mint\n{sid},Hornet Queen,SLZ,foil,lightly_played\n{sid},Hornet Queen,SLZ,etched,near_mint\n')
            (p/'b.csv').write_text(f'scryfall_id,card_name,set_code,is_foil,reason\n{sid},Hornet Queen,SLZ,false,manual\n')
            result = load_exceptions(p)
            self.assertEqual(len(result), 2)
            self.assertEqual(result[(sid,'hornet queen','slz',False)], 'manual')
            self.assertIn((sid,'hornet queen','slz',True), result)
    def test_invalid_foil(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d)/'a.csv').write_text('Scryfall ID,Name,Set code,Foil\n4e8f4f6c-dc34-4573-8596-885f61d4dda2,Hornet Queen,SLZ,unknown\n')
            with self.assertRaises(ValueError):
                load_exceptions(d)
