import unittest
from types import SimpleNamespace

from caid_chat.ai import _validate_result
from caid_chat.project_tools import execute_read_tool


class FakeBoard:
    def __init__(self):
        self.fp = SimpleNamespace(
            id="fp-1", reference_field=SimpleNamespace(text=SimpleNamespace(value="U1")),
            value_field=SimpleNamespace(text=SimpleNamespace(value="MX29LV160B")),
            definition=SimpleNamespace(id="Local:TSOP48"),
            position=SimpleNamespace(x=20_000_000, y=30_000_000))
        self.pad = SimpleNamespace(parent="fp-1", number="1", net=SimpleNamespace(name="VCC"))

    def get_footprints(self):
        return [self.fp]

    def get_pads(self):
        return [self.pad]

    def get_selection(self):
        return [self.fp]


class ProjectToolTests(unittest.TestCase):
    def setUp(self):
        self.board = FakeBoard()
        self.board_snapshot = {"project_path": "/missing", "footprints": [
            {"ref": "U1", "footprint_id": "Local:TSOP48"}]}
        self.schematic = {"components": [{"ref": "U1", "value": "MX29LV160B",
                                         "footprint": "Local:TSOP48"}],
                          "nets": [{"name": "VCC", "nodes": [{"ref": "U1", "pin": "1"}]}]}

    def run_tool(self, name, argument=""):
        return execute_read_tool({"tool": name, "argument": argument}, self.board,
                                 self.board_snapshot, self.schematic)

    def test_component_and_net_compare_saved_and_live_data(self):
        component = self.run_tool("component", "u1")
        self.assertEqual(component["schematic_pins"], [{"pin": "1", "net": "VCC"}])
        self.assertEqual(component["pcb_pads"], [{"ref": "U1", "pin": "1", "net": "VCC"}])
        self.assertEqual(component["live_pcb"]["position_mm"], [20.0, 30.0])
        net = self.run_tool("net", "VCC")
        self.assertEqual(net["live_pcb_pads"][0]["ref"], "U1")

    def test_selection_and_search_are_bounded(self):
        self.assertEqual(self.run_tool("selection")["items"][0]["ref"], "U1")
        self.assertEqual(self.run_tool("find_components", "MX29")["saved_schematic"][0]["ref"], "U1")
        with self.assertRaises(ValueError):
            self.run_tool("find_components", "M")

    def test_model_tool_requests_cannot_also_mutate(self):
        valid = {"answer": "", "edit_schematic": False, "placements": [],
                 "tool_requests": [{"tool": "component", "argument": "U1"}], "footprint_updates": []}
        self.assertEqual(_validate_result(valid), valid)
        with self.assertRaises(RuntimeError):
            _validate_result({**valid, "edit_schematic": True})
        with self.assertRaises(RuntimeError):
            _validate_result({**valid, "tool_requests": [{"tool": "delete_file", "argument": "x"}]})
        footprint_change = {**valid, "tool_requests": [],
                            "footprint_updates": [{"ref": "U1", "footprint_id": "Local:TSOP48"}]}
        self.assertEqual(_validate_result(footprint_change), footprint_change)
        with self.assertRaises(RuntimeError):
            _validate_result({**footprint_change, "placements": [{"ref": "U1"}]})

    def test_net_rename_uses_shared_provider_contract(self):
        reply = {"answer": "Rename LINK to CLOCK", "edit_schematic": False,
                 "placements": [], "tool_requests": [], "footprint_updates": [],
                 "field_updates": [], "net_renames": [{"from": "LINK", "to": "CLOCK"}]}
        self.assertEqual(_validate_result(reply)["net_renames"], reply["net_renames"])
        with self.assertRaisesRegex(RuntimeError, "separate review"):
            _validate_result({**reply, "placements": [{"ref": "U1"}]})
        with self.assertRaisesRegex(RuntimeError, "Invalid net rename"):
            _validate_result({**reply, "net_renames": [{"from": "LINK", "to": 4}]})


if __name__ == "__main__":
    unittest.main()
