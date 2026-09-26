import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from caid_chat.project_recovery import (StageGuard, archive_abandoned_stages,
                                        describe_stages, scan_stages)


class ProjectRecoveryTests(unittest.TestCase):
    def test_running_stage_is_preserved_and_abandoned_stage_is_archived(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            running = project / ".caid-routing-active"
            running.mkdir()
            guard = StageGuard(running, "routing")
            try:
                self.assertEqual(scan_stages(project)[0]["state"], "active")
                self.assertEqual(archive_abandoned_stages(project), [])
                self.assertTrue(running.exists())
            finally:
                guard.close()
            self.assertEqual(scan_stages(project)[0]["state"], "unknown")

            abandoned = project / ".caid-design-abandoned"
            abandoned.mkdir()
            (abandoned / "draft.kicad_sch").write_text("recoverable", encoding="utf-8")
            (abandoned / ".caid-stage.lock").write_bytes(b"0")
            (abandoned / ".caid-stage.json").write_text(json.dumps({
                "schema_version": 1, "kind": "design", "project": str(project.resolve())}),
                encoding="utf-8")
            states = {item["path"].name: item["state"] for item in scan_stages(project)}
            self.assertEqual(states[abandoned.name], "abandoned")
            self.assertEqual(states[running.name], "unknown")
            self.assertIn("/recovery save", describe_stages(project))
            moved = archive_abandoned_stages(project)
            self.assertEqual(len(moved), 1)
            self.assertEqual((moved[0] / "draft.kicad_sch").read_text(), "recoverable")
            self.assertFalse(abandoned.exists())
            self.assertTrue(running.exists())

    def test_invalid_marker_is_never_archived(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            directory = project / ".caid-routing-legacy"
            directory.mkdir()
            (directory / ".caid-stage.lock").write_bytes(b"0")
            (directory / ".caid-stage.json").write_text(json.dumps({
                "schema_version": 1, "kind": "routing", "project": "/different"}),
                encoding="utf-8")
            self.assertEqual(scan_stages(project)[0]["state"], "unknown")
            self.assertEqual(archive_abandoned_stages(project), [])
            self.assertTrue(directory.exists())

    def test_crashed_process_leaves_recoverable_stage(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            stage = project / ".caid-design-crashed"
            stage.mkdir()
            script = ("from caid_chat.project_recovery import StageGuard; "
                      "from pathlib import Path; import os,sys; "
                      "StageGuard(Path(sys.argv[1]), 'design'); "
                      "Path(sys.argv[1], 'draft.kicad_sch').write_text('saved'); "
                      "os._exit(0)")
            result = subprocess.run([sys.executable, "-c", script, str(stage)],
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(scan_stages(project)[0]["state"], "abandoned")
            moved = archive_abandoned_stages(project)
            self.assertEqual(len(moved), 1)
            self.assertEqual((moved[0] / "draft.kicad_sch").read_text(), "saved")


if __name__ == "__main__":
    unittest.main()
