"""Routing requirements and read-only KiCad preflight.

No routing operation is permitted from this module. Values in the brief are
requirements, not proof that KiCad's design rules or a fabricator match them.
"""

import math


RULES = ("track_width_mm", "clearance_mm", "via_diameter_mm",
         "via_drill_mm", "edge_clearance_mm")


def default_contract():
    return {"requested_layers": None, "routing_layers": [],
            "limits_mm": {key: None for key in RULES}, "source": "user"}


def validate_contract(contract):
    if not isinstance(contract, dict):
        raise ValueError("Invalid routing contract")
    layers = contract.get("requested_layers")
    if layers is not None and (type(layers) is not int or layers not in (1, *range(2, 33, 2))):
        raise ValueError("Routing layers must be 1 or an even number from 2 to 32")
    selected = contract.get("routing_layers", [])
    if not isinstance(selected, list) or any(not isinstance(item, str) for item in selected):
        raise ValueError("Invalid routing layer list")
    expected = (["F.Cu"] if layers == 1 else
                ["F.Cu", *[f"In{i}.Cu" for i in range(1, layers - 1)], "B.Cu"]
                if layers else [])
    if selected != expected:
        raise ValueError("Routing layer list does not match requested layer count")
    limits = contract.get("limits_mm")
    if not isinstance(limits, dict) or set(limits) != set(RULES):
        raise ValueError("Incomplete routing limits")
    for name, value in limits.items():
        if value is not None and (type(value) not in (int, float) or
                                  not math.isfinite(value) or not 0 < value <= 10):
            raise ValueError(f"Invalid {name}")
    if (limits["via_diameter_mm"] is not None and limits["via_drill_mm"] is not None and
            limits["via_diameter_mm"] <= limits["via_drill_mm"]):
        raise ValueError("Via diameter must exceed drill diameter")
    return contract


def set_layers(contract, count):
    updated = {**contract, "requested_layers": count,
               "routing_layers": ["F.Cu"] if count == 1 else
               ["F.Cu", *[f"In{i}.Cu" for i in range(1, count - 1)], "B.Cu"]}
    return validate_contract(updated)


def set_limits(contract, values):
    if len(values) == 3 and contract.get("requested_layers") == 1:
        keys = ("track_width_mm", "clearance_mm", "edge_clearance_mm")
    elif len(values) == len(RULES):
        keys = RULES
    else:
        raise ValueError("Provide width, clearance and edge clearance for one layer; otherwise width, clearance, via diameter, via drill and edge clearance (mm)")
    try:
        numbers = [float(value.replace(",", ".")) for value in values]
    except (ValueError, AttributeError) as exc:
        raise ValueError("Routing limits must be numbers in mm") from exc
    limits = dict(contract["limits_mm"])
    limits.update(zip(keys, numbers))
    updated = {**contract, "limits_mm": limits}
    return validate_contract(updated)


def preflight(contract, board_snapshot, drc_report=None):
    """Return blockers without claiming electrical or manufacturing approval."""
    validate_contract(contract)
    blockers = []
    layers = contract["requested_layers"]
    if layers is None:
        blockers.append("layer count missing")
    actual = board_snapshot.get("copper_layers")
    if actual is None:
        blockers.append("KiCad copper stackup could not be read")
    elif layers is not None and actual != max(2, layers):
        blockers.append(f"board has {actual} copper layers; requested {layers} routing layers")
    used = set(board_snapshot.get("track_layers", [])) - set(contract["routing_layers"])
    if used and layers is not None:
        blockers.append("existing tracks on excluded layers: " + ", ".join(sorted(used)))
    if layers == 1 and board_snapshot.get("counts", {}).get("vias", 0):
        blockers.append("existing vias conflict with one-sided routing")
    if not board_snapshot.get("outline_mm"):
        blockers.append("closed board outline not confirmed")
    target = board_snapshot.get("target_size_mm")
    if isinstance(target, dict):
        width, height = target.get("width_mm"), target.get("height_mm")
        if (isinstance(width, (int, float)) and isinstance(height, (int, float)) and
                width > 0 and height > 0):
            outline = board_snapshot.get("outline_mm")
            if outline:
                actual_width, actual_height = outline[2] - outline[0], outline[3] - outline[1]
                direct = actual_width <= width + 0.001 and actual_height <= height + 0.001
                rotated = actual_width <= height + 0.001 and actual_height <= width + 0.001
                if target.get("mode") == "exact":
                    direct = direct and abs(actual_width - width) <= 0.001 and abs(actual_height - height) <= 0.001
                    rotated = rotated and abs(actual_width - height) <= 0.001 and abs(actual_height - width) <= 0.001
                if not (direct or rotated):
                    blockers.append(f"board outline {actual_width:.2f} × {actual_height:.2f} mm "
                                    f"violates target {width:g} × {height:g} mm")
            if board_snapshot.get("truncated"):
                blockers.append("footprint list truncated; board fit cannot be confirmed")
            for footprint in board_snapshot.get("footprints", []):
                box = footprint.get("bbox_mm")
                if not box:
                    continue
                footprint_width, footprint_height = box[2] - box[0], box[3] - box[1]
                if not ((footprint_width <= width and footprint_height <= height) or
                        (footprint_height <= width and footprint_width <= height)):
                    blockers.append(f"{footprint['ref']} bounding box {footprint_width:.2f} × "
                                    f"{footprint_height:.2f} mm does not fit {width:g} × {height:g} mm")
                if outline and (box[0] < outline[0] - 0.001 or box[1] < outline[1] - 0.001 or
                                box[2] > outline[2] + 0.001 or box[3] > outline[3] + 0.001):
                    blockers.append(f"{footprint['ref']} extends outside the board outline")
    missing = [key for key, value in contract["limits_mm"].items()
               if value is None and (layers != 1 or key not in {"via_diameter_mm", "via_drill_mm"})]
    if missing:
        blockers.append("routing limits missing: " + ", ".join(missing))
    if drc_report is None:
        blockers.append("saved-board DRC not run")
    else:
        errors = [item for item in drc_report.get("violations", []) if item.get("severity") == "error"]
        parity = drc_report.get("schematic_parity", [])
        if errors:
            blockers.append(f"{len(errors)} DRC errors on saved board")
        if parity:
            blockers.append(f"{len(parity)} schematic/PCB differences")
    return blockers


