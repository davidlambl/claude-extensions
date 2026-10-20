"""sql-read end to end with the real lprun9, skipped unless SQL_READ_E2E=1 and lprun9 is found where the transport
looks for it (env LPRUN9, PATH, then where LINQPad 9 installs it on Windows or macOS); the skip says where it looked.
Run with:
SQL_READ_E2E=1 python3 -m unittest discover -s skills/sql-read/tests

plan reads the fixture's fake connections and runs nothing, so it is safe wherever lprun9 is installed. check runs
against a real saved connection, the one SQL_READ_E2E_CX names, and only when that is set. Saved connections are often
production ones: nothing but the user sets SQL_READ_E2E_CX. check sends only the login probe, which reads.

The probe's schema clause (fn_my_permissions asked for each schema by name) answers NULL, which reads as no permission,
for a name the server cannot resolve, so a clause that matched nothing would let every login through without a word.
Two tests are there to notice: the first, under SQL_READ_E2E_CX, asks the function for a schema by its literal name and
again the way the probe does, and the counts must agree; the second, under SQL_READ_E2E_SCHEMA_CX, names a saved
connection whose login holds a schema-scoped write grant (GRANT INSERT ON SCHEMA::dbo to a user in no write role, and
nothing else), which check and query must both refuse."""
import csv, io, json, os, subprocess, sys, tempfile, unittest
from pathlib import Path
from unittest import mock

from test_plan import SCRIPTS, read
from backend import ToolMissing, Unusable
from transports import lprun9

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "connections.xml"
E2E = os.environ.get("SQL_READ_E2E") == "1"
CX = os.environ.get("SQL_READ_E2E_CX", "").strip()
SCHEMA_CX = os.environ.get("SQL_READ_E2E_SCHEMA_CX", "").strip()
CANARY_SCHEMAS = ("dbo", "sys")  # schemas every login can be asked about


def schema_canary():
    """One SELECT that counts, for each of CANARY_SCHEMAS, the rows fn_my_permissions gives for the schema's literal
    name and the rows it gives for the same schema the way the probe asks, from sys.schemas through CROSS APPLY."""
    columns = []
    for name in CANARY_SCHEMAS:
        columns.append(f"(SELECT COUNT(*) FROM sys.fn_my_permissions('{name}', 'SCHEMA')) AS named_{name}")
        columns.append("(SELECT COUNT(*) FROM sys.schemas AS s CROSS APPLY sys.fn_my_permissions(s.name, 'SCHEMA') "
                       f"AS p WHERE s.name = '{name}') AS applied_{name}")
    return "SELECT " + ",\n       ".join(columns)


def find_lprun9():
    """(lprun9's path, None), found where the transport finds it in this environment; or (None, the transport's own
    account of where it looked) when it is in none of those places."""
    try:
        return lprun9.transport().locate(), None
    except Unusable as e:
        return None, str(e)


TOOL, LOOKED = find_lprun9() if E2E else (None, None)
NOT_FOUND = LOOKED or "lprun9 was not found: install LINQPad 9, or set LPRUN9"


def run_read(*argv, timeout=120):
    """python3 read.py ARGV in a process of its own, from the plugin's scripts folder, in this environment."""
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONIOENCODING", "PYTHONUTF8")}
    return subprocess.run([sys.executable, str(SCRIPTS / "read.py"), *[str(a) for a in argv]], capture_output=True,
                          timeout=timeout, env=env)


class Finder(unittest.TestCase):
    """find_lprun9() asks the transport, so these tests run against the lprun9 read.py itself would run, and when it
    finds nothing the skip carries the transport's own account of where it looked."""

    def test_it_keeps_the_transports_answer(self):
        with mock.patch.object(lprun9.Lprun9, "locate", return_value="/opt/lprun9") as locate:
            self.assertEqual(find_lprun9(), ("/opt/lprun9", None))
        locate.assert_called_once_with()

    def test_when_the_transport_finds_nothing_the_reason_says_where_it_looked(self):
        looked = "lprun9 was not found. Looked at --lprun (not given), env LPRUN9 (not set), lprun9 on PATH, /x/lprun9."
        with mock.patch.object(lprun9.Lprun9, "locate", side_effect=ToolMissing(looked)):
            self.assertEqual(find_lprun9(), (None, looked))


class Canary(unittest.TestCase):
    def test_the_schema_canary_passes_the_guard_so_it_can_run_as_a_query(self):
        ok, why = read.guard(schema_canary())
        self.assertTrue(ok, why)


@unittest.skipUnless(E2E, "set SQL_READ_E2E=1 to run against the real lprun9")
@unittest.skipUnless(TOOL, NOT_FOUND)
class Plan(unittest.TestCase):
    def test_plan_against_the_fixture_names_the_real_lprun9_and_runs_nothing(self):
        with tempfile.TemporaryDirectory() as temp:
            sql = Path(temp) / "q.sql"
            sql.write_text("SELECT TOP 5 name FROM sys.objects ORDER BY name", encoding="utf-8")
            proc = run_read("plan", "--cx", "sales", "--sql", sql, "--connections", FIXTURE)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            plan = json.loads(proc.stdout)
            script = str(Path(temp) / "sql-read" / "q.linq")
            self.assertEqual(plan["argv"], [TOOL, "-cxname=Sales", "-format=CSV", script])
            self.assertEqual((plan["connection"], plan["auth"], plan["production"], plan["artifact"]),
                             ("Sales", "aad", True, script))
            self.assertTrue(os.path.isabs(TOOL) and os.path.isfile(TOOL), TOOL)
            self.assertFalse((Path(temp) / "sql-read").exists())

    def test_plan_of_a_check_against_the_unnamed_connection_exits_3(self):
        proc = run_read("plan", "--cx", "SQL-PROD-01.example.local", "--connections", FIXTURE)
        self.assertEqual(proc.returncode, 3, proc.stderr)
        self.assertIn("give it a name in LINQPad", json.loads(proc.stdout)["error"])


