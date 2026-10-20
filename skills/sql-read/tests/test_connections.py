"""The LINQPad registry, registries/linqpad.py: LINQPad's ConnectionsV2.xml read into connections, and what read.py
connections, connections --json and --cx make of them. Run with: python3 -m unittest discover -s skills/sql-read/tests

No test reads LINQPad's own list. Each one points APPDATA at a temporary folder and clears SQL_READ_CONNECTIONS, then
names the fixture (fixtures/connections.xml) or a list it writes, through --connections or SQL_READ_CONNECTIONS. Every
password in them is FAKE-pw-7f3a9c2e, and no output may hold it. --cx is tried through read.py plan and query against
test_plan's fake transport, so nothing looks for lprun9 or runs it. Characters outside ASCII are written as escapes."""
import codecs, io, json, os, re, subprocess, sys, tempfile, threading, unittest
from pathlib import Path
from unittest import mock

from test_plan import SCRIPTS, FakeTransport, read
from backend import Connection, NotFound, NotNamed, Registry, Unusable
from registries import linqpad

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "connections.xml"
SECRET = "FAKE-pw-7f3a9c2e"
SALES = Connection("Sales", "sales-sql.database.windows.net", "Product", "aad", "reader@example.com", True)
UNNAMED = Connection(None, "SQL-PROD-01.example.local", None, "sql", "web_reader", True)
WEB = Connection("Web-Prod", "SQL-PROD-01.example.local", "Web", "sql", "web_reader", True)
DEV = Connection("Dev", "localhost", "Scratch", "windows", None, False)
# What the fixture holds besides the settings a connection is read from. None of it may reach a connection, a
# message or the output: the password, a custom connection string, a setting this module does not know, and the
# settings a driver keeps inside DriverData under the same names as a connection's own.
UNREAD = (SECRET, "left unread", "driver-setting.example.local", "Driver setting", "00000000-0000-4000-8000")


def settings(**values):
    """One <Connection> holding the settings given, in that order; a value of None leaves the setting out."""
    return "<Connection>" + "".join(f"<{k}>{v}</{k}>" for k, v in values.items() if v is not None) + "</Connection>"


