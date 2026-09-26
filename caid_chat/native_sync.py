"""KiCad's native forward-annotation action and a before/after report."""

try:
    from .i18n import localized
except ImportError:
    from i18n import localized


ACTION = "common.Control.updatePcbFromSchematic"


def invoke_kicad_update(client, language="en"):
    """Open KiCad's own F8 dialog; its Update PCB button remains user-controlled."""
    # F8 shows a modal dialog. Keep the same IPC connection, but extend its
    # reply timeout while the user reviews KiCad's native change list.
    ipc = getattr(client, "_client", None)
    old_timeout = getattr(ipc, "_timeout_ms", None)
    connection = getattr(ipc, "_conn", None) if getattr(ipc, "connected", False) else None
    old_send = getattr(connection, "send_timeout", None)
    old_recv = getattr(connection, "recv_timeout", None)
    try:
        if ipc is not None:
            ipc._timeout_ms = 600_000
        if connection is not None:
            connection.send_timeout = 600_000
            connection.recv_timeout = 600_000
        response = client.run_action(ACTION)
    finally:
        if ipc is not None and old_timeout is not None:
            ipc._timeout_ms = old_timeout
        if connection is not None:
            if old_send is not None:
                connection.send_timeout = old_send
            if old_recv is not None:
                connection.recv_timeout = old_recv
    if response.status != 1:  # RunActionStatus.RAS_OK
        raise RuntimeError(localized(
            language,
            f"KiCad did not open Update PCB from Schematic (status {response.status}). Use F8 in the PCB editor.",
            f"KiCad hat 'PCB aus Schaltplan aktualisieren' nicht geöffnet (Status {response.status}). Nutze F8 im PCB-Editor.",
        ))


def describe_outcome(before, after, language="en"):
    t = lambda en, de: localized(language, en, de)
    changed = before.board_sha256 != after.board_sha256
    fixed = sorted(set(before.footprint_mismatches) - set(after.footprint_mismatches))
    remaining = sorted(after.footprint_mismatches)
    lines = [t("KiCad PCB update finished.", "KiCad-PCB-Aktualisierung beendet.")]
    if not changed:
        lines.append(t("The board is unchanged; the KiCad dialog may have been cancelled.",
                       "Die Platine ist unverändert; der KiCad-Dialog wurde möglicherweise abgebrochen."))
    lines.append(t(f"Pad net differences: {len(before.changes)} → {len(after.changes)}.",
                   f"Pad-Netzabweichungen: {len(before.changes)} → {len(after.changes)}."))
    lines.append(t(f"Missing footprints: {len(before.missing_footprints)} → {len(after.missing_footprints)}.",
                   f"Fehlende Footprints: {len(before.missing_footprints)} → {len(after.missing_footprints)}."))
    lines.append(t(f"Footprint ID differences: {len(before.footprint_mismatches)} → {len(after.footprint_mismatches)}.",
                   f"Footprint-ID-Abweichungen: {len(before.footprint_mismatches)} → {len(after.footprint_mismatches)}."))
    if fixed:
        lines.append(t("Corrected footprints: ", "Korrigierte Footprints: ") + ", ".join(fixed))
    if remaining:
        lines.append(t("Still different: ", "Weiterhin abweichend: ") + ", ".join(remaining[:20]))
    old_footprints = dict(before.board_footprints)
    new_footprints = dict(after.board_footprints)
    footprint_changes = [f"{ref}: {old_footprints.get(ref, '∅')} → {value}"
                         for ref, value in sorted(new_footprints.items())
                         if old_footprints.get(ref) != value]
    footprint_changes.extend(f"{ref}: {old_footprints[ref]} → ∅"
                             for ref in sorted(set(old_footprints) - set(new_footprints)))
    if footprint_changes:
        lines.append(t(f"Actually changed footprints ({len(footprint_changes)}):",
                       f"Tatsächlich geänderte Footprints ({len(footprint_changes)}):"))
        lines.extend(footprint_changes[:20])
    old_pads = {(ref, pin): net for ref, pin, net in before.board_pad_nets}
    new_pads = {(ref, pin): net for ref, pin, net in after.board_pad_nets}
    pad_changes = [f"{ref}.{pin}: {old_pads.get((ref, pin), '') or '∅'} → {net or '∅'}"
                   for (ref, pin), net in sorted(new_pads.items())
                   if old_pads.get((ref, pin)) != net]
    if pad_changes:
        lines.append(t(f"Actually changed pad nets ({len(pad_changes)}; first 20):",
                       f"Tatsächlich geänderte Pad-Netze ({len(pad_changes)}; erste 20):"))
        lines.extend(pad_changes[:20])
    if changed:
        lines.append(t("Run DRC and save the PCB after review.", "Nach der Prüfung DRC ausführen und PCB speichern."))
    return "\n".join(lines)
