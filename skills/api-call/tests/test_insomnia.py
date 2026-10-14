"""The Insomnia source, run with: python3 -m unittest discover -s skills/api-call/tests

Each test writes an Insomnia data folder of NeDB files to a temporary directory and drives call.py --via insomnia on
it, as a user would, or loads the source's functions into pwsh to try one on its own. Needs PowerShell 7 (pwsh).

Offline tests point the base URL at https://127.0.0.1:1, which refuses at once: a run that ends in "the request
failed" got through every check made before sending. Tests that need a response call postman-echo.com or httpbin.org
with fake secrets, and run only when API_CALL_E2E is set; each retries once on a connection error or a status other
than the one expected. Test names carry the id of the review finding each covers; r2_NN is attack NN of the second
review round, and r3 marks the third round's narrowed rule: a secret is a value in a credential position (an API
key's value, a bearer token, a templated header value save for a standard header that never carries one, a password
in the base URL and the basic token from it, with every part substituted into each), matched in its own letter case;
hosts, paths, names and labels are shown, with any secret in them masked.
"""
import base64, html, json, os, re, shutil, subprocess, sys, tempfile, time, unittest, urllib.parse
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "api-call" / "scripts"
SOURCE = SCRIPTS / "sources" / "insomnia.ps1"
sys.path.insert(0, str(SCRIPTS))
import render  # noqa: E402
sys.path.insert(0, str(SCRIPTS / "sources"))
import postman  # noqa: E402

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
UNPARSED = "the request failed: the base URL holds credentials that could not be parsed or reached"

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


def esc(ch):
    """ch as a message shows it in a name: a backslash, then u and four hex digits, or U and eight past the BMP."""
    cp = ord(ch)
    return "\\" + (f"u{cp:04x}" if cp <= 0xFFFF else f"U{cp:08x}")


