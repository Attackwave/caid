import json
import subprocess
import unittest
from unittest.mock import patch

from caid_chat import cli_providers


REPLY = {"answer": "OK", "edit_schematic": False, "placements": [],
         "tool_requests": [], "footprint_updates": [], "field_updates": [], "net_renames": [],
         "pin_connections": [], "new_pin_nets": [], "symbol_additions": [],
         "pin_disconnections": [], "no_connect_markers": []}


class CliProviderTests(unittest.TestCase):
    def test_claude_reads_prompt_from_stdin_and_validates_structured_output(self):
        envelope = {"subtype": "success", "is_error": False,
                    "structured_output": REPLY}
        with patch.object(cli_providers, "run_command", return_value=subprocess.CompletedProcess(
                [], 0, json.dumps(envelope), "")) as run:
            result = cli_providers.ask_cli("claude_cli", "sonnet", [], {"name": "demo"})
        self.assertEqual(result, REPLY)
        args, kwargs = run.call_args
        self.assertIn('"$2"', args[0][4])
        self.assertEqual(args[0][-1], "sonnet")
        self.assertIn("demo", kwargs["input"])
        self.assertNotIn("demo", " ".join(args[0]))

    def test_agy_reads_stream_result_and_rejects_failed_status(self):
        envelope = {"event": "result", "result": {"status": "SUCCESS",
                    "structured_output": REPLY}}
        with patch.object(cli_providers, "run_command", return_value=subprocess.CompletedProcess(
                [], 0, json.dumps({"event": "init"}) + "\n" + json.dumps(envelope), "")) as run:
            self.assertEqual(cli_providers.ask_cli("agy_cli", "gemini-3.8-flash-low", [], {}), REPLY)
        self.assertEqual(json.loads(run.call_args.kwargs["input"])["event"], "user")
        envelope["result"]["status"] = "ERROR"
        with patch.object(cli_providers, "run_command", return_value=subprocess.CompletedProcess(
                [], 0, json.dumps(envelope), "")):
            with self.assertRaisesRegex(RuntimeError, "did not complete"):
                cli_providers.ask_cli("agy_cli", "gemini-3.8-flash-low", [], {})

    def test_opencode_validates_text_event_and_completion(self):
        events = [
            {"type": "text", "part": {"text": json.dumps(REPLY)}},
            {"type": "step_finish", "part": {"reason": "stop"}},
        ]
        with patch.object(cli_providers, "run_command", return_value=subprocess.CompletedProcess(
                [], 0, "\n".join(map(json.dumps, events)), "")) as run:
            self.assertEqual(cli_providers.ask_cli("opencode_cli", "opencode/big-pickle", [], {}), REPLY)
        self.assertIn("--agent plan", run.call_args.args[0][4])
        self.assertNotIn("big-pickle", run.call_args.kwargs["input"])
        events[0]["part"]["text"] = "invalid"
        with patch.object(cli_providers, "run_command", return_value=subprocess.CompletedProcess(
                [], 0, "\n".join(map(json.dumps, events)), "")):
            with self.assertRaisesRegex(RuntimeError, "invalid JSON"):
                cli_providers.ask_cli("opencode_cli", "opencode/big-pickle", [], {})


if __name__ == "__main__":
    unittest.main()
