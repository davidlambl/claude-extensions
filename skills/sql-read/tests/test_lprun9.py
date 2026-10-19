"""The lprun9 transport, transports/lprun9.py: the .linq script it writes, the command it runs, how it stops a run that
reaches its timeout, and where it finds lprun9. Run with: python3 -m unittest discover -s skills/sql-read/tests

CI has no lprun9 and no Windows, so most tests replace subprocess with a fake and choose whether the transport believes
it runs on Windows. The real-process tests start Python in lprun9's place, with the transport's command swapped for
it, to see a run stopped together with the process it started. The lookup tests build their own PATH, LPRUN9 and
install folders in a temporary folder. No test runs lprun9, and none names a real connection. Characters outside ASCII
are written as escapes."""
import io, json, ntpath, os, signal, subprocess, sys, tempfile, time, unittest
from pathlib import Path
from unittest import mock

from test_plan import SCRIPTS, read
from backend import AUTH_KINDS, Connection, NotNamed, Result, ToolMissing, Transport, Unusable
from transports import lprun9

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "connections.xml"
SALES = Connection("Sales", "sales-sql.database.windows.net", "Product", "aad", "reader@example.com", True)
UNNAMED =Connection(None, "SQL-PROD-01.example.local", None, "sql", "web_reader", True)
HEADER = b'<Query Kind="SQL" />\n\n'
BATCH = "SET NOCOUNT ON;\nSET LOCK_TIMEOUT 5000;\nSELECT TOP 5 * FROM dbo.Orders ORDER BY 1"
EXE = "LPRun9.exe" if os.name == "nt" else "lprun9"
REAL_INSTALLS = lprun9.installs
# A query of the user's own, as LINQPad saves one: a program, with a saved connection, in Windows line ends.
PROGRAM = (b'<Query Kind="Program">\r\n  <Connection>\r\n    <ID>00000000-0000-4000-8000-0000000000aa</ID>\r\n'
           b'    <DisplayName>Sales</DisplayName>\r\n  </Connection>\r\n</Query>\r\n\r\n'
           b"void Main() { /* the user's own work */ }\r\n")


def make_tool(folder, name=EXE):
    """An empty file standing for lprun9, executable where that matters; returns its absolute path."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_bytes(b"")
    path.chmod(0o755)
    return os.path.abspath(path)


class Case(unittest.TestCase):
    """A temporary folder; LPRUN9 unset, PATH an empty folder and the install folders inside the temporary one, so no
    test finds the real lprun9; and a fake tool to pass as --lprun."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(temp.cleanup)
        self.dir = Path(temp.name)
        environ = {k: v for k, v in os.environ.items() if k != "LPRUN9"}
        environ["PATH"] = str(self.dir / "empty-path")
        for patcher in (mock.patch.dict(os.environ, environ, clear=True),
                        mock.patch.object(lprun9, "installs", lambda: [str(self.dir / "pf" / "LPRun9.exe"),
                                                                       str(self.dir / "local" / "LPRun9.exe")])):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.tool = make_tool(self.dir / "bin", "LPRun9.exe")  # an .exe, as a faked Windows wants, executable elsewhere
        self.out = self.dir / "out"


class Fake:
    """Stands in for subprocess and os.killpg. Popen makes a process that answers communicate() with the steps given,
    one per call: ("ok", stdout, stderr, exit), "timeout" or "interrupt". run records taskkill; killpg records the
    group stopped."""

    def __init__(self, test, *steps, windows):
        self.steps, self.made, self.ran, self.groups = list(steps), [], [], []
        fake = self

        class Process:
            def __init__(self, args, **kwargs):
                self.args, self.kwargs, self.pid, self.returncode = list(args), kwargs, 4321, None
                self.kills, self.timeouts = 0, []
                fake.made.append(self)

            def communicate(self, timeout=None):
                self.timeouts.append(timeout)
                step = fake.steps.pop(0)
                if step == "timeout":
                    raise subprocess.TimeoutExpired(self.args, timeout)
                if step == "interrupt":
                    raise KeyboardInterrupt
                _, out, err, self.returncode = step
                return out, err

            def poll(self):
                return self.returncode

            def wait(self, timeout=None):
                return self.returncode

            def kill(self):
                self.kills += 1

        def run(args, **kwargs):
            fake.ran.append((list(args), kwargs))
            return subprocess.CompletedProcess(args, 0)

        for patcher in (mock.patch.object(lprun9, "WINDOWS", windows),
                        mock.patch.object(lprun9.subprocess, "Popen", Process),
                        mock.patch.object(lprun9.subprocess, "run", run),
                        mock.patch.object(lprun9.os, "killpg", lambda pid, sig: fake.groups.append((pid, sig)),
                                          create=True)):
            patcher.start()
            test.addCleanup(patcher.stop)

    @property
    def process(self):
        [process] = self.made
        return process


