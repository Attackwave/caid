"""Headless wx construction check with KiCad's Windows plugin interpreter."""

import os
from pathlib import Path
import sys
import tempfile
import time
from types import ModuleType, SimpleNamespace


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "caid_chat"))


class FakeKiCad:
    instances = 0

    def __init__(self):
        type(self).instances += 1

    def get_version(self):
        return SimpleNamespace(full_version="10.0.6", major=10)

    def get_board(self):
        return SimpleNamespace(name="smoke.kicad_pcb")


class SlowKiCad:
    def get_board(self):
        time.sleep(1)
        raise TimeoutError("KiCad IPC timed out")


def main():
    with tempfile.TemporaryDirectory() as appdata:
        os.environ["APPDATA"] = appdata
        saved_board = Path(appdata) / "smoke.kicad_pcb"
        saved_board.write_text("", encoding="utf-8")
        os.environ["CAID_BOARD_HINT"] = str(saved_board)
        # The standalone KiCad interpreter lacks the plugin's PCM-managed kipy venv.
        # Stub only its import-time exception/status names for a wx construction test.
        try:
            import kipy.errors  # noqa: F401
        except ImportError:
            kipy = ModuleType("kipy")
            errors = ModuleType("kipy.errors")
            errors.ApiError = type("ApiError", (Exception,), {})
            errors.ConnectionError = type("ConnectionError", (Exception,), {})
            proto = ModuleType("kipy.proto")
            common = ModuleType("kipy.proto.common")
            common.ApiStatusCode = SimpleNamespace()
            sys.modules.update({"kipy": kipy, "kipy.errors": errors,
                                "kipy.proto": proto, "kipy.proto.common": common})
        import wx
        from ui import ChatFrame
        from context import EditorContext
        import kipy
        kipy.KiCad = FakeKiCad

        app = wx.App(False)
        frame = ChatFrame(FakeKiCad())
        assert frame.input.GetMinSize().height >= 110
        assert not frame.input.GetWindowStyleFlag() & wx.TE_NO_VSCROLL
        frame.input.SetValue("\n".join(f"Line {number}" for number in range(15)))
        assert frame.input.GetMinSize().height == 220
        frame.input.Clear()
        assert frame.input.GetMinSize().height == 110
        frame._append("CAID", frame._t("help"), highlight_commands=True)
        help_text = frame.transcript.GetValue()
        for command in ("/platine", "/schaltplan", "/abgleich", "/f8", "/sch", "/erc", "/drc", "/neu", "/verbinden"):
            aliases = {"/platine": "/board", "/schaltplan": "/schematic",
                       "/abgleich": "/sync", "/neu": "/new", "/verbinden": "/reconnect"}
            position = help_text.find("\n" + command + " ")
            if position < 0:
                position = help_text.find("\n" + aliases.get(command, command) + " ")
            assert position >= 0
            position += 1
            style = wx.TextAttr()
            assert frame.transcript.GetStyle(position, style)
            assert style.GetFontWeight() == wx.FONTWEIGHT_BOLD, command
        for index in (1, 2, 3):
            frame.provider.SetSelection(index)
            frame._provider_changed(None)
            assert not frame.api_key.IsShown() and not frame.server_url.IsShown()
        frame.provider.SetSelection(4)  # OpenAI
        frame._provider_changed(None)
        frame.api_key.SetValue("openai-only")
        frame.provider.SetSelection(5)  # Anthropic
        frame._provider_changed(None)
        assert frame.api_key.GetValue() == ""
        frame.api_key.SetValue("anthropic-only")
        frame.provider.SetSelection(6)  # Gemini
        frame._provider_changed(None)
        assert frame.api_key.GetValue() == ""
        frame.provider.SetSelection(7)  # Ollama
        frame._provider_changed(None)
        assert frame.server_url.GetValue() == "http://127.0.0.1:11434"
        context = EditorContext("10.0.6", 10, "pcb", saved_board.name, True)
        frame._context_result(context, str(saved_board.parent), "")
        assert frame._connection_ok
        assert ("PCB file on disk" in frame.context_label.GetLabel() or
                "PCB-Datei auf Festplatte" in frame.context_label.GetLabel())
        first_client = frame._kicad
        frame._pending = ([], {})
        frame._context_result(None, None, "Connection refused")
        assert not frame._connection_ok and frame._pending is None
        assert saved_board.name in frame.context_label.GetLabel()
        frame._context_result(context, str(saved_board.parent), "")
        assert frame._connection_ok and frame._kicad is not first_client
        assert FakeKiCad.instances >= 2
        kipy.KiCad = lambda: (_ for _ in ()).throw(RuntimeError("client unavailable"))
        frame._context_result(context, str(saved_board.parent), "")
        assert not frame._connection_ok
        assert "unavailable" in frame.context_label.GetLabel() or \
               "nicht erreichbar" in frame.context_label.GetLabel()
        kipy.KiCad = FakeKiCad
        frame.Close()
        saved = (Path(appdata) / "CAID" / "provider.json").read_text(encoding="utf-8")
        assert '"provider": "ollama"' in saved
        assert "openai-only" not in saved and "anthropic-only" not in saved
        started = time.perf_counter()
        stalled = ChatFrame(SlowKiCad())
        assert time.perf_counter() - started < 0.5, "IPC probe blocked window construction"
        assert "Project status" in stalled.brief_text.GetValue() or \
               "Projektzustand" in stalled.brief_text.GetValue()
        started = time.perf_counter()
        stalled._send(command_text="/status")
        stalled._send(command_text="/projekt")
        stalled._send(command_text="/verbinden")
        stalled._send(command_text="Test")
        assert time.perf_counter() - started < 0.5, "IPC failure blocked chat controls"
        assert "Project status" in stalled.transcript.GetValue() or \
               "Projektzustand" in stalled.transcript.GetValue()
        time.sleep(1.1)
        wx.Yield()
        assert "KiCad API unavailable" in stalled.context_label.GetLabel() or \
               "KiCad-API nicht erreichbar" in stalled.context_label.GetLabel()
        assert "smoke.kicad_pcb" in stalled.context_label.GetLabel()
        stalled.Close()
        app.Destroy()
    print("Windows wx UI smoke: OK")


if __name__ == "__main__":
    main()
