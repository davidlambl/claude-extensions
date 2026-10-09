"""The Postman source, run with: python3 -m unittest discover -s skills/api-call/tests

The unit tests build requests directly, and drive send() and main() through urllib with only the socket faked:
http.client still builds and checks each request (its URL, its header values), and a fake socket records what was
sent and answers from a script. So the refusal, the masking, the redirect handling and the error scrubbing run end
to end without a network. EndToEnd sends real requests through call.py to postman-echo.com and httpbin.org with fake
secrets, and runs only with API_CALL_E2E set (CI sets it). A test's name carries the id of the review finding it
covers (docs_1, secrets_2, checks_4, ...)."""
import contextlib, http, http.client, io, json, os, subprocess, sys, tempfile, unittest, urllib.parse, urllib.request
from datetime import datetime
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "skills" / "api-call" / "scripts"
sys.path.insert(0, str(SCRIPTS / "sources"))
import postman  # noqa: E402

KEY = "sekret-123456"
ENV = {"name": "Test", "values": [
    {"key": "baseUrl", "value": "https://api.example.com/", "type": "default", "enabled": True},
    {"key": "apiKey", "value": KEY, "type": "secret", "enabled": True},
    {"key": "part", "value": "plain-part-abcdef", "type": "default", "enabled": True},
    {"key": "off", "value": "never", "enabled": False},
]}
V21 = "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"
V20 = "https://schema.getpostman.com/json/collection/v2.0.0/collection.json"


def collection(auth=None, folders=(), variables=None, schema=None):
    info = {"name": "Example", **({"schema": schema} if schema else {})}
    return {"info": info, "auth": auth, "item": list(folders),
            "variable": variables if variables is not None else [{"key": "version", "value": "v2"}, {"key": "baseUrl", "value": "https://collection"}]}


def folder(name, auth=None):
    """A top-level folder; auth=None leaves the key out, which in Postman means the folder inherits."""
    return {"name": name, "item": [], **({"auth": auth} if auth is not None else {})}


APIKEY_HEADER = {"type": "apikey", "apikey": [{"key": "key", "value": "X-Api-Key"}, {"key": "value", "value": "{{apiKey}}"}, {"key": "in", "value": "header"}]}
APIKEY_QUERY = {"type": "apikey", "apikey": [{"key": "key", "value": "api key"}, {"key": "value", "value": "{{apiKey}}"}, {"key": "in", "value": "query"}]}
BEARER = {"type": "bearer", "bearer": [{"key": "token", "value": "{{apiKey}}"}]}
BASIC = {"type": "basic", "basic": [{"key": "username", "value": "amy"}, {"key": "password", "value": "{{apiKey}}"}]}
NOAUTH = {"type": "noauth"}


# --- a fake network: http.client builds the request, a fake socket answers -----------------------------------------

REAL_HTTPS_HANDLER = urllib.request.HTTPSHandler


def reply(status, headers=None, body=b""):
    """The bytes of an HTTP/1.1 response."""
    head = [f"HTTP/1.1 {status} {http.HTTPStatus(status).phrase}"] + [f"{k}: {v}" for k, v in (headers or {}).items()]
    head.append(f"Content-Length: {len(body)}")
    return ("\r\n".join(head) + "\r\n\r\n").encode("latin-1") + body


class FakeSocket:
    """Takes what http.client sends; when it reads the response, notes the request in `seen` and answers."""

    def __init__(self, response, seen):
        self.response, self.seen, self.sent = response, seen, bytearray()

    def sendall(self, data):
        self.sent += data

    def makefile(self, mode="rb", *args, **kwargs):
        lines = bytes(self.sent).split(b"\r\n\r\n", 1)[0].decode("latin-1").split("\r\n")
        method, target, _ = lines[0].split(" ", 2)
        headers = {k.lower(): v for k, v in (line.split(": ", 1) for line in lines[1:])}
        self.seen.append({"method": method, "url": f"https://{headers['host']}{target}", "headers": headers})
        return io.BytesIO(self.response)

    def close(self):
        pass


class FakeHTTPS(REAL_HTTPS_HANDLER):
    """urllib's HTTPS handler with the socket faked. Each connection takes the next step of `script`: a
    (status, headers, body) reply, an exception to raise, or a function of the request that returns one."""
    script, seen = [], []

    def https_open(self, req):
        handler = type(self)

        class Connection(http.client.HTTPSConnection):
            def connect(self):
                step = handler.script.pop(0)
                if callable(step) and not isinstance(step, BaseException):
                    step = step(req)
                if isinstance(step, BaseException):
                    raise step
                self.sock = FakeSocket(reply(*step), handler.seen)

        return self.do_open(Connection, req, context=self._context)


@contextlib.contextmanager
def server(*script):
    """For the block, every HTTPS request goes to a FakeHTTPS with this script; yields the list of requests seen."""
    cls = type("ScriptedHTTPS", (FakeHTTPS,), {"script": list(script), "seen": []})
    with mock.patch.object(urllib.request, "HTTPSHandler", cls), mock.patch.object(urllib.request, "getproxies", return_value={}):
        yield cls.seen


OK_JSON = (200, {"Content-Type": "application/json; charset=utf-8"}, b'{"ok": true}')


def encodings(secret):
    """The secret as text could carry it, worked out here rather than by the code under test: plain, and URL-encoded
    by quote and by quote_plus with either case of hex."""
    out = {secret, urllib.parse.quote(secret, safe=""), urllib.parse.quote_plus(secret)}
    return out | {f.lower() for f in out}


