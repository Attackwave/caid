"""Read live PCB geometry and apply reviewed footprint placement proposals."""

from dataclasses import dataclass, replace
import hashlib
import math

try:
    from .i18n import localized
except ImportError:
    from i18n import localized


def _mm(vector):
    return [round(vector.x / 1_000_000, 3), round(vector.y / 1_000_000, 3)]


def _ref(footprint):
    return footprint.reference_field.text.value


def _side(footprint):
    from kipy.proto.board.board_types_pb2 import BoardLayer
    return "TOP" if footprint.layer == BoardLayer.BL_F_Cu else "BOTTOM"


def _outline(board):
    shapes = board.get_shapes()
    if not shapes:
        return None
    from kipy.proto.board.board_types_pb2 import BoardLayer
    edges = [shape for shape in shapes if shape.layer == BoardLayer.BL_Edge_Cuts]
    if (len(edges) == 1 and type(edges[0]).__name__ == "BoardRectangle" and
            hasattr(edges[0], "start") and hasattr(edges[0], "end")):
        a, b = _mm(edges[0].start), _mm(edges[0].end)
        return [min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1])]
    if len(edges) == 1 and hasattr(edges[0], "top_left") and hasattr(edges[0], "bottom_right"):
        a, b = _mm(edges[0].top_left), _mm(edges[0].bottom_right)
        return [min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1])]
    if len(edges) != 4 or not all(hasattr(edge, "start") and hasattr(edge, "end") for edge in edges):
        return None
    points = [_mm(point) for edge in edges for point in (edge.start, edge.end)]
    xs = sorted({point[0] for point in points})
    ys = sorted({point[1] for point in points})
    if len(xs) != 2 or len(ys) != 2:
        return None
    corners = {(x, y) for x in xs for y in ys}
    if any((tuple(_mm(edge.start)) not in corners or tuple(_mm(edge.end)) not in corners)
           for edge in edges):
        return None
    return [xs[0], ys[0], xs[1], ys[1]]


def snapshot(board):
    """Return a bounded live snapshot with sizes, sides and rectangular outline."""
    footprints = list(board.get_footprints())
    tracks = board.get_tracks()
    vias = board.get_vias()
    nets = board.get_nets()
    shown = sorted(footprints, key=_ref)[:100]
    boxes = board.get_item_bounding_box(shown) if shown else []
    if len(boxes) != len(shown):
        boxes = [board.get_item_bounding_box(fp) for fp in shown]
    items = []
    for fp, box in zip(shown, boxes):
        if box is None:
            bbox = None
        else:
            xy = _mm(box.pos)
            wh = _mm(box.size)
            bbox = [xy[0], xy[1], round(xy[0] + wh[0], 3), round(xy[1] + wh[1], 3)]
        items.append({
            "ref": _ref(fp), "value": fp.value_field.text.value,
            "footprint_id": str(fp.definition.id),
            "position_mm": _mm(fp.position), "side": _side(fp),
            "bbox_mm": bbox, "locked": fp.locked,
        })
    return {
        "document": board.name,
        "project_path": board.document.project.path,
        "board_sha256": hashlib.sha256(board.get_as_string().encode("utf-8")).hexdigest(),
        "copper_layers": board.get_copper_layer_count(),
        "outline_mm": _outline(board),
        "counts": {"footprints": len(footprints), "tracks": len(tracks),
                   "vias": len(vias), "nets": len(nets)},
        "track_layers": sorted({board.get_layer_name(track.layer) for track in tracks}),
        "net_names": [net.name for net in nets if net.name][:100],
        "footprints": items,
        "truncated": len(footprints) > 100,
    }


@dataclass(frozen=True)
class Placement:
    ref: str
    x_mm: float
    y_mm: float
    side: str


