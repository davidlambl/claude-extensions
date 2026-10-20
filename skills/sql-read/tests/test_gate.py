"""The read.py gate's decisions, run with: python3 -m unittest discover -s skills/sql-read/tests

Each test pipes the hook's JSON into hooks/gate.py as a subprocess, as Claude Code runs it, and reads what it prints:
nothing for silence, or one PreToolUse decision. Most run the same command as the Bash tool and as the PowerShell tool.
The SQL files the commands name are written to a temporary folder, which is also the hook's cwd; the gate itself runs
from an empty folder, so a relative path can only resolve against the cwd the hook names.

The gate's environment points APPDATA at a temporary folder holding the fixture's fake connections where LINQPad keeps
its list, LPRUN9 at an empty file standing for lprun9, and leaves SQL_READ_CONNECTIONS unset, so no test reads
LINQPad's own connection list. Nothing runs lprun9: a plan only looks for it. Characters outside ASCII are written as
escapes.
"""
import ast, fnmatch, importlib.util, json, os, re, shlex, subprocess, sys, tempfile, time, unittest
from pathlib import Path
from unittest import mock

PLUGIN = Path(__file__).resolve().parents[1]
GATE = PLUGIN / "hooks" / "gate.py"
HOOKS = PLUGIN / "hooks" / "hooks.json"
README = PLUGIN / "README.md"
SCRIPTS = PLUGIN / "skills" / "sql-read" / "scripts"
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "connections.xml"
LINQPAD_GATE = PLUGIN.parent / "linqpad" / "hooks" / "gate.py"  # keeps the same command-line reading
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
import read  # noqa: E402  (to make, in this process, the reason the gate should give)

READ_PY = (SCRIPTS / "read.py").as_posix()
RUN = f'python3 "{READ_PY}"'
BOTH = ("Bash", "PowerShell")
SECRET = "FAKE-pw-7f3a9c2e"
DASH, DOT, WARN, MORE, ESC = "\u2014", "\u00b7", "\u26a0", "\u2026", chr(27)
BS = "\\"
UNREAD = "sql-read: the gate could not read this command; approve only if you have read the SQL"
NETWORK = "a network or device path, which the gate does not open before you approve"
SALES = f'read on "Sales" {DASH} sales-sql.database.windows.net / Product {DASH} PRODUCTION'
LIMITS = f"up to 200 rows {DOT} 60 s timeout {DOT} lock timeout 5 s"
Q = "\n".join([f"sql-read: {SALES}", LIMITS, "| SELECT TOP 20 *", "| FROM dbo.Orders"])  # the reason for q.sql on Sales
CHECK_DEV = "\n".join([f'sql-read: login check on "Dev" {DASH} localhost / Scratch',
                       f"lists what this login can write {DOT} 60 s timeout {DOT} lock timeout 5 s"])
SHARED = ("# --- Reading a command line", "# --- The read.py gate")  # the section linqpad's gate keeps too

SQL = {
    "q.sql": "SELECT TOP 20 *\nFROM dbo.Orders\n",
    "sub/q.sql": "SELECT 2 AS two\n",
    "space dir/q.sql": "SELECT 3 AS three\n",
    "long.sql": "\n".join(f"-- line {n}" for n in range(1, 12)) + "\nSELECT 1 AS x\n",
    "bad.sql": "UPDATE dbo.Orders SET Total = 0\n",
    "escape.sql": f"SELECT 'a{ESC}[2Jb\u202ec' AS x\n",
}


def unread(*whys):
    """The could-not-read reason, with what the gate could not read."""
    return f"{UNREAD} ({'; '.join(whys)})"


def hook_ifs():
    """The `if` patterns of hooks.json, the ones that start the gate."""
    config = json.loads(HOOKS.read_text(encoding="utf-8"))
    return [h["if"] for group in config["hooks"]["PreToolUse"] for h in group["hooks"]]


def load_gate():
    """gate.py as a module, for the tests of its parts."""
    spec = importlib.util.spec_from_file_location("sql_read_gate", GATE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def git_bash(path):
    """A Windows path as Git Bash writes it: C:/x becomes /c/x."""
    return "/" + path[0].lower() + path[2:]


class GateCase(unittest.TestCase):
    """A temporary folder of SQL files, the hook's cwd; a fake lprun9; and the helpers that run the gate on a
    command."""

    @classmethod
    def setUpClass(cls):
        cls._folder = tempfile.TemporaryDirectory()
        cls.dir = Path(cls._folder.name).resolve()
        cls._empty = tempfile.TemporaryDirectory()  # where the gate itself runs
        for name, text in SQL.items():
            path = cls.dir / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8", newline="\n")
        (cls.dir / "c.xml").write_bytes(FIXTURE.read_bytes())
        cls.tool = cls.dir / "bin" / ("LPRun9.exe" if os.name == "nt" else "lprun9")
        cls.tool.parent.mkdir()
        cls.tool.write_bytes(b"")
        cls.tool.chmod(0o755)
        (cls.dir / "appdata" / "LINQPad").mkdir(parents=True)
        (cls.dir / "appdata" / "LINQPad" / "ConnectionsV2.xml").write_bytes(FIXTURE.read_bytes())
        cls.env = {**os.environ, "LPRUN9": str(cls.tool), "APPDATA": str(cls.dir / "appdata"),
                   "LOCALAPPDATA": str(cls.dir / "appdata")}
        cls.env.pop("SQL_READ_WRITABLE_OK", None)  # the user's own setting must not change what the tests expect
        for name in ("PYTHONIOENCODING", "SQL_READ_CONNECTIONS"):
            cls.env.pop(name, None)

    @classmethod
    def tearDownClass(cls):
        cls._folder.cleanup()
        cls._empty.cleanup()

    def p(self, name):
        """A file's absolute path, with forward slashes, as Claude types it in either shell."""
        return (self.dir / name).as_posix()

    def expected(self, *argv, cwd=None):
        """The reason read.py gives for argv, made in this process with the gate's environment."""
        with mock.patch.dict(os.environ, self.env, clear=True):
            return read.reason(read.plan_for_command(list(argv), str(cwd or self.dir)))

    def run_gate(self, stdin, env=None, args=()):
        return subprocess.run([sys.executable, *args, str(GATE)], input=stdin, capture_output=True, timeout=60,
                              env=self.env if env is None else env, cwd=self._empty.name)

    def hook(self, command, tool="Bash", cwd=None, mode="default", **fields):
        payload = {"session_id": "test-session", "transcript_path": "/nonexistent/transcript.jsonl",
                   "cwd": str(self.dir) if cwd is None else cwd, "permission_mode": mode,
                   "hook_event_name": "PreToolUse", "tool_name": tool,
                   "tool_input": {"command": command, "description": "a test command"}, "tool_use_id": "toolu_test"}
        if mode is None:
            del payload["permission_mode"]
        payload.update(fields)
        return json.dumps(payload).encode("utf-8")

    def decision(self, proc):
        """(decision, reason) from what the gate printed, or None for silence; checks the shape on the way."""
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stderr, b"")
        out = proc.stdout.decode("ascii")  # ASCII only, so no console code page can choke on it
        if not out:
            return None
        self.assertTrue(out.endswith("\n") and out.count("\n") == 1, out)
        printed = json.loads(out)
        self.assertEqual(list(printed), ["hookSpecificOutput"])
        output = printed["hookSpecificOutput"]
        self.assertEqual(set(output), {"hookEventName", "permissionDecision", "permissionDecisionReason"})
        self.assertEqual(output["hookEventName"], "PreToolUse")
        self.assertIn(output["permissionDecision"], ("ask", "deny"))
        self.assertTrue(output["permissionDecisionReason"].startswith("sql-read: "), output)
        self.assertNotIn(SECRET, output["permissionDecisionReason"])
        return output["permissionDecision"], output["permissionDecisionReason"]

    def decide(self, command, tool="Bash", env=None, **kw):
        return self.decision(self.run_gate(self.hook(command, tool, **kw), env=env))

    def asks(self, command, tool="Bash", fires=True, **kw):
        """The gate asks for the command, and, unless fires is False, Claude Code would start the gate for it: a form
        the gate asks for when driven directly but the hook's `if` patterns never pass would be let through."""
        if fires:
            self.assertTrue(matches(hook_ifs(), tool, command), f"{tool} {command!r}: no `if` pattern starts the gate")
        got = self.decide(command, tool, **kw)
        self.assertIsNotNone(got, f"{tool} {command!r} was let through silently")
        self.assertEqual(got[0], "ask", got)
        return got[1]

    def silent(self, command, tool="Bash", **kw):
        got = self.decide(command, tool, **kw)
        self.assertIsNone(got, f"{tool} {command!r} was stopped")

    def asks_each(self, commands, tools=BOTH, starts=None, equals=None, has=None, **kw):
        for tool in tools:
            for command in commands:
                with self.subTest(tool=tool, command=command):
                    reason = self.asks(command, tool, **kw)
                    if starts is not None:
                        self.assertTrue(reason.startswith(starts), reason)
                    if equals is not None:
                        self.assertEqual(reason, equals)
                    if has is not None:
                        self.assertIn(has, reason)

    def silent_each(self, commands, tools=BOTH, **kw):
        for tool in tools:
            for command in commands:
                with self.subTest(tool=tool, command=command):
                    self.silent(command, tool, **kw)


