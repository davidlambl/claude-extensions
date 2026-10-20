"""read.py running a batch against a fake transport: exit codes, what reaches stdout and stderr, the row count and the
cap note, check's verdict, and the connections listing. Run with: python3 -m unittest discover -s skills/sql-read/tests

The fakes and helpers come from test_plan.py. The last tests run read.py in a process of its own, from a copy of the
scripts folder with stand-in backend modules, under a console whose code page is cp1252 rather than UTF-8, as Git
Bash gives Python on Windows. Characters outside ASCII are written as escapes."""
import base64, codecs, csv, json, os, re, unittest
from dataclasses import dataclass
from pathlib import Path
from unittest import mock

from test_plan import (DEV, SALES, UNNAMED, FakeRegistry, FakeTransport, Harness, read, run_script, stage)
from backend import Connection, NotFound, Result, ToolMissing

WHO = "Sales (sales-sql.database.windows.net / Product, production)"
CAP_NOTE = " \u2014 cap reached; there may be more: raise --max-rows or add TOP and ORDER BY"
TIMED_OUT = ("timed out after 60 s; if the connection signs in with Azure AD interactively, open it once in LINQPad "
             "first to cache the token")


def rows(n, header=b"id"):
    return header + b"\r\n" + b"".join(b"%d\r\n" % i for i in range(n))


