"""post.py's guarantees. Run with: python3 -m unittest discover -s skills/ado-evidence/tests

Nothing here reaches Azure DevOps. Most tests run post.py whole, as its command line would, with every connection
urllib opens answered by a stand-in for Azure DevOps that opens no socket (Wire). The opener post.py builds, its
redirect handling and TLS settings included, is what runs above it, and every request that reaches the wire is
recorded. A test of that kind judges any version of the script, since it imports none of its parts.

The tests under API_CALL_E2E reach httpbin.org with a fake token, and nothing else; the one redirect they ask it for
names postman-echo.com, which must never be reached.
"""
import base64, contextlib, html, http.client, importlib.util, io, json, os, re, runpy, ssl, subprocess, sys, tempfile
import time, types, unittest, urllib.parse, urllib.request
from pathlib import Path, PurePosixPath, PureWindowsPath
from unittest import mock

POST = Path(__file__).resolve().parents[1] / "skills" / "ado-evidence" / "scripts" / "post.py"
ORG = "https://dev.azure.com/contoso"
PAT = ("fakepat" + "abcdefghijklmnopqrstuvwxyz234567" * 2)[:52]  # shaped like a personal access token; not one
BASIC = base64.b64encode(f":{PAT}".encode()).decode()
JWT = "eyJ0eXAiOiJKV1QifQ.eyJhdWQiOiJmYWtlLWZvci10ZXN0cyJ9.ZmFrZS1zaWduYXR1cmU"  # shaped like an Entra token; not one
# A token of the characters each encoder treats specially, whose Basic value holds '+', '/' and '==' too; not one.
PUNCT = "fake~~~pat???&<>\"'=+/%\\x;:@!$*(),[]{}|^`#"
PUNCT_BASIC = base64.b64encode(f":{PUNCT}".encode()).decode()
NAMED = {"+": "&plus;", "/": "&sol;", "=": "&equals;", "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&apos;"}


def encoded(secret):
    """secret in each form a response could carry it back in, one layer of encoding deep (D4)."""
    return {"as is, in whitespace": f"\n\t {secret} \n",
            "padding percent-encoded": secret.replace("=", "%3D"),
            "padding percent-encoded in lower case": secret.replace("=", "%3d"),
            "percent-encoded": urllib.parse.quote(secret, safe=""),
            "percent-encoded, space as +": urllib.parse.quote_plus(secret, safe=""),
            "every character percent-encoded": "".join(f"%{ord(c):02x}" for c in secret),
            "form-decoded, + as space": secret.replace("+", " "),
            "JSON-escaped, / too": json.dumps(secret)[1:-1].replace("/", "\\/"),
            "padding JSON-escaped": secret.replace("=", "\\u003d"),
            "every character JSON-escaped": "".join(f"\\u{ord(c):04X}" for c in secret),
            "HTML-escaped": html.escape(secret),
            "padding as &#61;": secret.replace("=", "&#61;"),
            "padding as &#x3D;": secret.replace("=", "&#x3D;"),
            "padding as &equals;": secret.replace("=", "&equals;"),
            "every character as &#NN;": "".join(f"&#{ord(c)};" for c in secret),
            "every character as &#xHH, no semicolon": "".join(f"&#x{ord(c):x}" for c in secret),
            "named references": "".join(NAMED.get(c, c) for c in secret)}


def decodings(text):
    """text, and text with one layer of each encoding in encoded() undone."""
    json_unescaped = re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m[1], 16)), text)
    json_unescaped = re.sub(r"\\([/\"'\\])", r"\1", json_unescaped)
    return (text, urllib.parse.unquote(text), urllib.parse.unquote_plus(text), html.unescape(text), json_unescaped,
            text.replace(" ", "+"))


def parts(secret, n=12):
    """Every run of n characters of a secret: enough of one to be worth hiding."""
    return {secret[i:i + n] for i in range(len(secret) - n + 1)}
PROJECT_ID = "0f3a5c1e-0000-4000-8000-000000000001"
PNG = b"\x89PNG\r\n\x1a\n not really a picture"
REAL_HTTP, REAL_HTTPS = http.client.HTTPConnection, http.client.HTTPSConnection
# A public root certificate (ISRG Root X1), standing in for certifi's bundle where certifi is not installed.
ROOT = """-----BEGIN CERTIFICATE-----
MIIFazCCA1OgAwIBAgIRAIIQz7DSQONZRGPgu2OCiwAwDQYJKoZIhvcNAQELBQAw
TzELMAkGA1UEBhMCVVMxKTAnBgNVBAoTIEludGVybmV0IFNlY3VyaXR5IFJlc2Vh
cmNoIEdyb3VwMRUwEwYDVQQDEwxJU1JHIFJvb3QgWDEwHhcNMTUwNjA0MTEwNDM4
WhcNMzUwNjA0MTEwNDM4WjBPMQswCQYDVQQGEwJVUzEpMCcGA1UEChMgSW50ZXJu
ZXQgU2VjdXJpdHkgUmVzZWFyY2ggR3JvdXAxFTATBgNVBAMTDElTUkcgUm9vdCBY
MTCCAiIwDQYJKoZIhvcNAQEBBQADggIPADCCAgoCggIBAK3oJHP0FDfzm54rVygc
h77ct984kIxuPOZXoHj3dcKi/vVqbvYATyjb3miGbESTtrFj/RQSa78f0uoxmyF+
0TM8ukj13Xnfs7j/EvEhmkvBioZxaUpmZmyPfjxwv60pIgbz5MDmgK7iS4+3mX6U
A5/TR5d8mUgjU+g4rk8Kb4Mu0UlXjIB0ttov0DiNewNwIRt18jA8+o+u3dpjq+sW
T8KOEUt+zwvo/7V3LvSye0rgTBIlDHCNAymg4VMk7BPZ7hm/ELNKjD+Jo2FR3qyH
B5T0Y3HsLuJvW5iB4YlcNHlsdu87kGJ55tukmi8mxdAQ4Q7e2RCOFvu396j3x+UC
B5iPNgiV5+I3lg02dZ77DnKxHZu8A/lJBdiB3QW0KtZB6awBdpUKD9jf1b0SHzUv
KBds0pjBqAlkd25HN7rOrFleaJ1/ctaJxQZBKT5ZPt0m9STJEadao0xAH0ahmbWn
OlFuhjuefXKnEgV4We0+UXgVCwOPjdAvBbI+e0ocS3MFEvzG6uBQE3xDk3SzynTn
jh8BCNAw1FtxNrQHusEwMFxIt4I7mKZ9YIqioymCzLq9gwQbooMDQaHWBfEbwrbw
qHyGO0aoSCqI3Haadr8faqU9GY/rOPNk3sgrDQoo//fb4hVC1CLQJ13hef4Y53CI
rU7m2Ys6xt0nUW7/vGT1M0NPAgMBAAGjQjBAMA4GA1UdDwEB/wQEAwIBBjAPBgNV
HRMBAf8EBTADAQH/MB0GA1UdDgQWBBR5tFnme7bl5AFzgAiIyBpY9umbbjANBgkq
hkiG9w0BAQsFAAOCAgEAVR9YqbyyqFDQDLHYGmkgJykIrGF1XIpu+ILlaS/V9lZL
ubhzEFnTIZd+50xx+7LSYK05qAvqFyFWhfFQDlnrzuBZ6brJFe+GnY+EgPbk6ZGQ
3BebYhtF8GaV0nxvwuo77x/Py9auJ/GpsMiu/X1+mvoiBOv/2X/qkSsisRcOj/KK
NFtY2PwByVS5uCbMiogziUwthDyC3+6WVwW6LLv3xLfHTjuCvjHIInNzktHCgKQ5
ORAzI4JMPJ+GslWYHb4phowim57iaztXOoJwTdwJx4nLCgdNbOhdjsnvzqvHu7Ur
TkXWStAmzOVyyghqpZXjFaH3pO3JLF+l+/+sKAIuvtd7u+Nxe5AW0wdeRlN8NwdC
jNPElpzVmbUq4JUagEiuTDkHzsxHpFKVK7q4+63SM1N95R1NbdWhscdCb+ZAJzVc
oyi3B43njTOQ5yOf+1CceWxG1bQVs5ZufpsMljq4Ui0/1lvh+wjChP4kqKOJ2qxq
4RgqsahDYVvTH9w7jXbyLeiNdd8XM2w9U/t7y0Ff/9yi0GE44Za4rF2LN9d11TPA
mRGunUHBcnWEvgJBQl9nJEiU0Zsnvgc/ubhPgXRR4Xq37Z0j4r7g1SgEEzwxA57d
emyPxgcYxn/eR44/KJ4EBs+lVDR3veyJm+kXQ99b21/+jh5Xos1AnX5iItreGCc=
-----END CERTIFICATE-----
"""


