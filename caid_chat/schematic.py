"""Saved KiCad schematic access and reviewed edits through an isolated copy."""

from dataclasses import dataclass
from datetime import datetime
import difflib
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from xml.etree import ElementTree

try:
    from .diagnostics import _cli_path
    from .footprints import find_footprint
    from .i18n import localized
    from .process import run_command
    from .schematic_fields import (rewrite_footprint_fields, rewrite_local_net_labels,
                                   rewrite_symbol_fields)
    from .schematic_fields import _root_forms
    from .schematic_connections import (rewrite_no_connect_markers, rewrite_pin_connections,
                                        rewrite_pin_disconnections)
except ImportError:  # KiCad starts main.py directly from the plugin directory.
    from diagnostics import _cli_path
    from footprints import find_footprint
    from i18n import localized
    from process import run_command
    from schematic_fields import (rewrite_footprint_fields, rewrite_local_net_labels,
                                  rewrite_symbol_fields)
    from schematic_fields import _root_forms
    from schematic_connections import (rewrite_no_connect_markers, rewrite_pin_connections,
                                       rewrite_pin_disconnections)


_NOTE_FORM = re.compile(r'^\((?:text|text_box)\s+("(?:\\.|[^"\\])*")', re.DOTALL)


def _schematic_notes(source):
    """Read bounded free-text notes from the saved root sheet, not the netlist."""
    notes = []
    count = 0
    length = 0
    truncated = False
    for start, end in _root_forms(source):
        match = _NOTE_FORM.match(source[start:end])
        if match is None:
            continue
        try:
            note = json.loads(match.group(1)).strip()
        except ValueError:
            continue
        if not note:
            continue
        count += 1
        excerpt = note[:500]
        if len(notes) < 40 and length + len(excerpt) <= 8000:
            notes.append(excerpt)
            length += len(excerpt)
            truncated |= len(note) > len(excerpt)
        else:
            truncated = True
    return notes, count, truncated


def schematic_path(board_snapshot, language="en"):
    path = Path(board_snapshot["project_path"]) / (Path(board_snapshot["document"]).stem + ".kicad_sch")
    if not path.is_file():
        raise FileNotFoundError(localized(language, f"Saved schematic not found: {path}", f"Gespeicherter Schaltplan nicht gefunden: {path}"))
    return path


def _run_cli_netlist(schematic, output, language="en", token=None):
    completed = run_command(
        [_cli_path(language), "sch", "export", "netlist", "--format", "kicadxml",
         "--output", str(output), str(schematic)],
        timeout=90, token=token,
    )
    if completed.returncode or not output.is_file():
        raise RuntimeError(localized(language, "KiCad could not read the schematic as a netlist: ", "KiCad konnte den Schaltplan nicht als Netzliste lesen: ") +
                           (completed.stderr.strip() or completed.stdout.strip())[-500:])
    return ElementTree.parse(output).getroot()


def _netlist_snapshot(root, path, *, full=False):
    source = path.read_bytes()
    notes, note_count, notes_truncated = _schematic_notes(source.decode("utf-8-sig"))
    components = []
    for comp in root.findall("./components/comp"):
        components.append({
            "ref": comp.get("ref", ""),
            "value": comp.findtext("value", default=""),
            "footprint": comp.findtext("footprint", default=""),
        })
    nets = []
    for net in root.findall("./nets/net"):
        nets.append({
            "name": net.get("name", ""),
            "nodes": [{"ref": node.get("ref", ""), "pin": node.get("pin", "")}
                      for node in net.findall("node")],
        })
    return {
        "document": path.name,
        "saved_file_sha256": hashlib.sha256(source).hexdigest(),
        "component_count": len(components),
        "net_count": len(nets),
        "components": components if full else components[:200],
        "nets": nets if full else nets[:300],
        "truncated": False if full else len(components) > 200 or len(nets) > 300,
        "notes": notes,
        "note_count": note_count,
        "notes_truncated": notes_truncated,
        "notes_scope": "saved_root_sheet",
    }


