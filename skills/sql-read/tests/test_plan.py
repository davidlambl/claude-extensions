"""read.py before anything runs: the batch it builds, the plan it prints and the prompt text made from it, and how it
picks the connection and the transport. Run with: python3 -m unittest discover -s skills/sql-read/tests

No test reaches a real backend. read.load_registry and read.load_transport are patched to fail in every test, then
replaced by a fake registry and a fake transport where a test needs them. The tests that need a process of their own
(what importing read.py does, a backend module that is missing) run a copy of the scripts folder holding read.py and
backend.py alone, with stand-in backend modules where a test wants them. test_run.py borrows the fakes and helpers
defined here. Characters outside ASCII are written as escapes."""
import codecs, dataclasses, io, json, os, shutil, subprocess, sys, tempfile, unittest
from pathlib import Path
from unittest import mock

PLUGIN = Path(__file__).resolve().parents[1]
SCRIPTS = PLUGIN / "skills" / "sql-read" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
import read  # noqa: E402
from backend import (AUTH_KINDS, Connection, NotFound, NotNamed, Registry, Result, ToolMissing, Transport,  # noqa: E402
                     Unusable)

SALES = Connection("Sales", "sales-sql.database.windows.net", "Product", "aad", "reader@example.com", True)
WEB = Connection("Web-Prod", "SQL-PROD-01.example.local", None, "sql", "web_reader", True)
DEV = Connection("Dev", "localhost", "Scratch", "windows", None, False)
UNNAMED = Connection(None, "SQL-PROD-01.example.local", None, "sql", "web_reader", True)
TOOL = "/fake/bin/LPRun9.exe"
FIXTURES = PLUGIN / "tests" / "fixtures"

# The plan's field list. read.PLAN_KEYS adds "command" first, and "exit" and "error" last.
PLAN_FIELDS = ("transport", "connection", "server", "database", "production", "auth", "sql", "guard", "max_rows",
               "timeout", "lock_timeout_ms", "nolock", "allow_writable_login", "format", "artifact", "argv")

PROBE = (
    "IF IS_SRVROLEMEMBER('sysadmin') = 1\n"
    "   OR IS_MEMBER('db_owner') = 1 OR IS_MEMBER('db_datawriter') = 1 OR IS_MEMBER('db_ddladmin') = 1\n"
    "   OR EXISTS (SELECT 1 FROM sys.fn_my_permissions(NULL, 'DATABASE') WHERE permission_name IN\n"
    "      ('INSERT','UPDATE','DELETE','ALTER','CONTROL','CREATE TABLE','CREATE PROCEDURE','CREATE FUNCTION',\n"
    "       'CREATE VIEW','ALTER ANY SCHEMA','TAKE OWNERSHIP','ALTER ANY USER','ALTER ANY ROLE'))\n"
    "  THROW 50001, 'sql-read-guard: this login can write to this database', 1;\n")
# What comes before the user's SQL by default, exactly as the plan lays it out.
PRELUDE = "SET NOCOUNT ON;\nSET LOCK_TIMEOUT 5000;\n" + PROBE + "SET ROWCOUNT 200;\n"


class FakeRegistry:
    """Stands in for registries/linqpad.py: resolves by display name, trimmed and compared case-insensitively."""

    def __init__(self, *items, source="/fake/LINQPad/ConnectionsV2.xml", fail=None):
        self.items, self.source, self.fail = list(items), source, fail
        self.resolved = []

    def connections(self):
        if self.fail:
            raise self.fail
        return list(self.items)

    def resolve(self, name):
        if self.fail:
            raise self.fail
        self.resolved.append(name)
        key = name.strip().casefold()
        found = [c for c in self.items if c.name and c.name.strip().casefold() == key]
        if len(found) == 1:
            return found[0]
        if any(c.name is None and c.server.casefold() == key for c in self.items):
            raise NotNamed(f"the connection to {name.strip()} has no name: rename it in LINQPad to use it")
        names = ", ".join(c.name for c in self.items if c.name)
        raise NotFound(f'no connection is named "{name.strip()}"; the named ones are {names}')


class FakeTransport:
    """Stands in for transports/lprun9.py: records every execute() and answers with the Result it was given."""

    name = "lprun9"

    def __init__(self, result=None, serves=AUTH_KINDS, missing=False, raises=None):
        self.result = result if result is not None else Result(0, b"", b"")
        self.serves, self.missing, self.raises = frozenset(serves), missing, raises
        self.calls = []

    def locate(self):
        if self.missing:
            raise ToolMissing("lprun9 was not found: looked at --lprun, env LPRUN9, PATH and C:/Program Files/LINQPad9")
        return TOOL

    def artifact(self, out, name):
        return Path(out) / f"{name}.linq"

    def argv(self, cx, fmt, artifact):
        return [self.locate(), f"-cxname={cx.name}", f"-format={fmt}", str(artifact)]

    def execute(self, cx, batch, fmt, timeout, out, name):
        self.calls.append({"cx": cx, "batch": batch, "fmt": fmt, "timeout": timeout, "out": out, "name": name})
        if self.raises:
            raise self.raises
        return self.result


class Harness(unittest.TestCase):
    """Runs read.main() in this process against fakes, with stdout and stderr as a console of a given encoding."""

    def setUp(self):
        # A test that reaches a real backend fails here instead of reading a connection list or starting lprun9.
        for name in ("load_registry", "load_transport"):
            patcher = mock.patch.object(read, name, side_effect=AssertionError(f"read.{name} reached a real backend"))
            patcher.start()
            self.addCleanup(patcher.stop)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.dir = Path(temp.name)
        self.registry = self.transport = None

    def use(self, registry=None, transport=None):
        """Puts a fake registry and transport behind read.py, recording what each loader was called with."""
        self.registry = registry or FakeRegistry(SALES, WEB, DEV, UNNAMED)
        self.transport = transport or FakeTransport()
        self.loaded = {"registry": [], "transport": []}

        def load_registry(path):
            self.loaded["registry"].append(path)
            return self.registry

        def load_transport(via, **options):
            self.loaded["transport"].append((via, options))
            return self.transport

        for name, fake in (("load_registry", load_registry), ("load_transport", load_transport)):
            patcher = mock.patch.object(read, name, fake)
            patcher.start()
            self.addCleanup(patcher.stop)
        return self.transport

    def sql_file(self, text, name="q.sql", bom=b"", encoding="utf-8", folder=None):
        folder = folder or self.dir
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / name
        path.write_bytes(bom + text.encode(encoding))
        return path

    def main(self, *argv, encoding="utf-8"):
        """read.main(argv) with stdout and stderr as strict text streams of the given encoding over bytes, as a console
        would be; returns the exit code and the bytes each stream received."""
        out = io.TextIOWrapper(io.BytesIO(), encoding=encoding, errors="strict")
        err = io.TextIOWrapper(io.BytesIO(), encoding=encoding, errors="strict")
        with mock.patch.object(sys, "stdout", out), mock.patch.object(sys, "stderr", err):
            code = read.main([str(a) for a in argv])
        out.flush()
        err.flush()
        return code, out.buffer.getvalue(), err.buffer.getvalue()

    def plan(self, *argv):
        """read.py plan ARGV: the exit code, the plan it printed, and stderr as text."""
        code, out, err = self.main("plan", *argv)
        return code, json.loads(out.decode("ascii")), err.decode("utf-8")


