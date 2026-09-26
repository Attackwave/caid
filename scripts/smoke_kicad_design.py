"""Run with KiCad 10's Python to verify schematic and PCB generation end to end."""

from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "caid_chat"))

from circuit_design import stage_new_design  # noqa: E402
from design_session import DesignSession  # noqa: E402
from requirement_review import review_spec  # noqa: E402


def main():
    spec = {
        "name": "CAIDSmoke",
        "components": [
            {"ref": "R1", "symbol": "Device:R", "value": "10k",
             "footprint": "Resistor_SMD:R_0603_1608Metric",
             "pcb_x_mm": 30, "pcb_y_mm": 30, "side": "TOP"},
            {"ref": "R2", "symbol": "Device:R", "value": "10k",
             "footprint": "Resistor_SMD:R_0603_1608Metric",
             "pcb_x_mm": 60, "pcb_y_mm": 30, "side": "BOTTOM"},
        ],
        "nets": [{"name": "LINK", "nodes": [{"ref": "R1", "pin": "2"},
                                             {"ref": "R2", "pin": "1"}]}],
        "no_connects": [{"ref": "R1", "pin": "1"}, {"ref": "R2", "pin": "2"}],
        "board": {"width_mm": 80, "height_mm": 25},
    }
    with tempfile.TemporaryDirectory(prefix="caid-kicad-smoke-") as directory:
        result = stage_new_design(spec, directory)
        target = Path(result["directory"])
        assert (target / "CAIDSmoke.kicad_sch").is_file()
        assert (target / "CAIDSmoke.kicad_pcb").is_file()
        assert "Saved PCB readback: 2 footprints" in (target / "CAID-REVIEW.txt").read_text()
        assert result["drc_errors"] == 0
        session = DesignSession.start("Maximum 60 x 16 mm", {
            "document": "source.kicad_pcb", "project_path": directory})
        smaller = session.finalize_spec({**spec, "name": "CAIDSmokeMax",
                                         "board": {"width_mm": 58, "height_mm": 15}})
        assert review_spec({"requirements": {}}, smaller, session.board_size,
                           session.board_size_mode)[0]["status"] == "pass"
        smaller_result = stage_new_design(smaller, directory)
        assert smaller_result["drc_errors"] == 0
        print("KiCad design smoke OK: 80 × 25 mm exact and 58 × 15 mm under a 60 × 16 mm maximum")


if __name__ == "__main__":
    main()