def _validate_circuit_candidate(before, after, language="en"):
    """Reject a text-only or largely erased circuit before offering Apply."""
    old_components = before["component_count"]
    new_components = after["component_count"]
    if new_components == 0:
        raise ValueError(localized(
            language,
            "The proposal contains no placed symbols. Text notes alone cannot update the PCB; the existing schematic was kept.",
            "Der Vorschlag enthält keine platzierten Symbole. Textnotizen allein können die Platine nicht aktualisieren; der bisherige Schaltplan blieb erhalten."))
    if old_components >= 4 and new_components * 2 < old_components:
        raise ValueError(localized(
            language,
            f"The proposal would remove most schematic components ({old_components} → {new_components}); the existing schematic was kept.",
            f"Der Vorschlag würde die meisten Schaltplanbauteile entfernen ({old_components} → {new_components}); der bisherige Schaltplan blieb erhalten."))
    if after["net_count"] == 0 and (before["net_count"] > 0 or old_components == 0):
        raise ValueError(localized(
            language,
            "The proposal has no connected nets. It is not a usable new circuit; the existing schematic was kept.",
            "Der Vorschlag enthält keine verbundenen Netze. Er ist keine nutzbare neue Schaltung; der bisherige Schaltplan blieb erhalten."))


def read_schematic(board_snapshot, language="en", token=None):
    """Read the on-disk schematic; KiCad CLI resolves labels and connectivity."""
    path = schematic_path(board_snapshot, language)
    with tempfile.TemporaryDirectory(prefix="caid-netlist-") as temp_dir:
        root = _run_cli_netlist(path, Path(temp_dir) / "netlist.xml", language, token)
    return _netlist_snapshot(root, path)


def _erc_counts(schematic, output, language="en", token=None):
    completed = run_command(
        [_cli_path(language), "sch", "erc", "--format", "json", "--output", str(output), str(schematic)],
        timeout=90, token=token,
    )
    if not output.is_file():
        raise RuntimeError(localized(language, "KiCad ERC could not check the design: ", "KiCad ERC konnte den Entwurf nicht prüfen: ") +
                           (completed.stderr.strip() or completed.stdout.strip())[-500:])
    data = json.loads(output.read_text(encoding="utf-8-sig"))
    violations = [v for sheet in data.get("sheets", []) for v in sheet.get("violations", [])]
    return (sum(v.get("severity") == "error" for v in violations),
            sum(v.get("severity") == "warning" for v in violations))


@dataclass
class StagedSchematic:
    original: Path
    candidate: Path
    stage_dir: Path
    original_hash: str
    diff: str
    agent_answer: str
    erc_before: tuple[int, int]
    erc_after: tuple[int, int]
    candidate_snapshot: dict | None = None
    before_snapshot: dict | None = None

    def cleanup(self):
        shutil.rmtree(self.stage_dir, ignore_errors=True)


def _wsl_path(windows_path, language="en", token=None):
    completed = run_command(
        ["wsl.exe", "--exec", "wslpath", "-a", str(windows_path)],
        timeout=15, token=token,
    )
    if completed.returncode:
        raise RuntimeError(localized(language, "Could not resolve WSL path: ", "WSL-Pfad konnte nicht ermittelt werden: ") + completed.stderr.strip()[-300:])
    return completed.stdout.strip()


def _copy_project_support(source, stage_dir):
    support = []
    project = source.with_suffix(".kicad_pro")
    for name in (project.name, "fp-lib-table", "sym-lib-table"):
        path = source.parent / name
        if path.is_file():
            shutil.copy2(path, stage_dir / name)
            support.append(stage_dir / name)
    for library in source.parent.glob("*.kicad_sym"):
        shutil.copy2(library, stage_dir / library.name)
        support.append(stage_dir / library.name)
    for library in source.parent.glob("*.pretty"):
        shutil.copytree(library, stage_dir / library.name)
        support.extend((stage_dir / library.name).rglob("*"))
    return support


