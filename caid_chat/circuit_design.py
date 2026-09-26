"""Deterministic, bounded KiCad 10 schematic drafts from a pin/net design model.

This creates a new single-sheet schematic in an isolated directory. It never
modifies a live project; KiCad CLI validation is required before using a draft.
"""

import json
from math import isclose
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import uuid

try:
    from .footprints import find_footprint
    from .schematic_fields import _root_forms
    from .schematic import _run_cli_netlist, _erc_counts
    from .diagnostics import _cli_path
    from .process import run_command
except ImportError:
    from footprints import find_footprint
    from schematic_fields import _root_forms
    from schematic import _run_cli_netlist, _erc_counts
    from diagnostics import _cli_path
    from process import run_command


_ID = re.compile(r"^[A-Za-z0-9_.+\-]+:[A-Za-z0-9_.+\-]+$")
_REF = re.compile(r"^[A-Za-z]{1,4}[1-9][0-9]*$")
_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,60}$")
_QUOTED = r'("(?:\\.|[^"\\])*")'
_PIN_AT = re.compile(r"\(at\s+(-?[0-9.]+)\s+(-?[0-9.]+)\s+(-?[0-9.]+)\)")
_PIN_NUMBER = re.compile(r"\(number\s+" + _QUOTED)
_PAD_NUMBER = re.compile(r"\(pad\s+" + _QUOTED)
_SYMBOL_LIBRARY = re.compile(r'\(lib\s+\(name\s+"([^"\\]+)"\).*?\(uri\s+"\$\{KIPRJMOD\}/([^"\\]+)"\)', re.S)


def _quote(value):
    return json.dumps(value, ensure_ascii=False)


def _symbol_root(symbol_root=None):
    if symbol_root is not None:
        return Path(symbol_root)
    configured = os.environ.get("KICAD10_SYMBOL_DIR") or os.environ.get("KICAD_SYMBOL_DIR")
    if configured:
        return Path(configured)
    if os.name == "nt":
        return Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "KiCad/10.0/share/kicad/symbols"
    return Path("/usr/share/kicad/symbols")


def _project_symbol_files(project_dir):
    """Only resolve symbol libraries contained within the project directory."""
    project = Path(project_dir).resolve()
    table = project / "sym-lib-table"
    if not table.is_file():
        return {}
    result = {}
    for nickname, relative in _SYMBOL_LIBRARY.findall(table.read_text(encoding="utf-8-sig")):
        candidate = (project / relative).resolve()
        if candidate.is_relative_to(project) and candidate.is_file():
            result[nickname] = candidate
    return result


def _library_symbol(identifier, root, project_symbols=None):
    if not _ID.fullmatch(identifier):
        raise ValueError(f"Invalid symbol ID: {identifier}")
    library, name = identifier.split(":", 1)
    path = (project_symbols or {}).get(library, root / (library + ".kicad_sym"))
    if not path.is_file():
        raise ValueError(f"Symbol library not found: {library}")
    source = path.read_text(encoding="utf-8-sig")
    for start, end in _root_forms(source):
        form = source[start:end]
        match = re.match(r"\(symbol\s+" + _QUOTED, form)
        if match is None or json.loads(match.group(1)) != name:
            continue
        if re.search(r"\(extends\s", form):
            raise ValueError(f"Inherited symbol is not supported yet: {identifier}")
        pins = {}
        for unit_start, unit_end in _root_forms(form):
            unit = form[unit_start:unit_end]
            unit_name = re.match(r"\(symbol\s+" + _QUOTED, unit)
            if unit_name is None:
                continue
            suffix = json.loads(unit_name.group(1)).rsplit("_", 2)
            if len(suffix) != 3 or suffix[2] != "1":
                continue
            if suffix[1] not in ("0", "1"):
                raise ValueError(f"Multi-unit symbol is not supported yet: {identifier}")
            for pin_start, pin_end in _root_forms(unit):
                pin = unit[pin_start:pin_end]
                if not pin.startswith("(pin "):
                    continue
                number = _PIN_NUMBER.search(pin)
                at = _PIN_AT.search(pin)
                if number is None or at is None:
                    raise ValueError(f"Cannot read pins of {identifier}")
                pin_number = json.loads(number.group(1))
                if pin_number in pins:
                    raise ValueError(f"Duplicate pin number {pin_number} in {identifier}")
                pins[pin_number] = (float(at.group(1)), float(at.group(2)))
        if not pins:
            raise ValueError(f"Symbol has no supported pins: {identifier}")
        embedded = form[:match.start(1)] + _quote(identifier) + form[match.end(1):]
        return embedded, pins
    raise ValueError(f"Symbol not found: {identifier}")


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 600:
        raise ValueError("Coordinates must be between 0 and 600 mm")
    return round(float(value), 4)


