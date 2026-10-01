import json
import unittest
from pathlib import Path


MANIFEST = Path(__file__).resolve().parents[1] / "deploy" / "official-tools.json"


class OfficialToolsManifestTest(unittest.TestCase):
    def test_pins_versions_and_only_the_selected_skills(self):
        data = json.loads(MANIFEST.read_text(encoding="utf-8"))
        self.assertNotIn("latest", json.dumps(data))
        self.assertEqual(
            data["lark_skills"],
            ["lark-shared", "lark-im", "lark-doc", "lark-task"],
        )
        self.assertEqual(data["meegle_skills"], ["meegle"])
        self.assertTrue(data["lark_cli"])
        self.assertTrue(data["meegle_cli"])


if __name__ == "__main__":
    unittest.main()