def stage_schematic_edit(board_snapshot, instruction, model, language="en", token=None):
    """Let Codex edit only a disposable copy, then validate and return a diff."""
    if not instruction.strip():
        raise ValueError(localized(language, "Describe the desired schematic change.", "Beschreibe die gewünschte Schaltplanänderung."))
    source = schematic_path(board_snapshot, language)
    stage_dir = Path(tempfile.mkdtemp(prefix="caid-schematic-"))
    candidate = stage_dir / source.name
    original = source.read_bytes()
    original_hash = hashlib.sha256(original).hexdigest()
    try:
        candidate.write_bytes(original)
        support = _copy_project_support(source, stage_dir)
        support_hashes = {
            path: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in support if path.is_file()
        }
        prompt = (
            f"Bearbeite ausschließlich die Datei {source.name} in diesem Arbeitsverzeichnis. "
            "Dies ist eine isolierte Kopie eines KiCad-10-Schaltplans. "
            "Die Originaldatei darfst du nicht lesen oder ändern. "
            "Bewahre gültiges KiCad-S-Expression-Format und vorhandene UUIDs. "
            "Vergib eindeutige Referenzen und UUIDs für neu hinzugefügte Objekte. "
            "Nutze vorhandene eingebettete Symbole; bei neuen Symbolen müssen die "
            "Bibliotheksdefinitionen korrekt eingebettet sein. "
            "Rate keine elektrischen Pinbelegungen; benenne fehlende Daten klar. "
            "Ersetze vorhandene Symbole oder Netze niemals durch reine Textnotizen. "
            "Wenn ein belastbarer Entwurf mangels Daten nicht möglich ist, ändere die Datei nicht "
            "und erkläre die fehlenden Angaben in deiner Antwort. "
            "Ändere keine Projektdatei. Antworte abschließend auf " +
            ("Deutsch" if language == "de" else "Englisch") + ". Aufgabe des Nutzers:\n" + instruction
        )
        answer_file = stage_dir / "codex-answer.txt"
        command = [
            "wsl.exe", "--exec", "bash", "-ic",
            'exec codex exec --ignore-user-config --sandbox workspace-write --ephemeral '
            '--skip-git-repo-check --output-last-message "$2" -C "$1" -m "$3" -',
            "caid", _wsl_path(stage_dir, language, token), _wsl_path(answer_file, language, token), model.strip(),
        ]
        try:
            completed = run_command(command, input=prompt, timeout=360, token=token)
        except subprocess.TimeoutExpired:
            raise RuntimeError(localized(language, "Codex did not finish the schematic edit within six minutes.", "Codex hat die Schaltplanbearbeitung nach sechs Minuten nicht abgeschlossen.")) from None
        if completed.returncode:
            raise RuntimeError(localized(language, "Codex could not edit the working copy: ", "Codex konnte die Arbeitskopie nicht bearbeiten: ") + completed.stderr.strip()[-600:])
        if not candidate.is_file() or not 0 < candidate.stat().st_size < 4_000_000:
            raise RuntimeError(localized(language, "The schematic working copy is missing or unusually large.", "Die Schaltplan-Arbeitskopie fehlt oder ist ungewöhnlich groß."))
        if any(not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest
               for path, digest in support_hashes.items()):
            raise RuntimeError(localized(language, "The working copy changed project or library files; change rejected.", "Die Arbeitskopie hat Projekt- oder Bibliotheksdateien verändert; Änderung verworfen."))
        updated = candidate.read_bytes()
        if updated == original:
            detail = answer_file.read_text(encoding="utf-8")[:600] if answer_file.exists() else ""
            raise ValueError(localized(language, "Codex made no change to the schematic file. ",
                                       "Codex hat die Schaltplan-Datei nicht geändert. ") + detail)
        with tempfile.TemporaryDirectory(prefix="caid-sch-check-") as check_dir:
            before_root = _run_cli_netlist(source, Path(check_dir) / "before.xml", language, token)
            before_snapshot = _netlist_snapshot(before_root, source)
            candidate_root = _run_cli_netlist(candidate, Path(check_dir) / "candidate.xml", language, token)
            candidate_snapshot = _netlist_snapshot(candidate_root, candidate)
            _validate_circuit_candidate(before_snapshot, candidate_snapshot, language)
            before = _erc_counts(source, Path(check_dir) / "before.json", language, token)
            after = _erc_counts(candidate, Path(check_dir) / "after.json", language, token)
        old_lines = original.decode("utf-8").splitlines(keepends=True)
        new_lines = updated.decode("utf-8").splitlines(keepends=True)
        diff = "".join(difflib.unified_diff(old_lines, new_lines,
                       fromfile=source.name + localized(language, " (before)", " (vorher)"),
                       tofile=source.name + localized(language, " (proposal)", " (Entwurf)")))
        (stage_dir / "change.diff").write_text(diff, encoding="utf-8")
        return StagedSchematic(
            source, candidate, stage_dir, original_hash, diff,
            answer_file.read_text(encoding="utf-8") if answer_file.exists() else "",
            before, after, candidate_snapshot,
        )
    except Exception:
        shutil.rmtree(stage_dir, ignore_errors=True)
        raise