class Types(unittest.TestCase):
    """backend.py, which read.py and every backend import: B2's registry and transport are written against it."""

    def test_a_connection_has_six_fields_and_none_for_a_secret(self):
        self.assertEqual([f.name for f in dataclasses.fields(Connection)],
                         ["name", "server", "database", "auth", "user", "production"])
        with self.assertRaises(dataclasses.FrozenInstanceError):
            SALES.name = "other"
        self.assertEqual(AUTH_KINDS, {"sql", "windows", "aad"})

    def test_a_result_carries_the_tools_bytes(self):
        self.assertEqual([f.name for f in dataclasses.fields(Result)],
                         ["exit", "stdout", "stderr", "timed_out", "artifact"])
        self.assertEqual(Result(0), Result(0, b"", b"", False, None))

    def test_every_error_that_stops_a_run_is_unusable(self):
        for error in (NotFound, NotNamed, ToolMissing):
            self.assertTrue(issubclass(error, Unusable), error)

    def test_the_fakes_hold_to_the_protocols(self):
        self.assertIsInstance(FakeRegistry(), Registry)
        self.assertIsInstance(FakeTransport(), Transport)


class Batch(unittest.TestCase):
    def test_the_prelude_in_the_plans_order_and_values(self):
        self.assertEqual(read.batch("SELECT 1 AS x"), PRELUDE + "SELECT 1 AS x")

    def test_nolock_reads_uncommitted_right_after_the_lock_timeout(self):
        self.assertEqual(read.batch("SELECT 1 AS x", nolock=True),
                         "SET NOCOUNT ON;\nSET LOCK_TIMEOUT 5000;\n"
                         "SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;\n" + PROBE + "SET ROWCOUNT 200;\nSELECT 1 AS x")

    def test_max_rows_0_drops_the_row_cap_and_nothing_else(self):
        self.assertEqual(read.batch("SELECT 1 AS x", max_rows=0),
                         "SET NOCOUNT ON;\nSET LOCK_TIMEOUT 5000;\n" + PROBE + "SELECT 1 AS x")

    def test_allow_writable_login_drops_the_probe_and_nothing_else(self):
        self.assertEqual(read.batch("SELECT 1 AS x", allow_writable_login=True),
                         "SET NOCOUNT ON;\nSET LOCK_TIMEOUT 5000;\nSET ROWCOUNT 200;\nSELECT 1 AS x")

    def test_the_values_given_are_the_values_sent(self):
        self.assertEqual(read.batch("SELECT 1 AS x", max_rows=7, lock_timeout_ms=250, nolock=True,
                                    allow_writable_login=True),
                         "SET NOCOUNT ON;\nSET LOCK_TIMEOUT 250;\nSET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;\n"
                         "SET ROWCOUNT 7;\nSELECT 1 AS x")

    def test_only_whole_numbers_reach_the_prelude(self):
        for bad in ("5; DROP TABLE x", "1.5", None):
            with self.subTest(bad):
                with self.assertRaises((TypeError, ValueError)):
                    read.batch("SELECT 1 AS x", max_rows=bad)
                with self.assertRaises((TypeError, ValueError)):
                    read.batch("SELECT 1 AS x", lock_timeout_ms=bad)

    def test_the_check_batch_is_the_prelude_without_the_cap_or_the_throw_then_the_check_query(self):
        self.assertEqual(read.check_batch(), "SET NOCOUNT ON;\nSET LOCK_TIMEOUT 5000;\n" + read.CHECK_SQL)
        self.assertNotIn("THROW", read.check_batch())
        self.assertNotIn("ROWCOUNT", read.check_batch())
        self.assertEqual(read.guard(read.CHECK_SQL)[0], True)

    def test_the_probe_and_the_check_look_for_the_same_roles_and_permissions(self):
        for name in ("sysadmin", *read.WRITE_ROLES):
            self.assertIn(f"'{name}'", PROBE)
            self.assertIn(f"'{name}'", read.CHECK_SQL)
        for permission in read.WRITE_PERMISSIONS:
            self.assertIn(f"'{permission}'", PROBE)
            self.assertIn(f"'{permission}'", read.CHECK_SQL)
        self.assertIn(read.GUARD_MARK, PROBE)


