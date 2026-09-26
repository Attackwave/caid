import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from caid_chat.i18n import MESSAGES, kicad_language, normalize_language, tr


class LanguageTests(unittest.TestCase):
    def test_kicad_selection_and_default(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "kicad_common.json"
            for setting, expected in (("German", "de"), ("de_DE", "de"),
                                      ("French", "en"), ("English", "en")):
                config.write_text(json.dumps({"system": {"language": setting}}), encoding="utf-8")
                self.assertEqual(kicad_language(config), expected)
            config.write_text('{"system": {"language": "Default"}}', encoding="utf-8")
            with patch("caid_chat.i18n.system_language", return_value="de"):
                self.assertEqual(kicad_language(config), "de")

    def test_catalog_and_fallback(self):
        self.assertEqual(normalize_language("fr_FR"), "en")
        for key, (english, german) in MESSAGES.items():
            self.assertEqual({name for _, name, _, _ in __import__("string").Formatter().parse(english) if name},
                             {name for _, name, _, _ in __import__("string").Formatter().parse(german) if name}, key)
        self.assertEqual(tr("de", "send"), "Senden")
        self.assertEqual(tr("fr", "send"), "Send")


if __name__ == "__main__":
    unittest.main()