def run_main(argv, env=None):
    """postman.main() with these arguments, in this process: (exit code, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with mock.patch.object(sys, "argv", ["postman.py", *argv]), mock.patch.dict(os.environ, env or {}), \
            contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            postman.main()
            code = 0
        except SystemExit as e:
            code = e.code
            if isinstance(code, str):  # sys.exit(message) prints the message and exits 1
                err.write(code)
                code = 1
            elif code is None:
                code = 0
    return code, out.getvalue(), err.getvalue()


# --- unit tests -----------------------------------------------------------------------------------------------------

class Variables(unittest.TestCase):
    def test_environment_over_collection_then_extra_and_secrets(self):
        variables, secrets = postman.load_variables(collection(), ENV, {"token": "t0ken-abcdef"})
        self.assertEqual(variables["baseUrl"], "https://api.example.com/")
        self.assertEqual(variables["version"], "v2")
        self.assertEqual(variables["token"], "t0ken-abcdef")
        self.assertNotIn("off", variables)
        self.assertEqual(secrets, [KEY, "t0ken-abcdef"])

    def test_templates_resolve_through_each_other(self):
        self.assertEqual(postman.resolve("{{a}}/x", {"a": "{{ b }}", "b": "y"}), "y/x")

    def test_unknown_and_dynamic_names_raise(self):
        with self.assertRaises(postman.Problem):
            postman.resolve("{{nope}}", {})
        with self.assertRaises(postman.Problem):
            postman.resolve("{{$guid}}", {})


class Scrub(unittest.TestCase):
    """What scrub() masks and forms() lists: every form an error or a record could carry a secret in."""

    def test_docs_2_scrub_masks_the_url_encoded_forms(self):
        key = "fake/key+with=1234567"  # '/', '+' and '=' are what URL encoding changes, as in a base64 key
        error = "URL can't contain control characters. '/anything?q=foo bar&api_key=fake%2Fkey%2Bwith%3D1234567' (found at least ' ')"
        scrubbed = postman.scrub(error, [key])
        self.assertNotIn("fake%2Fkey", scrubbed)
        self.assertIn("api_key=********", scrubbed)
        spaced = "two words key"  # a space is where quote and quote_plus differ: %20 against +
        for text in ("a two%20words%20key b", "a two+words+key b", "a two words key b"):
            self.assertEqual(postman.scrub(text, [spaced]), "a ******** b")
        slashed = "fake/key with-123"  # quote(safe=""), quote_plus and a path's encoding each differ: %2F%20, %2F+, /%20
        for text in ("a fake%2Fkey%20with-123 b", "a fake%2Fkey+with-123 b", "a fake/key%20with-123 b"):
            self.assertEqual(postman.scrub(text, [slashed]), "a ******** b")

    def test_secrets_3_scrub_masks_a_value_as_repr_shows_it(self):
        for key, error in (("FAKE-key-4b8d2e61\n", "Invalid header value b'FAKE-key-4b8d2e61\\n'"),
                           ("FAKE-key-4b8d2e61\n", "Invalid header value b'Bearer FAKE-key-4b8d2e61\\n'"),
                           ("FAKE-ü-4b8d2e61\n", "Invalid header value b'FAKE-\\xfc-4b8d2e61\\n'"),  # latin-1, as http.client encodes
                           ("FAKE-key-4b8d2e61\n", "bad value 'FAKE-key-4b8d2e61\\n'")):
            scrubbed = postman.scrub(error, [key])
            self.assertNotIn("4b8d2e61", scrubbed, scrubbed)
            self.assertIn("********", scrubbed)

    def test_scrub_withholds_a_message_it_cannot_mask(self):
        # The key JSON-escaped with upper-case hex: no form matches it, but decoded it is the key.
        self.assertEqual(postman.scrub("server said: fake\\u002Fkey\\u002Bwith=1234567", ["fake/key+with=1234567"]), postman.WITHHELD)
        self.assertEqual(postman.scrub("nothing secret here", ["fake/key+with=1234567"]), "nothing secret here")

    def test_scrub_ignores_an_empty_or_blank_secret(self):
        self.assertEqual(postman.scrub("a message", ["", " "]), "a message")

    def test_secrets_2_forms_include_the_trimmed_secret(self):
        for padded in ("FAKE-key-4b8d2e61 ", " FAKE-key-4b8d2e61\t", "FAKE-key-4b8d2e61\n"):
            self.assertIn("FAKE-key-4b8d2e61", postman.forms(padded))

    def test_a_form_under_six_characters_is_left_out(self):
        self.assertEqual(postman.forms("short"), [])
        self.assertNotIn("abcde", postman.forms("abcde "))  # the trimmed form is too short to tell from text
        self.assertIn("abcde ", postman.forms("abcde "))


class Build(unittest.TestCase):
    def build(self, auth=None, folders=(), folder=None, path="/items/1", extra=None, schema=None, env=ENV):
        return postman.build(collection(auth, folders, schema=schema), env, extra or {}, folder, "baseUrl", path)

    def test_plain_get(self):
        b = self.build(path="items/1")
        self.assertEqual(b["url"], "https://api.example.com/items/1")
        self.assertEqual(b["shown_url"], b["url"])
        self.assertEqual(b["headers"], {"Accept": "application/json", "User-Agent": "api-call"})
        self.assertEqual((b["auth"], b["folder"], b["path"], b["secrets"]), ("none", None, "/items/1", [KEY]))

    def test_api_key_in_header_is_sent_and_masked(self):
        b = self.build(APIKEY_HEADER)
        self.assertEqual(b["headers"]["X-Api-Key"], KEY)
        self.assertEqual(b["shown_headers"]["X-Api-Key"], "********")
        self.assertEqual(b["auth"], "API key in header X-Api-Key")

    def test_api_key_in_query_is_sent_and_masked(self):
        b = self.build(APIKEY_QUERY, path="/items?x=1")
        self.assertEqual(b["url"], "https://api.example.com/items?x=1&api+key=sekret-123456")
        self.assertEqual(b["shown_url"], "https://api.example.com/items?x=1&api+key=********")
        self.assertNotIn("sekret", b["shown_url"])

    def test_bearer_and_basic(self):
        b = self.build(BEARER)
        self.assertEqual((b["headers"]["Authorization"], b["shown_headers"]["Authorization"]), (f"Bearer {KEY}", "Bearer ********"))
        b = self.build(BASIC)
        self.assertTrue(b["headers"]["Authorization"].startswith("Basic "))
        self.assertEqual(b["shown_headers"]["Authorization"], "Basic ********")
        self.assertIn(b["headers"]["Authorization"][6:], b["secrets"])
        self.assertEqual(b["auth"], "basic auth as amy")

    def test_folder_auth(self):
        b = self.build(folders=[folder("v1", BEARER), folder("v2")])
        self.assertEqual((b["folder"], b["auth"]), ("v1", "bearer token"))
        with self.assertRaises(postman.Problem):
            self.build(folders=[folder("v1", BEARER), folder("v2", APIKEY_HEADER)])
        b = self.build(folders=[folder("v1", BEARER), folder("v2", APIKEY_HEADER)], folder="v2")
        self.assertEqual(b["auth"], "API key in header X-Api-Key")
        with self.assertRaises(postman.Problem):
            self.build(folder="v3")

    def test_collection_auth_wins_over_folders(self):
        b = self.build(BASIC, folders=[folder("v1", BEARER)])
        self.assertEqual((b["folder"], b["auth"]), (None, "basic auth as amy"))

    def test_correctness_13_a_named_folder_without_auth_inherits_the_collections(self):
        folders = [folder("Reports"), folder("Admin", APIKEY_HEADER), folder("Public", NOAUTH), {"name": "Null", "item": [], "auth": None}]
        b = self.build(BEARER, folders, folder="Reports")
        self.assertEqual((b["folder"], b["auth"], b["headers"].get("Authorization")), ("Reports", "bearer token", f"Bearer {KEY}"))
        self.assertEqual(self.build(BEARER, folders, folder="Null")["auth"], "bearer token")  # "auth": null inherits too
        self.assertEqual(self.build(BEARER, folders, folder="Admin")["auth"], "API key in header X-Api-Key")
        b = self.build(BEARER, folders, folder="Public")  # only an explicit noauth means none
        self.assertEqual((b["auth"], b["headers"].get("Authorization")), ("none", None))

    def test_https_and_base_url_required(self):
        with self.assertRaises(postman.Problem):
            postman.build(collection(), {"values": [{"key": "baseUrl", "value": "http://x"}]}, {}, None, "baseUrl", "/")
        with self.assertRaises(postman.Problem):
            postman.build(collection(variables=[]), {"values": []}, {}, None, "baseUrl", "/")

    def test_unsupported_auth(self):
        with self.assertRaises(postman.Problem):
            self.build({"type": "oauth2"})

    def test_checks_3_the_parts_of_a_composite_secret_are_secrets(self):
        # `part` is typed default, not secret: only resolve() recording what it substitutes makes it a secret.
        self.assertNotIn("plain-part-abcdef", postman.load_variables(collection(), ENV, {})[1])
        b = self.build({"type": "bearer", "bearer": [{"key": "token", "value": "pre-{{part}}-post"}]})
        self.assertIn("plain-part-abcdef", b["secrets"])
        self.assertIn("pre-plain-part-abcdef-post", b["secrets"])
        record = {"url": b["shown_url"], "request_headers": b["shown_headers"], "response_headers": {}, "body": '{"echo": "plain-part-abcdef"}'}
        self.assertEqual(postman.exposed(record, b["secrets"]), "plain-part-abcdef")

    def test_correctness_16_a_path_with_a_space_or_non_ascii_is_percent_encoded(self):
        b = self.build(path="/get?q=two words&city=Málaga")
        self.assertEqual(b["url"], "https://api.example.com/get?q=two%20words&city=M%C3%A1laga")
        self.assertEqual(b["shown_url"], b["url"])
        self.assertEqual(b["path"], "/get?q=two words&city=Málaga")  # the path as given
        self.assertEqual(self.build(path="/get?name=caf%C3%A9")["url"], "https://api.example.com/get?name=caf%C3%A9")  # an escape stays
        self.assertEqual(self.build(path="/get?off=100%")["url"], "https://api.example.com/get?off=100%25")  # a lone % does not
        self.assertEqual(self.build(path="/a/b?x=1&y=2:3@4;5,6=7+8&f[k]=$")["url"], "https://api.example.com/a/b?x=1&y=2:3@4;5,6=7+8&f[k]=$")
        b = self.build(APIKEY_QUERY, path="/v1 beta/items?q=a b")
        self.assertEqual(b["url"], "https://api.example.com/v1%20beta/items?q=a%20b&api+key=sekret-123456")
        self.assertEqual(b["shown_url"], "https://api.example.com/v1%20beta/items?q=a%20b&api+key=********")
        spaced = {"values": [{"key": "baseUrl", "value": "https://api.example.com/v1 beta"}]}  # from a shared environment
        self.assertEqual(self.build(path="/items", env=spaced)["url"], "https://api.example.com/v1%20beta/items")

    def test_extra_1_a_v2_0_export_with_object_auth_sends_the_credential(self):
        b = self.build({"type": "bearer", "bearer": {"token": "{{apiKey}}"}}, schema=V20)
        self.assertEqual(b["headers"]["Authorization"], f"Bearer {KEY}")
        b = self.build({"type": "apikey", "apikey": {"key": "X-Api-Key", "value": "{{apiKey}}", "in": "header"}}, schema=V20)
        self.assertEqual((b["headers"]["X-Api-Key"], b["auth"]), (KEY, "API key in header X-Api-Key"))
        b = self.build({"type": "basic", "basic": {"username": "amy", "password": "{{apiKey}}"}}, schema=V20)
        self.assertEqual(b["headers"]["Authorization"], "Basic " + postman.base64.b64encode(f"amy:{KEY}".encode()).decode())
        self.assertEqual(b["auth"], "basic auth as amy")
        self.assertEqual(self.build(BEARER, schema=V21)["headers"]["Authorization"], f"Bearer {KEY}")
        v20_vars = collection(variables=[{"id": "baseUrl", "value": "https://v20.example.com"}], schema=V20)  # named by id
        self.assertEqual(postman.build(v20_vars, {"values": []}, {}, None, "baseUrl", "/x")["url"], "https://v20.example.com/x")

    def test_extra_1_an_export_of_another_schema_or_shape_is_refused_by_name(self):
        with self.assertRaisesRegex(postman.Problem, "v3.0.0.*not supported"):
            self.build(BEARER, schema="https://schema.getpostman.com/json/collection/v3.0.0/collection.json")
        with self.assertRaisesRegex(postman.Problem, "no 'info' block"):
            postman.build({"id": "v1", "name": "Old", "requests": []}, ENV, {}, None, "baseUrl", "/")
        with self.assertRaisesRegex(postman.Problem, "not a Postman environment export"):
            postman.build(collection(), collection(), {}, None, "baseUrl", "/")

    def test_extra_1_an_empty_credential_is_refused_rather_than_claimed(self):
        blank = {"values": [{"key": "baseUrl", "value": "https://api.example.com"}, {"key": "apiKey", "value": "", "type": "secret"}]}
        for auth in (BEARER, APIKEY_HEADER, APIKEY_QUERY, BASIC):
            with self.assertRaisesRegex(postman.Problem, "empty value.*--vars", msg=auth["type"]):
                self.build(auth, env=blank)
        with self.assertRaisesRegex(postman.Problem, "names no header"):
            self.build({"type": "apikey", "apikey": [{"key": "value", "value": "{{apiKey}}"}, {"key": "in", "value": "header"}]})

    def test_secrets_3_a_header_value_with_a_control_character_is_refused_without_the_value(self):
        for auth, name in ((APIKEY_HEADER, "X-Api-Key"), (BEARER, "Authorization")):
            for bad in ("FAKE-key-4b8d2e61\n", "FAKE-key-4b8d2e61\r\n", "\tFAKE-key-4b8d2e61", "FAKE-key-\x7f4b8d2e61"):
                with self.assertRaises(postman.Problem) as cm:
                    self.build(auth, extra={"apiKey": bad})
                self.assertNotIn("FAKE-key", str(cm.exception))
                self.assertIn(name, str(cm.exception))
                self.assertIn("control character", str(cm.exception))
        with self.assertRaises(postman.Problem) as cm:
            self.build(APIKEY_HEADER, extra={"apiKey": "FAKE-key-✓-4b8d2e61"})
        self.assertNotIn("FAKE-key", str(cm.exception))
        self.assertIn("outside Latin-1", str(cm.exception))
        b = self.build(APIKEY_QUERY, extra={"apiKey": "FAKE-key-4b8d2e61\n"})  # a query parameter carries it encoded
        self.assertTrue(b["url"].endswith("api+key=FAKE-key-4b8d2e61%0A"))


class Exposed(unittest.TestCase):
    RECORD = {"url": "https://api.example.com/items", "auth": "none", "request_headers": {"Accept": "application/json"},
              "response_headers": {}, "body": "ok"}

    def exposed(self, body, secret):
        return postman.exposed({**self.RECORD, "body": body}, [secret])

    def test_a_clean_record_passes(self):
        self.assertIsNone(postman.exposed(self.RECORD, [KEY]))
        self.assertIsNone(self.exposed('{"headers": {"x-api-key": "another-key-123456"}}', KEY))

    def test_a_secret_in_the_body_or_a_header_is_found(self):
        self.assertEqual(self.exposed(f"token={KEY}", KEY), KEY)
        self.assertEqual(postman.exposed({**self.RECORD, "response_headers": {"Location": f"/x?k={KEY}"}}, [KEY]), KEY)

    def test_checks_4_quote_and_quote_plus_forms_are_each_found(self):
        spaced = "two words key"  # encodes differently under quote (%20) and quote_plus (+)
        self.assertEqual(self.exposed("k=two%20words%20key", spaced), spaced)
        self.assertEqual(self.exposed("k=two+words+key", spaced), spaced)
        self.assertEqual(self.exposed("k=a%2Fb%3Dc%21x", "a/b=c!x"), "a/b=c!x")
        self.assertEqual(self.exposed("k=a%2fb%3dc%21x", "a/b=c!x"), "a/b=c!x")  # lower-case escapes too

    def test_secrets_2_a_trimmed_echo_is_found(self):
        echo = '{"headers": {"x-api-key": "FAKE-key-4b8d2e61"}}'  # what a server sends back after trimming the value
        for padded in ("FAKE-key-4b8d2e61 ", " FAKE-key-4b8d2e61", "FAKE-key-4b8d2e61\r\n"):
            self.assertEqual(self.exposed(echo, padded), padded)

    def test_docs_1_a_json_escaped_echo_is_found(self):
        # Each body carries the secret only escaped, and render.py draws a JSON body with its escapes decoded.
        self.assertEqual(self.exposed('{"X-Api-Key": "fake-\\u00fc-1234567"}', "fake-ü-1234567"), "fake-ü-1234567")
        self.assertEqual(self.exposed('{"error": "invalid key abc\\u002Bdef\\/ghi12=="}', "abc+def/ghi12=="), "abc+def/ghi12==")  # System.Text.Json, PHP
        self.assertEqual(self.exposed('{"k": "ab\\"cd-123456"}', 'ab"cd-123456'), 'ab"cd-123456')
        self.assertEqual(self.exposed('{"k": "p\\\\ss-word123"}', "p\\ss-word123"), "p\\ss-word123")
        self.assertEqual(self.exposed('{"k": "emoji-\\ud83d\\ude00-123"}', "emoji-\U0001f600-123"), "emoji-\U0001f600-123")
        self.assertEqual(self.exposed("<p>key: fake-\\u00fc-1234567</p>", "fake-ü-1234567"), "fake-ü-1234567")  # not JSON as a whole
        self.assertEqual(self.exposed("<p>invalid key abc&#43;def/ghi12==</p>", "abc+def/ghi12=="), "abc+def/ghi12==")  # HTML escapes

    def test_docs_1_the_body_as_drawn_is_checked(self):
        drawn = {"k": "fake-ü-1234567"}
        body = json.dumps(drawn)  # ensure_ascii, as httpbin and ASP.NET send it
        self.assertNotIn("fake-ü", body)
        # render.py draws a JSON body pretty-printed with ensure_ascii=False, so the key in the clear.
        self.assertIn(json.dumps(drawn, indent=2, ensure_ascii=False), postman.texts({**self.RECORD, "body": body}))
        self.assertEqual(self.exposed(body, "fake-ü-1234567"), "fake-ü-1234567")

    def test_short_values_are_not_checked(self):
        self.assertIsNone(self.exposed("short", "short"))


class Send(unittest.TestCase):
    """send() through urllib and http.client, only the socket faked: the errors are the ones http.client raises."""

    def test_docs_2_http_clients_invalid_url_error_is_scrubbed(self):
        key = "fake/key+with=1234567"
        url = "https://api.example.com/anything?q=foo bar&api_key=" + urllib.parse.quote_plus(key)  # a URL as build() made it before the fix
        with server(OK_JSON) as seen, self.assertRaises(http.client.InvalidURL) as cm:
            postman.send(url, {"Accept": "application/json"}, None, None, 5)
        self.assertEqual(seen, [])
        self.assertIn("fake%2Fkey%2Bwith%3D1234567", str(cm.exception))  # the error carries the key, encoded
        scrubbed = postman.scrub(str(cm.exception), [key])
        self.assertIn("api_key=********", scrubbed)
        self.assertFalse(any(form in scrubbed for form in encodings(key)), scrubbed)

    def test_secrets_3_http_clients_invalid_header_error_is_scrubbed(self):
        for key in ("FAKE-key-4b8d2e61\n", "FAKE-üé-4b8d2e61\n"):
            with server(OK_JSON) as seen, self.assertRaises(ValueError) as cm:
                postman.send("https://api.example.com/headers", {"Authorization": f"Bearer {key}"}, None, None, 5)
            self.assertEqual(seen, [])
            self.assertIn("4b8d2e61", str(cm.exception))  # the error carries the key, as repr shows it
            self.assertNotIn("4b8d2e61", postman.scrub(str(cm.exception), [key]))

    def test_checks_4_send_does_not_follow_a_redirect(self):
        hop = (302, {"Location": "https://other.example/headers"}, b"")
        with server(hop, OK_JSON) as seen:
            status, headers, body, _, _ = postman.send("https://api.example.com/go", {"X-Api-Key": KEY}, None, None, 5)
        self.assertEqual((status, headers["Location"], len(seen)), (302, "https://other.example/headers", 1))
        req = urllib.request.Request("https://api.example.com/x", headers={"X-Api-Key": KEY})
        self.assertIsNone(postman.NoRedirect().redirect_request(req, None, 302, "Found", {}, "https://other.example/"))


class Main(unittest.TestCase):
    """main() end to end with the socket faked: what is printed, what is refused, what was sent."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)

    def files(self, auth=APIKEY_HEADER, env=ENV, extra=None, folders=(), schema=None):
        root = Path(self.dir.name)
        (root / "env.json").write_text(json.dumps(env), encoding="utf-8")
        (root / "col.json").write_text(json.dumps(collection(auth, folders, schema=schema)), encoding="utf-8")
        argv = ["--env", str(root / "env.json"), "--collection", str(root / "col.json")]
        if extra is not None:
            (root / "vars.json").write_text(json.dumps(extra), encoding="utf-8")
            argv += ["--vars", str(root / "vars.json")]
        return argv

    def test_checks_4_the_record_masks_the_query_key_the_server_saw(self):
        with server(OK_JSON) as seen:
            code, out, err = run_main(self.files(APIKEY_QUERY) + ["--path", "/get?x=1"])
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(seen[0]["url"], f"https://api.example.com/get?x=1&api+key={KEY}")
        record = json.loads(out)
        self.assertEqual(record["url"], "https://api.example.com/get?x=1&api+key=********")
        self.assertEqual((record["status"], record["body"], record["auth"]), (200, '{"ok": true}', "API key in query parameter api key"))
        self.assertEqual(record["response_headers"], {"Content-Type": "application/json; charset=utf-8", "Content-Length": "12"})
        self.assertFalse(any(form in out for form in encodings(KEY)), out)

    def test_checks_4_the_record_masks_the_header_the_server_saw(self):
        with server(OK_JSON) as seen:
            code, out, err = run_main(self.files(BEARER) + ["--path", "items/1"])
        self.assertEqual(code, 0, err)
        self.assertEqual((seen[0]["headers"]["authorization"], seen[0]["headers"]["user-agent"]), (f"Bearer {KEY}", "api-call"))
        record = json.loads(out)
        self.assertEqual(record["request_headers"], {"Accept": "application/json", "User-Agent": "api-call", "Authorization": "Bearer ********"})
        self.assertEqual((record["source"], record["environment"], record["collection"], record["path"]), ("postman", "Test", "Example", "/items/1"))
        self.assertNotIn(KEY, out)

    def test_checks_4_an_echoed_secret_is_refused_and_nothing_is_printed(self):
        for echo in ((200, {"Content-Type": "application/json"}, json.dumps({"headers": {"x-api-key": KEY}}).encode()),
                     (302, {"Location": f"https://other.example/?k={KEY}"}, b"")):
            with server(echo):
                code, out, err = run_main(self.files(APIKEY_HEADER) + ["--path", "/headers"])
            self.assertEqual((code, out), (1, ""))
            self.assertIn("refusing to print", err)
            self.assertNotIn(KEY, err)

    def test_secrets_2_an_echo_trimmed_in_transit_is_refused(self):
        echo = (200, {"Content-Type": "application/json"}, b'{"headers": {"x-api-key": "FAKE-key-4b8d2e61"}}')
        with server(echo) as seen:
            code, out, err = run_main(self.files(APIKEY_HEADER, extra={"apiKey": "FAKE-key-4b8d2e61 "}) + ["--path", "/headers"])
        self.assertEqual(seen[0]["headers"]["x-api-key"], "FAKE-key-4b8d2e61 ")  # sent as stored
        self.assertEqual((code, out), (1, ""))
        self.assertIn("refusing to print", err)

    def test_docs_1_an_echo_json_escaped_is_refused(self):
        for key, body in (("fake-ü-1234567", b'{"headers": {"X-Api-Key": "fake-\\u00fc-1234567"}}'),
                          ("abc+def/ghi12==", b'{"error": "invalid key abc\\u002Bdef/ghi12=="}')):
            with server((200, {"Content-Type": "application/json"}, body)):
                code, out, err = run_main(self.files(APIKEY_HEADER, extra={"apiKey": key}) + ["--path", "/headers"])
            self.assertEqual((code, out), (1, ""), key)
            self.assertIn("refusing to print", err)

    def test_checks_4_a_redirect_is_recorded_and_not_followed(self):
        hop = (302, {"Location": "https://other.example/headers", "Content-Type": "text/html"}, b"<a>moved</a>")
        with server(hop, OK_JSON) as seen:
            code, out, err = run_main(self.files(APIKEY_HEADER) + ["--path", "/redirect-to?url=https%3A%2F%2Fother.example%2Fheaders"])
        self.assertEqual(code, 0, err)
        self.assertEqual(len(seen), 1, "the redirect was followed")
        self.assertEqual(seen[0]["headers"]["x-api-key"], KEY)
        record = json.loads(out)
        self.assertEqual((record["status"], record["response_headers"]["Location"], record["body"]), (302, "https://other.example/headers", "<a>moved</a>"))

    def test_docs_2_a_failed_requests_error_is_scrubbed_in_every_form(self):
        key = "fake/key+with=1234567"
        quoting = lambda req: http.client.InvalidURL(f"URL can't contain control characters. {req.selector!r} (found at least ' ')")
        with server(quoting):
            code, out, err = run_main(self.files(APIKEY_QUERY, extra={"apiKey": key}) + ["--path", "/anything"])
        self.assertEqual((code, out), (1, ""))
        self.assertIn("the request failed: InvalidURL", err)
        self.assertIn("api+key=********", err)
        self.assertFalse(any(form in err for form in encodings(key)), err)
        # An error quoting the URL with lower-case escapes matches no form, but decoded it is the key: withheld.
        with server(lambda req: OSError(f"refused: {req.full_url.replace('%2F', '%2f').replace('%2B', '%2b')}")):
            code, out, err = run_main(self.files(APIKEY_QUERY, extra={"apiKey": key}) + ["--path", "/anything"])
        self.assertEqual((code, out), (1, ""))
        self.assertEqual(err, f"the request failed: URLError: {postman.WITHHELD}")

    def test_secrets_3_a_key_with_a_trailing_newline_is_refused_before_anything_is_sent(self):
        for auth in (APIKEY_HEADER, BEARER):
            with server(OK_JSON) as seen:
                code, out, err = run_main(self.files(auth, extra={"apiKey": "FAKE-key-4b8d2e61\n"}) + ["--path", "/headers"])
            self.assertEqual((code, out, seen), (1, "", []))
            self.assertIn("control character", err)
            self.assertNotIn("4b8d2e61", err)

    def test_extra_1_a_v2_0_export_sends_its_bearer_token(self):
        with server(OK_JSON) as seen:
            code, out, err = run_main(self.files({"type": "bearer", "bearer": {"token": "{{apiKey}}"}}, schema=V20) + ["--path", "/get"])
        self.assertEqual(code, 0, err)
        self.assertEqual(seen[0]["headers"]["authorization"], f"Bearer {KEY}")
        self.assertEqual(json.loads(out)["auth"], "bearer token")

    def test_extra_1_a_missing_credential_stops_the_call_before_it_is_sent(self):
        blank = {"name": "Test", "values": [{"key": "baseUrl", "value": "https://api.example.com"}, {"key": "apiKey", "value": "", "type": "secret"}]}
        with server(OK_JSON) as seen:
            code, out, err = run_main(self.files(BEARER, env=blank) + ["--path", "/get"])
        self.assertEqual((code, out, seen), (1, "", []))
        self.assertIn("empty value", err)

    def test_correctness_13_a_folder_inheriting_the_collections_auth_sends_it(self):
        with server(OK_JSON) as seen:
            code, out, err = run_main(self.files(BEARER, folders=[folder("Reports"), folder("Admin", APIKEY_HEADER)]) + ["--folder", "Reports", "--path", "/r"])
        self.assertEqual(code, 0, err)
        self.assertEqual(seen[0]["headers"]["authorization"], f"Bearer {KEY}")
        self.assertEqual((json.loads(out)["folder"], json.loads(out)["auth"]), ("Reports", "bearer token"))

    def test_correctness_16_a_path_with_a_space_is_sent_encoded(self):
        with server(OK_JSON) as seen:
            code, out, err = run_main(self.files() + ["--path", "/get?q=two words&city=Málaga"])
        self.assertEqual(code, 0, err)
        self.assertEqual(seen[0]["url"], "https://api.example.com/get?q=two%20words&city=M%C3%A1laga")
        self.assertEqual(json.loads(out)["path"], "/get?q=two words&city=Málaga")

    def test_correctness_17_a_bom_before_a_json_body_is_dropped(self):
        with server((200, {"Content-Type": "application/json"}, b'\xef\xbb\xbf{"widget": 42, "state": "removed"}')):
            code, out, err = run_main(self.files() + ["--path", "/widgets/42"])
        self.assertEqual(code, 0, err)
        body = json.loads(out)["body"]
        self.assertTrue(body.startswith("{"), repr(body[:3]))
        self.assertEqual(json.loads(body), {"widget": 42, "state": "removed"})

    def test_a_certificate_passphrase_is_a_secret(self):
        canned = (200, {"Content-Type": "text/plain"}, b"", 1, datetime.now().astimezone())
        echo = (200, {"Content-Type": "text/plain"}, b"unlocked with pass-phrase-123", 1, datetime.now().astimezone())
        argv = self.files() + ["--path", "/x", "--cert", "c.pem", "--key", "k.pem"]
        with mock.patch.object(postman, "send", return_value=canned):
            self.assertEqual(run_main(argv, {"API_CALL_KEY_PASSPHRASE": "pass-phrase-123"})[0], 0)
        with mock.patch.object(postman, "send", return_value=echo):
            code, out, err = run_main(argv, {"API_CALL_KEY_PASSPHRASE": "pass-phrase-123"})
        self.assertEqual((code, out), (1, ""))
        self.assertIn("refusing to print", err)

    DRIVER = (  # runs main() in a child Python with the socket faked, so the child's own stdout encoding is what is tested
        "import json, sys\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "import test_postman, postman\n"
        "body = json.dumps({'name': 'caf\\u00e9', 'check': '\\u2713', 'binary': '\\ufffd'}, ensure_ascii=False).encode('utf-8')\n"
        "with test_postman.server((200, {'Content-Type': 'application/json; charset=utf-8'}, body)):\n"
        "    sys.argv = ['postman.py', *sys.argv[2:]]\n"
        "    postman.main()\n")

    def test_correctness_9_output_is_utf8_whatever_the_console_code_page(self):
        # PYTHONIOENCODING=cp1252 stands in for a Windows pipe, which takes the ANSI code page; call.py reads UTF-8.
        env = {k: v for k, v in os.environ.items() if k != "PYTHONUTF8"}
        env["PYTHONIOENCODING"] = "cp1252"
        proc = subprocess.run([sys.executable, "-c", self.DRIVER, str(HERE), *self.files(), "--path", "/get"], capture_output=True, env=env, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr.decode("utf-8", "replace"))
        record = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(json.loads(record["body"]), {"name": "café", "check": "✓", "binary": "�"})


