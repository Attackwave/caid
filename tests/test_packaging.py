"""Checks for the installable package and PCM repository feed."""

from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest
from zipfile import ZipFile

from scripts import build_pcm, build_repository


class PackagingTests(unittest.TestCase):
    VERSION = json.loads((build_pcm.ROOT / "pcm" / "metadata.json").read_text(encoding="utf-8"))["versions"][0]["version"]

    def test_single_package_includes_the_menu_launcher(self):
        build_pcm.main()
        with ZipFile(build_pcm.OUTPUT) as archive:
            names = set(archive.namelist())
            self.assertIn("plugins/__init__.py", names)
            self.assertIn("plugins/plugin.json", names)
            self.assertIn("plugins/main.py", names)
            self.assertIsNone(archive.testzip())

    def test_repository_points_to_matching_release_archive(self):
        build_pcm.main()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "site" / "pcm"
            build_repository.build(build_pcm.OUTPUT, "v" + self.VERSION, output)
            repository_bytes = (output / "repository.json").read_bytes()
            packages_bytes = (output / "packages.json").read_bytes()
            repository = json.loads(repository_bytes)
            package = json.loads(packages_bytes)["packages"][0]
            version = package["versions"][0]
            self.assertEqual(repository["schema_version"], 2)
            self.assertEqual(repository["packages"]["sha256"], sha256(packages_bytes).hexdigest())
            self.assertEqual(version["version"], self.VERSION)
            self.assertEqual(version["runtime"], "ipc")
            self.assertTrue(version["download_url"].endswith(f"/v{self.VERSION}/caid-chat-kicad10-pcm-{self.VERSION}.zip"))
            archive = build_pcm.OUTPUT.with_name(f"caid-chat-kicad10-pcm-{self.VERSION}.zip")
            self.assertEqual(version["download_sha256"], sha256(archive.read_bytes()).hexdigest())
            self.assertTrue((output.parent / "index.html").exists())

    def test_repository_rejects_mismatched_tag(self):
        build_pcm.main()
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                build_repository.build(build_pcm.OUTPUT, "v999.999.999", Path(directory))


if __name__ == "__main__":
    unittest.main()
