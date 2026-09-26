"""Entrypoint launched by KiCad's IPC plugin manager."""

from pathlib import Path
import sys

# KiCad starts this file directly from the plugin directory. This works for
# both manually installed IPC plugins and packages installed through PCM.
sys.path.insert(0, str(Path(__file__).resolve().parent))


def main() -> None:
    import wx
    from kipy import KiCad

    from ui import ChatFrame

    kicad = KiCad()
    app = wx.App(False)
    frame = ChatFrame(kicad)
    frame.Show()
    app.MainLoop()


if __name__ == "__main__":
    main()
