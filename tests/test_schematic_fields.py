import unittest

from caid_chat.schematic_fields import (rewrite_footprint_fields, rewrite_local_net_labels,
                                        rewrite_symbol_fields)


SCHEMATIC = '''(kicad_sch
  (lib_symbols
    (symbol "Device:Flash" (property "Reference" "U1")
      (property "Footprint" "Library:DoNotTouch")))
  (symbol (lib_id "Device:Flash")
    (property "Reference" "U1" (at 0 0 0))
    (property "Footprint" "Library:Old" (at 0 0 0))
    (instances (project "Demo" (path "/" (reference "U1")))))
  (symbol (lib_id "Device:Flash")
    (property "Reference" "U2" (at 0 0 0))
    (property "Footprint" "Library:Other" (at 0 0 0))))
'''


class SchematicFieldTests(unittest.TestCase):
    def test_local_net_rename_touches_only_root_labels(self):
        source = '''(kicad_sch
          (lib_symbols (symbol "Lib:Part" (label "LINK")))
          (text "LINK")
          (label "LINK" (at 10 20 0))
          (label "LINK" (at 30 20 0))
          (label "OTHER" (at 40 20 0)))'''
        updated, counts = rewrite_local_net_labels(source, {"LINK": "CLOCK"})
        self.assertEqual(counts, {"LINK": 2})
        self.assertEqual(updated.count('(label "CLOCK"'), 2)
        self.assertIn('(label "LINK")))', updated)
        self.assertIn('(text "LINK")', updated)
        self.assertIn('(label "OTHER"', updated)
        for renames, issue in (({"MISSING": "NEW"}, "not found"),
                               ({"LINK": "OTHER"}, "already exists"),
                               ({"LINK": "BAD SPACE"}, "Invalid"),
                               ({"LINK": "OTHER", "OTHER": "NEXT"}, "Chained")):
            with self.subTest(renames=renames), self.assertRaisesRegex(ValueError, issue):
                rewrite_local_net_labels(source, renames)
        with self.assertRaisesRegex(ValueError, "single-sheet"):
            rewrite_local_net_labels(source.replace('(text "LINK")',
                                                   '(sheet (at 0 0))'), {"LINK": "CLOCK"})

    def test_rewrites_only_placed_symbol(self):
        updated, previous = rewrite_footprint_fields(SCHEMATIC, {"U1": "Library:New"})
        self.assertEqual(previous, {"U1": "Library:Old"})
        self.assertIn('"Footprint" "Library:DoNotTouch"', updated)
        self.assertIn('"Footprint" "Library:New"', updated)
        self.assertIn('"Footprint" "Library:Other"', updated)

    def test_rejects_missing_reference_and_invalid_id(self):
        with self.assertRaises(ValueError):
            rewrite_footprint_fields(SCHEMATIC, {"U9": "Library:New"})
        with self.assertRaises(ValueError):
            rewrite_footprint_fields(SCHEMATIC, {"U1": 'Library:New") (property "Reference" "U2'})

    def test_rewrites_value_on_every_unit_without_touching_library(self):
        source = SCHEMATIC.replace('(property "Footprint" "Library:Old"',
                                    '(property "Value" "Old" (at 0 0 0))\n    (property "Footprint" "Library:Old"')
        source = source.replace('  (symbol (lib_id "Device:Flash")\n    (property "Reference" "U2"',
                                '  (symbol (lib_id "Device:Flash")\n    (property "Reference" "U1"'
                                ' (at 0 0 0)) (property "Value" "Old" (at 0 0 0)))\n'
                                '  (symbol (lib_id "Device:Flash")\n    (property "Reference" "U2"')
        updated, previous = rewrite_symbol_fields(source, {("U1", "Value"): 'New "part"'})
        self.assertEqual(previous, {("U1", "Value"): "Old"})
        self.assertEqual(updated.count('(property "Value" "New \\"part\\""'), 2)
        self.assertIn('"Footprint" "Library:DoNotTouch"', updated)


if __name__ == "__main__":
    unittest.main()