class Interface(Case):
    def test_it_holds_to_the_protocol_and_serves_every_auth_kind(self):
        transport = lprun9.transport()
        self.assertIsInstance(transport, Transport)
        self.assertEqual(transport.name, "lprun9")
        self.assertEqual(transport.serves, frozenset({"sql", "windows", "aad"}))
        self.assertEqual(transport.serves, AUTH_KINDS)

    def test_making_it_looks_for_nothing_and_runs_nothing(self):
        with mock.patch("os.stat", side_effect=AssertionError("looked")), \
                mock.patch.object(lprun9.subprocess, "Popen", side_effect=AssertionError("ran")):
            lprun9.transport()
            lprun9.transport(self.tool)
            lprun9.transport("LPRun9.exe")

    def test_the_script_is_out_slash_name_dot_linq_made_absolute(self):
        transport = lprun9.transport(self.tool)
        self.assertEqual(transport.artifact(self.out, "q"), self.out / "q.linq")
        self.assertEqual(transport.artifact(Path("rel"), "check"), Path(os.path.abspath("rel")) / "check.linq")
        self.assertFalse(self.out.exists())

    def test_a_script_name_that_is_not_a_plain_file_name_is_refused(self):
        transport = lprun9.transport(self.tool)
        for name in ("", ".", "..", "a/b", "a\\b", "..\\q", "c:q", "a\x00b"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                transport.artifact(self.out, name)

    def test_argv_is_the_tool_then_cxname_format_and_the_script_all_absolute(self):
        transport = lprun9.transport(self.tool)
        script = transport.artifact(self.out, "q")
        self.assertEqual(transport.argv(SALES, "csv", script), [self.tool, "-cxname=Sales", "-format=CSV", str(script)])
        self.assertTrue(all(os.path.isabs(a) for a in (transport.argv(SALES, "csv", script)[0], str(script))))

    def test_the_formats_are_lprun9s_own_names(self):
        transport = lprun9.transport(self.tool)
        for fmt, name in (("csv", "CSV"), ("csvi", "CSVi"), ("text", "Text"), ("html", "HTML")):
            with self.subTest(fmt):
                self.assertEqual(transport.argv(SALES, fmt, self.out / "q.linq")[2], f"-format={name}")
        for fmt in ("json", "CSV", "htmlfrag", ""):
            with self.subTest(fmt), self.assertRaises(ValueError):
                transport.argv(SALES, fmt, self.out / "q.linq")

    def test_the_name_as_saved_is_the_one_argument_lprun9_selects_by(self):
        transport = lprun9.transport(self.tool)
        odd = Connection(' Sales "EU" & co ', "s", None, "sql", None, True)
        self.assertEqual(transport.argv(odd, "csv", self.out / "q.linq")[1], '-cxname= Sales "EU" & co ')

    def test_a_connection_with_no_name_is_refused(self):
        with self.assertRaises(NotNamed) as caught:
            lprun9.transport(self.tool).argv(UNNAMED, "csv", self.out / "q.linq")
        self.assertIn("give it a name in LINQPad", str(caught.exception))


class Script(Case):
    """What execute() writes, against a fake process."""

    def execute(self, batch=BATCH, *, name="q", steps=(("ok", b"", b"", 0),), windows=False):
        fake = Fake(self, *steps, windows=windows)
        result = lprun9.transport(self.tool).execute(SALES, batch, "csv", 60, self.out, name)
        return result, fake

    def test_the_script_is_the_sql_header_a_blank_line_then_the_batch(self):
        result, _ = self.execute()
        self.assertEqual(result.artifact, self.out / "q.linq")
        self.assertEqual((self.out / "q.linq").read_bytes(), HEADER + BATCH.encode("utf-8"))

    def test_the_batch_reaches_the_script_byte_for_byte_in_utf8(self):
        for label, batch in (("CRLF", "SELECT 1 AS a,\r\n  2 AS b\r\n"), ("CR", "SELECT 1\rAS a"),
                             ("tabs and trailing spaces", "SELECT\t1 AS a   \n\t "),
                             ("outside ASCII", "SELECT N'caf\u00e9 \u65e5\u672c \U0001f600' AS a"),
                             ("line and paragraph separators", "SELECT 1 AS a\u2028\u2029\u0085"),
                             ("a byte order mark inside", "\ufeffSELECT 1 AS a")):
            with self.subTest(label):
                (self.out / "q.linq").unlink(missing_ok=True)  # with no prelude, not a script execute() replaces
                self.execute(batch)
                self.assertEqual((self.out / "q.linq").read_bytes(), HEADER + batch.encode("utf-8"))

    def test_the_folder_is_made_and_an_old_script_replaced_whole(self):
        short = "SET NOCOUNT ON;\nSET LOCK_TIMEOUT 0;\nSELECT 2 AS b"
        self.execute(BATCH + " -- a long first version of the batch")
        self.execute(short)
        self.assertEqual((self.out / "q.linq").read_bytes(), HEADER + short.encode("utf-8"))
        self.assertEqual(sorted(p.name for p in self.out.iterdir()), ["q.linq"])

    def test_a_script_sql_read_wrote_is_replaced_whatever_its_prelude_or_line_ends(self):
        sql = "SELECT TOP 5 * FROM dbo.Orders ORDER BY 1"
        for batch in (read.batch(sql), read.check_batch(),
                      read.batch(sql, max_rows=0, lock_timeout_ms=0, nolock=True, allow_writable_login=True),
                      read.check_batch(lock_timeout_ms=2147483647), read.batch(sql, max_rows=1)):
            with self.subTest(batch=batch[:50]):
                self.execute(batch)
                self.assertEqual((self.out / "q.linq").read_bytes(), HEADER + batch.encode("utf-8"))
        # The same script after a checkout with Windows line ends (git's core.autocrlf) is still sql-read's.
        (self.out / "q.linq").write_bytes((HEADER + BATCH.encode("utf-8")).replace(b"\n", b"\r\n"))
        self.execute()
        self.assertEqual((self.out / "q.linq").read_bytes(), HEADER + BATCH.encode("utf-8"))

    def test_a_file_sql_read_did_not_write_is_left_alone_and_nothing_runs(self):
        script = self.out / "q.linq"
        query = (b'<Query Kind="SQL">\n  <Connection>\n    <DisplayName>Sales</DisplayName>\n  </Connection>\n'
                 b'</Query>\n\n' + BATCH.encode("utf-8"))
        for label, data in (("a program of the user's, with a connection", PROGRAM),
                            ("a SQL query of the user's, with a connection, then the prelude", query),
                            ("the script's header, then SQL of the user's", HEADER + b"SELECT 1 AS a"),
                            ("the header with a byte order mark before it", b"\xef\xbb\xbf" + HEADER + BATCH.encode()),
                            ("the prelude with no header", BATCH.encode("utf-8")),
                            ("an empty file", b"")):
            with self.subTest(label):
                self.out.mkdir(exist_ok=True)
                script.write_bytes(data)
                fake = Fake(self, ("ok", b"", b"", 0), windows=False)
                with self.assertRaises(Unusable) as caught:
                    lprun9.transport(self.tool).execute(SALES, BATCH, "csv", 60, self.out, "q")
                self.assertIn(str(script), str(caught.exception))
                self.assertIn("--name", str(caught.exception))
                self.assertEqual(script.read_bytes(), data)
                self.assertEqual(sorted(p.name for p in self.out.iterdir()), ["q.linq"])  # no temporary file left
                self.assertEqual(fake.made, [])

    def test_a_folder_where_the_script_goes_is_left_alone_and_nothing_runs(self):
        (self.out / "q.linq").mkdir(parents=True)
        fake = Fake(self, ("ok", b"", b"", 0), windows=False)
        with self.assertRaises(Unusable):
            lprun9.transport(self.tool).execute(SALES, BATCH, "csv", 60, self.out, "q")
        self.assertTrue((self.out / "q.linq").is_dir())
        self.assertEqual(sorted(p.name for p in self.out.iterdir()), ["q.linq"])
        self.assertEqual(fake.made, [])

    def test_through_read_py_a_query_of_the_users_is_kept_and_the_run_exits_3_naming_it(self):
        user, sql = self.dir / "LINQPad Queries", self.dir / "orders.sql"
        user.mkdir()
        (user / "monthly-report.linq").write_bytes(PROGRAM)
        sql.write_text("SELECT TOP (5) 1 AS n;\n", encoding="utf-8")
        fake = Fake(self, ("ok", b"n\r\n1\r\n", b"", 0), windows=False)
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(sys, "stdout", out), mock.patch.object(sys, "stderr", err):
            code = read.main(["query", "--cx", "Sales", "--sql", sql, "--connections", FIXTURE, "--lprun", self.tool,
                              "--out", user, "--name", "monthly-report"])
        self.assertEqual(code, 3, err.getvalue())
        self.assertTrue(err.getvalue().startswith(f"sql-read: {user / 'monthly-report.linq'} "), err.getvalue())
        self.assertEqual((out.getvalue(), fake.made), ("", []))
        self.assertEqual((user / "monthly-report.linq").read_bytes(), PROGRAM)
        self.assertEqual(sorted(p.name for p in user.iterdir()), ["monthly-report.linq"])

    def test_a_link_where_the_script_goes_is_left_alone_not_replaced_or_written_through(self):
        self.out.mkdir()
        # sql-read makes no link, so even one to a script of its own is someone else's.
        for label, data in (("the user's query", PROGRAM), ("a script sql-read wrote", HEADER + BATCH.encode())):
            with self.subTest(label):
                target, link = self.dir / "elsewhere.linq", self.out / "q.linq"
                target.write_bytes(data)
                link.unlink(missing_ok=True)
                try:
                    os.symlink(target, link)
                except (OSError, NotImplementedError) as e:
                    self.skipTest(f"cannot make a symbolic link here: {e}")
                fake = Fake(self, ("ok", b"", b"", 0), windows=False)
                with self.assertRaises(Unusable):
                    lprun9.transport(self.tool).execute(SALES, BATCH, "csv", 60, self.out, "q")
                self.assertEqual(target.read_bytes(), data)
                self.assertTrue(link.is_symlink())
                self.assertEqual(sorted(p.name for p in self.out.iterdir()), ["q.linq"])
                self.assertEqual(fake.made, [])

    def test_a_missing_tool_is_reported_before_anything_is_written_or_run(self):
        fake = Fake(self, windows=False)
        with self.assertRaises(ToolMissing):
            lprun9.transport(str(self.dir / "nowhere" / EXE)).execute(SALES, BATCH, "csv", 60, self.out, "q")
        self.assertFalse(self.out.exists())
        self.assertEqual(fake.made, [])

    def test_a_script_that_cannot_be_written_is_an_oserror_and_nothing_runs(self):
        fake = Fake(self, windows=False)
        self.out.write_bytes(b"a file where the folder should be")
        with self.assertRaises(OSError):
            lprun9.transport(self.tool).execute(SALES, BATCH, "csv", 60, self.out, "q")
        self.assertEqual(fake.made, [])

    def test_a_script_that_cannot_be_moved_into_place_leaves_nothing_behind(self):
        fake = Fake(self, windows=False)
        with mock.patch.object(lprun9.os, "replace", side_effect=PermissionError(13, "in use")), \
                self.assertRaises(OSError):
            lprun9.transport(self.tool).execute(SALES, BATCH, "csv", 60, self.out, "q")
        self.assertEqual(list(self.out.iterdir()), [])
        self.assertEqual(fake.made, [])


class Run(Case):
    """How execute() runs lprun9 and stops it, against a fake process."""

    def execute(self, *steps, windows, timeout=60):
        fake = Fake(self, *steps, windows=windows)
        result = lprun9.transport(self.tool).execute(SALES, BATCH, "csv", timeout, self.out, "q")
        return result, fake

    def test_it_runs_argv_with_no_shell_no_input_and_both_outputs_piped(self):
        for windows in (True, False):
            with self.subTest(windows=windows):
                result, fake = self.execute(("ok", b"id\r\n1\r\n", b"", 0), windows=windows, timeout=42)
                process = fake.process
                self.assertEqual(process.args, [self.tool, "-cxname=Sales", "-format=CSV", str(self.out / "q.linq")])
                self.assertEqual((process.kwargs["stdin"], process.kwargs["stdout"], process.kwargs["stderr"]),
                                 (subprocess.DEVNULL, subprocess.PIPE, subprocess.PIPE))
                self.assertFalse(process.kwargs.get("shell", False))
                self.assertEqual(process.timeouts, [42])
                self.assertEqual(result, Result(0, b"id\r\n1\r\n", b"", False, self.out / "q.linq"))
                self.assertEqual((fake.ran, fake.groups, process.kills), ([], [], 0))

    def test_on_windows_it_starts_lprun9_in_a_new_process_group(self):
        _, fake = self.execute(("ok", b"", b"", 0), windows=True)
        self.assertEqual(fake.process.kwargs.get("creationflags"), 0x00000200)  # CREATE_NEW_PROCESS_GROUP
        self.assertNotIn("start_new_session", fake.process.kwargs)

    def test_elsewhere_it_starts_lprun9_in_a_new_session(self):
        _, fake = self.execute(("ok", b"", b"", 0), windows=False)
        self.assertIs(fake.process.kwargs.get("start_new_session"), True)
        self.assertNotIn("creationflags", fake.process.kwargs)

    def test_the_tools_exit_code_and_bytes_come_back_unchanged(self):
        out, err = b'id,name\r\n1,"caf\xc3\xa9"\r\n2,\xff\r\n', b"Error: \x1b[31mno such connection\r\n"
        result, _ = self.execute(("ok", out, err, 3), windows=False)
        self.assertEqual(result, Result(3, out, err, False, self.out / "q.linq"))

    def test_on_windows_a_timeout_stops_the_tree_with_taskkill_then_kill(self):
        result, fake = self.execute("timeout", ("ok", b"partial", b"", 1), windows=True, timeout=7)
        [(args, kwargs)] = fake.ran
        self.assertEqual(args[1:], ["/PID", "4321", "/T", "/F"])
        self.assertEqual(ntpath.basename(args[0]).lower(), "taskkill.exe")
        self.assertIsNotNone(kwargs.get("timeout"))
        self.assertEqual((fake.groups, fake.process.kills), ([], 1))
        self.assertEqual(fake.process.timeouts, [7, lprun9.DRAIN_WAIT])
        self.assertEqual(result, Result(1, b"partial", b"", True, self.out / "q.linq"))

    def test_taskkill_comes_from_system32_never_from_path_or_the_current_folder(self):
        os.environ["SystemRoot"] = r"D:\Win"
        _, fake = self.execute("timeout", ("ok", b"", b"", 1), windows=True)
        self.assertEqual(fake.ran[0][0][0], r"D:\Win\System32\taskkill.exe")
        del os.environ["SystemRoot"]
        _, fake = self.execute("timeout", ("ok", b"", b"", 1), windows=True)
        self.assertEqual(fake.ran[0][0][0], r"C:\Windows\System32\taskkill.exe")

    def test_elsewhere_a_timeout_kills_the_process_group_then_the_process(self):
        result, fake = self.execute("timeout", ("ok", b"", b"late", -9), windows=False)
        self.assertEqual(fake.groups, [(4321, getattr(signal, "SIGKILL", 9))])
        self.assertEqual((fake.ran, fake.process.kills), ([], 1))
        self.assertEqual(result, Result(-9, b"", b"late", True, self.out / "q.linq"))

    def test_an_interrupt_stops_the_run_the_same_way_and_goes_on_up(self):
        for windows in (True, False):
            with self.subTest(windows=windows):
                fake = Fake(self, "interrupt", windows=windows)
                with self.assertRaises(KeyboardInterrupt):
                    lprun9.transport(self.tool).execute(SALES, BATCH, "csv", 60, self.out, "q")
                self.assertEqual(fake.process.kills, 1)
                self.assertEqual((len(fake.ran), len(fake.groups)), (1, 0) if windows else (0, 1))

    def test_output_still_held_open_after_the_stop_is_given_up_on(self):
        result, fake = self.execute("timeout", "timeout", windows=True)
        self.assertEqual((result.timed_out, result.exit, result.stdout), (True, -1, b""))
        self.assertIn(b"still holds", result.stderr)
        self.assertEqual(fake.process.timeouts, [60, lprun9.DRAIN_WAIT])

    def test_a_taskkill_that_fails_or_hangs_still_ends_in_kill(self):
        for error in (OSError("no taskkill"), subprocess.TimeoutExpired(["taskkill"], 30)):
            with self.subTest(error=type(error).__name__):
                fake = Fake(self, "timeout", ("ok", b"", b"", 1), windows=True)
                with mock.patch.object(lprun9.subprocess, "run", side_effect=error):
                    result = lprun9.transport(self.tool).execute(SALES, BATCH, "csv", 60, self.out, "q")
                self.assertTrue(result.timed_out)
                self.assertEqual(fake.process.kills, 1)

    def test_a_process_group_already_gone_still_ends_in_kill(self):
        fake = Fake(self, "timeout", ("ok", b"", b"", -9), windows=False)
        with mock.patch.object(lprun9.os, "killpg", side_effect=ProcessLookupError, create=True):
            result = lprun9.transport(self.tool).execute(SALES, BATCH, "csv", 60, self.out, "q")
        self.assertTrue(result.timed_out)
        self.assertEqual(fake.process.kills, 1)

    def test_a_tool_that_cannot_start_is_an_oserror(self):
        Fake(self, windows=False)
        with mock.patch.object(lprun9.subprocess, "Popen", side_effect=FileNotFoundError(2, "not found")), \
                self.assertRaises(OSError):
            lprun9.transport(self.tool).execute(SALES, BATCH, "csv", 60, self.out, "q")

    def test_it_never_prints(self):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(sys, "stdout", out), mock.patch.object(sys, "stderr", err):
            self.execute("timeout", "timeout", windows=True)
            self.execute(("ok", b"x", b"y", 1), windows=False)
        self.assertEqual((out.getvalue(), err.getvalue()), ("", ""))


class Lookup(Case):
    """Where lprun9 is found: --lprun, env LPRUN9, lprun9 on PATH, then LINQPad 9's install folders."""

    def places(self):
        """An lprun9 in every place it is looked for, the install folders being the ones Case set up."""
        return {"--lprun": make_tool(self.dir / "given"), "LPRUN9": make_tool(self.dir / "env"),
                "PATH": make_tool(self.dir / "path", "lprun9.exe" if lprun9.WINDOWS else "lprun9"),
                "Program Files": make_tool(self.dir / "pf", "LPRun9.exe"),
                "LocalAppData": make_tool(self.dir / "local", "LPRun9.exe")}

    def test_the_lookup_order(self):
        places = self.places()
        os.environ["LPRUN9"] = places["LPRUN9"]
        os.environ["PATH"] = os.pathsep.join([str(self.dir / "empty"), str(self.dir / "path")])
        self.assertEqual(lprun9.transport(places["--lprun"]).locate(), places["--lprun"])
        self.assertEqual(lprun9.transport().locate(), places["LPRUN9"])
        del os.environ["LPRUN9"]
        self.assertEqual(lprun9.transport().locate(), places["PATH"])
        os.environ["PATH"] = str(self.dir / "empty")
        self.assertEqual(lprun9.transport().locate(), places["Program Files"])
        os.remove(places["Program Files"])
        self.assertEqual(lprun9.transport().locate(), places["LocalAppData"])
        os.remove(places["LocalAppData"])
        with self.assertRaises(ToolMissing) as caught:
            lprun9.transport().locate()
        message = str(caught.exception)
        for said in ("--lprun", "LPRUN9", "PATH", str(self.dir / "pf" / "LPRun9.exe"),
                     str(self.dir / "local" / "LPRun9.exe")):
            self.assertIn(said, message)

    def test_the_install_folders_are_linqpad_9s(self):
        os.environ["LOCALAPPDATA"] = r"C:\Users\someone\AppData\Local"
        self.assertEqual(REAL_INSTALLS(), [r"C:\Program Files\LINQPad9\LPRun9.exe",
                                           r"C:\Users\someone\AppData\Local\Programs\LINQPad9\LPRun9.exe"])
        del os.environ["LOCALAPPDATA"]
        self.assertEqual(REAL_INSTALLS(), [r"C:\Program Files\LINQPad9\LPRun9.exe",
                                           r"%LocalAppData%\Programs\LINQPad9\LPRun9.exe"])

    def test_an_install_folder_that_is_no_absolute_path_here_is_named_but_not_looked_at(self):
        # With LOCALAPPDATA unset the second place is "%LocalAppData%\...", and elsewhere than Windows even the first
        # is no absolute path. Read from the current folder, either could reach a file planted there.
        relative = r"%LocalAppData%\Programs\LINQPad9\LPRun9.exe"
        make_tool((self.dir / relative).parent, (self.dir / relative).name)
        here = os.getcwd()
        os.chdir(self.dir)
        self.addCleanup(os.chdir, here)
        with mock.patch.object(lprun9, "installs", lambda: [relative]), self.assertRaises(ToolMissing) as caught:
            lprun9.transport().locate()
        self.assertIn(relative, str(caught.exception))

    @unittest.skipIf(os.name == "nt", "on Windows the install folders' paths are absolute, and may hold lprun9")
    def test_elsewhere_than_windows_the_install_folders_are_named_but_not_looked_at(self):
        windows = r"C:\Program Files\LINQPad9\LPRun9.exe"
        make_tool(self.dir, windows)  # a file of that very name, backslashes and all, in the current folder
        here = os.getcwd()
        os.chdir(self.dir)
        self.addCleanup(os.chdir, here)
        with mock.patch.object(lprun9, "installs", REAL_INSTALLS), self.assertRaises(ToolMissing) as caught:
            lprun9.transport().locate()
        self.assertIn(windows, str(caught.exception))

    def test_a_tool_named_but_missing_is_not_looked_for_elsewhere(self):
        self.places()
        os.environ["PATH"] = str(self.dir / "path")
        missing = os.path.abspath(self.dir / "nowhere" / EXE)
        for given, env, origin in ((missing, None, "--lprun"), (None, missing, "LPRUN9")):
            with self.subTest(origin):
                os.environ.pop("LPRUN9", None)
                if env:
                    os.environ["LPRUN9"] = env
                with self.assertRaises(ToolMissing) as caught:
                    lprun9.transport(given).locate()
                self.assertIn(missing, str(caught.exception))
                self.assertIn(origin, str(caught.exception))

    def test_spaces_and_quotes_around_lprun9_are_dropped(self):
        for value in (f"  {self.tool}  ", f'"{self.tool}"', f' " {self.tool} " '):
            with self.subTest(value=value):
                os.environ["LPRUN9"] = value
                self.assertEqual(lprun9.transport().locate(), self.tool)
        os.environ["LPRUN9"] = '""'
        with self.assertRaises(ToolMissing) as caught:
            lprun9.transport().locate()
        self.assertIn("LPRUN9 (not set)", str(caught.exception))

    def test_a_bare_program_name_is_looked_up_on_path(self):
        name = "lprun9.exe" if lprun9.WINDOWS else "lprun9"
        on_path = make_tool(self.dir / "path", name)
        os.environ["PATH"] = str(self.dir / "path")
        self.assertEqual(lprun9.transport(name).locate(), on_path)
        os.environ["LPRUN9"] = name
        self.assertEqual(lprun9.transport().locate(), on_path)
        os.environ["PATH"] = str(self.dir / "empty")
        with self.assertRaises(ToolMissing) as caught:
            lprun9.transport(name).locate()
        self.assertIn("PATH", str(caught.exception))

    def test_the_current_folder_and_relative_path_entries_are_never_searched(self):
        name = "lprun9.exe" if lprun9.WINDOWS else "lprun9"
        make_tool(self.dir, name)
        make_tool(self.dir / "relative", name)
        here = os.getcwd()
        os.chdir(self.dir)
        self.addCleanup(os.chdir, here)
        for entries in ("", ".", "relative", os.pathsep.join(["", ".", "relative"])):
            with self.subTest(PATH=entries):
                os.environ["PATH"] = entries
                with self.assertRaises(ToolMissing):
                    lprun9.transport().locate()
                with self.assertRaises(ToolMissing):
                    lprun9.transport(name).locate()

    def test_on_windows_only_an_exe_is_run(self):
        # A .cmd or .bat would run through cmd.exe, which reads the connection's name and the script's path as
        # commands: an & in either would start a program.
        with mock.patch.object(lprun9, "WINDOWS", True):
            for name in ("lprun9.cmd", "lprun9.bat", "lprun9"):
                make_tool(self.dir / "path", name)
            os.environ["PATH"] = str(self.dir / "path")
            with self.assertRaises(ToolMissing):
                lprun9.transport().locate()
            for name in ("lprun9.cmd", "lprun9.bat", "lprun9"):
                with self.subTest(name), self.assertRaises(Unusable) as caught:
                    lprun9.transport(str(self.dir / "path" / name)).locate()
                self.assertIn(".exe", str(caught.exception))
            exe = make_tool(self.dir / "other", "LPRUN9.EXE")
            self.assertEqual(lprun9.transport(exe).locate(), exe)

    def test_on_windows_a_quoted_path_entry_is_searched(self):
        with mock.patch.object(lprun9, "WINDOWS", True):
            tool = make_tool(self.dir / "with space", "lprun9.exe")
            os.environ["PATH"] = f'"{self.dir / "with space"}"'
            self.assertEqual(lprun9.transport().locate(), tool)

    @unittest.skipIf(os.name == "nt", "Windows has no execute permission to check")
    def test_elsewhere_a_tool_that_cannot_be_executed_is_passed_over_or_refused(self):
        tool = make_tool(self.dir / "path", "lprun9")
        os.chmod(tool, 0o644)
        os.environ["PATH"] = str(self.dir / "path")
        with self.assertRaises(ToolMissing):
            lprun9.transport().locate()
        with self.assertRaises(Unusable) as caught:
            lprun9.transport(tool).locate()
        self.assertIn("executable", str(caught.exception))

    def test_a_folder_named_as_the_tool_is_refused(self):
        folder = self.dir / "LPRun9.exe"
        folder.mkdir()
        with self.assertRaises(Unusable):
            lprun9.transport(str(folder)).locate()

    def test_looking_runs_nothing(self):
        self.places()
        with mock.patch.object(lprun9.subprocess, "Popen", side_effect=AssertionError("ran")), \
                mock.patch.object(lprun9.subprocess, "run", side_effect=AssertionError("ran")):
            self.assertEqual(lprun9.transport().locate(), os.path.abspath(self.dir / "pf" / "LPRun9.exe"))


# The stand-in for lprun9 in the real-process tests: as "child" it starts a grandchild that shares its output, waits
# until the grandchild beats, says "ready" and sleeps; as "grandchild" it appends a beat to a file every tenth of a
# second. Each stops by itself after 30 seconds at most.
CHILD = r'''
import os, subprocess, sys, time
role, beat = sys.argv[1], sys.argv[2]
if role == "child":
    subprocess.Popen([sys.executable, __file__, "grandchild", beat], stdout=sys.stdout, stderr=sys.stderr)
    deadline = time.monotonic() + 20
    while not os.path.exists(beat) and time.monotonic() < deadline:
        time.sleep(0.05)
    sys.stdout.write("ready\n")
    sys.stdout.flush()
    time.sleep(30)
else:
    end = time.monotonic() + 30
    while time.monotonic() < end:
        with open(beat, "a") as f:
            f.write(".")
        time.sleep(0.1)
'''


class RealProcess(Case):
    """execute() with a real process: Python in lprun9's place, through the transport's own Popen and stop."""

    def execute(self, argv, timeout):
        transport = lprun9.transport(self.tool)
        with mock.patch.object(transport, "argv", return_value=argv):
            return transport.execute(SALES, BATCH, "csv", timeout, self.out, "q")

    def test_a_run_returns_the_exit_code_and_both_outputs_as_bytes_and_reads_no_input(self):
        code = (r"import sys; data = sys.stdin.read(); sys.stdout.buffer.write(b'a,b\r\n1,\xff\r\n'); "
                r"sys.stderr.buffer.write(b'read %d' % len(data)); sys.exit(3)")
        result = self.execute([sys.executable, "-c", code], 60)
        self.assertEqual(result, Result(3, b"a,b\r\n1,\xff\r\n", b"read 0", False, self.out / "q.linq"))
        self.assertEqual((self.out / "q.linq").read_bytes(), HEADER + BATCH.encode("utf-8"))

    def test_a_run_past_its_timeout_is_stopped_with_the_process_it_started(self):
        child, beat = self.dir / "child.py", self.dir / "beat"
        child.write_text(CHILD, encoding="utf-8")
        started = time.monotonic()
        result = self.execute([sys.executable, str(child), "child", str(beat)], 4)
        took = time.monotonic() - started
        self.assertTrue(result.timed_out)
        self.assertIn(b"ready", result.stdout)  # the grandchild was running when the timeout came
        self.assertNotIn(b"still holds", result.stderr)  # nothing outlived the stop to hold the output open
        self.assertLess(took, 4 + lprun9.DRAIN_WAIT)
        size = beat.stat().st_size
        time.sleep(1.0)
        self.assertEqual(beat.stat().st_size, size, "the grandchild still beats")


# A stand-in for lprun9 itself, for read.py on POSIX: it records its arguments and the script it was given, then
# answers as lprun9 would with CSV, or sleeps when FAKE_LPRUN9_SLEEP is set.
FAKE_LPRUN9 = r'''#!{python}
import json, os, sys, time
with open(sys.argv[-1], "rb") as f:
    script = f.read().decode("utf-8")
with open(os.environ["FAKE_LPRUN9_RECORD"], "w", encoding="utf-8") as f:
    json.dump({{"argv": sys.argv, "script": script}}, f)
if os.environ.get("FAKE_LPRUN9_SLEEP"):
    time.sleep(float(os.environ["FAKE_LPRUN9_SLEEP"]))
sys.stdout.buffer.write(b"id,name\r\n1,a\r\n2,b\r\n")
'''


class ThroughReadPy(Case):
    """read.py in a process of its own, from the plugin's scripts folder, with the real registry and transport."""

    def run_read(self, *argv, env=None):
        clean = {k: v for k, v in os.environ.items()
                 if k not in ("SQL_READ_CONNECTIONS", "PYTHONIOENCODING", "PYTHONUTF8")}
        return subprocess.run([sys.executable, str(SCRIPTS / "read.py"), *[str(a) for a in argv]],
                              capture_output=True, timeout=120, env={**clean, **(env or {})})

    def sql(self, text="SELECT TOP 5 * FROM dbo.Orders ORDER BY 1"):
        path = self.dir / "q.sql"
        path.write_text(text, encoding="utf-8")
        return path

    def test_plan_shows_the_command_and_script_and_writes_nothing(self):
        proc = self.run_read("plan", "--cx", "sales", "--sql", self.sql(), "--connections", FIXTURE,
                             "--lprun", self.tool)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        plan = json.loads(proc.stdout)
        script = str(self.dir / "sql-read" / "q.linq")
        self.assertEqual(plan["argv"], [self.tool, "-cxname=Sales", "-format=CSV", script])
        self.assertEqual((plan["artifact"], plan["transport"], plan["auth"]), (script, "lprun9", "aad"))
        self.assertFalse((self.dir / "sql-read").exists())

    def test_a_tool_that_is_not_there_exits_3_naming_where_it_looked(self):
        missing = os.path.abspath(self.dir / "nowhere" / EXE)
        proc = self.run_read("plan", "--cx", "Sales", "--sql", self.sql(), "--connections", FIXTURE,
                             "--lprun", missing)
        self.assertEqual(proc.returncode, 3)
        plan = json.loads(proc.stdout)
        self.assertIn(missing, plan["error"])
        self.assertIn("--lprun", plan["error"])
        self.assertEqual(plan["artifact"], str(self.dir / "sql-read" / "q.linq"))

    def fake_lprun9(self):
        tool = self.dir / "bin" / "lprun9"
        tool.write_text(FAKE_LPRUN9.format(python=sys.executable), encoding="utf-8")
        tool.chmod(0o755)
        return str(tool)

    @unittest.skipIf(os.name == "nt", "the stand-in for lprun9 is a script, which Windows runs only through a shell")
    def test_query_runs_lprun9_with_the_script_and_passes_its_output_through(self):
        tool, record = self.fake_lprun9(), self.dir / "record.json"
        sql = "SELECT TOP 5 * FROM dbo.Orders ORDER BY 1"
        proc = self.run_read("query", "--cx", "Sales", "--sql", self.sql(sql), "--connections", FIXTURE,
                             "--lprun", tool, env={"FAKE_LPRUN9_RECORD": str(record)})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, b"id,name\r\n1,a\r\n2,b\r\n")
        self.assertRegex(proc.stderr.decode("utf-8"), r"sql-read: Sales \(.*\): 2 rows in \d+\.\d s\n\Z")
        ran = json.loads(record.read_text(encoding="utf-8"))
        script = str(self.dir / "sql-read" / "q.linq")
        self.assertEqual(ran["argv"], [tool, "-cxname=Sales", "-format=CSV", script])
        self.assertEqual(ran["script"], HEADER.decode() + read.batch(sql))

    @unittest.skipIf(os.name == "nt", "the stand-in for lprun9 is a script, which Windows runs only through a shell")
    def test_query_past_its_timeout_exits_6_promptly(self):
        tool, record = self.fake_lprun9(), self.dir / "record.json"
        started = time.monotonic()
        proc = self.run_read("query", "--cx", "Sales", "--sql", self.sql(), "--connections", FIXTURE, "--lprun", tool,
                             "--timeout", "1", env={"FAKE_LPRUN9_RECORD": str(record), "FAKE_LPRUN9_SLEEP": "30"})
        self.assertEqual(proc.returncode, 6, proc.stderr)
        self.assertLess(time.monotonic() - started, 20)
        self.assertIn(b"timed out after 1 s", proc.stderr)


if __name__ == "__main__":
    unittest.main()
