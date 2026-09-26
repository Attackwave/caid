"""Run the complete new-design path against an installed KiCad 10 on Windows."""

import tempfile
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from caid_chat.circuit_design import stage_new_design
from caid_chat.diagnostics import _cli_path
from caid_chat.process import run_command


def main():
    import pcbnew

    spec = {"name": "CAIDDesignSmoke", "components": [
        {"ref": "R1", "symbol": "Device:R", "footprint": "Resistor_SMD:R_0603_1608Metric", "value": "10k"},
        {"ref": "R2", "symbol": "Device:R", "footprint": "Resistor_SMD:R_0603_1608Metric",
         "value": "10k", "side": "BOTTOM"}],
        "nets": [{"name": "LINK", "nodes": [{"ref": "R1", "pin": "2"}, {"ref": "R2", "pin": "1"}]}],
        "board": {"width_mm": 130, "height_mm": 65}}
    with tempfile.TemporaryDirectory(prefix="caid-design-smoke-") as parent:
        result = stage_new_design(spec, parent)
        project = Path(result["directory"])
        assert (project / "CAIDDesignSmoke.kicad_sch").is_file()
        assert (project / "CAIDDesignSmoke.kicad_pro").is_file()
        board = pcbnew.LoadBoard(str(project / "CAIDDesignSmoke.kicad_pcb"))
        footprints = {item.GetReference(): item for item in board.GetFootprints()}
        assert set(footprints) == {"R1", "R2"}
        assert footprints["R1"].GetLayerName() == "F.Cu"
        assert footprints["R2"].GetLayerName() == "B.Cu"
        assert all(item.GetFPIDAsString() == "Resistor_SMD:R_0603_1608Metric"
                   for item in footprints.values())
        assert all(item.GetPath().AsString().startswith("/") for item in footprints.values())
        linked = {(fp.GetReference(), pad.GetNumber()) for fp in footprints.values()
                  for pad in fp.Pads() if pad.GetNetname() == "/LINK"}
        assert linked == {("R1", "2"), ("R2", "1")}, linked
        assert result["nets"] == 1 and result["components"] == 2
        report = project / "drc.json"
        run_command([_cli_path(), "pcb", "drc", "--format", "json", "--schematic-parity",
                     "--output", str(report), str(project / "CAIDDesignSmoke.kicad_pcb")], timeout=90)
        findings = json.loads(report.read_text(encoding="utf-8-sig"))
        assert not findings.get("schematic_parity"), findings.get("schematic_parity")
    print("Windows KiCad design smoke: OK")


if __name__ == "__main__":
    main()
