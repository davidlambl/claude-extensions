"""lprun9 itself, run with: SQL_READ_E2E=1 python3 -m unittest discover -s skills/linqpad/tests

Skipped unless SQL_READ_E2E=1 is set and lprun9 is found: in the LPRUN9 environment variable, on PATH, or where
LINQPad 9 installs it. Runs two scripts that touch no connection, a .NET 10 file-based app and a C# .linq with no
<Connection>, each under a timeout, and checks that the gate lets both commands through without a word. Nothing here
names a saved connection; a run that overstays its timeout has its process tree killed, by the PID this test started.
"""
import json, os, shutil, subprocess, sys, tempfile, unittest
from pathlib import Path

GATE = Path(__file__).resolve().parents[1] / "hooks" / "gate.py"
TIMEOUT = 120  # seconds; a file-based app's first run compiles it


def find_lprun9():
    """lprun9's path, looked for where sql-read's transport looks, or None."""
    candidates = [os.environ.get("LPRUN9"), shutil.which("lprun9"), r"C:\Program Files\LINQPad9\LPRun9.exe",
                  os.path.join(os.environ.get("LOCALAPPDATA") or "", "Programs", "LINQPad9", "LPRun9.exe")]
    return next((str(Path(c)) for c in candidates if c and Path(c).is_file()), None)


LPRUN9 = find_lprun9() if os.environ.get("SQL_READ_E2E") == "1" else None


def run(argv):
    """(exit code, stdout, stderr) of argv, or a failure once TIMEOUT passes, after killing the tree it started."""
    proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, text=True,
                            encoding="utf-8", errors="replace")
    try:
        out, err = proc.communicate(timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        if os.name == "nt":  # lprun9 hosts the script in a child, which a kill of the parent leaves running
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, timeout=30)
        else:
            proc.kill()
        try:
            proc.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            pass
        raise AssertionError(f"{argv} did not finish within {TIMEOUT} seconds and was stopped")
    return proc.returncode, out, err


@unittest.skipUnless(LPRUN9, "set SQL_READ_E2E=1 on a machine with lprun9 to run lprun9 itself")
class Lprun9(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.dir = Path(self.folder.name).resolve()

    def tearDown(self):
        self.folder.cleanup()

    def assert_gate_silent(self, command):
        for tool in ("Bash", "PowerShell"):
            payload = {"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": {"command": command},
                       "cwd": str(self.dir), "permission_mode": "default"}
            proc = subprocess.run([sys.executable, str(GATE)], input=json.dumps(payload).encode(), capture_output=True,
                                  timeout=60)
            self.assertEqual((proc.returncode, proc.stdout, proc.stderr), (0, b"", b""), f"{tool}: {command}")

    def test_a_file_based_app(self):
        app = self.dir / "fb.cs"
        app.write_text("Console.WriteLine(6 * 7);\n", encoding="utf-8")
        self.assert_gate_silent(f'lprun9 "{app.as_posix()}"')
        code, out, err = run([LPRUN9, str(app)])
        self.assertEqual(code, 0, err)
        self.assertIn("42", out)

    def test_a_linq_with_no_connection(self):
        script = self.dir / "statements.linq"
        script.write_text('<Query Kind="Statements" />\n\nConsole.WriteLine(6 * 7);\n', encoding="utf-8")
        self.assert_gate_silent(f'lprun9 "{script.as_posix()}"')
        code, out, err = run([LPRUN9, str(script)])
        self.assertEqual(code, 0, err)
        self.assertIn("42", out)


if __name__ == "__main__":
    unittest.main()