class SqlFile(Harness):
    TEXT = ("-- the user's own words\r\nSELECT N'caf\u00e9 \u65e5\u672c' AS name,\t'two  spaces  ' AS s\r\n"
            "FROM dbo.Orders  \r\nWHERE 1 = 1")

    def test_the_users_sql_reaches_the_transport_byte_for_byte(self):
        for label, bom, encoding in (("UTF-8", b"", "utf-8"), ("UTF-8 with a byte order mark", codecs.BOM_UTF8, "utf-8"),
                                     ("UTF-16 LE with a byte order mark", codecs.BOM_UTF16_LE, "utf-16-le"),
                                     ("UTF-16 BE with a byte order mark", codecs.BOM_UTF16_BE, "utf-16-be")):
            with self.subTest(label):
                transport = self.use()
                sql = self.sql_file(self.TEXT, bom=bom, encoding=encoding)
                code, _, err = self.main("query", "--cx", "Sales", "--sql", sql)
                self.assertEqual(code, 0, err)
                self.assertEqual(transport.calls[-1]["batch"], PRELUDE + self.TEXT)

    def test_a_file_that_is_not_text_is_a_command_line_error(self):
        self.use()
        sql = self.dir / "q.sql"
        sql.write_bytes(b"SELECT '\xff\xfe\xfd' AS x")
        code, _, err = self.main("query", "--cx", "Sales", "--sql", sql)
        self.assertEqual(code, 2)
        self.assertIn(b"not UTF-8", err)
        self.assertEqual(self.transport.calls, [])

    def test_a_missing_sql_file_is_a_command_line_error_that_names_it(self):
        self.use()
        missing = self.dir / "nowhere.sql"
        code, _, err = self.main("query", "--cx", "Sales", "--sql", missing)
        self.assertEqual(code, 2)
        self.assertIn(str(missing).encode(), err)
        self.assertIn(b"does not exist", err)
        code, plan, _ = self.plan("--cx", "Sales", "--sql", missing)
        self.assertEqual((code, plan["exit"]), (2, 2))
        self.assertIn("does not exist", plan["error"])

    def test_a_folder_given_as_the_sql_file_is_a_command_line_error(self):
        self.use()
        code, _, err = self.main("query", "--cx", "Sales", "--sql", self.dir)
        self.assertEqual(code, 2, err)
        self.assertIn(b"is not a regular file", err)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "a named pipe is a POSIX matter")
    def test_a_pipe_given_as_the_sql_file_is_refused_not_read(self):
        # Reading a pipe nobody writes to waits for ever; a hook calling plan_for_command has seconds.
        self.use()
        pipe = self.dir / "q.sql"
        os.mkfifo(pipe)
        plan = read.plan_for_command(["query", "--cx", "Sales", "--sql", str(pipe)])
        self.assertEqual(plan["exit"], 2)
        self.assertIn("is not a regular file", plan["error"])

    def test_a_file_far_over_64_kb_is_refused_by_the_guard_unread(self):
        self.use()
        sql = self.sql_file("SELECT 1 AS x -- " + "a" * (5 * read.MAX_SQL_BYTES))
        code, plan, _ = self.plan("--cx", "Sales", "--sql", sql)
        self.assertEqual((code, plan["guard"]["ok"]), (4, False))
        self.assertIn("64 KB", plan["error"])
        self.assertIsNone(plan["sql"])