@unittest.skipUnless(E2E, "set SQL_READ_E2E=1 to run against the real lprun9")
@unittest.skipUnless(TOOL, NOT_FOUND)
@unittest.skipUnless(CX, "set SQL_READ_E2E_CX to a saved connection's name to run check against it")
class Check(unittest.TestCase):
    def test_check_against_the_connection_sql_read_e2e_cx_names(self):
        proc = run_read("check", "--cx", CX, "--timeout", "60", timeout=180)
        err = proc.stderr.decode("utf-8", "replace")
        self.assertIn(proc.returncode, (0, 5), err)
        last = err.strip().splitlines()[-1]
        self.assertTrue(last.startswith("sql-read: "), last)
        self.assertRegex(last, "read-only|this login can write")

    def test_query_agrees_with_check_and_the_row_cap_holds(self):
        # The batch's probe (its THROW) and check's query look for the same grants, so for one login they must agree:
        # a login check says is read-only runs the query, and one it says can write stops it with exit 5. This is the
        # only test of the probe's own SQL, its THROW and SET ROWCOUNT, against a server.
        check = run_read("check", "--cx", CX, "--timeout", "60", timeout=180)
        self.assertIn(check.returncode, (0, 5), check.stderr.decode("utf-8", "replace"))
        with tempfile.TemporaryDirectory() as temp:
            sql = Path(temp) / "q.sql"
            sql.write_text("SELECT TOP 5 name FROM sys.objects ORDER BY name", encoding="utf-8")
            query = run_read("query", "--cx", CX, "--sql", sql, "--max-rows", "2", "--timeout", "60", timeout=180)
        err = query.stderr.decode("utf-8", "replace")
        self.assertEqual(query.returncode, check.returncode, err)
        if query.returncode == 0:
            self.assertRegex(err.strip().splitlines()[-1], r"\b2 rows\b")  # the cap, not the TOP 5
        else:
            self.assertIn("this login can write", err)

    def test_the_probes_schema_clause_finds_what_the_function_gives_for_a_name(self):
        # The probe's own form must give a schema the rows the literal name gives it. --allow-writable-login leaves
        # the probe out of this run, so a login that can write still runs the counts.
        with tempfile.TemporaryDirectory() as temp:
            sql = Path(temp) / "canary.sql"
            sql.write_text(schema_canary(), encoding="utf-8")
            proc = run_read("query", "--cx", CX, "--sql", sql, "--allow-writable-login", "--timeout", "60",
                            timeout=180)
        self.assertEqual(proc.returncode, 0, proc.stderr.decode("utf-8", "replace"))
        rows = [r for r in csv.reader(io.StringIO(proc.stdout.decode("utf-8-sig"))) if r]
        counts = dict(zip(rows[0], (int(v) for v in rows[1])))
        if not any(counts[f"named_{name}"] for name in CANARY_SCHEMAS):
            self.skipTest("this login holds no permission on dbo or sys, so a clause that matches nothing cannot be "
                          "told from one that works")
        for name in CANARY_SCHEMAS:
            with self.subTest(schema=name):
                self.assertEqual(counts[f"applied_{name}"], counts[f"named_{name}"],
                                 "fn_my_permissions(s.name, 'SCHEMA') found nothing where the literal name finds "
                                 "something: the probe's schema clause is not reading schemas")


@unittest.skipUnless(E2E, "set SQL_READ_E2E=1 to run against the real lprun9")
@unittest.skipUnless(TOOL, NOT_FOUND)
@unittest.skipUnless(SCHEMA_CX, "set SQL_READ_E2E_SCHEMA_CX to a saved connection whose login holds only a schema grant")
class SchemaGrant(unittest.TestCase):
    def test_check_and_query_refuse_a_login_whose_only_write_is_a_grant_on_a_schema(self):
        check = run_read("check", "--cx", SCHEMA_CX, "--timeout", "60", timeout=180)
        err = check.stderr.decode("utf-8", "replace")
        self.assertEqual(check.returncode, 5, err)
        self.assertIn("ON SCHEMA::", check.stdout.decode("utf-8", "replace"))
        with tempfile.TemporaryDirectory() as temp:
            sql = Path(temp) / "q.sql"
            sql.write_text("SELECT 1 AS x", encoding="utf-8")
            query = run_read("query", "--cx", SCHEMA_CX, "--sql", sql, "--timeout", "60", timeout=180)
        err = query.stderr.decode("utf-8", "replace")
        self.assertEqual(query.returncode, 5, err)
        self.assertIn("this login can write", err)


if __name__ == "__main__":
    unittest.main()