class Reads(GateCase):
    """A read.py query or check asks, with the plan read.py would print for it as the reason."""

    def test_a_query_asks_with_its_plan(self):
        self.asks_each([f'{RUN} query --cx Sales --sql "{self.p("q.sql")}"'], equals=Q)

    def test_a_check_asks_with_its_plan(self):
        self.asks_each([f"{RUN} check --cx Web-Prod"], equals="\n".join([
            f'sql-read: login check on "Web-Prod" {DASH} SQL-PROD-01.example.local / Web {DASH} PRODUCTION',
            f"lists what this login can write {DOT} 60 s timeout {DOT} lock timeout 5 s"]))
        self.asks_each([f"{RUN} check --cx dev"], equals=CHECK_DEV)

    def test_the_limits_and_warnings_given(self):
        command = (f'{RUN} query --cx Dev --sql "{self.p("q.sql")}" --max-rows 0 --timeout 2.5 --lock-timeout 1500 '
                   "--nolock --allow-writable-login")
        self.asks_each([command], equals="\n".join([
            f'sql-read: read on "Dev" {DASH} localhost / Scratch',
            f"no row cap {DOT} 2.5 s timeout {DOT} lock timeout 1.5 s", "| SELECT TOP 20 *", "| FROM dbo.Orders",
            f"{WARN} writable login allowed (NOT listed in env SQL_READ_WRITABLE_OK)", f"{WARN} no row cap", "NOLOCK (dirty reads)"]))

    def test_the_first_eight_lines_of_the_sql(self):
        reason = self.asks(f'{RUN} query --cx Sales --sql "{self.p("long.sql")}"')
        self.assertEqual(reason.split("\n")[2:], [f"| -- line {n}" for n in range(1, 9)] + [f"{MORE}(4 more lines)"])

    def test_control_characters_in_the_sql_are_shown_as_escapes(self):
        reason = self.asks(f'{RUN} query --cx Sales --sql "{self.p("escape.sql")}"')
        self.assertEqual(reason.split("\n")[2], f"| SELECT 'a{BS}x1b[2Jb{BS}u202ec' AS x")

    def test_every_way_to_name_python_and_read_py(self):
        tail = f'query --cx Sales --sql "{self.p("q.sql")}"'
        self.asks_each([f"{RUN} {tail}", f"python '{READ_PY}' {tail}", f"py -3 '{READ_PY}' {tail}",
                        f"python3.12 -I -X utf8 '{READ_PY}' {tail}", f"'{sys.executable}' '{READ_PY}' {tail}",
                        f"python3 '{self.dir.as_posix()}/READ.PY' {tail}", f"'{READ_PY}' {tail}",
                        f"./read.py {tail}", f"uv run '{READ_PY}' {tail}", f"python3 read.py {tail}"], equals=Q)
        windows = READ_PY.replace("/", BS)
        self.asks_each([f"python3 '{windows}' {tail}", f"& python3 '{READ_PY}' {tail}",
                        f"& 'C:{BS}Program Files{BS}Python312{BS}python.exe' '{windows}' {tail}"],
                       tools=["PowerShell"], equals=Q)

    def test_after_assignments_wrappers_and_other_shells(self):
        tail = f"query --cx Sales --sql {self.p('q.sql')}"
        run = f"python3 {READ_PY} {tail}"
        self.asks_each([f"FOO=1 {run}", f"timeout 120 {run}", f"nohup {run}", f"bash -c '{run}'",
                        f'sh -c "{run}"', f"echo '{run}' | bash", f"eval {run}", f"x=$({run})",
                        f"bash <<'EOF'\n{run}\nEOF"], tools=["Bash"], equals=Q)
        self.asks_each([f"& {run}", f"$out = {run}", f"pwsh -c '{run}'", f"iex '{run}'",
                        f"Start-Process python3 -ArgumentList '{READ_PY} {tail}' -Wait -NoNewWindow",
                        f"cmd /c {run}", f"python3 {READ_PY} --% {tail}"], tools=["PowerShell"], equals=Q)

    def test_start_process_splits_an_argument_list_as_the_program_will(self):
        # Start-Process joins an -ArgumentList's pieces with spaces and the program splits the line again, so a piece
        # that holds several arguments is several.
        args = ("query", "--cx", "Sales", "--sql", self.p("q.sql"))
        for command in (f"Start-Process python3 '{READ_PY}','query --cx Sales --sql {self.p('q.sql')}' -Wait",
                        f"Start-Process python3 -ArgumentList '{READ_PY}','query --cx Sales','--sql {self.p('q.sql')}'",
                        f"Start-Process python3 -ArgumentList '{READ_PY}','query','--cx Sales --sql',"
                        f"'{self.p('q.sql')}' -Wait -NoNewWindow",
                        f"Start-Process -FilePath python3 -ArgumentList '{READ_PY}','query --cx Sales',"
                        f"'--sql {self.p('q.sql')}'"):
            with self.subTest(command=command):
                self.assertEqual(self.asks(command, "PowerShell"), self.expected(*args))

    def test_start_process_with_a_group_or_a_scriptblock_asks_though_the_gate_cannot_read_it(self):
        tail = f"query --cx Sales --sql {self.p('q.sql')}"
        for command in (f'Start-Process python3 -ArgumentList ("{READ_PY} {tail}") -Wait',
                        f"Start-Process python3 -ArgumentList ('{READ_PY} ' + 'query') -Wait",
                        f"& ([scriptblock]::Create('python3 {READ_PY} {tail}'))",
                        f"& (Get-Command python3).Source -c ('{READ_PY}')"):
            with self.subTest(command=command):
                reason = self.asks(command, "PowerShell")
                self.assertTrue(reason.startswith(UNREAD), reason)

    def test_a_computed_command_in_a_line_that_names_no_read_py_run_is_not_asked_about(self):
        # The group rule needs the line to name read.py; any other group is not the gate's business.
        self.silent_each(["Start-Process notepad -ArgumentList (Get-Item x)", "& ([scriptblock]::Create('dir'))",
                          "echo ('read.py is a file')", f"Write-Host (\"{READ_PY}\")"], tools=["PowerShell"])

    def test_start_process_takes_its_parameters_in_any_order(self):
        listed = f"'{READ_PY}','query','--cx','Sales','--sql','{self.p('q.sql')}'"
        self.asks_each([f"Start-Process python3 -ArgumentList {listed} -Wait -NoNewWindow",
                        f"Start-Process -ArgumentList {listed} -FilePath python3 -Wait",
                        f"saps -Wait -FilePath py -ArgumentList {listed} -PassThru",
                        f"Start-Process -FilePath python3 -RedirectStandardOutput out.csv -ArgumentList {listed}",
                        f"Start-Process -ArgumentList:{listed} -FilePath python3 -WindowStyle Hidden"],
                       tools=["PowerShell"], equals=Q)
        self.asks_each([f"Start-Process -ArgumentList 'query','--cx','Sales' -FilePath '{READ_PY}'"],
                       tools=["PowerShell"], starts=UNREAD)

    def test_read_py_run_as_a_module_or_a_copy(self):
        tail = f"query --cx Sales --sql {self.p('q.sql')}"
        self.asks_each([f"python3 -m read {tail}", f"cd {SCRIPTS.as_posix()} && python3 -m read {tail}",
                        f"py -3 -mread {tail}", f"python3 -I -m read {tail}", f"uv run -m read {tail}"], equals=Q)
        self.asks_each(["python -m read check --cx dev"], equals=CHECK_DEV)
        self.asks_each([f'cp "{READ_PY}" r.py && python3 r.py {tail}', f'cat "{READ_PY}" > r.py; python3 ./r.py {tail}',
                        f'ln -s "{READ_PY}" r.py; timeout 60 python3 r.py {tail}'], tools=["Bash"],
                       equals=unread("r.py: the line may write it from read.py before it runs"))
        self.asks_each([f"Copy-Item '{READ_PY}' r.py; python3 r.py {tail}"], tools=["PowerShell"],
                       equals=unread("r.py: the line may write it from read.py before it runs"))
        self.silent_each([f'cp "{READ_PY}" r.py && python3 r.py plan --cx Sales', f'cp "{READ_PY}" backup.py',
                          f'cat "{READ_PY}"; python3 other.py {tail}', f"python3 -m reader {tail}",
                          f"git commit -m read {tail}"])

    def test_a_substitution_in_a_heredoc_or_here_string_runs(self):
        run = f"{RUN} query --cx Sales --sql {self.p('q.sql')}"
        self.asks_each([f"cat > report.md <<EOF\n# Results\n## Rows: $({run})\nEOF",
                        f"tee report.md <<EOF\nsee `{run}`\nEOF", f'cat <<< "rows: $({run})"', f'echo "# $({run})"'],
                       tools=["Bash"], equals=Q)
        # python in the line may read any of its text as code, so a substitution alone also reads as a command whose
        # program the shell computes
        self.asks_each([f"cat > report.md <<EOF\n$({run})\nEOF", f'echo "$({run})"'], tools=["Bash"], has=Q)
        # in text only the substitution runs
        self.silent_each([f"cat > notes.md <<EOF\nGenerated $(date). Run {run} to refresh.\nEOF",
                          f'git commit -m "docs: {READ_PY} query --cx Sales now asks ($(date))"'], tools=["Bash"])
        self.silent_each([f'Write-Output "run python3 {READ_PY} query --cx Sales, $(Get-Date)"'], tools=["PowerShell"])
        self.silent_each([f"cat > report.md <<'EOF'\n$({run})\nEOF", f'cat > report.md <<"EOF"\n`{run}`\nEOF',
                          f"cat <<< '$({run})'"], tools=["Bash"])

    def test_every_command_in_the_line(self):
        q = self.p("q.sql")
        run = f"{RUN} query --cx Sales --sql {q}"
        self.asks_each([f"git status && {run}", f"{run} | head -5", f"{run} 2>&1", f"echo start; {run}; echo done",
                        f"{RUN} plan --cx Sales --sql {q} && {run}", f"cd {self.dir.as_posix()} && {run}",
                        f"{run} > {self.p('out.csv')}", f"cat {q} && {run}", f"{run}; {run}", f"{run} || exit $?"],
                       equals=Q)
        self.asks_each([f"Get-Content {q}; {run}"], tools=["PowerShell"], equals=Q)

    def test_two_runs_ask_once_with_both_plans(self):
        both = f"{RUN} query --cx Sales --sql {self.p('q.sql')} && {RUN} check --cx Dev"
        self.asks_each([both], equals=f"{Q}\n{CHECK_DEV}")
        many = "; ".join(f"{RUN} check --cx {name}" for name in ("Dev", "Sales", "Web-Prod", "dev ", "DEV"))
        reason = self.asks(many)
        self.assertEqual(reason.count("sql-read: login check on"), 3)
        self.assertTrue(reason.endswith("\nsql-read: and 2 more read.py runs in this command"), reason)

    def test_relative_paths_against_the_hooks_cwd(self):
        self.asks_each([f"{RUN} query --cx Sales --sql q.sql", f"{RUN} query --cx Sales --sql ./sub/../q.sql"],
                       equals=Q)
        reason = self.asks(f"{RUN} query --cx Sales --sql q.sql", cwd=self.p("sub"))
        self.assertEqual(reason.split("\n")[2:], ["| SELECT 2 AS two"])

    def test_a_quoted_path_with_spaces(self):
        sql = self.p("space dir/q.sql")
        reason = self.asks(f'{RUN} query --cx "Sales" --sql "{sql}"')
        self.assertEqual(reason.split("\n")[2:], ["| SELECT 3 AS three"])
        reason = self.asks(f"{RUN} query --cx 'Sales' --sql '{sql}'", "PowerShell")
        self.assertEqual(reason.split("\n")[2:], ["| SELECT 3 AS three"])

    def test_the_forms_read_py_takes(self):
        q = self.p("q.sql")
        self.asks_each([f"{RUN} query --cx=Sales --sql={q}", f"{RUN} query --sql {q} --cx ' sales '",
                        f'{RUN} "query" --cx Sales --sql {q}'], equals=Q)
        # argparse takes a -- before the subcommand in Python 3.12.10 and refuses it in 3.12.3; the gate asks either
        # way, with what this Python's read.py makes of it.
        self.asks_each([f"{RUN} -- query --cx Sales --sql {q}"], equals=self.expected("--", "query", "--cx", "Sales",
                                                                                     "--sql", q))

    @unittest.skipUnless(os.name == "nt", "Git Bash's drive paths are a Windows matter")
    def test_git_bash_drive_paths(self):
        command = f"python3 {git_bash(READ_PY)} query --cx Sales --sql {git_bash(self.p('q.sql'))}"
        self.asks_each([command], tools=["Bash"], equals=Q, cwd=git_bash(self.dir.as_posix()))
        self.asks_each([f"{RUN} query --cx Sales --sql q.sql"], tools=["Bash"], equals=Q,
                       cwd=git_bash(self.dir.as_posix()))

    def test_a_plan_that_will_not_run_still_asks_and_says_why(self):
        bad = self.asks(f"{RUN} query --cx Sales --sql {self.p('bad.sql')}").split("\n")
        self.assertTrue(bad[0].startswith("sql-read: will not run (exit 4): the guard refused the SQL: "), bad[0])
        self.assertEqual(bad[1:], [SALES, LIMITS])
        nope = self.asks(f"{RUN} query --cx Nope --sql {self.p('q.sql')}").split("\n")
        self.assertTrue(nope[0].startswith("sql-read: will not run (exit 3): ") and '"Nope"' in nope[0], nope[0])
        self.assertEqual(nope[1:], ['read on "Nope"', LIMITS, "| SELECT TOP 20 *", "| FROM dbo.Orders"])
        unnamed = self.asks(f"{RUN} check --cx SQL-PROD-01.example.local")
        self.assertTrue(unnamed.startswith("sql-read: will not run (exit 3): "), unnamed)
        self.assertIn("give it a name in LINQPad", unnamed)
        self.asks_each([f"{RUN} query --cx Sales", f"{RUN} query --cx Sales --sql {self.p('q.sql')} --max-rows x"],
                       starts="sql-read: the command line is not valid, so nothing will run: ")
        env = {**self.env, "LPRUN9": str(self.dir / "missing" / "LPRun9.exe")}
        reason = self.asks(f"{RUN} query --cx Sales --sql {self.p('q.sql')}", env=env).split("\n")
        self.assertTrue(reason[0].startswith("sql-read: will not run (exit 3): "), reason[0])
        self.assertEqual(reason[1:], [SALES, LIMITS, "| SELECT TOP 20 *", "| FROM dbo.Orders"])

    def test_help_runs_nothing_and_says_so(self):
        self.asks_each([f"{RUN} query --help"], equals="sql-read: prints the usage of read.py query; runs nothing")
        self.asks_each([f"{RUN} -h query"], equals="sql-read: prints the usage of read.py; runs nothing")

    def test_bash_passes_a_stop_parsing_token_on(self):
        self.asks_each([f"{RUN} --% query --cx Sales --sql {self.p('q.sql')}"], tools=["Bash"],
                       starts="sql-read: the command line is not valid, so nothing will run: ", has="--%")

    def test_a_subcommand_in_another_case_runs_nothing_and_says_so(self):
        self.asks_each([f"{RUN} QUERY --cx Sales --sql {self.p('q.sql')}"],
                       starts="sql-read: the command line is not valid, so nothing will run: ", has="'QUERY'")

    def test_the_reason_is_read_py_s_own(self):
        q = self.p("q.sql")
        for argv in (["query", "--cx", "Web-Prod", "--sql", q, "--format", "text", "--max-rows", "1"],
                     ["check", "--cx", "Sales", "--timeout", "7"], ["query", "--cx", "Sales", "--sql", "q.sql"]):
            with self.subTest(argv=argv):
                self.assertEqual(self.asks(" ".join([RUN, *argv])), self.expected(*argv))

    def test_connections_from_another_list_say_so(self):
        listing = self.p("c.xml")  # a copy of the fixture, at a path short enough to show whole
        reason = self.asks(f"{RUN} query --cx Sales --sql {self.p('q.sql')} --connections {listing}")
        self.assertEqual(reason.split("\n")[:-1], Q.split("\n"))
        self.assertEqual(reason.split("\n")[-1], f"{WARN} details from --connections {listing}; lprun9 looks the name "
                                                 "up in LINQPad's own list")

    def test_connections_from_the_environment_say_so(self):
        listing = self.p("c.xml")
        for value in (listing, f' "{listing}" '):
            with self.subTest(value=value):
                env = {**self.env, "SQL_READ_CONNECTIONS": value}
                reason = self.asks(f"{RUN} query --cx Sales --sql {self.p('q.sql')}", env=env)
                self.assertEqual(reason.split("\n")[:-1], Q.split("\n"))
                self.assertEqual(reason.split("\n")[-1], f"{WARN} details from env SQL_READ_CONNECTIONS {listing}; "
                                                         "lprun9 looks the name up in LINQPad's own list")
        env = {**self.env, "SQL_READ_CONNECTIONS": " "}  # blank, so read.py reads LINQPad's own list
        self.assertEqual(self.asks(f"{RUN} query --cx Sales --sql {self.p('q.sql')}", env=env), Q)

    def test_python_settings_that_change_no_code(self):
        run = f"{RUN} query --cx Sales --sql {self.p('q.sql')}"
        self.asks_each([f"PYTHONUTF8=1 {run}", f"PYTHONIOENCODING=utf-8 {run}"], tools=["Bash"], equals=Q)
        self.asks_each([f"$env:PYTHONIOENCODING = 'utf-8'; {run}"], tools=["PowerShell"], equals=Q)

    def test_the_plan_writes_nothing(self):
        self.asks(f"{RUN} query --cx Sales --sql {self.p('q.sql')}")
        self.assertFalse((self.dir / "sql-read").exists())

    def test_loading_read_py_writes_no_bytecode_beside_it(self):
        import shutil
        with tempfile.TemporaryDirectory() as folder:
            copy = Path(folder) / "sql-read"
            for part in ("hooks", "skills"):
                shutil.copytree(PLUGIN / part, copy / part, ignore=shutil.ignore_patterns("__pycache__"))
            env = {k: v for k, v in self.env.items() if k != "PYTHONDONTWRITEBYTECODE"}
            proc = subprocess.run([sys.executable, str(copy / "hooks" / "gate.py")], capture_output=True, timeout=60,
                                  input=self.hook(f"{RUN} query --cx Sales --sql {self.p('q.sql')}"), env=env,
                                  cwd=self._empty.name)
            self.assertEqual(self.decision(proc), ("ask", Q))
            self.assertEqual(list(copy.rglob("__pycache__")), [])

    def test_a_read_py_the_command_names_is_never_imported(self):
        evil = self.dir / "evil"
        evil.mkdir(exist_ok=True)
        marker = evil / "imported"
        (evil / "read.py").write_text(f"open({str(marker)!r}, 'w').close()\n", encoding="utf-8")
        self.asks_each([f"python3 {evil.as_posix()}/read.py query --cx Sales --sql {self.p('q.sql')}",
                        f"python3 read.py query --cx Sales --sql ../q.sql"], equals=Q, cwd=str(evil))
        self.assertFalse(marker.exists())


