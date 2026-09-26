"""Review and apply pad net assignments from a saved schematic netlist."""

from dataclasses import dataclass
import hashlib

try:
    from .i18n import localized
    from .schematic import read_schematic, schematic_path
except ImportError:
    from i18n import localized
    from schematic import read_schematic, schematic_path


@dataclass(frozen=True)
class PadChange:
    pad_id: str
    ref: str
    pin: str
    old_net: str
    new_net: str


@dataclass(frozen=True)
class NetSyncPlan:
    document: str
    project_path: str
    board_sha256: str
    schematic_sha256: str
    changes: tuple[PadChange, ...]
    warnings: tuple[str, ...]
    blockers: tuple[str, ...]
    net_count: int
    pad_count: int
    footprint_mismatches: tuple[str, ...]
    missing_footprints: tuple[str, ...]
    board_footprints: tuple[tuple[str, str], ...]
    board_pad_nets: tuple[tuple[str, str, str], ...]
    component_count: int = 0


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def board_state(board):
    footprints = list(board.get_footprints())
    refs_by_id = {str(fp.id): fp.reference_field.text.value for fp in footprints}
    return {
        "document": board.name,
        "project_path": board.document.project.path,
        "sha256": _sha(board.get_as_string().encode("utf-8")),
        "footprints": [{"ref": fp.reference_field.text.value,
                        "footprint": str(fp.definition.id)} for fp in footprints],
        "pads": [{"id": str(pad.id), "ref": refs_by_id.get(str(pad.parent), ""),
                  "pin": pad.number, "net": pad.net.name} for pad in board.get_pads()],
        "copper_items": len(board.get_tracks()) + len(board.get_vias()) + len(board.get_zones()),
    }


def plan_sync(state, schematic, language="en"):
    """Pure validation and diff; all references and pins must be unambiguous."""
    t = lambda en, de: localized(language, en, de)
    blockers = []
    warnings = []
    if schematic.get("truncated"):
        blockers.append(t("The schematic netlist was truncated.", "Die Schaltplan-Netzliste wurde abgeschnitten."))
    board_refs = {fp["ref"]: fp for fp in state["footprints"]}
    sch_refs = {comp["ref"]: comp for comp in schematic["components"]}
    if board_refs and not sch_refs:
        blockers.append(t("The saved schematic has no components; the PCB cannot be updated from it.",
                          "Der gespeicherte Schaltplan enthält keine Bauteile; die Platine kann daraus nicht aktualisiert werden."))
    if len(board_refs) != len(state["footprints"]) or len(sch_refs) != len(schematic["components"]):
        blockers.append(t("Duplicate component references found.", "Doppelte Bauteilreferenzen gefunden."))
    missing_refs = sorted(set(sch_refs) - set(board_refs))
    if missing_refs:
        blockers.append(t("Missing PCB footprints: ", "Fehlende PCB-Footprints: ") + ", ".join(missing_refs[:20]))
    extra_refs = sorted(set(board_refs) - set(sch_refs))
    if extra_refs:
        warnings.append(t("PCB footprints without schematic symbol: ", "PCB-Footprints ohne Schaltplansymbol: ") + ", ".join(extra_refs[:20]))
    mismatches = [ref for ref in sorted(set(board_refs) & set(sch_refs))
                  if sch_refs[ref]["footprint"] and board_refs[ref]["footprint"] != sch_refs[ref]["footprint"]]
    if mismatches:
        warnings.append(t("Footprint library IDs differ (use KiCad F8 to replace them): ",
                          "Footprint-Bibliotheks-IDs weichen ab (mit KiCad F8 ersetzen): ") + ", ".join(mismatches[:20]))
    desired = {}
    for net in schematic["nets"]:
        for node in net["nodes"]:
            key = (node["ref"], node["pin"])
            if key in desired and desired[key] != net["name"]:
                blockers.append(t(f"Conflicting nets on {key[0]}.{key[1]}.", f"Widersprüchliche Netze an {key[0]}.{key[1]}."))
            desired[key] = net["name"]
    pad_keys = {(pad["ref"], pad["pin"]) for pad in state["pads"]}
    missing_pads = sorted(key for key in desired if key not in pad_keys)
    if missing_pads:
        blockers.append(t("Missing PCB pads: ", "Fehlende PCB-Pads: ") +
                        ", ".join(f"{ref}.{pin}" for ref, pin in missing_pads[:20]))
    changes = []
    for pad in state["pads"]:
        if pad["ref"] not in sch_refs:
            continue
        new_net = desired.get((pad["ref"], pad["pin"]), "")
        if pad["net"] != new_net:
            changes.append(PadChange(pad["id"], pad["ref"], pad["pin"], pad["net"], new_net))
    if changes and state["copper_items"]:
        blockers.append(t("The PCB has tracks, vias or zones; use KiCad F8 to preserve copper connectivity.",
                          "Die Platine enthält Leiterbahnen, Vias oder Zonen; für bestehende Kupferverbindungen KiCad F8 nutzen."))
    return NetSyncPlan(
        state["document"], state["project_path"], state["sha256"],
        schematic["saved_file_sha256"], tuple(changes), tuple(warnings),
        tuple(blockers), schematic["net_count"], len(state["pads"]),
        tuple(mismatches), tuple(missing_refs),
        tuple(sorted((fp["ref"], fp["footprint"]) for fp in state["footprints"])),
        tuple(sorted((pad["ref"], pad["pin"], pad["net"]) for pad in state["pads"])),
        schematic.get("component_count", len(sch_refs)),
    )


