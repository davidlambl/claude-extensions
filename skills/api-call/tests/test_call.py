"""call.py driven end to end, run with: python3 -m unittest discover -s skills/api-call/tests

call.py chooses its source by --via and runs it from the sources folder beside itself, so the scripts folder is copied
to a temporary directory with a stand-in for sources/postman.py that prints whatever record a test hands it; call.py
itself runs unchanged. CHROME names a stand-in browser that writes the PNG it is asked for, or fails when told to.
Only the test marked as needing the network touches it, and only with API_CALL_E2E set."""
import json, os, shutil, subprocess, sys, tempfile, time, unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "api-call" / "scripts"

RECORD = {"source": "postman", "environment": "Test", "collection": "Widgets", "folder": None, "method": "GET",
          "path": "/api/widgets/42", "url": "https://api.example.com/api/widgets/42", "auth": "none",
          "request_headers": {"Accept": "application/json"}, "client_cert": None, "status": 200, "elapsed_ms": 81,
          "sent": "2026-10-08T16:01:52.869-04:00", "sent_utc": "2026-10-08T20:01:52.869Z",
          "response_headers": {"Content-Type": "application/json"}, "body": '{"id": 42}'}

FAKE_SOURCE = '''\
"""Stands in for a source. The file --env names holds {"record": ...} to print, as the Postman source prints it,
{"print": "text"} to print as is, or {"fail": "message"} to stop with; with "inherit": true it prints in whatever
encoding its environment gives it, else in UTF-8. Each run appends its arguments to the file's .calls neighbour."""
import json, sys
from pathlib import Path
args = sys.argv[1:]
spec_file = Path(args[args.index("--env") + 1])
with spec_file.with_suffix(".calls").open("a", encoding="utf-8") as calls:
    calls.write(json.dumps(args) + "\\n")
spec = json.loads(spec_file.read_text(encoding="utf-8"))
if not spec.get("inherit"):
    sys.stdout.reconfigure(encoding="utf-8")
if "fail" in spec:
    sys.exit(spec["fail"])
print(spec["print"] if "print" in spec else json.dumps(spec["record"], ensure_ascii=False))
'''

FAKE_BROWSER = '''\
"""Stands in for headless Chrome: writes the file --screenshot names, or exits with FAKE_BROWSER_EXIT and writes nothing."""
import os, sys
from pathlib import Path
code = int(os.environ.get("FAKE_BROWSER_EXIT") or 0)
if code:
    sys.exit(code)
shot = next(a for a in sys.argv if a.startswith("--screenshot=")).split("=", 1)[1]
Path(shot).write_bytes(b"\\x89PNG\\r\\n\\x1a\\n")
'''


def launcher(folder, script):
    """An executable that runs script with this Python, for CHROME to name: a .cmd on Windows, a shell script elsewhere."""
    if os.name == "nt":
        path = folder / "browser.cmd"
        path.write_text(f'@echo off\r\n"{sys.executable}" "{script}" %*\r\n', encoding="utf-8")
    else:
        path = folder / "browser"
        path.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding="utf-8")
        path.chmod(0o755)
    return path