class Silence(GateCase):
    """read.py plan and connections, and everything else that runs no read, pass without a word."""

    def test_plan_and_connections(self):
        q = self.p("q.sql")
        self.silent_each([f"{RUN} plan --cx Sales --sql {q}", f"{RUN} plan --cx Sales --sql query.sql",
                          f"{RUN} plan --cx Sales", f"{RUN} connections", f"{RUN} connections --json",
                          f"{RUN} connections --connections check.xml", RUN, f"{RUN} --help",
                          f"{RUN} --via lprun9 query --cx Sales --sql {q}", f"{RUN} --cx Sales query --sql {q}",
                          f"timeout 60 {RUN} plan --cx Sales --sql {q} | python3 -m json.tool"])

    def test_a_plain_command(self):
        self.silent_each(["npm test", "git status", "", "python3 -m unittest discover -s skills/sql-read/tests"])

    def test_what_the_d_p_patterns_start_the_gate_for_passes_in_silence(self):
        # The Bash handlers for read.py are the fragment d.p in four casings, so they start the gate for any Bash
        # command that holds those letters, and the README names that cost. The gate lets each of these through.
        for command in ["ls build.py", "python3 BUILD.PY", "open word.pdf", "python3 -c 'import pandas as pd; pd.pivot'"]:
            with self.subTest(command=command):
                self.assertTrue(matches(hook_ifs(), "Bash", command), "no `if` pattern starts the gate")
                self.silent(command)
        for command in ["ls build.js", "git status", "open word.docx"]:
            with self.subTest(command=command):
                self.assertFalse(matches(hook_ifs(), "Bash", command), "an `if` pattern starts the gate")

    def test_tools_other_than_the_shells(self):
        command = f"{RUN} query --cx Sales --sql {self.p('q.sql')}"
        for tool, tool_input in [("Read", {"file_path": READ_PY}), ("Write", {"file_path": self.p("q.sql"),
                                                                             "content": "SELECT 1"}),
                                 ("Edit", {"file_path": READ_PY, "old_string": "a", "new_string": "b"}),
                                 ("Grep", {"pattern": "read.py query"}), ("mcp__fake__run", {"command": command}),
                                 ("bash", {"command": command}), ("Task", {"prompt": command})]:
            with self.subTest(tool=tool):
                payload = json.loads(self.hook("", tool))
                payload["tool_input"] = tool_input
                self.assertIsNone(self.decision(self.run_gate(json.dumps(payload).encode())))

    def test_events_other_than_pre_tool_use(self):
        for event in ("PostToolUse", "PermissionRequest"):
            with self.subTest(event=event):
                stdin = self.hook(f"{RUN} query --cx Sales --sql {self.p('q.sql')}", hook_event_name=event)
                self.assertIsNone(self.decision(self.run_gate(stdin)))

    def test_no_command(self):
        for tool_input in ({}, {"command": None}, None, "text"):
            with self.subTest(tool_input=tool_input):
                payload = json.loads(self.hook(""))
                payload["tool_input"] = tool_input
                self.assertIsNone(self.decision(self.run_gate(json.dumps(payload).encode())))

    def test_a_long_path_keeps_the_sql_files_name_in_the_prompt(self):
        # Windows scratch paths run past the 120 characters the prompt shows: the middle goes, not the end.
        long = f"{self.dir.as_posix()}/{'d' * 150}/orders-by-region.sql"
        self.asks_each([f"{RUN} query --cx Sales --sql {long}"], starts=UNREAD,
                       has="orders-by-region.sql: the gate cannot open it")
        reason = self.asks(f"{RUN} query --cx Sales --sql {long}")
        self.assertIn("...", reason)
        self.assertNotIn("d" * 150, reason)

    def test_text_that_only_mentions_read_py(self):
        q = self.p("q.sql")
        message = f"Ask before {RUN} query --cx Sales --sql {q}\n\nThe gate reads read.py runs."
        self.silent_each([f"git commit -m 'run {RUN} query --cx Sales --sql {q}'",
                          f"echo python3 read.py query --cx Sales --sql {q}", "grep -n 'read.py query' README.md",
                          f"cat {READ_PY} | head -50", "cat read.py query", f"code {READ_PY}",
                          f"# {RUN} query --cx Sales --sql {q}", f"git diff -- {READ_PY}", "ls read.py check",
                          f"cat {READ_PY} $F check"])
        self.silent_each([f"git commit -m \"$(cat <<'EOF'\n{message}\nEOF\n)\"", f"cat <<'EOF'\n{message}\nEOF",
                          f"cat > notes.md <<'EOF'\n{message}\nEOF"], tools=["Bash"])
        self.silent_each([f"Get-Content {READ_PY} | Select-String query", f"Write-Output '{RUN} query --cx Sales'"],
                         tools=["PowerShell"])

    def test_scripts_other_than_read_py(self):
        tail = f"query --cx Sales --sql {self.p('q.sql')}"
        self.silent_each([f"python3 reader.py {tail}", f"python3 read.pyc {tail}", f"python3 unread.py {tail}",
                          f"python3 read.py.bak {tail}", f"python3 -m readers {tail}", f"python3 {READ_PY}x {tail}"])

    def test_single_quotes_keep_a_backtick_or_a_substitution_as_text(self):
        q = self.p("q.sql")
        self.silent_each([f"git commit -m 'docs: run `python3 read.py query --cx Sales --sql {q}`'",
                          f"echo 'see $({RUN} query --cx Sales --sql {q})' >> notes.md"])
        self.silent_each([f"Add-Content notes.md -Value 'run `{RUN} query --cx Sales --sql {q}`'",
                          f"'{RUN} query --cx Sales --sql {q}' | Out-File notes.txt",
                          f"Set-Content -Path notes.md -Value @'\nRun `python3 read.py query --cx Sales --sql {q}`, "
                          "or `$(python3 read.py check --cx Dev)`.\n'@"], tools=["PowerShell"])
        self.asks_each([f'git commit -m "docs: `python3 {READ_PY} query --cx Sales --sql {q}`"'], tools=["Bash"],
                       equals=Q)
        self.asks_each([f'Write-Output "see $(python3 {READ_PY} query --cx Sales --sql {q})"'], tools=["PowerShell"],
                       equals=Q)


