"""The Insomnia source, run with: python3 -m unittest discover -s skills/api-call/tests

Each test writes an Insomnia data folder of NeDB files to a temporary directory and drives call.py --via insomnia on
it, as a user would, or loads the source's functions into pwsh to try one on its own. Needs PowerShell 7 (pwsh).

Offline tests point the base URL at https://127.0.0.1:1, which refuses at once: a run that ends in "the request
failed" got through every check made before sending. Tests that need a response call postman-echo.com or httpbin.org
with fake secrets, and run only when API_CALL_E2E is set; each retries once on a connection error or a status other
than the one expected. Test names carry the id of the review finding each covers; r2_NN is attack NN of the second
review round.
"""
import base64, json, os, re, shutil, subprocess, sys, tempfile, unittest, urllib.parse
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "api-call" / "scripts"
SOURCE = SCRIPTS / "sources" / "insomnia.ps1"
sys.path.insert(0, str(SCRIPTS))
import render  # noqa: E402

PWSH = shutil.which("pwsh") or next((p for p in ("/opt/homebrew/bin/pwsh", "/usr/local/bin/pwsh", "/usr/bin/pwsh")
                                     if os.path.exists(p)), None)
E2E = bool(os.environ.get("API_CALL_E2E"))
OFFLINE = "https://127.0.0.1:1"
ECHO = "https://postman-echo.com"
HTTPBIN = "https://httpbin.org"
# pwsh on Unix takes its console encoding from the locale; PYTHONUTF8 keeps call.py's own Python on UTF-8 regardless.
SINGLE_BYTE_CONSOLE = {"LC_ALL": "en_US.ISO8859-1", "LANG": "en_US.ISO8859-1", "PYTHONUTF8": "1"}
REFUSED = "refusing to print: a secret would appear in the output"
SENT = "the request failed"

needs_pwsh = unittest.skipUnless(PWSH, "PowerShell 7 (pwsh) is not installed")
needs_network = unittest.skipUnless(E2E, "calls postman-echo.com or httpbin.org; set API_CALL_E2E=1 to run")


def folder(name, auth=None, headers=(), environment=None):
    """A top-level folder: auth as Insomnia stores it, headers as (name, value) pairs, its own environment."""
    return {"name": name, "authentication": auth or {}, "headers": [{"name": n, "value": v} for n, v in headers],
            "environment": environment or {}}


def bearer(token="{{ token }}"):
    return {"type": "bearer", "token": token}


def apikey(key, value="{{ apiKey }}", add_to="header"):
    return {"type": "apikey", "key": key, "value": value, "addTo": add_to}


def echo_body(body):
    """An httpbin path whose response body is body: a server echoing a value back, in a form of the test's choosing."""
    return "/base64/" + base64.urlsafe_b64encode(body).decode("ascii")


def write_data(root, base, folders=(), subs=None, certs=()):
    """Insomnia's data folder: global environment 'Env' (base data, sub-environments), collection 'Coll'."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)

    def db(name, docs):
        text = "".join(json.dumps(d, ensure_ascii=False) + "\n" for d in docs)
        (root / name).write_text(text, encoding="utf-8")

    db("insomnia.Workspace.db", [
        {"_id": "wrk_env", "type": "Workspace", "parentId": "proj", "name": "Env", "scope": "environment"},
        {"_id": "wrk_col", "type": "Workspace", "parentId": "proj", "name": "Coll", "scope": "collection"}])
    db("insomnia.Environment.db",
       [{"_id": "env_base", "type": "Environment", "parentId": "wrk_env", "name": "Base Environment", "data": base}] +
       [{"_id": f"env_sub{i}", "type": "Environment", "parentId": "env_base", "name": n, "data": d}
        for i, (n, d) in enumerate((subs or {}).items())])
    db("insomnia.RequestGroup.db", [{"_id": f"fld_{i}", "type": "RequestGroup", "parentId": "wrk_col", **f}
                                    for i, f in enumerate(folders)])
    db("insomnia.ClientCertificate.db", [{"_id": f"crt_{i}", "type": "ClientCertificate", "parentId": "wrk_col",
                                          "disabled": False, **c} for i, c in enumerate(certs)])
    return root


class Result:
    def __init__(self, proc, out):
        self.code = proc.returncode
        self.stdout = proc.stdout.decode("utf-8", "replace")
        self.stderr = proc.stderr.decode("utf-8", "replace")
        record = out / "r.json"
        self.record = json.loads(record.read_text(encoding="utf-8")) if record.exists() else None
        self.files = "".join(p.read_text(encoding="utf-8", errors="replace") for p in out.glob("r.*") if p.suffix != ".png")

    def __repr__(self):
        return f"exit {self.code}\nstdout: {self.stdout[-800:]}\nstderr: {self.stderr[-1500:]}"


@needs_pwsh
class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="api-call-insomnia-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def data(self, base, folders=(), **kw):
        return write_data(self.tmp / "data", base, folders, **kw)

    def call(self, path, *options, expect=None, env=None, network=False):
        """call.py --via insomnia on this test's data; a network call is tried twice before it fails."""
        out = self.tmp / "out"
        argv = [sys.executable, str(SCRIPTS / "call.py"), "--via", "insomnia", "--env", "Env", "--collection", "Coll",
                "--out", str(out), "--name", "r", *options]
        if expect is not None:
            argv += ["--expect", str(expect)]
        argv += ["GET", path]
        environ = {**os.environ, "INSOMNIA_DATA": str(self.tmp / "data"),
                   "PATH": os.path.dirname(PWSH) + os.pathsep + os.environ.get("PATH", ""), **(env or {})}
        for attempt in (1, 2):
            shutil.rmtree(out, ignore_errors=True)
            result = Result(subprocess.run(argv, capture_output=True, env=environ, timeout=300), out)
            flaky = result.code == 3 or (result.code == 1 and SENT in result.stderr)
            if not (network and flaky and attempt == 1):
                return result

    def assertRefused(self, result, *secrets):
        self.assertEqual(result.code, 1, result)
        self.assertIn(REFUSED, result.stderr, result)
        self.assertIsNone(result.record, result)
        for s in secrets:
            for text in (result.stdout, result.stderr, result.files):
                self.assertNotIn(s.strip(), text)

    def assertReachedSend(self, result):
        """The run passed every check made before sending, and failed only to connect to OFFLINE."""
        self.assertEqual(result.code, 1, result)
        self.assertIn(SENT, result.stderr, result)

    def functions(self, body, env=None):
        """Runs body in pwsh with the source's functions loaded from it, and the script-level state they use
        ($secrets, $vars, $extended, $templateRx, $mark) set as the source sets it; returns the JSON it prints last."""
        # ASCII out, so that no console code page can alter what comes back.
        prelude = ("$ErrorActionPreference = 'Stop'\n"
                   "$PSDefaultParameterValues['ConvertTo-Json:EscapeHandling'] = 'EscapeNonAscii'\n"
                   "$Environment = 'Env'; $Collection = 'Coll'\n"
                   "$ast = [Management.Automation.Language.Parser]::ParseFile($env:INSOMNIA_PS1, [ref]$null, [ref]$null)\n"
                   "foreach ($f in $ast.FindAll({ param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] }, $true)) "
                   "{ . ([scriptblock]::Create($f.Extent.Text)) }\n"
                   "foreach ($s in $ast.EndBlock.Statements) { if ($s -is [Management.Automation.Language.AssignmentStatementAst] -and "
                   "$s.Left -is [Management.Automation.Language.VariableExpressionAst] -and "
                   "$s.Left.VariablePath.UserPath -in 'secrets', 'vars', 'extended', 'templateRx', 'mark') "
                   "{ . ([scriptblock]::Create($s.Extent.Text)) } }\n")
        encoded = base64.b64encode((prelude + body).encode("utf-16-le")).decode("ascii")
        proc = subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded], capture_output=True,
                              env={**os.environ, "INSOMNIA_PS1": str(SOURCE), **(env or {})}, timeout=120)
        out = proc.stdout.decode("utf-8", "replace")
        self.assertEqual(proc.returncode, 0, out + proc.stderr.decode("utf-8", "replace"))
        return json.loads(out.strip().splitlines()[-1])


