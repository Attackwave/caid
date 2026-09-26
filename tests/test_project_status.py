import tempfile
import unittest
from pathlib import Path

from caid_chat.ipc_support import board_hint_path, connection_detail
from caid_chat.project_status import overview


class ProjectStatusTests(unittest.TestCase):
    def test_hinted_board_must_exist_and_be_a_pcb(self):
        with tempfile.TemporaryDirectory() as directory:
            pcb = Path(directory) / "demo.kicad_pcb"
            pcb.write_text("", encoding="utf-8")
            self.assertEqual(board_hint_path(str(pcb)), pcb)
            self.assertIsNone(board_hint_path(str(pcb.with_suffix(".txt"))))
            self.assertIsNone(board_hint_path(""))

    def test_overview_labels_old_checks_and_open_work(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "demo.kicad_pcb").write_text("", encoding="utf-8")
            (root / "demo.kicad_sch").write_text("", encoding="utf-8")
            brief = {"requirements": {"Power": {"status": "assumed"}},
                     "open_questions": ["Supply voltage?"],
                     "pcb_size_mm": {"width_mm": 60, "height_mm": 16},
                     "pcb_size_mode": "maximum",
                     "generated_design": {"erc_errors": 3, "drc_errors": 1}}
            result = overview(root, brief, "de")
            self.assertIn("PCB 1, Schaltplan 1", result)
            self.assertIn("Supply voltage?", result)
            self.assertIn("Erzeugungsstand (historisch): ERC 3, DRC 1", result)
            self.assertIn("Aktuelle ERC/DRC-Werte: /erc und /drc", result)

    def test_connection_detail_does_not_include_token(self):
        result = connection_detail("Timed out", "de", {"KICAD_API_TOKEN": "secret"})
        self.assertIn("Timed out", result)
        self.assertNotIn("secret", result)
        self.assertIn("/verbinden", result)


if __name__ == "__main__":
    unittest.main()