def validate_placements(raw, board_snapshot, language="en"):
    """Turn model output into exact target positions and reject invalid references."""
    if not isinstance(raw, list) or len(raw) > 100:
        raise ValueError(localized(language, "At most 100 footprints per proposal.", "Maximal 100 Bauteile je Vorschlag."))
    available = {fp["ref"]: fp for fp in board_snapshot["footprints"]}
    if board_snapshot["truncated"] and raw:
        raise ValueError(localized(language, "The board has over 100 footprints; the proposal is incomplete.", "Die Platine enthält mehr als 100 Bauteile; Vorschlag ist unvollständig."))
    seen = set()
    placements = []
    for item in raw:
        if not isinstance(item, dict) or set(item) != {"ref", "side", "x_mm", "y_mm"}:
            raise ValueError(localized(language, "Invalid placement entry.", "Ungültiger Platzierungseintrag."))
        ref = item["ref"]
        if not isinstance(ref, str) or ref not in available or ref in seen:
            raise ValueError(localized(language, f"Footprint {ref!r} is missing or duplicated.", f"Bauteil {ref!r} fehlt oder kommt mehrfach vor."))
        if available[ref]["locked"]:
            raise ValueError(localized(language, f"Footprint {ref} is locked.", f"Bauteil {ref} ist gesperrt."))
        side = item["side"]
        if side not in ("TOP", "BOTTOM"):
            raise ValueError(localized(language, f"Invalid side for {ref}.", f"Ungültige Seite für {ref}."))
        x, y = item["x_mm"], item["y_mm"]
        if (x is None) != (y is None):
            raise ValueError(localized(language, f"For {ref}, provide both coordinates or neither.", f"Für {ref} müssen beide Koordinaten oder keine angegeben sein."))
        if x is None:
            x, y = available[ref]["position_mm"]
        for value in (x, y):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1000:
                raise ValueError(localized(language, f"Invalid coordinate for {ref}.", f"Ungültige Koordinate für {ref}."))
        # KiCad mirrors a footprint around its own origin. For asymmetric items
        # (notably a long pin row), compensate so its physical occupied area
        # stays at the requested layout position after the side change.
        if side != available[ref]["side"] and available[ref]["bbox_mm"] is not None:
            x1, _y1, x2, _y2 = available[ref]["bbox_mm"]
            x += x1 + x2 - 2 * available[ref]["position_mm"][0]
        placements.append(Placement(ref, round(float(x), 3), round(float(y), 3), side))
        seen.add(ref)
    return [item for item in placements if
            [item.x_mm, item.y_mm] != available[item.ref]["position_mm"] or
            item.side != available[item.ref]["side"]]


def _projected_boxes(placements, board_snapshot):
    targets = {p.ref: p for p in placements}
    projected = []
    for fp in board_snapshot["footprints"]:
        if fp["bbox_mm"] is None:
            continue
        original_x, original_y = fp["position_mm"]
        target = targets.get(fp["ref"])
        x = target.x_mm if target else original_x
        y = target.y_mm if target else original_y
        side = target.side if target else fp["side"]
        x1, y1, x2, y2 = fp["bbox_mm"]
        if side != fp["side"]:
            x1, x2 = 2 * original_x - x2, 2 * original_x - x1
        dx, dy = x - original_x, y - original_y
        projected.append((fp["ref"], side, [x1 + dx, y1 + dy, x2 + dx, y2 + dy]))
    return projected


def geometry_warnings(placements, board_snapshot, language="en"):
    """Conservative preview checks; KiCad DRC is still authoritative."""
    warnings = []
    boxes = _projected_boxes(placements, board_snapshot)
    outline = board_snapshot.get("outline_mm")
    if outline:
        left, top, right, bottom = outline
        for ref, _side_name, (x1, y1, x2, y2) in boxes:
            if x1 < left + 0.25 or y1 < top + 0.25 or x2 > right - 0.25 or y2 > bottom - 0.25:
                warnings.append(localized(language, f"{ref} is too close to or outside the board edge.", f"{ref} liegt zu nah am Platinenrand oder außerhalb."))
    else:
        warnings.append(localized(language, "No rectangular board outline found; edge clearance unchecked.", "Kein rechteckiger Platinenumriss erkannt; Randabstände ungeprüft."))
    for index, (ref_a, side_a, a) in enumerate(boxes):
        for ref_b, side_b, b in boxes[index + 1:]:
            if side_a != side_b:
                continue
            if a[0] < b[2] + 0.2 and a[2] + 0.2 > b[0] and a[1] < b[3] + 0.2 and a[3] + 0.2 > b[1]:
                warnings.append(localized(language, f"{ref_a} and {ref_b} overlap on {side_a} or are less than 0.2 mm apart.", f"{ref_a} und {ref_b} überlappen auf {side_a} oder haben weniger als 0,2 mm Abstand."))
    return warnings


