import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from caid_chat.project_brief import set_requirement
from caid_chat.route_project import (_findings, _project_copy, _routing_output_parent,
                                     _source_fingerprints, route_project)
from caid_chat.route_worker import _search_windows
from caid_chat.routing import default_contract, set_layers, set_limits


class RouteProjectTests(unittest.TestCase):
    def test_large_board_routes_start_with_local_windows(self):
        board = (0, 0, 500, 500)
        windows = _search_windows(board, [(240, 240), (245, 245)])
        self.assertEqual(windows[0], (235, 235, 250, 250))
        self.assertEqual(windows[-1], board)
        self.assertLess((windows[0][2] - windows[0][0]) *
                        (windows[0][3] - windows[0][1]), 500 * 500)

    def test_search_windows_clip_to_board_edges(self):
        board = (10, 20, 100, 80)
        windows = _search_windows(board, [(11, 22), (16, 25)])
        self.assertEqual(windows[0], (10, 20, 21, 30))
        self.assertEqual(windows[-1], board)

    def test_follow_up_route_copy_stays_beside_previous_copy(self):
        self.assertEqual(_routing_output_parent(Path("/project")), Path("/project/CAID-Routing"))
        self.assertEqual(_routing_output_parent(Path("/project/CAID-Routing/pass-1")),
                         Path("/project/CAID-Routing"))

    def test_route_copy_keeps_project_local_symbol_library(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source"
            source.mkdir()
            (source / "board.kicad_pcb").write_text("board", encoding="utf-8")
            (source / "Custom.kicad_sym").write_text("symbols", encoding="utf-8")
            target = Path(directory) / "copy"
            _project_copy(source, target, "board.kicad_pcb")
            self.assertEqual((target / "Custom.kicad_sym").read_text(), "symbols")

    def test_source_fingerprints_include_project_support_and_library_files(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / "board.kicad_pcb").write_text("board", encoding="utf-8")
            (project / "board.kicad_sch").write_text("circuit", encoding="utf-8")
            library = project / "Custom.pretty"
            library.mkdir()
            footprint = library / "Part.kicad_mod"
            footprint.write_text("first", encoding="utf-8")
            before = _source_fingerprints(project, "board.kicad_pcb")
            self.assertEqual(set(before), {"board.kicad_pcb", "board.kicad_sch",
                                           "Custom.pretty/Part.kicad_mod"})
            footprint.write_text("second", encoding="utf-8")
            self.assertNotEqual(before, _source_fingerprints(project, "board.kicad_pcb"))

    def test_routing_rejects_schematic_changed_during_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            board = project / "board.kicad_pcb"
            schematic = project / "board.kicad_sch"
            board.write_text("original", encoding="utf-8")
            schematic.write_text("original", encoding="utf-8")
            contract = set_limits(set_layers(default_contract(), 2),
                                  ["0.2", "0.2", "0.6", "0.3", "0.5"])
            reports = iter(({"violations": [], "schematic_parity": [],
                             "unconnected_items": [{}]},
                            {"violations": [], "schematic_parity": [],
                             "unconnected_items": []}))

            def worker(payload, _staging, _token):
                action = payload.get("action")
                if action == "inspect":
                    return {"copper_layers": 2, "outline_mm": [0, 0, 100, 100]}
                if action == "list":
                    return ["TEST"]
                Path(payload["destination"]).write_text("routed", encoding="utf-8")
                schematic.write_text("changed", encoding="utf-8")
                return {"net": "TEST"}

            with patch("caid_chat.route_project._drc", side_effect=lambda *args: next(reports)), patch(
                    "caid_chat.route_project._worker", side_effect=worker):
                with self.assertRaisesRegex(ValueError, "board.kicad_sch"):
                    route_project(project, board.name, contract)
            self.assertEqual(board.read_text(), "original")
            self.assertFalse((project / "CAID-Routing").exists())

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
