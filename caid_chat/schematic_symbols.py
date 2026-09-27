"""Bounded symbol additions to saved, single-sheet KiCad schematics."""

import json
from math import isfinite
from pathlib import Path
import re
import uuid

try:
    from .schematic_fields import _root_forms
    from .footprints import find_footprint
except ImportError:
    from schematic_fields import _root_forms
    from footprints import find_footprint


_REF = re.compile(r"[A-Za-z]{1,4}[1-9][0-9]*\Z")
_ID = re.compile(r"[A-Za-z0-9_.+\-]+:[A-Za-z0-9_.+\-]+\Z")
_PAPER = {"A0": (1189, 841), "A1": (841, 594), "A2": (594, 420),
          "A3": (420, 297), "A4": (297, 210), "A5": (210, 148)}


def _coordinate(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
        raise ValueError("Symbol coordinates must be finite numbers")
    return round(float(value), 4)


def _root_children(text):
    return [(start, end, text[start:end]) for start, end in _root_forms(text)]


def rewrite_symbol_additions(text, additions, project_dir, *, symbol_root=None,
                             footprint_root=None):
    """Insert installed single-unit symbols without changing existing root forms."""
    if not text.lstrip().startswith("(kicad_sch") or not isinstance(additions, list) or not 1 <= len(additions) <= 5:
        raise ValueError("Provide 1 to 5 symbol additions in a KiCad schematic")
    roots = _root_children(text)
    if any(form.startswith("(sheet ") for _, _, form in roots):
        raise ValueError("Symbol additions currently support single-sheet schematics only")
    library_root = next(((start, end, form) for start, end, form in roots
                         if form.startswith("(lib_symbols")), None)
    if library_root is None:
        raise ValueError("Schematic has no embedded symbol library")
    paper = next((form for _, _, form in roots if form.startswith("(paper ")), "")
    paper_match = re.match(r'^\(paper\s+"(A[0-5])"(?:\s+(portrait))?', paper)
    if paper_match is None:
        raise ValueError("Unsupported schematic paper size")
    width, height = _PAPER[paper_match.group(1)]
    if paper_match.group(2):
        width, height = height, width
    existing_refs = set()
    existing_positions = set()
    for _, _, form in roots:
        if not form.startswith("(symbol "):
            continue
        match = re.search(r'\(property\s+"Reference"\s+("(?:\\.|[^"\\])*")', form)
        if match:
            existing_refs.add(json.loads(match.group(1)))
        position = re.match(r'^\(symbol\s+\(lib_id\s+"(?:\\.|[^"\\])*"\)\s+\(at\s+([0-9.]+)\s+([0-9.]+)', form)
        if position:
            existing_positions.add((round(float(position.group(1)), 4),
                                    round(float(position.group(2)), 4)))
    library_form = library_root[2]
    embedded_ids = set()
    for start, end in _root_forms(library_form):
        match = re.match(r'^\(symbol\s+("(?:\\.|[^"\\])*")', library_form[start:end])
        if match:
            embedded_ids.add(json.loads(match.group(1)))

    try:
        from .circuit_design import (_PAD_NUMBER, _library_symbol, _project_symbol_files,
                                     _symbol_root, render_schematic)
    except ImportError:
        from circuit_design import (_PAD_NUMBER, _library_symbol, _project_symbol_files,
                                    _symbol_root, render_schematic)
    project_dir = Path(project_dir)
    project_symbols = _project_symbol_files(project_dir)
    new_definitions = []
    placed_forms = []
    added_refs = set()
    added_positions = set()
    for item in additions:
        fields = {"ref", "symbol", "value", "footprint", "x_mm", "y_mm"}
        if not isinstance(item, dict) or set(item) != fields:
            raise ValueError("Invalid symbol addition request")
        ref, identifier, value, footprint = (item[key] for key in
                                             ("ref", "symbol", "value", "footprint"))
        if (not isinstance(ref, str) or not _REF.fullmatch(ref) or
                not isinstance(identifier, str) or not _ID.fullmatch(identifier) or
                not isinstance(footprint, str) or not _ID.fullmatch(footprint) or
                not isinstance(value, str) or not 1 <= len(value) <= 100 or
                any(ord(char) < 32 for char in value)):
            raise ValueError("Invalid symbol addition request")
        if ref in existing_refs or ref in added_refs:
            raise ValueError(f"Symbol reference already exists: {ref}")
        x, y = _coordinate(item["x_mm"]), _coordinate(item["y_mm"])
        if not (20 <= x <= width - 20 and 20 <= y <= height - 20):
            raise ValueError(f"Symbol {ref} is outside the usable sheet area")
        if (x, y) in existing_positions or (x, y) in added_positions:
            raise ValueError(f"Symbol position already occupied: {ref}")
        found = find_footprint(project_dir, footprint, footprint_root)
        if found is None:
            raise ValueError(f"Footprint not installed for {ref}: {footprint}")
        embedded, pins, pin_names, pin_units, units = _library_symbol(
            identifier, _symbol_root(symbol_root), project_symbols)
        if units != [1]:
            raise ValueError(f"Symbol {identifier} needs multiple or unsupported units")
        pad_source = Path(found["file"]).read_text(encoding="utf-8-sig")
        pads = {json.loads(match) for match in _PAD_NUMBER.findall(pad_source)}
        if not set(pins) <= pads:
            raise ValueError(f"Footprint {footprint} lacks symbol pin numbers for {ref}")
        component = {"ref": ref, "symbol": identifier, "value": value,
                     "footprint": footprint}
        symbol_uuid = str(uuid.uuid4())
        resolved = {ref: {"source": component, "embedded": embedded, "pins": pins,
                          "pin_names": pin_names, "pin_units": pin_units, "units": units,
                          "unit_positions": {1: (x, y)}, "unit_uuids": {1: symbol_uuid}}}
        rendered = render_schematic({"components": [component]}, resolved, {})
        rendered_roots = _root_children(rendered)
        generated_library = next(form for _, _, form in rendered_roots if form.startswith("(lib_symbols"))
        generated_symbol = next(form for _, _, form in rendered_roots if form.startswith("(symbol "))
        if identifier not in embedded_ids:
            library_children = [generated_library[start:end]
                                for start, end in _root_forms(generated_library)]
            new_definitions.extend(library_children)
            embedded_ids.add(identifier)
        placed_forms.append(generated_symbol)
        added_refs.add(ref)
        added_positions.add((x, y))
    if new_definitions:
        start, end, form = library_root
        insertion = end - 1
        text = text[:insertion] + "\n    " + "\n    ".join(new_definitions) + "\n  " + text[insertion:]
    insertion = text.rstrip().rfind(")")
    text = text[:insertion] + "\n  " + "\n  ".join(placed_forms) + "\n" + text[insertion:]
    return text, added_refs
