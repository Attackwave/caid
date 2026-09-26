"""Write validated routing requirements into KiCad 10 project design settings."""

import json
from pathlib import Path

try:
    from .routing import validate_contract
except ImportError:
    from routing import validate_contract


def synchronize_project_rules(project_file, contract, *, preserve_stricter=False):
    """Update only routing-related settings in an isolated project copy."""
    validate_contract(contract)
    limits = contract["limits_mm"]
    layers = contract["requested_layers"]
    required = ("track_width_mm", "clearance_mm", "edge_clearance_mm")
    if layers != 1:
        required += ("via_diameter_mm", "via_drill_mm")
    missing = [key for key in required if limits[key] is None]
    if layers is None or missing:
        raise ValueError("Routing layer count and all required limits must be set before syncing KiCad rules")
    path = Path(project_file)
    project = json.loads(path.read_text(encoding="utf-8-sig")) if path.is_file() else {}
    if not isinstance(project, dict):
        raise ValueError("Invalid KiCad project file")
    settings = project.setdefault("board", {}).setdefault("design_settings", {})
    rules = settings.setdefault("rules", {})
    proposed = {"min_clearance": limits["clearance_mm"],
                "min_copper_edge_clearance": limits["edge_clearance_mm"],
                "min_track_width": limits["track_width_mm"]}
    if layers != 1:
        proposed["min_via_diameter"] = limits["via_diameter_mm"]
    if preserve_stricter:
        stricter = [key for key, value in proposed.items()
                    if isinstance(rules.get(key), (int, float)) and rules[key] > value + 1e-9]
        if stricter:
            raise ValueError("Existing KiCad rules are stricter than CAID routing limits: " +
                             ", ".join(stricter))
    rules.update(proposed)
    settings["track_widths"] = [0.0, limits["track_width_mm"]]
    net_settings = project.setdefault("net_settings", {})
    net_settings.setdefault("meta", {"version": 5})
    classes = net_settings.setdefault("classes", [])
    default = next((item for item in classes if item.get("name") == "Default"), None)
    if default is None:
        default = {"name": "Default"}
        classes.insert(0, default)
    default.update({"clearance": limits["clearance_mm"],
                    "track_width": limits["track_width_mm"]})
    if layers != 1:
        settings["via_dimensions"] = [
            {"diameter": 0.0, "drill": 0.0},
            {"diameter": limits["via_diameter_mm"], "drill": limits["via_drill_mm"]},
        ]
        default.update({"via_diameter": limits["via_diameter_mm"],
                        "via_drill": limits["via_drill_mm"]})
    path.write_text(json.dumps(project, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    saved = json.loads(path.read_text(encoding="utf-8"))
    saved_rules = saved["board"]["design_settings"]["rules"]
    if any(saved_rules[key] != value for key, value in (
            ("min_clearance", limits["clearance_mm"]),
            ("min_copper_edge_clearance", limits["edge_clearance_mm"]),
            ("min_track_width", limits["track_width_mm"]))):
        raise RuntimeError("KiCad project rules did not survive readback")
    return saved
