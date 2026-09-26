"""Bounded context-probe contract without a running KiCad editor."""

import json
import subprocess
import unittest
from unittest.mock import patch

from caid_chat.context_probe import probe_context


class ContextProbeTests(unittest.TestCase):
    def test_parses_only_marked_context(self):
        payload = {"document": "demo.kicad_pcb", "project_path": "C:/Demo",
                   "version": "10.0.6", "major": 10}
        output = "startup message\nCAID_CONTEXT=" + json.dumps(payload) + "\n"
        with patch("caid_chat.context_probe.run_command", return_value=subprocess.CompletedProcess(
                ["python"], 0, output, "")) as run:
            self.assertEqual(probe_context(), payload)
        self.assertEqual(run.call_args.kwargs["timeout"], 8)

    def test_timeout_is_reported_and_child_runner_owns_kill(self):
        with patch("caid_chat.context_probe.run_command", side_effect=subprocess.TimeoutExpired(
                ["python"], 4)):
            with self.assertRaisesRegex(TimeoutError, "within 4 seconds"):
                probe_context(timeout=4)

    def test_rejects_missing_or_invalid_context(self):
        for output in ("", 'CAID_CONTEXT={"version":"10","major":true,"document":null,"project_path":null}\n'):
            with self.subTest(output=output), patch("caid_chat.context_probe.run_command",
                                                      return_value=subprocess.CompletedProcess(
                                                          ["python"], 0, output, "")):
                with self.assertRaises(RuntimeError):
                    probe_context()

    def test_worker_failure_has_short_actionable_error(self):
        with patch("caid_chat.context_probe.run_command", return_value=subprocess.CompletedProcess(
                ["python"], 2, "", "CAID_PROBE_ERROR=Connection refused\n")):
            with self.assertRaisesRegex(RuntimeError, "Connection refused"):
                probe_context()


if __name__ == "__main__":
    unittest.main()
