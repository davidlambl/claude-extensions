"""The lprun9 gate's decisions, run with: python3 -m unittest discover -s skills/linqpad/tests

Each test pipes the hook's JSON into hooks/gate.py as a subprocess, as Claude Code runs it, and reads what it prints:
nothing for silence, or one PreToolUse decision. Most run the same command as the Bash tool and as the PowerShell tool.
The scripts the commands name are written to a temporary folder, which is also the hook's cwd; the gate itself runs
from an empty folder, so a relative path can only resolve against the cwd the hook names.

Nothing here runs lprun9, reads LINQPad's own connection list, or needs Windows, apart from the tests marked
Windows-only. The secrets in the scripts are obviously fake.
"""
import ast, fnmatch, importlib.util, itertools, json, os, re, subprocess, sys, tempfile, time, unittest
from pathlib import Path
from unittest import mock

PLUGIN = Path(__file__).resolve().parents[1]
GATE = PLUGIN / "hooks" / "gate.py"
HOOKS = PLUGIN / "hooks" / "hooks.json"
BOTH = ("Bash", "PowerShell")
PASSWORD = "FAKE-pw-7f3a9c2e"
CX_TAIL = " — not through sql-read: no shape check, no row cap, no login probe"
UNREAD = "linqpad: the gate could not read this command; approve only if you have read what it runs"
NETWORK = "a network or device path, which the gate does not open before you approve"
SHARED =("# --- Reading a command line", "# --- The lprun9 gate")  # the section sql-read's gate copies

CONNECTION = f"""  <Connection>
    <ID>0f3a5c1e-0000-4000-8000-000000000001</ID>
    <NamingServiceVersion>2</NamingServiceVersion>
    <Persist>true</Persist>
    <Server>sql-fake.example.test</Server>
    <SqlSecurity>true</SqlSecurity>
    <UserName>reader_fake</UserName>
    <Password>{PASSWORD}</Password>
    <Database>Orders</Database>
    <DisplayName>FakeProd</DisplayName>
    <IsProduction>true</IsProduction>
  </Connection>
"""
CONN_LINQ = f'<Query Kind="SQL">\n{CONNECTION}</Query>\n\nSELECT TOP 5 * FROM dbo.Orders ORDER BY Id DESC\n'
NAMESPACES = "".join(f"  <Namespace>Fake.Namespace{i:04}</Namespace>\n" for i in range(300))  # 12 KB of header
SCRIPTS = {
    "conn.linq": CONN_LINQ,
    "conn.txt": CONN_LINQ,  # a header lprun9 may read whatever the file is called
    "cxstring.linq": f"""<Query Kind="Statements">
  <Connection>
    <ID>0f3a5c1e-0000-4000-8000-000000000002</ID>
    <Persist>true</Persist>
    <CustomCxString>Server=sql-fake.example.test;User Id=reader_fake;Password={PASSWORD}</CustomCxString>
  </Connection>
</Query>

Console.WriteLine(1);
""",
    "odd-server.linq": f"""<Query Kind="SQL">
  <Connection>
    <Server>sql-fake.example.test;Password={PASSWORD}</Server>
    <Database>Orders</Database>
  </Connection>
</Query>

SELECT 1
""",
    "bidi.linq": '<Query Kind="SQL">\n  <Connection>\n    <Server>sql\u202efake</Server>\n'
                 '    <DisplayName>Fake\u200bProd</DisplayName>\n  </Connection>\n</Query>\n\nSELECT 1\n',
    "lower.linq": '<query kind="SQL">\n  <connection>\n    <server>sql-fake.example.test</server>\n  </connection>\n'
                  '</query>\n\nSELECT 1\n',
    "namespaced.linq": '<Query Kind="SQL" xmlns:lp="urn:fake"><lp:Connection><Server>sql-fake.example.test</Server>'
                       '</lp:Connection></Query>\n\nSELECT 1\n',
    "attribute.linq": '<Query Kind="SQL" Connection="FakeProd" />\n\nSELECT 1\n',
    "prolog.linq": '<?xml version="1.0" encoding="utf-8"?>\n' + CONN_LINQ,
    "deep.linq": '<Query Kind="SQL">\n' + NAMESPACES + CONNECTION + "</Query>\n\nSELECT 1\n",
    # a </Query> or /> that does not end the header, before the connection
    "comment-end.linq": f'<Query Kind="SQL"><!-- </Query> -->\n{CONNECTION}</Query>\n\nSELECT 1\n',
    "attribute-end.linq": f'<Query Kind="SQL" Note="/>">\n{CONNECTION}</Query>\n\nSELECT 1\n',
    "deep-comment.linq": '<Query Kind="SQL"><!-- </Query> -->\n' + NAMESPACES + CONNECTION + "</Query>\n\nSELECT 1\n",
    "deep-cdata.linq": '<Query Kind="SQL"><Namespace><![CDATA[</Query>]]></Namespace>\n' + NAMESPACES + CONNECTION
                       + "</Query>\n\nSELECT 1\n",
    "deep-attribute.linq": '<Query Kind="SQL" Note="/>">\n' + NAMESPACES + CONNECTION + "</Query>\n\nSELECT 1\n",
    # an entity that writes the connection where the text shows none
    "entity.linq": '<?xml version="1.0"?>\n<!DOCTYPE Query [<!ENTITY cx "&#60;Connection&#62;&#60;Server&#62;'
                   'sql-fake.example.test&#60;/Server&#62;&#60;/Connection&#62;">]>\n<Query Kind="SQL">&cx;</Query>\n\n'
                   "SELECT 1\n",
    "code-mentions.linq": '<Query Kind="Statements" />\n\nvar connection = "Connection=none";\n'
                          "Console.WriteLine(connection);\n",
    "plain.linq": '<Query Kind="Expression" />\n\n6 * 7\n',
    "statements.linq": '<Query Kind="Statements">\n  <Namespace>System.Text.Json</Namespace>\n</Query>\n\n'
                       'Console.WriteLine(JsonSerializer.Serialize(new { n = 6 * 7 }));\n',
    "bare.linq": "6 * 7\n",
    # a file-based app whose code holds "<Connection" as a generic argument, not a header
    "fb.cs": 'var all = new List<Connection> { new("a") };\nConsole.WriteLine(all.Count);\n\n'
             "record Connection(string Name);\n",
}
ENCODED = {
    "bom.linq": CONN_LINQ.encode("utf-8-sig"),
    "utf16.linq": CONN_LINQ.encode("utf-16"),
    "utf16-nobom.linq": CONN_LINQ.encode("utf-16-le"),
}