def stage_footprint_updates(board_snapshot, updates, language="en", token=None, standard_root=None):
    """Stage exact, installed footprint IDs on an isolated schematic copy."""
    source = schematic_path(board_snapshot, language)
    desired = {}
    for entry in updates:
        ref, identifier = entry["ref"], entry["footprint_id"]
        if ref in desired:
            raise ValueError(localized(language, f"Duplicate footprint update for {ref}.",
                                       f"Doppelte Footprint-Änderung für {ref}."))
        desired[ref] = identifier
    installed = {}
    for ref, identifier in desired.items():
        found = find_footprint(source.parent, identifier, standard_root)
        if found is None:
            raise ValueError(localized(language, f"Footprint {identifier} is not installed for {ref}.",
                                       f"Footprint {identifier} ist für {ref} nicht installiert."))
        installed[ref] = found
    original = source.read_bytes()
    updated_text, previous = rewrite_footprint_fields(original.decode("utf-8-sig"), desired)
    stage_dir = Path(tempfile.mkdtemp(prefix="caid-footprints-"))
    candidate = stage_dir / source.name
    try:
        candidate.write_text(updated_text, encoding="utf-8")
        _copy_project_support(source, stage_dir)
        with tempfile.TemporaryDirectory(prefix="caid-fp-check-") as check_dir:
            candidate_root = _run_cli_netlist(candidate, Path(check_dir) / "candidate.xml", language, token)
            candidate_snapshot = _netlist_snapshot(candidate_root, candidate)
            before = _erc_counts(source, Path(check_dir) / "before.json", language, token)
            after = _erc_counts(candidate, Path(check_dir) / "after.json", language, token)
        by_ref = {item["ref"]: item for item in candidate_snapshot["components"]}
        if any(by_ref.get(ref, {}).get("footprint") != identifier for ref, identifier in desired.items()):
            raise RuntimeError(localized(language, "KiCad netlist does not contain the requested footprint assignments.",
                                         "KiCads Netzliste enthält nicht die gewünschten Footprint-Zuordnungen."))
        diff = "".join(difflib.unified_diff(
            original.decode("utf-8-sig").splitlines(keepends=True), updated_text.splitlines(keepends=True),
            fromfile=source.name + localized(language, " (before)", " (vorher)"),
            tofile=source.name + localized(language, " (proposal)", " (Entwurf)")))
        (stage_dir / "change.diff").write_text(diff, encoding="utf-8")
        pad_label = localized(language, "pads", "Pads")
        details = ", ".join(f"{ref}: {previous[ref] or '∅'} → {identifier} "
                            f"({installed[ref]['pad_count']} {pad_label})" for ref, identifier in desired.items())
        note = localized(language, "Installed library files found; physical package fit is not verified.",
                         "Installierte Bibliotheksdateien gefunden; die Passung zum physischen Gehäuse ist nicht geprüft.")
        return StagedSchematic(source, candidate, stage_dir, hashlib.sha256(original).hexdigest(),
                               diff, details + "\n" + note, before, after, candidate_snapshot)
    except Exception:
        shutil.rmtree(stage_dir, ignore_errors=True)
        raise


