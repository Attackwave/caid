"""Make a clearly provisional 60 x 16 mm layout study with KiCad 10 Python.

The custom contact footprint is only a space claim for a future formed pin
carrier. It must never be treated as a purchasable or verified connector.
"""

import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "caid_chat"))
from circuit_design import stage_new_design  # noqa: E402


FOOTPRINT = "ROM42_OffsetCarrier42_Provisional"


def footprint_text():
    lines = [f'(footprint "{FOOTPRINT}"',
             '\t(version 20260206)',
             '\t(generator "CAID layout study")',
             '\t(layer "F.Cu")',
             '\t(descr "PROVISIONAL: 42 solder pads for a separately engineered offset pin carrier; 60x16 mm PCB study only")',
             '\t(tags "PROVISIONAL ROM42 carrier")',
             '\t(property "Reference" "REF**" (at 0 -5 0) (layer "F.SilkS")',
             '\t\t(effects (font (size 1 1) (thickness 0.15))))',
             f'\t(property "Value" "{FOOTPRINT}" (at 0 5 0) (layer "F.Fab")',
             '\t\t(effects (font (size 1 1) (thickness 0.15))))',
             '\t(attr smd)']
    # 21 contacts per row, 2.54 mm pitch. Pad centres are 14.2 mm apart.
    # Mating-pin centres are 15.24 mm apart: a carrier must provide a
    # 0.52 mm outward lateral offset on each row. No pin part is selected.
    for index in range(21):
        x = -25.4 + index * 2.54
        for number, y in ((index + 1, -7.1), (42 - index, 7.1)):
            lines.append(f'\t(pad "{number}" smd rect (at {x:.2f} {y:.2f}) '
                         '(size 1.6 0.8) (layers "F.Cu" "F.Paste" "F.Mask"))')
    for y in (-7.62, 7.62):
        lines.append(f'\t(fp_line (start -25.4 {y:.2f}) (end 25.4 {y:.2f}) '
                     '(stroke (width 0.1) (type dash)) (layer "Dwgs.User"))')
    lines.append('\t(fp_text user "PIN CARRIER NOT ENGINEERED" (at 0 0 0) (layer "F.Fab") '
                 '(effects (font (size 0.8 0.8) (thickness 0.12))))')
    lines.append(')')
    return '\n'.join(lines) + '\n'


def prepare_spec(project):
    spec = json.loads((project / "design.json").read_text(encoding="utf-8"))
    spec["name"] = "FlashROM42_60x16_LayoutStudy_v2"
    spec["board"] = {"width_mm": 60, "height_mm": 16}
    spec["design_brief"] = {
        "status": "mechanical layout study only",
        "user_envelope_mm": {"length": 60, "width": 16, "height_target": 10,
                             "height_absolute_max": 11,
                             "height_reference": "top of motherboard ROM socket"},
        "contact_carrier": "PROVISIONAL 42 SMT pads; mating pins need 0.52 mm outward offset per row",
        "programmer_connector": "existing 50-way 0.5 mm FFC kept for space study",
    }
    places = {
        "J1": (50, 28, "BOTTOM"), "U1": (32, 28, "TOP"),
        "J2": (63.7, 24.5, "TOP"), "SW1": (53.5, 32.5, "TOP"),
        "D1": (71, 23.2, "BOTTOM"), "D2": (63.5, 32.5, "TOP"),
        "U2": (25, 28, "BOTTOM"), "U3": (34, 28, "BOTTOM"),
        "U4": (43, 28, "BOTTOM"), "U5": (52, 28, "BOTTOM"),
        "U6": (61, 28, "BOTTOM"), "U8": (71, 28, "BOTTOM"),
        "U7": (71, 32.8, "BOTTOM"), "U9": (70.5, 32.5, "TOP"),
        "U10": (75.5, 32.5, "TOP"),
    }
    small = [f"C{number}" for number in range(1, 14)] + [f"R{number}" for number in range(1, 7)]
    for index, ref in enumerate(small):
        row = 0 if index < 12 else 1
        column = index if row == 0 else index - 12
        places[ref] = (23.7 + 3.4 * column, 22.2 if row == 0 else 33.8, "BOTTOM")
    for item in spec["components"]:
        x, y, side = places[item["ref"]]
        item["pcb_x_mm"], item["pcb_y_mm"], item["side"] = x, y, side
        if item["ref"] == "J1":
            item["footprint"] = "ROM42:" + FOOTPRINT
            item["value"] = "PROVISIONAL offset pin carrier 42"
    return spec


def main():
    project = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "tmp/hardware/FlashROM42_Prototype"
    footprint = project / "ROM42.pretty" / (FOOTPRINT + ".kicad_mod")
    footprint.write_text(footprint_text(), encoding="utf-8")
    spec = prepare_spec(project)
    result = stage_new_design(spec, project)
    target = Path(result["directory"])
    import pcbnew
    pcb = target / (spec["name"] + ".kicad_pcb")
    board = pcbnew.LoadBoard(str(pcb))
    for item in board.GetFootprints():
        item.Reference().SetVisible(False)
    pcbnew.SaveBoard(str(pcb), board)
    drc_file = target / "LAYOUT-DRC.json"
    completed = subprocess.run([str(Path(sys.executable).parent / "kicad-cli.exe"),
                                "pcb", "drc", "--format", "json", "--schematic-parity",
                                "--output", str(drc_file), str(pcb)],
                               capture_output=True, text=True, timeout=120)
    if not drc_file.is_file():
        raise RuntimeError(completed.stderr or completed.stdout)
    drc = json.loads(drc_file.read_text(encoding="utf-8-sig"))
    errors = sum(item.get("severity") == "error" for item in drc.get("violations", []))
    warnings = sum(item.get("severity") == "warning" for item in drc.get("violations", []))
    review_file = target / "CAID-REVIEW.txt"
    review = review_file.read_text(encoding="utf-8")
    review += (f"\nFinal layout-study DRC after hiding crowded silkscreen references: "
               f"{errors} errors, {warnings} warnings, "
               f"{len(drc.get('unconnected_items', []))} open connections; "
               f"{len(drc.get('schematic_parity', []))} schematic/PCB differences.\n")
    review_file.write_text(review, encoding="utf-8")
    (target / "MECHANICAL-BLOCKER.txt").write_text(
        "LAYOUT STUDY ONLY - DO NOT FABRICATE OR ASSEMBLE.\n"
        "The 60 x 16 mm PCB outline follows the user's maximum. The J1 footprint is a"
        " space claim for a future custom pin carrier, not a real connector.\n"
        "PCB solder-pad row pitch: 14.20 mm. Motherboard mating-pin row pitch:"
        " 15.24 mm. Required outward offset: 0.52 mm per row.\n"
        "Pin shape, support, mating depth, strength, solder method and insertion"
        " clearance require a mechanical part and physical measurement.\n"
        "Height target: 10 mm; absolute maximum: 11 mm above the ROM socket top."
        " Stack height and component clearance are not yet verified.\n",
        encoding="utf-8")
    print(json.dumps({**result, "final_drc_errors": errors, "final_drc_warnings": warnings},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
