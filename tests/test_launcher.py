"""Check the KiCad menu launcher during fresh installs and upgrades."""

import os
from pathlib import Path
import runpy
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


SOURCE = Path(__file__).resolve().parent.parent / "caid_launcher" / "__init__.py"


class LauncherMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.documents = Path(self.temporary.name)
        self.bundled = self.documents / "10.0" / "3rdparty" / "plugins" / "caid-chat" / "__init__.py"
        self.legacy = self.documents / "10.0" / "scripting" / "plugins" / "caid_launcher" / "__init__.py"
        self.bundled.parent.mkdir(parents=True)
        self.bundled.write_bytes(SOURCE.read_bytes())
        self.registered = []

        registered = self.registered

        class ActionPlugin:
            def register(self):
                registered.append(self)

        self.environment = patch.dict(os.environ, {"KICAD_DOCUMENTS_HOME": str(self.documents)})
        self.modules = patch.dict(sys.modules, {"pcbnew": SimpleNamespace(ActionPlugin=ActionPlugin)})
        self.environment.start()
        self.modules.start()
        self.addCleanup(self.environment.stop)
        self.addCleanup(self.modules.stop)

    def test_fresh_install_registers_bundled_launcher(self):
        runpy.run_path(str(self.bundled))
        self.assertEqual(len(self.registered), 1)

    def test_old_manual_launcher_takes_precedence(self):
        self.legacy.parent.mkdir(parents=True)
        self.legacy.write_text('"""KiCad 10 menu launcher for the CAID IPC chat window."""\n', encoding="utf-8")
        runpy.run_path(str(self.bundled))
        self.assertEqual(len(self.registered), 0)

    def test_updated_manual_launcher_yields_to_bundled(self):
        self.legacy.parent.mkdir(parents=True)
        self.legacy.write_bytes(SOURCE.read_bytes())
        runpy.run_path(str(self.legacy))
        runpy.run_path(str(self.bundled))
        self.assertEqual(len(self.registered), 1)


if __name__ == "__main__":
    unittest.main()