class Refusal(Case):
    """secrets-1, secrets-2 and docs-1: the one check that keeps a secret out of the record, in every form."""

    @needs_network
    def test_secrets_1_folder_bearer_header_token_echoed_alone_is_refused(self):
        # No auth tab: the folder header carries the token. httpbin's /bearer echoes the token on its own.
        self.data({"baseUrl": HTTPBIN, "token": "FAKE-tok-7f3a9c2e"},
                  [folder("API", headers=[("Authorization", "Bearer {{ token }}")])])
        self.assertRefused(self.call("/bearer", "--folder", "API", expect=200, network=True), "FAKE-tok-7f3a9c2e")

    @needs_network
    def test_secrets_1_folder_cookie_header_value_echoed_alone_is_refused(self):
        self.data({"baseUrl": ECHO, "sid": "FAKE-sid-5e1d0c4b"},
                  [folder("API", headers=[("Cookie", "sid={{ _.sid }}")])])
        self.assertRefused(self.call("/cookies", "--folder", "API", expect=200, network=True), "FAKE-sid-5e1d0c4b")

    @needs_network
    def test_secrets_2_trimmed_echo_of_padded_api_key_is_refused(self):
        # HTTP drops the whitespace around a header value, so the echo comes back trimmed.
        for padded in ("FAKE-key-4b8d2e61 ", " FAKE-key-4b8d2e61", "FAKE-key-4b8d2e61\t"):
            with self.subTest(padded=padded):
                self.data({"baseUrl": ECHO, "apiKey": padded}, [folder("API", apikey("X-Api-Key"))])
                self.assertRefused(self.call("/headers", expect=200, network=True), padded)

    @needs_network
    def test_secrets_2_trimmed_echo_of_padded_bearer_token_is_refused(self):
        self.data({"baseUrl": HTTPBIN, "token": "FAKE-tok-7f3a9c2e "}, [folder("API", bearer())])
        self.assertRefused(self.call("/bearer", expect=200, network=True), "FAKE-tok-7f3a9c2e")

    @needs_network
    def test_docs_1_json_escaped_echo_is_refused(self):
        # As ASP.NET Core's System.Text.Json writes it: + as +; and / as \/, as PHP writes it. render.py would
        # decode both and draw the key in clear.
        key = "abc+def/ghi12=="
        body = b'{"error":"invalid key abc\\u002Bdef\\/ghi12=="}'
        self.data({"baseUrl": HTTPBIN, "apiKey": key}, [folder("API", apikey("X-Api-Key"))])
        path = "/base64/" + base64.urlsafe_b64encode(body).decode("ascii")
        self.assertRefused(self.call(path, expect=200, network=True), key)

    @needs_network
    def test_docs_1_non_ascii_query_key_echoed_escaped_is_refused(self):
        # httpbin (Flask) writes JSON with non-ASCII escaped: the key comes back as fake-ü-1234567.
        key = "fake-ü-1234567"
        self.data({"baseUrl": HTTPBIN, "apiKey": key}, [folder("API", apikey("key", add_to="queryParams"))])
        self.assertRefused(self.call("/anything", expect=200, network=True), key)

    @needs_network
    def test_a_secret_the_response_does_not_echo_is_masked_not_refused(self):
        self.data({"baseUrl": ECHO, "token": "FAKE-tok-7f3a9c2e"}, [folder("API", bearer(), headers=[("X-Flag", "{{ f }}")])],
                  subs={"Flags": {"f": "FAKE-flag-1a2b3c"}})
        r = self.call("/status/200", "--env", "Env/Flags", expect=200, network=True)
        self.assertEqual(r.code, 0, r)
        self.assertEqual(r.record["request_headers"]["Authorization"], "Bearer ********")
        self.assertEqual(r.record["request_headers"]["X-Flag"], "********")
        self.assertNotIn("FAKE-tok-7f3a9c2e", r.files)
        self.assertNotIn("FAKE-flag-1a2b3c", r.files)

    def test_secrets_2_and_docs_1_every_form_of_a_secret_is_found(self):
        found = self.functions(r"""
            $cases = @(
                @('plain', 'x FAKE-key-4b8d2e61 y', 'FAKE-key-4b8d2e61'),
                @('trimmed', '{"x-api-key":"FAKE-key-4b8d2e61"}', 'FAKE-key-4b8d2e61 '),
                @('trimmed newline', 'FAKE-key-4b8d2e61', "`nFAKE-key-4b8d2e61`r`n"),
                @('url-encoded', 'https://h/x?key=abc%2Bdef%2Fghi12%3D%3D', 'abc+def/ghi12=='),
                @('url-encoded lower case', 'https://h/x?key=abc%2bdef%2fghi12%3d%3d', 'abc+def/ghi12=='),
                @('plus for space', 'q=my+secret+value', 'my secret value'),
                @('json \u escape', '{"error":"invalid key abc+def\/ghi12=="}', 'abc+def/ghi12=='),
                @('json non-ascii escape', '{"key":"fake-ü-1234567"}', "fake-$([char]0xfc)-1234567"),
                @('json quote escape', '{"k":"pa\"ss\\word1"}', 'pa"ss\word1'),
                @('json escaped url', '{"url":"https:\/\/h\/x?key=abc%2Bdef%2Fghi12%3D%3D"}', 'abc+def/ghi12=='),
                @('too short', 'abc12', 'abc12'),
                @('absent', '{"ok":true}', 'FAKE-key-4b8d2e61')
            )
            $out = [ordered]@{}
            foreach ($c in $cases) { $out[$c[0]] = Test-Exposed @($c[1]) @($c[2]) }
            $out | ConvertTo-Json -Compress
        """)
        expected = {k: True for k in found}
        expected.update({"too short": False, "absent": False})
        self.assertEqual(found, expected)