class PlanJson(Harness):
    def test_plan_prints_every_field_and_runs_nothing(self):
        transport = self.use()
        sql = self.sql_file("SELECT TOP 20 * FROM dbo.Orders ORDER BY Created DESC")
        code, plan, err = self.plan("--cx", " sales ", "--sql", sql)
        self.assertEqual(code, 0, err)
        self.assertEqual(list(plan), list(read.PLAN_KEYS))
        self.assertEqual([f for f in PLAN_FIELDS if f not in plan], [])
        artifact = sql.parent / "sql-read" / "q.linq"
        self.assertEqual(plan, {
            "command": "query", "transport": "lprun9", "connection": "Sales", "server": SALES.server,
            "database": "Product", "production": True, "auth": "aad",
            "sql": "SELECT TOP 20 * FROM dbo.Orders ORDER BY Created DESC",
            "guard": {"ok": True, "reason": read.guard("SELECT 1")[1]}, "max_rows": 200, "timeout": 60,
            "lock_timeout_ms": 5000, "nolock": False, "allow_writable_login": False, "format": "csv",
            "artifact": str(artifact), "argv": [TOOL, "-cxname=Sales", "-format=csv", str(artifact)],
            "exit": 0, "error": None})
        self.assertEqual(transport.calls, [])
        self.assertFalse(artifact.parent.exists(), "the plan wrote the run's script")
        self.assertEqual(err, "")

    def test_the_options_given_are_the_plans_values(self):
        self.use()
        sql = self.sql_file("SELECT 1 AS x")
        out = self.dir / "runs"
        code, plan, err = self.plan("--cx", "Dev", "--sql", sql, "--max-rows", "50", "--format", "text",
                                    "--timeout", "1.5", "--lock-timeout", "250", "--nolock", "--allow-writable-login",
                                    "--out", out, "--name", "run-1.a")
        self.assertEqual(code, 0, err)
        self.assertEqual((plan["max_rows"], plan["format"], plan["timeout"], plan["lock_timeout_ms"], plan["nolock"],
                          plan["allow_writable_login"]), (50, "text", 1.5, 250, True, True))
        self.assertEqual(plan["artifact"], str(out / "run-1.a.linq"))
        self.assertEqual(plan["argv"], [TOOL, "-cxname=Dev", "-format=text", str(out / "run-1.a.linq")])
        self.assertEqual((plan["production"], plan["auth"]), (False, "windows"))

    def test_plan_without_sql_is_the_plan_of_a_check(self):
        self.use()
        with mock.patch.dict(os.environ, {"TMPDIR": str(self.dir), "TEMP": str(self.dir), "TMP": str(self.dir)}):
            code, plan, err = self.plan("--cx", "Web-Prod")
        self.assertEqual(code, 0, err)
        artifact = self.dir / "sql-read" / "check.linq"
        self.assertEqual((plan["command"], plan["sql"], plan["guard"]["ok"]), ("check", read.CHECK_SQL, True))
        self.assertEqual((plan["max_rows"], plan["format"], plan["lock_timeout_ms"], plan["nolock"],
                          plan["allow_writable_login"], plan["timeout"]), (None, "csv", 5000, False, False, 60))
        self.assertEqual(plan["artifact"], str(artifact))
        self.assertEqual(plan["argv"], [TOOL, "-cxname=Web-Prod", "-format=csv", str(artifact)])
        self.assertEqual(plan["database"], None)

    def test_plan_exits_4_when_the_guard_refuses_and_still_shows_where_it_would_run(self):
        transport = self.use()
        sql = self.sql_file("UPDATE dbo.Orders SET Status = 'x'")
        code, plan, err = self.plan("--cx", "Sales", "--sql", sql)
        self.assertEqual((code, plan["exit"]), (4, 4))
        self.assertEqual(plan["guard"]["ok"], False)
        self.assertTrue(plan["error"].startswith("the guard refused the SQL: "), plan["error"])
        self.assertIn("UPDATE", plan["error"])
        self.assertEqual((plan["connection"], plan["server"], plan["production"]), ("Sales", SALES.server, True))
        self.assertIsNotNone(plan["argv"])
        self.assertIn("sql-read: the guard refused the SQL", err)
        self.assertEqual(transport.calls, [])

    def test_the_plan_holds_the_sql_only_once_the_guard_passes_it(self):
        # A refused file can be anything, a list of secrets included: none of its text reaches the plan, the prompt or
        # a message, and the guard's reason alone says why.
        self.use()
        sql = self.sql_file("-- FAKE-tok-7f3a9c2e\nUPDATE dbo.Orders SET Status = 'x'")
        code, plan, err = self.plan("--cx", "Sales", "--sql", sql)
        self.assertEqual((code, plan["sql"], plan["guard"]["ok"]), (4, None, False))
        reason = read.reason(read.plan_for_command(["query", "--cx", "Sales", "--sql", str(sql)]))
        self.assertNotIn("FAKE-tok", json.dumps(plan) + err + reason)

    def test_a_linq_script_or_the_connection_list_given_as_the_sql_shows_none_of_it(self):
        transport = self.use()
        linq = self.sql_file('<Query Kind="SQL">\n  <Connection>\n    <Server>sql-prod-01</Server>\n'
                             "    <Password>sp_FAKE-pw-7f3a9c2e</Password>\n  </Connection>\n</Query>\n\n"
                             "SELECT 1 AS x\n", name="q.linq")
        for path in (FIXTURES / "connections.xml", linq):
            for command in ("plan", "query"):
                with self.subTest(path=path.name, command=command):
                    code, out, err = self.main(command, "--cx", "Sales", "--sql", path)
                    self.assertEqual(code, 4, err)
                    self.assertNotIn(b"FAKE", out + err)
                    self.assertNotIn(b"Password", out + err)
            with self.subTest(path=path.name, command="the prompt"):
                reason = read.reason(read.plan_for_command(["query", "--cx", "Sales", "--sql", str(path)]))
                self.assertIn("the SQL starts with <", reason)
                self.assertNotIn("FAKE", reason)
        self.assertEqual(transport.calls, [])

    def test_plan_exits_3_when_the_connection_cannot_be_used(self):
        cases = (("an unknown name", FakeRegistry(SALES), "Nope", 'no connection is named "Nope"'),
                 ("an unnamed connection", FakeRegistry(SALES, UNNAMED), "SQL-PROD-01.example.local", "no name"),
                 ("a list that cannot be read",
                  FakeRegistry(fail=NotFound("no connection list at /x.xml: set --connections or SQL_READ_CONNECTIONS")),
                  "Sales", "SQL_READ_CONNECTIONS"))
        for label, registry, cx, said in cases:
            with self.subTest(label):
                self.use(registry=registry)
                sql = self.sql_file("SELECT 1 AS x")
                code, plan, err = self.plan("--cx", cx, "--sql", sql)
                self.assertEqual((code, plan["exit"]), (3, 3))
                self.assertIn(said, plan["error"])
                self.assertIn(said, err)
                self.assertEqual((plan["connection"], plan["server"], plan["argv"]), (cx, None, None))
                self.assertEqual(plan["guard"]["ok"], True)

    def test_plan_exits_3_when_the_tool_is_missing_and_still_names_the_script(self):
        self.use(transport=FakeTransport(missing=True))
        sql = self.sql_file("SELECT 1 AS x")
        code, plan, _ = self.plan("--cx", "Sales", "--sql", sql)
        self.assertEqual((code, plan["exit"], plan["argv"]), (3, 3, None))
        self.assertIn("lprun9 was not found", plan["error"])
        self.assertEqual(plan["artifact"], str(sql.parent / "sql-read" / "q.linq"))

    def test_plan_exits_3_when_the_transport_does_not_serve_the_connections_auth_kind(self):
        self.use(transport=FakeTransport(serves={"sql", "windows"}))
        sql = self.sql_file("SELECT 1 AS x")
        code, plan, _ = self.plan("--cx", "Sales", "--sql", sql)
        self.assertEqual((code, plan["argv"]), (3, None))
        for said in ("lprun9", "aad", "Sales", "sql, windows"):
            self.assertIn(said, plan["error"])

    def test_plan_exits_3_when_a_backend_module_cannot_be_loaded(self):
        self.use()
        sql = self.sql_file("SELECT 1 AS x")
        with mock.patch.object(read, "load_transport", side_effect=Unusable("the lprun9 transport could not be loaded")):
            code, plan, _ = self.plan("--cx", "Sales", "--sql", sql)
        self.assertEqual((code, plan["artifact"], plan["argv"]), (3, None, None))
        self.assertIn("could not be loaded", plan["error"])

    def test_the_guard_decides_the_exit_code_before_the_connection_does(self):
        self.use(registry=FakeRegistry(SALES))
        sql = self.sql_file("DELETE FROM dbo.Orders")
        code, plan, _ = self.plan("--cx", "Nope", "--sql", sql)
        self.assertEqual(code, 4)
        self.assertIn("DELETE", plan["error"])

    def test_the_plan_is_ascii_json_whatever_the_sql_holds(self):
        self.use()
        sql = self.sql_file("SELECT N'\u26a0 \u65e5\u672c \u00e9' AS x")
        code, out, err = self.main("plan", "--cx", "Sales", "--sql", sql, encoding="cp1252")
        self.assertEqual(code, 0, err)
        self.assertTrue(out.isascii())
        self.assertEqual(json.loads(out)["sql"], "SELECT N'\u26a0 \u65e5\u672c \u00e9' AS x")