def write_data(root, base, folders=(), subs=None, certs=(), collection="Coll", environment="Env", workspaces=()):
    """Insomnia's data folder: global environment 'Env' (base data, sub-environments), collection 'Coll', and any more
    workspaces, each a (scope, name) pair with nothing in it."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)

    def db(name, docs):
        text = "".join(json.dumps(d, ensure_ascii=False) + "\n" for d in docs)
        (root / name).write_text(text, encoding="utf-8")

    db("insomnia.Workspace.db", [
        {"_id": "wrk_env", "type": "Workspace", "parentId": "proj", "name": environment, "scope": "environment"},
        {"_id": "wrk_col", "type": "Workspace", "parentId": "proj", "name": collection, "scope": "collection"}] +
       [{"_id": f"wrk_more{i}", "type": "Workspace", "parentId": "proj", "name": n, "scope": s}
        for i, (s, n) in enumerate(workspaces)])
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
        ($secrets, $vars, $extended, $resolved, $longest, $templateRx, $htmlNames) set as the source sets it; returns
        the JSON it prints last."""
        # ASCII out, so that no console code page can alter what comes back.
        prelude = ("$ErrorActionPreference = 'Stop'\n"
                   "$PSDefaultParameterValues['ConvertTo-Json:EscapeHandling'] = 'EscapeNonAscii'\n"
                   "$Environment = 'Env'; $Collection = 'Coll'\n"
                   "$ast = [Management.Automation.Language.Parser]::ParseFile($env:INSOMNIA_PS1, [ref]$null, [ref]$null)\n"
                   "foreach ($f in $ast.FindAll({ param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] }, $true)) "
                   "{ . ([scriptblock]::Create($f.Extent.Text)) }\n"
                   "foreach ($s in $ast.EndBlock.Statements) { if ($s -is [Management.Automation.Language.AssignmentStatementAst] -and "
                   "$s.Left -is [Management.Automation.Language.VariableExpressionAst] -and "
                   "$s.Left.VariablePath.UserPath -in 'secrets', 'vars', 'extended', 'resolved', 'longest', 'templateRx', "
                   "'htmlNames') "
                   "{ . ([scriptblock]::Create($s.Extent.Text)) } }\n")
        encoded = base64.b64encode((prelude + body).encode("utf-16-le")).decode("ascii")
        proc = subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded], capture_output=True,
                              env={**os.environ, "INSOMNIA_PS1": str(SOURCE), **(env or {})}, timeout=120)
        out = proc.stdout.decode("utf-8", "replace")
        self.assertEqual(proc.returncode, 0, out + proc.stderr.decode("utf-8", "replace"))
        return json.loads(out.strip().splitlines()[-1])

    def exposed(self, cases):
        """Test-Exposed on each (label, text, secret), handed to pwsh as JSON: {label: whether the secret shows}."""
        return self.functions(r"""
            $out = [ordered]@{}
            foreach ($c in @($env:CASES | ConvertFrom-Json)) { $out[$c[0]] = Test-Exposed @($c[1]) @($c[2]) }
            $out | ConvertTo-Json -Compress
        """, env={"CASES": json.dumps(cases)})

    def timed_call(self, *args, **kw):
        """call, and how many seconds it took."""
        start = time.monotonic()
        r = self.call(*args, **kw)
        return r, time.monotonic() - start


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
        # httpbin (Flask) writes JSON with non-ASCII escaped: the key comes back as fake-\u00fc-1234567.
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
                @('json \u escape', ('{"error":"invalid key abc' + [char]0x5c + 'u002Bdef\/ghi12=="}'), 'abc+def/ghi12=='),
                @('json non-ascii escape', ('{"key":"fake-' + [char]0x5c + 'u00fc-1234567"}'), "fake-$([char]0xfc)-1234567"),
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
                'other case' = Hide-Secrets 'token fake-tok-7f3a9c2e rejected' $list
            } | ConvertTo-Json -Compress
        """)
        self.assertEqual(r["encoded"], "failed: https://h/x?key=********")
        self.assertEqual(r["trimmed"], "token ******** rejected")
        self.assertEqual(r["lower"], "failed: https://h/x?key=********")
        self.assertNotIn("ghi12", r["partly"])
        # r3: a secret is matched in its own letter case; the same letters in another case are another text
        self.assertEqual(r["other case"], "token fake-tok-7f3a9c2e rejected")

    def test_r3_a_failure_names_the_templated_host(self):
        # A host is configuration, not a secret, so the message says which one could not be reached.
        self.data({"baseUrl": "https://{{ host }}:1", "host": "127.0.0.1"}, [])
        r = self.call("/x")
        self.assertReachedSend(r)
        self.assertIn("127.0.0.1", r.stderr, r)
        self.assertNotIn("********", r.stderr, r)

    def test_r3_an_error_from_a_base_url_holding_an_at_quotes_no_part_of_it(self):
        # An unencoded / ? or # in a credential ends the authority before its @, so no userinfo is found and .NET takes
        # part of the credential for the host or the port: a DNS failure would name it, lowercased, where no mask can
        # find it. With an @ after the scheme, a URL that cannot be built or a request that fails is reported in words
        # that quote nothing. Each first half is a single label, which fails to resolve at once.
        cases = {"user with no password, holding a /": ("https://{{ tok }}@127.0.0.1:1", {"tok": "FAKEtok31415/abcdef12"}),
                 "user with no password, holding a ?": ("https://{{ tok }}@127.0.0.1:1", {"tok": "FAKEtok31415?abcdef12"}),
                 "user with no password, holding a #": ("https://{{ tok }}@127.0.0.1:1", {"tok": "FAKEtok31415#abcdef12"}),
                 "password holding an @, then a /": ("https://amy:{{ pw }}@127.0.0.1:1",
                                                     {"pw": "FAKEpw-2718@ssFAKE2718/word12"}),
                 "password .NET cannot build": ("https://amy:{{ pw }}@127.0.0.1:1", {"pw": "FAKEpw2718/rest99"})}
        for label, (base, env) in cases.items():
            with self.subTest(label):
                self.data({"baseUrl": base, **env}, [])
                r = self.call("/x")
                self.assertEqual(r.code, 1, r)
                self.assertIn(UNPARSED, r.stderr, r)
                for part in re.split(r"[/?#@]", next(iter(env.values()))):
                    self.assertNotIn(part.lower(), r.stderr.lower(), r)

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
        self.assertEqual((args["name"], args["check"], args["sep"]), ("café", "✓", "\u2028"))

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

    def test_r3_an_empty_api_key_or_bearer_token_stops_the_call_before_it_is_sent(self):
        # As in the Postman source: a credential that resolves to an empty value was never supplied, so nothing is
        # sent, and the message quotes no value. An empty password in the base URL is still sent, beside its user.
        cases = {"bearer token": (bearer(), {"token": ""}),
                 "bearer token of empty parts": (bearer("{{ a }}{{ b }}"), {"a": "", "b": None}),
                 "no bearer token at all": ({"type": "bearer"}, {}),
                 "API key in a header": (apikey("X-Api-Key"), {"apiKey": ""}),
                 "API key in a query parameter": (apikey("key", add_to="queryParams"), {"apiKey": ""}),
                 "API key in a cookie": (apikey("session", add_to="cookie"), {"apiKey": ""})}
        for label, (auth, env) in cases.items():
            with self.subTest(label):
                self.data({"baseUrl": OFFLINE, **env}, [folder("API", auth)])
                r = self.call("/x")
                self.assertEqual(r.code, 1, r)
                self.assertNotIn(SENT, r.stderr, r)
                what = "bearer token" if "bearer" in label else "API key"
                self.assertIn(f"the {what} on folder 'API' resolves to an empty value", r.stderr, r)
        self.data({"baseUrl": "https://FAKE-tok-31415926:@127.0.0.1:1"}, [])
        self.assertReachedSend(self.call("/x"))


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


    @needs_network
    def test_r3_a_templated_header_that_never_carries_a_credential_is_shown(self):
        # Accept: {{ accept }} = application/json is configuration: as a secret it would refuse every JSON response,
        # whose Content-Type carries it. So is a templated Content-Type, which a GET does not send, and which would
        # otherwise match the record's own Accept. Names compare in any letter case. httpbin's /headers echoes the
        # headers it received.
        env = {"baseUrl": HTTPBIN, "accept": "application/json", "ct": "application/json", "xrw": "XMLHttpRequest",
               "lang": "en-GB,en;q=0.8"}
        self.data(env, [folder("API", headers=[("Accept", "{{ accept }}"), ("content-type", "{{ ct }}"),
                                               ("x-requested-with", "{{ xrw }}"), ("Accept-Language", "{{ lang }}")])])
        r = self.call("/headers", "--folder", "API", expect=200, network=True)
        self.assertEqual(r.code, 0, r)
        shown = r.record["request_headers"]
        self.assertEqual((shown["Accept"], shown["x-requested-with"], shown["Accept-Language"]),
                         ("application/json", "XMLHttpRequest", "en-GB,en;q=0.8"))
        self.assertNotIn("content-type", [k.lower() for k in shown])
        received = json.loads(r.record["body"])["headers"]
        self.assertEqual((received["Accept"], received["X-Requested-With"]), ("application/json", "XMLHttpRequest"))
        self.data(env, [folder("API", headers=[("Content-Type", "{{ ct }}")])])
        r = self.call("/status/204", "--folder", "API", expect=204, network=True)
        self.assertEqual(r.code, 0, r)
        # Every other templated header value is still a secret, refused when a response carries it.
        self.data(env, [folder("API", headers=[("X-Format", "{{ accept }}")])])
        self.assertRefused(self.call("/json", "--folder", "API", expect=200, network=True))


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
        # A header name is configuration (r3): the record shows it resolved, as the echo does.
        self.data({"baseUrl": ECHO, "debug": True, "ratio": 1.10, "count": 3},
                  [folder("API", headers=[("X-Debug-{{ debug }}", "1"), ("X-Ratio-{{ ratio }}", "1"), ("X-N-{{ count }}", "1")])])
        r = self.call("/headers", "--folder", "API", expect=200, network=True)
        self.assertEqual(r.code, 0, r)
        self.assertEqual([k for k in r.record["request_headers"] if k.startswith("X-")],
                         ["X-Debug-true", "X-Ratio-1.1", "X-N-3"])
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

    def test_r3_an_extension_reads_the_other_values_of_its_layer_on_every_run(self):
        # The layer's other values are laid before its extensions are resolved, whatever order a hashtable would
        # give: section, defined only beside the extension, is always there. Each run is a new pwsh, with a new
        # string hash seed.
        self.data({"baseUrl": OFFLINE}, [], subs={"Test": {"baseUrl": "{{ baseUrl }}/{{ section }}", "section": "bytes"}})
        for run in range(6):
            with self.subTest(run=run):
                self.assertReachedSend(self.call("/201", "--env", "Env/Test"))

    @needs_network
    def test_r3_an_extension_uses_its_own_layer_value_on_every_run(self):
        self.data({"baseUrl": HTTPBIN, "section": "status"}, [],
                  subs={"Test": {"baseUrl": "{{ baseUrl }}/{{ section }}", "section": "anything"}})
        for run in range(3):
            with self.subTest(run=run):
                r = self.call("/x", "--env", "Env/Test", expect=200, network=True)
                self.assertEqual(r.code, 0, r)
                self.assertEqual(r.record["url"], HTTPBIN + "/anything/x")

    def test_r3_extensions_in_one_layer_resolve_in_the_file_order(self):
        # Two extensions that name each other: the first in the file reads the second as it was beneath; the second
        # reads the first as extended. A later layer's value does not change an extension already resolved.
        r = self.functions(r"""
            $data = [Text.Json.JsonDocument]::Parse($env:LAYERS)
            $layers = ConvertFrom-JsonElement $data.RootElement
            foreach ($layer in $layers) { Add-Layer $layer }
            [ordered]@{ a = (Get-Variable 'a' 'a' 1).Value; b = (Get-Variable 'b' 'b' 1).Value; c = (Get-Variable 'c' 'c' 1).Value;
                        keys = @($layers[1].Keys) } | ConvertTo-Json -Compress
        """, env={"LAYERS": '[{"a": "A", "b": "B", "v": "1"}, {"b": "{{ b }}-{{ a }}", "a": "{{ a }}-{{ b }}", '
                            '"c": "{{ v }}"}, {"a": "{{ a }}+", "v": "2"}]'})
        self.assertEqual(r["keys"], ["b", "a", "c"])
        self.assertEqual((r["a"], r["b"], r["c"]), ("A-B-A+", "B-A", "2"))


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

    def test_r3_the_default_base_url_variable_finds_one_spelt_in_another_case(self):
        for name in ("baseURL", "BaseUrl"):
            with self.subTest(name=name):
                self.data({name: OFFLINE}, [])
                self.assertReachedSend(self.call("/x"))

    def test_r3_the_default_base_url_variable_needs_a_single_match(self):
        self.data({"baseURL": OFFLINE, "BaseUrl": OFFLINE}, [])
        r = self.call("/x")
        self.assertEqual(r.code, 1, r)
        self.assertNotIn(SENT, r.stderr, r)
        self.assertIn("has no 'baseUrl' variable; variable names are case-sensitive, and it has 'BaseUrl' and 'baseURL'",
                      r.stderr, r)

    def test_r3_a_named_base_url_variable_and_other_templates_stay_case_sensitive(self):
        self.data({"ApiUrl": OFFLINE}, [])
        r = self.call("/x", "--base-url-var", "apiUrl")
        self.assertEqual(r.code, 1, r)
        self.assertIn("has no 'apiUrl' variable; variable names are case-sensitive, and it has 'ApiUrl'", r.stderr, r)
        self.data({"baseUrl": "https://{{ host }}:1", "Host": "127.0.0.1"}, [])
        r = self.call("/x")
        self.assertEqual(r.code, 1, r)
        self.assertIn("variable 'host' is not in environment 'Env'", r.stderr, r)


class TemplatedNames(Case):
    """Round 3, R2 (rewriting round 2's attacks 16, 17 and 18): a header name, an API key's name and the bearer prefix
    are configuration, not secrets, even when a variable fills them: the record shows them resolved, and a response
    that echoes them, in its own letter case or another, is printed. The values beside them stay secrets."""

    # The token and the key are under six characters, so that their own echo is not refused: only a name or the
    # prefix could be.
    ENV = {"baseUrl": ECHO, "token": "tk123", "apiKey": "ak123", "scheme": "FAKE-pfx-31415926",
           "hn": "FAKE-hn-27182818", "kn": "FAKE-kn-16180339"}

    @needs_network
    def test_r3_templated_names_and_prefix_are_shown_and_echoed_freely(self):
        # postman-echo's /headers echoes every header, its name lower-cased, and the query string is not echoed.
        cases = {
            "bearer prefix": (dict(bearer(), prefix="{{ scheme }}"), [], {"auth": "bearer token"},
                              {"Authorization": "FAKE-pfx-31415926 ********"}, ("authorization", "FAKE-pfx-31415926 tk123")),
            "header name": (bearer(), [("X-{{ hn }}", "literal")], {}, {"X-FAKE-hn-27182818": "literal"},
                            ("x-fake-hn-27182818", "literal")),
            "API key name": (apikey("X-{{ kn }}"), [], {"auth": "API key in header X-FAKE-kn-16180339"},
                             {"X-FAKE-kn-16180339": "********"}, ("x-fake-kn-16180339", "ak123")),
            "API key name in a query": (apikey("{{ kn }}", add_to="queryParams"), [],
                                        {"auth": "API key in query parameter FAKE-kn-16180339",
                                         "url": ECHO + "/headers?FAKE-kn-16180339=********"}, {}, None),
            "API key name in a cookie": (apikey("{{ kn }}", add_to="cookie"), [],
                                         {"auth": "API key in cookie FAKE-kn-16180339"},
                                         {"Cookie": "FAKE-kn-16180339=********"}, ("cookie", "FAKE-kn-16180339=ak123")),
        }
        for label, (auth, headers, fields, shown, echoed) in cases.items():
            with self.subTest(label):
                self.data(self.ENV, [folder("API", auth, headers=headers)])
                r = self.call("/headers", expect=200, network=True)
                self.assertEqual(r.code, 0, r)
                for field, value in fields.items():
                    self.assertEqual(r.record[field], value)
                for name, value in shown.items():
                    self.assertEqual(r.record["request_headers"].get(name), value, r.record["request_headers"])
                if echoed:
                    self.assertEqual(json.loads(r.record["body"])["headers"].get(echoed[0]), echoed[1])

    @needs_network
    def test_r3_a_templated_header_value_is_still_a_secret(self):
        # Beside a shown name, a value a variable fills is a secret, whole and in its parts.
        self.data(dict(self.ENV, flag="FAKE-flag-1a2b3c4d"), [folder("API", headers=[("X-{{ hn }}", "v-{{ flag }}")])])
        r = self.call("/headers", "--folder", "API", expect=200, network=True)
        self.assertRefused(r, "FAKE-flag-1a2b3c4d")


class BaseUrl(Case):
    """Round 3, R2 and R5 (rewriting round 2's attacks 25 and 26): the base URL is configuration, shown resolved and
    printed when a response echoes it, its scheme, host, port and path included, whatever variables fill them; only
    a password in its userinfo is a secret, typed or templated, with every part substituted into it. The password is
    found on the URL's text, so no template in the scheme, host or port can make it show."""

    @needs_network
    def test_r3_a_variable_in_the_base_url_path_is_shown_and_echoed_freely(self):
        self.data({"baseUrl": HTTPBIN + "/anything/{{ section }}", "section": "FAKE-section-8a7b6c5d"}, [])
        r = self.call("/x", expect=200, network=True)
        self.assertEqual(r.code, 0, r)
        self.assertEqual(r.record["url"], HTTPBIN + "/anything/FAKE-section-8a7b6c5d/x")
        self.assertIn("FAKE-section-8a7b6c5d", json.loads(r.record["body"])["url"])

    @needs_network
    def test_r3_a_secret_in_the_base_url_or_the_path_typed_is_masked_there_not_refused(self):
        # A templated header value is a secret, and so is an API key's value; the same value in the base URL, in the
        # path typed or as the user beside a password is configuration and the user's own input, never a response, so
        # the record shows it as ******** there and is printed. httpbin's 404 page echoes nothing.
        tenant = "FAKE-tenant-5d4c3b2a"
        header = [("X-Tenant-Id", "{{ tenant }}")]
        cases = {"base URL path": (HTTPBIN + "/tenants/{{ tenant }}", folder("API", headers=header), "/orders/42",
                                   HTTPBIN + "/tenants/********/orders/42", "/orders/42", "none"),
                 "path typed": (HTTPBIN, folder("API", headers=header), f"/tenants/{tenant}/orders",
                                HTTPBIN + "/tenants/********/orders", "/tenants/********/orders", "none"),
                 "API key in the base URL": (HTTPBIN + "/clients/{{ tenant }}",
                                             folder("API", apikey("X-Client-Id", "{{ tenant }}")), "/orders",
                                             HTTPBIN + "/clients/********/orders", "/orders",
                                             "API key in header X-Client-Id"),
                 "user beside a password": ("https://{{ tenant }}:pw123@httpbin.org", folder("API", headers=header),
                                            "/orders", "https://********:********@httpbin.org/orders", "/orders",
                                            "basic auth from the base URL as ********")}
        for label, (base, f, path, url, shown_path, auth) in cases.items():
            with self.subTest(label):
                self.data({"baseUrl": base, "tenant": tenant}, [f])
                r = self.call(path, "--folder", "API", expect=404, network=True)
                self.assertEqual(r.code, 0, r)
                self.assertEqual((r.record["url"], r.record["path"], r.record["auth"]), (url, shown_path, auth))
                self.assertNotIn(tenant, r.files)
        # A response that echoes it is still refused: /anything echoes the URL it was sent.
        self.data({"baseUrl": HTTPBIN + "/anything/{{ tenant }}", "tenant": tenant}, [folder("API", headers=header)])
        self.assertRefused(self.call("/x", "--folder", "API", expect=200, network=True), tenant)

    def test_r3_a_secret_in_the_url_is_masked_in_every_form_the_check_finds(self):
        # As is, percent-encoded in either case of hex, and as .NET writes it in a URL, which leaves a / as it is.
        # A value under six characters is not masked, nor one .NET reshapes (a \ turned to /), which reads as
        # another text; neither is found by the check.
        r = self.functions(r"""
            foreach ($s in 'FAKE-tenant-5d4c3b2a', 'FAKE tenant/5d4c3b2a', 'abc+def/ghi12==', "fake-$([char]0xfc)-1234567",
                    'v2', 'a\bcdefgh') { $secrets.Add($s) }
            $urls = @('https://h/tenants/FAKE-tenant-5d4c3b2a/orders', ([Uri]'https://h/t/FAKE tenant/5d4c3b2a/x').AbsoluteUri,
                      'https://h/x?k=abc%2Bdef%2Fghi12%3D%3D', 'https://h/x?k=abc%2bdef%2fghi12%3d%3d', 'https://h/abc+def/ghi12==',
                      ([Uri]"https://h/fake-$([char]0xfc)-1234567").AbsoluteUri, 'https://h/v2/x', ([Uri]'https://h/a\bcdefgh').AbsoluteUri)
            ,@(foreach ($u in $urls) { $m = Hide-SecretForms $u $secrets; ,@($u, $m, (Test-Exposed @($m) $secrets)) }) |
                ConvertTo-Json -Compress
        """)
        self.assertEqual([(u, m) for u, m, _ in r],
                         [("https://h/tenants/FAKE-tenant-5d4c3b2a/orders", "https://h/tenants/********/orders"),
                          ("https://h/t/FAKE%20tenant/5d4c3b2a/x", "https://h/t/********/x"),
                          ("https://h/x?k=abc%2Bdef%2Fghi12%3D%3D", "https://h/x?k=********"),
                          ("https://h/x?k=abc%2bdef%2fghi12%3d%3d", "https://h/x?k=********"),
                          ("https://h/abc+def/ghi12==", "https://h/********"),
                          ("https://h/fake-%C3%BC-1234567", "https://h/********"),
                          ("https://h/v2/x", "https://h/v2/x"),
                          ("https://h/a/bcdefgh", "https://h/a/bcdefgh")])
        self.assertEqual([exposed for _, _, exposed in r], [False] * len(r))

    @needs_network
    def test_r3_templated_scheme_host_and_path_are_shown_and_echoed_freely(self):
        # .NET lower-cases the host it sends, and the record shows the URL sent. /get and /anything echo the host and
        # the URL; an absolute redirect names the host in its Location.
        cases = {"host": ({"baseUrl": "https://{{ host }}", "host": "HttpBin.org"}, "/get", 200, HTTPBIN + "/get"),
                 "nested host": ({"baseUrl": "https://{{ h }}", "h": "{{ sub }}.org", "sub": "httpbin"}, "/get", 200,
                                 HTTPBIN + "/get"),
                 "scheme, host and path": ({"baseUrl": "{{ scheme }}://{{ host }}{{ base_path }}", "scheme": "https",
                                            "host": "httpbin.org", "base_path": "/anything/api"}, "/v1", 200,
                                           HTTPBIN + "/anything/api/v1"),
                 "redirect": ({"baseUrl": "https://{{ host }}", "host": "httpbin.org"}, "/absolute-redirect/1", 302,
                              HTTPBIN + "/absolute-redirect/1")}
        for label, (env, path, status, url) in cases.items():
            with self.subTest(label):
                self.data(env, [])
                r = self.call(path, expect=status, network=True)
                self.assertEqual(r.code, 0, r)
                self.assertEqual(r.record["url"], url)
                self.assertIn("httpbin.org", r.record["body"] + r.record["response_headers"].get("Location", ""))

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
    def test_r3_password_is_masked_whatever_fills_the_scheme_host_or_port(self):
        # pw1 is under six characters, so only the masking keeps it out of the record: the refusal would not.
        env = {"port": "443", "host": "httpbin.org", "s": "https://"}
        for base, password in (("https://amy:pw1@httpbin.org:{{ port }}", "pw1"),
                               ("{{ s }}amy:pw1@httpbin.org", "pw1"),
                               ("https://amy:hunter2pass@{{ host }}:{{ port }}", "hunter2pass")):
            with self.subTest(base=base):
                self.data({"baseUrl": base, **env}, [])
                r = self.call("/status/204", expect=204, network=True)
                self.assertEqual(r.code, 0, r)
                self.assertEqual(r.record["url"], "https://amy:********@httpbin.org/status/204")
                self.assertEqual(r.record["auth"], "basic auth from the base URL as amy")
                self.assertNotIn(":" + password + "@", r.files)
                self.assertNotIn(password, r.record["url"] + r.record["auth"])

    def test_r3_the_userinfo_is_split_on_the_text_by_its_last_at(self):
        # In any culture: Thai collation ignores punctuation, so a search for '@' as a string would match anywhere.
        for culture in ("en_US.UTF-8", "th_TH.UTF-8"):
            with self.subTest(culture=culture):
                self.check_userinfo_split({"LC_ALL": culture, "LANG": culture})

    def test_r3_userinfo_and_a_bare_path_reach_the_send_in_a_thai_culture(self):
        thai = {"LC_ALL": "th_TH.UTF-8", "LANG": "th_TH.UTF-8"}
        for base in (OFFLINE, "https://amy:FAKE-pw-9z8y7x6w@127.0.0.1:1"):
            with self.subTest(base=base):
                self.data({"baseUrl": base}, [])
                self.assertReachedSend(self.call("x", env=thai))

    def check_userinfo_split(self, env):
        r = self.functions(r"""
            $out = [ordered]@{}
            foreach ($u in 'https://amy:pw@h/x', 'https://:hunter2pass@h', 'https://amy:p@ssw0rd99@h', 'https://amy@h/x',
                    'https://h/x@y', 'https://amy:p@ss@h:8443/x?y=@z#@', 'https://h?x=a:b@c', 'https://@h', 'https://h') {
                $s = Split-BaseUrl $u
                $out[$u] = @($s.Url, $s.User, $s.Password, $s.PasswordAt)
            }
            $out | ConvertTo-Json -Compress
        """, env=env)
        self.assertEqual(r, {"https://h": ["https://h", None, None, -1],
                             "https://amy:pw@h/x": ["https://h/x", "amy", "pw", 12],
                             "https://:hunter2pass@h": ["https://h", "", "hunter2pass", 9],
                             "https://amy:p@ssw0rd99@h": ["https://h", "amy", "p@ssw0rd99", 12],
                             "https://amy@h/x": ["https://h/x", "amy", None, -1],
                             "https://h/x@y": ["https://h/x@y", None, None, -1],
                             "https://amy:p@ss@h:8443/x?y=@z#@": ["https://h:8443/x?y=@z#@", "amy", "p@ss", 12],
                             "https://h?x=a:b@c": ["https://h?x=a:b@c", None, None, -1],
                             "https://@h": ["https://h", "", None, -1]})

    def test_r3_the_parts_inside_the_password_are_secrets_and_the_rest_are_not(self):
        # creds runs across the start of the password: of it, only the piece inside counts, and of its own parts,
        # those inside that piece. The host is outside the password, so it is not a secret.
        r = self.functions(r"""
            $vars['host'] = 'httpbin.org'; $vars['creds'] = 'amy:{{ pw }}'; $vars['pw'] = 'FAKE-{{ mid }}-x'
            $vars['mid'] = 'inner-LEAF-0042'; $vars['ver'] = 'v1'
            $b = Resolve-Template 'https://{{ creds }}@{{ host }}/{{ ver }}'
            $s = Split-BaseUrl $b.Value
            $within = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
            Add-PartsWithin $b $s.PasswordAt ($s.PasswordAt + $s.Password.Length) $within
            $parts = [string[]]@($within); [Array]::Sort($parts, [StringComparer]::Ordinal)
            [ordered]@{ password = $s.Password; within = $parts; url = $s.Url } | ConvertTo-Json -Compress
        """)
        self.assertEqual(r["password"], "FAKE-inner-LEAF-0042-x")
        self.assertEqual(r["within"], ["FAKE-inner-LEAF-0042-x", "inner-LEAF-0042"])
        self.assertEqual(r["url"], "https://httpbin.org/v1")

    @needs_network
    def test_r3_a_nested_part_of_the_password_echoed_is_refused_and_the_host_is_not(self):
        env = {"baseUrl": "https://{{ creds }}@{{ host }}", "creds": "amy:{{ pw }}", "pw": "FAKE-{{ mid }}-x",
               "mid": "inner-LEAF-0042", "host": "httpbin.org"}
        self.data(env, [])
        self.assertRefused(self.call(echo_body(b'{"m":"inner-LEAF-0042"}'), expect=200, network=True), "inner-LEAF-0042")
        # not /get, which would echo the basic token, a secret too
        path = echo_body(b'{"host":"httpbin.org","user":"amy"}')
        r = self.call(path, expect=200, network=True)
        self.assertEqual(r.code, 0, r)
        self.assertEqual(r.record["url"], "https://amy:********@httpbin.org" + path)

    @needs_network
    def test_r3_a_templated_user_is_shown_and_echoed_freely(self):
        # httpbin answers 200 only when the basic auth arrives, and echoes the user. pw123 is under six characters,
        # so that the path, which must name it, is not refused.
        self.data({"baseUrl": "https://{{ user }}:{{ pw }}@httpbin.org", "user": "amyuser1", "pw": "pw123"}, [])
        r = self.call("/basic-auth/amyuser1/pw123", expect=200, network=True)
        self.assertEqual(r.code, 0, r)
        self.assertEqual(r.record["auth"], "basic auth from the base URL as amyuser1")
        self.assertEqual(r.record["url"], "https://amyuser1:********@httpbin.org/basic-auth/amyuser1/pw123")
        self.assertEqual(json.loads(r.record["body"]), {"authenticated": True, "user": "amyuser1"})

    @needs_network
    def test_r3_a_password_holding_an_unencoded_at_is_sent(self):
        # As Postman parses it: the userinfo ends at the last @ before the host.
        self.data({"baseUrl": "https://amy:p@ss1@httpbin.org"}, [])
        r = self.call("/basic-auth/amy/p@ss1", expect=200, network=True)
        self.assertEqual(r.code, 0, r)
        self.assertEqual(r.record["url"], "https://amy:********@httpbin.org/basic-auth/amy/p@ss1")
        self.assertEqual(json.loads(r.record["body"]), {"authenticated": True, "user": "amy"})

    def test_r3_an_empty_user_reaches_the_send(self):
        self.data({"baseUrl": "https://:hunter2pass@127.0.0.1:1"}, [])
        r = self.call("/x")
        self.assertReachedSend(r)
        self.assertNotIn("hunter2pass", r.stderr)

    @needs_network
    def test_r3_an_empty_user_is_sent_as_basic_auth(self):
        # The basic token for ":p1" is OnAx, under six characters, so its echo is printed and shows what was sent.
        self.data({"baseUrl": "https://:p1@httpbin.org"}, [])
        r = self.call("/headers", expect=200, network=True)
        self.assertEqual(r.code, 0, r)
        self.assertEqual(r.record["url"], "https://:********@httpbin.org/headers")
        self.assertEqual(r.record["auth"], "basic auth from the base URL, with an empty user")
        self.assertEqual(json.loads(r.record["body"])["headers"]["Authorization"], "Basic " + base64.b64encode(b":p1").decode())

    @needs_network
    def test_r2_26_password_in_the_base_url_userinfo_echoed_is_refused(self):
        self.data({"baseUrl": "https://user:FAKE-pw-literal-41@httpbin.org"}, [])
        self.assertRefused(self.call(echo_body(b'{"pw":"FAKE-pw-literal-41"}'), expect=200, network=True),
                           "FAKE-pw-literal-41")

    @needs_network
    def test_r3_a_user_with_no_password_is_the_credential(self):
        # https://TOKEN@host, as GitHub takes a token for the user: with no password, or an empty one, the user is the
        # credential, masked in the url and the auth and refused when a response echoes it, as in the Postman source.
        # Beside a password the user is shown (see test_r3_a_templated_user_is_shown_and_echoed_freely).
        token = "FAKE-tok-31415926"
        for base, url in (("https://{{ tok }}@httpbin.org", "https://********@httpbin.org/status/200"),
                          (f"https://{token}@httpbin.org", "https://********@httpbin.org/status/200"),
                          (f"https://{token}:@httpbin.org", "https://********:********@httpbin.org/status/200")):
            with self.subTest(base=base):
                self.data({"baseUrl": base, "tok": token}, [])
                r = self.call("/status/200", expect=200, network=True)
                self.assertEqual(r.code, 0, r)
                self.assertEqual((r.record["url"], r.record["auth"]), (url, "basic auth from the base URL as ********"))
                self.assertEqual(r.record["request_headers"]["Authorization"], "Basic ********")
                self.assertNotIn(token, r.files)
                self.assertRefused(self.call(echo_body(b'{"user":"FAKE-tok-31415926"}'), expect=200, network=True), token)

    @needs_network
    def test_r3_the_parts_of_a_user_with_no_password_are_secrets_and_the_host_is_not(self):
        env = {"baseUrl": "https://{{ creds }}@{{ host }}", "creds": "FAKE-{{ mid }}-x", "mid": "inner-LEAF-0042",
               "host": "httpbin.org"}
        self.data(env, [])
        self.assertRefused(self.call(echo_body(b'{"m":"inner-LEAF-0042"}'), expect=200, network=True), "inner-LEAF-0042")
        path = echo_body(b'{"host":"httpbin.org"}')
        r = self.call(path, expect=200, network=True)
        self.assertEqual(r.code, 0, r)
        self.assertEqual(r.record["url"], "https://********@httpbin.org" + path)


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


class DataFolder(Case):
    """A data folder that is not there, or is not Insomnia's, says so: it read as one with no collections, which sent a
    user who had given Git Bash an unconverted /c/Users/... path looking for a missing collection."""

    def test_a_missing_or_wrong_data_folder_is_named(self):
        self.data({"baseUrl": OFFLINE}, [])
        missing = self.tmp / "no-such-folder"
        r = self.call("/x", env={"INSOMNIA_DATA": str(missing)})
        self.assertEqual(r.code, 1, r)
        self.assertIn("there is no Insomnia data folder at", r.stderr)
        self.assertIn("INSOMNIA_DATA", r.stderr)
        empty = self.tmp / "empty"
        empty.mkdir()
        r = self.call("/x", env={"INSOMNIA_DATA": str(empty)})
        self.assertEqual(r.code, 1, r)
        self.assertIn("holds no insomnia.Workspace.db", r.stderr)
        r = self.call("/x")  # the test's own data folder still works
        self.assertNotIn("insomnia.Workspace.db", r.stderr)


NBSP, ZWSP, RLO, CR, LF, TAB = chr(0xA0), chr(0x200B), chr(0x202E), chr(13), chr(10), chr(9)


class NearNames(Case):
    """Issue 5: a global environment, a sub-environment, a collection or a top-level folder is chosen by its exact
    name, compared ordinal, so that letter case and every unseen character count. When none has the name, the message
    names those that differ from it only in letter case, or else the closest few, and never chooses one: a work item's
    'Test V2' may mean Insomnia's 'Test v2', or another environment."""

    def test_a_name_in_another_letter_case_is_named_and_never_chosen(self):
        # Before, a lookup ignored letter case, so each of these chose the one Insomnia has.
        self.data({"baseUrl": OFFLINE, "token": "FAKE-tok-7f3a9c2e"}, [folder("Partner API")], subs={"Staging v2": {}},
                  collection="My API", environment="My API - Test v2")
        env, col = ("--env", "My API - Test v2"), ("--collection", "My API")
        cases = {"global environment": (("--env", "My API - TEST V2", *col),
                                        "expected one Insomnia global environment named 'My API - TEST V2', found 0; "
                                        "names are case-sensitive, and 'My API - Test v2' differs only in letter case"),
                 "sub-environment": (("--env", "My API - Test v2/staging V2", *col),
                                     "expected one sub-environment 'staging V2' under 'My API - Test v2', found 0; "
                                     "names are case-sensitive, and 'Staging v2' differs only in letter case"),
                 "collection": ((*env, "--collection", "my api"),
                                "expected one Insomnia collection named 'my api', found 0; names are case-sensitive, "
                                "and 'My API' differs only in letter case"),
                 "folder": ((*env, *col, "--folder", "partner api"),
                            "expected one top-level folder 'partner api' in 'My API', found 0; names are "
                            "case-sensitive, and 'Partner API' differs only in letter case")}
        for label, (options, message) in cases.items():
            with self.subTest(label):
                r = self.call("/x", *options)
                self.assertEqual(r.code, 1, r)
                self.assertNotIn(SENT, r.stderr, r)
                self.assertIn(message, r.stderr, r)
                self.assertNotIn("FAKE-tok", r.stderr)  # names only, never a value
        self.assertReachedSend(self.call("/x", "--env", "My API - Test v2/Staging v2", *col, "--folder", "Partner API"))

    def test_names_that_differ_only_in_letter_case_are_each_chosen_by_their_own(self):
        # A lookup that ignored letter case found two for either name, and so could choose neither. Each one chosen
        # here points the base URL at a port of its own.
        self.data({"baseUrl": OFFLINE}, [folder("API"), folder("api", environment={"baseUrl": "https://127.0.0.1:3"})],
                  subs={"Prod": {}, "PROD": {"baseUrl": "https://127.0.0.1:2"}},
                  workspaces=[("environment", "ENV"), ("collection", "COLL")])
        for options, host in ((("--env", "Env/Prod", "--folder", "API"), "127.0.0.1:1"),
                              (("--env", "Env/PROD", "--folder", "API"), "127.0.0.1:2"),
                              (("--env", "Env/Prod", "--folder", "api"), "127.0.0.1:3")):
            with self.subTest(options=options):
                r = self.call("/x", *options)
                self.assertReachedSend(r)
                self.assertIn(host, r.stderr, r)

    def test_letter_case_is_folded_the_same_in_every_culture(self):
        # Turkish casing lowers I to a dotless i, and Thai has broken a culture's string search here before. Unix reads
        # the culture from LC_ALL; on Windows the test runs in the user's own culture.
        self.data({"baseUrl": OFFLINE}, [], environment="title")
        for culture in ("tr_TR.UTF-8", "th_TH.UTF-8"):
            with self.subTest(culture=culture):
                r = self.call("/x", "--env", "TITLE", env={"LC_ALL": culture, "LANG": culture})
                self.assertIn("named 'TITLE', found 0; names are case-sensitive, and 'title' differs only in letter case",
                              r.stderr, r)

    def test_close_names_are_named_closest_first_and_far_ones_are_not(self):
        self.data({"baseUrl": OFFLINE}, [], environment="My API - Test v2", collection="Test",
                  workspaces=[("environment", n) for n in ("My API - Test v3", "My API - Prod v2", "My API - Test", "Other")])
        r = self.call("/x", "--env", "My API - Tset v2", "--collection", "Test")
        self.assertIn("expected one Insomnia global environment named 'My API - Tset v2', found 0; the closest names are "
                      "'My API - Test v2' and 'My API - Test v3'", r.stderr, r)
        r = self.call("/x", "--env", "My API - Test v2", "--collection", "Tset")
        self.assertIn("expected one Insomnia collection named 'Tset', found 0; the closest name is 'Test'", r.stderr, r)
        r = self.call("/x", "--env", "Nothing like it", "--collection", "Test")  # nothing close: the message as it was
        self.assertEqual(r.stderr.strip().split("\n")[-1],
                         "expected one Insomnia global environment named 'Nothing like it', found 0", r)

    def test_a_name_that_differs_by_an_unseen_character_is_named_with_it_shown(self):
        # -eq compares as the invariant culture does, which ignores a zero-width space and reads a no-break space as a
        # space, so each of these environments was chosen for the name typed. Now each is named, its character escaped.
        for name, wanted in (("Env" + ZWSP, "Env"), ("My" + NBSP + "Env", "My Env")):
            with self.subTest(name=name):
                self.data({"baseUrl": OFFLINE}, [], environment=name)
                r = self.call("/x", "--env", wanted)
                self.assertEqual(r.code, 1, r)
                self.assertNotIn(SENT, r.stderr, r)
                shown = "'" + "".join(esc(c) if c in (ZWSP, NBSP) else c for c in name) + "'"
                self.assertIn(f"named '{wanted}', found 0; the closest name is {shown}", r.stderr, r)

    def test_listed_names_are_escaped_cut_and_counted_on_one_line(self):
        # A shared data folder names its own collections: none can break the message's line, hide a character in it,
        # fake its quoting or make it long.
        cases = {"escaped": (["Team" + NBSP + "API", "Team API" + CR + LF, "Team AP'"], "Team API",
                             "expected one Insomnia collection named 'Team API', found 0; the closest names are "
                             "'Team AP\\'', 'Team" + esc(NBSP) + "API' and 'Team API" + esc(CR) + esc(LF) + "'"),
                 "counted": (["TEAM", "Team", "team", "tEAM"], "TeAm",
                             "expected one Insomnia collection named 'TeAm', found 0; names are case-sensitive, and "
                             "'TEAM', 'Team', 'tEAM' and 1 more differ only in letter case"),
                 "cut": (["long" + "a" * 100], "LONG" + "A" * 100,
                         "expected one Insomnia collection named 'LONG" + "A" * 76 + "'..., found 0; names are "
                         "case-sensitive, and 'long" + "a" * 76 + "'... differs only in letter case")}
        for label, (names, wanted, message) in cases.items():
            with self.subTest(label):
                self.data({"baseUrl": OFFLINE}, [], workspaces=[("collection", n) for n in names])
                r = self.call("/x", "--collection", wanted)
                self.assertEqual(r.code, 1, r)
                self.assertEqual(r.stderr.strip().split("\n"), ["the insomnia source failed:", message], r)

    def test_many_long_names_are_looked_through_quickly(self):
        # The worst a shared data folder can do: a thousand names as long as any compared, each close to the one
        # wanted and differing only at its end, so that no comparison stops early.
        self.data({"baseUrl": OFFLINE}, [], workspaces=[("environment", "x" * 120 + f"{i:08d}") for i in range(999)])
        r, seconds = self.timed_call("/x", "--env", "x" * 120 + "0000000z")
        self.assertEqual(r.code, 1, r)
        self.assertIn("found 0; the closest names are", r.stderr, r)
        self.assertIn("and 996 more", r.stderr, r)
        self.assertLess(seconds, 20, r)

    def test_among_more_than_a_thousand_names_only_letter_case_is_looked_for(self):
        r = self.functions(r"""
            $names = [Collections.Generic.List[string]]::new()
            for ($i = 0; $i -lt 999; $i++) { $names.Add("name $i") }
            $names.Add('Target')
            $within = Get-NameHint 'Targat' $names
            $names.Add('one more')
            [ordered]@{ within = $within; past = (Get-NameHint 'Targat' $names); cased = (Get-NameHint 'TARGET' $names) } |
                ConvertTo-Json -Compress
        """)
        self.assertEqual(r, {"within": "; the closest name is 'Target'", "past": "",
                             "cased": "; names are case-sensitive, and 'Target' differs only in letter case"})

    # Names and wanted names to show and match in both sources. Characters outside the Basic Multilingual Plane, and
    # those whose case folds to more than one, are left out: the sources count them differently in an edit, and fold
    # them differently. Names only shown may hold them, since both show a name by its code points.
    SHOWN_ONLY = ["e" * 79 + chr(0x1F600), "e" * 80 + chr(0x1F600), "a" + chr(0xE0041) + "b", "n" * 78 + NBSP,
                  "n" * 74 + NBSP, "Stra" + chr(0xDF) + "e"]
    PARITY_NAMES =["My API - Test v2", "My API - TEST V2", "my api - test v3", "My API - Prod v2", "Team" + NBSP + "API",
                    "Team API" + CR + LF, "Team AP'", "C:\\Teams\\API", "Env" + ZWSP, "Rev" + RLO + "erse", "Café ✓",
                    "Tab" + TAB + "bed", "Ünïcödé Ëñv", "x" * 100, "X" * 100, "Form" + chr(0x0C) + "feed",
                    "Line" + chr(0x2028) + "sep", "Hangul" + chr(0x3164) + "filler", "Braille" + chr(0x2800) + "blank",
                    "Selector" + chr(0xFE0F), "Del" + chr(0x7F), "", "Q", "Env"]
    PARITY_WANTED = ["my api - test v2", "My API - Tset v2", "Team API", "Env", "ENV", "Nothing like it", "x" * 100,
                     "q", "Tab bed", "ÜNÏCÖDÉ ËÑV", "C:\\Teams\\APl", "Revrse", "Hangulfiller"]

    def test_names_show_and_near_misses_are_found_as_the_postman_source_finds_them(self):
        r = self.functions(r"""
            $doc = [Text.Json.JsonDocument]::Parse($env:CASES)
            $c = ConvertFrom-JsonElement $doc.RootElement
            [ordered]@{
                shown = @(foreach ($n in @($c['names']) + @($c['shown'])) { Format-Name $n })
                hints = @(foreach ($w in $c['wanted']) { Get-NameHint $w $c['names'] })
                surrogate = Format-Name ('a' + [char]0xD800 + 'b')
            } | ConvertTo-Json -Compress
        """, env={"CASES": json.dumps({"names": self.PARITY_NAMES, "wanted": self.PARITY_WANTED, "shown": self.SHOWN_ONLY})})
        self.assertEqual(r["shown"], [postman.shown_name(n) for n in self.PARITY_NAMES + self.SHOWN_ONLY])
        self.assertEqual(r["hints"], [postman.name_hint(w, self.PARITY_NAMES) for w in self.PARITY_WANTED])
        self.assertEqual(r["surrogate"], postman.shown_name("a" + chr(0xD800) + "b"))
        self.assertEqual(r["surrogate"], "'a" + esc(chr(0xD800)) + "b'")


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
                @('out of range', '&#x110000;&#0;&#xD800;&#99999999999;', 'FAKEsecret123')
            )
            $out = [ordered]@{}
            foreach ($c in $cases) { $out[$c[0]] = Test-Exposed @($c[1]) @($c[2]) }
            $out | ConvertTo-Json -Compress
        """)
        expected = {k: True for k in found}
        expected["out of range"] = False
        self.assertEqual(found, expected)

    # (label, text, secret): each text an echo in HTML character references; Python's html.unescape, which the
    # Postman source uses, says whether the secret shows in it.
    HTML_CASES = [
        ("lowbar", "FAKE&lowbar;tok&lowbar;abcdef123", "FAKE_tok_abcdef123"),
        ("period", "eyJhbGci&period;eyJzdWIi&period;sig12345", "eyJhbGci.eyJzdWIi.sig12345"),
        ("colon", "user&colon;pass123456", "user:pass123456"),
        ("commat and excl", "p&commat;ss&excl;word9", "p@ss!word9"),
        ("the rest of ASCII punctuation", "&num;&dollar;&percnt;&lpar;&rpar;&ast;&midast;&comma;&semi;&quest;&lbrack;"
         "&lsqb;&bsol;&rbrack;&rsqb;&Hat;&grave;&DiacriticalGrave;&lbrace;&lcub;&verbar;&vert;&VerticalLine;&rbrace;"
         "&rcub;&UnderBar;&Tab;&NewLine;&fjlig;", "#$%()**,;?[[\\]]^``{{|||}}_\t\nfj"),
        ("capitalised", "abc&AMP;def&LT;g&GT;h&QUOT;i", 'abc&def<g>h"i'),
        ("legacy without a semicolon", "abc&AMPdef&LTg&GThi&QUOTj", 'abc&def<g>hi"j'),
        ("legacy leading a longer name", "abc&ampxyz123;", "abc&xyz123;"),
        ("zero-padded decimal", "FAKE_tok_&#000000097;bcdef123", "FAKE_tok_abcdef123"),
        ("zero-padded hex", "&#x0000000046;AKE_tok_abc", "FAKE_tok_abc"),
        ("an HTML 4 name", "fake-&uuml;-1234567", "fake-\u00fc-1234567"),
        ("an HTML 4 name in capitals", "FAKE-&Uuml;-1234567", "FAKE-\u00dc-1234567"),
        ("Colon is not a colon", "user&Colon;pass123456", "user:pass123456"),
        ("lowbar needs its semicolon", "FAKE&lowbartok_abc", "FAKE_tok_abc"),
        ("names are case-sensitive", "FAKE&LOWBAR;tok_abc", "FAKE_tok_abc"),
    ]

    def test_r3_every_html_reference_to_ascii_is_decoded_as_python_decodes_it(self):
        found = self.exposed(self.HTML_CASES)
        expected = {label: secret in html.unescape(text) for label, text, secret in self.HTML_CASES}
        self.assertEqual(found, expected)
        self.assertEqual(list(expected.values()).count(False), 3)  # the last three, which no reader decodes

    @needs_network
    def test_r2_33_html_reference_echo_is_refused(self):
        self.data({"baseUrl": HTTPBIN, "apiKey": "FAKEsecret123"}, [folder("API", apikey("X-Api-Key"))])
        self.assertRefused(self.call(echo_body(b'{"k":"&#70;AKEsecret123"}'), expect=200, network=True), "FAKEsecret123")

    @needs_network
    def test_r3_named_and_zero_padded_reference_echoes_are_refused(self):
        self.data({"baseUrl": HTTPBIN, "apiKey": "FAKE_tok_abcdef123"}, [folder("API", apikey("X-Api-Key"))])
        for body in (b"FAKE&lowbar;tok&UnderBar;abcdef123", b"FAKE_tok_&#000000097;bcdef123"):
            with self.subTest(body=body):
                self.assertRefused(self.call(echo_body(body), expect=200, network=True), "FAKE_tok_abcdef123")

    @needs_network
    def test_location_with_a_json_escaped_key_reencoded_by_dotnet_is_refused(self):
        key = "FAKEsecret123"
        target = "https://example.com/?k=" + "".join(f"\\u{ord(c):04X}" for c in key[:2]) + key[2:]
        self.data({"baseUrl": HTTPBIN, "apiKey": key}, [folder("API", apikey("X-Api-Key"))])
        path = "/redirect-to?" + urllib.parse.urlencode({"url": target, "status_code": 302})
        self.assertRefused(self.call(path, expect=302, network=True), key)


class LetterCase(Case):
    """Round 3, R3 and R2: a secret is found in its own letter case only, and the record's labels (environment,
    collection and folder names) are never a reason to refuse, so an ordinary word a variable holds does not stop a
    run where another text spells it in another case, or where a label is that word."""

    def test_r3_a_secret_is_matched_in_its_own_letter_case(self):
        found = self.exposed([("exact", '{"t":"DEEPsecret3d"}', "DEEPsecret3d"),
                              ("upper-cased", '{"t":"DEEPSECRET3D"}', "DEEPsecret3d"),
                              ("lower-cased", "x-fake-hn-27182818: 1", "FAKE-hn-27182818"),
                              ("capitalised", "Env/Staging", "staging"),
                              ("percent-encoded, lower-case hex", "k=abc%2bdef%2fghi12", "abc+def/ghi12")])
        self.assertEqual(found, {"exact": True, "upper-cased": False, "lower-cased": False, "capitalised": False,
                                 "percent-encoded, lower-case hex": True})

    @needs_network
    def test_r3_a_word_in_another_letter_case_is_not_refused(self):
        # /json and the /base64 bodies never hold the header values in their own case.
        cases = {"sub-environment": ({"stage": "staging"}, [("X-Environment", "{{ stage }}")], {"Staging": {}},
                                     ("--env", "Env/Staging"), "/json", "Coll"),
                 "collection": ({"tenant": "contoso"}, [("X-Tenant", "{{ tenant }}")], None,
                                ("--collection", "Contoso API"), "/json", "Contoso API"),
                 "body": ({"channel": "online"}, [("X-Channel", "{{ channel }}")], None, (),
                          echo_body(b'{"status":"Online"}'), "Coll")}
        for label, (env, headers, subs, options, path, collection) in cases.items():
            with self.subTest(label):
                self.data({"baseUrl": HTTPBIN, **env}, [folder("API", headers=headers)], subs=subs, collection=collection)
                r = self.call(path, "--folder", "API", *options, expect=200, network=True)
                self.assertEqual(r.code, 0, r)

    @needs_network
    def test_r3_labels_are_never_a_reason_to_refuse(self):
        # Each templated header value, a secret, is spelt exactly as a label of the record is.
        self.data({"baseUrl": HTTPBIN, "stage": "staging", "tenant": "Contoso API", "team": "partners"},
                  [folder("partners", headers=[("X-Environment", "{{ stage }}"), ("X-Tenant", "{{ tenant }}"),
                                               ("X-Team", "{{ team }}")])],
                  subs={"staging": {}}, collection="Contoso API")
        r = self.call("/json", "--env", "Env/staging", "--collection", "Contoso API", "--folder", "partners", expect=200,
                      network=True)
        self.assertEqual(r.code, 0, r)
        self.assertEqual((r.record["environment"], r.record["collection"], r.record["folder"]),
                         ("Env/staging", "Contoso API", "partners"))
        self.assertEqual(r.record["request_headers"]["X-Environment"], "********")


class ResolutionLimits(Case):
    """Round 3: a value that names other values many times over is resolved once per variable, and stops at 65536
    characters, as in the Postman source, so a shared data folder cannot stall a run."""

    @staticmethod
    def fan_out(times, leaf):
        """a0 names a1 `times` times, a1 names a2 as often, and so on to a4: a0 is times**4 copies of leaf."""
        env = {f"a{i}": "{{ a%d }}" % (i + 1) * times for i in range(4)}
        env["a4"] = leaf
        return env

    def test_r3_a_value_resolving_past_the_cap_stops_at_once(self):
        self.data({"baseUrl": OFFLINE, **self.fan_out(20, "x")}, [folder("API", bearer("{{ a0 }}"))])
        r, seconds = self.timed_call("/x")
        self.assertEqual(r.code, 1, r)
        self.assertIn("variable 'a0' in 'Env' resolves to more than 65536 characters", r.stderr, r)
        self.assertLess(seconds, 20, r)

    def test_r3_a_wide_fan_out_of_empty_values_is_quick(self):
        # Empty values never reach the cap; without reuse, a0 would take 24**4 resolutions.
        self.data({"baseUrl": OFFLINE, **self.fan_out(24, "")}, [folder("API", bearer("FAKE-tok-{{ a0 }}"))])
        r, seconds = self.timed_call("/x")
        self.assertReachedSend(r)
        self.assertLess(seconds, 20, r)

    def test_r3_reuse_keeps_the_nesting_limit(self):
        # c is resolved first from the base URL, two deep; reused from the token, it would be six deep.
        self.data({"baseUrl": "https://127.0.0.1:1/{{ c }}", "c": "{{ d }}", "d": "{{ e }}", "e": "{{ f }}", "f": "{{ g }}",
                   "g": "x", "token": "{{ b }}", "b": "{{ c }}"}, [folder("API", bearer())])
        r = self.call("/x")
        self.assertEqual(r.code, 1, r)
        self.assertIn("variable 'token' in 'Env' nests templates more than five deep, or refers to itself", r.stderr, r)


if __name__ == "__main__":
    unittest.main()