def load_gate():
    """gate.py as a module, for the tests of the command-line reading it shares with sql-read's gate."""
    spec = importlib.util.spec_from_file_location("linqpad_gate", GATE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class GateCase(unittest.TestCase):
    """A temporary folder of scripts, the hook's cwd, and the helpers that run the gate on a command."""

    @classmethod
    def setUpClass(cls):
        cls._folder = tempfile.TemporaryDirectory()
        cls.dir = Path(cls._folder.name).resolve()
        cls._empty = tempfile.TemporaryDirectory()  # where the gate itself runs
        for name, text in SCRIPTS.items():
            (cls.dir / name).write_text(text, encoding="utf-8", newline="\n")
        for name, data in ENCODED.items():
            (cls.dir / name).write_bytes(data)
        (cls.dir / "dir.linq").mkdir()
        (cls.dir / "sub").mkdir()
        (cls.dir / "space dir").mkdir()
        (cls.dir / "space dir" / "conn.linq").write_text(CONN_LINQ, encoding="utf-8")
        (cls.dir / "space dir" / "conn.txt").write_text(CONN_LINQ, encoding="utf-8")
        (cls.dir / "space dir" / "plain.linq").write_text(SCRIPTS["plain.linq"], encoding="utf-8")
        (cls.dir / "space").write_text(SCRIPTS["bare.linq"], encoding="utf-8")  # a decoy at the path's first piece

    @classmethod
    def tearDownClass(cls):
        cls._folder.cleanup()
        cls._empty.cleanup()

    def p(self, name):
        """A script's absolute path, with forward slashes, as Claude types it in either shell."""
        return (self.dir / name).as_posix()

    def run_gate(self, stdin, env=None, args=()):
        return subprocess.run([sys.executable, *args, str(GATE)], input=stdin, capture_output=True, timeout=60,
                              env=env, cwd=self._empty.name)

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
        self.assertTrue(output["permissionDecisionReason"].startswith("linqpad: "), output)
        return output["permissionDecision"], output["permissionDecisionReason"]

    def decide(self, command, tool="Bash", **kw):
        return self.decision(self.run_gate(self.hook(command, tool, **kw)))

    def asks(self, command, tool="Bash", **kw):
        got = self.decide(command, tool, **kw)
        self.assertIsNotNone(got, f"{tool} {command!r} was let through silently")
        self.assertEqual(got[0], "ask", got)
        return got[1]

    def silent(self, command, tool="Bash", **kw):
        got = self.decide(command, tool, **kw)
        self.assertIsNone(got, f"{tool} {command!r} was stopped")

    def asks_each(self, commands, tools=BOTH, starts=None, equals=None, **kw):
        for tool in tools:
            for command in commands:
                with self.subTest(tool=tool, command=command):
                    reason = self.asks(command, tool, **kw)
                    if starts is not None:
                        self.assertTrue(reason.startswith(starts), reason)
                    if equals is not None:
                        self.assertEqual(reason, equals)

    def silent_each(self, commands, tools=BOTH, **kw):
        for tool in tools:
            for command in commands:
                with self.subTest(tool=tool, command=command):
                    self.silent(command, tool, **kw)


class SavedConnectionByName(GateCase):
    """lprun9 with -cxname= asks, naming the connection."""

    def setUp(self):
        self.run9 = f"lprun9 -cxname=Sales {self.p('fb.cs')}"

    def test_asks_and_names_the_connection(self):
        self.asks_each([self.run9], equals='linqpad: lprun9 against saved connection "Sales"' + CX_TAIL)

    def test_the_option_in_any_case_or_form(self):
        fb = self.p("fb.cs")
        options = ["-cxname=Sales", "-CXNAME=Sales", "-CxName=Sales", "--cxname=Sales", "-cxname:Sales", "/cxname=Sales",
                   "-cxname Sales"]
        self.asks_each([f"lprun9 {o} {fb}" for o in options],
                       equals='linqpad: lprun9 against saved connection "Sales"' + CX_TAIL)

    def test_a_quoted_name_with_spaces(self):
        fb = self.p("fb.cs")
        self.asks_each([f'lprun9 -cxname="RF Prod" {fb}', f"lprun9 '-cxname=RF Prod' {fb}",
                        f"lprun9 -cxname='RF Prod' {fb}", f'lprun9 "-cxname=RF Prod" {fb}'],
                       equals='linqpad: lprun9 against saved connection "RF Prod"' + CX_TAIL)

    def test_a_quoted_name_with_spaces_behind_a_wrapper(self):
        """timeout, env and nohup hand on the words the shell split, quotes gone: the name stays whole."""
        fb = self.p("fb.cs")
        self.asks_each([f'timeout 120 lprun9 -cxname="RF Prod" {fb}', f"timeout 60 lprun9 '-cxname=RF Prod' {fb}",
                        f'env A=1 nohup lprun9 "-cxname=RF Prod" {fb}', f'timeout 120 lprun9 -cxname "RF Prod" {fb}'],
                       equals='linqpad: lprun9 against saved connection "RF Prod"' + CX_TAIL)
        # eval reads its words again as a line, so lprun9 may get -cxname=RF and a script called Prod: both readings
        reason = self.asks(f'eval lprun9 "-cxname=RF Prod" {fb}')
        self.assertIn('saved connection "RF Prod"', reason)
        self.assertIn('saved connection "RF"', reason)

    def test_every_name_lprun9_goes_by(self):
        fb = self.p("fb.cs")
        runners = ["lprun9", "lprun9.exe", "LPRun9.exe", "LPRUN9.EXE", "LPRun9-x86.exe", "lprun9.cmd", "./lprun9",
                   '"C:/Program Files/LINQPad9/LPRun9.exe"', "C:/PROGRA~1/LINQPad9/LPRun9.exe", '"LPRun9.exe."',
                   '"/c/Program Files/LINQPad9/LPRun9.exe"']
        self.asks_each([f"{r} -cxname=Sales {fb}" for r in runners], starts='linqpad: lprun9 against saved connection')

    def test_powershell_call_operators(self):
        fb = self.p("fb.cs")
        commands = [f'& "C:\\Program Files\\LINQPad9\\LPRun9.exe" -cxname=Sales {fb}',
                    f"& 'C:\\Program Files\\LINQPad9\\LPRun9.exe' -cxname=Sales {fb}",
                    f".\\LPRun9.exe -cxname=Sales {fb}", f"&lprun9 -cxname=Sales {fb}", f". lprun9 -cxname=Sales {fb}"]
        self.asks_each(commands, tools=["PowerShell"], starts='linqpad: lprun9 against saved connection "Sales"')

    def test_after_leading_assignments(self):
        fb = self.p("fb.cs")
        self.asks_each([f'FOO=1 BAR="a b" lprun9 -cxname=Sales {fb}'], tools=["Bash"])
        self.asks_each([f"$x = lprun9 -cxname=Sales {fb}", f"$x=lprun9 -cxname=Sales {fb}",
                        f"[string]$x = lprun9 -cxname=Sales {fb}"], tools=["PowerShell"])

    def test_every_command_in_the_line(self):
        x = self.run9
        bash = [f"echo hi && {x}", f"echo hi || {x}", f"echo hi; {x}", f"echo hi | {x}", f"echo hi\n{x}",
                f"echo hi\r\n{x}", f"({x})", f"{{ {x}; }}", f"sleep 1 & {x}", f"echo hi |& {x}",
                f"if true; then {x}; fi", f"! {x}", f"for i in 1; do {x}; done", f"case a in a) {x};; esac"]
        powershell = [f"echo hi; {x}", f"echo hi | {x}", f"echo hi\n{x}", f"echo hi && {x}", f"echo hi || {x}",
                      f"if ($true) {{ {x} }}", f"foreach ($i in 1) {{ {x} }}", f"1 | ForEach-Object {{ {x} }}",
                      f"try {{ {x} }} catch {{ }}", f"Measure-Command {{ {x} }}", f"echo hi & {x}"]
        self.asks_each(bash, tools=["Bash"], starts='linqpad: lprun9 against saved connection "Sales"')
        self.asks_each(powershell, tools=["PowerShell"], starts='linqpad: lprun9 against saved connection "Sales"')

    def test_inside_a_substitution(self):
        x = self.run9
        bash = [f"echo $({x})", f"echo `{x}`", f'echo "$({x})"', f'echo "`{x}`"', f"cat <({x})", f"v=$({x})",
                f"echo $(echo $({x}))"]
        powershell = [f"echo $({x})", f'echo "$({x})"', f"$v = $({x})", f"@({x})", f"$v = ({x})"]
        self.asks_each(bash, tools=["Bash"], starts='linqpad: lprun9 against saved connection "Sales"')
        self.asks_each(powershell, tools=["PowerShell"], starts='linqpad: lprun9 against saved connection "Sales"')

    def test_through_a_wrapper_or_another_shell(self):
        x, fb = self.run9, self.p("fb.cs")
        bash = [f"timeout 120 {x}", f"timeout -k 5 60 {x}", f"env -i PATH=/usr/bin {x}", f"nohup {x}", f"time {x}",
                f"exec {x}", f"command {x}", f"nice -n 5 {x}", f"sudo -u someone {x}", f"winpty {x}",
                f'cmd //c "{x}"', f"cmd /c {x}", f'bash -c "{x}"', f"sh -c '{x}'", f'pwsh -c "{x}"',
                f'powershell -Command "{x}"', f"wsl {x}", f"eval '{x}'", f'start "" {x}',
                f"timeout 120 env A=1 nohup {x}"]
        powershell = [f"Start-Process lprun9 -ArgumentList '-cxname=Sales','{fb}'",
                      f'Start-Process -FilePath lprun9 -ArgumentList "-cxname=Sales {fb}"',
                      f"saps lprun9 '-cxname=Sales {fb}' -Wait", f"start lprun9 '-cxname=Sales {fb}'",
                      f"Invoke-Expression '{x}'", f'iex "{x}"', f"Start-Job {{ {x} }}", f"cmd /c {x}",
                      f'cmd /c "{x}"', f"& {{ {x} }}", f'pwsh -c "{x}"', f"Invoke-Command -ScriptBlock {{ {x} }}"]
        self.asks_each(bash, tools=["Bash"], starts="linqpad: lprun9 against saved connection")
        self.asks_each(powershell, tools=["PowerShell"], starts="linqpad: lprun9 against saved connection")

    def test_start_process_takes_its_parameters_in_any_order(self):
        fb, conn = self.p("fb.cs"), self.p("conn.linq")
        named = 'linqpad: lprun9 against saved connection "Sales"' + CX_TAIL
        commands = [f"Start-Process -ArgumentList '-cxname=Sales','{fb}' -FilePath lprun9 -Wait -NoNewWindow",
                    f'Start-Process -Wait -ArgumentList "-cxname=Sales {fb}" lprun9',
                    f"$p = Start-Process -PassThru -ArgumentList '-cxname=Sales','{fb}' -FilePath lprun9; "
                    "$p.WaitForExit()",
                    f"saps -Args '-cxname=Sales','{fb}' -FilePath LPRun9.exe",
                    f"Start-Process -ArgumentList:'-cxname=Sales','{fb}' -FilePath lprun9"]
        self.asks_each(commands, tools=["PowerShell"], equals=named)
        self.assertEqual(self.decide(commands[0], "PowerShell", mode="bypassPermissions"), ("deny", named))
        self.asks_each([f"Start-Process -ArgumentList '{conn}' -FilePath 'C:\\Program Files\\LINQPad9\\LPRun9.exe' "
                        "-Wait -NoNewWindow"], tools=["PowerShell"],
                       equals=f'linqpad: lprun9 runs "{conn}", which holds saved connection "FakeProd" '
                              f"(sql-fake.example.test / Orders)" + CX_TAIL)
        # the file Start-Process gives lprun9 as its standard input is a script lprun9 may read
        self.asks_each([f"Start-Process lprun9 -RedirectStandardInput '{conn}' -Wait"], tools=["PowerShell"],
                       starts=f'linqpad: lprun9 runs "{conn}"')
        # Start-Process's own parameters and their values are not lprun9's
        self.silent_each([f"Start-Process -RedirectStandardOutput out.txt -ArgumentList '{fb}' -FilePath lprun9 -Wait",
                          f"Start-Process -Verb RunAs -WindowStyle Hidden -FilePath lprun9 -ArgumentList '{fb}'",
                          f"Start-Process -ArgumentList '{fb}' lprun9 -NoNewWindow"], tools=["PowerShell"])

    def test_a_substitution_in_a_heredoc_or_here_string_runs(self):
        fb, conn = self.p("fb.cs"), self.p("conn.linq")
        self.asks_each([f"cat > report.md <<EOF\n# Results\n$(lprun9 -cxname=Sales {fb})\nEOF",
                        f"cat > report.md <<EOF\n`lprun9 -cxname=Sales {fb}`\nEOF",
                        f"tee report.md <<-EOF\n\t$(lprun9 -cxname=Sales {fb})\n\tEOF",
                        f'cat <<< "$(lprun9 -cxname=Sales {fb})"',
                        f"cat <<EOF\n## Rows: $(lprun9 -cxname=Sales {fb})\nEOF",
                        f'echo "# $(lprun9 -cxname=Sales {fb})"'], tools=["Bash"],
                       equals='linqpad: lprun9 against saved connection "Sales"' + CX_TAIL)
        self.asks_each([f"tee report.md <<EOF\n$(lprun9 {conn})\nEOF"], tools=["Bash"],
                       starts=f'linqpad: lprun9 runs "{conn}"')
        got = self.decide(f"cat > report.md <<EOF\n$(lprun9 -cxname=Sales {fb})\nEOF", mode="bypassPermissions")
        self.assertEqual(got, ("deny", 'linqpad: lprun9 against saved connection "Sales"' + CX_TAIL))

    def test_a_quoted_operator_does_not_end_the_command(self):
        fb = self.p("fb.cs")
        self.asks_each([f'lprun9 ";" -cxname=Sales {fb}', f"lprun9 '&&' -cxname=Sales {fb}",
                        f"lprun9 '|' -cxname=Sales {fb}", f'lprun9 "a b" -cxname=Sales {fb}'],
                       starts='linqpad: lprun9 against saved connection "Sales"')

    def test_comments_and_line_continuations_hide_nothing(self):
        fb = self.p("fb.cs")
        self.asks_each([f"echo hi # a note\nlprun9 -cxname=Sales {fb}",
                        f'echo "# not a comment"; lprun9 -cxname=Sales {fb}',
                        f"echo don't # it's a comment\nlprun9 -cxname=Sales {fb}"],
                       starts='linqpad: lprun9 against saved connection "Sales"')
        self.asks_each([f"lprun9 \\\n  -cxname=Sales {fb}"], tools=["Bash"],
                       starts='linqpad: lprun9 against saved connection "Sales"')
        self.asks_each([f"lprun9 `\n  -cxname=Sales {fb}", f"<# a note #> lprun9 -cxname=Sales {fb}",
                        f"<# a\nlonger note #>\nlprun9 -cxname=Sales {fb}"], tools=["PowerShell"],
                       starts='linqpad: lprun9 against saved connection "Sales"')

    def test_powershell_quirks(self):
        fb = self.p("fb.cs")
        commands = [f"lprun9 --% -cxname=Sales {fb}", f"lprun9 \u201c-cxname=Sales\u201d {fb}",
                    f"lprun9 \u2013cxname=Sales {fb}", f"lprun9 {fb},-cxname=Sales",
                    f"iex @'\nlprun9 -cxname=Sales {fb}\n'@", f'iex @"\nlprun9 -cxname=Sales {fb}\n"@']
        self.asks_each(commands, tools=["PowerShell"], starts="linqpad: lprun9 against saved connection")

    def test_a_runner_held_in_a_variable(self):
        fb = self.p("fb.cs")
        bash = [f"LP=lprun9; $LP -cxname=Sales {fb}", f'"$LPRUN9" -cxname=Sales {fb}', f"${{LPRUN9}} -cxname=Sales {fb}",
                f"$(which lprun9) -cxname=Sales {fb}", f"`which lprun9` -cxname=Sales {fb}"]
        powershell = [f"$lp = 'lprun9'; & $lp -cxname=Sales {fb}", f"& $env:LPRUN9 -cxname=Sales {fb}",
                      f"& (Get-Command lprun9) -cxname=Sales {fb}", f"& (Get-Command lprun9).Source -cxname=Sales {fb}"]
        self.asks_each(bash, tools=["Bash"], starts="linqpad: lprun9 against saved connection")
        self.asks_each(powershell, tools=["PowerShell"], starts="linqpad: lprun9 against saved connection")

    def test_named_by_code_or_text_that_a_shell_runs(self):
        fb = self.p("fb.cs")
        commands = [f"python3 -c \"import subprocess; subprocess.run(['lprun9', '-cxname=Sales', '{fb}'])\"",
                    f'echo "lprun9 -cxname=Sales {fb}" | bash', f"echo 'lprun9 -cxname=Sales {fb}' | sh",
                    f"printf 'lprun9 -cxname=Sales {fb}' | python3 -"]
        self.asks_each(commands, starts="linqpad: lprun9 against saved connection")
        self.asks_each([f'"lprun9 -cxname=Sales {fb}" | Invoke-Expression', f"'lprun9 -cxname=Sales {fb}' | iex"],
                       tools=["PowerShell"], starts="linqpad: lprun9 against saved connection")

    def test_named_in_a_heredoc_a_shell_or_interpreter_reads(self):
        fb = self.p("fb.cs")
        self.asks_each([f"bash <<'EOF'\nlprun9 -cxname=Sales {fb}\nEOF", f"sh <<< 'lprun9 -cxname=Sales {fb}'",
                        f"python3 - <<'EOF'\nimport subprocess\n"
                        f"subprocess.run(['lprun9', '-cxname=Sales', '{fb}'])\nEOF",
                        f"cat <<'EOF' | bash\nlprun9 -cxname=Sales {fb}\nEOF",
                        f"bash <<-EOF\n\tlprun9 -cxname=Sales {fb}\n\tEOF\necho done"], tools=["Bash"],
                       starts="linqpad: lprun9 against saved connection")

    def test_named_in_a_script_the_line_writes_and_runs(self):
        fb = self.p("fb.cs")
        self.asks_each([f"echo 'lprun9 -cxname=Sales {fb}' > run.sh; ./run.sh",
                        f"printf 'lprun9 -cxname=Sales {fb}' > run.cmd && run.cmd"], tools=["Bash"],
                       starts="linqpad: lprun9 against saved connection")
        self.asks_each([f"Write-Output 'lprun9 -cxname=Sales {fb}' > run.ps1; .\\run.ps1"], tools=["PowerShell"],
                       starts="linqpad: lprun9 against saved connection")


class ScriptWithAConnection(GateCase):
    """lprun9 naming a script whose header holds a <Connection> asks, naming its server and database."""

    def test_asks_naming_server_and_database(self):
        conn = self.p("conn.linq")
        self.asks_each([f"lprun9 {conn}", f'lprun9 "{conn}"', f"lprun9 -format=csv {conn}"],
                       equals=f'linqpad: lprun9 runs "{conn}", which holds saved connection "FakeProd" '
                              f"(sql-fake.example.test / Orders)" + CX_TAIL)

    def test_an_exotic_casing_of_the_runner(self):
        self.asks_each([f"LPRUN9.EXE {self.p('conn.linq')}", f"lPrUn9 {self.p('conn.linq')}"],
                       starts="linqpad: lprun9 runs")

    def test_a_relative_path_resolves_against_the_hooks_cwd(self):
        expected = 'which holds saved connection "FakeProd" (sql-fake.example.test / Orders)'
        for tool in BOTH:
            for command in ("lprun9 conn.linq", "lprun9 ./conn.linq", "lprun9 sub/../conn.linq",
                            'lprun9 "space dir/conn.linq"'):
                with self.subTest(tool=tool, command=command):
                    self.assertIn(expected, self.asks(command, tool))

    def test_a_path_with_spaces_behind_a_wrapper(self):
        """A wrapper that hands on the shell's words leaves a quoted path whole, so neither a decoy file at its first
        piece ("space") hides the connection nor a script with none asks."""
        conn, plain = self.p("space dir/conn.txt"), self.p("space dir/plain.linq")
        expected = 'which holds saved connection "FakeProd" (sql-fake.example.test / Orders)'
        for tool in BOTH:
            for command in (f'timeout 60 lprun9 "{conn}"', f"env A=1 nohup lprun9 '{conn}'",
                            f'cmd /c lprun9 "{conn}"', 'timeout 60 lprun9 "space dir/conn.txt"'):
                with self.subTest(tool=tool, command=command):
                    self.assertIn(expected, self.asks(command, tool))
        self.silent_each([f'timeout 120 lprun9 "{plain}"', f"env A=1 nohup lprun9 '{plain}'",
                          'timeout 120 lprun9 "space dir/plain.linq"'])

    def test_header_variants(self):
        names = ["bom.linq", "utf16.linq", "utf16-nobom.linq", "lower.linq", "namespaced.linq", "attribute.linq",
                 "prolog.linq"]
        self.asks_each([f"lprun9 {self.p(n)}" for n in names], starts="linqpad: lprun9 runs")

    def test_whatever_the_script_is_called(self):
        self.asks_each([f"lprun9 {self.p('conn.txt')}"], starts=f'linqpad: lprun9 runs "{self.p("conn.txt")}"')

    def test_a_runner_the_shell_computes_or_shortens(self):
        conn = self.p("conn.txt")  # not a .linq, so only a run the gate recognises reads it
        self.asks_each([f"$(which lprun9) {conn}", f"`which lprun9` {conn}", f'"$(command -v lprun9)" {conn}'],
                       tools=["Bash"], starts=f'linqpad: lprun9 runs "{conn}"')
        self.asks_each([f"& (Get-Command lprun9) {conn}", f"& (Get-Command lprun9).Source {conn}",
                        f"& (Get-Command lprun9)[0].Source {conn}"], tools=["PowerShell"],
                       starts=f'linqpad: lprun9 runs "{conn}"')
        self.asks_each([f"C:/PROGRA~1/LINQPad9/LPRUN9~1.EXE {conn}", f"LPRUN9~2 {conn}"],
                       starts=f'linqpad: lprun9 runs "{conn}"')

    def test_a_runner_behind_a_wrapper_or_an_alias(self):
        conn = self.p("conn.txt")
        self.asks_each([f"LP=lprun9; timeout 120 $LP {conn}", f"alias lp=lprun9\nlp {conn}",
                        f"alias lp='lprun9 -format=csv'\ntimeout 60 lp {conn}"], tools=["Bash"],
                       starts=f'linqpad: lprun9 runs "{conn}"')
        self.asks_each([f"$lp = 'lprun9'; Start-Process -FilePath $lp -ArgumentList '{conn}'",
                        f"$lp = 'lprun9'; . $lp {conn}",
                        f"Set-Alias lp lprun9; lp {conn}", f"New-Alias -Name lp -Value LPRun9.exe; lp {conn}",
                        f"sal lp 'C:\\Program Files\\LINQPad9\\LPRun9.exe'; lp {conn}"], tools=["PowerShell"],
                       starts=f'linqpad: lprun9 runs "{conn}"')

    def test_a_header_that_hides_its_end(self):
        self.asks_each([f"lprun9 {self.p(n)}" for n in ("comment-end.linq", "attribute-end.linq")],
                       starts="linqpad: lprun9 runs")

    def test_a_linq_passed_as_an_argument_too(self):
        self.asks_each([f"lprun9 {self.p('fb.cs')} {self.p('conn.linq')}"], starts="linqpad: lprun9 runs")

    def test_both_findings_in_one_reason(self):
        conn = self.p("conn.linq")
        reason = self.asks(f"lprun9 -cxname=Sales {conn}")
        self.assertEqual(reason, f'linqpad: lprun9 against saved connection "Sales"; lprun9 runs "{conn}", which holds '
                                 f'saved connection "FakeProd" (sql-fake.example.test / Orders)' + CX_TAIL)

    def test_never_quotes_a_password_or_a_connection_string(self):
        cxstring = self.p("cxstring.linq")
        for tool in BOTH:
            with self.subTest(tool=tool):
                reason = self.asks(f"lprun9 {cxstring}", tool)
                self.assertEqual(reason, f'linqpad: lprun9 runs "{cxstring}", which holds a saved connection' + CX_TAIL)
                reason = self.asks(f"lprun9 {self.p('odd-server.linq')}", tool)
                self.assertIn("((not shown) / Orders)", reason)
                for name in ("conn.linq", "utf16.linq", "cxstring.linq", "odd-server.linq", "deep.linq"):
                    reason = self.asks(f"lprun9 -cxname=Sales {self.p(name)}", tool)
                    for secret in (PASSWORD, "Password", "CustomCxString", "Server=", "reader_fake"):
                        self.assertNotIn(secret, reason)

    def test_format_characters_from_the_header_are_not_shown(self):
        reason = self.asks(f"lprun9 {self.p('bidi.linq')}")
        self.assertIn('"Fake?Prod" (sql?fake)', reason)

    @unittest.skipUnless(os.name == "nt", "Windows paths")
    def test_windows_paths(self):
        conn = self.dir / "conn.linq"
        msys = "/" + conn.drive[0].lower() + conn.as_posix()[2:]
        self.asks_each([f"lprun9 {msys}", f"lprun9 {self.p('CONN.LINQ')}"], starts="linqpad: lprun9 runs")
        self.asks_each([f"lprun9 {conn}", f"lprun9 '{conn}'", "lprun9 .\\conn.linq"], tools=["PowerShell"],
                       starts="linqpad: lprun9 runs")


class ProgramsThatOnlyReadAScript(GateCase):
    """A command whose program is not lprun9 is silent, whatever .linq it names."""

    def test_reading_or_editing_a_linq_never_prompts(self):
        conn = self.p("conn.linq")
        commands = [f"cat {conn}", f"type {conn}", f"code {conn}", f"Get-Content {conn}", f"head -5 {conn}",
                    f"less {conn}", f"notepad {conn}", f"cp {conn} {self.p('copy.linq')}", f"grep -n Server {conn}",
                    f"Select-String Server {conn}", f"cat {conn} | grep Connection", f"git add {conn}",
                    f"ls {self.dir.as_posix()}/*.linq", "cat conn.linq", f"Invoke-Item {conn}",
                    f"python3 -c \"print(open('{conn}').read())\"", f"[ -f {conn} ] && echo yes"]
        self.silent_each(commands)

    def test_text_that_only_mentions_lprun9(self):
        conn = self.p("conn.linq")
        self.silent_each([f'git commit -m "lprun9 -cxname=Sales now asks; so does lprun9 {conn}"',
                          f'echo "lprun9 -cxname=Sales {conn}"', f"grep -rn -e -cxname= -e lprun9 {self.dir.as_posix()}",
                          "grep -rn lprun9 skills/linqpad", f"grep lprun9 {conn}"])
        self.silent_each([f"Set-Content notes.md 'lprun9 -cxname=Sales {conn}'",
                          f"Write-Output \"lprun9 -cxname=Sales {conn}\" | Out-File notes.md"], tools=["PowerShell"])

    def test_a_heredoc_that_is_only_text(self):
        conn = self.p("conn.linq")
        message = f"Ask before lprun9 -cxname=Sales, and before lprun9 {conn}\n\nThe gate reads lprun9 runs."
        self.silent_each([f"git commit -m \"$(cat <<'EOF'\n{message}\nEOF\n)\"",
                          f"gh pr create --title Gate --body \"$(cat <<'EOF'\n{message}\nEOF\n)\"",
                          f"cat > notes.md <<'EOF'\n{message}\nEOF", f"cat <<< 'lprun9 -cxname=Sales {conn}'",
                          f"cat <<EOF\nlprun9 -cxname=Sales {conn}"], tools=["Bash"])

    def test_a_quoted_heredoc_keeps_a_substitution_as_text(self):
        conn = self.p("conn.linq")
        self.silent_each([f"cat > notes.md <<'EOF'\n$(lprun9 -cxname=Sales {conn})\nEOF",
                          f'cat > notes.md <<"EOF"\n`lprun9 -cxname=Sales {conn}`\nEOF',
                          f"cat > notes.md <<\\EOF\n$(lprun9 -cxname=Sales {conn})\nEOF",
                          f"cat <<< '$(lprun9 -cxname=Sales {conn})'"], tools=["Bash"])

    def test_only_the_substitution_in_text_runs(self):
        conn = self.p("conn.linq")
        self.silent_each([f"cat > notes.md <<EOF\nGenerated $(date). Run lprun9 -cxname=Sales {conn} to refresh.\nEOF",
                          f'git commit -m "gate: lprun9 -cxname=Sales {conn} now asks ($(date))"',
                          f"cat > notes.md <<EOF\n# lprun9 -cxname=Sales {conn}\n`date`\nEOF"], tools=["Bash"])
        self.silent_each([f'Write-Output "see lprun9 -cxname=Sales {conn}, $(Get-Date)"'], tools=["PowerShell"])

    def test_single_quotes_keep_a_backtick_or_a_substitution_as_text(self):
        conn, fb = self.p("conn.linq"), self.p("fb.cs")
        self.silent_each([f"git commit -am 'gate: handle `lprun9 -cxname=X {conn}`'",
                          f"echo 'see $(lprun9 -cxname=Sales {conn})' >> notes.md"])
        self.silent_each([f"Add-Content notes.md -Value 'see `lprun9 -cxname=Sales {conn}`'",
                          f"'lprun9 -cxname=Sales {conn}' | Out-File notes.txt",
                          "Set-Content -Path notes.md -Value @'\nRun `lprun9 -cxname=<name> script.linq` to query; "
                          "`python3 read.py query --cx <name> --sql q.sql` is the safe way.\n'@",
                          f'git commit -am "gate: handle `lprun9 -cxname=X {conn}` now"'], tools=["PowerShell"])
        # in double quotes Bash runs a backtick or $( ), and PowerShell runs $( )
        self.asks_each([f'git commit -am "gate: `lprun9 -cxname=Sales {fb}`"'], tools=["Bash"],
                       starts='linqpad: lprun9 against saved connection "Sales"')
        self.asks_each([f'Write-Output "see $(lprun9 -cxname=Sales {fb})"',
                        f'Set-Content notes.md -Value @"\n$(lprun9 -cxname=Sales {fb})\n"@',
                        f'"$(lprun9 -cxname=Sales {fb})" | Out-File notes.txt'], tools=["PowerShell"],
                       starts='linqpad: lprun9 against saved connection "Sales"')


class CouldNotRead(GateCase):
    """A run the gate cannot read asks, rather than guessing."""

    def test_a_script_that_is_not_there(self):
        self.asks_each([f"lprun9 {self.p('missing.linq')}", f"lprun9 {self.p('missing.cs')}",
                        f"lprun9 {self.p('dir.linq')}"], starts=UNREAD)

    def test_a_long_path_keeps_the_script_name_in_the_prompt(self):
        # Windows scratch paths run past the 120 characters the prompt shows: the middle goes, not the end.
        long = f"{self.dir.as_posix()}/{'d' * 150}/run-the-report.linq"
        self.asks_each([f"lprun9 {long}"], starts=UNREAD, has="run-the-report.linq: the gate cannot open it")
        reason = self.asks(f"lprun9 {long}")
        self.assertIn("...", reason)
        self.assertLess(max(len(line) for line in reason.split("\n")), 400)

    def test_a_path_the_gate_cannot_expand(self):
        self.asks_each(["lprun9 $HOME/q.linq", "lprun9 ~/q.linq", f"lprun9 {self.dir.as_posix()}/*.linq",
                        f"lprun9 {self.dir.as_posix()}/{{a,b}}.linq", f'lprun9 "$(echo {self.p("conn.linq")})"',
                        f"lprun9 @{self.p('args.rsp')}", "lprun9 -"], tools=["Bash"], starts=UNREAD)
        self.asks_each(["lprun9 $env:TEMP/q.linq", "lprun9 @a", '$a = "x.linq"; lprun9 @a', "lprun9 $(Get-Item x)"],
                       tools=["PowerShell"], starts=UNREAD)

    def test_a_line_that_does_not_parse(self):
        conn = self.p("conn.linq")
        self.asks_each([f'lprun9 "{conn}', f"lprun9 '{conn}", f"lprun9 -cxname=Sales {conn} \\"], tools=["Bash"],
                       starts="linqpad: ")
        self.asks_each([f'lprun9 "{conn}', f"lprun9 '{conn}", f"lprun9 {conn} <# open"], tools=["PowerShell"],
                       starts="linqpad: ")
        self.asks_each([f'lprun9 "{self.p("plain.linq")}'], starts=UNREAD)

    def test_a_relative_path_after_a_change_of_directory(self):
        self.asks_each(["cd sub && lprun9 plain.linq", "pushd sub; lprun9 plain.linq", "(cd sub; lprun9 plain.linq)",
                        "env -C sub lprun9 plain.linq"], tools=["Bash"], starts=UNREAD)
        self.asks_each(["Set-Location sub; lprun9 plain.linq", "cd sub; lprun9 plain.linq",
                        "Push-Location sub; lprun9 plain.linq",
                        "Start-Process lprun9 -WorkingDirectory sub -ArgumentList plain.linq"],
                       tools=["PowerShell"], starts=UNREAD)

    def test_a_script_the_same_line_could_write(self):
        plain = self.p("plain.linq")
        heredoc = (f"cat > {plain} <<'EOF'\n<Query Kind=\"SQL\"><Connection><Server>s</Server></Connection></Query>\n"
                   f"EOF\nlprun9 {plain}")
        self.asks_each([f"echo x > {plain} && lprun9 {plain}", heredoc,
                        f"cp {self.p('conn.linq')} {plain}; lprun9 {plain}",
                        f"tee {plain} < /dev/null; lprun9 plain.linq"], tools=["Bash"], starts=UNREAD)
        self.asks_each([f"Set-Content {plain} 'x'; lprun9 {plain}", f"Copy-Item conn.linq plain.linq; lprun9 {plain}"],
                       tools=["PowerShell"], starts=UNREAD)
        self.asks_each([f"git checkout -- {plain} && lprun9 {plain}",
                        f"cp {self.p('conn.linq')} *.linq; lprun9 {plain}",
                        f"bash -c 'cp conn.linq plain.linq'; lprun9 {plain}"], starts=UNREAD)

    def test_a_script_only_read_beside_the_run_is_no_write(self):
        fb, plain = self.p("fb.cs"), self.p("plain.linq")
        self.silent_each([f"cat {fb} && lprun9 {fb}", f"head -3 {plain}; lprun9 {plain}", f"lprun9 {fb}; lprun9 {fb}",
                          f"grep -c Query {plain} && timeout 60 lprun9 {plain}", f"lprun9 {plain} > {plain}.out"])
        self.silent_each([f"Get-Content {plain}; lprun9 {plain}", f"Select-String Query {plain}; lprun9 {plain}"],
                         tools=["PowerShell"])

    def test_a_script_from_standard_input(self):
        conn = self.p("conn.linq")
        heredoc = f"lprun9 -lang=SQL <<'EOF'\n{CONN_LINQ}EOF"
        self.asks_each([f"lprun9 <<< {conn}", f"lprun9 < {conn}", f"lprun9 -lang=SQL 0< {conn}", f"cat {conn} | lprun9",
                        f"cat {conn} | timeout 60 lprun9 -lang=SQL", heredoc, f"cat {conn} | (lprun9)",
                        f"cat {conn} | {{ lprun9; }}"], tools=["Bash"], starts=UNREAD)
        self.asks_each([f"Get-Content {conn} | lprun9", f"Get-Content {conn} -Raw | & lprun9 -lang=SQL"],
                       tools=["PowerShell"], starts=UNREAD)

    def test_arguments_that_arrive_from_elsewhere(self):
        self.asks_each([f"ls {self.dir.as_posix()}/*.linq | xargs lprun9",
                        f"find {self.dir.as_posix()} -name '*.linq' -exec lprun9 {{}} \\;"],
                       tools=["Bash"], starts=UNREAD)
        self.asks_each(["$f = 'conn.linq'; lprun9 $f", "Get-ChildItem *.linq | ForEach-Object { lprun9 $_ }"],
                       tools=["PowerShell"], starts=UNREAD)
        # what a pipe gives xargs is lprun9's arguments, not a script on lprun9's standard input
        self.asks_each([f"ls {self.dir.as_posix()}/*.linq | xargs lprun9"], tools=["Bash"],
                       equals=f"{UNREAD} (xargs or parallel supplies lprun9's arguments)")

    def test_a_header_that_does_not_end_within_8_kb(self):
        self.asks_each([f"lprun9 {self.p('deep.linq')}"], starts=UNREAD)

    def test_a_header_end_hidden_in_a_comment_cdata_or_attribute_does_not_count(self):
        names = ("deep-comment.linq", "deep-cdata.linq", "deep-attribute.linq")
        self.asks_each([f"lprun9 {self.p(n)}" for n in names], starts=UNREAD)

    def test_a_header_with_a_dtd(self):
        self.asks_each([f"lprun9 {self.p('entity.linq')}"], starts=UNREAD)

    def test_an_argument_the_shell_computes(self):
        conn = self.p("conn.linq")
        self.asks_each([f"lprun9 (Resolve-Path {conn})", f"lprun9 -format=csv (Get-Item {conn}).FullName",
                        f"lprun9 $(Resolve-Path {conn})"], tools=["PowerShell"], starts=UNREAD)
        self.asks_each([f"lprun9 $(echo {conn})", f"lprun9 `echo {conn}`"], tools=["Bash"], starts=UNREAD)

    def test_no_cwd_for_a_relative_path(self):
        for cwd in ("", "relative/folder"):
            with self.subTest(cwd=cwd):
                self.asks_each(["lprun9 plain.linq"], starts=UNREAD, cwd=cwd)

    def test_hook_input_that_is_not_json(self):
        for stdin in (b"", b"not json", b"[1, 2]", b"{\"tool_name\": \"Bash\", \"tool_input\": "):
            with self.subTest(stdin=stdin):
                got = self.decision(self.run_gate(stdin))
                self.assertEqual(got[0], "ask")
                self.assertTrue(got[1].startswith(UNREAD), got)

    def test_a_command_that_is_not_text(self):
        for command in (["lprun9", "-cxname=Sales"], 42):
            with self.subTest(command=command):
                got = self.decision(self.run_gate(self.hook(command)))
                self.assertEqual(got[0], "ask")
                self.assertTrue(got[1].startswith(UNREAD), got)


class NetworkPaths(unittest.TestCase):
    """A network path is never opened, or even looked at, before the command is approved: Windows would sign in to
    the host it names as the user. A run of one asks instead, saying why. Run in this process, with os.stat replaced,
    so that no test touches a share either."""

    @classmethod
    def setUpClass(cls):
        cls.gate = load_gate()

    def test_resolve_places_none(self):
        for path in ("//host/share/q.linq", "\\\\host\\share\\q.linq", "\\\\?\\UNC\\host\\share\\q.linq",
                     "//?/UNC/host/share/q.linq", "\\\\.\\pipe\\q", "/\\host\\share\\q.linq"):
            with self.subTest(path=path):
                self.assertEqual(self.gate.resolve(path, "/tmp"), (None, NETWORK))
        self.assertEqual(self.gate.resolve("a//b.linq", None)[0], None)  # relative, and no cwd: as before

    def test_the_gate_asks_without_touching_one(self):
        touched = []

        def stat(path, *args, **kwargs):
            touched.append(os.fspath(path))
            raise FileNotFoundError(path)

        share = "//127.0.0.1/no-such-share"
        with tempfile.TemporaryDirectory() as cwd, mock.patch("os.stat", stat):
            for command, shell in [(f"lprun9 {share}/x.linq", "bash"), (f"lprun9 -cxname=Sales {share}/x.cs", "bash"),
                                   ("lprun9 \\\\127.0.0.1\\no-such-share\\x.linq", "powershell"),
                                   (f"Start-Process lprun9 -ArgumentList '{share}/x.linq'", "powershell")]:
                with self.subTest(command=command):
                    reason = self.gate.judge(command, shell, cwd).reason()
                    self.assertIn(f"x.linq: {NETWORK}" if "x.linq" in command else f"x.cs: {NETWORK}", reason)
            code = f"python3 -c \"import subprocess; subprocess.run(['lprun9', '{share}/q.linq'])\""
            self.assertFalse(self.gate.judge(code, "bash", cwd))  # a .linq only named asks for a connection it sees
        self.assertEqual([path for path in touched if re.match(r"[\\/]{2}", path)], [])


class NobodyToAsk(GateCase):
    """In a mode with nobody to ask, every ask becomes a deny with the same reason."""

    def test_bypass_permissions_and_dont_ask_deny(self):
        fb, missing = self.p("fb.cs"), self.p("missing.linq")
        for mode in ("bypassPermissions", "dontAsk"):
            for tool in BOTH:
                with self.subTest(mode=mode, tool=tool):
                    got = self.decide(f"lprun9 -cxname=Sales {fb}", tool, mode=mode)
                    self.assertEqual(got, ("deny", 'linqpad: lprun9 against saved connection "Sales"' + CX_TAIL))
                    got = self.decide(f"lprun9 {missing}", tool, mode=mode)
                    self.assertEqual(got[0], "deny")
                    self.assertTrue(got[1].startswith(UNREAD), got)
                    self.assertIsNone(self.decide(f"lprun9 {fb}", tool, mode=mode))

    def test_the_other_modes_ask(self):
        for mode in ("default", "acceptEdits", "plan", "auto", None, "someFutureMode"):
            with self.subTest(mode=mode):
                self.assertEqual(self.decide(f"lprun9 -cxname=Sales {self.p('fb.cs')}", mode=mode)[0], "ask")


class Silence(GateCase):
    """Everything else passes without a word."""

    def test_a_plain_command(self):
        self.silent_each(["npm test", "git status", "dotnet --version", ""])

    def test_tools_other_than_the_shells(self):
        conn = self.p("conn.linq")
        for tool, tool_input in [("Read", {"file_path": conn}), ("Write", {"file_path": conn, "content": CONN_LINQ}),
                                 ("Edit", {"file_path": conn, "old_string": "a", "new_string": "b"}),
                                 ("Glob", {"pattern": "**/*.linq"}), ("Grep", {"pattern": "lprun9 -cxname="}),
                                 ("mcp__fake__run", {"command": f"lprun9 -cxname=Sales {conn}"}),
                                 ("bash", {"command": f"lprun9 -cxname=Sales {conn}"})]:
            with self.subTest(tool=tool):
                payload = json.loads(self.hook("", tool))
                payload["tool_input"] = tool_input
                self.assertIsNone(self.decision(self.run_gate(json.dumps(payload).encode())))

    def test_events_other_than_pre_tool_use(self):
        for event in ("PostToolUse", "PermissionRequest"):
            with self.subTest(event=event):
                stdin = self.hook(f"lprun9 -cxname=Sales {self.p('fb.cs')}", hook_event_name=event)
                self.assertIsNone(self.decision(self.run_gate(stdin)))

    def test_lprun9_runs_that_touch_no_connection(self):
        fb, plain = self.p("fb.cs"), self.p("plain.linq")
        commands = ["lprun9", "lprun9 -help", f"lprun9 {fb}", f"lprun9 -lang=e {self.p('bare.linq')}",
                    f"lprun9 -format=csv {self.p('statements.linq')}", f"lprun9 {plain} one two",
                    f"timeout 120 lprun9 {fb}", f"lprun9 {fb} 2>&1 | tail -5", f"lprun9 {fb} > {self.p('out.txt')}",
                    "lprun9 plain.linq", "lprun9 fb.cs", f"cd sub && lprun9 {plain}", f"dotnet run {fb}",
                    f"lprun9 {fb} && lprun9 {plain}", f"lprun9 -lang=SQL {fb}"]
        self.silent_each(commands)
        self.silent_each([f"echo 6 | lprun9 {fb}", f"lprun9 {fb} < {self.p('conn.txt')}", f"lprun9 {fb} <<< 6"],
                         tools=["Bash"])  # input for a script the line names
        self.silent_each([f"& 'C:\\Program Files\\LINQPad9\\LPRun9.exe' {fb}", f"lprun9 {fb} | Select-Object -Last 3",
                          f"Start-Process lprun9 -ArgumentList '{fb}' -Wait -NoNewWindow", f"6 | lprun9 {fb}"],
                         tools=["PowerShell"])

    def test_finding_lprun9_itself(self):
        self.silent_each(["which lprun9", "where.exe lprun9", "Get-Command lprun9", "lprun9 --version",
                          'ls "C:/Program Files/LINQPad9"', "find / -name lprun9 2>/dev/null"])

    def test_code_below_a_header_that_names_a_connection_variable(self):
        self.silent_each([f"lprun9 {self.p('code-mentions.linq')}"])

    def test_powershell_expressions_over_a_linq(self):
        conn = self.p("conn.linq")
        self.silent_each([f"(Get-Content {conn}).Length", f"$n = (Get-Item {conn}).Length",
                          f"[IO.File]::ReadAllText('{conn}')", f"(Get-Command lprun9).Source"], tools=["PowerShell"])


class Output(GateCase):
    """What the gate prints survives any console code page, and shows nothing that can hide."""

    def test_ascii_whatever_the_console_takes(self):
        name = "Prod\u2014EU\u2026\u26a0"
        command = f"lprun9 -cxname={name} {self.p('fb.cs')}"
        for encoding in ("cp1252", "ascii", "utf-8"):
            with self.subTest(encoding=encoding):
                env = {**os.environ, "PYTHONIOENCODING": encoding}
                got = self.decision(self.run_gate(self.hook(command), env=env))
                self.assertEqual(got[1], f'linqpad: lprun9 against saved connection "{name}"' + CX_TAIL)

    def test_input_that_is_not_utf8(self):
        stdin = self.hook(f"lprun9 -cxname=Sales {self.p('fb.cs')} #\u2013").replace(b"\\u2013", b"\x96")
        got = self.decision(self.run_gate(stdin))
        self.assertEqual(got, ("ask", 'linqpad: lprun9 against saved connection "Sales"' + CX_TAIL))

    def test_format_characters_are_replaced(self):
        reason = self.asks(f"lprun9 -cxname=Fake\u202eProd\u200b {self.p('fb.cs')}")
        self.assertEqual(reason, 'linqpad: lprun9 against saved connection "Fake?Prod?"' + CX_TAIL)

    def test_a_long_name_is_cut_short(self):
        reason = self.asks(f"lprun9 -cxname={'A' * 5000} {self.p('fb.cs')}")
        self.assertLess(len(reason), 400)

    def test_a_long_line_reads_well_inside_the_hooks_timeout(self):
        fb = self.p("fb.cs")
        lines = {"Bash": f"python3 -c \"{'x' * 300_000} lprun9\" && lprun9 -cxname=Sales {fb}",
                 "PowerShell": "echo " + "@'x' " * 40_000 + f"; lprun9 -cxname=Sales {fb}"}
        for tool, command in lines.items():
            with self.subTest(tool=tool):
                started = time.monotonic()
                reason = self.asks(command, tool)
                self.assertLess(time.monotonic() - started, 8)
                self.assertTrue(reason.startswith('linqpad: lprun9 against saved connection "Sales"'), reason)


class FastPath(GateCase):
    """Any other tool, or a command that names neither lprun9 nor a .linq, costs json, re and sys alone."""

    def test_the_module_imports_only_json_re_and_sys(self):
        tree = ast.parse(GATE.read_text(encoding="utf-8"))
        imported = set()
        for node in tree.body:
            if isinstance(node, ast.Import):
                imported |= {alias.name for alias in node.names}
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module)
        self.assertEqual(imported, {"json", "re", "sys"})

    def test_shlex_loads_only_for_a_command_worth_reading(self):
        read = json.loads(self.hook("", "Read"))
        read["tool_input"] = {"file_path": self.p("conn.linq")}
        # shlex takes the escapes off a word; a word with none only loses its quotes
        for stdin, reads in [(json.dumps(read).encode(), False), (self.hook("npm test"), False),
                             (self.hook("npm test", "PowerShell"), False),
                             (self.hook(f"lprun9 -cxname=Sales {self.p('fb.cs')}"), False),
                             (self.hook(f"lprun9 -cxname=NS\\DS {self.p('fb.cs')}"), True)]:
            with self.subTest(stdin=stdin[:120]):
                proc = self.run_gate(stdin, args=("-X", "importtime"))
                self.assertEqual(proc.returncode, 0, proc.stderr)
                loaded = re.search(r"\|\s*shlex\s*$", proc.stderr.decode("utf-8", "replace"), re.M) is not None
                self.assertEqual(loaded, reads)