class CommandLine(Harness):
    def test_bad_command_lines_exit_2_with_the_usage(self):
        self.use()
        sql = str(self.sql_file("SELECT 1 AS x"))
        query = ["query", "--cx", "Sales", "--sql", sql]
        for argv in ([], ["bogus"], ["plan"], ["query", "--cx", "Sales"], ["check"], ["check", "--cx", "Sales", "--sql", sql],
                     query + ["--max-rows", "-1"], query + ["--max-rows", "1.5"], query + ["--max-rows", "2147483648"],
                     query + ["--max-rows", "ten"], query + ["--timeout", "0"], query + ["--timeout", "nan"],
                     query + ["--timeout", "inf"], query + ["--timeout", "-1"], query + ["--timeout", "86401"],
                     query + ["--lock-timeout", "-1"], query + ["--lock-timeout", "1.5"], query + ["--format", "json"],
                     query + ["--via", "sqlcmd"], query + ["--allow"], query + ["--max", "5"], query + ["--bogus"],
                     query + ["--name", "../x"], query + ["--name", "a b"], query + ["--name", "con"],
                     query + ["--name", "LPT1.x"], query + ["--name", "x."], query + ["--name", "-x"],
                     query + ["--name", "a" * 101], ["query", "--cx", "  ", "--sql", sql], ["connections", "--cx", "x"]):
            with self.subTest(argv):
                code, out, err = self.main(*argv)
                self.assertEqual(code, 2, err)
                self.assertEqual(out, b"")
                self.assertIn(b"usage:", err)
                self.assertIn(b"error:", err)
        self.assertEqual(self.transport.calls, [])

    def test_help_prints_the_usage_and_exits_0(self):
        for argv in (["--help"], ["query", "--help"], ["-h"]):
            with self.subTest(argv):
                code, out, _ = self.main(*argv, encoding="cp1252")
                self.assertEqual(code, 0)
                self.assertIn(b"usage:", out)
        code, out, _ = self.main("--help")
        for said in (b"read.py connections", b"read.py plan", b"read.py check", b"read.py query", b"Exit codes",
                     b"SQL_READ_CONNECTIONS", b"LPRUN9", b"default 200", b"csv (the default)"):
            self.assertIn(said, out)

    def test_the_defaults_for_the_scripts_folder_and_name(self):
        self.use()
        for stem, name in (("q", "q"), ("my report (v2)", "my_report_v2"), ("con", "q_con"), ("nul.data", "q_nul.data"),
                           ("\u65e5\u672c", "query"), ("a.b", "a.b")):
            with self.subTest(stem):
                sql = self.sql_file("SELECT 1 AS x", name=f"{stem}.sql", folder=self.dir / "in")
                if sql.name not in {p.name for p in sql.parent.iterdir()}:
                    continue  # Windows before 11 takes a name such as con.sql for a device, and makes no file
                code, plan, err = self.plan("--cx", "Sales", "--sql", sql)
                self.assertEqual(code, 0, err)
                self.assertEqual(plan["artifact"], str(self.dir / "in" / "sql-read" / f"{name}.linq"))

    def test_relative_paths_resolve_against_cwd_and_a_bare_tool_name_is_left_for_path(self):
        self.use()
        self.sql_file("SELECT 1 AS x", folder=self.dir / "work")
        cwd = str(self.dir)
        base = ["query", "--cx", "Sales", "--sql", "work/q.sql", "--connections", "lists/c.xml", "--out", "runs"]
        plan = read.plan_for_command(base + ["--lprun", "tools/LPRun9.exe"], cwd=cwd)
        self.assertEqual(plan["exit"], 0, plan["error"])
        self.assertEqual(plan["sql"], "SELECT 1 AS x")
        self.assertEqual(plan["artifact"], str(self.dir / "runs" / "q.linq"))
        self.assertEqual(self.loaded["registry"], [str(self.dir / "lists" / "c.xml")])
        self.assertEqual(self.loaded["transport"], [("lprun9", {"lprun": str(self.dir / "tools" / "LPRun9.exe")})])
        read.plan_for_command(base + ["--lprun", "LPRun9.exe"], cwd=cwd)
        self.assertEqual(self.loaded["transport"][-1], ("lprun9", {"lprun": "LPRun9.exe"}))
        read.plan_for_command(base, cwd=cwd)
        self.assertEqual(self.loaded["transport"][-1], ("lprun9", {"lprun": None}))
        read.plan_for_command(["query", "--cx", "Sales", "--sql", str(self.dir / "work" / "q.sql")], cwd=cwd)
        self.assertEqual(self.loaded["registry"][-1], None)

    @unittest.skipUnless(os.name == "nt", "a Git Bash path such as /c/x is a Windows matter")
    def test_a_git_bash_drive_path_means_the_drive(self):
        self.use()
        sql = self.sql_file("SELECT 1 AS x")
        drive, rest = os.path.splitdrive(str(sql))
        bash = "/" + drive[0].lower() + rest.replace("\\", "/")
        plan = read.plan_for_command(["query", "--cx", "Sales", "--sql", bash], cwd=str(self.dir))
        self.assertEqual((plan["exit"], plan["sql"]), (0, "SELECT 1 AS x"))


class Wiring(Harness):
    def test_a_fake_registry_and_transport_pass_through_the_generic_layer(self):
        transport = self.use()
        sql = self.sql_file("SELECT TOP 5 * FROM dbo.Orders")
        code, _, err = self.main("query", "--cx", "web-prod", "--sql", sql, "--format", "csvi", "--timeout", "9",
                                 "--connections", self.dir / "c.xml", "--lprun", self.dir / "LPRun9.exe")
        self.assertEqual(code, 0, err)
        self.assertEqual(len(transport.calls), 1)
        call = transport.calls[0]
        self.assertEqual((call["cx"], call["fmt"], call["timeout"], call["out"], call["name"]),
                         (WEB, "csvi", 9, sql.parent / "sql-read", "q"))
        self.assertEqual(call["batch"], PRELUDE + "SELECT TOP 5 * FROM dbo.Orders")
        self.assertEqual(self.registry.resolved, ["web-prod"])
        self.assertEqual(self.loaded["registry"], [str(self.dir / "c.xml")])
        self.assertEqual(self.loaded["transport"], [("lprun9", {"lprun": str(self.dir / "LPRun9.exe")})])

    def test_check_sends_the_check_batch(self):
        transport = self.use(transport=FakeTransport(Result(0, b"kind,name\r\n")))
        with mock.patch.dict(os.environ, {"TMPDIR": str(self.dir), "TEMP": str(self.dir), "TMP": str(self.dir)}):
            code, _, err = self.main("check", "--cx", "Sales", "--timeout", "30")
        self.assertEqual(code, 0, err)
        call = transport.calls[0]
        self.assertEqual((call["batch"], call["fmt"], call["timeout"], call["out"], call["name"]),
                         (read.check_batch(), "csv", 30, self.dir / "sql-read", "check"))

    def test_a_transport_that_cannot_serve_the_auth_kind_exits_3_before_anything_runs(self):
        for argv in (["check", "--cx", "Sales"], ["query", "--cx", "Sales", "--sql", None]):
            with self.subTest(argv[0]):
                transport = self.use(transport=FakeTransport(serves={"sql"}))
                argv = [a if a is not None else self.sql_file("SELECT 1 AS x") for a in argv]
                code, out, err = self.main(*argv)
                self.assertEqual(code, 3)
                self.assertEqual(out, b"")
                self.assertIn(b"aad", err)
                self.assertEqual(transport.calls, [])

    def test_a_missing_tool_exits_3_before_anything_runs(self):
        transport = self.use(transport=FakeTransport(missing=True))
        code, _, err = self.main("query", "--cx", "Sales", "--sql", self.sql_file("SELECT 1 AS x"))
        self.assertEqual(code, 3)
        self.assertIn(b"lprun9 was not found", err)
        self.assertEqual(transport.calls, [])

    def test_an_unknown_or_unnamed_connection_exits_3_before_anything_runs(self):
        for cx in ("Nope", "SQL-PROD-01.example.local"):
            with self.subTest(cx):
                transport = self.use()
                code, _, err = self.main("query", "--cx", cx, "--sql", self.sql_file("SELECT 1 AS x"))
                self.assertEqual(code, 3, err)
                self.assertTrue(err.startswith(b"sql-read: "), err)
                self.assertEqual(transport.calls, [])

    def test_a_registry_that_resolves_to_a_connection_with_no_name_exits_3(self):
        class Careless(FakeRegistry):
            def resolve(self, name):
                return UNNAMED

        transport = self.use(registry=Careless(UNNAMED))
        code, _, err = self.main("query", "--cx", "SQL-PROD-01.example.local", "--sql", self.sql_file("SELECT 1 AS x"))
        self.assertEqual(code, 3, err)
        self.assertIn(b"no display name", err)
        self.assertEqual(transport.calls, [])

    def test_a_refused_sql_exits_4_without_loading_a_backend(self):
        # setUp left both loaders failing, so reaching either would end the run with exit 1, not 4.
        sql = self.sql_file("SELECT 1 AS x; DROP TABLE dbo.Orders")
        code, out, err = self.main("query", "--cx", "Sales", "--sql", sql)
        self.assertEqual(code, 4, err)
        self.assertEqual(out, b"")
        self.assertIn(b"DROP", err)
        read.load_registry.assert_not_called()
        read.load_transport.assert_not_called()