class CouldNotRead(GateCase):
    """A run whose plan the gate cannot make true to what will run asks, saying so, rather than guessing."""

    def test_a_line_that_does_not_parse(self):
        q = self.p("q.sql")
        self.asks_each([f'{RUN} query --cx "Sales --sql {q}', f"{RUN} query --cx 'Sales --sql {q}"],
                       equals=unread("the command line: a quote does not close"))
        self.asks_each([f"{RUN} query --cx Sales --sql {q} <# open"], tools=["PowerShell"], starts=UNREAD)

    def test_a_command_inside_it_that_does_not_parse(self):
        self.asks_each([f"bash -c \"{RUN} query --cx 'Sales\""], tools=["Bash"], starts=UNREAD,
                       has="a command inside it")

    def test_an_argument_the_shell_computes(self):
        q = self.p("q.sql")
        run = f"{RUN} query --cx Sales"
        self.asks_each([f"{run} --sql $F", f"{run} --sql {q} $FLAGS", f"{run} --sql $(echo {q})",
                        f"{run} --sql ~/q.sql", f"{run} --sql {self.dir.as_posix()}/*.sql",
                        f"{run} --sql {self.dir.as_posix()}/{{q,long}}.sql",
                        f"{RUN} query --cx \"$CX\" --sql {q}", f"{run} --sql `echo {q}`"], tools=["Bash"],
                       starts=UNREAD, has="the gate cannot expand it")
        self.asks_each([f"{run} --sql $env:TEMP/q.sql", f"{run} --sql {q} @rest", f"{run} --sql (Resolve-Path {q})",
                        f"{RUN} query --cx $cx --sql {q}"], tools=["PowerShell"], starts=UNREAD)
        self.asks_each([f"{run} --sql $F"], tools=["Bash"], equals=unread("$F: the gate cannot expand it"))

    def test_a_subcommand_or_a_read_py_the_shell_computes(self):
        q = self.p("q.sql")
        self.asks_each([f"{RUN} $SUB --cx Sales --sql {q}", f"R={READ_PY}; python3 $R query --cx Sales --sql {q}",
                        f'"$PY" {READ_PY} query --cx Sales --sql {q}'], tools=["Bash"], starts=UNREAD)
        self.asks_each([f"$r = '{READ_PY}'; python3 $r query --cx Sales --sql {q}",
                        f"& $py {READ_PY} query --cx Sales --sql {q}"], tools=["PowerShell"], starts=UNREAD)

    def test_read_py_from_standard_input(self):
        q = self.p("q.sql")
        self.asks_each([f"cat {READ_PY} | python3 - query --cx Sales --sql {q}",
                        f"python3 - query --cx Sales --sql {q} < {READ_PY}"], tools=["Bash"], starts=UNREAD)
        self.asks_each([f"Get-Content {READ_PY} | python3 - check --cx Sales"], tools=["PowerShell"], starts=UNREAD)
        self.asks_each([f"python3 <(cat {READ_PY}) query --cx Sales --sql {q}",
                        f"python3 /dev/stdin query --cx Sales --sql {q} < {READ_PY}",
                        f"cat {READ_PY} | python3 /dev/stdin check --cx Sales"], tools=["Bash"], starts=UNREAD)

    def test_cmd_escapes(self):
        q = self.p("q.sql")
        self.asks_each([f"cmd /c python3 {READ_PY} qu^ery --cx Sales --sql {q}",
                        f"cmd /c python3 {READ_PY} query --cx Sa^les --sql {q}"], starts=UNREAD,
                       has="the gate cannot expand it")

    def test_a_run_held_in_a_variable_and_run_later(self):
        q = self.p("q.sql")
        self.asks_each([f'C="python3 {READ_PY} query --cx Sales"; $C --sql {q}'], tools=["Bash"], starts=UNREAD)
        self.asks_each([f"$c = 'python3 {READ_PY} query --cx Sales'; iex \"$c --sql {q}\""], tools=["PowerShell"],
                       starts=UNREAD)
        whole = f"python3 {READ_PY} query --cx Sales --sql {q}"
        self.asks_each([f'C="{whole}"; bash -c "$C"', f'C="{whole}"; eval "$C"'], tools=["Bash"],
                       starts=unread("$C: the gate cannot expand it"))
        self.asks_each([f"$c = '{whole}'; iex $c"], tools=["PowerShell"],
                       starts=unread("$c: the gate cannot expand it"))

    def test_a_function_or_an_alias_that_may_stand_for_a_program(self):
        run = f"{RUN} query --cx Sales --sql {self.p('q.sql')}"
        self.asks_each([f"python3() {{ echo; }}; {run}", f"function python3 {{ echo; }}; {run}",
                        f"alias python3=echo\n{run}"], tools=["Bash"], starts=UNREAD, has="defines a function")
        self.asks_each([f"function python3 {{ 'x' }}; {run}", f"Set-Alias python3 echo; {run}",
                        f"New-Alias -Name python3 -Value echo; {run}"], tools=["PowerShell"], starts=UNREAD,
                       has="defines a function")

    def test_a_relative_path_after_a_change_of_directory(self):
        run = f"{RUN} query --cx Sales --sql q.sql"
        moved = unread("q.sql: a relative path after a change of directory")
        self.asks_each([f"cd sub && {run}", f"pushd sub; {run}", f"(cd sub; {run})", f"env -C sub {run}"],
                       tools=["Bash"], equals=moved)
        self.asks_each([f"Set-Location sub; {run}", f"cd sub; {run}", f"Push-Location sub; {run}",
                        f"Start-Process python3 -WorkingDirectory sub -ArgumentList '{READ_PY} query --cx Sales "
                        "--sql q.sql'"], tools=["PowerShell"], equals=moved)
        self.asks_each([f"cd sub && {RUN} check --cx Sales --connections c.xml"],
                       equals=unread("c.xml: a relative path after a change of directory"))
        self.asks_each([f"cd sub && {RUN} query --cx Sales --sql=q.sql"], equals=moved)

    def test_a_relative_path_with_no_cwd(self):
        for cwd in ("", "relative/folder"):
            with self.subTest(cwd=cwd):
                self.asks_each([f"{RUN} query --cx Sales --sql q.sql"], cwd=cwd,
                               equals=unread("q.sql: a relative path, and the gate cannot tell what it is relative to"))

    @unittest.skipUnless(os.name == "nt", "a path only Git Bash can place is a Windows matter")
    def test_a_path_the_gate_cannot_place(self):
        self.asks_each([f"{RUN} query --cx Sales --sql /tmp/q.sql"], tools=["Bash"],
                       equals=unread("/tmp/q.sql: the gate cannot tell which file this is"))
        self.asks_each([f"{RUN} query --cx Sales --sql {BS}q.sql", f"{RUN} query --cx Sales --sql C:q.sql"],
                       tools=["PowerShell"], starts=UNREAD, has="the gate cannot tell which file this is")

    def test_a_sql_file_the_gate_cannot_open(self):
        for name in ("missing.sql", "sub"):
            with self.subTest(name=name):
                self.asks_each([f"{RUN} query --cx Sales --sql {self.p(name)}"],
                               equals=unread(f"{self.p(name)}: the gate cannot open it"))

    def test_a_sql_file_the_same_line_could_write(self):
        q = self.p("q.sql")
        run = f"{RUN} query --cx Sales --sql {q}"
        written = unread("q.sql: this command may write it before read.py reads it")
        self.asks_each([f"echo 'SELECT 9' > {q} && {run}", f"cat > {q} <<'EOF'\nSELECT 9\nEOF\n{run}",
                        f"cp {self.p('long.sql')} {q}; {run}", f"bash -c 'cp long.sql q.sql'; {run}",
                        f"tee {q} < /dev/null; {RUN} query --cx Sales --sql q.sql"], tools=["Bash"], equals=written)
        self.asks_each([f"Set-Content {q} 'SELECT 9'; {run}", f"Copy-Item long.sql q.sql; {run}"],
                       tools=["PowerShell"], equals=written)
        self.asks_each([f"git checkout -- {q} && {run}", f"{run} > {q}"], equals=written)
        self.asks_each([f"bash -c 'cp long.sql q.sql; {run}'", f"bash -c 'cp long.sql q.sql' && bash -c '{run}'"],
                       tools=["Bash"], equals=written)
        self.asks_each([f"bash -c 'cp long.sql q.sql' {READ_PY} check --cx Sales; {run}"], tools=["Bash"],
                       starts=UNREAD, has="q.sql: this command may write it before read.py reads it")

    def test_the_line_changes_what_the_plan_reads(self):
        run = f"{RUN} query --cx Sales --sql {self.p('q.sql')}"
        self.asks_each([f"SQL_READ_CONNECTIONS=/x/c.xml {run}", f"export LPRUN9=/x/lprun9; {run}",
                        f"PATH=/x:$PATH {run}", f"env APPDATA=/x {run}", f"unset SQL_READ_CONNECTIONS; {run}",
                        f"env -u LPRUN9 {run}", f"HOME=/x {run}"], tools=["Bash"], starts=UNREAD,
                       has=", which the plan reads")
        self.asks_each([f"$env:SQL_READ_CONNECTIONS = 'x'; {run}", f"$env:Path += ';C:{BS}x'; {run}",
                        f"[Environment]::SetEnvironmentVariable('LPRUN9', 'x'); {run}",
                        f"Remove-Item Env:{BS}LPRUN9; {run}", f"cmd /c 'set LPRUN9=x && {run}'"],
                       tools=["PowerShell"], starts=UNREAD, has=", which the plan reads")
        self.asks_each([f"SQL_READ_CONNECTIONS=/x/c.xml {run}"], tools=["Bash"],
                       equals=unread("the line changes SQL_READ_CONNECTIONS, which the plan reads"))
        self.asks_each([f"SQL_READ_WRITABLE_OK=Sales {run}", f"export SQL_READ_WRITABLE_OK='*'; {run}"],
                       tools=["Bash"], starts=UNREAD, has="SQL_READ_WRITABLE_OK, which the plan reads")
        self.asks_each([f"$env:SQL_READ_WRITABLE_OK = '*'; {run}"], tools=["PowerShell"], starts=UNREAD,
                       has="SQL_READ_WRITABLE_OK, which the plan reads")
        self.asks_each([f"PYTHONPATH=/x {run}", f"export PYTHONHOME=/x; {run}"], tools=["Bash"], starts=UNREAD,
                       has="which changes how python runs read.py")
        self.asks_each([f"$env:PYTHONPATH = 'x'; {run}"], tools=["PowerShell"], starts=UNREAD,
                       has="the line changes PYTHONPATH, which changes how python runs read.py")

    def test_arguments_that_arrive_from_elsewhere(self):
        q = self.p("q.sql")
        self.asks_each([f"echo {q} | xargs {RUN} query --cx Sales --sql"], tools=["Bash"],
                       equals=unread("xargs or parallel supplies read.py's arguments"))
        self.asks_each([f"find {self.dir.as_posix()} -name q.sql -exec {RUN} query --cx Sales --sql {{}} {BS};"],
                       tools=["Bash"], starts=UNREAD)
        self.asks_each([f"Get-ChildItem *.sql | ForEach-Object {{ {RUN} query --cx Sales --sql $_ }}"],
                       tools=["PowerShell"], starts=UNREAD)

    def test_text_that_a_shell_in_the_line_runs(self):
        q = self.p("q.sql")
        self.asks_each([f"echo python3 {READ_PY} query --cx Sales --sql {q} | bash",
                        f"R={READ_PY}; echo python3 $R query --cx Sales --sql {q} | sh"], tools=["Bash"],
                       starts=UNREAD, has="text that a shell in the line may run")
        self.asks_each([f"echo python3 {READ_PY} query --cx Sales --sql {q} | iex"], tools=["PowerShell"],
                       starts=UNREAD, has="text that a shell in the line may run")

    def test_a_run_nested_deeper_than_the_gate_reads(self):
        run = f"python3 {READ_PY} query --cx Sales --sql {self.p('q.sql')}"
        for _ in range(3):
            run = f"bash -c {shlex.quote(run)}"
        self.asks_each([run], tools=["Bash"], equals=Q)
        self.asks_each([f"bash -c {shlex.quote(run)}"], tools=["Bash"], starts=UNREAD, has="nested deeper")

    def test_an_alias_that_adds_arguments(self):
        self.asks_each([f"alias rq='{RUN} query'\nrq --cx Sales --sql {self.p('q.sql')}"], tools=["Bash"],
                       starts=UNREAD, has="an alias, which adds the arguments it is given where it is used")

    def test_code_that_runs_before_read_py(self):
        q = self.p("q.sql")
        self.asks_each([f"python3 -c 'import sys; print(sys.argv)' {READ_PY} query --cx Sales --sql {q}",
                        f"bash -c 'echo hi' {READ_PY} query --cx Sales --sql {q}",
                        f"bash -c 'echo hi' {READ_PY} check --cx Sales"], starts=UNREAD,
                       has="code the gate cannot follow, before read.py")
        self.asks_each([f"python3 -c \"exec(open('{READ_PY}').read())\" query --cx Sales --sql {q}"], starts=UNREAD,
                       has="code the gate cannot follow")

    def test_hook_input_that_is_not_json(self):
        for stdin in (b"", b"not json", b"[1, 2]", b"{\"tool_name\": \"Bash\", \"tool_input\": "):
            with self.subTest(stdin=stdin):
                got = self.decision(self.run_gate(stdin))
                self.assertEqual(got, ("ask", unread("the hook's input is not a JSON object")))

    def test_a_command_that_is_not_text(self):
        for command in ([RUN, "query", "--cx", "Sales"], 42):
            with self.subTest(command=command):
                got = self.decision(self.run_gate(self.hook(command)))
                self.assertEqual(got, ("ask", unread("the command is not text")))

    def test_both_findings_in_one_reason(self):
        q = self.p("q.sql")
        reason = self.asks(f"{RUN} query --cx Sales --sql {q} && {RUN} check --cx $CX")
        self.assertEqual(reason, unread("$CX: the gate cannot expand it") + "\n" + Q)