class Sent:
    """One request as it reached the wire."""

    def __init__(self, conn, method, target, body, headers):
        self.scheme = "https" if isinstance(conn, REAL_HTTPS) else "http"
        self.host, self.method, self.body = conn.host, method, body
        u = urllib.parse.urlsplit(target)
        self.path, self.query = u.path, dict(urllib.parse.parse_qsl(u.query))
        self.headers = {k.lower(): v for k, v in headers.items()}
        self.context = getattr(conn, "_context", None)


class Wire:
    """Stands in for the network beneath urllib: each request is recorded and answered by answer(sent), which returns
    (status, reason, headers, body). No socket is opened, so nothing leaves the machine. With verify, a TLS connection
    whose context trusts no root fails as a real handshake would."""

    def __init__(self, answer, verify=False):
        self.answer, self.verify, self.sent = answer, verify, []

    @contextlib.contextmanager
    def installed(self):
        wire = self

        class Sink:
            def sendall(self, data):
                pass

            def close(self):
                pass

        class Reply:
            def __init__(self, raw):
                self.raw = raw

            def makefile(self, *args, **kwargs):
                return io.BytesIO(self.raw)

            def close(self):
                pass

        class StandIn:
            def connect(self):
                context = getattr(self, "_context", None)
                if wire.verify and context is not None and context.verify_mode == ssl.CERT_REQUIRED \
                        and context.cert_store_stats()["x509_ca"] == 0:
                    raise ssl.SSLCertVerificationError(1, "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: unable to get local issuer certificate")
                self.sock = Sink()

            def request(self, method, url, body=None, headers={}, **kwargs):
                super().request(method, url, body, headers, **kwargs)  # http.client checks every header here
                self.sent_now = Sent(self, method, url, body, headers)
                wire.sent.append(self.sent_now)

            def getresponse(self):
                status, reason, headers, body = wire.answer(self.sent_now)
                head = f"HTTP/1.1 {status} {reason}\r\n" + "".join(f"{k}: {v}\r\n" for k, v in headers.items())
                response = http.client.HTTPResponse(Reply((head + f"Content-Length: {len(body)}\r\n\r\n").encode("latin-1") + body),
                                                    method=self.sent_now.method)
                response.begin()
                return response

        class Plain(StandIn, REAL_HTTP):
            pass

        class Secure(StandIn, REAL_HTTPS):
            pass

        # urllib's handlers open their connections through these two methods, whichever opener is built.
        with mock.patch.object(urllib.request.HTTPHandler, "http_open", lambda handler, req: handler.do_open(Plain, req)), \
                mock.patch.object(urllib.request.HTTPSHandler, "https_open",
                                  lambda handler, req: handler.do_open(Secure, req, context=handler._context)), \
                mock.patch.object(urllib.request, "getproxies", dict), mock.patch.object(urllib.request, "_opener", None):
            yield self


def reply(status, obj=None, body=None, reason="OK", headers=None):
    if obj is not None:
        return status, reason, {"Content-Type": "application/json", **(headers or {})}, json.dumps(obj).encode("utf-8")
    return status, reason, headers or {}, body or b""


def render_markdown_images(text):
    """renderedText as Azure DevOps gives it, near enough: each ![alt](url) an <img>, everything else escaped."""
    out, last = [], 0
    for m in re.finditer(r"!\[([^\]]*)\]\(([^)\s]*)\)", text):
        out += [html.escape(text[last:m.start()]), f'<img src="{html.escape(m[2])}" alt="{html.escape(m[1])}">']
        last = m.end()
    return "<div>" + "".join(out) + html.escape(text[last:]) + "</div>"