# --- parity with the Insomnia source: decisions D1, D2, D4 and D5 ---------------------------------------------------

def plain_env(*pairs, base="https://api.example.com"):
    """An environment whose values are all typed default, not secret: only what build() registers makes one a secret."""
    return {"name": "Test", "values": [{"key": "baseUrl", "value": base, "type": "default", "enabled": True}]
            + [{"key": k, "value": v, "type": "default", "enabled": True} for k, v in pairs]}


def apikey(key, value="{{apiKey}}", where="header"):
    return {"type": "apikey", "apikey": [{"key": "key", "value": key}, {"key": "value", "value": value}, {"key": "in", "value": where}]}


NESTED = (  # Insomnia review entries 02, 03 and 04: a variable whose value holds templates of its own
    ((("token", "FAKE-tok-7f3a9c2e{{ suffix }}"), ("suffix", "")), "FAKE-tok-7f3a9c2e"),
    ((("token", "fake_{{ stage }}_NOTAKEY0nested0template"), ("stage", "test")), "fake_test_NOTAKEY0nested0template"),
    ((("token", "{{ a }}{{ b }}"), ("a", "Fk3x9"), ("b", "Qz7Lm")), "Fk3x9Qz7Lm"))
BEARER_IN_APIKEY = apikey("Authorization", "Bearer {{token}}")  # how a Postman collection often writes a bearer header


