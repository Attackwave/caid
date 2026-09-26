"""Build the static KiCad 10 PCM repository for a published package archive."""

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parent.parent
REPOSITORY_URL = "https://attackwave.github.io/caid/pcm/packages.json"
RELEASE_BASE = "https://github.com/Attackwave/caid/releases/download"


def write_json(path: Path, data: dict) -> bytes:
    content = (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    path.write_bytes(content)
    return content


def build(archive_path: Path, tag: str, output_dir: Path) -> None:
    metadata = json.loads((ROOT / "pcm" / "metadata.json").read_text(encoding="utf-8"))
    version = metadata["versions"][0]
    if tag != "v" + version["version"]:
        raise ValueError(f"Release tag {tag} does not match package version {version['version']}")

    with ZipFile(archive_path) as archive:
        if archive.testzip() is not None:
            raise ValueError("Package archive is damaged")
        included = json.loads(archive.read("metadata.json"))
        if included != metadata:
            raise ValueError("Package archive metadata differs from the source")
        if "plugins/__init__.py" not in archive.namelist():
            raise ValueError("Package archive is missing its KiCad launcher")
        install_size = sum(item.file_size for item in archive.infolist())

    published_name = f"caid-chat-kicad10-pcm-{version['version']}.zip"
    published_archive = archive_path.with_name(published_name)
    if archive_path != published_archive:
        published_archive.write_bytes(archive_path.read_bytes())
    package = deepcopy(metadata)
    package["versions"][0].update({
        "download_url": f"{RELEASE_BASE}/{tag}/{published_name}",
        "download_sha256": hashlib.sha256(published_archive.read_bytes()).hexdigest(),
        "download_size": published_archive.stat().st_size,
        "install_size": install_size,
    })
    output_dir.mkdir(parents=True, exist_ok=True)
    packages_bytes = write_json(output_dir / "packages.json", {"packages": [package]})
    now = datetime.now(timezone.utc)
    repository = {
        "$schema": "https://go.kicad.org/pcm/schemas/v2#/definitions/Repository",
        "schema_version": 2,
        "name": "CAID for KiCad 10",
        "maintainer": {"name": "CAID Project", "contact": {"web": "https://github.com/Attackwave/caid"}},
        "packages": {
            "url": REPOSITORY_URL,
            "sha256": hashlib.sha256(packages_bytes).hexdigest(),
            "update_timestamp": int(now.timestamp()),
            "update_time_utc": now.strftime("%Y-%m-%d %H:%M:%S"),
        },
    }
    write_json(output_dir / "repository.json", repository)
    (output_dir.parent / "index.html").write_text(
        '<!doctype html><html lang="en"><meta charset="utf-8">'
        '<title>CAID PCM repository</title><body><h1>CAID for KiCad 10</h1>'
        '<p>Add <code>https://attackwave.github.io/caid/pcm/repository.json</code> '
        'in KiCad Plugin and Content Manager.</p></body></html>\n',
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, default=ROOT / "build" / "caid-chat-kicad10-pcm.zip")
    parser.add_argument("--tag", required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "build" / "pcm-site" / "pcm")
    args = parser.parse_args()
    build(args.archive, args.tag, args.output)


if __name__ == "__main__":
    main()
