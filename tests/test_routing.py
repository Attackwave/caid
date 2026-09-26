import tempfile
import unittest
from pathlib import Path

from caid_chat.project_brief import load_brief, model_context, set_routing
from caid_chat.routing import default_contract, describe, preflight, set_layers, set_limits


class RoutingContractTests(unittest.TestCase):
    def test_one_routing_layer_uses_two_layer_board(self):
        contract = set_limits(set_layers(default_contract(), 1),
                              ["0.20", "0.20", "0.25"])
        self.assertEqual(contract["routing_layers"], ["F.Cu"])
        self.assertEqual(preflight(contract, {"copper_layers": 2, "outline_mm": [0, 0, 20, 10]},
                                   {"violations": [], "schematic_parity": []}), [])

    def test_four_layers_and_invalid_rules(self):
        contract = set_layers(default_contract(), 4)
        self.assertEqual(contract["routing_layers"], ["F.Cu", "In1.Cu", "In2.Cu", "B.Cu"])
        with self.assertRaises(ValueError):
            set_layers(default_contract(), 3)
        with self.assertRaises(ValueError):
            set_limits(contract, ["0.2", "0.2", "0.3", "0.3", "0.25"])

    def test_preflight_reports_real_blockers(self):
        contract = set_limits(set_layers(default_contract(), 2),
                              ["0.2", "0.2", "0.6", "0.3", "0.25"])
        result = preflight(contract, {"copper_layers": 4, "outline_mm": None},
                           {"violations": [{"severity": "error"}], "schematic_parity": [{}]})
        self.assertEqual(len(result), 4)
        self.assertIn("Sperren vor dem Routing", describe(contract, {"copper_layers": 4},
                                                           {"violations": [], "schematic_parity": []}, "de"))

    def test_one_sided_rejects_existing_bottom_copper(self):
        contract = set_limits(set_layers(default_contract(), 1), ["0.2", "0.2", "0.25"])
        blockers = preflight(contract, {"copper_layers": 2, "outline_mm": [0, 0, 20, 10],
                                        "track_layers": ["B.Cu"], "counts": {"vias": 1}},
                             {"violations": [], "schematic_parity": []})
        self.assertEqual(len(blockers), 2)

    def test_footprint_that_cannot_fit_rotated_board_blocks(self):
        contract = set_limits(set_layers(default_contract(), 2),
                              ["0.2", "0.2", "0.6", "0.3", "0.25"])
        blockers = preflight(contract, {"copper_layers": 2, "outline_mm": [0, 0, 60, 16],
                                        "target_size_mm": {"width_mm": 60, "height_mm": 16},
                                        "footprints": [{"ref": "J1", "bbox_mm": [0, 0, 17.39, 53.9]}]},
                             {"violations": [], "schematic_parity": []})
        self.assertTrue(any("J1 bounding box" in item for item in blockers))

    def test_actual_outline_and_placement_must_fit_project_maximum(self):
        contract = set_limits(set_layers(default_contract(), 2),
                              ["0.2", "0.2", "0.6", "0.3", "0.5"])
        clean = {"violations": [], "schematic_parity": []}
        board = {"copper_layers": 2, "outline_mm": [0, 0, 100, 100],
                 "target_size_mm": {"width_mm": 60, "height_mm": 16, "mode": "maximum"},
                 "footprints": [{"ref": "U1", "bbox_mm": [80, 80, 85, 85]}]}
        blockers = preflight(contract, board, clean)
        self.assertTrue(any("board outline 100.00 × 100.00" in item for item in blockers))
        board["outline_mm"] = [0, 0, 60, 16]
        blockers = preflight(contract, board, clean)
        self.assertTrue(any("U1 extends outside" in item for item in blockers))
        board["footprints"][0]["bbox_mm"] = [10, 3, 15, 8]
        self.assertEqual(preflight(contract, board, clean), [])

    def test_exact_size_rejects_smaller_outline(self):
        contract = set_limits(set_layers(default_contract(), 2),
                              ["0.2", "0.2", "0.6", "0.3", "0.5"])
        board = {"copper_layers": 2, "outline_mm": [0, 0, 58, 15],
                 "target_size_mm": {"width_mm": 60, "height_mm": 16, "mode": "exact"}}
        self.assertTrue(any("violates target" in item for item in preflight(
            contract, board, {"violations": [], "schematic_parity": []})))

    def test_contract_persists_in_project_and_model_context(self):
        with tempfile.TemporaryDirectory() as directory:
            contract = set_layers(default_contract(), 2)
            set_routing(directory, contract)
            self.assertEqual(load_brief(directory)["routing"], contract)
            self.assertEqual(model_context(load_brief(directory))["routing"], contract)
            self.assertEqual(len(load_brief(directory)["history"]), 1)


if __name__ == "__main__":
    unittest.main()