class Run(Harness):
    def query(self, result, *options, registry=None, encoding="utf-8", sql="SELECT TOP 3 * FROM dbo.Orders"):
        """read.py query --cx Sales against a fake transport that answers with result; stderr comes back as text."""
        transport = self.use(registry=registry, transport=FakeTransport(result))
        cx = (registry.items[0].name if registry else "Sales")
        code, out, err = self.main("query", "--cx", cx, "--sql", self.sql_file(sql), *options, encoding=encoding)
        return code, out, err.decode("utf-8"), transport

    def test_a_run_that_succeeds_exits_0_with_one_summary_line(self):
        data = b'OrderId,Note\r\n1,"caf\xc3\xa9"\r\n2,"two\r\nlines"\r\n'
        code, out, err, _ = self.query(Result(0, data))
        self.assertEqual(code, 0, err)
        self.assertEqual(out, data)
        self.assertRegex(err, rf"\Asql-read: {re.escape(WHO)}: 2 rows in \d+\.\d s\n\Z")

    def test_stdout_passes_through_untouched_whatever_the_bytes(self):
        data = b"OrderId Created\r\n\x00\xff\xfe not text \x1b[2J\r\n\n\rend"
        for fmt in ("text", "html", "csv"):
            with self.subTest(fmt):
                code, out, err, _ = self.query(Result(0, data), "--format", fmt, encoding="cp1252")
                self.assertEqual(code, 0, err)
                self.assertEqual(out, data)

    def test_the_tools_failure_is_exit_1_with_its_error_shown(self):
        code, out, err, _ = self.query(Result(2, b"partial", b"Login failed for user 'web_reader'.\r\n"))
        self.assertEqual(code, 1)
        self.assertEqual(out, b"partial")
        self.assertIn("Login failed for user 'web_reader'.\r\n", err)
        self.assertTrue(err.endswith(f"sql-read: {WHO}: lprun9 failed with exit code 2; its error is above\n"), err)
        self.assertNotIn("rows in", err)

    def test_a_failure_that_printed_nothing_says_so(self):
        code, _, err, _ = self.query(Result(1))
        self.assertEqual(code, 1)
        self.assertEqual(err, f"sql-read: {WHO}: lprun9 failed with exit code 1, and printed no error\n")

    def test_the_guards_throw_becomes_exit_5_with_a_clean_message(self):
        stderr = (b"Msg 50001, Level 16, State 1, Line 4\r\n"
                  b"sql-read-guard: this login can write to this database\r\n"
                  b"   at LINQPad.Runner.Execute(Query q)\r\n")
        code, out, err, _ = self.query(Result(1, b"", stderr))
        self.assertEqual(code, 5)
        self.assertEqual(out, b"")
        self.assertTrue(err.startswith(f"sql-read: {WHO}: this login can write to this database, so nothing ran."), err)
        self.assertIn("read.py check", err)
        for raw in ("Msg 50001", "LINQPad.Runner", "sql-read-guard"):
            self.assertNotIn(raw, err)
        self.assertEqual(err.count("\n"), 1)

    def test_the_probes_throw_on_stdout_is_exit_5_too(self):
        # lprun9 reports a script's error on stderr; were the probe's THROW to reach stdout instead, it must read the
        # same: exit 5, with the tool's report left out. Rows of a run that succeeded are data, whatever they say.
        report = b"Msg 50001, Level 16, State 1, Line 4\r\nsql-read-guard: this login can write to this database\r\n"
        code, out, err, _ = self.query(Result(1, report, b""))
        self.assertEqual(code, 5, err)
        self.assertEqual(out, b"")
        self.assertTrue(err.startswith(f"sql-read: {WHO}: this login can write to this database, so nothing ran."), err)
        self.assertNotIn("Msg 50001", err)
        rows = b"note\r\nsql-read-guard: this login can write to this database\r\n"
        code, out, err, _ = self.query(Result(0, rows))
        self.assertEqual(code, 0, err)
        self.assertEqual(out, rows)

    def test_exit_5_for_a_connection_listed_in_writable_ok_says_how_to_run_it(self):
        stderr = b"sql-read-guard: this login can write to this database\r\n"
        with mock.patch.dict(os.environ, {"SQL_READ_WRITABLE_OK": "sales"}):
            code, _, err, _ = self.query(Result(1, b"", stderr))
        self.assertEqual(code, 5)
        self.assertIn("Sales is listed in SQL_READ_WRITABLE_OK", err)
        self.assertIn(f"{read.run_prefix()} --cx", err)
        self.assertEqual(err.count("\n"), 1)
        code, _, err, _ = self.query(Result(1, b"", stderr))
        self.assertEqual(code, 5)
        self.assertNotIn("SQL_READ_WRITABLE_OK", err)

    def test_with_allow_writable_login_the_marker_means_nothing(self):
        code, _, err, _ = self.query(Result(1, b"", b"sql-read-guard: this login can write to this database\r\n"),
                                     "--allow-writable-login")
        self.assertEqual(code, 1)
        self.assertIn("lprun9 failed with exit code 1", err)

    def test_a_timeout_exits_6_with_the_plans_message(self):
        code, out, err, _ = self.query(Result(-1, b"id\r\n1\r\n", b"", timed_out=True))
        self.assertEqual(code, 6)
        self.assertEqual(out, b"id\r\n1\r\n")
        self.assertEqual(err, f"sql-read: {WHO}: {TIMED_OUT}\n")
        code, _, err, _ = self.query(Result(-1, timed_out=True), "--timeout", "1.5")
        self.assertEqual(code, 6)
        self.assertIn(": timed out after 1.5 s; if the connection", err)
        code, _, err, _ = self.query(Result(1, b"", b"sql-read-guard: this login can write\r\n", timed_out=True))
        self.assertEqual(code, 6, "a timeout is reported as one, whatever else the tool printed")

    def test_rows_are_counted_with_the_csv_module(self):
        data = b'id,note\r\n1,"two\r\nlines"\r\n2,"a, b"\r\n3,""\r\n4,"say ""hi"""\r\n'
        for fmt in ("csv", "csvi"):
            with self.subTest(fmt):
                code, _, err, _ = self.query(Result(0, data), "--format", fmt)
                self.assertEqual(code, 0, err)
                self.assertIn(": 4 rows in ", err)

    def test_the_cap_note_comes_at_exactly_max_rows(self):
        code, _, err, _ = self.query(Result(0, rows(3)), "--max-rows", "3")
        self.assertRegex(err, rf": 3 rows in \d+\.\d s{re.escape(CAP_NOTE)}\n\Z")
        code, _, err, _ = self.query(Result(0, rows(2)), "--max-rows", "3")
        self.assertRegex(err, r": 2 rows in \d+\.\d s\n\Z")
        code, _, err, _ = self.query(Result(0, rows(200)))
        self.assertRegex(err, rf": 200 rows in \d+\.\d s{re.escape(CAP_NOTE)}\n\Z")
        code, _, err, _ = self.query(Result(0, rows(5)), "--max-rows", "0")
        self.assertRegex(err, r": 5 rows in \d+\.\d s\n\Z")

    def test_one_row_no_rows_blank_lines_and_byte_order_marks(self):
        for data, said in ((rows(1), ": 1 row in "), (b"id\r\n", ": 0 rows in "), (b"", ": 0 rows in "),
                           (b"\r\nid\r\n1\r\n2\r\n\r\n\r\n", ": 2 rows in "),
                           (b"note\r\na\r\n\r\nb\r\n", ": 3 rows in "),
                           (codecs.BOM_UTF8 + rows(1), ": 1 row in "),
                           (codecs.BOM_UTF16_LE + "id\r\n1\r\n2\r\n".encode("utf-16-le"), ": 2 rows in ")):
            with self.subTest(data):
                code, _, err, _ = self.query(Result(0, data))
                self.assertEqual(code, 0, err)
                self.assertIn(said, err)

    def test_text_and_html_count_nothing(self):
        for fmt in ("text", "html"):
            with self.subTest(fmt):
                code, _, err, _ = self.query(Result(0, rows(200)), "--format", fmt)
                self.assertEqual(code, 0, err)
                self.assertNotRegex(err, r"\d+ rows? in")
                self.assertRegex(err, rf"\Asql-read: {re.escape(WHO)}: ran in \d+\.\d s; rows are counted for "
                                      r"--format csv and csvi only \(cap 200\)\n\Z")

    def test_output_the_csv_module_cannot_read_is_not_counted(self):
        with mock.patch("csv.reader", side_effect=csv.Error("bad")):
            code, _, err, _ = self.query(Result(0, rows(2)))
        self.assertEqual(code, 0, err)
        self.assertIn("the output could not be read as CSV, so its rows were not counted", err)

    def test_a_legacy_console_never_crashes_and_gets_utf8(self):
        odd = Connection("Prod \u26a0 \u65e5\u672c", "srv-\u00e9", "db", "sql", None, True)
        data = "id,name\r\n1,\u65e5\u672c\r\n".encode("utf-8")
        code, out, err, _ = self.query(Result(0, data), "--max-rows", "1", registry=FakeRegistry(odd),
                                       encoding="cp1252")
        self.assertEqual(code, 0, err)
        self.assertEqual(out, data)
        self.assertIn("sql-read: Prod \u26a0 \u65e5\u672c (srv-\u00e9 / db, production): 1 row in ", err)
        self.assertTrue(err.endswith(CAP_NOTE + "\n"), err)

    def test_control_characters_in_a_name_are_shown_not_sent_to_the_terminal(self):
        evil = Connection("Evil\x1b]0;x\x07", "srv\n", None, "sql", None, False)
        code, _, err, _ = self.query(Result(0, rows(1)), registry=FakeRegistry(evil))
        self.assertEqual(code, 0, err)
        self.assertIn("sql-read: Evil\\x1b]0;x\\x07 (srv\\x0a): 1 row in ", err)
        self.assertNotIn("\x1b", err)
        self.assertEqual(err.count("\n"), 1)

    def test_a_transport_that_cannot_write_or_start_exits_1(self):
        self.use(transport=FakeTransport(raises=PermissionError(13, "Permission denied", "C:/x/q.linq")))
        code, _, err = self.main("query", "--cx", "Sales", "--sql", self.sql_file("SELECT 1 AS x"))
        self.assertEqual(code, 1)
        self.assertTrue(err.startswith(f"sql-read: {WHO}: lprun9 could not run: ".encode()), err)
        self.assertIn(b"Permission denied", err)
        self.assertNotIn(b"unexpected", err)
        self.assertNotIn(b"Traceback", err)

    def test_an_error_with_a_line_break_stays_on_one_line(self):
        for raised, code in ((OSError("could not start\nsql-read: forged"), 1),
                             (ToolMissing("lprun9 was not found\nsql-read: forged"), 3),
                             (ZeroDivisionError("x\nsql-read: forged"), 1)):
            with self.subTest(type(raised).__name__):
                self.use(transport=FakeTransport(raises=raised))
                got, _, err = self.main("query", "--cx", "Sales", "--sql", self.sql_file("SELECT 1 AS x"))
                self.assertEqual(got, code)
                self.assertEqual(err.count(b"\n"), 1, err)
                self.assertIn(b"\\x0asql-read: forged\n", err)

    def test_a_tool_that_vanishes_before_the_run_exits_3(self):
        self.use(transport=FakeTransport(raises=ToolMissing("lprun9 was not found at C:/x")))
        code, _, err = self.main("query", "--cx", "Sales", "--sql", self.sql_file("SELECT 1 AS x"))
        self.assertEqual(code, 3)
        self.assertEqual(err, b"sql-read: lprun9 was not found at C:/x\n")

    def test_a_transport_bug_is_reported_as_one(self):
        self.use(transport=FakeTransport(raises=ZeroDivisionError("division by zero")))
        code, _, err = self.main("query", "--cx", "Sales", "--sql", self.sql_file("SELECT 1 AS x"))
        self.assertEqual(code, 1)
        self.assertIn(b"unexpected error: ZeroDivisionError: division by zero", err)
        self.assertNotIn(b"Traceback", err)

    def test_text_from_a_transport_instead_of_bytes_still_passes_through(self):
        code, out, err, _ = self.query(Result(0, "id\r\n1\r\n", "a warning\r\n"))
        self.assertEqual((code, out), (0, b"id\r\n1\r\n"))
        self.assertTrue(err.startswith("a warning\r\n"), err)
        self.assertIn(": 1 row in ", err)

    def test_the_tools_warnings_are_shown_before_the_summary(self):
        code, _, err, _ = self.query(Result(0, rows(1), b"WARNING: something\r\n"))
        self.assertEqual(code, 0)
        self.assertRegex(err, rf"\AWARNING: something\r\nsql-read: {re.escape(WHO)}: 1 row in ")


