import tempfile
import unittest
from pathlib import Path

from caid_chat.footprints import asks_about_footprint, find_footprint, inspect_footprints


class FootprintInspectionTests(unittest.TestCase):
    def test_project_library_and_board_comparison(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            pretty = project / "Local.pretty"
            pretty.mkdir()
            (project / "fp-lib-table").write_text(
                '(fp_lib_table (lib (name "Local")(type "KiCad")'
                '(uri "${KIPRJMOD}/Local.pretty")(options "")(descr "")))',
                encoding="utf-8")
            (pretty / "TSOP-I-48_12x20mm_P0.5mm.kicad_mod").write_text(
                '(footprint "TSOP-I-48_12x20mm_P0.5mm" '
                '(descr "local candidate") '
                '(pad "1" smd rect (at -10 -5) (size 1 0.3)) '
                '(pad "2" smd rect (at -10 -4.5) (size 1 0.3)))',
                encoding="utf-8")
            standard = project / "standard"
            family = standard / "Package_SO.pretty"
            family.mkdir(parents=True)
            (family / "TSOP-I-48_18.4x12mm_P0.5mm.kicad_mod").write_text(
                '(footprint "TSOP-I-48_18.4x12mm_P0.5mm")', encoding="utf-8")
            board = {"project_path": str(project), "footprints": [
                {"ref": "U1", "footprint_id": "Local:TSOP-I-48_12x20mm_P0.5mm"}]}
            schematic = {"components": [{"ref": "U1", "value": "MX29LV160B (TSOP48)",
                "footprint": "Local:TSOP-I-48_12x20mm_P0.5mm"}]}
            result = inspect_footprints(board, schematic, "Finde einen Footprint für 2MB Flash ROM", standard)
            self.assertEqual(result["components"][0]["ref"], "U1")
            self.assertEqual(result["components"][0]["pcb_footprint"], result["components"][0]["schematic_footprint"])
            self.assertEqual(result["exact_footprints"][0]["pad_count"], 2)
            self.assertEqual(result["exact_footprints"][0]["pad_1_xy_mm"], [-10.0, -5.0])
            self.assertEqual(result["similar_footprints"][0]["id"], "Package_SO:TSOP-I-48_18.4x12mm_P0.5mm")
            self.assertFalse(result["physical_part_verified"])
            self.assertIsNotNone(find_footprint(project, "Local:TSOP-I-48_12x20mm_P0.5mm", standard))
            self.assertIsNone(find_footprint(project, "Local:../../secret", standard))

    def test_question_detection(self):
        self.assertTrue(asks_about_footprint("Suche einen geeigneten Footprint"))
        self.assertTrue(asks_about_footprint("Welches Gehäuse passt?"))
        self.assertFalse(asks_about_footprint("Verteile U1 und U2 auf der Platine"))

    def test_explicit_library_id_is_inspected_without_an_existing_component(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            standard = project / "installed"
            library = standard / "Resistor_SMD.pretty"
            library.mkdir(parents=True)
            (library / "R_0805_2012Metric.kicad_mod").write_text(
                '(footprint "R_0805_2012Metric" '
                '(pad "1" smd rect (at -1 0) (size 1 1)) '
                '(pad "2" smd rect (at 1 0) (size 1 1)))', encoding="utf-8")
            board = {"project_path": str(project), "footprints": []}
            schematic = {"components": []}
            for query in ("Resistor_SMD:R_0805_2012Metric",
                          "Add R5 with Device:R and footprint Resistor_SMD:R_0805_2012Metric"):
                with self.subTest(query=query):
                    result = inspect_footprints(board, schematic, query, standard)
                    self.assertEqual([item["id"] for item in result["exact_footprints"]],
                                     ["Resistor_SMD:R_0805_2012Metric"])
                    self.assertEqual(result["exact_footprints"][0]["source"], "installed")


if __name__ == "__main__":
    unittest.main()