def stage_field_updates(board_snapshot, updates, language="en", token=None, standard_root=None):
    """Stage bounded Value/Footprint updates from any model on an isolated copy."""
    source = schematic_path(board_snapshot, language)
    if not 1 <= len(updates) <= 20:
        raise ValueError("Provide 1 to 20 schematic field updates")
    desired = {}
    for entry in updates:
        if not isinstance(entry, dict) or set(entry) != {"ref", "field", "value"}:
            raise ValueError("Invalid schematic field update")
        key = (entry["ref"], entry["field"])
        if key in desired:
            raise ValueError(f"Duplicate schematic field update: {key[0]}.{key[1]}")
        desired[key] = entry["value"]
    for (ref, field), value in desired.items():
        if field == "Footprint" and find_footprint(source.parent, value, standard_root) is None:
            raise ValueError(f"Footprint {value} is not installed for {ref}")
    original = source.read_bytes()
    updated_text, previous = rewrite_symbol_fields(original.decode("utf-8-sig"), desired)
    stage_dir = Path(tempfile.mkdtemp(prefix="caid-fields-"))
    candidate = stage_dir / source.name
    try:
        candidate.write_text(updated_text, encoding="utf-8")
        _copy_project_support(source, stage_dir)
        with tempfile.TemporaryDirectory(prefix="caid-field-check-") as check_dir:
            before_root = _run_cli_netlist(source, Path(check_dir) / "before.xml", language, token)
            candidate_root = _run_cli_netlist(candidate, Path(check_dir) / "after.xml", language, token)
            before_snapshot = _netlist_snapshot(before_root, source, full=True)
            candidate_snapshot = _netlist_snapshot(candidate_root, candidate, full=True)
            before_erc = _erc_counts(source, Path(check_dir) / "before.json", language, token)
            after_erc = _erc_counts(candidate, Path(check_dir) / "after.json", language, token)
        def topology(root):
            components = {comp.get("ref", "") for comp in root.findall("./components/comp")}
            nets = {net.get("name", ""): sorted((node.get("ref", ""), node.get("pin", ""))
                    for node in net.findall("node")) for net in root.findall("./nets/net")}
            return components, nets
        if topology(before_root) != topology(candidate_root):
            raise RuntimeError("KiCad netlist topology changed during a field-only update")
        by_ref = {comp.get("ref", ""): comp for comp in candidate_root.findall("./components/comp")}
        for (ref, field), value in desired.items():
            actual = by_ref[ref].findtext(field.casefold(), default="") if ref in by_ref else None
            if actual != value:
                raise RuntimeError(f"KiCad netlist does not contain {ref}.{field} = {value}")
        diff = "".join(difflib.unified_diff(
            original.decode("utf-8-sig").splitlines(keepends=True), updated_text.splitlines(keepends=True),
            fromfile=source.name + " (before)", tofile=source.name + " (proposal)"))
        (stage_dir / "change.diff").write_text(diff, encoding="utf-8")
        details = ", ".join(f"{ref}.{field}: {previous[(ref, field)] or '∅'} → {value}"
                            for ref, field in desired)
        return StagedSchematic(source, candidate, stage_dir, hashlib.sha256(original).hexdigest(),
                               diff, details, before_erc, after_erc, candidate_snapshot, before_snapshot)
    except Exception:
        shutil.rmtree(stage_dir, ignore_errors=True)
        raise


def stage_net_renames(board_snapshot, renames, language="en", token=None):
    """Stage exact local-label renames and verify every resulting KiCad net."""
    source = schematic_path(board_snapshot, language)
    if not isinstance(renames, list) or not 1 <= len(renames) <= 10 or any(
            not isinstance(item, dict) or set(item) != {"from", "to"} for item in renames):
        raise ValueError("Provide 1 to 10 local net renames")
    desired = {}
    for item in renames:
        if item["from"] in desired:
            raise ValueError("Duplicate source net: " + str(item["from"]))
        desired[item["from"]] = item["to"]
    original = source.read_bytes()
    updated_text, counts = rewrite_local_net_labels(original.decode("utf-8-sig"), desired)
    stage_dir = Path(tempfile.mkdtemp(prefix="caid-nets-"))
    candidate = stage_dir / source.name
    try:
        candidate.write_text(updated_text, encoding="utf-8")
        _copy_project_support(source, stage_dir)
        with tempfile.TemporaryDirectory(prefix="caid-net-check-") as check_dir:
            before_root = _run_cli_netlist(source, Path(check_dir) / "before.xml", language, token)
            after_root = _run_cli_netlist(candidate, Path(check_dir) / "after.xml", language, token)
            before_snapshot = _netlist_snapshot(before_root, source, full=True)
            after_snapshot = _netlist_snapshot(after_root, candidate, full=True)
            before_erc = _erc_counts(source, Path(check_dir) / "before.json", language, token)
            after_erc = _erc_counts(candidate, Path(check_dir) / "after.json", language, token)
        if before_snapshot["components"] != after_snapshot["components"]:
            raise RuntimeError("KiCad components changed during a net rename")
        def net_map(root):
            return {net.get("name", ""): sorted((node.get("ref", ""), node.get("pin", ""))
                    for node in net.findall("node")) for net in root.findall("./nets/net")}
        before_nets = net_map(before_root)
        after_nets = net_map(after_root)
        old_names = {"/" + old for old in desired}
        new_names = {"/" + new for new in desired.values()}
        if not old_names <= before_nets.keys() or new_names & before_nets.keys():
            raise ValueError("Source net missing or target net already exists in KiCad netlist")
        expected = {"/" + desired[name[1:]] if name in old_names else name: nodes
                    for name, nodes in before_nets.items()}
        if after_nets != expected:
            raise RuntimeError("KiCad netlist changed beyond the requested net names")
        diff = "".join(difflib.unified_diff(
            original.decode("utf-8-sig").splitlines(keepends=True),
            updated_text.splitlines(keepends=True),
            fromfile=source.name + " (before)", tofile=source.name + " (proposal)"))
        (stage_dir / "change.diff").write_text(diff, encoding="utf-8")
        details = ", ".join(f"{old} → {new} ({counts[old]} labels)"
                            for old, new in desired.items())
        return StagedSchematic(source, candidate, stage_dir,
                               hashlib.sha256(original).hexdigest(), diff, details,
                               before_erc, after_erc, after_snapshot, before_snapshot)
    except Exception:
        shutil.rmtree(stage_dir, ignore_errors=True)
        raise