class HooksJson(unittest.TestCase):
    """hooks.json, pinned: claude plugin validate --strict passes an unknown field or a wrong value, and a malformed
    handler switches its whole matcher group off at run time."""

    # A Bash pattern matches in the case written, so the four casings of "un9" stand for every casing of lprun9
    IFS = ["Bash(*un9*)", "Bash(*uN9*)", "Bash(*Un9*)", "Bash(*UN9*)", "Bash(*.linq*)", "PowerShell(*lprun9*)",
           "PowerShell(*.linq*)"]

    def setUp(self):
        self.config = json.loads(HOOKS.read_text(encoding="utf-8"))

    def fires(self, tool, command):
        """Whether a handler's if pattern matches the command as Claude Code matches it: against each subcommand,
        with * standing for any text; a PowerShell pattern in any case, a Bash one in the case written."""
        for rule in self.IFS:
            name, pattern = re.fullmatch(r"(\w+)\((.*)\)", rule).groups()
            for part in re.split(r"&&|\|\||;|\||&|\n", command) if name == tool else ():
                part = part.strip()
                if (fnmatch.fnmatchcase(part.lower(), pattern.lower()) if tool == "PowerShell"
                        else fnmatch.fnmatchcase(part, pattern)):
                    return True
        return False

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
        for tool, command in [("Bash", "lprun9 -cxname=Sales /tmp/fb.cs"),
                              ("Bash", '"C:/Program Files/LINQPad9/LPRun9.exe" -cxname=Sales C:/x/fb.cs'),
                              ("Bash", "LPRUN9.EXE C:/x/q.linq"), ("Bash", "cd x && lprun9 q.linq"),
                              ("Bash", "LPRUN9.EXE -cxname=Sales C:/x/fb.cs"), ("Bash", "LPRUN9.EXE C:/x/Q.LINQ"),
                              ("Bash", '"C:/PROGRA~1/LINQPAD9/LPRUN9.EXE" -cxname=Sales C:/x/fb.cs'),
                              ("Bash", "LPRUN9~1.EXE -cxname=Sales C:/x/fb.cs"),
                              ("Bash", "Lprun9 -cxname=Sales C:/x/fb.cs"),
                              ("PowerShell", "& 'C:\\Program Files\\LINQPad9\\LPRun9.exe' -cxname=Sales C:\\x\\fb.cs"),
                              ("PowerShell", "LPRUN9.EXE -cxname=Sales fb.cs"), ("PowerShell", "lprun9 C:\\x\\Q.LINQ"),
                              ("PowerShell",
                               "Start-Process -ArgumentList '-cxname=Sales','C:/x/fb.cs' -FilePath lprun9")]:
            with self.subTest(tool=tool, command=command):
                self.assertTrue(self.fires(tool, command))
        for tool, command in [("Bash", "npm test"), ("PowerShell", "npm test"), ("Bash", "git log -n9 --oneline"),
                              ("Bash", "python3 read.py query --cx Sales --sql q.sql")]:
            with self.subTest(tool=tool, command=command):
                self.assertFalse(self.fires(tool, command))

    def test_every_casing_of_the_runner_fires_for_bash(self):
        for letters in itertools.product(*((c, c.upper()) for c in "lprun")):
            runner = "".join(letters) + "9"
            for command in (f"{runner} -cxname=Sales C:/x/fb.cs", f"C:/PROGRA~1/LINQPAD9/{runner}.EXE C:/x/fb.cs",
                            f"timeout 60 {runner}~1.exe -cxname=Sales C:/x/fb.cs"):
                with self.subTest(command=command):
                    self.assertTrue(self.fires("Bash", command))