class AzureDevOps:
    """Answers post.py's requests the way Azure DevOps does, closely enough to upload, post, read back and download.
    Any step's answer can be replaced: replace={"upload" | "comment" | "read" | "download": answer or fn(sent, ado)}.
    Any other host answers like an echo service, so a request that should never have been made goes through."""

    def __init__(self, replace=None, render=render_markdown_images, attachment_url=None, host="dev.azure.com"):
        self.replace, self.render, self.host = replace or {}, render, host
        self.attachment_url = attachment_url or (lambda id, name: f"{ORG}/{PROJECT_ID}/_apis/wit/attachments/{id}?fileName={urllib.parse.quote(name)}")
        self.attachments, self.comments, self.posted = {}, {}, []

    def __call__(self, sent):
        if sent.host != self.host:
            return reply(200, {"url": f"{sent.scheme}://{sent.host}{sent.path}", "headers": sent.headers})
        step = self.step(sent)
        if step in self.replace:
            r = self.replace[step]
            return r(sent, self) if callable(r) else r
        return getattr(self, step)(sent)

    @staticmethod
    def step(sent):
        if sent.path.endswith("/_apis/wit/attachments") and sent.method == "POST":
            return "upload"
        if "/_apis/wit/attachments/" in sent.path and sent.method == "GET":
            return "download"
        if "/comments" in sent.path and sent.method in ("POST", "PATCH"):
            return "comment"
        if "/comments/" in sent.path and sent.method == "GET":
            return "read"
        return "unknown"

    def upload(self, sent):
        id = f"9b1c0000-0000-4000-8000-{len(self.attachments) + 1:012d}"
        self.attachments[id] = sent.body
        return reply(201, {"id": id, "url": self.attachment_url(id, sent.query.get("fileName", ""))}, reason="Created")

    def comment(self, sent):
        text = json.loads(sent.body.decode("utf-8"))["text"]
        id = int(sent.path.rsplit("/", 1)[1]) if sent.method == "PATCH" else 7
        self.comments[id] = text
        self.posted.append(text)
        return reply(200, {"id": id, "version": 1, "text": text})

    def read(self, sent):
        id = int(sent.path.rsplit("/", 1)[1])
        return reply(200, {"id": id, "version": 1, "text": self.comments[id], "renderedText": self.render(self.comments[id])})

    def download(self, sent):
        id = sent.path.rsplit("/", 1)[1]
        if id not in self.attachments:
            return reply(404, {"message": "no such attachment"}, reason="Not Found")
        return reply(200, body=self.attachments[id], headers={"Content-Type": "application/octet-stream"})

    def unknown(self, sent):
        return reply(404, {"message": "not an API post.py uses"}, reason="Not Found")


def run_post(comment, images=None, ado=None, token=PAT, args=(), verify=False):
    """Runs post.py whole, as `python3 post.py --org ... --comment comment.md ARGS` would, in a temporary folder that
    holds the comment and its images, over the Wire. Returns what it did: code (sys.exit's argument, or the error a
    traceback would have shown), out, err, shown (all three as text), sent (the requests) and ado."""
    ado = ado if ado is not None else AzureDevOps()
    wire = Wire(ado, verify)
    out, err = io.StringIO(), io.StringIO()
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "comment.md").write_text(comment, encoding="utf-8")
        for name, data in (images or {}).items():
            (Path(d) / name).write_bytes(data)
        argv = [str(POST), "--org", ORG, "--project", "Widgets", "--work-item", "70012", "--comment", str(Path(d) / "comment.md"), *args]
        environ = {k: v for k, v in os.environ.items() if k != "AZURE_DEVOPS_TOKEN"}
        if token is not None:
            environ["AZURE_DEVOPS_TOKEN"] = token
        with wire.installed(), mock.patch.dict(os.environ, environ, clear=True), mock.patch.object(sys, "argv", argv), \
                mock.patch("shutil.which", lambda *a, **k: None), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                runpy.run_path(str(POST), run_name="__main__")
                code = 0
            except SystemExit as e:
                code = e.code
            except Exception as e:  # the error a traceback would have shown
                code = f"{type(e).__name__}: {e}"
    shown = out.getvalue() + err.getvalue() + (code if isinstance(code, str) else "")
    return types.SimpleNamespace(code=code, out=out.getvalue(), err=err.getvalue(), shown=shown, sent=wire.sent, ado=ado)


def load_post():
    """post.py as a module, for the tests of its parts."""
    spec = importlib.util.spec_from_file_location("post_under_test", POST)
    module = importlib.util.module_from_spec(spec)
    try:
        with mock.patch.object(sys, "argv", [str(POST)]), contextlib.redirect_stderr(io.StringIO()):
            spec.loader.exec_module(module)
    except SystemExit:
        raise AssertionError("post.py does its work at import, so its parts cannot be tested alone") from None
    return module


TWO_SHOTS = "Test, build 1.\n\n**Removed route: 404 as expected**\n\n{{image:a.png}}\n\n**Control: 200 as expected**\n\n{{image: b.png }}\n"


class AsPosted(unittest.TestCase):
    def assertNothingLeaked(self, run):
        for secret in (PAT, BASIC, JWT):
            self.assertNotIn(secret, run.shown)

    def assertOnlyAzureDevOps(self, run):
        self.assertEqual({(s.scheme, s.host) for s in run.sent} - {("https", "dev.azure.com")}, set(),
                         "a request went somewhere other than https://dev.azure.com")

    def assertNoPartShown(self, shown, secrets=(PAT, BASIC, JWT, PUNCT, PUNCT_BASIC), fold=False):
        """No run of 12 characters of any secret shows, as is or with one layer of encoding undone (fold: in any case)."""
        for text in decodings(shown):
            for secret in secrets:
                for part in parts(secret):
                    if fold:
                        self.assertNotIn(part.lower(), text.lower(), f"part of {secret[:6]}... shown, in some case")
                    else:
                        self.assertNotIn(part, text, f"part of {secret[:6]}... shown")


class Baseline(AsPosted):
    def test_a_post_that_goes_well_uploads_posts_reads_back_and_passes_every_check(self):
        run = run_post(TWO_SHOTS, {"a.png": PNG, "b.png": PNG + b"2"})
        self.assertEqual(run.code, 0, run.shown)
        self.assertEqual([(s.method, AzureDevOps.step(s)) for s in run.sent],
                         [("POST", "upload"), ("POST", "upload"), ("POST", "comment"), ("GET", "read"), ("GET", "download"), ("GET", "download")])
        for line in ("uploaded a.png", "uploaded b.png", "ok   stored text matches", "ok   images rendered",
                     "ok   a.png matches local file", "ok   b.png matches local file", f"comment 7 (version 1) on {ORG}/Widgets/_workitems/edit/70012"):
            self.assertIn(line, run.out)
        self.assertEqual(run.ado.posted[0].count("![a.png](https://dev.azure.com/contoso/"), 1)
        self.assertOnlyAzureDevOps(run)
        self.assertNothingLeaked(run)

    def test_an_edit_replaces_the_text_of_the_comment_named(self):
        ado = AzureDevOps()
        ado.comments[12] = "the text first posted"
        run = run_post(TWO_SHOTS, {"a.png": PNG, "b.png": PNG}, ado, args=["--edit", "12"])
        self.assertEqual(run.code, 0, run.shown)
        self.assertIn(("PATCH", "/contoso/Widgets/_apis/wit/workItems/70012/comments/12"), [(s.method, s.path) for s in run.sent])
        self.assertIn("![b.png](https://dev.azure.com/contoso/", ado.comments[12])
        self.assertIn(f"comment 12 (version 1) on {ORG}/Widgets/_workitems/edit/70012", run.out)

    def test_a_dry_run_reaches_no_network_and_needs_no_token(self):
        run = run_post(TWO_SHOTS, {"a.png": PNG, "b.png": PNG}, token=None, args=["--dry-run"])
        self.assertEqual(run.code, 0, run.shown)
        self.assertTrue(run.out.startswith("DRY RUN - would add a comment on work item 70012 with 2 image(s):"), run.out)
        self.assertIn(f"![a.png](<upload {len(PNG)} bytes>)", run.out)
        self.assertEqual(run.sent, [])


