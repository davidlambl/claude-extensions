"""sql-read end to end with the real lprun9, skipped unless SQL_READ_E2E=1 and lprun9 is found where the transport
looks for it (env LPRUN9, PATH, LINQPad 9's install folders). Run with:
SQL_READ_E2E=1 python3 -m unittest discover -s skills/sql-read/tests

plan reads the fixture's fake connections and runs nothing, so it is safe wherever lprun9 is installed. check runs
against a real saved connection, the one SQL_READ_E2E_CX names, and only when that is set. Saved connections are often
production ones: nothing but the user sets SQL_READ_E2E_CX. check sends only the login probe, which reads."""
import json, os, subprocess, sys, tempfile, unittest
from pathlib import Path

from test_plan import SCRIPTS
from backend import Unusable
from transports import lprun9

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "connections.xml"
E2E = os.environ.get("SQL_READ_E2E") == "1"
CX = os.environ.get("SQL_READ_E2E_CX", "").strip()


def find_lprun9():
    """Where the transport finds lprun9 in this environment, or None."""
    try:
        return lprun9.transport().locate()
    except Unusable:
        return None


TOOL = find_lprun9() if E2E else None


def run_read(*argv, timeout=120):
    """python3 read.py ARGV in a process of its own, from the plugin's scripts folder, in this environment."""
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONIOENCODING", "PYTHONUTF8")}
    return subprocess.run([sys.executable, str(SCRIPTS / "read.py"), *[str(a) for a in argv]], capture_output=True,
                          timeout=timeout, env=env)


@unittest.skipUnless(E2E, "set SQL_READ_E2E=1 to run against the real lprun9")
@unittest.skipUnless(TOOL, "lprun9 was not found: install LINQPad 9, or set LPRUN9")
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
@unittest.skipUnless(TOOL, "lprun9 was not found: install LINQPad 9, or set LPRUN9")
@unittest.skipUnless(CX, "set SQL_READ_E2E_CX to a saved connection's name to run check against it")
class Check(unittest.TestCase):
    def test_check_against_the_connection_sql_read_e2e_cx_names(self):
        proc = run_read("check", "--cx", CX, "--timeout", "60", timeout=180)
        err = proc.stderr.decode("utf-8", "replace")
        self.assertIn(proc.returncode, (0, 5), err)
        last = err.strip().splitlines()[-1]
        self.assertTrue(last.startswith("sql-read: "), last)
        self.assertRegex(last, "read-only|this login can write")


if __name__ == "__main__":
    unittest.main()
