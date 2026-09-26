import unittest

from caid_chat.board_ops import (apply_placements, board_matches_snapshot, describe_placements, geometry_warnings,
                                 mark_footprints, repair_placements, snapshot,
                                 validate_placements)


BOARD = {
    "outline_mm": [0, 0, 30, 20],
    "truncated": False,
    "footprints": [
        {"ref": "U1", "position_mm": [10.0, 10.0], "side": "TOP",
         "bbox_mm": [8, 8, 12, 12], "locked": False},
        {"ref": "J1", "position_mm": [20.0, 10.0], "side": "TOP",
         "bbox_mm": [18, 8, 22, 12], "locked": False},
        {"ref": "JP1", "position_mm": [25.0, 10.0], "side": "TOP",
         "bbox_mm": [24, 8, 26, 12], "locked": True},
    ],
}


class PlacementTests(unittest.TestCase):
    def test_apply_accepts_context_metadata_but_rejects_hidden_board_change(self):
        from types import SimpleNamespace

        class Board:
            name = "demo.kicad_pcb"
            document = SimpleNamespace(project=SimpleNamespace(path="/tmp/demo"))

            def __init__(self):
                self.contents = "(kicad_pcb (segment first))"

            def get_footprints(self):
                return []

            def get_tracks(self):
                return [SimpleNamespace(layer=1)]

            def get_vias(self):
                return []

            def get_nets(self):
                return []

            def get_shapes(self):
                return []

            def get_copper_layer_count(self):
                return 2

            def get_layer_name(self, _layer):
                return "F.Cu"

            def get_as_string(self):
                return self.contents

        board = Board()
        original = snapshot(board)
        original["project_brief"] = {"requirements": {"size": "80x30"}}
        self.assertTrue(board_matches_snapshot(board, original))
        board.contents = "(kicad_pcb (segment moved))"
        self.assertEqual(original["counts"], snapshot(board)["counts"])
        self.assertFalse(board_matches_snapshot(board, original))
        with self.assertRaisesRegex(ValueError, "changed since the preview"):
            apply_placements(board, [], original)

    def test_mark_footprints_uses_editor_selection(self):
        from types import SimpleNamespace
        fp = SimpleNamespace(reference_field=SimpleNamespace(text=SimpleNamespace(value="U1")))
        class Board:
            def get_footprints(self):
                return [fp]
            def add_to_selection(self, items):
                self.selected = items
        board = Board()
        mark_footprints(board, ["U1"])
        self.assertEqual(board.selected, [fp])
        with self.assertRaises(ValueError):
            mark_footprints(board, ["U2"])

    def test_side_only_proposal_keeps_coordinates(self):
        items = validate_placements(
            [{"ref": "J1", "side": "BOTTOM", "x_mm": None, "y_mm": None}], BOARD)
        self.assertEqual((items[0].x_mm, items[0].y_mm, items[0].side), (20.0, 10.0, "BOTTOM"))
        self.assertIn("TOP Mitte 20.000, 10.000 mm → BOTTOM Mitte 20.000, 10.000 mm",
                      describe_placements(items, BOARD, "de"))
        self.assertEqual(geometry_warnings(items, BOARD), [])

    def test_asymmetric_connector_stays_on_board_after_flip(self):
        board = {
            "outline_mm": [115, 83, 178, 104], "truncated": False,
            "footprints": [{
                "ref": "J2", "position_mm": [117.68, 85.88], "side": "TOP",
                "bbox_mm": [115.88, 84.08, 170.28, 87.67], "locked": False,
            }],
        }
        placements = validate_placements(
            [{"ref": "J2", "side": "BOTTOM", "x_mm": None, "y_mm": None}], board)
        self.assertAlmostEqual(placements[0].x_mm, 168.48)
        self.assertEqual(geometry_warnings(placements, board), [])

    def test_collision_reported_before_apply(self):
        items = validate_placements(
            [{"ref": "J1", "side": "TOP", "x_mm": 11, "y_mm": 10}], BOARD)
        self.assertTrue(any("U1 und J1" in warning for warning in geometry_warnings(items, BOARD, "de")))

    def test_local_repair_separates_nearby_items(self):
        items = validate_placements(
            [{"ref": "J1", "side": "TOP", "x_mm": 13.9, "y_mm": 10}], BOARD)
        self.assertTrue(geometry_warnings(items, BOARD))
        self.assertEqual(geometry_warnings(repair_placements(items, BOARD), BOARD), [])

    def test_invalid_proposals_rejected(self):
        for proposal in (
            [{"ref": "U9", "side": "TOP", "x_mm": None, "y_mm": None}],
            [{"ref": "JP1", "side": "BOTTOM", "x_mm": None, "y_mm": None}],
            [{"ref": "U1", "side": "LEFT", "x_mm": None, "y_mm": None}],
            [{"ref": "U1", "side": "BOTTOM", "x_mm": 12, "y_mm": None}],
            [{"ref": "U1", "side": "BOTTOM", "x_mm": float("nan"), "y_mm": 10}],
        ):
            with self.subTest(proposal=proposal), self.assertRaises(ValueError):
                validate_placements(proposal, BOARD)


if __name__ == "__main__":
    unittest.main()
