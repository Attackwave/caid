import io
import json
import unittest
from unittest.mock import patch

from caid_chat import providers


REPLY = {"answer": "Ready", "edit_schematic": False, "placements": [],
         "tool_requests": [], "footprint_updates": []}


class _Response(io.BytesIO):
    def __init__(self, value):
        super().__init__(json.dumps(value).encode("utf-8"))


class ProviderTests(unittest.TestCase):
    def test_ollama_uses_native_schema_and_context(self):
        requests = []

        def fetch(request, timeout):
            requests.append((request, timeout))
            return _Response({"message": {"content": json.dumps(REPLY)}})

        with patch.object(providers, "urlopen", side_effect=fetch):
            result = providers.ask_provider("ollama", "", "", "local-model",
                                            [{"role": "user", "content": "Place U1"}],
                                            {"document": "board.kicad_pcb"})
        self.assertEqual(result, REPLY)
        request, _timeout = requests[0]
        self.assertEqual(request.full_url, "http://127.0.0.1:11434/api/chat")
        body = json.loads(request.data)
        self.assertEqual(body["format"], providers.SCHEMA)
        self.assertEqual(body["messages"][0]["role"], "system")
        self.assertIn("board.kicad_pcb", body["messages"][-1]["content"])

    def test_lmstudio_uses_chat_completions_schema(self):
        requests = []

        def fetch(request, timeout):
            requests.append(request)
            return _Response({"choices": [{"message": {"content": json.dumps(REPLY)}}]})

        with patch.object(providers, "urlopen", side_effect=fetch):
            self.assertEqual(providers.ask_provider("lmstudio", "", "http://localhost:1234",
                                                    "model", [], {}), REPLY)
        self.assertEqual(requests[0].full_url, "http://localhost:1234/v1/chat/completions")
        self.assertEqual(json.loads(requests[0].data)["response_format"]["type"], "json_schema")

    def test_anthropic_uses_messages_api_and_no_bearer_key(self):
        requests = []

        def fetch(request, timeout):
            requests.append(request)
            return _Response({"stop_reason": "end_turn", "content": [
                {"type": "text", "text": json.dumps(REPLY)}]})

        with patch.object(providers, "urlopen", side_effect=fetch):
            result = providers.ask_provider("anthropic", "anthropic-secret", "", "claude-test",
                                            [{"role": "user", "content": "Hello"}], {"document": "demo"})
        self.assertEqual(result, REPLY)
        request = requests[0]
        self.assertEqual(request.full_url, "https://api.anthropic.com/v1/messages")
        self.assertEqual(request.get_header("X-api-key"), "anthropic-secret")
        self.assertIsNone(request.get_header("Authorization"))
        body = json.loads(request.data)
        self.assertEqual(body["output_config"]["format"]["schema"], providers.SCHEMA)
        self.assertEqual(len(body["messages"]), 1)  # KiCad data joins the user turn.

    def test_gemini_uses_generate_content_and_no_key_in_url(self):
        requests = []

        def fetch(request, timeout):
            requests.append(request)
            return _Response({"candidates": [{"finishReason": "STOP", "content": {
                "parts": [{"text": json.dumps(REPLY)}]}}]})

        with patch.object(providers, "urlopen", side_effect=fetch):
            result = providers.ask_provider("gemini", "google-secret", "", "gemini-test",
                                            [{"role": "user", "content": "Hello"}], {"document": "demo"})
        self.assertEqual(result, REPLY)
        request = requests[0]
        self.assertEqual(request.full_url, "https://generativelanguage.googleapis.com/v1beta/models/gemini-test:generateContent")
        self.assertEqual(request.get_header("X-goog-api-key"), "google-secret")
        self.assertNotIn("google-secret", request.full_url)
        body = json.loads(request.data)
        self.assertEqual(body["generationConfig"]["responseJsonSchema"], providers.SCHEMA)
        self.assertEqual(body["contents"][0]["role"], "user")

    def test_cloud_providers_reject_incomplete_output(self):
        with patch.object(providers, "urlopen", return_value=_Response({"stop_reason": "max_tokens", "content": []})):
            with self.assertRaisesRegex(RuntimeError, "did not finish"):
                providers.ask_provider("anthropic", "key", "", "claude-test", [], {})
        with patch.object(providers, "urlopen", return_value=_Response({"candidates": [{"finishReason": "MAX_TOKENS"}]})):
            with self.assertRaisesRegex(RuntimeError, "did not finish"):
                providers.ask_provider("gemini", "key", "", "gemini-test", [], {})

    def test_model_listing_and_invalid_output(self):
        with patch.object(providers, "urlopen", return_value=_Response({"models": [{"name": "qwen:latest"}]})):
            self.assertEqual(providers.list_local_models("ollama"), ["qwen:latest"])
        with patch.object(providers, "urlopen", return_value=_Response({"message": {"content": "not json"}})):
            with self.assertRaisesRegex(RuntimeError, "invalid JSON"):
                providers.ask_provider("ollama", "", "", "qwen", [], {})

    def test_model_probe_rejects_unexpected_changes(self):
        changed = {**REPLY, "edit_schematic": True}
        with patch.object(providers, "urlopen", return_value=_Response({"message": {"content": json.dumps(changed)}})):
            with self.assertRaisesRegex(RuntimeError, "changes during"):
                providers.probe_local_model("ollama", "", "qwen")

    def test_rejects_url_with_embedded_credentials_or_path(self):
        for url in ("ftp://localhost", "http://user:pass@localhost", "http://localhost/other"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                providers.list_local_models("ollama", url)


if __name__ == "__main__":
    unittest.main()
