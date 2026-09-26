"""Read-only KiCad CLI checks for the saved schematic."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

try:
    from .i18n import localized
    from .process import run_command
except ImportError:
    from i18n import localized
    from process import run_command


def _cli_path(language="en"):
    configured = os.environ.get("CAID_KICAD_CLI")
    if configured:
        return configured
    found = shutil.which("kicad-cli")
    if found:
        return found
    if sys.platform == "win32":
        candidate = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "KiCad" / "10.0" / "bin" / "kicad-cli.exe"
        if candidate.exists():
            return str(candidate)
    raise FileNotFoundError(localized(language, "kicad-cli not found. Set CAID_KICAD_CLI to its path.", "kicad-cli nicht gefunden. CAID_KICAD_CLI kann den Pfad festlegen."))


def summarize_erc(report, language="en"):
    t = lambda en, de: localized(language, en, de)
    violations = [item for sheet in report.get("sheets", [])
                  for item in sheet.get("violations", [])]
    errors = [item for item in violations if item.get("severity") == "error"]
    warnings = [item for item in violations if item.get("severity") == "warning"]
    lines = [t(f"Saved schematic: {len(errors)} errors, {len(warnings)} warnings.", f"Gespeicherter Schaltplan: {len(errors)} Fehler, {len(warnings)} Warnungen.")]
    for item in errors[:15]:
        details = "; ".join(entry.get("description", "") for entry in item.get("items", []))
        lines.append(f"• {item.get('description', item.get('type', t('Error', 'Fehler')))}: {details}")
    if len(errors) > 15:
        lines.append(t(f"… {len(errors) - 15} more errors.", f"… weitere {len(errors) - 15} Fehler."))
    if not violations:
        lines.append(t("No ERC findings.", "Keine ERC-Befunde."))
    return "\n".join(lines)


def run_erc(board, language="en", token=None):
    project_path = board.document.project.path
    schematic = Path(project_path) / (Path(board.name).stem + ".kicad_sch")
    if not schematic.exists():
        raise FileNotFoundError(localized(language, f"Schematic not found: {schematic}", f"Schaltplan nicht gefunden: {schematic}"))
    with tempfile.TemporaryDirectory(prefix="caid-erc-") as temp_dir:
        report_file = Path(temp_dir) / "erc.json"
        completed = run_command(
            [_cli_path(language), "sch", "erc", "--format", "json", "--output",
             str(report_file), str(schematic)],
            timeout=90, token=token,
        )
        if not report_file.exists():
            raise RuntimeError(completed.stderr.strip() or completed.stdout.strip() or localized(language, "ERC failed.", "ERC fehlgeschlagen."))
        report = json.loads(report_file.read_text(encoding="utf-8-sig"))
    return summarize_erc(report, language)


def summarize_drc(report, language="en"):
    t = lambda en, de: localized(language, en, de)
    violations = report.get("violations", [])
    errors = [item for item in violations if item.get("severity") == "error"]
    warnings = [item for item in violations if item.get("severity") == "warning"]
    parity = report.get("schematic_parity", [])
    unconnected = report.get("unconnected_items", [])
    lines = [t(f"Saved board: {len(errors)} DRC errors, {len(warnings)} DRC warnings.", f"Gespeicherte Platine: {len(errors)} DRC-Fehler, {len(warnings)} DRC-Warnungen."),
             t(f"Schematic parity: {len(parity)} differences. Unconnected items: {len(unconnected)}.", f"Schaltplan-Abgleich: {len(parity)} Abweichungen. Unverbundene Elemente: {len(unconnected)}.")]
    for item in errors[:12]:
        details = "; ".join(entry.get("description", "") for entry in item.get("items", []))
        lines.append(f"• {item.get('description', item.get('type', t('Error', 'Fehler')))}: {details}")
    if len(errors) > 12:
        lines.append(t(f"… {len(errors) - 12} more DRC errors.", f"… weitere {len(errors) - 12} DRC-Fehler."))
    return "\n".join(lines)


def read_drc_report(board, language="en", token=None):
    project_path = board.document.project.path
    pcb = Path(project_path) / board.name
    if not pcb.exists():
        raise FileNotFoundError(localized(language, f"Board file not found: {pcb}", f"Platinen-Datei nicht gefunden: {pcb}"))
    with tempfile.TemporaryDirectory(prefix="caid-drc-") as temp_dir:
        report_file = Path(temp_dir) / "drc.json"
        completed = run_command(
            [_cli_path(language), "pcb", "drc", "--format", "json", "--schematic-parity",
             "--output", str(report_file), str(pcb)],
            timeout=90, token=token,
        )
        if not report_file.exists():
            raise RuntimeError(completed.stderr.strip() or completed.stdout.strip() or localized(language, "DRC failed.", "DRC fehlgeschlagen."))
        report = json.loads(report_file.read_text(encoding="utf-8-sig"))
    return report


def run_drc(board, language="en", token=None):
    return summarize_drc(read_drc_report(board, language, token), language)
