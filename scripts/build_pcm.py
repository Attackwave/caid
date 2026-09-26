"""Build and verify the installable KiCad Plugin and Content Manager archive."""

import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"
OUTPUT = BUILD / "caid-chat-kicad10-pcm.zip"
LAUNCHER_OUTPUT = BUILD / "caid-chat-kicad10-launcher.zip"
PLUGIN = ROOT / "caid_chat"


def main() -> None:
    BUILD.mkdir(parents=True, exist_ok=True)
    metadata = json.loads((ROOT / "pcm" / "metadata.json").read_text(encoding="utf-8"))
    plugin = json.loads((PLUGIN / "plugin.json").read_text(encoding="utf-8"))
    assert metadata["identifier"] == "caid-chat"
    assert plugin["identifier"] == "org.caid.kicad.chat"
    with ZipFile(OUTPUT, "w", ZIP_DEFLATED) as archive:
        archive.write(ROOT / "pcm" / "metadata.json", "metadata.json")
        for path in sorted(PLUGIN.glob("*.py")):
            archive.write(path, f"plugins/{path.name}")
        for name in ("plugin.json", "requirements.txt", "rom_reference.json"):
            archive.write(PLUGIN / name, f"plugins/{name}")
    with ZipFile(OUTPUT) as archive:
        assert archive.testzip() is None
        assert "plugins/providers.py" in archive.namelist()
        assert "plugins/provider_settings.py" in archive.namelist()
        assert "plugins/plugin.json" in archive.namelist()
    with ZipFile(LAUNCHER_OUTPUT, "w", ZIP_DEFLATED) as archive:
        archive.write(ROOT / "caid_launcher" / "__init__.py", "caid_launcher/__init__.py")
    with ZipFile(LAUNCHER_OUTPUT) as archive:
        assert archive.testzip() is None
        assert archive.namelist() == ["caid_launcher/__init__.py"]
    print(OUTPUT)
    print(LAUNCHER_OUTPUT)


if __name__ == "__main__":
    main()
