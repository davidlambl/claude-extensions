"""The plugin's manifest and files, run with: python3 -m unittest discover -s skills/split-plan/tests"""
import json, unittest
from pathlib import Path

PLUGIN = Path(__file__).resolve().parent.parent
SKILL = PLUGIN / "skills" / "split-plan"


class Manifest(unittest.TestCase):
    def test_names_the_plugin_with_a_version(self):
        manifest = json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["name"], "split-plan")
        self.assertRegex(manifest["version"], r"^\d+\.\d+\.\d+$")

    def test_skill_names_itself_and_ships_the_template_it_points_to(self):
        text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("name: split-plan", text.split("---")[1])
        self.assertIn("assets/handoff-template.md", text)
        self.assertTrue((SKILL / "assets" / "handoff-template.md").is_file())

    def test_skill_states_every_effort_tier(self):
        text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        for tier in ("`max`", "`high`", "`medium`"):
            self.assertIn(tier, text)


if __name__ == "__main__":
    unittest.main()