def prepare_sync(board, language="en", token=None):
    state = board_state(board)
    if token:
        token.check()
    schematic = read_schematic(state, language, token)
    if token:
        token.check()
    return plan_sync(state, schematic, language)


def apply_sync(board, plan, language="en"):
    """Apply a reviewed, unchanged net assignment in one KiCad undo step."""
    t = lambda en, de: localized(language, en, de)
    if plan.blockers:
        raise ValueError(t("Resolve the preview's blocking issues first.", "Behebe zuerst die blockierenden Probleme der Vorschau."))
    if board.name != plan.document or board.document.project.path != plan.project_path:
        raise ValueError(t("A different board is open.", "Es ist eine andere Platine geöffnet."))
    if _sha(board.get_as_string().encode("utf-8")) != plan.board_sha256:
        raise ValueError(t("The PCB changed since the preview. Create a new preview.", "Die Platine hat sich seit der Vorschau geändert. Erzeuge eine neue Vorschau."))
    path = schematic_path({"project_path": plan.project_path, "document": plan.document}, language)
    if _sha(path.read_bytes()) != plan.schematic_sha256:
        raise ValueError(t("The schematic changed since the preview. Create a new preview.", "Der Schaltplan hat sich seit der Vorschau geändert. Erzeuge eine neue Vorschau."))
    if not plan.changes:
        return 0
    from kipy.board_types import Net
    pads = {str(pad.id): pad for pad in board.get_pads()}
    if set(change.pad_id for change in plan.changes) - set(pads):
        raise ValueError(t("A PCB pad is no longer present.", "Ein PCB-Pad ist nicht mehr vorhanden."))
    commit = board.begin_commit()
    try:
        updated = []
        for change in plan.changes:
            pad = pads[change.pad_id]
            pad.net = Net(name=change.new_net)
            updated.append(pad)
        result = board.update_items(updated)
        result_by_id = {str(pad.id): pad.net.name for pad in result}
        if len(result) != len(updated) or any(result_by_id.get(change.pad_id) != change.new_net for change in plan.changes):
            raise RuntimeError(t("KiCad did not accept every pad net.", "KiCad hat nicht alle Pad-Netze übernommen."))
        board.push_commit(commit, t("CAID: Sync schematic nets", "CAID: Schaltplan-Netze übernehmen"))
    except Exception:
        board.drop_commit(commit)
        raise
    return len(plan.changes)