def stage_pin_connections(board_snapshot, requests, language="en", token=None):
    """Stage labels on unconnected pins and verify the exact KiCad netlist delta."""
    source = schematic_path(board_snapshot, language)
    original = source.read_bytes()
    updated_text, _ = rewrite_pin_connections(original.decode("utf-8-sig"), requests)
    stage_dir = Path(tempfile.mkdtemp(prefix="caid-pin-connections-"))
    candidate = stage_dir / source.name
    try:
        candidate.write_text(updated_text, encoding="utf-8")
        _copy_project_support(source, stage_dir)
        with tempfile.TemporaryDirectory(prefix="caid-pin-check-") as check_dir:
            before_root = _run_cli_netlist(source, Path(check_dir) / "before.xml", language, token)
            after_root = _run_cli_netlist(candidate, Path(check_dir) / "after.xml", language, token)
            before_snapshot = _netlist_snapshot(before_root, source, full=True)
            after_snapshot = _netlist_snapshot(after_root, candidate, full=True)
            before_erc = _erc_counts(source, Path(check_dir) / "before.json", language, token)
            after_erc = _erc_counts(candidate, Path(check_dir) / "after.json", language, token)
        if before_snapshot["components"] != after_snapshot["components"]:
            raise RuntimeError("KiCad components changed during a pin connection")
        def net_map(root):
            return {net.get("name", ""): sorted((node.get("ref", ""), node.get("pin", ""))
                    for node in net.findall("node")) for net in root.findall("./nets/net")}
        before_nets = net_map(before_root)
        after_nets = net_map(after_root)
        expected = {name: list(nodes) for name, nodes in before_nets.items()}
        for item in requests:
            node = (item["ref"], item["pin"])
            name = "/" + item["net"]
            existing_names = [old for old, nodes in before_nets.items() if node in nodes]
            if existing_names:
                if (len(existing_names) != 1 or
                        existing_names[0] != f"unconnected-({node[0]}-Pad{node[1]})" or
                        before_nets[existing_names[0]] != [node]):
                    raise ValueError(f"Pin {node[0]}.{node[1]} is already connected")
                expected.pop(existing_names[0])
            if name not in expected:
                raise ValueError(f"Local net {item['net']} is absent from the KiCad netlist")
            expected[name].append(node)
        expected = {name: sorted(nodes) for name, nodes in expected.items()}
        if after_nets != expected:
            raise RuntimeError("KiCad netlist changed beyond the requested pin connections")
        if after_erc[0] > before_erc[0]:
            raise RuntimeError("Pin connection introduced new ERC errors")
        diff = "".join(difflib.unified_diff(
            original.decode("utf-8-sig").splitlines(keepends=True),
            updated_text.splitlines(keepends=True),
            fromfile=source.name + " (before)", tofile=source.name + " (proposal)"))
        (stage_dir / "change.diff").write_text(diff, encoding="utf-8")
        details = ", ".join(f"{item['ref']}.{item['pin']} → {item['net']}" for item in requests)
        return StagedSchematic(source, candidate, stage_dir,
                               hashlib.sha256(original).hexdigest(), diff, details,
                               before_erc, after_erc, after_snapshot, before_snapshot)
    except Exception:
        shutil.rmtree(stage_dir, ignore_errors=True)
        raise