def repair_placements(placements, board_snapshot):
    """Try small local shifts when model coordinates cause rectangle collisions."""
    current = list(placements)
    offsets = []
    for distance in (0.2, 0.4, 0.6, 0.8, 1.0, 1.5, 2.0):
        offsets.extend(((0, distance), (0, -distance), (distance, 0), (-distance, 0),
                        (distance, distance), (distance, -distance),
                        (-distance, distance), (-distance, -distance)))
    for _attempt in range(6):
        baseline = geometry_warnings(current, board_snapshot)
        if not baseline:
            break
        best = None
        for index, item in enumerate(current):
            for dx, dy in offsets:
                candidate = current.copy()
                candidate[index] = replace(
                    item, x_mm=round(item.x_mm + dx, 3), y_mm=round(item.y_mm + dy, 3))
                warnings = geometry_warnings(candidate, board_snapshot)
                if len(warnings) >= len(baseline):
                    continue
                score = (len(warnings), abs(dx) + abs(dy), index)
                if best is None or score < best[0]:
                    best = (score, candidate)
        if best is None:
            break
        current = best[1]
    return current


def describe_placements(placements, board_snapshot, language="en"):
    current = {fp["ref"]: fp for fp in board_snapshot["footprints"]}
    projected = {ref: box for ref, _side_name, box in _projected_boxes(placements, board_snapshot)}
    lines = []
    for item in placements:
        old = current[item.ref]
        old_box = old["bbox_mm"]
        new_box = projected.get(item.ref)
        if old_box and new_box:
            old_center = [(old_box[0] + old_box[2]) / 2, (old_box[1] + old_box[3]) / 2]
            new_center = [(new_box[0] + new_box[2]) / 2, (new_box[1] + new_box[3]) / 2]
        else:
            old_center = old["position_mm"]
            new_center = [item.x_mm, item.y_mm]
        center = localized(language, "center", "Mitte")
        lines.append(f"{item.ref}: {old['side']} {center} {old_center[0]:.3f}, {old_center[1]:.3f} mm"
                     f" → {item.side} {center} {new_center[0]:.3f}, {new_center[1]:.3f} mm")
    return "\n".join(lines)


def mark_footprints(board, references, language="en"):
    """Add the affected footprints to KiCad's selection for visual review."""
    by_ref = {_ref(fp): fp for fp in board.get_footprints()}
    missing = sorted(set(references) - set(by_ref))
    if missing:
        raise ValueError(localized(language, "Footprints no longer present: ",
                                   "Bauteile nicht mehr vorhanden: ") + ", ".join(missing))
    if references:
        board.add_to_selection([by_ref[ref] for ref in references])


def board_matches_snapshot(board, original_snapshot):
    """Compare all live PCB facts while ignoring added model-context metadata."""
    current = snapshot(board)
    return all(original_snapshot.get(key) == value for key, value in current.items())


def apply_placements(board, placements, original_snapshot, language="en"):
    """Flip and move within one undoable KiCad commit."""
    if not board_matches_snapshot(board, original_snapshot):
        raise ValueError(localized(language, "The board changed since the preview. Please request a new proposal.", "Die Platine hat sich seit der Vorschau geändert. Bitte neu anfragen."))
    from kipy.geometry import Vector2
    by_ref = {_ref(fp): fp for fp in board.get_footprints()}
    if set(item.ref for item in placements) - set(by_ref):
        raise ValueError(localized(language, "A footprint is no longer present.", "Ein Bauteil ist nicht mehr vorhanden."))
    commit = board.begin_commit()
    try:
        updated = []
        for item in placements:
            fp = by_ref[item.ref]
            if fp.locked:
                raise ValueError(localized(language, f"Footprint {item.ref} is locked.", f"Bauteil {item.ref} ist gesperrt."))
            if _side(fp) != item.side:
                flipped = board.flip_items(fp)
                if len(flipped) != 1 or _side(flipped[0]) != item.side:
                    raise RuntimeError(localized(language, f"KiCad could not flip {item.ref} to {item.side}.", f"KiCad konnte {item.ref} nicht auf {item.side} drehen."))
                fp = flipped[0]
            fp.position = Vector2.from_xy_mm(item.x_mm, item.y_mm)
            updated.append(fp)
        result = board.update_items(updated)
        if len(result) != len(updated):
            raise RuntimeError(localized(language, "KiCad did not update every footprint.", "KiCad hat nicht alle Bauteile aktualisiert."))
        actual = {_ref(fp): (list(_mm(fp.position)), _side(fp)) for fp in result}
        for item in placements:
            if actual.get(item.ref) != ([item.x_mm, item.y_mm], item.side):
                raise RuntimeError(localized(language, f"KiCad did not apply the placement of {item.ref}.", f"KiCad hat die Platzierung von {item.ref} nicht übernommen."))
        board.push_commit(commit, localized(language, "CAID: Place footprints", "CAID: Bauteile platzieren"))
    except Exception:
        board.drop_commit(commit)
        raise