class ErrorMessages(Case):
    def test_error_message_is_scrubbed_in_every_form(self):
        # The trap's scrub: a .NET error quoting the URL carries a query key URL-encoded.
        r = self.functions(r"""
            $list = [Collections.Generic.List[string]]::new(); $list.Add('abc+def/ghi12=='); $list.Add('FAKE-tok-7f3a9c2e ')
            @{
                encoded = Hide-Secrets 'failed: https://h/x?key=abc%2Bdef%2Fghi12%3D%3D' $list
                trimmed = Hide-Secrets 'token FAKE-tok-7f3a9c2e rejected' $list
                lower = Hide-Secrets 'failed: https://h/x?key=abc%2bdef%2fghi12%3d%3d' $list
                partly = Hide-Secrets 'failed: https://h/x?key=abc+def%2Fghi12==' $list
            } | ConvertTo-Json -Compress
        """)
        self.assertEqual(r["encoded"], "failed: https://h/x?key=********")
        self.assertEqual(r["trimmed"], "token ******** rejected")
        self.assertEqual(r["lower"], "failed: https://h/x?key=********")
        self.assertNotIn("ghi12", r["partly"])

    def test_correctness_10_error_message_reaches_call_py_as_utf8(self):
        # A single-byte console encoding, as a Windows console's OEM code page is: pwsh must still write UTF-8,
        # which is what call.py reads. On Windows the console's own code page plays the part of LC_ALL.
        self.data({"baseUrl": OFFLINE}, [])
        r = self.call("/x", "--env", "Café ✓", env=SINGLE_BYTE_CONSOLE)
        self.assertEqual(r.code, 1, r)
        self.assertNotIn("UnicodeDecodeError", r.stderr, r)
        self.assertIn("named 'Café ✓'", r.stderr, r)


class Encoding(Case):
    @needs_network
    def test_correctness_10_non_ascii_body_survives_a_single_byte_console(self):
        self.data({"baseUrl": ECHO}, [])
        r = self.call("/get?name=caf%C3%A9&check=%E2%9C%93&sep=%E2%80%A8", expect=200, network=True, env=SINGLE_BYTE_CONSOLE)
        self.assertEqual(r.code, 0, r)
        args = json.loads(r.record["body"])["args"]
        self.assertEqual((args["name"], args["check"], args["sep"]), ("café", "✓", " "))

    @needs_network
    def test_correctness_15_unknown_charset_is_recorded_not_a_failure(self):
        # httpbin also sets each parameter as a response header, so the word is ASCII here; the unit test below
        # covers the decoding.
        self.data({"baseUrl": HTTPBIN}, [])
        r = self.call("/response-headers?Content-Type=application/json;%20charset=utf8&word=cafe", expect=200, network=True)
        self.assertEqual(r.code, 0, r)
        self.assertEqual(r.record["status"], 200)
        self.assertIn("charset=utf8", r.record["response_headers"]["Content-Type"])
        self.assertEqual(json.loads(r.record["body"])["word"], "cafe")

    def test_correctness_15_body_is_decoded_by_bom_then_known_charset_then_utf8(self):
        r = self.functions(r"""
            $cafe = [Text.Encoding]::UTF8.GetBytes("caf$([char]0xe9)")
            [ordered]@{
                'utf8 misspelt' = ConvertFrom-Body $cafe 'utf8'
                'unknown' = ConvertFrom-Body $cafe 'binary'
                'none' = ConvertFrom-Body $cafe $null
                'quoted' = ConvertFrom-Body $cafe '"utf-8"'
                'latin1' = ConvertFrom-Body ([byte[]](0x63, 0x61, 0x66, 0xe9)) 'iso-8859-1'
                'utf-8 bom' = ConvertFrom-Body ([byte[]](@(0xef, 0xbb, 0xbf) + $cafe)) 'iso-8859-1'
                'utf-16 bom' = ConvertFrom-Body ([byte[]](@(0xff, 0xfe) + [Text.Encoding]::Unicode.GetBytes("caf$([char]0xe9)"))) $null
                'empty' = ConvertFrom-Body ([byte[]]@()) 'utf8'
            } | ConvertTo-Json -Compress
        """)
        self.assertEqual(r, {"utf8 misspelt": "café", "unknown": "café", "none": "café", "quoted": "café",
                             "latin1": "café", "utf-8 bom": "café", "utf-16 bom": "café", "empty": ""})


