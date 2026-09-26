"""Bounded, read-only project inspections requested by the chat model."""

try:
    from .diagnostics import run_drc, run_erc
    from .footprints import inspect_footprints
    from .i18n import localized
except ImportError:
    from diagnostics import run_drc, run_erc
    from footprints import inspect_footprints
    from i18n import localized


TOOL_LABELS = {
    "component": ("Inspecting component {argument}", "Prüfe Bauteil {argument}"),
    "net": ("Inspecting net {argument}", "Prüfe Netz {argument}"),
    "footprint": ("Checking footprint for {argument}", "Prüfe Footprint für {argument}"),
    "selection": ("Reading PCB selection", "Lese PCB-Auswahl"),
    "find_components": ("Searching components for {argument}", "Suche Bauteile nach {argument}"),
    "erc": ("Running ERC on saved schematic", "Prüfe gespeicherten Schaltplan mit ERC"),
    "drc": ("Running DRC on saved PCB", "Prüfe gespeicherte Platine mit DRC"),
}


def tool_label(request, language="en"):
    en, de = TOOL_LABELS[request["tool"]]
    return localized(language, en, de).format(argument=request["argument"][:40])


def _references(board):
    footprints = list(board.get_footprints())
    return {str(fp.id): fp.reference_field.text.value for fp in footprints}, {
        fp.reference_field.text.value.upper(): fp for fp in footprints}


def _pad_data(board, ref_by_id, target=None):
    result = []
    for pad in board.get_pads():
        ref = ref_by_id.get(str(pad.parent), "")
        if target is not None and ref.upper() != target.upper():
            continue
        result.append({"ref": ref, "pin": str(pad.number), "net": pad.net.name})
    return result


def execute_read_tool(request, board, board_snapshot, schematic_snapshot, language="en", token=None):
    """Return data for one allowlisted inspection without changing KiCad state."""
    if token:
        token.check()
    tool = request["tool"]
    argument = request["argument"].strip()
    components = schematic_snapshot.get("components", []) if isinstance(schematic_snapshot, dict) else []
    nets = schematic_snapshot.get("nets", []) if isinstance(schematic_snapshot, dict) else []
    if tool == "footprint":
        if not argument:
            raise ValueError(localized(language, "Specify a component reference.", "Gib eine Bauteilreferenz an."))
        return inspect_footprints(board_snapshot, schematic_snapshot, argument)
    if tool == "erc":
        return {"source": "saved_schematic", "report": run_erc(board, language, token)}
    if tool == "drc":
        return {"source": "saved_pcb", "report": run_drc(board, language, token)}
    if tool == "find_components":
        if len(argument) < 2:
            raise ValueError(localized(language, "Search text must have at least two characters.",
                                       "Der Suchtext muss mindestens zwei Zeichen lang sein."))
        query = argument.casefold()
        _, by_ref = _references(board)
        saved = [item for item in components if query in item["ref"].casefold() or
                 query in item.get("value", "").casefold() or query in item.get("footprint", "").casefold()]
        live = [{"ref": fp.reference_field.text.value, "value": fp.value_field.text.value,
                 "footprint": str(fp.definition.id)} for fp in by_ref.values()
                if query in fp.reference_field.text.value.casefold() or
                query in fp.value_field.text.value.casefold() or
                query in str(fp.definition.id).casefold()]
        return {"saved_schematic": saved[:30], "live_pcb": live[:30],
                "truncated": len(saved) > 30 or len(live) > 30 or
                bool(schematic_snapshot.get("truncated"))}
    if tool == "selection":
        ref_by_id, _ = _references(board)
        selected = list(board.get_selection())
        items = []
        for item in selected[:30]:
            ref = getattr(getattr(getattr(item, "reference_field", None), "text", None), "value", None)
            parent = getattr(item, "parent", None)
            items.append({"type": type(item).__name__, "ref": ref or ref_by_id.get(str(parent)),
                          "pin": str(item.number) if hasattr(item, "number") else None,
                          "id": str(item.id)})
        return {"items": items, "count": len(selected), "truncated": len(selected) > 30}
    if tool == "component":
        if not argument:
            raise ValueError(localized(language, "Specify a component reference.", "Gib eine Bauteilreferenz an."))
        ref_by_id, by_ref = _references(board)
        schematic = next((item for item in components if item["ref"].upper() == argument.upper()), None)
        footprint = by_ref.get(argument.upper())
        if schematic is None and footprint is None:
            raise ValueError(localized(language, f"Component {argument} not found.",
                                       f"Bauteil {argument} nicht gefunden."))
        pins = [{"pin": node["pin"], "net": net["name"]} for net in nets
                for node in net["nodes"] if node["ref"].upper() == argument.upper()]
        return {"ref": argument.upper(), "saved_schematic": schematic,
                "schematic_pins": pins[:128], "schematic_pins_truncated": len(pins) > 128,
                "schematic_snapshot_truncated": bool(schematic_snapshot.get("truncated")),
                "live_pcb": ({"value": footprint.value_field.text.value,
                              "footprint": str(footprint.definition.id),
                              "position_mm": [round(footprint.position.x / 1_000_000, 3),
                                              round(footprint.position.y / 1_000_000, 3)]}
                             if footprint else None),
                "pcb_pads": _pad_data(board, ref_by_id, argument)[:128] if footprint else []}
    if tool == "net":
        if not argument:
            raise ValueError(localized(language, "Specify a net name.", "Gib einen Netznamen an."))
        found = [net for net in nets if net["name"].casefold() == argument.casefold()]
        if not found:
            found = [net for net in nets if argument.casefold() in net["name"].casefold()][:8]
        ref_by_id, _ = _references(board)
        names = {net["name"].casefold() for net in found}
        pcb_pads = [pad for pad in _pad_data(board, ref_by_id)
                    if pad["net"].casefold() in names]
        return {"saved_schematic": [{"name": net["name"], "nodes": net["nodes"][:100]}
                                    for net in found[:8]], "live_pcb_pads": pcb_pads[:100],
                "truncated": len(found) > 8 or len(pcb_pads) > 100 or
                bool(schematic_snapshot.get("truncated"))}
    raise ValueError(localized(language, f"Unknown inspection tool: {tool}", f"Unbekanntes Prüfwerkzeug: {tool}"))
