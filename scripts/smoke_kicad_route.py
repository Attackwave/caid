"""Run with KiCad 10 Python: route a real PCB and verify its DRC."""

from collections import Counter
import json
from pathlib import Path
import subprocess
import sys
import tempfile

import pcbnew

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from caid_chat.route_worker import route_one  # noqa: E402
from caid_chat.route_project import route_project  # noqa: E402
from caid_chat.routing import default_contract, set_layers, set_limits  # noqa: E402


def drc(pcb, output):
    command = [str(Path(sys.executable).parent / "kicad-cli.exe"), "pcb", "drc",
               "--format", "json", "--output", str(output), str(pcb)]
    subprocess.run(command, check=False, capture_output=True, text=True, timeout=90)
    return json.loads(output.read_text(encoding="utf-8-sig"))


def build_board(path, blocking_track=False, layers=2, three_pads=False):
    board = pcbnew.BOARD()
    board.SetCopperLayerCount(layers)
    net = pcbnew.NETINFO_ITEM(board, "LINK")
    board.Add(net)
    library = Path(sys.executable).parent.parent / "share/kicad/footprints/Resistor_SMD.pretty"
    positions = [(30, 30), (60, 30)] + ([(45, 34)] if three_pads else [])
    for index, (x, y) in enumerate(positions, 1):
        footprint = pcbnew.FootprintLoad(str(library), "R_0603_1608Metric")
        footprint.SetReference(f"R{index}")
        footprint.SetValue("10k")
        footprint.SetPosition(pcbnew.VECTOR2I(pcbnew.FromMM(x), pcbnew.FromMM(y)))
        board.Add(footprint)
        next(pad for pad in footprint.Pads() if pad.GetNumber() == "1").SetNet(net)
    outline = pcbnew.PCB_SHAPE(board)
    outline.SetShape(pcbnew.SHAPE_T_RECT)
    outline.SetLayer(pcbnew.Edge_Cuts)
    outline.SetStart(pcbnew.VECTOR2I(pcbnew.FromMM(20), pcbnew.FromMM(20)))
    outline.SetEnd(pcbnew.VECTOR2I(pcbnew.FromMM(70), pcbnew.FromMM(40)))
    board.Add(outline)
    if blocking_track:
        blocker_net = pcbnew.NETINFO_ITEM(board, "OBSTACLE")
        board.Add(blocker_net)
        blocker = pcbnew.PCB_TRACK(board)
        blocker.SetStart(pcbnew.VECTOR2I(pcbnew.FromMM(45), pcbnew.FromMM(20.6)))
        blocker.SetEnd(pcbnew.VECTOR2I(pcbnew.FromMM(45), pcbnew.FromMM(39.4)))
        blocker.SetWidth(pcbnew.FromMM(0.2))
        blocker.SetLayer(pcbnew.F_Cu)
        blocker.SetNet(blocker_net)
        board.Add(blocker)
    pcbnew.SaveBoard(str(path), board)


def main():
    with tempfile.TemporaryDirectory(prefix="caid-route-smoke-") as directory:
        root = Path(directory)
        source, destination = root / "before.kicad_pcb", root / "after.kicad_pcb"
        build_board(source)
        before = drc(source, root / "before.json")
        contract = set_limits(set_layers(default_contract(), 1), ["0.2", "0.2", "0.25"])
        routed = route_one(source, destination, "LINK", contract)
        after = drc(destination, root / "after.json")
        old_errors = Counter((item.get("type"), item.get("description"))
                             for item in before.get("violations", []) if item.get("severity") == "error")
        new_errors = Counter((item.get("type"), item.get("description"))
                             for item in after.get("violations", []) if item.get("severity") == "error")
        assert not new_errors - old_errors, (before.get("violations"), after.get("violations"))
        assert len(after.get("unconnected_items", [])) < len(before.get("unconnected_items", []))
        assert routed["segments"] > 0 and routed["vias"] == 0
        reviewed = route_project(root, source.name, contract, "LINK")
        assert reviewed["unconnected_after"] == 0
        assert len(reviewed["accepted"]) == 1
        assert (Path(reviewed["directory"]) / source.name).is_file()
        layered = root / "layered.kicad_pcb"
        build_board(layered, blocking_track=True)
        layered_contract = set_limits(set_layers(default_contract(), 2),
                                      ["0.2", "0.2", "0.6", "0.3", "0.25"])
        layered_result = route_project(root, layered.name, layered_contract, "LINK")
        assert layered_result["accepted"][0]["vias"] >= 2, layered_result
        four_layer = root / "four_layer.kicad_pcb"
        build_board(four_layer, blocking_track=True, layers=4)
        four_contract = set_limits(set_layers(default_contract(), 4),
                                   ["0.2", "0.2", "0.6", "0.3", "0.25"])
        four_result = route_project(root, four_layer.name, four_contract, "LINK")
        assert four_result["accepted"][0]["vias"] >= 2, four_result
        three_pad = root / "three_pad.kicad_pcb"
        build_board(three_pad, three_pads=True)
        three_result = route_project(root, three_pad.name, contract, "LINK")
        assert three_result["accepted"][0]["pads"] == 3
        assert three_result["unconnected_after"] == 0
        print(f"KiCad route smoke OK: {routed['segments']} track segments, "
              f"{len(before['unconnected_items'])} to {len(after['unconnected_items'])} open connections; "
              f"two-layer route with {layered_result['accepted'][0]['vias']} vias; "
              f"four-layer route with {four_result['accepted'][0]['vias']} vias; "
              "three-pad net connected")


if __name__ == "__main__":
    main()