class Redirects(AsPosted):
    """docs-3: a redirect is an error, never followed, so the token goes to no host a response names."""

    def test_docs_3_a_redirect_answering_any_request_is_not_followed(self):
        for step in ("upload", "comment", "read", "download"):
            for code in (301, 302, 303, 307, 308):
                for location in ("https://evil.example/collect", "http://dev.azure.com/contoso/plain"):
                    with self.subTest(step=step, code=code, location=location):
                        run = run_post(TWO_SHOTS, {"a.png": PNG, "b.png": PNG},
                                       AzureDevOps(replace={step: reply(code, body=b"", reason="Moved", headers={"Location": location})}))
                        self.assertOnlyAzureDevOps(run)
                        self.assertNotEqual(run.code, 0)
                        self.assertIn(f"HTTP {code}", run.shown)
                        self.assertIn(urllib.parse.urlsplit(location).hostname, run.shown)
                        self.assertIn("not followed", run.shown)
                        self.assertNothingLeaked(run)

    def test_docs_3_a_redirect_after_the_comment_is_up_says_it_was_posted(self):
        run = run_post(TWO_SHOTS, {"a.png": PNG, "b.png": PNG},
                       AzureDevOps(replace={"read": reply(302, body=b"", reason="Found", headers={"Location": "https://evil.example/"})}))
        self.assertIn("comment 7 was posted", run.shown)
        self.assertOnlyAzureDevOps(run)

    def test_docs_3_the_opener_carries_a_redirect_handler_that_refuses(self):
        post = load_post()
        handlers = post.build_opener().handlers
        self.assertTrue(any(isinstance(h, post.NoRedirect) for h in handlers))
        self.assertFalse(any(type(h) is urllib.request.HTTPRedirectHandler for h in handlers))
        self.assertIsNone(post.NoRedirect().redirect_request(None, None, 302, "Found", {}, "https://evil.example/"))


class HostCheck(AsPosted):
    """docs-3: the token goes to dev.azure.com or ORG.visualstudio.com over https, and nowhere else."""

    def test_docs_3_the_client_sends_nothing_to_a_url_off_azure_devops(self):
        post = load_post()
        opened = []
        opener = types.SimpleNamespace(open=lambda req, timeout=None: opened.append(req.full_url))
        client = post.Client(PAT, opener)
        for url in ("https://evil.example/x", "http://dev.azure.com/contoso/x", "https://dev.azure.com.evil.example/x",
                    "https://dev.azure.com@evil.example/x", "https://visualstudio.com/x", "ftp://dev.azure.com/x"):
            with self.subTest(url=url):
                with self.assertRaises(post.Problem):
                    client.call("GET", url)
        self.assertEqual(opened, [])

    def test_docs_3_an_org_off_azure_devops_is_refused_before_anything_is_sent(self):
        for org in ("https://evil.example/contoso", "http://dev.azure.com/contoso", "https://dev.azure.com.evil.example/contoso",
                    "https://dev.azure.com@evil.example/contoso"):
            with self.subTest(org=org):
                run = run_post(TWO_SHOTS, {"a.png": PNG, "b.png": PNG}, args=["--org", org])
                self.assertIn("--org must be", str(run.code))
                self.assertEqual(run.sent, [])

    def test_docs_3_and_secrets_5_an_attachment_url_off_azure_devops_is_refused_before_the_comment(self):
        for url in ("https://evil.example/contoso/_apis/wit/attachments/1?fileName=a.png",
                    "http://dev.azure.com/contoso/_apis/wit/attachments/1?fileName=a.png",
                    "https://evil.example@dev.azure.com/contoso/_apis/wit/attachments/1?fileName=a.png"):
            with self.subTest(url=url):
                run = run_post(TWO_SHOTS, {"a.png": PNG, "b.png": PNG}, AzureDevOps(attachment_url=lambda id, name: url))
                self.assertOnlyAzureDevOps(run)
                self.assertEqual(run.ado.posted, [], "a comment was posted with an attachment url off Azure DevOps")
                self.assertIn("refusing to embed", str(run.code))


class AttachmentUrls(AsPosted):
    """secrets-5: a url from an upload's answer cannot carry markdown into the comment."""

    def test_secrets_5_markdown_in_an_attachment_url_never_reaches_the_comment(self):
        base = f"{ORG}/{PROJECT_ID}/_apis/wit/attachments/"
        for shape in ("{base}{id}?fileName={name})\n\n![ ](https://tracker.example/p.png",
                      "{base}{id}?fileName={name}) [Approve the release here](https://tracker.example/x",
                      "{base}{id})[Approve the release here](https://tracker.example/x",
                      "{base}{id}\"onerror=\"fetch('https://tracker.example/')",
                      "{base}{id} \"tracker.example\"",
                      "{base}{id}<b>tracker.example</b>"):
            with self.subTest(shape=shape):
                ado = AzureDevOps(attachment_url=lambda id, name: shape.format(base=base, id=id, name=name))
                run = run_post(TWO_SHOTS, {"a.png": PNG, "b.png": PNG}, ado)
                for text in run.ado.posted:
                    self.assertNotIn("tracker.example", text)
                    self.assertEqual(len(re.findall(r"!\[", text)), 2, text)
                    self.assertEqual(len(re.findall(r"\]\(", text)), 2, text)
                self.assertOnlyAzureDevOps(run)
                if run.ado.posted:  # kept, with the answer's query dropped and the name added here
                    self.assertEqual(run.code, 0, run.shown)
                    self.assertRegex(run.ado.posted[0], r"!\[a\.png\]\(https://dev\.azure\.com/contoso/[0-9a-f-]+/_apis/wit/attachments/[0-9a-f-]+\?fileName=a\.png\)")
                else:
                    self.assertIn("refusing to embed", str(run.code))

    def test_secrets_5_the_file_name_in_an_embedded_url_is_encoded_here(self):
        run = run_post("{{image:shot (1) é.png}}\n", {"shot (1) é.png": PNG},
                       AzureDevOps(attachment_url=lambda id, name: f"{ORG}/{PROJECT_ID}/_apis/wit/attachments/{id}?fileName=shot (1) é.png"))
        self.assertEqual(run.code, 0, run.shown)
        self.assertIn("?fileName=shot%20%281%29%20%C3%A9.png)", run.ado.posted[0])


