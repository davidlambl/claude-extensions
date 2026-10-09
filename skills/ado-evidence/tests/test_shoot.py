"""The demo screenshot's script, screenshots/shoot.py. Run with: python3 -m unittest discover -s skills/ado-evidence/tests

Nothing here takes a screenshot or writes into the clone: the test reads the rows shoot.py would draw, and every run
in it is a --dry-run, which touches nothing and reaches no network.
"""
import importlib.util, shlex, shutil, subprocess, unittest
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]
SHOOT = PLUGIN / "screenshots" / "shoot.py"


def load_shoot():
    if 'if __name__ == "__main__":' not in SHOOT.read_text(encoding="utf-8"):
        # Importing it would take the screenshot, overwriting the committed image.
        raise AssertionError("shoot.py does its work at import, so the command it draws cannot be checked")
    spec = importlib.util.spec_from_file_location("shoot_under_test", SHOOT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def typed(lines):
    """The words a shell reads from the drawn command: the prompt and the line continuations taken out."""
    text = " ".join(line[2:] if i == 0 else line for i, line in enumerate(lines))
    return shlex.split(text.replace(" \\ ", " "))


class Demo(unittest.TestCase):
    @unittest.skipUnless(shutil.which("python3"), "the demo's command runs python3 from the PATH, as a reader types it")
    def test_docs_16_the_command_drawn_is_the_command_run_and_prints_exactly_what_is_drawn(self):
        shoot = load_shoot()
        rows = shoot.terminal()
        drawn = [text for kind, text in rows if kind == "cmd"]
        printed = [text for kind, text in rows if kind != "cmd"]
        self.assertTrue(drawn[0].startswith("$ python3 "), drawn)
        words = typed(drawn)
        self.assertEqual(words, shoot.command())
        self.assertIn("--images", words)
        self.assertEqual(words[-1], "--dry-run")
        # Typed as drawn, in the plugin folder the README's commands are typed in, it prints what the image shows.
        proc = subprocess.run(words, cwd=PLUGIN, capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.rstrip("\n").splitlines(), printed)
        self.assertTrue(printed[0].startswith("DRY RUN - would add a comment on work item 70012 with 2 image(s):"), printed)


if __name__ == "__main__":
    unittest.main()