class Timestamps(Case):
    @needs_network
    def test_correctness_3_sent_is_iso_and_gregorian_in_any_culture(self):
        # Finnish writes times with dots; Thai counts years in the Buddhist era. Unix reads the culture from
        # LC_ALL; on Windows the test runs in the user's own culture.
        self.data({"baseUrl": ECHO}, [])
        iso = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}[+-]\d{2}:\d{2}$")
        for culture in ("fi_FI.UTF-8", "th_TH.UTF-8", "en_US.UTF-8"):
            with self.subTest(culture=culture):
                r = self.call("/status/200", expect=200, network=True, env={"LC_ALL": culture, "LANG": culture})
                self.assertEqual(r.code, 0, r)
                self.assertRegex(r.record["sent"], iso)
                self.assertRegex(r.record["sent_utc"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")
                sent = datetime.fromisoformat(r.record["sent"])
                self.assertLess(abs((datetime.now(timezone.utc) - sent).total_seconds()), 600)
                self.assertEqual(sent, datetime.fromisoformat(r.record["sent_utc"].replace("Z", "+00:00")))
                render.rows(r.record)  # what --shot draws parses it


class Auth(Case):
    def test_correctness_5_no_auth_folder_alone_is_no_auth(self):
        self.data({"baseUrl": OFFLINE}, [folder("Public", {"type": "none"})])
        self.assertReachedSend(self.call("/x"))
        self.assertReachedSend(self.call("/x", "--folder", "Public"))

    def test_correctness_5_no_auth_folder_beside_an_auth_folder_is_not_counted(self):
        self.data({"baseUrl": OFFLINE, "token": "FAKE-tok-7f3a9c2e"},
                  [folder("Public", {"type": "none"}), folder("Private", bearer())])
        self.assertReachedSend(self.call("/x"))

    @needs_network
    def test_correctness_5_record_of_a_no_auth_folder_beside_an_auth_folder(self):
        self.data({"baseUrl": ECHO, "token": "FAKE-tok-7f3a9c2e"},
                  [folder("Public", {"type": "none"}), folder("Private", bearer())])
        r = self.call("/status/200", expect=200, network=True)
        self.assertEqual(r.code, 0, r)
        self.assertEqual((r.record["folder"], r.record["auth"]), ("Private", "bearer token"))
        r = self.call("/status/200", "--folder", "Public", expect=200, network=True)
        self.assertEqual(r.code, 0, r)
        self.assertEqual((r.record["folder"], r.record["auth"]), ("Public", "none"))
        self.assertNotIn("Authorization", r.record["request_headers"])

    @needs_network
    def test_correctness_6_api_key_added_to_cookie_is_sent_as_a_cookie(self):
        # A value under six characters is not checked for, so the echo of it may be printed.
        self.data({"baseUrl": ECHO, "apiKey": "ab1c2"},
                  [folder("API", apikey("session", add_to="cookie"), headers=[("Cookie", "theme=dark")])])
        r = self.call("/cookies", expect=200, network=True)
        self.assertEqual(r.code, 0, r)
        self.assertEqual(r.record["auth"], "API key in cookie session")
        self.assertEqual(r.record["request_headers"]["Cookie"], "session=********; theme=dark")
        self.assertNotIn("session", r.record["request_headers"])
        self.assertEqual(json.loads(r.record["body"])["cookies"], {"session": "ab1c2", "theme": "dark"})

    @needs_network
    def test_correctness_6_api_key_cookie_echoed_is_refused(self):
        self.data({"baseUrl": ECHO, "apiKey": "FAKE-ses-9a8b7c6d"}, [folder("API", apikey("session", add_to="cookie"))])
        self.assertRefused(self.call("/cookies", expect=200, network=True), "FAKE-ses-9a8b7c6d")

    def test_correctness_6_api_key_added_anywhere_else_is_refused(self):
        self.data({"baseUrl": OFFLINE, "apiKey": "FAKE-key-4b8d2e61"}, [folder("API", apikey("k", add_to="body"))])
        r = self.call("/x")
        self.assertEqual(r.code, 1, r)
        self.assertIn("'body', which is not supported", r.stderr, r)


class Headers(Case):
    def test_secrets_4_line_break_in_a_folder_header_value_is_refused(self):
        for value in ("ok\r\nX-Injected: yes", "t-{{ token }}\r\n\r\nGET /smuggled HTTP/1.1\r\nX-Pad: x", "ok\nX-Injected: yes"):
            with self.subTest(value=value):
                self.data({"baseUrl": OFFLINE, "token": "FAKE-tok-7f3a9c2e"}, [folder("API", headers=[("X-Note", value)])])
                r = self.call("/x", "--folder", "API")
                self.assertEqual(r.code, 1, r)
                self.assertNotIn(SENT, r.stderr, r)
                self.assertIn("header 'X-Note' on folder 'API' holds a line break", r.stderr, r)
                self.assertNotIn("FAKE-tok-7f3a9c2e", r.stderr)

    def test_secrets_4_line_break_from_a_variable_is_refused(self):
        self.data({"baseUrl": OFFLINE, "token": "FAKE-tok-7f3a9c2e\r\nX-Injected: yes"}, [folder("API", bearer())])
        r = self.call("/x")
        self.assertEqual(r.code, 1, r)
        self.assertNotIn(SENT, r.stderr, r)
        self.assertIn("header 'Authorization' on folder 'API' holds a line break", r.stderr, r)

    def test_secrets_4_line_break_in_a_folder_header_name_is_refused(self):
        self.data({"baseUrl": OFFLINE}, [folder("API", headers=[("X-A\r\nX-Injected", "yes")])])
        r = self.call("/x", "--folder", "API")
        self.assertEqual(r.code, 1, r)
        self.assertNotIn(SENT, r.stderr, r)
        self.assertIn("a header name on folder 'API' holds a line break", r.stderr, r)

    def test_an_invalid_header_name_is_refused_not_dropped(self):
        self.data({"baseUrl": OFFLINE}, [folder("API", headers=[("X Bad", "yes")])])
        r = self.call("/x", "--folder", "API")
        self.assertEqual(r.code, 1, r)
        self.assertIn("header 'X Bad' on folder 'API' is not a valid HTTP header name", r.stderr, r)

    @needs_network
    def test_correctness_14_content_type_on_a_get_is_not_claimed_as_sent(self):
        self.data({"baseUrl": ECHO}, [folder("API", headers=[("Content-Type", "application/json"), ("X-Tenant", "contoso")])])
        r = self.call("/headers", "--folder", "API", expect=200, network=True)
        self.assertEqual(r.code, 0, r)
        self.assertNotIn("Content-Type", r.record["request_headers"])
        self.assertEqual(r.record["request_headers"]["X-Tenant"], "contoso")
        received = json.loads(r.record["body"])["headers"]
        self.assertNotIn("content-type", received)
        self.assertEqual(received["x-tenant"], "contoso")


class Values(Case):
    @needs_network
    def test_extra_3_literal_iso_date_header_is_sent_as_typed(self):
        self.data({"baseUrl": ECHO}, [folder("API", headers=[("X-As-Of", "2026-10-08T23:30:00Z"),
                                                            ("X-Since", "2026-10-08T12:00:00.123+02:00")])])
        r = self.call("/headers", "--folder", "API", expect=200, network=True)
        self.assertEqual(r.code, 0, r)
        self.assertEqual(r.record["request_headers"]["X-As-Of"], "2026-10-08T23:30:00Z")
        received = json.loads(r.record["body"])["headers"]
        self.assertEqual((received["x-as-of"], received["x-since"]), ("2026-10-08T23:30:00Z", "2026-10-08T12:00:00.123+02:00"))

    @needs_network
    def test_correctness_19_boolean_and_number_variables_render_as_insomnia_does(self):
        # A templated part of a header name is a secret, masked in the record; the echo shows what was sent. Each
        # value is under six characters, so its echo is not refused.
        self.data({"baseUrl": ECHO, "debug": True, "ratio": 1.10, "count": 3},
                  [folder("API", headers=[("X-Debug-{{ debug }}", "1"), ("X-Ratio-{{ ratio }}", "1"), ("X-N-{{ count }}", "1")])])
        r = self.call("/headers", "--folder", "API", expect=200, network=True)
        self.assertEqual(r.code, 0, r)
        self.assertEqual([k for k in r.record["request_headers"] if k.startswith("X-")],
                         ["X-Debug-********", "X-Ratio-********", "X-N-********"])
        received = json.loads(r.record["body"])["headers"]
        for name in ("x-debug-true", "x-ratio-1.1", "x-n-3"):
            self.assertIn(name, received)

    def test_extra_3_and_correctness_19_values_read_as_insomnia_holds_them(self):
        root = self.data({"baseUrl": OFFLINE, "since": "2026-10-08T12:00:00Z", "at": "2026-10-08T12:00:00.123+02:00",
                          "day": "2023-10-01", "debug": True, "off": False, "ratio": 1.10, "n": 3, "none": None,
                          "list": ["a", 1], "obj": {"a": 1}})
        r = self.functions(r"""
            $InsomniaDir = $env:DATA
            $d = (Read-InsomniaDb 'insomnia.Environment.db')['env_base']['data']
            $out = [ordered]@{}
            foreach ($k in 'since', 'at', 'day', 'debug', 'off', 'ratio', 'n', 'none', 'list', 'obj') { $out[$k] = Format-Value $d[$k] }
            $out['since type'] = $d['since'].GetType().Name
            $out | ConvertTo-Json -Compress
        """, env={"DATA": str(root)})
        self.assertEqual(r, {"since": "2026-10-08T12:00:00Z", "at": "2026-10-08T12:00:00.123+02:00", "day": "2023-10-01",
                             "debug": "true", "off": "false", "ratio": "1.1", "n": "3", "none": "", "list": "a,1",
                             "obj": "[object Object]", "since type": "String"})


class FolderEnvironment(Case):
    def test_extra_2_variable_only_in_the_folder_environment_resolves(self):
        self.data({"baseUrl": OFFLINE}, [folder("Partner API", apikey("X-Api-Key", "{{ partnerKey }}"),
                                                environment={"partnerKey": "FAKE-folder-222222"})])
        self.assertReachedSend(self.call("/x"))

    @needs_network
    def test_extra_2_folder_environment_overrides_the_global_one(self):
        # The global environment points at a host that refuses; the folder's at postman-echo, with its own key.
        self.data({"baseUrl": OFFLINE, "apiKey": "GKEY5"},
                  [folder("Partner API", apikey("X-Api-Key"), environment={"baseUrl": ECHO, "apiKey": "fk123"})])
        r = self.call("/headers", expect=200, network=True)
        self.assertEqual(r.code, 0, r)
        self.assertEqual((r.record["folder"], r.record["url"]), ("Partner API", ECHO + "/headers"))
        self.assertEqual(json.loads(r.record["body"])["headers"]["x-api-key"], "fk123")

    @needs_network
    def test_extra_2_folder_environment_value_extends_its_own_variable(self):
        # As in Insomnia, baseUrl = {{ baseUrl }}/status builds on the value beneath it.
        self.data({"baseUrl": ECHO}, [folder("Status", environment={"baseUrl": "{{ baseUrl }}/status"})])
        r = self.call("/200", "--folder", "Status", expect=200, network=True)
        self.assertEqual(r.code, 0, r)
        self.assertEqual(r.record["url"], ECHO + "/status/200")

    @needs_network
    def test_extra_2_secret_in_an_extended_variable_is_still_a_secret(self):
        # token = {{ token }}-v2 in the folder: the global token is a part of what is sent, so a response that
        # quotes that part alone is refused. httpbin's /base64 answers with a body of the test's choosing.
        self.data({"baseUrl": HTTPBIN, "token": "FAKE-tok-7f3a9c2e"},
                  [folder("API", bearer(), environment={"token": "{{ token }}-v2"})])
        path = "/base64/" + base64.urlsafe_b64encode(b'{"seen":"FAKE-tok-7f3a9c2e"}').decode("ascii")
        self.assertRefused(self.call(path, expect=200, network=True), "FAKE-tok-7f3a9c2e")


class ClientCertificates(Case):
    def test_correctness_23_certificate_host_ports_match_as_insomnia_matches_them(self):
        r = self.functions(r"""
            $cases = @(
                @('mtls.example.com:443', 'https://mtls.example.com/x', $true),
                @('mtls.example.com:443', 'https://mtls.example.com:443/x', $true),
                @('mtls.example.com', 'https://mtls.example.com:8443/x', $false),
                @('mtls.example.com:8443', 'https://mtls.example.com:8443/x', $true),
                @('mtls.example.com:8443', 'https://mtls.example.com/x', $false),
                @('https://mtls.example.com:443/', 'https://mtls.example.com/x', $true),
                @('*.example.com', 'https://a.example.com/x', $true),
                @('*.example.com', 'https://example.org/x', $false),
                @('mtls.example.com:*', 'https://mtls.example.com:8443/x', $true),
                @('mtls.example.com', 'https://mtls.example.com/x', $true)
            )
            ,@(foreach ($c in $cases) { if ((Test-HostMatch $c[0] ([Uri]$c[1])) -ne $c[2]) { "$($c[0]) on $($c[1])" } }) | ConvertTo-Json -Compress
        """)
        self.assertEqual(r, [], "host patterns that matched wrongly")

    @needs_network
    def test_correctness_23_certificate_for_host_443_is_presented(self):
        cert, key = self.tmp / "client.pem", self.tmp / "client.key"
        self.functions(r"""
            $rsa = [Security.Cryptography.RSA]::Create(2048)
            $req = [Security.Cryptography.X509Certificates.CertificateRequest]::new('CN=api-call test', $rsa,
                [Security.Cryptography.HashAlgorithmName]::SHA256, [Security.Cryptography.RSASignaturePadding]::Pkcs1)
            $c = $req.CreateSelfSigned([DateTimeOffset]::Now.AddDays(-1), [DateTimeOffset]::Now.AddDays(1))
            [IO.File]::WriteAllText($env:CERT, $c.ExportCertificatePem())
            [IO.File]::WriteAllText($env:KEY, $rsa.ExportPkcs8PrivateKeyPem())
            '{}'
        """, env={"CERT": str(cert), "KEY": str(key)})
        host = ECHO.split("//")[1]
        for pattern, shown in ((host + ":443", "client.pem"), (host + ":8443", None)):
            with self.subTest(pattern=pattern):
                self.data({"baseUrl": ECHO}, [], certs=[{"host": pattern, "cert": str(cert), "key": str(key)}])
                r = self.call("/status/200", expect=200, network=True)
                self.assertEqual(r.code, 0, r)
                self.assertEqual(r.record["client_cert"], shown)


class NestedVariables(Case):
    """Round 2, attacks 02, 03, 04, 20 and 24 (D1): a variable whose value holds a template is a secret, resolved, and
    so is every value substituted into it, at any depth and from any layer."""

    def test_r2_02_every_depth_is_registered_resolved(self):
        r = self.functions(r"""
            $vars['token'] = 'FAKE-{{ mid }}-x'; $vars['mid'] = 'inner-{{ _.leaf }}'; $vars['leaf'] = 'LEAF-0042'
            $sent = Resolve-Secret 'Bearer {{ token }}'
            [ordered]@{ sent = $sent; secrets = @($secrets); exposed = (Test-Exposed @('{"token":"FAKE-inner-LEAF-0042-x"}') $secrets) } |
                ConvertTo-Json -Compress
        """)
        self.assertEqual(r["sent"], "Bearer FAKE-inner-LEAF-0042-x")
        for s in ("FAKE-inner-LEAF-0042-x", "inner-LEAF-0042", "LEAF-0042"):
            self.assertIn(s, r["secrets"])
        self.assertTrue(r["exposed"])

    @needs_network
    def test_r2_02_03_04_nested_token_echoed_is_refused(self):
        # httpbin's /bearer echoes the token alone, resolved.
        cases = {"empty suffix": ({"token": "FAKE-tok-7f3a9c2e{{ suffix }}", "suffix": ""}, "FAKE-tok-7f3a9c2e"),
                 "inner stage": ({"token": "fake_{{ stage }}_NOTAKEY0nested0template", "stage": "test"},
                                 "fake_test_NOTAKEY0nested0template"),
                 "short parts": ({"token": "{{ a }}{{ b }}", "a": "Fk3x9", "b": "Qz7Lm"}, "Fk3x9Qz7Lm")}
        for label, (env, token) in cases.items():
            with self.subTest(label):
                self.data({"baseUrl": HTTPBIN, **env}, [folder("API", headers=[("Authorization", "Bearer {{ token }}")])])
                self.assertRefused(self.call("/bearer", "--folder", "API", expect=200, network=True), token)

    @needs_network
    def test_r2_20_24_inner_template_from_a_folder_or_sub_environment_is_refused(self):
        # The token goes in a folder header, inside a longer value, so that /bearer echoes a part of what was sent.
        header = [("Authorization", "Bearer {{ token }}")]
        with self.subTest("folder environment"):
            self.data({"baseUrl": HTTPBIN, "region": "eu"},
                      [folder("API", headers=header, environment={"token": "FAKE-folder-secret-{{ region }}"})])
            self.assertRefused(self.call("/bearer", "--folder", "API", expect=200, network=True), "FAKE-folder-secret-eu")
        with self.subTest("sub-environment"):
            self.data({"baseUrl": HTTPBIN, "ver": "2"}, [folder("API", headers=header)],
                      subs={"Prod": {"token": "FAKE-prod-56565656-v{{ ver }}"}})
            self.assertRefused(self.call("/bearer", "--folder", "API", "--env", "Env/Prod", expect=200, network=True),
                               "FAKE-prod-56565656-v2")


class SelfExtension(Case):
    """Round 2, attack 21: {{ _.token }} and {{ _['token'] }} extend their own variable, as {{ token }} does."""

    def test_r2_21_underscore_forms_extend_their_own_variable(self):
        for form in ("{{ _.token }}-v2", "{{ _['token'] }}-v2", '{{_["token"]}}-v2'):
            with self.subTest(form=form):
                self.data({"baseUrl": OFFLINE, "token": "FAKE-tok-7f3a9c2e"},
                          [folder("API", bearer(), environment={"token": form})])
                self.assertReachedSend(self.call("/x"))

    @needs_network
    def test_r2_21_value_beneath_an_underscore_extension_is_a_secret(self):
        self.data({"baseUrl": HTTPBIN, "token": "FAKE-tok-7f3a9c2e"},
                  [folder("API", bearer(), environment={"token": "{{ _.token }}-v2"})])
        self.assertRefused(self.call(echo_body(b'{"seen":"FAKE-tok-7f3a9c2e"}'), expect=200, network=True),
                           "FAKE-tok-7f3a9c2e")

    def test_a_variable_that_refers_to_itself_is_an_error_that_quotes_no_value(self):
        self.data({"baseUrl": OFFLINE, "token": "FAKE-{{ other }}", "other": "x-{{ token }}"}, [folder("API", bearer())])
        r = self.call("/x")
        self.assertEqual(r.code, 1, r)
        self.assertIn("variable 'token' in 'Env' nests templates more than five deep, or refers to itself", r.stderr, r)


class CaseSensitiveNames(Case):
    """Round 2, attack 36 (D5): variable names are case-sensitive, as in Insomnia."""

    def test_r2_36_a_name_in_another_case_is_another_variable(self):
        self.data({"baseUrl": OFFLINE, "apiKey": "FAKE-key-4b8d2e61"}, [folder("API", apikey("X-Api-Key", "{{ APIKEY }}"))])
        r = self.call("/x")
        self.assertEqual(r.code, 1, r)
        self.assertIn("variable 'APIKEY' is not in environment 'Env'", r.stderr, r)

    @needs_network
    def test_r2_36_names_that_differ_in_case_send_their_own_values(self):
        self.data({"baseUrl": ECHO, "apiKey": "pub12", "APIKEY": "prd99"}, [folder("API", apikey("X-Api-Key"))])
        r = self.call("/headers", expect=200, network=True)
        self.assertEqual(r.code, 0, r)
        self.assertEqual(json.loads(r.record["body"])["headers"]["x-api-key"], "pub12")


class TemplatedNames(Case):
    """Round 2, attacks 16, 17 and 18 (D1): a templated part of a header name, of an API key's name or of the bearer
    prefix is a secret: masked in the record, and refused when echoed, in whatever case the server echoes it."""

    ENV = {"baseUrl": ECHO, "token": "FAKE-tok-7f3a9c2e", "apiKey": "FAKE-key-4b8d2e61", "scheme": "FAKE-pfx-31415926",
           "hn": "FAKE-hn-27182818", "kn": "FAKE-kn-16180339"}

    def test_r2_17_resolve_shown_masks_and_registers_each_part(self):
        r = self.functions(r"""
            $vars['hn'] = 'FAKE-hn-27182818'; $vars['v'] = '2'
            $n = Resolve-Shown 'X-{{ hn }}-v{{ v }}'
            [ordered]@{ value = $n.Value; shown = (Format-Shown $n.Shown); secrets = @($secrets) } | ConvertTo-Json -Compress
        """)
        self.assertEqual((r["value"], r["shown"]), ("X-FAKE-hn-27182818-v2", "X-********-v********"))
        self.assertIn("FAKE-hn-27182818", r["secrets"])

    @needs_network
    def test_r2_16_17_18_templated_names_and_prefix_are_masked_in_the_record(self):
        cases = {
            "bearer prefix": (dict(bearer(), prefix="{{ scheme }}"), [],
                              {"auth": "bearer token"}, {"Authorization": "******** ********"}),
            "header name": (bearer(), [("X-{{ hn }}", "literal")], {}, {"X-********": "literal"}),
            "API key name": (apikey("X-{{ kn }}"), [], {"auth": "API key in header X-********"}, {"X-********": "********"}),
            "API key name in a query": (apikey("{{ kn }}", add_to="queryParams"), [],
                                        {"auth": "API key in query parameter ********",
                                         "url": ECHO + "/status/200?********=********"}, {}),
            "API key name in a cookie": (apikey("{{ kn }}", add_to="cookie"), [],
                                         {"auth": "API key in cookie ********"}, {"Cookie": "********=********"}),
        }
        for label, (auth, headers, fields, shown) in cases.items():
            with self.subTest(label):
                self.data(self.ENV, [folder("API", auth, headers=headers)])
                r = self.call("/status/200", expect=200, network=True)
                self.assertEqual(r.code, 0, r)
                for field, value in fields.items():
                    self.assertEqual(r.record[field], value)
                for name, value in shown.items():
                    self.assertEqual(r.record["request_headers"].get(name), value, r.record["request_headers"])
                for name, secret in self.ENV.items():
                    if name != "baseUrl":
                        self.assertNotIn(secret.lower(), r.files.lower())

    @needs_network
    def test_r2_16_17_18_templated_names_and_prefix_echoed_are_refused(self):
        # postman-echo echoes header names lower-cased. The token and key are under six characters, so that only the
        # templated name or prefix can be what is refused.
        env = dict(self.ENV, token="tk123", apiKey="ak123")
        cases = {"bearer prefix": (dict(bearer(), prefix="{{ scheme }}"), [], "FAKE-pfx-31415926"),
                 "header name": ({}, [("X-{{ hn }}", "literal")], "FAKE-hn-27182818"),
                 "API key name": (apikey("X-{{ kn }}"), [], "FAKE-kn-16180339")}
        for label, (auth, headers, secret) in cases.items():
            with self.subTest(label):
                self.data(env, [folder("API", auth, headers=headers)])
                r = self.call("/headers", "--folder", "API", expect=200, network=True)
                self.assertRefused(r, secret)
                self.assertNotIn(secret.lower(), (r.stdout + r.stderr + r.files).lower())


class BaseUrl(Case):
    """Round 2, attacks 25 and 26 (D2): the base URL's own text is shown; a value substituted into it is masked in the
    record's url and refused when echoed; a password in its userinfo is masked whatever its source."""

    @needs_network
    def test_r2_25_key_in_the_base_url_echoed_is_refused(self):
        self.data({"baseUrl": HTTPBIN + "/anything/{{ fnKey }}", "fnKey": "FAKE-fnkey-8a7b6c5d"}, [])
        self.assertRefused(self.call("/x", expect=200, network=True), "FAKE-fnkey-8a7b6c5d")

    @needs_network
    def test_r2_25_value_substituted_into_the_base_url_is_masked_in_the_record(self):
        # .NET lower-cases the host it sends; the record shows the base URL's own text, with the part masked.
        self.data({"baseUrl": "https://{{ host }}/status", "host": "HttpBin.org"}, [])
        r = self.call("/204", expect=204, network=True)
        self.assertEqual(r.code, 0, r)
        self.assertEqual(r.record["url"], "https://********/status/204")
        self.assertNotIn("httpbin.org", r.files.lower())

    @needs_network
    def test_r2_26_password_in_the_base_url_userinfo_is_masked(self):
        for base, password in (("https://user:{{ pw }}@httpbin.org", "FAKE-pw-9z8y7x6w"),
                               ("https://user:FAKE-pw-literal-41@httpbin.org", "FAKE-pw-literal-41")):
            with self.subTest(base=base):
                self.data({"baseUrl": base, "pw": "FAKE-pw-9z8y7x6w"}, [])
                r = self.call("/status/204", expect=204, network=True)
                self.assertEqual(r.code, 0, r)
                self.assertEqual(r.record["url"], "https://user:********@httpbin.org/status/204")
                self.assertNotIn(password, r.files)

    @needs_network
    def test_r2_26_password_in_the_base_url_userinfo_echoed_is_refused(self):
        self.data({"baseUrl": "https://user:FAKE-pw-literal-41@httpbin.org"}, [])
        self.assertRefused(self.call(echo_body(b'{"pw":"FAKE-pw-literal-41"}'), expect=200, network=True),
                           "FAKE-pw-literal-41")


    @needs_network
    def test_userinfo_in_the_base_url_is_sent_as_basic_auth_as_insomnia_sends_it(self):
        # httpbin answers /basic-auth/<user>/<password> with 200 only when that basic auth arrives, 401 otherwise.
        # The endpoint needs the password in the path too, which the refusal would rightly stop, so the password here
        # is five characters, under the length the refusal checks for. The basic token itself is longer and checked.
        self.data({"baseUrl": "https://user:{{ pw }}@httpbin.org", "pw": "pw123"}, [])
        r = self.call("/basic-auth/user/pw123", expect=200, network=True)
        self.assertEqual(r.code, 0, r)
        self.assertEqual(r.record["status"], 200, r.record)
        self.assertEqual(r.record["request_headers"]["Authorization"], "Basic ********")
        self.assertEqual(r.record["auth"], "basic auth from the base URL as user")
        self.assertTrue(r.record["url"].startswith("https://user:********@httpbin.org/"), r.record["url"])
        self.assertNotIn("dXNlcjpwdzEyMw", r.files)  # base64 of user:pw123
        # the folder's own auth wins over the URL's credentials, as in Insomnia
        self.data({"baseUrl": "https://user:FAKE-pw-9z8y7x6w@httpbin.org", "token": "FAKE-tok-7f3a9c2e"},
                  [folder("API", bearer())])
        r = self.call("/status/204", "--folder", "API", expect=204, network=True)  # an endpoint that echoes nothing
        self.assertEqual(r.code, 0, r)
        self.assertEqual(r.record["auth"], "bearer token")
        self.assertEqual(r.record["request_headers"]["Authorization"], "Bearer ********")

class CookieKey(Case):
    """Round 2, attack 35: an API key that cannot travel as one cookie is refused before anything is sent."""

    def test_r2_35_cookie_key_holding_a_separator_is_refused_unsent(self):
        for name, value in (("session", "FAKE-part1;FAKE-part2"), ("session", "FAKE-part1,FAKE-part2"),
                            ("ses=sion", "FAKE-part1-part2"), ("ses;sion", "FAKE-part1-part2")):
            with self.subTest(name=name, value=value):
                self.data({"baseUrl": OFFLINE, "apiKey": value}, [folder("API", apikey(name, add_to="cookie"))])
                r = self.call("/x")
                self.assertEqual(r.code, 1, r)
                self.assertNotIn(SENT, r.stderr, r)
                self.assertIn("cannot be sent as one cookie", r.stderr, r)
                self.assertNotIn("FAKE-part", r.stderr)


class SecretHoldingBraces(Case):
    """Round 2, attack 38: a secret whose value holds {{ }} text is not quoted in the error that says it cannot be
    resolved; the error names the variable that holds it."""

    def test_r2_38_name_inside_a_secret_is_not_printed(self):
        cases = {"apiKey": ({"apiKey": "p4ss{{Hunter2-FAKE-pw}}w0rd"}, folder("API", apikey("X-Api-Key"))),
                 "token": ({"token": "FAKE-tok-7f3a9c2e"},
                           folder("API", bearer(), environment={"token": "{{ token }}-{{Hunter2-FAKE-pw}}"}))}
        for holder, (env, f) in cases.items():
            with self.subTest(holder):
                self.data({"baseUrl": OFFLINE, **env}, [f])
                r = self.call("/x")
                self.assertEqual(r.code, 1, r)
                self.assertIn(f"variable '{holder}' in 'Env' names a variable that is not there", r.stderr, r)
                for part in ("Hunter2", "p4ss", "w0rd"):
                    self.assertNotIn(part, r.stdout + r.stderr)


class HtmlReferences(Case):
    """Round 2, attack 33 and the escaped-echo review (D4): an echo in HTML character references is refused, and so is
    a Location whose JSON escapes .NET re-encoded as %5Cu."""

    def test_r2_33_html_references_and_a_reencoded_location_are_found(self):
        found = self.functions(r"""
            $cases = @(
                @('decimal', '{"k":"&#70;AKEsecret123"}', 'FAKEsecret123'),
                @('decimal without ;', '{"k":"&#70AKEsecret123"}', 'FAKEsecret123'),
                @('hex', '{"k":"&#x46;&#x41;KEsecret123"}', 'FAKEsecret123'),
                @('named', 'abc&plus;def&sol;ghi12&equals;&equals;', 'abc+def/ghi12=='),
                @('hex url characters', 'abc&#x2B;def&#x2F;ghi12&#x3D;&#x3D;', 'abc+def/ghi12=='),
                @('json-escaped reference', '{"k":"&#70;AKEsecret123"}', 'FAKEsecret123'),
                @('markup', 'a&lt;b&gt;c&amp;d&quot;e&apos;f', "a<b>c&d`"e'f"),
                @('location re-encoded', 'https://example.com/?k=%5Cu0046%5Cu0041KEsecret123', 'FAKEsecret123'),
                @('header name lower-cased', 'x-fake-hn-27182818: 1', 'FAKE-hn-27182818'),
                @('out of range', '&#x110000;&#0;&#xD800;&#99999999999;', 'FAKEsecret123')
            )
            $out = [ordered]@{}
            foreach ($c in $cases) { $out[$c[0]] = Test-Exposed @($c[1]) @($c[2]) }
            $out | ConvertTo-Json -Compress
        """)
        expected = {k: True for k in found}
        expected["out of range"] = False
        self.assertEqual(found, expected)

    @needs_network
    def test_r2_33_html_reference_echo_is_refused(self):
        self.data({"baseUrl": HTTPBIN, "apiKey": "FAKEsecret123"}, [folder("API", apikey("X-Api-Key"))])
        self.assertRefused(self.call(echo_body(b'{"k":"&#70;AKEsecret123"}'), expect=200, network=True), "FAKEsecret123")

    @needs_network
    def test_location_with_a_json_escaped_key_reencoded_by_dotnet_is_refused(self):
        key = "FAKEsecret123"
        target = "https://example.com/?k=" + "".join(f"\\u{ord(c):04X}" for c in key[:2]) + key[2:]
        self.data({"baseUrl": HTTPBIN, "apiKey": key}, [folder("API", apikey("X-Api-Key"))])
        path = "/redirect-to?" + urllib.parse.urlencode({"url": target, "status_code": 302})
        self.assertRefused(self.call(path, expect=302, network=True), key)


if __name__ == "__main__":
    unittest.main()
