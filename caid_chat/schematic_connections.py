"""Bounded pin-to-local-net edits of saved KiCad single-sheet schematics."""

import json
import re
import uuid

try:
    from .schematic_fields import _root_forms, _LOCAL_NET_NAME
except ImportError:
    from schematic_fields import _root_forms, _LOCAL_NET_NAME


_QUOTED = r'("(?:\\.|[^"\\])*")'
_AT = re.compile(r'^\(at\s+(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)\)')
_POINT = re.compile(r'^\(at\s+(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)\)')
_PIN_AT = re.compile(r'\(at\s+(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)\s+-?\d+(?:\.\d+)?\)')


def _children(form):
    return [form[start:end] for start, end in _root_forms(form)]


def _named_child(form, name):
    return next((child for child in _children(form) if child.startswith("(" + name + " ")), None)


def _quoted_child(form, name):
    child = _named_child(form, name)
    match = re.match(r'^\(' + re.escape(name) + r'\s+' + _QUOTED, child or "")
    return json.loads(match.group(1)) if match else None


def _pin_location(library_form, instance, pin_number):
    unit_text = _named_child(instance, "unit")
    unit_match = re.match(r'^\(unit\s+(\d+)\)', unit_text or "")
    at = _AT.match(_named_child(instance, "at") or "")
    if not unit_match or not at or float(at.group(3)) != 0 or _named_child(instance, "mirror"):
        raise ValueError("Pin operation currently needs an unmirrored, unrotated symbol unit")
    unit_number = int(unit_match.group(1))
    matches = []
    for unit in _children(library_form):
        name = re.match(r'^\(symbol\s+' + _QUOTED, unit)
        if not name:
            continue
        suffix = re.search(r'_(\d+)_[01]$', json.loads(name.group(1)))
        if not suffix or int(suffix.group(1)) not in (0, unit_number):
            continue
        for pin in _children(unit):
            if not pin.startswith("(pin ") or _quoted_child(pin, "number") != pin_number:
                continue
            pin_at = _PIN_AT.search(pin)
            if not pin_at:
                raise ValueError("Cannot locate the selected symbol pin")
            matches.append((float(pin_at.group(1)), float(pin_at.group(2))))
    if len(matches) != 1:
        raise ValueError("Selected pin is absent or ambiguous in the embedded symbol")
    return round(float(at.group(1)) + matches[0][0], 4), round(float(at.group(2)) - matches[0][1], 4)


def _validate_requests(text, requests, fields):
    if not text.lstrip().startswith("(kicad_sch") or not isinstance(requests, list) or not 1 <= len(requests) <= 10:
        raise ValueError("Provide 1 to 10 pin operations in a KiCad schematic")
    if any(not isinstance(item, dict) or set(item) != fields or
           not all(isinstance(item[key], str) for key in fields) or
           not re.fullmatch(r"[A-Za-z]{1,4}[1-9][0-9]*", item["ref"]) or
           not re.fullmatch(r"[A-Za-z0-9.+_-]{1,20}", item["pin"]) or
           ("net" in fields and not _LOCAL_NET_NAME.fullmatch(item["net"]))
           for item in requests):
        raise ValueError("Invalid pin operation request")
    if len({(item["ref"], item["pin"]) for item in requests}) != len(requests):
        raise ValueError("Duplicate pin operation request")


def _schematic_context(text):
    roots = [text[start:end] for start, end in _root_forms(text)]
    if any(form.startswith("(sheet ") for form in roots):
        raise ValueError("Pin operations currently support single-sheet schematics only")
    labels = set()
    for form in roots:
        match = re.match(r'^\(label\s+' + _QUOTED, form)
        if match:
            labels.add(json.loads(match.group(1)))
    libraries = {}
    for form in roots:
        if not form.startswith("(lib_symbols"):
            continue
        for symbol in _children(form):
            match = re.match(r'^\(symbol\s+' + _QUOTED, symbol)
            if match:
                libraries[json.loads(match.group(1))] = symbol
    placed = {}
    for form in roots:
        if not form.startswith("(symbol "):
            continue
        ref = None
        for child in _children(form):
            match = re.match(r'^\(property\s+"Reference"\s+' + _QUOTED, child)
            if match:
                ref = json.loads(match.group(1))
                break
        if ref:
            placed.setdefault(ref, []).append(form)
    occupied = set()
    for form in roots:
        if form.startswith("(no_connect "):
            at = _POINT.match(_named_child(form, "at") or "")
            if at:
                occupied.add((round(float(at.group(1)), 4), round(float(at.group(2)), 4)))
    return labels, libraries, placed, occupied


