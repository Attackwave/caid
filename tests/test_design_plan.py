import unittest
from unittest.mock import patch
from pathlib import Path

from caid_chat.design_plan import (apply_design_plan, describe_design_plan,
                                   new_erc_findings, prepare_design_plan)


STATE = {"document": "demo.kicad_pcb", "project_path": "/demo", "sha256": "board-hash",
         "footprints": [{"ref": "U1", "footprint": "Lib:Old"}],
         "pads": [{"id": "pad-1", "ref": "U1", "pin": "1", "net": "VCC"}], "copper_items": 0}
CURRENT = {"saved_file_sha256": "old", "component_count": 1, "net_count": 1,
           "components": [{"ref": "U1", "value": "Flash", "footprint": "Lib:Old"}],
           "nets": [{"name": "VCC", "nodes": [{"ref": "U1", "pin": "1"}]}], "truncated": False}
CANDIDATE = {"saved_file_sha256": "new", "component_count": 1, "net_count": 1,
             "components": [{"ref": "U1", "value": "Flash", "footprint": "Lib:New"}],
             "nets": [{"name": "GND", "nodes": [{"ref": "U1", "pin": "1"}]}], "truncated": False}


class FakeStage:
    candidate_snapshot = CANDIDATE
    original_hash = "old"
    erc_before = (1, 2)
    erc_after = (0, 2)
    stage_dir = Path("/tmp/stage")


class DesignPlanTests(unittest.TestCase):
    def test_combined_preview_shows_incremental_pcb_impact(self):
        with patch("caid_chat.design_plan.board_state", return_value=STATE):
            plan = prepare_design_plan(object(), FakeStage(), CURRENT, "de")
        self.assertEqual(plan.pad_differences_before, 0)
        self.assertEqual(plan.pad_differences_after, 1)
        self.assertEqual(plan.footprint_differences_after, 1)
        self.assertEqual(plan.net_changes, (("U1", "1", "VCC", "GND"),))
        self.assertEqual(plan.footprint_changes, (("U1", "Lib:Old", "Lib:New"),))
        self.assertIn("Footprint-Zuordnungen", describe_design_plan(plan, "de"))

    def test_changed_board_blocks_application(self):
        with patch("caid_chat.design_plan.board_state", return_value=STATE), patch(
                "caid_chat.design_plan.find_footprint", return_value={"id": "Lib:New"}):
            plan = prepare_design_plan(object(), FakeStage(), CURRENT)
        with patch("caid_chat.design_plan.board_state", return_value={**STATE, "sha256": "changed"}):
            with self.assertRaisesRegex(ValueError, "PCB changed"):
                apply_design_plan(object(), plan)

    def test_increased_erc_errors_are_prominent_in_preview(self):
        old = {"sheet": "/", "type": "legacy", "severity": "warning",
               "description": "Earlier issue", "items": ()}
        fresh = {"sheet": "/", "type": "pin_not_connected", "severity": "error",
                 "description": "Pin not connected",
                 "items": ({"uuid": "pin-1", "description": "Symbol R3 Pin 1",
                            "pos": {"x": 42.5, "y": 55.0}},)}

        class ErrorStage(FakeStage):
            erc_after = (2, 2)
            erc_findings_before = (old, old)
            erc_findings_after = (old, fresh, old)

        with patch("caid_chat.design_plan.board_state", return_value=STATE):
            plan = prepare_design_plan(object(), ErrorStage(), CURRENT, "de")
        description = describe_design_plan(plan, "de")
        self.assertIn("Neue ERC-Fehler", description)
        self.assertIn("Pin not connected", description)
        self.assertIn("Symbol R3 Pin 1 (42.5, 55 mm)", description)
        self.assertEqual(new_erc_findings((old, old), (old, fresh, old)), (fresh,))


if __name__ == "__main__":
    unittest.main()