class Rendered(AsPosted):
    """correctness-21: an image is rendered when its url is drawn by an <img>, however many images the comment holds."""

    SHOWN_TWICE = "**One**\n\n{{image:a.png}}\n\n**Again**\n\n{{image:a.png}}\n\n![badge](https://img.example/b.svg)\n"

    def test_correctness_21_one_image_shown_twice_beside_a_hand_written_one_is_rendered(self):
        run = run_post(self.SHOWN_TWICE, {"a.png": PNG})
        self.assertEqual(run.code, 0, run.shown)
        self.assertIn("ok   images rendered", run.out)
        self.assertEqual(run.ado.posted[0].count("![a.png](https://dev.azure.com/"), 2)
        self.assertEqual([AzureDevOps.step(s) for s in run.sent].count("upload"), 1)

    def test_correctness_21_an_uploaded_image_the_comment_does_not_draw_fails(self):
        # Two <img> for two uploads, so a count of tags would pass; but one is the badge, and b.png is not drawn.
        drops_b = lambda text: render_markdown_images(re.sub(r"!\[b\.png\]\([^)]*\)", "", text))
        run = run_post("{{image:a.png}}\n\n{{image:b.png}}\n\n![badge](https://img.example/b.svg)\n",
                       {"a.png": PNG, "b.png": PNG}, AzureDevOps(render=drops_b))
        self.assertEqual(run.code, 1, run.shown)
        self.assertIn("FAIL images rendered", run.out)
        self.assertIn("ok   b.png matches local file", run.out)

    def test_correctness_21_a_url_drawn_with_entities_and_another_query_still_counts(self):
        rewrites = lambda text: re.sub(r'(<img src="[^"?]*)\?[^"]*"', r'\1?fileName=x&amp;download=false"', render_markdown_images(text))
        run = run_post(self.SHOWN_TWICE, {"a.png": PNG}, AzureDevOps(render=rewrites))
        self.assertEqual(run.code, 0, run.shown)
        self.assertIn("ok   images rendered", run.out)


class ImageNames(AsPosted):
    """correctness-20: an image is a file name alone, under Windows rules as well as POSIX ones."""

    REFUSED = ("C:secret.png", "c:secret.png", "D:", "Z:..\\x.png", "a.png:hidden", "x:y.png", "a/b.png", "a\\b.png",
               "/etc/passwd", "\\\\srv\\share\\x.png", "../a.png", "..\\a.png", "..", ".", "\\x.png")

    def test_correctness_20_a_drive_relative_name_is_refused_and_nothing_is_read(self):
        for name in ("C:secret.png", "c:report.pdf", "a.png:hidden"):
            with self.subTest(name=name):
                images = {"ok.png": PNG}
                if os.name != "nt":  # a colon is an ordinary character in a POSIX file name; make it there to be found
                    images[name] = b"a file the comment must not reach"
                run = run_post(f"**Shot**\n\n{{{{image:{name}}}}}\n\n{{{{image:ok.png}}}}\n", images, args=["--dry-run"])
                self.assertIn("file name alone", str(run.code), run.shown)
                self.assertIn(name, str(run.code))
                self.assertNotIn("DRY RUN", run.out)

    def test_correctness_20_every_name_accepted_stays_in_the_images_folder_on_windows_and_posix(self):
        post = load_post()
        for name in self.REFUSED:
            self.assertFalse(post.plain_name(name), name)
        windows, posix = PureWindowsPath("D:/work/shots"), PurePosixPath("/work/shots")
        self.assertNotEqual((windows / "C:secret.png").parent, windows)  # why a drive-relative name must be refused
        for name in (*self.REFUSED, "a.png", "my shot (1).png", ".hidden.png", "..a.png", "shot-é.png", "a..png"):
            if post.plain_name(name):
                self.assertEqual((windows / name).parent, windows, name)
                self.assertEqual((posix / name).parent, posix, name)


class Tokens(AsPosted):
    """docs-10: an Entra token goes as Bearer, a personal access token as Basic with an empty user name."""

    def test_docs_10_a_personal_access_token_goes_as_basic_with_an_empty_user(self):
        run = run_post(TWO_SHOTS, {"a.png": PNG, "b.png": PNG}, token=PAT)
        self.assertEqual(run.code, 0, run.shown)
        self.assertEqual({s.headers.get("authorization") for s in run.sent}, {f"Basic {BASIC}"})

    def test_docs_10_an_entra_token_goes_as_bearer(self):
        run = run_post(TWO_SHOTS, {"a.png": PNG, "b.png": PNG}, token=JWT)
        self.assertEqual(run.code, 0, run.shown)
        self.assertEqual({s.headers.get("authorization") for s in run.sent}, {f"Bearer {JWT}"})

    def test_docs_10_a_refused_token_says_how_it_was_sent_and_shows_it_in_no_form(self):
        body = json.dumps({"message": f"TF400813: {PAT} {BASIC} is not authorized"}).encode()
        for answer, says in ((reply(401, body=body, reason="Unauthorized"), "HTTP 401"),
                             (reply(203, body=b"<html>Sign in</html>", reason="Non-Authoritative Information",
                                    headers={"Content-Type": "text/html"}), "token was not accepted")):
            with self.subTest(says=says):
                run = run_post(TWO_SHOTS, {"a.png": PNG, "b.png": PNG}, AzureDevOps(replace={"upload": answer}))
                self.assertIn(says, run.shown)
                self.assertIn("personal access token, as Basic", run.shown)
                self.assertNotEqual(run.code, 0)
                self.assertNothingLeaked(run)
                self.assertEqual(run.ado.posted, [])

    def test_a_token_that_cannot_go_in_a_header_is_refused_unsent_and_unshown(self):
        for token in ("fakepat-part-one\r\nX-Injected: fakepat-part-two", "eyJ0eXAi.eyJhdWQi\n.fakesig-part-three"):
            with self.subTest(token=token):
                run = run_post(TWO_SHOTS, {"a.png": PNG, "b.png": PNG}, token=token)
                self.assertNotEqual(run.code, 0)
                self.assertEqual(run.sent, [])
                for part in ("fakepat-part-one", "fakepat-part-two", "fakesig-part-three", "eyJ0eXAi"):
                    self.assertNotIn(part, run.shown)


class Certificates(AsPosted):
    """docs-9: where Python has no root certificates, as python.org's macOS build has none, certifi's are used."""

    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        bundle = Path(d.name) / "roots.pem"
        bundle.write_text(ROOT, encoding="ascii")
        self.certifi = types.ModuleType("certifi")
        self.certifi.where = lambda: str(bundle)
        empty = lambda *args, **kwargs: ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)  # verifies, but trusts no root yet
        for name in ("create_default_context", "_create_default_https_context"):
            patch = mock.patch.object(ssl, name, empty)
            patch.start()
            self.addCleanup(patch.stop)

    def test_docs_9_servers_are_verified_against_certifi_where_python_has_no_roots(self):
        with mock.patch.dict(sys.modules, {"certifi": self.certifi}):
            run = run_post(TWO_SHOTS, {"a.png": PNG, "b.png": PNG}, verify=True)
        self.assertEqual(run.code, 0, run.shown)
        for sent in run.sent:
            self.assertEqual(sent.context.verify_mode, ssl.CERT_REQUIRED)
            self.assertTrue(sent.context.check_hostname)
            self.assertGreater(sent.context.cert_store_stats()["x509_ca"], 0, "a request went out with no root to verify against")

    def test_docs_9_no_roots_and_no_certifi_fails_before_anything_is_sent_and_says_what_to_do(self):
        with mock.patch.dict(sys.modules, {"certifi": None}):
            run = run_post(TWO_SHOTS, {"a.png": PNG, "b.png": PNG}, verify=True)
        self.assertIn("CERTIFICATE_VERIFY_FAILED", str(run.code))
        self.assertIn("Install Certificates.command", str(run.code))
        self.assertEqual(run.sent, [])
        self.assertEqual(run.ado.posted, [])


