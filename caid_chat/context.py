"""KiCad editor context and the capabilities exposed by this plugin."""

from dataclasses import dataclass
import re
from typing import Optional

try:
    from .i18n import localized
except ImportError:
    from i18n import localized


@dataclass(frozen=True)
class EditorContext:
    version: str
    major_version: Optional[int]
    editor: str
    document: Optional[str]

    @property
    def can_read_board(self) -> bool:
        return self.editor == "pcb" and self.document is not None

    @property
    def schematic_plugin_available(self) -> bool:
        return self.major_version is not None and self.major_version >= 11


def parse_major_version(version: str) -> Optional[int]:
    match = re.search(r"(?<!\d)(\d+)\.(?:\d+)", version)
    return int(match.group(1)) if match else None


def detect_context(kicad_client) -> EditorContext:
    """The plugin's PCB action scope identifies the active editor."""
    try:
        kicad_version = kicad_client.get_version()
        version = str(kicad_version.full_version)
        major_version = int(kicad_version.major)
    except Exception:
        version = "unknown"
        major_version = None

    try:
        board = kicad_client.get_board()
        filename = str(board.name) if board is not None else ""
    except Exception:
        filename = ""

    return EditorContext(
        version=version,
        major_version=major_version,
        editor="pcb",
        document=filename or None,
    )


def capability_summary(context: EditorContext, language="en") -> str:
    t = lambda en, de: localized(language, en, de)
    lines = [f"KiCad: {context.version}", t("Editor: PCB editor", "Editor: PCB-Editor")]
    lines.append(t("Board: ", "Platine: ") + (context.document or t("no file saved yet", "noch keine Datei gespeichert")))
    lines.append(t("PCB context: available", "PCB-Kontext: verfügbar") if context.can_read_board else t("PCB context: no saved board", "PCB-Kontext: keine gespeicherte Platine"))
    if context.major_version is None:
        lines.append(t("Schematic plugin API: version unknown", "Schaltplan-Plugin-Schnittstelle: Version nicht sicher erkannt"))
    elif context.schematic_plugin_available:
        lines.append(t("Schematic plugin API: available according to KiCad version", "Schaltplan-Plugin-Schnittstelle: laut KiCad-Version verfügbar"))
    else:
        lines.append(t("Schematic plugin API: unavailable in this KiCad version", "Schaltplan-Plugin-Schnittstelle: in dieser KiCad-Version nicht verfügbar"))
    return "\n".join(lines)
