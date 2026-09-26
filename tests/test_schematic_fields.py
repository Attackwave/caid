import unittest

from caid_chat.schematic_fields import rewrite_footprint_fields


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


if __name__ == "__main__":
    unittest.main()
