import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
from xml.etree import ElementTree

from caid_chat.circuit_design import (_verify_board_manifest, _verify_netlist,
                                      render_schematic, validate_design)
from caid_chat.design_ai import ask_design, parse_design_answer


class CircuitDesignTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        symbols = self.root / "symbols"
        footprints = self.root / "footprints" / "Test.pretty"
        symbols.mkdir()
        footprints.mkdir(parents=True)
        (symbols / "Test.kicad_sym").write_text('''(kicad_symbol_lib (version 20260101)
 (symbol "Part" (symbol "Part_1_1"
  (pin passive line (at -2.54 0 0) (length 2.54) (name "A") (number "1"))
  (pin passive line (at 2.54 0 180) (length 2.54) (name "B") (number "2")))))''')
        (footprints / "Part.kicad_mod").write_text('''(footprint "Part"
 (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu"))
 (pad "2" smd rect (at 2 0) (size 1 1) (layers "F.Cu")))''')
        self.symbols = symbols
        self.footprints = footprints.parent
        self.spec = {"name": "Demo", "components": [
            {"ref": "U1", "symbol": "Test:Part", "value": "A", "footprint": "Test:Part"},
            {"ref": "U2", "symbol": "Test:Part", "value": "B", "footprint": "Test:Part"}],
            "nets": [{"name": "LINK", "nodes": [{"ref": "U1", "pin": "2"}, {"ref": "U2", "pin": "1"}]}]}

    def test_validates_and_renders_real_pins(self):
        parts, connections = validate_design(self.spec, self.root,
                                             symbol_root=self.symbols, footprint_root=self.footprints)
        source = render_schematic(self.spec, parts, connections)
        self.assertIn('(label "LINK"', source)
        self.assertEqual(connections[("U1", "2")], "LINK")

    def test_rejects_missing_footprint_pad(self):
        (self.footprints / "Test.pretty" / "Part.kicad_mod").write_text('''(footprint "Part" (pad "1" smd rect))''')
        with self.assertRaisesRegex(ValueError, "lacks symbol pin numbers"):
            validate_design(self.spec, self.root, symbol_root=self.symbols,
                            footprint_root=self.footprints)

    def test_project_symbol_library_is_resolved(self):
        (self.root / "Local.kicad_sym").write_text(
            (self.symbols / "Test.kicad_sym").read_text().replace('"Part"', '"Device"')
            .replace('"Part_1_1"', '"Device_1_1"'))
        (self.root / "sym-lib-table").write_text(
            '(sym_lib_table (lib (name "Local")(type "KiCad")'
            '(uri "${KIPRJMOD}/Local.kicad_sym")(options "")(descr "")))')
        self.spec["components"][0]["symbol"] = "Local:Device"
        parts, _ = validate_design(self.spec, self.root,
                                   symbol_root=self.symbols, footprint_root=self.footprints)
        self.assertEqual(parts["U1"]["symbol_file"], str(self.root / "Local.kicad_sym"))
        self.assertIn('"Local:Device"', parts["U1"]["embedded"])

    def test_inherited_symbol_uses_parent_pins_and_child_properties(self):
        (self.symbols / "Test.kicad_sym").write_text('''(kicad_symbol_lib (version 20260101)
 (symbol "Base" (property "Value" "Base") (symbol "Base_1_1"
  (pin passive line (at -2.54 0 0) (length 2.54) (name "A") (number "1"))
  (pin passive line (at 2.54 0 180) (length 2.54) (name "B") (number "2"))))
 (symbol "Child" (extends "Base") (property "Value" "Child")))''')
        for component in self.spec["components"]:
            component["symbol"] = "Test:Child"
        parts, connections = validate_design(self.spec, self.root,
                                             symbol_root=self.symbols, footprint_root=self.footprints)
        self.assertEqual(set(parts["U1"]["pins"]), {"1", "2"})
        self.assertIn('(symbol "Child_1_1"', parts["U1"]["embedded"])
        self.assertIn('(property "Value" "Child")', parts["U1"]["embedded"])
        self.assertEqual(connections[("U1", "2")], "LINK")

    def test_multi_unit_symbol_keeps_power_unit_pins(self):
        (self.symbols / "Test.kicad_sym").write_text('''(kicad_symbol_lib (version 20260101)
 (symbol "Base" (symbol "Base_1_1"
  (pin passive line (at -2.54 0 0) (length 2.54) (name "A") (number "1")))
  (symbol "Base_2_1"
  (pin passive line (at 2.54 0 180) (length 2.54) (name "B") (number "2")))
  (symbol "Base_3_0"
  (pin power_in line (at 0 2.54 270) (length 2.54) (name "VCC") (number "3")))
  (symbol "Base_3_1" (rectangle (start -1 -1) (end 1 1))))
 (symbol "Child" (extends "Base") (property "Value" "Child")))''')
        (self.footprints / "Test.pretty" / "Part.kicad_mod").write_text('''(footprint "Part"
 (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu"))
 (pad "2" smd rect (at 2 0) (size 1 1) (layers "F.Cu"))
 (pad "3" smd rect (at 4 0) (size 1 1) (layers "F.Cu")))''')
        for component in self.spec["components"]:
            component["symbol"] = "Test:Child"
        parts, connections = validate_design(self.spec, self.root,
                                             symbol_root=self.symbols, footprint_root=self.footprints)
        self.assertEqual(parts["U1"]["units"], [1, 2, 3])
        self.assertEqual(parts["U1"]["pin_units"]["3"], 3)
        source = render_schematic(self.spec, parts, connections)
        self.assertIn('(unit 3)', source)
        self.assertIn('(symbol "Child_3_0"', source)

    def test_no_connect_marker_must_not_hide_a_real_net(self):
        self.spec["no_connects"] = [{"ref": "U1", "pin": "1"}]
        parts, connections = validate_design(self.spec, self.root,
                                             symbol_root=self.symbols, footprint_root=self.footprints)
        self.assertIn("(no_connect", render_schematic(self.spec, parts, connections))
        self.spec["no_connects"] = [{"ref": "U1", "pin": "2"}]
        with self.assertRaisesRegex(ValueError, "connected no-connect pin"):
            validate_design(self.spec, self.root,
                            symbol_root=self.symbols, footprint_root=self.footprints)

    def test_rejects_netlist_with_extra_connection(self):
        parts, connections = validate_design(self.spec, self.root,
                                             symbol_root=self.symbols, footprint_root=self.footprints)
        xml = ElementTree.fromstring(f'''<export><components>
          <comp ref="U1"><footprint>Test:Part</footprint><tstamps>{parts["U1"]["uuid"]}</tstamps></comp>
          <comp ref="U2"><footprint>Test:Part</footprint><tstamps>{parts["U2"]["uuid"]}</tstamps></comp>
          </components><nets><net name="/LINK"><node ref="U1" pin="2"/>
          <node ref="U2" pin="1"/><node ref="U1" pin="1"/></net></nets></export>''')
        with self.assertRaisesRegex(ValueError, "unintended pins"):
            _verify_netlist(xml, parts, connections)

    def test_model_must_request_missing_information_or_return_spec(self):
        base = {"answer": '{"status":"needs_information","questions":["Which board revision?"]}',
                "edit_schematic": "", "placements": [], "tool_requests": [], "footprint_updates": []}
        self.assertEqual(parse_design_answer(base), (None, ["Which board revision?"]))
        with self.assertRaisesRegex(ValueError, "unrelated changes"):
            parse_design_answer({**base, "placements": [{"ref": "U1"}]})

    def test_saved_board_facts_must_match_requested_size_and_sides(self):
        resolved = {"U1": {"side": "TOP", "pcb_x": 30, "pcb_y": 35}}
        manifest = {"components": {"U1": {"side": "TOP", "x_mm": 30, "y_mm": 35,
                                         "bounds_mm": [28, 33, 32, 37]}},
                    "outline": {"width_mm": 80, "height_mm": 25}, "edge_items": 1}
        _verify_board_manifest(manifest, resolved, {"width_mm": 80, "height_mm": 25})
        manifest["components"]["U1"]["side"] = "BOTTOM"
        with self.assertRaisesRegex(ValueError, "placement"):
            _verify_board_manifest(manifest, resolved, {"width_mm": 80, "height_mm": 25})
        manifest["components"]["U1"]["side"] = "TOP"
        manifest["outline"]["width_mm"] = 90
        with self.assertRaisesRegex(ValueError, "outline"):
            _verify_board_manifest(manifest, resolved, {"width_mm": 80, "height_mm": 25})
        manifest["outline"]["width_mm"] = 80
        manifest["components"]["U1"]["bounds_mm"][0] = 19
        with self.assertRaisesRegex(ValueError, "extends outside"):
            _verify_board_manifest(manifest, resolved, {"width_mm": 80, "height_mm": 25})

    def test_amiga_design_receives_user_pin_reference(self):
        response = {"answer": '{"status":"needs_information","questions":["Which flash part?"]}',
                    "edit_schematic": False, "placements": [], "tool_requests": [], "footprint_updates": []}
        with patch("caid_chat.design_ai.ask_provider", return_value=response) as provider:
            spec, questions = ask_design("ollama", "", "http://127.0.0.1:11434", "local",
                                         "Amiga 500+ Kickstart adapter", {"document": "test.kicad_pcb"})
        self.assertIsNone(spec)
        self.assertEqual(questions, ["Which flash part?"])
        prompt = provider.call_args.args[4][0]["content"]
        self.assertIn('"42": "A19"', prompt)
        self.assertIn("CAID_NEW_DESIGN_MODE", prompt)

    def test_40_pin_task_does_not_receive_42_pin_reference(self):
        response = {"answer": '{"status":"needs_information","questions":["Which socket pinout?"]}',
                    "edit_schematic": False, "placements": [], "tool_requests": [], "footprint_updates": []}
        with patch("caid_chat.design_ai.ask_provider", return_value=response) as provider:
            ask_design("ollama", "", "http://127.0.0.1:11434", "local",
                       "40-pin Amiga 500 ROM adapter", {"document": "test.kicad_pcb",
                                                       "project_brief": {"project": "FlashROM42"}})
        prompt = provider.call_args.args[4][0]["content"]
        self.assertNotIn('"42": "A19"', prompt)


if __name__ == "__main__":
    unittest.main()
