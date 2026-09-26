"""One review object for a staged schematic change and its PCB consequences."""

from dataclasses import dataclass

try:
    from .footprints import find_footprint
    from .i18n import localized
    from .net_sync import board_state, plan_sync
    from .schematic import apply_schematic_edit
except ImportError:
    from footprints import find_footprint
    from i18n import localized
    from net_sync import board_state, plan_sync
    from schematic import apply_schematic_edit


def _net_map(schematic):
    return {(node["ref"], node["pin"]): net["name"]
            for net in schematic["nets"] for node in net["nodes"]}


@dataclass
class DesignPlan:
    staged: object
    board_document: str
    board_project: str
    board_hash: str
    net_changes: tuple
    footprint_changes: tuple
    pad_differences_before: int
    pad_differences_after: int
    footprint_differences_before: int
    footprint_differences_after: int
    blockers: tuple
    unresolved_footprints: tuple

    def cleanup(self):
        self.staged.cleanup()


def prepare_design_plan(board, staged, current_schematic, language="en"):
    """Compare both saved and proposed schematic with the same live PCB state."""
    if staged.candidate_snapshot is None:
        raise ValueError("Staged schematic has no validated netlist")
    if current_schematic.get("saved_file_sha256") != staged.original_hash:
        raise ValueError(localized(language, "The saved schematic changed while preparing the proposal.",
                                   "Der gespeicherte Schaltplan hat sich während der Vorschau geändert."))
    if getattr(staged, "before_snapshot", None) is not None:
        current_schematic = staged.before_snapshot
    state = board_state(board)
    before = plan_sync(state, current_schematic, language)
    after = plan_sync(state, staged.candidate_snapshot, language)
    old_nets = _net_map(current_schematic)
    new_nets = _net_map(staged.candidate_snapshot)
    net_changes = tuple((ref, pin, old_nets.get((ref, pin), ""), new_nets.get((ref, pin), ""))
                        for ref, pin in sorted(set(old_nets) | set(new_nets))
                        if old_nets.get((ref, pin), "") != new_nets.get((ref, pin), ""))
    old_components = {item["ref"]: item for item in current_schematic["components"]}
    new_components = {item["ref"]: item for item in staged.candidate_snapshot["components"]}
    footprint_changes = tuple((ref,
                               old_components.get(ref, {}).get("footprint", ""),
                               new_components.get(ref, {}).get("footprint", ""))
                              for ref in sorted(set(old_components) | set(new_components))
                              if old_components.get(ref, {}).get("footprint", "") !=
                              new_components.get(ref, {}).get("footprint", ""))
    unresolved = tuple((ref, identifier) for ref, _old, identifier in footprint_changes
                       if identifier and find_footprint(state["project_path"], identifier) is None)
    return DesignPlan(
        staged, state["document"], state["project_path"], state["sha256"],
        net_changes, footprint_changes, len(before.changes), len(after.changes),
        len(before.footprint_mismatches), len(after.footprint_mismatches),
        tuple(after.blockers), unresolved,
    )


def describe_design_plan(plan, language="en"):
    t = lambda en, de: localized(language, en, de)
    before_e, before_w = plan.staged.erc_before
    after_e, after_w = plan.staged.erc_after
    lines = [t("Saved schematic → proposal", "Gespeicherter Schaltplan → Vorschlag"),
             t(f"ERC errors {before_e} → {after_e}; warnings {before_w} → {after_w}.",
               f"ERC-Fehler {before_e} → {after_e}; Warnungen {before_w} → {after_w}."),
             "",
             t("Impact on the open PCB", "Auswirkung auf die geöffnete Platine"),
             t(f"Pad net differences to the schematic: {plan.pad_differences_before} → {plan.pad_differences_after}.",
               f"Pad-Netzabweichungen zum Schaltplan: {plan.pad_differences_before} → {plan.pad_differences_after}."),
             t(f"Footprint ID differences: {plan.footprint_differences_before} → {plan.footprint_differences_after}.",
               f"Footprint-ID-Abweichungen: {plan.footprint_differences_before} → {plan.footprint_differences_after}.")]
    if plan.footprint_changes:
        lines.append(t("Footprint assignments:", "Footprint-Zuordnungen:"))
        lines.extend(f"  {ref}: {old or '∅'} → {new or '∅'}"
                     for ref, old, new in plan.footprint_changes[:20])
    if plan.unresolved_footprints:
        lines.append(t("Target footprint files not found in project or standard libraries:",
                       "Ziel-Footprint-Dateien nicht in Projekt- oder Standardbibliothek gefunden:"))
        lines.extend(f"  {ref}: {identifier}" for ref, identifier in plan.unresolved_footprints[:20])
    if plan.net_changes:
        lines.append(t("Changed schematic pin nets (excerpt):", "Geänderte Schaltplan-Pinnetze (Auszug):"))
        lines.extend(f"  {ref}.{pin}: {old or '∅'} → {new or '∅'}"
                     for ref, pin, old, new in plan.net_changes[:20])
        if len(plan.net_changes) > 20:
            lines.append(f"  … +{len(plan.net_changes) - 20}")
    if plan.blockers:
        lines.append(t("PCB update needs review:", "PCB-Aktualisierung benötigt Prüfung:"))
        lines.extend("  • " + issue for issue in plan.blockers[:8])
        if len(plan.blockers) > 8:
            lines.append(f"  … +{len(plan.blockers) - 8}")
    lines.extend(["", t("Apply the schematic first; then review KiCad's PCB update (F8).",
                          "Zuerst den Schaltplan übernehmen; danach KiCads PCB-Aktualisierung (F8) prüfen."),
                  t(f"Full schematic diff: {plan.staged.stage_dir / 'change.diff'}",
                    f"Vollständiger Schaltplan-Diff: {plan.staged.stage_dir / 'change.diff'}")])
    return "\n".join(lines)


def apply_design_plan(board, plan, language="en"):
    if plan.unresolved_footprints:
        raise ValueError(localized(language,
                                   "A proposed footprint library file is unavailable. Resolve it before applying.",
                                   "Eine vorgeschlagene Footprint-Bibliotheksdatei fehlt. Behebe das vor der Übernahme."))
    state = board_state(board)
    if (state["document"] != plan.board_document or state["project_path"] != plan.board_project or
            state["sha256"] != plan.board_hash):
        raise ValueError(localized(language, "The PCB changed since the combined preview. Create a new proposal.",
                                   "Die Platine hat sich seit der gemeinsamen Vorschau geändert. Bitte neu vorschlagen lassen."))
    return apply_schematic_edit(plan.staged, language)