def guid_url(template):
    """An upload's answer whose url is template, with {id} the attachment's id and {p} the project's."""
    return lambda id, name: template.format(id=id, p=PROJECT_ID)


class Scrubbing(AsPosted):
    """D6: what post.py prints is scrubbed after it is built and before it is cut to length, for the token and the
    Basic value as is, trimmed, percent-encoded, JSON-escaped and as HTML character references."""

    def refused(self, answer, token=PAT, step="upload"):
        run = run_post(TWO_SHOTS, {"a.png": PNG, "b.png": PNG}, AzureDevOps(replace={step: answer}), token=token)
        self.assertNotEqual(run.code, 0)
        self.assertEqual(run.ado.posted, [])
        return run

    def test_d6_a_cut_through_a_token_an_error_body_echoes_shows_none_of_it(self):
        for token, secret in ((PAT, PAT), (PAT, BASIC), (PAT, BASIC.rstrip("=")), (JWT, JWT), (PUNCT, PUNCT_BASIC)):
            for inside in (12, 40, len(secret) - 1, len(secret)):
                with self.subTest(secret=secret[:8], inside=inside):
                    body = ("x" * (300 - inside) + secret + " and the rest of the page").encode()
                    run = self.refused(reply(401, body=body, reason="Unauthorized"), token)
                    self.assertIn("HTTP 401", run.shown)
                    self.assertNoPartShown(run.shown)

    def test_d6_a_cut_through_a_token_a_reason_phrase_echoes_shows_none_of_it(self):
        for inside in (12, 40, len(BASIC) - 1):
            with self.subTest(inside=inside):
                run = self.refused(reply(401, body=b"", reason="z" * (100 - inside) + BASIC))
                self.assertIn("HTTP 401", run.shown)
                self.assertNoPartShown(run.shown)

    def test_d6_an_error_body_echoing_the_token_in_any_one_layer_of_encoding_shows_none_of_it(self):
        for token, secrets in ((PAT, (PAT, BASIC, BASIC.rstrip("="))), (PUNCT, (PUNCT, PUNCT_BASIC)), (JWT, (JWT,))):
            for secret in secrets:
                for form, text in encoded(secret).items():
                    with self.subTest(secret=secret[:8], form=form):
                        run = self.refused(reply(400, body=f"rejected: Authorization={text}; try again".encode(),
                                                 reason="Bad Request", headers={"Content-Type": "text/plain"}), token)
                        self.assertIn("HTTP 400", run.shown)
                        self.assertIn("try again", run.shown)  # the rest of the body is still shown
                        self.assertNoPartShown(run.shown)

    def test_d6_scrub_masks_the_token_and_its_header_value_in_every_form_of_one_layer(self):
        post = load_post()
        for token in (PAT, PUNCT, JWT):
            client = post.Client(token, opener=types.SimpleNamespace(open=None))
            value = client.header.split(" ", 1)[1]
            for secret in {token, value, value.rstrip("=")}:
                for form, text in encoded(secret).items():
                    with self.subTest(secret=secret[:8], form=form):
                        shown = post.scrub(f"before {text} after", client.secrets)
                        self.assertTrue(shown.startswith("before ") and shown.endswith(" after"), shown)
                        self.assertNoPartShown(shown)

    def test_d6_a_status_line_echoing_the_token_shows_none_of_it(self):
        for text in (BASIC.replace("=", "%3D"), "".join(f"&#{ord(c)};" for c in PAT), "y" * 250 + BASIC):
            with self.subTest(text=text[:8]):
                run = self.refused(("200x", f"echo {text}", {}, b""))
                self.assertIn("failed", run.shown)
                self.assertNoPartShown(run.shown)

    def test_d6_a_redirect_to_a_host_made_of_the_token_shows_none_of_it_in_any_case(self):
        for location in (f"https://{'x' * 60}{PAT}.evil.example/", f"https://{BASIC.rstrip('=')}.evil.example/",
                         f"https://{''.join(f'%{ord(c):02X}' for c in PAT)}.evil.example/",
                         f"https://user:{PAT}@evil.example/"):
            with self.subTest(location=location[:30]):
                run = self.refused(reply(302, body=b"", reason="Found", headers={"Location": location}))
                self.assertIn("not followed", run.shown)
                self.assertIn("evil.example", run.shown)
                self.assertNoPartShown(run.shown, fold=True)

    def test_d6_an_attachment_url_refused_with_the_token_at_the_cut_shows_none_of_it(self):
        url = f"https://evil.example/{'x' * 139}{PAT}/_apis/wit/attachments/{{id}}"
        run = run_post(TWO_SHOTS, {"a.png": PNG, "b.png": PNG}, AzureDevOps(attachment_url=guid_url(url)))
        self.assertIn("refusing to embed", str(run.code))
        self.assertEqual(run.ado.posted, [])
        self.assertNoPartShown(run.shown)

    def test_d6_the_success_line_shows_no_token_the_answer_put_in_it(self):
        for version in (f"Basic {BASIC}", PAT, BASIC.replace("=", "%3D"), BASIC.rstrip("=")):
            def read(sent, ado, version=version):
                answer = json.loads(ado.read(sent)[3])
                answer["version"] = version
                return reply(200, answer)

            with self.subTest(version=version[:8]):
                run = run_post(TWO_SHOTS, {"a.png": PNG, "b.png": PNG}, AzureDevOps(replace={"read": read}))
                self.assertEqual(run.code, 0, run.shown)
                self.assertIn(f"comment 7 (version ", run.out)
                self.assertIn(f"on {ORG}/Widgets/_workitems/edit/70012", run.out)
                self.assertNoPartShown(run.shown)

    def test_d6_a_comment_id_holding_the_token_is_refused_unshown_and_says_the_comment_was_posted(self):
        for id in (PAT, f"7?{PAT}", BASIC.replace("=", "%3D")):
            with self.subTest(id=id[:8]):
                run = run_post(TWO_SHOTS, {"a.png": PNG, "b.png": PNG},
                               AzureDevOps(replace={"comment": reply(200, {"id": id, "version": 1})}))
                self.assertNotEqual(run.code, 0)
                self.assertIn("was posted", run.shown)
                self.assertNotIn("read", [AzureDevOps.step(s) for s in run.sent])
                self.assertNoPartShown(run.shown)


