r"""lprun9 itself, run with: SQL_READ_E2E=1 python3 -m unittest discover -s skills/linqpad/tests

Skipped unless SQL_READ_E2E=1 is set and lprun9 is found: in the LPRUN9 environment variable, on PATH, or where
LINQPad 9 installs it (C:\Program Files\LINQPad9 or %LocalAppData%\Programs\LINQPad9 on Windows; the app bundle in
/Applications or ~/Applications on macOS), and the skip says where it looked. Runs two scripts that touch no
connection, a .NET 10 file-based app and a C# .linq with no <Connection>, each under a timeout, and checks that the
gate lets the commands through without a word. The file-based app is run bare, as the skill tells Claude to type it
and as 9.11.10 on Windows ran it, and with -lang=program, which 9.10.20 on macOS needed: an lprun9 that refuses the bare form skips that
part, naming its version. Nothing here names a saved connection; a run that overstays its timeout has its process
tree killed, by the PID this test started. The Finder tests run everywhere and start nothing.
"""
import json, os, re, shutil, subprocess, sys, tempfile, unittest
from pathlib import Path
from unittest import mock

HERE = sys.modules[__name__]  # this module, whose flags and installs() the Finder tests patch
GATE = Path(__file__).resolve().parents[1] / "hooks" / "gate.py"
TIMEOUT = 120  # seconds; a file-based app's first run compiles it
WINDOWS = os.name == "nt"
MACOS = sys.platform == "darwin"
NEEDS_LANG = "You must specify a language"  # what lprun9 9.10.20 on macOS said to a .cs file run without -lang


def installs():
    """Where LINQPad 9 installs lprun9 on this system, for every user and then for this user alone; nowhere on a
    system that is neither Windows nor macOS."""
    if MACOS:
        return ["/Applications/LINQPad 9.app/Contents/MacOS/lprun9",
                os.path.join(os.path.expanduser("~"), "Applications", "LINQPad 9.app", "Contents", "MacOS", "lprun9")]
    if WINDOWS:
        local = os.environ.get("LOCALAPPDATA")
        return [r"C:\Program Files\LINQPad9\LPRun9.exe"] + (
            [os.path.join(local, "Programs", "LINQPad9", "LPRun9.exe")] if local else [])
    return []


def find_lprun9():
    """(lprun9's path, None), looked for as sql-read's transport looks: env LPRUN9, lprun9 on PATH, then where
    LINQPad 9 installs it; or (None, where it looked) when it is in none of those places."""
    given = os.environ.get("LPRUN9", "").strip()
    for place in (given, shutil.which("lprun9"), *installs()):
        if place and Path(place).is_file():
            return str(Path(place)), None
    looked = [f"env LPRUN9 ({given or 'not set'})", "lprun9 on PATH", *installs()]
    return None, f"lprun9 was not found: looked at {', '.join(looked)}; install LINQPad 9, or set LPRUN9"


LPRUN9, LOOKED = find_lprun9() if os.environ.get("SQL_READ_E2E") == "1" else (None, None)


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


def version(tool):
    """What lprun9 says it is: the Version= line of the usage text it prints when run with no script."""
    code, out, err = run([tool])
    found = re.search(r"Version=(\S+)", out + err)
    return found.group(1) if found else "(no version printed)"


class Finder(unittest.TestCase):
    """find_lprun9() looks where sql-read's transport looks, on this system, and says where when it finds nothing."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.dir = Path(temp.name)
        self.home = str(self.dir / "home")
        environ = {k: v for k, v in os.environ.items() if k != "LPRUN9"}
        environ.update(PATH=str(self.dir / "empty"), HOME=self.home, USERPROFILE=self.home,
                       LOCALAPPDATA=str(self.dir / "Local"))
        patcher = mock.patch.dict(os.environ, environ, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_on_macos_it_looks_in_linqpad_9s_app_bundle_for_every_user_then_this_one(self):
        with mock.patch.object(HERE, "WINDOWS", False), mock.patch.object(HERE, "MACOS", True):
            self.assertEqual(installs(), ["/Applications/LINQPad 9.app/Contents/MacOS/lprun9",
                                          os.path.join(self.home, "Applications", "LINQPad 9.app", "Contents",
                                                       "MacOS", "lprun9")])

    def test_on_windows_it_looks_under_program_files_then_localappdata(self):
        with mock.patch.object(HERE, "WINDOWS", True), mock.patch.object(HERE, "MACOS", False):
            self.assertEqual(installs(), [r"C:\Program Files\LINQPad9\LPRun9.exe",
                                          os.path.join(str(self.dir / "Local"), "Programs", "LINQPad9", "LPRun9.exe")])
            del os.environ["LOCALAPPDATA"]
            self.assertEqual(installs(), [r"C:\Program Files\LINQPad9\LPRun9.exe"])

    def test_elsewhere_it_looks_in_no_install_folder(self):
        with mock.patch.object(HERE, "WINDOWS", False), mock.patch.object(HERE, "MACOS", False):
            self.assertEqual(installs(), [])

    def test_lprun9_then_path_then_the_install_folders_and_the_reason_names_them_all(self):
        tools = {}
        for name in ("given", "path", "system", "user"):
            tool = self.dir / name / ("lprun9.exe" if os.name == "nt" else "lprun9")
            tool.parent.mkdir()
            tool.write_bytes(b"")
            tool.chmod(0o755)
            tools[name] = str(tool)
        with mock.patch.object(HERE, "installs", lambda: [tools["system"], tools["user"]]):
            os.environ["LPRUN9"] = tools["given"]
            os.environ["PATH"] = str(self.dir / "path")
            self.assertEqual(find_lprun9(), (tools["given"], None))
            del os.environ["LPRUN9"]
            # On Windows shutil.which adds the extension as PATHEXT spells it (lprun9.EXE), so compare as Windows does.
            found, reason = find_lprun9()
            self.assertEqual((os.path.normcase(found), reason), (os.path.normcase(tools["path"]), None))
            os.environ["PATH"] = str(self.dir / "empty")
            self.assertEqual(find_lprun9(), (tools["system"], None))
            os.remove(tools["system"])
            self.assertEqual(find_lprun9(), (tools["user"], None))
            os.remove(tools["user"])
            tool, reason = find_lprun9()
        self.assertIsNone(tool)
        for said in ("LPRUN9", "PATH", tools["system"], tools["user"]):
            self.assertIn(said, reason)


@unittest.skipUnless(LPRUN9, LOOKED or "set SQL_READ_E2E=1 on a machine with lprun9 to run lprun9 itself")
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
        self.assert_gate_silent(f'lprun9 -lang=program "{app.as_posix()}"')
        code, out, err = run([LPRUN9, "-lang=program", str(app)])
        self.assertEqual(code, 0, err)
        self.assertIn("42", out)
        with self.subTest(form="bare, as the skill tells Claude to type it"):
            code, out, err = run([LPRUN9, str(app)])
            if NEEDS_LANG in out or NEEDS_LANG in err:
                self.skipTest(f"lprun9 {version(LPRUN9)} needs -lang=program for a .cs file, where 9.11 runs it bare: "
                              f"{(err or out).strip()}")
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