class NobodyToAsk(GateCase):
    """In a mode with nobody to ask, every ask becomes a deny with the same reason."""

    def test_bypass_permissions_and_dont_ask_deny(self):
        q = self.p("q.sql")
        for mode in ("bypassPermissions", "dontAsk"):
            for tool in BOTH:
                with self.subTest(mode=mode, tool=tool):
                    self.assertEqual(self.decide(f"{RUN} query --cx Sales --sql {q}", tool, mode=mode), ("deny", Q))
                    got = self.decide(f"{RUN} query --cx Sales --sql $F", tool, mode=mode)
                    self.assertEqual(got, ("deny", unread("$F: the gate cannot expand it")))
                    self.assertIsNone(self.decide(f"{RUN} plan --cx Sales --sql {q}", tool, mode=mode))

    def test_the_other_modes_ask(self):
        for mode in ("default", "acceptEdits", "plan", "auto", None, "someFutureMode", 7):
            with self.subTest(mode=mode):
                self.assertEqual(self.decide(f"{RUN} check --cx Dev", mode=mode), ("ask", CHECK_DEV))


class Output(GateCase):
    """What the gate prints survives any console code page, and shows nothing that can hide."""

    def test_ascii_whatever_the_console_takes(self):
        command = f"{RUN} query --cx Sales --sql {self.p('q.sql')}"
        for encoding in ("cp1252", "ascii", "utf-8"):
            with self.subTest(encoding=encoding):
                env = {**self.env, "PYTHONIOENCODING": encoding}
                self.assertEqual(self.decide(command, env=env), ("ask", Q))

    def test_input_that_is_not_utf8(self):
        stdin = self.hook(f"{RUN} query --cx Sales --sql {self.p('q.sql')} #\u2013")
        stdin = stdin.replace((BS + "u2013").encode(), bytes([0x96]))
        self.assertEqual(self.decision(self.run_gate(stdin)), ("ask", Q))

    def test_a_name_from_the_command_is_shown_not_sent_to_the_terminal(self):
        reason = self.asks(f"{RUN} check --cx 'Sa\u202eles{chr(7)}'")
        self.assertTrue(reason.startswith(f"sql-read: will not run (exit 3): "), reason)
        self.assertIn(f'login check on "Sa{BS}u202eles{BS}x07"', reason)
        self.assertNotIn(chr(7), reason)

    def test_a_long_line_reads_well_inside_the_hooks_timeout(self):
        run = f"{RUN} query --cx Sales --sql {self.p('q.sql')}"
        lines = {"Bash": f"python3 -c \"{'x' * 300_000} read.py\" && {run}",
                 "PowerShell": "echo " + "@'x' " * 40_000 + f"; {run}"}
        for tool, command in lines.items():
            with self.subTest(tool=tool):
                started = time.monotonic()
                self.assertEqual(self.asks(command, tool), Q)
                self.assertLess(time.monotonic() - started, 8)

    def test_a_long_document_that_names_many_runs_reads_inside_the_hooks_timeout(self):
        line = ("Run `lprun9 -cxname=Sales script.linq` or `python3 read.py query --cx X --sql q.sql`; see "
                "docs/notes.linq and $(nothing).\n")
        body = line * (100_000 // len(line))
        for tool, command in [("PowerShell", f"Set-Content -Path docs/README.md -Value @'\n{body}'@"),
                              ("PowerShell", f'Set-Content -Path docs/README.md -Value @"\n{body}"@'),
                              ("Bash", f"cat > docs/README.md <<EOF\n{body}EOF")]:
            with self.subTest(tool=tool, opens=command[42:46]):
                started = time.monotonic()
                self.decide(command, tool)
                self.assertLess(time.monotonic() - started, 8)


class FastPath(GateCase):
    """Any other tool, or a command that does not name read.py, costs json, re and sys alone; read.py itself loads
    only for a query or a check."""

    def test_the_module_imports_only_json_re_and_sys(self):
        tree = ast.parse(GATE.read_text(encoding="utf-8"))
        imported = set()
        for node in tree.body:
            if isinstance(node, ast.Import):
                imported |= {alias.name for alias in node.names}
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module)
        self.assertEqual(imported, {"json", "re", "sys"})

    def test_shlex_and_read_py_load_only_when_needed(self):
        q = self.p("q.sql")
        other = json.loads(self.hook("", "Read"))
        other["tool_input"] = {"file_path": READ_PY}
        # shlex takes the escapes off a word; a word with none only loses its quotes
        for stdin, parses, plans in [(json.dumps(other).encode(), False, False), (self.hook("npm test"), False, False),
                                     (self.hook("npm test", "PowerShell"), False, False),
                                     (self.hook(f"{RUN} plan --cx Sales --sql {q}"), False, False),
                                     (self.hook(f"{RUN} plan --cx Sa\\les --sql {q}"), True, False),
                                     (self.hook(f"{RUN} query --cx Sa\\les --sql {q}"), True, True)]:
            with self.subTest(stdin=stdin[:160]):
                proc = self.run_gate(stdin, args=("-X", "importtime"))
                self.assertEqual(proc.returncode, 0, proc.stderr)
                log = proc.stderr.decode("utf-8", "replace")
                self.assertEqual(re.search(r"\|\s*shlex\s*$", log, re.M) is not None, parses)
                self.assertEqual(re.search(r"\|\s*backend\s*$", log, re.M) is not None, plans)