class ReadingAScriptsHead(unittest.TestCase):
    """read_head opens only a regular file, within the time the line has, so a pipe, a device or a network path can
    never hold the gate past its timeout, which would block the command with no reason given."""

    @classmethod
    def setUpClass(cls):
        cls.gate = load_gate()

    def setUp(self):
        self._folder = tempfile.TemporaryDirectory()
        self.addCleanup(self._folder.cleanup)
        self.dir = Path(self._folder.name)

    def test_the_first_8_kb_of_a_regular_file(self):
        path = self.dir / "q.linq"
        path.write_bytes(b"x" * (self.gate.HEAD_BYTES + 100))
        self.assertEqual(self.gate.read_head(str(path), time.monotonic() + 5), b"x" * self.gate.HEAD_BYTES)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "a named pipe is a POSIX matter")
    def test_a_pipe_is_never_opened(self):
        # Opening a FIFO nobody writes to waits for ever: read_head must answer None at once, so it is asked in a thread
        # the test stops waiting for, and fails rather than hangs.
        import threading
        pipe = self.dir / "q.linq"
        os.mkfifo(pipe)
        answered = []
        worker = threading.Thread(daemon=True, target=lambda: answered.append(
            self.gate.read_head(str(pipe), time.monotonic() + 5)))
        started = time.monotonic()
        worker.start()
        worker.join(3)
        self.assertFalse(worker.is_alive(), "read_head is waiting on the pipe")
        self.assertEqual(answered, [None])
        self.assertLess(time.monotonic() - started, 2)

    def test_a_deadline_that_has_passed_opens_nothing(self):
        path = self.dir / "q.linq"
        path.write_bytes(b"<Query />")
        with mock.patch("os.stat", side_effect=AssertionError("looked")):
            self.assertIsNone(self.gate.read_head(str(path), time.monotonic() - 1))

    def test_a_file_that_does_not_answer_before_the_deadline_is_given_up_on(self):
        # A network path can take minutes: the gate waits out the line's budget, not the path's own.
        path = self.dir / "q.linq"
        path.write_bytes(b"<Query />")
        real = os.stat

        def slow(target, *args, **kwargs):
            time.sleep(2)
            return real(target, *args, **kwargs)

        started = time.monotonic()
        with mock.patch("os.stat", slow):
            self.assertIsNone(self.gate.read_head(str(path), time.monotonic() + 0.3))
        self.assertLess(time.monotonic() - started, 1.5)

    def test_a_missing_file_is_none(self):
        self.assertIsNone(self.gate.read_head(str(self.dir / "missing.linq"), time.monotonic() + 5))


