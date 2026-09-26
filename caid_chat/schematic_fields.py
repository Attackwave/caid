"""Small, bounded edits to existing top-level KiCad schematic symbol properties."""

import re


_REFERENCE = re.compile(r'\(property\s+"Reference"\s+"([^"\\]+)"')
_FOOTPRINT = re.compile(r'\(property\s+"Footprint"\s+"([^"\\]*)"')
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


def rewrite_footprint_fields(text, updates):
    """Replace existing Footprint fields by reference, preserving all other bytes."""
    if not text.lstrip().startswith("(kicad_sch"):
        raise ValueError("Not a KiCad schematic")
    if not updates or len(updates) > 20 or any(
            not re.fullmatch(r"[A-Za-z]{1,4}[0-9]+", ref) or
            not _IDENTIFIER.fullmatch(identifier)
            for ref, identifier in updates.items()):
        raise ValueError("Invalid footprint update")
    replacements = []
    found = {ref: 0 for ref in updates}
    previous = {}
    for start, end in _root_forms(text):
        form = text[start:end]
        if not re.match(r'\(symbol\s', form):
            continue
        reference = _REFERENCE.search(form)
        if not reference or reference.group(1) not in updates:
            continue
        ref = reference.group(1)
        field = _FOOTPRINT.search(form)
        if field is None:
            raise ValueError(f"{ref} has no existing Footprint property")
        old = field.group(1)
        if ref in previous and previous[ref] != old:
            raise ValueError(f"{ref} has inconsistent Footprint properties")
        previous[ref] = old
        found[ref] += 1
        if old != updates[ref]:
            replacements.append((start + field.start(1), start + field.end(1), updates[ref]))
    missing = sorted(ref for ref, count in found.items() if count == 0)
    if missing:
        raise ValueError("Symbol reference not found: " + ", ".join(missing))
    if not replacements:
        raise ValueError("Footprint assignment is already current")
    for start, end, value in reversed(replacements):
        text = text[:start] + value + text[end:]
    return text, previous
