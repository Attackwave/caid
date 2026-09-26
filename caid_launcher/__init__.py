"""KiCad 10 menu launcher for the CAID IPC chat window."""

import os
import json
from pathlib import Path
import subprocess

import pcbnew


def _is_german():
    """Use the same KiCad setting as the IPC window for the legacy menu entry."""
    config = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / "kicad" / "10.0" / "kicad_common.json"
    try:
        name = str(json.loads(config.read_text(encoding="utf-8-sig"))["system"]["language"]).casefold()
    except (OSError, ValueError, KeyError, TypeError):
        name = "default"
    if name == "default":
        try:
            import ctypes
            buffer = ctypes.create_unicode_buffer(85)
            if ctypes.windll.kernel32.GetUserDefaultLocaleName(buffer, len(buffer)):
                name = buffer.value.casefold()
        except (AttributeError, OSError):
            name = "en"
    return name in ("german", "deutsch", "de") or name.startswith(("de_", "de-"))


class CaidChatLauncher(pcbnew.ActionPlugin):
    def defaults(self):
        self.name = "CAID Chat öffnen" if _is_german() else "Open CAID Chat"
        self.category = "CAID"
        self.description = "Öffnet das CAID IPC-Chatfenster" if _is_german() else "Opens the CAID IPC chat window"
        self.show_toolbar_button = True

    def Run(self):
        user = Path.home()
        local_data = Path(os.environ.get("LOCALAPPDATA", user / "AppData" / "Local"))
        documents = Path(os.environ.get("KICAD_DOCUMENTS_HOME", user / "Documents" / "KiCad"))
        interpreter = (
            local_data
            / "KiCad"
            / "10.0"
            / "python-environments"
            / "org.caid.kicad.chat"
            / "Scripts"
            / "pythonw.exe"
        )
        entrypoint = documents / "10.0" / "3rdparty" / "plugins" / "caid-chat" / "main.py"

        if not interpreter.is_file() or not entrypoint.is_file():
            import wx

            message = ("CAID Chat ist noch nicht vollständig installiert.\n"
                       "Prüfe die Installation in der Plugin- und Content-Verwaltung und starte KiCad neu.") if _is_german() else (
                       "CAID Chat is not fully installed.\n"
                       "Check the Plugin and Content Manager installation and restart KiCad.")
            wx.MessageBox(
                message,
                "CAID Chat",
                wx.OK | wx.ICON_ERROR,
            )
            return

        environment = os.environ.copy()
        board = pcbnew.GetBoard()
        if board is not None:
            filename = board.GetFileName()
            if filename:
                environment["CAID_BOARD_HINT"] = filename
        subprocess.Popen([str(interpreter), str(entrypoint)],
                         cwd=str(entrypoint.parent), env=environment)


def _superseded_by_pcm():
    """Avoid a duplicate action when an older manual launcher is still present."""
    user = Path.home()
    documents = Path(os.environ.get("KICAD_DOCUMENTS_HOME", user / "Documents" / "KiCad"))
    bundled = documents / "10.0" / "3rdparty" / "plugins" / "caid-chat" / "__init__.py"
    legacy = documents / "10.0" / "scripting" / "plugins" / "caid_launcher" / "__init__.py"
    try:
        current = Path(__file__).resolve()
        if bundled.resolve() == current:
            return legacy.is_file() and "def _superseded_by_pcm" not in legacy.read_text(encoding="utf-8")
        return bundled.read_text(encoding="utf-8").startswith('"""KiCad 10 menu launcher')
    except OSError:
        return False


if not _superseded_by_pcm():
    CaidChatLauncher().register()