def matches(rules, tool, command):
    """Whether a permission rule or an if pattern matches the command as Claude Code matches it: against each
    subcommand, with * standing for any text; a PowerShell pattern in any case, a Bash one in the case written."""
    for rule in rules:
        name, pattern = re.fullmatch(r"(\w+)\((.*)\)", rule).groups()
        for part in re.split(r"&&|\|\||;|\||&|\n", command) if name == tool else ():
            part = part.strip()
            if (fnmatch.fnmatchcase(part.lower(), pattern.lower()) if tool == "PowerShell"
                    else fnmatch.fnmatchcase(part, pattern)):
                return True
    return False


class HooksJson(unittest.TestCase):
    """hooks.json, pinned: claude plugin validate --strict passes an unknown field or a wrong value, and a malformed
    handler switches its whole matcher group off at run time."""

    # Any command that names read.py, or runs a module named read, starts the gate, which decides; plan and
    # connections pass in silence
    # A Bash pattern matches the case written and Windows does not care how a file is cased, so the one for read.py is
    # a short fragment of it, d.p, in each of its four casings.
    IFS = ["Bash(*d.p*)", "Bash(*D.p*)", "Bash(*d.P*)", "Bash(*D.P*)", "Bash(*m read *)", "Bash(*mread *)",
           "PowerShell(*read.py*)", "PowerShell(*m read *)", "PowerShell(*mread *)"]

    def setUp(self):
        self.config = json.loads(HOOKS.read_text(encoding="utf-8"))

    def fires(self, tool, command):
        return matches(self.IFS, tool, command)

    def test_one_gate_per_if_pattern_that_blocks_on_failure(self):
        self.assertEqual(set(self.config), {"description", "hooks"})
        self.assertEqual(list(self.config["hooks"]), ["PreToolUse"])
        groups = self.config["hooks"]["PreToolUse"]
        self.assertEqual(len(groups), 1)
        self.assertEqual(set(groups[0]), {"matcher", "hooks"})
        self.assertEqual(groups[0]["matcher"], "Bash|PowerShell")
        handlers = groups[0]["hooks"]
        self.assertEqual([h.get("if") for h in handlers], self.IFS)
        for handler in handlers:
            with self.subTest(handler=handler.get("if")):
                self.assertEqual(handler, {"type": "command", "command": "python3",
                                           "args": ["${CLAUDE_PLUGIN_ROOT}/hooks/gate.py"], "if": handler["if"],
                                           "timeout": 10, "onFailure": "block"})
                script = Path(handler["args"][0].replace("${CLAUDE_PLUGIN_ROOT}", str(PLUGIN)))
                self.assertEqual(script.resolve(), GATE.resolve())
                self.assertTrue(script.is_file())

    def test_the_patterns_let_through_what_the_gate_judges(self):
        for tool, command in [("Bash", 'python3 "C:/x/scripts/read.py" query --cx Sales --sql "C:/x/q.sql"'),
                              ("Bash", "python3 /x/read.py check --cx Sales"),
                              ("Bash", "cd /x && python3 read.py query --cx Sales --sql q.sql"),
                              ("Bash", "python3 read.py plan --cx Sales --sql query.sql"),
                              ("Bash", "python3 '/x/read.py' 'query' --cx Sales --sql q.sql"),
                              # NTFS keeps no case, and a Bash pattern does: every casing of the name must fire.
                              ("Bash", "python3 /x/READ.PY query --cx Sales --sql q.sql"),
                              ("Bash", "python3 /x/Read.py query --cx Sales --sql q.sql"),
                              ("Bash", "python3 /x/read.PY check --cx Sales"),
                              ("Bash", "python3 /x/ReAd.Py check --cx Sales"),
                              ("Bash", "cd /x/scripts && python3 -m read query --cx Sales --sql /x/q.sql"),
                              ("Bash", "py -3 -mread check --cx Sales"),
                              ("Bash", 'cp "/x/read.py" r.py && python3 r.py query --cx Sales --sql q.sql'),
                              ("PowerShell", "python3 'C:\\x\\read.py' query --cx Sales --sql 'C:\\x\\q.sql'"),
                              ("PowerShell", "& python3 C:\\x\\READ.PY Check --cx Sales"),
                              ("PowerShell", "Start-Process python3 -ArgumentList 'C:/x/read.py','query','--cx',"
                                             "'Sales' -Wait -NoNewWindow"),
                              ("PowerShell", "python -m read query --cx Sales --sql C:/x/q.sql")]:
            with self.subTest(tool=tool, command=command):
                self.assertTrue(self.fires(tool, command))
        for tool, command in [("Bash", "npm test"), ("PowerShell", "npm test"),
                              ("Bash", "lprun9 -cxname=Sales C:/x/fb.cs"), ("Bash", "rm readme.md"),
                              ("Bash", "python3 -m unittest discover -s skills/sql-read/tests"),
                              ("Bash", "git commit -m 'read the docs'"), ("PowerShell", "Get-Content README.md")]:
            with self.subTest(tool=tool, command=command):
                self.assertFalse(self.fires(tool, command))


