"""Check that user routing requirements become KiCad project settings."""

import json
from pathlib import Path
import tempfile
import unittest

from caid_chat.project_rules import synchronize_project_rules
from caid_chat.routing import default_contract, set_layers, set_limits


class ProjectRulesTests(unittest.TestCase):
    def test_synchronizes_four_layer_rules_without_erasing_other_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "Demo.kicad_pro"
            project.write_text(json.dumps({"board": {"design_settings": {"rules": {
                "min_hole_clearance": 0.4}}}, "text_variables": {"REV": "A"}}))
            contract = set_limits(set_layers(default_contract(), 4),
                                  ["0.2", "0.15", "0.6", "0.3", "0.25"])
            result = synchronize_project_rules(project, contract)
            rules = result["board"]["design_settings"]["rules"]
            self.assertEqual(rules["min_hole_clearance"], 0.4)
            self.assertEqual(rules["min_clearance"], 0.15)
            self.assertEqual(rules["min_copper_edge_clearance"], 0.25)
            self.assertEqual(rules["min_track_width"], 0.2)
            self.assertEqual(rules["min_via_diameter"], 0.6)
            self.assertEqual(result["net_settings"]["classes"][0]["via_drill"], 0.3)
            self.assertEqual(result["text_variables"]["REV"], "A")

    def test_refuses_incomplete_rules(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "Demo.kicad_pro"
            with self.assertRaisesRegex(ValueError, "all required limits"):
                synchronize_project_rules(project, set_layers(default_contract(), 2))
            self.assertFalse(project.exists())

    def test_preserves_stricter_existing_constraints_in_routing_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "Demo.kicad_pro"
            project.write_text(json.dumps({"board": {"design_settings": {"rules": {
                "min_track_width": 0.3}}}}))
            contract = set_limits(set_layers(default_contract(), 1), ["0.2", "0.2", "0.25"])
            with self.assertRaisesRegex(ValueError, "stricter"):
                synchronize_project_rules(project, contract, preserve_stricter=True)
            self.assertEqual(json.loads(project.read_text())["board"]["design_settings"]
                             ["rules"]["min_track_width"], 0.3)


if __name__ == "__main__":
    unittest.main()
