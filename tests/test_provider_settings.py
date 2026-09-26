import json
from pathlib import Path
import tempfile
import unittest

from caid_chat.provider_settings import load_settings, save_settings


class ProviderSettingsTests(unittest.TestCase):
    def test_round_trip_keeps_choices_but_no_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "provider.json"
            save_settings("gemini", {"gemini": "gemini-test", "unknown": "x"},
                          {"ollama": "http://127.0.0.1:11434", "openai": "bad"}, path)
            self.assertEqual(load_settings(path), {"provider": "gemini",
                                                   "models": {"gemini": "gemini-test"},
                                                   "urls": {"ollama": "http://127.0.0.1:11434"}})
            self.assertNotIn("key", path.read_text(encoding="utf-8"))

    def test_invalid_or_missing_settings_use_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "provider.json"
            self.assertEqual(load_settings(path), {})
            path.write_text("{broken", encoding="utf-8")
            self.assertEqual(load_settings(path), {})
            path.write_text(json.dumps({"provider": "other", "api_key": "secret"}), encoding="utf-8")
            self.assertEqual(load_settings(path), {})


if __name__ == "__main__":
    unittest.main()