class PlanForCommand(Harness):
    def test_it_returns_what_read_py_plan_prints(self):
        self.use()
        sql = str(self.sql_file("SELECT TOP 3 * FROM dbo.Orders"))
        bad = str(self.sql_file("UPDATE dbo.Orders SET x = 1", name="bad.sql"))
        with mock.patch.dict(os.environ, {"TMPDIR": str(self.dir), "TEMP": str(self.dir), "TMP": str(self.dir)}):
            for options in (["--cx", "Sales", "--sql", sql], ["--cx", "Dev", "--sql", sql, "--max-rows", "0", "--nolock"],
                            ["--cx", "Sales", "--sql", bad], ["--cx", "Nope", "--sql", sql], ["--cx", "Sales"]):
                with self.subTest(options):
                    _, printed, _ = self.plan(*options)
                    self.assertEqual(read.plan_for_command(["plan", *options]), printed)
                    command = "query" if "--sql" in options else "check"
                    if command == "check":
                        self.assertEqual(read.plan_for_command(["check", *options]), printed)
                    else:
                        self.assertEqual(read.plan_for_command(["query", *options]), printed)

    def test_a_relative_sql_resolves_against_cwd_not_this_process(self):
        self.use()
        self.sql_file("SELECT 2 AS two", folder=self.dir / "scratch")
        plan = read.plan_for_command(["query", "--cx", "Sales", "--sql", "scratch/q.sql"], cwd=str(self.dir))
        self.assertEqual((plan["exit"], plan["sql"]), (0, "SELECT 2 AS two"))
        elsewhere = read.plan_for_command(["query", "--cx", "Sales", "--sql", "scratch/q.sql"], cwd=str(self.dir / "x"))
        self.assertEqual(elsewhere["exit"], 2)
        self.assertIn(str(self.dir / "x" / "scratch" / "q.sql"), elsewhere["error"])

    def test_a_bad_command_line_gives_exit_2_and_an_error(self):
        self.use()
        for argv in ([], ["query"], ["query", "--cx", "Sales", "--sql"], ["plan", "--max-rows", "x"], ["bogus"]):
            with self.subTest(argv):
                plan = read.plan_for_command(argv)
                self.assertEqual(plan["exit"], 2)
                self.assertIsInstance(plan["error"], str)
                self.assertTrue(plan["error"])
                self.assertEqual(list(plan), list(read.PLAN_KEYS))

    def test_it_never_raises_and_always_carries_exit(self):
        self.use()
        sql = str(self.sql_file("SELECT 1 AS x"))
        for argv in (None, 42, "query", [None], [b"query"], [["query"]]):
            with self.subTest(argv):
                plan = read.plan_for_command(argv)
                self.assertIn(plan["exit"], (1, 2))
                self.assertTrue(plan["error"])
        for failure in (RuntimeError("the XML is broken"), SystemExit(9), KeyError("x")):
            with self.subTest(failure):
                with mock.patch.object(read, "load_registry", side_effect=failure):
                    plan = read.plan_for_command(["query", "--cx", "Sales", "--sql", sql])
                self.assertEqual(plan["exit"], 1)
                self.assertIn(type(failure).__name__, plan["error"])

    def test_it_runs_nothing(self):
        transport = self.use()
        sql = str(self.sql_file("SELECT 1 AS x"))
        for argv in (["query", "--cx", "Sales", "--sql", sql], ["check", "--cx", "Sales"], ["plan", "--cx", "Sales"]):
            self.assertEqual(read.plan_for_command(argv)["exit"], 0)
        self.assertEqual(transport.calls, [])
        self.assertFalse((self.dir / "sql-read").exists())

    def test_help_and_connections_run_nothing_either(self):
        self.use()
        plan = read.plan_for_command(["query", "--help"])
        self.assertEqual((plan["exit"], plan["help"]), (0, True))
        self.assertIn("runs nothing", read.reason(plan))
        plan = read.plan_for_command(["connections"])
        self.assertEqual((plan["exit"], plan["command"]), (0, "connections"))
        self.assertIn("touches no database", read.reason(plan))