def stage_no_connect_markers(board_snapshot, requests, language="en", token=None):
    """Stage explicit unused-pin markers with exact KiCad netlist/ERC checks."""
    source = schematic_path(board_snapshot, language)
    original = source.read_bytes()
    updated_text, _ = rewrite_no_connect_markers(original.decode("utf-8-sig"), requests)
    stage_dir = Path(tempfile.mkdtemp(prefix="caid-no-connects-"))
    candidate = stage_dir / source.name
    try:
        candidate.write_text(updated_text, encoding="utf-8")
        _copy_project_support(source, stage_dir)
        with tempfile.TemporaryDirectory(prefix="caid-nc-check-") as check_dir:
            before_root = _run_cli_netlist(source, Path(check_dir) / "before.xml", language, token)
            after_root = _run_cli_netlist(candidate, Path(check_dir) / "after.xml", language, token)
            before_snapshot = _netlist_snapshot(before_root, source, full=True)
            after_snapshot = _netlist_snapshot(after_root, candidate, full=True)
            before_erc = _erc_counts(source, Path(check_dir) / "before.json", language, token)
            after_erc = _erc_counts(candidate, Path(check_dir) / "after.json", language, token)
        if before_snapshot["components"] != after_snapshot["components"]:
            raise RuntimeError("KiCad components changed during a no-connect edit")
        def net_map(root):
            return {net.get("name", ""): sorted((node.get("ref", ""), node.get("pin", ""))
                    for node in net.findall("node")) for net in root.findall("./nets/net")}
        before_nets = net_map(before_root)
        after_nets = net_map(after_root)
        expected = {name: list(nodes) for name, nodes in before_nets.items()}
        removable = set()
        for item in requests:
            node = (item["ref"], item["pin"])
            names = [name for name, nodes in before_nets.items() if node in nodes]
            auto_name = f"unconnected-({node[0]}-Pad{node[1]})"
            if names and (names != [auto_name] or before_nets[auto_name] != [node]):
                raise ValueError(f"Pin {node[0]}.{node[1]} is already connected")
            if names:
                removable.add(auto_name)
        for name in removable:
            if name not in after_nets:
                expected.pop(name)
        if after_nets != expected:
            raise RuntimeError("KiCad netlist changed during a no-connect edit")
        if after_erc[0] > before_erc[0]:
            raise RuntimeError("No-connect marker introduced new ERC errors")
        diff = "".join(difflib.unified_diff(
            original.decode("utf-8-sig").splitlines(keepends=True),
            updated_text.splitlines(keepends=True),
            fromfile=source.name + " (before)", tofile=source.name + " (proposal)"))
        (stage_dir / "change.diff").write_text(diff, encoding="utf-8")
        details = ", ".join(f"{item['ref']}.{item['pin']}: no-connect" for item in requests)
        return StagedSchematic(source, candidate, stage_dir,
                               hashlib.sha256(original).hexdigest(), diff, details,
                               before_erc, after_erc, after_snapshot, before_snapshot)
    except Exception:
        shutil.rmtree(stage_dir, ignore_errors=True)
        raise


