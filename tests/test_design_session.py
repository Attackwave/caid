import tempfile
from pathlib import Path
import unittest

from caid_chat.design_session import DesignSession, parse_board_size
from caid_chat.design_checklist import is_open_answer
from caid_chat.brief_ai import parse_brief_answer
from caid_chat.project_brief import (add_open_question, apply_brief_proposal, brief_hash,
                                     clear_active_design, complete_design, load_brief, model_context,
                                     resolve_open_question,
                                     save_active_design, set_fact, set_requirement)
from caid_chat.requirement_review import mismatches, review_spec


class DesignSessionTests(unittest.TestCase):
    def test_board_dimensions_and_explicit_open_state(self):
        self.assertEqual(parse_board_size("80 × 25 mm"), {"width_mm": 80.0, "height_mm": 25.0})
        self.assertEqual(parse_board_size("80,5 x 25,2 mm"), {"width_mm": 80.5, "height_mm": 25.2})
        self.assertIsNone(parse_board_size("same as original"))
        session = DesignSession.start("ROM adapter", {"document": "a.kicad_pcb", "project_path": "/tmp/demo"})
        self.assertTrue(session.needs_board_size())
        self.assertFalse(session.record_board_size("same as original"))
        self.assertTrue(session.record_board_size("offen"))
        self.assertIsNone(session.board_size)
        self.assertIn("without a board outline", session.model_task())
        self.assertNotIn("board", session.finalize_spec({"board": {"width_mm": 10, "height_mm": 10}}))

    def test_maximum_board_size_allows_smaller_design_and_survives_resume(self):
        snapshot = {"document": "a.kicad_pcb", "project_path": "/tmp/demo"}
        session = DesignSession.start("Module, maximal 60 x 16 mm", snapshot)
        self.assertEqual(session.board_size_mode, "maximum")
        restored = DesignSession.from_record(session.to_record())
        self.assertEqual(restored.board_size_mode, "maximum")
        spec = restored.finalize_spec({"board": {"width_mm": 58, "height_mm": 15}})
        self.assertEqual(spec["board"], {"width_mm": 58, "height_mm": 15})
        brief = {"requirements": {}}
        self.assertEqual(review_spec(brief, spec, restored.board_size, restored.board_size_mode)[0]["status"], "pass")
        spec["board"] = {"width_mm": 15, "height_mm": 58}
        self.assertEqual(review_spec(brief, spec, restored.board_size, restored.board_size_mode)[0]["status"], "pass")
        spec["board"]["width_mm"] = 61
        self.assertEqual(review_spec(brief, spec, restored.board_size, restored.board_size_mode)[0]["status"], "fail")

    def test_board_requirement_updates_routing_target(self):
        with tempfile.TemporaryDirectory() as directory:
            set_requirement(directory, "PCB size / Platinengröße", "Maximal 60 × 16 mm",
                            check={"kind": "board_size", "mode": "maximum",
                                   "width_mm": 60, "height_mm": 16})
            brief = load_brief(directory)
            self.assertEqual(brief["pcb_size_mm"], {"width_mm": 60.0, "height_mm": 16.0})
            self.assertEqual(brief["pcb_size_mode"], "maximum")

    def test_questions_are_answered_in_same_draft_and_restored(self):
        with tempfile.TemporaryDirectory() as directory:
            snapshot = {"document": "board.kicad_pcb", "project_path": directory}
            session = DesignSession.start("Make an adapter", snapshot)
            session.record_board_size("75 x 28 mm")
            session.set_questions(["Which socket orientation?", "Which side for flash?"])
            self.assertTrue(session.answer_question("Pin 1 left"))
            save_active_design(directory, session)
            restored = DesignSession.from_record(load_brief(directory)["active_design"])
            self.assertEqual(restored.questions, ["Which side for flash?"])
            self.assertTrue(restored.answer_question("TOP"))
            self.assertIn("Pin 1 left", restored.model_task())
            self.assertEqual(restored.finalize_spec({"name": "Demo"})["board"],
                             {"width_mm": 75.0, "height_mm": 28.0})
            clear_active_design(directory)
            self.assertIsNone(load_brief(directory)["active_design"])

    def test_project_brief_follows_new_project(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source"
            target = Path(directory) / "draft"
            source.mkdir()
            target.mkdir()
            set_fact(source, "Target", "A500+ Rev 8")
            add_open_question(source, "Which board revision?")
            session = DesignSession.start("ROM adapter", {"document": "a.kicad_pcb", "project_path": str(source)})
            session.record_board_size("offen")
            save_active_design(source, session)
            result = {"name": "ROM", "components": 3, "nets": 2,
                      "erc_errors": 0, "erc_warnings": 0, "drc_errors": 1, "drc_warnings": 0}
            complete_design(source, target, session, result)
            project = load_brief(target)
            self.assertEqual(project["requirements"]["Target"]["value"], "A500+ Rev 8")
            self.assertEqual(project["requirements"]["Target"]["status"], "provided")
            self.assertEqual(project["open_questions"], ["Which board revision?"])
            self.assertEqual(project["pcb_size_status"], "open")
            self.assertEqual(model_context(project)["objective"], "ROM adapter")
            self.assertIsNone(load_brief(source)["active_design"])
            resolve_open_question(target, "Which board revision?")
            self.assertEqual(load_brief(target)["open_questions"], [])

    def test_rom_questions_are_guided_persisted_and_can_be_deferred(self):
        snapshot = {"document": "rom.kicad_pcb", "project_path": "/tmp/rom",
                    "project_brief": {"requirements": {"ROM-Images": {
                        "value": "2 × 512 KiB", "status": "provided"}}}}
        session = DesignSession.start("Amiga Kickstart Adapter", snapshot, "de")
        self.assertEqual(len(session.guided), 3)
        self.assertEqual(session.guided[0]["topic"], "Sockelgeometrie")
        self.assertTrue(is_open_answer("offen"))
        session.record_board_size("offen")
        deferred = session.answer_guided("offen")
        self.assertTrue(deferred["deferred"])
        restored = DesignSession.from_record(session.to_record())
        self.assertEqual(len(restored.guided), 2)
        self.assertIn("Do not infer", restored.model_task())
        self.assertEqual(len(restored.finalize_spec({"name": "Demo"})["design_brief"]["deferred_questions"]), 2)
        based_on_project = DesignSession.start("New module", {**snapshot,
            "project_brief": {"project": "FlashROM42", "requirements": {}}}, "en")
        self.assertEqual(len(based_on_project.guided), 4)

    def test_deferred_guided_question_stays_open_in_new_project(self):
        with tempfile.TemporaryDirectory() as directory:
            source, target = Path(directory) / "source", Path(directory) / "target"
            source.mkdir()
            target.mkdir()
            snapshot = {"document": "rom.kicad_pcb", "project_path": str(source),
                        "project_brief": {"requirements": {}}}
            session = DesignSession.start("Amiga Kickstart", snapshot, "de")
            item = session.guided[0]
            add_open_question(source, item["question"])
            session.answer_guided("offen")
            result = {"name": "ROM", "components": 3, "nets": 2, "erc_errors": 0,
                      "erc_warnings": 0, "drc_errors": 1, "drc_warnings": 0}
            complete_design(source, target, session, result)
            self.assertIn(item["question"], load_brief(target)["open_questions"])
            self.assertEqual(load_brief(target)["decisions"][-1]["status"], "open")

    def test_new_rom_variant_does_not_inherit_other_socket_requirements(self):
        with tempfile.TemporaryDirectory() as directory:
            source, target = Path(directory) / "source", Path(directory) / "target"
            source.mkdir()
            target.mkdir()
            set_fact(source, "Zielsystem", "42-pin A500+")
            snapshot = {"document": "rom.kicad_pcb", "project_path": str(source),
                        "project_brief": {"project": "FlashROM42", "requirements": {
                            "Zielsystem": {"value": "42-pin A500+", "status": "provided"}}}}
            session = DesignSession.start("40-pin Amiga 500 ROM adapter", snapshot, "en")
            self.assertFalse(session.inherit_requirements)
            session.answer_guided("2 × 256 KiB, even/odd words")
            restored = DesignSession.from_record(session.to_record())
            self.assertFalse(restored.inherit_requirements)
            result = {"name": "ROM40", "components": 3, "nets": 2, "erc_errors": 0,
                      "erc_warnings": 0, "drc_errors": 1, "drc_warnings": 0}
            complete_design(source, target, restored, result)
            new_brief = load_brief(target)
            self.assertNotIn("Zielsystem", new_brief["requirements"])
            self.assertEqual(new_brief["requirements"]["ROM-Variante"]["value"], "40-pin")
            self.assertEqual(new_brief["requirements"]["ROM-Images"]["value"],
                             "2 × 256 KiB, even/odd words")
            self.assertEqual(load_brief(source)["requirements"]["Zielsystem"]["value"],
                             "42-pin A500+")

    def test_old_brief_migrates_without_claiming_electrical_verification(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "CAID-Projekt.json").write_text(
                '{"schema_version":1,"project":"old","facts":{"ROM":"2 MiB"}}', encoding="utf-8")
            brief = load_brief(directory)
            self.assertEqual(brief["schema_version"], 2)
            self.assertEqual(brief["requirements"]["ROM"]["source"], "legacy project brief")
            self.assertEqual(brief["requirements"]["ROM"]["status"], "provided")
            self.assertEqual(brief["history"], [])
            set_requirement(directory, "ROM", "4 MiB", status="assumed")
            self.assertEqual(load_brief(directory)["history"][-1]["before"]["value"], "2 MiB")

    def test_requirements_have_provenance_and_check_design_constraints(self):
        with tempfile.TemporaryDirectory() as directory:
            set_requirement(directory, "Flash", "Example 2 MiB", status="sourced",
                            evidence="data sheet page 1")
            with self.assertRaises(ValueError):
                set_requirement(directory, "Flash", "Example 2 MiB", status="verified")
            set_requirement(directory, "PCB size / Platinengröße", "80 × 25 mm",
                            check={"kind": "board_size", "width_mm": 80, "height_mm": 25})
            set_requirement(directory, "PCB side / Seite U1", "TOP",
                            check={"kind": "component_side", "ref": "U1", "side": "TOP"})
            brief = load_brief(directory)
            spec = {"board": {"width_mm": 80, "height_mm": 25},
                    "components": [{"ref": "U1", "side": "BOTTOM"}]}
            rows = review_spec(brief, spec)
            self.assertEqual([(row["topic"], row["status"]) for row in rows],
                             [("Flash", "unchecked"), ("PCB size / Platinengröße", "pass"),
                              ("PCB side / Seite U1", "fail")])
            self.assertEqual(len(mismatches(rows)), 1)

    def test_model_brief_proposal_requires_review_and_rejects_stale_apply(self):
        with tempfile.TemporaryDirectory() as directory:
            set_fact(directory, "Target", "A500")
            before = load_brief(directory)
            result = {"answer": '{"updates":[{"topic":"Target","value":"A500+ Rev. 8","status":"provided","evidence":""}],"open_questions":["Socket height?"]}',
                      "tool_requests": [], "placements": [], "footprint_updates": [],
                      "edit_schematic": False}
            updates, questions = parse_brief_answer(result)
            self.assertEqual(load_brief(directory)["requirements"]["Target"]["value"], "A500")
            apply_brief_proposal(directory, brief_hash(before), updates, questions)
            after = load_brief(directory)
            self.assertEqual(after["requirements"]["Target"]["value"], "A500+ Rev. 8")
            self.assertEqual(after["open_questions"], ["Socket height?"])
            self.assertEqual(after["history"][-2]["before"]["value"], "A500")
            with self.assertRaises(ValueError):
                apply_brief_proposal(directory, brief_hash(before), updates, questions)


if __name__ == "__main__":
    unittest.main()
