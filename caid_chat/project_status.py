"""Compact project status derived from persisted, inspectable project files."""

from pathlib import Path


def overview(project_path, brief, language="en"):
    de = language == "de"
    project = Path(project_path)
    pcb = sorted(project.glob("*.kicad_pcb"))
    schematic = sorted(project.glob("*.kicad_sch"))
    requirements = brief.get("requirements", {})
    open_questions = brief.get("open_questions", [])
    active = brief.get("active_design")
    if active:
        open_questions = open_questions + active.get("questions", []) + [
            item.get("question", "") for item in active.get("guided", [])]
    conflicts = [topic for topic, record in requirements.items()
                 if record.get("status") == "conflict"]
    assumptions = [topic for topic, record in requirements.items()
                   if record.get("status") == "assumed"]
    lines = ["Projektzustand" if de else "Project status"]
    lines.append(("Dateien: " if de else "Files: ") +
                 (f"PCB {len(pcb)}, Schaltplan {len(schematic)}" if de else
                  f"PCB {len(pcb)}, schematic {len(schematic)}"))
    lines.append((f"Vorgaben: {len(requirements)} · offene Fragen: {len(open_questions)} · "
                  f"Annahmen: {len(assumptions)} · Widersprüche: {len(conflicts)}" if de else
                  f"Requirements: {len(requirements)} · open questions: {len(open_questions)} · "
                  f"assumptions: {len(assumptions)} · conflicts: {len(conflicts)}"))
    size = brief.get("pcb_size_mm")
    if isinstance(size, dict):
        mode = brief.get("pcb_size_mode")
        lines.append(f"PCB: {'max. ' if mode == 'maximum' else ''}"
                     f"{size['width_mm']:g} × {size['height_mm']:g} mm")
    if open_questions:
        lines.append(("Nächster offener Punkt: " if de else "Next open item: ") +
                     str(open_questions[0]))
    if conflicts:
        lines.append(("Widerspruch: " if de else "Conflict: ") + ", ".join(conflicts[:3]))
    generated = brief.get("generated_design")
    if generated:
        lines.append(("Erzeugungsstand (historisch): " if de else "Generation result (historical): ") +
                     f"ERC {generated.get('erc_errors', '?')}, DRC {generated.get('drc_errors', '?')}")
    lines.append(("Aktuelle ERC/DRC-Werte: /erc und /drc prüfen die gespeicherten Dateien."
                  if de else "Current ERC/DRC: use /erc and /drc to check the saved files."))
    return "\n".join(lines)
