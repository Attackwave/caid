"""Read installed and project-local KiCad footprint libraries for chat context."""

import os
from pathlib import Path
import re


_LIBRARY = re.compile(r'\(lib\s+\(name\s+"([^"]+)"\).*?\(uri\s+"([^"]+)"\)', re.S)
_PAD = re.compile(r'\(pad\s+"([^"]+)"\s+\w+\s+\w+\s+\(at\s+([-\d.]+)\s+([-\d.]+)', re.S)
_DESC = re.compile(r'\(descr\s+"([^"]*)"\)')
_MODEL = re.compile(r'\(model\s+"([^"]+)"')
_TERM = re.compile(r'footprint|fußabdruck|fussabdruck|gehäuse|gehaeuse|package|land.?pattern', re.I)


def _libraries(project_path, standard_root=None):
    project = Path(project_path)
    table = project / "fp-lib-table"
    if table.is_file():
        for nickname, uri in _LIBRARY.findall(table.read_text(encoding="utf-8-sig")):
            if uri.startswith("${KIPRJMOD}/"):
                directory = project / uri[len("${KIPRJMOD}/"):]
            else:
                directory = Path(os.path.expandvars(uri))
            if directory.is_dir():
                yield nickname, directory, "project"
    if standard_root is None:
        root = os.environ.get("KICAD10_FOOTPRINT_DIR") or os.environ.get("KICAD_FOOTPRINT_DIR")
        if root:
            standard_root = Path(root)
        elif os.name == "nt":
            standard_root = Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "KiCad/10.0/share/kicad/footprints"
        else:
            standard_root = Path("/usr/share/kicad/footprints")
    root = Path(standard_root)
    if root.is_dir():
        for directory in root.glob("*.pretty"):
            yield directory.stem, directory, "installed"


def _details(library, file, source):
    content = file.read_text(encoding="utf-8-sig")
    pads = _PAD.findall(content)
    description = _DESC.search(content)
    models = _MODEL.findall(content)
    first = next(((float(x), float(y)) for number, x, y in pads if number == "1"), None)
    return {
        "id": f"{library}:{file.stem}", "source": source,
        "file": str(file), "description": description.group(1) if description else "",
        "pad_count": len(pads), "pad_1_xy_mm": list(first) if first else None,
        "models": models[:3],
    }


def find_footprint(project_path, identifier, standard_root=None):
    """Resolve one installed footprint ID; return None when the file is absent."""
    if not re.fullmatch(r"[A-Za-z0-9_.+\-]+:[A-Za-z0-9_.+\-]+", identifier):
        return None
    nickname, name = identifier.split(":", 1)
    for library, directory, source in _libraries(project_path, standard_root):
        if library == nickname:
            file = directory / f"{name}.kicad_mod"
            return _details(library, file, source) if file.is_file() else None
    return None


def inspect_footprints(board_snapshot, schematic_snapshot, query, standard_root=None):
    """Return bounded, factual evidence for the user's footprint question."""
    components = schematic_snapshot.get("components", []) if schematic_snapshot else []
    board = {item["ref"]: item for item in board_snapshot.get("footprints", [])}
    references = {ref.upper() for ref in re.findall(r'\b[A-Za-z]{1,4}\d+\b', query)}
    selected = [part for part in components if part["ref"].upper() in references]
    if not selected:
        words = query.casefold().split()
        selected = [part for part in components if any(word in part["value"].casefold()
                    for word in words if len(word) >= 5 and word not in {"suche", "finde", "einem", "flash", "footprint"})][:4]
    if not selected and "flash" in query.casefold():
        selected = [part for part in components if "flash" in part["value"].casefold() or
                    "mx29" in part["value"].casefold()][:4]
    if not selected and len(components) == 1:
        selected = components
    libraries = list(_libraries(board_snapshot["project_path"], standard_root))
    by_name = {name: (directory, source) for name, directory, source in libraries}
    exact = []
    for part in selected[:8]:
        footprint_id = part.get("footprint", "")
        if ":" not in footprint_id:
            continue
        lib, name = footprint_id.split(":", 1)
        if lib in by_name:
            directory, source = by_name[lib]
            file = directory / f"{name}.kicad_mod"
            if file.is_file():
                exact.append(_details(lib, file, source))
    names = {item["id"] for item in exact}
    similar = []
    for match in exact:
        base = match["id"].split(":", 1)[1]
        prefix = re.match(r'^(.*?\d+)_', base)
        if not prefix:
            continue
        family = prefix.group(1)
        for library, directory, source in libraries:
            for file in directory.glob(f"{family}*.kicad_mod"):
                identifier = f"{library}:{file.stem}"
                if identifier not in names:
                    similar.append(_details(library, file, source))
                    names.add(identifier)
                if len(similar) >= 20:
                    break
            if len(similar) >= 20:
                break
    return {
        "query": query, "components": [{"ref": part["ref"], "value": part["value"],
                 "schematic_footprint": part.get("footprint", ""),
                 "pcb_footprint": board.get(part["ref"], {}).get("footprint_id"),
                 "pcb_pad_count": board.get(part["ref"], {}).get("pad_count")}
                for part in selected[:8]],
        "exact_footprints": exact, "similar_footprints": similar,
        "library_count": len(libraries),
        "physical_part_verified": False,
    }


def asks_about_footprint(message):
    return bool(_TERM.search(message))