class Case(unittest.TestCase):
    """A temporary folder that APPDATA points into, so LINQPad's own list is out of reach; SQL_READ_CONNECTIONS and
    LPRUN9 cleared; and read.py's transport replaced by test_plan's fake."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.dir = Path(temp.name)
        environ = {k: v for k, v in os.environ.items() if k not in ("SQL_READ_CONNECTIONS", "LPRUN9")}
        environ["APPDATA"] = str(self.dir / "AppData" / "Roaming")
        patcher = mock.patch.dict(os.environ, environ, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.transport = FakeTransport()
        patcher = mock.patch.object(read, "load_transport", lambda via, **options: self.transport)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.lists = 0

    def write(self, *connections, head='<?xml version="1.0" encoding="utf-8"?>', root="Connections", encoding="utf-8",
              bom=b""):
        """A connection list holding the <Connection> elements given; returns its path."""
        self.lists += 1
        path = self.dir / f"list{self.lists}.xml"
        path.write_bytes(bom + f"{head}<{root}>{''.join(connections)}</{root}>".encode(encoding))
        return path

    def read(self, *connections, **options):
        return linqpad.registry(str(self.write(*connections, **options))).connections()

    def refusal(self, path, error=NotFound):
        """The message the registry at path refuses with, from connections() and from resolve() alike."""
        with self.assertRaises(error) as listing:
            linqpad.registry(str(path)).connections()
        with self.assertRaises(error) as resolving:
            linqpad.registry(str(path)).resolve("Sales")
        self.assertEqual(str(listing.exception), str(resolving.exception))
        return str(listing.exception)

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

    def plan(self, *argv, connections=FIXTURE):
        """read.py plan --connections LIST ARGV: the exit code, the plan it printed, and stderr as text."""
        code, out, err = self.main("plan", "--connections", connections, *argv)
        return code, json.loads(out.decode("ascii")), err.decode("utf-8")

    def assertNothingUnread(self, *outputs):
        for output in outputs:
            text = output.decode("utf-8", "replace") if isinstance(output, bytes) else str(output)
            for unread in UNREAD:
                self.assertNotIn(unread, text)


class Reading(Case):
    def test_the_fixture_reads_as_its_four_connections_in_order(self):
        registry = linqpad.registry(str(FIXTURE))
        self.assertIsInstance(registry, Registry)
        self.assertEqual(registry.source, str(FIXTURE))
        self.assertEqual(registry.connections(), [SALES, UNNAMED, WEB, DEV])

    def test_the_auth_kind_comes_from_sqlsecurity_and_universalauthentication(self):
        # Universal authentication is Azure AD whatever SqlSecurity says, since it signs in with a user name too.
        for sql, universal, kind in ((None, None, "windows"), ("false", "false", "windows"), ("true", None, "sql"),
                                     ("true", "false", "sql"), (None, "true", "aad"), ("false", "true", "aad"),
                                     ("true", "true", "aad"), ("True", None, "sql"), (" true ", None, "sql"),
                                     ("1", None, "sql"), ("0", "1", "aad"), ("yes", None, "windows")):
            with self.subTest(SqlSecurity=sql, UniversalAuthentication=universal):
                [cx] = self.read(settings(Server="s", SqlSecurity=sql, DisplayName="A",
                                          UniversalAuthentication=universal))
                self.assertEqual(cx.auth, kind)

    def test_the_production_flag(self):
        for value, production in (("true", True), ("True", True), ("1", True), ("false", False), ("", False),
                                  (None, False)):
            with self.subTest(IsProduction=value):
                [cx] = self.read(settings(Server="s", DisplayName="A", IsProduction=value))
                self.assertIs(cx.production, production)

    def test_a_connection_with_no_display_name_is_listed_unnamed(self):
        for value in (None, "", "   ", "\t\r\n"):
            with self.subTest(DisplayName=value):
                [cx] = self.read(settings(Server="s", DisplayName=value))
                self.assertIsNone(cx.name)

    def test_a_name_is_kept_as_saved_and_the_other_settings_are_trimmed(self):
        # lprun9's -cxname gets the name exactly as LINQPad holds it; the rest is only shown.
        [cx] = self.read(settings(Server="  host\n", DisplayName=" Sales ", Database=" db ", UserName=" u ",
                                  SqlSecurity="true"))
        self.assertEqual(cx, Connection(" Sales ", "host", "db", "sql", "u", False))

    def test_an_empty_database_or_user_name_is_none(self):
        [cx] = self.read(settings(Server="s", DisplayName="A", Database="", UserName="  "))
        self.assertEqual((cx.database, cx.user), (None, None))

    def test_a_connection_with_no_server_has_an_empty_one(self):
        [cx] = self.read(settings(DisplayName="A"))
        self.assertEqual(cx.server, "")

    def test_only_a_connections_own_settings_are_read(self):
        # The fixture's Dev keeps a Server, a DisplayName and a Password inside DriverData: none of them is Dev's.
        self.assertEqual(linqpad.registry(str(FIXTURE)).connections()[3], DEV)
        nested = settings(Server="s", DisplayName="A",
                          DriverData="<Connection><Server>inner</Server><DisplayName>Inner</DisplayName></Connection>")
        self.assertEqual([c.name for c in self.read(nested)], ["A"])
        self.assertEqual([c.server for c in self.read(settings(Server="s<b>x</b>t", DisplayName="A"))], ["st"])
        # A driver's setting does not stand in for one the connection lacks.
        [cx] = self.read(settings(DriverData="<Server>inner</Server><DisplayName>Inner</DisplayName>"))
        self.assertEqual((cx.name, cx.server), (None, ""))

    def test_a_setting_saved_twice_counts_once_as_first_saved(self):
        twice = "<Connection><DisplayName>A</DisplayName><Server>s</Server><DisplayName>B</DisplayName></Connection>"
        self.assertEqual([c.name for c in self.read(twice)], ["A"])

    def test_text_outside_ascii_in_any_encoding_the_file_declares(self):
        name = "Ventes-R\u00e9el \u65e5\u672c"
        for label, head, encoding, bom in (
                ("UTF-8", '<?xml version="1.0" encoding="utf-8"?>', "utf-8", b""),
                ("UTF-8 with a byte order mark", '<?xml version="1.0" encoding="utf-8"?>', "utf-8", codecs.BOM_UTF8),
                ("UTF-16 with a byte order mark", '<?xml version="1.0" encoding="utf-16"?>', "utf-16-le",
                 codecs.BOM_UTF16_LE),
                ("no declaration", "", "utf-8", b"")):
            with self.subTest(label):
                [cx] = self.read(settings(Server="s", DisplayName=name), head=head, encoding=encoding, bom=bom)
                self.assertEqual(cx.name, name)
        [cx] = self.read(settings(Server="s", DisplayName="Ventes-R&#233;el &amp; co"))
        self.assertEqual(cx.name, "Ventes-R\u00e9el & co")

    def test_an_empty_list(self):
        self.assertEqual(self.read(), [])

    def test_the_list_is_read_once(self):
        registry = linqpad.registry(str(FIXTURE))
        with mock.patch("builtins.open", wraps=open) as opened:
            registry.connections()
            registry.resolve("Sales")
            registry.connections()
        self.assertEqual(opened.call_count, 1)

    def test_no_password_is_kept_anywhere_in_the_registry(self):
        registry = linqpad.registry(str(FIXTURE))
        listed = registry.connections()
        registry.resolve("Sales")
        self.assertNothingUnread(repr(vars(registry)), repr(listed))

    def test_the_parser_hands_on_the_seven_settings_and_no_other_text(self):
        self.assertEqual(linqpad.SETTINGS, {"DisplayName", "Server", "Database", "UniversalAuthentication",
                                            "SqlSecurity", "UserName", "IsProduction"})
        records = linqpad._parse(str(FIXTURE), None)
        self.assertEqual(len(records), 4)
        self.assertTrue(all(set(record) <= linqpad.SETTINGS for record in records), records)
        self.assertNothingUnread(repr(records))


class Default(Case):
    def test_making_a_registry_reads_nothing(self):
        with mock.patch("builtins.open", side_effect=AssertionError("opened")), \
                mock.patch("os.stat", side_effect=AssertionError("looked at")):
            registry = linqpad.registry(str(self.dir / "nowhere.xml"))
            linqpad.registry()
        self.assertEqual(registry.source, str(self.dir / "nowhere.xml"))

    def test_the_default_is_linqpads_own_list_under_appdata(self):
        expected = os.path.join(os.environ["APPDATA"], "LINQPad", "ConnectionsV2.xml")
        self.assertEqual(linqpad.registry().source, expected)
        self.assertEqual(linqpad.registry(None).source, expected)

    def test_without_appdata_the_default_is_under_the_home_folder(self):
        del os.environ["APPDATA"]
        expected = os.path.join(os.path.expanduser("~"), "AppData", "Roaming", "LINQPad", "ConnectionsV2.xml")
        self.assertEqual(linqpad.registry().source, os.path.abspath(expected))

    def test_sql_read_connections_overrides_the_default(self):
        os.environ["SQL_READ_CONNECTIONS"] = str(FIXTURE)
        self.assertEqual(linqpad.registry().source, str(FIXTURE))
        self.assertEqual(linqpad.registry().connections(), [SALES, UNNAMED, WEB, DEV])
        os.environ["SQL_READ_CONNECTIONS"] = "   "
        self.assertEqual(linqpad.registry().source,
                         os.path.join(os.environ["APPDATA"], "LINQPad", "ConnectionsV2.xml"))

    def test_spaces_and_quotes_around_sql_read_connections_are_dropped(self):
        for value in (f"  {FIXTURE}  ", f'"{FIXTURE}"', f' " {FIXTURE} " '):
            with self.subTest(value=value):
                os.environ["SQL_READ_CONNECTIONS"] = value
                self.assertEqual(linqpad.registry().source, str(FIXTURE))
        os.environ["SQL_READ_CONNECTIONS"] = '""'
        self.assertEqual(linqpad.registry().source, os.path.join(os.environ["APPDATA"], "LINQPad", "ConnectionsV2.xml"))

    def test_a_relative_path_is_made_absolute(self):
        os.environ["SQL_READ_CONNECTIONS"] = os.path.join("lists", "c.xml")
        self.assertEqual(linqpad.registry().source, os.path.abspath(os.path.join("lists", "c.xml")))
        self.assertEqual(linqpad.registry("c.xml").source, os.path.abspath("c.xml"))

    def test_a_path_given_wins_over_sql_read_connections(self):
        os.environ["SQL_READ_CONNECTIONS"] = str(self.dir / "nowhere.xml")
        self.assertEqual(linqpad.registry(str(FIXTURE)).connections(), [SALES, UNNAMED, WEB, DEV])


class Unreadable(Case):
    def test_a_missing_list_names_the_path_and_how_to_point_at_another(self):
        default = self.dir / "AppData" / "Roaming" / "LINQPad" / "ConnectionsV2.xml"
        from_env, given = self.dir / "from-env.xml", self.dir / "given.xml"
        for label, env, path, missing in (("the default", None, None, default),
                                          ("SQL_READ_CONNECTIONS", str(from_env), None, from_env),
                                          ("--connections", str(from_env), str(given), given)):
            with self.subTest(label):
                os.environ.pop("SQL_READ_CONNECTIONS", None)
                if env:
                    os.environ["SQL_READ_CONNECTIONS"] = env
                with self.assertRaises(NotFound) as caught:
                    linqpad.registry(path).connections()
                message = str(caught.exception)
                for said in (str(missing), "--connections", "SQL_READ_CONNECTIONS"):
                    self.assertIn(said, message)

    def test_a_folder_is_refused(self):
        self.assertIn("not a file", self.refusal(self.dir))

    @unittest.skipUnless(hasattr(os, "mkfifo"), "a named pipe is a POSIX matter")
    def test_a_pipe_is_refused_not_read(self):
        # Reading a pipe nobody writes to waits for ever, and a hook's plan has seconds. A registry that tried would
        # hang the suite, so it is asked in a thread that the test stops waiting for after ten seconds.
        pipe = self.dir / "c.xml"
        os.mkfifo(pipe)
        caught = []

        def ask():
            try:
                linqpad.registry(str(pipe)).connections()
            except Exception as e:  # checked below, whatever it is
                caught.append(e)

        worker = threading.Thread(target=ask, daemon=True)
        worker.start()
        worker.join(10)
        self.assertFalse(worker.is_alive(), "the registry is waiting to read the pipe")
        [error] = caught
        self.assertIsInstance(error, NotFound)
        self.assertIn("not a file", str(error))

    def test_a_file_too_large_to_be_a_connection_list_is_refused_unread(self):
        path = self.write(settings(Server="s", DisplayName="A"))
        with mock.patch.object(linqpad, "MAX_BYTES", 10), \
                mock.patch("builtins.open", side_effect=AssertionError("the file was read")):
            message = self.refusal(path)
        self.assertIn(str(path), message)
        self.assertIn("too large", message)

    def test_a_declared_encoding_python_does_not_know_is_refused_not_a_crash(self):
        path = self.dir / "enc.xml"
        path.write_bytes(f'<?xml version="1.0" encoding="bogus-{SECRET}"?><Connections/>'.encode("ascii"))
        message = self.refusal(path)
        self.assertIn(str(path), message)
        self.assertIn("encoding Python does not know", message)
        self.assertNotIn(SECRET, message)
        code, _, err = self.main("connections", "--connections", str(path))
        self.assertEqual(code, 3, err)

    def test_xml_that_does_not_parse_is_refused_with_the_place_and_no_content(self):
        for label, text in (("cut short", f"<Connections><Connection><Password>{SECRET}</Password>"),
                            ("an entity it does not know", f"<Connections><Connection><Password>{SECRET}&bogus;"
                                                            "</Password></Connection></Connections>"),
                            ("mismatched", f"<Connections><Password>{SECRET}</Connection></Connections>"),
                            ("not XML", SECRET)):
            with self.subTest(label):
                path = self.dir / "bad.xml"
                path.write_text(text, encoding="utf-8")
                message = self.refusal(path)
                self.assertIn(str(path), message)
                self.assertRegex(message, r"line \d+, column \d+")
                self.assertNotIn(SECRET, message)

    def test_a_doctype_is_refused_before_any_entity_is_expanded(self):
        head = ('<?xml version="1.0"?><!DOCTYPE Connections [<!ENTITY a "aaaaaaaaaa">'
                '<!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">]>')
        path = self.write(settings(Server="s", DisplayName="&b;"), head=head)
        self.assertIn("DOCTYPE", self.refusal(path))

    def test_a_file_that_is_not_a_connection_list_is_refused(self):
        path = self.write(settings(Server="s", DisplayName="A"), root="Queries")
        message = self.refusal(path)
        self.assertIn("<Connections>", message)
        self.assertIn(str(path), message)

    def test_every_refusal_is_unusable_so_read_py_exits_3(self):
        self.assertTrue(issubclass(NotFound, Unusable) and issubclass(NotNamed, Unusable))


class Resolving(Case):
    def setUp(self):
        super().setUp()
        self.registry = linqpad.registry(str(FIXTURE))

    def test_by_display_name_trimmed_and_ignoring_case(self):
        for typed, cx in (("Sales", SALES), ("sales", SALES), ("  SALES  ", SALES), ("\tweb-PROD\n", WEB),
                          ("dev", DEV)):
            with self.subTest(typed=typed):
                self.assertEqual(self.registry.resolve(typed), cx)

    def test_ignoring_case_outside_ascii_too(self):
        registry = linqpad.registry(str(self.write(settings(Server="s", DisplayName="\u00c9t\u00e9"))))
        self.assertEqual(registry.resolve("\u00e9T\u00c9").name, "\u00c9t\u00e9")

    def test_a_connection_with_no_name_cannot_be_selected(self):
        # It answers to what LINQPad shows for it, its server, and to what read.py connections shows, "(no name)".
        for typed in ("SQL-PROD-01.example.local", " sql-prod-01.example.local ", "SQL-PROD-01", "(no name)"):
            with self.subTest(typed=typed):
                with self.assertRaises(NotNamed) as caught:
                    self.registry.resolve(typed)
                message = str(caught.exception)
                self.assertIn("SQL-PROD-01.example.local", message)
                self.assertIn("give it a name in LINQPad", message)
                for name in ('"Sales"', '"Web-Prod"', '"Dev"'):
                    self.assertIn(name, message)
                self.assertNothingUnread(message)

    def test_a_display_name_wins_over_what_only_sql_read_knows_an_unnamed_connection_by(self):
        # The host part and "(no name)" are sql-read's own labels; LINQPad shows neither, so lprun9 cannot mix them up.
        registry = linqpad.registry(str(self.write(settings(Server="db1.example.local"),
                                                   settings(Server="db2", DisplayName="DB1"),
                                                   settings(Server="db3", DisplayName="(no name)"))))
        self.assertEqual(registry.resolve("db1").server, "db2")
        self.assertEqual(registry.resolve("(no name)").server, "db3")

    def test_a_display_name_that_linqpad_also_shows_an_unnamed_connection_by_is_refused(self):
        # lprun9's -cxname matches the name LINQPad shows: the server, or server.database, for a connection with none.
        for unnamed, typed in ((settings(Server="db1", SqlSecurity="true", UserName="a"), "DB1"),
                               (settings(Server="db1", Database="Sales", UserName="a"), "db1.sales")):
            with self.subTest(typed=typed):
                registry = linqpad.registry(str(self.write(unnamed, settings(Server="db2", DisplayName=typed))))
                with self.assertRaises(NotFound) as caught:
                    registry.resolve(typed)
                message = str(caught.exception)
                self.assertIn("lprun9 could take either", message)
                self.assertIn("give that one a name in LINQPad", message)
                self.assertIn("db2", message)
                self.assertIn("user a", message)

    def test_two_unnamed_connections_are_both_named_in_the_message(self):
        registry = linqpad.registry(str(self.write(settings(Server="db1", UserName="a"),
                                                   settings(Server="db1", UserName="b"))))
        with self.assertRaises(NotNamed) as caught:
            registry.resolve("db1")
        message = str(caught.exception)
        self.assertIn("give each a name in LINQPad", message)
        self.assertRegex(message, r"user a\b.*user b\b")

    def test_an_unknown_name_lists_the_names_there_are(self):
        with self.assertRaises(NotFound) as caught:
            self.registry.resolve("Nope")
        message = str(caught.exception)
        for said in ('"Nope"', '"Sales"', '"Web-Prod"', '"Dev"', "no name", str(FIXTURE)):
            self.assertIn(said, message)
        self.assertNothingUnread(message)

    def test_a_name_saved_twice_is_ambiguous_and_refused(self):
        registry = linqpad.registry(str(self.write(settings(Server="a", DisplayName="Sales"),
                                                   settings(Server="b", DisplayName=" SALES"))))
        with self.assertRaises(NotFound) as caught:
            registry.resolve("sales")
        self.assertIn("2 connections", str(caught.exception))
        self.assertIn("different name", str(caught.exception))

    def test_a_list_with_no_connections_or_none_named(self):
        with self.assertRaises(NotFound) as caught:
            linqpad.registry(str(self.write())).resolve("Sales")
        self.assertIn("no saved connections", str(caught.exception))
        with self.assertRaises(NotFound) as caught:
            linqpad.registry(str(self.write(settings(Server="s")))).resolve("Sales")
        self.assertIn("none of them has a name", str(caught.exception))

    def test_a_long_list_of_names_is_cut_short(self):
        registry = linqpad.registry(str(self.write(*(settings(Server="s", DisplayName=f"cx{i:03d}")
                                                     for i in range(100)))))
        with self.assertRaises(NotFound) as caught:
            registry.resolve("Nope")
        message = str(caught.exception)
        self.assertIn('"cx000"', message)
        self.assertNotIn('"cx099"', message)
        self.assertIn(f"{100 - linqpad.MAX_NAMES} more", message)
        self.assertIn("read.py connections", message)

    def test_a_blank_name_selects_nothing(self):
        for typed in ("", "   "):
            with self.subTest(typed=typed), self.assertRaises(NotFound) as caught:
                self.registry.resolve(typed)
            self.assertIn("no connection name", str(caught.exception))


class Listing(Case):
    """read.py connections, in this process, against the real registry."""

    JSON = {"source": str(FIXTURE), "connections": [
        {"name": "Sales", "server": "sales-sql.database.windows.net", "database": "Product", "auth": "aad",
         "user": "reader@example.com", "production": True},
        {"name": None, "server": "SQL-PROD-01.example.local", "database": None, "auth": "sql", "user": "web_reader",
         "production": True},
        {"name": "Web-Prod", "server": "SQL-PROD-01.example.local", "database": "Web", "auth": "sql",
         "user": "web_reader", "production": True},
        {"name": "Dev", "server": "localhost", "database": "Scratch", "auth": "windows", "user": None,
         "production": False}]}

    def test_connections_lists_every_connection_and_flags_the_unnamed_one(self):
        code, out, err = self.main("connections", "--connections", FIXTURE)
        self.assertEqual((code, err), (0, b""))
        text = out.decode("utf-8")
        self.assertIn(f"connections in {FIXTURE}:", text)
        for row in (r"Sales\s+aad\s+yes\s+sales-sql\.database\.windows\.net / Product\s+reader@example\.com",
                    r"\(no name\)\s+sql\s+yes\s+SQL-PROD-01\.example\.local\s+web_reader",
                    r"Web-Prod\s+sql\s+yes\s+SQL-PROD-01\.example\.local / Web\s+web_reader",
                    r"Dev\s+windows\s+no\s+localhost / Scratch"):
            self.assertRegex(text, re.compile(rf"^{row}$", re.M))
        self.assertIn("1 connection has no name, so --cx cannot select it", text)
        self.assertNothingUnread(out, err)

    def test_connections_json_prints_six_fields_of_each_and_no_password(self):
        code, out, err = self.main("connections", "--json", "--connections", FIXTURE)
        self.assertEqual((code, err), (0, b""))
        self.assertEqual(json.loads(out.decode("ascii")), self.JSON)
        self.assertNothingUnread(out, err)

    def test_sql_read_connections_names_the_list_when_connections_is_not_given(self):
        os.environ["SQL_READ_CONNECTIONS"] = str(FIXTURE)
        code, out, err = self.main("connections", "--json")
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out), self.JSON)
        os.environ["SQL_READ_CONNECTIONS"] = str(self.dir / "nowhere.xml")
        code, out, err = self.main("connections", "--json", "--connections", FIXTURE)
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out), self.JSON)

    def test_a_missing_list_exits_3_naming_the_path_and_the_env_var(self):
        default = self.dir / "AppData" / "Roaming" / "LINQPad" / "ConnectionsV2.xml"
        for argv, env, missing in ((["connections"], None, default),
                                   (["connections", "--json"], self.dir / "env.xml", self.dir / "env.xml"),
                                   (["connections", "--connections", self.dir / "given.xml"], None,
                                    self.dir / "given.xml")):
            with self.subTest(argv=argv, env=env):
                os.environ.pop("SQL_READ_CONNECTIONS", None)
                if env:
                    os.environ["SQL_READ_CONNECTIONS"] = str(env)
                code, out, err = self.main(*argv)
                self.assertEqual((code, out), (3, b""))
                text = err.decode("utf-8")
                self.assertTrue(text.startswith("sql-read: "), text)
                self.assertIn(str(missing), text)
                self.assertIn("SQL_READ_CONNECTIONS", text)
                self.assertNotIn("Traceback", text)

    def test_names_outside_ascii_reach_a_cp1252_console_as_utf8(self):
        name = "Ventes-R\u00e9el \u65e5\u672c"
        path = self.write(settings(Server="s", DisplayName=name))
        code, out, err = self.main("connections", "--connections", path, encoding="cp1252")
        self.assertEqual(code, 0, err)
        self.assertIn(name.encode("utf-8"), out)
        code, out, err = self.main("connections", "--json", "--connections", path, encoding="cp1252")
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out.decode("ascii"))["connections"][0]["name"], name)


class Selecting(Case):
    """--cx through read.py plan, query and check, against the real registry and test_plan's fake transport."""

    def sql(self, text="SELECT TOP 5 * FROM dbo.Orders ORDER BY 1"):
        path = self.dir / "q.sql"
        path.write_text(text, encoding="utf-8")
        return path

    def argv(self, command, typed):
        sql = ["--sql", self.sql()] if command != "check" else []
        return [command, "--connections", FIXTURE, "--cx", typed, *sql]

    def test_cx_selects_by_display_name_trimmed_and_ignoring_case(self):
        for typed in ("Sales", "sales", "  SALES  "):
            with self.subTest(typed=typed):
                code, plan, err = self.plan("--cx", typed, "--sql", self.sql())
                self.assertEqual(code, 0, err)
                self.assertEqual({k: plan[k] for k in ("connection", "server", "database", "auth", "production")},
                                 {"connection": "Sales", "server": SALES.server, "database": "Product", "auth": "aad",
                                  "production": True})
                self.assertIn("-cxname=Sales", plan["argv"])  # the name as saved, not as typed

    def test_the_auth_kinds_reach_the_plan(self):
        for typed, kind in (("Sales", "aad"), ("Web-Prod", "sql"), ("Dev", "windows")):
            with self.subTest(typed=typed):
                code, plan, err = self.plan("--cx", typed)
                self.assertEqual((code, plan["auth"]), (0, kind), err)

    def test_an_unnamed_connection_cannot_be_selected_and_nothing_runs(self):
        for command in ("plan", "query", "check"):
            with self.subTest(command):
                code, _, err = self.main(*self.argv(command, "SQL-PROD-01.example.local"))
                self.assertEqual(code, 3)
                self.assertIn("give it a name in LINQPad", err.decode("utf-8"))
        self.assertEqual(self.transport.calls, [])

    def test_an_unknown_name_exits_3_listing_the_names_and_nothing_runs(self):
        for command in ("plan", "query", "check"):
            with self.subTest(command):
                code, _, err = self.main(*self.argv(command, "Nope"))
                self.assertEqual(code, 3)
                for said in ('"Nope"', '"Sales"', '"Web-Prod"', '"Dev"'):
                    self.assertIn(said, err.decode("utf-8"))
        self.assertEqual(self.transport.calls, [])

    def test_a_missing_list_exits_3_from_plan_too(self):
        missing = self.dir / "nowhere.xml"
        code, plan, err = self.plan("--cx", "Sales", connections=missing)
        self.assertEqual((code, plan["exit"]), (3, 3))
        self.assertIn(str(missing), plan["error"])
        self.assertIn("SQL_READ_CONNECTIONS", plan["error"])

    def test_no_password_reaches_any_output_or_the_prompt_whatever_is_selected(self):
        for typed in ("Sales", "Web-Prod", "Dev", "SQL-PROD-01.example.local", "Nope"):
            for command in ("plan", "query", "check"):
                with self.subTest(typed=typed, command=command):
                    code, out, err = self.main(*self.argv(command, typed))
                    self.assertNothingUnread(out, err)
                    plan = read.plan_for_command([str(a) for a in self.argv(command, typed)])
                    self.assertNothingUnread(json.dumps(plan), read.reason(plan))


