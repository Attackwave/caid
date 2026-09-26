import unittest

from caid_chat.schematic_connections import rewrite_no_connect_markers, rewrite_pin_connections


SOURCE = '''(kicad_sch
  (lib_symbols (symbol "Device:R" (symbol "R_1_1"
    (pin passive line (at -2.54 0 0) (length 2.54) (name "A") (number "1"))
    (pin passive line (at 2.54 0 180) (length 2.54) (name "B") (number "2")))))
  (symbol (lib_id "Device:R") (at 50 55 0) (unit 1)
    (property "Reference" "R1" (at 50 50 0)))
  (label "LINK" (at 60 55 0)))'''


class PinConnectionTests(unittest.TestCase):
    def test_adds_label_at_exact_embedded_pin_position(self):
        result, positions = rewrite_pin_connections(
            SOURCE, [{"ref": "R1", "pin": "1", "net": "LINK"}])
        self.assertEqual(positions, {(47.46, 55.0)})
        self.assertIn('(label "LINK" (at 47.46 55 0)', result)
        self.assertEqual(result.count('(label "LINK"'), 2)

    def test_adds_no_connect_only_at_exact_unused_pin(self):
        result, positions = rewrite_no_connect_markers(SOURCE, [{"ref": "R1", "pin": "1"}])
        self.assertEqual(positions, {(47.46, 55.0)})
        self.assertIn('(no_connect (at 47.46 55)', result)
        with self.assertRaisesRegex(ValueError, "already marked"):
            rewrite_no_connect_markers(result, [{"ref": "R1", "pin": "1"}])
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            rewrite_no_connect_markers(SOURCE, [{"ref": "R1", "pin": "1"}] * 2)

    def test_rejects_ambiguous_or_unsupported_geometry(self):
        request = [{"ref": "R1", "pin": "1", "net": "LINK"}]
        cases = ((SOURCE.replace('(at 50 55 0)', '(at 50 55 90)'), "unrotated"),
                 (SOURCE.replace('(unit 1)', '(unit 1) (mirror x)'), "unmirrored"),
                 (SOURCE.replace('(label "LINK"', '(no_connect (at 47.46 55)) (label "LINK"'),
                  "already marked"),
                 (SOURCE.replace('(label "LINK"', '(sheet (at 0 0)) (label "LINK"'),
                  "single-sheet"))
        for source, issue in cases:
            with self.subTest(issue=issue), self.assertRaisesRegex(ValueError, issue):
                rewrite_pin_connections(source, request)
        with self.assertRaisesRegex(ValueError, "not found"):
            rewrite_pin_connections(SOURCE, [{"ref": "R1", "pin": "1", "net": "UNKNOWN"}])
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            rewrite_pin_connections(SOURCE, request * 2)


if __name__ == "__main__":
    unittest.main()
