import unittest
import tempfile
from pathlib import Path

from caid_chat.context import (EditorContext, capability_summary, detect_context,
                               parse_major_version, saved_pcb_file_exists)


class FakeBoard:
    name = "rom-adapter.kicad_pcb"


class FakeVersion:
    full_version = "10.0.6"
    major = 10


class FakeKicad:
    @staticmethod
    def get_version():
        return FakeVersion()

    @staticmethod
    def get_board():
        return FakeBoard()


class ContextTests(unittest.TestCase):
    def test_version_parsing(self):
        self.assertEqual(parse_major_version("10.0.6"), 10)
        self.assertEqual(parse_major_version("KiCad 11.0.0-rc1"), 11)
        self.assertIsNone(parse_major_version("unknown"))

    def test_board_and_capabilities(self):
        context = detect_context(FakeKicad)
        self.assertEqual(context.editor, "pcb")
        self.assertTrue(context.can_read_board)
        self.assertFalse(context.schematic_plugin_available)
        self.assertIn("rom-adapter.kicad_pcb", capability_summary(context))

    def test_disk_presence_does_not_claim_editor_is_saved(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertFalse(saved_pcb_file_exists(directory, "demo.kicad_pcb"))
            (Path(directory) / "demo.kicad_pcb").write_text("saved", encoding="utf-8")
            self.assertTrue(saved_pcb_file_exists(directory, "demo.kicad_pcb"))
            self.assertIsNone(saved_pcb_file_exists(None, "demo.kicad_pcb"))
            context = EditorContext("10.0.6", 10, "pcb", "demo.kicad_pcb", True)
            report = capability_summary(context)
            self.assertIn("present on disk", report)
            self.assertIn("Unsaved editor changes: not reported", report)

if __name__ == "__main__":
    unittest.main()