class AttachmentHost(AsPosted):
    """An attachment url from an upload's answer must be on --org's own host, under --org's path, and hold no token,
    or the comment is not posted and the url is never requested."""

    def assertRefused(self, run, shown=True):
        self.assertIn("refusing to embed", str(run.code))
        self.assertEqual(run.ado.posted, [], "a comment was posted with the attachment url")
        self.assertNotIn("download", [AzureDevOps.step(s) for s in run.sent])
        if shown:
            self.assertNoPartShown(run.shown)

    def test_an_attachment_on_another_org_or_host_is_refused_and_gets_no_request(self):
        for org, url in ((ORG, "https://otherorg.visualstudio.com/_apis/wit/attachments/{id}"),
                         (ORG, "https://evil.example.visualstudio.com/{p}/_apis/wit/attachments/{id}"),
                         (ORG, "https://dev.azure.com/otherorg/_apis/wit/attachments/{id}"),
                         (ORG, "https://dev.azure.com/otherorg/{p}/_apis/wit/attachments/{id}"),
                         (ORG, "https://dev.azure.com/contoso-other/{p}/_apis/wit/attachments/{id}"),
                         (ORG, "https://dev.azure.com/_apis/wit/attachments/{id}"),
                         (ORG, "https://contoso.visualstudio.com/{p}/_apis/wit/attachments/{id}"),
                         ("https://contoso.visualstudio.com", "https://dev.azure.com/contoso/{p}/_apis/wit/attachments/{id}"),
                         ("https://contoso.visualstudio.com", "https://other.visualstudio.com/{p}/_apis/wit/attachments/{id}")):
            with self.subTest(org=org, url=url):
                host = urllib.parse.urlsplit(org).hostname
                run = run_post(TWO_SHOTS, {"a.png": PNG, "b.png": PNG},
                               AzureDevOps(attachment_url=guid_url(url), host=host), args=["--org", org])
                self.assertRefused(run)
                self.assertEqual({s.host for s in run.sent}, {host}, "a request went to a host other than --org's")

    def test_the_token_in_an_attachment_url_path_or_query_is_refused_and_unshown(self):
        for url in (f"https://dev.azure.com/{PAT}/_apis/wit/attachments/{{id}}",
                    f"{ORG}/{PAT}/_apis/wit/attachments/{{id}}",
                    f"{ORG}/{PAT[:26]}/{PAT[26:]}/_apis/wit/attachments/{{id}}",
                    f"{ORG}/{{p}}/_apis/wit/attachments/{PAT}",
                    f"{ORG}/{{p}}/_apis/wit/attachments/{{id}}?fileName=a.png&k={PAT}",
                    f"{ORG}/{{p}}/_apis/wit/attachments/{{id}}?k={urllib.parse.quote(BASIC, safe='')}",
                    f"{ORG}/{{p}}/_apis/wit/attachments/{{id}}?k={BASIC.rstrip('=')}",
                    f"{ORG}/{{p}}/_apis/wit/attachments/{{id}}?k={''.join(f'%{ord(c):02x}' for c in PAT)}"):
            with self.subTest(url=url[:60]):
                run = run_post(TWO_SHOTS, {"a.png": PNG, "b.png": PNG}, AzureDevOps(attachment_url=guid_url(url)))
                # A token cut in two by a '/' is no form a scrub matches, like one encoded twice; the refusal is
                # what keeps it out of the comment, while the message shows the url it refused.
                self.assertRefused(run, shown=f"{PAT[:26]}/{PAT[26:]}" not in url)
                self.assertOnlyAzureDevOps(run)

    def test_an_attachment_on_the_orgs_own_host_in_any_of_its_shapes_is_kept(self):
        for org, url in ((ORG, "https://dev.azure.com/contoso/_apis/wit/attachments/{id}"),
                         (ORG, "https://DEV.AZURE.COM/Contoso/{p}/_apis/wit/attachments/{id}?fileName=a.png"),
                         (ORG, "https://dev.azure.com/contoso/Widgets/_apis/wit/attachments/{id}"),
                         ("https://contoso.visualstudio.com", "https://contoso.visualstudio.com/{p}/_apis/wit/attachments/{id}"),
                         ("https://contoso.visualstudio.com", "https://contoso.visualstudio.com/DefaultCollection/{p}/_apis/wit/attachments/{id}"),
                         ("https://contoso.visualstudio.com/", "https://contoso.visualstudio.com/_apis/wit/attachments/{id}")):
            with self.subTest(org=org, url=url):
                host = urllib.parse.urlsplit(org).hostname
                run = run_post(TWO_SHOTS, {"a.png": PNG, "b.png": PNG},
                               AzureDevOps(attachment_url=guid_url(url), host=host), args=["--org", org])
                self.assertEqual(run.code, 0, run.shown)
                self.assertEqual({(s.scheme, s.host) for s in run.sent}, {("https", host)})
                self.assertEqual([AzureDevOps.step(s) for s in run.sent].count("download"), 2)


