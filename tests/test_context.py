import unittest

from caid_chat.context import capability_summary, detect_context, parse_major_version


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

if __name__ == "__main__":
    unittest.main()
