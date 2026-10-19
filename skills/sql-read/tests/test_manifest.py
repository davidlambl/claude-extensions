"""The plugin's manifest, run with: python3 -m unittest discover -s skills/sql-read/tests"""
import json, unittest
from pathlib import Path

PLUGIN = Path(__file__).resolve().parent.parent


class Manifest(unittest.TestCase):
    def test_names_the_plugin_with_a_version(self):
        manifest = json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["name"], "sql-read")
        self.assertRegex(manifest["version"], r"^\d+\.\d+\.\d+$")


if __name__ == "__main__":
    unittest.main()