def describe(contract, board_snapshot, drc_report=None, language="en"):
    blockers = preflight(contract, board_snapshot, drc_report)
    de = language == "de"
    layers = contract["requested_layers"]
    lines = [("Routing preparation" if not de else "Routing-Vorbereitung"),
             f"{('Requested routing layers' if not de else 'Gewünschte Routing-Lagen')}: "
             f"{layers if layers is not None else ('open' if not de else 'offen')} "
             f"({', '.join(contract['routing_layers']) or '—'})",
             f"{('PCB copper layers' if not de else 'PCB-Kupferlagen')}: {board_snapshot.get('copper_layers') or '—'}"]
    labels = {"track_width_mm": ("Track width", "Leiterbahnbreite"),
              "clearance_mm": ("Clearance", "Abstand"),
              "via_diameter_mm": ("Via diameter", "Via-Durchmesser"),
              "via_drill_mm": ("Via drill", "Via-Bohrung"),
              "edge_clearance_mm": ("Edge clearance", "Randabstand")}
    for key, value in contract["limits_mm"].items():
        if layers == 1 and key in {"via_diameter_mm", "via_drill_mm"}:
            lines.append(f"{labels[key][de]}: {'not used' if not de else 'entfällt'}")
        else:
            lines.append(f"{labels[key][de]}: {value:g} mm" if value is not None else
                         f"{labels[key][de]}: {'open' if not de else 'offen'}")
    if drc_report is not None:
        lines.append(f"{('Unconnected items' if not de else 'Offene Verbindungen')}: "
                     f"{len(drc_report.get('unconnected_items', []))}")
    lines.append("")
    lines.append(("Preflight blockers:" if not de else "Sperren vor dem Routing:") if blockers else
                 ("Preflight passed; routing will synchronize rules in a project copy." if not de else
                  "Vorprüfung bestanden; beim Routing werden Regeln in der Projektkopie übernommen."))
    translations = {
        "layer count missing": "Lagenzahl fehlt",
        "KiCad copper stackup could not be read": "KiCad-Kupferlagen konnten nicht gelesen werden",
        "closed board outline not confirmed": "geschlossener Platinenumriss nicht bestätigt",
        "saved-board DRC not run": "DRC der gespeicherten Platine noch nicht ausgeführt",
    }
    for item in blockers:
        if de:
            item = translations.get(item, item)
            item = item.replace("board has ", "PCB hat ").replace(" copper layers; requested ",
                           " Kupferlagen; gewünscht sind ").replace(" routing layers", " Routing-Lagen")
            item = item.replace("routing limits missing: ", "Routing-Grenzwerte fehlen: ")
            item = item.replace("existing tracks on excluded layers: ", "Vorhandene Leiterbahnen auf ausgeschlossenen Lagen: ")
            item = item.replace("existing vias conflict with one-sided routing", "Vorhandene Vias widersprechen einseitigem Routing")
            item = item.replace(" bounding box ", " Hüllrechteck ").replace(" does not fit ", " passt nicht in ")
            item = item.replace("board outline ", "Platinenumriss ").replace(" violates target ", " verletzt Vorgabe ")
            item = item.replace("footprint list truncated; board fit cannot be confirmed", "Bauteilliste gekürzt; Passung kann nicht bestätigt werden")
            item = item.replace(" extends outside the board outline", " liegt außerhalb des Platinenumrisses")
            item = item.replace(" DRC errors on saved board", " DRC-Fehler auf der gespeicherten Platine")
            item = item.replace(" schematic/PCB differences", " Schaltplan/PCB-Abweichungen")
        lines.append(f"• {item}")
    lines.append("\n" + ("The values above are requirements; KiCad design rules and fabricator limits have not been verified." if not de else
                          "Diese Werte sind Vorgaben; KiCad-Regeln und Fertigergrenzen sind damit noch nicht verifiziert."))
    return "\n".join(lines)
