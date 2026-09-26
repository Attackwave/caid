import unittest

from caid_chat.net_sync import apply_sync, plan_sync


STATE = {
    "document": "demo.kicad_pcb", "project_path": "/tmp/demo", "sha256": "board-hash",
    "footprints": [{"ref": "U1", "footprint": "Package_DIP:DIP-8"}],
    "pads": [
        {"id": "a", "ref": "U1", "pin": "1", "net": ""},
        {"id": "b", "ref": "U1", "pin": "2", "net": "STALE"},
    ],
    "copper_items": 0,
}
SCHEMATIC = {
    "saved_file_sha256": "schematic-hash", "net_count": 1, "truncated": False,
    "components": [{"ref": "U1", "footprint": "Package_DIP:DIP-8"}],
    "nets": [{"name": "/VCC", "nodes": [{"ref": "U1", "pin": "1"}]}],
}


class NetSyncTests(unittest.TestCase):
    def test_empty_schematic_blocks_sync_for_populated_board(self):
        empty = {**SCHEMATIC, "components": [], "nets": [],
                 "component_count": 0, "net_count": 0}
        plan = plan_sync(STATE, empty, "de")
        self.assertEqual(plan.component_count, 0)
        self.assertTrue(any("keine Bauteile" in message for message in plan.blockers))

    def test_net_change_and_stale_net_removal(self):
        plan = plan_sync(STATE, SCHEMATIC)
        self.assertFalse(plan.blockers)
        self.assertEqual([(c.ref, c.pin, c.old_net, c.new_net) for c in plan.changes],
                         [("U1", "1", "", "/VCC"), ("U1", "2", "STALE", "")])

    def test_missing_pad_blocks_update(self):
        schematic = {**SCHEMATIC, "nets": [{"name": "/VCC", "nodes": [{"ref": "U1", "pin": "3"}]}]}
        plan = plan_sync(STATE, schematic, "de")
        self.assertIn("U1.3", " ".join(plan.blockers))

    def test_copper_blocks_and_footprint_mismatch_warns(self):
        state = {**STATE, "copper_items": 1,
                 "footprints": [{"ref": "U1", "footprint": "Other:Footprint"}]}
        plan = plan_sync(state, SCHEMATIC)
        self.assertTrue(any("tracks" in message for message in plan.blockers))
        self.assertTrue(any("U1" in message for message in plan.warnings))

    def test_changed_board_rejects_preview(self):
        class Project:
            path = "/tmp/demo"

        class Document:
            project = Project()

        class Board:
            name = "demo.kicad_pcb"
            document = Document()

            @staticmethod
            def get_as_string():
                return "board changed"

        with self.assertRaisesRegex(ValueError, "changed since the preview"):
            apply_sync(Board(), plan_sync(STATE, SCHEMATIC))


if __name__ == "__main__":
    unittest.main()
