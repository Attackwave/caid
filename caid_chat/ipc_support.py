"""Safe, user-facing diagnostics for the KiCad IPC endpoint."""

import os
from pathlib import Path
import tempfile


def board_hint_path(value):
    """Accept only an existing saved PCB passed by the in-editor launcher."""
    if not value:
        return None
    path = Path(value)
    return path if path.is_file() and path.suffix.lower() == ".kicad_pcb" else None


def connection_detail(error, language="en", environ=None, temp_dir=None):
    environ = os.environ if environ is None else environ
    de = language == "de"
    socket = environ.get("KICAD_API_SOCKET", "")
    if socket:
        endpoint = ("KiCad hat einen API-Endpunkt an CAID übergeben."
                    if de else "KiCad supplied an API endpoint to CAID.")
    else:
        endpoint = ("Kein API-Endpunkt vom Menü-Starter übergeben; CAID nutzt KiCads Standardadresse."
                    if de else "The menu launcher supplied no API endpoint; CAID uses KiCad's default address.")
        if os.name == "nt":
            candidate = Path(temp_dir or tempfile.gettempdir()) / "kicad" / "api.sock"
            if not candidate.exists():
                endpoint += (" An der Standardadresse ist derzeit keine Socket-Datei sichtbar."
                             if de else " No socket file is currently visible at the default address.")
    recovery = ("Prüfe im PCB-Editor Einstellungen → Plugins und starte danach /verbinden."
                if de else "Check Settings → Plugins in the PCB editor, then use /reconnect.")
    return f"{error}\n{endpoint}\n{recovery}"