class ReadmeAskRules(unittest.TestCase):
    """The ask rules the README's setup recommends prompt for every way to run query or check that the gate itself
    catches, so that they hold where the hook cannot (the plugin disabled, a mod approving past it), and leave plan
    and connections alone, so a read prompts once."""

    def setUp(self):
        text = README.read_text(encoding="utf-8")
        block = re.search(r"```json\n(\{\s*\"permissions\".*?)```", text, re.S)
        self.assertIsNotNone(block, "the README's setup has no permissions block")
        self.rules = json.loads(block.group(1))["permissions"]["ask"]

    def test_every_form_the_gate_runs_on_prompts(self):
        tail = "--cx Sales --sql q.sql"
        for tool, command in [("Bash", f'python3 "C:/x/scripts/read.py" query {tail}'),
                              ("Bash", "python3 /x/read.py check --cx Sales"),
                              ("Bash", f"python3 /x/READ.PY query {tail}"),
                              ("Bash", f"python3 /x/Read.py query {tail}"),
                              ("Bash", "python3 /x/READ.PY check --cx Sales"),
                              ("Bash", f"cd /x && python3 read.py query {tail}"),
                              ("Bash", f"python3 '/x/read.py' 'query' {tail}"),
                              ("Bash", 'python3 read.py "check" --cx Sales'),
                              ("Bash", f"cd /x/scripts && python3 -m read query {tail}"),
                              ("Bash", f"PYTHONPATH=/x/scripts python3 -m read 'query' {tail}"),
                              ("Bash", "py -3 -mread check --cx Sales"),
                              ("Bash", f"python3 -I -m read query {tail}"),
                              ("Bash", "lprun9 -cxname=Sales C:/x/fb.cs"),
                              ("PowerShell", f"python3 'C:{BS}x{BS}read.py' query {tail}"),
                              ("PowerShell", f"& python3 C:{BS}x{BS}READ.PY Check --cx Sales"),
                              ("PowerShell", "Start-Process python3 -ArgumentList 'C:/x/read.py','query','--cx',"
                                             "'Sales' -Wait -NoNewWindow"),
                              ("PowerShell", f"python -m read query {tail}"),
                              ("PowerShell", "py -3 -mread 'check' --cx Sales"),
                              ("PowerShell", f"LPRun9.exe -cxname=Sales C:{BS}x{BS}fb.cs")]:
            with self.subTest(tool=tool, command=command):
                self.assertTrue(matches(self.rules, tool, command))

    def test_plan_and_connections_do_not_prompt(self):
        for tool in BOTH:
            for command in ["python3 /x/read.py plan --cx Sales --sql recent-orders.sql",
                            "python3 '/x/read.py' plan --cx Sales", "python3 /x/read.py connections --json",
                            "python3 -m read plan --cx Sales --sql recent-orders.sql", "py -3 -mread connections",
                            "npm test", "git commit -m 'read the docs'"]:
                with self.subTest(tool=tool, command=command):
                    self.assertFalse(matches(self.rules, tool, command))