class Check(Harness):
    def check(self, result, cx="Sales"):
        transport = self.use(transport=FakeTransport(result))
        code, out, err = self.main("check", "--cx", cx)
        return code, out, err.decode("utf-8"), transport

    def test_a_check_that_finds_nothing_is_read_only_and_exits_0(self):
        code, out, err, transport = self.check(Result(0, b"kind,name\r\n"))
        self.assertEqual(code, 0, err)
        self.assertEqual(out, b"kind,name\r\n")
        self.assertEqual(err, f"sql-read: {WHO}: read-only: no write role, no database permission and no "
                              f"schema permission to write (grants on single objects are not checked)\n")
        self.assertEqual(transport.calls[0]["batch"], read.check_batch())

    def test_a_check_that_finds_write_roles_or_permissions_exits_5(self):
        data = b"kind,name\r\nrole,db_owner\r\npermission,INSERT\r\npermission,ALTER ANY SCHEMA\r\n"
        code, out, err, _ = self.check(Result(0, data))
        self.assertEqual(code, 5)
        self.assertEqual(out, data)
        self.assertTrue(err.startswith(f"sql-read: {WHO}: this login can write (role db_owner, permission INSERT, "
                                       f"permission ALTER ANY SCHEMA)"), err)
        self.assertEqual(err.count("\n"), 1)

    def test_a_long_verdict_names_the_first_five_and_counts_the_rest(self):
        # A db_owner login holds every write permission on every schema: a hundred and more, all on stdout already.
        rows = [f"permission,INSERT ON SCHEMA::s{n}" for n in range(12)]
        data = ("kind,name\r\n" + "\r\n".join(rows) + "\r\n").encode("ascii")
        code, out, err, _ = self.check(Result(0, data))
        self.assertEqual(code, 5)
        self.assertEqual(out, data)
        self.assertIn("permission INSERT ON SCHEMA::s4, and 7 more, listed above)", err)
        self.assertNotIn("SCHEMA::s5", err)
        self.assertEqual(err.count("\n"), 1)
        code, _, err, _ = self.check(Result(0, ("kind,name\r\n" + "\r\n".join(rows[:5])).encode("ascii")))
        self.assertIn("SCHEMA::s4)", err)
        self.assertNotIn("more", err)

    def test_a_check_of_a_listed_connection_says_query_may_take_the_flag(self):
        with mock.patch.dict(os.environ, {"SQL_READ_WRITABLE_OK": "*"}):
            code, _, err, _ = self.check(Result(0, b"kind,name\r\nrole,db_owner\r\n"))
        self.assertEqual(code, 5)
        self.assertIn("listed in SQL_READ_WRITABLE_OK", err)
        self.assertIn("--allow-writable-login", err)
        self.assertNotIn("ask for a login that can only read", err)
        code, _, err, _ = self.check(Result(0, b"kind,name\r\nrole,db_owner\r\n"))
        self.assertIn("ask for a login that can only read", err)

    def test_a_check_whose_output_has_no_header_is_no_verdict(self):
        # Exit 0 reads as read-only, so output that is not the check's rows must never get it: not none at all (the
        # tool ran and printed nothing), and not a first row that is no header.
        for name, data, says in (("nothing", b"", "it printed nothing"), ("blank lines", b"\r\n\r\n", "it printed nothing"),
                                 ("rows with no header", b"role,db_datawriter\r\n", "no kind,name header"),
                                 ("an error shown as output", b"Msg 229, Level 14\r\n", "no kind,name header")):
            with self.subTest(name):
                code, _, err, _ = self.check(Result(0, data))
                self.assertEqual(code, 1, err)
                self.assertIn(says, err)
                self.assertNotIn("read-only", err)
                self.assertNotIn("this login can write (", err)

    def test_a_check_takes_out_and_name_as_the_readme_says(self):
        transport = self.use(transport=FakeTransport(Result(0, b"kind,name\r\n")))
        code, _, err = self.main("check", "--cx", "Sales", "--out", "scripts-out", "--name", "mycheck")
        self.assertEqual(code, 0, err)
        self.assertEqual(Path(transport.calls[0]["name"]), Path("mycheck"))
        self.assertEqual(Path(transport.calls[0]["out"]).name, "scripts-out")

    def test_a_check_that_fails_or_times_out(self):
        code, _, err, _ = self.check(Result(1, b"", b"boom\r\n"))
        self.assertEqual(code, 1)
        self.assertIn("boom", err)
        code, _, err, _ = self.check(Result(-1, timed_out=True))
        self.assertEqual(code, 6)
        self.assertIn(TIMED_OUT, err)