class Reason(Harness):
    def setUp(self):
        super().setUp()
        self.use()

    def reason(self, sql="SELECT TOP 20 *\nFROM dbo.Orders", *options, cx="Sales"):
        path = self.sql_file(sql)
        return read.reason(read.plan_for_command(["query", "--cx", cx, "--sql", str(path), *options])).split("\n")

    def test_the_first_lines_name_the_connection_the_limits_and_the_sql(self):
        self.assertEqual(self.reason(), [
            'sql-read: read on "Sales" \u2014 sales-sql.database.windows.net / Product \u2014 PRODUCTION',
            "up to 200 rows \u00b7 60 s timeout \u00b7 lock timeout 5 s",
            "| SELECT TOP 20 *",
            "| FROM dbo.Orders"])

    def test_without_a_database_or_production(self):
        self.assertEqual(self.reason(cx="Web-Prod")[0],
                         'sql-read: read on "Web-Prod" \u2014 SQL-PROD-01.example.local \u2014 PRODUCTION')
        self.assertEqual(self.reason(cx="Dev")[0], 'sql-read: read on "Dev" \u2014 localhost / Scratch')

    def test_the_limits_given(self):
        self.assertEqual(self.reason("SELECT 1 AS x", "--max-rows", "1", "--timeout", "2.5", "--lock-timeout", "1500")[1],
                         "up to 1 row \u00b7 2.5 s timeout \u00b7 lock timeout 1.5 s")

    def test_the_sql_s_first_eight_lines_then_a_count_of_the_rest(self):
        sql = "\n\n" + "\n".join(f"-- line {n}" for n in range(1, 13)) + "\nSELECT 1 AS x\n\n"
        lines = self.reason(sql)
        self.assertEqual(lines[2:], [f"| -- line {n}" for n in range(1, 9)] + ["\u2026(5 more lines)"])
        self.assertEqual(self.reason("SELECT 1 AS x\r\nFROM a\r\n")[2:], ["| SELECT 1 AS x", "| FROM a"])
        self.assertEqual(self.reason("SELECT 1 AS x\n\nFROM a")[2:], ["| SELECT 1 AS x", "|", "| FROM a"])
        self.assertEqual(self.reason("\n".join(["SELECT 1 AS x"] + ["-- c"] * 8))[-1], "\u2026(1 more line)")

    def test_at_most_600_characters_of_sql(self):
        long = "SELECT 1 AS x -- " + "a" * 1000
        lines = self.reason(long + "\nFROM b")
        self.assertEqual(lines[2], "| " + long[:600] +"\u2026")
        self.assertEqual(lines[3], "\u2026(1 more line)")
        lines = self.reason("SELECT 1 AS x -- " + "b" * 283 + "\n-- " + "c" * 400 + "\n-- d")
        self.assertEqual(sum(len(line[2:].rstrip("\u2026")) for line in lines[2:4]), 600)
        self.assertEqual(lines[4], "\u2026(1 more line)")

    def test_each_line_of_sql_is_marked_so_none_passes_for_a_line_of_the_prompts_own(self):
        # A string may span lines. This one holds the prompt's own first two lines, for another connection.
        head, limits = self.reason(cx="Dev")[0], self.reason()[1]
        forged = f"SELECT TOP (5) name, 'n/a\n{head}\n{limits}\n' AS note\n\nFROM sys.tables;"
        self.assertTrue(read.guard(forged)[0])
        lines = self.reason(forged)
        self.assertEqual(lines[2:], ["| SELECT TOP (5) name, 'n/a", f"| {head}", f"| {limits}", "| ' AS note", "|",
                                     "| FROM sys.tables;"])
        self.assertEqual([n for n, line in enumerate(lines) if line in (head, limits)], [1])

    def test_the_warnings_come_last(self):
        lines = self.reason("SELECT 1 AS x", "--allow-writable-login", "--max-rows", "0", "--nolock")
        self.assertEqual(lines[1], "no row cap \u00b7 60 s timeout \u00b7 lock timeout 5 s")
        self.assertEqual(lines[-3:], ["\u26a0 writable login allowed", "\u26a0 no row cap", "NOLOCK (dirty reads)"])
        self.assertEqual(len(self.reason("SELECT 1 AS x")), 3)

    def test_a_check(self):
        lines = read.reason(read.plan_for_command(["check", "--cx", "Sales"])).split("\n")
        self.assertEqual(lines, [
            'sql-read: login check on "Sales" \u2014 sales-sql.database.windows.net / Product \u2014 PRODUCTION',
            "lists what this login can write \u00b7 60 s timeout \u00b7 lock timeout 5 s"])

    def test_a_plan_that_will_not_run_says_why_first(self):
        lines = self.reason("UPDATE dbo.Orders SET x = 1")
        self.assertTrue(lines[0].startswith("sql-read: will not run (exit 4): the guard refused the SQL: UPDATE"),
                        lines[0])
        self.assertEqual(lines[1], 'read on "Sales" \u2014 sales-sql.database.windows.net / Product \u2014 PRODUCTION')
        self.assertEqual(len(lines), 3, "a refused SQL is not shown: the guard's reason says why")
        lines = self.reason(cx="Nope")
        self.assertTrue(lines[0].startswith('sql-read: will not run (exit 3): no connection is named "Nope"'), lines[0])
        self.assertEqual(lines[1], 'read on "Nope"')
        self.assertEqual(lines[3:], ["| SELECT TOP 20 *", "| FROM dbo.Orders"])
        text = read.reason(read.plan_for_command(["query", "--cx", "Sales"]))
        self.assertTrue(text.startswith("sql-read: the command line is not valid, so nothing will run: "), text)
        self.assertIn("--sql", text)
        with mock.patch.object(read, "load_registry", side_effect=RuntimeError("boom")):
            text = self.reason()[0]
        self.assertTrue(text.startswith("sql-read: the plan could not be built"), text)

    def test_control_and_invisible_characters_are_shown_not_sent_to_the_terminal(self):
        lines = self.reason("SELECT 'a\x1b[2Jb\u202ec\u2028d' AS x")
        self.assertEqual(lines[2], "| SELECT 'a\\x1b[2Jb\\u202ec\\u2028d' AS x")
        self.use(registry=FakeRegistry(Connection("Evil\n\x1b[2J", "srv\r", "db\x07", "sql", None, True)))
        first = read.reason(read.plan_for_command(["check", "--cx", "Evil\n\x1b[2J"])).split("\n")[0]
        self.assertEqual(first, 'sql-read: login check on "Evil\\x0a\\x1b[2J" \u2014 srv\\x0d / db\\x07 \u2014 PRODUCTION')

    def test_it_never_raises(self):
        for plan in (None, {}, [], {"command": "query", "exit": 0}, {"command": "query", "exit": 0, "max_rows": "x",
                                                                       "timeout": None, "sql": 5}):
            with self.subTest(plan):
                text = read.reason(plan)
                self.assertTrue(text.startswith("sql-read: "), text)


# Stand-in backend modules for a copy of the scripts folder. The registry's --connections file is JSON: a list of
# connections' fields. The transport's --lprun file is JSON saying what the run gives back; it is the "tool", so a
# --lprun that does not exist is a missing tool. Each run writes the batch where lprun9's script would go, and appends
# what it ran to the spec's .calls neighbour.
FAKE_REGISTRY = '''\
import json
from pathlib import Path
from backend import Connection, NotFound, NotNamed


class Registry:
    def __init__(self, path):
        self.source = str(path)

    def connections(self):
        try:
            return [Connection(**c) for c in json.loads(Path(self.source).read_text(encoding="utf-8"))]
        except OSError:
            raise NotFound(f"no connection list at {self.source}: point --connections or SQL_READ_CONNECTIONS at one")

    def resolve(self, name):
        key = name.strip().casefold()
        found = [c for c in self.connections() if c.name and c.name.strip().casefold() == key]
        if len(found) == 1:
            return found[0]
        if any(c.name is None and c.server.casefold() == key for c in self.connections()):
            raise NotNamed(f"the connection to {name.strip()} has no name: rename it in LINQPad")
        raise NotFound(f'no connection is named "{name.strip()}"')


def registry(path=None):
    return Registry(path or "no-list-given.json")
'''

