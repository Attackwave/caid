"""Checks for part evidence and explicit mechanical limits."""

import unittest

from caid_chat.component_review import review_components


class ComponentReviewTests(unittest.TestCase):
    def setUp(self):
        self.resolved = {"U1": {"source": {"ref": "U1", "part": {}},
                                "pins": {"1": (0, 0), "2": (1, 0)}, "side": "TOP"}}

    def test_missing_evidence_remains_visible(self):
        result = review_components({}, self.resolved)[0]
        self.assertEqual(result["status"], "needs_review")
        self.assertEqual(result["physical_verification"], "required")
        self.assertTrue(any("pin map" in item for item in result["issues"]))

    def test_documented_part_still_requires_physical_verification(self):
        self.resolved["U1"]["source"]["part"] = {
            "mpn": "Example-2", "datasheet_url": "https://example.com/part.pdf",
            "package": "PKG-2", "pin_map": {"1": "VCC", "2": "GND"},
            "body_height_mm": 2.5, "height_source": "PKG-2 page 3"}
        result = review_components({"mechanical": {"body_clearance_mm": {"TOP": 3}}},
                                   self.resolved)[0]
        self.assertEqual(result["status"], "documented")
        self.assertEqual(result["physical_verification"], "required")

    def test_height_violation_blocks_draft(self):
        self.resolved["U1"]["source"]["part"] = {"body_height_mm": 4}
        with self.assertRaisesRegex(ValueError, "exceeds TOP clearance"):
            review_components({"mechanical": {"body_clearance_mm": {"TOP": 3}}},
                              self.resolved)

    def test_pin_map_cannot_introduce_unseen_pins(self):
        self.resolved["U1"]["source"]["part"] = {"pin_map": {"3": "VCC"}}
        with self.assertRaisesRegex(ValueError, "unknown symbol pins"):
            review_components({}, self.resolved)

    def test_pin_name_disagreement_is_reported(self):
        self.resolved["U1"]["pin_names"] = {"1": "VCC", "2": "GND"}
        self.resolved["U1"]["source"]["part"] = {
            "mpn": "Example-2", "datasheet_url": "https://example.com/part.pdf",
            "package": "PKG-2", "pin_map": {"1": "VDD", "2": "GND"}}
        result = review_components({}, self.resolved)[0]
        self.assertEqual(result["symbol_name_differences"], ["1"])
        self.assertEqual(result["status"], "needs_review")


if __name__ == "__main__":
    unittest.main()
