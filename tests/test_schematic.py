import hashlib
from pathlib import Path
import tempfile
import unittest

from caid_chat.schematic import (StagedSchematic, _schematic_notes,
                                 _validate_circuit_candidate, apply_schematic_edit)


class SchematicApplyTests(unittest.TestCase):
    def test_root_sheet_notes_are_read_without_symbol_library_text(self):
        source = '''(kicad_sch
          (lib_symbols (symbol "Lib:Part" (text "inside library")))
          (text "ELEKTRISCHE DATEN OFFEN\\nKeine Pinverbindungen eingetragen"
            (at 10 20 0) (uuid "a"))
          (text_box "Top und Bottom möglich" (at 30 40) (uuid "b"))
        )'''
        notes, count, truncated = _schematic_notes(source)
        self.assertEqual(notes, ["ELEKTRISCHE DATEN OFFEN\nKeine Pinverbindungen eingetragen",
                                 "Top und Bottom möglich"])
        self.assertEqual(count, 2)
        self.assertFalse(truncated)

    def test_text_only_proposal_cannot_replace_a_circuit(self):
        before = {"component_count": 22, "net_count": 98}
        after = {"component_count": 0, "net_count": 0}
        with self.assertRaisesRegex(ValueError, "keine platzierten Symbole"):
            _validate_circuit_candidate(before, after, "de")
        with self.assertRaisesRegex(ValueError, "meisten Schaltplanbauteile"):
            _validate_circuit_candidate(before, {"component_count": 2, "net_count": 10}, "de")
        with self.assertRaisesRegex(ValueError, "keine verbundenen Netze"):
            _validate_circuit_candidate({"component_count": 0, "net_count": 0},
                                        {"component_count": 3, "net_count": 0}, "de")

    def test_lock_prevents_overwrite_then_creates_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "design.kicad_sch"
            source.write_text("original", encoding="utf-8")
            stage_dir = root / "stage"
            stage_dir.mkdir()
            candidate = stage_dir / source.name
            candidate.write_text("candidate", encoding="utf-8")
            staged = StagedSchematic(
                source, candidate, stage_dir,
                hashlib.sha256(b"original").hexdigest(),
                "", "", (0, 0), (0, 0))
            lock = root / "~design.kicad_sch.lck"
            lock.write_text("locked", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "geöffnet"):
                apply_schematic_edit(staged, "de")
            self.assertEqual(source.read_text(encoding="utf-8"), "original")
            lock.unlink()
            backup = apply_schematic_edit(staged)
            self.assertEqual(source.read_text(encoding="utf-8"), "candidate")
            self.assertEqual(backup.read_text(encoding="utf-8"), "original")
            self.assertFalse(stage_dir.exists())

    def test_changed_source_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "design.kicad_sch"
            source.write_text("changed", encoding="utf-8")
            stage_dir = root / "stage"
            stage_dir.mkdir()
            candidate = stage_dir / source.name
            candidate.write_text("candidate", encoding="utf-8")
            staged = StagedSchematic(
                source, candidate, stage_dir,
                hashlib.sha256(b"original").hexdigest(),
                "", "", (0, 0), (0, 0))
            with self.assertRaisesRegex(RuntimeError, "seit der Vorschau geändert"):
                apply_schematic_edit(staged, "de")
            self.assertEqual(source.read_text(encoding="utf-8"), "changed")


if __name__ == "__main__":
    unittest.main()