class AfterPosting(AsPosted):
    """Whatever stops post.py once the comment is up is reported as an error that says the comment was posted, with
    its id where Azure DevOps gave one, so it is not posted twice."""

    def test_a_malformed_redirect_at_any_step_is_refused_as_a_redirect(self):
        for step in ("upload", "comment", "read", "download"):
            for location in ("https://[bad", "https://[::1/x", "https://dev.azure.com:port/x"):
                with self.subTest(step=step, location=location):
                    run = run_post(TWO_SHOTS, {"a.png": PNG, "b.png": PNG},
                                   AzureDevOps(replace={step: reply(302, body=b"", reason="Found", headers={"Location": location})}))
                    self.assertNotEqual(run.code, 0)
                    self.assertNotIn("stopped by", run.shown)
                    self.assertIn("HTTP 302", run.shown)
                    self.assertIn("not followed", run.shown)
                    self.assertOnlyAzureDevOps(run)
                    if step in ("read", "download"):
                        self.assertIn("comment 7 was posted", run.shown)

    def test_a_comment_id_that_is_not_a_number_is_refused_and_says_the_comment_was_posted(self):
        for id in ("7é", "7/../../8", "7?x=1", "7", 7.5, True, None, [7], {"id": 7}):
            with self.subTest(id=id):
                run = run_post(TWO_SHOTS, {"a.png": PNG, "b.png": PNG},
                               AzureDevOps(replace={"comment": reply(200, {"id": id, "version": 1})}))
                self.assertNotEqual(run.code, 0)
                self.assertNotIn("stopped by", run.shown)
                self.assertIn("was posted", run.shown)
                self.assertIn("look at the work item", run.shown)
                self.assertNotIn("read", [AzureDevOps.step(s) for s in run.sent])
                if isinstance(id, str):
                    self.assertIn(id, run.shown)  # the id as Azure DevOps gave it

    def test_an_edit_answered_with_an_id_that_is_not_a_number_names_the_comment_edited(self):
        ado = AzureDevOps(replace={"comment": reply(200, {"id": "12é", "version": 2})})
        ado.comments[12] = "the text first posted"
        run = run_post(TWO_SHOTS, {"a.png": PNG, "b.png": PNG}, ado, args=["--edit", "12"])
        self.assertNotEqual(run.code, 0)
        self.assertIn("comment 12 was posted", run.shown)
        self.assertNotIn("stopped by", run.shown)

    def test_an_image_source_that_is_not_a_url_does_not_stop_the_checks(self):
        render = lambda text: render_markdown_images(text) + '<img src="https://[bad"><img src="https://[::1/x">'
        run = run_post(TWO_SHOTS, {"a.png": PNG, "b.png": PNG}, AzureDevOps(render=render))
        self.assertEqual(run.code, 0, run.shown)
        self.assertIn("ok   images rendered", run.out)

    def test_anything_unforeseen_after_posting_says_the_comment_was_posted(self):
        read_bytes, posted = Path.read_bytes, []

        def unreadable_once_posted(path):
            if posted:
                raise PermissionError(13, "Permission denied", str(path))
            return read_bytes(path)

        def comment(sent, ado):
            posted.append(True)
            return ado.comment(sent)

        with mock.patch.object(Path, "read_bytes", unreadable_once_posted):
            run = run_post(TWO_SHOTS, {"a.png": PNG, "b.png": PNG}, AzureDevOps(replace={"comment": comment}))
        self.assertNotEqual(run.code, 0)
        self.assertIn("comment 7 was posted", run.shown)
        self.assertIn("PermissionError", run.shown)
        self.assertEqual(len(run.ado.posted), 1)


def dry_run(comment_text, images, env):
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "comment.md").write_text(comment_text, encoding="utf-8")
        for name, data in images.items():
            (Path(d) / name).write_bytes(data)
        argv = [sys.executable, str(POST), "--org", ORG, "--project", "Widgets", "--work-item", "1",
                "--comment", str(Path(d) / "comment.md"), "--dry-run"]
        return subprocess.run(argv, capture_output=True, env=env, timeout=60)


class ConsoleCodePage(unittest.TestCase):
    """correctness-11: Windows writes a piped stdout in the ANSI code page, cp1252 in the West, which has no ✅ or →.
    PYTHONIOENCODING=cp1252 puts any platform in that state."""

    def setUp(self):
        self.env = {k: v for k, v in os.environ.items() if k not in ("PYTHONUTF8", "PYTHONIOENCODING")}
        self.env["PYTHONIOENCODING"] = "cp1252"

    def test_correctness_11_the_preview_prints_in_utf8_whatever_the_code_page(self):
        proc = dry_run("✅ Removed route on Test: 404 as expected → ✓\n\n{{image:a.png}}\n", {"a.png": PNG}, self.env)
        self.assertEqual(proc.returncode, 0, proc.stderr.decode("utf-8", "replace"))
        out = proc.stdout.decode("utf-8")
        self.assertIn("DRY RUN - would add a comment on work item 1 with 1 image(s):", out)
        self.assertIn("✅ Removed route on Test: 404 as expected → ✓", out)
        self.assertIn(f"![a.png](<upload {len(PNG)} bytes>)", out)

    def test_correctness_11_a_refusal_naming_a_file_prints_in_utf8_too(self):
        proc = dry_run("{{image:✅-shot.png}}\n", {}, self.env)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("missing images:", proc.stderr.decode("utf-8"))
        self.assertIn("✅-shot.png", proc.stderr.decode("utf-8"))


def twice_if_the_network_failed(attempt):
    """A network test's call, made again once when the network, not the script, failed: a connection error, or a
    5xx from the public host."""
    try:
        return attempt()
    except Exception as e:
        if e.__cause__ is None and not re.search(r"answered HTTP 5\d\d", str(e)):
            raise
        time.sleep(3)
        return attempt()


class Recorder(urllib.request.BaseHandler):
    """Sees every request the opener makes, a followed redirect's second request included."""

    def __init__(self):
        self.requests = []

    def https_request(self, req):
        self.requests.append(req)
        return req

    http_request = https_request


@unittest.skipUnless(os.environ.get("API_CALL_E2E"), "reaches httpbin.org; set API_CALL_E2E=1 to run it")
class OverTheNetwork(unittest.TestCase):
    """Real TLS to public echo hosts, with a fake token. The host check is widened to the one public host each test
    calls first, so the opener's own redirect handling is what stands between the token and the second host."""

    def client(self, first_host):
        post = load_post()
        patch = mock.patch.object(post, "azure_devops", lambda url: urllib.parse.urlsplit(url).hostname == first_host)
        patch.start()
        self.addCleanup(patch.stop)
        self.seen = Recorder()
        opener = post.build_opener()
        opener.add_handler(self.seen)
        return post, post.Client(PAT, opener)

    def test_docs_3_a_redirect_from_httpbin_is_not_followed_and_the_token_never_reaches_the_second_host(self):
        # postman-echo.com/headers echoes the headers it gets, so a followed redirect would come back as a 200
        # carrying the token. It must come back as httpbin's 302 instead, with no request to postman-echo.com.
        post, client = self.client("httpbin.org")
        url = "https://httpbin.org/redirect-to?" + urllib.parse.urlencode({"url": "https://postman-echo.com/headers", "status_code": 302})

        def attempt():
            self.seen.requests.clear()
            try:
                answer = client.call("GET", url)
            except post.Problem as e:
                if e.__cause__ is not None or re.search(r"answered HTTP 5\d\d", str(e)):
                    raise
                return str(e)
            self.fail(f"the redirect was followed: {answer}")

        problem = twice_if_the_network_failed(attempt)
        self.assertIn("HTTP 302", problem)
        self.assertIn("postman-echo.com", problem)
        self.assertIn("not followed", problem)
        self.assertNotIn(PAT, problem)
        self.assertNotIn(BASIC, problem)
        self.assertEqual([urllib.parse.urlsplit(r.full_url).hostname for r in self.seen.requests], ["httpbin.org"])
        self.assertEqual(self.seen.requests[0].get_header("Authorization"), f"Basic {BASIC}")

    def test_docs_9_and_docs_10_a_verified_tls_call_carries_the_token_as_sent(self):
        post, client = self.client("httpbin.org")
        echo = twice_if_the_network_failed(lambda: client.call("GET", "https://httpbin.org/headers"))
        self.assertEqual(echo["headers"].get("Authorization"), f"Basic {BASIC}")
        self.assertEqual([urllib.parse.urlsplit(r.full_url).hostname for r in self.seen.requests], ["httpbin.org"])


if __name__ == "__main__":
    unittest.main()