@dataclass(frozen=True)
class Leaky(Connection):
    """A connection record carrying a field it must never have; the listing must not pass it on."""
    password: str = "FAKE-pw-7f3a9c2e"


class Connections(Harness):
    def test_the_listing_names_every_connection_and_flags_the_unnamed(self):
        self.use()
        code, out, err = self.main("connections", encoding="cp1252")
        self.assertEqual(code, 0, err)
        text = out.decode("utf-8")
        for said in ("Sales", "aad", "sales-sql.database.windows.net / Product", "reader@example.com",
                     "Web-Prod", "SQL-PROD-01.example.local", "web_reader", "Dev", "windows", "localhost / Scratch",
                     "(no name)", "1 connection has no name", self.registry.source):
            self.assertIn(said, text)
        self.assertEqual(err, b"")

    def test_json_holds_the_six_fields_and_nothing_else(self):
        self.use(registry=FakeRegistry(Leaky("Sales", "s", "d", "aad", "u", True), UNNAMED, DEV))
        code, out, err = self.main("connections", "--json")
        self.assertEqual(code, 0, err)
        listing = json.loads(out)
        self.assertEqual(listing["source"], self.registry.source)
        self.assertEqual(listing["connections"], [
            {"name": "Sales", "server": "s", "database": "d", "auth": "aad", "user": "u", "production": True},
            {"name": None, "server": UNNAMED.server, "database": None, "auth": "sql", "user": "web_reader",
             "production": True},
            {"name": "Dev", "server": "localhost", "database": "Scratch", "auth": "windows", "user": None,
             "production": False}])
        self.assertTrue(out.isascii())
        code, text, _ = self.main("connections")
        self.assertNotIn(b"FAKE-pw", out + err + text)

    def test_an_empty_list_says_so(self):
        self.use(registry=FakeRegistry())
        code, out, _ = self.main("connections")
        self.assertEqual(code, 0)
        self.assertIn(b"no saved connections", out)

    def test_a_backends_message_is_printed_with_control_characters_shown(self):
        self.use(registry=FakeRegistry(fail=NotFound('no connection is named "x\x1b[2J\r"\nthe named ones are a, b')))
        code, _, err = self.main("connections")
        self.assertEqual(code, 3)
        self.assertEqual(err, b'sql-read: no connection is named "x\\x1b[2J\\x0d"\\x0athe named ones are a, b\n')

    def test_a_name_with_a_line_break_cannot_add_a_line_of_its_own(self):
        # The registry quotes every name when none matches: a line break in one must not start a line on stderr that
        # reads as read.py's own summary.
        forged = Connection("Old\nsql-read: Sales (localhost / Scratch): 0 rows in 0.2 s", "srv", None, "sql", None,
                            False)
        for command in ("query", "check", "plan"):
            with self.subTest(command):
                self.use(registry=FakeRegistry(SALES, forged))
                sql = [] if command == "check" else ["--sql", self.sql_file("SELECT 1 AS x")]
                code, _, err = self.main(command, "--cx", "Sale", *sql)
                self.assertEqual(code, 3)
                self.assertEqual(err.count(b"\n"), 1, err)
                self.assertIn(b"Old\\x0asql-read: Sales (localhost / Scratch): 0 rows in 0.2 s", err)

    def test_a_plans_error_is_printed_with_control_characters_shown(self):
        for command in ("query", "plan"):
            with self.subTest(command):
                self.use(registry=FakeRegistry(fail=NotFound("no connection list at x\x1b[2J")))
                code, _, err = self.main(command, "--cx", "Sales", "--sql", self.sql_file("SELECT 1 AS x"))
                self.assertEqual(code, 3)
                self.assertIn(b"sql-read: no connection list at x\\x1b[2J\n", err)
                self.assertNotIn(b"\x1b", err)

    def test_a_list_that_cannot_be_read_exits_3(self):
        self.use(registry=FakeRegistry(fail=NotFound("no connection list at /x.xml: set --connections or "
                                                     "SQL_READ_CONNECTIONS")))
        code, out, err = self.main("connections")
        self.assertEqual((code, out), (3, b""))
        self.assertEqual(err, b"sql-read: no connection list at /x.xml: set --connections or SQL_READ_CONNECTIONS\n")


