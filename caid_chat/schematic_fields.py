"""Small, bounded edits to existing top-level KiCad schematic symbol properties."""

import json
import re


_REFERENCE = re.compile(r'\(property\s+"Reference"\s+"([^"\\]+)"')
_FOOTPRINT = re.compile(r'\(property\s+"Footprint"\s+"([^"\\]*)"')
_FIELD_VALUE = re.compile(r'\(property\s+"(Value|Footprint)"\s+("(?:\\.|[^"\\])*")')
_IDENTIFIER = re.compile(r'^[A-Za-z0-9_.+\-]+:[A-Za-z0-9_.+\-]+$')


def _root_forms(text):
    """Yield spans of direct children of the kicad_sch root expression."""
    depth = 0
    start = None
    quoted = False
    escaped = False
    for index, char in enumerate(text):
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
            continue
        if char == '"':
            quoted = True
        elif char == "(":
            if depth == 1:
                start = index
            depth += 1
        elif char == ")":
            if depth == 2 and start is not None:
                yield start, index + 1
                start = None
            depth -= 1
            if depth < 0:
                raise ValueError("Unbalanced schematic expression")
    if depth != 0 or quoted:
        raise ValueError("Unbalanced schematic expression")


def rewrite_symbol_fields(text, updates):
    """Replace placed Value/Footprint fields, including all units of one reference."""
    if not text.lstrip().startswith("(kicad_sch"):
        raise ValueError("Not a KiCad schematic")
    if not updates or len(updates) > 20 or any(
            not isinstance(key, tuple) or len(key) != 2 or
            not re.fullmatch(r"[A-Za-z]{1,4}[0-9]+", key[0]) or
            key[1] not in {"Value", "Footprint"} or
            not isinstance(value, str) or
            (not _IDENTIFIER.fullmatch(value) if key[1] == "Footprint" else
             not 1 <= len(value) <= 100 or any(ord(char) < 32 for char in value))
            for key, value in updates.items()):
        raise ValueError("Invalid schematic field update")
    replacements = []
    found = {key: 0 for key in updates}
    previous = {}
    for start, end in _root_forms(text):
        form = text[start:end]
        if not re.match(r'\(symbol\s', form):
            continue
        reference = _REFERENCE.search(form)
        if not reference:
            continue
        ref = reference.group(1)
        relevant = {field: value for (target, field), value in updates.items() if target == ref}
        if not relevant:
            continue
        fields = {match.group(1): match for match in _FIELD_VALUE.finditer(form)}
        for field_name, value in relevant.items():
            field = fields.get(field_name)
            if field is None:
                raise ValueError(f"{ref} has no existing {field_name} property")
            old = json.loads(field.group(2))
            key = (ref, field_name)
            if key in previous and previous[key] != old:
                raise ValueError(f"{ref} has inconsistent {field_name} properties")
            previous[key] = old
            found[key] += 1
            if old != value:
                replacements.append((start + field.start(2), start + field.end(2),
                                     json.dumps(value, ensure_ascii=False)))
    missing = sorted(key for key, count in found.items() if count == 0)
    if missing:
        raise ValueError("Symbol field not found: " + ", ".join(f"{ref}.{field}" for ref, field in missing))
    if not replacements:
        raise ValueError("Schematic field assignment is already current")
    for start, end, value in reversed(replacements):
        text = text[:start] + value + text[end:]
    return text, previous


def rewrite_footprint_fields(text, updates):
    """Compatibility wrapper for footprint-only updates."""
    updated, previous = rewrite_symbol_fields(text, {(ref, "Footprint"): value
                                                     for ref, value in updates.items()})
    return updated, {ref: previous[(ref, "Footprint")] for ref in updates}
