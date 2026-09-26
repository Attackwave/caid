import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from caid_chat.project_brief import set_requirement
from caid_chat.route_project import _findings, route_project
from caid_chat.routing import default_contract, set_layers, set_limits


class RouteProjectTests(unittest.TestCase):
    def test_new_drc_finding_is_distinct_from_existing(self):
        old = {"violations": [{"severity": "warning", "type": "courtyard",
                               "description": "Missing courtyard", "items": [{"uuid": "pad-1"}]}]}
        new = {"violations": old["violations"] + [{"severity": "error", "type": "shorting_items",
                                                   "description": "Short", "items": [{"uuid": "track-1"}]}]}
        self.assertEqual(sum((_findings(new) - _findings(old)).values()), 1)
        self.assertEqual(sum((_findings(old) - _findings(old)).values()), 0)

    def test_unset_contract_blocks_before_any_project_edit(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "routing layers"):
                route_project(directory, "board.kicad_pcb", default_contract())
            with self.assertRaisesRegex(ValueError, "routing limits"):
                route_project(directory, "board.kicad_pcb", set_layers(default_contract(), 2))

    def test_saved_board_preflight_blocks_oversize_before_route_worker(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "board.kicad_pcb").write_text("dummy", encoding="utf-8")
            set_requirement(directory, "Board", "Maximum 60 × 16 mm",
                            check={"kind": "board_size", "mode": "maximum",
                                   "width_mm": 60, "height_mm": 16})
            contract = set_limits(set_layers(default_contract(), 2),
                                  ["0.2", "0.2", "0.6", "0.3", "0.5"])
            inspected = {"copper_layers": 2, "outline_mm": [0, 0, 100, 100],
                         "footprints": []}
            with patch("caid_chat.route_project._drc", return_value={
                    "violations": [], "schematic_parity": []}), patch(
                    "caid_chat.route_project._worker", return_value=inspected) as worker:
                with self.assertRaisesRegex(ValueError, "board outline 100.00 × 100.00"):
                    route_project(directory, "board.kicad_pcb", contract)
            self.assertEqual(worker.call_count, 1)
            self.assertEqual(worker.call_args.args[0]["action"], "inspect")


if __name__ == "__main__":
    unittest.main()