class InAProcess(unittest.TestCase):
    """read.py in a process of its own, from a staged copy with stand-in backends, on a cp1252 console."""

    def setUp(self):
        import tempfile
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.dir = Path(temp.name)
        self.scripts = stage(self.dir)
        self.listing = self.dir / "connections.json"
        self.listing.write_text(json.dumps([
            {"name": "Sales", "server": "s", "database": "d", "auth": "aad", "user": "u", "production": True},
            {"name": "Dev", "server": "localhost", "database": None, "auth": "windows", "user": None,
             "production": False}]), encoding="utf-8")
        self.tool = self.dir / "LPRun9.json"
        self.sql = self.dir / "work" / "q.sql"
        self.sql.parent.mkdir()
        self.text = "SELECT TOP 2 id, name\r\nFROM dbo.People -- caf\u00e9\r\n"
        self.sql.write_bytes(self.text.encode("utf-8"))

    def answer(self, stdout=b"", stderr=b"", **fields):
        spec = {"stdout": base64.b64encode(stdout).decode(), "stderr": base64.b64encode(stderr).decode(), **fields}
        self.tool.write_text(json.dumps(spec), encoding="utf-8")

    def read_py(self, *argv, cwd=None):
        # check writes its script to the temp folder: this test's own, so the stand-in's headerless script never lands
        # in the user's, where a real check would then refuse it as a file sql-read did not write.
        temp = str(self.dir / "temp")
        return run_script(self.scripts, *argv, "--connections", self.listing, "--lprun", self.tool, cwd=cwd,
                          env={"PYTHONIOENCODING": "cp1252", "TMPDIR": temp, "TEMP": temp, "TMP": temp})

    def test_a_query_end_to_end(self):
        data = "id,name\r\n1,caf\u00e9\r\n2,\u65e5\u672c\r\n".encode("utf-8")
        self.answer(data)
        proc = self.read_py("query", "--cx", "sales", "--sql", self.sql, "--max-rows", "2")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, data)
        self.assertRegex(proc.stderr.decode("utf-8"),
                         rf"\Asql-read: Sales \(s / d, production\): 2 rows in \d+\.\d s{re.escape(CAP_NOTE)}\r?\n\Z")
        script = self.sql.parent / "sql-read" / "q.linq"
        self.assertEqual(script.read_bytes(), read.batch(self.text, max_rows=2).encode("utf-8"))
        call = json.loads((Path(str(self.tool) + ".calls")).read_text(encoding="utf-8"))
        self.assertEqual(call["argv"][1:], ["-cxname=Sales", "-format=csv", str(script)])
        self.assertEqual(call["timeout"], 60)

    def test_exit_codes_end_to_end(self):
        for fields, code, said in (
                ({"exit": 1, "stderr": b"x\r\nsql-read-guard: this login can write to this database\r\n"}, 5,
                 "so nothing ran"),
                ({"exit": -1, "timed_out": True}, 6, "timed out after 60 s"),
                ({"exit": 3, "stderr": b"boom"}, 1, "lprun9 failed with exit code 3"),
                ({"serves": ["sql", "windows"]}, 3, "aad")):
            with self.subTest(code):
                self.answer(**fields)
                proc = self.read_py("query", "--cx", "Sales", "--sql", self.sql)
                self.assertEqual(proc.returncode, code, proc.stderr)
                self.assertIn(said, proc.stderr.decode("utf-8"))
                self.assertNotIn(b"Traceback", proc.stderr)

    def test_a_missing_tool_end_to_end(self):
        proc = self.read_py("query", "--cx", "Sales", "--sql", self.sql)
        self.assertEqual(proc.returncode, 3, proc.stderr)
        self.assertIn(b"lprun9 was not found", proc.stderr)

    def test_check_end_to_end(self):
        self.answer(b"kind,name\r\nrole,db_owner\r\n")
        proc = self.read_py("check", "--cx", "Dev")
        self.assertEqual(proc.returncode, 5, proc.stderr)
        self.assertIn(b"sql-read: Dev (localhost): this login can write (role db_owner)", proc.stderr)
        self.assertTrue((self.dir / "temp" / "sql-read" / "check.linq").is_file(), "check's script left the test's folder")

    def test_the_plan_end_to_end_is_ascii_json(self):
        self.answer()
        self.sql.write_bytes("SELECT N'\u26a0 \u65e5\u672c' AS x".encode("utf-8"))
        proc = self.read_py("plan", "--cx", "Sales", "--sql", self.sql)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(proc.stdout.isascii())
        plan = json.loads(proc.stdout)
        self.assertEqual((plan["sql"], plan["connection"], plan["exit"]),
                         ("SELECT N'\u26a0 \u65e5\u672c' AS x", "Sales", 0))

    def test_a_relative_sql_against_the_working_folder(self):
        self.answer(rows(1))
        proc = self.read_py("query", "--cx", "Sales", "--sql", "q.sql", cwd=self.sql.parent)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, rows(1))

    def test_help_and_a_bad_command_line_on_a_legacy_console(self):
        proc = run_script(self.scripts, "--help", env={"PYTHONIOENCODING": "cp1252"})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn(b"usage:", proc.stdout)
        proc = run_script(self.scripts, "query", "--cx", "Sales", env={"PYTHONIOENCODING": "cp1252"})
        self.assertEqual(proc.returncode, 2)
        self.assertIn(b"the following arguments are required: --sql", proc.stderr)


if __name__ == "__main__":
    unittest.main()