def _mm(value):
    return f"{value:.4f}".rstrip("0").rstrip(".") if value else "0"


def validate_design(spec, project_dir, *, symbol_root=None, footprint_root=None):
    """Validate all references, library pins, footprint pads and net endpoints."""
    if not isinstance(spec, dict) or not _NAME.fullmatch(str(spec.get("name", ""))):
        raise ValueError("Design needs a simple name")
    components = spec.get("components")
    nets = spec.get("nets")
    if not isinstance(components, list) or not 1 <= len(components) <= 60:
        raise ValueError("Design needs 1 to 60 components")
    if not isinstance(nets, list) or not 1 <= len(nets) <= 300:
        raise ValueError("Design needs 1 to 300 nets")
    symbol_root = _symbol_root(symbol_root)
    project_symbols = _project_symbol_files(project_dir)
    resolved = {}
    refs = set()
    for index, item in enumerate(components):
        if not isinstance(item, dict):
            raise ValueError("Component must be an object")
        ref = item.get("ref")
        if not isinstance(ref, str) or not _REF.fullmatch(ref) or ref in refs:
            raise ValueError(f"Invalid or duplicate reference: {ref}")
        refs.add(ref)
        identifier = item.get("symbol")
        footprint = item.get("footprint")
        value = item.get("value")
        if not isinstance(identifier, str) or not isinstance(footprint, str) or not isinstance(value, str) or not value.strip():
            raise ValueError(f"Incomplete component {ref}")
        embedded, pins = _library_symbol(identifier, symbol_root, project_symbols)
        found = find_footprint(project_dir, footprint, footprint_root)
        if found is None:
            raise ValueError(f"Footprint not installed for {ref}: {footprint}")
        pad_source = Path(found["file"]).read_text(encoding="utf-8-sig")
        pads = {json.loads(match) for match in _PAD_NUMBER.findall(pad_source)}
        missing = set(pins) - pads
        if missing:
            raise ValueError(f"Footprint {footprint} lacks symbol pin numbers for {ref}: {', '.join(sorted(missing))}")
        x = _number(item.get("x_mm", 50 + index % 4 * 55))
        y = _number(item.get("y_mm", 55 + index // 4 * 55))
        pcb_x = _number(item.get("pcb_x_mm", 35 + index % 4 * 35))
        pcb_y = _number(item.get("pcb_y_mm", 35 + index // 4 * 35))
        side = item.get("side", "TOP")
        if side not in ("TOP", "BOTTOM"):
            raise ValueError(f"Invalid PCB side for {ref}: {side}")
        if x < 20 or y < 20:
            raise ValueError(f"Component {ref} is too close to the sheet edge")
        resolved[ref] = {"source": item, "embedded": embedded, "pins": pins,
                         "symbol_file": str(project_symbols.get(identifier.split(":", 1)[0], "")),
                         "footprint_file": found["file"], "footprint_source": found["source"],
                         "x": x, "y": y, "pcb_x": pcb_x, "pcb_y": pcb_y,
                         "side": side, "uuid": str(uuid.uuid4())}
    connections = {}
    seen_names = set()
    multi_endpoint_net = False
    for net in nets:
        if not isinstance(net, dict) or not isinstance(net.get("name"), str):
            raise ValueError("Invalid net")
        name = net["name"]
        if not name or len(name) > 80 or name in seen_names:
            raise ValueError(f"Invalid or duplicate net name: {name}")
        seen_names.add(name)
        nodes = net.get("nodes")
        if not isinstance(nodes, list) or not 1 <= len(nodes) <= 120:
            raise ValueError(f"Net {name} needs 1 to 120 endpoints")
        multi_endpoint_net |= len(nodes) >= 2
        for node in nodes:
            if not isinstance(node, dict):
                raise ValueError(f"Invalid endpoint on {name}")
            ref, pin = node.get("ref"), str(node.get("pin", ""))
            if ref not in resolved or pin not in resolved[ref]["pins"]:
                raise ValueError(f"Unknown pin {ref}.{pin} on {name}")
            key = (ref, pin)
            if key in connections:
                raise ValueError(f"Pin {ref}.{pin} is assigned to two nets")
            connections[key] = name
    if not multi_endpoint_net:
        raise ValueError("At least one net must connect two pins")
    no_connects = spec.get("no_connects", [])
    if not isinstance(no_connects, list) or len(no_connects) > 100:
        raise ValueError("Invalid no-connect list")
    seen_nc = set()
    for item in no_connects:
        if not isinstance(item, dict):
            raise ValueError("Invalid no-connect pin")
        ref, pin = item.get("ref"), str(item.get("pin", ""))
        key = (ref, pin)
        if ref not in resolved or pin not in resolved[ref]["pins"] or key in connections or key in seen_nc:
            raise ValueError(f"Invalid or connected no-connect pin: {ref}.{pin}")
        seen_nc.add(key)
    return resolved, connections


def render_schematic(spec, resolved, connections):
    """Render one KiCad schematic sheet with embedded library symbols and labels."""
    root_id = str(uuid.uuid4())
    libraries = {}
    for item in resolved.values():
        libraries[item["source"]["symbol"]] = item["embedded"]
    paper = "A1" if len(resolved) > 30 else "A2" if len(resolved) > 16 else "A3"
    parts = ["(kicad_sch", "  (version 20260306)", '  (generator "caid")',
             '  (generator_version "0.19")', f'  (uuid "{root_id}")',
             f'  (paper "{paper}")', "  (lib_symbols"]
    parts.extend("    " + form for form in libraries.values())
    parts.append("  )")
    for ref, item in resolved.items():
        component = item["source"]
        x, y = item["x"], item["y"]
        parts.extend((
            f'  (symbol (lib_id {_quote(component["symbol"])}) (at {_mm(x)} {_mm(y)} 0) (unit 1)',
            '    (exclude_from_sim no) (in_bom yes) (on_board yes) (dnp no)',
            f'    (uuid "{item["uuid"]}")',
            f'    (property "Reference" {_quote(ref)} (at {_mm(x + 5.08)} {_mm(y - 5.08)} 0)'
            ' (effects (font (size 1.27 1.27))))',
            f'    (property "Value" {_quote(component["value"])} (at {_mm(x + 5.08)} {_mm(y)} 0)'
            ' (effects (font (size 1.27 1.27))))',
            f'    (property "Footprint" {_quote(component["footprint"])} (at {_mm(x)} {_mm(y)} 0)'
            ' (effects (font (size 1.27 1.27)) (hide yes)))',
            "  )",
        ))
    for (ref, pin), name in connections.items():
        item = resolved[ref]
        pin_x, pin_y = item["pins"][pin]
        x, y = item["x"] + pin_x, item["y"] - pin_y
        justify = "right" if pin_x < 0 else "left"
        parts.append(f'  (label {_quote(name)} (at {_mm(x)} {_mm(y)} 0)'
                     f' (effects (font (size 1.27 1.27)) (justify {justify})) (uuid "{uuid.uuid4()}"))')
    for item in spec.get("no_connects", []):
        ref, pin = item["ref"], str(item["pin"])
        part = resolved[ref]
        pin_x, pin_y = part["pins"][pin]
        x, y = part["x"] + pin_x, part["y"] - pin_y
        parts.append(f'  (no_connect (at {_mm(x)} {_mm(y)}) (uuid "{uuid.uuid4()}"))')
    parts.extend(('  (sheet_instances (path "/" (page "1")))', '  (embedded_fonts no)', ')'))
    return "\n".join(parts) + "\n"


def expected_pin_nets(connections):
    return {(ref, pin): name for (ref, pin), name in connections.items()}


def _verify_netlist(root, resolved, connections):
    exported = {comp.get("ref"): comp for comp in root.findall("./components/comp")}
    actual_refs = set(exported)
    if actual_refs != set(resolved):
        raise ValueError(f"KiCad exported unexpected components: {sorted(actual_refs ^ set(resolved))}")
    for ref, item in resolved.items():
        if exported[ref].findtext("footprint", default="") != item["source"]["footprint"]:
            raise ValueError(f"KiCad exported a different footprint for {ref}")
        if exported[ref].findtext("tstamps", default="") != item["uuid"]:
            raise ValueError(f"KiCad exported a different schematic ID for {ref}")
    actual = {}
    connected = {}
    for net in root.findall("./nets/net"):
        name = net.get("name", "").removeprefix("/")
        for node in net.findall("node"):
            key = (node.get("ref"), node.get("pin"))
            connected.setdefault(name, set()).add(key)
            if key in connections:
                actual[key] = name
    if actual != connections:
        mismatch = [f"{ref}.{pin}: {connections[(ref, pin)]} != {actual.get((ref, pin), '<missing>')}"
                    for ref, pin in connections if actual.get((ref, pin)) != connections[(ref, pin)]]
        raise ValueError("KiCad netlist differs from proposed connections: " + "; ".join(mismatch[:8]))
    for name in set(connections.values()):
        expected_nodes = {key for key, net_name in connections.items() if net_name == name}
        if connected.get(name) != expected_nodes:
            raise ValueError(f"KiCad found unintended pins on net {name}")


def _verify_board_manifest(manifest, resolved, outline):
    actual = manifest.get("components", {})
    if set(actual) != set(resolved):
        raise ValueError("Generated PCB has missing or extra footprints")
    for ref, item in resolved.items():
        footprint = actual[ref]
        if footprint.get("side") != item["side"] or any(
                not isclose(footprint.get(axis, float("nan")), item[field], abs_tol=0.001)
                for axis, field in (("x_mm", "pcb_x"), ("y_mm", "pcb_y"))):
            raise ValueError(f"Generated PCB differs from requested placement for {ref}")
    measured = manifest.get("outline")
    if outline is None:
        if measured is not None or manifest.get("edge_items"):
            raise ValueError("Generated PCB has an unexpected board outline")
    elif (not isinstance(measured, dict) or manifest.get("edge_items") != 1 or
          any(not isclose(measured.get(key, float("nan")), outline[key], abs_tol=0.001)
              for key in ("width_mm", "height_mm"))):
        raise ValueError("Generated PCB outline differs from requested size")
    if outline is not None:
        board_box = (20, 20, 20 + outline["width_mm"], 20 + outline["height_mm"])
        for ref, footprint in actual.items():
            bounds = footprint.get("bounds_mm")
            if (not isinstance(bounds, list) or len(bounds) != 4 or
                    any(not isinstance(value, (int, float)) for value in bounds) or
                    bounds[0] < board_box[0] - 0.001 or bounds[1] < board_box[1] - 0.001 or
                    bounds[2] > board_box[2] + 0.001 or bounds[3] > board_box[3] + 0.001):
                raise ValueError(f"Footprint {ref} extends outside the requested PCB outline")


def _write_pcb(staging, name, resolved, connections, outline, language, token):
    if os.name == "nt":
        bundled = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "KiCad/10.0/bin/python.exe"
        interpreter = bundled if bundled.is_file() else Path(sys.executable)
    else:
        interpreter = Path(sys.executable)
    payload = {"components": [], "connections": {f"{ref}.{pin}": "/" + net for (ref, pin), net in connections.items()},
               "outline": outline}
    for ref, item in resolved.items():
        path = Path(item["footprint_file"])
        payload["components"].append({"ref": ref, "value": item["source"]["value"],
                                      "footprint_id": item["source"]["footprint"],
                                      "directory": str(path.parent), "name": path.stem,
                                      "uuid": item["uuid"], "side": item["side"],
                                      "pcb_x_mm": item["pcb_x"], "pcb_y_mm": item["pcb_y"]})
    input_file = staging / "board_input.json"
    input_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    output = staging / (name + ".kicad_pcb")
    completed = run_command([str(interpreter), str(Path(__file__).with_name("board_writer.py")),
                             str(input_file), str(output)], timeout=90, token=token)
    input_file.unlink()
    if completed.returncode or not output.is_file():
        raise RuntimeError(f"KiCad could not create the PCB (exit {completed.returncode}): " +
                           (completed.stderr.strip() or completed.stdout.strip())[-500:])
    marker = next((line.removeprefix("CAID_BOARD_MANIFEST=") for line in completed.stdout.splitlines()
                   if line.startswith("CAID_BOARD_MANIFEST=")), None)
    if marker is None:
        raise RuntimeError("KiCad did not report generated PCB facts")
    manifest = json.loads(marker)
    _verify_board_manifest(manifest, resolved, outline)
    return manifest


def _check_pcb_parity(staging, name, language, token):
    report = staging / "drc.json"
    completed = run_command([_cli_path(language), "pcb", "drc", "--format", "json",
                             "--schematic-parity", "--output", str(report),
                             str(staging / (name + ".kicad_pcb"))], timeout=90, token=token)
    if not report.is_file():
        raise RuntimeError("KiCad could not check PCB parity: " +
                           (completed.stderr.strip() or completed.stdout.strip())[-500:])
    data = json.loads(report.read_text(encoding="utf-8-sig"))
    parity = data.get("schematic_parity", [])
    if parity:
        raise ValueError("KiCad found schematic/PCB differences: " +
                         "; ".join(item.get("description", "unknown") for item in parity[:4]))
    violations = data.get("violations", [])
    errors = sum(item.get("severity") == "error" for item in violations)
    warnings = sum(item.get("severity") == "warning" for item in violations)
    report.unlink()
    return errors, warnings


def stage_new_design(spec, parent, *, language="en", token=None, symbol_root=None, footprint_root=None):
    """Write a separate project only after KiCad can read and verify every proposed net."""
    parent = Path(parent)
    if not parent.is_dir():
        raise ValueError(f"Project folder not found: {parent}")
    resolved, connections = validate_design(spec, parent, symbol_root=symbol_root,
                                            footprint_root=footprint_root)
    outline = spec.get("board")
    if outline is not None:
        if not isinstance(outline, dict) or set(outline) != {"width_mm", "height_mm"}:
            raise ValueError("Board outline needs width_mm and height_mm")
        outline = {key: _number(outline[key]) for key in ("width_mm", "height_mm")}
        if not 10 <= outline["width_mm"] <= 400 or not 10 <= outline["height_mm"] <= 400:
            raise ValueError("Board outline must be 10 to 400 mm")
        for ref, item in resolved.items():
            if not (20 <= item["pcb_x"] <= 20 + outline["width_mm"] and
                    20 <= item["pcb_y"] <= 20 + outline["height_mm"]):
                raise ValueError(f"Footprint centre {ref} is outside the requested board size")
    staging = Path(tempfile.mkdtemp(prefix=".caid-design-", dir=parent))
    try:
        name = spec["name"]
        schematic = staging / (name + ".kicad_sch")
        schematic.write_text(render_schematic(spec, resolved, connections), encoding="utf-8")
        root = _run_cli_netlist(schematic, staging / "netlist.xml", language, token)
        _verify_netlist(root, resolved, connections)
        local_libraries = {}
        for item in resolved.values():
            if item["footprint_source"] == "project":
                nickname = item["source"]["footprint"].split(":", 1)[0]
                local_libraries[nickname] = Path(item["footprint_file"]).parent
        if local_libraries:
            entries = []
            for nickname, library in local_libraries.items():
                shutil.copytree(library, staging / (nickname + ".pretty"))
                entries.append(f'(lib (name "{nickname}")(type "KiCad")'
                               f'(uri "${{KIPRJMOD}}/{nickname}.pretty")(options "")(descr ""))')
            (staging / "fp-lib-table").write_text("(fp_lib_table\n  " + "\n  ".join(entries) + "\n)\n",
                                                   encoding="utf-8")
        local_symbols = {}
        for item in resolved.values():
            if item["symbol_file"]:
                nickname = item["source"]["symbol"].split(":", 1)[0]
                local_symbols[nickname] = Path(item["symbol_file"])
        if local_symbols:
            entries = []
            for nickname, source in local_symbols.items():
                target = staging / (nickname + ".kicad_sym")
                shutil.copy2(source, target)
                entries.append(f'(lib (name "{nickname}")(type "KiCad")'
                               f'(uri "${{KIPRJMOD}}/{target.name}")(options "")(descr ""))')
            (staging / "sym-lib-table").write_text("(sym_lib_table\n  " + "\n  ".join(entries) + "\n)\n",
                                                     encoding="utf-8")
        (staging / (name + ".kicad_pro")).write_text("{}\n", encoding="utf-8")
        erc = _erc_counts(schematic, staging / "erc.json", language, token)
        board_facts = _write_pcb(staging, name, resolved, connections, outline, language, token)
        drc = _check_pcb_parity(staging, name, language, token)
        (staging / "design.json").write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (staging / "CAID-REVIEW.txt").write_text(
            f"CAID design draft: {name}\nComponents: {len(resolved)}\nNets: {len(spec['nets'])}\n"
            + (f"User requirements: {json.dumps(spec['design_brief'], ensure_ascii=False)}\n"
               if isinstance(spec.get("design_brief"), dict) else "") +
            ("Requirement review: " + json.dumps(spec["requirement_review"], ensure_ascii=False) + "\n"
             if isinstance(spec.get("requirement_review"), list) else "") +
            f"KiCad ERC: {erc[0]} errors, {erc[1]} warnings\n"
            f"KiCad DRC: {drc[0]} errors, {drc[1]} warnings; schematic parity: 0 differences\n"
            f"Saved PCB readback: {len(board_facts['components'])} footprints, outline {board_facts['outline']}\n"
            "All proposed pin/net assignments were verified against KiCad's exported netlist.\n"
            "The PCB has footprints and pad nets, but no routed traces. Open this project and review PCB parity with F8.\n"
            "Review electrical pinout, footprints, placement and all ERC/DRC findings before manufacture.\n",
            encoding="utf-8")
        for temporary in ("netlist.xml", "erc.json"):
            (staging / temporary).unlink()
        destination = parent / "CAID-Entwuerfe" / name
        destination.parent.mkdir(exist_ok=True)
        if destination.exists():
            raise FileExistsError(f"Design already exists: {destination}")
        staging.replace(destination)
        return {"directory": str(destination), "name": name, "components": len(resolved),
                "nets": len(spec["nets"]), "erc_errors": erc[0], "erc_warnings": erc[1],
                "drc_errors": drc[0], "drc_warnings": drc[1]}
    finally:
        shutil.rmtree(staging, ignore_errors=True)