class Process(Case):
    """read.py in a process of its own, from the plugin's scripts folder, loading the real registry."""

    def run_read(self, *argv, env=None):
        clean = {k: v for k, v in os.environ.items() if k not in ("PYTHONIOENCODING", "PYTHONUTF8")}
        return subprocess.run([sys.executable, str(SCRIPTS / "read.py"), *[str(a) for a in argv]],
                              capture_output=True, timeout=120, env={**clean, **(env or {})})

    def test_read_py_lists_the_list_sql_read_connections_names(self):
        proc = self.run_read("connections", "--json", env={"SQL_READ_CONNECTIONS": str(FIXTURE)})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout), Listing.JSON)
        self.assertNothingUnread(proc.stdout, proc.stderr)
        proc = self.run_read("connections", "--connections", FIXTURE)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn(b"(no name)", proc.stdout)
        self.assertNothingUnread(proc.stdout, proc.stderr)

    def test_read_py_names_the_path_it_looked_at_when_the_list_is_missing(self):
        proc = self.run_read("connections")
        self.assertEqual((proc.returncode, proc.stdout), (3, b""))
        err = proc.stderr.decode("utf-8")
        self.assertIn(os.path.join(os.environ["APPDATA"], "LINQPad", "ConnectionsV2.xml"), err)
        self.assertIn("SQL_READ_CONNECTIONS", err)
        self.assertNotIn("Traceback", err)


if __name__ == "__main__":
    unittest.main()
