"""Run CAID's saved-file and routing smoke checks with KiCad 10 Python on Windows.

Usage: "C:\\Program Files\\KiCad\\10.0\\bin\\python.exe" scripts\\check_windows_integration.py
"""

import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile

if sys.platform != "win32":
    raise SystemExit("Run this script with KiCad's bundled Python on Windows")

import pcbnew

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from caid_chat.route_project import route_project
from caid_chat.circuit_design import stage_new_design
from caid_chat.project_recovery import scan_stages
from caid_chat.routing import default_contract, set_layers, set_limits
from caid_chat.schematic import stage_field_updates


KICAD_ROOT = Path(sys.executable).resolve().parent.parent
DEMO = KICAD_ROOT / "share/kicad/demos/ecc83"
FOOTPRINT_LIBRARY = KICAD_ROOT / "share/kicad/footprints/TestPoint.pretty"


def check_field_preview(parent):
    project = parent / "field-preview"
    project.mkdir()
    for name in ("ecc83-pp.kicad_sch", "ecc83-pp.kicad_pcb", "ecc83-pp.kicad_pro",
                 "ecc83-pp.kicad_sym", "sym-lib-table", "fp-lib-table"):
        shutil.copy2(DEMO / name, project / name)
    shutil.copytree(DEMO / "footprints.pretty", project / "footprints.pretty")
    source = project / "ecc83-pp.kicad_sch"
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    staged = stage_field_updates(
        {"project_path": str(project), "document": "ecc83-pp.kicad_pcb"},
        [{"ref": "R1", "field": "Value", "value": "12k"}])
    try:
        assert staged.erc_after == staged.erc_before == (0, 0)
        assert "R1.Value" in staged.agent_answer
        assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    finally:
        staged.cleanup()
    print("Field preview: ERC unchanged; source schematic preserved")


def check_new_design(parent):
    project = parent / "design-draft"
    project.mkdir()
    spec = {"name": "RecoverySmoke", "board": {"width_mm": 80, "height_mm": 30},
            "components": [
                {"ref": ref, "symbol": "Device:R", "value": "10k",
                 "footprint": "Resistor_SMD:R_0805_2012Metric",
                 "pcb_x_mm": x, "pcb_y_mm": 35}
                for ref, x in (("R1", 35), ("R2", 70))],
            "nets": [{"name": "LINK", "nodes": [{"ref": "R1", "pin": "2"},
                                                 {"ref": "R2", "pin": "1"}]}]}
    result = stage_new_design(spec, project)
    output = Path(result["directory"])
    assert output.joinpath("RecoverySmoke.kicad_sch").is_file()
    assert output.joinpath("RecoverySmoke.kicad_pcb").is_file()
    assert result["drc_errors"] == 0
    assert scan_stages(project) == []
    print("New design: saved schematic and PCB; no staging copy left behind")


def _point(x, y):
    return pcbnew.VECTOR2I(pcbnew.FromMM(x), pcbnew.FromMM(y))


def check_route_continuation(parent):
    project = parent / "route-passes"
    project.mkdir()
    board = pcbnew.BOARD()
    nets = [pcbnew.NETINFO_ITEM(board, name) for name in ("TEST", "TEST2")]
    for net in nets:
        board.Add(net)
    for number, (x, y) in enumerate(((240, 240), (245, 245), (260, 260), (265, 265)), 1):
        footprint = pcbnew.FootprintLoad(str(FOOTPRINT_LIBRARY), "TestPoint_Pad_1.0x1.0mm")
        assert footprint is not None
        footprint.SetReference(f"TP{number}")
        footprint.SetPosition(_point(x, y))
        board.Add(footprint)
        for pad in footprint.Pads():
            pad.SetNet(nets[0 if number <= 2 else 1])
    for a, b in (((0, 0), (500, 0)), ((500, 0), (500, 500)),
                 ((500, 500), (0, 500)), ((0, 500), (0, 0))):
        edge = pcbnew.PCB_SHAPE(board)
        edge.SetShape(pcbnew.SHAPE_T_SEGMENT)
        edge.SetStart(_point(*a))
        edge.SetEnd(_point(*b))
        edge.SetLayer(pcbnew.Edge_Cuts)
        board.Add(edge)
    board_file = project / "source.kicad_pcb"
    pcbnew.SaveBoard(str(board_file), board)
    source_hash = hashlib.sha256(board_file.read_bytes()).hexdigest()
    contract = set_limits(set_layers(default_contract(), 2),
                          ["0.2", "0.2", "0.6", "0.3", "0.5"])
    first = route_project(project, board_file.name, contract, max_nets=1)
    second = route_project(first["directory"], board_file.name, contract, max_nets=1)
    assert (first["unconnected_before"], first["unconnected_after"]) == (2, 1)
    assert (second["unconnected_before"], second["unconnected_after"]) == (1, 0)
    assert first["unattempted"] == 1 and second["unattempted"] == 0
    assert Path(first["directory"]).parent == Path(second["directory"]).parent
    assert hashlib.sha256(board_file.read_bytes()).hexdigest() == source_hash
    for result in (first, second):
        report = json.loads((Path(result["directory"]) / "CAID-ROUTING.json").read_text())
        assert report["accepted"] and all("destination" not in row for row in report["accepted"])
    print("Route continuation: open connections 2 -> 1 -> 0; source board preserved")


def main():
    if not DEMO.is_dir() or not FOOTPRINT_LIBRARY.is_dir():
        raise SystemExit("KiCad 10 demo or TestPoint library not found beside the selected Python")
    with tempfile.TemporaryDirectory(prefix="caid-integration-") as directory:
        parent = Path(directory)
        check_field_preview(parent)
        check_new_design(parent)
        check_route_continuation(parent)
    print("KiCad 10 integration checks passed")


if __name__ == "__main__":
    main()