def _pin_positions(requests, libraries, placed, occupied):
    positions = set()
    for item in requests:
        ref, pin = item["ref"], item["pin"]
        instances = placed.get(ref, [])
        if len(instances) != 1:
            raise ValueError(f"Expected exactly one placed symbol unit for {ref}")
        library = libraries.get(_quoted_child(instances[0], "lib_id"))
        if library is None:
            raise ValueError(f"Embedded symbol definition not found for {ref}")
        x, y = _pin_location(library, instances[0], pin)
        if (x, y) in occupied or (x, y) in positions:
            raise ValueError(f"Pin position is already marked or ambiguous: {ref}.{pin}")
        positions.add((x, y))
        yield item, x, y


def _append_forms(text, additions):
    end = text.rstrip().rfind(")")
    return text[:end] + "\n" + "\n".join(additions) + "\n" + text[end:]


def rewrite_pin_connections(text, requests):
    """Add labels at exact unconnected pin positions; CLI netlist checks follow."""
    _validate_requests(text, requests, {"ref", "pin", "net"})
    labels, libraries, placed, occupied = _schematic_context(text)
    if any(item["net"] not in labels for item in requests):
        raise ValueError("Existing local net label not found")
    additions = []
    positions = set()
    for item, x, y in _pin_positions(requests, libraries, placed, occupied):
        net = item["net"]
        positions.add((x, y))
        additions.append(f'  (label {json.dumps(net, ensure_ascii=False)} (at {x:g} {y:g} 0)'
                         f' (effects (font (size 1.27 1.27))) (uuid "{uuid.uuid4()}"))')
    return _append_forms(text, additions), positions


def rewrite_pin_disconnections(text, requests):
    """Remove one exact local label at each selected pin; CLI checks the delta."""
    _validate_requests(text, requests, {"ref", "pin", "net"})
    labels, libraries, placed, occupied = _schematic_context(text)
    if any(item["net"] not in labels for item in requests):
        raise ValueError("Existing local net label not found")
    placed_labels = []
    for start, end in _root_forms(text):
        form = text[start:end]
        match = re.match(r'^\(label\s+' + _QUOTED, form)
        if not match:
            continue
        at = _AT.match(_named_child(form, "at") or "")
        if at:
            placed_labels.append((start, end, json.loads(match.group(1)),
                                  (round(float(at.group(1)), 4), round(float(at.group(2)), 4))))
    removals = []
    positions = set()
    for item, x, y in _pin_positions(requests, libraries, placed, occupied):
        at_pin = [label for label in placed_labels if label[3] == (x, y)]
        if len(at_pin) != 1 or at_pin[0][2] != item["net"]:
            raise ValueError(f"Expected one exact local label at {item['ref']}.{item['pin']}")
        removals.append(at_pin[0][:2])
        positions.add((x, y))
    for start, end in sorted(removals, reverse=True):
        text = text[:start] + text[end:]
    return text, positions


def rewrite_no_connect_markers(text, requests):
    """Mark exact placed pins as unused; a full KiCad check follows."""
    _validate_requests(text, requests, {"ref", "pin"})
    _, libraries, placed, occupied = _schematic_context(text)
    additions = []
    positions = set()
    for _, x, y in _pin_positions(requests, libraries, placed, occupied):
        positions.add((x, y))
        additions.append(f'  (no_connect (at {x:g} {y:g}) (uuid "{uuid.uuid4()}"))')
    return _append_forms(text, additions), positions