FAKE_TRANSPORT = '''\
import base64, json
from pathlib import Path
from backend import AUTH_KINDS, Result, ToolMissing


class Lprun9:
    name = "lprun9"

    def __init__(self, lprun):
        self.lprun = lprun
        self.serves = frozenset(self.spec().get("serves", AUTH_KINDS)) if self.found() else AUTH_KINDS

    def found(self):
        return bool(self.lprun) and Path(self.lprun).is_file()

    def spec(self):
        return json.loads(Path(self.lprun).read_text(encoding="utf-8"))

    def locate(self):
        if not self.found():
            raise ToolMissing(f"lprun9 was not found at {self.lprun}")
        return str(Path(self.lprun).resolve())

    def artifact(self, out, name):
        return Path(out) / f"{name}.linq"

    def argv(self, cx, fmt, artifact):
        return [self.locate(), f"-cxname={cx.name}", f"-format={fmt}", str(artifact)]

    def execute(self, cx, batch, fmt, timeout, out, name):
        artifact = self.artifact(out, name)
        argv = self.argv(cx, fmt, artifact)
        artifact.parent.mkdir(parents=True, exist_ok=True)
        with open(artifact, "w", encoding="utf-8", newline="") as f:
            f.write(batch)
        with open(self.lprun + ".calls", "a", encoding="utf-8") as calls:
            calls.write(json.dumps({"argv": argv, "timeout": timeout}) + "\\n")
        spec = self.spec()
        return Result(spec.get("exit", 0), base64.b64decode(spec.get("stdout", "")),
                      base64.b64decode(spec.get("stderr", "")), spec.get("timed_out", False), artifact)


def transport(lprun=None):
    return Lprun9(lprun)
'''


def stage(root, backends=True):
    """A copy of the scripts folder under root holding read.py and backend.py, with the stand-in backend modules when
    backends is true; returns the copy's path."""
    scripts = Path(root) / "scripts"
    scripts.mkdir(parents=True)
    for name in ("read.py", "backend.py"):
        shutil.copy2(SCRIPTS / name, scripts / name)
    if backends:
        for package, module, source in (("registries", "linqpad", FAKE_REGISTRY), ("transports", "lprun9", FAKE_TRANSPORT)):
            (scripts / package).mkdir()
            (scripts / package / "__init__.py").write_text("", encoding="utf-8")
            (scripts / package / f"{module}.py").write_text(source, encoding="utf-8")
    return scripts


def run_script(scripts, *argv, cwd=None, env=None):
    """python3 read.py ARGV from a staged copy, in a process of its own: stdout and stderr come back as bytes."""
    clean = {k: v for k, v in os.environ.items()
             if k not in ("SQL_READ_CONNECTIONS", "LPRUN9", "PYTHONIOENCODING", "PYTHONUTF8")}
    return subprocess.run([sys.executable, str(Path(scripts) / "read.py"), *[str(a) for a in argv]],
                          capture_output=True, timeout=120, cwd=cwd, env={**clean, **(env or {})})


IMPORT_PROBE = '''\
import builtins, io, json, os, subprocess, sys
calls = []


def record(name, real):
    def stand_in(*args, **kwargs):
        calls.append(name)
        return real(*args, **kwargs)
    return stand_in


builtins.open = record("open", builtins.open)
io.open = record("io.open", io.open)
os.open = record("os.open", os.open)
os.system = record("os.system", os.system)
os.popen = record("os.popen", os.popen)
subprocess.Popen.__init__ = record("subprocess.Popen", subprocess.Popen.__init__)
sys.path.insert(0, sys.argv[1])
import read
loaded = sorted(m for m in sys.modules if m.split(".")[0] in ("registries", "transports"))
sys.stdout.write(json.dumps({"calls": calls, "backends": loaded, "plan": callable(read.plan_for_command)}))
'''


class Process(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.dir = Path(temp.name)

    def test_importing_read_starts_no_process_opens_no_file_and_loads_no_backend(self):
        scripts = stage(self.dir)
        probe = self.dir / "probe.py"
        probe.write_text(IMPORT_PROBE, encoding="utf-8")
        proc = subprocess.run([sys.executable, str(probe), str(scripts)], capture_output=True, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout), {"calls": [], "backends": [], "plan": True})

    def test_a_missing_backend_module_exits_3_with_a_clear_message(self):
        scripts = stage(self.dir, backends=False)
        sql = self.dir / "q.sql"
        sql.write_text("SELECT 1 AS x", encoding="utf-8")
        for argv, module in ((["query", "--cx", "Sales", "--sql", sql], "registries/linqpad.py"),
                             (["connections"], "registries/linqpad.py")):
            with self.subTest(argv[0]):
                proc = run_script(scripts, *argv)
                self.assertEqual(proc.returncode, 3, proc.stderr)
                err = proc.stderr.decode("utf-8")
                self.assertIn(module, err)
                self.assertIn("could not be loaded", err)
                self.assertNotIn("Traceback", err)

    def test_a_missing_transport_module_exits_3_and_the_plan_says_so(self):
        scripts = stage(self.dir)
        shutil.rmtree(scripts / "transports")
        listing = self.dir / "c.json"
        listing.write_text(json.dumps([{"name": "Sales", "server": "s", "database": "d", "auth": "aad", "user": None,
                                        "production": True}]), encoding="utf-8")
        sql = self.dir / "q.sql"
        sql.write_text("SELECT 1 AS x", encoding="utf-8")
        proc = run_script(scripts, "plan", "--cx", "Sales", "--sql", sql, "--connections", listing)
        self.assertEqual(proc.returncode, 3, proc.stderr)
        plan = json.loads(proc.stdout)
        self.assertIn("transports/lprun9.py", plan["error"])
        self.assertEqual(plan["server"], "s")

    def test_the_guard_needs_no_backend(self):
        scripts = stage(self.dir, backends=False)
        sql = self.dir / "q.sql"
        sql.write_text("DELETE FROM dbo.Orders", encoding="utf-8")
        proc = run_script(scripts, "query", "--cx", "Sales", "--sql", sql)
        self.assertEqual(proc.returncode, 4, proc.stderr)
        self.assertIn(b"DELETE", proc.stderr)


if __name__ == "__main__":
    unittest.main()
