"""Build and verify the installable KiCad Plugin and Content Manager archive."""

import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"
OUTPUT = BUILD / "caid-chat-kicad10-pcm.zip"
PLUGIN = ROOT / "caid_chat"


def main() -> None:
    BUILD.mkdir(parents=True, exist_ok=True)
    metadata = json.loads((ROOT / "pcm" / "metadata.json").read_text(encoding="utf-8"))
    plugin = json.loads((PLUGIN / "plugin.json").read_text(encoding="utf-8"))
    assert metadata["identifier"] == "caid-chat"
    assert plugin["identifier"] == "org.caid.kicad.chat"
    with ZipFile(OUTPUT, "w", ZIP_DEFLATED) as archive:
        archive.write(ROOT / "pcm" / "metadata.json", "metadata.json")
        archive.write(ROOT / "caid_launcher" / "__init__.py", "plugins/__init__.py")
        for path in sorted(PLUGIN.glob("*.py")):
            if path.name == "__init__.py":
                continue
            archive.write(path, f"plugins/{path.name}")
        for name in ("plugin.json", "requirements.txt", "rom_reference.json"):
            archive.write(PLUGIN / name, f"plugins/{name}")
    with ZipFile(OUTPUT) as archive:
        assert archive.testzip() is None
        assert "plugins/providers.py" in archive.namelist()
        assert "plugins/context_probe.py" in archive.namelist()
        assert "plugins/provider_settings.py" in archive.namelist()
        assert "plugins/plugin.json" in archive.namelist()
        assert "plugins/__init__.py" in archive.namelist()
    print(OUTPUT)


if __name__ == "__main__":
    main()
