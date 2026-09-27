import tempfile
from pathlib import Path
import unittest

from caid_chat.schematic_symbols import rewrite_symbol_additions


SOURCE = '''(kicad_sch (version 20260306) (paper "A4")
  (lib_symbols)
  (symbol (lib_id "Other:Part") (at 50 50 0) (unit 1)
    (property "Reference" "U1" (at 50 45 0)))
  (sheet_instances (path "/" (page "1"))))'''


class SymbolAdditionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        symbols = self.root / "symbols"
        symbols.mkdir()
        (symbols / "Test.kicad_sym").write_text('''(kicad_symbol_lib (version 20260101)
          (symbol "Part" (symbol "Part_1_1"
            (pin passive line (at -2.54 0 0) (length 2.54) (name "A") (number "1"))
            (pin passive line (at 2.54 0 180) (length 2.54) (name "B") (number "2")))))''')
        footprints = self.root / "footprints" / "Test.pretty"
        footprints.mkdir(parents=True)
        (footprints / "Part.kicad_mod").write_text('''(footprint "Part"
          (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu"))
          (pad "2" smd rect (at 2 0) (size 1 1) (layers "F.Cu")))''')
        self.symbols = symbols
        self.footprints = footprints.parent
        self.request = {"ref": "U2", "symbol": "Test:Part", "value": "Example",
                        "footprint": "Test:Part", "x_mm": 100, "y_mm": 80}

    def rewrite(self, source=SOURCE, requests=None):
        return rewrite_symbol_additions(source, requests or [self.request], self.root,
                                        symbol_root=self.symbols, footprint_root=self.footprints)

    def test_adds_installed_symbol_without_touching_existing_instance(self):
        result, refs = self.rewrite()
        self.assertEqual(refs, {"U2"})
        self.assertIn('(symbol "Test:Part"', result)
        self.assertIn('(property "Reference" "U2"', result)
        self.assertIn('(property "Footprint" "Test:Part"', result)
        self.assertIn('(property "Reference" "U1"', result)
        self.assertEqual(result.count('(symbol "Test:Part"'), 1)

    def test_reuses_embedded_definition_and_rejects_ambiguous_requests(self):
        result, _ = self.rewrite()
        second, _ = self.rewrite(result, [{**self.request, "ref": "U3", "x_mm": 120}])
        self.assertEqual(second.count('(symbol "Test:Part"'), 1)
        for request, issue in (
            ({**self.request, "ref": "U1"}, "already exists"),
            ({**self.request, "x_mm": 50, "y_mm": 50}, "already occupied"),
            ({**self.request, "x_mm": 300}, "usable sheet"),
            ({**self.request, "footprint": "Test:Unknown"}, "not installed"),
        ):
            with self.subTest(issue=issue), self.assertRaisesRegex(ValueError, issue):
                self.rewrite(requests=[request])

    def test_rejects_footprint_without_all_symbol_pads(self):
        (self.footprints / "Test.pretty" / "Part.kicad_mod").write_text(
            '(footprint "Part" (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu")))')
        with self.assertRaisesRegex(ValueError, "lacks symbol pin numbers"):
            self.rewrite()


if __name__ == "__main__":
    unittest.main()