def stage_pin_disconnections(board_snapshot, requests, language="en", token=None):
    """Stage removal of pin labels and verify only requested pins leave local nets."""
    source = schematic_path(board_snapshot, language)
    original = source.read_bytes()
    updated_text, _ = rewrite_pin_disconnections(original.decode("utf-8-sig"), requests)
    stage_dir = Path(tempfile.mkdtemp(prefix="caid-pin-disconnections-"))
    candidate = stage_dir / source.name
    try:
        candidate.write_text(updated_text, encoding="utf-8")
        _copy_project_support(source, stage_dir)
        with tempfile.TemporaryDirectory(prefix="caid-disconnect-check-") as check_dir:
            before_root = _run_cli_netlist(source, Path(check_dir) / "before.xml", language, token)
            after_root = _run_cli_netlist(candidate, Path(check_dir) / "after.xml", language, token)
            before_snapshot = _netlist_snapshot(before_root, source, full=True)
            after_snapshot = _netlist_snapshot(after_root, candidate, full=True)
            before_erc = _erc_counts(source, Path(check_dir) / "before.json", language, token)
            after_erc = _erc_counts(candidate, Path(check_dir) / "after.json", language, token)
        if before_snapshot["components"] != after_snapshot["components"]:
            raise RuntimeError("KiCad components changed during a pin disconnection")
        def net_map(root):
            return {net.get("name", ""): sorted((node.get("ref", ""), node.get("pin", ""))
                    for node in net.findall("node")) for net in root.findall("./nets/net")}
        before_nets = net_map(before_root)
        after_nets = net_map(after_root)
        expected = {name: list(nodes) for name, nodes in before_nets.items()}
        for item in requests:
            node = (item["ref"], item["pin"])
            name = "/" + item["net"]
            if name not in expected or node not in expected[name]:
                raise ValueError(f"Pin {node[0]}.{node[1]} is not on local net {item['net']}")
            expected[name].remove(node)
            auto_name = f"unconnected-({node[0]}-Pad{node[1]})"
            if auto_name in after_nets:
                if after_nets[auto_name] != [node]:
                    raise RuntimeError("KiCad assigned unexpected pins to a disconnected net")
                expected[auto_name] = [node]
        expected = {name: sorted(nodes) for name, nodes in expected.items() if nodes}
        if after_nets != expected:
            raise RuntimeError("KiCad netlist changed beyond the requested pin disconnections")
        diff = "".join(difflib.unified_diff(
            original.decode("utf-8-sig").splitlines(keepends=True),
            updated_text.splitlines(keepends=True),
            fromfile=source.name + " (before)", tofile=source.name + " (proposal)"))
        (stage_dir / "change.diff").write_text(diff, encoding="utf-8")
        details = ", ".join(f"{item['ref']}.{item['pin']}: {item['net']} → unconnected"
                            for item in requests)
        return StagedSchematic(source, candidate, stage_dir,
                               hashlib.sha256(original).hexdigest(), diff, details,
                               before_erc, after_erc, after_snapshot, before_snapshot)
    except Exception:
        shutil.rmtree(stage_dir, ignore_errors=True)
        raise


def apply_schematic_edit(staged, language="en"):
    """Back up and atomically replace a closed, unchanged schematic file."""
    source = staged.original
    if not staged.candidate.is_file():
        raise FileNotFoundError(localized(language, "The working copy no longer exists.", "Die Arbeitskopie ist nicht mehr vorhanden."))
    lock = source.with_name("~" + source.name + ".lck")
    if lock.exists():
        raise RuntimeError(localized(language, "The schematic is open in KiCad. Close the schematic editor and retry.", "Der Schaltplan ist in KiCad geöffnet. Schließe den Schaltplan-Editor und versuche es erneut."))
    original = source.read_bytes()
    if hashlib.sha256(original).hexdigest() != staged.original_hash:
        raise RuntimeError(localized(language, "The schematic changed since the preview. Create a new proposal.", "Der Schaltplan hat sich seit der Vorschau geändert. Bitte den Entwurf neu erzeugen."))
    backup = source.with_name(source.stem + ".caid-backup-" +
                              datetime.now().strftime("%Y%m%d-%H%M%S-%f") +
                              source.suffix)
    with backup.open("xb") as stream:
        stream.write(original)
        stream.flush()
        os.fsync(stream.fileno())
    with tempfile.NamedTemporaryFile(dir=source.parent, prefix=".caid-", suffix=source.suffix, delete=False) as stream:
        temp_path = Path(stream.name)
        stream.write(staged.candidate.read_bytes())
        stream.flush()
        os.fsync(stream.fileno())
    try:
        if lock.exists() or source.read_bytes() != original:
            raise RuntimeError(localized(language,
                                         "The schematic changed or opened while applying. Create a new proposal.",
                                         "Der Schaltplan wurde während der Übernahme geöffnet oder geändert. Bitte neu vorschlagen lassen."))
        os.replace(temp_path, source)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise
    staged.cleanup()
    return backup