class NetworkPaths(unittest.TestCase):
    """A network path the command names is never opened, or even looked at, before the command is approved: Windows
    would sign in to the host it names as the user. The run asks, unplanned, saying why. Run in this process, with
    os.stat replaced, so that no test touches a share either."""

    @classmethod
    def setUpClass(cls):
        cls.gate = load_gate()

    def test_the_gate_asks_without_touching_one(self):
        touched = []

        def stat(path, *args, **kwargs):
            touched.append(os.fspath(path))
            raise FileNotFoundError(path)

        share = "//127.0.0.1/no-such-share"
        with tempfile.TemporaryDirectory() as cwd, mock.patch("os.stat", stat):
            Path(cwd, "q.sql").write_text("SELECT 1 AS x", encoding="utf-8")
            for command, shell, label in [
                    (f"{RUN} query --cx Sales --sql {share}/q.sql", "bash", f"{share}/q.sql"),
                    (f"{RUN} check --cx Sales --connections \\\\127.0.0.1\\no-such-share\\cx.xml", "powershell",
                     "\\\\127.0.0.1\\no-such-share\\cx.xml"),
                    (f"{RUN} query --cx Sales --sql q.sql --lprun {share}/LPRun9.exe", "powershell",
                     f"{share}/LPRun9.exe")]:
                with self.subTest(command=command):
                    found = self.gate.judge(command, shell, cwd)
                    self.assertEqual(found.jobs, [])
                    self.assertIn(f"{label}: {NETWORK}", found.unread)
            reason = self.gate.decide(f"{RUN} query --cx Sales --sql {share}/q.sql", "bash", cwd)
            self.assertEqual(reason, unread(f"{share}/q.sql: {NETWORK}"))
        self.assertEqual([path for path in touched if re.match(r"[\\/]{2}", path)], [])


class ReadingACommandLine(unittest.TestCase):
    """The command-line reading this gate shares with linqpad's, and the gate's own parts, in this process."""

    @classmethod
    def setUpClass(cls):
        cls.gate = load_gate()

    def test_the_shared_section_stands_on_its_own(self):
        """linqpad's gate keeps a copy of this section, so every name the section uses is defined in it, or is json,
        re, sys or a builtin."""
        import builtins, symtable
        text = GATE.read_text(encoding="utf-8")
        section = text[text.index(SHARED[0]):text.index(SHARED[1])]
        top = symtable.symtable(section, "shared", "exec")
        allowed = {s.get_name() for s in top.get_symbols() if s.is_assigned() or s.is_imported()}
        allowed |= set(dir(builtins)) | {"json", "re", "sys"}
        used, tables = set(), [top]
        while tables:
            table = tables.pop()
            tables += table.get_children()
            used |= {s.get_name() for s in table.get_symbols()
                     if s.is_referenced() and (s.is_global() or (table is top and not s.is_assigned()))}
        self.assertEqual(used - allowed, set())
        self.assertGreater(len(allowed & used), 30)

    @unittest.skipUnless(LINQPAD_GATE.is_file(), "linqpad's gate is not beside this plugin")
    def test_the_shared_section_is_linqpads_to_the_letter(self):
        """Both gates read a command line the same way: a fix to one goes in the other."""
        def code(path, end):
            text = path.read_text(encoding="utf-8")
            section = text[text.index(SHARED[0]):text.index(end)]
            return section[section.index("\nSHELLS = "):]

        self.assertEqual(code(GATE, SHARED[1]), code(LINQPAD_GATE, "# --- The lprun9 gate"))

    def test_what_names_read_py(self):
        for word in ("read.py", "C:\\x\\READ.PY", "/c/x/read.py", "read.py::$DATA", "read.py. ", "re^ad.py"):
            with self.subTest(word=word):
                self.assertTrue(self.gate.is_read_py(word))
        for word in ("reader.py", "xread.py", "read.pyc", "read.py.bak", "read", "python3 C:/x/read.py query",
                     "C:/x/read.py/q.sql", "--sql=read.py"):
            with self.subTest(word=word):
                self.assertFalse(self.gate.is_read_py(word))

    def test_planning_that_overruns_its_time_asks(self):
        def slow():
            time.sleep(3)
            raise RuntimeError("too late")

        with tempfile.TemporaryDirectory() as folder:
            Path(folder, "q.sql").write_text("SELECT 1 AS x", encoding="utf-8")
            with mock.patch.object(self.gate, "load_read", slow), mock.patch.object(self.gate, "PLAN_BUDGET", 0.5):
                started = time.monotonic()
                reason = self.gate.decide(f"{RUN} query --cx Sales --sql q.sql", "bash", folder)
            self.assertLess(time.monotonic() - started, 2)
        self.assertEqual(reason, unread("read.py took too long to make the plan"))

    def test_a_read_py_that_does_not_load_asks(self):
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, "q.sql").write_text("SELECT 1 AS x", encoding="utf-8")
            with mock.patch.object(self.gate, "load_read", side_effect=SyntaxError("broken")):
                reason = self.gate.decide(f"{RUN} query --cx Sales --sql q.sql", "bash", folder)
        self.assertEqual(reason, unread("the gate could not load read.py (SyntaxError)"))

    def test_it_loads_the_read_py_beside_it(self):
        module = self.gate.load_read()
        self.assertEqual(Path(module.__file__).resolve(), (SCRIPTS / "read.py").resolve())
        self.assertTrue(callable(module.plan_for_command) and callable(module.reason))


if __name__ == "__main__":
    unittest.main()