class CallPy(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.stage = tempfile.TemporaryDirectory()
        root = Path(cls.stage.name)
        cls.scripts = root / "scripts"
        shutil.copytree(SCRIPTS, cls.scripts, ignore=shutil.ignore_patterns("sources", "__pycache__"))
        (cls.scripts / "sources").mkdir()
        (cls.scripts / "sources" / "postman.py").write_text(FAKE_SOURCE, encoding="utf-8")
        (root / "fake_browser.py").write_text(FAKE_BROWSER, encoding="utf-8")
        cls.browser = launcher(root, root / "fake_browser.py")

    @classmethod
    def tearDownClass(cls):
        cls.stage.cleanup()

    def setUp(self):
        self.case = tempfile.TemporaryDirectory(dir=self.stage.name)
        self.dir = Path(self.case.name)
        self.out = self.dir / "out"
        self.spec = self.dir / "env.json"

    def tearDown(self):
        self.case.cleanup()

    def call(self, record=RECORD, *options, path="/api/widgets/42", fail=None, raw=None, env=None, inherit=False):
        """Runs call.py --via postman against the stand-in source, with the record (or failure, or raw text) it should print."""
        spec = {"fail": fail} if fail else {"print": raw} if raw is not None else {"record": record}
        spec["inherit"] = inherit
        self.spec.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
        argv = [sys.executable, str(self.scripts / "call.py"), "--via", "postman", "--env", str(self.spec),
                "--collection", str(self.spec), "--out", str(self.out), "--name", "step", *options, "GET", path]
        return subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", timeout=120,
                              env={**os.environ, "CHROME": str(self.browser), **(env or {})})

    def source_calls(self):
        calls = self.spec.with_suffix(".calls")
        return [json.loads(line) for line in calls.read_text(encoding="utf-8").splitlines()] if calls.exists() else []

    def path_sent(self):
        args = self.source_calls()[-1]
        return args[args.index("--path") + 1]

    def written(self):
        return json.loads((self.out / "step.json").read_text(encoding="utf-8"))

    # correctness-2: the source prints one line of JSON, and a body may carry a line or paragraph separator raw in it.
    def test_correctness_2_a_body_with_a_unicode_line_separator_is_recorded(self):
        for ch in ("\u2028", "\u2029", "\u0085"):
            with self.subTest(character=f"U+{ord(ch):04X}"):
                proc = self.call({**RECORD, "body": f'{{"note": "line{ch}two"}}'}, "--expect", "200")
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertEqual(self.written()["body"], f'{{"note": "line{ch}two"}}')
                self.assertEqual(json.loads(proc.stdout)["verdict"], "PASS")

    def test_correctness_2_the_record_is_the_last_line_after_other_output(self):
        proc = self.call(raw="a note first\r\n" + json.dumps({**RECORD, "body": "a\u2028b"}, ensure_ascii=False) + "\r\n\r\n")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.written()["body"], "a\u2028b")
        proc = self.call(raw="\ufeff" + json.dumps({**RECORD, "body": "c\u2029d"}, ensure_ascii=False) + "\r\n")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.written()["body"], "c\u2029d")

    def test_the_record_file_escapes_invisible_and_bidi_characters(self):
        # A bidi override or zero-width character from a response must not sit raw in the record,
        # where an editor would draw it and could reorder or hide text around a masked value.
        body = '{"note": "a\u202eb\u200bc"}'
        proc = self.call({**RECORD, "body": body})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        raw = (self.out / "step.json").read_bytes()
        self.assertTrue(raw.isascii(), "the record file holds a raw non-ASCII character")
        self.assertIn(b"\\u202e", raw)
        self.assertEqual(self.written()["body"], body)

    def test_a_python_source_is_asked_for_the_utf8_call_py_reads(self):
        # PYTHONIOENCODING=cp1252 stands in for the ANSI code page Windows gives a pipe; call.py reads the pipe as UTF-8.
        body = '{"name": "café ✓ 日本"}'
        proc = self.call({**RECORD, "body": body}, inherit=True, env={"PYTHONIOENCODING": "cp1252"})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.written()["body"], body)

    def test_a_source_that_prints_no_record_is_reported_plainly(self):
        for raw, shown in (("WARNING: something\nnot json at all", "not json at all"), ("42", "42"), ("", "")):
            with self.subTest(raw=raw):
                proc = self.call(raw=raw)
                self.assertEqual(proc.returncode, 1)
                self.assertIn(f"did not print a record: {shown}", proc.stderr)
                self.assertNotIn("Traceback", proc.stderr)
                self.assertFalse((self.out / "step.json").exists())

    # correctness-4: the record is written before the screenshot is attempted, so a failed capture never loses it.
    def test_correctness_4_a_failed_screenshot_keeps_the_record(self):
        proc = self.call(RECORD, "--expect", "200", "--shot", env={"FAKE_BROWSER_EXIT": "1"})
        self.assertEqual(proc.returncode, 1)
        self.assertTrue((self.out / "step.json").exists(), proc.stderr)
        written = self.written()
        self.assertEqual((written["status"], written["verdict"], written["label"]), (200, "PASS", "Test"))
        self.assertNotIn("png", written)
        self.assertFalse((self.out / "step.png").exists())
        self.assertIn(str(self.out / "step.json"), proc.stderr)
        self.assertIn("HTTP 200 (PASS against --expect 200)", proc.stderr)
        self.assertIn("screenshot failed: the browser exited with status 1", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_correctness_4_a_record_that_cannot_be_drawn_is_still_kept(self):
        # A sent time with dots for its time separator, as the Insomnia source wrote in some cultures, stops the drawing.
        proc = self.call({**RECORD, "status": 404, "sent": "2026-10-09T00.27.01.788-04:00"}, "--expect", "200", "--shot")
        self.assertEqual(proc.returncode, 1)
        self.assertEqual((self.written()["status"], self.written()["verdict"]), (404, "FAIL"))
        self.assertIn("HTTP 404 (FAIL against --expect 200)", proc.stderr)
        self.assertIn("screenshot failed: ValueError", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_correctness_4_a_rerun_whose_screenshot_fails_leaves_no_stale_record(self):
        first = self.call({**RECORD, "url": "https://api.example.com/api/widgets/42?run=1"}, "--shot")
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertIn("png", self.written())
        second = self.call({**RECORD, "url": "https://api.example.com/api/widgets/42?run=2"}, "--shot",
                           env={"FAKE_BROWSER_EXIT": "1"})
        self.assertEqual(second.returncode, 1)
        self.assertTrue(self.written()["url"].endswith("?run=2"))
        self.assertNotIn("png", self.written())
        self.assertFalse((self.out / "step.png").exists())

    def test_a_screenshot_is_added_to_the_record(self):
        proc = self.call(RECORD, "--expect", "200", "--shot", "--label", "After: Test")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout)
        self.assertTrue(Path(result["png"]).exists())
        self.assertEqual(Path(result["png"]).name, "step.png")
        self.assertEqual(self.written()["png"], result["png"])
        self.assertIn("After: Test - GET /api/widgets/42", (self.out / "step.html").read_text(encoding="utf-8"))

    # correctness-12: a path Git Bash has rewritten into a Windows path is refused before any request is sent.
    def test_correctness_12_a_path_git_bash_rewrote_is_refused_before_the_source_runs(self):
        for rewritten in ("C:/Program Files/Git/api/widgets/42", r"C:\Program Files\Git\api\widgets\42",
                          "c:/msys64/api/widgets/42", "D:/Git/api/widgets?id=42"):
            with self.subTest(path=rewritten):
                proc = self.call(RECORD, "--expect", "404", path=rewritten)
                self.assertEqual(proc.returncode, 1, proc.stdout)
                self.assertIn("MSYS_NO_PATHCONV=1", proc.stderr)
                self.assertIn("//api/widgets/42", proc.stderr)
                self.assertEqual(self.source_calls(), [], "the source ran, so a request would have been sent")
                self.assertFalse(self.out.exists())

    def test_correctness_12_a_doubled_leading_slash_reaches_the_source_as_one(self):
        # Git Bash passes //api/widgets/42 through unchanged, so the workaround the refusal names must not send //api.
        for typed in ("//api/widgets/42", "///api/widgets/42"):
            with self.subTest(path=typed):
                proc = self.call(RECORD, path=typed)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertEqual(self.path_sent(), "/api/widgets/42")

    def test_ordinary_paths_reach_the_source_as_given(self):
        for path in ("/api/widgets/42", "api/widgets/42", "/api/widgets?id=42&x=C:/"):
            with self.subTest(path=path):
                proc = self.call(RECORD, path=path)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertEqual(self.path_sent(), path)

    # The rest of call.py's contract, so a stub source covers what the README says about exit codes.
    def test_a_mismatch_exits_3_with_the_record_written(self):
        proc = self.call({**RECORD, "status": 404}, "--expect", "200")
        self.assertEqual(proc.returncode, 3)
        self.assertEqual((self.written()["verdict"], self.written()["expected"]), ("FAIL", 200))
        self.assertEqual(json.loads(proc.stdout)["verdict"], "FAIL")

    def test_warm_sends_twice_and_records_the_second(self):
        proc = self.call(RECORD, "--warm")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(len(self.source_calls()), 2)
        self.assertIsNone(self.written()["verdict"])

    def test_a_failing_source_stops_the_call(self):
        proc = self.call(fail="the request failed: nothing listens there")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("the postman source failed", proc.stderr)
        self.assertIn("nothing listens there", proc.stderr)
        self.assertFalse((self.out / "step.json").exists())

    def test_only_get(self):
        proc = subprocess.run([sys.executable, str(self.scripts / "call.py"), "--via", "postman", "--env", str(self.spec),
                               "--collection", str(self.spec), "--out", str(self.out), "--name", "step", "POST", "/x"],
                              capture_output=True, text=True, encoding="utf-8", timeout=120)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("only GET", proc.stderr)
        self.assertEqual(self.source_calls(), [])


@unittest.skipUnless(os.environ.get("API_CALL_E2E"), "needs the network: set API_CALL_E2E=1 to call postman-echo.com")
class CallPyOverTheNetwork(unittest.TestCase):
    """The real Postman source through call.py, against postman-echo.com, which echoes the query back in its body."""

    def run_call(self, folder, path):
        env_file, collection_file = folder / "Echo.postman_environment.json", folder / "Echo.postman_collection.json"
        env_file.write_text(json.dumps({"name": "Echo", "values": [
            {"key": "baseUrl", "value": "https://postman-echo.com", "enabled": True}]}), encoding="utf-8")
        collection_file.write_text(json.dumps({"info": {"name": "Echo"}, "item": []}), encoding="utf-8")
        argv = [sys.executable, str(SCRIPTS / "call.py"), "--via", "postman", "--env", str(env_file),
                "--collection", str(collection_file), "--out", str(folder / "out"), "--name", "sep", "--expect", "200",
                "GET", path]
        for attempt in range(2):
            proc = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", timeout=180)
            if not (proc.returncode == 1 and "the request failed" in proc.stderr) or attempt:
                return proc
            time.sleep(2)

    def test_correctness_2_a_line_separator_echoed_by_a_real_api_is_recorded(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = self.run_call(Path(tmp), "/get?note=line%E2%80%A8two")
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(json.loads(proc.stdout)["verdict"], "PASS")
            record = json.loads((Path(tmp) / "out" / "sep.json").read_text(encoding="utf-8"))
            self.assertEqual(json.loads(record["body"])["args"]["note"], "line\u2028two")


if __name__ == "__main__":
    unittest.main()