class ReadingACommandLine(unittest.TestCase):
    """The command-line reading sql-read's gate shares: split_commands, program_index and base_name."""

    @classmethod
    def setUpClass(cls):
        cls.gate = load_gate()

    def split(self, line, shell):
        return [(c.words, c.redirects, c.called) for c in self.gate.split_commands(line, shell)]

    def test_operators_end_a_command_and_quotes_keep_them(self):
        self.assertEqual(self.split('a "b;c" | d && e\nf', "bash"),
                         [(["a", "b;c"], [], False), (["d"], [], False), (["e"], [], False), (["f"], [], False)])
        self.assertEqual(self.split("a 'b|c'; d || e", "powershell"),
                         [(["a", "b|c"], [], False), (["d"], [], False), (["e"], [], False)])

    def test_quotes_and_escapes_come_off(self):
        self.assertEqual(self.split("""a\\ b 'c d' "e\\"f" $'g'""", "bash"), [(["a b", "c d", 'e"f', "$g"], [], False)])
        self.assertEqual(self.split("""C:\\x\\y 'it''s' "a`"b" "c""d" a`;b""", "powershell"),
                         [(["C:\\x\\y", "it's", 'a"b', 'c"d', "a;b"], [], False)])

    def test_comments_go(self):
        self.assertEqual(self.split("a # b; c\nd", "bash"), [(["a"], [], False), (["d"], [], False)])
        self.assertEqual(self.split("a <# b; c #> d # e\nf", "powershell"),
                         [(["a", "d"], [], False), (["f"], [], False)])

    def test_redirections_are_not_arguments(self):
        self.assertEqual(self.split("a x > out.txt 2>&1 < in.txt", "bash"),
                         [(["a", "x"], ["out.txt", "1", "in.txt"], False)])
        self.assertEqual(self.split("a x *> out.txt", "powershell"), [(["a", "x"], ["out.txt"], False)])

    def test_the_powershell_call_operator(self):
        self.assertEqual(self.split("& 'C:\\Program Files\\x.exe' -a; b &", "powershell"),
                         [(["C:\\Program Files\\x.exe", "-a"], [], True), (["b"], [], False)])

    def test_here_strings_and_stop_parsing(self):
        self.assertEqual(self.split("iex @'\nlprun9 -x\n'@", "powershell"), [(["iex", "lprun9 -x"], [], False)])
        self.assertEqual(self.split('lprun9 --% -cxname="a b" x.linq; y', "powershell"),
                         [(["lprun9", "--%", "-cxname=a b", "x.linq;", "y"], [], False)])

    def test_an_unclosed_quote_is_an_error(self):
        for line, shell in [('a "b', "bash"), ("a 'b", "powershell"), ("a <# b", "powershell")]:
            with self.subTest(line=line):
                with self.assertRaises(ValueError):
                    self.gate.split_commands(line, shell)

    def test_the_program_comes_after_assignments_and_keywords(self):
        index = self.gate.program_index
        self.assertEqual(index(["FOO=1", "BAR=", "timeout", "5", "x"], "bash"), (2, "timeout"))
        self.assertEqual(index(["if", "!", "x"], "bash"), (2, "x"))
        self.assertEqual(index(["$v", "=", "x", "y"], "powershell"), (2, "x"))
        self.assertEqual(index(["$v=x", "y"], "powershell"), (0, "x"))
        self.assertEqual(index([".", "x"], "powershell"), (1, "x"))
        self.assertIsNone(index(["for", "x", "in", "y"], "bash"))
        self.assertIsNone(index(["A=1"], "bash"))

    def test_base_name_compares_as_windows_does(self):
        base = self.gate.base_name
        self.assertEqual(base("C:\\Program Files\\LINQPad9\\LPRun9.exe"), "lprun9")
        self.assertEqual(base("/c/Program Files/LINQPad9/LPRun9-x86.EXE. "), "lprun9-x86")
        self.assertEqual(base("lp^run9.cmd"), "lprun9")
        self.assertEqual(base("python3"), "python3")

    def test_what_feeds_standard_input(self):
        def fed(line, shell):
            return [c.reads_input for c in self.gate.split_commands(line, shell)]

        self.assertEqual(fed("a | b < in.txt; c <<< x; d > out.txt 2>&1; e 0< in.txt", "bash"),
                         [False, True, True, False, True])
        self.assertEqual(fed("a <<'EOF'\nbody\nEOF\nb |& c", "bash"), [True, False, True])
        self.assertEqual(fed("a | { b; c; }; d | (e)", "bash"), [False, True, False, False, True])
        self.assertEqual(fed("a | b; c | & d", "powershell"), [False, True, False, True])
        heredocs = self.gate.split_commands("a <<'A' | b <<'B'; c\none\nA\ntwo\nB", "bash")
        self.assertEqual([(c.words, c.stdin) for c in heredocs], [(["a"], ["one"]), (["b"], ["two"]), (["c"], [])])

    def test_a_program_behind_wrappers(self):
        gate = self.gate

        def run(line, shell="bash", **options):
            python = lambda word: gate.base_name(word) in ("python", "python3", "py")
            return gate.find_run(gate.split_commands(line, shell)[0], python, **options)

        self.assertEqual(run("python3 read.py query"), (0, False, False))
        self.assertEqual(run("FOO=1 timeout 120 python3 read.py query"), (3, True, False))
        self.assertEqual(run("xargs -n1 python3 read.py query"), (2, True, True))
        self.assertEqual(run("& 'C:\\Python312\\python.exe' read.py query", "powershell"), (0, False, False))
        self.assertEqual(run("Start-Process py -ArgumentList 'read.py query'", "powershell"), (1, True, False))
        self.assertIsNone(run("echo python3 read.py query"))
        self.assertIsNone(run("python3 read.py query", direct=False))
        self.assertEqual(run("timeout 5 python3 x", direct=False), (2, True, False))

    def test_the_shared_section_stands_on_its_own(self):
        """sql-read's gate keeps a copy of this section, so every name the section uses is defined in it, or is json,
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


if __name__ == "__main__":
    unittest.main()