class Parity(unittest.TestCase):
    """D1: every value a variable puts into the request is a secret, at every depth, and a templated part is shown as
    ********. D2: the base URL's own text is shown, a variable in it is a secret, a userinfo password is always masked.
    D4: one layer of encoding is checked. D5: names are case-sensitive. A test's number is the Insomnia review entry
    it is modelled on."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)

    def build(self, auth, env, path="/x", extra=None):
        return postman.build(collection(auth), env, extra or {}, None, "baseUrl", path)

    def main(self, auth, env, path, *script, extra=None):
        """main() on these exports against a fake server: (exit code, stdout, stderr, requests seen)."""
        root = Path(self.dir.name)
        (root / "env.json").write_text(json.dumps(env), encoding="utf-8")
        (root / "col.json").write_text(json.dumps(collection(auth)), encoding="utf-8")
        argv = ["--env", str(root / "env.json"), "--collection", str(root / "col.json"), "--path", path]
        if extra is not None:
            (root / "vars.json").write_text(json.dumps(extra), encoding="utf-8")
            argv += ["--vars", str(root / "vars.json")]
        with server(*script) as seen:
            code, out, err = run_main(argv)
        return code, out, err, seen

    def assertRefused(self, result, *secrets):
        code, out, err, _ = result
        self.assertEqual((code, out), (1, ""), err)
        self.assertIn("refusing to print", err)
        for s in secrets:
            self.assertNotIn(s, err)

    def test_d1_02_resolve_registers_each_variable_fully_resolved_at_every_depth(self):
        used = []
        variables = {"a": "x-{{ b }}", "b": "y-{{c}}", "c": "zzz-123456"}
        self.assertEqual(postman.resolve("pre {{a}}", variables, used), "pre x-y-zzz-123456")
        self.assertEqual(set(used), {"zzz-123456", "y-zzz-123456", "x-y-zzz-123456"})
        self.assertFalse(any("{{" in u for u in used), used)

    def test_d1_02_03_04_a_variable_holding_a_template_is_a_secret_resolved(self):
        for pairs, token in NESTED:
            b = self.build(BEARER_IN_APIKEY, plain_env(*pairs))
            self.assertEqual(b["headers"]["Authorization"], f"Bearer {token}")
            self.assertIn(token, b["secrets"])
            echo = (200, {"Content-Type": "application/json"}, json.dumps({"authenticated": True, "token": token}).encode())
            self.assertRefused(self.main(BEARER_IN_APIKEY, plain_env(*pairs), "/bearer", echo), token)

    def test_d1_16_each_part_of_a_composite_api_key_value_is_a_secret(self):
        env = plain_env(("scheme", "FAKE-pfx-31415926"), ("token", "FAKE-tok-27182818"))
        auth = apikey("Authorization", "{{ scheme }} {{ token }}")
        b = self.build(auth, env)
        self.assertEqual((b["headers"]["Authorization"], b["shown_headers"]["Authorization"]), ("FAKE-pfx-31415926 FAKE-tok-27182818", "********"))
        self.assertIn("FAKE-pfx-31415926", b["secrets"])
        echo = (200, {"Content-Type": "application/json"}, b'{"scheme": "FAKE-pfx-31415926"}')  # the prefix alone
        self.assertRefused(self.main(auth, env, "/headers", echo), "FAKE-pfx-31415926")

    def test_d1_17_a_templated_query_parameter_name_is_masked_and_a_secret(self):
        env = plain_env(("qn", "FAKE-qn-27182818"), ("apiKey", "FAKE-key-4b8d2e61"))
        auth = apikey("q {{ qn }}", where="query")
        b = self.build(auth, env, "/get?x=1")
        self.assertEqual(b["url"], "https://api.example.com/get?x=1&q+FAKE-qn-27182818=FAKE-key-4b8d2e61")
        self.assertEqual(b["shown_url"], "https://api.example.com/get?x=1&q+********=********")
        self.assertEqual(b["auth"], "API key in query parameter q ********")
        self.assertIn("FAKE-qn-27182818", b["secrets"])
        code, out, err, seen = self.main(auth, env, "/get?x=1", OK_JSON)
        self.assertEqual(code, 0, err)
        self.assertEqual(seen[0]["url"], b["url"])
        self.assertEqual((json.loads(out)["url"], json.loads(out)["auth"]), (b["shown_url"], b["auth"]))
        self.assertNotIn("FAKE-qn", out)
        echo = (200, {"Content-Type": "application/json"}, b'{"args": {"q FAKE-qn-27182818": "x"}}')
        self.assertRefused(self.main(auth, env, "/get", echo), "FAKE-qn-27182818")

    def test_d1_18_a_templated_header_name_is_masked_and_a_secret_in_any_letter_case(self):
        env = plain_env(("kn", "FAKE-kn-16180339"), ("apiKey", "FAKE-key-4b8d2e61"))
        auth = apikey("X-{{ kn }}")
        b = self.build(auth, env)
        self.assertEqual(b["headers"]["X-FAKE-kn-16180339"], "FAKE-key-4b8d2e61")
        self.assertEqual(b["shown_headers"], {"Accept": "application/json", "User-Agent": "api-call", "X-********": "********"})
        self.assertEqual(b["auth"], "API key in header X-********")
        self.assertEqual(b["names"], ["FAKE-kn-16180339"])
        code, out, err, seen = self.main(auth, env, "/x", OK_JSON)
        self.assertEqual(code, 0, err)
        self.assertEqual(seen[0]["headers"]["x-fake-kn-16180339"], "FAKE-key-4b8d2e61")
        self.assertEqual((json.loads(out)["request_headers"], json.loads(out)["auth"]), (b["shown_headers"], b["auth"]))
        self.assertNotIn("kn-16180339", out.lower())
        # httpbin echoes a header's name title-cased, postman-echo lower-cased: either is the name's secret part.
        for echoed in ("X-Fake-Kn-16180339", "x-fake-kn-16180339", "X-FAKE-kn-16180339"):
            echo = (200, {"Content-Type": "application/json"}, json.dumps({"headers": {echoed: "short"}}).encode())
            self.assertRefused(self.main(auth, env, "/headers", echo))

    def test_d1_18_a_problem_with_a_templated_header_name_does_not_quote_it(self):
        for kn, apikey_value, why in (("FAKE kn:16180339", "FAKE-key-4b8d2e61", "names no header"),
                                      ("FAKE-kn-16180339", "FAKE-key-4b8d2e61\n", "control character")):
            env = plain_env(("kn", kn), ("apiKey", apikey_value))
            with self.assertRaisesRegex(postman.Problem, why) as cm:
                self.build(apikey("X-{{ kn }}"), env)
            self.assertIn("X-********", str(cm.exception))
            self.assertNotIn("16180339", str(cm.exception))
            code, out, err, seen = self.main(apikey("X-{{ kn }}"), env, "/x", OK_JSON)
            self.assertEqual((code, out, seen), (1, "", []))
            self.assertNotIn("16180339", err)

    def test_d1_38_a_name_inside_a_variables_value_is_not_quoted(self):
        secret = "p4ss{{Hunter2-FAKE-pw}}w0rd"
        with self.assertRaises(postman.Problem) as cm:
            self.build(APIKEY_HEADER, plain_env(), extra={"apiKey": secret})
        self.assertNotIn("Hunter2", str(cm.exception))
        self.assertIn("'apiKey'", str(cm.exception))
        for extra, env in (({"apiKey": secret}, plain_env()), (None, plain_env(("apiKey", secret)))):
            code, out, err, seen = self.main(APIKEY_HEADER, env, "/x", OK_JSON, extra=extra)
            self.assertEqual((code, out, seen), (1, "", []))
            self.assertNotIn("Hunter2", err)
        with self.assertRaisesRegex(postman.Problem, "variable 'nope' is not"):  # a name the auth itself uses is named
            self.build(apikey("X-Api-Key", "{{nope}}"), plain_env())

    def test_d1_a_templated_basic_auth_user_is_masked_and_a_secret(self):
        env = plain_env(("user", "FAKE-user-51505150"), ("apiKey", "FAKE-key-4b8d2e61"))
        auth = {"type": "basic", "basic": [{"key": "username", "value": "svc-{{ user }}"}, {"key": "password", "value": "{{apiKey}}"}]}
        b = self.build(auth, env)
        self.assertEqual(b["auth"], "basic auth as svc-********")
        self.assertIn("FAKE-user-51505150", b["secrets"])
        echo = (200, {"Content-Type": "application/json"}, b'{"authenticated": true, "user": "svc-FAKE-user-51505150"}')
        self.assertRefused(self.main(auth, env, "/basic-auth", echo), "FAKE-user-51505150")

    def test_d1_a_resolver_cycle_or_runaway_value_is_refused_quickly(self):
        with self.assertRaisesRegex(postman.Problem, "cannot resolve"):
            postman.resolve("{{a}}", {"a": "x{{b}}", "b": "{{a}}"})
        runaway = {"a": "{{b}}" * 20, "b": "{{c}}" * 20, "c": "x" * 200}  # 80,000 characters, past the limit
        with self.assertRaisesRegex(postman.Problem, "more than"):
            postman.resolve("{{a}}", runaway)

    def test_d2_25_a_variable_in_the_base_url_is_masked_and_a_secret(self):
        env = plain_env(("fnKey", "FAKE-fnkey-8a7b6c5d"), base="https://api.example.com/anything/{{ fnKey }}/")
        b = self.build(None, env)
        self.assertEqual(b["url"], "https://api.example.com/anything/FAKE-fnkey-8a7b6c5d/x")
        self.assertEqual(b["shown_url"], "https://api.example.com/anything/********/x")
        self.assertIn("FAKE-fnkey-8a7b6c5d", b["secrets"])
        code, out, err, seen = self.main(None, env, "/x", OK_JSON)
        self.assertEqual(code, 0, err)
        self.assertEqual((seen[0]["url"], json.loads(out)["url"]), (b["url"], b["shown_url"]))
        self.assertNotIn("FAKE-fnkey", out)
        echo = (200, {"Content-Type": "application/json"}, json.dumps({"url": b["url"]}).encode())  # as /anything echoes
        self.assertRefused(self.main(None, env, "/x", echo), "FAKE-fnkey-8a7b6c5d")
        host = self.build(None, plain_env(("host", "api.example.com"), ("v", "v2"), base="https://{{host}}/{{ v }}"))
        self.assertEqual((host["url"], host["shown_url"]), ("https://api.example.com/v2/x", "https://********/********/x"))
        self.assertEqual(self.build(None, plain_env(("u", "https://api.example.com"), base="{{u}}"))["shown_url"], "********/x")

    def test_d2_26_a_userinfo_password_is_masked_and_sent_as_basic_auth(self):
        env = plain_env(("pw", "FAKE-pw-9z8y7x6w"), base="https://user:{{ pw }}@api.example.com")
        b = self.build(None, env, "/headers")
        basic = postman.base64.b64encode(b"user:FAKE-pw-9z8y7x6w").decode()
        self.assertEqual((b["url"], b["shown_url"]), ("https://api.example.com/headers", "https://user:********@api.example.com/headers"))
        self.assertEqual((b["headers"]["Authorization"], b["shown_headers"]["Authorization"]), (f"Basic {basic}", "Basic ********"))
        self.assertEqual(b["auth"], "basic auth from the base URL as user")
        self.assertTrue({"FAKE-pw-9z8y7x6w", basic} <= set(b["secrets"]))
        code, out, err, seen = self.main(None, env, "/headers", OK_JSON)  # before: http.client's InvalidURL quoted the password
        self.assertEqual(code, 0, err)
        self.assertEqual((seen[0]["url"], seen[0]["headers"]["authorization"]), ("https://api.example.com/headers", f"Basic {basic}"))
        self.assertEqual(json.loads(out)["url"], b["shown_url"])
        self.assertNotIn("FAKE-pw", out)
        self.assertNotIn(basic, out)
        echo = (200, {"Content-Type": "application/json"}, json.dumps({"headers": {"Authorization": f"Basic {basic}"}}).encode())
        self.assertRefused(self.main(None, env, "/headers", echo), basic)
        literal = self.build(None, plain_env(base="https://us%40er:Lit%2Fpw-424242@api.example.com/v1"))  # literal: masked all the same
        self.assertEqual(literal["shown_url"], "https://us%40er:********@api.example.com/v1/x")
        self.assertEqual(literal["headers"]["Authorization"], "Basic " + postman.base64.b64encode(b"us@er:Lit/pw-424242").decode())
        self.assertTrue({"Lit%2Fpw-424242", "Lit/pw-424242"} <= set(literal["secrets"]))
        own = self.build(BEARER, {**env, "values": env["values"] + [{"key": "apiKey", "value": KEY}]})  # the auth's own Authorization wins
        self.assertEqual((own["headers"]["Authorization"], own["auth"]), (f"Bearer {KEY}", "bearer token"))
        self.assertEqual(own["shown_url"], "https://user:********@api.example.com/x")
        whole = self.build(None, plain_env(("creds", "FAKE-user:FAKE-pw-9z8y7x6w"), base="https://{{creds}}@api.example.com"))
        self.assertEqual((whole["shown_url"], whole["auth"]), ("https://********@api.example.com/x", "basic auth from the base URL as ********"))

    def test_d4_33_every_form_of_one_layer_of_encoding_is_refused(self):
        record = {"url": "https://api.example.com/x", "auth": "none", "request_headers": {}, "response_headers": {}}
        key = "abc+def/ghi12=="
        for body in ("abc+def/ghi12==", "abc%2Bdef%2Fghi12%3D%3D", "abc%2bdef%2fghi12%3d%3d", '{"k": "abc\\u002Bdef\\/ghi12=="}',
                     "abc&#43;def&#47;ghi12&#61;&#61;", "abc&#x2B;def&#x2F;ghi12&#x3D;&#x3D;", "abc&#X2b;def&#X2f;ghi12&#X3d;&#X3D;",
                     "abc&plus;def&sol;ghi12&equals;&equals;"):
            self.assertEqual(postman.exposed({**record, "body": body}, [key]), key, body)
        for secret, body in ((" FAKE-key-4b8d2e61\t", "FAKE-key-4b8d2e61"),  # trimmed
                             ("two words-key1", "two+words-key1"), ("two words-key1", "two%20words-key1"),  # + as space, or not
                             ("a&b<c>d\"e'f-12", "a&amp;b&lt;c&gt;d&quot;e&apos;f-12"), ("a&b<c>d\"e'f-12", "a&amp;b&lt;c&gt;d&quot;e&#39;f-12")):
            self.assertEqual(postman.exposed({**record, "body": body}, [secret]), secret, body)
        echo = (200, {"Content-Type": "text/html"}, b"<p>invalid key abc&#x2B;def&#x2F;ghi12&#x3D;&#x3D;</p>")
        self.assertRefused(self.main(APIKEY_HEADER, plain_env(), "/x", echo, extra={"apiKey": key}))

    def test_d5_variable_names_are_case_sensitive(self):
        for pairs in ((("apiKey", "pub12"), ("APIKEY", "prd99")), (("APIKEY", "prd99"), ("apiKey", "pub12"))):
            code, out, err, seen = self.main(APIKEY_HEADER, plain_env(*pairs), "/headers", OK_JSON)
            self.assertEqual(code, 0, err)
            self.assertEqual(seen[0]["headers"]["x-api-key"], "pub12")
        with self.assertRaisesRegex(postman.Problem, "variable 'apikey' is not"):
            self.build(apikey("X-Api-Key", "{{apikey}}"), plain_env(("apiKey", "pub12"), ("APIKEY", "prd99")))


# --- end to end, against real hosts ---------------------------------------------------------------------------------

@unittest.skipUnless(os.environ.get("API_CALL_E2E"), "set API_CALL_E2E=1 to send real requests to postman-echo.com and httpbin.org")
class EndToEnd(unittest.TestCase):
    """Real requests through call.py, with fake secrets, to hosts that echo what they receive."""
    FAKE = "FAKE-key-4b8d2e61"

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)

    def call(self, path, host="https://postman-echo.com", auth=APIKEY_HEADER, key=FAKE, expect=None, env_extra=None, name="e2e",
             env_values=(), variables=None):
        """call.py --via postman against host: (process, record or None, stdout + stderr as text). Run again once
        when the first try failed the way a network does: a connection error, or a 5xx from the echo host.
        env_values are (name, value) pairs typed default in the environment; variables replace the --vars file."""
        root = Path(self.dir.name)
        (root / "env.json").write_text(json.dumps(plain_env(*env_values, base=host)), encoding="utf-8")
        (root / "col.json").write_text(json.dumps(collection(auth)), encoding="utf-8")
        (root / "vars.json").write_text(json.dumps({"apiKey": key} if variables is None else variables), encoding="utf-8")
        argv = [sys.executable, str(SCRIPTS / "call.py"), "--via", "postman", "--env", str(root / "env.json"), "--collection", str(root / "col.json"),
                "--vars", str(root / "vars.json"), "--out", str(root / "out"), "--name", name, "--timeout", "30"]
        if expect is not None:
            argv += ["--expect", str(expect)]
        env = {k: v for k, v in os.environ.items() if k != "PYTHONUTF8"}
        env.update(env_extra or {})
        record_file = root / "out" / f"{name}.json"
        for attempt in range(2):
            record_file.unlink(missing_ok=True)
            proc = subprocess.run(argv + ["GET", path], capture_output=True, env=env, timeout=150)
            text = proc.stdout.decode("utf-8", "replace") + proc.stderr.decode("utf-8", "replace")
            record = json.loads(record_file.read_text(encoding="utf-8")) if record_file.exists() else None
            if "the request failed" not in text and not (record and record["status"] >= 500):
                break
        return proc, record, text

    def test_checks_4_e2e_an_echoed_secret_is_refused_and_no_record_is_written(self):
        for path, auth in (("/headers", APIKEY_HEADER), ("/get", APIKEY_QUERY)):
            proc, record, text = self.call(path, auth=auth)
            self.assertEqual(proc.returncode, 1, text)
            self.assertIn("refusing to print", text)
            self.assertIsNone(record)
            self.assertNotIn(self.FAKE, text)

    def test_checks_4_e2e_a_query_key_is_sent_and_masked_in_the_record(self):
        proc, record, text = self.call("/status/200", auth=APIKEY_QUERY, expect=200)
        self.assertEqual(proc.returncode, 0, text)
        self.assertEqual(record["url"], "https://postman-echo.com/status/200?api+key=********")
        self.assertNotIn(self.FAKE, json.dumps(record))

    def test_checks_4_e2e_a_redirect_is_recorded_with_its_location_and_not_followed(self):
        # Followed, the key would reach postman-echo.com, which echoes it, and the call would be refused.
        proc, record, text = self.call("/redirect-to?url=https%3A%2F%2Fpostman-echo.com%2Fheaders", host="https://httpbin.org", expect=302)
        self.assertEqual(proc.returncode, 0, text)
        self.assertEqual((record["status"], record["verdict"], record["response_headers"]["Location"]), (302, "PASS", "https://postman-echo.com/headers"))

    def test_secrets_2_e2e_an_echo_trimmed_in_transit_is_refused(self):
        proc, record, text = self.call("/headers", key=self.FAKE + " ")
        self.assertEqual(proc.returncode, 1, text)
        self.assertIn("refusing to print", text)
        self.assertIsNone(record)

    def test_docs_1_e2e_an_echo_json_escaped_is_refused(self):
        proc, record, text = self.call("/headers", host="https://httpbin.org", key="fake-ü-1234567")  # httpbin sends fake-ü-1234567
        self.assertEqual(proc.returncode, 1, text)
        self.assertIn("refusing to print", text)
        self.assertIsNone(record)

    def test_secrets_3_e2e_a_key_with_a_trailing_newline_is_refused_without_the_value(self):
        proc, record, text = self.call("/headers", key=self.FAKE + "\n")
        self.assertEqual(proc.returncode, 1, text)
        self.assertIn("control character", text)
        self.assertNotIn("4b8d2e61", text)
        self.assertIsNone(record)

    def test_correctness_16_e2e_a_path_with_a_space_and_non_ascii_is_sent_encoded(self):
        proc, record, text = self.call("/get?q=two words&city=Málaga", auth=None, expect=200)
        self.assertEqual(proc.returncode, 0, text)
        self.assertEqual(json.loads(record["body"])["args"], {"q": "two words", "city": "Málaga"})
        self.assertEqual(record["url"], "https://postman-echo.com/get?q=two%20words&city=M%C3%A1laga")

    def test_correctness_9_e2e_a_non_ascii_body_reaches_call_py_under_a_windows_code_page(self):
        proc, record, text = self.call("/get?name=caf%C3%A9&check=%E2%9C%93", auth=None, expect=200, env_extra={"PYTHONIOENCODING": "cp1252"})
        self.assertEqual(proc.returncode, 0, text)
        self.assertEqual(json.loads(record["body"])["args"], {"name": "café", "check": "✓"})

    def test_correctness_17_e2e_a_bom_before_a_json_body_is_dropped(self):
        proc, record, text = self.call("/base64/77u_eyJ3aWRnZXQiOiA0MiwgInN0YXRlIjogInJlbW92ZWQifQ==", host="https://httpbin.org", auth=None, expect=200)
        self.assertEqual(proc.returncode, 0, text)
        self.assertTrue(record["body"].startswith("{"), repr(record["body"][:3]))
        self.assertEqual(json.loads(record["body"]), {"widget": 42, "state": "removed"})

    # Parity with the Insomnia source (see Parity): each test's number is the Insomnia review entry it is modelled on.

    def assertRefusedE2E(self, result, *secrets):
        proc, record, text = result
        self.assertEqual(proc.returncode, 1, text)
        self.assertIn("refusing to print", text)
        self.assertIsNone(record)
        for s in secrets:
            self.assertNotIn(s, text)

    @staticmethod
    def served(body):
        """A path on httpbin.org that answers with this body: a server that echoes exactly this."""
        return "/base64/" + postman.base64.urlsafe_b64encode(body.encode("utf-8")).decode("ascii")

    def test_d1_02_03_04_e2e_a_variable_holding_a_template_echoed_by_bearer_is_refused(self):
        for pairs, token in NESTED:
            self.assertRefusedE2E(self.call("/bearer", host="https://httpbin.org", auth=BEARER_IN_APIKEY, env_values=pairs, variables={}), token)

    def test_d1_16_e2e_a_part_of_an_api_key_value_echoed_alone_is_refused(self):
        pairs = (("scheme", "FAKE-pfx-31415926"), ("token", "FAKE-tok-27182818"))
        self.assertRefusedE2E(self.call(self.served('{"scheme": "FAKE-pfx-31415926"}'), host="https://httpbin.org",
                                        auth=apikey("X-Auth", "{{ scheme }} {{ token }}"), env_values=pairs, variables={}), "FAKE-pfx-31415926")

    def test_d1_17_e2e_a_templated_query_parameter_name_is_masked_and_refused_when_echoed(self):
        auth, pairs = apikey("q-{{ qn }}", where="query"), (("qn", "FAKE-qn-27182818"),)
        proc, record, text = self.call("/status/200", host="https://httpbin.org", auth=auth, env_values=pairs, expect=200)
        self.assertEqual(proc.returncode, 0, text)
        self.assertEqual((record["url"], record["auth"]), ("https://httpbin.org/status/200?q-********=********", "API key in query parameter q-********"))
        self.assertNotIn("FAKE-qn", json.dumps(record))
        # The key is too short to check for, so only the parameter's name, echoed in args and url, can refuse the call.
        self.assertRefusedE2E(self.call("/anything", host="https://httpbin.org", auth=auth, key="k1234", env_values=pairs), "FAKE-qn-27182818")

    def test_d1_18_e2e_a_templated_header_name_is_masked_and_refused_when_echoed_title_cased(self):
        auth, pairs = apikey("X-{{ kn }}"), (("kn", "FAKE-kn-16180339"),)
        proc, record, text = self.call("/status/200", host="https://httpbin.org", auth=auth, env_values=pairs, expect=200)
        self.assertEqual(proc.returncode, 0, text)
        self.assertEqual((record["request_headers"]["X-********"], record["auth"]), ("********", "API key in header X-********"))
        self.assertNotIn("kn-16180339", json.dumps(record).lower())
        # httpbin echoes the name as X-Fake-Kn-16180339; the key is too short to check for, so only the name can refuse it.
        self.assertRefusedE2E(self.call("/headers", host="https://httpbin.org", auth=auth, key="k1234", env_values=pairs))

    def test_d2_25_e2e_a_variable_in_the_base_url_is_masked_and_refused_when_echoed(self):
        self.assertRefusedE2E(self.call("/x", host="https://httpbin.org/anything/{{ fnKey }}", auth=None,
                                        env_values=(("fnKey", "FAKE-fnkey-8a7b6c5d"),)), "FAKE-fnkey-8a7b6c5d")
        proc, record, text = self.call("/status/200", host="https://{{ host }}", auth=None, env_values=(("host", "httpbin.org"),), expect=200)
        self.assertEqual(proc.returncode, 0, text)
        self.assertEqual(record["url"], "https://********/status/200")

    def test_d2_26_e2e_a_userinfo_password_is_masked_and_sent_as_basic_auth(self):
        pairs = (("pw", "FAKE-pw-9z8y7x6w"),)
        # /headers echoes the Authorization header: refused, so the password was sent, as basic auth.
        self.assertRefusedE2E(self.call("/headers", host="https://user:{{ pw }}@httpbin.org", auth=None, env_values=pairs), "FAKE-pw-9z8y7x6w")
        for host in ("https://user:{{ pw }}@httpbin.org", "https://user:Lit-pw-424242@httpbin.org"):
            proc, record, text = self.call("/status/200", host=host, auth=None, env_values=pairs, expect=200)
            self.assertEqual(proc.returncode, 0, text)
            self.assertEqual(record["url"], "https://user:********@httpbin.org/status/200")
            self.assertEqual((record["request_headers"]["Authorization"], record["auth"]), ("Basic ********", "basic auth from the base URL as user"))
            self.assertNotIn("pw-", json.dumps(record))

    def test_d4_33_e2e_an_html_escaped_echo_is_refused(self):
        key = "abc+def/ghi12=="
        for echoed in ("abc&#x2B;def&#x2F;ghi12&#x3D;&#x3D;", "abc&#43;def&#47;ghi12&#61;&#61;", "abc&plus;def&sol;ghi12&equals;&equals;"):
            self.assertRefusedE2E(self.call(self.served(f'{{"k": "{echoed}"}}'), host="https://httpbin.org", key=key), echoed)

    def test_d5_e2e_variable_names_are_case_sensitive(self):
        proc, record, text = self.call("/headers", host="https://httpbin.org", env_values=(("apiKey", "pub12"), ("APIKEY", "prd99")),
                                       variables={}, expect=200)
        self.assertEqual(proc.returncode, 0, text)
        self.assertEqual(json.loads(record["body"])["headers"]["X-Api-Key"], "pub12")


if __name__ == "__main__":
    unittest.main()
